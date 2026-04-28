"""Locust load test for the TrackMe backend API.

Simulates 1000 desktop agents sending heartbeats, activity sessions,
app usage data, and URL visits at realistic intervals.

Usage:
    locust -f locustfile.py --host=http://localhost:8000 --users=1000 --spawn-rate=50
"""

import os
import random
import string
import uuid
from datetime import datetime, timedelta, timezone

from locust import HttpUser, between, tag, task


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Pre-generated API keys for load testing (set via env var or use defaults)
# In a real load test, these would be provisioned beforehand.
LOAD_TEST_API_KEY = os.environ.get("TRACKME_LOAD_TEST_API_KEY", "load-test-api-key")
LOAD_TEST_BASE_URL = os.environ.get("TRACKME_LOAD_TEST_BASE_URL", "/api/v1")

# Sample data pools
PROCESS_NAMES = [
    "chrome.exe", "firefox.exe", "code.exe", "teams.exe", "outlook.exe",
    "slack.exe", "explorer.exe", "notepad++.exe", "terminal.exe", "excel.exe",
    "word.exe", "powershell.exe", "devenv.exe", "rider64.exe", "figma.exe",
    "zoom.exe", "discord.exe", "postman.exe", "docker.exe", "pgadmin4.exe",
]

WINDOW_TITLES = [
    "Dashboard - TrackMe", "Pull Request #42", "Slack - General",
    "Outlook - Inbox", "VS Code - main.py", "Teams Meeting",
    "Excel - Q1 Report.xlsx", "Figma - Design System", "Terminal - bash",
    "Chrome - Stack Overflow", "Docker Desktop", "Postman - API Tests",
    "Word - Proposal.docx", "Zoom Meeting", "PgAdmin - trackme_db",
]

DOMAINS = [
    "github.com", "stackoverflow.com", "google.com", "docs.python.org",
    "learn.microsoft.com", "slack.com", "figma.com", "notion.so",
    "jira.atlassian.net", "confluence.atlassian.com", "aws.amazon.com",
    "console.cloud.google.com", "portal.azure.com", "npmjs.com",
    "pypi.org", "hub.docker.com", "reddit.com", "news.ycombinator.com",
]

URL_PATHS = [
    "/dashboard", "/issues", "/search?q=python", "/docs/api",
    "/pull/123", "/settings", "/profile", "/notifications",
    "/projects/trackme", "/wiki/getting-started", "/releases/latest",
]


def _random_string(length: int = 8) -> str:
    """Generate a random alphanumeric string."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_iso_timestamp(minutes_ago_max: int = 60) -> str:
    """Generate a random ISO timestamp within the last N minutes."""
    offset = random.randint(0, minutes_ago_max)
    dt = datetime.now(timezone.utc) - timedelta(minutes=offset)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Locust user class
# ---------------------------------------------------------------------------

class TrackMeAgent(HttpUser):
    """Simulates a TrackMe desktop agent sending telemetry to the backend.

    Each virtual user represents one desktop agent that periodically
    sends heartbeats, activity sessions, app usage, and URL visit data.
    """

    wait_time = between(30, 60)

    def on_start(self) -> None:
        """Initialize agent identity and authenticate."""
        self.agent_id = str(uuid.uuid4())
        self.device_id = str(uuid.uuid4())
        self.api_key = LOAD_TEST_API_KEY
        self.hostname = f"AGENT-{_random_string(6).upper()}"

        # Set default headers for all requests
        self.client.headers.update({
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Agent-Id": self.agent_id,
        })

        # Attempt registration (may fail if already registered, that's OK)
        self._register()

    def _register(self) -> None:
        """Register this agent with the backend."""
        with self.client.post(
            f"{LOAD_TEST_BASE_URL}/agents/register",
            json={
                "hostname": self.hostname,
                "os_version": "Windows 11 Pro 10.0.26200",
                "agent_version": "1.0.0",
                "user_email": f"loadtest-{_random_string(6)}@trackme-test.com",
            },
            name="/api/v1/agents/register",
            catch_response=True,
        ) as response:
            if response.status_code in (201, 200, 409):
                response.success()
                if response.status_code == 201:
                    data = response.json()
                    self.device_id = data.get("device_id", self.device_id)
                    self.api_key = data.get("api_key", self.api_key)
                    self.client.headers["X-API-Key"] = self.api_key
            else:
                response.failure(f"Registration failed: {response.status_code}")

    @task(5)
    @tag("heartbeat")
    def send_heartbeat(self) -> None:
        """Send a heartbeat signal to the backend."""
        self.client.post(
            f"{LOAD_TEST_BASE_URL}/agents/heartbeat",
            json={
                "agent_version": "1.0.0",
                "cpu_usage": round(random.uniform(5.0, 95.0), 1),
                "ram_usage": round(random.uniform(20.0, 90.0), 1),
                "queue_depth": random.randint(0, 100),
            },
            name="/api/v1/agents/heartbeat",
        )

    @task(3)
    @tag("activity")
    def upload_activity_sessions(self) -> None:
        """Upload a batch of activity sessions."""
        session_count = random.randint(1, 5)
        sessions = []

        for _ in range(session_count):
            start_offset = random.randint(5, 55)
            duration = random.randint(5, 30)
            active_seconds = duration * 60 - random.randint(0, duration * 10)
            idle_seconds = duration * 60 - active_seconds

            start_time = datetime.now(timezone.utc) - timedelta(minutes=start_offset)
            end_time = start_time + timedelta(minutes=duration)

            sessions.append({
                "client_id": str(uuid.uuid4()),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "active_seconds": max(active_seconds, 0),
                "idle_seconds": max(idle_seconds, 0),
            })

        self.client.post(
            f"{LOAD_TEST_BASE_URL}/activity/sessions",
            json={
                "device_id": self.device_id,
                "sessions": sessions,
            },
            name="/api/v1/activity/sessions [POST]",
        )

    @task(2)
    @tag("apps")
    def upload_app_usage(self) -> None:
        """Upload a batch of application usage records."""
        record_count = random.randint(3, 10)
        records = []

        for _ in range(record_count):
            process = random.choice(PROCESS_NAMES)
            title = random.choice(WINDOW_TITLES)
            start_time = _random_iso_timestamp(minutes_ago_max=30)
            duration = random.randint(10, 600)

            records.append({
                "process_name": process,
                "window_title": title,
                "start_time": start_time,
                "duration_seconds": duration,
            })

        self.client.post(
            f"{LOAD_TEST_BASE_URL}/apps/usage",
            json={
                "device_id": self.device_id,
                "records": records,
            },
            name="/api/v1/apps/usage [POST]",
        )

    @task(2)
    @tag("urls")
    def upload_url_visits(self) -> None:
        """Upload a batch of URL visit records."""
        visit_count = random.randint(2, 8)
        visits = []

        for _ in range(visit_count):
            domain = random.choice(DOMAINS)
            path = random.choice(URL_PATHS)
            url = f"https://{domain}{path}"
            visited_at = _random_iso_timestamp(minutes_ago_max=30)
            duration = random.randint(5, 300)

            visits.append({
                "url": url,
                "title": f"{domain} - {path.strip('/')}",
                "visited_at": visited_at,
                "duration_seconds": duration,
            })

        self.client.post(
            f"{LOAD_TEST_BASE_URL}/urls/visits",
            json={
                "device_id": self.device_id,
                "visits": visits,
            },
            name="/api/v1/urls/visits [POST]",
        )
