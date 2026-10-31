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


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_BOX_SLACK = 10  # grid units a box may poke past 0..1000 before it counts as nonsense


def _first_json(raw: str) -> Any:
    """The first JSON array or object in `raw` (fences and surrounding text ignored)."""
    fenced = _FENCE.search(raw)
    if fenced:
        raw = fenced.group(1)
    decoder = json.JSONDecoder()
    for i, char in enumerate(raw):
        if char in "[{":
            try:
                return decoder.raw_decode(raw, i)[0]
            except json.JSONDecodeError:
                continue
    return None


def parse_box_2d(text: str) -> tuple[float, float, float, float] | None:
    """Read Gemma's native box reply and return (x1, y1, x2, y2) on the 0..1000 grid.

    The model answers [{"box_2d": [y1, x1, y2, x2], "label": ...}] (y first); a single
    object is accepted too, and only the first item is used. Corners are put in order.
    Returns None for an empty list, a wrong-length box, non-finite numbers or values
    far outside 0..1000.
    """
    if not text or not text.strip():
        return None
    data = _first_json(text.strip())
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    box = data.get("box_2d")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    values = [_to_float(v) for v in box]
    if any(v is None or not -_BOX_SLACK <= v <= GRID + _BOX_SLACK for v in values):
        return None
    y1, x1, y2, x2 = values
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def box_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def detected_nothing(text: str) -> bool:
    """True when the reply is an empty list: the model says the target is not visible."""
    return bool(text and text.strip()) and _first_json(text.strip()) == []


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


def padded_grid_to_image_px(gx: float, gy: float, box: CropBox, grid: int = GRID) -> tuple[float, float]:
    """Like box_grid_to_image_px, for a crop padded to a square (content top-left).

    The grid then spans the square's side on both axes, not the crop's own width/height.
    """
    side = max(box.width, box.height)
    return box.left + gx / grid * side, box.top + gy / grid * side


def tile_boxes(width: int, height: int, k: int, overlap: float = 0.25) -> list[CropBox]:
    """A k x k grid of tiles covering the image, each grown by `overlap` so that a target
    on a seam is fully inside at least one tile. Row by row, top-left first."""
    if k <= 1:
        return [CropBox(0, 0, width, height)]
    tile_w = min(width, round(width / k * (1 + overlap)))
    tile_h = min(height, round(height / k * (1 + overlap)))
    lefts = [round(i * (width - tile_w) / (k - 1)) for i in range(k)]
    tops = [round(i * (height - tile_h) / (k - 1)) for i in range(k)]
    return [CropBox(left, top, tile_w, tile_h) for top in tops for left in lefts]


def pick_candidate(centers: list[tuple[float, float]], areas: list[float], radius: float) -> int | None:
    """Index of the best of several answers (one per tile), or None if there are none.

    Overlapping tiles that found the same thing agree, so the answer with the most
    other answers within `radius` pixels wins; ties go to the smaller box (more
    specific), then to the earlier tile.
    """
    if not centers:
        return None

    def votes(i: int) -> int:
        cx, cy = centers[i]
        return sum(1 for x, y in centers if math.hypot(x - cx, y - cy) <= radius)

    return min(range(len(centers)), key=lambda i: (-votes(i), areas[i], i))


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