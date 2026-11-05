#!/usr/bin/env python3
"""Find or start Home Assistant, install the hermes_voice integration and point it at a Hermes profile.

Idempotent: an existing HA, user, token, integration copy or config entry is reused. Supports --dry-run.
Credentials it creates (HA admin password, long-lived token) are written 0600 into --secrets-dir and
never printed. The Hermes API key is read from the profile's .env and only sent to Home Assistant.

Examples:
  ha_setup.py --profile voice --docker --config-dir ~/hermes-voice/ha-config --dry-run
  ha_setup.py --profile voice --docker --config-dir ~/hermes-voice/ha-config --set-default
  ha_setup.py --profile voice --token-file ~/ha.token --hermes-url http://192.168.1.10:8650/v1
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import read_text, act, die, ha_ws, http, read_env_file, say, set_dry_run, write_secret  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The skill carries a copy of the integration (assets/); a full repo checkout also has it at the top level.
DEFAULT_COMPONENT = next(
    (
        p
        for p in (
            os.path.join(SKILL_DIR, "assets", "hermes_voice"),
            os.path.normpath(os.path.join(SKILL_DIR, "..", "..", "custom_components", "hermes_voice")),
        )
        if os.path.isdir(p)
    ),
    os.path.join(SKILL_DIR, "assets", "hermes_voice"),
)
DOMAIN = "hermes_voice"


def local_health_url(base_url: str, env: dict[str, str]) -> str:
    """Where this machine can check the gateway: host.docker.internal means this machine."""
    root = base_url.rstrip("/").rsplit("/v1", 1)[0]
    if "host.docker.internal" in root:
        root = f"http://127.0.0.1:{env.get('API_SERVER_PORT', '8642')}"
    return root.split("/p/")[0] + "/health"


def wait_gateway(url: str, seconds: int = 60) -> bool:
    for _ in range(seconds // 2):
        if http("GET", url, timeout=3)[0] == 200:
            return True
        time.sleep(2)
    return False


def wait_ha(url: str, seconds: int = 180) -> bool:
    for _ in range(seconds // 2):
        status, _ = http("GET", url + "/api/", timeout=3)
        if status in (200, 401):
            return True
        time.sleep(2)
    return False


def get_list(url: str, token: str, tries: int = 30) -> list:
    """GET a JSON list, retrying while HA is still starting (it answers with text/5xx then)."""
    for _ in range(tries):
        status, body = http("GET", url, token=token)
        if status == 200 and isinstance(body, list):
            return body
        time.sleep(2)
    die(f"Home Assistant did not return a list from {url}")
    return []


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"docker {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc


def local_time_zone() -> str | None:
    """This computer's IANA time zone (TZ, /etc/timezone or the /etc/localtime link), or None."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz and not tz.startswith("/"):
        return tz
    try:
        tz = read_text("/etc/timezone").strip()
        if "/" in tz:
            return tz
    except OSError:
        pass
    link = os.path.realpath("/etc/localtime")
    return link.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in link else None


def onboard(ha: str, secrets_dir: str, time_zone: str | None = None) -> str | None:
    """Create the first user on a fresh HA and return a long-lived token (or None in dry-run)."""
    client_id = ha + "/"
    if not act("onboard fresh Home Assistant (admin user 'hermes-voice', password saved to secrets dir)"):
        return None
    password = secrets.token_urlsafe(24)
    status, r = http("POST", ha + "/api/onboarding/users", body={
        "client_id": client_id, "name": "Hermes Voice Admin", "username": "hermes-voice",
        "password": password, "language": "en"})
    if status != 200:
        die(f"onboarding failed (HTTP {status})")
    write_secret(os.path.join(secrets_dir, "ha_admin_password"), password)
    status, tok = http("POST", ha + "/auth/token", form={
        "grant_type": "authorization_code", "code": r["auth_code"], "client_id": client_id})
    access = tok["access_token"]
    for step, body in (("core_config", {}), ("analytics", {}),
                       ("integration", {"client_id": client_id, "redirect_uri": client_id + "?auth_callback=1"})):
        http("POST", f"{ha}/api/onboarding/{step}", token=access, body=body)
    ws = ha_ws(ha, access)
    # Onboarding leaves a new HA on UTC: its answers to "what time is it?" (and the time hermes_voice tells
    # Hermes) would be off by hours.
    if time_zone:
        res = ws.call("config/core/update", time_zone=time_zone)
        say(f"   time zone {time_zone}" if res.get("success") else f"   could not set time zone {time_zone}: "
            f"{res.get('error')}; set it in Settings → System → General")
    res = ws.call("auth/long_lived_access_token", client_name="hermes-voice", lifespan=3650)
    ws.close()
    if not res.get("success"):
        die("could not create a long-lived token")
    token = res["result"]
    write_secret(os.path.join(secrets_dir, "ha_token"), token)
    say(f"   long-lived token saved to {os.path.join(secrets_dir, 'ha_token')}")
    return token


def relogin(ha: str, secrets_dir: str) -> str | None:
    """Mint a new long-lived token with the admin password this script created earlier."""
    pw_file = os.path.join(secrets_dir, "ha_admin_password")
    if not os.path.exists(pw_file):
        return None
    if not act("log in with the stored admin password and create a new long-lived token"):
        return None
    client_id = ha + "/"
    _, flow = http("POST", ha + "/auth/login_flow", body={
        "client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id})
    if not isinstance(flow, dict) or "flow_id" not in flow:
        return None
    _, res = http("POST", f"{ha}/auth/login_flow/{flow['flow_id']}", body={
        "username": "hermes-voice", "password": read_text(pw_file), "client_id": client_id})
    if not isinstance(res, dict) or res.get("type") != "create_entry":
        return None
    _, tok = http("POST", ha + "/auth/token", form={
        "grant_type": "authorization_code", "code": res["result"], "client_id": client_id})
    ws = ha_ws(ha, tok["access_token"])
    out = ws.call("auth/long_lived_access_token", client_name="hermes-voice", lifespan=3650)
    ws.close()
    if not out.get("success"):
        return None
    write_secret(os.path.join(secrets_dir, "ha_token"), out["result"])
    return str(out["result"])


def component_differs(src: str, dst: str) -> bool:
    if not os.path.isdir(dst):
        return True
    cmp = filecmp.dircmp(src, dst, ignore=["__pycache__"])

    def walk(c: filecmp.dircmp) -> bool:
        if c.left_only or c.diff_files or c.funny_files:
            return True
        return any(walk(sub) for sub in c.subdirs.values())

    return walk(cmp)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help="voice profile; its .env supplies the API key")
    p.add_argument("--hermes-env", help="explicit path to the profile .env (instead of --profile)")
    p.add_argument("--hermes-url", help="Hermes base URL as seen FROM Home Assistant "
                   "(default http://host.docker.internal:<API_SERVER_PORT>/v1)")
    p.add_argument("--name", help="display name in Home Assistant (default: profile name)")
    p.add_argument("--ha-url", default="http://localhost:8123")
    p.add_argument("--token-file", help="existing HA long-lived token (else: secrets-dir/ha_token or onboarding)")
    p.add_argument("--secrets-dir", default=os.path.expanduser("~/.config/hermes-voice/secrets"))
    p.add_argument("--docker", action="store_true", help="start an HA container if none answers at --ha-url")
    p.add_argument("--container", default="homeassistant")
    p.add_argument("--image", default="ghcr.io/home-assistant/home-assistant:stable")
    p.add_argument("--config-dir", help="HA config directory (needed to copy the integration / create a container)")
    p.add_argument("--component-src", default=DEFAULT_COMPONENT)
    p.add_argument("--session-mode", choices=["per_conversation", "per_device"])
    p.add_argument("--idle-reset-minutes", type=float)
    p.add_argument("--set-default", action="store_true", help="make it the conversation agent of the preferred Assist pipeline "
                   "and turn on 'Prefer handling commands locally' there (time, timers, device control)")
    p.add_argument("--time-zone", help="time zone for a Home Assistant this script onboards, e.g. "
                   "America/Chicago (default: this computer's)")
    p.add_argument("--no-local-commands", action="store_true", help="with --set-default: leave 'Prefer handling "
                   "commands locally' off, so every sentence goes to Hermes")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    set_dry_run(a.dry_run)
    ha = a.ha_url.rstrip("/")
    port = int(ha.rsplit(":", 1)[1]) if ha.count(":") == 2 else 8123

    # Hermes side
    env_path = a.hermes_env or (os.path.expanduser(f"~/.hermes/profiles/{a.profile}/.env") if a.profile else None)
    if not env_path:
        die("pass --profile or --hermes-env")
    if a.profile == "default":
        die("refusing to wire the default profile; use a dedicated voice profile")
    env = read_env_file(env_path)
    api_key = env.get("API_SERVER_KEY", "")
    if len(api_key) < 16:
        die(f"no usable API_SERVER_KEY in {env_path}; run voice_profile.py first")
    hermes_url = a.hermes_url or f"http://host.docker.internal:{env.get('API_SERVER_PORT', '8642')}/v1"
    name = a.name or a.profile or "Hermes"

    # 1. Home Assistant reachable?
    if wait_ha(ha, 4):
        say(f"Home Assistant answers at {ha}")
    elif a.docker:
        if not a.config_dir:
            die("--docker needs --config-dir")
        existing = docker("ps", "-a", "--filter", f"name=^{a.container}$", "--format", "{{.Names}}", check=False).stdout.strip()
        if existing:
            if act(f"start existing container {a.container}"):
                docker("start", a.container)
        elif act(f"run container {a.container} ({a.image}, port {port}, config {a.config_dir})"):
            os.makedirs(a.config_dir, exist_ok=True)
            docker("run", "-d", "--name", a.container, "--restart", "unless-stopped", "-p", f"{port}:8123",
                   "-v", f"{os.path.abspath(a.config_dir)}:/config", a.image)
        if not a.dry_run and not wait_ha(ha):
            die("Home Assistant did not come up")
    else:
        die(f"no Home Assistant at {ha}; pass --docker --config-dir DIR to start one")

    # 2. token
    token = None
    for path in filter(None, [a.token_file, os.path.join(a.secrets_dir, "ha_token")]):
        if os.path.exists(path):
            token = read_text(path)
            break
    status, steps = http("GET", ha + "/api/onboarding")
    fresh = status == 200 and isinstance(steps, list) and any(s["step"] == "user" and not s["done"] for s in steps)
    if token is None and fresh:
        token = onboard(ha, a.secrets_dir, a.time_zone or local_time_zone())
    elif token is None and not a.dry_run:
        die("HA is already onboarded: create a long-lived token (Profile > Security) and pass --token-file")
    if token and http("GET", ha + "/api/", token=token)[0] != 200:
        token = relogin(ha, a.secrets_dir) if not a.token_file else None
        if not token or http("GET", ha + "/api/", token=token)[0] != 200:
            die("Home Assistant rejected the token")

    # 3. integration files
    status, handlers = http("GET", ha + "/api/config/config_entries/flow_handlers", token=token) if token else (0, [])
    installed = isinstance(handlers, list) and DOMAIN in handlers
    if a.config_dir:
        dst = os.path.join(a.config_dir, "custom_components", DOMAIN)
        if not os.path.isdir(a.component_src):
            die(f"integration source not found at {a.component_src} (pass --component-src)")
        if component_differs(a.component_src, dst) and act(f"copy integration to {dst}"):
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(a.component_src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            installed = False  # needs restart to load new code
    elif not installed:
        die("hermes_voice is not installed in this HA: install it via HACS (custom repository) or pass --config-dir")
    if not installed and token and act("restart Home Assistant to load hermes_voice"):
        # HA writes its auth store on a delay; restarting before that loses a just-created token.
        auth_file = os.path.join(a.config_dir or "", ".storage", "auth")
        for _ in range(20):
            if a.config_dir and os.path.exists(auth_file) and "long_lived_access_token" in read_text(auth_file):
                break
            if not a.config_dir:
                time.sleep(15)
                break
            time.sleep(2)
        # Restart the authenticated HA instance, including when it runs in Docker.
        status, _ = http("POST", ha + "/api/services/homeassistant/restart", token=token, body={}, timeout=60)
        # HA may close the connection while restarting.
        if status not in (0, 200):
            die(f"Home Assistant rejected the restart request (HTTP {status})")
        time.sleep(10)
        if not wait_ha(ha):
            die("Home Assistant did not come back after restart")
        for _ in range(90):
            handlers = http("GET", ha + "/api/config/config_entries/flow_handlers", token=token)[1]
            if isinstance(handlers, list) and DOMAIN in handlers:
                break
            time.sleep(2)
        else:
            die("hermes_voice did not load after the restart; check the HA log for errors")

    if not token:
        say("[dry-run] stopping before config entry (no token yet)")
        return

    # 4. config entry (one per gateway URL; the flow itself aborts on duplicates)
    entries = [e for e in get_list(ha + "/api/config/config_entries/entry", token) if e["domain"] == DOMAIN]
    norm = hermes_url.rstrip("/") if hermes_url.rstrip("/").endswith("/v1") else hermes_url.rstrip("/") + "/v1"
    entry = None
    for e in entries:  # the entry list has no unique_id; diagnostics carry base_url (key redacted)
        _, diag = http("GET", f"{ha}/api/diagnostics/config_entry/{e['entry_id']}", token=token)
        if isinstance(diag, dict) and (diag.get("data") or {}).get("data", {}).get("base_url") == norm:
            entry = e
            break
    if entry:
        say(f"hermes_voice entry for {norm} already exists ({entry['title']})")
    elif act(f"create hermes_voice entry '{name}' -> {norm}"):
        # The integration checks the gateway when the entry is created, so wait for it to be up.
        health = local_health_url(norm, env)
        if not wait_gateway(health):
            die(f"the Hermes gateway does not answer at {health}; start it (voice_profile.py --profile "
                f"{a.profile or '<name>'} --service) and run this again")
        _, f = http("POST", ha + "/api/config/config_entries/flow", token=token, body={"handler": DOMAIN})
        if not isinstance(f, dict) or "flow_id" not in f:
            die(f"Home Assistant did not start the hermes_voice setup: {f}")
        status, r = http("POST", f"{ha}/api/config/config_entries/flow/{f['flow_id']}", token=token,
                         body={"base_url": hermes_url, "api_key": api_key, "display_name": name})
        if not isinstance(r, dict) or r.get("type") != "create_entry":
            http("DELETE", f"{ha}/api/config/config_entries/flow/{f['flow_id']}", token=token)  # don't leave it open
            why = (r.get("errors") or r.get("reason")) if isinstance(r, dict) else r
            hint = {"cannot_connect": f"Home Assistant cannot reach the gateway at {hermes_url}; from HA in Docker "
                                      "use host.docker.internal or this machine's LAN IP (--hermes-url)",
                    "invalid_auth": "the gateway rejected API_SERVER_KEY; restart the gateway after changing .env",
                    "already_configured": "an entry for this URL already exists"}
            key = next(iter(why.values())) if isinstance(why, dict) and why else why
            die(f"hermes_voice setup failed: {why}. {hint.get(str(key), '')}".strip())
        entry = r["result"]

    # 5. options
    if entry and (a.session_mode or a.idle_reset_minutes is not None):
        f = http("POST", ha + "/api/config/config_entries/options/flow", token=token, body={"handler": entry["entry_id"]})[1]
        cur = {d["name"]: d.get("description", {}).get("suggested_value") for d in f["data_schema"]}
        new = dict(cur)
        if a.session_mode:
            new["session_mode"] = a.session_mode
        if a.idle_reset_minutes is not None:
            new["idle_reset_minutes"] = a.idle_reset_minutes
        if new != cur and act(f"set options {({k: v for k, v in new.items() if cur.get(k) != v})}"):
            http("POST", f"{ha}/api/config/config_entries/options/flow/{f['flow_id']}", token=token, body=new)
        else:
            http("DELETE", f"{ha}/api/config/config_entries/options/flow/{f['flow_id']}", token=token)

    # 6. agent id + Assist pipeline
    agent_id = None
    if entry:
        ws = ha_ws(ha, token)
        for _ in range(15):
            reg = ws.call("config/entity_registry/list")["result"]
            agent_id = next((e["entity_id"] for e in reg if e.get("config_entry_id") == entry["entry_id"]
                             and e["entity_id"].startswith("conversation.")), None)
            if agent_id:
                break
            time.sleep(2)
        if a.set_default and agent_id:
            pl = ws.call("assist_pipeline/pipeline/list")["result"]
            pref = next(x for x in pl["pipelines"] if x["id"] == pl["preferred_pipeline"])
            # Home Assistant answers its own sentences first (time, timers, lights), Hermes everything else.
            local = not a.no_local_commands
            if pref.get("conversation_engine") == agent_id and bool(pref.get("prefer_local_intents")) == local:
                say(f"Assist pipeline '{pref['name']}' already uses {agent_id}"
                    f" (prefer handling commands locally: {'on' if local else 'off'})")
            elif act(f"set Assist pipeline '{pref['name']}' conversation agent to {agent_id}, "
                     f"prefer handling commands locally: {'on' if local else 'off'}"):
                upd = {k: v for k, v in pref.items() if k != "id"}
                upd["conversation_engine"] = agent_id
                upd["conversation_language"] = upd.get("conversation_language") or "*"
                upd["prefer_local_intents"] = local
                res = ws.call("assist_pipeline/pipeline/update", pipeline_id=pref["id"], **upd)
                if not res.get("success"):
                    die(f"pipeline update failed: {res.get('error')}")
        ws.close()

    say(json.dumps({"ha_url": ha, "hermes_url": norm, "entry_id": entry and entry.get("entry_id"),
                    "agent_id": agent_id, "token_file": a.token_file or os.path.join(a.secrets_dir, "ha_token"),
                    "dry_run": a.dry_run}, indent=2))


if __name__ == "__main__":
    main()
