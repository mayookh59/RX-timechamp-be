"""Shared pytest fixtures for the TrackMe backend test suite.

Provides async HTTP client, in-memory SQLite test database,
test user fixtures (admin, manager, viewer), and JWT auth token fixtures.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.services.auth_service import create_access_token, create_refresh_token
from app.storage.database import Base, get_db

# ---------------------------------------------------------------------------
# Test database (async SQLite in-memory)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite://"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    """Create all tables before each test and drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency override that provides a test database session."""
    async with test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Async HTTP test client
# ---------------------------------------------------------------------------

@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an httpx AsyncClient wired to the FastAPI app with DB override."""
    from app.main import app

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a raw async database session for direct DB operations in tests."""
    async with test_session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Test user fixtures
# ---------------------------------------------------------------------------

def _make_user_dict(
    *,
    role: str = "viewer",
    email: str | None = None,
    org_id: str | None = None,
) -> dict:
    """Build a user dictionary for token creation and DB seeding."""
    user_id = str(uuid.uuid4())
    return {
        "id": user_id,
        "sub": user_id,
        "email": email or f"{role}-{user_id[:8]}@trackme-test.com",
        "role": role,
        "org_id": org_id or str(uuid.uuid4()),
        "is_active": True,
    }


@pytest.fixture
def admin_user() -> dict:
    """Return an admin user dictionary."""
    return _make_user_dict(role="admin")


@pytest.fixture
def manager_user() -> dict:
    """Return a manager user dictionary."""
    return _make_user_dict(role="manager")


@pytest.fixture
def viewer_user() -> dict:
    """Return a viewer (regular) user dictionary."""
    return _make_user_dict(role="viewer")


# ---------------------------------------------------------------------------
# Auth token fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_token(admin_user: dict) -> str:
    """Return a valid JWT access token for the admin user."""
    return create_access_token(admin_user)


@pytest.fixture
def manager_token(manager_user: dict) -> str:
    """Return a valid JWT access token for the manager user."""
    return create_access_token(manager_user)


@pytest.fixture
def viewer_token(viewer_user: dict) -> str:
    """Return a valid JWT access token for the viewer user."""
    return create_access_token(viewer_user)


@pytest.fixture
def admin_refresh_token(admin_user: dict) -> str:
    """Return a valid JWT refresh token for the admin user."""
    return create_refresh_token(admin_user)


def auth_headers(token: str) -> dict[str, str]:
    """Build Authorization header dict from a JWT token."""
    return {"Authorization": f"Bearer {token}"}
