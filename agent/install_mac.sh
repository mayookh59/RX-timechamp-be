#!/bin/bash
# TrackMe Production Installer for macOS
#
# Installs the agent as a LaunchAgent that:
#   - Runs at user logon (RunAtLoad)
#   - Restarts on crash (KeepAlive on SuccessfulExit=false)
#   - ThrottleInterval=30 prevents tight crash loops
#   - Wrapper script resolves Python at runtime (survives Python upgrades)
#
# Usage:
#   bash install_mac.sh [SERVER_URL] [USER_EMAIL]
# Example:
#   bash install_mac.sh http://192.168.31.253:8000/api/v1 alice@company.com

set -u

SERVER_URL="${1:-http://192.168.31.253:8000/api/v1}"
USER_EMAIL="${2:-user@company.com}"

APP_SUPPORT="$HOME/Library/Application Support/TrackMe"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS/com.trackme.agent.plist"
AGENT_PY="$APP_SUPPORT/trackme_agent_mac.py"
CONFIG_JSON="$APP_SUPPORT/config.json"
WRAPPER="$APP_SUPPORT/launch_wrapper.sh"
INSTALL_LOG="$HOME/Desktop/TrackMe-Install.log"

exec > >(tee -a "$INSTALL_LOG") 2>&1

echo "===================================================="
echo "  TrackMe Agent Installer (Production)"
echo "===================================================="
echo "  Server: $SERVER_URL"
echo "  User:   $USER_EMAIL"
echo "  Data:   $APP_SUPPORT"
echo "  Log:    $INSTALL_LOG"
echo "===================================================="
echo

# ── 1. Check Python ─────────────────────────────────────────────
echo "[1/7] Checking Python..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "  ERROR: python3 not found."
    echo "  Install via Xcode CLI tools:  xcode-select --install"
    echo "  Or Homebrew:                  brew install python3"
    exit 1
fi
PY_VERSION=$(python3 --version 2>&1)
echo "  Found: $PY_VERSION"

# ── 2. Create app-support directory ─────────────────────────────
echo "[2/7] Creating data directory..."
mkdir -p "$APP_SUPPORT"
mkdir -p "$LAUNCH_AGENTS"

# ── 3. Download agent script ────────────────────────────────────
echo "[3/7] Downloading agent script from $SERVER_URL/agents/download-script-mac ..."
if ! curl -sS --max-time 30 -o "$AGENT_PY" "$SERVER_URL/agents/download-script-mac"; then
    echo "  ERROR: Could not download agent script. Check that backend is reachable:"
    echo "         curl $SERVER_URL/docs"
    exit 1
fi
if [ ! -s "$AGENT_PY" ]; then
    echo "  ERROR: Downloaded file is empty."
    exit 1
fi

# ── 4. Write config ─────────────────────────────────────────────
echo "[4/7] Writing config..."
cat > "$CONFIG_JSON" <<EOF
{
    "api_base_url": "$SERVER_URL",
    "user_email":   "$USER_EMAIL",
    "idle_threshold_seconds":      60,
    "sync_interval_seconds":       60,
    "screenshot_interval_seconds": 120,
    "log_level": "INFO"
}
EOF

# ── 5. Install Python deps ──────────────────────────────────────
echo "[5/7] Installing Python dependencies..."
python3 -m pip install --quiet --upgrade pip 2>/dev/null || true
python3 -m pip install --quiet --user requests psutil pillow pystray 2>&1 | tail -5 || true

# ── 6. Generate plist + wrapper via the agent's --install-only ──
echo "[6/7] Installing LaunchAgent..."
launchctl unload "$PLIST" 2>/dev/null || true
pkill -f trackme_agent_mac.py 2>/dev/null || true

python3 "$AGENT_PY" --install-only

if [ ! -f "$PLIST" ]; then
    echo "  ERROR: LaunchAgent plist was not created at $PLIST"
    exit 1
fi
if [ -f "$WRAPPER" ]; then
    chmod +x "$WRAPPER"
fi

# ── 7. Load and verify ──────────────────────────────────────────
echo "[7/7] Loading LaunchAgent..."
if launchctl load "$PLIST" 2>&1; then
    echo "  LaunchAgent loaded."
else
    echo "  WARNING: launchctl load reported a failure. Trying bootstrap..."
    UID_=$(id -u)
    launchctl bootstrap gui/$UID_ "$PLIST" 2>&1 || true
fi

sleep 3
if launchctl list 2>/dev/null | grep -q com.trackme.agent; then
    echo "  Agent registered with launchd."
else
    echo "  WARNING: launchctl list does not show com.trackme.agent."
    echo "  Inspect:"
    echo "    tail -50 $APP_SUPPORT/launchd_err.log"
    echo "    tail -50 $APP_SUPPORT/agent.log"
fi

echo
echo "===================================================="
echo "  INSTALL COMPLETE"
echo "===================================================="
echo "  Plist:        $PLIST"
echo "  Agent script: $AGENT_PY"
echo "  Wrapper:      $WRAPPER"
echo "  Config:       $CONFIG_JSON"
echo "  Logs:         $APP_SUPPORT/agent.log"
echo
echo "  Status:       launchctl list | grep trackme"
echo "  Stop:         launchctl unload $PLIST"
echo "  Logs (live):  tail -f \"$APP_SUPPORT/agent.log\""
echo "===================================================="
echo
echo "  IMPORTANT: macOS will prompt for Screen Recording permission."
echo "  Grant it: System Settings -> Privacy & Security -> Screen Recording"
echo "  (enable Terminal or whichever app launched python3)"
echo
