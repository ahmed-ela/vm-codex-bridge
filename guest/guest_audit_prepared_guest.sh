#!/usr/bin/env bash
set -euo pipefail

JSON_OUTPUT=false
if [[ "${1:-}" == "--json" ]]; then
  JSON_OUTPUT=true
  shift
fi

if [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--json]" >&2
  exit 2
fi

AGENT_LABEL="com.elamins.vm_codex_bridge.guest_agent"
KEEPAWAKE_LABEL="com.elamins.vm_codex_bridge.keepawake"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
KEEPAWAKE_PLIST="$HOME/Library/LaunchAgents/$KEEPAWAKE_LABEL.plist"

IDLE_TIME="$(defaults -currentHost read com.apple.screensaver idleTime 2>/dev/null || true)"
ASK_FOR_PASSWORD="$(defaults read com.apple.screensaver askForPassword 2>/dev/null || true)"
ASK_FOR_PASSWORD_DELAY="$(defaults read com.apple.screensaver askForPasswordDelay 2>/dev/null || true)"
AUTO_LOGIN_USER="$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null || true)"
REMOTE_LOGIN_STATUS="$(/usr/sbin/systemsetup -getremotelogin 2>/dev/null || echo unknown)"
CONSOLE_USER="$(stat -f '%Su' /dev/console 2>/dev/null || true)"
HOSTNAME_VALUE="$(scutil --get LocalHostName 2>/dev/null || hostname)"
PMSET_CUSTOM="$(/usr/bin/pmset -g custom 2>/dev/null || true)"
PMSET_ASSERTIONS="$(/usr/bin/pmset -g assertions 2>/dev/null || true)"
AGENT_HEALTH="$(/usr/bin/curl --silent --show-error http://127.0.0.1:8765/health 2>/dev/null || true)"
AGENT_READY="$(/usr/bin/curl --silent --show-error http://127.0.0.1:8765/ready 2>/dev/null || true)"

AGENT_LOADED=false
KEEPAWAKE_LOADED=false
if launchctl print "gui/$UID/$AGENT_LABEL" >/dev/null 2>&1; then
  AGENT_LOADED=true
fi
if launchctl print "gui/$UID/$KEEPAWAKE_LABEL" >/dev/null 2>&1; then
  KEEPAWAKE_LOADED=true
fi

export IDLE_TIME ASK_FOR_PASSWORD ASK_FOR_PASSWORD_DELAY AUTO_LOGIN_USER
export REMOTE_LOGIN_STATUS CONSOLE_USER HOSTNAME_VALUE PMSET_CUSTOM PMSET_ASSERTIONS
export AGENT_HEALTH AGENT_READY AGENT_LOADED KEEPAWAKE_LOADED

if [[ "$JSON_OUTPUT" == true ]]; then
  python3 - <<'PY'
import json
import os

payload = {
    "ok": True,
    "user": os.environ.get("USER", ""),
    "uid": os.getuid(),
    "hostname": os.environ.get("HOSTNAME_VALUE", ""),
    "console_user": os.environ.get("CONSOLE_USER", ""),
    "auto_login_user": os.environ.get("AUTO_LOGIN_USER", ""),
    "remote_login_status": os.environ.get("REMOTE_LOGIN_STATUS", ""),
    "screensaver": {
        "idleTime": os.environ.get("IDLE_TIME", ""),
        "askForPassword": os.environ.get("ASK_FOR_PASSWORD", ""),
        "askForPasswordDelay": os.environ.get("ASK_FOR_PASSWORD_DELAY", ""),
    },
    "launch_agents": {
        "guest_agent": {
            "label": "com.elamins.vm_codex_bridge.guest_agent",
            "plist": os.path.expanduser("~/Library/LaunchAgents/com.elamins.vm_codex_bridge.guest_agent.plist"),
            "exists": os.path.exists(os.path.expanduser("~/Library/LaunchAgents/com.elamins.vm_codex_bridge.guest_agent.plist")),
            "loaded": os.environ.get("AGENT_LOADED", "false").lower() == "true",
        },
        "keepawake": {
            "label": "com.elamins.vm_codex_bridge.keepawake",
            "plist": os.path.expanduser("~/Library/LaunchAgents/com.elamins.vm_codex_bridge.keepawake.plist"),
            "exists": os.path.exists(os.path.expanduser("~/Library/LaunchAgents/com.elamins.vm_codex_bridge.keepawake.plist")),
            "loaded": os.environ.get("KEEPAWAKE_LOADED", "false").lower() == "true",
        },
    },
    "agent_health_raw": os.environ.get("AGENT_HEALTH", ""),
    "agent_ready_raw": os.environ.get("AGENT_READY", ""),
    "pmset_custom": os.environ.get("PMSET_CUSTOM", ""),
    "pmset_assertions": os.environ.get("PMSET_ASSERTIONS", ""),
}
print(json.dumps(payload, indent=2))
PY
else
  cat <<EOF
hostname=$HOSTNAME_VALUE
console_user=$CONSOLE_USER
auto_login_user=$AUTO_LOGIN_USER
remote_login_status=$REMOTE_LOGIN_STATUS
idleTime=$IDLE_TIME
askForPassword=$ASK_FOR_PASSWORD
askForPasswordDelay=$ASK_FOR_PASSWORD_DELAY
agent_loaded=$AGENT_LOADED
keepawake_loaded=$KEEPAWAKE_LOADED
agent_health_raw=$AGENT_HEALTH
agent_ready_raw=$AGENT_READY
EOF
fi
