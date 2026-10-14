import shutil
from pathlib import Path

from conftest import FIXTURES
from xboxsteamsync import steam
from xboxsteamsync.config import DEFAULT_CONFIG
from xboxsteamsync.sync import launch_options, plan_user
from xboxsteamsync.xbox_games import XboxGame

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
