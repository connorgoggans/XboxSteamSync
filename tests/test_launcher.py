import shutil

import pytest

from conftest import FIXTURES
from xboxsteamsync import launcher
from xboxsteamsync.config import DEFAULT_CONFIG

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
