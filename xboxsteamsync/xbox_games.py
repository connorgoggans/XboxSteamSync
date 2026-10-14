"""Discover installed Xbox app (GDK) games in the Xbox install folders."""

import ctypes
import json
import string
import struct
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from xboxsteamsync.winutil import POWERSHELL, powershell_env


@dataclass
class XboxGame:
    folder: str          # directory name under the Xbox games root
    name: str            # display name from MicrosoftGame.config
    identity: str        # package identity name, e.g. BethesdaSoftworks.ProjectTitan
    aumid: str           # PackageFamilyName!AppId, used to launch via shell:AppsFolder
    content_dir: Path    # <root>/<folder>/Content
    install_dir: str     # Appx InstallLocation (a WindowsApps junction to content_dir)
    exes: list           # executable file names from the ExecutableList


@dataclass
class GameConfig:
    """What MicrosoftGame.config tells us about a full game."""
    identity: str
    name: str | None     # None when missing or an unresolved ms-resource: string
    exes: list
    root: ET.Element


# ---------------------------------------------------------------------------
# Install folders (.GamingRoot)
# ---------------------------------------------------------------------------


def parse_gaming_root(data: bytes):
    """Folder names from a .GamingRoot file, relative to its drive root.

    Format: b"RGBX", a little-endian uint32 count, then that many null-terminated
    UTF-16LE strings.
    """
    if len(data) < 8 or data[:4] != b"RGBX":
        raise ValueError("not a .GamingRoot file")
    count = struct.unpack_from("<I", data, 4)[0]
    text = data[8:].decode("utf-16-le", errors="replace")
    return [p for p in text.split("\x00")[:count] if p]


def _drives():
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]


def gaming_roots():
    """Every Xbox install folder on this PC, found through each drive's .GamingRoot."""
    roots = []
    for drive in _drives():
        marker = Path(drive) / ".GamingRoot"
        try:
            names = parse_gaming_root(marker.read_bytes())
        except (OSError, ValueError):
            continue
        roots += [Path(drive) / n for n in names if (Path(drive) / n).is_dir()]
    return roots


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------


def strip_ns(root):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def read_game_config(config_path: Path):
    """Parse a MicrosoftGame.config. Returns a GameConfig for a full game, or None for
    DLC and other add-on content.

    A package is a full game when it declares an <ExecutableList>. DLC declares no
    executables and points at its parent via <MainPackageDependency>.
    """
    cfg = strip_ns(ET.parse(config_path).getroot())
    if cfg.find("./ExecutableList/Executable") is None:
        return None
    if cfg.find("./DesktopRegistration/MainPackageDependency") is not None:
        return None
    identity = cfg.find("./Identity").get("Name")
    visuals = cfg.find("./ShellVisuals")
    name = visuals.get("DefaultDisplayName") if visuals is not None else None
    if not name or name.startswith("ms-resource:"):
        name = None
    exes = [Path(e.get("Name").replace("\\", "/")).name
            for e in cfg.findall("./ExecutableList/Executable") if e.get("Name")]
    return GameConfig(identity=identity, name=name, exes=exes, root=cfg)


def _installed_package_families():
    """Map lowercased package identity name -> (PackageFamilyName, InstallLocation)."""
    out = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command",
         "Get-AppxPackage | Select-Object Name, PackageFamilyName, InstallLocation | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, env=powershell_env(), creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    data = json.loads(out) if out.strip() else []
    if isinstance(data, dict):
        data = [data]
    return {p["Name"].lower(): (p["PackageFamilyName"], p["InstallLocation"] or "") for p in data}


def _app_id(content_dir: Path, game_config):
    """The Appx <Application Id>, falling back to the first Executable's Id, then 'Game'."""
    manifest = content_dir / "appxmanifest.xml"
    if manifest.exists():
        app = strip_ns(ET.parse(manifest).getroot()).find("./Applications/Application")
        if app is not None and app.get("Id"):
            return app.get("Id")
    exe = game_config.find("./ExecutableList/Executable")
    return (exe.get("Id") if exe is not None else None) or "Game"


def candidate_folders(root: Path):
    """(folder, MicrosoftGame.config path) for each folder under `root` that has one.
    Folders without a config, like GameSave, are skipped."""
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        config_path = folder / "Content" / "MicrosoftGame.config"
        if config_path.exists():
            yield folder, config_path


def is_excluded(exclude, folder, identity, name):
    return bool({folder.lower(), identity.lower(), name.lower()} & {e.lower() for e in exclude})


def kept_reason(content_dir: Path, exclude=()):
    """Why a game that find_games didn't return should keep its shortcut, or None if it's
    really gone (its config was deleted, or it's in `exclude`).

    Detection can miss a game that's still installed: its package may not be registered
    with Windows (e.g. while the Xbox app repairs it), its config may be unreadable, or
    its drive may be disconnected.
    """
    root = content_dir.parent.parent
    if not root.is_dir():
        return f"{root} isn't available"
    config_path = content_dir / "MicrosoftGame.config"
    if not config_path.exists():
        return None
    try:
        info = read_game_config(config_path)
    except Exception:
        return "its MicrosoftGame.config couldn't be read"
    if info is None:
        return None
    folder = content_dir.parent.name
    if is_excluded(exclude, folder, info.identity, info.name or folder):
        return None
    return "it's still installed, but wasn't detected"


def find_games(roots, exclude=(), families=None):
    """Return full games (not DLC, saves, or other content) installed under `roots`."""
    families = _installed_package_families() if families is None else families
    games, seen = [], set()
    for root in roots:
        for folder, config_path in candidate_folders(root):
            try:
                info = read_game_config(config_path)
            except Exception as e:
                print(f"  ! {folder.name}: couldn't read {config_path.name} ({e}), skipping")
                continue
            if info is None:
                continue
            name = info.name or folder.name
            if is_excluded(exclude, folder.name, info.identity, name):
                continue
            family, install_dir = families.get(info.identity.lower(), (None, None))
            if not family:
                print(f"  ! {name}: package {info.identity} is not registered with Windows, skipping")
                continue
            content = config_path.parent
            aumid = f"{family}!{_app_id(content, info.root)}"
            if aumid in seen:
                continue
            seen.add(aumid)
            games.append(XboxGame(folder=folder.name, name=name, identity=info.identity, aumid=aumid,
                                  content_dir=content, install_dir=install_dir, exes=info.exes))
    return games
