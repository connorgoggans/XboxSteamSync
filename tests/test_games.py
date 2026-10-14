from pathlib import Path

import pytest

from xboxsteamsync.config import DEFAULT_CONFIG, game_entry, input_mode
from xboxsteamsync.games import invalid_settings, match_games, set_input, unknown_settings
from xboxsteamsync.sources.xbox import XboxGame

AOM = ("Age of Mythology- Retold Standard Edition", "Microsoft.AthensStandardEdition", "Age of Mythology: Retold")


def _cfg(games):
    return {**DEFAULT_CONFIG, "games": games}


def _game(folder, identity, name):
    return XboxGame(folder=folder, name=name, identity=identity, aumid=f"{identity}_8wekyb3d8bbwe!Game",
                    content_dir=Path(r"C:\XboxGames") / folder / "Content", install_dir="", exes=[])


@pytest.mark.parametrize("key", [AOM[0], AOM[1], AOM[2], AOM[2].upper()])
def test_matches_folder_package_or_display_name(key):
    cfg = _cfg({key: {"input": "keyboard-mouse"}})
    assert game_entry(cfg, *AOM) == (key, {"input": "keyboard-mouse"})
    assert input_mode(cfg, *AOM) == "keyboard-mouse"


def test_folder_name_wins_over_display_name():
    cfg = _cfg({AOM[2]: {"input": "keyboard-mouse"}, AOM[0]: {"input": "controller"}})
    assert input_mode(cfg, *AOM) == "controller"


def test_default_is_controller():
    assert input_mode(DEFAULT_CONFIG, *AOM) == "controller"
    assert input_mode(_cfg({"Other Game": {"input": "keyboard-mouse"}}), *AOM) == "controller"
    assert input_mode(_cfg({AOM[0]: {}}), *AOM) == "controller"


def test_invalid_setting_falls_back_to_controller():
    cfg = _cfg({AOM[0]: {"input": "mouse"}, "Other": "keyboard-mouse"})
    assert input_mode(cfg, *AOM) == "controller"
    assert input_mode(cfg, "Other") == "controller"  # not an object
    assert invalid_settings(cfg) == {AOM[0]: "mouse"}


def test_unknown_settings():
    cfg = _cfg({AOM[2]: {"input": "keyboard-mouse"}, "Uninstalled Game": {"input": "keyboard-mouse"}})
    assert unknown_settings(cfg, [_game(*AOM)]) == ["Uninstalled Game"]


def test_match_games():
    games = [_game(*AOM), _game("Forza Horizon 6", "Microsoft.ForteBaseGame", "Forza Horizon 6"),
             _game("Forza Horizon 5", "Microsoft.624F8B84B80", "Forza Horizon 5")]
    assert match_games(games, "microsoft.athensstandardedition") == [games[0]]
    assert match_games(games, "mythology") == [games[0]]
    assert match_games(games, "Forza Horizon 6") == [games[1]]
    assert match_games(games, "forza") == games[1:]
    assert match_games(games, "halo") == []


def test_set_input_adds_entry_by_folder_and_keeps_other_settings():
    raw = {"collection_name": "Xbox"}
    assert set_input(raw, _game(*AOM), "keyboard-mouse")
    assert raw == {"collection_name": "Xbox", "games": {AOM[0]: {"input": "keyboard-mouse"}}}
    assert not set_input(raw, _game(*AOM), "keyboard-mouse")


def test_set_input_updates_the_existing_entry():
    raw = {"games": {AOM[2]: {"input": "keyboard-mouse", "note": "x"}}}
    assert set_input(raw, _game(*AOM), "controller")
    assert raw == {"games": {AOM[2]: {"note": "x"}}}


def test_set_input_controller_removes_empty_entry():
    raw = {"games": {AOM[0]: {"input": "keyboard-mouse"}}}
    assert set_input(raw, _game(*AOM), "controller")
    assert raw == {"games": {}}
    assert not set_input({}, _game(*AOM), "controller")
