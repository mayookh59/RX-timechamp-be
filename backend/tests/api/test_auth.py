"""Tests for the authentication API endpoints.

Covers login with valid/invalid credentials and token refresh flows.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.services.auth_service import create_refresh_token


class TestLogin:
    """Tests for POST /api/v1/auth/login."""

    async def test_login_valid_credentials_returns_tokens(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Successful login returns access and refresh tokens with 200 status."""
        # Arrange - seed a user with known credentials
        from app.models.user import User
        from app.services.auth_service import get_password_hash

        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        hashed = get_password_hash("correct-password-123")

        user = User(
            id=user_id,
            org_id=org_id,
            email="alice@trackme-test.com",
            hashed_password=hashed,
            full_name="Alice Test",
            role="admin",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        # Act
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "alice@trackme-test.com", "password": "correct-password-123"},
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert len(data["access_token"]) > 0
        assert len(data["refresh_token"]) > 0

    async def test_login_invalid_password_returns_401(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Login with wrong password returns 401 Unauthorized."""
        # Arrange
        from app.models.user import User
        from app.services.auth_service import get_password_hash

        user = User(
            id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            email="bob@trackme-test.com",
            hashed_password=get_password_hash("real-password"),
            full_name="Bob Test",
            role="viewer",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        # Act
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "bob@trackme-test.com", "password": "wrong-password"},
        )

        # Assert
        assert response.status_code == 401

    async def test_login_nonexistent_email_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Login with an email that does not exist returns 401."""
        # Act
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@nowhere.com", "password": "any-password"},
        )

        # Assert
        assert response.status_code == 401

    async def test_login_missing_fields_returns_422(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Login with missing required fields returns 422 Unprocessable Entity."""
        # Act
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com"},
        )

        # Assert
        assert response.status_code == 422


class TestRefreshToken:
    """Tests for POST /api/v1/auth/refresh."""

    async def test_refresh_valid_token_returns_new_tokens(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Valid refresh token exchange returns new access and refresh tokens."""
        # Arrange - create a user and a valid refresh token
        from app.models.user import User
        from app.services.auth_service import get_password_hash

        user_id = uuid.uuid4()
        org_id = uuid.uuid4()

        user = User(
            id=user_id,
            org_id=org_id,
            email="carol@trackme-test.com",
            hashed_password=get_password_hash("password"),
            full_name="Carol Test",
            role="viewer",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        refresh = create_refresh_token({
            "sub": str(user_id),
            "email": "carol@trackme-test.com",
            "role": "viewer",
            "org_id": str(org_id),
        })

        # Act
        response = await async_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh},
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_invalid_token_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Invalid refresh token returns 401 Unauthorized."""
        # Act
        response = await async_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.value"},
        )

        # Assert
        assert response.status_code == 401

    async def test_refresh_expired_token_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Expired refresh token returns 401 Unauthorized."""
        # Arrange - create a token with past expiry by manipulating claims
        import jwt
        from datetime import datetime, timedelta, timezone

        from app.core.config import settings

        expired_payload = {
            "sub": str(uuid.uuid4()),
            "email": "expired@test.com",
            "role": "viewer",
            "org_id": str(uuid.uuid4()),
            "type": "refresh",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired_token = jwt.encode(
            expired_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
        )

        # Act
        response = await async_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": expired_token},
        )

        # Assert
        assert response.status_code == 401

    async def test_refresh_with_access_token_type_returns_401(
        self,
        async_client: AsyncClient,
        db_session,
    ) -> None:
        """Using an access token (wrong type) for refresh returns 401."""
        # Arrange
        from app.services.auth_service import create_access_token

        access_token = create_access_token({
            "sub": str(uuid.uuid4()),
            "email": "user@test.com",
            "role": "viewer",
            "org_id": str(uuid.uuid4()),
        })

        # Act
        response = await async_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )

        # Assert
        assert response.status_code == 401
