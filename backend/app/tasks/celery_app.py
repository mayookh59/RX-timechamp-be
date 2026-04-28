"""Celery application configuration for TrackMe background tasks."""

from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "trackme",
    broker_url="redis://redis:6379/0",
    result_backend="redis://redis:6379/1",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "retry-dead-letter-queue": {
        "task": "app.tasks.maintenance.retry_dlq_entries",
        "schedule": crontab(minute="*/5"),
    },
    "purge-expired-data": {
        "task": "app.tasks.maintenance.purge_expired_records",
        "schedule": crontab(hour=2, minute=0),
    },
    "purge-expired-screenshots": {
        "task": "app.tasks.maintenance.purge_expired_screenshots",
        "schedule": crontab(hour=2, minute=30),
    },
    "database-maintenance": {
        "task": "app.tasks.maintenance.run_database_maintenance",
        "schedule": crontab(hour=3, minute=0, day_of_week="sunday"),
    },
    "create-database-backup": {
        "task": "app.tasks.maintenance.create_database_backup",
        "schedule": crontab(hour=1, minute=0),
    },
}

celery_app.autodiscover_tasks(["app.tasks"])
