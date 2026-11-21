"""Always-on-top banner shown while a phone call has the PC input locked."""
from __future__ import annotations

import time
import tkinter as tk


class CallBanner:
    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        self._win: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._started = 0.0
        self._job: str | None = None

    def show(self, hint: str) -> None:
        if self._win is not None:
            return
        self._hint = hint
        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        label = tk.Label(
            win, fg="white", bg="#b3261e", font=("Segoe UI", 11, "bold"), padx=16, pady=8
        )
        label.pack()
        self._win, self._label = win, label
        self._started = time.monotonic()
        self._tick()
        win.update_idletasks()
        w = win.winfo_reqwidth()
        x = (win.winfo_screenwidth() - w) // 2
        win.geometry(f"+{x}+{win.winfo_screenheight() - 90}")

    def hide(self) -> None:
        if self._job is not None:
            try:
                self._root.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._win = self._label = None

    def _tick(self) -> None:
        if self._label is None:
            return
        secs = int(time.monotonic() - self._started)
        self._label.configure(
            text=f"PHONE CALL ACTIVE {secs // 60}:{secs % 60:02d} — PC input locked · {self._hint}"
        )
        self._job = self._root.after(1000, self._tick)
