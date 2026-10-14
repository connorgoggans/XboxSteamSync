"""Sync installed Xbox app games into Steam as non-Steam shortcuts in an "Xbox" collection.

Re-running is safe: shortcuts for games that are no longer installed are removed
(but not when detection may have missed them, see kept_reason), and only shortcuts
this tool created are ever touched. Steam is only closed and
restarted when something actually changes.
"""

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from xboxsteamsync import artwork, paths, steam, update
from xboxsteamsync.config import load_config
from xboxsteamsync.games import game_input_mode
from xboxsteamsync.xbox_games import find_games, gaming_roots, kept_reason

AUMID_RE = re.compile(r"--aumid (\S+)")
DIR_RE = re.compile(r'--dir (?:"([^"]*)"|(\S+))')
LEGACY_LAUNCHER_RE = re.compile(r'^"?(.+?launcher\.py)"? ')
BACKUP_FILES = [
    "shortcuts.vdf",
    "localconfig.vdf",
    "cloudstorage/cloud-storage-namespace-1.json",
    "cloudstorage/cloud-storage-namespace-1.modified.json",
    "cloudstorage/cloud-storage-namespaces.json",
]


def is_legacy(shortcut):
    """A shortcut made by the old script version (pythonw launcher.py --aumid ...)."""
    return "launcher.py" in shortcut.get("LaunchOptions", "")


def is_managed(shortcut):
    if "--aumid" not in shortcut.get("LaunchOptions", ""):
        return False
    exe = Path(shortcut.get("Exe", "").strip('"')).name.lower()
    return exe == paths.LAUNCHER_NAME or is_legacy(shortcut)


def managed_aumid(shortcut):
    m = AUMID_RE.search(shortcut.get("LaunchOptions", ""))
    return m.group(1) if m and is_managed(shortcut) else None


def shortcut_dirs(shortcut):
    """The --dir folders in a shortcut's launch options (the Content folder first)."""
    return [Path(a or b) for a, b in DIR_RE.findall(shortcut.get("LaunchOptions", ""))]


def xbox_kept_reason(roots, exclude):
    """For plan_user: why an Xbox shortcut whose game wasn't found should stay."""
    def reason(shortcut):
        if not roots:
            return "no Xbox install folders were found"
        dirs = shortcut_dirs(shortcut)
        return kept_reason(dirs[0], exclude) if dirs else None
    return reason


def launch_options(game):
    args = ["--aumid", game.aumid]
    for d in (game.content_dir, game.install_dir):
        if d:
            args += ["--dir", str(d)]
    for exe in game.exes:
        args += ["--exe", exe]
    return subprocess.list2cmdline(args)


def backup(user_cfg: Path, dest: Path):
    """Copy every Steam file we modify, so a bad run can be undone by hand."""
    for rel in BACKUP_FILES:
        src = user_cfg / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


@dataclass
class UserPlan:
    user_cfg: Path
    shortcuts: list
    kept: list                                  # app ids of every shortcut we manage
    removed: list                               # app ids of shortcuts being removed
    added_names: list = field(default_factory=list)
    removed_names: list = field(default_factory=list)
    migrated_names: list = field(default_factory=list)
    kept_names: list = field(default_factory=list)  # (name, reason) of missing games left in place
    changed: bool = False


def plan_user(user_cfg: Path, games, cfg, launcher: Path, art=None, keep_missing=None) -> UserPlan:
    """Work out the new shortcut list for one Steam user, without writing Steam files.

    `art` is (ArtState, SteamGridDB or None, steamgriddb_ids) to fetch missing artwork;
    None only looks up art that is already there. `keep_missing(shortcut)` returns why a
    shortcut whose game wasn't found should stay as it is, or None to remove it.
    """
    shortcuts = steam.load_shortcuts(user_cfg)
    grid_dir = user_cfg / "grid"
    exe = f'"{launcher}"'
    start_dir = f'"{launcher.parent}"'

    existing = {}
    for s in shortcuts:
        if aumid := managed_aumid(s):
            existing[aumid] = s

    wanted = {g.aumid for g in games}
    plan = UserPlan(user_cfg, [s for s in shortcuts if not managed_aumid(s)], [], [])

    for aumid, s in existing.items():
        if aumid in wanted:
            continue
        if keep_missing and (reason := keep_missing(s)):
            plan.shortcuts.append(s)
            plan.kept.append(steam.to_unsigned(s["appid"]))
            plan.kept_names.append((s["AppName"], reason))
        else:
            plan.removed.append(steam.to_unsigned(s["appid"]))
            plan.removed_names.append(s["AppName"])

    for game in games:
        launch = launch_options(game)
        s = existing.get(game.aumid)
        if s:
            appid = steam.to_unsigned(s["appid"])
            if is_legacy(s):
                plan.migrated_names.append(s["AppName"])
            # Keep the user's name/settings and the app id (artwork, collections and
            # playtime hang off it); refresh paths in case the install moved.
            s.update({"Exe": exe, "StartDir": start_dir, "LaunchOptions": launch})
        else:
            appid = steam.shortcut_appid(exe, game.name)
            s = steam.new_shortcut(appid, game.name, exe, start_dir, launch)
            plan.added_names.append(game.name)
        if art:
            state, sgdb, sgdb_ids = art
            icon = artwork.ensure_art(grid_dir, appid, game.name, game.content_dir, state,
                                      sgdb, sgdb_ids.get(game.folder))
        else:
            icon = artwork.existing_art(grid_dir, appid, "_icon")
        if icon:
            s["icon"] = str(icon)
        plan.shortcuts.append(s)
        plan.kept.append(appid)

    plan.changed = (steam.save_shortcuts(user_cfg, plan.shortcuts, write=False)
                    | steam.set_steam_input(user_cfg, plan.kept, plan.removed, write=False)
                    | steam.sync_collection(user_cfg, cfg["collection_name"], plan.kept, plan.removed,
                                            write=False))
    return plan


def apply_plan(plan: UserPlan, cfg, art_state, backup_root: Path):
    backup(plan.user_cfg, backup_root / plan.user_cfg.parent.name)
    steam.save_shortcuts(plan.user_cfg, plan.shortcuts)
    steam.set_steam_input(plan.user_cfg, plan.kept, plan.removed)
    steam.sync_collection(plan.user_cfg, cfg["collection_name"], plan.kept, plan.removed)
    for appid in plan.removed:
        artwork.delete_art(plan.user_cfg / "grid", appid, art_state)


def migrate_legacy_files(user_cfgs):
    """Copy config.json and art_state.json from an old script checkout (found through
    its shortcuts) into the data folder, unless they already exist there."""
    for user_cfg in user_cfgs:
        for s in steam.load_shortcuts(user_cfg):
            m = LEGACY_LAUNCHER_RE.match(s.get("LaunchOptions", "")) if is_legacy(s) else None
            if not m:
                continue
            old_dir = Path(m.group(1)).parent
            for src, dest in ((old_dir / "config.json", paths.CONFIG_PATH),
                              (old_dir / "art_state.json", paths.ART_STATE_PATH)):
                if src.exists() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    print(f"Copied {src} to {dest}")
            return


def _print_plan(plan: UserPlan):
    for name in plan.added_names:
        print(f"  + {name}")
    for name in plan.removed_names:
        print(f"  - {name}")
    for name in plan.migrated_names:
        print(f"  ~ {name} (moved to {paths.LAUNCHER_NAME})")
    for name, reason in plan.kept_names:
        print(f"  ? {name}: not found, but kept because {reason}")
    if not steam.has_collections_file(plan.user_cfg):
        print("  ! Steam's collections file isn't there yet; open the Steam library once, then sync again")
    if not plan.changed:
        print("  up to date")


def run_sync(dry_run=False, restart_steam=False) -> int:
    """`xss sync`. `restart_steam` restarts a running Steam even when nothing changed
    (setup uses it after turning on Steam's debugging flag)."""
    try:
        steam_path = steam.steam_dir()
    except steam.SteamNotFound as e:
        print(e)
        return 1
    user_cfgs = steam.user_config_dirs(steam_path)
    if not user_cfgs:
        print(steam.NO_USERS_MESSAGE)
        return 1

    launcher = paths.launcher_exe()
    if not launcher.exists():
        print(f"{launcher} is missing; reinstall XboxSteamSync.")
        return 1
    if paths.FROZEN and not paths.running_from_install_dir():
        print(f"! Running from {launcher.parent}, not {paths.APP_DIR}. Steam shortcuts will break if this "
              "folder moves; install with the install script instead.")

    if not dry_run:
        migrate_legacy_files(user_cfgs)
    cfg = load_config()
    roots = [Path(d) for d in cfg["xbox_games_dirs"]] or gaming_roots()
    if not roots:
        print("No Xbox install folders found. Install a game with the Xbox app, or set xbox_games_dirs "
              f"in {paths.CONFIG_PATH}. Existing Xbox shortcuts are left as they are.")
    games = find_games(roots, cfg["exclude"])
    keep_missing = xbox_kept_reason(roots, cfg["exclude"])
    print(f"Found {len(games)} installed Xbox games in {', '.join(map(str, roots)) or 'no folders'}:")
    for g in games:
        print(f"  {g.name}  [{g.aumid}]  input: {game_input_mode(cfg, g)}")

    sgdb = artwork.SteamGridDB(cfg["steamgriddb_api_key"]) if cfg["steamgriddb_api_key"] else None
    if not sgdb and not dry_run:
        print("No SteamGridDB API key configured; using local Xbox art.")
    art_state = artwork.ArtState(paths.ART_STATE_PATH)
    art = None if dry_run else (art_state, sgdb, cfg["steamgriddb_ids"])

    plans = []
    for user_cfg in user_cfgs:
        print(f"\nSteam user {user_cfg.parent.name}:")
        plan = plan_user(user_cfg, games, cfg, launcher, art, keep_missing)
        _print_plan(plan)
        plans.append(plan)

    changed = any(p.changed for p in plans)
    if dry_run:
        print("\nDry run; nothing written." if changed else "\nNothing to change.")
        return 0
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    art_state.save()

    was_running = steam.is_running()
    if not changed:
        update.record_sync()
        print("\nSteam is already up to date.")
        if restart_steam and was_running:
            print("Restarting Steam...")
            steam.shutdown(steam_path)
            steam.start(steam_path)
        return 0

    if was_running:
        print("\nClosing Steam...")
        steam.shutdown(steam_path)
        # Steam rewrites its files on exit, so plan again against what it left behind.
        plans = [plan_user(p.user_cfg, games, cfg, launcher, keep_missing=keep_missing) for p in plans]

    backup_root = paths.BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    for plan in plans:
        if plan.changed:
            apply_plan(plan, cfg, art_state, backup_root)
    art_state.save()
    update.record_sync()
    print(f"Backed up the previous Steam files to {backup_root}")

    if was_running:
        print("Restarting Steam...")
        steam.start(steam_path)
    print("Done.")
    return 0
