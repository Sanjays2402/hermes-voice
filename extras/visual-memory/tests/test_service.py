"""The running service over HTTP: token, display allow-list, evidence feed, sessions."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
from vismem.config import Config
from vismem.service import Service

from .scenes import BLACK_BOX, DESK, FakeVision, draw, shot


class Camera:
    def __init__(self) -> None:
        self.objs = list(DESK)
        self.n = 0

    def snapshot(self):
        self.n += 1
        return shot(draw(self.objs, rng_seed=self.n))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def svc(tmp_path: Path):
    cfg = Config(
        data_dir=str(tmp_path),
        api_port=free_port(),
        display_port=free_port(),
        display_host="127.0.0.1",
        sample_seconds=0.1,
        stable_seconds=0.3,
        min_auto_interval=0,
        demo_min_interval=0,
        display_allow=["127.0.0.1"],
        auto_analyze=True,
    )
    fake, cam = FakeVision(), Camera()
    fake.scene = list(DESK)
    s = Service(cfg, vision=fake, camera=cam)
    t = threading.Thread(target=s.serve, daemon=True)
    # serve() installs signal handlers, which only works on the main thread: skip them here
    import signal

    orig = signal.signal
    signal.signal = lambda *a, **k: None  # type: ignore[assignment]
    t.start()
    time.sleep(0.3)
    signal.signal = orig  # type: ignore[assignment]
    yield s, fake, cam
    s.stop.set()
    t.join(3)


def req(s: Service, path: str, body: dict | None = None, token: str | None = None, port: int | None = None):
    url = f"http://127.0.0.1:{port or s.cfg.api_port}{path}"
    r = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token if token is not None else s.token}"},
    )
    with urllib.request.urlopen(r, timeout=10) as resp:
        return resp.status, resp.read()


def wait_for(cond, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_api_needs_the_token(svc) -> None:
    s, _, _ = svc
    assert req(s, "/health", token="")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        req(s, "/status", token="wrong")
    assert e.value.code == 403
    assert json.loads(req(s, "/status")[1])["on"] is False  # off until asked


def test_session_observe_compare_show(svc) -> None:
    s, fake, cam = svc
    events: list[dict] = []

    def listen() -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{s.cfg.display_port}/display/events", timeout=20) as r:
            for line in r:
                if line.startswith(b"data: "):
                    events.append(json.loads(line[6:]))

    threading.Thread(target=listen, daemon=True).start()
    wait_for(lambda: events and events[0]["type"] == "vismem")
    assert events[0]["on"] is False
    req(s, "/session", {"on": True, "mode": "demo"})
    wait_for(lambda: any(e.get("on") for e in events if e["type"] == "vismem"))
    wait_for(lambda: len(fake.calls) == 1)
    cam.objs = [o for o in DESK if o.label != "keys"] + [BLACK_BOX]
    fake.scene = list(cam.objs)
    wait_for(lambda: len(fake.calls) == 2 and not s.mem.live.analyzing)
    said = json.loads(req(s, "/changed")[1])["say"]
    assert "keys" in said and "small black box" in said
    shown = json.loads(req(s, "/show", {"what": "comparison"})[1])
    assert shown["shown"] and shown["listeners"] == 1
    wait_for(lambda: any(e["type"] == "vismem_show" and e["kind"] == "comparison" for e in events))
    key = [e["key"] for e in events if e["type"] == "vismem_show"][-1]  # (the first look showed one too)
    code, img = req(s, f"/display/image/{key}.jpg", port=s.cfg.display_port)
    assert code == 200 and img[:2] == b"\xff\xd8"
    with pytest.raises(urllib.error.HTTPError):
        req(s, "/display/image/guess.jpg", port=s.cfg.display_port)
    code, img = req(s, f"/evidence/comparison/{s.mem.baseline_id}.jpg")
    assert img[:2] == b"\xff\xd8"
    req(s, "/session", {"on": False})
    n = cam.n
    time.sleep(0.5)
    assert cam.n == n, "off means no pictures are taken"
    usage = json.loads(req(s, "/usage")[1])
    assert usage["calls"] == 2 and usage["input_tokens"] == 2000


def test_display_refuses_other_addresses(tmp_path: Path) -> None:
    cfg = Config(data_dir=str(tmp_path), display_allow=["192.168.1.20"])
    assert "192.168.1.20" in cfg.allowed_display_clients and "192.168.1.10" not in cfg.allowed_display_clients
    default = Config(data_dir=str(tmp_path), eye_url="http://192.168.1.10:28940")
    assert default.allowed_display_clients == {"192.168.1.10", "127.0.0.1", "::1"}


def test_restart_does_not_resume_watching(tmp_path: Path) -> None:
    cfg = Config(data_dir=str(tmp_path))
    s = Service(cfg, vision=FakeVision(), camera=Camera())
    s.mem.set_enabled(True)
    s.store.close()
    s2 = Service(cfg, vision=FakeVision(), camera=Camera())
    assert s2.mem.enabled is False
    assert s2.store.q("SELECT * FROM events WHERE kind='session'")[-1]["detail"] == {"on": False}


def test_local_vision_is_built_from_settings(tmp_path: Path) -> None:
    from vismem.service import make_vision
    from vismem.vision_model import LocalVision

    cfg = Config(data_dir=str(tmp_path), vision="local", local_model="qwen2.5vl:7b")
    v = make_vision(cfg, quick=True)
    assert isinstance(v, LocalVision) and v.model == "qwen2.5vl:7b" and v.api_key == ""


def test_local_models_describe_only_the_current_picture(tmp_path: Path) -> None:
    assert Config(data_dir=str(tmp_path)).compares
    assert not Config(data_dir=str(tmp_path), vision="local", local_model="m").compares
    assert Config(data_dir=str(tmp_path), vision="local", local_model="m", model_compares="true").compares
