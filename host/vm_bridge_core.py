from __future__ import annotations

import json
import logging
import os
import posixpath
import shlex
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import paramiko


LOGGER = logging.getLogger("vm_bridge_core")
DEFAULT_CONFIG_ENV = "VM_BRIDGE_CONFIG"
DEFAULT_CONFIG_PATH = "~/.vm-codex-bridge/profiles.json"
HTTP_STATUS_MARKER = "__VM_CODEX_HTTP_STATUS__="
GUEST_AGENT_LABEL = "com.elamins.vm_codex_bridge.guest_agent"
HOST_GUEST_ASSETS_DIR = Path(__file__).resolve().parent.parent / "guest"
GUEST_SUPPORT_FILES: tuple[tuple[str, int], ...] = (
    ("guest_session_agent.py", 0o755),
    ("guest_install_agent.sh", 0o755),
    ("guest_prepare_prepared_guest.sh", 0o755),
    ("guest_audit_prepared_guest.sh", 0o755),
)
LOCAL_VM_INPUT_SWIFT = textwrap.dedent(
    r"""
    import ApplicationServices
    import Foundation

    enum HelperError: Error {
        case usage(String)
        case invalidPid(String)
        case invalidRepeat(String)
        case unsupportedKey(String)
        case invalidCoordinate(String)
    }

    let specialKeyCodes: [String: CGKeyCode] = [
        "return": 36,
        "enter": 36,
        "tab": 48,
        "space": 49,
        "escape": 53,
        "esc": 53,
        "delete": 51,
        "backspace": 51,
        "forwarddelete": 117,
        "left": 123,
        "right": 124,
        "down": 125,
        "up": 126,
        "home": 115,
        "end": 119,
        "pageup": 116,
        "pagedown": 121,
        "minus": 27,
        "dash": 27,
        "period": 47,
        "dot": 47,
        "slash": 44,
    ]

    let characterKeyCodes: [Character: CGKeyCode] = [
        "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34,
        "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
        "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
        "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
        "-": 27, "=": 24, "[": 33, "]": 30, ";": 41, "'": 39, ",": 43, ".": 47, "/": 44, "\\": 42, "`": 50
    ]

    let modifierFlags: [String: CGEventFlags] = [
        "cmd": .maskCommand,
        "command": .maskCommand,
        "meta": .maskCommand,
        "shift": .maskShift,
        "ctrl": .maskControl,
        "control": .maskControl,
        "opt": .maskAlternate,
        "option": .maskAlternate,
        "alt": .maskAlternate,
    ]

    func postUnicode(_ text: String, to pid: pid_t, delayMicros: useconds_t) {
        for scalar in text.unicodeScalars {
            var value = UInt16(scalar.value)
            guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
                continue
            }
            down.keyboardSetUnicodeString(stringLength: 1, unicodeString: &value)
            up.keyboardSetUnicodeString(stringLength: 1, unicodeString: &value)
            down.postToPid(pid)
            usleep(delayMicros)
            up.postToPid(pid)
            usleep(delayMicros)
        }
    }

    func postKey(code: CGKeyCode, flags: CGEventFlags, to pid: pid_t, repeatCount: Int, delayMicros: useconds_t) {
        for _ in 0..<max(repeatCount, 1) {
            guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else {
                continue
            }
            down.flags = flags
            up.flags = flags
            down.postToPid(pid)
            usleep(delayMicros)
            up.postToPid(pid)
            usleep(delayMicros)
        }
    }

    func postClick(x: Double, y: Double, to pid: pid_t, delayMicros: useconds_t) {
        let point = CGPoint(x: x, y: y)
        guard let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left),
              let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
              let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else {
            return
        }
        move.postToPid(pid)
        usleep(delayMicros)
        down.postToPid(pid)
        usleep(delayMicros)
        up.postToPid(pid)
        usleep(delayMicros)
    }

    func parseKeySpec(_ spec: String) throws -> (CGKeyCode, CGEventFlags) {
        let parts = spec
            .lowercased()
            .split(separator: "-")
            .map { String($0) }
            .filter { !$0.isEmpty }
        guard let keyToken = parts.last else {
            throw HelperError.unsupportedKey(spec)
        }

        var flags: CGEventFlags = []
        for modifier in parts.dropLast() {
            guard let flag = modifierFlags[modifier] else {
                throw HelperError.unsupportedKey(spec)
            }
            flags.insert(flag)
        }

        if let code = specialKeyCodes[keyToken] {
            return (code, flags)
        }
        if keyToken.count == 1, let char = keyToken.first, let code = characterKeyCodes[char] {
            return (code, flags)
        }
        throw HelperError.unsupportedKey(spec)
    }

    let arguments = CommandLine.arguments
    guard arguments.count >= 2 else {
        throw HelperError.usage("expected mode")
    }
    let mode = arguments[1]

    switch mode {
    case "window":
        guard arguments.count >= 4 else {
            throw HelperError.usage("expected window appName windowHint")
        }
        let appName = arguments[2]
        let windowHint = arguments[3]
        let rawWindows = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
        var matches = [[String: Any]]()
        var fallbacks = [[String: Any]]()

        for window in rawWindows {
            let owner = (window[kCGWindowOwnerName as String] as? String) ?? ""
            if owner != appName {
                continue
            }
            let name = (window[kCGWindowName as String] as? String) ?? ""
            let bounds = (window[kCGWindowBounds as String] as? [String: Any]) ?? [:]
            let record: [String: Any] = [
                "owner_name": owner,
                "window_name": name,
                "window_number": Int(window[kCGWindowNumber as String] as? NSNumber ?? 0),
                "owner_pid": Int(window[kCGWindowOwnerPID as String] as? NSNumber ?? 0),
                "layer": Int(window[kCGWindowLayer as String] as? NSNumber ?? 0),
                "bounds": [
                    "x": Int(bounds["X"] as? NSNumber ?? 0),
                    "y": Int(bounds["Y"] as? NSNumber ?? 0),
                    "width": Int(bounds["Width"] as? NSNumber ?? 0),
                    "height": Int(bounds["Height"] as? NSNumber ?? 0),
                ],
                "area": Int((bounds["Width"] as? NSNumber ?? 0).intValue * (bounds["Height"] as? NSNumber ?? 0).intValue),
            ]
            if !windowHint.isEmpty && name.contains(windowHint) {
                matches.append(record)
            } else {
                fallbacks.append(record)
            }
        }

        let records = (matches.isEmpty ? fallbacks : matches).sorted { left, right in
            let leftLayer = left["layer"] as? Int ?? 0
            let rightLayer = right["layer"] as? Int ?? 0
            if leftLayer != rightLayer {
                return leftLayer < rightLayer
            }
            let leftArea = left["area"] as? Int ?? 0
            let rightArea = right["area"] as? Int ?? 0
            if leftArea != rightArea {
                return leftArea > rightArea
            }
            let leftWindow = left["window_number"] as? Int ?? 0
            let rightWindow = right["window_number"] as? Int ?? 0
            return leftWindow < rightWindow
        }

        let payload = ["matches": records]
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
    case "type":
        guard arguments.count >= 4 else {
            throw HelperError.usage("expected type pid text")
        }
        guard let pidValue = Int32(arguments[2]) else {
            throw HelperError.invalidPid(arguments[2])
        }
        let pid = pid_t(pidValue)
        let text = arguments[3]
        let submit = arguments.contains("--submit")
        postUnicode(text, to: pid, delayMicros: 12000)
        if submit {
            postKey(code: 36, flags: [], to: pid, repeatCount: 1, delayMicros: 15000)
        }
    case "key":
        guard arguments.count >= 4 else {
            throw HelperError.usage("expected key pid spec [repeat]")
        }
        guard let pidValue = Int32(arguments[2]) else {
            throw HelperError.invalidPid(arguments[2])
        }
        let pid = pid_t(pidValue)
        let spec = arguments[3]
        let repeatCount: Int
        if arguments.count >= 5 {
            guard let parsed = Int(arguments[4]) else {
                throw HelperError.invalidRepeat(arguments[4])
            }
            repeatCount = max(parsed, 1)
        } else {
            repeatCount = 1
        }
        let (code, flags) = try parseKeySpec(spec)
        postKey(code: code, flags: flags, to: pid, repeatCount: repeatCount, delayMicros: 15000)
    case "click":
        guard arguments.count >= 5 else {
            throw HelperError.usage("expected click pid x y")
        }
        guard let pidValue = Int32(arguments[2]) else {
            throw HelperError.invalidPid(arguments[2])
        }
        guard let x = Double(arguments[3]) else {
            throw HelperError.invalidCoordinate(arguments[3])
        }
        guard let y = Double(arguments[4]) else {
            throw HelperError.invalidCoordinate(arguments[4])
        }
        let pid = pid_t(pidValue)
        postClick(x: x, y: y, to: pid, delayMicros: 15000)
    default:
        throw HelperError.usage("unsupported mode \(mode)")
    }
    """
).strip()


class BridgeError(RuntimeError):
    """Base exception for VM bridge failures."""


@dataclass(slots=True)
class VMConfig:
    path: str
    app: str | None = None
    app_path: str | None = None
    open_args: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SSHConfig:
    host: str | None = None
    user: str | None = None
    port: int = 22
    key_path: str | None = None
    password_env: str | None = None
    known_hosts_path: str | None = None
    allow_unknown_hosts: bool = False
    connect_timeout_s: int = 10
    banner_timeout_s: int = 10
    auth_timeout_s: int = 15


@dataclass(slots=True)
class VNCConfig:
    host: str | None = None
    port: int = 5900
    username: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    connect_timeout_s: int = 10


@dataclass(slots=True)
class AgentConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    connect_timeout_s: int = 10


@dataclass(slots=True)
class PolicyConfig:
    mode: str = "prepared_guest"
    allow_local_window_controls: bool = False
    require_agent_ready: bool = True
    require_vnc: bool = False
    startup_timeout_s: int = 180
    recovery_timeout_s: int = 240
    restart_on_unready: bool = True
    restart_grace_s: int = 12


@dataclass(slots=True)
class Profile:
    name: str
    vm: VMConfig
    ssh: SSHConfig
    vnc: VNCConfig
    agent: AgentConfig
    policy: PolicyConfig


@dataclass(slots=True)
class SSHExecResult:
    profile: str
    command: str
    wrapped_command: str
    exit_status: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.exit_status == 0,
            "profile": self.profile,
            "command": self.command,
            "wrapped_command": self.wrapped_command,
            "exit_status": self.exit_status,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class Bridge:
    def __init__(
        self,
        profiles: dict[str, Profile],
        *,
        default_profile: str | None = None,
        config_path: str | None = None,
    ) -> None:
        if not profiles:
            raise BridgeError("No bridge profiles were loaded")
        self._profiles = profiles
        self._default_profile = default_profile
        self._config_path = config_path
        self._local_capture_meta: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_file(cls, config_path: str | os.PathLike[str]) -> "Bridge":
        path = Path(config_path).expanduser().resolve()
        if not path.exists():
            raise BridgeError(
                f"Bridge config file not found: {path}. Set {DEFAULT_CONFIG_ENV} or create the default config at {DEFAULT_CONFIG_PATH}."
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BridgeError(f"Invalid JSON in bridge config {path}: {exc}") from exc

        base_dir = path.parent
        if "profiles" in raw:
            default_profile = raw.get("default_profile")
            profiles_raw = raw.get("profiles", {})
        else:
            default_profile = None
            profiles_raw = raw

        if not isinstance(profiles_raw, dict):
            raise BridgeError("Bridge config must contain an object under 'profiles'")

        profiles: dict[str, Profile] = {}
        for name, profile_raw in profiles_raw.items():
            if not isinstance(name, str):
                raise BridgeError(f"Profile names must be strings, got {name!r}")
            if not isinstance(profile_raw, dict):
                raise BridgeError(f"Profile {name!r} must be a JSON object")
            profiles[name] = _parse_profile(name, profile_raw, base_dir=base_dir)

        if default_profile is not None and default_profile not in profiles:
            raise BridgeError(
                f"default_profile={default_profile!r} is not defined in config {path}"
            )

        return cls(profiles, default_profile=default_profile, config_path=str(path))

    def list_profiles(self) -> list[str]:
        return sorted(self._profiles)

    def profile_info(self, profile: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        return {
            "ok": True,
            "profile": p.name,
            "config_path": self._config_path,
            "vm": {
                "path": p.vm.path,
                "app": p.vm.app,
                "app_path": p.vm.app_path,
                "open_args": list(p.vm.open_args),
            },
            "ssh": {
                "configured": self._ssh_configured(p),
                "host": p.ssh.host,
                "port": p.ssh.port,
                "user": p.ssh.user,
                "key_path": p.ssh.key_path,
                "password_env": p.ssh.password_env,
                "password_present": _env_present(p.ssh.password_env),
                "known_hosts_path": p.ssh.known_hosts_path,
                "allow_unknown_hosts": p.ssh.allow_unknown_hosts,
                "connect_timeout_s": p.ssh.connect_timeout_s,
            },
            "vnc": {
                "configured": self._vnc_configured(p),
                "host": p.vnc.host,
                "port": p.vnc.port,
                "username": p.vnc.username,
                "username_env": p.vnc.username_env,
                "username_present": _env_present(p.vnc.username_env),
                "password_env": p.vnc.password_env,
                "password_present": _env_present(p.vnc.password_env),
                "connect_timeout_s": p.vnc.connect_timeout_s,
            },
            "agent": {
                "host": p.agent.host,
                "port": p.agent.port,
                "connect_timeout_s": p.agent.connect_timeout_s,
            },
            "policy": {
                "mode": p.policy.mode,
                "allow_local_window_controls": p.policy.allow_local_window_controls,
                "require_agent_ready": p.policy.require_agent_ready,
                "require_vnc": p.policy.require_vnc,
                "startup_timeout_s": p.policy.startup_timeout_s,
                "recovery_timeout_s": p.policy.recovery_timeout_s,
                "restart_on_unready": p.policy.restart_on_unready,
                "restart_grace_s": p.policy.restart_grace_s,
            },
        }

    def vm_open(self, profile: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        vm_path = Path(p.vm.path).expanduser().resolve()
        if not vm_path.exists():
            raise BridgeError(f"VM path does not exist for profile {profile!r}: {vm_path}")

        app_name = p.vm.app
        if not app_name and not p.vm.app_path:
            if vm_path.suffix.lower() == ".vimi":
                app_name = "Vimy"
            else:
                app_name = "Viable"

        cmd = ["/usr/bin/open"]
        if p.vm.app_path:
            cmd.extend(["-a", p.vm.app_path])
        elif app_name:
            cmd.extend(["-a", app_name])
        cmd.append(str(vm_path))
        if p.vm.open_args:
            cmd.append("--args")
            cmd.extend(p.vm.open_args)

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "profile": profile,
            "command": cmd,
            "exit_status": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "vm_path": str(vm_path),
            "app": p.vm.app_path or app_name,
        }

    def check_ssh(self, profile: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        if not self._ssh_configured(p):
            return {
                "ok": False,
                "profile": profile,
                "error": (
                    f"SSH is not configured for profile={profile}. "
                    "Fill in ssh.host and ssh.user in VM_BRIDGE_CONFIG before using SSH-based tools."
                ),
                "error_type": "BridgeConfigError",
            }
        try:
            with self._ssh_client(p) as client:
                transport = client.get_transport()
                peer = transport.getpeername() if transport is not None else None
                return {
                    "ok": True,
                    "profile": profile,
                    "host": p.ssh.host,
                    "port": p.ssh.port,
                    "user": p.ssh.user,
                    "peer": peer,
                }
        except Exception as exc:
            return {
                "ok": False,
                "profile": profile,
                "host": p.ssh.host,
                "port": p.ssh.port,
                "user": p.ssh.user,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    def check_vnc(self, profile: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        if not self._vnc_configured(p):
            return {
                "ok": False,
                "profile": profile,
                "error": (
                    f"VNC is not configured for profile={profile}. "
                    "Fill in vnc.host and the related password env before using VNC tools."
                ),
                "error_type": "BridgeConfigError",
            }
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                self._run_vnc_command(
                    p,
                    ["capture", str(tmp_path)],
                    timeout_s=max(int(p.vnc.connect_timeout_s) + 10, 15),
                )
            finally:
                tmp_path.unlink(missing_ok=True)
            return {
                "ok": True,
                "profile": profile,
                "host": p.vnc.host,
                "port": p.vnc.port,
            }
        except Exception as exc:
            return {
                "ok": False,
                "profile": profile,
                "host": p.vnc.host,
                "port": p.vnc.port,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    def check_agent(self, profile: str) -> dict[str, Any]:
        try:
            return self._agent_request(profile, "GET", "/health", timeout_s=10)
        except Exception as exc:
            p = self._get_profile(profile)
            return {
                "ok": False,
                "profile": profile,
                "host": p.agent.host,
                "port": p.agent.port,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    def check_agent_ready(self, profile: str) -> dict[str, Any]:
        try:
            return self._agent_request(profile, "GET", "/ready", timeout_s=15)
        except Exception as exc:
            p = self._get_profile(profile)
            return {
                "ok": False,
                "profile": profile,
                "host": p.agent.host,
                "port": p.agent.port,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    def remote_status(
        self,
        profile: str,
        *,
        require_agent_ready: bool | None = None,
        require_vnc: bool | None = None,
        include_local_window: bool = True,
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        want_agent_ready = (
            p.policy.require_agent_ready
            if require_agent_ready is None
            else bool(require_agent_ready)
        )
        want_vnc = p.policy.require_vnc if require_vnc is None else bool(require_vnc)
        resolved_hosts = {
            "ssh": self._resolve_host_addrs(p.ssh.host),
            "vnc": self._resolve_host_addrs(p.vnc.host),
        }
        ssh = self.check_ssh(profile)
        agent_health = self.check_agent(profile)
        agent_ready = self.check_agent_ready(profile) if want_agent_ready else {"ok": True}
        vnc = self.check_vnc(profile) if want_vnc else {"ok": True}
        status = {
            "ok": bool(ssh.get("ok") and agent_health.get("ok") and agent_ready.get("ok") and vnc.get("ok")),
            "profile": profile,
            "policy": {
                "mode": p.policy.mode,
                "allow_local_window_controls": p.policy.allow_local_window_controls,
                "require_agent_ready": want_agent_ready,
                "require_vnc": want_vnc,
            },
            "resolved_hosts": resolved_hosts,
            "ssh": ssh,
            "agent_health": agent_health,
            "agent_ready": agent_ready,
            "vnc": vnc,
        }
        if include_local_window:
            try:
                status["local_vm"] = self.local_vm_info(profile)
            except Exception as exc:
                status["local_vm"] = {
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
        return status

    def vm_restart(self, profile: str, *, grace_s: int | None = None) -> dict[str, Any]:
        p = self._get_profile(profile)
        info = self.local_vm_info(profile)
        owner_pid = int(info["owner_pid"])
        grace = max(int(grace_s or p.policy.restart_grace_s), 1)
        os.kill(owner_pid, signal.SIGTERM)
        deadline = time.time() + grace
        terminated = False
        while time.time() < deadline:
            if not self._pid_exists(owner_pid):
                terminated = True
                break
            time.sleep(0.5)
        if not terminated and self._pid_exists(owner_pid):
            raise BridgeError(
                f"Timed out waiting for VM app pid={owner_pid} to exit for profile={profile}"
            )
        reopened = self.vm_open(profile)
        return {
            "ok": bool(reopened.get("ok")),
            "profile": profile,
            "terminated_pid": owner_pid,
            "reopened": reopened,
        }

    def recover_prepared_guest(
        self,
        profile: str,
        *,
        timeout_s: int | None = None,
        restart_on_unready: bool | None = None,
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        if p.policy.mode != "prepared_guest":
            raise BridgeError(
                f"recover_prepared_guest requires policy.mode='prepared_guest', got {p.policy.mode!r}"
            )
        total_timeout = max(int(timeout_s or p.policy.recovery_timeout_s), 30)
        allow_restart = (
            p.policy.restart_on_unready
            if restart_on_unready is None
            else bool(restart_on_unready)
        )
        actions: list[dict[str, Any]] = []

        open_result = self.vm_open(profile)
        actions.append({"step": "vm_open", "result": open_result})
        initial = self._wait_for_remote_ready(
            profile,
            timeout_s=min(p.policy.startup_timeout_s, total_timeout),
        )
        actions.append({"step": "wait_for_remote_ready", "result": initial})
        if initial.get("ok"):
            return {"ok": True, "profile": profile, "actions": actions, "status": initial}

        initial_status = initial.get("status") or {}
        if initial_status.get("ssh", {}).get("ok") and not initial_status.get("agent_ready", {}).get("ok"):
            repair = self.repair_guest_agent(profile)
            actions.append({"step": "repair_guest_agent", "result": repair})
            repaired = self._wait_for_remote_ready(profile, timeout_s=20)
            actions.append({"step": "wait_for_remote_ready_after_agent_repair", "result": repaired})
            if repaired.get("ok"):
                return {"ok": True, "profile": profile, "actions": actions, "status": repaired}

        if not allow_restart:
            return {
                "ok": False,
                "profile": profile,
                "actions": actions,
                "error": "Guest did not become remotely ready and restart_on_unready is disabled",
                "status": initial,
            }

        restart = self.vm_restart(profile)
        actions.append({"step": "vm_restart", "result": restart})
        remaining = max(total_timeout - min(p.policy.startup_timeout_s, total_timeout), 30)
        post_restart = self._wait_for_remote_ready(profile, timeout_s=remaining)
        actions.append({"step": "wait_for_remote_ready_after_restart", "result": post_restart})
        post_restart_status = post_restart.get("status") or {}
        final = post_restart
        if post_restart_status.get("ssh", {}).get("ok") and not post_restart_status.get("agent_ready", {}).get("ok"):
            repair = self.repair_guest_agent(profile)
            actions.append({"step": "repair_guest_agent_after_restart", "result": repair})
            final = self._wait_for_remote_ready(profile, timeout_s=20)
            actions.append({"step": "wait_for_remote_ready_after_restart_agent_repair", "result": final})
        return {
            "ok": bool(final.get("ok")),
            "profile": profile,
            "actions": actions,
            "status": final,
        }

    def repair_guest_agent(self, profile: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        plist_path = f"/Users/{p.ssh.user}/Library/LaunchAgents/{GUEST_AGENT_LABEL}.plist"
        command = textwrap.dedent(
            f"""
            set -euo pipefail
            label={shlex.quote(GUEST_AGENT_LABEL)}
            plist={shlex.quote(plist_path)}
            if [[ ! -f "$plist" ]]; then
              echo "missing_launchagent:$plist" >&2
              exit 4
            fi
            launchctl bootstrap "gui/$UID" "$plist" >/dev/null 2>&1 || true
            launchctl enable "gui/$UID/$label" >/dev/null 2>&1 || true
            launchctl kickstart -k "gui/$UID/$label"
            sleep 1
            launchctl print "gui/$UID/$label" >/dev/null
            """
        ).strip()
        transport = self._ssh_exec_raw(p, command, timeout_s=30)
        health = self.check_agent(profile)
        ready = self.check_agent_ready(profile)
        return {
            "ok": bool(transport.exit_status == 0 and health.get("ok")),
            "profile": profile,
            "label": GUEST_AGENT_LABEL,
            "transport": transport.to_dict(),
            "agent_health": health,
            "agent_ready": ready,
        }

    def stage_guest_support(self, profile: str, remote_dir: str = "~/vm-codex-bridge") -> dict[str, Any]:
        p = self._get_profile(profile)
        dest_dir = self._expand_remote_path(p, remote_dir)
        staged: list[dict[str, Any]] = []

        with self._ssh_client(p) as client:
            with client.open_sftp() as sftp:
                self._sftp_mkdir_p(sftp, dest_dir)
                for filename, mode in GUEST_SUPPORT_FILES:
                    src = HOST_GUEST_ASSETS_DIR / filename
                    if not src.exists():
                        raise BridgeError(f"Guest support asset is missing on the host: {src}")
                    dest = posixpath.join(dest_dir, filename)
                    sftp.put(str(src), dest)
                    sftp.chmod(dest, mode)
                    stat = sftp.stat(dest)
                    staged.append(
                        {
                            "filename": filename,
                            "remote_path": dest,
                            "size": int(stat.st_size),
                            "mode": oct(mode),
                        }
                    )

        return {
            "ok": True,
            "profile": profile,
            "remote_dir": dest_dir,
            "files": staged,
        }

    def audit_prepared_guest(self, profile: str, remote_dir: str = "~/vm-codex-bridge") -> dict[str, Any]:
        p = self._get_profile(profile)
        staged = self.stage_guest_support(profile, remote_dir=remote_dir)
        remote_script = posixpath.join(staged["remote_dir"], "guest_audit_prepared_guest.sh")
        result = self._ssh_exec_raw(
            p,
            f"bash {shlex.quote(remote_script)} --json",
            timeout_s=60,
            get_pty=False,
            login_shell=True,
        )
        raw_stdout = result.stdout.strip()
        try:
            audit = json.loads(raw_stdout) if raw_stdout else {}
        except json.JSONDecodeError:
            audit = {"raw_stdout": raw_stdout}
        return {
            "ok": result.ok,
            "profile": profile,
            "staged": staged,
            "audit": audit,
            "ssh": result.to_dict(),
        }

    def prepare_prepared_guest(
        self,
        profile: str,
        desktopctl_path: str,
        remote_dir: str = "~/vm-codex-bridge",
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        staged = self.stage_guest_support(profile, remote_dir=remote_dir)
        install_script = posixpath.join(staged["remote_dir"], "guest_install_agent.sh")
        prepare_script = posixpath.join(staged["remote_dir"], "guest_prepare_prepared_guest.sh")

        install_result = self._ssh_exec_raw(
            p,
            f"bash {shlex.quote(install_script)} {shlex.quote(desktopctl_path)}",
            timeout_s=120,
            get_pty=False,
            login_shell=True,
        )
        prepare_result = self._ssh_exec_raw(
            p,
            f"bash {shlex.quote(prepare_script)} --json",
            timeout_s=120,
            get_pty=False,
            login_shell=True,
        )
        prepare_stdout = prepare_result.stdout.strip()
        try:
            prepare_payload = json.loads(prepare_stdout) if prepare_stdout else {}
        except json.JSONDecodeError:
            prepare_payload = {"raw_stdout": prepare_stdout}
        audit = self.audit_prepared_guest(profile, remote_dir=remote_dir)
        health = self.check_agent(profile)
        ready = self.check_agent_ready(profile)
        return {
            "ok": install_result.ok and prepare_result.ok and bool(health.get("ok")) and bool(ready.get("ok")),
            "profile": profile,
            "staged": staged,
            "install": install_result.to_dict(),
            "prepare": prepare_payload,
            "prepare_ssh": prepare_result.to_dict(),
            "audit": audit,
            "agent_health": health,
            "agent_ready": ready,
        }

    def ssh_exec(
        self,
        profile: str,
        command: str,
        *,
        timeout_s: int = 60,
        get_pty: bool = False,
        login_shell: bool = True,
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        result = self._ssh_exec_raw(
            p,
            command,
            timeout_s=timeout_s,
            get_pty=get_pty,
            login_shell=login_shell,
        )
        return result.to_dict()

    def sftp_put(self, profile: str, local_path: str, remote_path: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        src = Path(local_path).expanduser().resolve()
        if not src.exists():
            raise BridgeError(f"Local file does not exist: {src}")
        if src.is_dir():
            raise BridgeError(f"sftp_put only supports files, not directories: {src}")
        dest = self._expand_remote_path(p, remote_path)

        with self._ssh_client(p) as client:
            with client.open_sftp() as sftp:
                self._sftp_mkdir_p(sftp, posixpath.dirname(dest))
                sftp.put(str(src), dest)
                stat = sftp.stat(dest)

        return {
            "ok": True,
            "profile": profile,
            "local_path": str(src),
            "remote_path": dest,
            "size": int(stat.st_size),
        }

    def sftp_get(self, profile: str, remote_path: str, local_path: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        src = self._expand_remote_path(p, remote_path)
        dest = Path(local_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        with self._ssh_client(p) as client:
            with client.open_sftp() as sftp:
                stat = sftp.stat(src)
                sftp.get(src, str(dest))

        return {
            "ok": True,
            "profile": profile,
            "remote_path": src,
            "local_path": str(dest),
            "size": int(stat.st_size),
        }

    def guest_desktopctl(
        self,
        profile: str,
        argv: list[str],
        *,
        timeout_s: int = 60,
        parse_json: bool = True,
    ) -> dict[str, Any]:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise BridgeError("guest_desktopctl argv must be a non-empty list[str]")
        payload = {
            "argv": argv,
            "timeout_s": int(timeout_s),
            "parse_json": bool(parse_json),
        }
        return self._agent_request(
            profile,
            "POST",
            "/desktopctl",
            payload=payload,
            timeout_s=max(timeout_s + 5, 10),
        )

    def vnc_capture_bytes(self, profile: str) -> bytes:
        p = self._get_profile(profile)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            self._run_vnc_command(
                p,
                ["capture", str(tmp_path)],
                timeout_s=max(int(p.vnc.connect_timeout_s) + 10, 15),
            )
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

    def vnc_capture_to_file(self, profile: str, output_path: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        dest = Path(output_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._run_vnc_command(
            p,
            ["capture", str(dest)],
            timeout_s=max(int(p.vnc.connect_timeout_s) + 10, 15),
        )
        return {
            "ok": True,
            "profile": profile,
            "output_path": str(dest),
        }

    def vnc_move(self, profile: str, x: int, y: int) -> dict[str, Any]:
        p = self._get_profile(profile)
        self._run_vnc_command(p, ["move", str(int(x)), str(int(y))])
        return {"ok": True, "profile": profile, "x": int(x), "y": int(y)}

    def vnc_click(self, profile: str, x: int, y: int, *, button: int = 1) -> dict[str, Any]:
        p = self._get_profile(profile)
        self._run_vnc_command(
            p,
            ["move", str(int(x)), str(int(y)), "click", str(int(button))],
        )
        return {
            "ok": True,
            "profile": profile,
            "x": int(x),
            "y": int(y),
            "button": int(button),
        }

    def vnc_drag(
        self,
        profile: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        step: int = 5,
        button: int = 1,
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        self._run_vnc_command(
            p,
            [
                "move",
                str(int(start_x)),
                str(int(start_y)),
                "mousedown",
                str(int(button)),
                "drag",
                str(int(end_x)),
                str(int(end_y)),
                "mouseup",
                str(int(button)),
            ],
        )
        return {
            "ok": True,
            "profile": profile,
            "start": {"x": int(start_x), "y": int(start_y)},
            "end": {"x": int(end_x), "y": int(end_y)},
            "step": max(int(step), 1),
            "button": int(button),
        }

    def vnc_key(self, profile: str, key: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        if not key:
            raise BridgeError("vnc_key requires a non-empty key string")
        self._run_vnc_command(p, ["key", self._normalize_vnc_key(key)])
        return {"ok": True, "profile": profile, "key": key}

    def vnc_type(self, profile: str, text: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        self._run_vnc_command(
            p,
            ["type", text],
            timeout_s=max(int(p.vnc.connect_timeout_s) + max(len(text) // 8, 0) + 5, 15),
        )
        return {
            "ok": True,
            "profile": profile,
            "length": len(text),
        }

    def local_vm_info(self, profile: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        window = self._find_local_vm_window(p)
        payload = {
            "ok": True,
            "profile": profile,
            "app": self._local_vm_app_name(p),
            "vm_window_hint": Path(p.vm.path).name,
            **window,
        }
        cached = self._local_capture_meta.get(profile)
        if cached and cached.get("window_number") == window.get("window_number"):
            payload["capture"] = dict(cached)
        return payload

    def local_vm_capture_bytes(self, profile: str) -> bytes:
        p = self._get_profile(profile)
        window = self._find_local_vm_window(p)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            self._capture_local_window(window["window_number"], tmp_path)
            self._update_local_capture_meta(profile, window, tmp_path)
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

    def local_vm_capture_to_file(self, profile: str, output_path: str) -> dict[str, Any]:
        p = self._get_profile(profile)
        window = self._find_local_vm_window(p)
        dest = Path(output_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._capture_local_window(window["window_number"], dest)
        capture_meta = self._update_local_capture_meta(profile, window, dest)
        return {
            "ok": True,
            "profile": profile,
            "output_path": str(dest),
            "window_number": window["window_number"],
            "capture": capture_meta,
        }

    def local_vm_type(self, profile: str, text: str, *, submit: bool = False) -> dict[str, Any]:
        if not isinstance(text, str):
            raise BridgeError("local_vm_type requires a text string")
        p = self._get_profile(profile)
        self._assert_local_window_controls_allowed(p, action="local_vm_type")
        window = self._find_local_vm_window(p)
        self._run_local_vm_input(window["owner_pid"], "type", text, submit=submit)
        return {
            "ok": True,
            "profile": profile,
            "length": len(text),
            "submitted": bool(submit),
            "owner_pid": window["owner_pid"],
            "window_number": window["window_number"],
        }

    def local_vm_key(self, profile: str, key: str, *, repeat: int = 1) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip():
            raise BridgeError("local_vm_key requires a non-empty key string")
        p = self._get_profile(profile)
        self._assert_local_window_controls_allowed(p, action="local_vm_key")
        window = self._find_local_vm_window(p)
        self._run_local_vm_input(
            window["owner_pid"],
            "key",
            key.strip(),
            repeat=max(int(repeat), 1),
        )
        return {
            "ok": True,
            "profile": profile,
            "key": key.strip(),
            "repeat": max(int(repeat), 1),
            "owner_pid": window["owner_pid"],
            "window_number": window["window_number"],
        }

    def local_vm_click(
        self,
        profile: str,
        x: float,
        y: float,
        *,
        coordinate_space: str = "points",
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        self._assert_local_window_controls_allowed(p, action="local_vm_click")
        window = self._find_local_vm_window(p)
        bounds = window.get("bounds") or {}
        rel_x, rel_y, resolved_space, capture_meta = self._resolve_local_vm_click_coordinates(
            profile,
            window,
            x,
            y,
            coordinate_space=coordinate_space,
        )
        abs_x = int(bounds.get("x", 0)) + rel_x
        abs_y = int(bounds.get("y", 0)) + rel_y
        self._run_local_vm_input(window["owner_pid"], "click", str(abs_x), extra=str(abs_y))
        return {
            "ok": True,
            "profile": profile,
            "x": x,
            "y": y,
            "coordinate_space": resolved_space,
            "relative_x": rel_x,
            "relative_y": rel_y,
            "absolute_x": abs_x,
            "absolute_y": abs_y,
            "owner_pid": window["owner_pid"],
            "window_number": window["window_number"],
            "capture": capture_meta,
        }

    def _get_profile(self, profile: str | None) -> Profile:
        name = profile or self._default_profile
        if not name:
            raise BridgeError("No profile was supplied and no default_profile is configured")
        if name not in self._profiles:
            raise BridgeError(
                f"Unknown profile {name!r}. Known profiles: {', '.join(self.list_profiles())}"
            )
        return self._profiles[name]

    @contextmanager
    def _ssh_client(self, profile: Profile) -> Iterator[paramiko.SSHClient]:
        if not self._ssh_configured(profile):
            raise BridgeError(
                f"SSH is not configured for profile={profile.name}. "
                "Fill in ssh.host and ssh.user in VM_BRIDGE_CONFIG."
            )
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if profile.ssh.known_hosts_path:
            known_hosts_path = Path(profile.ssh.known_hosts_path).expanduser()
            if known_hosts_path.exists():
                client.load_host_keys(str(known_hosts_path))
        if profile.ssh.allow_unknown_hosts:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        connect_kwargs: dict[str, Any] = {
            "port": int(profile.ssh.port),
            "username": profile.ssh.user,
            "timeout": float(profile.ssh.connect_timeout_s),
            "banner_timeout": float(profile.ssh.banner_timeout_s),
            "auth_timeout": float(profile.ssh.auth_timeout_s),
        }

        password = _read_env(profile.ssh.password_env, required=False)
        if profile.ssh.key_path:
            connect_kwargs["key_filename"] = profile.ssh.key_path
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        elif password:
            connect_kwargs["password"] = password
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        else:
            connect_kwargs["look_for_keys"] = True
            connect_kwargs["allow_agent"] = True

        errors: list[str] = []
        try:
            for host_candidate in self._ssh_host_candidates(profile):
                try:
                    client.connect(hostname=host_candidate, **connect_kwargs)
                    yield client
                    return
                except Exception as exc:
                    errors.append(f"{host_candidate}: {exc}")
            raise BridgeError(
                f"SSH connection failed for profile={profile.name} host={profile.ssh.host}:{profile.ssh.port} "
                f"user={profile.ssh.user}: {'; '.join(errors) if errors else 'no connection candidates succeeded'}"
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _ssh_exec_raw(
        self,
        profile: Profile,
        command: str,
        *,
        timeout_s: int = 60,
        get_pty: bool = False,
        login_shell: bool = True,
        stdin_text: str | None = None,
    ) -> SSHExecResult:
        wrapped_command = (
            f"/bin/zsh -lc {shlex.quote(command)}" if login_shell else command
        )
        LOGGER.debug(
            "SSH exec profile=%s command=%s wrapped=%s",
            profile.name,
            command,
            wrapped_command,
        )
        with self._ssh_client(profile) as client:
            stdin, stdout, stderr = client.exec_command(
                wrapped_command,
                timeout=timeout_s,
                get_pty=get_pty,
            )
            try:
                stdout.channel.settimeout(timeout_s)
                stderr.channel.settimeout(timeout_s)
            except Exception:
                pass
            if stdin_text is not None:
                stdin.write(stdin_text)
                stdin.flush()
            try:
                stdin.channel.shutdown_write()
            except Exception:
                try:
                    stdin.close()
                except Exception:
                    pass

            stdout_text = stdout.read().decode("utf-8", errors="replace")
            stderr_text = stderr.read().decode("utf-8", errors="replace")
            exit_status = stdout.channel.recv_exit_status()

        return SSHExecResult(
            profile=profile.name,
            command=command,
            wrapped_command=wrapped_command,
            exit_status=exit_status,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    def _agent_request(
        self,
        profile: str,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_s: int = 15,
    ) -> dict[str, Any]:
        p = self._get_profile(profile)
        if not self._ssh_configured(p):
            raise BridgeError(
                f"Guest agent access requires SSH, but SSH is not configured for profile={profile}."
            )
        url = f"http://{p.agent.host}:{p.agent.port}{path}"
        curl_parts = [
            "/usr/bin/curl",
            "--silent",
            "--show-error",
            "--max-time",
            str(max(int(timeout_s), 1)),
            "--output",
            "-",
            "--write-out",
            shlex.quote(f"\n{HTTP_STATUS_MARKER}%{{http_code}}"),
        ]
        if method.upper() == "POST":
            curl_parts.extend(
                [
                    "-X",
                    "POST",
                    "-H",
                    shlex.quote("Content-Type: application/json"),
                    "--data-binary",
                    "@-",
                ]
            )
        elif method.upper() != "GET":
            raise BridgeError(f"Unsupported guest agent method: {method}")
        curl_parts.append(shlex.quote(url))
        command = " ".join(curl_parts)
        stdin_text = json.dumps(payload) if payload is not None else None
        result = self._ssh_exec_raw(
            p,
            command,
            timeout_s=max(timeout_s + 5, 10),
            stdin_text=stdin_text,
        )

        stdout_text = result.stdout
        if HTTP_STATUS_MARKER not in stdout_text:
            if result.exit_status != 0:
                raise BridgeError(
                    f"Guest agent curl failed for profile={profile}: exit_status={result.exit_status} stderr={result.stderr or stdout_text}"
                )
            raise BridgeError(
                f"Guest agent response did not include HTTP status marker for profile={profile}"
            )

        body_text, status_text = stdout_text.rsplit(HTTP_STATUS_MARKER, 1)
        body_text = body_text.rstrip("\n")
        http_status = int(status_text.strip())

        parsed_body: dict[str, Any] | str
        try:
            parsed_body = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            parsed_body = body_text

        if isinstance(parsed_body, dict):
            response: dict[str, Any] = dict(parsed_body)
            response.setdefault("ok", 200 <= http_status < 300)
        else:
            response = {"ok": 200 <= http_status < 300, "body": parsed_body}

        response.update(
            {
                "profile": profile,
                "http_status": http_status,
                "agent_url": url,
                "transport_exit_status": result.exit_status,
                "transport_stderr": result.stderr,
            }
        )
        return response

    def _run_vnc_command(
        self,
        profile: Profile,
        args: list[str],
        *,
        timeout_s: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if not self._vnc_configured(profile):
            raise BridgeError(
                f"VNC is not configured for profile={profile.name}. Fill in vnc.host first."
            )
        hostspec = f"{profile.vnc.host}::{profile.vnc.port}"
        password = _read_env(profile.vnc.password_env, required=bool(profile.vnc.password_env))
        username = _read_env(profile.vnc.username_env, required=False)
        if username is None:
            username = profile.vnc.username
        if username is None and password is not None:
            username = ""
        command = [
            sys.executable,
            "-m",
            "vncdotool.command",
            "-s",
            hostspec,
            "-t",
            str(max(int(timeout_s or profile.vnc.connect_timeout_s), 1)),
        ]
        if username is not None:
            command.extend(["-u", username])
        if password is not None:
            command.extend(["-p", password])
        command.extend(args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(int(timeout_s or profile.vnc.connect_timeout_s) + 5, 10),
                check=False,
            )
            if completed.returncode != 0:
                raise BridgeError(
                    f"VNC command failed for profile={profile.name} host={hostspec}: {' '.join(shlex.quote(part) for part in command)} stderr={completed.stderr.strip() or completed.stdout.strip() or f'exit_status={completed.returncode}'}"
                )
            return completed
        except Exception as exc:
            raise BridgeError(
                f"VNC connection failed for profile={profile.name} host={hostspec}: {exc}"
            ) from exc

    def _find_local_vm_window(self, profile: Profile) -> dict[str, Any]:
        app_name = self._local_vm_app_name(profile)
        window_hint = Path(profile.vm.path).name
        helper = self._ensure_local_vm_input_helper()
        result = subprocess.run(
            [str(helper), "window", app_name, window_hint],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise BridgeError(
                f"Failed to inspect local VM windows for profile={profile.name} app={app_name}: {result.stderr.strip() or result.stdout.strip() or f'exit_status={result.returncode}'}"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise BridgeError(
                f"Local VM window inspection returned invalid JSON for profile={profile.name}: {exc}"
            ) from exc
        matches = payload.get("matches")
        if not isinstance(matches, list) or not matches:
            raise BridgeError(
                f"No local {app_name} window matched profile={profile.name} vm={window_hint}. Open the VM first."
            )
        window = matches[0]
        if not isinstance(window, dict):
            raise BridgeError(f"Unexpected local VM window payload for profile={profile.name}")
        return window

    def _capture_local_window(self, window_number: int, output_path: Path) -> None:
        result = subprocess.run(
            [
                "/usr/sbin/screencapture",
                "-x",
                "-l",
                str(int(window_number)),
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise BridgeError(
                f"Local VM capture failed for window={window_number}: {result.stderr.strip() or result.stdout.strip() or f'exit_status={result.returncode}'}"
            )

    @staticmethod
    def _png_dimensions(path: Path) -> tuple[int, int]:
        with path.open("rb") as fh:
            header = fh.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            raise BridgeError(f"Local VM capture is not a PNG: {path}")
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)

    def _update_local_capture_meta(
        self,
        profile: str,
        window: dict[str, Any],
        capture_path: Path,
    ) -> dict[str, Any]:
        pixel_width, pixel_height = self._png_dimensions(capture_path)
        bounds = window.get("bounds") or {}
        width_points = max(int(bounds.get("width", 0)), 1)
        height_points = max(int(bounds.get("height", 0)), 1)
        capture_meta = {
            "window_number": int(window.get("window_number", 0)),
            "pixel_width": pixel_width,
            "pixel_height": pixel_height,
            "width_points": width_points,
            "height_points": height_points,
            "scale_x": round(pixel_width / width_points, 6),
            "scale_y": round(pixel_height / height_points, 6),
            "coordinate_spaces": ["points", "pixels", "normalized"],
        }
        self._local_capture_meta[profile] = capture_meta
        return dict(capture_meta)

    def _ensure_local_capture_meta(
        self,
        profile: str,
        window: dict[str, Any],
    ) -> dict[str, Any]:
        cached = self._local_capture_meta.get(profile)
        if cached and cached.get("window_number") == window.get("window_number"):
            return dict(cached)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            self._capture_local_window(int(window["window_number"]), tmp_path)
            return self._update_local_capture_meta(profile, window, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _resolve_local_vm_click_coordinates(
        self,
        profile: str,
        window: dict[str, Any],
        x: float,
        y: float,
        *,
        coordinate_space: str,
    ) -> tuple[int, int, str, dict[str, Any] | None]:
        bounds = window.get("bounds") or {}
        width_points = max(int(bounds.get("width", 0)), 1)
        height_points = max(int(bounds.get("height", 0)), 1)
        try:
            x_value = float(x)
            y_value = float(y)
        except (TypeError, ValueError) as exc:
            raise BridgeError("local VM click coordinates must be numeric") from exc

        space = (coordinate_space or "points").strip().lower()
        capture_meta: dict[str, Any] | None = None
        if space == "points":
            rel_x = int(round(x_value))
            rel_y = int(round(y_value))
        elif space == "normalized":
            rel_x = int(round(x_value * width_points))
            rel_y = int(round(y_value * height_points))
        elif space == "pixels":
            capture_meta = self._ensure_local_capture_meta(profile, window)
            scale_x = float(capture_meta["scale_x"])
            scale_y = float(capture_meta["scale_y"])
            rel_x = int(round(x_value / max(scale_x, 0.000001)))
            rel_y = int(round(y_value / max(scale_y, 0.000001)))
        else:
            raise BridgeError(
                "Unsupported local VM coordinate space. Use one of: points, pixels, normalized"
            )

        rel_x = max(0, min(rel_x, width_points - 1))
        rel_y = max(0, min(rel_y, height_points - 1))
        return rel_x, rel_y, space, capture_meta

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _resolve_host_addrs(host: str | None) -> dict[str, Any]:
        if not host:
            return {"host": host, "ok": False, "addresses": []}
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except Exception as exc:
            return {
                "host": host,
                "ok": False,
                "addresses": [],
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        addresses = sorted({info[4][0] for info in infos if info[4]})
        return {"host": host, "ok": bool(addresses), "addresses": addresses}

    def _ssh_host_candidates(self, profile: Profile) -> list[str]:
        host = profile.ssh.host
        if not host:
            return []
        resolved = self._resolve_host_addrs(host)
        addresses = list(resolved.get("addresses") or [])
        ipv4 = [addr for addr in addresses if ":" not in addr]
        ipv6 = [addr for addr in addresses if ":" in addr]
        ordered = [host, *ipv4, *ipv6]
        seen: set[str] = set()
        result: list[str] = []
        for candidate in ordered:
            if candidate and candidate not in seen:
                seen.add(candidate)
                result.append(candidate)
        return result

    def _wait_for_remote_ready(self, profile: str, *, timeout_s: int) -> dict[str, Any]:
        deadline = time.time() + max(int(timeout_s), 1)
        checks: list[dict[str, Any]] = []
        last_status: dict[str, Any] | None = None
        while time.time() < deadline:
            status = self.remote_status(profile)
            checks.append(status)
            last_status = status
            if status.get("ok"):
                return {
                    "ok": True,
                    "profile": profile,
                    "checks": checks,
                    "status": status,
                }
            time.sleep(2)
        return {
            "ok": False,
            "profile": profile,
            "checks": checks,
            "status": last_status,
            "error": f"Timed out after {timeout_s}s waiting for a prepared remote guest session",
        }

    @staticmethod
    def _assert_local_window_controls_allowed(profile: Profile, *, action: str) -> None:
        if profile.policy.allow_local_window_controls:
            return
        raise BridgeError(
            f"{action} is disabled for profile={profile.name} because policy.mode={profile.policy.mode!r} "
            "is configured for prepared guest remote-only control"
        )

    def _run_local_vm_input(
        self,
        pid: int,
        mode: str,
        payload: str,
        *,
        submit: bool = False,
        repeat: int = 1,
        extra: str | None = None,
    ) -> None:
        helper = self._ensure_local_vm_input_helper()
        command = [str(helper), mode, str(int(pid)), payload]
        if mode == "type":
            if submit:
                command.append("--submit")
        elif mode == "key":
            command.append(str(max(int(repeat), 1)))
        elif mode == "click":
            if extra is None:
                raise BridgeError("local VM click requires both x and y coordinates")
            command.append(extra)
        else:
            raise BridgeError(f"Unsupported local VM input mode: {mode}")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise BridgeError(
                f"Local VM input failed for pid={pid} mode={mode}: {result.stderr.strip() or result.stdout.strip() or f'exit_status={result.returncode}'}"
            )

    def _ensure_local_vm_input_helper(self) -> Path:
        helper_dir = Path(tempfile.gettempdir()) / "vm-codex-bridge"
        helper_dir.mkdir(parents=True, exist_ok=True)
        source_path = helper_dir / "local_vm_input.swift"
        binary_path = helper_dir / "local_vm_input"
        desired_source = LOCAL_VM_INPUT_SWIFT + "\n"
        if source_path.exists():
            current_source = source_path.read_text(encoding="utf-8")
        else:
            current_source = None
        needs_compile = current_source != desired_source or not binary_path.exists()
        if needs_compile:
            source_path.write_text(desired_source, encoding="utf-8")
            result = subprocess.run(
                ["/usr/bin/swiftc", str(source_path), "-o", str(binary_path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                raise BridgeError(
                    f"Failed to compile local VM input helper: {result.stderr.strip() or result.stdout.strip() or f'exit_status={result.returncode}'}"
                )
        return binary_path

    @staticmethod
    def _local_vm_app_name(profile: Profile) -> str:
        if profile.vm.app:
            return profile.vm.app
        if profile.vm.app_path:
            return Path(profile.vm.app_path).stem
        return "Vimy" if Path(profile.vm.path).suffix.lower() == ".vimi" else "Viable"

    @staticmethod
    def _sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
        if not remote_dir or remote_dir == "/":
            return
        path = ""
        for part in PurePosixPath(remote_dir).parts:
            if part == "/":
                path = "/"
                continue
            path = posixpath.join(path, part) if path else part
            try:
                sftp.stat(path)
            except FileNotFoundError:
                sftp.mkdir(path)
            except OSError:
                try:
                    sftp.stat(path)
                except Exception as exc:
                    raise BridgeError(f"Failed to create remote directory {path}: {exc}") from exc

    @staticmethod
    def _normalize_vnc_key(key: str) -> str:
        alias = {
            "cmd": "meta",
            "command": "meta",
            "super": "meta",
            "win": "meta",
            "ctrl": "ctrl",
            "control": "ctrl",
            "opt": "alt",
            "option": "alt",
            "return": "enter",
            "esc": "escape",
            "del": "delete",
            "bksp": "backspace",
        }
        parts = [part.strip().lower() for part in key.split("-") if part.strip()]
        if not parts:
            raise BridgeError("Invalid VNC key specification")
        mapped = [alias.get(part, part) for part in parts]
        return "-".join(mapped)

    @staticmethod
    def _expand_remote_path(profile: Profile, remote_path: str) -> str:
        if not profile.ssh.user:
            raise BridgeError(
                f"SSH user is not configured for profile={profile.name}; remote paths cannot be expanded."
            )
        if remote_path == "~":
            return f"/Users/{profile.ssh.user}"
        if remote_path.startswith("~/"):
            return f"/Users/{profile.ssh.user}/{remote_path[2:]}"
        return str(PurePosixPath(remote_path))

    @staticmethod
    def _ssh_configured(profile: Profile) -> bool:
        return bool(profile.ssh.host and profile.ssh.user)

    @staticmethod
    def _vnc_configured(profile: Profile) -> bool:
        return bool(profile.vnc.host)


def _parse_profile(name: str, raw: dict[str, Any], *, base_dir: Path) -> Profile:
    vm_raw = _ensure_obj(raw, "vm", profile=name)
    ssh_raw = raw.get("ssh", {})
    if not isinstance(ssh_raw, dict):
        raise BridgeError(f"Profile {name!r} field 'ssh' must be an object when present")
    vnc_raw = raw.get("vnc", {})
    if not isinstance(vnc_raw, dict):
        raise BridgeError(f"Profile {name!r} field 'vnc' must be an object when present")
    agent_raw = raw.get("agent", {})
    if not isinstance(agent_raw, dict):
        raise BridgeError(f"Profile {name!r} field 'agent' must be an object")
    policy_raw = raw.get("policy", {})
    if not isinstance(policy_raw, dict):
        raise BridgeError(f"Profile {name!r} field 'policy' must be an object when present")

    vm = VMConfig(
        path=_resolve_local_path(_require_str(vm_raw, "path", profile=name), base_dir=base_dir),
        app=_optional_str(vm_raw.get("app")),
        app_path=_optional_str(vm_raw.get("app_path")),
        open_args=_ensure_str_list(vm_raw.get("open_args", []), field_name="vm.open_args", profile=name),
    )
    ssh = SSHConfig(
        host=_optional_str(ssh_raw.get("host")),
        user=_optional_str(ssh_raw.get("user")),
        port=_optional_int(ssh_raw.get("port"), default=22),
        key_path=_resolve_optional_local_path(ssh_raw.get("key_path"), base_dir=base_dir),
        password_env=_optional_str(ssh_raw.get("password_env")),
        known_hosts_path=_resolve_optional_local_path(ssh_raw.get("known_hosts_path"), base_dir=base_dir),
        allow_unknown_hosts=bool(ssh_raw.get("allow_unknown_hosts", False)),
        connect_timeout_s=_optional_int(ssh_raw.get("connect_timeout_s"), default=10),
        banner_timeout_s=_optional_int(ssh_raw.get("banner_timeout_s"), default=10),
        auth_timeout_s=_optional_int(ssh_raw.get("auth_timeout_s"), default=15),
    )
    vnc = VNCConfig(
        host=_optional_str(vnc_raw.get("host")),
        port=_optional_int(vnc_raw.get("port"), default=5900),
        username=_optional_str(vnc_raw.get("username")),
        username_env=_optional_str(vnc_raw.get("username_env")),
        password_env=_optional_str(vnc_raw.get("password_env")),
        connect_timeout_s=_optional_int(vnc_raw.get("connect_timeout_s"), default=10),
    )
    agent = AgentConfig(
        host=_optional_str(agent_raw.get("host")) or "127.0.0.1",
        port=_optional_int(agent_raw.get("port"), default=8765),
        connect_timeout_s=_optional_int(agent_raw.get("connect_timeout_s"), default=10),
    )
    mode = (_optional_str(policy_raw.get("mode")) or "prepared_guest").strip().lower()
    if mode not in {"prepared_guest", "bootstrap"}:
        raise BridgeError(
            f"Profile {name!r} policy.mode must be 'prepared_guest' or 'bootstrap', got {mode!r}"
        )
    policy = PolicyConfig(
        mode=mode,
        allow_local_window_controls=bool(
            policy_raw.get("allow_local_window_controls", mode == "bootstrap")
        ),
        require_agent_ready=bool(policy_raw.get("require_agent_ready", mode == "prepared_guest")),
        require_vnc=bool(policy_raw.get("require_vnc", False)),
        startup_timeout_s=_optional_int(policy_raw.get("startup_timeout_s"), default=180),
        recovery_timeout_s=_optional_int(policy_raw.get("recovery_timeout_s"), default=240),
        restart_on_unready=bool(policy_raw.get("restart_on_unready", mode == "prepared_guest")),
        restart_grace_s=_optional_int(policy_raw.get("restart_grace_s"), default=12),
    )
    return Profile(name=name, vm=vm, ssh=ssh, vnc=vnc, agent=agent, policy=policy)


def load_bridge_from_env() -> Bridge:
    config_path = os.environ.get(DEFAULT_CONFIG_ENV, DEFAULT_CONFIG_PATH)
    return Bridge.from_file(config_path)


def _ensure_obj(raw: dict[str, Any], key: str, *, profile: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise BridgeError(f"Profile {profile!r} is missing required object field {key!r}")
    return value


def _require_str(raw: dict[str, Any], key: str, *, profile: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"Profile {profile!r} field {key!r} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BridgeError(f"Expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _optional_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise BridgeError("Boolean values are not valid integers in bridge config")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BridgeError(f"Expected integer-like value, got {value!r}") from exc


def _ensure_str_list(value: Any, *, field_name: str, profile: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BridgeError(f"Profile {profile!r} field {field_name!r} must be list[str]")
    return list(value)


def _resolve_local_path(value: str, *, base_dir: Path) -> str:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        expanded = (base_dir / expanded).resolve()
    return str(expanded)


def _resolve_optional_local_path(value: Any, *, base_dir: Path) -> str | None:
    str_value = _optional_str(value)
    if str_value is None:
        return None
    return _resolve_local_path(str_value, base_dir=base_dir)


def _read_env(name: str | None, *, required: bool) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None:
        if required:
            raise BridgeError(f"Required environment variable is not set: {name}")
        return None
    return value


def _env_present(name: str | None) -> bool | None:
    if not name:
        return None
    return name in os.environ
