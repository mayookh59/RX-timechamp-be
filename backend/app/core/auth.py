"""Authentication and authorization dependencies for FastAPI.

Provides JWT token validation, device API-key authentication,
and role-based access control dependencies.
"""

import uuid

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.device import Device
from app.models.user import User
from app.storage.database import get_db

logger = structlog.stdlib.get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the current user from a JWT bearer token.

    Args:
        credentials: HTTP Bearer token credentials.
        db: Async database session.

    Returns:
        The authenticated User object.

    Raises:
        HTTPException: If the token is invalid, expired, or the user is not found.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
    except JWTError as exc:
        logger.warning("jwt_decode_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        ) from exc

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require the current user to have admin role.

    Args:
        current_user: The authenticated user.

    Returns:
        The authenticated admin User.

    Raises:
        HTTPException: If the user is not an admin.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_current_manager_or_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require the current user to have admin or manager role.

    Args:
        current_user: The authenticated user.

    Returns:
        The authenticated admin or manager User.

    Raises:
        HTTPException: If the user is not an admin or manager.
    """
    if current_user.role not in {"admin", "manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or manager access required",
        )
    return current_user


async def get_device_from_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Device:
    """Authenticate a desktop agent device via API key.

    Accepts the API key from either:
      - X-API-Key header (with X-Device-Id header)
      - Authorization: Bearer <key> header

    Args:
        request: The incoming HTTP request.
        db: Async database session.

    Returns:
        The authenticated Device object.

    Raises:
        HTTPException: If the device is not found or inactive.
    """
    from app.services.auth_service import verify_password

    # Try X-API-Key header first (agent sends this)
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    device_id = request.headers.get("X-Device-Id") or request.headers.get("x-device-id")

    # Fallback to Bearer token
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    # Scan all devices and verify API key via bcrypt
    result = await db.execute(select(Device))
    devices = result.scalars().all()
    logger.info("device_auth_attempt", num_devices=len(devices), api_key_prefix=api_key[:10])
    for device in devices:
        try:
            hash_val = device.api_key_hash
            if hash_val:
                matched = verify_password(api_key, hash_val)
                logger.info("device_verify", host=device.hostname, matched=matched, hash_prefix=hash_val[:15])
                if matched:
                    return device
        except HTTPException:
            raise
        except Exception as e:
            logger.error("device_verify_error", host=getattr(device, 'hostname', '?'), error=str(e))
            continue

    logger.warning("device_auth_failed", api_key_prefix=api_key[:8] if api_key else "none")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid device API key",
    )
