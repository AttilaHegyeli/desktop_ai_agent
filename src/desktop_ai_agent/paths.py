from pathlib import Path

# src/desktop_ai_agent/paths.py -> repo root
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
SETUP_JSON = CONFIG_DIR / "setup.json"
MODELFILE = CONFIG_DIR / "Modelfile.gemma4-agent"
PROGRAM_FILES_INDEX_JSON = CONFIG_DIR / "program_files_index.json"
PROGRAM_FILES_INDEX_TXT = CONFIG_DIR / "program_files_index.txt"
