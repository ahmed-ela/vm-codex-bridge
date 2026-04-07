# vm-codex-bridge

A host-side MCP bridge that lets Codex running on your **macOS host** control a **macOS guest VM** launched by **Viable/Vimy** without fighting for the host keyboard, mouse, or focus.

The intended steady-state architecture is **prepared guest, remote-only control**:

- the host uses Vimy or Viable only to open, restart, and capture proof of the VM window
- the guest is expected to auto-login into a persistent desktop session
- SSH is the primary control plane
- the guest session agent plus `desktopctl` are the semantic GUI control plane
- optional guest-side VNC can exist, but it is not the normal path
- host-window typing and clicking are bootstrap tools, not recurring operations

## What this includes

- `host/vm_bridge_server.py` — FastMCP stdio server exposed to Codex
- `host/vm_bridge_core.py` — VM, SSH, SFTP, guest agent, and VNC primitives
- `host/smoke_test.py` — direct CLI smoke test without Codex
- `scripts/check-bridge.sh` — validates profiles and reports whether local bootstrap and remote services are configured
- `guest/guest_session_agent.py` — localhost-only HTTP service inside the guest session
- `guest/guest_install_agent.sh` — installs and starts the guest LaunchAgent
- `guest/guest_prepare_prepared_guest.sh` — applies keep-awake and anti-lock defaults for the automation user
- `guest/guest_audit_prepared_guest.sh` — reports whether the guest still meets the prepared-guest contract
- `vm_profiles.example.json` — example bridge profile file
- `codex_config.example.toml` — example Codex MCP config
- `requirements.txt` — Python dependencies for the host bridge

## Architecture

```text
host macOS
  Codex CLI / IDE
    -> MCP stdio server (host/vm_bridge_server.py)
      -> open Vimy / Viable to launch the VM
      -> capture and inspect the local VM window without stealing host focus
      -> SSH / SFTP into the guest
      -> SSH-local curl into guest session agent on 127.0.0.1:8765
      -> guest-side semantic control through desktopctl
      -> optional guest-side VNC only if you explicitly enable it

guest macOS VM
  logged-in desktop session
    -> LaunchAgent runs guest_session_agent.py as the logged-in user
    -> guest_session_agent.py spawns desktopctl inside that GUI session
    -> Remote Login (SSH)
    -> Remote Management + VNC password
```

## Why the guest session agent exists

Shell commands over SSH are good for files, processes, and CLI work. GUI automation on macOS is much more reliable when the automator runs in the **logged-in user session**. The guest agent solves that by listening only on `127.0.0.1` inside the guest and spawning `desktopctl` locally in that session.

The host never talks to the agent over the network directly. It reaches it indirectly by running `curl http://127.0.0.1:8765/...` **inside the guest over SSH**.

## Operating modes

Profiles can declare a `policy` block.

- `prepared_guest`: the robust default. Local host-window input is disabled. The bridge expects SSH and the guest agent to come back on every boot. If they do not, recovery means reopening or restarting the VM, not typing into the host VM window.
- `bootstrap`: one-time image preparation mode. Local host-window controls are allowed so you can complete login, approvals, or initial setup before turning the VM into a prepared guest.

For recurring Codex work, prefer `prepared_guest`.

## Prerequisites

### Host

- Apple silicon Mac
- Codex CLI installed
- Python 3.10+
- Homebrew
- `viable` and `vimy` installed if you want the exact VM stack discussed here

### Guest VM

- macOS guest configured and bootable
- a logged-in desktop session that stays active while automation runs
- auto-login enabled for a dedicated automation user if you want a no-compromise unattended flow
- **Remote Login** enabled
- the guest session agent installed as a per-user LaunchAgent
- one-time **Accessibility** and **Automation** approvals for the binary that launches `desktopctl`
- optional: **Remote Management** enabled with **VNC viewers may control screen with password**
- `desktopctl` installed in the guest if you want semantic-first GUI automation

If the VM still requires host-window typing, clicking, unlocking, or permission handling on ordinary runs, it is not prepared yet.

## Quick setup

### 1) Install Viable and Vimy on the host

```bash
brew install --cask viable
brew install --cask vimy
```

### 2) Build or configure the VM in Viable

Create the VM in Viable, then save it as either:

- a `.bundle` opened by **Viable**
- a `.vimi` launcher opened by **Vimy**

If available, prefer **bridged networking** so the guest gets its own IP.

### 3) In the guest, enable remote access

Enable **Remote Login**:

```bash
sudo systemsetup -setremotelogin on
```

Enable **Remote Management** in System Settings, then in **Computer Settings** enable:

- `VNC viewers may control screen with password`

### 4) Install the guest session agent and harden the image

Copy `guest/guest_session_agent.py`, `guest/guest_install_agent.sh`, `guest/guest_prepare_prepared_guest.sh`, and `guest/guest_audit_prepared_guest.sh` into the guest, then run:

```bash
chmod +x guest_install_agent.sh guest_prepare_prepared_guest.sh guest_audit_prepared_guest.sh
./guest_install_agent.sh /absolute/path/to/desktopctl.py
./guest_prepare_prepared_guest.sh --json
```

That installs a per-user LaunchAgent in `~/Library/LaunchAgents/`, starts it for the current logged-in user, installs a keep-awake LaunchAgent, and disables the ordinary screensaver lock path for that automation user.

If `guest_prepare_prepared_guest.sh` reports pending `sudo` commands, run those once in the guest as well. The robust target is:

- Remote Login enabled
- guest power settings set not to sleep on their own
- no automatic drift back to the lock screen during normal unattended runs
- a dedicated automation account that auto-logs in or a VM lifecycle that resumes from a logged-in saved state

### 5) On the host, create a Python environment

```bash
cd /path/to/vm-codex-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 6) Create your bridge config

```bash
mkdir -p ~/.vm-codex-bridge
cp vm_profiles.example.json ~/.vm-codex-bridge/profiles.json
```

Then edit `~/.vm-codex-bridge/profiles.json` and fill in:

- `vm.path`
- `ssh.host`, `ssh.user`, `ssh.key_path`
- `vnc.host`
- optional `vnc.username` or `vnc.username_env` if your guest offers Apple Remote Desktop style VNC auth
- the VNC password environment variable name

Prefer the guest's Bonjour hostname such as `my-vm.local` over a raw DHCP IP for `ssh.host` and `vnc.host` when the VM advertises one.

For robust recurring use, also add a `policy` block like:

```json
"policy": {
  "mode": "prepared_guest",
  "allow_local_window_controls": false,
  "require_agent_ready": true,
  "require_vnc": false,
  "startup_timeout_s": 180,
  "recovery_timeout_s": 240,
  "restart_on_unready": true,
  "restart_grace_s": 12
}
```

You can also start in local-only bootstrap mode with just:

- `vm.path`
- optional `vm.app`

In that mode, `vm_open`, `local_vm_info`, `local_vm_capture`, `local_vm_click`, `local_vm_type`, and `local_vm_key` work immediately, while SSH/VNC and guest-agent tools stay unavailable until you fill in the remote sections.

### 7) Export the VNC password

```bash
export VM_SEQUOIA_LAB_VNC_PASSWORD='your-vnc-password'
export VM_SONOMA_LAB_VNC_PASSWORD='your-vnc-password'
```

### 8) Run a smoke test before adding Codex

```bash
export VM_BRIDGE_CONFIG="$HOME/.vm-codex-bridge/profiles.json"
./scripts/check-bridge.sh
python host/smoke_test.py --profile sequoia-lab --open-vm --capture /tmp/sequoia-lab.png
```

Bootstrap-only proof capture without waiting on SSH, VNC, or the guest agent:

```bash
python host/smoke_test.py --profile sequoia-lab --local-only --local-capture /tmp/sequoia-local.png
```

Optional semantic check:

```bash
python host/smoke_test.py \
  --profile sequoia-lab \
  --desktopctl browser snapshot --json
```

### 9) Add the MCP server to Codex

With the CLI:

```bash
codex mcp add vmBridge \
  --env VM_BRIDGE_CONFIG=$HOME/.vm-codex-bridge/profiles.json \
  -- /absolute/path/to/vm-codex-bridge/.venv/bin/python \
     /absolute/path/to/vm-codex-bridge/host/vm_bridge_server.py
```

Or copy the example from `codex_config.example.toml` into `~/.codex/config.toml`.

## Profile file format

The bridge reads JSON from `VM_BRIDGE_CONFIG`, defaulting to:

```text
~/.vm-codex-bridge/profiles.json
```

The expected shape is:

```json
{
  "default_profile": "sequoia-lab",
  "profiles": {
    "sequoia-lab": {
      "vm": {
        "path": "/Users/you/VMs/sequoia-lab.vimi",
        "app": "Vimy",
        "open_args": []
      },
      "ssh": {
        "host": "sequoia-lab.local",
        "port": 22,
        "user": "automation",
        "key_path": "~/.ssh/id_ed25519",
        "known_hosts_path": "~/.ssh/known_hosts",
        "allow_unknown_hosts": false,
        "connect_timeout_s": 10,
        "banner_timeout_s": 10,
        "auth_timeout_s": 15
      },
      "vnc": {
        "host": "sequoia-lab.local",
        "port": 5900,
        "username": "",
        "password_env": "VM_SEQUOIA_LAB_VNC_PASSWORD",
        "connect_timeout_s": 10
      },
      "agent": {
        "host": "127.0.0.1",
        "port": 8765,
        "connect_timeout_s": 10
      },
      "policy": {
        "mode": "prepared_guest",
        "allow_local_window_controls": false,
        "require_agent_ready": true,
        "require_vnc": false,
        "startup_timeout_s": 180,
        "recovery_timeout_s": 240,
        "restart_on_unready": true,
        "restart_grace_s": 12
      }
    }
  }
}
```

Notes:

- `vm.app` can be omitted. The bridge defaults to `Vimy` for `.vimi` and `Viable` for everything else.
- `vm.app_path` is also supported if you want to point at a specific `.app` bundle.
- Prefer the guest's Bonjour hostname such as `my-vm.local` over a raw DHCP IP for `ssh.host` and `vnc.host` when the VM advertises one.
- `ssh` and `vnc` are optional when you only need local bootstrap tools first.
- `ssh.password_env` is optional if you use key-based auth or `ssh-agent`.
- `vnc.username` and `vnc.username_env` are optional. Some macOS guests require a username to complete ARD-style VNC authentication even when you set a VNC password.
- `vnc.password_env` should point to a host environment variable so the password does not live in JSON.
- Remote paths passed to `sftp_put` and `sftp_get` may be absolute or begin with `~/`.
- `local_vm_*` tools target the host-side Vimy or Viable window by app name plus VM window title. They are meant for bootstrap and login screens before SSH, VNC, or the guest agent is ready.
- `local_vm_click` accepts `points`, capture `pixels`, or `normalized` coordinates. Use `pixels` when you are clicking from a `local_vm_capture` screenshot so Retina scaling does not drift the click target.
- In `prepared_guest` mode, local host-window controls are intentionally disabled. Use them only in `bootstrap` mode when preparing an image.

## MCP tools exposed to Codex

- `list_profiles`
- `profile_info`
- `vm_open`
- `local_vm_info`
- `local_vm_capture`
- `local_vm_capture_to_file`
- `local_vm_click`
- `local_vm_type`
- `local_vm_key`
- `remote_status`
- `vm_restart`
- `recover_prepared_guest`
- `wait_for_services`
- `ssh_exec`
- `sftp_put`
- `sftp_get`
- `guest_agent_health`
- `guest_agent_ready`
- `repair_guest_agent`
- `stage_guest_support`
- `audit_prepared_guest`
- `prepare_prepared_guest`
- `guest_desktopctl`
- `vnc_capture`
- `vnc_capture_to_file`
- `vnc_move`
- `vnc_click`
- `vnc_drag`
- `vnc_key`
- `vnc_type`

## Expected working pattern in Codex

1. `vm_open(profile="sequoia-lab")`
2. `remote_status(profile="sequoia-lab")`
3. `recover_prepared_guest(profile="sequoia-lab")` if SSH or agent readiness did not return
4. `wait_for_services(profile="sequoia-lab", require_vnc=false, require_agent=true)`
5. `audit_prepared_guest(profile="sequoia-lab")` when validating or maintaining the image
6. `guest_desktopctl(profile="sequoia-lab", argv=["browser", "snapshot", "--json"])`
7. Use `guest_desktopctl` for semantic actions inside the guest

## Security notes

- The guest session agent binds only to `127.0.0.1` inside the guest.
- Prefer SSH keys over SSH passwords.
- Keep the VNC password out of JSON config.
- Use the VM for isolated browser/desktop work and keep sensitive host secrets out of the guest when possible.
- If you enable VNC, use it as a guest-side option, not as the primary control plane.

## Limitations

- This does **not** remove CPU / RAM contention between host and guest.
- Raw VNC control remains less robust than semantic automation.
- Host-window input into an unfocused Vimy or Viable guest cannot be treated as a fully reliable automation channel. That is why `prepared_guest` mode disables it.
- A prepared guest still has to be prepared correctly. If the VM falls back to login or loses SSH whenever it idles, fix the guest image with `guest_prepare_prepared_guest.sh`, a dedicated automation account, and either auto-login or saved-state resume.
- `local_vm_type` assumes the guest already has a focused text target, such as the macOS login password field or a selected text field inside the guest.
- The first run inside the guest still requires macOS privacy approvals.
- `vnc_key` maps `cmd` to `meta`; if your guest VNC server expects a different modifier mapping, adjust the alias table in `host/vm_bridge_core.py`.

## Example prompts for Codex

- “Open the `sequoia-lab` VM, recover the prepared guest if needed, and show me the active browser tab list from the guest.”
- “Report whether `sequoia-lab` is remotely ready without using host-window input.”
- “Audit whether `sequoia-lab` is still a valid prepared guest and tell me what is missing.”
- “Stage the guest support bundle, reinstall the guest agent, and apply prepared-guest defaults in `sequoia-lab`.”
- “In `sequoia-lab`, run `desktopctl route run upload_receipt --json`.”
