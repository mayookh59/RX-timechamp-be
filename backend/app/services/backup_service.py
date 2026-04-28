"""Database backup service for scheduled PostgreSQL dumps to S3.

Creates gzip-compressed pg_dump backups and uploads them to S3 storage.
Supports automatic cleanup of backups older than a configurable retention
period. Designed for daily execution (1 AM UTC).
"""

import asyncio
import gzip
import os
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import boto3
import structlog
from botocore.config import Config as BotoConfig

from app.core.config import settings

logger = structlog.stdlib.get_logger(__name__)

# S3 prefix for backup files
BACKUP_S3_PREFIX = "backups/database/"

# Default retention for old backups
DEFAULT_RETAIN_DAYS = 30


def _get_s3_client():
    """Create a configured boto3 S3 client for backup storage.

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
            connect_timeout=10,
            read_timeout=300,
            retries={"max_attempts": 3},
        ),
    )


def _parse_database_url() -> dict[str, str]:
    """Parse the DATABASE_URL into pg_dump connection parameters.

    Extracts host, port, database name, user, and password from the
    SQLAlchemy-style connection string.

    Returns:
        Dict with host, port, dbname, user, and password keys.
    """
    # Strip the asyncpg driver prefix for URL parsing
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(url)

    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": (parsed.path or "/trackme").lstrip("/"),
        "user": parsed.username or "trackme",
        "password": parsed.password or "",
    }


async def create_backup() -> dict:
    """Create a gzip-compressed PostgreSQL backup and upload to S3.

    Runs pg_dump as a subprocess, compresses the output with gzip,
    and uploads the result to the configured S3 bucket.

    Returns:
        Dict with backup metadata: s3_key, size_bytes, duration_seconds,
        and timestamp.

    Raises:
        RuntimeError: If pg_dump exits with a non-zero code.
        Exception: If S3 upload fails.
    """
    start_time = datetime.now(timezone.utc)
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    s3_key = f"{BACKUP_S3_PREFIX}trackme_{timestamp}.sql.gz"

    db_params = _parse_database_url()

    logger.info(
        "backup_started",
        timestamp=timestamp,
        s3_key=s3_key,
        database=db_params["dbname"],
    )

    # Create temporary file for the compressed dump
    with tempfile.NamedTemporaryFile(
        suffix=".sql.gz",
        delete=False,
    ) as tmp_file:
        tmp_path = tmp_file.name

    try:
        # Run pg_dump and capture output
        env = os.environ.copy()
        env["PGPASSWORD"] = db_params["password"]

        pg_dump_cmd = [
            "pg_dump",
            "-h", db_params["host"],
            "-p", db_params["port"],
            "-U", db_params["user"],
            "-d", db_params["dbname"],
            "--no-owner",
            "--no-acl",
            "--format=plain",
        ]

        logger.debug("backup_running_pg_dump", command=pg_dump_cmd[0])

        process = await asyncio.create_subprocess_exec(
            *pg_dump_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            logger.error(
                "backup_pg_dump_failed",
                returncode=process.returncode,
                stderr=error_msg,
            )
            raise RuntimeError(f"pg_dump failed with code {process.returncode}: {error_msg}")

        # Compress with gzip
        with gzip.open(tmp_path, "wb", compresslevel=6) as gz_file:
            gz_file.write(stdout)

        file_size = os.path.getsize(tmp_path)

        logger.info(
            "backup_compressed",
            raw_size=len(stdout),
            compressed_size=file_size,
        )

        # Upload to S3
        s3_client = _get_s3_client()
        s3_client.upload_file(
            tmp_path,
            settings.S3_BUCKET,
            s3_key,
            ExtraArgs={
                "ContentType": "application/gzip",
                "Metadata": {
                    "backup-timestamp": timestamp,
                    "database": db_params["dbname"],
                },
            },
        )

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        result = {
            "s3_key": s3_key,
            "size_bytes": file_size,
            "duration_seconds": elapsed,
            "timestamp": start_time.isoformat(),
        }

        logger.info(
            "backup_completed",
            s3_key=s3_key,
            size_bytes=file_size,
            duration_seconds=elapsed,
        )

        return result

    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def cleanup_old_backups(retain_days: int = DEFAULT_RETAIN_DAYS) -> dict:
    """Delete backup files from S3 older than the retention period.

    Lists objects under the backup prefix and deletes those whose
    LastModified timestamp exceeds the retention window.

    Args:
        retain_days: Number of days to keep backups. Defaults to 30.

    Returns:
        Dict with deleted_count and errors list.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    results: dict = {
        "deleted_count": 0,
        "errors": [],
    }

    logger.info(
        "backup_cleanup_started",
        cutoff=cutoff.isoformat(),
        retain_days=retain_days,
    )

    s3_client = _get_s3_client()

    try:
        # List all backup objects
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=settings.S3_BUCKET,
            Prefix=BACKUP_S3_PREFIX,
        )

        objects_to_delete: list[str] = []

        for page in pages:
            for obj in page.get("Contents", []):
                last_modified = obj["LastModified"]
                # Ensure timezone awareness
                if last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=timezone.utc)

                if last_modified < cutoff:
                    objects_to_delete.append(obj["Key"])

        if not objects_to_delete:
            logger.info("backup_cleanup_nothing_to_delete")
            return results

        # Delete in batches of 1000 (S3 limit)
        batch_size = 1000
        for i in range(0, len(objects_to_delete), batch_size):
            batch = objects_to_delete[i : i + batch_size]
            try:
                delete_request = {
                    "Objects": [{"Key": key} for key in batch],
                    "Quiet": True,
                }
                s3_client.delete_objects(
                    Bucket=settings.S3_BUCKET,
                    Delete=delete_request,
                )
                results["deleted_count"] += len(batch)
            except Exception as exc:
                error_msg = f"Batch delete failed at offset {i}: {exc}"
                results["errors"].append(error_msg)
                logger.error("backup_cleanup_batch_failed", error=str(exc))

    except Exception as exc:
        error_msg = f"Backup cleanup failed: {exc}"
        results["errors"].append(error_msg)
        logger.error("backup_cleanup_failed", error=str(exc))

    logger.info(
        "backup_cleanup_completed",
        deleted_count=results["deleted_count"],
        errors=len(results["errors"]),
    )

    return results
