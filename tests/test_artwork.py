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
