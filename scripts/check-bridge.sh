#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${VM_BRIDGE_CONFIG:-$HOME/.vm-codex-bridge/profiles.json}"
PROFILE="${1:-}"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export VM_BRIDGE_CONFIG="$CONFIG_PATH"

"$PYTHON_BIN" - "$ROOT" "$PROFILE" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
profile_filter = sys.argv[2] or None
sys.path.insert(0, str(root / "host"))

from vm_bridge_core import BridgeError, load_bridge_from_env  # noqa: E402


def app_exists(vm_info: dict) -> bool | None:
    app_path = vm_info.get("app_path")
    if app_path:
        return Path(app_path).expanduser().exists()
    app_name = vm_info.get("app")
    if not app_name:
        return None
    return (Path("/Applications") / f"{app_name}.app").exists()


config_path = Path(os.environ["VM_BRIDGE_CONFIG"]).expanduser()
payload: dict[str, object] = {"config_path": str(config_path), "ok": False}

if not config_path.exists():
    payload["error"] = f"Config file not found: {config_path}"
    print(json.dumps(payload, indent=2))
    raise SystemExit(1)

try:
    bridge = load_bridge_from_env()
except Exception as exc:
    payload["error"] = str(exc)
    payload["error_type"] = type(exc).__name__
    print(json.dumps(payload, indent=2))
    raise SystemExit(1)

profiles = bridge.list_profiles()
if profile_filter:
    if profile_filter not in profiles:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Unknown profile {profile_filter!r}",
                    "known_profiles": profiles,
                },
                indent=2,
            )
        )
        raise SystemExit(1)
    profiles = [profile_filter]

summaries = []
overall_ok = True
for name in profiles:
    info = bridge.profile_info(name)
    vm_path = Path(info["vm"]["path"]).expanduser()
    ssh_configured = bool(info["ssh"].get("configured"))
    vnc_configured = bool(info["vnc"].get("configured"))
    summary = {
        "profile": name,
        "vm_path": str(vm_path),
        "vm_exists": vm_path.exists(),
        "vm_app": info["vm"].get("app") or info["vm"].get("app_path"),
        "vm_app_exists": app_exists(info["vm"]),
        "policy": info.get("policy"),
        "ssh_configured": ssh_configured,
        "vnc_configured": vnc_configured,
        "agent_host": info["agent"]["host"],
        "agent_port": info["agent"]["port"],
    }

    if ssh_configured:
        summary["ssh_check"] = bridge.check_ssh(name)
        summary["remote_status"] = bridge.remote_status(
            name,
            require_agent_ready=bool(info.get("policy", {}).get("require_agent_ready", True)),
            require_vnc=bool(info.get("policy", {}).get("require_vnc", False)),
        )
    if vnc_configured:
        summary["vnc_password_present"] = info["vnc"].get("password_present")
        summary["vnc_check"] = bridge.check_vnc(name)

    summary["local_bootstrap_ready"] = bool(summary["vm_exists"] and summary["vm_app_exists"])
    policy_mode = (info.get("policy", {}) or {}).get("mode", "prepared_guest")
    if policy_mode == "prepared_guest":
        summary["ready_for_policy"] = bool((summary.get("remote_status") or {}).get("ok"))
    else:
        summary["ready_for_policy"] = summary["local_bootstrap_ready"]
    overall_ok = overall_ok and bool(summary["ready_for_policy"])
    summaries.append(summary)

print(json.dumps({"ok": overall_ok, "profiles": summaries}, indent=2))
PY
