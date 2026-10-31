"""Aim test: can the model point at things on the screen?  (Throwaway tool. Never clicks.)

For every target in scripts/aim_targets.txt it asks the model in Gemma's native
pointing format (a box_2d on a 0..1000 grid, y first):
  N  native   "Detect: <target>" on the whole screenshot -> a box; its centre is the point
  R  refine   crop around N's centre, zoom in, ask the same question on the crop

With --legacy it asks the old way instead, for comparison:
  P  pixels   the model answers in pixels of the screenshot
  G  grid     the model answers a point on a 0..1000 grid
  R  refine   as above, but around G's answer and with the old point prompt

It saves one image per target with a box and a cross per method (N blue, R green,
P red, G orange) plus results.json, so YOU can judge hit or miss by eye. Nothing is
moved or clicked.

    python scripts/aim_test.py --quick        (first 3 targets)
    python scripts/aim_test.py                (all targets)
    python scripts/aim_test.py --legacy       (old pixel/grid prompts)

Arrange the windows you want tested, start the script, then switch to them: it waits
--delay seconds before capturing (the terminal itself would otherwise be in the shot).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from desktop_ai_agent.input.geometry import (  # noqa: E402
    CropBox,
    box_center,
    box_grid_to_image_px,
    crop_box,
    detected_nothing,
    grid_to_image_px,
    image_px_to_screen,
    parse_box_2d,
    parse_point,
    padded_grid_to_image_px,
    parse_targets,
    pick_candidate,
    slugify,
    snap_to_image,
    tile_boxes,
)

DETECT_PROMPT = (
    "Detect: {target}\n"
    'Reply with JSON only: [{{"box_2d": [y1, x1, y2, x2], "label": "<short name>"}}]\n'
    "Coordinates are normalized to 0-1000, with y first. Return only the best match.\n"
    "If it is not visible in this image, reply []."
)

# ---- legacy prompts (--legacy only) ----
PIXEL_PROMPT = (
    "This is a screenshot of a Windows desktop. Its size is {w}x{h} pixels and (0,0) is its "
    "top-left corner.\nFind: {target}\n"
    'Reply with JSON only: {{"x": <pixel x>, "y": <pixel y>}} - the centre of that element, '
    "in pixels of this {w}x{h} image."
)
GRID_PROMPT = (
    "This is a screenshot of a Windows desktop. Imagine a grid laid over the whole image: (0,0) is "
    "the top-left corner and (1000,1000) is the bottom-right corner, whatever the image size.\n"
    "Find: {target}\n"
    'Reply with JSON only: {{"x": <0-1000>, "y": <0-1000>}} - the centre of that element on this grid.'
)
REFINE_PROMPT = (
    "This is a zoomed crop of a Windows screenshot. Imagine a grid laid over this crop: (0,0) is its "
    "top-left corner and (1000,1000) is its bottom-right corner.\nFind: {target}\n"
    'Reply with JSON only: {{"x": <0-1000>, "y": <0-1000>}} - the centre of that element on this grid.'
)

# letter, name, marker colour (RGB); drawn and listed in this order when present
METHODS = (
    ("N", "native", (50, 100, 235)),
    ("P", "pixels", (225, 50, 50)),
    ("G", "grid", (230, 140, 0)),
    ("R", "refine", (30, 160, 70)),
)

AskFn = Callable[[str, str], str]  # (prompt, base64 png) -> raw model text
Box = tuple[float, float, float, float]  # (x1, y1, x2, y2)
# raw reply -> (point, box) in the answer's own coordinates, or None if unusable
ParseFn = Callable[[str], "tuple[tuple[float, float], Box | None] | None"]


@dataclass
class Shot:
    """One measurement: what the model said and where that lands."""

    method: str
    raw: str = ""
    seconds: float = 0.0
    box: Box | None = None  # as the model answered: 0..1000 grid (of the image or the crop)
    center: tuple[float, float] | None = None  # same coordinates as box
    box_px: tuple[int, int, int, int] | None = None  # box in screenshot image pixels
    image_px: tuple[int, int] | None = None
    screen_px: tuple[int, int] | None = None
    note: str = ""  # "", "not found", "no point in reply", "no box in reply", "outside image", or an error
    tiles: list[dict[str, Any]] = field(default_factory=list)  # --tiles: every tile's answer


def parse_native(raw: str) -> tuple[tuple[float, float], Box] | None:
    box = parse_box_2d(raw)
    return None if box is None else (box_center(box), box)


def parse_legacy(raw: str) -> tuple[tuple[float, float], None] | None:
    point = parse_point(raw)
    return None if point is None else (point, None)


def _b64_of_image(image: Any) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def make_ask(model: str, host: str, num_ctx: int) -> AskFn:
    """A fresh, history-free model call per question, so earlier images never pile up."""
    from desktop_ai_agent.core.chat_client import ChatClient

    def ask(prompt: str, b64: str) -> str:
        client = ChatClient(
            model,
            host,
            think=False,
            # Plain JSON mode. Never None: ChatClient maps None to its script/code schema.
            response_format="json",
            system="",  # no system prompt: the question is self-contained
            normalize=False,
            options={"temperature": 0, "num_ctx": num_ctx},
        )
        client.add_user(prompt, images=[b64])
        return client.chat().content

    return ask


def _measure(
    method: str,
    prompt: str,
    b64: str,
    ask: AskFn,
    to_image: Callable[[float, float], tuple[float, float]],
    size: tuple[int, int],
    origin: tuple[int, int],
    parse: ParseFn = parse_native,
    bounds: CropBox | None = None,
) -> Shot:
    """Ask once and map the answer to image pixels. With `bounds`, a point outside that
    part of the image (e.g. in the padding of a padded tile) counts as outside."""
    shot = Shot(method=method)
    started = time.perf_counter()
    try:
        shot.raw = ask(prompt, b64)
    except Exception as exc:  # noqa: BLE001 - record and carry on with the next question
        shot.note = f"error: {exc}"
    shot.seconds = round(time.perf_counter() - started, 2)
    if shot.note:
        return shot

    parsed = parse(shot.raw)
    if parsed is None:
        if parse is parse_native:
            shot.note = "not found" if detected_nothing(shot.raw) else "no box in reply"
        else:
            shot.note = "no point in reply"
        return shot
    point, box = parsed
    shot.center, shot.box = point, box
    if box is not None:
        x1, y1 = to_image(box[0], box[1])
        x2, y2 = to_image(box[2], box[3])
        shot.box_px = (round(x1), round(y1), round(x2), round(y2))
    px, py = to_image(*point)
    if bounds is not None and not (
        bounds.left - 1 <= px <= bounds.right + 1 and bounds.top - 1 <= py <= bounds.bottom + 1
    ):
        shot.note = "outside image"
        return shot
    snapped = snap_to_image(px, py, *size)
    if snapped is None:
        shot.note = "outside image"
        return shot
    shot.image_px = snapped
    shot.screen_px = image_px_to_screen(*snapped, *origin)
    return shot


def _view(full: Any, box: CropBox, zoom: int, pad: bool) -> tuple[Any, Callable[[float, float], tuple[float, float]]]:
    """The image sent to the model for `box` (cropped, zoomed, optionally padded to a
    square with the content top-left) and the function mapping its 0..1000 grid back
    to screenshot pixels."""
    from PIL import Image

    view = full.crop(box.as_tuple())
    if zoom != 1:
        view = view.resize((box.width * zoom, box.height * zoom), Image.LANCZOS)
    if pad and view.width != view.height:
        square = Image.new("RGB", (max(view.size), max(view.size)), (0, 0, 0))
        square.paste(view, (0, 0))
        view = square
    mapper = padded_grid_to_image_px if pad else box_grid_to_image_px

    def to_image(gx: float, gy: float) -> tuple[float, float]:
        return mapper(gx, gy, box)

    return view, to_image


def _measure_box(
    method: str,
    prompt: str,
    full: Any,
    box: CropBox,
    zoom: int,
    pad: bool,
    ask: AskFn,
    size: tuple[int, int],
    origin: tuple[int, int],
    parse: ParseFn,
) -> Shot:
    view, to_image = _view(full, box, zoom, pad)
    return _measure(method, prompt, _b64_of_image(view), ask, to_image, size, origin, parse, bounds=box)


def _box_area(box_px: tuple[int, int, int, int] | None) -> float:
    return 0.0 if box_px is None else abs(box_px[2] - box_px[0]) * abs(box_px[3] - box_px[1])


def _measure_tiled(
    prompt: str,
    full: Any,
    tiles: list[CropBox],
    pad: bool,
    ask: AskFn,
    size: tuple[int, int],
    origin: tuple[int, int],
) -> Shot:
    """N on every tile; the tiles that found something vote (see pick_candidate)."""
    per_tile = [_measure_box("N", prompt, full, t, 1, pad, ask, size, origin, parse_native) for t in tiles]
    found = [s for s in per_tile if s.image_px is not None]
    radius = max(30.0, min(size) / 20)
    best = pick_candidate([s.image_px for s in found], [_box_area(s.box_px) for s in found], radius)
    if best is not None:
        chosen = found[best]
    else:
        errors = [s.note for s in per_tile if s.note.startswith("error")]
        chosen = Shot("N", note=errors[0] if len(errors) == len(per_tile) else "not found")
    chosen.seconds = round(sum(s.seconds for s in per_tile), 2)
    chosen.tiles = [
        {
            "tile": i,
            "crop": list(t.as_tuple()),
            "raw": s.raw,
            "box_px": s.box_px,
            "image_px": s.image_px,
            "note": s.note,
            "chosen": s is chosen,
        }
        for i, (t, s) in enumerate(zip(tiles, per_tile))
    ]
    return chosen


def _summary(shots: dict[str, Shot]) -> str:
    parts = []
    for letter, _name, _color in METHODS:
        shot = shots.get(letter)
        if shot is not None:
            parts.append(f"{letter}={shot.note or shot.image_px}")
    return "  ".join(parts)


def draw_markers(image: Any, target: str, shots: dict[str, Shot]) -> None:
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    width, height = image.size
    radius = max(8, round(min(width, height) / 120))
    for shot in shots.values():  # answers from tiles that lost the vote: thin grey
        for tile in shot.tiles:
            if not tile["chosen"] and tile["box_px"] is not None:
                draw.rectangle(tile["box_px"], outline=(150, 150, 150), width=1)
    for letter, name, color in METHODS:
        shot = shots.get(letter)
        if shot is None or shot.image_px is None:
            continue
        if shot.box_px is not None:
            draw.rectangle(shot.box_px, outline=color, width=2)
        x, y = shot.image_px
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=color, width=3)
        draw.line([x - radius * 1.6, y, x + radius * 1.6, y], fill=color, width=2)
        draw.line([x, y - radius * 1.6, x, y + radius * 1.6], fill=color, width=2)
        draw.text((x + radius + 4, y - radius), letter, fill=color, font=font)

    lines: list[tuple[str, tuple[int, int, int]]] = [(f"target: {target}", (255, 255, 255))]
    for letter, name, color in METHODS:
        shot = shots.get(letter)
        if shot is not None:
            lines.append((f"{letter} {name}: {shot.note or shot.image_px}", color))
    box_h = 14 * len(lines) + 8
    draw.rectangle([0, 0, 420, box_h], fill=(0, 0, 0))
    for i, (line, color) in enumerate(lines):
        draw.text((6, 4 + 14 * i), line, fill=color, font=font)


def run_aim_test(
    targets: list[str],
    *,
    capture: Callable[[], dict[str, Any]],
    ask: AskFn,
    out_dir: Path,
    crop_size: int = 400,
    zoom: int = 2,
    refine: bool = True,
    legacy: bool = False,
    tiles: int = 1,
    pad: bool = False,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Capture once, then measure every target. Returns (and saves) the results."""
    from PIL import Image

    cap = capture()
    width, height = int(cap["width"]), int(cap["height"])
    origin = (int(cap.get("left", 0)), int(cap.get("top", 0)))
    out_dir.mkdir(parents=True, exist_ok=True)
    full = Image.open(cap["path"]).convert("RGB")
    full_b64 = _b64_of_image(full)
    whole = CropBox(0, 0, width, height)
    tile_list = tile_boxes(width, height, tiles)
    log(f"Captured {width}x{height} at screen position {origin}; saving to {out_dir}")

    results: list[dict[str, Any]] = []
    for n, target in enumerate(targets, 1):
        shots: dict[str, Shot] = {}
        fmt = {"w": width, "h": height, "target": target}

        on_grid = lambda gx, gy: grid_to_image_px(gx, gy, width, height)  # noqa: E731

        if legacy:
            shots["P"] = _measure("P", PIXEL_PROMPT.format(**fmt), full_b64, ask,
                                  lambda x, y: (x, y), (width, height), origin, parse_legacy)
            shots["G"] = _measure("G", GRID_PROMPT.format(**fmt), full_b64, ask,
                                  on_grid, (width, height), origin, parse_legacy)
            anchor, refine_prompt, refine_parse = shots["G"], REFINE_PROMPT, parse_legacy
        elif tiles > 1:
            shots["N"] = _measure_tiled(DETECT_PROMPT.format(**fmt), full, tile_list, pad, ask,
                                        (width, height), origin)
            anchor, refine_prompt, refine_parse = shots["N"], DETECT_PROMPT, parse_native
        else:
            shots["N"] = _measure_box("N", DETECT_PROMPT.format(**fmt), full, whole, 1, pad, ask,
                                      (width, height), origin, parse_native)
            anchor, refine_prompt, refine_parse = shots["N"], DETECT_PROMPT, parse_native

        crop_file = None
        if refine and anchor.image_px is not None:
            box = crop_box(*anchor.image_px, width, height, crop_size)
            use_pad = pad and not legacy
            crop, _ = _view(full, box, zoom, use_pad)
            crop_file = out_dir / f"{n:02d}_{slugify(target)}_crop.png"
            crop.save(crop_file)
            shots["R"] = _measure_box("R", refine_prompt.format(**fmt), full, box, zoom, use_pad, ask,
                                      (width, height), origin, refine_parse)

        canvas = full.copy()
        draw_markers(canvas, target, shots)
        marked = out_dir / f"{n:02d}_{slugify(target)}.png"
        canvas.save(marked)

        row = {
            "n": n,
            "target": target,
            "image": marked.name,
            "crop": crop_file.name if crop_file else None,
            "shots": {letter: asdict(shot) for letter, shot in shots.items()},
        }
        results.append(row)
        log(f"[{n}/{len(targets)}] {target!r}: {_summary(shots)}")

    payload = {
        "capture": {"width": width, "height": height, "left": origin[0], "top": origin[1]},
        "crop_size": crop_size,
        "zoom": zoom,
        "mode": "legacy" if legacy else "native",
        "tiles": 1 if legacy else tiles,
        "pad": pad and not legacy,
        "results": results,
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure how well the model points at screen targets.")
    parser.add_argument("--targets", default=str(ROOT / "scripts" / "aim_targets.txt"))
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--quick", action="store_true", help="only the first 3 targets")
    parser.add_argument("--no-refine", action="store_true", help="skip the zoom-in pass")
    parser.add_argument("--legacy", action="store_true", help="old pixel/grid point prompts instead of box_2d")
    parser.add_argument("--tiles", type=int, default=1,
                        help="first guess on N x N overlapping tiles instead of the whole screen (native only)")
    parser.add_argument("--pad", action="store_true",
                        help="pad images to a square before sending so they are not squashed (native only)")
    parser.add_argument("--crop", type=int, default=400, help="crop size in pixels for the refine pass")
    parser.add_argument("--zoom", type=int, default=2, help="how much the crop is enlarged")
    parser.add_argument("--delay", type=float, default=5.0, help="seconds to wait before capturing")
    parser.add_argument("--num-ctx", type=int, default=8192)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    from desktop_ai_agent.action.tools.screenshot import capture_screen
    from desktop_ai_agent.core.monitors import enable_dpi_awareness, report_at_setup

    enable_dpi_awareness()  # before any capture, so image pixels are real screen pixels
    report_at_setup(print)  # shows monitors and warns if capture and Windows disagree on size

    targets_path = Path(args.targets)
    if not targets_path.is_file():
        print(f"Targets file not found: {targets_path}")
        return 1
    targets = parse_targets(targets_path.read_text(encoding="utf-8"))
    if args.quick:
        targets = targets[:3]
    if not targets:
        print(f"No targets in {targets_path} (one description per line).")
        return 1

    out_dir = ROOT / "logs" / "aim_test" / datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"{len(targets)} target(s). Capturing in {args.delay:g}s - switch to the windows to test...")
    time.sleep(max(0.0, args.delay))

    results = run_aim_test(
        targets,
        capture=capture_screen,
        ask=make_ask(args.model, args.host, args.num_ctx),
        out_dir=out_dir,
        crop_size=args.crop,
        zoom=args.zoom,
        refine=not args.no_refine,
        legacy=args.legacy,
        tiles=max(1, args.tiles),
        pad=args.pad,
    )

    calls = [s for row in results for s in row["shots"].values()]
    if calls and all(s["note"].startswith("error") for s in calls):
        print("Every model call failed. Is Ollama running, and is the model name right (--model)?")
        print(calls[0]["note"])
        return 1

    print(f"\nDone. Open the images in: {out_dir}")
    print("Judge each one: is the marker on the target?  P = pixels (red), G = grid (blue), R = refine (green).")
    print("Then tell me the hit counts per method, e.g.  P 6/10, G 8/10, R 9/10.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())