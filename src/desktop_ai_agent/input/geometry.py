"""Pure coordinate helpers for pointing at the screen. No I/O, no Windows calls.

Vocabulary
  image pixel   (x, y) inside a screenshot; origin = the image's top-left corner
  screen pixel  real desktop coordinates; the origin can be negative on multi-monitor setups

A screenshot's result carries `left`/`top` (its top-left corner in screen pixels), so
image pixel (x, y) is screen pixel (left + x, top + y).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


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


# ---- small text helpers for the probe script --------------------------------------

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
