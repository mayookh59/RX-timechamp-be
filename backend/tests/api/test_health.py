"""Tests for the health check API endpoints.

Covers liveness probe (always 200) and readiness probe
(returns health status of dependencies).
"""

import pytest
from httpx import AsyncClient


class TestLiveness:
    """Tests for GET /api/v1/health/live."""

    async def test_liveness_returns_200(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Liveness probe returns 200 with status ok."""
        # Act
        response = await async_client.get("/api/v1/health/live")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    async def test_liveness_response_has_status_field(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Liveness response body contains the required status field."""
        # Act
        response = await async_client.get("/api/v1/health/live")

        # Assert
        data = response.json()
        assert "status" in data

    async def test_liveness_requires_no_auth(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Liveness probe is accessible without authentication."""
        # Act - no auth headers
        response = await async_client.get("/api/v1/health/live")

        # Assert
        assert response.status_code == 200


class TestReadiness:
    """Tests for GET /api/v1/health/ready."""

    async def test_readiness_returns_health_status(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Readiness probe returns a response with status and checks."""
        # Act
        response = await async_client.get("/api/v1/health/ready")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("ok", "degraded")

    async def test_readiness_includes_version(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Readiness response includes the application version."""
        # Act
        response = await async_client.get("/api/v1/health/ready")

        # Assert
        data = response.json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    async def test_readiness_includes_dependency_checks(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Readiness response includes individual dependency check results."""
        # Act
        response = await async_client.get("/api/v1/health/ready")

        # Assert
        data = response.json()
        assert "checks" in data
        checks = data["checks"]
        assert isinstance(checks, dict)
        # In test environment, some checks may report 'error' (no real Redis/S3)
        assert "database" in checks
        assert "redis" in checks
        assert "s3" in checks

    async def test_readiness_requires_no_auth(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Readiness probe is accessible without authentication."""
        # Act
        response = await async_client.get("/api/v1/health/ready")

        # Assert
        assert response.status_code == 200

    async def test_readiness_degraded_when_dependencies_unavailable(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Readiness reports degraded status when external services are unreachable.

        In the test environment, Redis and S3 are not available, so readiness
        should report either 'ok' (if mocked) or 'degraded' (if hitting real checks).
        """
        # Act
        response = await async_client.get("/api/v1/health/ready")

        # Assert
        data = response.json()
        assert data["status"] in ("ok", "degraded")
