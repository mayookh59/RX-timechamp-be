"""
TrackMe Initial Setup Script
Creates the database tables, default organization, and admin user.
Run this ONCE after first deployment.

Usage:
    cd backend
    python seed_init.py
"""

import asyncio
import uuid


async def main():
    from app.storage.database import engine, Base, async_session_factory
    from app.models import Organization, User
    from app.services.auth_service import get_password_hash

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Database tables created.")

    async with async_session_factory() as db:
        # Check if org already exists
        from sqlalchemy import select
        existing = await db.execute(select(Organization).limit(1))
        if existing.scalar_one_or_none():
            print("[SKIP] Organization already exists. Use the dashboard to manage users.")
            return

        # Create default organization
        org_id = uuid.uuid4()
        org = Organization(id=org_id, name="My Organization")
        db.add(org)
        await db.flush()
        print(f"[OK] Organization created: My Organization (ID: {org_id})")

        # Create admin user
        admin_id = uuid.uuid4()
        admin = User(
            id=admin_id,
            org_id=org_id,
            email="admin@trackme.com",
            password_hash=get_password_hash("admin123"),
            full_name="System Administrator",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        print(f"[OK] Admin user created (ID: {admin_id})")

    print()
    print("=" * 50)
    print("  TrackMe is ready!")
    print("=" * 50)
    print()
    print("  Login credentials:")
    print("    Email:    admin@trackme.com")
    print("    Password: admin123")
    print()
    print("  IMPORTANT: Change the admin password after first login.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
