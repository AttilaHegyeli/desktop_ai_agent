from __future__ import annotations

import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Any

from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.config_loader import load_setup
from desktop_ai_agent.core.session_setup import SessionSetup


class LauncherApp:
    """Setup + embedded terminal chat in one window."""

    def __init__(self, setup: dict[str, Any] | None = None) -> None:
        self.setup = setup or load_setup()
        ui = self.setup.get("ui", {})
        self.root = tk.Tk()
        self.root.title(ui.get("title", "Desktop AI Agent — Ollama Setup"))
        self.root.geometry(f"{ui.get('width', 820)}x{ui.get('height', 640)}")
        self.root.minsize(640, 480)

        self._busy = False
        self._chat_ready = False
        self._chat: ChatClient | None = None
        self._build()
        if ui.get("auto_open", True):
            self.root.after(200, self._on_open)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(frame, text="Ollama / Gemma4 Setup", font=("Segoe UI", 14, "bold"))
        title.pack(anchor=tk.W, padx=4, pady=(0, 4))

        ollama = self.setup.get("ollama", {})
        summary = (
            f"Model: {ollama.get('agent_model', 'gemma4-agent')}  |  "
            f"Base: {ollama.get('base_model', 'gemma4')}  |  "
            "serve → pull → configure → in-app terminal"
        )
        ttk.Label(frame, text=summary).pack(anchor=tk.W, padx=4, pady=(0, 8))

        ttk.Label(frame, text="Setup log").pack(anchor=tk.W, padx=4)
        self.log = scrolledtext.ScrolledText(
            frame, height=8, state=tk.DISABLED, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.log.pack(fill=tk.BOTH, expand=False, padx=4, pady=(0, 8))

        ttk.Label(frame, text="Terminal").pack(anchor=tk.W, padx=4)
        self.terminal = scrolledtext.ScrolledText(
            frame,
            height=16,
            state=tk.DISABLED,
            wrap=tk.WORD,
            font=("Consolas", 10),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.terminal.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 8))

        prompt_row = ttk.Frame(frame)
        prompt_row.pack(fill=tk.X, padx=4, pady=(0, 8))

        ttk.Label(prompt_row, text=">").pack(side=tk.LEFT)
        self.prompt = ttk.Entry(prompt_row)
        self.prompt.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        self.prompt.bind("<Return>", lambda _e: self._on_send())
        self.prompt.configure(state=tk.DISABLED)

        self.send_btn = ttk.Button(prompt_row, text="Send", command=self._on_send, state=tk.DISABLED)
        self.send_btn.pack(side=tk.RIGHT)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, padx=4)

        self.open_btn = ttk.Button(buttons, text="Setup again", command=self._on_open)
        self.open_btn.pack(side=tk.LEFT)

        self.exit_btn = ttk.Button(buttons, text="Exit", command=self.root.destroy)
        self.exit_btn.pack(side=tk.RIGHT)

        self._append_log("Starting automatically from: python -m app")
        self._append_term("Waiting for setup...\n")

    def _append_log(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _append_term(self, message: str, *, newline: bool = False) -> None:
        self.terminal.configure(state=tk.NORMAL)
        self.terminal.insert(tk.END, message + ("\n" if newline else ""))
        self.terminal.see(tk.END)
        self.terminal.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.open_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)
        chat_state = tk.NORMAL if (self._chat_ready and not busy) else tk.DISABLED
        self.prompt.configure(state=chat_state)
        self.send_btn.configure(state=chat_state)

    def _set_chat_ready(self, ready: bool, chat: ChatClient | None = None) -> None:
        self._chat_ready = ready
        self._chat = chat
        if ready:
            self.prompt.configure(state=tk.NORMAL)
            self.send_btn.configure(state=tk.NORMAL)
            self.prompt.focus_set()
        else:
            self.prompt.configure(state=tk.DISABLED)
            self.send_btn.configure(state=tk.DISABLED)

    def _on_open(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_chat_ready(False)
        self._append_log("--- Full setup ---")
        self._append_term("\n[setup] configuring agent...\n")

        def worker() -> None:
            try:
                session = SessionSetup(self.setup)
                chat = session.run_full_open(log=lambda m: self.root.after(0, self._append_log, m))
                self.root.after(0, self._set_chat_ready, True, chat)
                self.root.after(
                    0,
                    self._append_term,
                    "[ready] type a prompt below. JSON replies: {\"script\",\"code\"}\n",
                )
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, self._append_log, f"ERROR: {exc}")
                self.root.after(0, self._append_term, f"[error] {exc}\n")
            finally:
                self.root.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_send(self) -> None:
        if self._busy or not self._chat_ready or self._chat is None:
            return
        text = self.prompt.get().strip()
        if not text:
            return
        self.prompt.delete(0, tk.END)
        self._append_term(f"\n> {text}\n")
        self._set_busy(True)

        chat = self._chat

        def worker() -> None:
            try:
                self.root.after(0, self._append_term, "< ")

                def on_chunk(piece: str) -> None:
                    self.root.after(0, self._append_term, piece)

                chat.ask_stream(text, on_chunk=on_chunk)
                self.root.after(0, self._append_term, "\n")
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, self._append_term, f"\n[error] {exc}\n")
            finally:
                self.root.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()
