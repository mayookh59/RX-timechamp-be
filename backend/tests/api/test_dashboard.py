"""Tests for the dashboard API endpoints.

Covers organization overview, individual user dashboard,
and trend data retrieval.
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import auth_headers


class TestDashboardOverview:
    """Tests for GET /api/v1/dashboard/overview."""

    async def test_overview_returns_organization_metrics(
        self,
        async_client: AsyncClient,
        admin_token: str,
    ) -> None:
        """Authenticated user gets organization-wide overview data."""
        # Act
        response = await async_client.get(
            "/api/v1/dashboard/overview",
            params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
            headers=auth_headers(admin_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    async def test_overview_without_auth_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Dashboard overview without authentication returns 401."""
        # Act
        response = await async_client.get("/api/v1/dashboard/overview")

        # Assert
        assert response.status_code in (401, 403)

    async def test_overview_without_date_params_uses_defaults(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Overview without explicit date range still returns valid data."""
        # Act
        response = await async_client.get(
            "/api/v1/dashboard/overview",
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 200


class TestUserDashboard:
    """Tests for GET /api/v1/dashboard/user/{target_user_id}."""

    async def test_user_dashboard_own_data_returns_200(
        self,
        async_client: AsyncClient,
        viewer_user: dict,
        viewer_token: str,
    ) -> None:
        """User can view their own dashboard data."""
        # Act
        user_id = viewer_user["id"]
        response = await async_client.get(
            f"/api/v1/dashboard/user/{user_id}",
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    async def test_user_dashboard_other_user_as_viewer_returns_403(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Viewer cannot access another user's dashboard."""
        # Arrange
        other_user_id = str(uuid.uuid4())

        # Act
        response = await async_client.get(
            f"/api/v1/dashboard/user/{other_user_id}",
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 403

    async def test_user_dashboard_other_user_as_admin_returns_200(
        self,
        async_client: AsyncClient,
        admin_token: str,
    ) -> None:
        """Admin can view any user's dashboard."""
        # Arrange
        target_user_id = str(uuid.uuid4())

        # Act
        response = await async_client.get(
            f"/api/v1/dashboard/user/{target_user_id}",
            headers=auth_headers(admin_token),
        )

        # Assert
        # May return 200 or 404 (user not found), but not 403
        assert response.status_code in (200, 404)

    async def test_user_dashboard_without_auth_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """User dashboard without authentication returns 401."""
        # Act
        response = await async_client.get(
            f"/api/v1/dashboard/user/{uuid.uuid4()}",
        )

        # Assert
        assert response.status_code in (401, 403)

    async def test_user_dashboard_invalid_uuid_returns_422(
        self,
        async_client: AsyncClient,
        admin_token: str,
    ) -> None:
        """Invalid UUID in path returns 422."""
        # Act
        response = await async_client.get(
            "/api/v1/dashboard/user/not-a-uuid",
            headers=auth_headers(admin_token),
        )

        # Assert
        assert response.status_code == 422


class TestDashboardTrends:
    """Tests for GET /api/v1/dashboard/trends."""

    async def test_trends_returns_time_series_data(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Trends endpoint returns time-series data for the requested range."""
        # Act
        response = await async_client.get(
            "/api/v1/dashboard/trends",
            params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    async def test_trends_without_auth_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Trends endpoint without authentication returns 401."""
        # Act
        response = await async_client.get("/api/v1/dashboard/trends")

        # Assert
        assert response.status_code in (401, 403)

    async def test_trends_without_dates_uses_defaults(
        self,
        async_client: AsyncClient,
        admin_token: str,
    ) -> None:
        """Trends without explicit dates defaults to the last 30 days."""
        # Act
        response = await async_client.get(
            "/api/v1/dashboard/trends",
            headers=auth_headers(admin_token),
        )

        # Assert
        assert response.status_code == 200
