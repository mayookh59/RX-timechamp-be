"""Tests for the agent management API endpoints.

Covers device registration, heartbeat, and admin-only agent listing.
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import auth_headers


class TestAgentRegistration:
    """Tests for POST /api/v1/agents/register."""

    async def test_register_agent_with_valid_user_returns_201(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Registering a new agent with a valid user email returns device_id and api_key."""
        # Arrange - seed a user
        from app.models.user import User
        from app.services.auth_service import get_password_hash

        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        user = User(
            id=user_id,
            org_id=org_id,
            email="dev@trackme-test.com",
            hashed_password=get_password_hash("password"),
            full_name="Dev User",
            role="viewer",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        # Act
        response = await async_client.post(
            "/api/v1/agents/register",
            json={
                "hostname": "DEV-WORKSTATION",
                "os_version": "Windows 11 Pro 10.0.26100",
                "agent_version": "1.0.0",
                "user_email": "dev@trackme-test.com",
            },
        )

        # Assert
        assert response.status_code == 201
        data = response.json()
        assert "device_id" in data
        assert "api_key" in data
        assert len(data["api_key"]) > 0

    async def test_register_agent_with_unknown_email_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Registering with an unknown user email returns 404."""
        # Act
        response = await async_client.post(
            "/api/v1/agents/register",
            json={
                "hostname": "GHOST-PC",
                "os_version": "Windows 11",
                "agent_version": "1.0.0",
                "user_email": "nonexistent@trackme-test.com",
            },
        )

        # Assert
        assert response.status_code == 404

    async def test_register_agent_missing_fields_returns_422(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Registration with missing required fields returns 422."""
        # Act
        response = await async_client.post(
            "/api/v1/agents/register",
            json={"hostname": "PC-ONLY"},
        )

        # Assert
        assert response.status_code == 422


class TestAgentHeartbeat:
    """Tests for POST /api/v1/agents/heartbeat."""

    async def test_heartbeat_with_valid_api_key_returns_200(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Heartbeat with a valid device API key returns 200 with response payload."""
        # Arrange
        device_id, api_key = await _seed_registered_device(db_session)

        # Act
        response = await async_client.post(
            "/api/v1/agents/heartbeat",
            json={
                "agent_version": "1.0.0",
                "cpu_usage": 45.2,
                "ram_usage": 68.7,
                "queue_depth": 12,
            },
            headers={"X-API-Key": api_key},
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "force_upgrade_required" in data

    async def test_heartbeat_without_api_key_returns_error(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Heartbeat without API key returns 401 or 403."""
        # Act
        response = await async_client.post(
            "/api/v1/agents/heartbeat",
            json={
                "agent_version": "1.0.0",
                "cpu_usage": 10.0,
                "ram_usage": 50.0,
                "queue_depth": 0,
            },
        )

        # Assert
        assert response.status_code in (401, 403, 422)


class TestListAgents:
    """Tests for GET /api/v1/agents."""

    async def test_list_agents_as_admin_returns_200(
        self,
        async_client: AsyncClient,
        admin_token: str,
    ) -> None:
        """Admin user can list all enrolled agents."""
        # Act
        response = await async_client.get(
            "/api/v1/agents",
            headers=auth_headers(admin_token),
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    async def test_list_agents_as_viewer_returns_403(
        self,
        async_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        """Non-admin user is forbidden from listing agents."""
        # Act
        response = await async_client.get(
            "/api/v1/agents",
            headers=auth_headers(viewer_token),
        )

        # Assert
        assert response.status_code == 403

    async def test_list_agents_without_auth_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Unauthenticated request to list agents returns 401."""
        # Act
        response = await async_client.get("/api/v1/agents")

        # Assert
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_registered_device(db_session) -> tuple[uuid.UUID, str]:
    """Seed a user and registered device, returning (device_id, api_key)."""
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
        email=f"hb-user-{user_id.hex[:8]}@trackme-test.com",
        hashed_password=get_password_hash("password"),
        full_name="Heartbeat User",
        role="viewer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    device = Device(
        id=device_id,
        user_id=user_id,
        org_id=org_id,
        hostname="HB-PC",
        os_version="Windows 11",
        agent_version="1.0.0",
        api_key_hash=get_password_hash(api_key),
        is_active=True,
    )
    db_session.add(device)
    await db_session.commit()

    return device_id, api_key
