"""API key management service with zero-downtime rotation.

Provides functions for generating, rotating, revoking, and validating
API keys. Rotation supports a 24-hour grace period where both the old
and new keys are accepted, allowing agents to transition seamlessly.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey

logger = structlog.stdlib.get_logger(__name__)

# Key generation parameters
KEY_LENGTH_BYTES = 32
KEY_PREFIX_LENGTH = 10

# Grace period for key rotation (both old and new valid)
ROTATION_GRACE_HOURS = 24

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)


def _generate_raw_key() -> str:
    """Generate a cryptographically secure API key string.

    Returns:
        A URL-safe random string suitable for use as an API key.
    """
    return secrets.token_urlsafe(KEY_LENGTH_BYTES)


async def generate_key(
    device_id: uuid.UUID,
    db: AsyncSession,
    expires_in_days: int | None = None,
) -> dict[str, str]:
    """Create a new API key for a device.

    Generates a random key, hashes it with bcrypt, stores the hash
    and prefix, and returns the plaintext key (shown only once).

    Args:
        device_id: UUID of the device to create the key for.
        db: Async database session.
        expires_in_days: Optional number of days until key expiration.

    Returns:
        Dict with key_id, raw_key (plaintext), key_prefix, and expires_at.
    """
    raw_key = _generate_raw_key()
    key_hash = pwd_context.hash(raw_key)
    key_prefix = raw_key[:KEY_PREFIX_LENGTH]

    expires_at: datetime | None = None
    if expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    api_key = ApiKey(
        id=uuid.uuid4(),
        device_id=device_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        status="active",
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()

    logger.info(
        "api_key_generated",
        key_id=str(api_key.id),
        device_id=str(device_id),
        prefix=key_prefix,
        expires_at=expires_at.isoformat() if expires_at else None,
    )

    return {
        "key_id": str(api_key.id),
        "raw_key": raw_key,
        "key_prefix": key_prefix,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


async def rotate_key(
    device_id: uuid.UUID,
    db: AsyncSession,
) -> dict[str, str]:
    """Rotate the API key for a device with a zero-downtime grace period.

    Creates a new key and marks the current active key as 'rotating'.
    Both keys remain valid during the grace period. After the grace
    period, the old key should be revoked via a scheduled task.

    Args:
        device_id: UUID of the device whose key should be rotated.
        db: Async database session.

    Returns:
        Dict with new_key_id, raw_key (plaintext), key_prefix,
        old_key_id, and grace_period_expires_at.

    Raises:
        ValueError: If no active key exists for the device.
    """
    # Find current active key
    stmt = (
        select(ApiKey)
        .where(
            ApiKey.device_id == device_id,
            ApiKey.status == "active",
        )
        .order_by(ApiKey.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    old_key = result.scalar_one_or_none()

    if old_key is None:
        raise ValueError(f"No active key found for device {device_id}")

    # Mark old key as rotating
    old_key.status = "rotating"
    old_key.expires_at = datetime.now(timezone.utc) + timedelta(hours=ROTATION_GRACE_HOURS)

    # Generate new key
    raw_key = _generate_raw_key()
    key_hash = pwd_context.hash(raw_key)
    key_prefix = raw_key[:KEY_PREFIX_LENGTH]

    new_key = ApiKey(
        id=uuid.uuid4(),
        device_id=device_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        status="active",
        rotated_from=old_key.id,
    )
    db.add(new_key)
    await db.flush()

    grace_expires = old_key.expires_at

    logger.info(
        "api_key_rotated",
        device_id=str(device_id),
        old_key_id=str(old_key.id),
        new_key_id=str(new_key.id),
        new_prefix=key_prefix,
        grace_period_expires=grace_expires.isoformat() if grace_expires else None,
    )

    return {
        "new_key_id": str(new_key.id),
        "raw_key": raw_key,
        "key_prefix": key_prefix,
        "old_key_id": str(old_key.id),
        "grace_period_expires_at": grace_expires.isoformat() if grace_expires else None,
    }


async def revoke_key(
    key_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """Revoke an API key by ID.

    Sets the key status to 'revoked', preventing any further use.

    Args:
        key_id: UUID of the API key to revoke.
        db: Async database session.

    Returns:
        True if the key was found and revoked; False if not found.
    """
    stmt = select(ApiKey).where(ApiKey.id == key_id)
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if api_key is None:
        logger.warning("api_key_revoke_not_found", key_id=str(key_id))
        return False

    api_key.status = "revoked"
    await db.flush()

    logger.info(
        "api_key_revoked",
        key_id=str(key_id),
        device_id=str(api_key.device_id),
        prefix=api_key.key_prefix,
    )

    return True


async def validate_key(
    key_string: str,
    db: AsyncSession,
) -> ApiKey | None:
    """Validate an API key string against stored hashes.

    Looks up candidate keys by prefix, then verifies the full hash.
    Accepts keys with status 'active' or 'rotating' (within grace period).
    Updates the last_used_at timestamp on successful validation.

    Args:
        key_string: The plaintext API key to validate.
        db: Async database session.

    Returns:
        The matching ApiKey object if valid; None otherwise.
    """
    prefix = key_string[:KEY_PREFIX_LENGTH]

    # Find candidate keys by prefix with acceptable statuses
    stmt = (
        select(ApiKey)
        .where(
            ApiKey.key_prefix == prefix,
            ApiKey.status.in_(["active", "rotating"]),
        )
    )
    result = await db.execute(stmt)
    candidates = list(result.scalars().all())

    for candidate in candidates:
        # Check expiration for rotating keys
        if candidate.status == "rotating" and candidate.expires_at:
            if datetime.now(timezone.utc) > candidate.expires_at:
                # Grace period expired, revoke it
                candidate.status = "revoked"
                await db.flush()
                logger.info(
                    "api_key_grace_period_expired",
                    key_id=str(candidate.id),
                    device_id=str(candidate.device_id),
                )
                continue

        # Check expiration for active keys
        if candidate.status == "active" and candidate.expires_at:
            if datetime.now(timezone.utc) > candidate.expires_at:
                candidate.status = "revoked"
                await db.flush()
                continue

        # Verify hash
        if pwd_context.verify(key_string, candidate.key_hash):
            candidate.last_used_at = datetime.now(timezone.utc)
            await db.flush()

            logger.debug(
                "api_key_validated",
                key_id=str(candidate.id),
                device_id=str(candidate.device_id),
                status=candidate.status,
            )
            return candidate

    logger.warning("api_key_validation_failed", prefix=prefix)
    return None


async def cleanup_expired_rotating_keys(db: AsyncSession) -> int:
    """Revoke rotating keys whose grace period has expired.

    Should be called periodically to clean up keys that have
    completed their rotation grace period.

    Args:
        db: Async database session.

    Returns:
        Number of keys revoked.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        update(ApiKey)
        .where(
            ApiKey.status == "rotating",
            ApiKey.expires_at < now,
        )
        .values(status="revoked")
    )
    result = await db.execute(stmt)
    count = result.rowcount or 0

    if count > 0:
        logger.info("expired_rotating_keys_revoked", count=count)

    return count
