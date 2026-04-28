"""Screenshot archival service for cold storage lifecycle management.

Manages the lifecycle of screenshots by transitioning them from
standard S3 storage to Glacier for long-term retention, and
eventually deleting them after the configured retention period.
Also supports on-demand restoration from Glacier.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Default lifecycle thresholds (days)
DEFAULT_ARCHIVE_AFTER_DAYS = 30
DEFAULT_DELETE_AFTER_DAYS = 90
DEFAULT_BATCH_SIZE = 100
DEFAULT_RESTORE_DAYS = 7

# S3 storage classes
STORAGE_CLASS_STANDARD = "STANDARD"
STORAGE_CLASS_GLACIER = "GLACIER"


class ArchiveService:
    """Manages screenshot archival, deletion, and restoration.

    Coordinates between the application database (tracking screenshot
    metadata) and S3 (storing the actual image files) to move old
    screenshots through a defined lifecycle:

    1. Standard storage (0-30 days)
    2. Glacier archive (30-90 days)
    3. Permanent deletion (>90 days)
    """

    def __init__(
        self,
        session: AsyncSession,
        s3_bucket: str,
        s3_client: Optional[Any] = None,
        archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
        delete_after_days: int = DEFAULT_DELETE_AFTER_DAYS,
    ) -> None:
        """Initialize the archive service.

        Args:
            session: An async SQLAlchemy session.
            s3_bucket: The S3 bucket name for screenshots.
            s3_client: An optional pre-configured boto3 S3 client.
                If not provided, a new client is created.
            archive_after_days: Days after which to move to Glacier.
            delete_after_days: Days after which to permanently delete.
        """
        self._session = session
        self._s3_bucket = s3_bucket
        self._s3 = s3_client or boto3.client("s3")
        self._archive_after_days = archive_after_days
        self._delete_after_days = delete_after_days

    # -----------------------------------------------------------------
    # Archive old screenshots to Glacier
    # -----------------------------------------------------------------

    async def archive_old_screenshots(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Dict[str, Any]:
        """Move screenshots older than the archive threshold to Glacier.

        Queries for screenshots still in STANDARD storage that are
        older than archive_after_days, then issues S3 copy operations
        to change their storage class to GLACIER.

        Args:
            batch_size: Number of screenshots to process per batch.

        Returns:
            A dictionary with counts of archived, failed, and total
            screenshots processed.
        """
        cutoff_date = datetime.utcnow() - timedelta(
            days=self._archive_after_days
        )

        logger.info(
            "archive_start",
            extra={
                "cutoff_date": cutoff_date.isoformat(),
                "batch_size": batch_size,
            },
        )

        query = text("""
            SELECT id, s3_key
            FROM screenshots
            WHERE captured_at < :cutoff_date
              AND storage_class = :standard_class
              AND deleted_at IS NULL
            ORDER BY captured_at ASC
            LIMIT :batch_size
        """)

        result = await self._session.execute(
            query,
            {
                "cutoff_date": cutoff_date,
                "standard_class": STORAGE_CLASS_STANDARD,
                "batch_size": batch_size,
            },
        )
        rows = result.fetchall()

        stats: Dict[str, Any] = {
            "total": len(rows),
            "archived": 0,
            "failed": 0,
            "failed_ids": [],
        }

        for row in rows:
            try:
                self._s3.copy_object(
                    Bucket=self._s3_bucket,
                    Key=row.s3_key,
                    CopySource={"Bucket": self._s3_bucket, "Key": row.s3_key},
                    StorageClass=STORAGE_CLASS_GLACIER,
                    MetadataDirective="COPY",
                )

                await self._session.execute(
                    text("""
                        UPDATE screenshots
                        SET storage_class = :glacier_class,
                            archived_at = :now
                        WHERE id = :screenshot_id
                    """),
                    {
                        "glacier_class": STORAGE_CLASS_GLACIER,
                        "now": datetime.utcnow(),
                        "screenshot_id": row.id,
                    },
                )
                stats["archived"] += 1

            except ClientError:
                logger.exception(
                    "archive_screenshot_failed",
                    extra={"screenshot_id": str(row.id), "s3_key": row.s3_key},
                )
                stats["failed"] += 1
                stats["failed_ids"].append(str(row.id))

        await self._session.commit()

        logger.info("archive_complete", extra=stats)
        return stats

    # -----------------------------------------------------------------
    # Delete expired screenshots
    # -----------------------------------------------------------------

    async def delete_expired_screenshots(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Dict[str, Any]:
        """Permanently delete screenshots older than the retention period.

        Removes both the S3 object and marks the database record as
        deleted for screenshots past the delete_after_days threshold.

        Args:
            batch_size: Number of screenshots to process per batch.

        Returns:
            A dictionary with counts of deleted and failed screenshots.
        """
        cutoff_date = datetime.utcnow() - timedelta(
            days=self._delete_after_days
        )

        logger.info(
            "delete_expired_start",
            extra={
                "cutoff_date": cutoff_date.isoformat(),
                "batch_size": batch_size,
            },
        )

        query = text("""
            SELECT id, s3_key
            FROM screenshots
            WHERE captured_at < :cutoff_date
              AND deleted_at IS NULL
            ORDER BY captured_at ASC
            LIMIT :batch_size
        """)

        result = await self._session.execute(
            query,
            {"cutoff_date": cutoff_date, "batch_size": batch_size},
        )
        rows = result.fetchall()

        stats: Dict[str, Any] = {
            "total": len(rows),
            "deleted": 0,
            "failed": 0,
            "failed_ids": [],
        }

        # Batch delete from S3 (up to 1000 objects per request)
        objects_to_delete: List[Dict[str, str]] = []
        row_map: Dict[str, Any] = {}

        for row in rows:
            objects_to_delete.append({"Key": row.s3_key})
            row_map[row.s3_key] = row

        if objects_to_delete:
            try:
                response = self._s3.delete_objects(
                    Bucket=self._s3_bucket,
                    Delete={"Objects": objects_to_delete, "Quiet": False},
                )

                # Track successful deletions
                deleted_keys = {
                    d["Key"] for d in response.get("Deleted", [])
                }
                error_keys = {
                    e["Key"] for e in response.get("Errors", [])
                }

                for key in deleted_keys:
                    row = row_map[key]
                    await self._session.execute(
                        text("""
                            UPDATE screenshots
                            SET deleted_at = :now
                            WHERE id = :screenshot_id
                        """),
                        {
                            "now": datetime.utcnow(),
                            "screenshot_id": row.id,
                        },
                    )
                    stats["deleted"] += 1

                for key in error_keys:
                    row = row_map[key]
                    stats["failed"] += 1
                    stats["failed_ids"].append(str(row.id))
                    logger.error(
                        "s3_delete_failed",
                        extra={"screenshot_id": str(row.id), "s3_key": key},
                    )

            except ClientError:
                logger.exception("batch_delete_failed")
                stats["failed"] = len(objects_to_delete)

        await self._session.commit()

        logger.info("delete_expired_complete", extra=stats)
        return stats

    # -----------------------------------------------------------------
    # Restore from Glacier on demand
    # -----------------------------------------------------------------

    async def restore_screenshot(
        self,
        screenshot_id: UUID,
        restore_days: int = DEFAULT_RESTORE_DAYS,
        tier: str = "Standard",
    ) -> Dict[str, Any]:
        """Initiate restoration of an archived screenshot from Glacier.

        Sends a restore request to S3 for the specified screenshot.
        The restore is asynchronous; the object will be available
        after Glacier processing completes (typically a few hours
        for Standard tier).

        Args:
            screenshot_id: The UUID of the screenshot to restore.
            restore_days: Number of days to keep the restored copy.
            tier: Glacier retrieval tier (Expedited, Standard, Bulk).

        Returns:
            A dictionary with the restoration status.
        """
        logger.info(
            "restore_start",
            extra={
                "screenshot_id": str(screenshot_id),
                "restore_days": restore_days,
                "tier": tier,
            },
        )

        query = text("""
            SELECT id, s3_key, storage_class
            FROM screenshots
            WHERE id = :screenshot_id AND deleted_at IS NULL
        """)
        result = await self._session.execute(
            query, {"screenshot_id": screenshot_id}
        )
        row = result.fetchone()

        if row is None:
            logger.warning(
                "restore_not_found",
                extra={"screenshot_id": str(screenshot_id)},
            )
            return {"status": "not_found", "screenshot_id": str(screenshot_id)}

        if row.storage_class != STORAGE_CLASS_GLACIER:
            return {
                "status": "not_archived",
                "screenshot_id": str(screenshot_id),
                "storage_class": row.storage_class,
            }

        try:
            self._s3.restore_object(
                Bucket=self._s3_bucket,
                Key=row.s3_key,
                RestoreRequest={
                    "Days": restore_days,
                    "GlacierJobParameters": {"Tier": tier},
                },
            )

            await self._session.execute(
                text("""
                    UPDATE screenshots
                    SET restore_requested_at = :now
                    WHERE id = :screenshot_id
                """),
                {
                    "now": datetime.utcnow(),
                    "screenshot_id": screenshot_id,
                },
            )
            await self._session.commit()

            logger.info(
                "restore_initiated",
                extra={
                    "screenshot_id": str(screenshot_id),
                    "s3_key": row.s3_key,
                    "tier": tier,
                },
            )

            return {
                "status": "restore_initiated",
                "screenshot_id": str(screenshot_id),
                "tier": tier,
                "restore_days": restore_days,
            }

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "RestoreAlreadyInProgress":
                logger.info(
                    "restore_already_in_progress",
                    extra={"screenshot_id": str(screenshot_id)},
                )
                return {
                    "status": "already_in_progress",
                    "screenshot_id": str(screenshot_id),
                }

            logger.exception(
                "restore_failed",
                extra={"screenshot_id": str(screenshot_id)},
            )
            return {
                "status": "error",
                "screenshot_id": str(screenshot_id),
                "error": str(e),
            }
