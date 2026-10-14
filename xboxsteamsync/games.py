"""`xss game`: show and change per-game settings (the input mode) in config.json."""

import json
from pathlib import Path

from xboxsteamsync import paths
from xboxsteamsync.config import CONTROLLER, INPUT_MODES, game_entry, input_mode, load_config


def game_input_mode(cfg, game):
    return input_mode(cfg, game.folder, game.identity, game.name)


def unknown_settings(cfg, games):
    """`games` keys in config.json that match no installed game."""
    used = {(game_entry(cfg, g.folder, g.identity, g.name)[0] or "").lower() for g in games}
    return [k for k in (cfg.get("games") or {}) if k.lower() not in used]


def invalid_settings(cfg):
    """{key: value} for `games` entries whose input setting isn't a known mode."""
    return {k: v.get("input") for k, v in (cfg.get("games") or {}).items()
            if isinstance(v, dict) and "input" in v and v["input"] not in INPUT_MODES}


def match_games(games, query):
    """Installed games matching `query`: an exact (case-insensitive) folder, package or
    display name, or failing that, every game whose folder or display name contains it."""
    q = query.lower()
    exact = [g for g in games if q in (g.folder.lower(), g.identity.lower(), g.name.lower())]
    return exact or [g for g in games if q in g.folder.lower() or q in g.name.lower()]


def set_input(raw, game, mode) -> bool:
    """Set `game`'s input mode in `raw` (config.json's contents), in place. Updates the
    entry that already matches the game, or adds one keyed by folder name; the default
    (controller) is stored by removing the setting. Returns whether anything changed."""
    key, entry = game_entry(raw, game.folder, game.identity, game.name)
    if key is None and mode == CONTROLLER:
        return False
    key = key or game.folder
    new = {k: v for k, v in entry.items() if k != "input"}
    if mode != CONTROLLER:
        new["input"] = mode
    games = raw.setdefault("games", {})
    if games.get(key) == new:
        return False
    if new:
        games[key] = new
    else:
        del games[key]
    return True


def _load_raw(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def _save_raw(path: Path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _installed(cfg):
    from xboxsteamsync.xbox_games import find_games, gaming_roots
    roots = [Path(d) for d in cfg["xbox_games_dirs"]] or gaming_roots()
    return find_games(roots, cfg["exclude"])


def _print_list(cfg, games):
    width = max((len(g.name) for g in games), default=0)
    for g in sorted(games, key=lambda g: g.name.lower()):
        print(f"  {g.name:<{width}}  {game_input_mode(cfg, g)}")
    for key, value in invalid_settings(cfg).items():
        print(f"  ! {key}: unknown input {value!r} (use {' or '.join(INPUT_MODES)}); using {CONTROLLER}")
    for key in unknown_settings(cfg, games):
        print(f"  ! {key}: in config.json, but no installed game matches it")


def run_game(name=None, mode=None) -> int:
    try:
        cfg = load_config()
    except ValueError as e:
        print(f"Couldn't read {paths.CONFIG_PATH}: {e}")
        return 1
    games = _installed(cfg)
    if not name:
        if not games:
            print("No installed Xbox games found.")
            return 0
        print(f"Input mode of {len(games)} installed Xbox games:")
        _print_list(cfg, games)
        return 0

    matches = match_games(games, name)
    if not matches:
        print(f"No installed Xbox game matches {name!r}. Run `xss game --list` to see them.")
        return 1
    if len(matches) > 1:
        print(f"{name!r} matches more than one game; use its full name:")
        for g in matches:
            print(f"  {g.name}")
        return 1
    game = matches[0]

    if mode is None:
        print(f"{game.name}: {game_input_mode(cfg, game)}")
        return 0
    raw = _load_raw(paths.CONFIG_PATH)
    if set_input(raw, game, mode):
        _save_raw(paths.CONFIG_PATH, raw)
        print(f"{game.name}: input set to {mode}. This takes effect the next time the game starts.")
    else:
        print(f"{game.name}: input is already {mode}.")
    if mode != CONTROLLER:
        print("In Steam, open the shortcut's controller settings and pick a keyboard-and-mouse layout, "
              "e.g. \"Keyboard (WASD) and Mouse\".")
    return 0
