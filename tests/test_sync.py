import json
import shutil
from pathlib import Path

from conftest import FIXTURES
from xboxsteamsync import steam
from xboxsteamsync.artwork import ArtState
from xboxsteamsync.config import DEFAULT_CONFIG
from xboxsteamsync.sources import Detection, Game, epic, shortcut_dirs
from xboxsteamsync.sync import (apply_plan, keep_missing_for, launch_options, managed_key, plan_user, sgdb_id,
                                xbox_kept_reason)
from xboxsteamsync.sources.xbox import XboxGame, find_games

LAUNCHER = Path(r"C:\Users\player\AppData\Local\XboxSteamSync\app\xss-launch.exe")


def _game(name, aumid, folder=None):
    folder = folder or name
    return XboxGame(folder=folder, name=name, identity=aumid.split("_")[0], aumid=aumid,
                    content_dir=Path(r"C:\XboxGames") / folder / "Content", install_dir="", exes=["game.exe"])


def _user(tmp_path):
    shutil.copy(FIXTURES / "shortcuts.vdf", tmp_path / "shortcuts.vdf")
    return tmp_path


def test_migrates_legacy_shortcuts_keeping_app_ids(tmp_path):
    user = _user(tmp_path)
    before = {s["AppName"]: s for s in steam.load_shortcuts(user)}
    games = [
        _game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6"),
        _game("Persona 5 Royal", "SEGAofAmericaInc.F0cb6b3aer_s751p9cej88mt!P5R"),
        _game("New Game", "Studio.New_abc!Game"),
    ]
    plan = plan_user(user, games, DEFAULT_CONFIG, LAUNCHER)
    assert plan.changed
    assert plan.migrated_names == ["Forza Horizon 6", "Persona 5 Royal"]
    assert plan.added_names == ["New Game"]
    assert len(plan.removed) == 6  # the other legacy shortcuts are no longer installed

    after = {s["AppName"]: s for s in plan.shortcuts}
    assert set(after) == {"SISR Marker", "Forza Horizon 6", "Persona 5 Royal", "New Game"}
    assert after["SISR Marker"] == before["SISR Marker"]  # not ours, untouched
    forza = after["Forza Horizon 6"]
    assert forza["appid"] == before["Forza Horizon 6"]["appid"]
    assert forza["icon"] == before["Forza Horizon 6"]["icon"]  # no art lookup when grid is missing
    assert forza["Exe"] == f'"{LAUNCHER}"'
    assert forza["LaunchOptions"] == launch_options(games[0])
    assert "launcher.py" not in forza["LaunchOptions"]
    # nothing written by planning
    assert (user / "shortcuts.vdf").read_bytes() == (FIXTURES / "shortcuts.vdf").read_bytes()


def test_second_run_is_up_to_date(tmp_path):
    user = _user(tmp_path)
    games = [_game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6")]
    plan = plan_user(user, games, DEFAULT_CONFIG, LAUNCHER)
    steam.save_shortcuts(user, plan.shortcuts)
    again = plan_user(user, games, DEFAULT_CONFIG, LAUNCHER)
    assert not again.changed and not again.added_names and not again.removed and not again.migrated_names


def test_new_shortcut_app_id_matches_steam_formula(tmp_path):
    games = [_game("New Game", "Studio.New_abc!Game")]
    plan = plan_user(tmp_path, games, DEFAULT_CONFIG, LAUNCHER)
    assert plan.kept == [steam.shortcut_appid(f'"{LAUNCHER}"', "New Game")]
    assert steam.to_unsigned(plan.shortcuts[0]["appid"]) == plan.kept[0]


def _synced_ball_x_pit(tmp_path):
    """A user whose shortcuts have Ball X Pit, installed under tmp_path/XboxGames."""
    root = tmp_path / "XboxGames"
    content = root / "Ball X Pit" / "Content"
    content.mkdir(parents=True)
    shutil.copy(FIXTURES / "configs" / "game.config", content / "MicrosoftGame.config")
    user = tmp_path / "user"
    user.mkdir()
    games = find_games([root], families=FAMILIES)
    steam.save_shortcuts(user, plan_user(user, games, DEFAULT_CONFIG, LAUNCHER).shortcuts)
    return root, user


FAMILIES = {"devolverdigital.ballxpit": ("DevolverDigital.BallxPit_6kzv4j18v0c96", "")}


def test_unregistered_game_still_on_disk_is_kept(tmp_path):
    root, user = _synced_ball_x_pit(tmp_path)
    games = find_games([root], families={})  # e.g. the Xbox app is repairing it
    plan = plan_user(user, games, DEFAULT_CONFIG, LAUNCHER, keep_missing=xbox_kept_reason([root], []))
    assert not plan.changed and not plan.removed
    assert [n for n, _ in plan.kept_names] == ["Ball X Pit"]


def test_nothing_removed_without_install_folders(tmp_path):
    _, user = _synced_ball_x_pit(tmp_path)
    plan = plan_user(user, [], DEFAULT_CONFIG, LAUNCHER, keep_missing=xbox_kept_reason([], []))
    assert not plan.changed and not plan.removed


def test_offline_drive_is_kept(tmp_path):
    root, user = _synced_ball_x_pit(tmp_path)
    shutil.rmtree(root)  # the whole install folder is gone, like a disconnected drive
    plan = plan_user(user, [], DEFAULT_CONFIG, LAUNCHER, keep_missing=xbox_kept_reason([tmp_path / "Other"], []))
    assert not plan.changed and "isn't available" in plan.kept_names[0][1]


def test_uninstalled_or_excluded_game_is_removed(tmp_path):
    root, user = _synced_ball_x_pit(tmp_path)
    plan = plan_user(user, find_games([root], ["Ball X Pit"], FAMILIES), DEFAULT_CONFIG, LAUNCHER,
                     keep_missing=xbox_kept_reason([root], ["Ball X Pit"]))
    assert plan.removed_names == ["Ball X Pit"]
    shutil.rmtree(root / "Ball X Pit")
    plan = plan_user(user, find_games([root], families=FAMILIES), DEFAULT_CONFIG, LAUNCHER,
                     keep_missing=xbox_kept_reason([root], []))
    assert plan.removed_names == ["Ball X Pit"]


def test_shortcut_dirs():
    content = Path(r"C:\XboxGames\Forza Horizon 6\Content")
    install = r"C:\Program Files\WindowsApps\Forza"
    game = XboxGame("Forza Horizon 6", "Forza Horizon 6", "Microsoft.ForteBaseGame",
                    "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6", content, install, [])
    assert shortcut_dirs({"LaunchOptions": launch_options(game)}) == [content, Path(install)]


# ---------------------------------------------------------------------------
# Epic Games and Battle.net
# ---------------------------------------------------------------------------


def _launcher_game(tmp_path, source, game_id, name):
    install = tmp_path / "Games" / name
    install.mkdir(parents=True, exist_ok=True)
    (install / "game.exe").write_bytes(b"")
    return Game(source=source, id=game_id, name=name, folder=name, identity=game_id, dirs=[install],
                launch=f"{source}://{game_id}")


def _collections_user(tmp_path):
    user = tmp_path / "user"
    (user / "cloudstorage").mkdir(parents=True)
    (user / "cloudstorage" / "cloud-storage-namespace-1.json").write_text("[]", encoding="utf-8")
    return user


def _members(user):
    entries = json.loads((user / "cloudstorage" / "cloud-storage-namespace-1.json").read_text(encoding="utf-8"))
    out = {}
    for key, e in entries:
        if "value" in e and not e.get("is_deleted"):
            value = json.loads(e["value"])
            out[value["name"]] = value["added"]
    return out


def _sync(user, games, cfg=DEFAULT_CONFIG, keep_missing=None, off=()):
    plan = plan_user(user, games, cfg, LAUNCHER, keep_missing=keep_missing, off=off)
    apply_plan(plan, cfg, ArtState(user / "art_state.json"), user.parent / "backup")
    return plan


def test_managed_keys(tmp_path):
    xbox_game = _game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6")
    cyberpunk = _launcher_game(tmp_path, "epic", "Ginger", "Cyberpunk 2077")
    diablo = _launcher_game(tmp_path, "battlenet", "fenris", "Diablo IV")
    exe = f'"{LAUNCHER}"'
    for game in (xbox_game, cyberpunk, diablo):
        assert managed_key({"Exe": exe, "LaunchOptions": launch_options(game)}) == game.key
    assert launch_options(cyberpunk) == f'--source epic --id Ginger --dir "{cyberpunk.dirs[0]}"'
    assert not managed_key({"Exe": r'"C:\other.exe"', "LaunchOptions": launch_options(cyberpunk)})
    # today's --aumid shortcuts, including the old script version's
    legacy = [s for s in steam.load_shortcuts(FIXTURES) if s["AppName"] == "Forza Horizon 6"][0]
    assert managed_key(legacy) == "xbox:Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6"


def test_one_collection_per_launcher(tmp_path):
    user = _collections_user(tmp_path)
    xbox_game = _game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6")
    cyberpunk = _launcher_game(tmp_path, "epic", "Ginger", "Cyberpunk 2077")
    diablo = _launcher_game(tmp_path, "battlenet", "fenris", "Diablo IV")
    cfg = {**DEFAULT_CONFIG, "collections": {"battlenet": "Blizzard"}}
    plan = _sync(user, [xbox_game, cyberpunk, diablo], cfg)
    ids = dict(zip(plan.added_names, plan.kept))
    assert _members(user) == {"Xbox": [ids["Forza Horizon 6"]], "Epic Games": [ids["Cyberpunk 2077"]],
                              "Blizzard": [ids["Diablo IV"]]}
    assert not plan_user(user, [xbox_game, cyberpunk, diablo], cfg, LAUNCHER).changed


def test_xbox_only_setup_does_not_change_with_launchers_undecided(tmp_path):
    user = _collections_user(tmp_path)
    xbox_game = _game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6")
    _sync(user, [xbox_game])
    detections = {"xbox": Detection("xbox", True, [xbox_game]),
                  "epic": Detection("epic", True, [_launcher_game(tmp_path, "epic", "Ginger", "Cyberpunk 2077")]),
                  "battlenet": Detection("battlenet", False, [])}
    states = {"xbox": True, "epic": None, "battlenet": None}
    plan = plan_user(user, [xbox_game], DEFAULT_CONFIG, LAUNCHER,
                     keep_missing=keep_missing_for(detections, states, []))
    assert not plan.changed
    assert set(_members(user)) == {"Xbox"}


def test_turning_a_launcher_off_removes_its_games_and_empty_collection(tmp_path):
    user = _collections_user(tmp_path)
    xbox_game = _game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6")
    cyberpunk = _launcher_game(tmp_path, "epic", "Ginger", "Cyberpunk 2077")
    _sync(user, [xbox_game, cyberpunk])
    detections = {"xbox": Detection("xbox", True, [xbox_game]),
                  "epic": Detection("epic", True, [cyberpunk], keep_missing=epic.keep_missing),
                  "battlenet": Detection("battlenet", False, [])}
    states = {"xbox": True, "epic": False, "battlenet": None}
    plan = _sync(user, [xbox_game], keep_missing=keep_missing_for(detections, states, []), off={"epic"})
    assert plan.removed_names == ["Cyberpunk 2077"]  # still installed, but turned off
    assert set(_members(user)) == {"Xbox"}
    assert [s["AppName"] for s in steam.load_shortcuts(user)] == ["Forza Horizon 6"]


def test_launcher_games_missing_from_its_data(tmp_path):
    user = _collections_user(tmp_path)
    cyberpunk = _launcher_game(tmp_path, "epic", "Ginger", "Cyberpunk 2077")
    _sync(user, [cyberpunk])
    states = {"xbox": True, "epic": True, "battlenet": None}

    def plan(detection, exclude=()):
        detections = {"xbox": Detection("xbox", True, []), "epic": detection,
                      "battlenet": Detection("battlenet", False, [])}
        return plan_user(user, [], DEFAULT_CONFIG, LAUNCHER, keep_missing=keep_missing_for(detections, states, exclude))

    gone = Detection("epic", True, [], keep_missing=epic.keep_missing)
    assert "still has the game" in plan(gone).kept_names[0][1]  # e.g. Epic is mid-repair
    assert plan(gone, ["Cyberpunk 2077"]).removed_names == ["Cyberpunk 2077"]
    assert plan(Detection("epic", True, [], error="couldn't read x.item")).kept_names == [
        ("Cyberpunk 2077", "couldn't read x.item")]
    states["epic"] = None
    assert "isn't turned on or off" in plan(gone).kept_names[0][1]
    states["epic"] = True
    shutil.rmtree(cyberpunk.dirs[0])
    assert plan(gone).removed_names == ["Cyberpunk 2077"]  # uninstalled


def test_same_name_from_two_launchers_gets_distinct_app_ids(tmp_path):
    xbox_game = _game("DOOM", "Bethesda.Doom_abc!Game")
    epic_game = _launcher_game(tmp_path, "epic", "Doom", "DOOM")
    plan = plan_user(tmp_path, [xbox_game, epic_game], DEFAULT_CONFIG, LAUNCHER)
    assert plan.added_names == ["DOOM", "DOOM (Epic Games)"]
    assert len(set(plan.kept)) == 2


def test_sgdb_id_matches_any_setting_key(tmp_path):
    game = _launcher_game(tmp_path, "battlenet", "fenris", "Diablo IV")
    assert sgdb_id({"FENRIS": 7}, game) == 7
    assert sgdb_id({"diablo iv": 8}, game) == 8
    assert sgdb_id({}, game) is None
