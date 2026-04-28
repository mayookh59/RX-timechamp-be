#!/bin/bash
# TrackMe Mac Agent — Diagnostic + Recovery script
#
# Send this to Pooja (or any Mac user whose agent has stopped reporting).
# Usage:
#   bash mac_diagnose_and_fix.sh [SERVER_IP]
#
# Example:
#   bash mac_diagnose_and_fix.sh 192.168.31.253

SERVER_IP="${1:-192.168.31.253}"
APP_SUPPORT="$HOME/Library/Application Support/TrackMe"
PLIST="$HOME/Library/LaunchAgents/com.trackme.agent.plist"

echo "=========================================="
echo "  TrackMe Mac Agent Diagnostic"
echo "=========================================="

echo
echo "[1/7] Current WiFi / IP"
ipconfig getifaddr en0 2>/dev/null || echo "  (no en0 IP)"
route -n get default 2>/dev/null | grep "gateway:"

echo
echo "[2/7] LAN reachability to server ($SERVER_IP:8000)"
if curl -sS --max-time 5 "http://$SERVER_IP:8000/docs" -o /dev/null -w "  HTTP %{http_code}  (%{time_total}s)\n"; then
    echo "  OK — server is reachable"
else
    echo "  !! CANNOT REACH SERVER"
    echo "  Pooja is probably on a different network. Connect her to the same WiFi as the server."
fi

echo
echo "[3/7] LaunchAgent status"
if launchctl list | grep -q trackme; then
    launchctl list | grep trackme
else
    echo "  !! LaunchAgent is NOT loaded"
fi

echo
echo "[4/7] Config file"
if [ -f "$APP_SUPPORT/config.json" ]; then
    cat "$APP_SUPPORT/config.json"
else
    echo "  !! Config missing at $APP_SUPPORT/config.json"
fi

echo
echo "[5/7] Last 30 lines of agent.log"
if [ -f "$APP_SUPPORT/agent.log" ]; then
    tail -30 "$APP_SUPPORT/agent.log"
else
    echo "  !! agent.log missing"
fi

echo
echo "[6/7] Last 20 lines of launchd_err.log"
if [ -f "$APP_SUPPORT/launchd_err.log" ]; then
    tail -20 "$APP_SUPPORT/launchd_err.log"
else
    echo "  (no launchd_err.log — agent hasn't crashed)"
fi

echo
echo "[7/7] Python deps"
for mod in requests psutil PIL; do
    if python3 -c "import $mod" 2>/dev/null; then
        echo "  OK   $mod"
    else
        echo "  MISS $mod   (run: pip3 install requests psutil pillow)"
    fi
done

echo
echo "=========================================="
echo "  Recovery (attempts to restart the agent)"
echo "=========================================="

# Make sure config points at the right server
if [ -f "$APP_SUPPORT/config.json" ]; then
    python3 -c "
import json, sys
p = '$APP_SUPPORT/config.json'
with open(p) as f: c = json.load(f)
c['api_base_url'] = 'http://$SERVER_IP:8000/api/v1'
with open(p, 'w') as f: json.dump(c, f, indent=2)
print('  Updated api_base_url -> http://$SERVER_IP:8000/api/v1')
"
fi

# Reload the LaunchAgent
echo "  Unloading LaunchAgent..."
launchctl unload "$PLIST" 2>/dev/null || true
sleep 1
echo "  Loading LaunchAgent..."
launchctl load "$PLIST"

sleep 3
echo
echo "  LaunchAgent status after reload:"
launchctl list | grep trackme || echo "  !! Still not loaded — check launchd_err.log above"

echo
echo "  Tailing agent.log for 10 seconds — you should see a heartbeat/sync message:"
tail -f "$APP_SUPPORT/agent.log" &
TAIL_PID=$!
sleep 10
kill $TAIL_PID 2>/dev/null
echo
echo "Done. Refresh the dashboard — Pooja's agent should show 'online' within 1 minute."
