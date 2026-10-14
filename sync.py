"""Sync installed Xbox app games into Steam as non-Steam shortcuts in an "Xbox" collection.

Re-running is safe: shortcuts for games that are no longer installed are removed,
and only shortcuts this tool created are ever touched.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import artwork
import steam
from xbox_games import find_games

HERE = Path(__file__).resolve().parent
LAUNCHER = HERE / "launcher.py"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
MARKER = "--aumid"
AUMID_RE = re.compile(r"--aumid (\S+)")
BACKUP_FILES = [
    "shortcuts.vdf",
    "localconfig.vdf",
    "cloudstorage/cloud-storage-namespace-1.json",
    "cloudstorage/cloud-storage-namespace-1.modified.json",
    "cloudstorage/cloud-storage-namespaces.json",
]

DEFAULT_CONFIG = {
    "xbox_games_dir": r"C:\XboxGames",
    "collection_name": "Xbox",
    "steamgriddb_api_key": "",
    "exclude": [],
    "steamgriddb_ids": {},
    "sisr_path": r"%LOCALAPPDATA%\SISR\SISR.exe",
    "sisr_args": ["--window.show", "--window.fullscreen"],
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = HERE / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    cfg["steamgriddb_api_key"] = os.environ.get("STEAMGRIDDB_API_KEY") or cfg["steamgriddb_api_key"]
    cfg["sisr_path"] = os.path.expandvars(cfg["sisr_path"]) if cfg["sisr_path"] else ""
    return cfg


def backup(user_cfg: Path, dest: Path):
    """Copy every Steam file we modify, so a bad run can be undone by hand."""
    for rel in BACKUP_FILES:
        src = user_cfg / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


def launch_options(game, cfg):
    args = [str(LAUNCHER), "--aumid", game.aumid]
    for d in (game.content_dir, game.install_dir):
        if d:
            args += ["--dir", str(d)]
    for exe in game.exes:
        args += ["--exe", exe]
    if cfg["sisr_path"]:
        args += ["--sisr", cfg["sisr_path"]]
        for a in cfg["sisr_args"]:
            args.append(f"--sisr-arg={a}")  # '=' form so values like -w aren't parsed as flags
    return subprocess.list2cmdline(args)


def is_managed(shortcut):
    return str(LAUNCHER.name) in shortcut.get("LaunchOptions", "") and MARKER in shortcut.get("LaunchOptions", "")


def plan_user(user_cfg: Path, games, cfg, art=None):
    """Work out the new shortcut list for one Steam user. Returns (shortcuts, kept_ids, removed_ids).

    `art` is (ArtState, SteamGridDB or None, steamgriddb_ids); None skips artwork (dry run).
    """
    shortcuts = steam.load_shortcuts(user_cfg)
    grid_dir = user_cfg / "grid"
    exe = f'"{PYTHONW}"'
    start_dir = f'"{HERE}"'

    existing = {}
    for s in shortcuts:
        if is_managed(s) and (m := AUMID_RE.search(s["LaunchOptions"])):
            existing[m.group(1)] = s

    wanted = {g.aumid: g for g in games}
    result = [s for s in shortcuts if not is_managed(s)]
    kept, removed = [], []

    for aumid, s in existing.items():
        if aumid not in wanted:
            removed.append(steam.to_unsigned(s["appid"]))
            print(f"  - {s['AppName']}")

    for game in games:
        launch = launch_options(game, cfg)
        s = existing.get(game.aumid)
        if s:
            appid = steam.to_unsigned(s["appid"])
            # Keep the user's name/settings; refresh paths in case the venv or repo moved.
            s.update({"Exe": exe, "StartDir": start_dir, "LaunchOptions": launch})
        else:
            appid = steam.shortcut_appid(exe, game.name)
            s = steam.new_shortcut(appid, game.name, exe, start_dir, launch)
            print(f"  + {game.name}")
        if art:
            state, sgdb, sgdb_ids = art
            icon = artwork.ensure_art(grid_dir, appid, game.name, game.content_dir, state,
                                      sgdb, sgdb_ids.get(game.folder))
            if icon:
                s["icon"] = icon
        result.append(s)
        kept.append(appid)

    return result, kept, removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing anything")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    games = find_games(Path(cfg["xbox_games_dir"]), cfg["exclude"])
    print(f"Found {len(games)} installed Xbox games:")
    for g in games:
        print(f"  {g.name}  [{g.aumid}]")

    sgdb = artwork.SteamGridDB(cfg["steamgriddb_api_key"]) if cfg["steamgriddb_api_key"] else None
    if not sgdb:
        print("No SteamGridDB API key configured; using local Xbox art.")
    art_state = artwork.ArtState(HERE / "art_state.json")
    art = None if args.dry_run else (art_state, sgdb, cfg["steamgriddb_ids"])

    steam_path = steam.steam_dir()
    plans = []
    for user_cfg in steam.user_config_dirs(steam_path):
        print(f"\nSteam user {user_cfg.parent.name}:")
        plans.append((user_cfg, *plan_user(user_cfg, games, cfg, art)))

    if args.dry_run:
        print("\nDry run; nothing written.")
        return

    was_running = steam.is_running()
    if was_running:
        print("\nClosing Steam...")
        steam.shutdown(steam_path)

    backup_root = HERE / "backups" / time.strftime("%Y%m%d-%H%M%S")
    for user_cfg, shortcuts, kept, removed in plans:
        backup(user_cfg, backup_root / user_cfg.parent.name)
        steam.save_shortcuts(user_cfg, shortcuts)
        steam.set_steam_input(user_cfg, kept, removed)
        steam.sync_collection(user_cfg, cfg["collection_name"], kept, removed)
        for appid in removed:
            artwork.delete_art(user_cfg / "grid", appid, art_state)
    art_state.save()

    if was_running:
        print("Restarting Steam...")
        steam.start(steam_path)
    print("Done.")


if __name__ == "__main__":
    main()
