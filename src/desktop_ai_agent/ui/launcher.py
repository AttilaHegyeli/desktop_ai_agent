from __future__ import annotations

import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Any

from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.config_loader import (
    load_dotenv,
    load_setup,
    openrouter_api_key,
    save_openrouter_key_to_env,
    save_setup,
)
from desktop_ai_agent.core.core_router import CoreRouter
from desktop_ai_agent.core.model_catalog import parse_selection, selected_id
from desktop_ai_agent.core.session_setup import SessionSetup
from desktop_ai_agent.input.hand_tracker import (
    HandTracker,
    HandTrackerError,
    camera_enabled,
    from_setup as hand_from_setup,
    hand_tracking_available,
)
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
from desktop_ai_agent.orchestrator.service import CONTEXT_TOKENS, AgentService
from desktop_ai_agent.output.tts import (
    Speaker,
    TtsError,
    from_setup as tts_from_setup,
    tts_available,
    tts_enabled,
)
from desktop_ai_agent.ui.event_format import format_event
from desktop_ai_agent.ui.hand_overlay import HandOverlay
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
        self._router: CoreRouter | None = None
        self._camera_enabled = camera_enabled(self.setup)
        self._hand_tracker: HandTracker | None = None
        self._hand_overlay: HandOverlay | None = None

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

        load_dotenv()
        key_row = ttk.Frame(frame)
        key_row.pack(fill=tk.X, padx=4, pady=(0, 8))
        ttk.Label(key_row, text="OpenRouter key (.env)").pack(side=tk.LEFT)
        self._api_key_var = tk.StringVar(value=openrouter_api_key() or "")
        self._api_key_entry = ttk.Entry(key_row, textvariable=self._api_key_var, show="*", width=42)
        self._api_key_entry.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        ttk.Button(key_row, text="Save to .env", command=self._save_api_key).pack(
            side=tk.LEFT, padx=(8, 0)
        )

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

    def _save_api_key(self) -> None:
        key = (self._api_key_var.get() or "").strip()
        save_openrouter_key_to_env(key)
        self._append_log("[core_ai] OpenRouter API key saved to .env (gitignored)")
        if self._router is not None:
            self._router.api_key = key or None
            # Rebuild active client if user is on an API model without a previous key.
            self._sync_model_dropdown()
            if key and self._agent is not None and not self._agent.busy:
                try:
                    self._agent.set_model(self._router.selection_id)
                    self._on_context_usage(self._agent.context_used)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"[core_ai] could not refresh API client: {exc}")

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
                on_new_chat=self._on_new_chat,
                on_stop=self._on_stop,
                on_model_change=self._on_model_change,
            )
            self._bar.set_context(0, CONTEXT_TOKENS)
            self._sync_model_dropdown()
        return self._bar

    def _sync_model_dropdown(self) -> None:
        bar = self._ensure_bar()
        router = self._router
        if router is None:
            # Preview from setup before router exists.
            from desktop_ai_agent.core.model_catalog import load_catalog

            catalog = load_catalog(self.setup)
            choices = [(c.selection_id, c.display) for c in catalog]
            sid = selected_id(self.setup)
            bar.set_core_models(
                choices,
                selected_id=sid,
                api_enabled=bool(openrouter_api_key()),
            )
            return
        choices = [(c.selection_id, c.display) for c in router.dropdown_choices()]
        bar.set_core_models(
            choices,
            selected_id=router.selection_id,
            api_enabled=router.has_api_key(),
        )

    def _persist_selection(self, selection_id: str) -> None:
        core = dict(self.setup.get("core_ai") or {})
        core["selected"] = selection_id
        self.setup["core_ai"] = core
        try:
            save_setup(self.setup)
        except OSError as exc:
            self._append_log(f"[core_ai] could not save selection: {exc}")

    def _on_model_change(self, selection_id: str) -> None:
        if self._busy or self._agent is None or self._router is None:
            return
        provider, _ = parse_selection(selection_id)
        if provider == "openrouter" and not self._router.has_api_key():
            self._append_log("[core_ai] set an OpenRouter API key in Configuration first")
            self._ensure_bar().status_message("OpenRouter API key required")
            self._sync_model_dropdown()
            return
        try:
            label = self._agent.set_model(selection_id)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[core_ai] model switch failed: {exc}")
            self._sync_model_dropdown()
            return
        self._persist_selection(selection_id)
        self._last_user = ""
        self._ensure_bar().reset_chat()
        self._on_context_usage(self._agent.context_used)
        self._append_log(f"[core_ai] active model: {label}")

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
            # Start camera while the root is still mapped — Tk overlays created
            # under a withdrawn master are unreliable on Windows.
            self._start_camera_if_needed()
            self._hide_setup_window()
            bar.set_ready(True)
            self._start_wake_if_needed()
        else:
            bar.set_ready(False)
            self._stop_wake()
            self._stop_camera()
            self._show_setup_window()
        self._refresh_mic_button()

    def _start_camera_if_needed(self) -> None:
        if not self._camera_enabled:
            return
        if not hand_tracking_available():
            self._append_log(
                '[camera] deps missing. Install with: pip install -e ".[camera]"'
            )
            return
        if self._hand_tracker is not None and self._hand_tracker.is_running:
            if self._hand_overlay is not None:
                self._hand_overlay.show()
            return
        cam = self.setup.get("camera") or {}
        try:
            tracker = hand_from_setup(self.setup)
            tracker.start()
        except HandTrackerError as exc:
            self._append_log(f"[camera] {exc}")
            return
        self._hand_tracker = tracker
        opacity = float(cam.get("overlay_opacity", 0.42))
        show_feed = bool(cam.get("show_feed", True))
        feed_gain = float(cam.get("feed_gain", 0.75))
        overlay = HandOverlay(
            self.root,
            tracker,
            opacity=opacity,
            show_feed=show_feed,
            feed_gain=feed_gain,
        )

        def on_first_frame(frame: Any) -> None:
            n = len(frame.hands)
            self._append_log(
                f"[camera] live {frame.width}x{frame.height} — "
                f"fullscreen self-view on; raise a hand for skeleton (hands={n})"
            )

        overlay.on_first_frame = on_first_frame
        self._hand_overlay = overlay
        overlay.show()
        self._append_log(
            "[camera] overlay on — fullscreen shaded self-view + skeleton "
            "(click-through)"
        )
        self.root.after(2500, self._check_camera_error)

    def _check_camera_error(self) -> None:
        tracker = self._hand_tracker
        overlay = self._hand_overlay
        if tracker is None:
            return
        err = tracker.last_error
        if err:
            self._append_log(f"[camera] {err}")
            if overlay is not None:
                overlay.hide()
            return
        if overlay is not None and overlay.frames_drawn == 0:
            latest = tracker.latest()
            if latest is None:
                self._append_log(
                    "[camera] no frames yet — check webcam privacy settings / "
                    "close apps using the camera"
                )
            else:
                self._append_log(
                    "[camera] frames received but overlay did not paint — "
                    "try raising overlay_opacity in config/setup.json"
                )

    def _stop_camera(self) -> None:
        if self._hand_overlay is not None:
            self._hand_overlay.destroy()
            self._hand_overlay = None
        tracker = self._hand_tracker
        self._hand_tracker = None
        if tracker is not None:
            tracker.stop()

    def _set_agent(self, agent: AgentService | None) -> None:
        self._agent = agent
        if agent is not None:
            agent.set_usage_listener(
                lambda used: self.root.after(0, self._on_context_usage, used)
            )
            self._on_context_usage(agent.context_used)

    def _on_context_usage(self, used: int) -> None:
        limit = self._agent.context_limit if self._agent is not None else CONTEXT_TOKENS
        self._ensure_bar().set_context(used, limit)

    def _on_new_chat(self) -> None:
        if self._busy or self._agent is None or self._mic_phase != "idle":
            return
        if self._speaker is not None:
            # Never join the TTS worker on the UI thread — that freezes the overlay.
            self._speaker.interrupt()
        self._agent.reset()  # fires the usage listener with 0
        self._last_user = ""
        self._ensure_bar().reset_chat()
        self._append_log("[ui] new chat — conversation cleared")

    def _on_stop(self) -> None:
        if self._agent is None or not self._agent.busy:
            return
        self._ensure_bar().set_stopping()
        if self._speaker is not None:
            self._speaker.interrupt()
        self._append_log("[ui] stop requested")
        self._agent.stop()

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
        self._stop_camera()
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
        self._stop_camera()
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
                router = CoreRouter.from_setup(
                    self.setup,
                    local_host=session.host,
                    api_key=openrouter_api_key(),
                )
                # Honor saved selection; missing API key falls back inside router.
                want = selected_id(self.setup, local_model=session.base_model)
                router.set_selection(want, reset=True)
                agent = AgentService.create_with_router(router)
                self.root.after(0, self._set_router, router)
                self.root.after(0, self._set_agent, agent)
                self.root.after(0, self._set_chat_ready, True, chat)
                self.root.after(
                    0,
                    self._append_log,
                    f"[ready] core AI: {router.active_label}. "
                    "Top-edge bar active — type, Mic, or say 'hey' to wake. "
                    "Use the Core AI dropdown to switch models.",
                )
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, self._append_log, f"ERROR: {exc}")
                self.root.after(0, self._show_setup_window)
            finally:
                self.root.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _set_router(self, router: CoreRouter | None) -> None:
        self._router = router
        self._sync_model_dropdown()

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
        self._ensure_bar().set_task_running(True)
        self._ensure_bar().set_exchange(text, None)
        self._append_log(f"> {text}")

        def on_event(kind: str, data: Any) -> None:
            try:
                line = format_event(kind, data)
            except Exception as exc:  # noqa: BLE001
                line = f"[display error] {exc}\n"
            if line:
                self.root.after(0, self._append_log, line.rstrip("\n"))
            if kind == "model_switch":
                self.root.after(0, self._on_model_switched, data)
            if kind == "final" and data:
                reply = str(data)
                self.root.after(0, self._on_agent_reply, reply)
                self.root.after(0, self._speak, reply)

        def on_done(result: TaskResult) -> None:
            if result.status != "done":
                msg = f"[{result.status}] {result.text}"
                self.root.after(0, self._append_log, msg)
                self.root.after(0, self._on_agent_reply, msg)
                if result.text and result.status != "stopped":
                    self.root.after(0, self._speak, msg)
            self.root.after(0, self._ensure_bar().set_task_running, False)
            self.root.after(0, self._set_busy, False)

        if not agent.submit(text, on_event=on_event, on_done=on_done):
            self._append_log("[busy] the agent is still working on the previous request")
            self._ensure_bar().status_message("Busy — wait for the previous request")
            self._ensure_bar().set_task_running(False)
            self._set_busy(False)

    def _on_agent_reply(self, reply: str) -> None:
        self._ensure_bar().set_exchange(self._last_user, reply)

    def _on_model_switched(self, data: Any) -> None:
        if self._agent is not None:
            self._on_context_usage(self._agent.context_used)
        if isinstance(data, dict):
            label = data.get("label")
            if label:
                self._ensure_bar().status_message(f"Switched to {label}")

    def run(self) -> None:
        self.root.mainloop()
