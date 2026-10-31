"""Pure coordinate helpers for aiming at the screen. No I/O, no Windows calls.

Vocabulary
  image pixel   (x, y) inside a screenshot; origin = the image's top-left corner
  grid point    (gx, gy) on a 0..1000 grid laid over a whole image or over a crop
  screen pixel  real desktop coordinates; the origin can be negative on multi-monitor setups

Used by scripts/aim_test.py now and by the mouse tool later.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

GRID = 1000

_NUM = r"-?\d+(?:\.\d+)?"
_XY = re.compile(rf"\bx\W{{0,6}}({_NUM})\W{{1,10}}y\W{{0,6}}({_NUM})", re.IGNORECASE)
_NOT_FOUND = re.compile(
    r"not\s+(found|visible|present)|cannot\s+find|can't\s+find|unable\s+to|no\s+such",
    re.IGNORECASE,
)


# ---- reading the model's reply -------------------------------------------------

def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and re.fullmatch(_NUM, value.strip()):
        number = float(value.strip())
    else:
        return None
    return number if math.isfinite(number) else None


def _point_from_mapping(data: dict[str, Any]) -> tuple[float, float] | None:
    x, y = _to_float(data.get("x")), _to_float(data.get("y"))
    if x is not None and y is not None:
        return x, y
    for key in ("point", "coordinates", "center"):
        value = data.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            x, y = _to_float(value[0]), _to_float(value[1])
            if x is not None and y is not None:
                return x, y
    return None


def parse_point(text: str) -> tuple[float, float] | None:
    """Extract an (x, y) pair from a model reply, or None if it gave no usable point.

    Accepts {"x": 1, "y": 2}, {"point": [1, 2]}, "x=1, y=2", "(1, 2)" and "[1, 2]".
    A reply that says the target was not found returns None.
    """
    if not text or not text.strip():
        return None
    raw = text.strip()

    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if data.get("found") is False:
                return None
            point = _point_from_mapping(data)
            if point is not None:
                return point

    match = _XY.search(raw)
    if match:
        return float(match.group(1)), float(match.group(2))
    if _NOT_FOUND.search(raw):
        return None
    numbers = re.findall(_NUM, raw)
    if len(numbers) >= 2:
        return float(numbers[0]), float(numbers[1])
    return None


# ---- converting between coordinate systems -------------------------------------

def grid_to_image_px(gx: float, gy: float, width: int, height: int, grid: int = GRID) -> tuple[float, float]:
    """0..grid point over a whole image -> image pixel (float, not clamped)."""
    return gx / grid * width, gy / grid * height


def snap_to_image(x: float, y: float, width: int, height: int, tolerance: float = 1.0) -> tuple[int, int] | None:
    """Round to an integer pixel inside the image, or None if clearly outside.

    Points up to `tolerance` pixels past an edge are pulled onto it (a grid value of
    exactly 1000 lands on the last pixel); anything further out is rejected.
    """
    if not (-tolerance <= x <= width + tolerance and -tolerance <= y <= height + tolerance):
        return None
    return max(0, min(width - 1, round(x))), max(0, min(height - 1, round(y)))


def image_px_to_screen(x: float, y: float, left: int, top: int) -> tuple[int, int]:
    """Image pixel -> real screen pixel, using the screenshot's top-left screen position."""
    return round(left + x), round(top + y)


def screen_to_image_px(sx: float, sy: float, left: int, top: int) -> tuple[int, int]:
    return round(sx - left), round(sy - top)


@dataclass(frozen=True)
class CropBox:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def as_tuple(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom), the order Pillow's crop() expects."""
        return self.left, self.top, self.right, self.bottom


def crop_box(cx: float, cy: float, img_w: int, img_h: int, size: int) -> CropBox:
    """A size x size box centred on (cx, cy), shifted to stay inside the image.

    If the image is smaller than `size` in a direction, the box covers that whole direction.
    """
    width, height = min(size, img_w), min(size, img_h)
    left = max(0, min(img_w - width, round(cx - width / 2)))
    top = max(0, min(img_h - height, round(cy - height / 2)))
    return CropBox(left, top, width, height)


def box_grid_to_image_px(gx: float, gy: float, box: CropBox, grid: int = GRID) -> tuple[float, float]:
    """0..grid point over a crop -> image pixel of the original screenshot (float)."""
    return box.left + gx / grid * box.width, box.top + gy / grid * box.height


# ---- small text helpers for the aim script ---------------------------------------

def parse_targets(text: str) -> list[str]:
    """One target per line; blank lines and lines starting with # are ignored."""
    targets: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            targets.append(line)
    return targets


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_len].strip("_")
    return slug or "target"