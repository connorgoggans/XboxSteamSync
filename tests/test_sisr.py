from conftest import FIXTURES
from xboxsteamsync import sisr

HELP = (FIXTURES / "sisr-0.6.2-help.txt").read_text(encoding="utf-8")
RUN_HELP = (FIXTURES / "sisr-0.6.2-run-help.txt").read_text(encoding="utf-8")


def test_parse_version():
    assert sisr.parse_version(HELP) == (0, 6, 2)
    assert sisr.parse_version("no version here") is None


def test_tested_versions_match_help_output():
    flags = sisr.parse_flags(RUN_HELP)
    assert sisr.TESTED_VERSIONS["0.6.2"] <= flags


def test_default_args_are_accepted():
    from xboxsteamsync.config import DEFAULT_CONFIG
    flags = sisr.parse_flags(RUN_HELP)
    assert sisr.unsupported_args(DEFAULT_CONFIG["sisr_args"], flags) == []


def test_old_short_flags_are_rejected():
    flags = sisr.parse_flags(RUN_HELP)
    assert sisr.unsupported_args(["-w", "-f", "--window.max-fps=30", "value"], flags) == ["-w", "-f"]


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_install_script_hash_is_checked(tmp_path, monkeypatch):
    import hashlib

    import pytest

    monkeypatch.setattr(sisr.urllib.request, "urlopen", lambda req, timeout: _Resp(b"Write-Host evil"))
    with pytest.raises(sisr.ScriptHashMismatch):
        sisr.download_install_script(tmp_path / "install.ps1")
    assert not (tmp_path / "install.ps1").exists()

    good = b"Write-Host ok"
    monkeypatch.setattr(sisr, "INSTALL_SCRIPT_SHA256", hashlib.sha256(good).hexdigest())
    monkeypatch.setattr(sisr.urllib.request, "urlopen", lambda req, timeout: _Resp(good))
    assert sisr.download_install_script(tmp_path / "install.ps1").read_bytes() == good
