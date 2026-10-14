"""Where things live.

    %LOCALAPPDATA%\\XboxSteamSync\\          data that survives updates
        app\\                               the build (xss.exe, xss-launch.exe); replaced on update
        config.json, art_state.json, setup_state.json, update_state.json
        logs\\, backups\\, updates\\

XSS_HOME overrides the data folder (useful for development and tests).
"""

import os
import sys
from pathlib import Path

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
CUSTOM_HOME = bool(os.environ.get("XSS_HOME"))
DATA_DIR = Path(os.environ.get("XSS_HOME") or LOCALAPPDATA / "XboxSteamSync")
APP_DIR = DATA_DIR / "app"

CONFIG_PATH = DATA_DIR / "config.json"
ART_STATE_PATH = DATA_DIR / "art_state.json"
SETUP_STATE_PATH = DATA_DIR / "setup_state.json"
UPDATE_STATE_PATH = DATA_DIR / "update_state.json"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
UPDATES_DIR = DATA_DIR / "updates"  # downloaded release waiting to be installed

LAUNCHER_NAME = "xss-launch.exe"

FROZEN = getattr(sys, "frozen", False)


def launcher_exe() -> Path:
    """The Steam shortcut target. In a build it sits next to xss.exe; when running from
    source it's the gui-script uv/pip put next to the venv's python.exe."""
    return Path(sys.executable).resolve().with_name(LAUNCHER_NAME)


def xss_command() -> list[str]:
    """Command line that runs `xss` itself (for RunOnce entries and the like)."""
    if FROZEN:
        return [sys.executable]
    return [sys.executable, "-m", "xboxsteamsync"]


def running_from_install_dir() -> bool:
    return Path(sys.executable).resolve().parent == APP_DIR.resolve()
