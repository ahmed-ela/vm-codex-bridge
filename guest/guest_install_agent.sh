#!/usr/bin/env bash
set -euo pipefail

LABEL="com.elamins.vm_codex_bridge.guest_agent"
APP_DIR="$HOME/Library/Application Support/vm-codex-bridge"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
AGENT_TARGET="$APP_DIR/guest_session_agent.py"
STDERR_LOG="$HOME/Library/Logs/vm-codex-bridge-guest-agent.stderr.log"
STDOUT_LOG="$HOME/Library/Logs/vm-codex-bridge-guest-agent.stdout.log"
LISTEN_HOST="${VM_CODEX_GUEST_AGENT_HOST:-127.0.0.1}"
LISTEN_PORT="${VM_CODEX_GUEST_AGENT_PORT:-8765}"
PYTHON_BIN="${VM_CODEX_GUEST_AGENT_PYTHON:-$(command -v python3)}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /absolute/path/to/desktopctl.py" >&2
  exit 2
fi

DESKTOPCTL_PATH="$1"
if [[ "$DESKTOPCTL_PATH" != /* ]]; then
  echo "desktopctl path must be absolute: $DESKTOPCTL_PATH" >&2
  exit 2
fi
if [[ ! -f "$DESKTOPCTL_PATH" ]]; then
  echo "desktopctl path does not exist: $DESKTOPCTL_PATH" >&2
  exit 2
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python3 was not found: $PYTHON_BIN" >&2
  exit 2
fi

mkdir -p "$APP_DIR" "$LAUNCH_AGENTS_DIR" "$HOME/Library/Logs"
install -m 755 "$(dirname "$0")/guest_session_agent.py" "$AGENT_TARGET"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$AGENT_TARGET</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>DESKTOPCTL_PATH</key>
    <string>$DESKTOPCTL_PATH</string>
    <key>DESKTOPCTL_PYTHON</key>
    <string>$PYTHON_BIN</string>
    <key>VM_CODEX_GUEST_AGENT_HOST</key>
    <string>$LISTEN_HOST</string>
    <key>VM_CODEX_GUEST_AGENT_PORT</key>
    <string>$LISTEN_PORT</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
  </array>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$STDOUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$STDERR_LOG</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST_PATH" >/dev/null

set +e
launchctl bootout "gui/$UID" "$PLIST_PATH" >/dev/null 2>&1
set -e

launchctl bootstrap "gui/$UID" "$PLIST_PATH"
launchctl enable "gui/$UID/$LABEL"
launchctl kickstart -k "gui/$UID/$LABEL"

sleep 1

echo "Installed LaunchAgent: $PLIST_PATH"
echo "guest_session_agent.py: $AGENT_TARGET"
echo "desktopctl.py: $DESKTOPCTL_PATH"
echo "python3: $PYTHON_BIN"
echo "Health check:"
/usr/bin/curl --silent --show-error "http://$LISTEN_HOST:$LISTEN_PORT/health" || true
echo
echo "Readiness check:"
/usr/bin/curl --silent --show-error "http://$LISTEN_HOST:$LISTEN_PORT/ready" || true
