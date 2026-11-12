"""Webcam hand landmark tracking via OpenCV + MediaPipe Tasks HandLandmarker.

Optional deps: pip install -e ".[camera]"
``HandFrame.index_tip`` / thumb tip feed the hand-mouse controller (action layer).
"""
from __future__ import annotations

import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop_ai_agent.paths import HAND_LANDMARKER_TASK, MODELS_DIR

# MediaPipe hand topology (21 landmarks). Exported for overlay / tests.
HAND_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    (0, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    (0, 9),
    (9, 10),
    (10, 11),
    (11, 12),
    (0, 13),
    (13, 14),
    (14, 15),
    (15, 16),
    (0, 17),
    (17, 18),
    (18, 19),
    (19, 20),
    (5, 9),
    (9, 13),
    (13, 17),
)

THUMB_TIP = 4
INDEX_TIP = 8
_NUM_LANDMARKS = 21
_INSTALL_HINT = 'pip install -e ".[camera]"'
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)


class HandTrackerError(RuntimeError):
    """Camera / MediaPipe failure with a user-facing message."""


@dataclass(frozen=True)
class Landmark:
    """Normalized image coordinates (x,y in 0..1; z relative depth)."""

    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class HandLandmarks:
    """One detected hand (21 landmarks)."""

    landmarks: tuple[Landmark, ...]
    handedness: str = "Unknown"

    def __post_init__(self) -> None:
        if len(self.landmarks) != _NUM_LANDMARKS:
            raise ValueError(f"expected {_NUM_LANDMARKS} landmarks, got {len(self.landmarks)}")

    @property
    def index_tip(self) -> Landmark:
        return self.landmarks[INDEX_TIP]

    @property
    def thumb_tip(self) -> Landmark:
        return self.landmarks[THUMB_TIP]


@dataclass(frozen=True)
class HandFrame:
    """Latest tracker snapshot. ``preview_rgb`` is optional HxWx3 uint8 (mirrored)."""

    timestamp: float
    width: int
    height: int
    hands: tuple[HandLandmarks, ...]
    preview_rgb: Any | None = None  # numpy ndarray when available

    @property
    def index_tip(self) -> Landmark | None:
        """First hand's index fingertip, or None if no hands."""
        if not self.hands:
            return None
        return self.hands[0].index_tip


def hand_tracking_available() -> bool:
    """True when OpenCV and MediaPipe Tasks can be imported."""
    try:
        import cv2  # noqa: F401
        from mediapipe.tasks.python import vision  # noqa: F401
    except ImportError:
        return False
    return True


def camera_enabled(setup: dict[str, Any] | None) -> bool:
    cam = (setup or {}).get("camera") or {}
    return bool(cam.get("enabled", False))


def skeleton_segments(
    landmarks: tuple[Landmark, ...] | list[Landmark],
    *,
    width: int,
    height: int,
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Map HAND_CONNECTIONS to pixel line segments for drawing / tests."""
    pts = [(int(lm.x * width), int(lm.y * height)) for lm in landmarks]
    segs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for a, b in HAND_CONNECTIONS:
        if 0 <= a < len(pts) and 0 <= b < len(pts):
            segs.append((pts[a], pts[b]))
    return segs


def ensure_hand_model(path: Path | None = None) -> Path:
    """Return path to the HandLandmarker ``.task`` file, downloading once if needed."""
    dest = path or HAND_LANDMARKER_TASK
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".task.partial")
    try:
        urllib.request.urlretrieve(_MODEL_URL, tmp)
        tmp.replace(dest)
    except Exception as exc:  # noqa: BLE001
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise HandTrackerError(
            f"Could not download hand landmarker model: {exc}"
        ) from exc
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise HandTrackerError("Hand landmarker model download produced an empty file")
    return dest


def _require_camera() -> None:
    if not hand_tracking_available():
        raise HandTrackerError(
            f"Camera deps missing. Install with: {_INSTALL_HINT}"
        )


def _hand_from_tasks(hand_landmarks: Any, handedness: str) -> HandLandmarks:
    pts = tuple(
        Landmark(x=float(lm.x), y=float(lm.y), z=float(getattr(lm, "z", 0.0) or 0.0))
        for lm in hand_landmarks
    )
    return HandLandmarks(landmarks=pts, handedness=handedness or "Unknown")


class HandTracker:
    """Background webcam loop → MediaPipe HandLandmarker → latest ``HandFrame``."""

    def __init__(
        self,
        *,
        device_index: int = 0,
        width: int = 640,
        height: int = 480,
        max_num_hands: int = 2,
        mirror: bool = True,
        detection_confidence: float = 0.6,
        tracking_confidence: float = 0.5,
        preview_max_side: int = 960,
        model_path: Path | None = None,
    ) -> None:
        self.device_index = int(device_index)
        self.width = max(160, int(width))
        self.height = max(120, int(height))
        self.max_num_hands = max(1, min(4, int(max_num_hands)))
        self.mirror = bool(mirror)
        self.detection_confidence = float(detection_confidence)
        self.tracking_confidence = float(tracking_confidence)
        self.preview_max_side = max(160, int(preview_max_side))
        self.model_path = model_path

        self._lock = threading.Lock()
        self._latest: HandFrame | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    @property
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    @property
    def last_error(self) -> str | None:
        return self._error

    def latest(self) -> HandFrame | None:
        with self._lock:
            return self._latest

    def start(self) -> None:
        """Open the camera and begin tracking on a daemon thread."""
        _require_camera()
        if self.is_running:
            return
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="hand-tracker"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        self._thread = None

    def _publish(self, frame: HandFrame) -> None:
        with self._lock:
            self._latest = frame

    def _run(self) -> None:  # pragma: no cover - needs webcam / MediaPipe
        _quiet_mediapipe_logs()
        import cv2
        from mediapipe import Image as MpImage
        from mediapipe import ImageFormat
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            HandLandmarker,
            HandLandmarkerOptions,
            RunningMode,
        )

        try:
            model = ensure_hand_model(self.model_path)
        except HandTrackerError as exc:
            self._error = str(exc)
            return

        cap = _open_camera(self.device_index, self.width, self.height)
        if cap is None:
            self._error = (
                f"Could not open camera index {self.device_index} "
                "(close other apps using the webcam, then retry)"
            )
            return

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=RunningMode.VIDEO,
            num_hands=self.max_num_hands,
            min_hand_detection_confidence=self.detection_confidence,
            min_hand_presence_confidence=self.detection_confidence,
            min_tracking_confidence=self.tracking_confidence,
        )

        try:
            landmarker = HandLandmarker.create_from_options(options)
        except Exception as exc:  # noqa: BLE001
            cap.release()
            self._error = f"HandLandmarker init failed: {exc}"
            return

        t0 = time.perf_counter()
        last_ts = -1
        try:
            while not self._stop.is_set():
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    time.sleep(0.02)
                    continue
                if self.mirror:
                    bgr = cv2.flip(bgr, 1)

                h, w = bgr.shape[:2]
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                # VIDEO mode requires strictly increasing timestamps (ms).
                ts_ms = int((time.perf_counter() - t0) * 1000)
                if ts_ms <= last_ts:
                    ts_ms = last_ts + 1
                last_ts = ts_ms

                mp_image = MpImage(image_format=ImageFormat.SRGB, data=rgb)
                result = landmarker.detect_for_video(mp_image, ts_ms)

                detected: list[HandLandmarks] = []
                hands = result.hand_landmarks or []
                handed_list = result.handedness or []
                for i, hlm in enumerate(hands):
                    label = "Unknown"
                    if i < len(handed_list) and handed_list[i]:
                        cats = handed_list[i]
                        if cats:
                            label = str(cats[0].category_name or "Unknown")
                    if len(hlm) == _NUM_LANDMARKS:
                        detected.append(_hand_from_tasks(hlm, label))

                preview = _downscale_rgb(rgb, self.preview_max_side)
                self._publish(
                    HandFrame(
                        timestamp=time.time(),
                        width=w,
                        height=h,
                        hands=tuple(detected),
                        preview_rgb=preview,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
        finally:
            landmarker.close()
            cap.release()


def _quiet_mediapipe_logs() -> None:
    """Reduce noisy TFLite / MediaPipe stderr on Windows consoles."""
    import os
    import logging

    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")
    for name in ("mediapipe", "tensorflow", "absl"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        from absl import logging as absl_logging

        absl_logging.set_verbosity(absl_logging.ERROR)
        absl_logging.set_stderrthreshold("error")
    except Exception:  # noqa: BLE001
        pass


def _open_camera(device_index: int, width: int, height: int) -> Any | None:
    """Open a webcam that can actually ``read()`` a frame.

    Prefer MSMF on Windows; avoid probing DSHOW first (noisy failures on index 0).
    """
    import cv2

    backends: list[int] = []
    if hasattr(cv2, "CAP_MSMF"):
        backends.append(cv2.CAP_MSMF)
    backends.append(cv2.CAP_ANY)
    if hasattr(cv2, "CAP_DSHOW"):
        backends.append(cv2.CAP_DSHOW)

    # Try configured index, then a couple of neighbors (virtual cams shift indexes).
    indexes = [device_index]
    for alt in (0, 1, 2):
        if alt not in indexes:
            indexes.append(alt)

    for backend in backends:
        for idx in indexes:
            cap = cv2.VideoCapture(idx, backend)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap
            cap.release()
    return None


def _downscale_rgb(rgb: Any, max_side: int) -> Any:
    import cv2

    h, w = rgb.shape[:2]
    side = max(h, w)
    if side <= max_side:
        return rgb.copy()
    scale = max_side / float(side)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)


def from_setup(setup: dict[str, Any] | None) -> HandTracker:
    """Build a HandTracker from config/setup.json's ``camera`` block."""
    cam = (setup or {}).get("camera") or {}
    return HandTracker(
        device_index=int(cam.get("device_index", 0)),
        width=int(cam.get("width", 640)),
        height=int(cam.get("height", 480)),
        max_num_hands=int(cam.get("max_num_hands", 2)),
        mirror=bool(cam.get("mirror", True)),
        detection_confidence=float(cam.get("detection_confidence", 0.6)),
        tracking_confidence=float(cam.get("tracking_confidence", 0.5)),
        preview_max_side=int(cam.get("preview_max_side", 960)),
    )
