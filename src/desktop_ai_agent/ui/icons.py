"""Smooth SVG icons for tkinter (MuPDF rasterization + opaque PhotoImage)."""
from __future__ import annotations

import base64
import io
import re
import tkinter as tk
from functools import lru_cache
from pathlib import Path

_ICONS_DIR = Path(__file__).resolve().parent / "assets" / "icons"


def icon_path(name: str) -> Path:
    return _ICONS_DIR / f"{name}.svg"


def _parse_hex(color: str) -> tuple[int, int, int]:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _recolor(svg: str, color: str) -> str:
    svg = re.sub(r"<!DOCTYPE[^>]*>", "", svg, flags=re.I)
    svg = re.sub(r'fill="#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})"', f'fill="{color}"', svg)
    svg = re.sub(r'stroke="#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})"', f'stroke="{color}"', svg)
    return svg


@lru_cache(maxsize=256)
def _rgba_icon(name: str, size: int, color: str) -> bytes:
    """Raw PNG bytes of the recolored SVG with transparency (high-DPI)."""
    import pymupdf
    from PIL import Image

    path = icon_path(name)
    if not path.is_file():
        raise FileNotFoundError(path)

    svg = _recolor(path.read_text(encoding="utf-8"), color)
    doc = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg")
    try:
        page = doc[0]
        # Render at 4× for clean edges, then downscale.
        target = max(1, int(size)) * 4
        scale = target / max(page.rect.width, page.rect.height, 1.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
        img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        alpha = img.getchannel("A")
        bbox = alpha.getbbox()
        if bbox:
            img = img.crop(bbox)
        side = max(img.size)
        # Small padding so strokes are not clipped after downscale.
        pad = max(2, side // 16)
        square = Image.new("RGBA", (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
        square.paste(img, ((square.width - img.width) // 2, (square.height - img.height) // 2))
        out = square.resize((size, size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


@lru_cache(maxsize=512)
def _png_on_bg(name: str, size: int, color: str, bg: str) -> bytes:
    """Composite icon onto an opaque background — required for clean Tk PhotoImage."""
    from PIL import Image

    icon = Image.open(io.BytesIO(_rgba_icon(name, size, color))).convert("RGBA")
    base = Image.new("RGBA", (size, size), (*_parse_hex(bg), 255))
    base.alpha_composite(icon)
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def photo_icon(
    master: tk.Misc,
    name: str,
    *,
    size: int = 16,
    color: str = "#000000",
    bg: str = "#ffffff",
) -> tk.PhotoImage:
    """Crisp PhotoImage for assets/icons/{name}.svg, baked onto `bg`."""
    data = _png_on_bg(name, int(size), color.lower(), bg.lower())
    return tk.PhotoImage(master=master, data=base64.b64encode(data))
