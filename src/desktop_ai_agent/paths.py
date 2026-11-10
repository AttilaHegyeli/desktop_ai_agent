from pathlib import Path

# src/desktop_ai_agent/paths.py -> repo root
ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
CONFIG_DIR = ROOT / "config"
MODELS_DIR = CONFIG_DIR / "models"
HAND_LANDMARKER_TASK = MODELS_DIR / "hand_landmarker.task"
SETUP_JSON = CONFIG_DIR / "setup.json"
SECRETS_JSON = CONFIG_DIR / "secrets.json"
MODELFILE = CONFIG_DIR / "Modelfile.gemma4-agent"
PROGRAM_FILES_INDEX_JSON = CONFIG_DIR / "program_files_index.json"
PROGRAM_FILES_INDEX_TXT = CONFIG_DIR / "program_files_index.txt"
LOGS_DIR = ROOT / "logs"
SCREENSHOTS_DIR = LOGS_DIR / "screenshots"
