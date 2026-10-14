"""Steam library artwork for shortcuts: SteamGridDB first, then the Xbox package's own art.

Art generated from local Xbox assets is recorded in a state file so that a later run
with a SteamGridDB key can upgrade it. Art the user set themselves is never replaced.
"""

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

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
    """Tracks which grid files were generated from local Xbox assets (upgradeable)."""

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
# Local Xbox assets
# ---------------------------------------------------------------------------


def _local_assets(content_dir: Path):
    cfg = ET.parse(content_dir / "MicrosoftGame.config").getroot()
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


def _local_art(content_dir: Path, suffix: str):
    splash_path, logo_path = _local_assets(content_dir)
    if suffix == "_icon" and logo_path:
        return Image.open(logo_path).convert("RGBA")
    if not splash_path:
        return None
    splash = Image.open(splash_path).convert("RGB")
    if suffix == "p":
        return _portrait(splash)
    if suffix == "":
        return _cover(splash, (920, 430))
    if suffix == "_hero":
        return _cover(splash, (1920, 620))
    return None  # no transparent logo available locally; the splash already shows the title


# ---------------------------------------------------------------------------


def ensure_art(grid_dir: Path, appid: int, name: str, content_dir: Path, state: ArtState,
               sgdb: SteamGridDB = None, sgdb_id=None):
    """Fill in missing art for `appid`. Returns the icon path (or None)."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    # Missing, or generated locally on an earlier run and worth upgrading.
    todo = [k for k in ART_KINDS
            if not existing_art(grid_dir, appid, k[2]) or state.is_local(grid_dir, appid, k[2])]

    if sgdb and todo:
        try:
            game_id = sgdb_id or sgdb.find_game_id(name)
            if not game_id:
                print(f"    no SteamGridDB match for '{name}', using local Xbox art")
            for endpoint, params, suffix in (todo if game_id else []):
                url = sgdb.image_url(endpoint, game_id, params)
                if not url:
                    continue
                ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png"
                _remove_art(grid_dir, appid, suffix)
                sgdb.download(url, grid_dir / f"{appid}{suffix}{ext}")
                state.set_local(grid_dir, appid, suffix, False)
        except Exception as e:  # art is best-effort; never fail the sync over it
            print(f"    SteamGridDB error for '{name}': {e}; using local Xbox art")

    for _, _, suffix in ART_KINDS:
        if existing_art(grid_dir, appid, suffix):
            continue
        try:
            img = _local_art(content_dir, suffix)
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
