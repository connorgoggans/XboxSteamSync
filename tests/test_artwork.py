import shutil

from PIL import Image

from conftest import FIXTURES
from xboxsteamsync import artwork


def _content(tmp_path, config="game_subdir_exe.config"):
    content = tmp_path / "Content"
    (content / "Assets").mkdir(parents=True)
    shutil.copy(FIXTURES / "configs" / config, content / "MicrosoftGame.config")
    Image.new("RGB", (1920, 1080), "navy").save(content / "Assets" / "SplashScreen.png")
    Image.new("RGBA", (150, 150), "white").save(content / "Assets" / "Logo150.png")
    return content


def test_local_assets_with_namespaced_config(tmp_path):
    content = _content(tmp_path)
    splash, logo = artwork._local_assets(content)
    assert splash == content / "Assets" / "SplashScreen.png"
    assert logo == content / "Assets" / "Logo150.png"


def test_local_assets_without_config(tmp_path):
    assert artwork._local_assets(tmp_path) == (None, None)


def test_ensure_art_from_namespaced_config(tmp_path):
    content = _content(tmp_path)
    grid = tmp_path / "grid"
    state = artwork.ArtState(tmp_path / "art_state.json")
    icon = artwork.ensure_art(grid, 123, "Sample", content, state)
    assert icon == str(grid / "123_icon.png")
    for suffix, size in (("p", (600, 900)), ("", (920, 430)), ("_hero", (1920, 620))):
        with Image.open(grid / f"123{suffix}.png") as img:
            assert img.size == size
        assert state.is_local(grid, 123, suffix)


def test_launcher_art(tmp_path):
    """Epic: wide + tall box art; Battle.net: square key art + a square logo as icon."""
    Image.new("RGB", (2560, 1440), "red").save(tmp_path / "wide.png")
    Image.new("RGB", (1200, 1600), "blue").save(tmp_path / "tall.png")
    Image.new("RGBA", (400, 200), (0, 0, 0, 0)).save(tmp_path / "logo.png")
    grid = tmp_path / "grid"
    state = artwork.ArtState(tmp_path / "art_state.json")
    local = {"wide": tmp_path / "wide.png", "tall": tmp_path / "tall.png", "logo": tmp_path / "logo.png"}
    assert artwork.ensure_art(grid, 1, "Epic Game", local, state) is None  # no icon image
    with Image.open(grid / "1p.png") as img:
        assert img.size == (600, 900) and img.getpixel((300, 450)) == (0, 0, 255)  # from the tall art
    with Image.open(grid / "1_logo.png") as img:
        assert img.mode == "RGBA"
    assert (grid / "1.png").exists() and (grid / "1_hero.png").exists()

    Image.new("RGB", (2160, 2160), "green").save(tmp_path / "box.png")
    local = {"wide": tmp_path / "box.png", "icon": tmp_path / "logo.png"}
    assert artwork.ensure_art(grid, 2, "Battle.net Game", local, state) == str(grid / "2_icon.png")
    with Image.open(grid / "2p.png") as img:
        assert img.size == (600, 900)
    assert not (grid / "2_logo.png").exists()


def test_failed_download_is_reported_once(tmp_path, capsys, monkeypatch):
    calls = []

    def fail(src):
        calls.append(src)
        raise OSError("offline")
    monkeypatch.setattr(artwork, "_open_image", fail)
    state = artwork.ArtState(tmp_path / "art_state.json")
    artwork.ensure_art(tmp_path / "grid", 3, "Game", {"wide": "https://x/wide.jpg"}, state)
    assert calls == ["https://x/wide.jpg"]
    assert capsys.readouterr().out.count("offline") == 1
