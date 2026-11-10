"""The observer: sample the camera, judge each picture, wait for a steady scene, and decide when the
vision model must look. Then turn the model's list into memory: what stayed, moved, appeared, and what
can no longer be seen, and why (gone from a visible spot, or the spot may be blocked).

The model is only asked "what is in this picture, and which of these earlier objects are they". Whether
an object is gone or hidden is decided here, from the pictures themselves (did its spot change, is
something new now in front of it) together with what the model said.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any

import cv2
import numpy as np

from .camera import Shot
from .config import Config
from .store import Store
from .vision_local import (
    Difference,
    Frame,
    Thresholds,
    Visibility,
    assess,
    decode,
    difference,
    jpeg,
    spot_change,
    still,
)
from .vision_model import Analysis, Detection, Vision, VisionError

log = logging.getLogger("vismem")

BELIEVED_PRESENT = ("visible", "unconfirmed", "possibly_obstructed", "uncertain")
SPOT_CHANGED = (
    0.30  # how much an object's spot must change (spot_change, 0..1) before "it's not there" is believable
)
COVERED = 0.30  # share of an object's box covered by something new: the view of it may be blocked
HIDES_ALL = 0.80  # ... and when the model says the spot is empty, it must be covered almost entirely


@dataclass
class Pending:
    frame: Frame
    jpeg: bytes
    ts: float
    trigger: str


@dataclass
class Live:
    """What the observer knows right now (not stored)."""

    visibility: Visibility = Visibility.UNREADABLE
    why: str = "not started"
    since: float = 0.0
    frame_ts: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    sharpness: float = 0.0
    steady: bool = False
    analyzing: bool = False
    interrupted_at: float | None = None  # the camera stopped giving a usable picture after the baseline
    last_error: str = ""
    deferred: str = ""  # why a changed scene is waiting (a usage limit)
    samples: int = 0
    on_since: float = 0.0  # when it was last turned on
    extra: dict[str, Any] = field(default_factory=dict)


def area(b: list[int]) -> float:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def iou(a: list[int], b: list[int]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = area(a) + area(b) - inter
    return inter / union if union else 0.0


def center(b: list[int]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def moved(a: list[int], b: list[int]) -> bool:
    (ax, ay), (bx, by) = center(a), center(b)
    # 0-1000 on both axes; the picture is 16:9, so a vertical step counts for less
    dist = ((ax - bx) ** 2 + ((ay - by) * 9 / 16) ** 2) ** 0.5
    return dist > 70 and iou(a, b) < 0.4


def coverage(target: list[int], boxes: list[list[int]]) -> float:
    """Share of `target` covered by the union of `boxes`."""
    if not boxes or area(target) == 0:
        return 0.0
    grid = np.zeros((200, 200), np.uint8)
    for b in boxes:
        grid[b[1] // 5 : max(b[1] // 5 + 1, b[3] // 5), b[0] // 5 : max(b[0] // 5 + 1, b[2] // 5)] = 1
    t = grid[
        target[1] // 5 : max(target[1] // 5 + 1, target[3] // 5),
        target[0] // 5 : max(target[0] // 5 + 1, target[2] // 5),
    ]
    return float(t.mean()) if t.size else 0.0


def position(box: list[int], mirror: bool) -> str:
    """Where in the view, said as the user sees it (facing the Echo) when mirror is on."""
    cx, cy = center(box)
    if mirror:
        cx = 1000 - cx
    h = "on the left" if cx < 333 else "in the middle" if cx < 667 else "on the right"
    if cy < 250:
        h += ", toward the top of the view"
    elif cy > 750:
        h += ", toward the bottom of the view"
    return h


def neighbours(i: int, boxes: list[list[int]], labels: list[str], n: int = 2) -> list[str]:
    cx, cy = center(boxes[i])
    others = sorted(
        (((center(b)[0] - cx) ** 2 + (center(b)[1] - cy) ** 2) ** 0.5, labels[j])
        for j, b in enumerate(boxes)
        if j != i
    )
    return [label for d, label in others[:n] if d < 450]


def words(text: str) -> set[str]:
    out = set()
    for w in "".join(c if c.isalnum() else " " for c in text.lower()).split():
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


COLOURS = {
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "violet",
    "pink",
    "brown",
    "black",
    "white",
    "gray",
    "grey",
    "silver",
    "gold",
    "beige",
    "tan",
    "teal",
    "turquoise",
    "navy",
    "maroon",
    "clear",
    "transparent",
}


def colours(label: str, attributes: list[str]) -> set[str]:
    found = (words(label) | words(" ".join(attributes))) & COLOURS
    return {"gray" if c == "grey" else "clear" if c == "transparent" else c for c in found}


class Limiter:
    """Model calls: automatic ones have a minimum gap and hourly/daily caps; asked-for ones an hourly cap."""

    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg, self.store = cfg, store

    def allow(self, trigger: str, now: float, demo: bool) -> tuple[bool, str]:
        recent = self.store.usage(now - 86400)
        if not self.cfg.auto_analyze:
            if trigger == "change":  # the desk settled after a change (local model): free, but spaced out
                last = max((u["ts"] for u in recent), default=0.0)
                if now - last < self.cfg.settled_min_interval:
                    return False, "a moment between looks"
                return True, ""
            n = sum(1 for u in recent if u["ts"] > now - 3600 and u["trigger"] != "change")
            if self.cfg.manual_per_hour and n >= self.cfg.manual_per_hour:
                return False, f"{n} looks in the last hour (the limit is {self.cfg.manual_per_hour})"
            return True, ""
        demo_looks = ("first", "before_obstruction", "after_obstruction")
        if trigger == "asked" or (demo and trigger in demo_looks):
            # Asked for, or the looks a demo is about (the view before the cover, and after it): an hourly
            # budget of their own, so a day of automatic looks can't leave a demo without its before/after.
            own = ("asked", *demo_looks) if demo else ("asked",)
            n = sum(1 for u in recent if u["trigger"] in own and u["ts"] > now - 3600)
            if n >= self.cfg.manual_per_hour:
                return False, f"{n} asked-for analyses in the last hour"
            if trigger != "asked":
                last = max((u["ts"] for u in recent if u["trigger"] in own), default=0.0)
                if now - last < self.cfg.demo_min_interval:
                    return False, "a moment between looks"
            return True, ""
        auto = [u for u in recent if u["trigger"] != "asked"]
        if sum(1 for u in auto if u["ts"] > now - 3600) >= self.cfg.auto_per_hour:
            return False, f"hourly limit ({self.cfg.auto_per_hour}) reached"
        if len(auto) >= self.cfg.auto_per_day:
            return False, f"daily limit ({self.cfg.auto_per_day}) reached"
        # Ordinary changes wait min_auto_interval (a person moving in view settles again and again). The
        # first look and the comparison after the camera was obstructed are what a demo waits for.
        gap = self.cfg.min_auto_interval
        if trigger in ("first", "after_obstruction"):
            gap = self.cfg.demo_min_interval if demo else min(gap, 15.0)
        last = max((u["ts"] for u in auto), default=0.0)
        if now - last < gap:
            return False, f"waiting {gap - (now - last):.0f} s between automatic analyses"
        return True, ""


class Memory:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        vision: Vision,
        clock: Callable[[], float] = time.time,
        background: bool = True,
        quick_vision: Vision | None = None,
    ) -> None:
        self.cfg, self.store, self.vision, self.clock = cfg, store, vision, clock
        # A look someone is waiting for (a question) uses the quicker model: the Echo gives up on an answer
        # that hasn't started speaking 15 s after it was asked.
        self.quick_vision = quick_vision or vision
        self._last_steady: Pending | None = None
        self._need_first = False  # turned on: look once at the first steady view
        self._last_local: np.ndarray | None = None  # the last view saved without a look
        self.workspace: list[int] | None = None  # the desk being watched (0-1000); None: the whole view
        ws = self.store.get("workspace")
        if ws and self.area == "desk":
            self.workspace = [int(v) for v in ws.split()]
        self.background = background
        self.t = Thresholds(
            cfg.unreadable_contrast,
            cfg.unreadable_dark,
            cfg.unreadable_bright,
            cfg.uncertain_contrast,
            cfg.uncertain_sharpness,
            cfg.unreadable_sharpness,
        )
        self.limiter = Limiter(cfg, store)
        self.live = Live(since=clock())
        self.lock = threading.RLock()
        self.done = threading.Condition(self.lock)
        self._last_small: np.ndarray | None = None
        self._still_since: float | None = None
        self._settled: np.ndarray | None = None
        self._latest: tuple[Frame, bytes, float] | None = None
        self._current_small: np.ndarray | None = None
        self._detail: deque[float] = deque(maxlen=30)  # sharpness of recent clear frames
        self._dull = 0
        self.pending: Pending | None = None
        self._retry_after = 0.0
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.baseline_id: int | None = None
        self.baseline_small: np.ndarray | None = None
        self._load_baseline()

    # State that survives restarts

    def _load_baseline(self) -> None:
        sid = self.store.get("baseline")
        scene = self.store.scene(int(sid)) if sid else None
        path = self.store.image_path(scene)
        if scene and path:
            img = cv2.imread(str(path))
            if img is not None:
                self.baseline_id = scene["id"]
                self.baseline_small = assess(img, self.t).small
                return
        self.baseline_id, self.baseline_small = None, None

    @property
    def enabled(self) -> bool:
        return self.store.get("enabled") == "1"

    @property
    def mode(self) -> str:
        return self.store.get("mode", "memory") or "memory"

    @property
    def area(self) -> str:
        """'desk' (the work surface the first look finds) or 'all' (the whole view)."""
        return self.store.get("area", "desk") or "desk"

    @property
    def first_pending(self) -> bool:
        """Turned on, and the first look (which finds the desk) isn't done yet."""
        return self.enabled and (self._need_first or self.live.extra.get("first_running", False))

    def _m(self, small: np.ndarray) -> np.ndarray:
        """Only the watched area counts: everything outside it is blanked out before frames are compared,
        so someone sitting behind the desk, a chair or a doorway doesn't count as a change."""
        if self.workspace is None:
            return small
        h, w = small.shape[:2]
        x0, y0, x1, y1 = self.workspace
        out = np.full_like(small, 128)
        r = (slice(y0 * h // 1000, -(-y1 * h // 1000)), slice(x0 * w // 1000, -(-x1 * w // 1000)))
        out[r] = small[r]
        return out

    @property
    def session_id(self) -> int | None:
        s = self.store.get("session")
        return int(s) if s else None

    def emit(self, event: dict[str, Any]) -> None:
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:  # a display client going away must not stop the observer
                log.exception("listener failed")

    def set_enabled(self, on: bool, mode: str | None = None, area: str | None = None) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if mode:
                self.store.put("mode", mode)
            if area in ("desk", "all") and area != self.area:
                self.store.put("area", area)
                self._need_first = on  # find the desk (or drop it) with a look
                if area == "all":
                    self.workspace = None
            if on and not self.enabled:
                sid = self.store.x("INSERT INTO sessions(started, mode) VALUES(?, ?)", (now, self.mode))
                self.store.put("session", str(sid))
                self.store.put("enabled", "1")
                self.store.event("session", {"on": True, "mode": self.mode, "session": sid}, now)
                self._reset_motion()
                self._need_first = True
                self.live.on_since = now
                self.workspace = None  # the camera may have moved: the first look finds the desk again
            elif not on and self.enabled:
                if self.session_id:
                    self.store.x("UPDATE sessions SET ended=? WHERE id=?", (now, self.session_id))
                self.store.put("enabled", "0")
                self.store.event("session", {"on": False}, now)
                self.live.visibility, self.live.why, self.live.since = Visibility.UNREADABLE, "paused", now
                self.pending = None
            self.emit({"type": "status", "on": self.enabled, "mode": self.mode})
            return {"on": self.enabled, "mode": self.mode, "session": self.session_id, "area": self.area}

    def _reset_motion(self) -> None:
        self._last_small, self._still_since, self._settled = None, None, None

    # One camera sample

    def step(self, shot: Shot) -> None:
        with self.lock:
            now = self.clock()
            self.live.samples += 1
            if shot.jpeg is None:
                self._visibility(Visibility.UNREADABLE, shot.reason, now)
                return
            img = decode(shot.jpeg)
            if img is None:
                self._visibility(Visibility.UNREADABLE, "bad picture", now)
                return
            frame = assess(img, self.t)
            self._compare_detail(frame)
            lv = self.live
            lv.frame_ts, lv.brightness, lv.contrast, lv.sharpness = (
                now,
                frame.brightness,
                frame.contrast,
                frame.sharpness,
            )
            self._visibility(frame.visibility, frame.why, now)
            if frame.visibility is Visibility.UNREADABLE:
                return
            self._latest = (frame, shot.jpeg, now)
            if self._last_small is not None and still(self._m(self._last_small), self._m(frame.small), self.cfg.still_fraction):
                if self._still_since is None:
                    self._still_since = now
            else:
                self._still_since = None
            self._last_small = frame.small
            steady = (
                self._still_since is not None and now - self._still_since >= self.cfg.stable_seconds - 1e-6
            )
            self.live.steady = steady
            if (
                not steady
                and self._need_first
                and not self.live.analyzing
                and now >= self._retry_after
                and frame.visibility is Visibility.CLEAR
                and now - self.live.on_since >= self.cfg.first_look_within
            ):
                # Just turned on and someone keeps moving in view: don't wait for a still picture to find
                # the desk (after that, only the desk has to hold still).
                self._request(Pending(frame, shot.jpeg, now, "first"))
                return
            if not steady:
                return
            if frame.visibility is Visibility.UNCERTAIN and frame.why == "less detail than usual":
                # Steady and only somewhat plainer than before: something was taken away, not a hand in front
                # (a hand is never this still). This is the view now.
                self._detail.clear()
                self._dull = 0
                frame.visibility, frame.why = Visibility.CLEAR, ""
                self._visibility(Visibility.CLEAR, "", now)
            self._last_steady = Pending(frame, shot.jpeg, now, "before_obstruction")
            if (
                self.pending
                and not self.live.analyzing
                and now >= self._retry_after
                and still(self._m(self.pending.frame.small), self._m(frame.small), self.cfg.change_fraction)
            ):
                self._request(Pending(frame, shot.jpeg, now, self.pending.trigger))
                return
            if self.live.analyzing:
                return  # one look at a time; the scene is checked again when it is done
            if (
                self._settled is not None
                and self.live.interrupted_at is None
                and not (self._need_first and now >= self._retry_after)
                and still(self._m(self._settled), self._m(frame.small), self.cfg.change_fraction)
            ):
                return  # this steady scene was already handled
            self._settled = frame.small
            self._steady_scene(frame, shot.jpeg, now)

    def _compare_detail(self, frame: Frame) -> None:
        """A hand right over the lens is out of focus but lit: auto-exposure keeps it bright, so it can pass
        the fixed checks. Compared with how much detail this view usually has, it is obvious (measured on the
        Show 5: about 320 for the desk, 20-55 under a hand)."""
        hist = self._detail
        if frame.visibility is not Visibility.UNREADABLE and len(hist) >= 5:
            ref = float(np.median(hist))
            ratio = frame.sharpness / ref if ref else 1.0
            if ratio < self.cfg.uncertain_detail:
                self._dull += 1
                if self._dull <= self.cfg.dull_accept_samples:
                    if ratio < self.cfg.obstructed_detail:
                        frame.visibility, frame.why = Visibility.UNREADABLE, "much less detail than usual"
                    else:
                        frame.visibility, frame.why = Visibility.UNCERTAIN, "less detail than usual"
                    return
                # It stayed that way for a minute: a plainer view (things moved away) is the new normal.
                hist.clear()
                self._dull = 0
        if frame.visibility is Visibility.CLEAR:
            hist.append(frame.sharpness)
            self._dull = 0

    def _visibility(self, vis: Visibility, why: str, now: float) -> None:
        lv = self.live
        if vis is not lv.visibility:
            self.store.event("visibility", {"from": lv.visibility.value, "to": vis.value, "why": why}, now)
            if vis is Visibility.UNREADABLE:
                self._reset_motion()
                if self.baseline_id is not None and lv.interrupted_at is None:
                    lv.interrupted_at = now
                    self._look_before(now)
            lv.visibility, lv.since = vis, now
            self.emit({"type": "visibility", "state": vis.value})
        lv.why = why
        if vis is Visibility.UNREADABLE:
            lv.steady = False

    def _look_before(self, now: float) -> None:
        """The camera was just covered. "What changed?" will mean "since just before that", so if the last
        steady view differs from what memory last analysed, analyse it now, while the camera is covered."""
        last, self._last_steady = self._last_steady, None
        if last is None or self.baseline_small is None or self.live.analyzing or now - last.ts > 30:
            return
        base = self.store.scene(self.baseline_id)
        if base and last.ts <= base["ts"]:
            return
        diff = difference(self._m(self.baseline_small), self._m(last.frame.small))
        if diff.fraction >= self.cfg.change_fraction and diff.regions:
            if self.cfg.auto_analyze:
                self._request(last)
            else:
                self._save_local(last.frame, last.ts, "before_obstruction")

    def _steady_scene(self, frame: Frame, data: bytes, now: float) -> None:
        after = self.live.interrupted_at is not None
        if frame.visibility is not Visibility.CLEAR:
            # Not clear enough to update memory on its own: wait for a clear view (or a question). Without
            # automatic looks, a changed view is still kept, for a question to look at.
            if not self.cfg.auto_analyze and self.baseline_small is not None and not self._need_first:
                d = difference(self._m(self.baseline_small), self._m(frame.small))
                if d.fraction >= self.cfg.change_fraction and d.regions:
                    self._save_local(frame, now, "after_obstruction" if after else "change")
            self._settled = None
            self.live.deferred = f"waiting for a clear view ({frame.why})"
            return
        if self.baseline_id is None or self.baseline_small is None or self._need_first:
            if now < self._retry_after:
                return
            self._request(Pending(frame, data, now, "first"))
            return
        diff = difference(self._m(self.baseline_small), self._m(frame.small))
        if diff.fraction < self.cfg.change_fraction or not diff.regions:
            self._unchanged(frame, data, now, diff, after)
            return
        trigger = "after_obstruction" if after else "change"
        if self.cfg.auto_analyze or self.cfg.settled_looks or (after and self.cfg.look_after_uncover):
            self._request(Pending(frame, data, now, trigger))
        else:
            self._save_local(frame, now, trigger)

    def _save_local(self, frame: Frame, now: float, trigger: str) -> None:
        """A changed view, kept on this computer without asking the model. A question looks at it later."""
        self.pending = None
        self.live.deferred = ""
        if self._last_local is not None:
            d = difference(self._m(self._last_local), self._m(frame.small))
            if d.fraction < self.cfg.change_fraction or not d.regions:
                return  # already saved
        self._last_local = frame.small
        sid = self.store.add_scene(
            ts=now,
            camera=self.cfg.camera_id,
            session=self.session_id,
            kind="local",
            trigger=trigger,
            visibility=frame.visibility.value,
            prev=self.baseline_id,
            after_obstruction=int(trigger == "after_obstruction"),
            summary="",
            brightness=frame.brightness,
            contrast=frame.contrast,
            sharpness=frame.sharpness,
        )
        self.store.save_image(sid, jpeg(frame.image, 85), now)
        self.emit({"type": "saved", "scene": sid})

    def _unchanged(self, frame: Frame, data: bytes, now: float, diff: Difference, after: bool) -> None:
        """The steady scene looks like the baseline: no model call. Everything there is still there."""
        self.store.x(
            "UPDATE items SET last_verified=? WHERE status IN ('visible', 'unconfirmed') AND last_scene IS NOT NULL",
            (now,),
        )
        self.pending = None
        self.live.deferred = ""
        if after:
            sid = self.store.add_scene(
                ts=now,
                camera=self.cfg.camera_id,
                session=self.session_id,
                kind="check",
                trigger="after_obstruction",
                visibility=frame.visibility.value,
                prev=self.baseline_id,
                after_obstruction=1,
                summary="The view looks the same as before the camera was obstructed.",
                changed=diff.fraction,
                brightness=frame.brightness,
                contrast=frame.contrast,
                sharpness=frame.sharpness,
            )
            self.store.save_image(sid, jpeg(frame.image, 85), now)
            self.store.x(
                "INSERT INTO changes(scene, prev, ts, kind, detail) VALUES(?, ?, ?, 'unchanged', ?)",
                (sid, self.baseline_id, now, '{"by": "local comparison"}'),
            )
            self.live.interrupted_at = None
            self.store.put("last_comparison", str(sid))
            self.emit({"type": "compared", "scene": sid, "changes": 0})

    # Asking the model

    def _request(self, p: Pending) -> bool:
        demo = self.mode == "demo"
        if p.trigger != "asked":
            ok, why = self.limiter.allow(p.trigger, p.ts, demo)
            if not ok:
                if self.pending is None:
                    self.store.event("deferred", {"trigger": p.trigger, "why": why}, p.ts)
                self.pending, self.live.deferred = p, why
                return False
        if self.live.analyzing:
            self.pending = p
            return False
        self.pending, self.live.deferred = None, ""
        if p.trigger == "first":
            self._need_first = False  # (set again if this look fails)
        self.live.analyzing = True
        self.live.extra["first_running"] = p.trigger == "first"
        if self.background:
            threading.Thread(target=self._analyze, args=(p,), daemon=True, name="vismem-analyze").start()
        else:
            self._analyze(p)
        return True

    def analyze_now(self, wait: float = 25.0) -> dict[str, Any]:
        """Asked for (e.g. "what changed?"): analyse the current steady view now if it differs from memory."""
        with self.lock:
            deadline = time.monotonic() + wait
            while self.live.analyzing and time.monotonic() < deadline:
                self.done.wait(deadline - time.monotonic())
            if self.live.analyzing:
                return {"state": "busy"}
            if self.live.visibility is Visibility.UNREADABLE or self._latest is None:
                return {"state": "unreadable"}
            frame, data, ts = self._latest
            if self.clock() - ts > 10:
                return {"state": "stale"}
            if self.clock() - self.live.extra.get("looked_at", 0.0) < self.cfg.fresh_for:
                return {"state": "same"}  # a look just finished: that is the answer
            if self.baseline_small is not None:
                diff = difference(self._m(self.baseline_small), self._m(frame.small))
                if diff.fraction < self.cfg.change_fraction or not diff.regions:
                    if self.live.interrupted_at is not None:
                        self._unchanged(frame, data, ts, diff, True)
                    return {"state": "same"}
            ok, why = self.limiter.allow("asked", self.clock(), False)
            if not ok:
                return {"state": "limited", "why": why}
            # The first look after turning on also finds the desk.
            trigger = "first" if self._need_first else "asked"
            self._request(Pending(frame, data, ts, trigger))
            while self.live.analyzing and time.monotonic() < deadline:
                self.done.wait(deadline - time.monotonic())
            return {"state": "busy" if self.live.analyzing else "done", "trigger": trigger}

    def _watch(self, p: Pending, result: Analysis) -> None:
        """The first look finds the desk; after that, things off the desk are not tracked."""
        if self.area != "desk":
            return
        if p.trigger == "first" and result.workspace:
            x0, y0, x1, y1 = result.workspace
            pad = 30  # a little room around the edges
            box = [max(0, x0 - pad), max(0, y0 - pad), min(1000, x1 + pad), min(1000, y1 + pad)]
            if area(box) < 0.9 * 1000 * 1000:  # (the whole view anyway: nothing to leave out)
                with self.lock:
                    self.workspace = box
                    self.store.put("workspace", " ".join(map(str, box)))
        if self.workspace:
            result.objects = [d for d in result.objects if self._inside(d.box)]

    def _inside(self, box: list[int]) -> bool:
        """On the desk: its bottom edge (where it stands) is in the watched area. A tall thing standing on
        the desk reaches above it."""
        if not self.workspace:
            return True
        x, y = center(box)[0], box[3]
        x0, y0, x1, y1 = self.workspace
        # ...and a good part of it is on the desk: a lamp or a stand at the back reaches far above it.
        on_desk = (box[3] - max(box[1], y0)) / max(1, box[3] - box[1])
        return x0 <= x <= x1 and y0 <= y <= y1 and on_desk >= 0.35

    def _analyze(self, p: Pending) -> None:
        try:
            self._analyze_inner(p)
        except Exception as err:  # never leave the observer stuck in "analyzing"
            log.exception("analysis failed")
            self.live.last_error = str(err)[:300]
        finally:
            with self.lock:
                self.live.analyzing = False
                self.live.extra["first_running"] = False
                self.done.notify_all()

    def _analyze_inner(self, p: Pending) -> None:
        with self.lock:
            base = self.store.scene(self.baseline_id)
            if p.trigger == "first" and not self.cfg.auto_analyze:
                # Turned on: start a fresh picture of the desk. What was remembered before stays as history
                # ("where did you last see...?") but is not believed to be on the desk now.
                base = None
                self.store.x("UPDATE items SET status='earlier' WHERE status != 'earlier'")
            base_path = self.store.image_path(base)
            prev_items = [i for i in self.store.items(BELIEVED_PRESENT) if i.get("last_box")] if base else []
            if base and not self.cfg.compares:
                # A model that only lists what it sees now: compare with the last look only. A thing it
                # missed earlier is not carried along, to be called "gone" several looks later.
                seen_last = {o["item"] for o in self.store.observations(base["id"])}
                for i in prev_items:
                    if i["id"] not in seen_last:
                        self.store.x("UPDATE items SET status='missing' WHERE id=?", (i["id"],))
                prev_items = [i for i in prev_items if i["id"] in seen_last]
            prev_jpeg = None
            if base_path and prev_items:
                img = cv2.imread(str(base_path))
                prev_jpeg = jpeg(img, 80, self.cfg.analysis_width) if img is not None else None
            base_small = self.baseline_small
        prev_objects = [
            {"id": i["id"], "label": i["label"], "attributes": i["attributes"] or [], "box": i["last_box"]}
            for i in prev_items
        ]
        started = self.clock()
        try:
            # Looks someone is about to wait for (a question, and the before/after of covering the camera) use
            # the quicker model; ordinary background looks the more careful one.
            quick = ("asked", "before_obstruction", "after_obstruction")
            if not self.cfg.auto_analyze:
                quick += ("first",)  # someone may ask "what's in front of you?" right after turning it on
            vision = self.quick_vision if p.trigger in quick else self.vision
            used = vision.model
            if not self.cfg.compares:
                prev_jpeg, prev_objects = None, []  # only the current picture; matched here
            result = vision.analyze(jpeg(p.frame.image, 80, self.cfg.analysis_width), prev_jpeg, prev_objects)
            self._watch(p, result)
            if self.workspace:
                prev_items = [i for i in prev_items if self._inside(i["last_box"])]
        except VisionError as err:
            self.store.log_usage(
                ts=started,
                trigger=p.trigger,
                model=used,
                ok=0,
                seconds=self.clock() - started,
                error=str(err)[:300],
            )
            self.store.event("analysis_failed", {"trigger": p.trigger, "error": str(err)[:300]})
            with self.lock:
                self.live.last_error = str(err)[:300]
                if p.trigger == "first":
                    # No first look yet: nothing to compare with. Try again soon with the view then.
                    self._need_first, self.pending = True, None
                    self._retry_after = self.clock() + 5
                else:
                    self.pending = p if p.trigger != "asked" else None
                    self._retry_after = self.clock() + 60
            return
        u = result.usage
        self.store.log_usage(
            ts=started,
            trigger=p.trigger,
            model=result.model or self.vision.model,
            ok=1,
            seconds=result.seconds,
            input_tokens=u.get("input_tokens"),
            cached_tokens=u.get("cached_input_tokens"),
            output_tokens=u.get("output_tokens"),
        )
        with self.lock:
            diff = difference(self._m(base_small), self._m(p.frame.small)) if base_small is not None else None
            before = p.trigger == "before_obstruction"
            after = self.live.interrupted_at is not None and not before
            sid = self.store.add_scene(
                ts=p.ts,
                camera=self.cfg.camera_id,
                session=self.session_id,
                kind="model",
                trigger=p.trigger,
                visibility=p.frame.visibility.value,
                prev=self.baseline_id if base else None,
                after_obstruction=int(after),
                summary=result.summary,
                model=result.model,
                changed=diff.fraction if diff else None,
                brightness=p.frame.brightness,
                contrast=p.frame.contrast,
                sharpness=p.frame.sharpness,
            )
            self.store.save_image(sid, jpeg(p.frame.image, 85), p.ts)
            self._current_small, self._prev_scene = p.frame.small, (base or {}).get("id")
            self._prev_small = base_small
            self._ref_cache: dict[int, np.ndarray | None] = {}
            n = self.reconcile(sid, p.ts, result, prev_items, diff, p.frame.visibility, first=base is None)
            self.baseline_id, self.baseline_small = sid, p.frame.small
            self._settled = self._last_local = p.frame.small
            self.store.put("baseline", str(sid))
            # "What changed?" answers from the latest comparison that found something (or the one after the
            # camera was obstructed): a quiet re-look, e.g. after someone moved in the background, must not
            # push the real before/after aside.
            found = [c for c in self.store.changes(sid) if c["kind"] not in ("unconfirmed",)]
            if base is not None and (found or after) and not before and p.trigger != "first":
                self.store.put("last_comparison", str(sid))
            if not before:
                self.live.interrupted_at = None
            self.live.last_error = ""
            self.live.extra["looked_at"] = self.clock()
        self.emit({"type": "compared" if base else "scene", "scene": sid, "changes": n, "trigger": p.trigger})

    # Turning the model's list into memory

    def reconcile(
        self,
        sid: int,
        ts: float,
        result: Analysis,
        prev_items: list[dict],
        diff: Difference | None,
        vis: Visibility,
        first: bool = False,
    ) -> int:
        prev = {i["id"]: i for i in prev_items}
        view_shift = diff is not None and diff.fraction > self.cfg.view_shift_fraction
        used: set[int] = set()
        linked: dict[int, dict] = {}  # detection index -> previous item
        for n, det in enumerate(result.objects):  # the model's own matches first
            item = prev.get(det.same) if det.same is not None and det.same not in used else None
            if item is not None:
                used.add(item["id"])
                linked[n] = item
        for n, det in enumerate(result.objects):  # then the same kind of thing
            if n not in linked and (
                item := self._same_kind(det, [p for p in prev_items if p["id"] not in used])
            ):
                used.add(item["id"])
                linked[n] = item
        if not view_shift:  # then the same spot and size: the model only named it differently
            for n, det in enumerate(result.objects):
                if n in linked:
                    continue
                spots = [
                    (iou(p["last_box"], det.box), p)
                    for p in prev_items
                    if p["id"] not in used and p.get("last_box")
                ]
                best = max(spots, key=lambda x: x[0], default=(0.0, None))
                close = best[0] >= 0.5 or (
                    best[1] is not None
                    and best[0] >= 0.25
                    and colours(det.label, det.attributes)
                    & colours(best[1]["label"], best[1].get("attributes") or [])
                )
                if best[1] is not None and close:
                    used.add(best[1]["id"])
                    linked[n] = best[1]
        placed: list[tuple[Detection, int, str]] = []  # detection, item id, what happened
        for n, det in enumerate(result.objects):
            item = linked.get(n)
            if item is not None:
                kind = (
                    "moved"
                    if (not view_shift and item["last_box"] and moved(item["last_box"], det.box))
                    else "stayed"
                )
                placed.append((det, item["id"], kind))
                continue
            back = self._returning(det, exclude=used | set(prev))
            if back is not None:
                used.add(back["id"])
                placed.append((det, back["id"], "returned"))
                continue
            iid = self.store.x(
                "INSERT INTO items(label, category, attributes, status, first_seen, last_seen, camera) "
                "VALUES(?, ?, ?, 'visible', ?, ?, ?)",
                (det.label, det.category, _json(det.attributes), ts, ts, self.cfg.camera_id),
            )
            placed.append((det, iid, "added"))

        boxes = [d.box for d, _, _ in placed]
        labels = [d.label for d, _, _ in placed]
        changes = 0
        for i, (det, iid, kind) in enumerate(placed):
            pos = position(det.box, self.cfg.mirror_for_user)
            near = neighbours(i, boxes, labels)
            old = self.store.item(iid) or {}
            attrs = det.attributes if (det.attributes or not old.get("attributes")) else old["attributes"]
            self.store.x(
                "UPDATE items SET label=?, category=?, attributes=?, status='visible', last_seen=?, last_scene=?, "
                "last_box=?, last_position=?, last_near=?, last_verified=?, status_ts=?, status_scene=? WHERE id=?",
                (
                    det.label,
                    det.category,
                    _json(attrs),
                    ts,
                    sid,
                    _json(det.box),
                    pos,
                    _json(near),
                    ts,
                    ts,
                    sid,
                    iid,
                ),
            )
            self.store.x(
                "INSERT INTO observations(scene, item, label, category, attributes, box, vis, position, near) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    iid,
                    det.label,
                    det.category,
                    _json(det.attributes),
                    _json(det.box),
                    det.vis,
                    pos,
                    _json(near),
                ),
            )
            if first or kind == "stayed":
                continue
            detail: dict[str, Any] = {"label": det.label, "to": pos, "box": det.box}
            if kind == "moved":
                detail.update({"from": old.get("last_position"), "from_box": old.get("last_box")})
            if kind == "returned":
                detail.update({"was": old.get("status"), "last_seen": old.get("last_seen")})
            self._change(sid, ts, kind, iid, detail)
            changes += 1

        # Earlier objects not found now: why can't they be seen?
        new_here = [d.box for d, _, k in placed if k in ("added", "moved", "returned")]
        for item in prev_items:
            if item["id"] in used:
                continue
            status, evidence = self.judge(item, result, diff, vis, view_shift, new_here)
            self.store.x(
                "UPDATE items SET status=?, status_ts=?, status_scene=? WHERE id=?",
                (status, ts, sid, item["id"]),
            )
            if status == "unconfirmed" and item["status"] == "unconfirmed":
                continue
            self._change(
                sid,
                ts,
                status,
                item["id"],
                {
                    "label": item["label"],
                    "at": item["last_position"],
                    "box": item["last_box"],
                    "last_seen": item["last_seen"],
                    **evidence,
                },
            )
            changes += 1
        if view_shift:
            self._change(
                sid, ts, "view_changed", None, {"changed": round(diff.fraction, 2) if diff else None}
            )
        return changes

    def judge(
        self,
        item: dict,
        result: Analysis,
        diff: Difference | None,
        vis: Visibility,
        view_shift: bool,
        new_here: list[list[int]],
    ) -> tuple[str, dict[str, Any]]:
        """Gone from a visible spot, possibly hidden behind something, or just not re-detected?"""
        box = item["last_box"]
        said = result.missing.get(item["id"], "unsure")
        prev_small = getattr(self, "_prev_small", None)
        spot = 1.0
        if prev_small is not None and self._current_small is not None:
            spot = spot_change(prev_small, self._current_small, box)
        # If it wasn't seen in the previous scene (it was hidden or unconfirmed there), its spot must be
        # compared with the picture where it was last seen, not with a picture of whatever was in front of it.
        seen_in = item.get("last_scene")
        hidden_before = bool(seen_in) and seen_in != getattr(self, "_prev_scene", None)
        if hidden_before and self._current_small is not None:
            ref = self._reference(seen_in)
            if ref is not None:
                spot = spot_change(ref, self._current_small, box)
        cover = coverage(box, new_here)
        big = False
        # A large change around a spot that was visible a moment ago: something big arrived in front. (If the
        # object was already hidden, a large change there is more likely whatever hid it going away.)
        if diff is not None and not hidden_before:
            big = any(coverage(box, [r]) >= 0.5 and area(r) >= 3 * area(box) for r in diff.regions)
        evidence = {
            "model": said,
            "spot_changed": round(spot, 2),
            "covered_by_new": round(cover, 2),
            "large_change_around": big,
        }
        if vis is not Visibility.CLEAR or view_shift:
            return "uncertain", evidence
        # The model saw the spot empty and the spot did change somewhat: believe it. Otherwise a spot that
        # looks as before means the model just overlooked the thing.
        if spot < (SPOT_CHANGED / 2 if said == "gone" else SPOT_CHANGED) and cover < COVERED:
            return "unconfirmed", evidence  # its spot looks just as before: it's very likely still there
        # The model saw the spot and says it's empty: only something hiding nearly all of it overrules that
        # (a marker laid across a scissors' old spot hides little). Otherwise a smaller overlap is enough.
        # The model's "hidden" counts only with something in the picture to back it: a new thing overlapping
        # the spot, or a large change around it. (Live, it guessed "hidden" for keys that had been taken away.)
        # A model that only lists what it sees now (local ones: no comparison) didn't see it, and that
        # counts like "the spot is empty": a mouse moved over the bottom of a snow globe's spot doesn't
        # hide the snow globe.
        sure_empty = said == "gone" or (said == "unsure" and not self.cfg.compares)
        if (
            (cover >= (HIDES_ALL if sure_empty else COVERED))
            or (said == "hidden" and cover >= 0.1)
            or (big and not sure_empty)
        ):
            return "possibly_obstructed", evidence
        if said == "gone" or spot >= SPOT_CHANGED:
            return "missing", evidence
        return "uncertain", evidence

    def _reference(self, scene_id: int) -> np.ndarray | None:
        cache = getattr(self, "_ref_cache", {})
        if scene_id not in cache:
            path = self.store.image_path(self.store.scene(scene_id))
            img = cv2.imread(str(path)) if path else None
            cache[scene_id] = assess(img, self.t).small if img is not None else None
            self._ref_cache = cache
        return cache[scene_id]

    @staticmethod
    def _compatible(det: Detection, item: dict) -> bool:
        """Same kind of thing, and no clearly different colour or marking."""
        if not (words(det.category) & (words(item.get("category") or "") | words(item["label"]))):
            return False
        a = colours(det.label, det.attributes)
        b = colours(item["label"], item.get("attributes") or [])
        return not (a and b) or bool(a & b)

    def _same_kind(self, det: Detection, candidates: list[dict]) -> dict | None:
        """The model didn't link it: if exactly one earlier object of the same kind is unaccounted for, it's that."""
        fits = [c for c in candidates if c.get("last_box") and self._compatible(det, c)]
        return fits[0] if len(fits) == 1 else None

    def _returning(self, det: Detection, exclude: set[int]) -> dict | None:
        """A thing not in the last scene that looks like one seen before (and since lost): it's back."""
        want = words(det.label) | words(det.category)
        attrs = words(" ".join(det.attributes))
        best, best_score = None, 0.0
        for item in self.store.items(("missing", "possibly_obstructed", "uncertain")):
            if item["id"] in exclude or not self._compatible(det, item):
                continue
            have = words(item["label"]) | words(item.get("category") or "")
            iattrs = words(" ".join(item.get("attributes") or []))
            score = len(want & have) + len(attrs & iattrs)
            if score > best_score:
                best, best_score = item, score
        return best

    def _change(self, sid: int, ts: float, kind: str, item: int | None, detail: dict[str, Any]) -> None:
        self.store.x(
            "INSERT INTO changes(scene, prev, ts, kind, item, detail) "
            "VALUES(?, (SELECT prev FROM scenes WHERE id=?), ?, ?, ?, ?)",
            (sid, sid, ts, kind, item, _json(detail)),
        )


def _json(v: Any) -> str:
    import json

    return json.dumps(v)
