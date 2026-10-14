"""Steam library artwork for shortcuts: SteamGridDB first, then the game's own art (the
Xbox package's images, or the key art Epic and Battle.net show for it).

Art generated from the game's own images is recorded in a state file so that a later run
with a SteamGridDB key can upgrade it. Art the user set themselves is never replaced.
"""

import io
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from xboxsteamsync.sources.xbox import strip_ns

API = "https://www.steamgriddb.com/api/v2"

# (endpoint, query, filename suffix) -> saved as <appid><suffix>.<ext> in config/grid
ART_KINDS = [
    ("grids", {"dimensions": "600x900", "types": "static", "mimes": "image/png,image/jpeg"}, "p"),
    ("grids", {"dimensions": "920x430,460x215", "types": "static", "mimes": "image/png,image/jpeg"}, ""),
    ("heroes", {"types": "static", "mimes": "image/png,image/jpeg"}, "_hero"),
    ("logos", {"types": "static", "mimes": "image/png"}, "_logo"),
    ("icons", {"types": "static", "mimes": "image/png,image/vnd.microsoft.icon"}, "_icon"),
]
EXTENSIONS = (".png", ".jpg", ".jpeg", ".ico")


class SteamGridDB:
    def __init__(self, api_key):
        self.api_key = api_key

    def _get(self, path, params=None):
        url = f"{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "XboxSteamSync",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        return body.get("data") if body.get("success") else None

    def find_game_id(self, name):
        # Store names often carry platform suffixes ("MIO - Windows") that hurt search.
        term = re.sub(r"\s*[-–—]\s*(Windows|PC)( Edition)?$", "", name, flags=re.I)
        term = term.replace("™", "").replace("®", "")
        results = self._get(f"search/autocomplete/{urllib.parse.quote(term)}") or []
        return results[0]["id"] if results else None

    def image_url(self, endpoint, game_id, params):
        images = self._get(f"{endpoint}/game/{game_id}", params) or []
        return images[0]["url"] if images else None

    def download(self, url, dest: Path):
        req = urllib.request.Request(url, headers={"User-Agent": "XboxSteamSync"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())


class ArtState:
    """Tracks which grid files were generated from the game's own images (upgradeable)."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def is_local(self, grid_dir, appid, suffix):
        return suffix in self.data.get(f"{grid_dir}|{appid}", [])

    def set_local(self, grid_dir, appid, suffix, local):
        kinds = set(self.data.get(f"{grid_dir}|{appid}", []))
        kinds.add(suffix) if local else kinds.discard(suffix)
        key = f"{grid_dir}|{appid}"
        if kinds:
            self.data[key] = sorted(kinds)
        else:
            self.data.pop(key, None)

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def existing_art(grid_dir: Path, appid: int, suffix: str):
    for ext in EXTENSIONS:
        p = grid_dir / f"{appid}{suffix}{ext}"
        if p.exists():
            return p
    return None


def _remove_art(grid_dir: Path, appid: int, suffix: str):
    while p := existing_art(grid_dir, appid, suffix):
        p.unlink()


# ---------------------------------------------------------------------------
# The game's own images
# ---------------------------------------------------------------------------


def _local_assets(content_dir: Path):
    config_path = content_dir / "MicrosoftGame.config"
    if not config_path.exists():
        return None, None
    # GDK configs may declare an xmlns, which would hide <ShellVisuals> from find().
    cfg = strip_ns(ET.parse(config_path).getroot())
    visuals = cfg.find("ShellVisuals")
    if visuals is None:
        return None, None

    def asset(attr):
        name = visuals.get(attr)
        path = content_dir / name if name else None
        return path if path and path.exists() else None

    return asset("SplashScreenImage"), asset("Square150x150Logo") or asset("StoreLogo")


def _cover(img, size):
    return ImageOps.fit(img, size, Image.LANCZOS)


def _portrait(splash, size=(600, 900)):
    """Wide splash centred over a blurred, zoomed copy of itself."""
    bg = _cover(splash, size).filter(ImageFilter.GaussianBlur(24))
    fg = ImageOps.contain(splash, size, Image.LANCZOS)
    bg.paste(fg, ((size[0] - fg.width) // 2, (size[1] - fg.height) // 2))
    return bg


class LocalImages:
    """The game's own images, opened (or downloaded) once each when first needed.

    `local` is a Game's `art`: {"config": <Xbox Content dir>}, or image paths/URLs under
    "wide" (key art, any shape), "tall" (portrait box art), "logo" and "icon". A bare Path
    means an Xbox Content dir.
    """

    def __init__(self, local):
        if isinstance(local, Path):
            local = {"config": local}
        local = dict(local or {})
        if local.get("config"):
            local["wide"], local["icon"] = _local_assets(Path(local.pop("config")))
        self.sources = local
        self.cache = {}

    def get(self, kind):
        if kind not in self.cache:
            src = self.sources.get(kind)
            self.cache[kind] = None  # a failed download is reported once, not for every kind
            self.cache[kind] = _open_image(src) if src else None
        return self.cache[kind]


def _open_image(src):
    if str(src).startswith(("http://", "https://")):
        req = urllib.request.Request(str(src), headers={"User-Agent": "XboxSteamSync"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            src = io.BytesIO(resp.read())
    img = Image.open(src)
    img.load()
    return img


def _local_art(images: LocalImages, suffix: str):
    if suffix == "_icon":
        icon = images.get("icon")
        return icon.convert("RGBA") if icon else None
    if suffix == "_logo":
        logo = images.get("logo")  # only a transparent logo; the key art already shows the title
        return logo.convert("RGBA") if logo else None
    if suffix == "p" and (tall := images.get("tall")):
        return _cover(tall.convert("RGB"), (600, 900))
    wide = images.get("wide")
    if not wide:
        return None
    wide = wide.convert("RGB")
    if suffix == "p":
        return _portrait(wide)
    if suffix == "":
        return _cover(wide, (920, 430))
    if suffix == "_hero":
        return _cover(wide, (1920, 620))
    return None


# ---------------------------------------------------------------------------


def ensure_art(grid_dir: Path, appid: int, name: str, local, state: ArtState,
               sgdb: SteamGridDB = None, sgdb_id=None):
    """Fill in missing art for `appid`; `local` is the game's own art (see LocalImages).
    Returns the icon path (or None)."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    # Missing, or generated locally on an earlier run and worth upgrading.
    todo = [k for k in ART_KINDS
            if not existing_art(grid_dir, appid, k[2]) or state.is_local(grid_dir, appid, k[2])]

    if sgdb and todo:
        try:
            game_id = sgdb_id or sgdb.find_game_id(name)
            if not game_id:
                print(f"    no SteamGridDB match for '{name}', using the game's own art")
            for endpoint, params, suffix in (todo if game_id else []):
                url = sgdb.image_url(endpoint, game_id, params)
                if not url:
                    continue
                ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png"
                _remove_art(grid_dir, appid, suffix)
                sgdb.download(url, grid_dir / f"{appid}{suffix}{ext}")
                state.set_local(grid_dir, appid, suffix, False)
        except Exception as e:  # art is best-effort; never fail the sync over it
            print(f"    SteamGridDB error for '{name}': {e}; using the game's own art")

    images = LocalImages(local)
    for _, _, suffix in ART_KINDS:
        if existing_art(grid_dir, appid, suffix):
            continue
        try:
            img = _local_art(images, suffix)
        except Exception as e:
            print(f"    local art error for '{name}': {e}")
            img = None
        if img is not None:
            img.save(grid_dir / f"{appid}{suffix}.png")
            state.set_local(grid_dir, appid, suffix, True)

    icon = existing_art(grid_dir, appid, "_icon")
    return str(icon) if icon else None


def delete_art(grid_dir: Path, appid: int, state: ArtState):
    for _, _, suffix in ART_KINDS:
        _remove_art(grid_dir, appid, suffix)
        state.set_local(grid_dir, appid, suffix, False)
