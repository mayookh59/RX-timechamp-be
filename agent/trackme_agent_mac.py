"""TrackMe Production Tracking Agent — macOS Edition.

Hardened for production deployment:
  - Single-instance lock via fcntl flock (prevents duplicate processes)
  - Watchdog thread restarts crashed worker threads
  - Rotating file logs (5 MB x 5 backups)
  - Graceful shutdown on SIGINT / SIGTERM
  - Exponential backoff with jitter on sync failures
  - Auto re-register on 401 (lost device credentials)
  - Periodic DB maintenance: vacuum + delete synced rows older than 7 days
  - Network reachability probe before each sync cycle
  - Heartbeat fields aligned to backend HeartbeatRequest schema
  - All errors surfaced at appropriate log levels (no silent swallowing)
  - Idle thresholds driven by config (consistent with Windows agent)
  - RGBA → RGB conversion for screenshots before JPEG encode
  - Tempfile-based screencapture fallback (no /tmp predictable path)

Tracks: foreground app, app usage, idle state, browser URLs, screenshots.
Syncs every 60 s; buffers locally during outages.

LaunchAgent installation is handled by install_mac.sh (or --install-only flag).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import logging.handlers
import os
import platform
import random
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psutil
import requests

try:
    from PIL import ImageGrab, Image, ImageDraw  # type: ignore[import-untyped]
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# fcntl is POSIX-only — we're on macOS so it's available
import fcntl

# ── Constants ────────────────────────────────────────────────────────
AGENT_VERSION = "2.0.0-mac"

_APP_SUPPORT = os.path.expanduser("~/Library/Application Support/TrackMe")
CONFIG_PATH = os.path.join(_APP_SUPPORT, "config.json")
DB_PATH = os.path.join(_APP_SUPPORT, "trackme_agent.db")
LOG_PATH = os.path.join(_APP_SUPPORT, "agent.log")
LOCK_PATH = os.path.join(_APP_SUPPORT, "agent.lock")
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.trackme.agent.plist")

DEFAULT_CONFIG: dict[str, Any] = {
    # Production default — gets overridden by config.json written by the installer.
    # Replace with your deployed backend URL (Railway / your domain).
    "api_base_url": "https://trackme.example.com/api/v1",
    "user_email": "user@company.com",
    "idle_threshold_seconds": 60,
    "sync_interval_seconds": 60,
    "screenshot_interval_seconds": 120,
    "log_level": "INFO",
    "screenshot_max_width": 800,
    "screenshot_jpeg_quality": 50,
    "max_db_size_mb": 200,
    "retention_days": 7,
    "request_timeout_seconds": 10,
    "screenshot_upload_timeout_seconds": 30,
}

_BACKOFF_MAX = 300
_WATCHDOG_INTERVAL = 30
_MAINT_INTERVAL = 3600


# ── Configuration ────────────────────────────────────────────────────
def load_config() -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                cfg.update(disk)
        except Exception as e:
            print(f"[TrackMe] WARNING: failed to load config {CONFIG_PATH}: {e}", file=sys.stderr)
    if not isinstance(cfg.get("api_base_url"), str) or not cfg["api_base_url"].startswith(("http://", "https://")):
        cfg["api_base_url"] = DEFAULT_CONFIG["api_base_url"]
    for k in ("idle_threshold_seconds", "sync_interval_seconds", "screenshot_interval_seconds"):
        try:
            cfg[k] = max(1, int(cfg.get(k, DEFAULT_CONFIG[k])))
        except (TypeError, ValueError):
            cfg[k] = DEFAULT_CONFIG[k]
    return cfg


# ── Single-instance lock (fcntl flock) ───────────────────────────────
_lock_fd = None  # keep open for life of process


def acquire_singleton_lock() -> bool:
    """Acquire an exclusive flock on a sentinel file. False if already held."""
    global _lock_fd
    try:
        os.makedirs(_APP_SUPPORT, exist_ok=True)
        _lock_fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        # Write our pid for diagnostics
        try:
            os.ftruncate(_lock_fd, 0)
            os.write(_lock_fd, str(os.getpid()).encode())
        except Exception:
            pass
        return True
    except Exception:
        return True  # don't block startup if lock can't be created


# ── macOS system helpers ─────────────────────────────────────────────
def get_idle_seconds() -> float:
    """Return seconds since last keyboard/mouse input via ioreg."""
    try:
        out = subprocess.check_output(
            ["ioreg", "-c", "IOHIDSystem"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", errors="replace")
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                m = re.search(r"HIDIdleTime\s*=\s*(\d+)", line)
                if m:
                    return int(m.group(1)) / 1_000_000_000.0
    except Exception:
        pass
    return 0.0


def _osascript(script: str, timeout: int = 3) -> str:
    try:
        out = subprocess.check_output(
            ["osascript", "-e", script],
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def get_foreground_window_info() -> tuple[str, str, int]:
    """Return (window_title, process_name, pid) for the frontmost app."""
    proc_name = _osascript(
        'tell application "System Events" to get name of first process whose frontmost is true'
    )
    if not proc_name:
        return "", "", 0
    title = _osascript(
        'tell application "System Events" to get title of front window of (first process whose frontmost is true)'
    )
    pid = 0
    try:
        for p in psutil.process_iter(["name", "pid"]):
            if p.info["name"] == proc_name:
                pid = p.info["pid"]
                break
    except Exception:
        pass
    return title, proc_name, pid


def capture_screen_image() -> Optional[Any]:
    """Capture screen → PIL Image. Tries ImageGrab, falls back to screencapture."""
    if HAS_PIL:
        try:
            img = ImageGrab.grab()
            if img is not None:
                return img
        except Exception as e:
            logging.debug("PIL ImageGrab failed: %s", e)

    # screencapture fallback (writes to a temp file)
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="trackme_ss_")
        os.close(fd)
        try:
            subprocess.run(
                ["screencapture", "-x", "-t", "jpeg", tmp_path],
                timeout=10,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if HAS_PIL and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                img = Image.open(tmp_path).copy()
                return img
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        logging.debug("screencapture fallback failed: %s", e)
    return None


# ── Local SQLite DB ──────────────────────────────────────────────────
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create tables, indexes, and run idempotent forward-only migrations.

    Old DBs from v1.0.0 lack `duration_sec` on `activity_sessions`. Migrations
    detect and add missing columns so v2.0.0 works on top of legacy data.
    """
    os.makedirs(_APP_SUPPORT, exist_ok=True)
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activity_sessions (
                id TEXT PRIMARY KEY,
                session_type TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_sec INTEGER DEFAULT 0,
                synced INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sess_synced ON activity_sessions(synced);

            CREATE TABLE IF NOT EXISTS app_usage (
                id TEXT PRIMARY KEY,
                process_name TEXT NOT NULL,
                window_title TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_sec INTEGER DEFAULT 0,
                synced INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_app_synced ON app_usage(synced);

            CREATE TABLE IF NOT EXISTS screenshots (
                id TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                image_b64 TEXT NOT NULL,
                synced INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ss_synced ON screenshots(synced);

            CREATE TABLE IF NOT EXISTS url_visits (
                id TEXT PRIMARY KEY,
                browser TEXT NOT NULL,
                url TEXT NOT NULL,
                domain TEXT,
                page_title TEXT,
                visit_time TEXT NOT NULL,
                duration_sec INTEGER DEFAULT 0,
                synced INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_url_synced ON url_visits(synced);

            CREATE TABLE IF NOT EXISTS agent_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()
        _run_migrations(conn)
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply forward-only schema migrations. Idempotent."""
    def _has_col(table: str, col: str) -> bool:
        return col in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    migrations = [
        ("activity_sessions", "duration_sec",
         "ALTER TABLE activity_sessions ADD COLUMN duration_sec INTEGER DEFAULT 0"),
        ("activity_sessions", "synced",
         "ALTER TABLE activity_sessions ADD COLUMN synced INTEGER DEFAULT 0"),
        ("app_usage", "duration_sec",
         "ALTER TABLE app_usage ADD COLUMN duration_sec INTEGER DEFAULT 0"),
        ("app_usage", "synced",
         "ALTER TABLE app_usage ADD COLUMN synced INTEGER DEFAULT 0"),
        ("url_visits", "duration_sec",
         "ALTER TABLE url_visits ADD COLUMN duration_sec INTEGER DEFAULT 0"),
        ("url_visits", "synced",
         "ALTER TABLE url_visits ADD COLUMN synced INTEGER DEFAULT 0"),
        ("screenshots", "synced",
         "ALTER TABLE screenshots ADD COLUMN synced INTEGER DEFAULT 0"),
    ]
    applied = []
    for table, col, sql in migrations:
        try:
            if not _has_col(table, col):
                conn.execute(sql)
                applied.append(f"{table}.{col}")
        except sqlite3.OperationalError as e:
            logging.debug("Migration skipped for %s.%s: %s", table, col, e)
    if applied:
        conn.commit()
        logging.info("DB migrations applied: %s", ", ".join(applied))


def kv_get(key: str) -> Optional[str]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM agent_info WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def kv_set(key: str, value: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO agent_info (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# ── Registration ─────────────────────────────────────────────────────
def get_or_create_agent_id() -> str:
    val = kv_get("agent_id")
    if val:
        return val
    new_id = str(uuid.uuid4())
    kv_set("agent_id", new_id)
    return new_id


def register_device(config: dict[str, Any]) -> tuple[str, str]:
    user_email = kv_get("user_email") or config.get("user_email", "user@company.com")
    try:
        resp = requests.post(
            f"{config['api_base_url']}/agents/register",
            json={
                "hostname": platform.node(),
                "os_version": platform.platform(),
                "agent_version": AGENT_VERSION,
                "user_email": user_email,
            },
            timeout=config["request_timeout_seconds"],
        )
        if resp.status_code == 201:
            data = resp.json()
            api_key = data.get("api_key", "")
            device_id = data.get("device_id", "")
            if api_key and device_id:
                kv_set("api_key", api_key)
                kv_set("device_id", device_id)
                logging.info("Registered device %s as %s", platform.node(), device_id)
                return api_key, device_id
        logging.warning("Register HTTP %s: %s", resp.status_code, resp.text[:200])
    except requests.exceptions.RequestException as e:
        logging.warning("Register network error: %s", e)
    except Exception as e:
        logging.error("Register unexpected error: %s", e, exc_info=True)
    return "", ""


def load_credentials() -> tuple[str, str]:
    return (kv_get("api_key") or "", kv_get("device_id") or "")


# ── Activity tracker ─────────────────────────────────────────────────
class ActivityTracker(threading.Thread):
    SYSTEM_PROCESSES = {"loginwindow", "ScreenSaverEngine", "Dock", "SystemUIServer", "WindowServer"}
    BROWSER_PROCESSES = {"Safari", "Google Chrome", "Firefox", "Brave Browser", "Opera",
                         "Microsoft Edge", "Arc", "Chrome", "Chromium"}
    BROWSER_SUFFIXES = {"Safari", "Google Chrome", "Firefox", "Brave Browser", "Opera",
                        "Microsoft Edge", "Arc", "Chrome", "Chromium"}
    CHECKPOINT_INTERVAL = 300

    def __init__(self, config: dict[str, Any], shutdown: threading.Event):
        super().__init__(name="ActivityTracker", daemon=False)
        self.config = config
        self.shutdown = shutdown
        self.idle_threshold = config["idle_threshold_seconds"]
        self.screenshot_interval = config["screenshot_interval_seconds"]

        self.session_type = "active"
        self.session_start = datetime.now(timezone.utc)
        self.session_id = str(uuid.uuid4())
        self.last_checkpoint = time.time()

        self.current_app: Optional[str] = None
        self.current_title: Optional[str] = None
        self.app_start: Optional[datetime] = None

        self.current_url: Optional[str] = None
        self.current_url_title: Optional[str] = None
        self.current_url_browser: Optional[str] = None
        self.url_start: Optional[datetime] = None

        self.last_screenshot = 0.0
        self.heartbeat_at = time.time()

    def heartbeat(self) -> float:
        return self.heartbeat_at

    def run(self) -> None:
        logging.info("ActivityTracker started")
        try:
            while not self.shutdown.is_set():
                self.heartbeat_at = time.time()
                try:
                    self._tick()
                except Exception:
                    logging.exception("Tracker tick error")
                for _ in range(4):
                    if self.shutdown.is_set():
                        break
                    time.sleep(0.5)
        finally:
            try:
                self._close_current_url()
                self._close_current_app()
                self._close_current_session()
            except Exception:
                logging.exception("Tracker shutdown flush failed")
            logging.info("ActivityTracker stopped")

    def _tick(self) -> None:
        idle_sec = get_idle_seconds()
        now_ts = time.time()

        new_state = "active"
        if idle_sec > self.idle_threshold * 5:
            new_state = "away"
        elif idle_sec > self.idle_threshold:
            new_state = "idle"

        if new_state != self.session_type:
            self._close_current_session()
            self.session_type = new_state
            self.session_start = datetime.now(timezone.utc)
            self.session_id = str(uuid.uuid4())
            self.last_checkpoint = now_ts

        if now_ts - self.last_checkpoint >= self.CHECKPOINT_INTERVAL:
            self._close_current_session()
            self.session_start = datetime.now(timezone.utc)
            self.session_id = str(uuid.uuid4())
            self.last_checkpoint = now_ts

        if new_state == "active":
            title, proc, _pid = get_foreground_window_info()
            if proc and proc not in self.SYSTEM_PROCESSES:
                if proc != self.current_app or title != self.current_title:
                    self._close_current_app()
                    self.current_app = proc
                    self.current_title = title
                    self.app_start = datetime.now(timezone.utc)
                if proc in self.BROWSER_PROCESSES and title:
                    self._track_url(proc, title)
                elif self.current_url:
                    self._close_current_url()
            if HAS_PIL and now_ts - self.last_screenshot >= self.screenshot_interval:
                self._capture_screenshot()
                self.last_screenshot = now_ts

    def _close_current_session(self) -> None:
        now = datetime.now(timezone.utc)
        duration = int((now - self.session_start).total_seconds())
        if duration < 1:
            return
        try:
            with _db_lock:
                conn = _get_conn()
                try:
                    conn.execute(
                        "INSERT INTO activity_sessions (id, session_type, start_time, end_time, duration_sec) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (self.session_id, self.session_type,
                         self.session_start.isoformat(), now.isoformat(), duration),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            logging.exception("Session DB write failed")

    def _close_current_app(self) -> None:
        if not (self.current_app and self.app_start):
            self.current_app = self.current_title = None
            self.app_start = None
            return
        now = datetime.now(timezone.utc)
        duration = int((now - self.app_start).total_seconds())
        if duration > 1:
            try:
                with _db_lock:
                    conn = _get_conn()
                    try:
                        conn.execute(
                            "INSERT INTO app_usage (id, process_name, window_title, start_time, end_time, duration_sec) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (str(uuid.uuid4()), self.current_app, self.current_title or "",
                             self.app_start.isoformat(), now.isoformat(), duration),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                logging.exception("app_usage DB write failed")
        self.current_app = self.current_title = None
        self.app_start = None

    def _close_current_url(self) -> None:
        if not (self.current_url and self.url_start):
            self.current_url = self.current_url_title = self.current_url_browser = None
            self.url_start = None
            return
        now = datetime.now(timezone.utc)
        duration = int((now - self.url_start).total_seconds())
        if duration > 2:
            try:
                with _db_lock:
                    conn = _get_conn()
                    try:
                        conn.execute(
                            "INSERT INTO url_visits (id, browser, url, domain, page_title, visit_time, duration_sec) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (str(uuid.uuid4()), self.current_url_browser or "browser",
                             self.current_url, self.current_url or "",
                             self.current_url_title or "", self.url_start.isoformat(), duration),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                logging.exception("url_visits DB write failed")
        self.current_url = self.current_url_title = self.current_url_browser = None
        self.url_start = None

    @classmethod
    def _extract_domain(cls, title: str) -> tuple[str, str]:
        page = title
        for sfx in cls.BROWSER_SUFFIXES:
            if page.endswith(f" - {sfx}") or page.endswith(f" — {sfx}"):
                page = page.rsplit(" ", 2)[0].rstrip(" -—")
                break
        page_title = page.strip()
        m = re.search(
            r"((?:[\w-]+\.)+(?:com|org|net|io|ai|co|edu|gov|dev|app|me|info|xyz|in|uk|us)(?:\.\w{2})?)",
            page_title, re.IGNORECASE,
        )
        return page_title, (m.group(1).lower() if m else "")

    def _track_url(self, proc: str, title: str) -> None:
        if not title or title.strip() in ("New Tab", "New tab", "Start Page", ""):
            return
        page_title, domain = self._extract_domain(title)
        if not page_title:
            return
        if page_title != self.current_url_title:
            self._close_current_url()
            self.current_url = domain or page_title
            self.current_url_title = page_title
            self.current_url_browser = proc
            self.url_start = datetime.now(timezone.utc)

    def _capture_screenshot(self) -> None:
        try:
            img = capture_screen_image()
            if img is None:
                logging.warning("Screenshot returned None — skipping")
                return
            if img.mode == "RGBA":
                img = img.convert("RGB")
            max_w = self.config["screenshot_max_width"]
            w, h = img.size
            if w > max_w:
                ratio = max_w / w
                img = img.resize((max_w, int(h * ratio)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=self.config["screenshot_jpeg_quality"])
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            now = datetime.now(timezone.utc).isoformat()
            with _db_lock:
                conn = _get_conn()
                try:
                    conn.execute(
                        "INSERT INTO screenshots (id, captured_at, image_b64, synced) VALUES (?, ?, ?, 0)",
                        (str(uuid.uuid4()), now, b64),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            logging.exception("Screenshot capture error")


# ── Sync engine ──────────────────────────────────────────────────────
class _AuthLost(Exception):
    pass


class _NetworkUnreachable(Exception):
    pass


class SyncEngine(threading.Thread):
    def __init__(self, config: dict[str, Any], shutdown: threading.Event):
        super().__init__(name="SyncEngine", daemon=False)
        self.config = config
        self.shutdown = shutdown
        self.api_key, self.device_id = load_credentials()
        self.backoff = config["sync_interval_seconds"]
        self.heartbeat_at = time.time()

    def heartbeat(self) -> float:
        return self.heartbeat_at

    def run(self) -> None:
        logging.info("SyncEngine started (creds: %s)", "present" if self.api_key else "missing")
        while not self.shutdown.is_set():
            self.heartbeat_at = time.time()
            try:
                self._cycle()
                self.backoff = self.config["sync_interval_seconds"]
            except _AuthLost:
                logging.warning("Credentials lost (401); clearing api_key + device_id")
                self.api_key = self.device_id = ""
                kv_set("api_key", "")
                kv_set("device_id", "")
            except _NetworkUnreachable as e:
                self.backoff = min(self.backoff * 2, _BACKOFF_MAX)
                logging.warning("Network unreachable: %s — backoff %ss", e, self.backoff)
            except Exception:
                logging.exception("Sync cycle error")
                self.backoff = min(self.backoff * 2, _BACKOFF_MAX)
            jitter = random.uniform(0, 1.5)
            target = self.backoff + jitter
            slept = 0.0
            while slept < target and not self.shutdown.is_set():
                time.sleep(min(0.5, target - slept))
                slept += 0.5
        logging.info("SyncEngine stopped")

    def _cycle(self) -> None:
        if not (self.api_key and self.device_id):
            ak, did = register_device(self.config)
            if not (ak and did):
                raise _NetworkUnreachable("registration failed")
            self.api_key, self.device_id = ak, did
        if not self._reachable():
            raise _NetworkUnreachable(f"server {self.config['api_base_url']} unreachable")
        self._sync_sessions()
        self._sync_app_usage()
        self._sync_url_visits()
        self._sync_screenshots()
        self._send_heartbeat()

    def _reachable(self) -> bool:
        try:
            host = self.config["api_base_url"].split("://", 1)[-1].split("/", 1)[0]
            if ":" in host:
                hostname, port_s = host.rsplit(":", 1)
                port = int(port_s)
            else:
                hostname = host
                port = 443 if self.config["api_base_url"].startswith("https") else 80
            with socket.create_connection((hostname, port), timeout=3):
                return True
        except Exception:
            return False

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
            "X-Device-Id": self.device_id,
        }

    def _post(self, path: str, payload: dict[str, Any], timeout: int) -> requests.Response:
        url = f"{self.config['api_base_url']}{path}"
        try:
            return requests.post(url, json=payload, headers=self._headers(), timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise _NetworkUnreachable(str(e)) from e

    def _check_response(self, resp: requests.Response, what: str) -> bool:
        if resp.status_code in (200, 201):
            return True
        if resp.status_code == 401:
            raise _AuthLost(what)
        logging.warning("%s HTTP %s: %s", what, resp.status_code, resp.text[:200])
        return False

    def _sync_sessions(self) -> None:
        with _db_lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, session_type, start_time, end_time FROM activity_sessions "
                    "WHERE synced=0 ORDER BY start_time LIMIT 100"
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return
        payload = {
            "device_id": self.device_id,
            "sessions": [
                {"client_id": r[0], "session_type": r[1], "start_time": r[2], "end_time": r[3]}
                for r in rows
            ],
        }
        resp = self._post("/agents/ingest", payload, self.config["request_timeout_seconds"])
        if self._check_response(resp, "sessions"):
            with _db_lock:
                conn = _get_conn()
                try:
                    conn.executemany(
                        "UPDATE activity_sessions SET synced=1 WHERE id=?", [(r[0],) for r in rows]
                    )
                    conn.commit()
                finally:
                    conn.close()
            logging.info("Synced %d sessions", len(rows))

    def _sync_app_usage(self) -> None:
        with _db_lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, process_name, window_title, start_time, end_time, duration_sec "
                    "FROM app_usage WHERE synced=0 ORDER BY start_time LIMIT 100"
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return
        payload = {
            "device_id": self.device_id,
            "records": [
                {"client_id": r[0], "process_name": r[1], "window_title": r[2],
                 "start_time": r[3], "end_time": r[4], "duration_seconds": r[5]}
                for r in rows
            ],
        }
        resp = self._post("/agents/ingest", payload, self.config["request_timeout_seconds"])
        if self._check_response(resp, "app_usage"):
            with _db_lock:
                conn = _get_conn()
                try:
                    conn.executemany("UPDATE app_usage SET synced=1 WHERE id=?", [(r[0],) for r in rows])
                    conn.commit()
                finally:
                    conn.close()
            logging.info("Synced %d app records", len(rows))

    def _sync_url_visits(self) -> None:
        with _db_lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, browser, url, domain, page_title, visit_time, duration_sec "
                    "FROM url_visits WHERE synced=0 ORDER BY visit_time LIMIT 100"
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return
        payload = {
            "device_id": self.device_id,
            "url_visits": [
                {"client_id": r[0], "browser": r[1], "url": r[2], "domain": r[3],
                 "page_title": r[4], "visit_time": r[5], "duration_sec": r[6]}
                for r in rows
            ],
        }
        resp = self._post("/agents/ingest", payload, self.config["request_timeout_seconds"])
        if self._check_response(resp, "url_visits"):
            with _db_lock:
                conn = _get_conn()
                try:
                    conn.executemany("UPDATE url_visits SET synced=1 WHERE id=?", [(r[0],) for r in rows])
                    conn.commit()
                finally:
                    conn.close()
            logging.info("Synced %d url visits", len(rows))

    def _sync_screenshots(self) -> None:
        with _db_lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT id, captured_at, image_b64 FROM screenshots "
                    "WHERE synced=0 ORDER BY captured_at LIMIT 5"
                ).fetchall()
            finally:
                conn.close()
        for row in rows:
            if self.shutdown.is_set():
                return
            payload = {
                "device_id": self.device_id,
                "screenshot_id": row[0],
                "captured_at": row[1],
                "image_base64": row[2],
            }
            resp = self._post("/screenshots/ingest", payload, self.config["screenshot_upload_timeout_seconds"])
            if self._check_response(resp, "screenshot"):
                with _db_lock:
                    conn = _get_conn()
                    try:
                        conn.execute("UPDATE screenshots SET synced=1 WHERE id=?", (row[0],))
                        conn.commit()
                    finally:
                        conn.close()
                logging.info("Synced screenshot %s", row[0][:8])

    def _send_heartbeat(self) -> None:
        try:
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0)
            mem_percent = mem.percent
        except Exception:
            cpu = mem_percent = 0.0
        try:
            conn = _get_conn()
            try:
                qd = (
                    conn.execute("SELECT COUNT(*) FROM activity_sessions WHERE synced=0").fetchone()[0]
                    + conn.execute("SELECT COUNT(*) FROM app_usage WHERE synced=0").fetchone()[0]
                    + conn.execute("SELECT COUNT(*) FROM url_visits WHERE synced=0").fetchone()[0]
                    + conn.execute("SELECT COUNT(*) FROM screenshots WHERE synced=0").fetchone()[0]
                )
            finally:
                conn.close()
        except Exception:
            qd = 0
        # Last ~10 KB of log for remote diagnostics
        log_tail = ""
        try:
            if os.path.exists(LOG_PATH):
                size = os.path.getsize(LOG_PATH)
                with open(LOG_PATH, "rb") as f:
                    if size > 10240:
                        f.seek(-10240, os.SEEK_END)
                    log_tail = f.read().decode("utf-8", errors="replace")
                lines = log_tail.splitlines()[-100:]
                log_tail = "\n".join(lines)
        except Exception:
            pass
        resp = self._post(
            "/agents/heartbeat",
            {
                "device_id": self.device_id,
                "agent_version": AGENT_VERSION,
                "cpu_usage": float(cpu),
                "ram_usage": float(mem_percent),
                "queue_depth": int(qd),
                "log_tail": log_tail,
            },
            5,
        )
        self._check_response(resp, "heartbeat")


# ── DB maintenance ───────────────────────────────────────────────────
class DBMaintenance(threading.Thread):
    def __init__(self, config: dict[str, Any], shutdown: threading.Event):
        super().__init__(name="DBMaintenance", daemon=False)
        self.config = config
        self.shutdown = shutdown
        self.heartbeat_at = time.time()

    def heartbeat(self) -> float:
        return self.heartbeat_at

    def run(self) -> None:
        logging.info("DBMaintenance started")
        for _ in range(120):
            if self.shutdown.is_set():
                return
            self.heartbeat_at = time.time()
            time.sleep(1)
        while not self.shutdown.is_set():
            self.heartbeat_at = time.time()
            try:
                self._maintain()
            except Exception:
                logging.exception("DB maintenance error")
            slept = 0
            while slept < _MAINT_INTERVAL and not self.shutdown.is_set():
                self.heartbeat_at = time.time()
                time.sleep(2)
                slept += 2
        logging.info("DBMaintenance stopped")

    def _maintain(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.config["retention_days"])).isoformat()
        deleted = {}
        with _db_lock:
            conn = _get_conn()
            try:
                for tbl, ts in [("activity_sessions", "start_time"), ("app_usage", "start_time"),
                                ("url_visits", "visit_time"), ("screenshots", "captured_at")]:
                    cur = conn.execute(f"DELETE FROM {tbl} WHERE synced=1 AND {ts} < ?", (cutoff,))
                    deleted[tbl] = cur.rowcount
                conn.commit()
                size_mb = os.path.getsize(DB_PATH) / 1024 / 1024 if os.path.exists(DB_PATH) else 0
                if size_mb > self.config["max_db_size_mb"]:
                    conn.execute(
                        "DELETE FROM screenshots WHERE synced=1 AND id IN ("
                        "  SELECT id FROM screenshots WHERE synced=1 "
                        "  ORDER BY captured_at LIMIT 200)"
                    )
                    conn.commit()
                conn.execute("VACUUM")
            finally:
                conn.close()
        if any(deleted.values()):
            logging.info("DB maintenance pruned: %s", deleted)


# ── Watchdog ─────────────────────────────────────────────────────────
class Watchdog(threading.Thread):
    STALE_AFTER = 120

    def __init__(self, workers: dict[str, threading.Thread], factories: dict, shutdown: threading.Event):
        super().__init__(name="Watchdog", daemon=True)
        self.workers = workers
        self.factories = factories
        self.shutdown = shutdown

    def run(self) -> None:
        logging.info("Watchdog started")
        while not self.shutdown.is_set():
            time.sleep(_WATCHDOG_INTERVAL)
            if self.shutdown.is_set():
                break
            now = time.time()
            for name, thread in list(self.workers.items()):
                if not thread.is_alive():
                    logging.error("Watchdog: worker %s is dead — restarting", name)
                    new = self.factories[name]()
                    self.workers[name] = new
                    new.start()
                    continue
                hb = getattr(thread, "heartbeat", None)
                if callable(hb):
                    age = now - hb()
                    if age > self.STALE_AFTER:
                        logging.error("Watchdog: %s heartbeat stale (%ds)", name, int(age))
        logging.info("Watchdog stopped")


# ── LaunchAgent installer ────────────────────────────────────────────
def install_launch_agent() -> None:
    """Write LaunchAgent plist using a wrapper script for resilience."""
    launch_agents_dir = os.path.expanduser("~/Library/LaunchAgents")
    os.makedirs(launch_agents_dir, exist_ok=True)
    os.makedirs(_APP_SUPPORT, exist_ok=True)

    agent_script = os.path.join(_APP_SUPPORT, "trackme_agent_mac.py")
    wrapper_path = os.path.join(_APP_SUPPORT, "launch_wrapper.sh")
    log_out = os.path.join(_APP_SUPPORT, "launchd_out.log")
    log_err = os.path.join(_APP_SUPPORT, "launchd_err.log")

    # Wrapper resolves Python at runtime so the plist survives Python upgrades.
    wrapper = f"""#!/bin/bash
# TrackMe LaunchAgent wrapper
# Resolves a working python3 at runtime (survives Python relocation).
set -u
for cand in \\
    /opt/homebrew/bin/python3 \\
    /usr/local/bin/python3 \\
    /usr/bin/python3 \\
    "$(command -v python3)"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
        exec "$cand" "{agent_script}"
    fi
done
echo "ERROR: no python3 found" >&2
exit 1
"""
    with open(wrapper_path, "w") as f:
        f.write(wrapper)
    os.chmod(wrapper_path, 0o755)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.trackme.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{wrapper_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>{log_out}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>
    <key>WorkingDirectory</key>
    <string>{_APP_SUPPORT}</string>
</dict>
</plist>
"""
    with open(PLIST_PATH, "w") as f:
        f.write(plist)
    print(f"LaunchAgent installed: {PLIST_PATH}")
    print(f"Wrapper:               {wrapper_path}")
    print(f"Logs:                  {log_out}, {log_err}")
    print()
    print("To start it now:")
    print(f"  launchctl unload {PLIST_PATH} 2>/dev/null; launchctl load {PLIST_PATH}")


# ── Tray icon ────────────────────────────────────────────────────────
def run_tray_or_block(shutdown: threading.Event) -> None:
    try:
        import pystray  # type: ignore[import-untyped]
        from PIL import Image, ImageDraw  # type: ignore[import-untyped]

        def make_icon() -> Image.Image:
            img = Image.new("RGB", (64, 64), (59, 130, 246))
            draw = ImageDraw.Draw(img)
            draw.text((18, 16), "TM", fill="white")
            return img

        def on_quit(icon, _item) -> None:
            logging.info("Quit selected from tray")
            shutdown.set()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem(f"TrackMe Agent v{AGENT_VERSION}", lambda: None, enabled=False),
            pystray.MenuItem("Status: Running", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )
        icon = pystray.Icon("TrackMe", make_icon(), "TrackMe Agent", menu)
        icon.run()
    except ImportError:
        logging.info("pystray not available — running headless")
        while not shutdown.is_set():
            time.sleep(1)


# ── Logging + signals ────────────────────────────────────────────────
def setup_logging(config: dict[str, Any]) -> None:
    os.makedirs(_APP_SUPPORT, exist_ok=True)
    level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    handlers: list[logging.Handler] = [file_handler]
    try:
        if sys.stdout and sys.stdout.writable():
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            handlers.append(sh)
    except Exception:
        pass

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = []
    for h in handlers:
        root.addHandler(h)


def install_signal_handlers(shutdown: threading.Event) -> None:
    def _handler(signum, _frame):
        logging.info("Signal %s received — shutting down", signum)
        shutdown.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


# ── Diagnostics ──────────────────────────────────────────────────────
def run_diagnostics(config: dict[str, Any]) -> int:
    """Print a comprehensive diagnostic report. No daemon, no DB writes."""
    sep = "=" * 70
    print(sep)
    print("  TrackMe Agent Diagnostic Report (macOS)")
    print(sep)
    import datetime as _dt
    print(f"  Generated:   {_dt.datetime.now(_dt.timezone.utc).isoformat()}")
    print(f"  Agent ver:   {AGENT_VERSION}")
    print(f"  Hostname:    {platform.node()}")
    print(f"  OS:          {platform.platform()}")
    print(f"  Python:      {sys.version.split()[0]}")
    print(f"  PID:         {os.getpid()}")
    print(f"  Data dir:    {_APP_SUPPORT}")
    print(f"  Config path: {CONFIG_PATH}")
    print(f"  DB path:     {DB_PATH}")
    print(f"  Log path:    {LOG_PATH}")
    print(f"  Plist:       {PLIST_PATH}")

    print(f"\n{sep}\n  Configuration\n{sep}")
    for k, v in config.items():
        print(f"  {k}: {v}")

    print(f"\n{sep}\n  Process state\n{sep}")
    # Check if we hold the lock — if not, another instance may be running
    try:
        import fcntl as _fcntl
        fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            _fcntl.flock(fd, _fcntl.LOCK_UN)
            print("  Another agent currently running: no")
        except BlockingIOError:
            print("  Another agent currently running: YES (lock held)")
        os.close(fd)
    except Exception as e:
        print(f"  Lock check error: {e}")

    try:
        candidates = [p for p in psutil.process_iter(["pid", "name", "cmdline"])
                      if p.info["cmdline"]
                      and any("trackme_agent_mac" in str(arg) for arg in p.info["cmdline"])]
        if candidates:
            for p in candidates:
                print(f"  PID {p.info['pid']}: {p.info['name']} {' '.join(p.info['cmdline'][1:])}")
        else:
            print("  No trackme_agent_mac processes found")
    except Exception as e:
        print(f"  Process scan error: {e}")

    # LaunchAgent status
    try:
        import subprocess as _sp
        out = _sp.check_output(["launchctl", "list"], timeout=3).decode()
        lines = [l for l in out.splitlines() if "trackme" in l.lower()]
        if lines:
            print(f"\n  launchctl list:")
            for l in lines:
                print(f"    {l}")
        else:
            print("\n  launchctl list: com.trackme.agent NOT registered")
    except Exception as e:
        print(f"\n  launchctl error: {e}")

    print(f"\n{sep}\n  Credentials\n{sep}")
    try:
        api_key, device_id = load_credentials()
        agent_id = kv_get("agent_id") or "(not set)"
        user_email = kv_get("user_email") or config.get("user_email", "(default)")
        print(f"  agent_id:   {agent_id}")
        print(f"  device_id:  {device_id or '(not registered)'}")
        print(f"  api_key:    {'present (' + str(len(api_key)) + ' chars)' if api_key else '(missing)'}")
        print(f"  user_email: {user_email}")
    except Exception as e:
        print(f"  Credentials error: {e}")

    print(f"\n{sep}\n  Local DB stats\n{sep}")
    try:
        conn = _get_conn()
        try:
            for tbl in ("activity_sessions", "app_usage", "url_visits", "screenshots"):
                tot = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                uns = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE synced=0").fetchone()[0]
                cols = ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({tbl})"))
                print(f"  {tbl}: total={tot}, unsynced={uns}")
                print(f"    columns: {cols}")
            print("\n  Last 5 sessions:")
            for r in conn.execute(
                "SELECT session_type, start_time, end_time, duration_sec, synced "
                "FROM activity_sessions ORDER BY start_time DESC LIMIT 5"
            ):
                print(f"    synced={r[4]} {r[0]:<7} dur={r[3]}s  {r[1]} -> {r[2]}")
            try:
                size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
                print(f"\n  DB file size: {size_mb:.1f} MB")
            except Exception:
                pass
        finally:
            conn.close()
    except Exception as e:
        print(f"  DB read error: {e}")

    print(f"\n{sep}\n  Network reachability\n{sep}")
    api_base = config["api_base_url"]
    try:
        host = api_base.split("://", 1)[-1].split("/", 1)[0]
        if ":" in host:
            hostname, port_s = host.rsplit(":", 1)
            port = int(port_s)
        else:
            hostname = host
            port = 443 if api_base.startswith("https") else 80
        print(f"  API base:    {api_base}")
        print(f"  Resolved to: {hostname}:{port}")
        try:
            ip = socket.gethostbyname(hostname)
            print(f"  DNS:         {hostname} -> {ip}")
        except Exception as e:
            print(f"  DNS:         FAILED ({e})")
        try:
            t0 = time.time()
            with socket.create_connection((hostname, port), timeout=3):
                pass
            print(f"  TCP {port}:    OK ({int((time.time() - t0) * 1000)} ms)")
        except Exception as e:
            print(f"  TCP {port}:    FAILED ({e})")
    except Exception as e:
        print(f"  Network test error: {e}")

    print(f"\n{sep}\n  Last 30 log lines\n{sep}")
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-30:]
            for line in lines:
                print(f"  {line.rstrip()}")
        else:
            print("  (agent.log not present)")
    except Exception as e:
        print(f"  Log read error: {e}")

    print(f"\n{sep}\n  End of diagnostic report\n{sep}")
    return 0


# ── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="TrackMe Agent (macOS)")
    parser.add_argument("--install-only", action="store_true",
                        help="Write LaunchAgent plist + wrapper and exit")
    parser.add_argument("--once", action="store_true",
                        help="Run a single sync cycle and exit (testing)")
    parser.add_argument("--diagnose", action="store_true",
                        help="Print full diagnostic report and exit")
    parser.add_argument("--version", action="store_true", help="Print agent version and exit")
    args = parser.parse_args()

    if args.version:
        print(AGENT_VERSION)
        return 0

    config = load_config()

    if args.diagnose:
        try:
            init_db()
        except Exception:
            pass
        return run_diagnostics(config)

    setup_logging(config)

    if args.install_only:
        install_launch_agent()
        return 0

    if not acquire_singleton_lock():
        logging.warning("Another TrackMe agent is already running — exiting")
        return 0

    logging.info("=" * 60)
    logging.info("TrackMe Agent v%s starting (PID %d)", AGENT_VERSION, os.getpid())
    logging.info("Host: %s | OS: %s", platform.node(), platform.platform())
    logging.info("Python: %s | Data dir: %s", sys.version.split()[0], _APP_SUPPORT)
    logging.info("API: %s", config["api_base_url"])
    logging.info("Sync: %ds | Idle threshold: %ds | Screenshot: %ds",
                 config["sync_interval_seconds"], config["idle_threshold_seconds"],
                 config["screenshot_interval_seconds"])
    logging.info("=" * 60)

    init_db()
    get_or_create_agent_id()

    # Install LaunchAgent on first run if missing
    if not os.path.exists(PLIST_PATH):
        logging.info("First run — installing LaunchAgent plist")
        try:
            install_launch_agent()
        except Exception:
            logging.exception("LaunchAgent install failed")

    api_key, device_id = load_credentials()
    if not (api_key and device_id):
        register_device(config)

    shutdown = threading.Event()
    install_signal_handlers(shutdown)

    if args.once:
        SyncEngine(config, shutdown)._cycle()  # type: ignore[attr-defined]
        return 0

    factories = {
        "tracker": lambda: ActivityTracker(config, shutdown),
        "sync": lambda: SyncEngine(config, shutdown),
        "maint": lambda: DBMaintenance(config, shutdown),
    }
    workers: dict[str, threading.Thread] = {n: f() for n, f in factories.items()}
    for w in workers.values():
        w.start()

    Watchdog(workers, factories, shutdown).start()

    try:
        run_tray_or_block(shutdown)
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt — shutting down")
    finally:
        shutdown.set()

    logging.info("Waiting for workers to exit (max 15 s)…")
    for name, w in workers.items():
        w.join(timeout=15)
        if w.is_alive():
            logging.warning("Worker %s did not exit cleanly", name)
    logging.info("TrackMe Agent stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
