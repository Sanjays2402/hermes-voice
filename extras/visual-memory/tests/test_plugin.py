"""The Hermes plugin: registration, vision through Hermes (ctx.llm), and the tools end to end."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import time
from types import SimpleNamespace

import pytest
from vismem import plugin, tools
from vismem.vision_model import HermesVision, VisionError

from .scenes import DESK, draw, shot


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answer_for(objs) -> dict:
    return {
        "summary": "a desk",
        "workspace": "0,150,1000,1000",
        "objects": [
            f"{o.label}|{o.category}|{','.join(o.attrs)}|{','.join(map(str, o.box))}|f" for o in objs
        ],
        "missing": [],
    }


class FakeLlm:
    def __init__(self, reply: dict | str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete_structured(self, **kw):
        self.calls.append(kw)
        if isinstance(self.reply, Exception):
            raise self.reply
        parsed = self.reply if isinstance(self.reply, dict) else None
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        usage = SimpleNamespace(input_tokens=900, output_tokens=80)
        return SimpleNamespace(parsed=parsed, text=text, provider="custom", model="qwen3.6", usage=usage)


class FakeCtx:
    def __init__(self, settings: dict, llm: FakeLlm) -> None:
        self.settings = settings
        self.llm = llm
        self.tools: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kw}

    def register_auxiliary_task(self, key, **kw):
        self.tasks[key] = kw


class Camera:
    def __init__(self, *_a, **_k) -> None:
        self.n = 0

    def snapshot(self):
        self.n += 1
        return shot(draw(DESK, rng_seed=self.n))


def run(ctx: FakeCtx, name: str, **args) -> str:
    out = json.loads(ctx.tools[name]["handler"](args, task_id="t"))
    return out.get("result") or out["error"]


def test_registers_the_tools_and_its_own_vision_task() -> None:
    ctx = FakeCtx({}, FakeLlm({}))
    plugin.register(ctx)
    assert set(ctx.tools) == set(tools.HANDLERS)
    for name, t in ctx.tools.items():
        assert t["schema"]["name"] == name and t["toolset"] == "visual_memory"
        assert set(t["schema"]["parameters"]["properties"]) == set(tools.PARAMETERS[name])
    task = ctx.tasks["visual_memory"]
    assert task["inherit_from"] == "vision"  # follows the user's vision model until they pin another


def test_manifest_declares_what_it_registers() -> None:
    manifest = (Path(__file__).parents[1] / "plugin.yaml").read_text()
    declared = manifest.split("provides_tools:")[1].split("requires_env:")[0]
    assert {ln.strip(" -") for ln in declared.split() if ln.strip(" -")} == set(tools.HANDLERS)
    assert "codex" not in manifest.lower()  # the plugin never runs the Codex CLI


def test_hermes_vision_asks_hermes_with_the_pictures() -> None:
    llm = FakeLlm(answer_for(DESK[:3]))
    a = HermesVision(llm).analyze(b"jpeg-now", None, [])
    call = llm.calls[0]
    assert call["task"] == "visual_memory" and call["json_mode"] is True
    assert [b["data"] for b in call["input"]] == [b"jpeg-now"]
    assert [d.label for d in a.objects] == ["red screwdriver", "coffee mug", "keys"]
    assert a.workspace == [0, 150, 1000, 1000]
    assert a.model == "custom/qwen3.6" and a.usage == {"input_tokens": 900, "output_tokens": 80}


def test_hermes_vision_reads_json_in_text_and_reports_failures() -> None:
    fenced = "Here you go:\n```json\n" + json.dumps(answer_for(DESK[:1])) + "\n```"
    assert [d.label for d in HermesVision(FakeLlm(fenced)).analyze(b"x", None, []).objects] == [
        "red screwdriver"
    ]
    with pytest.raises(VisionError, match="no vision model"):
        HermesVision(FakeLlm(RuntimeError("no vision model configured"))).analyze(b"x", None, [])


def test_tells_how_to_set_it_up_without_a_camera_address(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(plugin, "_data_dir", lambda: tmp_path)
    ctx = FakeCtx({}, FakeLlm({}))
    plugin.register(ctx)
    assert "eye_url" in run(ctx, "visual_memory", action="status")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_secret", lambda name: "eye-token")
    monkeypatch.setattr("vismem.camera.EyeCamera", Camera)
    settings = {
        "eye_url": "http://127.0.0.1:9",
        "api_port": free_port(),
        "display_port": free_port(),
        "display_allow": ["127.0.0.1"],
    }
    runtimes = []
    real = plugin.Runtime

    def tracked(ctx):
        r = real(ctx)
        runtimes.append(r)
        return r

    monkeypatch.setattr(plugin, "Runtime", tracked)
    yield settings, runtimes
    for r in runtimes:
        if r.service:
            r.service.shutdown()


def test_turn_on_look_and_answer_through_hermes(live) -> None:
    settings, runtimes = live
    llm = FakeLlm(answer_for(DESK[:3]))
    ctx = FakeCtx(settings, llm)
    plugin.register(ctx)
    assert "OK, I'm watching your desk" in run(ctx, "visual_memory", action="on")
    end = time.time() + 15
    while not llm.calls and time.time() < end:
        time.sleep(0.1)
    assert llm.calls and llm.calls[0]["task"] == "visual_memory"  # the first look went through Hermes
    end = time.time() + 15
    while time.time() < end and "keys" not in (said := run(ctx, "describe_scene", which="now")):
        time.sleep(0.3)
    assert "keys" in said
    assert "off" in run(ctx, "visual_memory", action="off")
    assert runtimes[0].service is not None


def test_a_second_session_uses_the_running_service(live) -> None:
    settings, runtimes = live
    first, second = FakeCtx(settings, FakeLlm({})), FakeCtx(settings, FakeLlm({}))
    plugin.register(first)
    plugin.register(second)
    assert "off" in run(first, "visual_memory", action="status").lower()
    assert "off" in run(second, "visual_memory", action="status").lower()  # ports taken: talks to the first
    assert runtimes[0].service is not None and runtimes[1].service is None
