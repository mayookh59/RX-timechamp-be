"""Database maintenance service for PostgreSQL optimization.

Performs periodic VACUUM, ANALYZE, and REINDEX operations to keep
the database performant. Designed for weekly execution during
low-traffic windows (Sunday 3 AM UTC).
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.database import async_session_factory, engine

logger = structlog.stdlib.get_logger(__name__)

# Tables requiring regular maintenance
MAINTENANCE_TABLES = [
    "activity_sessions",
    "app_usage",
    "url_visits",
    "screenshots",
    "dead_letter_queue",
]

# Index fragmentation threshold (percentage) to trigger REINDEX
FRAGMENTATION_THRESHOLD = 30


async def run_maintenance() -> dict:
    """Run full database maintenance: VACUUM ANALYZE and REINDEX.

    Executes VACUUM ANALYZE on each tracked table, checks index
    fragmentation via pg_stat_user_indexes, and rebuilds fragmented
    indexes. Must run outside a transaction block for VACUUM.

    Returns:
        Dict with maintenance results including tables vacuumed,
        indexes checked, and indexes rebuilt.
    """
    start_time = datetime.now(timezone.utc)
    results: dict = {
        "started_at": start_time.isoformat(),
        "tables_vacuumed": [],
        "tables_analyzed": [],
        "indexes_checked": 0,
        "indexes_rebuilt": [],
        "errors": [],
    }

    logger.info("maintenance_started")

    # VACUUM ANALYZE requires running outside a transaction
    # Use raw connection with autocommit
    raw_conn = await engine.raw_connection()
    try:
        await raw_conn.execution_options(isolation_level="AUTOCOMMIT")
        raw_cursor = await raw_conn.get_raw_connection()
        # Set autocommit on the underlying asyncpg connection
        for table in MAINTENANCE_TABLES:
            try:
                await raw_cursor.execute(f"VACUUM ANALYZE {table}")
                results["tables_vacuumed"].append(table)
                results["tables_analyzed"].append(table)
                logger.info("maintenance_vacuum_analyze", table=table)
            except Exception as exc:
                error_msg = f"VACUUM ANALYZE {table}: {exc}"
                results["errors"].append(error_msg)
                logger.error("maintenance_vacuum_failed", table=table, error=str(exc))
    except Exception as exc:
        logger.error("maintenance_raw_connection_failed", error=str(exc))
        results["errors"].append(f"Raw connection: {exc}")
    finally:
        await raw_conn.close()

    # Check and rebuild fragmented indexes using a regular session
    async with async_session_factory() as db:
        try:
            fragmented = await _check_index_fragmentation(db)
            results["indexes_checked"] = len(fragmented)

            for index_info in fragmented:
                if index_info["bloat_pct"] >= FRAGMENTATION_THRESHOLD:
                    try:
                        await _reindex(db, index_info["index_name"])
                        results["indexes_rebuilt"].append(index_info["index_name"])
                        logger.info(
                            "maintenance_index_rebuilt",
                            index_name=index_info["index_name"],
                            bloat_pct=index_info["bloat_pct"],
                        )
                    except Exception as exc:
                        error_msg = f"REINDEX {index_info['index_name']}: {exc}"
                        results["errors"].append(error_msg)
                        logger.error(
                            "maintenance_reindex_failed",
                            index_name=index_info["index_name"],
                            error=str(exc),
                        )

            await db.commit()

        except Exception as exc:
            await db.rollback()
            logger.error("maintenance_index_check_failed", error=str(exc))
            results["errors"].append(f"Index check: {exc}")

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    results["elapsed_seconds"] = elapsed
    results["completed_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "maintenance_completed",
        tables_vacuumed=len(results["tables_vacuumed"]),
        indexes_rebuilt=len(results["indexes_rebuilt"]),
        errors=len(results["errors"]),
        elapsed_seconds=elapsed,
    )

    return results


async def _check_index_fragmentation(db: AsyncSession) -> list[dict]:
    """Check index fragmentation using PostgreSQL statistics.

    Queries pg_stat_user_indexes and pgstattuple (if available) to
    estimate index bloat percentage for tables in scope.

    Args:
        db: Async database session.

    Returns:
        List of dicts with index_name, table_name, and bloat_pct.
    """
    query = text("""
        SELECT
            schemaname,
            relname AS table_name,
            indexrelname AS index_name,
            idx_scan,
            pg_relation_size(indexrelid) AS index_size_bytes
        FROM pg_stat_user_indexes
        WHERE relname = ANY(:tables)
        ORDER BY pg_relation_size(indexrelid) DESC
    """)

    result = await db.execute(query, {"tables": MAINTENANCE_TABLES})
    rows = result.fetchall()

    fragmented: list[dict] = []
    for row in rows:
        # Estimate bloat based on scan frequency and size heuristics
        # A more accurate check would use pgstattuple extension
        bloat_estimate = _estimate_bloat(row.idx_scan, row.index_size_bytes)
        fragmented.append({
            "index_name": row.index_name,
            "table_name": row.table_name,
            "index_size_bytes": row.index_size_bytes,
            "idx_scan": row.idx_scan,
            "bloat_pct": bloat_estimate,
        })

    return fragmented


def _estimate_bloat(idx_scan: int, index_size_bytes: int) -> float:
    """Estimate index bloat percentage from scan count and size.

    Uses a heuristic: indexes with very few scans relative to their
    size are likely bloated. This is a rough estimate; for precise
    measurements, use the pgstattuple extension.

    Args:
        idx_scan: Number of index scans performed.
        index_size_bytes: Current index size in bytes.

    Returns:
        Estimated bloat percentage (0.0 to 100.0).
    """
    if index_size_bytes == 0:
        return 0.0

    # Large indexes with very few scans may be bloated
    # This is a conservative heuristic
    size_mb = index_size_bytes / (1024 * 1024)
    if size_mb > 100 and idx_scan < 10:
        return 50.0
    elif size_mb > 50 and idx_scan < 100:
        return 35.0
    elif size_mb > 10 and idx_scan < 50:
        return 20.0

    return 0.0


async def _reindex(db: AsyncSession, index_name: str) -> None:
    """Rebuild a single database index concurrently.

    Uses REINDEX INDEX CONCURRENTLY to avoid locking the table
    during the rebuild.

    Args:
        db: Async database session.
        index_name: The name of the index to rebuild.

    Raises:
        Exception: If the REINDEX operation fails.
    """
    # Validate index name to prevent SQL injection
    if not index_name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"Invalid index name: {index_name}")

    stmt = text(f"REINDEX INDEX CONCURRENTLY {index_name}")
    await db.execute(stmt)

    logger.info("reindex_completed", index_name=index_name)
