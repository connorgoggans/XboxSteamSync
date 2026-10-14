import hashlib
import io
import logging
import zipfile

import pytest

from xboxsteamsync import paths, update

DAY = 24 * 3600
NOW = 1_800_000_000


@pytest.mark.parametrize("newer, older", [
    ("v0.4.0", "0.3.0"),
    ("0.3.1", "v0.3.0"),
    ("1.0", "0.10.0"),
    ("0.10.0", "0.9.0"),
    ("0.4.0", "0.4.0-beta.2"),
    ("0.4.0-beta.10", "0.4.0-beta.2"),
    ("0.4.0-beta.2", "0.4.0-beta"),
    ("0.4.0-beta", "0.4.0-alpha.5"),
    ("0.4.0-rc1", "0.4.0-beta.1"),
])
def test_version_order(newer, older):
    assert update.is_newer(newer, older)
    assert not update.is_newer(older, newer)


@pytest.mark.parametrize("tag", ["v0.3.0", "0.3", "0.3.0", "garbage", "", None])
def test_not_newer(tag):
    assert not update.is_newer(tag, "0.3.0")


def test_check_due():
    assert update.check_due({}, NOW)
    assert not update.check_due({"last_check": NOW - 3600}, NOW)
    assert update.check_due({"last_check": NOW - DAY - 1}, NOW)
    assert update.check_due({"last_check": NOW + 3600}, NOW)  # clock went backwards
    assert update.check_due({"last_check": "bad"}, NOW)


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


ZIP = _zip({"XboxSteamSync/xss.exe": b"exe", "XboxSteamSync/install.ps1": b"# installer"})
SHA = f"{hashlib.sha256(ZIP).hexdigest()}  {update.ASSET}".encode()


def test_verify():
    update.verify(ZIP, SHA.decode())
    update.verify(ZIP, "﻿" + hashlib.sha256(ZIP).hexdigest().upper() + "\r\n")
    with pytest.raises(update.ChecksumMismatch):
        update.verify(ZIP + b"x", SHA.decode())
    with pytest.raises(update.ChecksumMismatch):
        update.verify(ZIP, "Not Found")


def test_download_and_extract_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_get", lambda url, timeout: SHA if url.endswith(".sha256") else ZIP)
    zip_path = update.download("v0.4.0", tmp_path / "updates")
    assert zip_path.read_bytes() == ZIP
    assert update.extract_installer(zip_path, tmp_path).read_bytes() == b"# installer"


def test_zip_without_installer(tmp_path):
    (tmp_path / "a.zip").write_bytes(_zip({"XboxSteamSync/xss.exe": b"exe"}))
    with pytest.raises(update.UpdateError):
        update.extract_installer(tmp_path / "a.zip", tmp_path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An installed build at v0.3.0 whose GitHub latest release is v0.4.0. Records API
    calls, downloads and installer starts instead of making them."""
    calls = {"api": 0, "zip": 0, "installer": [], "config": {"auto_update": True}, "busy": [],
             "zip_data": ZIP}
    monkeypatch.setattr(paths, "UPDATE_STATE_PATH", tmp_path / "update_state.json")
    monkeypatch.setattr(paths, "UPDATES_DIR", tmp_path / "updates")
    monkeypatch.setattr(update, "LOG_PATH", tmp_path / "update.log")
    monkeypatch.setattr(update, "__version__", "0.3.0")
    monkeypatch.setattr(update, "can_update", lambda: True)
    monkeypatch.setattr(update, "load_config", lambda: calls["config"])
    monkeypatch.setattr(update, "app_in_use", lambda: calls["busy"])
    monkeypatch.setattr(update, "started", None)

    def get(url, timeout):
        if url == update.LATEST_URL:
            calls["api"] += 1
            return b'{"tag_name": "v0.4.0"}'
        if url.endswith(".sha256"):
            return SHA
        calls["zip"] += 1
        return calls["zip_data"]
    monkeypatch.setattr(update, "_get", get)
    monkeypatch.setattr(update, "_start_installer", lambda script, zip_path: calls["installer"].append(script))
    _close_log()
    yield calls
    _close_log()


def _close_log():
    log = logging.getLogger("xboxsteamsync.update")
    for handler in [h for h in log.handlers if isinstance(h, logging.FileHandler)]:
        log.removeHandler(handler)
        handler.close()


def test_auto_update_installs_newer_release(env, capsys):
    update.auto_update(NOW)
    assert env["api"] == 1 and len(env["installer"]) == 1
    assert env["installer"][0].read_bytes() == b"# installer"
    assert update.started == "v0.4.0"
    assert "v0.4.0 is available" in capsys.readouterr().out
    state = update.load_state()
    assert state["latest"] == "v0.4.0" and state["last_check"] == NOW


def test_checks_at_most_once_a_day(env):
    env["busy"] = ["xss-launch.exe"]  # so nothing is installed either
    update.auto_update(NOW)
    update.auto_update(NOW + 3600)
    assert env["api"] == 1
    update.auto_update(NOW + DAY + 1)
    assert env["api"] == 2


def test_up_to_date_does_nothing(env, monkeypatch):
    monkeypatch.setattr(update, "__version__", "0.4.0")
    (tmp_updates := paths.UPDATES_DIR).mkdir()
    update.auto_update(NOW)
    assert env["api"] == 1 and env["zip"] == 0 and not env["installer"]
    assert not tmp_updates.exists()  # leftovers of the installed update are removed


def test_skipped_when_opted_out(env):
    env["config"] = {"auto_update": False}
    update.auto_update(NOW)
    assert env["api"] == 0 and not env["installer"]


def test_skipped_when_not_installed_build(env, monkeypatch):
    monkeypatch.setattr(update, "can_update", lambda: False)
    update.auto_update(NOW)
    assert env["api"] == 0


def test_can_update_needs_frozen_build_in_app_dir(monkeypatch):
    monkeypatch.setattr(paths, "CUSTOM_HOME", False)
    monkeypatch.setattr(paths, "running_from_install_dir", lambda: True)
    monkeypatch.setattr(paths, "FROZEN", False)
    assert not update.can_update()  # running from source
    monkeypatch.setattr(paths, "FROZEN", True)
    assert update.can_update()
    monkeypatch.setattr(paths, "running_from_install_dir", lambda: False)
    assert not update.can_update()


def test_waits_while_a_game_runs_then_retries(env):
    env["busy"] = ["xss-launch.exe"]
    update.auto_update(NOW)
    assert env["zip"] == 0 and not env["installer"]
    env["busy"] = []
    update.auto_update(NOW + 600)  # next run, no new API call needed
    assert env["api"] == 1 and len(env["installer"]) == 1


def test_checksum_mismatch_is_not_installed(env, capsys):
    env["zip_data"] = ZIP + b"tampered"
    update.auto_update(NOW)
    assert env["zip"] == 1 and not env["installer"] and update.started is None
    assert capsys.readouterr().out == ""  # silent; the command carries on
    assert "SHA-256" in update.LOG_PATH.read_text(encoding="utf-8")
    assert not (paths.UPDATES_DIR / update.ASSET).exists()
    update.auto_update(NOW + 3600)  # not downloaded again the same day
    assert env["zip"] == 1
    update.auto_update(NOW + DAY + 1)
    assert env["zip"] == 2


def test_network_errors_are_ignored(env, monkeypatch, capsys):
    def offline(url, timeout):
        raise OSError("no network")
    monkeypatch.setattr(update, "_get", offline)
    update.auto_update(NOW)
    assert capsys.readouterr().out == ""
    assert update.load_state()["last_check"] == NOW


def test_resync_needed(env, monkeypatch):
    assert update.resync_needed() is None  # nothing recorded: synced by the first release
    monkeypatch.setattr(update, "SHORTCUTS_CHANGED_IN", "0.5.0")
    assert update.resync_needed() == "0.2.0"
    monkeypatch.setattr(update, "__version__", "0.5.0")
    update.record_sync()
    assert update.resync_needed() is None
