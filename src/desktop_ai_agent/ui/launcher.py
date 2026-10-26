from __future__ import annotations

import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Any

from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.config_loader import load_setup
from desktop_ai_agent.core.session_setup import SessionSetup
from desktop_ai_agent.input.voice import (
    PushToTalk,
    VoiceError,
    WakeListener,
    WhisperEngine,
    engine_from_setup,
    from_setup,
    voice_available,
    wake_enabled,
    wake_from_setup,
)
from desktop_ai_agent.orchestrator.agent_loop import TaskResult
from desktop_ai_agent.orchestrator.service import AgentService
from desktop_ai_agent.output.tts import (
    Speaker,
    TtsError,
    from_setup as tts_from_setup,
    tts_available,
    tts_enabled,
)
from desktop_ai_agent.ui.event_format import format_event
from desktop_ai_agent.ui.overlay_bar import OverlayBar


class LauncherApp:
    """Setup window, then always-on-top top-edge chat bar."""

    def __init__(self, setup: dict[str, Any] | None = None) -> None:
        self.setup = setup or load_setup()
        ui = self.setup.get("ui", {})
        self.root = tk.Tk()
        self.root.title(ui.get("title", "Desktop AI Agent — Configuration"))
        self.root.geometry(f"{ui.get('width', 820)}x{ui.get('height', 640)}")
        self.root.minsize(480, 360)
        # Hide configuration → return to chat bar (do not quit the app).
        self.root.protocol("WM_DELETE_WINDOW", self._on_config_close)

        self._busy = False
        self._chat_ready = False
        self._chat: ChatClient | None = None
        self._agent: AgentService | None = None
        self._voice_enabled = bool((self.setup.get("voice") or {}).get("enabled", True))
        self._wake_enabled = wake_enabled(self.setup)
        self._engine: WhisperEngine | None = None
        self._ptt: PushToTalk | None = None
        self._wake: WakeListener | None = None
        self._mic_phase = "idle"  # idle | recording | transcribing
        self._tts_enabled = tts_enabled(self.setup)
        self._speaker: Speaker | None = None
        self._tts_speaking = False
        self._tts_keep_command = False
        self._last_user = ""
        self._bar: OverlayBar | None = None

        self._build()
        self._ensure_bar()
        if ui.get("auto_open", True):
            self.root.after(200, self._on_open)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(frame, text="Configuration", font=("Segoe UI", 14, "bold"))
        title.pack(anchor=tk.W, padx=4, pady=(0, 4))

        ollama = self.setup.get("ollama", {})
        summary = (
            f"Model: {ollama.get('agent_model', 'gemma4-agent')}  |  "
            f"Base: {ollama.get('base_model', 'gemma4')}  |  "
            "serve → pull → configure → top-edge chat bar"
        )
        ttk.Label(frame, text=summary).pack(anchor=tk.W, padx=4, pady=(0, 8))

        ttk.Label(frame, text="Setup log").pack(anchor=tk.W, padx=4)
        self.log = scrolledtext.ScrolledText(
            frame, height=18, state=tk.DISABLED, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, padx=4)

        self.open_btn = ttk.Button(buttons, text="Run setup again", command=self._on_open)
        self.open_btn.pack(side=tk.LEFT)

        self.back_btn = ttk.Button(buttons, text="Back to chat", command=self._on_config_close)
        self.back_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.exit_btn = ttk.Button(buttons, text="Exit app", command=self._on_close)
        self.exit_btn.pack(side=tk.RIGHT)

        self._append_log("Starting automatically from: python -m app")
        self._append_log("Waiting for setup... the chat bar appears when ready.")

    def _ensure_bar(self) -> OverlayBar:
        if self._bar is None:
            ui = self.setup.get("ui", {})
            self._bar = OverlayBar(
                self.root,
                ui=ui if isinstance(ui, dict) else {},
                voice_enabled=self._voice_enabled,
                on_send=self._on_send,
                on_mic=self._on_mic,
                on_exit=self._on_close,
                on_config=self._open_config,
                on_close_chat=self._on_bar_close_chat,
            )
        return self._bar

    def _open_config(self) -> None:
        """Show the configuration / setup log window without quitting chat."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_config_close(self) -> None:
        """Close configuration window; keep the chat bar if the agent is ready."""
        if self._chat_ready:
            self._hide_setup_window()
            bar = self._ensure_bar()
            bar.show()
        else:
            self._on_close()

    def _on_bar_close_chat(self) -> None:
        """Chat ✕ already collapses the bar; keep a breadcrumb in the log."""
        self._append_log("[ui] chat closed — hover the top pill to open again")

    def _show_setup_window(self) -> None:
        self._open_config()

    def _hide_setup_window(self) -> None:
        self.root.withdraw()

    def _ensure_speaker(self) -> Speaker | None:
        if not self._tts_enabled:
            return None
        if self._speaker is not None:
            return self._speaker
        if not tts_available():
            self._append_log('[tts] deps missing. Install with: pip install -e ".[tts]"')
            self._tts_enabled = False
            return None
        try:
            self._speaker = tts_from_setup(self.setup)
        except TtsError as exc:
            self._append_log(f"[tts] {exc}")
            self._tts_enabled = False
            return None
        return self._speaker

    def _speak(self, text: str, *, keep_command: bool = False) -> None:
        speaker = self._ensure_speaker()
        if speaker is None:
            return
        self._tts_speaking = True
        self._tts_keep_command = keep_command
        self._pause_wake(keep_command=keep_command)

        def on_done() -> None:
            self.root.after(0, self._on_tts_done)

        try:
            speaker.speak(text, on_done=on_done)
        except TtsError as exc:
            self._tts_speaking = False
            self._tts_keep_command = False
            self._append_log(f"[tts] {exc}")
            self._resume_wake(refresh_command=keep_command)

    def _on_tts_done(self) -> None:
        keep = self._tts_keep_command
        self._tts_speaking = False
        self._tts_keep_command = False
        self._resume_wake(refresh_command=keep)

    def _append_log(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        was_busy = self._busy
        self._busy = busy
        mic_busy = self._mic_phase != "idle"
        self.open_btn.configure(state=tk.DISABLED if (busy or mic_busy) else tk.NORMAL)
        bar = self._ensure_bar()
        bar.set_busy(busy or mic_busy)
        self._refresh_mic_button()
        if busy and not was_busy:
            self._pause_wake()
        elif not busy and was_busy and self._mic_phase == "idle":
            self._resume_wake()

    def _set_chat_ready(self, ready: bool, chat: ChatClient | None = None) -> None:
        self._chat_ready = ready
        self._chat = chat
        bar = self._ensure_bar()
        if ready:
            self._hide_setup_window()
            bar.set_ready(True)
            self._start_wake_if_needed()
        else:
            bar.set_ready(False)
            self._stop_wake()
            self._show_setup_window()
        self._refresh_mic_button()

    def _set_agent(self, agent: AgentService | None) -> None:
        self._agent = agent

    def _refresh_mic_button(self) -> None:
        if not self._voice_enabled:
            return
        can_start = self._chat_ready and not self._busy and self._agent is not None
        self._ensure_bar().set_mic_phase(self._mic_phase, can_start=can_start)

    def _ensure_engine(self) -> WhisperEngine:
        if self._engine is None:
            self._engine = engine_from_setup(self.setup)
        return self._engine

    def _ensure_ptt(self) -> PushToTalk:
        if self._ptt is None:
            self._ptt = from_setup(self.setup, engine=self._ensure_engine())
        return self._ptt

    def _start_wake_if_needed(self) -> None:
        if not self._voice_enabled or not self._wake_enabled:
            return
        if not voice_available():
            self._append_log(
                '[voice] Wake word needs voice deps: pip install -e ".[voice]"'
            )
            return
        if self._wake is not None and self._wake.is_running:
            return

        def on_command(text: str) -> None:
            self.root.after(0, self._on_wake_command, text)

        def on_status(kind: str, detail: str) -> None:
            self.root.after(0, self._on_wake_status, kind, detail)

        try:
            self._wake = wake_from_setup(
                self.setup,
                on_command=on_command,
                on_status=on_status,
                engine=self._ensure_engine(),
            )
            self._wake.start()
            if self._busy or self._mic_phase != "idle":
                self._wake.pause()
        except VoiceError as exc:
            self._append_log(f"[voice] {exc}")
            self._wake = None

    def _stop_wake(self) -> None:
        wake = self._wake
        self._wake = None
        if wake is not None:
            wake.stop()

    def _pause_wake(self, *, keep_command: bool = False) -> None:
        if self._wake is not None and self._wake.is_running:
            self._wake.pause(keep_command=keep_command)

    def _resume_wake(self, *, refresh_command: bool = False) -> None:
        if (
            self._wake is not None
            and self._wake.is_running
            and self._chat_ready
            and not self._busy
            and self._mic_phase == "idle"
            and not self._tts_speaking
        ):
            self._wake.resume(refresh_command=refresh_command)

    def _on_wake_status(self, kind: str, detail: str) -> None:
        if kind == "listening":
            if detail.startswith("hey"):
                self._append_log(f"[voice] listening for '{detail}'...")
            elif detail.startswith("stopped"):
                self._append_log("[voice] sleeping — say hey to wake again")
            elif detail == "no command after wake":
                self._append_log("[voice] no command after wake — listening again")
        elif kind == "heard_wake":
            speaker = self._ensure_speaker()
            ack = (
                speaker.wake_ack
                if speaker is not None
                else (
                    ((self.setup.get("output") or {}).get("tts") or {}).get("wake_ack")
                    or "yes my lord?"
                )
            )
            self._append_log(f"{ack} (wake: {detail})")
            if speaker is not None:
                self._speak(ack, keep_command=True)
        elif kind == "miss":
            self._append_log(f'[voice] heard "{detail}" (not wake)')
        elif kind == "error":
            self._append_log(f"[voice] {detail}")
            self._ensure_bar().status_message(f"[voice] {detail}")

    def _on_wake_command(self, text: str) -> None:
        if self._busy or not self._chat_ready or self._agent is None:
            return
        text = (text or "").strip()
        if not text:
            return
        self._send_agent(text)

    def _on_mic(self) -> None:
        if not self._voice_enabled:
            return
        if self._mic_phase == "recording":
            self._stop_mic_and_submit()
            return
        if self._busy or not self._chat_ready or self._agent is None:
            return
        if not voice_available():
            self._append_log(
                '[mic] Voice deps missing. Install with: pip install -e ".[voice]"'
            )
            self._ensure_bar().status_message("Voice deps missing")
            return
        self._pause_wake()
        try:
            ptt = self._ensure_ptt()
            ptt.start()
        except VoiceError as exc:
            self._append_log(f"[mic] {exc}")
            self._ensure_bar().status_message(str(exc))
            self._resume_wake()
            return
        self._mic_phase = "recording"
        self.open_btn.configure(state=tk.DISABLED)
        self._ensure_bar().set_busy(True)
        self._refresh_mic_button()
        self._append_log("[mic] recording... click Stop when done")

    def _stop_mic_and_submit(self) -> None:
        ptt = self._ptt
        if ptt is None:
            self._mic_phase = "idle"
            self._refresh_mic_button()
            self._resume_wake()
            return
        self._mic_phase = "transcribing"
        self._refresh_mic_button()
        self._append_log("[mic] transcribing...")

        def worker() -> None:
            try:
                text = ptt.stop_and_transcribe()
            except VoiceError as exc:
                self.root.after(0, self._on_mic_failed, str(exc))
                return
            self.root.after(0, self._on_mic_transcript, text)

        threading.Thread(target=worker, daemon=True, name="voice-stt").start()

    def _on_mic_failed(self, message: str) -> None:
        self._mic_phase = "idle"
        self._set_busy(self._busy)
        self._resume_wake()
        self._append_log(f"[mic] {message}")
        self._ensure_bar().status_message(message)

    def _on_mic_transcript(self, text: str) -> None:
        self._mic_phase = "idle"
        if not text:
            self._set_busy(self._busy)
            self._resume_wake()
            self._append_log("[mic] no speech detected")
            return
        self._send_agent(text)

    def _on_close(self) -> None:
        self._stop_wake()
        if self._ptt is not None and self._ptt.is_recording:
            self._ptt.cancel()
        if self._speaker is not None:
            self._speaker.stop()
        if self._bar is not None:
            self._bar.destroy()
            self._bar = None
        self.root.destroy()

    def _on_open(self) -> None:
        if self._busy:
            return
        self._stop_wake()
        self._set_busy(True)
        self._set_chat_ready(False)
        self._set_agent(None)
        self._last_user = ""
        self._show_setup_window()
        self._append_log("--- Full setup ---")

        def worker() -> None:
            try:
                session = SessionSetup(self.setup)
                chat = session.run_full_open(log=lambda m: self.root.after(0, self._append_log, m))
                agent = AgentService.create(model=session.base_model, host=session.host)
                self.root.after(0, self._set_agent, agent)
                self.root.after(0, self._set_chat_ready, True, chat)
                self.root.after(
                    0,
                    self._append_log,
                    "[ready] top-edge bar is active — type, Mic, or say 'hey' to wake. "
                    "Right-click the bar for Exit / Setup again.",
                )
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, self._append_log, f"ERROR: {exc}")
                self.root.after(0, self._show_setup_window)
            finally:
                self.root.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_send(self) -> None:
        if self._busy or not self._chat_ready or self._agent is None:
            return
        bar = self._ensure_bar()
        text = bar.get_prompt()
        if not text:
            return
        bar.clear_prompt()
        self._send_agent(text)

    def _send_agent(self, text: str) -> None:
        agent = self._agent
        assert agent is not None
        self._last_user = text
        self._set_busy(True)
        self._ensure_bar().set_exchange(text, None)
        self._append_log(f"> {text}")

        def on_event(kind: str, data: Any) -> None:
            try:
                line = format_event(kind, data)
            except Exception as exc:  # noqa: BLE001
                line = f"[display error] {exc}\n"
            if line:
                self.root.after(0, self._append_log, line.rstrip("\n"))
            if kind == "final" and data:
                reply = str(data)
                self.root.after(0, self._on_agent_reply, reply)
                self.root.after(0, self._speak, reply)

        def on_done(result: TaskResult) -> None:
            if result.status != "done":
                msg = f"[{result.status}] {result.text}"
                self.root.after(0, self._append_log, msg)
                self.root.after(0, self._on_agent_reply, msg)
                if result.text:
                    self.root.after(0, self._speak, msg)
            self.root.after(0, self._set_busy, False)

        if not agent.submit(text, on_event=on_event, on_done=on_done):
            self._append_log("[busy] the agent is still working on the previous request")
            self._ensure_bar().status_message("Busy — wait for the previous request")
            self._set_busy(False)

    def _on_agent_reply(self, reply: str) -> None:
        self._ensure_bar().set_exchange(self._last_user, reply)

    def run(self) -> None:
        self.root.mainloop()
