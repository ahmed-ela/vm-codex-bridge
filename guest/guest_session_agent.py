#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pwd
import shlex
import subprocess
import sys
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LISTEN_HOST = os.environ.get("VM_CODEX_GUEST_AGENT_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("VM_CODEX_GUEST_AGENT_PORT", "8765"))
DESKTOPCTL_PATH = os.environ.get("DESKTOPCTL_PATH")
DESKTOPCTL_PYTHON = os.environ.get("DESKTOPCTL_PYTHON", sys.executable)
DEFAULT_TIMEOUT_S = int(os.environ.get("VM_CODEX_GUEST_AGENT_DEFAULT_TIMEOUT_S", "90"))
READY_PROBE_ARGV = json.loads(
    os.environ.get(
        "VM_CODEX_GUEST_AGENT_READY_PROBE_ARGV",
        '["app", "state", "--json"]',
    )
)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _run_desktopctl(argv: list[str], timeout_s: int, parse_json: bool) -> dict[str, Any]:
    if not DESKTOPCTL_PATH:
        raise RuntimeError(
            "DESKTOPCTL_PATH is not set. Export it in the LaunchAgent plist or set it before starting the guest agent."
        )

    desktopctl = str(Path(DESKTOPCTL_PATH).expanduser().resolve())
    cmd = [DESKTOPCTL_PYTHON, desktopctl, *argv]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    payload: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "cmd": cmd,
        "exit_status": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if parse_json:
        try:
            payload["json"] = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload["json_error"] = "stdout was not valid JSON"
    return payload


def _desktopctl_ready_payload() -> dict[str, Any]:
    probe = _run_desktopctl(
        list(READY_PROBE_ARGV),
        timeout_s=min(DEFAULT_TIMEOUT_S, 20),
        parse_json=True,
    )
    probe_json = probe.get("json") if isinstance(probe.get("json"), dict) else {}
    trusted = probe_json.get("trusted")
    ready = bool(probe.get("ok")) and trusted is not False
    return {
        "ok": ready,
        "probe_argv": list(READY_PROBE_ARGV),
        "probe": probe,
        "trusted": trusted,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "vm-codex-guest-agent/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(format % args, file=sys.stderr)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            payload = {
                "ok": True,
                "user": pwd.getpwuid(os.getuid()).pw_name,
                "uid": os.getuid(),
                "pid": os.getpid(),
                "desktopctl_path": DESKTOPCTL_PATH,
                "desktopctl_python": DESKTOPCTL_PYTHON,
                "listen_host": LISTEN_HOST,
                "listen_port": LISTEN_PORT,
            }
            _json_response(self, 200, payload)
            return

        if self.path == "/ready":
            try:
                probe_payload = _desktopctl_ready_payload()
                payload = {
                    "ok": probe_payload["ok"],
                    "user": pwd.getpwuid(os.getuid()).pw_name,
                    "uid": os.getuid(),
                    "pid": os.getpid(),
                    "desktopctl_path": DESKTOPCTL_PATH,
                    "desktopctl_python": DESKTOPCTL_PYTHON,
                    "listen_host": LISTEN_HOST,
                    "listen_port": LISTEN_PORT,
                    "ready": probe_payload,
                }
                _json_response(self, 200 if payload["ok"] else 503, payload)
                return
            except subprocess.TimeoutExpired:
                _json_response(
                    self,
                    504,
                    {"ok": False, "error": "desktopctl readiness probe timed out"},
                )
                return
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return

        _json_response(self, 404, {"ok": False, "error": f"Unknown path: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length)
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"ok": False, "error": f"Invalid JSON: {exc}"})
            return

        if self.path == "/health":
            payload = {
                "ok": True,
                "echo": data,
                "user": pwd.getpwuid(os.getuid()).pw_name,
                "pid": os.getpid(),
            }
            _json_response(self, 200, payload)
            return

        if self.path == "/desktopctl":
            argv = data.get("argv")
            if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
                _json_response(
                    self,
                    400,
                    {"ok": False, "error": "Body must contain argv: list[str]"},
                )
                return

            timeout_s = int(data.get("timeout_s", DEFAULT_TIMEOUT_S))
            parse_json = bool(data.get("parse_json", True))

            try:
                payload = _run_desktopctl(argv, timeout_s=timeout_s, parse_json=parse_json)
                status = 200 if payload.get("ok") else 500
                _json_response(self, status, payload)
                return
            except subprocess.TimeoutExpired:
                _json_response(
                    self,
                    504,
                    {"ok": False, "error": f"desktopctl timed out after {timeout_s}s"},
                )
                return
            except Exception as exc:  # pragma: no cover - runtime environment dependent
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return

        _json_response(self, 404, {"ok": False, "error": f"Unknown path: {self.path}"})


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(
        f"vm-codex-guest-agent listening on http://{LISTEN_HOST}:{LISTEN_PORT}",
        file=sys.stderr,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
