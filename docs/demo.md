# Demo & usage (v0.1.0)

How to use the current demo day to day. For system design see [architecture.md](architecture.md).

## Start

```powershell
pip install -e .
python -m app
```

Or `scripts\start.bat`. First run indexes application `.exe` files, starts Ollama, and opens the UI.

**Useful first prompts**

| Try | What happens |
|-----|----------------|
| `open steam` / `open vs code` | `open_app` via the executable index |
| `wait 2 seconds` | `wait` |
| `what do you see on my screen?` | Auto screenshot (needs `[screenshot]`) + vision reply |
| `create a file on my desktop` | No matching tool → asks if you want a script |
| Then `yes` | `provide_script` shows JSON; **code is never executed** |

## Modes of input

### Typed chat

Type in the terminal and submit. Always available; no extra packages.

### Mic (push-to-talk)

```powershell
pip install -e ".[voice]"
```

Click **Mic** → speak → **Stop** → text is submitted.

### Wake word

Same `[voice]` install. Say a wake phrase (default includes `hey`, `hey Zsuzsi`, …) → hear the wake ack → keep giving tasks without waking again → say **`stop`** / `sleep` / `goodbye` to return to wake-only listening.

Config: `voice` and `voice.wake_word` in [`config/setup.json`](../config/setup.json). Disable wake with `"wake_word": {"enabled": false}` without removing Mic.

Continuous Whisper uses CPU. Hugging Face rate-limit tips in the log are normal for free downloads.

## Speech output (optional)

```powershell
pip install -e ".[tts]"
```

Speaks wake ack, final replies, and error status. Under `output.tts`:

| Key | Notes |
|-----|--------|
| `engine` | `"edge"` (neural, needs internet) or `"sapi"` (offline Windows) |
| `voice` | e.g. `en-US-JennyNeural` (edge) or `Zira` (sapi) |
| `rate` | `"+0%"` (edge) or int like `160` (sapi) |
| `wake_ack` | Spoken after wake, default `"yes my lord?"` |
| `enabled` | Master switch |

List edge voices: `python -m edge_tts --list-voices`.

## Screenshot / screen vision

```powershell
pip install -e ".[screenshot]"
```

- Explicit tool: model can call `screenshot`.
- **Auto-capture** only when the user message looks like screen vision (“what do you see?”, “look at my screen”, …). Greetings and script requests do not trigger it.
- Images land in `logs/screenshots/` and are attached for the next model turn.

## Safety (what you can trust)

- The model may only call actions in the registry.
- Bad names/args are rejected; the reason goes back so the model can retry.
- Risk levels (`low` / `medium` / `high`) are set by us in code, never by the model.
- High-risk actions need confirmation; the confirm UI is **not wired yet**, so they are **denied**.
- Limits: **8** rounds per task, **2** failed rounds in a row → stop.
- `provide_script` is display-only. Generated code is **never** run.

## Logs

`logs/` is gitignored (relative to where you start the app):

- `logs/audit.jsonl` — one JSON line per event (`rejected`, `declined`, `execute`, `result`)
- `logs/screenshots/` — timestamped PNGs

## Known limitations

- Small local model (`gemma4`); it can pick the wrong tool — check the audit log.
- No mouse/keyboard actions yet.
- Wake word is rolling Whisper checks, not a dedicated wake engine; mishears happen.
- The `gemma4-agent` Modelfile profile is still built at setup but is **not** used for chatting (tools + system prompt in code are).

## Next reading

- [Action layer](action.md) — tools and how to add one  
- [Configuration](configuration.md) — all config keys  
- [Core AI](core.md) — Ollama and the app index  
