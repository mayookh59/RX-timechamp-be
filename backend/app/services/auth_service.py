"""Authentication service for JWT token management and password hashing.

Provides functions for creating and verifying JWT tokens using python-jose,
and password hashing/verification using passlib with bcrypt.
"""

from datetime import datetime, timedelta, timezone

import structlog
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User

logger = structlog.stdlib.get_logger(__name__)

# Token type identifiers embedded in JWT claims
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.

    Args:
        plain_password: The plaintext password to check.
        hashed_password: The bcrypt hash to verify against.

    Returns:
        True if the password matches the hash; False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a plaintext password using bcrypt.

    Args:
        password: The plaintext password to hash.

    Returns:
        The bcrypt-hashed password string.
    """
    return pwd_context.hash(password)


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a short-lived JWT access token.

    Args:
        data: Claims to encode in the token (must include 'sub').
        expires_delta: Optional custom expiration duration. Defaults to
            the configured ACCESS_TOKEN_EXPIRE_MINUTES.

    Returns:
        The encoded JWT access token string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "type": ACCESS_TOKEN_TYPE,
        "iat": datetime.now(timezone.utc),
    })

    token = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    logger.debug(
        "access_token_created",
        sub=data.get("sub"),
        expires_at=expire.isoformat(),
    )
    return token


def create_refresh_token(data: dict) -> str:
    """Create a long-lived JWT refresh token.

    Args:
        data: Claims to encode in the token (must include 'sub').

    Returns:
        The encoded JWT refresh token string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
    )
    to_encode.update({
        "exp": expire,
        "type": REFRESH_TOKEN_TYPE,
        "iat": datetime.now(timezone.utc),
    })

    token = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    logger.debug(
        "refresh_token_created",
        sub=data.get("sub"),
        expires_at=expire.isoformat(),
    )
    return token


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token.

    Args:
        token: The JWT token string to decode.

    Returns:
        The decoded token claims dictionary, or None if invalid.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except JWTError as exc:
        logger.warning("token_decode_failed", error=str(exc))
        return None


def validate_token_type(payload: dict, expected_type: str) -> bool:
    """Validate that a decoded token has the expected type claim.

    Args:
        payload: Decoded JWT claims dictionary.
        expected_type: Expected token type ('access' or 'refresh').

    Returns:
        True if the token type matches; False otherwise.
    """
    token_type = payload.get("type")
    if token_type != expected_type:
        logger.warning(
            "token_type_mismatch",
            expected=expected_type,
            actual=token_type,
        )
        return False
    return True


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User | None:
    """Authenticate a user by email and password.

    Queries the database for the user, verifies the password,
    and checks that the account is active.

    Args:
        db: Async database session.
        email: User's email address.
        password: Plaintext password to verify.

    Returns:
        The authenticated User object, or None if authentication fails.
    """
    logger.info("authenticate_user_attempt", email=email)

    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        logger.warning("authenticate_user_not_found", email=email)
        return None

    if not user.is_active:
        logger.warning("authenticate_user_inactive", email=email)
        return None

    if not verify_password(password, user.password_hash):
        logger.warning("authenticate_user_invalid_password", email=email)
        return None

    logger.info("authenticate_user_success", email=email, user_id=str(user.id))
    return user
