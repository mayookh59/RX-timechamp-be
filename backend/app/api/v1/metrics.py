"""Prometheus metrics scrape endpoint.

Exposes collected application metrics in Prometheus text format
for scraping by a Prometheus server instance.
"""

import logging

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description="Returns all collected metrics in Prometheus text exposition format.",
    response_class=Response,
    include_in_schema=False,
)
async def get_metrics() -> Response:
    """Serve Prometheus metrics in text exposition format.

    Returns:
        A plain-text response containing all registered Prometheus
        metrics formatted for scraping.
    """
    try:
        metrics_output = generate_latest()
        return Response(
            content=metrics_output,
            media_type=CONTENT_TYPE_LATEST,
        )
    except Exception:
        logger.exception("Failed to generate Prometheus metrics")
        return Response(
            content="# Error generating metrics\n",
            media_type="text/plain",
            status_code=500,
        )
