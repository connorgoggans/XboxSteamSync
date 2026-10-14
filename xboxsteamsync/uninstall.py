"""`xss uninstall`: remove everything XboxSteamSync added to Steam."""

import subprocess
import time
import winreg

from xboxsteamsync import artwork, paths, steam
from xboxsteamsync.config import load_config
from xboxsteamsync.sources import SOURCES, collection_name
from xboxsteamsync.sync import backup, managed_key


def _by_source(shortcuts):
    return {source: [steam.to_unsigned(s["appid"]) for s in shortcuts if managed_key(s).startswith(source + ":")]
            for source in SOURCES}


def _sync_collections(user_cfg, cfg, ours, write=True):
    changed = False
    for source, appids in _by_source(ours).items():
        changed |= steam.sync_collection(user_cfg, collection_name(cfg, source), [], appids, write=write,
                                         delete_if_empty=True)
    return changed


def _plan(user_cfg, cfg):
    shortcuts = steam.load_shortcuts(user_cfg)
    ours = [s for s in shortcuts if managed_key(s)]
    keep = [s for s in shortcuts if not managed_key(s)]
    appids = [steam.to_unsigned(s["appid"]) for s in ours]
    changed = (steam.save_shortcuts(user_cfg, keep, write=False)
               | steam.set_steam_input(user_cfg, [], appids, write=False)
               | _sync_collections(user_cfg, cfg, ours, write=False))
    return ours, keep, appids, changed


def _remove_from_path():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as k:
            value, kind = winreg.QueryValueEx(k, "Path")
            parts = [p for p in value.split(";") if p and p.rstrip("\\").lower() != str(paths.APP_DIR).lower()]
            winreg.SetValueEx(k, "Path", 0, kind, ";".join(parts))
    except OSError:
        pass


def _delete_data_dir_after_exit():
    """We can't delete our own folder while running, so a detached cmd waits for us to exit."""
    cmd = f'ping -n 4 127.0.0.1 >nul & rmdir /s /q "{paths.DATA_DIR}"'
    subprocess.Popen(["cmd", "/c", cmd], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                     cwd=paths.LOCALAPPDATA)


def run_uninstall(dry_run=False, purge=False) -> int:
    try:
        steam_path = steam.steam_dir()
        user_cfgs = steam.user_config_dirs(steam_path)
    except steam.SteamNotFound as e:
        print(e)
        steam_path, user_cfgs = None, []
    cfg = load_config()

    plans = []
    for user_cfg in user_cfgs:
        ours, keep, appids, changed = _plan(user_cfg, cfg)
        print(f"Steam user {user_cfg.parent.name}: removing {len(ours)} shortcut(s)")
        for s in ours:
            print(f"  - {s['AppName']}")
        plans.append((user_cfg, appids, changed))

    if dry_run:
        print("\nDry run; nothing written.")
        return 0

    if any(changed for *_, changed in plans):
        was_running = steam.is_running()
        if was_running:
            print("\nClosing Steam...")
            steam.shutdown(steam_path)
        art_state = artwork.ArtState(paths.ART_STATE_PATH)
        backup_root = paths.BACKUP_DIR / (time.strftime("%Y%m%d-%H%M%S") + "-uninstall")
        for user_cfg, _, _ in plans:
            # Plan again: Steam rewrites its files on exit.
            ours, keep, appids, changed = _plan(user_cfg, cfg)
            if not changed:
                continue
            backup(user_cfg, backup_root / user_cfg.parent.name)
            steam.save_shortcuts(user_cfg, keep)
            steam.set_steam_input(user_cfg, [], appids)
            _sync_collections(user_cfg, cfg, ours)
            for appid in appids:
                artwork.delete_art(user_cfg / "grid", appid, art_state)
        if not purge:
            art_state.save()
            print(f"Backed up the previous Steam files to {backup_root}")
        if was_running:
            print("Restarting Steam...")
            steam.start(steam_path)
    else:
        print("Nothing to remove from Steam.")

    if purge:
        _remove_from_path()
        _delete_data_dir_after_exit()
        print(f"Deleting {paths.DATA_DIR} (settings, logs, backups and the program itself).")
    else:
        print(f"\nTo delete the program and its settings too, run `xss uninstall --purge`, "
              f"or delete {paths.DATA_DIR}.")
    print("SISR and VIIPER were left installed; to remove them, run "
          f"{paths.LOCALAPPDATA / 'SISR' / 'uninstall.ps1'} (also listed in Windows' installed apps).")
    return 0
