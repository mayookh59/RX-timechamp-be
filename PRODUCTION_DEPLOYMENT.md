# TrackMe Production Deployment

This is the permanent setup. Once installed, agents and backend survive reboots,
crashes, network changes, and Python upgrades — without manual intervention.

## Server Machine (this laptop, 192.168.31.253)

### Already installed
- **Scheduled Task: `TrackMe Backend`** — auto-starts the FastAPI backend at user logon
- **Scheduled Task: `TrackMe Agent`** — auto-starts the v2.0.0 agent at user logon

Both tasks have `RestartOnFailure` (every 1 min, up to 999 times). They survive
reboots and signed-out sessions (next logon brings them up).

### Verify
```cmd
schtasks /query /tn "TrackMe Agent" /fo LIST
schtasks /query /tn "TrackMe Backend" /fo LIST
```

Both should show `Status: Running` (or `Ready` if not currently active).

### Stop / start manually
```cmd
schtasks /run  /tn "TrackMe Agent"
schtasks /end  /tn "TrackMe Agent"
```

### Logs
- Agent:   `C:\ProgramData\TrackMe\agent.log` (5 MB rotating × 5 backups)
- Backend: stdout of the scheduled task — visible in Task Scheduler history

### Dashboard
The Vite dev server is **not** auto-started — it's only needed when the admin
views the dashboard. Run it manually with:
```cmd
cd C:\Users\Hrishi\Desktop\TRACKER.AI\dashboard
npx vite
```

For production, build it once with `npm run build` and serve the `dist/` directory
via the backend's static file handler or a simple `python -m http.server`.

---

## Remote Windows Machines (e.g. Thinkpad)

### One-line installer (when machine is on the office LAN)

On this server first:
```cmd
cd C:\Users\Hrishi\Desktop\TRACKER.AI\agent
python -m http.server 8080
```

Then on the remote Windows machine, **as Administrator**:
```cmd
curl -o %TEMP%\install_windows.bat http://192.168.31.253:8080/install_windows.bat
%TEMP%\install_windows.bat http://192.168.31.253:8000/api/v1 thinkpad@gmail.com
```

The installer:
1. Installs Python 3.11 if missing
2. Downloads the v2.0.0 agent script
3. Writes the config pointing at the right server
4. Removes any old fragile Startup-folder shims
5. Installs a Scheduled Task `TrackMe Agent` with restart-on-failure
6. Starts the agent immediately
7. Runs the schema migration (auto on first agent startup)

### Verify (on the remote machine)
```cmd
schtasks /query /tn "TrackMe Agent" /fo LIST
type "C:\ProgramData\TrackMe\agent.log"
```

Within 60 seconds the device shows online on the dashboard.

---

## Remote Mac Machines (e.g. Pooja)

On this server:
```cmd
cd C:\Users\Hrishi\Desktop\TRACKER.AI\agent
python -m http.server 8080
```

Then on the Mac, in Terminal:
```bash
curl -sS http://192.168.31.253:8080/install_mac.sh | bash -s -- http://192.168.31.253:8000/api/v1 Pooja@rhythmrx.ai
```

The installer:
1. Verifies Python 3 is available (xcode-select install if missing)
2. Downloads the agent script
3. Writes config
4. Installs LaunchAgent via wrapper script (resolves Python at runtime)
5. Loads the LaunchAgent immediately

### Verify (on the Mac)
```bash
launchctl list | grep trackme
tail -f ~/Library/Application\ Support/TrackMe/agent.log
```

### Important — macOS permission
First run will prompt for **Screen Recording** permission. Grant it:
**System Settings → Privacy & Security → Screen Recording → enable Terminal/Python**.

---

## Why this is "permanent"

### Auto-start on every boot
- **Windows**: Scheduled Task triggered by `LogonTrigger`
- **Mac**: LaunchAgent with `RunAtLoad=true`

Survives reboots, signouts, sleep/wake, app crashes.

### Auto-restart on crash
- **Windows**: Scheduled Task `RestartOnFailure` — every 1 min, up to 999 times
- **Mac**: LaunchAgent `KeepAlive { SuccessfulExit: false }` with `ThrottleInterval=30`
- **Both**: Built-in watchdog thread restarts dead worker threads inside the process

### Network resilience
- TCP reachability probe before each sync cycle
- Exponential backoff with jitter on failures (5 s → 5 min cap)
- Buffers data locally (SQLite WAL) when backend is down — uploads on reconnect
- Auto re-register if device credentials become invalid (HTTP 401)

### Schema migrations
- v2.0.0 agents detect missing columns in legacy v1.0.0 DBs and add them
  via `ALTER TABLE`. Idempotent — safe to run on every startup.

### Single-instance guarantee
- **Windows**: Named mutex `Global\TrackMeAgentSingletonMutex`
- **Mac**: `fcntl.flock` on `agent.lock`

If the Scheduled Task accidentally launches a 2nd instance, it exits immediately
with `"Another TrackMe agent is already running — exiting"`.

### DB hygiene
- Hourly maintenance: deletes `synced=1` rows older than 7 days, vacuums DB
- Caps DB size at 200 MB (drops oldest synced screenshots if exceeded)

### Diagnosability
- Rotating logs (5 MB × 5 backups) — never fills disk
- All errors logged at appropriate levels (no silent `except: pass`)
- Heartbeat carries `queue_depth` so admin can see backlog at a glance

---

## Troubleshooting

### Agent appears offline on dashboard

1. **Is the machine on the LAN?**
   ```cmd
   ping 192.168.31.253
   ```
   If "Destination host unreachable" → wrong network or AP isolation.

2. **Is the agent process running?**
   ```cmd
   tasklist /FI "IMAGENAME eq pythonw.exe" /V
   ```
   Should show `pythonw.exe` running `trackme_agent.py`.

3. **Is the Scheduled Task registered?**
   ```cmd
   schtasks /query /tn "TrackMe Agent"
   ```
   Should report `Running` or `Ready`.

4. **What does the log say?**
   ```cmd
   powershell Get-Content C:\ProgramData\TrackMe\agent.log -Tail 30
   ```
   Look for `[WARNING] Heartbeat network unreachable` (network) or `[ERROR]` lines.

### Sessions / app data missing

If older v1.0.0 DB is in use and migration hasn't run:
```cmd
schtasks /end /tn "TrackMe Agent"
schtasks /run /tn "TrackMe Agent"
```
First-startup of v2.0.0 runs migrations automatically.

### Force re-register a device
```cmd
schtasks /end /tn "TrackMe Agent"
del C:\ProgramData\TrackMe\trackme_agent.db
schtasks /run /tn "TrackMe Agent"
```
The agent re-registers with the backend and creates a new local DB.
