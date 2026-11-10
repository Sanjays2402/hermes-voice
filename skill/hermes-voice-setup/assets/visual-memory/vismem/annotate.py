"""Evidence pictures: the stored snapshots themselves, with boxes drawn on them. Nothing is generated.

Shown as the user sees the scene (mirrored) when mirror_for_user is on, like the video-mode window.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from .store import Store

# BGR
COLOURS = {
    "added": (80, 200, 60),
    "returned": (80, 200, 60),
    "moved": (0, 200, 255),
    "missing": (60, 60, 230),
    "possibly_obstructed": (0, 140, 255),
    "uncertain": (180, 180, 180),
    "unconfirmed": (200, 200, 200),
    "item": (0, 200, 255),
}
WORDS = {
    "added": "new",
    "returned": "back",
    "moved": "moved",
    "missing": "not seen",
    "possibly_obstructed": "may be hidden",
    "uncertain": "unclear",
    "unconfirmed": "unchanged",
}


def _load(store: Store, scene: dict | None, mirror: bool) -> np.ndarray | None:
    path = store.image_path(scene)
    img = cv2.imread(str(path)) if path else None
    if img is not None and mirror:
        img = cv2.flip(img, 1)
    return img


def _rect(
    img: np.ndarray,
    box: list[int],
    colour: tuple[int, int, int],
    text: str,
    mirror: bool,
    dashed: bool = False,
) -> None:
    h, w = img.shape[:2]
    x0, x1 = (1000 - box[2], 1000 - box[0]) if mirror else (box[0], box[2])
    p0 = (int(x0 * w / 1000), int(box[1] * h / 1000))
    p1 = (int(x1 * w / 1000), int(box[3] * h / 1000))
    t = max(2, w // 320)
    if dashed:
        for i in range(p0[0], p1[0], 16):
            cv2.line(img, (i, p0[1]), (min(i + 8, p1[0]), p0[1]), colour, t)
            cv2.line(img, (i, p1[1]), (min(i + 8, p1[0]), p1[1]), colour, t)
        for i in range(p0[1], p1[1], 16):
            cv2.line(img, (p0[0], i), (p0[0], min(i + 8, p1[1])), colour, t)
            cv2.line(img, (p1[0], i), (p1[0], min(i + 8, p1[1])), colour, t)
    else:
        cv2.rectangle(img, p0, p1, colour, t)
    if text:
        scale = w / 1400
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, t - 1))
        y = p0[1] - 6 if p0[1] - th - 10 > 0 else p1[1] + th + 8
        cv2.rectangle(img, (p0[0], y - th - 6), (p0[0] + tw + 8, y + 4), colour, -1)
        cv2.putText(
            img,
            text,
            (p0[0] + 4, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (20, 20, 20),
            max(1, t - 1),
            cv2.LINE_AA,
        )


def _caption(img: np.ndarray, text: str) -> np.ndarray:
    w = img.shape[1]
    bar = np.full((max(36, w // 22), w, 3), 24, np.uint8)
    cv2.putText(
        bar,
        text,
        (12, bar.shape[0] - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        w / 1500,
        (240, 240, 240),
        max(1, w // 640),
        cv2.LINE_AA,
    )
    return np.vstack([bar, img])


def _stamp(ts: float) -> str:
    return time.strftime("%-I:%M:%S %p, %b %-d", time.localtime(ts))


def comparison(store: Store, scene_id: int, mirror: bool, width: int = 1280) -> bytes | None:
    """Before | after, with what changed boxed on each side."""
    scene = store.scene(scene_id)
    prev = store.scene(scene["prev"]) if scene else None
    after, before = _load(store, scene, mirror), _load(store, prev, mirror)
    if after is None:
        return None
    for c in store.changes(scene_id):
        d = c["detail"] or {}
        k = c["kind"]
        label = f"{d.get('label', '')}: {WORDS.get(k, k)}"
        if k in ("added", "returned") and d.get("box"):
            _rect(after, d["box"], COLOURS[k], label, mirror)
        elif k == "moved":
            if d.get("box"):
                _rect(after, d["box"], COLOURS[k], label, mirror)
            if before is not None and d.get("from_box"):
                _rect(
                    before, d["from_box"], COLOURS[k], f"{d.get('label', '')}: was here", mirror, dashed=True
                )
        elif k in ("missing", "possibly_obstructed", "uncertain") and d.get("box"):
            if before is not None:
                _rect(before, d["box"], COLOURS[k], f"{d.get('label', '')}", mirror)
            _rect(after, d["box"], COLOURS[k], label, mirror, dashed=True)
    after = _caption(after, f"After  {_stamp(scene['ts'])}")
    if before is None:
        out = after
    else:
        before = _caption(before, f"Before  {_stamp(prev['ts'])}")
        out = np.hstack([before, np.full((before.shape[0], 8, 3), 255, np.uint8), after])
    if out.shape[1] > width:
        out = cv2.resize(out, (width, int(out.shape[0] * width / out.shape[1])), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def item_evidence(store: Store, item: dict, mirror: bool, width: int = 1280) -> bytes | None:
    """The snapshot where the item was last seen, with it boxed."""
    scene = store.scene(item.get("last_scene"))
    img = _load(store, scene, mirror)
    if img is None or not item.get("last_box"):
        return None
    _rect(img, item["last_box"], COLOURS["item"], item["label"], mirror)
    img = _caption(img, f"{item['label']}: last seen {_stamp(item['last_seen'])}")
    if img.shape[1] > width:
        img = cv2.resize(img, (width, int(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def scene_picture(store: Store, scene_id: int, mirror: bool) -> bytes | None:
    scene = store.scene(scene_id)
    img = _load(store, scene, mirror)
    if img is None:
        return None
    img = _caption(img, _stamp(scene["ts"]))
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def watching(store: Store, scene_id: int, workspace: list[int] | None, mirror: bool, width: int = 1280) -> bytes | None:
    """The first look: what is being watched (the desk outlined) and the things on it, boxed."""
    scene = store.scene(scene_id)
    img = _load(store, scene, mirror)
    if img is None:
        return None
    if workspace:
        _rect(img, workspace, (255, 255, 255), "", mirror, dashed=True)
    obs = store.observations(scene_id)
    for o in obs:
        if o.get("box"):
            _rect(img, o["box"], COLOURS["item"], o["label"], mirror)
    where = "your desk" if workspace else "the whole view"
    img = _caption(img, f"Watching {where}: {len(obs)} thing{'s' if len(obs) != 1 else ''}  {_stamp(scene['ts'])}")
    if img.shape[1] > width:
        img = cv2.resize(img, (width, int(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
