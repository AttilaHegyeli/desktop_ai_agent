"""Small window that shows a QR code for the phone link (drawn on a Tk canvas)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

QUIET_ZONE = 4  # modules of white border the QR spec requires


class QrUnavailable(RuntimeError):
    pass


def qr_matrix(text: str) -> list[list[bool]]:
    """QR code modules (True = dark) for ``text``. Needs the ``segno`` package."""
    try:
        import segno
    except ImportError as exc:
        raise QrUnavailable("QR needs the segno package: pip install segno") from exc
    return [[bool(cell) for cell in row] for row in segno.make(text, error="m").matrix]


def show_qr(root: tk.Misc, url: str, *, size: int = 340) -> tk.Toplevel:
    matrix = qr_matrix(url)
    modules = len(matrix) + 2 * QUIET_ZONE
    cell = max(2, size // modules)
    px = cell * modules

    win = tk.Toplevel(root)
    win.title("Scan with your phone")
    win.attributes("-topmost", True)
    win.resizable(False, False)

    ttk.Label(
        win, text="Point your phone camera at the code", font=("Segoe UI", 11, "bold")
    ).pack(padx=16, pady=(14, 8))

    canvas = tk.Canvas(win, width=px, height=px, bg="white", highlightthickness=0)
    canvas.pack(padx=16)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                x0 = (x + QUIET_ZONE) * cell
                y0 = (y + QUIET_ZONE) * cell
                canvas.create_rectangle(
                    x0, y0, x0 + cell, y0 + cell, fill="black", outline="black"
                )

    link = ttk.Entry(win, width=44, justify="center")
    link.insert(0, url)
    link.configure(state="readonly")
    link.pack(padx=16, pady=(10, 4))
    ttk.Label(win, text="The link changes every time the app starts.").pack()
    ttk.Button(win, text="Close", command=win.destroy).pack(pady=(8, 14))
    return win
