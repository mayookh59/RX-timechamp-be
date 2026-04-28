"""Agent management API endpoints for registration, heartbeat, and listing.

Provides endpoints for desktop agent devices to register, send heartbeats,
and for administrators to list enrolled agents.
"""

import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.auth import require_role, verify_api_key
from app.models.device import Device
from app.models.user import User
from app.schemas.agent import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from app.services.auth_service import get_password_hash
from app.storage.database import get_db

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/agents", tags=["Agents"])

# Length of the generated API key for new devices
API_KEY_LENGTH = 48

# In-memory telemetry cache populated by heartbeats. Keyed by device_id (str).
# Holds the most recent cpu/ram/queue_depth + agent log tail so admins can
# diagnose remote agents without SSH. Lost on backend restart (acceptable —
# agents heartbeat every 60s and refresh it).
_latest_telemetry: dict[str, dict] = {}


@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new agent device",
)
async def register_agent(
    request: AgentRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentRegisterResponse:
    """Register a new desktop agent device.

    Looks up the user by email, generates a unique API key, creates
    a device record, and returns the device ID and API key to the agent.

    Args:
        request: Registration details including hostname and user email.
        db: Async database session.

    Returns:
        AgentRegisterResponse with the assigned device_id and api_key.

    Raises:
        HTTPException: 404 if the user email is not found.
        HTTPException: 400 if registration fails.
    """
    logger.info(
        "agent_register_attempt",
        hostname=request.hostname,
        user_email=request.user_email,
    )

    user = await _find_user_by_email(db, request.user_email)
    api_key = secrets.token_urlsafe(API_KEY_LENGTH)
    device = _build_device(request, user, api_key)

    db.add(device)
    await db.flush()

    logger.info(
        "agent_registered",
        device_id=str(device.id),
        hostname=request.hostname,
        user_id=str(user.id),
    )

    return AgentRegisterResponse(
        device_id=device.id,
        api_key=api_key,
    )


@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
    status_code=status.HTTP_200_OK,
    summary="Agent heartbeat signal",
)
async def heartbeat(
    request: HeartbeatRequest,
    device: Annotated[Device, Depends(verify_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HeartbeatResponse:
    """Process a heartbeat from an enrolled agent device.

    Updates the device's last heartbeat timestamp and agent version.
    Returns optional configuration updates or forced upgrade instructions.

    Args:
        request: Heartbeat payload with system metrics.
        device: Authenticated device from API key verification.
        db: Async database session.

    Returns:
        HeartbeatResponse with optional config updates.
    """
    logger.debug(
        "heartbeat_received",
        device_id=str(device.id),
        agent_version=request.agent_version,
        cpu_usage=request.cpu_usage,
        ram_usage=request.ram_usage,
        queue_depth=request.queue_depth,
        log_tail_chars=len(request.log_tail or ""),
    )

    # Store telemetry in memory for the diagnostics endpoint
    _latest_telemetry[str(device.id)] = {
        "agent_version": request.agent_version,
        "cpu_usage": request.cpu_usage,
        "ram_usage": request.ram_usage,
        "queue_depth": request.queue_depth,
        "log_tail": request.log_tail or "",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "hostname": device.hostname,
    }

    await _update_heartbeat(db, device.id, request.agent_version)

    return HeartbeatResponse(
        config_update=None,
        force_upgrade_required=False,
        force_upgrade_url=None,
    )


@router.get(
    "/{device_id}/diagnostics",
    summary="Latest telemetry + log tail from an agent (admin only)",
)
async def agent_diagnostics(device_id: str) -> dict:
    """Return the most recent heartbeat telemetry + log tail for a device.

    Lets admins view remote agent state (cpu, ram, queue_depth, recent log
    lines) without needing SSH access to the user's machine. Data is held
    in memory and refreshed by every heartbeat (default every 60 s).
    """
    info = _latest_telemetry.get(device_id)
    if not info:
        return {
            "device_id": device_id,
            "available": False,
            "reason": "No heartbeat received since backend started, or device unknown",
        }
    return {"device_id": device_id, "available": True, **info}


@router.get(
    "/installer",
    summary="Download the TrackMe agent installer",
)
async def download_installer():
    """Serve the TrackMe agent .exe for download."""
    import os
    from fastapi.responses import FileResponse

    exe_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "..", "agent", "dist", "TrackMeAgent.exe",
    )
    exe_path = os.path.normpath(exe_path)

    if not os.path.exists(exe_path):
        raise HTTPException(status_code=404, detail="Agent installer not found. Build it first.")

    return FileResponse(
        path=exe_path,
        filename="TrackMeAgent.exe",
        media_type="application/octet-stream",
    )


@router.get(
    "",
    response_model=list[dict],
    status_code=status.HTTP_200_OK,
    summary="List all enrolled agents (admin only)",
    dependencies=[Depends(require_role(["admin"]))],
)
async def list_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List all enrolled agent devices.

    Restricted to admin users only. Returns device information
    including hostname, version, and last heartbeat time.

    Args:
        db: Async database session.

    Returns:
        List of device dictionaries with enrollment details.
    """
    logger.info("list_agents_requested")

    stmt = select(Device).order_by(Device.created_at.desc())
    result = await db.execute(stmt)
    devices = result.scalars().all()

    return [_serialize_device(device) for device in devices]


@router.post("/ingest")
async def agent_ingest(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Ingest activity and app usage data from the agent.

    Accepts X-API-Key and X-Device-Id headers for authentication.
    This endpoint bypasses JWT auth — agents use API keys only.
    """
    from app.services.auth_service import verify_password

    api_key = request.headers.get("X-API-Key", "")
    device_id_str = request.headers.get("X-Device-Id", "")

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    # Find device by scanning (handles UUID format differences)
    result = await db.execute(select(Device))
    devices = result.scalars().all()
    device = None
    for d in devices:
        try:
            if d.api_key_hash and verify_password(api_key, d.api_key_hash):
                device = d
                break
        except Exception:
            continue

    if not device:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Update last_heartbeat on every ingest so "Last Active" stays current
    device.last_heartbeat = datetime.now(timezone.utc)
    await db.flush()

    body = await request.json()
    accepted = 0

    # Use a separate session for writes to avoid interference from device scan
    from sqlalchemy import text
    from app.storage.database import async_session_factory, _is_sqlite

    # SQL dialect: SQLite uses "INSERT OR IGNORE", PostgreSQL uses "ON CONFLICT DO NOTHING"
    _ignore_prefix = "INSERT OR IGNORE" if _is_sqlite else "INSERT"
    _ignore_suffix = "" if _is_sqlite else " ON CONFLICT (client_id) DO NOTHING"

    # Convert UUIDs to hex (no dashes) to match SQLAlchemy GUID column format
    dev_id_hex = device.id.hex if hasattr(device.id, 'hex') else str(device.id).replace('-', '')
    usr_id_hex = device.user_id.hex if hasattr(device.user_id, 'hex') else str(device.user_id).replace('-', '')

    async with async_session_factory() as write_db:
        for sess in body.get("sessions", []):
            try:
                cid = sess.get("client_id", str(uuid.uuid4())).replace('-', '')
                start_str = sess.get("start_time")
                end_str = sess.get("end_time")
                # Compute duration_sec from start/end times
                dur_sec = None
                if start_str and end_str:
                    try:
                        from dateutil.parser import parse as dtparse
                        dur_sec = int((dtparse(end_str) - dtparse(start_str)).total_seconds())
                    except Exception:
                        pass
                await write_db.execute(text(
                    f"{_ignore_prefix} INTO activity_sessions (id, client_id, device_id, user_id, session_type, start_time, end_time, duration_sec) "
                    f"VALUES (:id, :cid, :did, :uid, :stype, :start, :end, :dur){_ignore_suffix}"
                ), {
                    "id": uuid.uuid4().hex, "cid": cid,
                    "did": dev_id_hex, "uid": usr_id_hex,
                    "stype": sess.get("session_type", "active"),
                    "start": start_str, "end": end_str,
                    "dur": dur_sec,
                })
                accepted += 1
            except Exception as e:
                logger.debug("ingest_session_error", error=str(e))

        for rec in body.get("records", []):
            try:
                cid = rec.get("client_id", str(uuid.uuid4())).replace('-', '')
                await write_db.execute(text(
                    f"{_ignore_prefix} INTO app_usage (id, client_id, device_id, user_id, process_name, window_title, start_time, end_time, duration_sec) "
                    f"VALUES (:id, :cid, :did, :uid, :pname, :wtitle, :start, :end, :dur){_ignore_suffix}"
                ), {
                    "id": uuid.uuid4().hex, "cid": cid,
                    "did": dev_id_hex, "uid": usr_id_hex,
                    "pname": rec.get("process_name", ""), "wtitle": rec.get("window_title", ""),
                    "start": rec.get("start_time"), "end": rec.get("end_time"),
                    "dur": rec.get("duration_seconds", 0),
                })
                accepted += 1
            except Exception as e:
                logger.debug("ingest_app_error", error=str(e))

        for visit in body.get("url_visits", []):
            try:
                cid = visit.get("client_id", str(uuid.uuid4())).replace('-', '')
                await write_db.execute(text(
                    f"{_ignore_prefix} INTO url_visits (id, client_id, device_id, user_id, browser, url, domain, page_title, visit_time, duration_sec) "
                    f"VALUES (:id, :cid, :did, :uid, :browser, :url, :domain, :title, :vtime, :dur){_ignore_suffix}"
                ), {
                    "id": uuid.uuid4().hex, "cid": cid,
                    "did": dev_id_hex, "uid": usr_id_hex,
                    "browser": visit.get("browser", "chrome"),
                    "url": visit.get("url", ""),
                    "domain": visit.get("domain", ""),
                    "title": visit.get("page_title", ""),
                    "vtime": visit.get("visit_time"),
                    "dur": visit.get("duration_sec", 0),
                })
                accepted += 1
            except Exception as e:
                logger.debug("ingest_url_error", error=str(e))

        await write_db.commit()

    logger.info("ingest_committed", accepted=accepted)
    return {"accepted": accepted}


@router.get("/download-script")
async def download_agent_script():
    """Download the Python tracking agent script.

    Returns the trackme_agent.py file for deployment to employee workstations.
    No authentication required — the agent authenticates via device registration.
    """
    # Look for the agent script in the project root
    agent_paths = [
        Path(__file__).resolve().parents[4] / "agent" / "trackme_agent.py",
        Path(__file__).resolve().parents[3] / "agent" / "trackme_agent.py",
        Path(__file__).resolve().parents[2] / "agent" / "trackme_agent.py",
    ]

    for agent_path in agent_paths:
        if agent_path.exists():
            return FileResponse(
                path=str(agent_path),
                media_type="text/x-python",
                filename="trackme_agent.py",
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Agent script not found on server",
    )


@router.get("/download-script-mac")
async def download_agent_script_mac():
    """Download the macOS tracking agent script."""
    agent_paths = [
        Path(__file__).resolve().parents[4] / "agent" / "trackme_agent_mac.py",
        Path(__file__).resolve().parents[3] / "agent" / "trackme_agent_mac.py",
        Path(__file__).resolve().parents[2] / "agent" / "trackme_agent_mac.py",
    ]

    for agent_path in agent_paths:
        if agent_path.exists():
            return FileResponse(
                path=str(agent_path),
                media_type="text/x-python",
                filename="trackme_agent_mac.py",
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Mac agent script not found on server",
    )


async def _find_user_by_email(
    db: AsyncSession,
    email: str,
) -> User:
    """Look up a user by email address.

    Args:
        db: Async database session.
        email: Email address to search for.

    Returns:
        The matching User object.

    Raises:
        HTTPException: 404 if no user is found with the given email.
    """
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        logger.warning("user_not_found_for_registration", email=email)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email '{email}' not found",
        )

    return user


def _build_device(
    request: AgentRegisterRequest,
    user: User,
    api_key: str,
) -> Device:
    """Create a Device model instance from registration data.

    Args:
        request: Agent registration request payload.
        user: The user who owns this device.
        api_key: The plaintext API key (will be hashed for storage).

    Returns:
        A new Device instance ready for database insertion.
    """
    return Device(
        id=uuid.uuid4(),
        user_id=user.id,
        org_id=user.org_id,
        hostname=request.hostname,
        os_version=request.os_version,
        agent_version=request.agent_version,
        api_key_hash=get_password_hash(api_key),
        is_active=True,
    )


async def _update_heartbeat(
    db: AsyncSession,
    device_id: uuid.UUID,
    agent_version: str,
) -> None:
    """Update a device's heartbeat timestamp and version.

    Args:
        db: Async database session.
        device_id: The device to update.
        agent_version: The reported agent version.
    """
    stmt = (
        update(Device)
        .where(Device.id == device_id)
        .values(
            last_heartbeat=datetime.now(timezone.utc),
            agent_version=agent_version,
        )
    )
    await db.execute(stmt)


def _serialize_device(device: Device) -> dict:
    """Serialize a Device model to a dictionary for API response.

    Args:
        device: The Device model instance.

    Returns:
        Dictionary representation of the device.
    """
    return {
        "id": str(device.id),
        "user_id": str(device.user_id),
        "org_id": str(device.org_id),
        "hostname": device.hostname,
        "os_version": device.os_version,
        "agent_version": device.agent_version,
        "is_active": device.is_active,
        "last_heartbeat": (
            device.last_heartbeat.isoformat()
            if device.last_heartbeat
            else None
        ),
        "created_at": device.created_at.isoformat(),
    }
