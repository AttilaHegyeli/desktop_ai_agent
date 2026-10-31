"""Aim test: can the model point at things on the screen?  (Throwaway tool. Never clicks.)

For every target in scripts/aim_targets.txt it asks the model for a point three ways:
  P  pixels   the model answers in pixels of the screenshot
  G  grid     the model answers on a 0..1000 grid laid over the screenshot
  R  refine   crop around the grid answer, zoom in, ask again on the crop

It saves one image per target with a marker per method (P red, G blue, R green) plus
results.json, so YOU can judge hit or miss by eye. Nothing is moved or clicked.

    python scripts/aim_test.py --quick        (first 3 targets)
    python scripts/aim_test.py                (all targets)

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
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from desktop_ai_agent.input.geometry import (  # noqa: E402
    box_grid_to_image_px,
    crop_box,
    grid_to_image_px,
    image_px_to_screen,
    parse_point,
    parse_targets,
    slugify,
    snap_to_image,
)

POINT_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
    "required": ["x", "y"],
}

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

# letter, name, marker colour (RGB)
METHODS = (("P", "pixels", (225, 50, 50)), ("G", "grid", (50, 100, 235)), ("R", "refine", (30, 160, 70)))

AskFn = Callable[[str, str], str]  # (prompt, base64 png) -> raw model text


@dataclass
class Shot:
    """One measurement: what the model said and where that lands."""

    method: str
    raw: str = ""
    seconds: float = 0.0
    image_px: tuple[int, int] | None = None
    screen_px: tuple[int, int] | None = None
    note: str = ""  # "", "no point in reply", "outside image", or an error message


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
            response_format=POINT_SCHEMA,  # reply must be JSON with x and y
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
) -> Shot:
    shot = Shot(method=method)
    started = time.perf_counter()
    try:
        shot.raw = ask(prompt, b64)
    except Exception as exc:  # noqa: BLE001 - record and carry on with the next question
        shot.note = f"error: {exc}"
    shot.seconds = round(time.perf_counter() - started, 2)
    if shot.note:
        return shot

    point = parse_point(shot.raw)
    if point is None:
        shot.note = "no point in reply"
        return shot
    snapped = snap_to_image(*to_image(*point), *size)
    if snapped is None:
        shot.note = "outside image"
        return shot
    shot.image_px = snapped
    shot.screen_px = image_px_to_screen(*snapped, *origin)
    return shot


def _summary(shots: dict[str, Shot]) -> str:
    parts = []
    for letter, _name, _color in METHODS:
        shot = shots.get(letter)
        parts.append(f"{letter}={'-' if shot is None else (shot.note or shot.image_px)}")
    return "  ".join(parts)


def draw_markers(image: Any, target: str, shots: dict[str, Shot]) -> None:
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    width, height = image.size
    radius = max(8, round(min(width, height) / 120))
    for letter, name, color in METHODS:
        shot = shots.get(letter)
        if shot is None or shot.image_px is None:
            continue
        x, y = shot.image_px
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=color, width=3)
        draw.line([x - radius * 1.6, y, x + radius * 1.6, y], fill=color, width=2)
        draw.line([x, y - radius * 1.6, x, y + radius * 1.6], fill=color, width=2)
        draw.text((x + radius + 4, y - radius), letter, fill=color, font=font)

    lines = [f"target: {target}"]
    for letter, name, color in METHODS:
        shot = shots.get(letter)
        status = "-" if shot is None else (shot.note or f"{shot.image_px}")
        lines.append(f"{letter} {name}: {status}")
    box_h = 14 * len(lines) + 8
    draw.rectangle([0, 0, 420, box_h], fill=(0, 0, 0))
    for i, line in enumerate(lines):
        color = (255, 255, 255) if i == 0 else METHODS[i - 1][2]
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
    log(f"Captured {width}x{height} at screen position {origin}; saving to {out_dir}")

    results: list[dict[str, Any]] = []
    for n, target in enumerate(targets, 1):
        shots: dict[str, Shot] = {}
        fmt = {"w": width, "h": height, "target": target}

        shots["P"] = _measure("P", PIXEL_PROMPT.format(**fmt), full_b64, ask,
                              lambda x, y: (x, y), (width, height), origin)
        shots["G"] = _measure("G", GRID_PROMPT.format(**fmt), full_b64, ask,
                              lambda gx, gy: grid_to_image_px(gx, gy, width, height), (width, height), origin)

        crop_file = None
        if refine and shots["G"].image_px is not None:
            box = crop_box(*shots["G"].image_px, width, height, crop_size)
            crop = full.crop(box.as_tuple()).resize((box.width * zoom, box.height * zoom), Image.LANCZOS)
            crop_file = out_dir / f"{n:02d}_{slugify(target)}_crop.png"
            crop.save(crop_file)
            shots["R"] = _measure("R", REFINE_PROMPT.format(**fmt), _b64_of_image(crop), ask,
                                  lambda gx, gy, b=box: box_grid_to_image_px(gx, gy, b),
                                  (width, height), origin)

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