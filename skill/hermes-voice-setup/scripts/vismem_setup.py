#!/usr/bin/env python3
"""Hermes Visual Memory: Hermes remembers what the Echo Show's camera saw (extras/visual-memory in the repo).

Installs the visual-memory service on this computer (its own Python environment, a background service
that starts at login, a settings file), and connects its tools to the voice profile. Needs Hermes Eye
(eye_setup.py) and the Codex CLI signed in (it does the image analysis on the user's ChatGPT plan).
Idempotent, every step can be previewed with --dry-run, and the profile is backed up before it changes.
Visual memory stays OFF after install: the user turns it on ("Hey Hermes, turn on visual memory").

Examples:
  vismem_setup.py --check                                   # what is installed, what is missing
  vismem_setup.py --profile voice --dry-run
  vismem_setup.py --profile voice                           # install, start the service, connect, test
  vismem_setup.py --uninstall --profile voice               # stop and remove it (memory is kept)
  vismem_setup.py --uninstall --profile voice --delete-data # ... and delete the memory and pictures
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import act, die, http, read_text, say, set_dry_run

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_SRC = os.path.join(SKILL_DIR, "assets", "visual-memory")
APP_DIR = os.path.expanduser("~/.local/share/hermes-voice/visual-memory-app")  # code + its own venv
DATA_DIR = os.path.expanduser("~/.local/share/hermes-visual-memory")  # memory and pictures
CONFIG = os.path.expanduser("~/.config/hermes-voice/visual-memory.toml")
EYE_TOKEN = os.path.expanduser("~/.config/hermes-voice/secrets/eye_token")
API = "http://127.0.0.1:28950"


def hermes(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["hermes", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"hermes {' '.join(args[:4])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def pick_python(override: str | None) -> str:
    """A Python >= 3.11 for the service. Default: the one Hermes itself runs on. On macOS a background
    service needs Local Network access to reach the Echo, and Hermes' Python usually has it already."""
    candidates = [override] if override else []
    hv = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python")
    if os.path.exists(hv):
        candidates.append(os.path.realpath(hv))
    candidates += [shutil.which(n) or "" for n in ("python3.13", "python3.12", "python3.11", "python3")]
    for py in candidates:
        if py and os.access(py, os.X_OK):
            ok = subprocess.run(
                [py, "-c", "import sys, venv; sys.exit(sys.version_info < (3, 11))"], capture_output=True
            )
            if ok.returncode == 0:
                return py
    die("no Python 3.11 or newer found; pass --python /path/to/python3.11")
    return ""


def codex() -> tuple[str | None, bool]:
    path = shutil.which("codex")
    if not path:
        return None, False
    logged_in = subprocess.run([path, "login", "status"], capture_output=True, text=True).returncode == 0
    return path, logged_in


def eye_url(profile: str | None, override: str | None) -> str | None:
    if override:
        return override.rstrip("/")
    if profile:
        out = hermes("-p", profile, "config", "get", "mcp_servers.eye", check=False)
        m = re.search(r"EYE_URL\W+(http://[^\s'\"}]+)", out)
        if m:
            return m.group(1).rstrip("/")
    return None


SERVICE_FILES = (
    os.path.expanduser("~/Library/LaunchAgents/org.hermesvoice.visual-memory.plist"),
    os.path.expanduser("~/.config/systemd/user/hermes-visual-memory.service"),
)


def service_installed(venv_py: str) -> bool:
    """A service that runs this install's Python (not an older copy somewhere else)."""
    return any(
        os.path.exists(f) and venv_py in open(f, encoding="utf-8", errors="replace").read()
        for f in SERVICE_FILES
    )


def service_running() -> bool:
    status, body = http("GET", API + "/health", timeout=3)
    return status == 200 and isinstance(body, dict) and bool(body.get("ok"))


def check(a: argparse.Namespace) -> None:
    py = pick_python(a.python)
    cx, logged_in = codex()
    say(f"Python for the service: {py}")
    say(
        f"Codex CLI: {cx or 'missing'}"
        + ("" if not cx else ", signed in" if logged_in else ", NOT signed in")
    )
    say(f"Hermes Eye token: {'present' if os.path.exists(EYE_TOKEN) else 'missing (run eye_setup.py first)'}")
    say(f"Camera URL (from profile {a.profile or '?'}): {eye_url(a.profile, a.eye_url) or 'unknown'}")
    say(f"Installed at {APP_DIR}: {'yes' if os.path.exists(os.path.join(APP_DIR, '.venv')) else 'no'}")
    say(f"Service: {'running' if service_running() else 'not running'}")
    if a.profile:
        on = "vismem" in hermes("-p", a.profile, "config", "get", "mcp_servers", check=False)
        say(f"Tools in profile {a.profile}: {'yes' if on else 'no'}")
    if not cx:
        say(
            "Codex CLI: install it (npm install -g @openai/codex) and run `codex login` (ask the user first)."
        )


def install_app(py: str) -> tuple[str, bool]:
    """Copy the code next to its own venv and install it. Returns the venv's python, and whether it changed."""
    venv_py = os.path.join(APP_DIR, ".venv", "bin", "python")
    same = os.path.exists(os.path.join(APP_DIR, "pyproject.toml")) and all(
        read_text(os.path.join(APP_SRC, rel)) == read_text(os.path.join(APP_DIR, rel))
        for rel in (
            "pyproject.toml",
            *(
                os.path.join("vismem", f)
                for f in os.listdir(os.path.join(APP_SRC, "vismem"))
                if f.endswith(".py")
            ),
        )
    )
    if not same and act(f"copy visual memory's code to {APP_DIR}"):
        os.makedirs(APP_DIR, exist_ok=True)
        for item in ("pyproject.toml", "config.example.toml", "README.md"):
            shutil.copy2(os.path.join(APP_SRC, item), os.path.join(APP_DIR, item))
        shutil.rmtree(os.path.join(APP_DIR, "vismem"), ignore_errors=True)
        shutil.copytree(
            os.path.join(APP_SRC, "vismem"),
            os.path.join(APP_DIR, "vismem"),
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    if not os.path.exists(venv_py) and act(f"create its Python environment with {py}"):
        subprocess.run([py, "-m", "venv", os.path.join(APP_DIR, ".venv")], check=True)
    if (not same or not _imports(venv_py)) and act(
        "install its Python packages: numpy, opencv-python-headless, mcp (downloads about 60 MB)"
    ):
        subprocess.run([venv_py, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
        subprocess.run([venv_py, "-m", "pip", "install", "-q", APP_DIR + "[mcp]"], check=True)
    return venv_py, not same


def _imports(py: str) -> bool:
    return (
        os.path.exists(py)
        and subprocess.run(
            [py, "-c", "import vismem, cv2, numpy, mcp.server.fastmcp"], capture_output=True
        ).returncode
        == 0
    )


def write_config(url: str, vision: dict[str, str]) -> None:
    lines = read_text(CONFIG).splitlines() if os.path.exists(CONFIG) else []
    want = {"eye_url": f'"{url}"', **{k: f'"{v}"' for k, v in vision.items()}}
    have = {
        ln.split("=", 1)[0].strip(): ln.split("=", 1)[1].strip()
        for ln in lines
        if "=" in ln and not ln.lstrip().startswith("#")
    }
    if all(have.get(k) == v for k, v in want.items()):
        return
    model = vision.get("local_model") or "Codex CLI"
    if act(f"write {CONFIG} (camera {url}, vision {model}; defaults for the rest, see config.example.toml)"):
        if os.path.exists(CONFIG):
            shutil.copy2(CONFIG, CONFIG + time.strftime(".%Y%m%d-%H%M%S.bak"))
        kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in want]
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w") as f:
            f.write("\n".join([*(f"{k} = {v}" for k, v in want.items()), *kept]) + "\n")


def local_model_ok(url: str, model: str) -> bool:
    """The server answers and lists the model (Ollama and LM Studio both serve /v1/models)."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=5) as r:
            ids = {m.get("id") for m in json.load(r).get("data", [])}
    except (OSError, ValueError):
        return False
    return model in ids


def connected(a: argparse.Namespace, venv_py: str) -> bool:
    current = hermes("-p", a.profile, "config", "get", "mcp_servers.vismem", check=False)
    return venv_py in current and "vismem.mcp_server" in current


def connect_profile(a: argparse.Namespace, venv_py: str) -> None:
    pdir = os.path.join(a.hermes_root, "profiles", a.profile)
    if not os.path.isdir(pdir):
        die(f"no profile {a.profile} in {a.hermes_root}/profiles")
    server = {"command": venv_py, "args": ["-m", "vismem.mcp_server"]}
    current = hermes("-p", a.profile, "config", "get", "mcp_servers.vismem", check=False)
    if venv_py in current and "vismem.mcp_server" in current:
        return
    if act(f"add the MCP server 'vismem' (visual memory tools) to profile {a.profile}"):
        backup_dir = os.path.join(pdir, "hermes-voice-backups", time.strftime("%Y%m%d-%H%M%S") + "-vismem")
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(os.path.join(pdir, "config.yaml"), backup_dir)
        hermes("-p", a.profile, "config", "set", "mcp_servers.vismem", json.dumps(server))
        say(f"   backup: {backup_dir}")
        if a.restart and act(f"restart the {a.profile} gateway"):
            restart(a.profile)


def restart(profile: str) -> None:
    """A failed restart is not a failed setup: say so, and how to do it by hand."""
    proc = subprocess.run(["hermes", "-p", profile, "gateway", "restart"], capture_output=True, text=True)
    if proc.returncode != 0:
        say(
            f"   the gateway didn't restart ({proc.stderr.strip().splitlines()[-1][:120] if proc.stderr.strip() else 'no reason given'});"
            f" run: hermes -p {profile} gateway restart"
        )


def uninstall(a: argparse.Namespace) -> None:
    venv_py = os.path.join(APP_DIR, ".venv", "bin", "python")
    if os.path.exists(venv_py) and act("stop and remove the visual memory service"):
        subprocess.run([venv_py, "-m", "vismem.cli", "uninstall-service"], check=False)
    if (
        a.profile
        and "vismem" in hermes("-p", a.profile, "config", "get", "mcp_servers", check=False)
        and act(f"remove the visual memory tools from profile {a.profile}")
    ):
        hermes("-p", a.profile, "mcp", "remove", "vismem", check=False)
        if a.restart and act(f"restart the {a.profile} gateway"):
            restart(a.profile)
    if os.path.isdir(APP_DIR) and act(f"delete {APP_DIR} (the code and its environment)"):
        shutil.rmtree(APP_DIR)
    if a.delete_data and os.path.isdir(DATA_DIR) and act(f"delete {DATA_DIR} (all memory and pictures)"):
        shutil.rmtree(DATA_DIR)
    elif os.path.isdir(DATA_DIR):
        say(f"Memory kept in {DATA_DIR} (rerun with --delete-data to delete it). Settings: {CONFIG}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help="the voice profile that gets the visual memory tools")
    p.add_argument(
        "--eye-url", help="Hermes Eye's address, http://<echo-ip>:28940 (default: from the profile)"
    )
    p.add_argument("--hermes-root", default=os.path.expanduser("~/.hermes"))
    p.add_argument("--python", help="Python 3.11+ for the service (default: the one Hermes runs on)")
    p.add_argument("--check", action="store_true", help="show what is installed and what is missing")
    p.add_argument("--no-restart", dest="restart", action="store_false", help="don't restart the gateway")
    p.add_argument("--uninstall", action="store_true", help="stop and remove the service and the tools")
    p.add_argument("--delete-data", action="store_true", help="with --uninstall: delete the memory too")
    p.add_argument(
        "--local-model",
        help="use a local vision model instead of Codex, e.g. qwen2.5vl:7b (pictures stay on this network)",
    )
    p.add_argument(
        "--local-url",
        default="http://127.0.0.1:11434/v1",
        help="its OpenAI-compatible server (Ollama: http://127.0.0.1:11434/v1, LM Studio: http://127.0.0.1:1234/v1)",
    )
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    set_dry_run(a.dry_run)

    if a.check:
        check(a)
        return
    if a.uninstall:
        uninstall(a)
        return
    if not a.profile:
        die("--profile is required")
    if a.profile == "default":
        die("refusing the default profile: use the dedicated voice profile")
    if a.local_model:
        vision = {"vision": "local", "local_url": a.local_url, "local_model": a.local_model}
        if not local_model_ok(a.local_url, a.local_model):
            die(
                f"{a.local_model} isn't available at {a.local_url}: start the server (Ollama or LM Studio) and "
                "load or pull a vision model first"
            )
    else:
        cx, logged_in = codex()
        vision = {"vision": "codex", "codex_bin": cx or ""}
    if not a.local_model and (not cx or not logged_in):
        die(
            "the Codex CLI is needed and must be signed in (`codex login`); it analyses the pictures on the "
            "user's ChatGPT plan. Install with: npm install -g @openai/codex (ask the user first)"
        )
    if not os.path.exists(EYE_TOKEN):
        die("Hermes Eye is not set up: run eye_setup.py --profile <profile> first")
    url = eye_url(a.profile, a.eye_url)
    if not url:
        die("could not find the camera's address in the profile: pass --eye-url http://<echo-ip>:28940")

    venv_py, updated = install_app(pick_python(a.python))
    write_config(url, vision)
    refresh = updated and service_installed(venv_py)  # new code: the running service must restart on it
    if (refresh or not (service_installed(venv_py) and service_running())) and act(
        "restart the visual memory service on the new code"
        if refresh
        else "start the visual memory service (and at every login)"
    ):
        subprocess.run([venv_py, "-m", "vismem.cli", "install-service"], check=True)
    was_connected = connected(a, venv_py)
    connect_profile(a, venv_py)
    if (
        updated
        and was_connected
        and a.restart
        and act(f"restart the {a.profile} gateway so its visual memory tools run the new code")
    ):
        restart(a.profile)
    if a.dry_run:
        return
    for _ in range(20):
        if service_running():
            break
        time.sleep(1)
    else:
        die("the service did not start; see ~/.local/share/hermes-visual-memory/logs/service.log")
    out = subprocess.run(
        [venv_py, "-m", "vismem.cli", "status"], capture_output=True, text=True
    ).stdout.strip()
    say(f"   {out}")
    say(
        'Done. Visual memory is OFF until the user turns it on: "Hey Hermes, turn on visual memory", then '
        '"what changed?" or "where\'s my …?". If the camera shows as offline on macOS, allow Local Network '
        "access for the service's Python (System Settings > Privacy & Security > Local Network)."
    )


if __name__ == "__main__":
    main()
