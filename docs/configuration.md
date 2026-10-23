# Configuration

Main file: [`config/setup.json`](../config/setup.json).

## `ollama`

| Key | Default | Meaning |
|-----|---------|---------|
| `base_model` | `gemma4` | Model used for chat / tools |
| `agent_model` | `gemma4-agent` | Built from Modelfile at setup (legacy) |
| `host` | `http://127.0.0.1:11434` | Ollama HTTP API |
| `startup_timeout_sec` | `60` | Wait for Ollama to become ready |
| `interactive_commands` | `/set nothink`, `/set format json` | Applied during setup |

Related: `config/Modelfile.gemma4-agent` — older scriptwriter profile. Live agent instructions are in `src/desktop_ai_agent/orchestrator/prompts.py`.

## `folder_index`

(Legacy key `program_files_index` is still accepted if `folder_index` is absent.)

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `true` | Run indexing on setup |
| `drives` | `["C","D","E"]` | Drive letters to scan |
| `apps.enabled` | `true` | Index usable `.exe` launchers |
| `apps.include_start_menu` | `true` | Resolve Start Menu / Startup `.lnk` targets (stores `link` + exe `path`) |
| `apps.include_installed_apps` | `true` | Windows Settings → Apps (Uninstall registry) |
| `apps.include_appx` | `true` | Store / AppX apps via Shell:AppsFolder (AUMID) |
| `apps.include_games_folders` | `true` | Scan `Games` / `games` trees |
| `apps.extra_roots` | `[]` | Extra folders to scan for `.exe` |
| `apps.max_exe_depth` | `4` | Max depth under each app/games root |
| `apps.max_exes` | `20000` | Safety cap |

Output files (generated; gitignored):

- `config/program_files_index.json` / `.txt`

## `ui`

| Key | Meaning |
|-----|---------|
| `title` | Window title |
| `width` / `height` | Window size |
| `auto_open` | Open UI on start |

## `voice` (needs `pip install -e ".[voice]"`)

| Key | Meaning |
|-----|---------|
| `enabled` | Master switch |
| `model_size` | Whisper size (`base`, …) |
| `language` | STT language (`en`, …) |
| `sample_rate` | Audio sample rate |
| `device` / `compute_type` | Inference device (`cpu`, `int8`, …) |

### `voice.wake_word`

| Key | Meaning |
|-----|---------|
| `enabled` | Continuous wake listening |
| `phrases` | Wake phrases |
| `stop_phrases` | End awake session (`stop`, `sleep`, …) |
| `silence_sec` | End of utterance silence |
| `post_wake_grace_sec` | Listen window after wake |
| `max_utterance_sec` | Cap utterance length |
| `chunk_sec` | Audio chunk size |
| `energy_threshold` | Simple energy gate |

## `output.tts` (needs `pip install -e ".[tts]"`)

| Key | Meaning |
|-----|---------|
| `enabled` | Master switch |
| `engine` | `"edge"` or `"sapi"` |
| `voice` | Voice id / name |
| `rate` | Speed (`"+0%"` or int) |
| `volume` | 0.0–1.0 |
| `wake_ack` | Phrase spoken after wake |

## `formats`

Documents the older `{script, code}` response schema. Prefer tool calls + `provide_script` in the current demo.

## Environment / install extras

Defined in `pyproject.toml`:

| Extra | Packages |
|-------|----------|
| `voice` | faster-whisper, sounddevice, numpy |
| `tts` | pyttsx3, edge-tts, pygame |
| `screenshot` | mss, Pillow |
| `dev` | pytest |
