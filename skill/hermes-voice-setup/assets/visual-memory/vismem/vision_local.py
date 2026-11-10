"""Local, deterministic image work: picture quality, stillness, and where two scenes differ.

No model is involved here. The model is asked to look only when this says a steady scene has changed.
Boxes are [x0, y0, x1, y1] in 0-1000 image coordinates (as the camera sees it, not mirrored).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import cv2
import numpy as np

SMALL = (320, 180)  # change detection works on this size
SHARP = (640, 360)  # sharpness is measured on this size


class Visibility(StrEnum):
    CLEAR = "clear"
    UNCERTAIN = "uncertain"
    UNREADABLE = "unreadable"


@dataclass
class Frame:
    image: np.ndarray  # BGR, full size
    small: np.ndarray  # gray, SMALL
    brightness: float
    contrast: float
    sharpness: float
    visibility: Visibility
    why: str = ""


@dataclass
class Thresholds:
    unreadable_contrast: float = 10.0
    unreadable_dark: float = 20.0
    unreadable_bright: float = 245.0
    uncertain_contrast: float = 12.0
    uncertain_sharpness: float = 25.0
    unreadable_sharpness: float = 6.0


def decode(jpeg: bytes) -> np.ndarray | None:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


def assess(image: np.ndarray, t: Thresholds) -> Frame:
    """Measure a picture and say whether it can be trusted."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, SMALL, interpolation=cv2.INTER_AREA)
    brightness = float(small.mean())
    contrast = float(small.std())
    sharp = cv2.resize(gray, SHARP, interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(sharp, cv2.CV_64F).var())
    if contrast < t.unreadable_contrast:
        vis, why = Visibility.UNREADABLE, "no detail (covered, or nothing in view)"
    elif brightness < t.unreadable_dark:
        vis, why = Visibility.UNREADABLE, "too dark"
    elif brightness > t.unreadable_bright:
        vis, why = Visibility.UNREADABLE, "washed out"
    elif sharpness < t.unreadable_sharpness:
        vis, why = Visibility.UNREADABLE, "very blurry"
    elif contrast < t.uncertain_contrast:
        vis, why = Visibility.UNCERTAIN, "low contrast"
    elif sharpness < t.uncertain_sharpness:
        vis, why = Visibility.UNCERTAIN, "blurry"
    else:
        vis, why = Visibility.CLEAR, ""
    return Frame(image, small, brightness, contrast, sharpness, vis, why)


def _normalize(gray: np.ndarray) -> np.ndarray:
    """Even out exposure: the camera's auto-exposure and room light change the whole picture at once."""
    g = cv2.GaussianBlur(gray.astype(np.float32), (5, 5), 0)
    std = float(g.std()) or 1.0
    return (g - float(g.mean())) / std


def still(a: np.ndarray, b: np.ndarray, fraction: float) -> bool:
    """True when two consecutive small frames show the same thing (nobody moving, nothing settling)."""
    d = np.abs(_normalize(a) - _normalize(b))
    return float((d > 0.6).mean()) < fraction


@dataclass
class Difference:
    fraction: float  # share of the frame that changed
    regions: list[list[int]] = field(default_factory=list)  # boxes of changed areas, 0-1000
    shift: tuple[float, float] = (0.0, 0.0)  # how far the view moved, in small-frame pixels
    mask: np.ndarray | None = None  # changed pixels (SMALL), for checking an object's spot

    def changed_in(self, box: list[int]) -> float:
        """Share of a box (0-1000) that changed."""
        if self.mask is None:
            return 0.0
        h, w = self.mask.shape
        x0, y0, x1, y1 = _px(box, w, h)
        cell = self.mask[y0:y1, x0:x1]
        return float(cell.mean()) if cell.size else 0.0


def _px(box: list[int], w: int, h: int) -> tuple[int, int, int, int]:
    x0 = max(0, min(w - 1, int(box[0] * w / 1000)))
    y0 = max(0, min(h - 1, int(box[1] * h / 1000)))
    x1 = max(x0 + 1, min(w, round(box[2] * w / 1000)))
    y1 = max(y0 + 1, min(h, round(box[3] * h / 1000)))
    return x0, y0, x1, y1


def difference(before: np.ndarray, after: np.ndarray, min_region: float = 0.0015) -> Difference:
    """Where the scene changed, ignoring overall light changes and a small nudge of the camera.

    A pixel counts as changed only when both its brightness pattern and its edges changed: a dimmer room
    or a soft shadow changes brightness but hardly any edges.
    """
    a, b = _normalize(before), _normalize(after)
    window = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(a, b, window)
    if 0.5 < abs(dx) + abs(dy) < 25:
        m = np.float32([[1, 0, -dx], [0, 1, -dy]])
        b = cv2.warpAffine(b, m, a.shape[::-1], borderMode=cv2.BORDER_REPLICATE)
    inten = np.abs(a - b) > 0.6

    def edges(g: np.ndarray) -> np.ndarray:
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        return cv2.magnitude(gx, gy)

    ea, eb = edges(a), edges(b)
    edge = np.abs(ea - eb) > 0.9
    edge = cv2.dilate(edge.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    mask = (inten & edge).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    # fill the inside of changed objects (edges change at the outline; a removed object changes inside too)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    h, w = mask.shape
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    regions = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area >= min_region * w * h:
            regions.append(
                [int(x * 1000 / w), int(y * 1000 / h), int((x + bw) * 1000 / w), int((y + bh) * 1000 / h)]
            )
    return Difference(float(mask.mean()), regions, (float(dx), float(dy)), mask.astype(np.float32))


def spot_change(before: np.ndarray, after: np.ndarray, box: list[int]) -> float:
    """How much one object's spot (a box, 0-1000) changed between two small frames, 0..1.

    The whole-scene check is strict on purpose (light and noise must not cost model calls), which also wipes
    out small thin things like a key ring. Here only this spot is compared, on its own terms so that changes
    elsewhere in the picture don't leak in: its pattern (each crop evened out by itself), its texture (keys on
    a plain desk: measured 21 -> 5) and its brightness beyond the picture's overall change.
    """
    h, w = before.shape
    x0, y0, x1, y1 = _px(box, w, h)
    if (x1 - x0) * (y1 - y0) < 6:
        return 0.0
    a = cv2.GaussianBlur(before.astype(np.float32), (3, 3), 0)
    b = cv2.GaussianBlur(after.astype(np.float32), (3, 3), 0)
    ca, cb = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
    sa, sb = float(ca.std()), float(cb.std())
    pattern = float((np.abs((ca - ca.mean()) / max(sa, 8.0) - (cb - cb.mean()) / max(sb, 8.0)) > 0.8).mean())
    ratio = (max(sa, sb) + 1.0) / (min(sa, sb) + 1.0)
    texture = min(1.0, max(0.0, (ratio - 1.5) / 1.5))
    # light dims or brightens everything in proportion: compare the spot's change with the picture's, as ratios
    spot_ratio = (float(cb.mean()) + 5.0) / (float(ca.mean()) + 5.0)
    scene_ratio = (float(b.mean()) + 5.0) / (float(a.mean()) + 5.0)
    shift = abs(np.log(spot_ratio / scene_ratio))
    level = min(1.0, max(0.0, (shift - 0.25) / 0.35))
    return max(pattern, texture, level)


def jpeg(image: np.ndarray, quality: int = 85, width: int | None = None) -> bytes:
    if width and image.shape[1] > width:
        h = int(image.shape[0] * width / image.shape[1])
        image = cv2.resize(image, (width, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("could not encode JPEG")
    return buf.tobytes()
