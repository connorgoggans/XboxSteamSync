"""Sync installed Xbox app, Epic Games and Battle.net games into Steam as non-Steam
shortcuts, one collection per launcher.

Re-running is safe: shortcuts for games that are no longer installed are removed
(but not when detection may have missed them, see Detection.keep_missing), and only
shortcuts this tool created are ever touched. Steam is only closed and restarted when
something actually changes.
"""

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from xboxsteamsync import artwork, onboarding, paths, steam, update
from xboxsteamsync.config import load_config
from xboxsteamsync.games import game_input_mode
from xboxsteamsync.sources import LABELS, SOURCES, collection_name, detect_all, included, shortcut_options, xbox

AUMID_RE = re.compile(r"--aumid (\S+)")
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
    options = shortcut.get("LaunchOptions", "")
    if "--aumid" not in options and "--source" not in options:
        return False
    exe = Path(shortcut.get("Exe", "").strip('"')).name.lower()
    return exe == paths.LAUNCHER_NAME or is_legacy(shortcut)


def managed_key(shortcut):
    """"<source>:<id>" for a shortcut we made, else None: "xbox:<AUMID>" (Xbox shortcuts
    keep their original --aumid launch options), "epic:<AppName>", "battlenet:<code>"."""
    if not is_managed(shortcut):
        return None
    if m := AUMID_RE.search(shortcut.get("LaunchOptions", "")):
        return f"xbox:{m.group(1)}"
    source, ids = shortcut_options(shortcut, "--source"), shortcut_options(shortcut, "--id")
    if source and ids and source[0] in SOURCES:
        return f"{source[0]}:{ids[0]}"
    return None


def xbox_kept_reason(roots, exclude):
    """For plan_user: why an Xbox shortcut whose game wasn't found should stay."""
    reason = xbox.keep_missing(roots)
    return lambda shortcut: reason(shortcut, exclude)


def keep_missing_for(detections, states, exclude):
    """For plan_user: why a shortcut whose game wasn't found should stay, by its source.

    A launcher that's turned off loses its shortcuts; one that isn't decided yet, or
    whose data couldn't be read, keeps them.
    """
    def reason(shortcut):
        source = managed_key(shortcut).split(":", 1)[0]
        state = states.get(source)
        if state is False:
            return None
        if state is None:
            return f"{LABELS[source]} isn't turned on or off yet (see `xss sources`)"
        detection = detections[source]
        if detection.error:
            return detection.error
        return detection.keep_missing(shortcut, exclude) if detection.keep_missing else None
    return reason


def launch_options(game):
    if game.source == "xbox":
        args = ["--aumid", game.id]
    else:
        args = ["--source", game.source, "--id", game.id]
    for d in game.dirs:
        if d:
            args += ["--dir", str(d)]
    for exe in game.exes:
        args += ["--exe", exe]
    return subprocess.list2cmdline(args)


def sgdb_id(ids, game):
    """The steamgriddb_ids entry for a game, matched like `exclude`."""
    ids = {k.lower(): v for k, v in (ids or {}).items()}
    return next((ids[k.lower()] for k in game.setting_keys if k and k.lower() in ids), None)


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
    collections: dict = field(default_factory=dict)  # source -> (member app ids, removed app ids, off)
    changed: bool = False


def plan_user(user_cfg: Path, games, cfg, launcher: Path, art=None, keep_missing=None, off=()) -> UserPlan:
    """Work out the new shortcut list for one Steam user, without writing Steam files.

    `art` is (ArtState, SteamGridDB or None, steamgriddb_ids) to fetch missing artwork;
    None only looks up art that is already there. `keep_missing(shortcut)` returns why a
    shortcut whose game wasn't found should stay as it is, or None to remove it. `off`
    are the sources turned off: their collections are deleted once empty.
    """
    shortcuts = steam.load_shortcuts(user_cfg)
    grid_dir = user_cfg / "grid"
    exe = f'"{launcher}"'
    start_dir = f'"{launcher.parent}"'

    existing = {}
    for s in shortcuts:
        if key := managed_key(s):
            existing[key] = s

    wanted = {g.key for g in games}
    plan = UserPlan(user_cfg, [s for s in shortcuts if not managed_key(s)], [], [])
    members = {source: [] for source in SOURCES}
    dropped = {source: [] for source in SOURCES}

    for key, s in existing.items():
        if key in wanted:
            continue
        source, appid = key.split(":", 1)[0], steam.to_unsigned(s["appid"])
        if keep_missing and (reason := keep_missing(s)):
            plan.shortcuts.append(s)
            plan.kept.append(appid)
            members[source].append(appid)
            plan.kept_names.append((s["AppName"], reason))
        else:
            plan.removed.append(appid)
            dropped[source].append(appid)
            plan.removed_names.append(s["AppName"])

    used = {steam.to_unsigned(s["appid"]) for s in plan.shortcuts if "appid" in s}
    used |= {steam.to_unsigned(existing[g.key]["appid"]) for g in games if g.key in existing}
    for game in games:
        launch = launch_options(game)
        s = existing.get(game.key)
        if s:
            appid = steam.to_unsigned(s["appid"])
            if is_legacy(s):
                plan.migrated_names.append(s["AppName"])
            # Keep the user's name/settings and the app id (artwork, collections and
            # playtime hang off it); refresh paths in case the install moved.
            s.update({"Exe": exe, "StartDir": start_dir, "LaunchOptions": launch})
        else:
            name = game.name
            appid = steam.shortcut_appid(exe, name)
            if appid in used:  # the same game from two launchers: Steam needs distinct app ids
                name = f"{game.name} ({LABELS[game.source]})"
                appid = steam.shortcut_appid(exe, name)
            s = steam.new_shortcut(appid, name, exe, start_dir, launch)
            plan.added_names.append(name)
        used.add(appid)
        if art:
            state, sgdb, sgdb_ids = art
            icon = artwork.ensure_art(grid_dir, appid, game.name, game.art, state, sgdb, sgdb_id(sgdb_ids, game))
        else:
            icon = artwork.existing_art(grid_dir, appid, "_icon")
        if icon:
            s["icon"] = str(icon)
        plan.shortcuts.append(s)
        plan.kept.append(appid)
        members[game.source].append(appid)

    plan.collections = {source: (members[source], dropped[source], source in off) for source in SOURCES}
    plan.changed = (steam.save_shortcuts(user_cfg, plan.shortcuts, write=False)
                    | steam.set_steam_input(user_cfg, plan.kept, plan.removed, write=False))
    for source, (kept, removed, is_off) in plan.collections.items():
        plan.changed |= steam.sync_collection(user_cfg, collection_name(cfg, source), kept, removed,
                                              write=False, delete_if_empty=is_off)
    return plan


def apply_plan(plan: UserPlan, cfg, art_state, backup_root: Path):
    backup(plan.user_cfg, backup_root / plan.user_cfg.parent.name)
    steam.save_shortcuts(plan.user_cfg, plan.shortcuts)
    steam.set_steam_input(plan.user_cfg, plan.kept, plan.removed)
    for source, (kept, removed, is_off) in plan.collections.items():
        steam.sync_collection(plan.user_cfg, collection_name(cfg, source), kept, removed, delete_if_empty=is_off)
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


def _print_games(cfg, detections, states):
    """List the games being synced, grouped by launcher. Returns them."""
    games = []
    for source in SOURCES:
        detection, state = detections[source], states[source]
        found = included(detection.games, cfg["exclude"])
        label = LABELS[source]
        if source == "xbox" and not detection.found:
            print("No Xbox install folders found. Install a game with the Xbox app, or set xbox_games_dirs "
                  f"in {paths.CONFIG_PATH}. Existing Xbox shortcuts are left as they are.")
        if detection.error and detection.found and source != "xbox":
            print(f"! {label}: {detection.error}; its shortcuts are left as they are")
        if not state:
            if state is False and found:
                print(f"{label}: off ({len(found)} games; `xss sources {source} --on` adds them)")
            continue
        if source == "xbox":
            where = detection.detail or "no folders"
            print(f"Found {len(found)} installed Xbox games in {where}:")
        elif detection.found:
            print(f"Found {len(found)} installed games from {label}:")
        else:
            continue
        for g in found:
            print(f"  {g.name}  [{g.id}]  input: {game_input_mode(cfg, g)}")
        games += found
    return games


def run_sync(dry_run=False, restart_steam=False, assume_yes=False) -> int:
    """`xss sync`. `restart_steam` restarts a running Steam even when nothing changed
    (setup uses it after turning on Steam's debugging flag). `assume_yes` (`setup --yes`)
    links every newly detected launcher without asking."""
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
    detections = detect_all(cfg)
    states = onboarding.choose(detections, cfg, onboarding.mode_for(dry_run, assume_yes))
    cfg = load_config()  # onboarding may have changed `sources` and `exclude`
    games = _print_games(cfg, detections, states)
    keep_missing = keep_missing_for(detections, states, cfg["exclude"])
    off = {source for source, state in states.items() if state is False}

    sgdb = artwork.SteamGridDB(cfg["steamgriddb_api_key"]) if cfg["steamgriddb_api_key"] else None
    if not sgdb and not dry_run:
        print("No SteamGridDB API key configured; using each game's own art.")
    art_state = artwork.ArtState(paths.ART_STATE_PATH)
    art = None if dry_run else (art_state, sgdb, cfg["steamgriddb_ids"])

    plans = []
    for user_cfg in user_cfgs:
        print(f"\nSteam user {user_cfg.parent.name}:")
        plan = plan_user(user_cfg, games, cfg, launcher, art, keep_missing, off)
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
        plans = [plan_user(p.user_cfg, games, cfg, launcher, keep_missing=keep_missing, off=off) for p in plans]

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
