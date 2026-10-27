"""Always-on-top desktop AI command bar with a polished light-glass interface."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any, Literal

from desktop_ai_agent.ui.chat_text import normalize_chat_reply
from desktop_ai_agent.ui.icons import photo_icon

BarMode = Literal["collapsed", "expanded", "reply"]

# Chromakey for true rounded corners on Windows.
_KEY = "#01fe03"

# iOS-inspired palette: soft system neutrals, hairline borders and system indigo.
_CARD = "#f5f5f9"
_CARD_STROKE = "#e5e5ec"
_SHADOW = "#dfe0e8"
_FG = "#1c1c1e"
_MUTED = "#86868b"
_PLACEHOLDER = "#aeaeb2"
_WHITE = "#ffffff"
_VOICE_STROKE = "#e7e7ed"
_SEND = "#5e5ce6"
_SEND_HI = "#4f4dcf"
_DANGER = "#ff3b30"
_NUB_FILL = "#1c1c1e"
_NUB_STROKE = "#1c1c1e"
_CHIP = "#ffffff"
_CHIP_STROKE = "#e7e7ed"
_ACCENT = "#5e5ce6"
_ACCENT_SOFT = "#eeedff"
_SUCCESS = "#30a46c"
_INPUT_STROKE = "#e5e5eb"
_REPLY_BG = "#efeff6"
_PROMPT_HISTORY_MAX = 20
# Compact while thinking; taller (scrollable) once a reply is typing / shown.
_AI_THINK_LINES = 2
_AI_THINK_PX = 36
_AI_VIEW_LINES = 6
_AI_VIEW_PX = 108
_TYPE_MS = 16
_THINK_DOT_MS = 420


def _place_icon(
    canvas: tk.Canvas,
    name: str,
    cx: float,
    cy: float,
    size: int,
    color: str,
    *,
    bg: str,
    tags: str,
    keep: list[tk.PhotoImage],
) -> None:
    """Paint a smooth SVG icon baked onto `bg` (Tk PhotoImage needs opaque pixels)."""
    canvas.delete(tags)
    img = photo_icon(canvas, name, size=size, color=color, bg=bg)
    keep.append(img)
    canvas.create_image(cx, cy, image=img, tags=tags)


def format_exchange_lines(user: str, ai: str | None) -> tuple[str, str]:
    """Return the two display strings for the reply panel (never more than me + ai)."""
    me = f"me: {(user or '').strip()}"
    if ai is None:
        return me, "…"
    return me, normalize_chat_reply(ai)


def clip_line(text: str, max_chars: int = 120) -> str:
    """Single-line clip for the short YOU row (AI text uses the scroll panel instead)."""
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float,
    **kwargs: Any,
) -> int:
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [
        x1 + r,
        y1,
        x2 - r,
        y1,
        x2,
        y1,
        x2,
        y1 + r,
        x2,
        y2 - r,
        x2,
        y2,
        x2 - r,
        y2,
        x1 + r,
        y2,
        x1,
        y2,
        x1,
        y2 - r,
        x1,
        y1 + r,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundButton(tk.Canvas):
    """Circular / capsule control with true border-radius and SVG icons."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        icon: str | None = None,
        icon_size: int = 16,
        command: Callable[[], None] | None = None,
        diameter: int = 36,
        pill_width: int | None = None,
        fill: str = _CHIP,
        fg: str = _FG,
        active_fill: str | None = None,
        font: tuple[str, int] | tuple[str, int, str] = ("Segoe UI", 12),
        stroke: str | None = None,
        widget_bg: str | None = None,
    ) -> None:
        self._bw = int(pill_width or diameter)
        self._bh = int(diameter)
        self._fill = fill
        self._active = active_fill or fill
        self._fg = fg
        self._font = font
        self._stroke = stroke
        self._command = command
        self._enabled = True
        self._icon = icon
        self._icon_size = icon_size
        self._text = text
        self._icon_keep: list[tk.PhotoImage] = []
        self._surface = fill
        # Canvas widget is always rectangular; match parent surface so only the
        # oval fill reads as a circle (critical inside the white input capsule).
        canvas_bg = widget_bg if widget_bg is not None else master.cget("bg")

        super().__init__(
            master,
            width=self._bw,
            height=self._bh,
            bg=canvas_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._shape = self._draw_shape(self._fill)
        self._txt: int | None = None
        self._redraw_content()
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self._paint(self._active if self._enabled else self._fill))
        self.bind("<Leave>", lambda _e: self._paint(self._fill))

    def _draw_shape(self, fill: str) -> int:
        pad = 1.5
        bw, bh = self._bw, self._bh
        if bw == bh:
            return self.create_oval(
                pad,
                pad,
                bw - pad,
                bh - pad,
                fill=fill,
                outline=self._stroke or fill,
                width=1 if self._stroke else 0,
            )
        return _round_rect(
            self,
            pad,
            pad,
            bw - pad,
            bh - pad,
            bh / 2,
            fill=fill,
            outline=self._stroke or fill,
            width=1 if self._stroke else 0,
        )

    def _content_color(self) -> str:
        return self._fg if self._enabled else _MUTED

    def _redraw_content(self) -> None:
        self.delete("content")
        self._icon_keep.clear()
        color = self._content_color()
        has_icon = bool(self._icon)
        has_text = bool(self._text)
        mid_y = self._bh / 2
        bg = self._surface

        if has_icon and has_text:
            _place_icon(
                self,
                self._icon or "",
                18,
                mid_y,
                self._icon_size,
                color,
                bg=bg,
                tags="content",
                keep=self._icon_keep,
            )
            self._txt = self.create_text(
                58 if self._bw >= 90 else self._bw * 0.62,
                mid_y,
                text=self._text,
                fill=color,
                font=self._font,
                tags="content",
            )
        elif has_icon:
            _place_icon(
                self,
                self._icon or "",
                self._bw / 2,
                mid_y,
                self._icon_size,
                color,
                bg=bg,
                tags="content",
                keep=self._icon_keep,
            )
            self._txt = None
        elif has_text:
            self._txt = self.create_text(
                self._bw / 2,
                mid_y,
                text=self._text,
                fill=color,
                font=self._font,
                tags="content",
            )
        else:
            self._txt = None

    def _paint(self, fill: str) -> None:
        self._surface = fill
        self.itemconfigure(self._shape, fill=fill, outline=self._stroke or fill)
        if self._icon:
            self._redraw_content()

    def _click(self, _event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        if self._enabled and self._command:
            self._command()

    def set_style(
        self,
        *,
        text: str | None = None,
        icon: str | None = None,
        fill: str | None = None,
        fg: str | None = None,
        active_fill: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        redraw = False
        if text is not None and text != self._text:
            self._text = text
            redraw = True
        if icon is not None and icon != self._icon:
            self._icon = icon
            redraw = True
        if fill is not None:
            self._fill = fill
            self._surface = fill
            self.itemconfigure(self._shape, fill=fill, outline=self._stroke or fill)
            redraw = True
        if active_fill is not None:
            self._active = active_fill
        if fg is not None and fg != self._fg:
            self._fg = fg
            redraw = True
        if enabled is not None:
            self._enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            redraw = True
        if redraw:
            self._redraw_content()


class OverlayBar:
    """Frameless topmost bar owned by the setup root window."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        ui: dict[str, Any] | None = None,
        voice_enabled: bool = True,
        on_send: Callable[[], None] | None = None,
        on_mic: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        on_config: Callable[[], None] | None = None,
        on_close_chat: Callable[[], None] | None = None,
    ) -> None:
        cfg = ui or {}
        # Prefer an explicit width; otherwise size from the screen so the chat
        # stays readable on small and large displays.
        screen_w = int(master.winfo_screenwidth())
        default_w = min(720, max(400, int(screen_w * 0.42)))
        self._width = int(cfg.get("bar_width", default_w))
        self._auto_hide_ms = int(cfg.get("bar_auto_hide_ms", 20_000))
        self._collapsed_h = max(22, int(cfg.get("bar_collapsed_height", 28)))
        self._top_margin = int(cfg.get("bar_top_margin", 10))
        self._nub_w = 112
        self._pad = 9  # room for the soft, layered card shadow
        self._radius = 34
        # Keep native child widgets well inside the rounded silhouette.
        self._body_inset = 13
        self._btn = 36
        self._leave_grace_ms = 220
        self._circle = "#f0f0f5"

        self._on_send = on_send
        self._on_mic = on_mic
        self._on_exit = on_exit
        self._on_config = on_config
        self._on_close_chat = on_close_chat
        self._voice_enabled = voice_enabled

        self._mode: BarMode = "collapsed"
        self._ready = False
        self._busy = False
        self._pointer_inside = False
        self._leave_job: str | None = None
        self._hide_job: str | None = None
        self._user = ""
        self._ai: str | None = None
        self._prompt_history: list[str] = []
        self._history_index: int | None = None
        self._history_draft = ""
        self._type_job: str | None = None
        self._type_target = ""
        self._type_index = 0
        self._dot_job: str | None = None
        self._dot_phase = 0

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", _KEY)
        except tk.TclError:
            pass
        self.win.configure(bg=_KEY)
        self.win.withdraw()

        self._canvas = tk.Canvas(
            self.win, bg=_KEY, highlightthickness=0, bd=0, width=self._width, height=120
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # Card body. The canvas draws the rounded outer shell; these native
        # widgets keep keyboard input and accessibility straightforward.
        self._body = tk.Frame(self._canvas, bg=_CARD, highlightthickness=0)

        # Reply preview (shown only after a message has been sent).
        self._exchange = tk.Frame(self._body, bg=_CARD, highlightthickness=0)
        self._exchange_accent = tk.Frame(self._exchange, bg=_ACCENT, width=3)
        self._exchange_accent.pack(side=tk.LEFT, fill=tk.Y, padx=(18, 12), pady=9)
        self._exchange_text = tk.Frame(self._exchange, bg=_CARD, highlightthickness=0)
        self._exchange_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 18), pady=(7, 9))
        self._me_lbl = tk.Label(
            self._exchange_text,
            text="YOU  ·  Ready for your next task",
            anchor=tk.W,
            justify=tk.LEFT,
            bg=_CARD,
            fg=_MUTED,
            font=("Segoe UI", 9),
            wraplength=self._chat_wraplength(),
        )
        self._me_lbl.pack(fill=tk.X, pady=(0, 4))
        self._ai_hdr = tk.Label(
            self._exchange_text,
            text="AI",
            anchor=tk.W,
            bg=_CARD,
            fg=_MUTED,
            font=("Segoe UI Semibold", 8),
        )
        self._ai_hdr.pack(fill=tk.X, pady=(0, 2))
        self._ai_panel = tk.Frame(self._exchange_text, bg=_CARD, highlightthickness=0)
        self._ai_panel.pack(fill=tk.BOTH, expand=True)
        self._ai_scroll = tk.Scrollbar(self._ai_panel, orient=tk.VERTICAL)
        self._ai_view = tk.Text(
            self._ai_panel,
            height=_AI_VIEW_LINES,
            wrap=tk.WORD,
            bg=_CARD,
            fg=_FG,
            font=("Segoe UI Semibold", 10),
            relief=tk.FLAT,
            highlightthickness=0,
            bd=0,
            padx=0,
            pady=0,
            cursor="arrow",
            yscrollcommand=self._ai_scroll.set,
        )
        self._ai_scroll.configure(command=self._ai_view.yview)
        self._ai_view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._ai_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._ai_view.insert("1.0", "Your response will appear here.")
        self._ai_view.configure(state=tk.DISABLED)
        self._ai_view.bind("<MouseWheel>", self._on_ai_wheel)
        self._ai_view.bind("<Enter>", self._on_enter, add="+")
        self._ai_view.bind("<Leave>", self._on_leave, add="+")
        self._ai_scroll.bind("<Enter>", self._on_enter, add="+")
        self._ai_scroll.bind("<Leave>", self._on_leave, add="+")

        # Header: app identity and quiet utility controls.
        self._header = tk.Frame(self._body, bg=_CARD, highlightthickness=0)
        self._header.pack(fill=tk.X, padx=18, pady=(0, 13))

        self._icon_keep: list[tk.PhotoImage] = []
        self._brand_mark = tk.Canvas(
            self._header, width=30, height=30, bg=_CARD, highlightthickness=0, bd=0
        )
        self._brand_mark.pack(side=tk.LEFT, padx=(0, 9))
        self._brand_mark.create_oval(1, 1, 29, 29, fill=self._circle, outline="")
        _place_icon(
            self._brand_mark,
            "spark",
            15,
            15,
            16,
            _ACCENT,
            bg=self._circle,
            tags="brand-icon",
            keep=self._icon_keep,
        )

        self._brand_text = tk.Frame(self._header, bg=_CARD, highlightthickness=0)
        self._brand_text.pack(side=tk.LEFT)
        self._brand_title = tk.Label(
            self._brand_text,
            text="Desktop AI",
            bg=_CARD,
            fg=_FG,
            font=("Segoe UI Semibold", 11),
            anchor=tk.W,
        )
        self._brand_title.pack(anchor=tk.W)
        self._brand_subtitle = tk.Label(
            self._brand_text,
            text="Your personal copilot",
            bg=_CARD,
            fg=_MUTED,
            font=("Segoe UI", 8),
            anchor=tk.W,
        )
        self._brand_subtitle.pack(anchor=tk.W, pady=(1, 0))

        tk.Frame(self._header, bg=_CARD).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.cfg_btn = RoundButton(
            self._header,
            icon="settings",
            icon_size=16,
            command=self._fire_config,
            diameter=30,
            fill=self._circle,
            fg=_MUTED,
            active_fill=_ACCENT_SOFT,
            stroke=self._circle,
        )
        self.cfg_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.close_btn = RoundButton(
            self._header,
            icon="close",
            icon_size=15,
            command=self._fire_close_chat,
            diameter=30,
            fill=self._circle,
            fg=_MUTED,
            active_fill="#ffecef",
            stroke=self._circle,
        )
        self.close_btn.pack(side=tk.LEFT)

        # Composer: spark + prompt + voice/send circles inside one capsule.
        self._top = tk.Canvas(
            self._body,
            height=50,
            bg=_CARD,
            highlightthickness=0,
            bd=0,
        )
        self._top.pack(fill=tk.X, padx=18, pady=(0, 4))
        self._input_focused = False
        self._input_shape = self._top.create_polygon(
            24, 1, 400, 1, 420, 1, 440, 1, 440, 49, 420, 49, 400, 49, 24, 49,
            smooth=True,
            fill=_WHITE,
            outline=_INPUT_STROKE,
            width=1,
            tags="input-shape",
        )

        self._prompt_icon = tk.Canvas(
            self._top, width=22, height=22, bg=_WHITE, highlightthickness=0, bd=0
        )
        _place_icon(
            self._prompt_icon,
            "spark",
            11,
            11,
            16,
            _ACCENT,
            bg=_WHITE,
            tags="prompt-icon",
            keep=self._icon_keep,
        )
        self._prompt_icon_window = self._top.create_window(
            29, 25, window=self._prompt_icon, anchor=tk.CENTER
        )

        self.prompt = tk.Entry(
            self._top,
            bg=_WHITE,
            fg=_FG,
            insertbackground=_ACCENT,
            selectbackground=_ACCENT_SOFT,
            selectforeground=_FG,
            relief=tk.FLAT,
            font=("Segoe UI", 12),
            highlightthickness=0,
            bd=0,
        )
        self._prompt_window = self._top.create_window(
            49, 25, window=self.prompt, anchor=tk.W, width=max(120, self._width - 160)
        )
        self._top.bind("<Configure>", self._layout_composer, add="+")
        self._top.bind("<Button-1>", lambda _e: self.focus_prompt(), add="+")
        self.prompt.bind("<Return>", lambda _e: self._fire_send())
        self.prompt.bind("<Up>", lambda e: self._history_step(-1))
        self.prompt.bind("<Down>", lambda e: self._history_step(1))
        self.prompt.bind("<Key>", self._on_prompt_key, add="+")
        self.prompt.configure(state=tk.DISABLED)
        self._placeholder = "Ask anything…"
        self._ph_active = False
        self.prompt.bind("<FocusIn>", self._clear_placeholder)
        self.prompt.bind("<FocusIn>", lambda _e: self._paint_composer(True), add="+")
        self.prompt.bind("<FocusOut>", self._maybe_placeholder)
        self.prompt.bind("<FocusOut>", lambda _e: self._paint_composer(False), add="+")

        self.mic_btn = RoundButton(
            self._top,
            icon="voice",
            icon_size=15,
            command=self._fire_mic,
            diameter=30,
            fill=self._circle,
            fg=_MUTED,
            active_fill=_ACCENT_SOFT,
            stroke=self._circle,
            widget_bg=_WHITE,
        )
        self._mic_window = self._top.create_window(
            0, 25, window=self.mic_btn, anchor=tk.CENTER, state="hidden"
        )
        if voice_enabled:
            self._top.itemconfigure(self._mic_window, state="normal")
        else:
            self.mic_btn.set_style(enabled=False)

        self.send_btn = RoundButton(
            self._top,
            icon="send",
            icon_size=15,
            command=self._fire_send,
            diameter=30,
            fill=self._circle,
            fg=_MUTED,
            active_fill=_ACCENT_SOFT,
            stroke=self._circle,
            widget_bg=_WHITE,
        )
        self._send_window = self._top.create_window(
            0, 25, window=self.send_btn, anchor=tk.CENTER
        )

        self._body_win = self._canvas.create_window(
            self._pad, self._pad, anchor=tk.NW, window=self._body
        )

        menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="Configuration", command=self._fire_config)
        menu.add_command(label="Close chat", command=self._fire_close_chat)
        menu.add_separator()
        menu.add_command(label="Exit app", command=self._fire_exit)
        self._menu = menu

        self._bind_hover_tree(
            self.win,
            self._canvas,
            self._body,
            self._exchange,
            self._exchange_accent,
            self._exchange_text,
            self._me_lbl,
            self._ai_hdr,
            self._ai_panel,
            self._header,
            self._brand_mark,
            self._brand_text,
            self._brand_title,
            self._brand_subtitle,
            self._top,
            self._prompt_icon,
            self.prompt,
            self.mic_btn,
            self.send_btn,
            self.cfg_btn,
            self.close_btn,
        )
        self._apply_mode("collapsed")

    def _layout_composer(self, event: tk.Event | None = None) -> None:
        """Keep the capsule shape and prompt / action buttons aligned."""
        width = int(event.width) if event is not None else int(self._top.winfo_width())
        height = int(event.height) if event is not None else int(self._top.winfo_height())
        if width < 4 or height < 4:
            return
        self._top.delete("input-shape")
        self._input_shape = _round_rect(
            self._top,
            1,
            1,
            width - 1,
            height - 1,
            height / 2,
            fill=_WHITE,
            outline="#d6d5e2" if self._input_focused else _INPUT_STROKE,
            width=1,
            tags="input-shape",
        )
        self._top.tag_lower("input-shape")
        mid = height / 2
        send_x = width - 24
        mic_x = send_x - 36
        right_pad = 78 if self._voice_enabled else 42
        if hasattr(self, "_prompt_icon_window"):
            self._top.coords(self._prompt_icon_window, 28, mid)
        if hasattr(self, "_prompt_window"):
            self._top.coords(self._prompt_window, 49, mid)
            self._top.itemconfigure(self._prompt_window, width=max(80, width - right_pad - 49))
        if hasattr(self, "_send_window"):
            self._top.coords(self._send_window, send_x, mid)
        if hasattr(self, "_mic_window"):
            self._top.coords(self._mic_window, mic_x, mid)

    def _paint_composer(self, focused: bool) -> None:
        """Apply a subtle focus treatment instead of a harsh system-blue outline."""
        self._input_focused = focused
        self._layout_composer()
        if hasattr(self, "_prompt_icon"):
            color = _ACCENT if focused else "#7774a8"
            self._prompt_icon.configure(bg=_WHITE)
            _place_icon(
                self._prompt_icon,
                "spark",
                11,
                11,
                16,
                color,
                bg=_WHITE,
                tags="prompt-icon",
                keep=self._icon_keep,
            )

    def _on_prompt_key(self, _event: tk.Event) -> str | None:  # type: ignore[type-arg]
        """Block typing while the model is running."""
        if self._busy or not self._ready:
            return "break"
        return None

    def _history_step(self, delta: int) -> str:
        """Browse typed submits with Up/Down (newest first step; max depth capped)."""
        if self._busy or not self._ready or not self._prompt_history:
            return "break"
        if self._history_index is None:
            if delta >= 0:
                return "break"
            self._history_draft = self.get_prompt()
            self._history_index = len(self._prompt_history) - 1
        else:
            nxt = self._history_index + delta
            if nxt < 0:
                self._history_index = 0
            elif nxt >= len(self._prompt_history):
                self._history_index = None
                self._fill_prompt(self._history_draft)
                return "break"
            else:
                self._history_index = nxt
        self._fill_prompt(self._prompt_history[self._history_index])
        return "break"

    def _fill_prompt(self, text: str) -> None:
        self._clear_placeholder()
        self.prompt.configure(state=tk.NORMAL, fg=_FG)
        self.prompt.delete(0, tk.END)
        if text:
            self.prompt.insert(0, text)
        self.prompt.icursor(tk.END)

    def _bind_hover_tree(self, *widgets: tk.Misc) -> None:
        for w in widgets:
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            w.bind("<Button-3>", self._on_context, add="+")

    def _clear_placeholder(self, _event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        if self._ph_active:
            self.prompt.delete(0, tk.END)
            self.prompt.configure(fg=_FG)
            self._ph_active = False

    def _maybe_placeholder(self, _event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        if self._ready and not self._busy and not self.prompt.get().strip():
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.prompt.configure(state=tk.NORMAL, fg=_PLACEHOLDER)
        self.prompt.delete(0, tk.END)
        self.prompt.insert(0, self._placeholder)
        self._ph_active = True

    def _fire_send(self) -> None:
        if self._on_send:
            self._on_send()

    def _fire_mic(self) -> None:
        if self._on_mic:
            self._on_mic()

    def _fire_exit(self) -> None:
        if self._on_exit:
            self._on_exit()

    def _fire_config(self) -> None:
        if self._on_config:
            self._on_config()

    def _fire_close_chat(self) -> None:
        self.close_chat()
        if self._on_close_chat:
            self._on_close_chat()

    def close_chat(self) -> None:
        self._cancel_auto_hide()
        self._cancel_leave()
        self._cancel_typewriter()
        self._cancel_thinking_dots()
        self._user = ""
        self._ai = None
        self._me_lbl.configure(text="YOU  ·")
        self._set_ai_text("", muted=False, follow_end=False)
        try:
            self.win.focus_set()
        except tk.TclError:
            pass
        self._apply_mode("collapsed")

    def _on_ai_wheel(self, event: tk.Event) -> str:  # type: ignore[type-arg]
        """Scroll the AI reply without collapsing the bar."""
        if event.delta:
            self._ai_view.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _set_ai_text(self, text: str, *, muted: bool, follow_end: bool = False) -> None:
        self._ai_view.configure(state=tk.NORMAL, fg=_MUTED if muted else _FG)
        self._ai_view.delete("1.0", tk.END)
        if text:
            self._ai_view.insert("1.0", text)
        self._ai_view.configure(state=tk.DISABLED)
        if follow_end:
            self._ai_view.see(tk.END)
        else:
            self._ai_view.yview_moveto(0.0)
        self._refresh_ai_scroll()

    def _refresh_ai_scroll(self) -> None:
        """Show the scrollbar only when the reply overflows the fixed viewport."""
        try:
            self.win.update_idletasks()
            first, last = self._ai_view.yview()
            if self._ai is None or last - first >= 0.999:
                self._ai_scroll.pack_forget()
            else:
                self._ai_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        except tk.TclError:
            pass

    def _cancel_typewriter(self) -> None:
        if self._type_job is not None:
            self.win.after_cancel(self._type_job)
            self._type_job = None
        self._type_target = ""
        self._type_index = 0

    def _cancel_thinking_dots(self) -> None:
        if self._dot_job is not None:
            self.win.after_cancel(self._dot_job)
            self._dot_job = None
        self._dot_phase = 0

    def _start_thinking_dots(self) -> None:
        self._cancel_thinking_dots()
        self._dot_phase = 0
        self._tick_thinking_dots()

    def _tick_thinking_dots(self) -> None:
        self._dot_job = None
        if self._ai is not None:
            return
        dots = "." * ((self._dot_phase % 3) + 1)
        self._set_ai_text(f"Thinking{dots}", muted=True, follow_end=False)
        self._dot_phase += 1
        self._dot_job = self.win.after(_THINK_DOT_MS, self._tick_thinking_dots)

    def _start_typewriter(self, text: str) -> None:
        self._cancel_typewriter()
        self._type_target = text or ""
        self._type_index = 0
        if not self._type_target:
            self._set_ai_text("", muted=False, follow_end=False)
            return
        # Instant for tiny status pings; animate real answers.
        if len(self._type_target) <= 3:
            self._set_ai_text(self._type_target, muted=False, follow_end=False)
            return
        self._set_ai_text("", muted=False, follow_end=False)
        self._tick_typewriter()

    def _type_chunk_size(self, remaining: int) -> int:
        # Longer replies type a bit faster so they stay snappy.
        total = len(self._type_target)
        if total > 600:
            return min(remaining, 6)
        if total > 200:
            return min(remaining, 3)
        return min(remaining, 2)

    def _tick_typewriter(self) -> None:
        self._type_job = None
        target = self._type_target
        if self._type_index >= len(target):
            self._refresh_ai_scroll()
            if not self._pointer_inside and not self._busy:
                self._arm_auto_hide()
            return
        step = self._type_chunk_size(len(target) - self._type_index)
        self._type_index += step
        self._set_ai_text(target[: self._type_index], muted=False, follow_end=True)
        self._type_job = self.win.after(_TYPE_MS, self._tick_typewriter)

    def _is_typing(self) -> bool:
        return self._type_job is not None

    def _on_context(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _on_enter(self, _event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        self._pointer_inside = True
        self._cancel_leave()
        self._cancel_auto_hide()
        if self._mode == "collapsed":
            target: BarMode = "reply" if (self._user or self._ai is not None) else "expanded"
            self._apply_mode(target)

    def _on_leave(self, _event: tk.Event | None = None) -> None:  # type: ignore[type-arg]
        self._pointer_inside = False
        self._cancel_leave()
        self._leave_job = self.win.after(self._leave_grace_ms, self._maybe_collapse_after_leave)

    def _maybe_collapse_after_leave(self) -> None:
        self._leave_job = None
        if self._pointer_inside:
            return
        if self.win.focus_get() == self.prompt:
            return
        if self._busy or self._is_typing():
            return
        if self._mode == "expanded":
            self._apply_mode("collapsed")
        elif self._mode == "reply" and self._ai is not None:
            self._arm_auto_hide()

    def _cancel_leave(self) -> None:
        if self._leave_job is not None:
            self.win.after_cancel(self._leave_job)
            self._leave_job = None

    def _cancel_auto_hide(self) -> None:
        if self._hide_job is not None:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None

    def _arm_auto_hide(self) -> None:
        self._cancel_auto_hide()
        if self._auto_hide_ms <= 0:
            return
        self._hide_job = self.win.after(self._auto_hide_ms, self._auto_hide)

    def _auto_hide(self) -> None:
        self._hide_job = None
        if self._pointer_inside or self._busy or self._is_typing():
            return
        if self.win.focus_get() == self.prompt:
            return
        self._apply_mode("collapsed")

    def _screen_center_x(self, width: int) -> int:
        sw = self.win.winfo_screenwidth()
        return max(0, (sw - width) // 2)

    def _place(self, width: int, height: int, *, top_gap: bool) -> None:
        x = self._screen_center_x(width)
        y = self._top_margin if top_gap else 0
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self._canvas.configure(width=width, height=height)

    def _draw_card(self, width: int, height: int, *, nub: bool) -> None:
        self._canvas.delete("card")
        self._canvas.delete("nub-icon")
        pad = self._pad
        if nub:
            nw, nh = self._nub_w, self._collapsed_h
            x1 = (width - nw) / 2
            y1 = 0.0
            x2, y2 = x1 + nw, y1 + nh
            _round_rect(
                self._canvas,
                x1,
                y1,
                x2,
                y2,
                nh / 2,
                fill=_NUB_FILL,
                outline=_NUB_STROKE,
                width=1,
                tags="card",
            )
            _place_icon(
                self._canvas,
                "spark",
                x1 + 18,
                (y1 + y2) / 2,
                12,
                "#c4b5ff",
                bg=_NUB_FILL,
                tags="nub-icon",
                keep=self._icon_keep,
            )
            self._canvas.create_text(
                (x1 + x2) / 2 + 6,
                (y1 + y2) / 2,
                text="DESKTOP AI",
                fill="#f4f3ff",
                font=("Segoe UI Semibold", 9),
                tags="card",
            )
            self._canvas.itemconfigure(self._body_win, state="hidden")
            return

        # Soft drop shadow (stacked rounded rects) then the light card
        x1, y1 = float(pad), float(pad)
        x2, y2 = float(width - pad), float(height - pad)
        for i, col in enumerate(("#e9eaf0", _SHADOW, "#d8dae4")):
            o = 2 + i
            _round_rect(
                self._canvas,
                x1 + 1,
                y1 + o,
                x2 + 1,
                y2 + o,
                self._radius,
                fill=col,
                outline="",
                tags="card",
            )
        _round_rect(
            self._canvas,
            x1,
            y1,
            x2,
            y2,
            self._radius,
            fill=_CARD,
            outline=_CARD_STROKE,
            width=1,
            tags="card",
        )
        inner_w = max(1, int(x2 - x1))
        inner_h = max(1, int(y2 - y1))
        inset = min(self._body_inset, max(1, inner_w // 4), max(1, inner_h // 4))
        body_x = x1 + inset
        body_y = y1 + inset
        body_w = max(1, inner_w - inset * 2)
        body_h = max(1, inner_h - inset * 2)
        self._canvas.coords(self._body_win, body_x, body_y)
        self._canvas.itemconfigure(
            self._body_win, width=body_w, height=body_h, state="normal"
        )
        self._body.configure(width=body_w, height=body_h)

    def _reply_panel_size(self) -> tuple[int, int, int]:
        """Return (text_lines, panel_px, window_base_h) for thinking vs answer."""
        if self._ai is None:
            return _AI_THINK_LINES, _AI_THINK_PX, 178
        return _AI_VIEW_LINES, _AI_VIEW_PX, 250

    def _apply_mode(self, mode: BarMode) -> None:
        self._mode = mode
        self._exchange.pack_forget()

        if mode == "collapsed":
            w = self._nub_w + 4
            h = self._collapsed_h + 2
            self._place(w, h, top_gap=False)
            self._draw_card(w, h, nub=True)
            return

        self._reflow_chat()
        if mode == "reply":
            lines, px, base_h = self._reply_panel_size()
            self._ai_view.configure(height=lines)
            self._ai_panel.configure(height=px)
            self._ai_panel.pack_propagate(False)
            self._exchange.pack(fill=tk.X, padx=18, before=self._top)
        else:
            base_h = 128

        self._place(self._width, base_h, top_gap=True)
        self._draw_card(self._width, base_h, nub=False)
        self.win.update_idletasks()
        needed = (
            self._body.winfo_reqheight()
            + self._pad * 2
            + self._body_inset * 2
            + 4
        )
        # Cap so the overlay stays on-screen; overflow scrolls inside AI panel.
        max_h = max(base_h, int(self.win.winfo_screenheight() * 0.42))
        needed = min(max(needed, base_h), max_h)
        if needed != base_h:
            self._place(self._width, needed, top_gap=True)
            self._draw_card(self._width, needed, nub=False)

    def show(self) -> None:
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self._apply_mode(self._mode)

    def hide(self) -> None:
        self._cancel_leave()
        self._cancel_auto_hide()
        self.win.withdraw()

    def destroy(self) -> None:
        self._cancel_leave()
        self._cancel_auto_hide()
        self._cancel_typewriter()
        self._cancel_thinking_dots()
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def set_ready(self, ready: bool) -> None:
        self._ready = ready
        self._refresh_controls()
        if ready:
            self.show()
            self._apply_mode("expanded")
            self._show_placeholder()
            self.focus_prompt()
            self.win.after(4000, self._idle_intro_collapse)
        else:
            self.hide()

    def _idle_intro_collapse(self) -> None:
        if self._pointer_inside or self._busy or self._mode == "reply":
            return
        if self.win.focus_get() == self.prompt and not self._ph_active and self.prompt.get().strip():
            return
        if self._mode == "expanded":
            self._apply_mode("collapsed")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_controls()
        if busy:
            self._cancel_auto_hide()
            # Pull focus out of the entry so the user cannot keep typing.
            try:
                self.win.focus_set()
            except tk.TclError:
                pass

    def _chat_wraplength(self) -> int:
        return max(220, self._width - 140)

    def _chat_clip_chars(self) -> int:
        # YOU row only — AI replies scroll instead of hard-clipping.
        return max(100, self._width // 3)

    def _reflow_chat(self) -> None:
        wrap = self._chat_wraplength()
        self._me_lbl.configure(wraplength=wrap)
        # Keep the Text widget width in sync with the card so wrapping matches.
        try:
            self._ai_view.configure(width=max(28, wrap // 7))
        except tk.TclError:
            pass

    def _refresh_controls(self) -> None:
        ok = self._ready and not self._busy
        circle = self._circle
        if ok:
            self.prompt.configure(state=tk.NORMAL)
            if not self.prompt.get().strip() and not self._ph_active:
                self._show_placeholder()
        else:
            if self._ph_active:
                # Clear the visual placeholder before disabling, so it cannot
                # accidentally be sent as a real user prompt after processing.
                self.prompt.configure(state=tk.NORMAL)
                self.prompt.delete(0, tk.END)
                self._ph_active = False
            self.prompt.configure(state=tk.DISABLED, fg=_FG)
        self.send_btn.set_style(
            enabled=ok,
            fill=circle,
            fg=_ACCENT if ok else _MUTED,
            active_fill=_ACCENT_SOFT if ok else circle,
        )
        if self._voice_enabled and self._busy:
            self.mic_btn.set_style(
                text="",
                icon="voice",
                fill=circle,
                fg=_MUTED,
                active_fill=circle,
                enabled=False,
            )

    def set_mic_phase(self, phase: str, *, can_start: bool) -> None:
        if not self._voice_enabled:
            return
        circle = self._circle
        if phase == "recording":
            self.mic_btn.set_style(
                text="",
                icon="stop",
                fill="#ffe5e5",
                fg=_DANGER,
                active_fill="#ffd0d0",
                enabled=True,
            )
        elif phase == "transcribing":
            self.mic_btn.set_style(
                text="",
                icon="voice",
                fill=circle,
                fg=_MUTED,
                active_fill=circle,
                enabled=False,
            )
        else:
            self.mic_btn.set_style(
                text="",
                icon="voice",
                fill=circle,
                fg=_MUTED if can_start else _MUTED,
                active_fill=_ACCENT_SOFT if can_start else circle,
                enabled=can_start and not self._busy,
            )

    def get_prompt(self) -> str:
        if self._ph_active:
            return ""
        return self.prompt.get().strip()

    def clear_prompt(self) -> None:
        text = self.get_prompt()
        if text and (not self._prompt_history or self._prompt_history[-1] != text):
            self._prompt_history.append(text)
            if len(self._prompt_history) > _PROMPT_HISTORY_MAX:
                self._prompt_history = self._prompt_history[-_PROMPT_HISTORY_MAX:]
        self._history_index = None
        self._history_draft = ""
        was = str(self.prompt.cget("state"))
        self.prompt.configure(state=tk.NORMAL)
        self.prompt.delete(0, tk.END)
        self._ph_active = False
        self.prompt.configure(state=was, fg=_FG)
        if self._ready and not self._busy:
            self._show_placeholder()

    def focus_prompt(self) -> None:
        if self._ready and not self._busy:
            self.prompt.configure(state=tk.NORMAL)
            self.prompt.focus_force()

    def set_exchange(self, user: str, ai: str | None) -> None:
        self._cancel_typewriter()
        self._cancel_thinking_dots()
        self._cancel_auto_hide()
        self._user = user or ""
        self._ai = ai
        me, ai_body = format_exchange_lines(self._user, self._ai)
        limit = self._chat_clip_chars()
        self._reflow_chat()
        self._me_lbl.configure(text=clip_line(me, max_chars=limit).replace("me:", "YOU  ·", 1))
        # Resize first: compact while thinking, taller once the answer exists.
        self._apply_mode("reply")
        self.show()
        if ai is None:
            self._start_thinking_dots()
        else:
            self._start_typewriter(ai_body)

    def status_message(self, text: str) -> None:
        if not self._user:
            self._user = "(system)"
        self.set_exchange(self._user, text)
