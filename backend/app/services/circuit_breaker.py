"""Circuit breaker for S3 operations with local storage fallback.

Implements the circuit breaker pattern to protect the application
from cascading failures when S3 is unavailable. When the circuit
opens, uploads fall back to local disk storage for later replay.

States:
    - CLOSED: Normal operation, requests pass through to S3.
    - OPEN: S3 is failing, requests are routed to local fallback.
    - HALF_OPEN: Testing if S3 has recovered with a single probe request.
"""

import os
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

import boto3
import structlog
from botocore.config import Config as BotoConfig

from app.core.config import settings

logger = structlog.stdlib.get_logger(__name__)

# Circuit breaker configuration
FAIL_MAX = 5
RESET_TIMEOUT_SECONDS = 60

# Local fallback storage directory
LOCAL_FALLBACK_DIR = Path("/tmp/trackme-s3-fallback")


class CircuitState(str, Enum):
    """Circuit breaker state enumeration."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when the circuit breaker is open and the request cannot proceed."""

    pass


class S3CircuitBreaker:
    """Circuit breaker wrapping S3 upload operations.

    Tracks consecutive failures and opens the circuit when the failure
    threshold is exceeded. While open, requests are routed to local
    fallback storage. After the reset timeout, allows a single probe
    request through (half-open state) to test recovery.

    Attributes:
        state: Current circuit state.
        failure_count: Number of consecutive failures.
        last_failure_time: Timestamp of the most recent failure.
        success_count: Number of successful operations since last reset.
    """

    def __init__(
        self,
        fail_max: int = FAIL_MAX,
        reset_timeout: int = RESET_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the circuit breaker.

        Args:
            fail_max: Number of consecutive failures before opening.
            reset_timeout: Seconds to wait before transitioning to half-open.
        """
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._last_success_time: float | None = None
        self._lock = Lock()

        # Ensure fallback directory exists
        LOCAL_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def state(self) -> CircuitState:
        """Get the current circuit state, checking for timeout transition.

        Returns:
            Current CircuitState value.
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                if (
                    self._last_failure_time is not None
                    and time.time() - self._last_failure_time >= self._reset_timeout
                ):
                    self._state = CircuitState.HALF_OPEN
                    logger.info(
                        "circuit_breaker_half_open",
                        timeout=self._reset_timeout,
                    )
            return self._state

    @property
    def failure_count(self) -> int:
        """Get the current consecutive failure count."""
        return self._failure_count

    @property
    def success_count(self) -> int:
        """Get the success count since last state change."""
        return self._success_count

    def get_status(self) -> dict[str, Any]:
        """Get the full circuit breaker status as a dictionary.

        Returns:
            Dict with state, failure_count, success_count, config,
            and timing information.
        """
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "fail_max": self._fail_max,
            "reset_timeout_seconds": self._reset_timeout,
            "last_failure_time": (
                datetime.fromtimestamp(self._last_failure_time, tz=timezone.utc).isoformat()
                if self._last_failure_time
                else None
            ),
            "last_success_time": (
                datetime.fromtimestamp(self._last_success_time, tz=timezone.utc).isoformat()
                if self._last_success_time
                else None
            ),
            "fallback_dir": str(LOCAL_FALLBACK_DIR),
        }

    def _record_success(self) -> None:
        """Record a successful operation, resetting to closed state."""
        with self._lock:
            self._failure_count = 0
            self._success_count += 1
            self._last_success_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("circuit_breaker_closed", reason="probe_success")

    def _record_failure(self) -> None:
        """Record a failed operation, potentially opening the circuit."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._success_count = 0

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(
                    "circuit_breaker_open",
                    reason="probe_failed",
                    failure_count=self._failure_count,
                )
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self._fail_max
            ):
                self._state = CircuitState.OPEN
                logger.warning(
                    "circuit_breaker_open",
                    reason="threshold_exceeded",
                    failure_count=self._failure_count,
                    fail_max=self._fail_max,
                )

    async def upload_file(
        self,
        storage_key: str,
        file_data: bytes,
        content_type: str = "image/png",
    ) -> dict[str, Any]:
        """Upload a file to S3 with circuit breaker protection.

        When the circuit is closed or half-open, attempts to upload to S3.
        When open, writes to local fallback storage instead.

        Args:
            storage_key: The S3 object key to upload to.
            file_data: Raw file bytes to upload.
            content_type: MIME type of the file.

        Returns:
            Dict with storage_location ("s3" or "local"), key, and size.
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            return await self._fallback_to_local(storage_key, file_data)

        try:
            s3_client = _get_s3_client()
            s3_client.put_object(
                Bucket=settings.S3_BUCKET,
                Key=storage_key,
                Body=file_data,
                ContentType=content_type,
            )

            self._record_success()

            logger.info(
                "s3_upload_success",
                storage_key=storage_key,
                size=len(file_data),
                circuit_state=current_state.value,
            )

            return {
                "storage_location": "s3",
                "key": storage_key,
                "size": len(file_data),
            }

        except Exception as exc:
            self._record_failure()

            logger.error(
                "s3_upload_failed",
                storage_key=storage_key,
                error=str(exc),
                failure_count=self._failure_count,
                circuit_state=self.state.value,
            )

            # Fall back to local storage
            return await self._fallback_to_local(storage_key, file_data)

    async def _fallback_to_local(
        self,
        storage_key: str,
        file_data: bytes,
    ) -> dict[str, Any]:
        """Write file to local fallback storage when S3 is unavailable.

        Args:
            storage_key: The intended S3 key (used as subdirectory path).
            file_data: Raw file bytes to write.

        Returns:
            Dict with storage_location "local", local path, and size.
        """
        safe_key = storage_key.replace("/", os.sep)
        local_path = LOCAL_FALLBACK_DIR / safe_key

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(file_data)

        logger.info(
            "s3_fallback_local",
            storage_key=storage_key,
            local_path=str(local_path),
            size=len(file_data),
        )

        return {
            "storage_location": "local",
            "key": storage_key,
            "local_path": str(local_path),
            "size": len(file_data),
        }

    async def replay_local_files(self) -> dict[str, int]:
        """Replay locally stored files back to S3 after recovery.

        Only runs when the circuit is closed. Scans the fallback
        directory and uploads each file to S3, deleting the local
        copy on success.

        Returns:
            Dict with uploaded and failed counts.
        """
        if self.state != CircuitState.CLOSED:
            logger.info(
                "replay_skipped_circuit_not_closed",
                state=self.state.value,
            )
            return {"uploaded": 0, "failed": 0}

        results: dict[str, int] = {"uploaded": 0, "failed": 0}

        if not LOCAL_FALLBACK_DIR.exists():
            return results

        s3_client = _get_s3_client()

        for file_path in LOCAL_FALLBACK_DIR.rglob("*"):
            if not file_path.is_file():
                continue

            # Reconstruct S3 key from relative path
            relative = file_path.relative_to(LOCAL_FALLBACK_DIR)
            s3_key = str(relative).replace(os.sep, "/")

            try:
                file_data = file_path.read_bytes()
                s3_client.put_object(
                    Bucket=settings.S3_BUCKET,
                    Key=s3_key,
                    Body=file_data,
                )
                file_path.unlink()
                results["uploaded"] += 1

                logger.info(
                    "replay_uploaded",
                    s3_key=s3_key,
                    size=len(file_data),
                )
            except Exception as exc:
                results["failed"] += 1
                logger.error(
                    "replay_upload_failed",
                    s3_key=s3_key,
                    error=str(exc),
                )

        logger.info("replay_completed", results=results)
        return results


def _get_s3_client():
    """Create a configured boto3 S3 client.

    Returns:
        A boto3 S3 client configured with application settings.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 1},
        ),
    )


# Global singleton instance
s3_circuit_breaker = S3CircuitBreaker()
