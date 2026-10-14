import base64
import json
import shutil

import pytest

from conftest import FIXTURES
from xboxsteamsync.sources import epic

EPIC = FIXTURES / "epic"


def _manifest(name):
    return json.loads((EPIC / name).read_text(encoding="utf-8"))


def test_full_games():
    cyberpunk = epic.parse_manifest(_manifest("cyberpunk.item"))
    assert (cyberpunk.source, cyberpunk.id, cyberpunk.name, cyberpunk.folder) == (
        "epic", "Ginger", "Cyberpunk 2077", "Cyberpunk2077")
    assert cyberpunk.setting_keys == ("Cyberpunk2077", "Ginger", "Cyberpunk 2077")
    assert [str(d) for d in cyberpunk.dirs] == [r"C:\Program Files\Epic Games\Cyberpunk2077"]
    assert cyberpunk.key == "epic:Ginger"
    assert epic.parse_manifest(_manifest("dredge.item")).name == "DREDGE"


@pytest.mark.parametrize("name", ["redmod.item", "phantom_liberty.item"])
def test_extras_and_addons_are_not_games(name):
    assert epic.parse_manifest(_manifest(name)) is None


def test_incomplete_install_is_not_a_game():
    assert epic.parse_manifest({**_manifest("dredge.item"), "bIsIncompleteInstall": True}) is None


def test_launch_uri():
    assert epic.parse_manifest(_manifest("cyberpunk.item")).launch == (
        "com.epicgames.launcher://apps/77f2b98e2cef40c8a7437518bf420e47%3A5beededaad9743df90e8f07d92df153f"
        "%3AGinger?action=launch&silent=true")


def test_catalog_images():
    catalog = [{"namespace": "8b454b47f5544fc6829cf0fed42ebae0", "id": "eac6533129434f98a6b04a81cbcaf357",
                "keyImages": [{"type": "Thumbnail", "url": "https://x/thumb.jpg"},
                              {"type": "DieselGameBox", "url": "https://x/wide.jpg"},
                              {"type": "DieselGameBoxTall", "url": "https://x/tall.jpg"}]},
               {"namespace": "other", "id": "noimages", "keyImages": []}]
    images = epic.parse_catalog(base64.b64encode(json.dumps(catalog).encode()))
    assert images == {("8b454b47f5544fc6829cf0fed42ebae0", "eac6533129434f98a6b04a81cbcaf357"):
                      {"wide": "https://x/wide.jpg", "tall": "https://x/tall.jpg"}}
    assert epic.parse_manifest(_manifest("dredge.item"), images).art == {
        "wide": "https://x/wide.jpg", "tall": "https://x/tall.jpg"}


def test_read_games(tmp_path):
    manifests = tmp_path / "Manifests"
    manifests.mkdir()
    for name in ("cyberpunk.item", "dredge.item", "redmod.item", "phantom_liberty.item"):
        manifest = _manifest(name)
        install = tmp_path / "Epic Games" / manifest["InstallLocation"].rsplit("\\", 1)[1]
        install.mkdir(parents=True, exist_ok=True)
        manifest["InstallLocation"] = str(install)
        (manifests / name).write_text(json.dumps(manifest), encoding="utf-8")
    shutil.rmtree(tmp_path / "Epic Games" / "DREDGEmKMzx")  # not on disk (e.g. its drive is offline)
    games, error = epic.read_games(manifests)
    assert [g.name for g in games] == ["Cyberpunk 2077"] and not error
    (manifests / "broken.item").write_text("{", encoding="utf-8")
    assert "broken.item" in epic.read_games(manifests)[1]
