"""Synthetic desk scenes and a scripted stand-in for the vision model, for repeatable tests."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np
from vismem.camera import Shot
from vismem.vision_model import Analysis, Detection

W, H = 1280, 720


@dataclass(frozen=True)
class Obj:
    label: str
    category: str
    attrs: tuple[str, ...]
    box: tuple[int, int, int, int]  # 0-1000
    colour: tuple[int, int, int]  # BGR as drawn

    def at(self, box: tuple[int, int, int, int]) -> Obj:
        return replace(self, box=box)


def desk(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (70, 110, 150)  # wood-ish
    grain = rng.normal(0, 1, (H, W)).astype(np.float32)
    grain = cv2.GaussianBlur(grain, (0, 0), sigmaX=25, sigmaY=2) * 160
    img = np.clip(img.astype(np.float32) + grain[..., None], 0, 255).astype(np.uint8)
    cv2.line(img, (0, 120), (W, 100), (40, 60, 90), 6)  # back edge of the desk
    return img


def draw(
    objs: list[Obj],
    seed: int = 1,
    light: float = 1.0,
    shift: tuple[int, int] = (0, 0),
    noise: float = 2.0,
    rng_seed: int = 0,
) -> np.ndarray:
    img = desk(seed)
    for o in objs:
        x0, y0, x1, y1 = (
            int(o.box[0] * W / 1000),
            int(o.box[1] * H / 1000),
            int(o.box[2] * W / 1000),
            int(o.box[3] * H / 1000),
        )
        cv2.rectangle(img, (x0, y0), (x1, y1), o.colour, -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (20, 20, 20), 3)
        cv2.line(img, (x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2), (240, 240, 240), 2)  # some inner detail
    if shift != (0, 0):
        m = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
        img = cv2.warpAffine(img, m, (W, H), borderMode=cv2.BORDER_REPLICATE)
    img = img.astype(np.float32) * light
    img += np.random.default_rng(rng_seed).normal(0, noise, img.shape)  # sensor noise
    return np.clip(img, 0, 255).astype(np.uint8)


def shot(img: np.ndarray) -> Shot:
    return Shot(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())


COVERED = Shot(None, "covered")


def dark() -> np.ndarray:
    rng = np.random.default_rng(3)
    return np.clip(rng.normal(12, 3, (H, W, 3)), 0, 255).astype(np.uint8)


class FakeVision:
    """Sees exactly `scene` (minus `blind` labels), links by label, and says `missing_says` for the rest."""

    model = "fake"

    def __init__(self) -> None:
        self.scene: list[Obj] = []
        self.blind: set[str] = set()
        self.missing_says = "gone"
        self.link = True
        self.calls: list[tuple[bool, list[dict]]] = []
        self.workspace: list[int] | None = None

    def analyze(self, current: bytes, previous: bytes | None, previous_objects: list[dict]) -> Analysis:
        self.calls.append((previous is not None, previous_objects))
        by_label = {p["label"]: p["id"] for p in previous_objects}
        dets = []
        for o in self.scene:
            if o.label in self.blind:
                continue
            same = by_label.get(o.label) if self.link else None
            dets.append(Detection(o.label, o.category, list(o.attrs), list(o.box), "f", same))
        seen = {o.label for o in self.scene if o.label not in self.blind}
        missing = {p["id"]: self.missing_says for p in previous_objects if p["label"] not in seen}
        a = Analysis(dets, missing, "a desk", "fake", 0.1, {"input_tokens": 1000, "output_tokens": 100})
        a.workspace = self.workspace
        return a


DESK = [
    Obj("red screwdriver", "screwdriver", ("red handle",), (60, 300, 200, 360), (40, 40, 220)),
    Obj("coffee mug", "mug", ("white",), (250, 250, 330, 420), (235, 235, 235)),
    Obj("keys", "keys", ("silver",), (380, 600, 450, 660), (190, 190, 190)),
    Obj("blue tape", "tape", ("blue",), (500, 280, 570, 380), (200, 90, 30)),
    Obj("green notebook", "notebook", ("green",), (620, 200, 780, 330), (60, 160, 60)),
    Obj("yellow pencil", "pencil", ("yellow",), (820, 620, 960, 650), (40, 220, 230)),
    Obj("black pliers", "pliers", ("black handles",), (100, 520, 250, 600), (30, 30, 30)),
    Obj("orange ball", "ball", ("orange",), (700, 480, 780, 600), (20, 130, 250)),
    Obj("purple stapler", "stapler", ("purple",), (420, 120, 560, 200), (150, 40, 130)),
]
BLACK_BOX = Obj("small black box", "box", ("black",), (800, 300, 900, 420), (15, 15, 15))
CARDBOARD = Obj("cardboard box", "box", ("brown", "cardboard"), (20, 200, 260, 470), (60, 120, 170))
