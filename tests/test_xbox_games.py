import shutil
import struct

import pytest

from conftest import FIXTURES
from xboxsteamsync.xbox_games import find_games, parse_gaming_root, read_game_config

CONFIGS = FIXTURES / "configs"


def test_parse_gaming_root_real_bytes():
    # C:\.GamingRoot from a real PC
    data = bytes.fromhex("52474258" "01000000" "580062006F00780047006100" "6D00650073000000")
    assert parse_gaming_root(data) == ["XboxGames"]


def test_parse_gaming_root_multiple():
    names = ["XboxGames", r"Games\Xbox"]
    data = b"RGBX" + struct.pack("<I", 2) + b"".join(n.encode("utf-16-le") + b"\0\0" for n in names)
    assert parse_gaming_root(data) == names


def test_parse_gaming_root_rejects_other_files():
    with pytest.raises(ValueError):
        parse_gaming_root(b"nope")


def test_game():
    info = read_game_config(CONFIGS / "game.config")
    assert info.identity == "DevolverDigital.BallxPit"
    assert info.name == "Ball X Pit"
    assert info.exes == ["BALL x PIT.exe"]  # commented-out executables are ignored


def test_game_with_namespace_subdir_exes_and_resource_name():
    info = read_game_config(CONFIGS / "game_subdir_exe.config")
    assert info.identity == "Studio.Sample"
    assert info.name is None  # ms-resource: names fall back to the folder name
    assert info.exes == ["Sample-WinGDK-Shipping.exe", "Launcher.exe"]


@pytest.mark.parametrize("name", ["dlc.config", "dlc_with_exe.config"])
def test_dlc_is_not_a_game(name):
    assert read_game_config(CONFIGS / name) is None


def _install(root, folder, config):
    content = root / folder / "Content"
    content.mkdir(parents=True)
    shutil.copy(CONFIGS / config, content / "MicrosoftGame.config")


def test_find_games(tmp_path):
    root = tmp_path / "XboxGames"
    _install(root, "Ball X Pit", "game.config")
    _install(root, "Sample", "game_subdir_exe.config")
    _install(root, "DOOM- The Dark Ages Void DOOM Slayer Skin", "dlc.config")
    (root / "GameSave" / "wgs").mkdir(parents=True)  # save folders have no config
    families = {
        "devolverdigital.ballxpit": ("DevolverDigital.BallxPit_6kzv4j18v0c96", r"C:\Program Files\WindowsApps\x"),
        "studio.sample": ("Studio.Sample_abc", ""),
    }
    games = find_games([root, root], families=families)  # same root twice: no duplicates
    assert [(g.folder, g.name, g.aumid) for g in games] == [
        ("Ball X Pit", "Ball X Pit", "DevolverDigital.BallxPit_6kzv4j18v0c96!Game"),
        ("Sample", "Sample", "Studio.Sample_abc!AppSampleShipping"),
    ]
    assert find_games([root], exclude=["ball x pit"], families=families)[0].folder == "Sample"


def test_unregistered_package_is_skipped(tmp_path, capsys):
    _install(tmp_path, "Ball X Pit", "game.config")
    assert find_games([tmp_path], families={}) == []
    assert "not registered" in capsys.readouterr().out
