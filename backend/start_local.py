"""Local development startup script.

Creates SQLite database, initializes schema, seeds admin user,
and starts the FastAPI server. No external dependencies needed.
"""
import asyncio
import os
import sys
import uuid

# Ensure we're in the backend directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

async def init_database():
    """Create all tables and seed the admin user."""
    from app.storage.database import engine, Base
    from app.models import (  # noqa: F401 — import so Base.metadata knows about them
        Organization, User, Device, ActivitySession,
        AppUsage, UrlVisit, Screenshot, DeadLetterQueue,
    )

    # Also import security/pipeline models if available
    try:
        from app.models.api_key import ApiKey  # noqa: F401
        from app.models.audit_log import AuditLog  # noqa: F401
    except Exception:
        pass
    try:
        from app.models.aggregation import DailyUserSummary, MonthlyOrgSummary  # noqa: F401
    except Exception:
        pass

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Database tables created")

    # Seed admin user if not exists
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.storage.database import async_session_factory
    from app.models.user import User
    from app.models.organization import Organization

    async with async_session_factory() as session:
        # Check if org exists
        result = await session.execute(select(Organization).limit(1))
        org = result.scalar_one_or_none()
        if not org:
            org = Organization(
                id=uuid.uuid4(),
                name="TrackMe Demo Organization",
            )
            session.add(org)
            await session.flush()
            print(f"[OK] Organization created: {org.name}")

        # Check if admin exists
        result = await session.execute(
            select(User).where(User.email == "admin@trackme.com")
        )
        admin = result.scalar_one_or_none()
        if not admin:
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

            admin = User(
                id=uuid.uuid4(),
                org_id=org.id,
                email="admin@trackme.com",
                password_hash=pwd_context.hash("admin123"),
                full_name="Admin User",
                role="admin",
                is_active=True,
            )
            session.add(admin)
            print(f"[OK] Admin user created: admin@trackme.com / admin123")
        else:
            print(f"[OK] Admin user already exists: {admin.email}")

        await session.commit()

    print("[OK] Database seeded successfully")


def main():
    """Initialize DB and start uvicorn."""
    print("=" * 50)
    print("  TrackMe Backend - Local Development Server")
    print("=" * 50)
    print()

    # Initialize database
    print("[..] Initializing database...")
    asyncio.run(init_database())
    print()

    # Start uvicorn
    print("[..] Starting API server on http://localhost:8000")
    print("[..] API docs at http://localhost:8000/docs")
    print("[..] Dashboard at http://localhost:5173")
    print()
    print("  Login: admin@trackme.com / admin123")
    print()

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
