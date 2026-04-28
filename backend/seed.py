"""Seed script: create all tables and add a test admin user."""

import asyncio
import uuid
from datetime import datetime, timedelta
import random

async def main():
    from app.storage.database import engine, Base, async_session_factory
    from app.models import (
        Organization, User, Device, ActivitySession, AppUsage, UrlVisit,
    )
    from app.services.auth_service import get_password_hash

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created.")

    async with async_session_factory() as db:
        # Create organization
        org_id = uuid.uuid4()
        org = Organization(id=org_id, name="Acme Corp")
        db.add(org)
        await db.flush()

        # Create admin user
        admin_id = uuid.uuid4()
        admin = User(
            id=admin_id,
            org_id=org_id,
            email="admin@trackme.com",
            password_hash=get_password_hash("admin123"),
            full_name="Admin User",
            role="admin",
            is_active=True,
        )
        db.add(admin)

        # Create test employees
        employees = []
        names = [
            ("Alice Johnson", "alice@trackme.com", "viewer"),
            ("Bob Smith", "bob@trackme.com", "viewer"),
            ("Carol White", "carol@trackme.com", "viewer"),
            ("David Brown", "david@trackme.com", "manager"),
            ("Eve Davis", "eve@trackme.com", "viewer"),
        ]
        for full_name, email, role in names:
            uid = uuid.uuid4()
            user = User(
                id=uid,
                org_id=org_id,
                email=email,
                password_hash=get_password_hash("pass123"),
                full_name=full_name,
                role=role,
                is_active=True,
            )
            db.add(user)
            employees.append((uid, full_name))
        await db.flush()

        # Create devices for each employee
        devices = []
        for i, (uid, name) in enumerate(employees):
            dev_id = uuid.uuid4()
            device = Device(
                id=dev_id,
                user_id=uid,
                org_id=org_id,
                hostname=f"WS-{i+1:03d}",
                os_version="Windows 11 Pro 23H2",
                agent_version="1.0.0",
                api_key_hash=get_password_hash(f"key-{dev_id}"),
                last_heartbeat=datetime.utcnow() - timedelta(minutes=random.randint(1, 30)),
                is_active=True,
            )
            db.add(device)
            devices.append((dev_id, uid))
        await db.flush()

        # Generate sample activity sessions (last 7 days)
        now = datetime.utcnow()
        for dev_id, user_id in devices:
            for day_offset in range(7):
                day = now - timedelta(days=day_offset)
                work_start = day.replace(hour=9, minute=0, second=0)

                for hour in range(8):
                    session_start = work_start + timedelta(hours=hour)
                    active_mins = random.randint(30, 55)
                    session_end = session_start + timedelta(minutes=active_mins)

                    db.add(ActivitySession(
                        id=uuid.uuid4(),
                        client_id=uuid.uuid4(),
                        device_id=dev_id,
                        user_id=user_id,
                        session_type="active",
                        start_time=session_start,
                        end_time=session_end,
                    ))

                    # Add idle session
                    idle_end = session_start + timedelta(hours=1)
                    if session_end < idle_end:
                        db.add(ActivitySession(
                            id=uuid.uuid4(),
                            client_id=uuid.uuid4(),
                            device_id=dev_id,
                            user_id=user_id,
                            session_type="idle",
                            start_time=session_end,
                            end_time=idle_end,
                        ))
            await db.flush()

        # Generate sample app usage
        apps = [
            ("Visual Studio Code", "Code - TrackMe"),
            ("Google Chrome", "GitHub - Pull Requests"),
            ("Slack", "engineering - Slack"),
            ("Microsoft Teams", "Standup Meeting"),
            ("Terminal", "bash - ~/projects"),
            ("Figma", "Dashboard Designs"),
        ]
        for dev_id, user_id in devices:
            for day_offset in range(7):
                day = now - timedelta(days=day_offset)
                work_start = day.replace(hour=9, minute=0, second=0)
                for i in range(random.randint(8, 15)):
                    app_name, window = random.choice(apps)
                    start = work_start + timedelta(minutes=random.randint(0, 420))
                    duration = random.randint(300, 3600)
                    db.add(AppUsage(
                        id=uuid.uuid4(),
                        client_id=uuid.uuid4(),
                        device_id=dev_id,
                        user_id=user_id,
                        process_name=app_name,
                        window_title=window,
                        start_time=start,
                        end_time=start + timedelta(seconds=duration),
                        duration_sec=duration,
                    ))
            await db.flush()

        # Generate sample URL visits
        urls = [
            ("github.com", "GitHub", "chrome"),
            ("stackoverflow.com", "Stack Overflow", "chrome"),
            ("docs.python.org", "Python Docs", "chrome"),
            ("slack.com", "Slack", "chrome"),
            ("jira.atlassian.net", "Jira Board", "edge"),
            ("figma.com", "Figma", "chrome"),
        ]
        for dev_id, user_id in devices:
            for day_offset in range(7):
                day = now - timedelta(days=day_offset)
                for i in range(random.randint(10, 25)):
                    domain, title, browser = random.choice(urls)
                    visit_time = day.replace(
                        hour=random.randint(9, 17),
                        minute=random.randint(0, 59),
                    )
                    db.add(UrlVisit(
                        id=uuid.uuid4(),
                        client_id=uuid.uuid4(),
                        device_id=dev_id,
                        user_id=user_id,
                        browser=browser,
                        url=f"https://{domain}/page-{random.randint(1,100)}",
                        domain=domain,
                        page_title=f"{title} - Page {random.randint(1,50)}",
                        visit_time=visit_time,
                        duration_sec=random.randint(30, 600),
                    ))
            await db.flush()

        await db.commit()

    print("Seed data created successfully!")
    print()
    print("Test accounts:")
    print("  Admin:  admin@trackme.com / admin123")
    print("  User:   alice@trackme.com / pass123")
    print("  User:   bob@trackme.com / pass123")
    print("  Manager: david@trackme.com / pass123")


if __name__ == "__main__":
    asyncio.run(main())
