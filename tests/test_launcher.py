import logging
import shutil
from pathlib import Path

import pytest

from conftest import FIXTURES
from xboxsteamsync import launcher
from xboxsteamsync.config import DEFAULT_CONFIG
from xboxsteamsync.sources import Game

AUMID = "DevolverDigital.BallxPit_8wekyb3d8bbwe!Game"


class FakePopen:
    calls = []

    def __init__(self, args, **kwargs):
        self.args, self.pid, self.returncode = args, 1234, None
        FakePopen.calls.append(args)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    kill = terminate


@pytest.fixture
def content_dir(tmp_path):
    content = tmp_path / "XboxGames" / "Ball X Pit Folder" / "Content"
    content.mkdir(parents=True)
    shutil.copy(FIXTURES / "configs" / "game.config", content / "MicrosoftGame.config")
    return content


@pytest.fixture
def run(monkeypatch, tmp_path, content_dir):
    """Run the launcher with `games` settings; returns the commands it started."""
    sisr = tmp_path / "SISR.exe"
    sisr.write_bytes(b"")
    FakePopen.calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(launcher, "_kill_with_us", lambda proc: None)
    monkeypatch.setattr(launcher, "_game_running", lambda prefixes, exe_names: None)
    monkeypatch.setattr(launcher, "STARTUP_TIMEOUT", -1)  # give up waiting for the game straight away
    monkeypatch.setattr(launcher, "SISR_WARMUP", 0)

    def _run(games):
        cfg = {**DEFAULT_CONFIG, "sisr_path": str(sisr), "games": games}
        monkeypatch.setattr(launcher, "load_config", lambda **kw: cfg)
        launcher.main(["--aumid", AUMID, "--dir", str(content_dir), "--exe", "game.exe"])
        return FakePopen.calls
    _run.sisr = str(sisr)
    return _run


def test_game_keys(content_dir):
    assert launcher.game_keys(AUMID, [str(content_dir)]) == (
        "Ball X Pit Folder", "DevolverDigital.BallxPit", "Ball X Pit")
    assert launcher.game_keys(AUMID, []) == ("", "DevolverDigital.BallxPit", "")


def test_controller_mode_starts_sisr(run):
    calls = run({})
    assert calls[0][0].endswith("SISR.exe")
    assert calls[1][0] == "explorer.exe"


@pytest.mark.parametrize("key", ["Ball X Pit Folder", "DevolverDigital.BallxPit", "ball x pit"])
def test_keyboard_mouse_mode_skips_sisr(run, key):
    calls = run({key: {"input": "keyboard-mouse"}})
    assert calls == [["explorer.exe", f"shell:AppsFolder\\{AUMID}"]]


# ---------------------------------------------------------------------------
# Epic Games and Battle.net
# ---------------------------------------------------------------------------

EPIC_URI = "com.epicgames.launcher://apps/ns%3Aitem%3AGinger?action=launch&silent=true"


@pytest.fixture
def run_source(monkeypatch, run, tmp_path):
    """Run the launcher for an Epic/Battle.net game; returns the commands it started."""
    install = tmp_path / "Cyberpunk2077"
    install.mkdir()
    monkeypatch.setattr(launcher, "LAUNCHER_STARTUP_TIMEOUT", -1)
    monkeypatch.setattr(launcher.epic, "find", lambda app: Game(
        "epic", app, "Cyberpunk 2077", "Cyberpunk2077", app, [install], launch=EPIC_URI))
    monkeypatch.setattr(launcher.battlenet, "find", lambda code: None)

    def _run(source, game_id, games=None):
        cfg = {**DEFAULT_CONFIG, "sisr_path": run.sisr, "games": games or {}}
        monkeypatch.setattr(launcher, "load_config", lambda **kw: cfg)
        launcher.main(["--source", source, "--id", game_id, "--dir", str(install)])
        return FakePopen.calls
    return _run


def test_epic_launches_through_its_url(run_source):
    calls = run_source("epic", "Ginger")
    assert calls[0][0].endswith("SISR.exe")
    assert calls[1] == ["rundll32.exe", "url.dll,FileProtocolHandler", EPIC_URI]


@pytest.mark.parametrize("key", ["Cyberpunk2077", "Ginger", "cyberpunk 2077"])
def test_epic_keyboard_mouse_mode_skips_sisr(run_source, key):
    assert run_source("epic", "Ginger", {key: {"input": "keyboard-mouse"}}) == [["rundll32.exe", "url.dll,FileProtocolHandler", EPIC_URI]]


def test_battlenet_running_gets_exec_command(monkeypatch, run_source):
    monkeypatch.setattr(launcher.battlenet, "running_exe", lambda: Path(r"C:\BN\Battle.net.exe"))
    assert run_source("battlenet", "fenris")[1] == [r"C:\BN\Battle.net.exe", "--exec=launch Fen"]
    FakePopen.calls = []
    calls = run_source("battlenet", "fenris", {"Cyberpunk2077": {"battlenet_code": "Custom"}})
    assert calls[1] == [r"C:\BN\Battle.net.exe", "--exec=launch Custom"]


def test_battlenet_unknown_code_opens_battlenet(monkeypatch, run_source):
    monkeypatch.setattr(launcher.battlenet, "running_exe", lambda: Path(r"C:\BN\Battle.net.exe"))
    assert run_source("battlenet", "newgame")[1] == ["rundll32.exe", "url.dll,FileProtocolHandler", "battlenet://game/newgame"]


def test_battlenet_waits_for_its_window_before_exec(monkeypatch, clock):
    FakePopen.calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(launcher.battlenet, "running_exe", lambda: None)
    monkeypatch.setattr(launcher.battlenet, "battlenet_exe", lambda: Path(r"C:\BN\Battle.net.exe"))
    shown = []
    monkeypatch.setattr(launcher.battlenet, "main_window_shown", lambda: bool(shown))
    launch = launcher.BattlenetLaunch("Fen", logging.getLogger("test"))
    launch.start()
    launch.poll()
    assert FakePopen.calls == [["explorer.exe", r"C:\BN\Battle.net.exe"]]  # still starting up
    shown.append(True)
    launch.poll()
    assert FakePopen.calls[-1] == [r"C:\BN\Battle.net.exe", "--exec=launch Fen"]
    launch.poll()
    assert len(FakePopen.calls) == 2
    clock.now += launcher.BATTLENET_RESEND
    launch.poll()  # the game still hasn't appeared: ask again
    assert len(FakePopen.calls) == 3


# ---------------------------------------------------------------------------
# Waiting for the game
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(launcher.time, "monotonic", c.monotonic)
    monkeypatch.setattr(launcher.time, "sleep", c.sleep)
    return c


class FakeProc:
    pid, info = 1, {"exe": r"C:\Games\x.exe"}


def _wait(monkeypatch, clock, running, timeout=300):
    """Run wait_for_game with the game running whenever running(seconds since start)."""
    start = clock.now
    monkeypatch.setattr(launcher, "_game_running", lambda p, e: FakeProc() if running(clock.now - start) else None)
    appeared = launcher.wait_for_game((), set(), timeout, logging.getLogger("test"))
    return appeared, clock.now - start


def test_game_exits_after_grace(monkeypatch, clock):
    appeared, elapsed = _wait(monkeypatch, clock, lambda t: 5 <= t < 100)
    assert appeared and 100 + launcher.HANDOFF_GRACE - launcher.POLL_INTERVAL <= elapsed < 100 + launcher.HANDOFF_GRACE + 2 * launcher.POLL_INTERVAL


def test_short_lived_prelauncher_keeps_waiting(monkeypatch, clock):
    # REDprelauncher runs half a second, then the game starts 60 s later from another launcher.
    appeared, elapsed = _wait(monkeypatch, clock, lambda t: 8 <= t < 9 or 70 <= t < 400)
    assert appeared and elapsed >= 400 + launcher.HANDOFF_GRACE - launcher.POLL_INTERVAL


def test_short_lived_process_ends_at_startup_timeout(monkeypatch, clock):
    appeared, elapsed = _wait(monkeypatch, clock, lambda t: 8 <= t < 9, timeout=180)
    assert appeared and 180 <= elapsed < 180 + launcher.HANDOFF_GRACE + 2 * launcher.POLL_INTERVAL


def test_game_never_appears(monkeypatch, clock):
    appeared, elapsed = _wait(monkeypatch, clock, lambda t: False, timeout=180)
    assert not appeared and 180 <= elapsed < 180 + 2 * launcher.POLL_INTERVAL


def test_startup_timeouts():
    assert launcher.startup_timeout("xbox") == 180
    assert launcher.startup_timeout("epic") == launcher.startup_timeout("battlenet") == 300
