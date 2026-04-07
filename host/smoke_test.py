#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time

from vm_bridge_core import load_bridge_from_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a vm-codex-bridge profile")
    parser.add_argument("--profile", required=True, help="Profile name from VM_BRIDGE_CONFIG")
    parser.add_argument("--open-vm", action="store_true", help="Launch the VM before waiting")
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=180,
        help="How long to wait for SSH/VNC/agent readiness",
    )
    parser.add_argument(
        "--skip-vnc",
        action="store_true",
        help="Do not require VNC when waiting",
    )
    parser.add_argument(
        "--skip-ssh",
        action="store_true",
        help="Do not require SSH when waiting",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Do not require the guest session agent when waiting",
    )
    parser.add_argument(
        "--capture",
        help="Write a VNC screenshot to this path after readiness succeeds",
    )
    parser.add_argument(
        "--local-capture",
        help="Write a host-local VM window capture to this path after the VM opens",
    )
    parser.add_argument(
        "--desktopctl",
        nargs=argparse.REMAINDER,
        help="Run desktopctl argv in the guest after readiness. Example: --desktopctl browser snapshot --json",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Only use the host-local bootstrap path. Skip SSH, VNC, and agent waits.",
    )
    args = parser.parse_args()

    if args.local_only:
        args.skip_ssh = True
        args.skip_vnc = True
        args.skip_agent = True

    bridge = load_bridge_from_env()

    print(json.dumps(bridge.profile_info(args.profile), indent=2))

    if args.open_vm:
        print(json.dumps(bridge.vm_open(args.profile), indent=2))

    if args.local_capture:
        deadline = time.time() + args.timeout_s
        last_error = None
        while time.time() < deadline:
            try:
                print(
                    json.dumps(
                        bridge.local_vm_capture_to_file(args.profile, args.local_capture),
                        indent=2,
                    )
                )
                break
            except Exception as exc:
                last_error = str(exc)
                time.sleep(2)
        else:
            raise SystemExit(
                f"Timed out after {args.timeout_s}s waiting for a local VM window: {last_error}"
            )

    if (
        args.skip_ssh
        and args.skip_vnc
        and args.skip_agent
        and not args.capture
        and not args.desktopctl
    ):
        return

    deadline = time.time() + args.timeout_s
    while time.time() < deadline:
        ssh = {"ok": True} if args.skip_ssh else bridge.check_ssh(args.profile)
        vnc = {"ok": True} if args.skip_vnc else bridge.check_vnc(args.profile)
        agent = {"ok": True} if args.skip_agent else bridge.check_agent(args.profile)
        status = {"ssh": ssh, "vnc": vnc, "agent": agent}
        print(json.dumps(status, indent=2))
        if ssh.get("ok") and vnc.get("ok") and agent.get("ok"):
            break
        time.sleep(2)
    else:
        raise SystemExit(f"Timed out after {args.timeout_s}s waiting for services")

    if args.capture:
        print(json.dumps(bridge.vnc_capture_to_file(args.profile, args.capture), indent=2))

    if args.desktopctl:
        argv = list(args.desktopctl)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if argv:
            print(json.dumps(bridge.guest_desktopctl(args.profile, argv), indent=2))


if __name__ == "__main__":
    main()
