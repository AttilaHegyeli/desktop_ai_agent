# Camera hand tracking

Webcam hand tracking turns your **index fingertip into the mouse**. A fullscreen, click-through overlay shows a shaded self-view and skeleton so you can see what the system sees. Control is **gesture-only** (no voice “type / enter / click” shortcuts).

Windows only for pointer injection (`SetCursorPos` / `mouse_event`).

---

## How it works (end-to-end)

```text
Webcam
  → OpenCV capture (Media Foundation on Windows)
  → MediaPipe Hand Landmarker (21 points per hand)
  → HandTracker.latest() snapshot
       ├─→ HandOverlay (fullscreen self-view + skeleton)
       └─→ HandMouseController (~20 Hz)
              → map index tip through an inset “track rect” → screen pixels
              → dwell / pinch / thumbs gestures
              → pointer_control → mouse_win32 (OS cursor / buttons / wheel)
```

1. **Capture** — `HandTracker` opens the camera on a background thread, runs MediaPipe, and publishes the latest `HandFrame` (hands + optional RGB preview).
2. **Overlay** — `HandOverlay` paints a translucent fullscreen window (click-through) with the mirrored feed and skeleton. Index tip is highlighted orange; thumb tip red.
3. **Pointing** — `HandMouseController` reads the first hand’s **index tip**, maps it from a smaller region of the camera frame onto the full screen (so you can reach screen edges without putting your hand at the FOV border), and moves the OS cursor.
4. **Actions** — Gestures fire absolute mouse actions via `pointer_control` (not registered for the LLM agent).

Toggle the whole stack (camera + overlay + hand-mouse) with the **camera button** on the chat bar. State is saved to `config/setup.json` → `camera.enabled`.

---

## Gestures (what you do)

| Goal | Gesture | Notes |
|------|---------|--------|
| **Move cursor** | Point with **index finger** | Tip follows continuously when `mouse_follow` is on. |
| **Left click** | Hold index tip **still** for `click_dwell_sec` | One finger only — no pinch. Click fires at the dwell anchor so small jitter does not move the target. Move again before the next click. |
| **Drag** | **Pinch** (thumb + index), hold ~`drag_hold_sec`, move, release | Pinch does **not** click. Cursor **freezes** at pinch-start until drag arms, then follows the tip. Release opens the pinch (with frame debounce). |
| **Scroll up** | **Thumbs up** (thumb up, other fingers curled), hold | After `gesture_frames` stable frames, scrolls up each poll by `scroll_ticks`. |
| **Scroll down** | **Thumbs down**, hold | Same, negative wheel. |

If the hand disappears mid-drag, the controller **releases the mouse button** so Windows is not left with LMB stuck down.

---

## Track rect (why edges are easier)

Normalized camera coords are **not** mapped 1:1 to the screen. Only an inset rectangle maps to the full screen:

```text
camera frame (0..1)
┌────────────────────────────┐
│         margins            │
│   ┌──────────────────┐     │
│   │   track rect     │ → full screen
│   │  index tip here  │
│   └──────────────────┘     │
└────────────────────────────┘
```

Defaults (`track_left/top/right/bottom`): roughly the central ~44% × 50% of the frame. Move your finger inside that region; the corners of the rect map to the corners of the screen. Smaller rect → larger motion on screen → easier edges, less precision.

---

## Turning it on / off

| Control | Effect |
|---------|--------|
| Chat bar **camera** icon | Starts/stops tracker + overlay + hand-mouse; writes `camera.enabled`. |
| Right-click menu → **Toggle camera** | Same. |
| `config/setup.json` → `camera.enabled` | Default on launch when chat becomes ready. |
| `camera.pointing_enabled` | If `false`, overlay can still run but hand-mouse (cursor control) does not start. |

Install optional deps once:

```bash
pip install -e ".[camera]"
```

On first run the Hand Landmarker `.task` model is downloaded into the project models directory if missing.

---

## Configuration (`config/setup.json` → `camera`)

### Camera / overlay

| Key | Role |
|-----|------|
| `enabled` | Master switch (also toggled from the UI). |
| `device_index` | Webcam index (0 = default). |
| `width` / `height` | Capture request size. |
| `max_num_hands` | MediaPipe max hands (controller uses **first** hand only). |
| `mirror` | Mirror preview / landmarks horizontally (selfie view). |
| `overlay_opacity` | Fullscreen overlay alpha. |
| `show_feed` / `feed_gain` | Draw shaded camera feed under skeleton; brightness gain. |
| `detection_confidence` / `tracking_confidence` | MediaPipe thresholds. |
| `preview_max_side` | Cap preview resolution for the overlay. |
| `pointing_enabled` | Enable hand → mouse controller. |
| `mouse_follow` | Continuously move cursor with the index tip. |

### Pointing / gestures

| Key | Role | Typical |
|-----|------|---------|
| `track_left` / `track_top` / `track_right` / `track_bottom` | Inset mapping rect (0..1). | `0.28, 0.22, 0.72, 0.72` |
| `mouse_poll_hz` | Controller tick rate. | `20` |
| `click_dwell_sec` | Stillness time for left click. | `0.55`–`1.5` |
| `still_px` | Max screen-pixel drift counted as “still”. | `14` |
| `pinch_threshold` | Distance ≤ this → pinch down. | `0.07` |
| `pinch_release` | Distance ≥ this → pinch open (hysteresis). | `0.11` |
| `pinch_release_frames` | Consecutive open frames before release sticks. | `5` |
| `drag_hold_sec` | Pinch hold before `mouse_down`. | `1.0` |
| `scroll_ticks` | Wheel ticks per poll while like/dislike held. | `3` |
| `gesture_frames` | Stable frames before scroll starts. | `3` |

Tune **click**: raise `click_dwell_sec` / `still_px` if accidental clicks; lower dwell if clicks feel slow.  
Tune **drag**: longer `drag_hold_sec` and higher `pinch_release_frames` if pinch flicker ends drag early.

---

## Architecture (code map)

| Piece | Path | Responsibility |
|-------|------|----------------|
| Tracker | `src/desktop_ai_agent/input/hand_tracker.py` | OpenCV + MediaPipe Tasks; `HandFrame` / landmarks. |
| Gestures | `src/desktop_ai_agent/input/hand_mouse.py` | Track-rect mapping, dwell click, pinch drag, thumbs scroll. |
| Overlay | `src/desktop_ai_agent/ui/hand_overlay.py` | Fullscreen click-through self-view + skeleton. |
| Wiring | `src/desktop_ai_agent/ui/launcher.py` | Start/stop on chat ready; camera toggle; persist config. |
| Bar UI | `src/desktop_ai_agent/ui/overlay_bar.py` | Camera button + “Toggle camera” menu item. |
| Pointer API | `src/desktop_ai_agent/action/tools/pointer_control.py` | Absolute move / click / down / up / scroll (**not** LLM tools). |
| Win32 | `src/desktop_ai_agent/action/tools/mouse_win32.py` | `SetCursorPos`, `mouse_event`, clipboard paste helpers. |
| Safety | `src/desktop_ai_agent/action/tools/mouse_safety.py` | Click rate limits; corner emergency lock for intentional clicks. |

### Why a separate `pointer_control`?

The agent’s LLM tools use element lists and rate-limited clicks. Hand control needs **trusted absolute coordinates** at high rate. Those helpers stay off the LLM registry so the model cannot call `click_at` / `mouse_down_at` with arbitrary pixels the same way.

### Safety notes

- **Corner lock** — Pushing the real mouse into a monitor corner arms a ~30s pause for actions that check corners (e.g. normal clicks). Hand **move / drag-down / scroll** use `arm_corner=False` so pointing at screen edges does not lock you out.
- **`mouse_up_at`** — Always releases the button even if emergency stop would block other moves.
- **Hand loss** — Aborts pinch/drag and releases LMB if needed.

---

## Controller state machine (simplified)

```text
                 index tip
                    │
                    ▼
              move cursor ──────────────────────────────┐
                    │                                   │
         still ≥ click_dwell_sec?                       │
                    │ yes                               │
                    ▼                                   │
               left click                               │
               (need move to re-arm)                    │
                    │                                   │
         pinch edge (thumb≈index)?                      │
                    │ yes                               │
                    ▼                                   │
           freeze cursor at pinch-start                 │
                    │                                   │
         hold ≥ drag_hold_sec?                          │
                    │ yes                               │
                    ▼                                   │
              mouse_down → follow tip while pinched     │
                    │                                   │
              pinch released (debounced)                │
                    │                                   │
                    ▼                                   │
                mouse_up                                │
                    │                                   │
         thumbs up / down stable? ──► scroll_at ────────┘
```

---

## Tests

| File | Covers |
|------|--------|
| `tests/input/test_hand_tracker.py` | Config / availability helpers. |
| `tests/input/test_hand_mouse.py` | Mapping, dwell click, pinch≠click, drag hold, freeze, hand-loss release, scroll. |
| `tests/action/test_pointer_control.py` | Absolute pointer helpers and safety interaction. |
| `tests/ui/…` | Overlay / bar pieces as present in the suite. |

```bash
python -m pytest tests/input/test_hand_mouse.py tests/action/test_pointer_control.py -q
```

---

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| No overlay / “deps missing” | `pip install -e ".[camera]"` |
| Camera busy / no frames | Close other apps using the webcam; check Windows privacy → Camera; try another `device_index`. |
| Overlay invisible | Raise `overlay_opacity`; confirm chat is ready (camera starts with the bar). |
| Cursor jittery | Slightly larger `still_px`; better lighting; face the camera. |
| Accidental clicks | Increase `click_dwell_sec`. |
| Drag never starts / ends early | Loosen pinch (`pinch_threshold` / `pinch_release`); raise `pinch_release_frames`; hold pinch steadier for `drag_hold_sec`. |
| Stuck left button | Toggle camera off (stop releases button); or move hand out of view so abort runs. |
| Hard to reach screen corners | Shrink the track rect (increase insets) or move hand more within the current rect. |
| Scroll false triggers | Need clearer thumbs + curled fingers; raise `gesture_frames`. |

---

## What this feature is not

- Not driven by voice phrases (`type …`, `enter`, `click`) — those shortcuts were removed; the agent chat/voice path is separate.
- Not multi-hand control — only `hands[0]` drives the mouse.
- Not an LLM tool — the agent still uses `list_elements` / relative UI tools; hand control is a parallel human input channel.
