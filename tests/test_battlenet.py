import json

from conftest import FIXTURES
from xboxsteamsync.sources import battlenet

BNET = FIXTURES / "battlenet"


def test_parse_product_db():
    products = battlenet.parse_product_db((BNET / "product.db").read_bytes())
    assert products == [
        ("agent", "agent", "C:/ProgramData/Battle.net/Agent"),
        ("battle.net", "bna", "C:/Program Files (x86)/Battle.net"),
        ("fenris", "fenris", "C:/Program Files (x86)/Diablo IV"),
        ("heroes", "hero", "C:/Program Files (x86)/Heroes of the Storm"),
    ]


def test_parse_aggregate():
    aggregate = battlenet.parse_aggregate(json.loads((BNET / "aggregate.json").read_text(encoding="utf-8")))
    assert set(aggregate) == {"fenris", "hero"}
    assert aggregate["fenris"]["launch_uri"] == "battlenet://game/fenris"


def _products(tmp_path):
    for folder in ("Diablo IV", "Heroes of the Storm", "Battle.net"):
        (tmp_path / folder).mkdir()
    return [("agent", "agent", str(tmp_path / "Agent")),
            ("battle.net", "bna", str(tmp_path / "Battle.net")),
            ("fenris", "fenris", str(tmp_path / "Diablo IV")),
            ("heroes", "hero", str(tmp_path / "Heroes of the Storm")),
            ("gone", "gone", str(tmp_path / "Uninstalled"))]


def test_make_games_with_aggregate(tmp_path):
    aggregate = battlenet.parse_aggregate(json.loads((BNET / "aggregate.json").read_text(encoding="utf-8")))
    games = battlenet.make_games(_products(tmp_path), aggregate)
    assert [(g.key, g.name, g.folder, g.launch) for g in games] == [
        ("battlenet:fenris", "Diablo IV", "Diablo IV", "battlenet://game/fenris"),
        ("battlenet:hero", "Heroes of the Storm", "Heroes of the Storm", "battlenet://game/hero"),
    ]
    assert games[0].art == {"wide": "https://bnetxboxassets.akamaized.net/fenris/box-enUS.webp",
                            "icon": "https://bnetxboxassets.akamaized.net/fenris/logo-enUS.webp"}
    assert games[1].setting_keys == ("Heroes of the Storm", "hero", "Heroes of the Storm")


def test_make_games_without_aggregate_uses_uninstall_names(tmp_path):
    games = battlenet.make_games(_products(tmp_path), {}, {"heroes": ("Heroes of the Storm (registry)", "")})
    assert [(g.name, g.launch, g.art) for g in games] == [
        ("Diablo IV", "battlenet://game/fenris", {}),  # the folder name
        ("Heroes of the Storm (registry)", "battlenet://game/hero", {}),
    ]


def test_exec_codes():
    assert battlenet.EXEC_CODES["fenris"] == "Fen"  # verified; case matters
    assert battlenet.EXEC_CODES["hero"] == "Hero"
