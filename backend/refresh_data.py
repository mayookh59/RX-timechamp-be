"""Refresh script: add activity data for today and recent days to existing database."""

import asyncio
import uuid
from datetime import datetime, timedelta
import random


async def main():
    from app.storage.database import async_session_factory
    from app.models import (
        User, Device, ActivitySession, AppUsage, UrlVisit,
    )
    from sqlalchemy import select, func

    async with async_session_factory() as db:
        # Get all devices with their user IDs
        result = await db.execute(select(Device.id, Device.user_id))
        devices = result.all()
        if not devices:
            print("No devices found. Run seed.py first.")
            return

        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Check what date range already has data
        latest_result = await db.execute(
            select(func.max(ActivitySession.start_time))
        )
        latest_date = latest_result.scalar()
        if latest_date:
            print(f"Latest existing data: {latest_date.strftime('%Y-%m-%d %H:%M')}")
        else:
            print("No existing activity data found.")

        # Generate data for the last 3 days including today
        apps = [
            ("Visual Studio Code", "Code - TrackMe"),
            ("Google Chrome", "GitHub - Pull Requests"),
            ("Slack", "engineering - Slack"),
            ("Microsoft Teams", "Standup Meeting"),
            ("Terminal", "bash - ~/projects"),
            ("Figma", "Dashboard Designs"),
        ]
        urls = [
            ("github.com", "GitHub", "chrome"),
            ("stackoverflow.com", "Stack Overflow", "chrome"),
            ("docs.python.org", "Python Docs", "chrome"),
            ("slack.com", "Slack", "chrome"),
            ("jira.atlassian.net", "Jira Board", "edge"),
            ("figma.com", "Figma", "chrome"),
        ]

        days_to_generate = 3  # today + 2 previous days
        count = 0

        for dev_id, user_id in devices:
            for day_offset in range(days_to_generate):
                day = now - timedelta(days=day_offset)
                day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)

                # Skip if data already exists for this device+day
                existing = await db.execute(
                    select(func.count()).where(
                        ActivitySession.device_id == dev_id,
                        ActivitySession.start_time >= day_start,
                        ActivitySession.start_time < day_start + timedelta(days=1),
                    )
                )
                if existing.scalar() > 0:
                    continue

                work_start = day.replace(hour=9, minute=0, second=0)

                # For today, only generate up to current hour
                max_hours = 8
                if day_offset == 0:
                    current_hour = now.hour
                    max_hours = max(0, min(8, current_hour - 9))

                # Activity sessions
                for hour in range(max_hours):
                    session_start = work_start + timedelta(hours=hour)
                    active_mins = random.randint(30, 55)
                    session_end = session_start + timedelta(minutes=active_mins)

                    active_duration = int((session_end - session_start).total_seconds())
                    db.add(ActivitySession(
                        id=uuid.uuid4(),
                        client_id=uuid.uuid4(),
                        device_id=dev_id,
                        user_id=user_id,
                        session_type="active",
                        start_time=session_start,
                        end_time=session_end,
                        duration_sec=active_duration,
                    ))

                    idle_end = session_start + timedelta(hours=1)
                    if session_end < idle_end:
                        idle_duration = int((idle_end - session_end).total_seconds())
                        db.add(ActivitySession(
                            id=uuid.uuid4(),
                            client_id=uuid.uuid4(),
                            device_id=dev_id,
                            user_id=user_id,
                            session_type="idle",
                            start_time=session_end,
                            end_time=idle_end,
                            duration_sec=idle_duration,
                        ))
                    count += 1

                # App usage
                for i in range(random.randint(8, 15)):
                    app_name, window = random.choice(apps)
                    start = work_start + timedelta(minutes=random.randint(0, max_hours * 60 if max_hours > 0 else 60))
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

                # URL visits
                for i in range(random.randint(10, 25)):
                    domain, title, browser = random.choice(urls)
                    visit_hour = random.randint(9, min(17, now.hour) if day_offset == 0 else 17)
                    visit_time = day.replace(
                        hour=visit_hour,
                        minute=random.randint(0, 59),
                    )
                    db.add(UrlVisit(
                        id=uuid.uuid4(),
                        client_id=uuid.uuid4(),
                        device_id=dev_id,
                        user_id=user_id,
                        browser=browser,
                        url=f"https://{domain}/page-{random.randint(1, 100)}",
                        domain=domain,
                        page_title=f"{title} - Page {random.randint(1, 50)}",
                        visit_time=visit_time,
                        duration_sec=random.randint(30, 600),
                    ))

            await db.flush()

        # Update device heartbeats to now
        for dev_id, user_id in devices:
            device = await db.get(Device, dev_id)
            if device:
                device.last_heartbeat = now - timedelta(minutes=random.randint(0, 5))
        await db.flush()

        await db.commit()

    print(f"Data refreshed! Added activity data for {days_to_generate} days across {len(devices)} devices.")
    print(f"Total new session groups: {count}")
    print(f"Device heartbeats updated to: ~{now.strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    asyncio.run(main())
