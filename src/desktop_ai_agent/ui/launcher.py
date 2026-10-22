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


class LauncherApp:
    """Setup + embedded terminal chat in one window."""

    def __init__(self, setup: dict[str, Any] | None = None) -> None:
        self.setup = setup or load_setup()
        ui = self.setup.get("ui", {})
        self.root = tk.Tk()
        self.root.title(ui.get("title", "Desktop AI Agent — Ollama Setup"))
        self.root.geometry(f"{ui.get('width', 820)}x{ui.get('height', 640)}")
        self.root.minsize(640, 480)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._busy = False
        self._chat_ready = False
        self._chat: ChatClient | None = None  # gemma4-agent profile from setup; no longer used for chatting
        self._agent: AgentService | None = None  # what prompts actually go through now
        self._voice_enabled = bool((self.setup.get("voice") or {}).get("enabled", True))
        self._wake_enabled = wake_enabled(self.setup)
        self._engine: WhisperEngine | None = None
        self._ptt: PushToTalk | None = None
        self._wake: WakeListener | None = None
        self._mic_phase = "idle"  # idle | recording | transcribing
        self._tts_enabled = tts_enabled(self.setup)
        self._speaker: Speaker | None = None
        self._tts_speaking = False
        self._tts_keep_command = False  # wake-ack: resume into command phase
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

        self.mic_btn = ttk.Button(
            prompt_row, text="Mic", command=self._on_mic, state=tk.DISABLED
        )
        if self._voice_enabled:
            self.mic_btn.pack(side=tk.RIGHT, padx=(0, 6))

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, padx=4)

        self.open_btn = ttk.Button(buttons, text="Setup again", command=self._on_open)
        self.open_btn.pack(side=tk.LEFT)

        self.exit_btn = ttk.Button(buttons, text="Exit", command=self._on_close)
        self.exit_btn.pack(side=tk.RIGHT)

        self._append_log("Starting automatically from: python -m app")
        self._append_term("Waiting for setup...\n")

    def _ensure_speaker(self) -> Speaker | None:
        """Lazy-create Speaker when TTS is enabled and deps are installed."""
        if not self._tts_enabled:
            return None
        if self._speaker is not None:
            return self._speaker
        if not tts_available():
            self._append_term(
                '[tts] deps missing. Install with: pip install -e ".[tts]"\n'
            )
            self._tts_enabled = False
            return None
        try:
            self._speaker = tts_from_setup(self.setup)
        except TtsError as exc:
            self._append_term(f"[tts] {exc}\n")
            self._tts_enabled = False
            return None
        return self._speaker

    def _speak(self, text: str, *, keep_command: bool = False) -> None:
        """Speak text if TTS is on; mute mic until playback finishes.

        keep_command=True: wake-ack path — stay in command phase so the user
        can say the task right after 'yes my lord?'.
        """
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
            self._append_term(f"[tts] {exc}\n")
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

    def _append_term(self, message: str, *, newline: bool = False) -> None:
        self.terminal.configure(state=tk.NORMAL)
        self.terminal.insert(tk.END, message + ("\n" if newline else ""))
        self.terminal.see(tk.END)
        self.terminal.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        was_busy = self._busy
        self._busy = busy
        mic_busy = self._mic_phase != "idle"
        self.open_btn.configure(state=tk.DISABLED if (busy or mic_busy) else tk.NORMAL)
        chat_ok = self._chat_ready and not busy and not mic_busy
        chat_state = tk.NORMAL if chat_ok else tk.DISABLED
        self.prompt.configure(state=chat_state)
        self.send_btn.configure(state=chat_state)
        self._refresh_mic_button()
        # Pause wake captures while the agent works; resume when idle again.
        if busy and not was_busy:
            self._pause_wake()
        elif not busy and was_busy and self._mic_phase == "idle":
            self._resume_wake()

    def _set_chat_ready(self, ready: bool, chat: ChatClient | None = None) -> None:
        self._chat_ready = ready
        self._chat = chat
        if ready:
            self.prompt.configure(state=tk.NORMAL)
            self.send_btn.configure(state=tk.NORMAL)
            self.prompt.focus_set()
            self._start_wake_if_needed()
        else:
            self.prompt.configure(state=tk.DISABLED)
            self.send_btn.configure(state=tk.DISABLED)
            self._stop_wake()
        self._refresh_mic_button()

    def _set_agent(self, agent: AgentService | None) -> None:
        self._agent = agent

    def _refresh_mic_button(self) -> None:
        if not self._voice_enabled:
            return
        if self._mic_phase == "recording":
            self.mic_btn.configure(text="Stop", state=tk.NORMAL)
        elif self._mic_phase == "transcribing":
            self.mic_btn.configure(text="...", state=tk.DISABLED)
        else:
            can_start = self._chat_ready and not self._busy and self._agent is not None
            self.mic_btn.configure(
                text="Mic", state=tk.NORMAL if can_start else tk.DISABLED
            )

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
            self._append_term(
                '[voice] Wake word needs voice deps: pip install -e ".[voice]"\n'
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
            self._append_term(f"[voice] {exc}\n")
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
                self._append_term(f"[voice] listening for '{detail}'...\n")
            elif detail.startswith("stopped"):
                self._append_term("[voice] sleeping — say hey to wake again\n")
            elif detail == "no command after wake":
                self._append_term("[voice] no command after wake — listening again\n")
        elif kind == "heard_wake":
            # Acknowledgement in the terminal + speakers when the wake word is detected.
            speaker = self._ensure_speaker()
            ack = (
                speaker.wake_ack
                if speaker is not None
                else (
                    ((self.setup.get("output") or {}).get("tts") or {}).get("wake_ack")
                    or "yes my lord?"
                )
            )
            self._append_term(f"{ack}\n")
            self._append_log(f"{ack} (wake: {detail})")
            if speaker is not None:
                # Mute mic during ack but stay in command phase for the task.
                self._speak(ack, keep_command=True)
        elif kind == "miss":
            # What Whisper heard that did not match — helps tune aliases.
            self._append_term(f'[voice] heard "{detail}" (not wake)\n')
        elif kind == "error":
            self._append_term(f"[voice] {detail}\n")
        # "capturing" / "paused" are quiet (command path or mic handoff)

    def _on_wake_command(self, text: str) -> None:
        if self._busy or not self._chat_ready or self._agent is None:
            return
        text = (text or "").strip()
        if not text:
            return
        self._append_term(f"\n> {text}\n")
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
            self._append_term(
                '[mic] Voice deps missing. Install with: pip install -e ".[voice]"\n'
            )
            return
        self._pause_wake()
        try:
            ptt = self._ensure_ptt()
            ptt.start()
        except VoiceError as exc:
            self._append_term(f"[mic] {exc}\n")
            self._resume_wake()
            return
        self._mic_phase = "recording"
        self.open_btn.configure(state=tk.DISABLED)
        self.prompt.configure(state=tk.DISABLED)
        self.send_btn.configure(state=tk.DISABLED)
        self._refresh_mic_button()
        self._append_term("[mic] recording... click Stop when done\n")

    def _stop_mic_and_submit(self) -> None:
        ptt = self._ptt
        if ptt is None:
            self._mic_phase = "idle"
            self._refresh_mic_button()
            self._resume_wake()
            return
        self._mic_phase = "transcribing"
        self._refresh_mic_button()
        self._append_term("[mic] transcribing...\n")

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
        self._set_busy(self._busy)  # refresh prompt/send/open/mic from current flags
        self._resume_wake()
        self._append_term(f"[mic] {message}\n")

    def _on_mic_transcript(self, text: str) -> None:
        self._mic_phase = "idle"
        if not text:
            self._set_busy(self._busy)
            self._resume_wake()
            self._append_term("[mic] no speech detected\n")
            return
        self._append_term(f"\n> {text}\n")
        self._send_agent(text)  # _set_busy(True) keeps wake paused until done

    def _on_close(self) -> None:
        self._stop_wake()
        if self._ptt is not None and self._ptt.is_recording:
            self._ptt.cancel()
        if self._speaker is not None:
            self._speaker.stop()
        self.root.destroy()

    def _on_open(self) -> None:
        if self._busy:
            return
        self._stop_wake()
        self._set_busy(True)
        self._set_chat_ready(False)
        self._set_agent(None)
        self._append_log("--- Full setup ---")
        self._append_term("\n[setup] configuring agent...\n")

        def worker() -> None:
            try:
                session = SessionSetup(self.setup)
                chat = session.run_full_open(log=lambda m: self.root.after(0, self._append_log, m))
                # Prompts go through the action layer using the BASE model plus our own
                # system prompt (see orchestrator/prompts.py). The gemma4-agent profile
                # above is still created by setup, but it is not used for chatting.
                agent = AgentService.create(model=session.base_model, host=session.host)
                self.root.after(0, self._set_agent, agent)
                self.root.after(0, self._set_chat_ready, True, chat)
                self.root.after(
                    0,
                    self._append_term,
                    "[ready] type, use Mic, or say 'hey' to wake. Stay awake for more "
                    "requests; say 'stop' to sleep. I act through the action layer; "
                    "if no action fits, I can offer a script as JSON.\n",
                )
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, self._append_log, f"ERROR: {exc}")
                self.root.after(0, self._append_term, f"[error] {exc}\n")
            finally:
                self.root.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_send(self) -> None:
        if self._busy or not self._chat_ready or self._agent is None:
            return
        text = self.prompt.get().strip()
        if not text:
            return
        self.prompt.delete(0, tk.END)
        self._append_term(f"\n> {text}\n")
        self._send_agent(text)

    def _send_agent(self, text: str) -> None:
        """Run the prompt through the action layer; events stream into the terminal."""
        agent = self._agent
        assert agent is not None
        self._set_busy(True)
        self._append_term("< ")

        def on_event(kind: str, data: Any) -> None:  # runs on the agent's thread
            try:
                line = format_event(kind, data)
            except Exception as exc:  # noqa: BLE001
                line = f"[display error] {exc}\n"
            if line:
                self.root.after(0, self._append_term, line)
            if kind == "final" and data:
                self.root.after(0, self._speak, str(data))

        def on_done(result: TaskResult) -> None:  # runs on the agent's thread
            if result.status != "done":  # on "done" the final text was already shown
                msg = f"[{result.status}] {result.text}"
                self.root.after(0, self._append_term, msg + "\n")
                if result.text:
                    self.root.after(0, self._speak, msg)
            self.root.after(0, self._set_busy, False)

        if not agent.submit(text, on_event=on_event, on_done=on_done):
            self._append_term("[busy] the agent is still working on the previous request\n")
            self._set_busy(False)

    def run(self) -> None:
        self.root.mainloop()