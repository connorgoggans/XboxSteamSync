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
}


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
