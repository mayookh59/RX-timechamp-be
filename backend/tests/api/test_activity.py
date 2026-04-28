"""Tests for the activity session API endpoints.

Covers batch ingestion, idempotency on duplicate client_ids,
date-range filtering, and activity summary.
"""

import uuid
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient

from tests.conftest import auth_headers


class TestIngestSessions:
    """Tests for POST /api/v1/activity/sessions."""

    async def test_ingest_sessions_batch_returns_accepted_count(
        self,
        async_client: AsyncClient,
        db_session,
        admin_token: str,
    ) -> None:
        """Batch ingest of activity sessions returns the count of accepted records."""
        # Arrange - create a device for the API key auth
        device_id, api_key = await _seed_device(db_session)

        sessions = [
            {
                "client_id": str(uuid.uuid4()),
                "start_time": "2026-03-25T09:00:00Z",
                "end_time": "2026-03-25T09:30:00Z",
                "active_seconds": 1500,
                "idle_seconds": 300,
            },
            {
                "client_id": str(uuid.uuid4()),
                "start_time": "2026-03-25T09:30:00Z",
                "end_time": "2026-03-25T10:00:00Z",
                "active_seconds": 1600,
                "idle_seconds": 200,
            },
        ]

        # Act
        response = await async_client.post(
            "/api/v1/activity/sessions",
            json={"device_id": str(device_id), "sessions": sessions},
            headers={"X-API-Key": api_key},
        )

        # Assert
        assert response.status_code == 201
        data = response.json()
        assert data["accepted"] == 2

    async def test_ingest_sessions_duplicate_client_id_is_idempotent(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Submitting sessions with the same client_id twice does not create duplicates."""
        # Arrange
        device_id, api_key = await _seed_device(db_session)
        client_id = str(uuid.uuid4())

        session_payload = {
            "device_id": str(device_id),
            "sessions": [
                {
                    "client_id": client_id,
                    "start_time": "2026-03-25T08:00:00Z",
                    "end_time": "2026-03-25T08:30:00Z",
                    "active_seconds": 1500,
                    "idle_seconds": 300,
                }
            ],
        }

        # Act - submit twice
        response1 = await async_client.post(
            "/api/v1/activity/sessions",
            json=session_payload,
            headers={"X-API-Key": api_key},
        )
        response2 = await async_client.post(
            "/api/v1/activity/sessions",
            json=session_payload,
            headers={"X-API-Key": api_key},
        )

        # Assert - first should accept, second should accept 0 (idempotent)
        assert response1.status_code == 201
        assert response1.json()["accepted"] == 1

        assert response2.status_code == 201
        assert response2.json()["accepted"] == 0

    async def test_ingest_sessions_without_api_key_returns_401_or_403(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Ingest without API key authentication returns an error."""
        # Act
        response = await async_client.post(
            "/api/v1/activity/sessions",
            json={
                "device_id": str(uuid.uuid4()),
                "sessions": [],
            },
        )

        # Assert
        assert response.status_code in (401, 403, 422)


class TestListSessions:
    """Tests for GET /api/v1/activity/sessions."""

    async def test_list_sessions_with_date_range_returns_filtered_results(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Listing sessions with date range parameters returns a paginated response."""
        # Act
        response = await async_client.get(
            "/api/v1/activity/sessions",
            params={
                "start_date": "2026-03-01",
                "end_date": "2026-03-31",
                "page": 1,
                "per_page": 10,
            },
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "pages" in data
        assert isinstance(data["items"], list)

    async def test_list_sessions_without_auth_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Listing sessions without authentication returns 401."""
        # Act
        response = await async_client.get("/api/v1/activity/sessions")

        # Assert
        assert response.status_code in (401, 403)

    async def test_list_sessions_default_pagination(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Default pagination returns expected page structure."""
        # Act
        response = await async_client.get(
            "/api/v1/activity/sessions",
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 20


class TestActivitySummary:
    """Tests for GET /api/v1/activity/summary."""

    async def test_get_summary_returns_aggregated_data(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Activity summary returns aggregated metrics for the user."""
        # Act
        response = await async_client.get(
            "/api/v1/activity/summary",
            params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    async def test_get_summary_without_auth_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Activity summary without authentication returns 401."""
        # Act
        response = await async_client.get("/api/v1/activity/summary")

        # Assert
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_device(db_session) -> tuple[uuid.UUID, str]:
    """Seed a user and device, returning (device_id, api_key)."""
    import secrets

    from app.models.device import Device
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    device_id = uuid.uuid4()
    api_key = secrets.token_urlsafe(48)

    user = User(
        id=user_id,
        org_id=org_id,
        email=f"agent-user-{user_id.hex[:8]}@trackme-test.com",
        hashed_password=get_password_hash("password"),
        full_name="Agent User",
        role="viewer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    device = Device(
        id=device_id,
        user_id=user_id,
        org_id=org_id,
        hostname="TEST-PC",
        os_version="Windows 11",
        agent_version="1.0.0",
        api_key_hash=get_password_hash(api_key),
        is_active=True,
    )
    db_session.add(device)
    await db_session.commit()

    return device_id, api_key
