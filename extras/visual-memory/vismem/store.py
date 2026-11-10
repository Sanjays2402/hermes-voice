"""SQLite memory: scenes, what was in them, items over time, changes, camera events and model use.

One file (WAL mode), plus a folder of JPEG snapshots. Everything survives restarts.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL, mode TEXT NOT NULL);
-- A scene is one steady view. kind: 'model' (analysed by the vision model) or 'check' (looked the same as
-- the scene before, by local comparison only: no model call).
CREATE TABLE IF NOT EXISTS scenes (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, camera TEXT NOT NULL, session INTEGER, kind TEXT NOT NULL,
  trigger TEXT NOT NULL, visibility TEXT NOT NULL, prev INTEGER, after_obstruction INTEGER DEFAULT 0,
  image TEXT, summary TEXT, model TEXT, changed REAL, brightness REAL, contrast REAL, sharpness REAL);
-- An item is "the red screwdriver": the same thing across scenes, linked when the model says so.
-- status: visible, unconfirmed (not re-detected, but its spot looks unchanged), possibly_obstructed,
-- missing (its spot is visible and it is not there), uncertain (the picture was not good enough).
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, category TEXT, attributes TEXT, status TEXT NOT NULL,
  first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_scene INTEGER, last_box TEXT, last_position TEXT,
  last_near TEXT, last_verified REAL, status_ts REAL, status_scene INTEGER, camera TEXT);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, scene INTEGER NOT NULL, item INTEGER, label TEXT, category TEXT,
  attributes TEXT, box TEXT, vis TEXT, position TEXT, near TEXT);
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY, scene INTEGER NOT NULL, prev INTEGER, ts REAL NOT NULL, kind TEXT NOT NULL,
  item INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, trigger TEXT, model TEXT, ok INTEGER, seconds REAL,
  input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER, error TEXT);
CREATE INDEX IF NOT EXISTS obs_scene ON observations(scene);
CREATE INDEX IF NOT EXISTS obs_item ON observations(item);
CREATE INDEX IF NOT EXISTS changes_scene ON changes(scene);
CREATE INDEX IF NOT EXISTS scenes_ts ON scenes(ts);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
"""


def _j(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for key in ("attributes", "box", "last_box", "detail", "near", "last_near"):
        if isinstance(d.get(key), str):
            d[key] = json.loads(d[key])
    return d


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dir.chmod(0o700)  # pictures of someone's home: this account only
        (self.dir / "snapshots").mkdir(exist_ok=True, mode=0o700)
        path = self.dir / "visual-memory.sqlite3"
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)  # SQLite creates its -wal and -shm files with the same permissions
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    def close(self) -> None:
        self.db.close()

    # Small helpers

    def q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [_row(r) for r in self.db.execute(sql, args).fetchall()]  # type: ignore[misc]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self.lock:
            return _row(self.db.execute(sql, args).fetchone())

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock, self.db:
            return self.db.execute(sql, args).lastrowid or 0

    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else default

    def put(self, key: str, value: str) -> None:
        self.x(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def event(self, kind: str, detail: dict[str, Any] | None = None, ts: float | None = None) -> None:
        self.x("INSERT INTO events(ts, kind, detail) VALUES(?, ?, ?)", (ts or time.time(), kind, _j(detail)))

    # Snapshots

    def save_image(self, scene_id: int, jpeg: bytes, ts: float) -> str:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        rel = f"snapshots/{day}/{scene_id}.jpg"
        path = self.dir / rel
        path.parent.mkdir(exist_ok=True, mode=0o700)
        path.write_bytes(jpeg)
        path.chmod(0o600)
        self.x("UPDATE scenes SET image=? WHERE id=?", (rel, scene_id))
        return rel

    def image_path(self, scene: dict[str, Any] | None) -> Path | None:
        if not scene or not scene.get("image"):
            return None
        p = self.dir / scene["image"]
        return p if p.exists() else None

    # Scenes and items

    def add_scene(self, **cols: Any) -> int:
        keys = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        return self.x(f"INSERT INTO scenes({keys}) VALUES({marks})", tuple(cols.values()))

    def scene(self, scene_id: int | None) -> dict[str, Any] | None:
        return self.one("SELECT * FROM scenes WHERE id=?", (scene_id,)) if scene_id else None

    def observations(self, scene_id: int) -> list[dict[str, Any]]:
        return self.q("SELECT * FROM observations WHERE scene=? ORDER BY id", (scene_id,))

    def changes(self, scene_id: int) -> list[dict[str, Any]]:
        return self.q("SELECT * FROM changes WHERE scene=? ORDER BY id", (scene_id,))

    def item(self, item_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM items WHERE id=?", (item_id,))

    def items(self, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            return self.q(f"SELECT * FROM items WHERE status IN ({marks}) ORDER BY id", statuses)
        return self.q("SELECT * FROM items ORDER BY id")

    def usage(self, since: float) -> list[dict[str, Any]]:
        return self.q("SELECT * FROM usage WHERE ts>=? ORDER BY ts", (since,))

    def log_usage(self, **cols: Any) -> None:
        keys = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        self.x(f"INSERT INTO usage({keys}) VALUES({marks})", tuple(cols.values()))

    # Deleting

    def forget(self, before: float | None = None, images_only: bool = False) -> dict[str, int]:
        """Delete pictures (and, unless images_only, observations) older than `before` (all if None)."""
        cutoff = before if before is not None else float("inf")
        gone = {"images": 0, "scenes": 0}
        keep = (
            int(self.get("baseline") or 0) if before is not None else 0
        )  # the scene memory compares against
        for s in self.q(
            "SELECT id, image FROM scenes WHERE ts<? AND image IS NOT NULL AND id!=?", (cutoff, keep)
        ):
            p = self.dir / s["image"]
            if p.exists():
                p.unlink()
                gone["images"] += 1
        with self.lock, self.db:
            self.db.execute("UPDATE scenes SET image=NULL WHERE ts<? AND id!=?", (cutoff, keep))
            if not images_only:
                ids = [r[0] for r in self.db.execute("SELECT id FROM scenes WHERE ts<?", (cutoff,))]
                gone["scenes"] = len(ids)
                self.db.execute(
                    "DELETE FROM observations WHERE scene IN (SELECT id FROM scenes WHERE ts<? AND id!=?)",
                    (cutoff, keep),
                )
                self.db.execute("DELETE FROM changes WHERE ts<?", (cutoff,))
                self.db.execute("DELETE FROM scenes WHERE ts<? AND id!=?", (cutoff, keep))
                self.db.execute("DELETE FROM items WHERE last_seen<? AND status_ts<?", (cutoff, cutoff))
                self.db.execute("DELETE FROM events WHERE ts<?", (cutoff,))
                if before is None:
                    for table in ("items", "sessions"):
                        self.db.execute(f"DELETE FROM {table}")
                    self.db.execute("DELETE FROM meta WHERE key IN ('baseline', 'pending')")
        for d in (self.dir / "snapshots").glob("*"):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        if not images_only:
            with self.lock:
                self.db.execute("VACUUM")
        return gone

    def forget_session(self, session_id: int) -> dict[str, int]:
        scenes = self.q("SELECT id, image FROM scenes WHERE session=?", (session_id,))
        for s in scenes:
            if s["image"] and (self.dir / s["image"]).exists():
                (self.dir / s["image"]).unlink()
        ids = tuple(s["id"] for s in scenes) or (-1,)
        marks = ",".join("?" for _ in ids)
        with self.lock, self.db:
            self.db.execute(f"DELETE FROM observations WHERE scene IN ({marks})", ids)
            self.db.execute(f"DELETE FROM changes WHERE scene IN ({marks})", ids)
            self.db.execute(f"DELETE FROM scenes WHERE id IN ({marks})", ids)
            self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            # items no longer seen in any remaining scene
            self.db.execute(
                "DELETE FROM items WHERE id NOT IN (SELECT DISTINCT item FROM observations "
                "WHERE item IS NOT NULL)"
            )
            self.db.execute("DELETE FROM meta WHERE key IN ('baseline', 'pending')")
        return {"scenes": len(scenes)}
