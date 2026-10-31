"""UI Automation probe: what can Windows tell us about the screen, and can the model pick
the right element by name?  (Throwaway measuring tool. Read-only. Never clicks.)

1. Captures the screen and scans the clickable elements via UI Automation.
2. Saves elements.png: every element as a numbered yellow box (shows what UIA can see).
3. For each target in scripts/aim_targets.txt, sends the model ONLY the text list (no
   image) and asks which number it is. The pick is drawn as a green box + cross.

    python scripts/ui_probe.py              (all targets)
    python scripts/ui_probe.py --quick      (first 3 targets)
    python scripts/ui_probe.py --scan-only  (no model: just the element map)

Arrange the windows, start the script, then switch to them within --delay seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from desktop_ai_agent.input.geometry import parse_targets, screen_to_image_px, slugify  # noqa: E402
from desktop_ai_agent.input.ui_elements import (  # noqa: E402
    UiElement,
    find_element,
    format_for_model,
    list_ui_elements,
    parse_choice,
)

PICK_PROMPT = (
    "Below is a numbered list of the clickable elements on a Windows screen: "
    "number, element type, name, and (in brackets) the window or area it belongs to.\n\n"
    "{elements}\n\n"
    "Which element is: {target}\n"
    'Reply with JSON only: {{"id": <number>}}, or {{"id": null}} if no element matches.'
)

AskFn = Callable[[str], str]  # prompt -> raw model text (text only, no image)
ScanFn = Callable[[], list[UiElement]]

YELLOW, GREEN = (240, 200, 0), (30, 200, 70)


def make_ask(model: str, host: str, num_ctx: int) -> AskFn:
    """A fresh, history-free text-only model call per question."""
    from desktop_ai_agent.core.chat_client import ChatClient

    def ask(prompt: str) -> str:
        client = ChatClient(
            model,
            host,
            think=False,
            # Plain JSON mode. Never None: ChatClient maps None to its script/code schema.
            response_format="json",
            system="",
            normalize=False,
            options={"temperature": 0, "num_ctx": num_ctx},
        )
        client.add_user(prompt)
        return client.chat().content

    return ask


def _image_box(el: UiElement, origin: tuple[int, int]) -> tuple[int, int, int, int]:
    x1, y1 = screen_to_image_px(el.left, el.top, *origin)
    x2, y2 = screen_to_image_px(el.right, el.bottom, *origin)
    return x1, y1, x2, y2


def draw_elements(image: Any, elements: list[UiElement], origin: tuple[int, int]) -> None:
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    for el in elements:
        box = _image_box(el, origin)
        draw.rectangle(box, outline=YELLOW, width=1)
        draw.text((box[0] + 2, box[1] + 1), str(el.id), fill=YELLOW, font=font)


def draw_pick(image: Any, target: str, el: UiElement | None, note: str, origin: tuple[int, int]) -> None:
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    if el is not None:
        box = _image_box(el, origin)
        draw.rectangle(box, outline=GREEN, width=3)
        x, y = screen_to_image_px(*el.center, *origin)
        draw.line([x - 12, y, x + 12, y], fill=GREEN, width=2)
        draw.line([x, y - 12, x, y + 12], fill=GREEN, width=2)
    label = f"#{el.id} {el.type} \"{el.name}\" ({el.app})" if el else note
    draw.rectangle([0, 0, 520, 36], fill=(0, 0, 0))
    draw.text((6, 4), f"target: {target}", fill=(255, 255, 255), font=font)
    draw.text((6, 20), f"pick: {label}", fill=GREEN if el else (225, 50, 50), font=font)


def run_probe(
    targets: list[str],
    *,
    capture: Callable[[], dict[str, Any]],
    scan: ScanFn,
    ask: AskFn | None,
    out_dir: Path,
    max_items: int = 400,
    scan_stats: dict[str, int] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Capture, scan once, then ask once per target. Returns (and saves) the results."""
    from PIL import Image

    cap = capture()
    origin = (int(cap.get("left", 0)), int(cap.get("top", 0)))
    full = Image.open(cap["path"]).convert("RGB")
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    elements = scan()
    scan_seconds = round(time.perf_counter() - started, 2)
    listing, listed = format_for_model(elements, max_items=max_items)
    hidden = (scan_stats or {}).get("hidden", 0)
    log(f"UI Automation: {len(elements)} elements ({listed} named, sent to the model), "
        f"{hidden} hidden behind other windows dropped, in {scan_seconds}s")

    overview = full.copy()
    draw_elements(overview, elements, origin)
    overview.save(out_dir / "elements.png")
    (out_dir / "elements.txt").write_text(listing, encoding="utf-8")

    rows: list[dict[str, Any]] = []
    for n, target in enumerate(targets if ask else [], 1):
        row: dict[str, Any] = {"n": n, "target": target, "raw": "", "seconds": 0.0, "note": "",
                               "element": None, "screen_px": None, "image_px": None}
        t0 = time.perf_counter()
        try:
            row["raw"] = ask(PICK_PROMPT.format(elements=listing, target=target))  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - record and carry on
            row["note"] = f"error: {exc}"
        row["seconds"] = round(time.perf_counter() - t0, 2)

        el = None
        if not row["note"]:
            kind, element_id = parse_choice(row["raw"])
            if kind == "none":
                row["note"] = "model says no element matches"
            elif kind == "invalid":
                row["note"] = "no id in reply"
            else:
                el = find_element(elements, element_id)
                if el is None:
                    row["note"] = f"id {element_id} is not in the list"
        if el is not None:
            row["element"] = asdict(el)
            row["screen_px"] = el.center
            row["image_px"] = screen_to_image_px(*el.center, *origin)

        canvas = full.copy()
        draw_pick(canvas, target, el, row["note"], origin)
        row["image"] = f"{n:02d}_{slugify(target)}.png"
        canvas.save(out_dir / row["image"])
        rows.append(row)
        pick = f"#{el.id} {el.type} {el.name!r} ({el.app}) at {el.center}" if el else row["note"]
        log(f"[{n}/{len(targets)}] {target!r}: {pick}  ({row['seconds']}s)")

    payload = {
        "capture": {"width": full.width, "height": full.height, "left": origin[0], "top": origin[1]},
        "scan_seconds": scan_seconds,
        "element_count": len(elements),
        "hidden_dropped": hidden,
        "listed_for_model": listed,
        "elements": [asdict(el) for el in elements],
        "results": rows,
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List on-screen UI elements and test picking them by name.")
    parser.add_argument("--targets", default=str(ROOT / "scripts" / "aim_targets.txt"))
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--quick", action="store_true", help="only the first 3 targets")
    parser.add_argument("--scan-only", action="store_true", help="only the element map, no model calls")
    parser.add_argument("--show-hidden", action="store_true",
                        help="keep elements hidden behind other windows (for comparison)")
    parser.add_argument("--max-items", type=int, default=400, help="most elements listed for the model")
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds allowed for the UIA scan")
    parser.add_argument("--delay", type=float, default=5.0, help="seconds to wait before capturing")
    parser.add_argument("--num-ctx", type=int, default=16384, help="the element list is long text")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    from desktop_ai_agent.action.tools.screenshot import capture_screen
    from desktop_ai_agent.core.monitors import enable_dpi_awareness, get_monitors, virtual_desktop

    enable_dpi_awareness()  # screenshot pixels must be real screen pixels, like UIA's
    targets_path = Path(args.targets)
    targets = parse_targets(targets_path.read_text(encoding="utf-8")) if targets_path.is_file() else []
    if args.quick:
        targets = targets[:3]
    if not targets and not args.scan_only:
        print(f"No targets in {targets_path} (one description per line).")
        return 1

    out_dir = ROOT / "logs" / "ui_probe" / datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"{len(targets)} target(s). Capturing in {args.delay:g}s - switch to the windows to test...")
    time.sleep(max(0.0, args.delay))

    def capture() -> dict[str, Any]:
        return capture_screen(scope="primary")

    stats: dict[str, int] = {}

    def scan() -> list[UiElement]:
        desk = virtual_desktop(get_monitors())  # (left, top, width, height)
        bounds = None if desk is None else (desk[0], desk[1], desk[0] + desk[2], desk[1] + desk[3])
        return list_ui_elements(bounds=bounds, timeout=args.timeout, visible_only=not args.show_hidden, stats=stats)

    try:
        payload = run_probe(
            targets,
            capture=capture,
            scan=scan,
            ask=None if args.scan_only else make_ask(args.model, args.host, args.num_ctx),
            out_dir=out_dir,
            max_items=args.max_items,
            scan_stats=stats,
        )
    except RuntimeError as exc:
        print(f"Failed: {exc}")
        return 1

    rows = payload["results"]
    if rows and all(r["note"].startswith("error") for r in rows):
        print("Every model call failed. Is Ollama running, and is the model name right (--model)?")
        print(rows[0]["note"])
        return 1
    print(f"\nDone. Open: {out_dir}")
    print("elements.png shows everything UI Automation sees (yellow, numbered).")
    print("Each target image shows the model's pick in green. Tell me hit or miss per target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
