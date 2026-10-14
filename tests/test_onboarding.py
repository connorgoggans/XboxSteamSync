import json
from pathlib import Path

import pytest

from xboxsteamsync import onboarding, paths
from xboxsteamsync.config import DEFAULT_CONFIG, load_config, source_state
from xboxsteamsync.sources import Detection, Game, included


def _game(source, game_id, name):
    return Game(source, game_id, name, name, game_id, [Path(r"C:\Games") / name])


DETECTIONS = {
    "xbox": Detection("xbox", True, [_game("xbox", "Forza_x!Game", "Forza Horizon 6")]),
    "epic": Detection("epic", True, [_game("epic", "Ginger", "Cyberpunk 2077"),
                                     _game("epic", "dredge", "DREDGE")]),
    "battlenet": Detection("battlenet", True, [_game("battlenet", "fenris", "Diablo IV")]),
}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(paths, "UPDATE_STATE_PATH", tmp_path / "update_state.json")
    return tmp_path


def _existing_user(home, raw=None):
    (home / "update_state.json").write_text(json.dumps({"last_synced_version": "0.4.1"}), encoding="utf-8")
    if raw is not None:
        (home / "config.json").write_text(json.dumps(raw), encoding="utf-8")


def _answers(monkeypatch, *answers):
    asked = []
    queue = list(answers)

    def fake_input(prompt):
        asked.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def _saved(home):
    path = home / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_xbox_counts_as_on_without_an_entry():
    assert source_state(DEFAULT_CONFIG, "xbox") is True
    assert source_state(DEFAULT_CONFIG, "epic") is None
    cfg = {**DEFAULT_CONFIG, "sources": {"xbox": False, "epic": True}}
    assert (source_state(cfg, "xbox"), source_state(cfg, "epic")) == (False, True)


@pytest.mark.parametrize("mode", [onboarding.DRY_RUN, onboarding.QUIET])
def test_nobody_to_ask_leaves_launchers_undecided(home, capsys, mode):
    _existing_user(home)
    states = onboarding.choose(DETECTIONS, load_config(), mode)
    assert states == {"xbox": True, "epic": None, "battlenet": None}
    assert _saved(home) is None
    out = capsys.readouterr().out
    assert "Epic Games (2 games)" in out and "Battle.net (1 game)" in out
    assert ("xss sources" in out) == (mode == onboarding.QUIET)


def test_setup_yes_links_everything(home):
    states = onboarding.choose(DETECTIONS, load_config(), onboarding.YES)
    assert states == {"xbox": True, "epic": True, "battlenet": True}
    assert _saved(home) == {"sources": {"xbox": True, "epic": True, "battlenet": True}}


def test_first_run_link_everything(home, monkeypatch):
    asked = _answers(monkeypatch, "")
    assert onboarding.choose(DETECTIONS, load_config(), onboarding.ASK) == {
        "xbox": True, "epic": True, "battlenet": True}
    assert len(asked) == 1 and "all of them" in asked[0]
    assert _saved(home)["sources"] == {"xbox": True, "epic": True, "battlenet": True}


def test_first_run_choose_each(home, monkeypatch):
    # no to "all of them"; Xbox: all; Epic: pick (yes to Cyberpunk, no to DREDGE); Battle.net: skip
    _answers(monkeypatch, "n", "a", "p", "y", "n", "s")
    states = onboarding.choose(DETECTIONS, load_config(), onboarding.ASK)
    assert states == {"xbox": True, "epic": True, "battlenet": False}
    assert _saved(home) == {"sources": {"xbox": True, "epic": True, "battlenet": False}, "exclude": ["DREDGE"]}


def test_existing_user_is_asked_only_about_new_launchers(home, monkeypatch):
    _existing_user(home, {"collection_name": "Xbox", "sources": {"battlenet": False}})
    asked = _answers(monkeypatch, "a")
    states = onboarding.choose(DETECTIONS, load_config(), onboarding.ASK)
    assert states == {"xbox": True, "epic": True, "battlenet": False}
    assert len(asked) == 1 and asked[0].startswith("Epic Games (2 games)")  # no "all of them?"
    assert _saved(home) == {"collection_name": "Xbox", "sources": {"battlenet": False, "epic": True}}


def test_existing_user_with_nothing_new_is_not_asked(home, monkeypatch):
    _existing_user(home, {"sources": {"epic": True, "battlenet": False}})
    asked = _answers(monkeypatch)
    assert onboarding.choose(DETECTIONS, load_config(), onboarding.ASK)["epic"] is True
    assert asked == []


def test_no_answer_saves_nothing(home, monkeypatch):
    _existing_user(home)
    _answers(monkeypatch)  # EOF straight away
    assert onboarding.choose(DETECTIONS, load_config(), onboarding.ASK)["epic"] is None
    assert _saved(home) is None


def test_mode_for(monkeypatch):
    assert onboarding.mode_for(dry_run=True, assume_yes=True) == onboarding.DRY_RUN
    assert onboarding.mode_for(assume_yes=True) == onboarding.YES
    monkeypatch.setattr("sys.stdin", None)
    assert onboarding.mode_for() == onboarding.QUIET


def test_pick_rewrites_only_that_launchers_excludes_and_later_games_are_added():
    games = DETECTIONS["epic"].games
    raw = {"exclude": ["Some Xbox Game", "Cyberpunk 2077"]}
    onboarding.set_picks(raw, games, [games[0]])
    assert raw["exclude"] == ["Some Xbox Game", "DREDGE"]
    later = _game("epic", "newapp", "A Game Installed Later")
    assert [g.name for g in included([*games, later], raw["exclude"])] == ["Cyberpunk 2077",
                                                                           "A Game Installed Later"]


def test_sources_on_off(home, capsys):
    assert onboarding.run_sources("epic", on=True) == 0
    assert onboarding.run_sources("Battle.net", on=False) == 0
    assert _saved(home) == {"sources": {"epic": True, "battlenet": False}}
    assert onboarding.run_sources("steam", on=True) == 1
