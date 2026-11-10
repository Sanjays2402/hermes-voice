"""Hermes Visual Memory as a native Hermes plugin.

register(ctx) adds the seven visual memory tools and a `visual_memory` auxiliary model task. Nothing runs
at registration. The first tool call starts the service inside this Hermes process (the camera loop,
127.0.0.1 API and the display feed the Echo's screen reads); the camera is only read while the user has
turned visual memory on. If another process already runs it (a second Hermes session of the same profile),
the tools talk to that one instead.

Settings: plugins.entries.hermes-visual-memory.settings (see plugin.yaml). The camera's token comes from
Hermes's secret scope (EYE_TOKEN). Memory lives in Hermes's per-plugin data directory.
"""

from __future__ import annotations

from dataclasses import fields
import json
import logging
from pathlib import Path
import threading
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from . import tools

NAME = "hermes-visual-memory"
TOOLSET = "visual_memory"
log = logging.getLogger("vismem.plugin")

# Settings a user may set (plugin.yaml config_schema declares the same keys).
SETTINGS = (
    "eye_url",
    "vision",
    "local_url",
    "local_model",
    "local_quick_model",
    "retention_days",
    "history_days",
    "api_port",
    "display_port",
    "display_allow",
    "mirror_for_user",
    "quick_wait",
    "look_when_settled",
    "manual_per_hour",
)


class Runtime:
    """The service this process runs (or the one another process runs), started on first use."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.lock = threading.Lock()
        self.service: Any = None
        self.url = ""
        self.token = ""

    # Settings

    def config(self) -> Any:
        from .config import Config

        cfg = Config(vision="hermes", eye_url="")
        known = {f.name for f in fields(Config)}
        for key in SETTINGS:
            if key not in known:
                continue
            value = self.ctx.get_config(key, None)
            if value is not None and value != "":
                setattr(cfg, key, value)
        cfg.data_dir = str(_data_dir())
        if cfg.vision not in ("hermes", "local"):
            raise SetupError(
                'vision must be "hermes" (the model Hermes picks) or "local" (an OpenAI-compatible server).'
            )
        if cfg.vision == "local" and not cfg.local_model:
            raise SetupError('vision = "local" needs local_model, e.g. "qwen3.6:35b-a3b".')
        if not cfg.eye_url:
            raise SetupError(
                "Visual memory needs the camera's address: set eye_url to Hermes Eye on the Echo Show, e.g. "
                "hermes config set plugins.entries.hermes-visual-memory.settings.eye_url http://<echo-ip>:28940"
            )
        return cfg

    # Start on first use

    def ensure(self) -> None:
        with self.lock:
            if self.url:
                return
            cfg = self.config()
            from .camera import EyeCamera
            from .service import Service, make_vision
            from .vision_model import HermesVision

            if cfg.vision == "hermes":
                vision = HermesVision(self.ctx.llm, cfg.analysis_timeout)
                quick = HermesVision(self.ctx.llm, 30.0)
            else:
                vision, quick = make_vision(cfg, quick=False), make_vision(cfg, quick=True)
            camera = EyeCamera(cfg.eye_url, _secret("EYE_TOKEN"))
            service = Service(cfg, vision=vision, camera=camera, quick_vision=quick)
            try:
                service.start()
            except OSError:
                # Another process (this profile's other Hermes session) runs it: use that one.
                service.store.close()
                self.token = (cfg.data_path / "api_token").read_text().strip()
            else:
                self.service, self.token = service, service.token
            self.url = f"http://127.0.0.1:{cfg.api_port}"

    def call(
        self, path: str, params: dict | None = None, body: dict | None = None, timeout: float = 30
    ) -> dict:
        try:
            self.ensure()
        except SetupError as err:
            return {"say": str(err)}
        url = self.url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if err.code == 401:
                port = urllib.parse.urlparse(self.url).port
                with self.lock:
                    self.url = ""  # try again next time
                return {
                    "say": f"Another visual memory service is using port {port}: stop the standalone "
                    "service, or set api_port and display_port to free ports."
                }
            return {"say": f"Visual memory answered {err.code}."}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            with self.lock:
                if self.service is None:
                    self.url = ""  # the other process went away: start our own next time
            return {"say": "The visual memory service isn't running."}


class SetupError(Exception):
    pass


def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return Path(plugin_data_dir(NAME))


def _secret(name: str) -> str:
    from agent.secret_scope import get_secret

    return get_secret(name, "") or ""


def _handler(runtime: Runtime, name: str) -> Any:
    fn = tools.HANDLERS[name]
    allowed = set(tools.PARAMETERS[name])

    def handle(args: dict | None = None, **_: Any) -> str:
        kwargs = {k: v for k, v in (args or {}).items() if k in allowed}
        try:
            return json.dumps({"result": fn(runtime.call, **kwargs)})
        except Exception as err:  # never let a tool call crash the agent's turn
            log.exception("visual memory tool %s failed", name)
            return json.dumps({"error": f"Visual memory failed: {err}"})

    return handle


def register(ctx: Any) -> None:
    ctx.register_auxiliary_task(
        "visual_memory",
        display_name="Visual memory",
        description="Looks at the camera's pictures: finds the desk and names the things on it.",
        inherit_from="vision",
        defaults={"timeout": 60},
    )
    runtime = Runtime(ctx)
    for name in tools.HANDLERS:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=tools.schema(name),
            handler=_handler(runtime, name),
            description=tools.DESCRIPTIONS[name].split(". ")[0],
            emoji="👁",
        )
