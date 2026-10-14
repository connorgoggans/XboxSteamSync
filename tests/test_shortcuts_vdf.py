import shutil

from conftest import FIXTURES
from xboxsteamsync import steam
from xboxsteamsync.sync import is_legacy, is_managed

REAL = FIXTURES / "shortcuts.vdf"  # a real Steam file, with user names scrubbed


def test_round_trip_is_byte_identical():
    data = REAL.read_bytes()
    assert steam.encode_shortcuts(steam.parse_shortcuts(data)) == data


def test_parses_fields_with_their_types():
    shortcuts = steam.parse_shortcuts(REAL.read_bytes())
    assert len(shortcuts) == 9
    first = shortcuts[0]
    assert first["AppName"] == "SISR Marker"
    assert isinstance(first["appid"], int) and first["appid"] < 0  # stored signed
    assert isinstance(first["tags"], dict)
    assert isinstance(first["LastPlayTime"], int)


def test_save_reports_changes_only(tmp_path):
    shutil.copy(REAL, tmp_path / "shortcuts.vdf")
    shortcuts = steam.load_shortcuts(tmp_path)
    assert steam.save_shortcuts(tmp_path, shortcuts) is False
    shortcuts[1]["AppName"] = "Renamed"
    assert steam.save_shortcuts(tmp_path, shortcuts, write=False) is True
    assert steam.load_shortcuts(tmp_path)[1]["AppName"] != "Renamed"  # write=False wrote nothing
    assert steam.save_shortcuts(tmp_path, shortcuts) is True
    assert steam.load_shortcuts(tmp_path)[1]["AppName"] == "Renamed"


def test_missing_file_and_empty_list_is_no_change(tmp_path):
    assert steam.load_shortcuts(tmp_path) == []
    assert steam.save_shortcuts(tmp_path, []) is False


def test_managed_detection():
    shortcuts = steam.parse_shortcuts(REAL.read_bytes())
    assert not is_managed(shortcuts[0])  # SISR's own marker shortcut
    assert all(is_managed(s) and is_legacy(s) for s in shortcuts[1:])
    new = steam.new_shortcut(1 | 0x80000000, "Game", r'"C:\x\xss-launch.exe"', r'"C:\x"', "--aumid A!B")
    assert is_managed(new) and not is_legacy(new)
    assert not is_managed(steam.new_shortcut(1, "Other", r'"C:\other.exe"', "", "--aumid A!B"))


def test_signed_unsigned_appids():
    assert steam.to_unsigned(steam.to_signed(0x9234ABCD)) == 0x9234ABCD
    assert steam.shortcut_appid('"x"', "y") & 0x80000000
