"""Deduplication service for activity data ingestion.

Provides idempotent upsert operations using client-generated IDs
to prevent duplicate records during data ingestion. Supports both
single-record and batch deduplication workflows.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class DedupService:
    """Handles deduplication of ingested activity data.

    Uses PostgreSQL ON CONFLICT clauses to ensure idempotent inserts
    keyed on client_id, preventing duplicates from retries or
    concurrent submissions.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the deduplication service.

        Args:
            session: An async SQLAlchemy session for database operations.
        """
        self._session = session

    async def upsert_activity(
        self,
        table_name: str,
        data: Dict[str, Any],
        conflict_column: str = "client_id",
    ) -> Tuple[bool, Optional[UUID]]:
        """Perform an idempotent upsert for a single activity record.

        Inserts the record if no conflict exists on the specified column;
        otherwise the conflicting row is left unchanged (DO NOTHING).

        Args:
            table_name: The target database table name.
            data: A dictionary of column-value pairs to insert.
            conflict_column: The column to check for conflicts.

        Returns:
            A tuple of (was_inserted, record_id). was_inserted is True
            if a new row was created, False if it was a duplicate.
        """
        try:
            stmt = pg_insert(text(table_name)).values(**data)
            stmt = stmt.on_conflict_do_nothing(index_elements=[conflict_column])
            stmt = stmt.returning(text("id"))

            result = await self._session.execute(stmt)
            row = result.fetchone()

            if row is not None:
                logger.debug(
                    "record_inserted",
                    extra={
                        "table": table_name,
                        "client_id": data.get(conflict_column),
                    },
                )
                return True, row[0]

            logger.debug(
                "duplicate_skipped",
                extra={
                    "table": table_name,
                    "client_id": data.get(conflict_column),
                },
            )
            return False, None

        except Exception:
            logger.exception(
                "upsert_failed",
                extra={
                    "table": table_name,
                    "client_id": data.get(conflict_column),
                },
            )
            raise

    async def batch_upsert(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        conflict_column: str = "client_id",
        batch_size: int = 500,
    ) -> Dict[str, int]:
        """Perform a batch idempotent upsert for multiple records.

        Processes records in chunks to avoid excessively large SQL
        statements. Each chunk is inserted with ON CONFLICT DO NOTHING.

        Args:
            table_name: The target database table name.
            records: A list of dictionaries, each representing a row.
            conflict_column: The column to check for conflicts.
            batch_size: Number of records to process per batch.

        Returns:
            A dictionary with counts: inserted, duplicates, total, errors.
        """
        stats: Dict[str, int] = {
            "inserted": 0,
            "duplicates": 0,
            "total": len(records),
            "errors": 0,
        }

        if not records:
            logger.info("batch_upsert_empty", extra={"table": table_name})
            return stats

        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            batch_num = (i // batch_size) + 1

            try:
                stmt = pg_insert(text(table_name)).values(batch)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[conflict_column]
                )

                result = await self._session.execute(stmt)
                inserted_count = result.rowcount

                batch_duplicates = len(batch) - inserted_count
                stats["inserted"] += inserted_count
                stats["duplicates"] += batch_duplicates

                logger.info(
                    "batch_upsert_chunk",
                    extra={
                        "table": table_name,
                        "batch_num": batch_num,
                        "batch_size": len(batch),
                        "inserted": inserted_count,
                        "duplicates": batch_duplicates,
                    },
                )

            except Exception:
                stats["errors"] += len(batch)
                logger.exception(
                    "batch_upsert_chunk_failed",
                    extra={
                        "table": table_name,
                        "batch_num": batch_num,
                        "batch_size": len(batch),
                    },
                )
                raise

        await self._session.commit()

        logger.info(
            "batch_upsert_complete",
            extra={
                "table": table_name,
                "total": stats["total"],
                "inserted": stats["inserted"],
                "duplicates": stats["duplicates"],
                "errors": stats["errors"],
            },
        )

        return stats

    async def check_exists(
        self,
        table_name: str,
        client_id: str,
        conflict_column: str = "client_id",
    ) -> bool:
        """Check whether a record with the given client_id already exists.

        Args:
            table_name: The target database table name.
            client_id: The client-generated identifier to look up.
            conflict_column: The column containing the client ID.

        Returns:
            True if a record with the given client_id exists.
        """
        query = text(
            f"SELECT EXISTS(SELECT 1 FROM {table_name} WHERE {conflict_column} = :cid)"
        )
        result = await self._session.execute(query, {"cid": client_id})
        return result.scalar() or False

    async def get_duplicate_count(
        self,
        table_name: str,
        conflict_column: str = "client_id",
    ) -> int:
        """Count the number of duplicate client_id values in a table.

        Args:
            table_name: The target database table name.
            conflict_column: The column to check for duplicates.

        Returns:
            The number of client_id values that appear more than once.
        """
        query = text(
            f"""
            SELECT COUNT(*) FROM (
                SELECT {conflict_column}
                FROM {table_name}
                GROUP BY {conflict_column}
                HAVING COUNT(*) > 1
            ) AS dupes
            """
        )
        result = await self._session.execute(query)
        count = result.scalar() or 0

        logger.info(
            "duplicate_count",
            extra={
                "table": table_name,
                "duplicate_keys": count,
            },
        )
        return count
