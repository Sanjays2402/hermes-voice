#!/usr/bin/env python3
"""Read-only helper for the Hermes screen: what Hermes is working on (docs/show-screen.md, step 4).

Home Assistant cannot run `hermes` commands (it usually runs in Docker) and Hermes' API has no endpoint for
its task board, so this small server runs next to Hermes and answers one request:

    GET /activity?scope=profile|all     (Authorization: Bearer <the voice profile's API_SERVER_KEY>)

with the tasks on Hermes' kanban board that are running, that need attention (blocked, waiting for review)
and that finished recently. It only runs `hermes kanban ... --json` (the supported, read-only commands) and
never changes anything. `scope=profile` (the default in hermes_voice) keeps to tasks assigned to this
profile; `scope=all` shows every profile's tasks, for people who opt in.

It listens on this computer only (127.0.0.1) unless told otherwise, and accepts only the profile's API key,
which only Home Assistant holds.

Examples:
  screen_activity.py --profile voice --check                 # print what the screen would get, then exit
  screen_activity.py --profile voice                         # run in the foreground
  screen_activity.py --profile voice --install [--dry-run]   # login service (launchd on macOS, systemd on Linux)
  screen_activity.py --profile voice --uninstall
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import act, die, read_env_file, say, set_dry_run

DEFAULT_PORT = 8660
RECENT_SECONDS = 3 * 3600  # "just finished" stays this long
CACHE_SECONDS = 15
MAX_ITEMS = 6
SCOPES = ("profile", "all")
ATTENTION = ("blocked", "review")
# Fields the screen gets. Not the body, result or paths: titles are enough for a screen in a shared room.
TASK_FIELDS = ("id", "title", "assignee", "status", "created_at", "started_at", "completed_at")
SERVICE_LABEL = "ai.hermes-voice.screen-activity"


class Kanban:
    """Runs `hermes kanban` and turns its JSON into what the screen shows."""

    def __init__(self, hermes: str, profile: str, timeout: float = 20) -> None:
        self.hermes = hermes
        self.profile = profile
        self.timeout = timeout
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._refreshing: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def run(self, *args: str) -> Any:
        proc = subprocess.run(
            [self.hermes, "kanban", *args, "--json"], capture_output=True, text=True, timeout=self.timeout
        )
        if proc.returncode != 0:
            raise RuntimeError(f"hermes kanban {' '.join(args[:3])} failed: {proc.stderr.strip()[:200]}")
        return json.loads(proc.stdout or "null")

    def boards(self) -> list[str]:
        rows = self.run("boards", "list") or []
        return [b["slug"] for b in rows if isinstance(b, dict) and b.get("slug") and not b.get("archived")]

    def tasks(self, board: str, status: str, scope: str, sort: str | None = None) -> list[dict[str, Any]]:
        args = ["--board", board, "list", "--status", status]
        if scope == "profile":
            args += ["--assignee", self.profile]
        if sort:
            args += ["--sort", sort]
        rows = self.run(*args) or []
        return [{**{k: t.get(k) for k in TASK_FIELDS}, "board": board} for t in rows if isinstance(t, dict)]

    def activity(self, scope: str, now: float | None = None) -> dict[str, Any]:
        """The activity for a scope. Fresh from the cache; stale from the cache while a refresh runs in the
        background (each `hermes kanban` call takes about a second, so a screen never waits for one);
        computed on the spot only the first time."""
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
        cached = self._cache.get(scope)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        if cached:
            self._refresh_in_background(scope, now)
            return cached[1]
        return self._compute(scope, now)

    def _refresh_in_background(self, scope: str, now: float | None) -> None:
        with self._lock:
            if self._refreshing.get(scope):
                return
            self._refreshing[scope] = thread = threading.Thread(
                target=self._compute, args=(scope, now), kwargs={"background": True}, daemon=True
            )
        thread.start()

    def _compute(self, scope: str, now: float | None, background: bool = False) -> dict[str, Any]:
        try:
            now = time.time() if now is None else now
            boards = self.boards()
            queries = [(b, s) for b in boards for s in ("running", *ATTENTION, "done")]
            # In parallel: about one `hermes` start-up in all instead of one per query.
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(
                    pool.map(
                        lambda q: (
                            q[1],
                            self.tasks(q[0], q[1], scope, "completed-desc" if q[1] == "done" else None),
                        ),
                        queries,
                    )
                )
            running = [t for s, rows in results if s == "running" for t in rows]
            attention = [t for s, rows in results if s in ATTENTION for t in rows]
            recent = [
                t
                for s, rows in results
                if s == "done"
                for t in rows
                if (t.get("completed_at") or 0) >= now - RECENT_SECONDS
            ]
            running.sort(key=lambda t: t.get("started_at") or 0, reverse=True)
            recent.sort(key=lambda t: t.get("completed_at") or 0, reverse=True)
            out = {
                "scope": scope,
                "profile": self.profile,
                "boards": len(boards),
                "running": running[:MAX_ITEMS],
                "attention": attention[:MAX_ITEMS],
                "recent": recent[:MAX_ITEMS],
                "generated_at": int(now),
            }
            self._cache[scope] = (time.monotonic(), out)
            return out
        except Exception as err:
            if not background:
                raise
            sys.stderr.write(f"refresh failed ({scope}): {err}\n")
            return self._cache[scope][1]
        finally:
            if background:
                with self._lock:
                    self._refreshing.pop(scope, None)


def make_handler(kanban: Kanban, key: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "hermes-voice-screen-activity"

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/health":
                return self._send(200, {"ok": True})
            if url.path != "/activity":
                return self._send(404, {"error": "not found"})
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {key}".encode()):
                return self._send(401, {"error": "unauthorized"})
            scope = (parse_qs(url.query).get("scope") or ["profile"])[0]
            if scope not in SCOPES:
                return self._send(400, {"error": f"scope must be one of {', '.join(SCOPES)}"})
            try:
                return self._send(200, kanban.activity(scope))
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as err:
                return self._send(502, {"error": str(err)[:300]})

        def log_message(self, fmt: str, *args: Any) -> None:
            if not str(args[1] if len(args) > 1 else "").startswith("2"):  # log failures only
                sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")

    return Handler


# ---------------------------------------------------------------------------------------------- service --


def service_paths(profile: str) -> tuple[str, str]:
    if sys.platform == "darwin":
        return "launchd", os.path.expanduser(f"~/Library/LaunchAgents/{SERVICE_LABEL}.{profile}.plist")
    return "systemd", os.path.expanduser(
        f"~/.config/systemd/user/hermes-voice-screen-activity-{profile}.service"
    )


def install(a: argparse.Namespace, hermes: str) -> None:
    kind, path = service_paths(a.profile)
    log = os.path.join(a.hermes_root, "profiles", a.profile, "logs", "screen-activity.log")
    argv = [
        sys.executable,
        os.path.abspath(__file__),
        "--profile",
        a.profile,
        "--port",
        str(a.port),
        "--host",
        a.host,
        "--hermes",
        hermes,
        "--hermes-root",
        a.hermes_root,
    ]
    if kind == "launchd":
        args_xml = "".join(f"\n      <string>{x}</string>" for x in argv)
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>{SERVICE_LABEL}.{a.profile}</string>
    <key>ProgramArguments</key>
    <array>{args_xml}
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
  </dict>
</plist>
"""
    else:
        content = f"""[Unit]
Description=Hermes Voice screen activity helper ({a.profile})
After=network.target

[Service]
ExecStart={" ".join(argv)}
Restart=always
RestartSec=5
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""
    if os.path.exists(path):
        with open(path) as f:
            if f.read() == content:
                say(f"service already installed: {path}")
                return
    if not act(f"install login service {path} (read-only helper on {a.host}:{a.port})"):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    if kind == "launchd":
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", path], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", path], check=True)
    else:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", os.path.basename(path)], check=True)
    say(f"   started; log: {log}")


def uninstall(a: argparse.Namespace) -> None:
    kind, path = service_paths(a.profile)
    if not os.path.exists(path):
        say("service not installed")
        return
    if not act(f"remove login service {path}"):
        return
    if kind == "launchd":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", path], capture_output=True)
    else:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", os.path.basename(path)], capture_output=True
        )
    os.remove(path)
    if kind == "systemd":
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", required=True, help="the voice profile (its API key guards the helper)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default="127.0.0.1", help="listen address (default: this computer only)")
    p.add_argument("--hermes", default=shutil.which("hermes") or "hermes", help="path to the hermes command")
    p.add_argument("--hermes-root", default=os.path.expanduser("~/.hermes"))
    p.add_argument("--check", action="store_true", help="print the activity for --scope and exit")
    p.add_argument("--scope", choices=SCOPES, default="profile")
    p.add_argument("--install", action="store_true", help="install and start as a login service")
    p.add_argument("--uninstall", action="store_true", help="stop and remove the login service")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    set_dry_run(a.dry_run)

    if a.uninstall:
        return uninstall(a)
    hermes = a.hermes if os.path.isabs(a.hermes) else (shutil.which(a.hermes) or a.hermes)
    if not shutil.which(hermes) and not os.path.isfile(hermes):
        die("hermes command not found; pass --hermes /path/to/hermes")
    key = read_env_file(os.path.join(a.hermes_root, "profiles", a.profile, ".env")).get("API_SERVER_KEY", "")
    if not key and not a.check:
        die(f"profile '{a.profile}' has no API_SERVER_KEY; set it up with voice_profile.py first")
    if a.install:
        return install(a, hermes)
    kanban = Kanban(hermes, a.profile)
    if a.check:
        say(json.dumps(kanban.activity(a.scope), indent=2))
        return
    server = ThreadingHTTPServer((a.host, a.port), make_handler(kanban, key))
    say(f"screen activity helper for '{a.profile}' on http://{a.host}:{a.port}/activity")
    server.serve_forever()


if __name__ == "__main__":
    main()
