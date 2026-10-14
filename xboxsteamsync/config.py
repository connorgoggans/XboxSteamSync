"""User settings in config.json (see config.example.json)."""

import json
import os

from xboxsteamsync import paths

DEFAULT_CONFIG = {
    # Xbox install folders. Empty means find them from each drive's .GamingRoot file.
    "xbox_games_dirs": [],
    "collection_name": "Xbox",
    "steamgriddb_api_key": "",
    "exclude": [],
    "steamgriddb_ids": {},
    "sisr_path": r"%LOCALAPPDATA%\SISR\SISR.exe",
    "sisr_args": ["--window.show", "--window.fullscreen"],
    # Per-game settings, e.g. {"<folder name>": {"input": "keyboard-mouse"}}.
    "games": {},
}

CONTROLLER, KEYBOARD_MOUSE = "controller", "keyboard-mouse"
INPUT_MODES = (CONTROLLER, KEYBOARD_MOUSE)


def load_config(path=None, read_file=True):
    """Settings from config.json over the defaults; read_file=False gives just the defaults."""
    path = path or paths.CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG)
    if read_file and path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
    # Older configs had a single folder.
    if cfg.get("xbox_games_dir") and not cfg["xbox_games_dirs"]:
        cfg["xbox_games_dirs"] = [cfg["xbox_games_dir"]]
    cfg.pop("xbox_games_dir", None)
    cfg["steamgriddb_api_key"] = os.environ.get("STEAMGRIDDB_API_KEY") or cfg["steamgriddb_api_key"]
    cfg["sisr_path"] = os.path.expandvars(cfg["sisr_path"]) if cfg["sisr_path"] else ""
    return cfg


def game_entry(cfg, folder, identity="", name=""):
    """(key, settings) of the `games` entry for a game, or (None, {}).

    Keys are matched without case against the install folder name first, then the
    package name, then the display name, like `exclude`.
    """
    games = {k.lower(): (k, v) for k, v in (cfg.get("games") or {}).items()}
    for candidate in (folder, identity, name):
        if candidate and candidate.lower() in games:
            key, entry = games[candidate.lower()]
            return key, entry if isinstance(entry, dict) else {}
    return None, {}


def input_mode(cfg, folder, identity="", name=""):
    """The game's input mode: its `input` setting if valid, otherwise controller."""
    mode = game_entry(cfg, folder, identity, name)[1].get("input", CONTROLLER)
    return mode if mode in INPUT_MODES else CONTROLLER
