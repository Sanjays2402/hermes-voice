"""The read-only activity helper (skill/hermes-voice-setup/scripts/screen_activity.py), with a fake `hermes`."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import time
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skill/hermes-voice-setup/scripts/screen_activity.py"
NOW = 1_800_000_000

# What the fake `hermes kanban ... --json` returns: board "default", tasks for two profiles.
BOARD = [
    {
        "id": "t1",
        "title": "Research Echo Show 8",
        "assignee": "voice",
        "status": "running",
        "started_at": NOW - 600,
        "body": "private notes",
        "result": None,
        "workspace_path": "/somewhere",
    },
    {
        "id": "t2",
        "title": "Refactor the gateway",
        "assignee": "coder",
        "status": "running",
        "started_at": NOW - 60,
    },
    {"id": "t3", "title": "Draft upstream issue", "assignee": "voice", "status": "review"},
    {"id": "t4", "title": "Waiting on a key", "assignee": "coder", "status": "blocked"},
    {
        "id": "t5",
        "title": "Nightly digest",
        "assignee": "voice",
        "status": "done",
        "completed_at": NOW - 1200,
        "result": "long private result",
    },
    {
        "id": "t6",
        "title": "Old cleanup",
        "assignee": "voice",
        "status": "done",
        "completed_at": NOW - 5 * 3600,
    },
    {"id": "t7", "title": "Coder finished", "assignee": "coder", "status": "done", "completed_at": NOW - 100},
]

FAKE = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(args) + "\n")
board = json.loads(os.environ["FAKE_BOARD"])
if args[:3] == ["kanban", "boards", "list"]:
    print(json.dumps([{"slug": "default", "archived": False}, {"slug": "old", "archived": True}]))
    sys.exit(0)
def opt(name):
    return args[args.index(name) + 1] if name in args else None
rows = [t for t in board if t["status"] == opt("--status")]
if opt("--assignee"):
    rows = [t for t in rows if t["assignee"] == opt("--assignee")]
print(json.dumps(rows))
"""


@pytest.fixture
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("screen_activity", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_hermes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    exe = tmp_path / "hermes"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_BOARD", json.dumps(BOARD))
    return str(exe), log


def test_profile_scope(mod: ModuleType, fake_hermes: tuple[str, Path]) -> None:
    """Default scope: only tasks assigned to the voice profile; finished ones from the last 3 hours."""
    exe, log = fake_hermes
    out = mod.Kanban(exe, "voice").activity("profile", now=NOW)
    assert [t["id"] for t in out["running"]] == ["t1"]
    assert [t["id"] for t in out["attention"]] == ["t3"]
    assert [t["id"] for t in out["recent"]] == ["t5"]  # t6 finished 5 h ago
    assert out["scope"] == "profile" and out["profile"] == "voice" and out["boards"] == 1
    calls = log.read_text().splitlines()
    assert all("--assignee voice" in c for c in calls if " list --status " in c)
    assert not any("--board old" in c for c in calls)  # archived boards are skipped


def test_all_scope(mod: ModuleType, fake_hermes: tuple[str, Path]) -> None:
    """Opt-in: every profile's tasks, newest first."""
    exe, log = fake_hermes
    out = mod.Kanban(exe, "voice").activity("all", now=NOW)
    assert [t["id"] for t in out["running"]] == ["t2", "t1"]
    assert {t["id"] for t in out["attention"]} == {"t3", "t4"}
    assert [t["id"] for t in out["recent"]] == ["t7", "t5"]
    assert not any("--assignee" in c for c in log.read_text().splitlines())


def test_only_display_fields(mod: ModuleType, fake_hermes: tuple[str, Path]) -> None:
    """Bodies, results and paths stay on the computer: the screen gets titles, owners, status and times."""
    exe, _ = fake_hermes
    out = mod.Kanban(exe, "voice").activity("all", now=NOW)
    for task in out["running"] + out["attention"] + out["recent"]:
        assert set(task) <= {*mod.TASK_FIELDS, "board"}
        assert "body" not in task and "result" not in task and "workspace_path" not in task


def test_bad_scope_and_failing_hermes(mod: ModuleType, tmp_path: Path) -> None:
    exe = tmp_path / "hermes"
    exe.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    exe.chmod(0o755)
    with pytest.raises(ValueError):
        mod.Kanban(str(exe), "voice").activity("everything")
    with pytest.raises(RuntimeError, match="boom"):
        mod.Kanban(str(exe), "voice").activity("profile")


class _FakeSocket:
    """Enough of a socket for http.server's handler (the HA test plugin blocks real sockets)."""

    def __init__(self, request: bytes) -> None:
        self.rfile = io.BytesIO(request)
        self.sent = io.BytesIO()

    def makefile(self, mode: str, *args: Any, **kwargs: Any) -> io.BytesIO:
        return self.rfile if "r" in mode else self.sent

    def sendall(self, data: bytes) -> None:
        self.sent.write(data)


def _get(handler: Any, path: str, key: str | None = None) -> tuple[int, dict[str, Any]]:
    headers = f"Authorization: Bearer {key}\r\n" if key else ""
    sock = _FakeSocket(f"GET {path} HTTP/1.1\r\nHost: x\r\n{headers}\r\n".encode())
    handler(sock, ("127.0.0.1", 0), None)
    head, _, body = sock.sent.getvalue().partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


def test_http_handler(mod: ModuleType, fake_hermes: tuple[str, Path]) -> None:
    """Only the profile's key gets the activity; health is open; unknown scopes are refused."""
    exe, _ = fake_hermes
    handler = mod.make_handler(mod.Kanban(exe, "voice"), "the-key")
    assert _get(handler, "/health") == (200, {"ok": True})
    assert _get(handler, "/activity")[0] == 401
    assert _get(handler, "/activity", "wrong")[0] == 401
    assert _get(handler, "/activity?scope=everything", "the-key")[0] == 400
    status, body = _get(handler, "/activity?scope=all", "the-key")
    assert status == 200 and body["scope"] == "all" and body["running"]
    assert _get(handler, "/nothing", "the-key")[0] == 404


def test_cache(mod: ModuleType, fake_hermes: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Screens poll; hermes runs at most every CACHE_SECONDS per scope."""
    exe, log = fake_hermes
    kanban = mod.Kanban(exe, "voice")
    kanban.activity("profile", now=NOW)
    first = len(log.read_text().splitlines())
    kanban.activity("profile", now=NOW)
    assert len(log.read_text().splitlines()) == first
    later = time.monotonic() + mod.CACHE_SECONDS + 1
    monkeypatch.setattr(mod.time, "monotonic", lambda: later)
    stale = kanban.activity("profile", now=NOW)  # answered at once from the cache ...
    assert stale["running"]
    kanban._refreshing["profile"].join(5)  # ... while a refresh runs in the background
    assert len(log.read_text().splitlines()) > first
