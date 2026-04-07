from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP, Image

from vm_bridge_core import BridgeError, load_bridge_from_env


logging.basicConfig(
    level=os.environ.get("VM_BRIDGE_LOG_LEVEL", "INFO").upper(),
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("vm_bridge_server")

mcp = FastMCP("vm-codex-bridge")
BRIDGE = load_bridge_from_env()


def _wrap_error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


@mcp.tool()
def list_profiles() -> list[str]:
    """List configured VM bridge profiles."""
    return BRIDGE.list_profiles()


@mcp.tool()
def profile_info(profile: str) -> dict[str, Any]:
    """Return a redacted profile description."""
    try:
        return BRIDGE.profile_info(profile)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vm_open(profile: str) -> dict[str, Any]:
    """Open the VM using Vimy or Viable."""
    try:
        return BRIDGE.vm_open(profile)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def local_vm_info(profile: str) -> dict[str, Any]:
    """Inspect the host-local Vimy/Viable window used for this VM profile."""
    try:
        return BRIDGE.local_vm_info(profile)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool(structured_output=False)
def local_vm_capture(profile: str) -> Image | dict[str, Any]:
    """Capture the host-local VM window without bringing it to the front."""
    try:
        png_bytes = BRIDGE.local_vm_capture_bytes(profile)
        return Image(data=png_bytes, format="png")
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def local_vm_capture_to_file(profile: str, output_path: str) -> dict[str, Any]:
    """Capture the host-local VM window to a host file path."""
    try:
        return BRIDGE.local_vm_capture_to_file(profile, output_path)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def local_vm_type(profile: str, text: str, submit: bool = False) -> dict[str, Any]:
    """Type into the VM window in the background without switching host focus."""
    try:
        return BRIDGE.local_vm_type(profile, text, submit=submit)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def local_vm_key(profile: str, key: str, repeat: int = 1) -> dict[str, Any]:
    """Send a special key or key chord to the VM window in the background."""
    try:
        return BRIDGE.local_vm_key(profile, key, repeat=repeat)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def local_vm_click(
    profile: str,
    x: float,
    y: float,
    coordinate_space: str = "points",
) -> dict[str, Any]:
    """Click inside the VM window in points, capture pixels, or normalized coordinates."""
    try:
        return BRIDGE.local_vm_click(
            profile,
            x,
            y,
            coordinate_space=coordinate_space,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def remote_status(
    profile: str,
    require_agent_ready: bool | None = None,
    require_vnc: bool | None = None,
) -> dict[str, Any]:
    """Report whether the prepared guest is remotely usable without host-window input."""
    try:
        return BRIDGE.remote_status(
            profile,
            require_agent_ready=require_agent_ready,
            require_vnc=require_vnc,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vm_restart(profile: str, grace_s: int | None = None) -> dict[str, Any]:
    """Restart the local VM app instance for this profile and reopen the VM."""
    try:
        return BRIDGE.vm_restart(profile, grace_s=grace_s)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def recover_prepared_guest(
    profile: str,
    timeout_s: int | None = None,
    restart_on_unready: bool | None = None,
) -> dict[str, Any]:
    """Recover a prepared guest by reopening or restarting the VM until SSH and agent readiness return."""
    try:
        return BRIDGE.recover_prepared_guest(
            profile,
            timeout_s=timeout_s,
            restart_on_unready=restart_on_unready,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
async def wait_for_services(
    profile: str,
    timeout_s: int = 180,
    require_vnc: bool = False,
    require_agent: bool = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Wait until SSH, VNC, and optionally the guest session agent are ready."""
    try:
        if ctx is not None:
            await ctx.info(
                f"Waiting for services on profile={profile} require_vnc={require_vnc} require_agent={require_agent}"
            )

        # Custom loop here so Codex sees progress updates instead of one long blocking call.
        import time

        deadline = time.time() + timeout_s
        checks: list[dict[str, Any]] = []
        while time.time() < deadline:
            status = BRIDGE.remote_status(
                profile,
                require_agent_ready=require_agent,
                require_vnc=require_vnc,
            )
            checks.append(status)

            if ctx is not None:
                elapsed = max(timeout_s - (deadline - time.time()), 0.0)
                await ctx.report_progress(
                    progress=min(elapsed / max(timeout_s, 1), 1.0),
                    total=1.0,
                    message=(
                        f"ssh={status['ssh'].get('ok')} "
                        f"agent_health={status['agent_health'].get('ok')} "
                        f"agent_ready={status['agent_ready'].get('ok')} "
                        f"vnc={status['vnc'].get('ok')}"
                    ),
                )

            if status.get("ok"):
                return {
                    "ok": True,
                    "profile": profile,
                    "status": status,
                    "checks": checks,
                }
            time.sleep(2.0)

        return {
            "ok": False,
            "profile": profile,
            "checks": checks,
            "error": f"Timed out after {timeout_s}s waiting for services",
        }
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def ssh_exec(
    profile: str,
    command: str,
    timeout_s: int = 60,
    get_pty: bool = False,
    login_shell: bool = True,
) -> dict[str, Any]:
    """Run a command over SSH inside the guest."""
    try:
        return BRIDGE.ssh_exec(
            profile,
            command,
            timeout_s=timeout_s,
            get_pty=get_pty,
            login_shell=login_shell,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def sftp_put(profile: str, local_path: str, remote_path: str) -> dict[str, Any]:
    """Copy a file from host to guest over SFTP."""
    try:
        return BRIDGE.sftp_put(profile, local_path, remote_path)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def sftp_get(profile: str, remote_path: str, local_path: str) -> dict[str, Any]:
    """Copy a file from guest to host over SFTP."""
    try:
        return BRIDGE.sftp_get(profile, remote_path, local_path)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def guest_agent_health(profile: str) -> dict[str, Any]:
    """Check whether the guest session agent is reachable."""
    try:
        return BRIDGE.check_agent(profile)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def guest_agent_ready(profile: str) -> dict[str, Any]:
    """Check whether the guest session agent and desktopctl are ready for semantic control."""
    try:
        return BRIDGE.check_agent_ready(profile)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def repair_guest_agent(profile: str) -> dict[str, Any]:
    """Kickstart the guest LaunchAgent over SSH and re-check guest agent readiness."""
    try:
        return BRIDGE.repair_guest_agent(profile)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def stage_guest_support(profile: str, remote_dir: str = "~/vm-codex-bridge") -> dict[str, Any]:
    """Upload the bundled guest support scripts into the VM over SSH."""
    try:
        return BRIDGE.stage_guest_support(profile, remote_dir=remote_dir)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def audit_prepared_guest(profile: str, remote_dir: str = "~/vm-codex-bridge") -> dict[str, Any]:
    """Run the prepared-guest audit script inside the guest over SSH."""
    try:
        return BRIDGE.audit_prepared_guest(profile, remote_dir=remote_dir)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def prepare_prepared_guest(
    profile: str,
    desktopctl_path: str,
    remote_dir: str = "~/vm-codex-bridge",
) -> dict[str, Any]:
    """Stage guest support files, install the guest agent, and apply prepared-guest defaults."""
    try:
        return BRIDGE.prepare_prepared_guest(
            profile,
            desktopctl_path=desktopctl_path,
            remote_dir=remote_dir,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def guest_desktopctl(
    profile: str,
    argv: list[str],
    timeout_s: int = 60,
    parse_json: bool = True,
) -> dict[str, Any]:
    """Run desktopctl inside the guest's logged-in GUI session via the guest agent."""
    try:
        return BRIDGE.guest_desktopctl(
            profile,
            argv,
            timeout_s=timeout_s,
            parse_json=parse_json,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool(structured_output=False)
def vnc_capture(profile: str) -> Image | dict[str, Any]:
    """Return a live VNC screenshot as an MCP image."""
    try:
        png_bytes = BRIDGE.vnc_capture_bytes(profile)
        return Image(data=png_bytes, format="png")
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vnc_capture_to_file(profile: str, output_path: str) -> dict[str, Any]:
    """Capture a VNC screenshot to a host file path."""
    try:
        return BRIDGE.vnc_capture_to_file(profile, output_path)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vnc_move(profile: str, x: int, y: int) -> dict[str, Any]:
    """Move the VNC mouse pointer to x,y."""
    try:
        return BRIDGE.vnc_move(profile, x, y)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vnc_click(profile: str, x: int, y: int, button: int = 1) -> dict[str, Any]:
    """Move the VNC mouse to x,y and click."""
    try:
        return BRIDGE.vnc_click(profile, x, y, button=button)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vnc_drag(
    profile: str,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    step: int = 5,
    button: int = 1,
) -> dict[str, Any]:
    """Drag the VNC mouse from one coordinate to another."""
    try:
        return BRIDGE.vnc_drag(
            profile,
            start_x,
            start_y,
            end_x,
            end_y,
            step=step,
            button=button,
        )
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vnc_key(profile: str, key: str) -> dict[str, Any]:
    """Send a single VNC keypress such as enter, esc, tab, cmd-c, etc."""
    try:
        return BRIDGE.vnc_key(profile, key)
    except Exception as exc:
        return _wrap_error(exc)


@mcp.tool()
def vnc_type(profile: str, text: str) -> dict[str, Any]:
    """Type or paste plain text over VNC."""
    try:
        return BRIDGE.vnc_type(profile, text)
    except Exception as exc:
        return _wrap_error(exc)


if __name__ == "__main__":
    try:
        mcp.run()
    except BridgeError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        raise
