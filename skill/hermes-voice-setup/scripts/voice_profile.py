#!/usr/bin/env python3
"""Prepare ONE Hermes profile for voice and (optionally) start its API server.

Only ever reads or writes ~/.hermes/profiles/<profile>/ (plus `hermes -p <profile> ...` commands
scoped to it). Refuses the default profile. Idempotent: re-running changes nothing that is already
set. Every file is backed up before it is changed.

Examples:
  voice_profile.py --profile voice --create --dry-run
  voice_profile.py --profile voice --create --write-soul --start
  voice_profile.py --profile voice --service          # keep it running across restarts (login service)
  voice_profile.py --profile voice --start            # adopt an existing profile
  voice_profile.py --profile voice --tools safe       # web search only (default for new setups)
  voice_profile.py --profile voice --tools full       # code by voice: terminal, files, git, ...
  voice_profile.py --profile voice --tools web,memory,terminal,file
  voice_profile.py --profile voice --reasoning low    # answer sooner
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import read_text, act, die, http, read_env_file, say, set_dry_run  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOUL_TEMPLATE = os.path.join(SKILL_DIR, "references", "SOUL.voice.md")
HERMES_DEFAULT_SOUL_START = "You are Hermes Agent, built by Nous Research."

# What the voice assistant may do. Voice requests reach Hermes through its api_server platform,
# so these apply to that platform only; the profile's CLI and other platforms are left alone.
SAFE = ["web", "x_search", "memory", "todo"]
TOOL_PRESETS = {
    # answer questions and look things up; nothing that touches files, shells or other machines
    "safe": SAFE,
    # safe + controlling Home Assistant (lights, switches, ...)
    "home": SAFE + ["homeassistant"],
    # Hermes' own defaults: shell, files, code, browser, skills, cron, sub-agents. For people who
    # want to code by voice ("what's the diff", "open a PR"). Anyone who can talk to the speaker can use it.
    "full": SAFE + ["browser", "terminal", "file", "code_execution", "vision", "image_gen", "skills",
                    "connections", "delegation", "cronjob"],
}


def platform_toolsets(name: str, platform: str) -> dict[str, bool]:
    """Built-in toolsets of a platform and whether each is enabled, from `hermes tools list`."""
    out = hermes("-p", name, "tools", "list", "--platform", platform)
    state: dict[str, bool] = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[0] in ("✓", "✗") and parts[1] in ("enabled", "disabled"):
            state[parts[2]] = parts[1] == "enabled"
        elif ln.strip().startswith("Plugin toolsets"):
            break
    return state


def resolve_tools(spec: str) -> list[str]:
    if spec in TOOL_PRESETS:
        return TOOL_PRESETS[spec]
    names = [t.strip() for t in spec.split(",") if t.strip()]
    if not names:
        die("--tools needs a preset (" + ", ".join(TOOL_PRESETS) + ") or a comma-separated list of toolsets")
    return names


def hermes(*args: str, check: bool = True) -> str:
    """Run the hermes CLI and return stdout."""
    proc = subprocess.run(["hermes", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"hermes {' '.join(args[:3])} ... failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def backup(path: str, backup_dir: str) -> None:
    if os.path.exists(path):
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))


def set_env(env_path: str, values: dict[str, str], backup_dir: str) -> list[str]:
    """Set KEY=VALUE lines in the profile .env; returns the keys that changed (values never printed)."""
    current = read_env_file(env_path)
    changed = [k for k, v in values.items() if current.get(k) != v]
    if not changed:
        return []
    if not act(f"set {', '.join(changed)} in {env_path}"):
        return changed
    backup(env_path, backup_dir)
    lines = read_text(env_path).splitlines() if os.path.exists(env_path) else []
    lines = [ln for ln in lines if ln.split("=", 1)[0].strip() not in changed]
    lines.append("# --- hermes-voice ---")
    lines += [f"{k}={values[k]}" for k in changed]
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return changed


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", required=True, help="voice profile name (never 'default')")
    p.add_argument("--create", action="store_true", help="create the profile if it does not exist")
    p.add_argument("--clone-from", help="with --create: copy config/.env/SOUL/skills from this profile "
                   "(hermes profile create --clone-from). Copies its memory and MCP servers too.")
    p.add_argument("--write-soul", action="store_true", help="install the voice SOUL.md template "
                   "(only if SOUL.md is missing or still the template; use --force-soul to overwrite)")
    p.add_argument("--force-soul", action="store_true")
    p.add_argument("--port", type=int, default=8650, help="API server port for a standalone gateway")
    p.add_argument("--host", default="127.0.0.1", help="API server bind address")
    p.add_argument("--multiplexed", action="store_true",
                   help="do not mark the profile standalone; it is served by the host gateway at /p/<profile>/v1")
    p.add_argument("--keep-session-search", action="store_true", help="leave the session_search toolset enabled")
    p.add_argument("--tools", help="what the voice assistant may do: a preset (" + ", ".join(
                   f"{k}: {'+'.join(v) if k != 'full' else 'Hermes defaults incl. terminal/files'}"
                   for k, v in TOOL_PRESETS.items()) + ") or a comma-separated toolset list, e.g. "
                   "web,memory,terminal,file. Exactly these are enabled for voice; the rest are disabled. "
                   "Omit to leave the current tools unchanged")
    p.add_argument("--reasoning", help="model thinking effort for this profile (agent.reasoning_effort), e.g. "
                   "low, medium, high. Lower answers sooner; 'low' is recommended for voice. Omit to leave as is")
    p.add_argument("--tools-platform", default="api_server", help="Hermes platform the voice requests use")
    p.add_argument("--start", action="store_true", help="start the standalone gateway if it is not running "
                   "(until logout or restart; see --service)")
    p.add_argument("--service", action="store_true", help="install the standalone gateway as a login service "
                   "(launchd/systemd via `hermes gateway install`): starts now, at login and after a crash")
    p.add_argument("--hermes-root", default=os.path.expanduser("~/.hermes"))
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    set_dry_run(a.dry_run)

    name = a.profile.strip()
    if name in ("", "default") or "/" in name or name.startswith("."):
        die("refusing: pick a dedicated voice profile name (not 'default')")
    if shutil.which("hermes") is None:
        die("hermes CLI not found on PATH")
    pdir = os.path.join(a.hermes_root, "profiles", name)
    backup_dir = os.path.join(pdir, "hermes-voice-backups", time.strftime("%Y%m%d-%H%M%S"))

    # 1. profile
    created = False
    if not os.path.isdir(pdir):
        if not a.create:
            die(f"profile '{name}' does not exist; pass --create")
        cmd = ["profile", "create", name, "--no-alias", "--description", "Voice assistant profile (hermes-voice)"]
        if a.clone_from:
            if a.clone_from == name:
                die("--clone-from must differ from --profile")
            cmd += ["--clone-from", a.clone_from]
        if act(f"run: hermes {' '.join(cmd)}"):
            hermes(*cmd)
        created = True
    else:
        say(f"profile '{name}' exists ({pdir})")
    exists = os.path.isdir(pdir)

    # 2. SOUL.md
    soul = os.path.join(pdir, "SOUL.md")
    template = read_text(SOUL_TEMPLATE)
    current_soul = read_text(soul) if os.path.exists(soul) else ""
    # `hermes profile create` writes its own generic SOUL.md; that one is not "custom content".
    hermes_default = created or current_soul.startswith(HERMES_DEFAULT_SOUL_START)
    if a.write_soul and current_soul != template:
        if current_soul and not hermes_default and not a.force_soul:
            say("SOUL.md already has custom content; keeping it (use --force-soul to replace)")
        elif act(f"write voice SOUL.md template to {soul}"):
            backup(soul, backup_dir)
            with open(soul, "w") as f:
                f.write(template)

    # 3. API server env (key generated once, never printed)
    env_path = os.path.join(pdir, ".env")
    env = read_env_file(env_path)
    key = env.get("API_SERVER_KEY") or ""
    want = {"API_SERVER_ENABLED": "true",
            "API_SERVER_KEY": key if len(key) >= 16 else "hv-" + secrets.token_urlsafe(32)}
    if not a.multiplexed:
        want.update({"API_SERVER_PORT": str(a.port), "API_SERVER_HOST": a.host})
    changed_env = set_env(env_path, want, backup_dir) if exists or not a.dry_run else list(want)

    # 4. config: standalone + no session_search
    def cfg_get(key_: str) -> str:
        return hermes("-p", name, "config", "get", key_, check=False).strip() if exists else ""

    changed_cfg = []
    if not a.multiplexed and cfg_get("gateway.standalone").splitlines()[:1] != ["true"]:
        changed_cfg.append("gateway.standalone")
        if act(f"set gateway.standalone true for '{name}'"):
            backup(os.path.join(pdir, "config.yaml"), backup_dir)
            hermes("-p", name, "config", "set", "gateway.standalone", "true")
    if not a.keep_session_search:
        disabled = cfg_get("agent.disabled_toolsets")
        if "session_search" not in disabled:
            current = [ln.strip("- ").strip() for ln in disabled.splitlines()
                       if ln.strip().startswith("-") and ln.strip("- ").strip()]
            changed_cfg.append("agent.disabled_toolsets")
            if act(f"add session_search to agent.disabled_toolsets for '{name}'"):
                backup(os.path.join(pdir, "config.yaml"), backup_dir)
                hermes("-p", name, "config", "set", "agent.disabled_toolsets",
                       json.dumps(sorted(set(current) | {"session_search"})))

    # 4b. which tools voice may use
    voice_tools = None
    if a.tools:
        wanted = resolve_tools(a.tools)
        if exists:
            state = platform_toolsets(name, a.tools_platform)
            unknown = [t for t in wanted if t not in state]
            if unknown:
                die(f"unknown toolset(s) {', '.join(unknown)}; known: {', '.join(sorted(state))}")
            enable = [t for t in wanted if not state[t]]
            disable = [t for t, on in state.items() if on and t not in wanted]
            if enable or disable:
                changed_cfg.append(f"platform_toolsets.{a.tools_platform}")
                if act(f"voice tools for '{name}': enable {enable or '-'}, disable {disable or '-'}"):
                    backup(os.path.join(pdir, "config.yaml"), backup_dir)
                    if enable:
                        hermes("-p", name, "tools", "enable", "--platform", a.tools_platform, *enable)
                    if disable:
                        hermes("-p", name, "tools", "disable", "--platform", a.tools_platform, *disable)
            else:
                say(f"voice tools already: {', '.join(sorted(wanted))}")
        else:
            changed_cfg.append(f"platform_toolsets.{a.tools_platform}")
            act(f"voice tools for '{name}': only {', '.join(wanted)} (all other toolsets disabled for voice)")
        voice_tools = sorted(wanted)

    # 4c. thinking effort
    if a.reasoning:
        effort = a.reasoning.strip().lower()
        current_effort = cfg_get("agent.reasoning_effort").splitlines()[:1]
        if current_effort == [effort]:
            say(f"reasoning effort already {effort}")
        else:
            changed_cfg.append("agent.reasoning_effort")
            if act(f"set agent.reasoning_effort {effort} for '{name}'"):
                backup(os.path.join(pdir, "config.yaml"), backup_dir)
                hermes("-p", name, "config", "set", "agent.reasoning_effort", effort)

    # 5. gateway
    base_url = f"http://{a.host}:{a.port}/v1" if not a.multiplexed else f"http://127.0.0.1:8642/p/{name}/v1"
    health_url = base_url.rsplit("/v1", 1)[0].replace("/p/" + name, "") + "/health"
    port_busy = http("GET", health_url, timeout=3)[0] == 200
    status_out = hermes("-p", name, "gateway", "status", check=False) if exists else ""
    own_status = status_out.split("Other profiles:")[0]  # the rest describes other profiles' gateways
    # a gateway started by hand says "running"; one installed as a service says "supervised by launchd/systemd"
    running = port_busy and ("Gateway is running" in own_status or "Gateway is supervised" in own_status)
    if port_busy and not running and not a.multiplexed:
        die(f"port {a.port} already serves another gateway; pick a free --port for '{name}'")
    if running and (changed_env or changed_cfg) and not a.dry_run:
        say("note: gateway is running; restart it for the changes to apply "
            f"(hermes -p {name} gateway stop && hermes -p {name} gateway start)")
    if a.service and not a.multiplexed:
        installed_service = "supervised by" in own_status or "Service definition matches" in own_status
        if installed_service:
            say(f"gateway service for '{name}' already installed")
        elif act(f"install login service: hermes -p {name} gateway install --start-now --start-on-login"):
            if running:  # a hand-started gateway holds the port; the service replaces it
                hermes("-p", name, "gateway", "stop", check=False)
                running = False
            hermes("-p", name, "gateway", "install", "--start-now", "--start-on-login")
            for _ in range(60):
                time.sleep(1)
                if http("GET", health_url, timeout=2)[0] == 200:
                    running = True
                    break
            if not running:
                die(f"gateway service did not come up; see `hermes -p {name} gateway status`")
    if a.start and not a.service and not running and not a.multiplexed:
        log = os.path.join(pdir, "logs", "hermes-voice-gateway.log")
        if act(f"start: hermes -p {name} gateway run (log {log})"):
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "a") as lf:
                subprocess.Popen(["hermes", "-p", name, "gateway", "run"], stdout=lf, stderr=lf,
                                 start_new_session=True)
            for _ in range(60):
                time.sleep(1)
                if http("GET", health_url, timeout=2)[0] == 200:
                    running = True
                    break
            if not running:
                die(f"gateway did not come up; see {log}")

    say(json.dumps({"profile": name, "base_url": base_url, "env_file": env_path,
                    "api_key": "stored in env_file as API_SERVER_KEY (not printed)",
                    "gateway_running": running, "voice_tools": voice_tools, "changed_env": changed_env, "changed_config": changed_cfg,
                    "backups": backup_dir if os.path.isdir(backup_dir) else None,
                    "dry_run": a.dry_run}, indent=2))


if __name__ == "__main__":
    main()
