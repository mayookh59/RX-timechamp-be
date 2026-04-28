"""Security middleware for HTTP hardening and input protection.

Adds defense-in-depth security headers, enforces request size limits,
performs input sanitization, and detects common SQL injection patterns.
"""

import re

import structlog
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

logger = structlog.stdlib.get_logger(__name__)

# Maximum request body size in bytes (10 MB)
MAX_REQUEST_SIZE_BYTES = 10 * 1024 * 1024

# Security headers applied to every response
SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
    "Pragma": "no-cache",
}

# SQL injection detection patterns
_SQL_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC)\b\s)", re.IGNORECASE),
    re.compile(r"(--|#|/\*)", re.IGNORECASE),
    re.compile(r"(\b(OR|AND)\b\s+\d+\s*=\s*\d+)", re.IGNORECASE),
    re.compile(r"(';\s*(DROP|DELETE|UPDATE|INSERT)\b)", re.IGNORECASE),
    re.compile(r"(\bSLEEP\s*\(\s*\d+\s*\))", re.IGNORECASE),
    re.compile(r"(\bBENCHMARK\s*\()", re.IGNORECASE),
    re.compile(r"(\bWAITFOR\s+DELAY\b)", re.IGNORECASE),
    re.compile(r"(CHAR\s*\(\s*\d+\s*\))", re.IGNORECASE),
    re.compile(r"(\bCONCAT\s*\()", re.IGNORECASE),
    re.compile(r"(0x[0-9a-fA-F]{8,})", re.IGNORECASE),
]

# Paths exempt from body inspection (e.g., file uploads)
_EXEMPT_PATHS: set[str] = {
    "/api/v1/screenshots/upload",
    "/api/v1/screenshots/confirm",
}


def _detect_sql_injection(value: str) -> bool:
    """Check a string for common SQL injection patterns.

    Args:
        value: The string to inspect.

    Returns:
        True if a suspicious pattern is detected; False otherwise.
    """
    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(value):
            return True
    return False


def _sanitize_header_value(value: str) -> str:
    """Sanitize a header value by removing control characters.

    Strips carriage returns and line feeds to prevent header injection.

    Args:
        value: The raw header value.

    Returns:
        The sanitized header value.
    """
    return value.replace("\r", "").replace("\n", "").strip()


async def _check_query_params(request: Request) -> str | None:
    """Inspect query parameters for SQL injection patterns.

    Args:
        request: The incoming HTTP request.

    Returns:
        The suspicious parameter name if detected; None otherwise.
    """
    for param_name, param_value in request.query_params.items():
        if _detect_sql_injection(param_value):
            return param_name
    return None


async def _check_body_content(request: Request) -> bool:
    """Inspect the request body for SQL injection patterns.

    Only inspects JSON text bodies; binary uploads are skipped.

    Args:
        request: The incoming HTTP request.

    Returns:
        True if a suspicious pattern is detected; False otherwise.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return False

    try:
        body = await request.body()
        if not body:
            return False

        body_str = body.decode("utf-8", errors="ignore")
        return _detect_sql_injection(body_str)
    except Exception:
        return False


class SecurityMiddleware(BaseHTTPMiddleware):
    """Starlette middleware for HTTP security hardening.

    Applies security headers to all responses, enforces request
    size limits, and scans inputs for SQL injection patterns.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request through security checks.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            The response with security headers, or a 400/413 error.
        """
        # 1. Request size limiting
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_SIZE_BYTES:
                    logger.warning(
                        "request_too_large",
                        content_length=size,
                        max_size=MAX_REQUEST_SIZE_BYTES,
                        path=request.url.path,
                    )
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={
                            "detail": f"Request body too large. Maximum size is {MAX_REQUEST_SIZE_BYTES // (1024 * 1024)}MB.",
                        },
                    )
            except ValueError:
                pass

        # 2. SQL injection detection in query parameters
        suspicious_param = await _check_query_params(request)
        if suspicious_param:
            logger.warning(
                "sql_injection_detected",
                source="query_param",
                param=suspicious_param,
                path=request.url.path,
                client_ip=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Potentially malicious input detected."},
            )

        # 3. SQL injection detection in request body (for non-exempt paths)
        if request.url.path not in _EXEMPT_PATHS and request.method in ("POST", "PUT", "PATCH"):
            if await _check_body_content(request):
                logger.warning(
                    "sql_injection_detected",
                    source="request_body",
                    path=request.url.path,
                    client_ip=request.client.host if request.client else "unknown",
                )
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Potentially malicious input detected."},
                )

        # 4. Process the request
        response = await call_next(request)

        # 5. Apply security headers
        for header_name, header_value in SECURITY_HEADERS.items():
            response.headers[header_name] = header_value

        # Remove server identification header
        if "Server" in response.headers:
            del response.headers["Server"]

        return response
