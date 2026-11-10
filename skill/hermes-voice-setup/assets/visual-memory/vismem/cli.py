"""vismem: run the service, or control and inspect it.

vismem serve                     run (launchd runs this; see `vismem install-service`)
vismem status | usage | changed | scene [which] | find <words> | history [words] [--since S]
vismem on [--demo] | off         start / stop observing
vismem show comparison|item|scene [words] | hide
vismem forget all|images|session | forget older <days>
vismem install-service | uninstall-service
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import config

LABEL = "org.hermesvoice.visual-memory"
PLIST = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()


def _api(cfg: config.Config, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    from .service import api_token

    url = f"http://{cfg.api_host}:{cfg.api_port}{path}" + (
        "?" + urllib.parse.urlencode(params) if params else ""
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {api_token(cfg)}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as err:
        sys.exit(f"visual memory service not reachable ({err.reason}); start it: vismem serve")


def _print(r: dict, raw: bool) -> None:
    print(json.dumps(r, indent=2, default=str) if raw or "say" not in r else r["say"])


UNIT = Path("~/.config/systemd/user/hermes-visual-memory.service").expanduser()


def install_service(cfg: config.Config) -> None:
    logs = cfg.data_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    exe = Path(sys.executable)
    if sys.platform != "darwin":
        _install_systemd(exe)
        return
    plist = {
        "Label": LABEL,
        "ProgramArguments": [str(exe), "-m", "vismem.cli", "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "service.log"),
        "StandardErrorPath": str(logs / "service.log"),
        # codex lives with node (nvm/homebrew): keep the PATH of the shell that installed the service
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "VISMEM_CONFIG": str(config.CONFIG_FILE),
        },
    }
    if PLIST.exists():
        _unload()
    PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)
    print(f"installed and started: {PLIST}\nlogs: {logs / 'service.log'}")


def _unload() -> None:
    """Stop the service and wait until launchd has let go of it (bootout returns before that)."""
    import time

    target = f"gui/{os.getuid()}/{LABEL}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True)
    for _ in range(50):
        if subprocess.run(["launchctl", "print", target], capture_output=True).returncode != 0:
            return
        time.sleep(0.2)


def _install_systemd(exe: Path) -> None:
    """Linux: a systemd user service (starts at login; `loginctl enable-linger` keeps it running without one)."""
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    path = os.environ.get("PATH", "/usr/bin:/bin")
    UNIT.write_text(
        "[Unit]\nDescription=Hermes Visual Memory\nAfter=network-online.target\n\n"
        f"[Service]\nExecStart={exe} -m vismem.cli serve\nRestart=always\nRestartSec=10\n"
        f"Environment=PATH={path}\nEnvironment=VISMEM_CONFIG={config.CONFIG_FILE}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT.name], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT.name], check=True)
    print(f"installed and started: {UNIT}")


def uninstall_service() -> None:
    if sys.platform != "darwin":
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT.name], capture_output=True)
        if UNIT.exists():
            UNIT.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        print("service stopped and removed (memory is kept)")
        return
    _unload()
    if PLIST.exists():
        PLIST.unlink()
    print("service stopped and removed (memory is kept)")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    raw = "--json" in args
    args = [a for a in args if a != "--json"]
    cfg = config.load()
    cmd = args[0] if args else "status"
    rest = args[1:]
    if cmd == "serve":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        from .service import Service

        Service(cfg).serve()
    elif cmd == "status":
        _print(_api(cfg, "/status"), raw or bool(rest and rest[0] == "-v"))
    elif cmd == "usage":
        _print(_api(cfg, "/usage", {"days": rest[0] if rest else "1"}), True)
    elif cmd in ("on", "off"):
        _print(
            _api(
                cfg,
                "/session",
                body={
                    "on": cmd == "on",
                    "mode": "demo" if "--demo" in rest else ("memory" if cmd == "on" else None),
                },
            ),
            True,
        )
    elif cmd == "changed":
        _print(_api(cfg, "/changed", {"fresh": "1"}), raw)
    elif cmd == "scene":
        _print(_api(cfg, "/scene", {"which": rest[0] if rest else "now"}), raw)
    elif cmd == "find":
        _print(_api(cfg, "/find", {"q": " ".join(rest)}), raw)
    elif cmd == "history":
        since = ""
        if "--since" in rest:
            i = rest.index("--since")
            since = rest[i + 1]
            rest = rest[:i] + rest[i + 2 :]
        _print(_api(cfg, "/history", {"q": " ".join(rest), "since": since}), raw)
    elif cmd == "show":
        _print(
            _api(cfg, "/show", body={"what": rest[0] if rest else "comparison", "query": " ".join(rest[1:])}),
            True,
        )
    elif cmd == "hide":
        _print(_api(cfg, "/hide", body={}), True)
    elif cmd == "forget":
        scope = rest[0] if rest else ""
        body: dict = {"scope": scope}
        if scope == "older":
            body["days"] = float(rest[1])
        _print(_api(cfg, "/forget", body=body), raw)
    elif cmd == "install-service":
        install_service(cfg)
    elif cmd == "uninstall-service":
        uninstall_service()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
