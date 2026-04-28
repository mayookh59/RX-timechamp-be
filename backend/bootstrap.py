"""Production bootstrap — runs once on the first Railway deploy.

Creates all tables + seeds the admin user. Idempotent: safe to run multiple
times. After first run subsequent runs become no-ops.

Usage on Railway:
    python -m backend.bootstrap
or:
    cd backend && python bootstrap.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid


async def main() -> None:
    # Make `app.*` importable regardless of where the script is run from
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    from sqlalchemy import select  # noqa: E402

    from app.storage.database import engine, Base, async_session_factory  # noqa: E402
    # Ensure every model class is imported so Base.metadata.create_all knows them
    from app.models import (  # noqa: F401, E402
        Organization,
        User,
        Device,
        ActivitySession,
        AppUsage,
        UrlVisit,
        Screenshot,
        DeadLetterQueue,
    )
    try:
        from app.models.api_key import ApiKey  # noqa: F401
        from app.models.audit_log import AuditLog  # noqa: F401
    except Exception:
        pass
    try:
        from app.models.aggregation import (  # noqa: F401
            DailyUserSummary,
            MonthlyOrgSummary,
        )
    except Exception:
        pass

    print("[bootstrap] Creating tables (if missing)…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[bootstrap] Tables ready.")

    from app.models.organization import Organization  # noqa: E402
    from app.models.user import User  # noqa: E402

    async with async_session_factory() as session:
        # Organization
        result = await session.execute(select(Organization).limit(1))
        org = result.scalar_one_or_none()
        if not org:
            org = Organization(id=uuid.uuid4(), name="TrackMe")
            session.add(org)
            await session.flush()
            print(f"[bootstrap] Org created: {org.name} ({org.id})")
        else:
            print(f"[bootstrap] Org already exists: {org.name}")

        # Admin user
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@trackme.com")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")

        result = await session.execute(select(User).where(User.email == admin_email))
        admin = result.scalar_one_or_none()
        if not admin:
            from passlib.context import CryptContext

            pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
            admin = User(
                id=uuid.uuid4(),
                org_id=org.id,
                email=admin_email,
                password_hash=pwd_ctx.hash(admin_password),
                full_name="Admin User",
                role="admin",
                is_active=True,
            )
            session.add(admin)
            print(f"[bootstrap] Admin created: {admin_email} / {admin_password}")
            print("[bootstrap] CHANGE THE PASSWORD FROM THE DASHBOARD IMMEDIATELY.")
        else:
            print(f"[bootstrap] Admin already exists: {admin.email}")

        await session.commit()

    print("[bootstrap] Done.")


if __name__ == "__main__":
    asyncio.run(main())
