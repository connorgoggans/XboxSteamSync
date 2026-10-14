import shutil
from pathlib import Path

from conftest import FIXTURES
from xboxsteamsync import steam
from xboxsteamsync.config import DEFAULT_CONFIG
from xboxsteamsync.sync import launch_options, plan_user, shortcut_dirs, xbox_kept_reason
from xboxsteamsync.xbox_games import XboxGame, find_games

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
    game = _game("Forza Horizon 6", "Microsoft.ForteBaseGame_8wekyb3d8bbwe!Forzahorizon6")
    game.install_dir = r"C:\Program Files\WindowsApps\Forza"
    assert shortcut_dirs({"LaunchOptions": launch_options(game)}) == [game.content_dir, Path(game.install_dir)]
