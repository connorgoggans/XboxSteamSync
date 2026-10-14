"""`xss game`: show and change per-game settings (the input mode) in config.json."""

from xboxsteamsync import paths
from xboxsteamsync.config import (CONTROLLER, INPUT_MODES, game_entry, input_mode, load_config, load_raw,
                                  save_raw, source_state)
from xboxsteamsync.sources import LABELS, SOURCES, detect, included


def game_input_mode(cfg, game):
    return input_mode(cfg, *game.setting_keys)


def unknown_settings(cfg, games):
    """`games` keys in config.json that match no installed game."""
    used = {(game_entry(cfg, *g.setting_keys)[0] or "").lower() for g in games}
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
    key, entry = game_entry(raw, *game.setting_keys)
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


def installed_games(cfg):
    """Games from every launcher that's turned on, without excluded ones."""
    games = []
    for source in SOURCES:
        if source_state(cfg, source):
            games += detect(source, cfg).games
    return included(games, cfg["exclude"])


def _print_list(cfg, games):
    width = max((len(g.name) for g in games), default=0)
    source_width = max((len(LABELS[g.source]) for g in games), default=0)
    for g in sorted(games, key=lambda g: (SOURCES.index(g.source), g.name.lower())):
        print(f"  {g.name:<{width}}  {LABELS[g.source]:<{source_width}}  {game_input_mode(cfg, g)}")
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
    games = installed_games(cfg)
    if not name:
        if not games:
            print("No installed games found.")
            return 0
        print(f"Input mode of {len(games)} installed games:")
        _print_list(cfg, games)
        return 0

    matches = match_games(games, name)
    if not matches:
        print(f"No installed game matches {name!r}. Run `xss game --list` to see them.")
        return 1
    if len(matches) > 1:
        print(f"{name!r} matches more than one game; use its full name:")
        for g in matches:
            print(f"  {g.name} ({LABELS[g.source]})")
        return 1
    game = matches[0]

    if mode is None:
        print(f"{game.name}: {game_input_mode(cfg, game)}")
        return 0
    raw = load_raw()
    if set_input(raw, game, mode):
        save_raw(raw)
        print(f"{game.name}: input set to {mode}. This takes effect the next time the game starts.")
    else:
        print(f"{game.name}: input is already {mode}.")
    if mode != CONTROLLER:
        print("In Steam, open the shortcut's controller settings and pick a keyboard-and-mouse layout, "
              "e.g. \"Keyboard (WASD) and Mouse\".")
    return 0
