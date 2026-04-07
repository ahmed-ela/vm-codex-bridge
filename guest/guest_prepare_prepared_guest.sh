#!/usr/bin/env bash
set -euo pipefail

KEEPAWAKE_LABEL="com.elamins.vm_codex_bridge.keepawake"
KEEPAWAKE_PLIST="$HOME/Library/LaunchAgents/$KEEPAWAKE_LABEL.plist"
KEEPAWAKE_STDOUT_LOG="$HOME/Library/Logs/vm-codex-bridge-keepawake.stdout.log"
KEEPAWAKE_STDERR_LOG="$HOME/Library/Logs/vm-codex-bridge-keepawake.stderr.log"
JSON_OUTPUT=false

if [[ "${1:-}" == "--json" ]]; then
  JSON_OUTPUT=true
  shift
fi

if [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--json]" >&2
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

# A prepared guest should not drift back to the lock screen during ordinary runs.
defaults -currentHost write com.apple.screensaver idleTime -int 0
defaults write com.apple.screensaver askForPassword -int 0
defaults write com.apple.screensaver askForPasswordDelay -float 0

cat > "$KEEPAWAKE_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$KEEPAWAKE_LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-dims</string>
  </array>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
  </array>

  <key>StandardOutPath</key>
  <string>$KEEPAWAKE_STDOUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$KEEPAWAKE_STDERR_LOG</string>
</dict>
</plist>
PLIST

plutil -lint "$KEEPAWAKE_PLIST" >/dev/null

set +e
launchctl bootout "gui/$UID" "$KEEPAWAKE_PLIST" >/dev/null 2>&1
set -e
launchctl bootstrap "gui/$UID" "$KEEPAWAKE_PLIST"
launchctl enable "gui/$UID/$KEEPAWAKE_LABEL"
launchctl kickstart -k "gui/$UID/$KEEPAWAKE_LABEL"

ROOT_STATUS="pending"
ROOT_CMDS_FILE="$(mktemp)"
cleanup() {
  rm -f "$ROOT_CMDS_FILE"
}
trap cleanup EXIT

printf '%s\n' \
  "sudo systemsetup -setremotelogin on" \
  "sudo pmset -a sleep 0 displaysleep 0 disksleep 0 powernap 0 tcpkeepalive 1 autorestart 1" \
  > "$ROOT_CMDS_FILE"

if /usr/bin/sudo -n true >/dev/null 2>&1; then
  if /usr/bin/sudo /usr/sbin/systemsetup -setremotelogin on >/dev/null 2>&1 && \
     /usr/bin/sudo /usr/bin/pmset -a sleep 0 displaysleep 0 disksleep 0 powernap 0 tcpkeepalive 1 autorestart 1 >/dev/null 2>&1; then
    ROOT_STATUS="applied"
    : > "$ROOT_CMDS_FILE"
  else
    ROOT_STATUS="failed"
  fi
fi

IDLE_TIME="$(defaults -currentHost read com.apple.screensaver idleTime 2>/dev/null || true)"
ASK_FOR_PASSWORD="$(defaults read com.apple.screensaver askForPassword 2>/dev/null || true)"
ASK_FOR_PASSWORD_DELAY="$(defaults read com.apple.screensaver askForPasswordDelay 2>/dev/null || true)"
AUTO_LOGIN_USER="$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null || true)"
REMOTE_LOGIN_STATUS="$(/usr/sbin/systemsetup -getremotelogin 2>/dev/null || echo unknown)"
PMSET_CUSTOM="$(/usr/bin/pmset -g custom 2>/dev/null || true)"
KEEPAWAKE_LOADED=false
if launchctl print "gui/$UID/$KEEPAWAKE_LABEL" >/dev/null 2>&1; then
  KEEPAWAKE_LOADED=true
fi

export ROOT_STATUS IDLE_TIME ASK_FOR_PASSWORD ASK_FOR_PASSWORD_DELAY AUTO_LOGIN_USER
export REMOTE_LOGIN_STATUS PMSET_CUSTOM KEEPAWAKE_LOADED

if [[ "$JSON_OUTPUT" == true ]]; then
  python3 - "$ROOT_CMDS_FILE" <<'PY'
import json
import os
import sys
from pathlib import Path

root_cmds_path = Path(sys.argv[1])
pending_root_commands = [line.strip() for line in root_cmds_path.read_text(encoding="utf-8").splitlines() if line.strip()]

payload = {
    "ok": True,
    "user": os.environ.get("USER", ""),
    "uid": os.getuid(),
    "keepawake": {
        "label": "com.elamins.vm_codex_bridge.keepawake",
        "plist": os.path.expanduser("~/Library/LaunchAgents/com.elamins.vm_codex_bridge.keepawake.plist"),
        "loaded": os.environ.get("KEEPAWAKE_LOADED", "false").lower() == "true",
    },
    "screensaver": {
        "idleTime": os.environ.get("IDLE_TIME", ""),
        "askForPassword": os.environ.get("ASK_FOR_PASSWORD", ""),
        "askForPasswordDelay": os.environ.get("ASK_FOR_PASSWORD_DELAY", ""),
    },
    "root_status": os.environ.get("ROOT_STATUS", "pending"),
    "pending_root_commands": pending_root_commands,
    "remote_login_status": os.environ.get("REMOTE_LOGIN_STATUS", ""),
    "auto_login_user": os.environ.get("AUTO_LOGIN_USER", ""),
    "pmset_custom": os.environ.get("PMSET_CUSTOM", ""),
    "notes": [
        "Prepared guest mode assumes this VM does not fall back to login or lock screens during ordinary runs.",
        "Enable automatic login or resume from a saved logged-in state if you want unattended startup.",
        "Install the guest session agent separately with guest_install_agent.sh if it is not already present.",
    ],
}
print(json.dumps(payload, indent=2))
PY
else
  cat <<EOF
Prepared guest defaults applied.
keepawake_label=$KEEPAWAKE_LABEL
keepawake_loaded=$KEEPAWAKE_LOADED
idleTime=$IDLE_TIME
askForPassword=$ASK_FOR_PASSWORD
askForPasswordDelay=$ASK_FOR_PASSWORD_DELAY
root_status=$ROOT_STATUS
remote_login_status=$REMOTE_LOGIN_STATUS
auto_login_user=$AUTO_LOGIN_USER
pending_root_commands:
$(sed 's/^/  - /' "$ROOT_CMDS_FILE")
EOF
fi
