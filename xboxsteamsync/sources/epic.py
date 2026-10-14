"""Games installed by the Epic Games Launcher.

The launcher keeps one JSON manifest per installed item in <AppDataPath>\\Manifests\\*.item,
where AppDataPath comes from the registry (usually C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data).
Its catalog cache (<AppDataPath>\\Catalog\\catcache.bin, base64 JSON) has each game's key
images on Epic's public CDN, which we use for artwork without signing in.
"""

import base64
import json
import winreg
from pathlib import Path

from xboxsteamsync.sources import Detection, Game, folder_kept_reason, shortcut_dirs, shortcut_options

REGISTRY_KEY = r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher"
DEFAULT_DATA_DIR = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data")
LAUNCH_URI = "com.epicgames.launcher://apps/{ns}%3A{item}%3A{app}?action=launch&silent=true"

# keyImages type -> our art kind
IMAGE_TYPES = {"DieselGameBox": "wide", "OfferImageWide": "wide",
               "DieselGameBoxTall": "tall", "OfferImageTall": "tall",
               "DieselGameBoxLogo": "logo"}


def data_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY) as key:
            return Path(winreg.QueryValueEx(key, "AppDataPath")[0])
    except OSError:
        return DEFAULT_DATA_DIR if DEFAULT_DATA_DIR.is_dir() else None


def launch_uri(manifest):
    return LAUNCH_URI.format(ns=manifest["CatalogNamespace"], item=manifest["CatalogItemId"],
                             app=manifest["AppName"])


def is_game(manifest):
    """A full game: in the "games" category, not an add-on of another game, launchable
    and completely installed. Drops DLC ("addons"), extras like REDmod and half-done
    installs."""
    return ("games" in (manifest.get("AppCategories") or [])
            and not manifest.get("MainGameAppName")
            and bool(manifest.get("LaunchExecutable"))
            and not manifest.get("bIsIncompleteInstall"))


def parse_manifest(manifest, images=None):
    """A Game from a parsed .item manifest, or None if it isn't a full game."""
    if not is_game(manifest):
        return None
    install = Path(manifest["InstallLocation"])
    app = manifest["AppName"]
    art = (images or {}).get((manifest.get("CatalogNamespace"), manifest.get("CatalogItemId")), {})
    return Game(source="epic", id=app, name=manifest.get("DisplayName") or install.name, folder=install.name,
                identity=app, dirs=[install], launch=launch_uri(manifest), art=dict(art))


def parse_catalog(data: bytes):
    """{(namespace, item id): {"wide"/"tall"/"logo": url}} from catcache.bin."""
    images = {}
    for item in json.loads(base64.b64decode(data)):
        found = {}
        for image in item.get("keyImages") or []:
            kind = IMAGE_TYPES.get(image.get("type"))
            if kind and image.get("url") and kind not in found:
                found[kind] = image["url"]
        if found:
            images[(item.get("namespace"), item.get("id"))] = found
    return images


def _catalog_images(data: Path):
    try:
        return parse_catalog((data / "Catalog" / "catcache.bin").read_bytes())
    except Exception:  # art only; never fail detection over it
        return {}


def read_games(manifests: Path, images=None):
    """(games, error) for every installed game in a Manifests folder."""
    games, errors = [], []
    for path in sorted(manifests.glob("*.item")):
        try:
            game = parse_manifest(json.loads(path.read_text(encoding="utf-8-sig")), images)
        except (OSError, ValueError, KeyError) as e:
            errors.append(f"{path.name}: {e}")
            continue
        if game and game.dirs[0].is_dir():
            games.append(game)
    return games, "; ".join(errors)


def keep_missing(shortcut, exclude):
    dirs = shortcut_dirs(shortcut)
    return folder_kept_reason(dirs[0] if dirs else None, exclude, *shortcut_options(shortcut, "--id"),
                              shortcut.get("AppName", ""))


def detect(cfg=None) -> Detection:
    data = data_dir()
    if data is None or not data.is_dir():
        return Detection("epic", False, [])
    manifests = data / "Manifests"
    if not manifests.is_dir():  # nothing installed yet
        return Detection("epic", True, [], detail=str(manifests), keep_missing=keep_missing)
    games, error = read_games(manifests, _catalog_images(data))
    return Detection("epic", True, games, detail=str(manifests), keep_missing=keep_missing,
                     error=f"couldn't read {error}" if error else "")


def find(app_name):
    """The installed game with this AppName, or None."""
    return next((g for g in detect().games if g.id == app_name), None)
