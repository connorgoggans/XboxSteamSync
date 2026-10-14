"""Discover installed Xbox app (GDK) games under the Xbox install folder."""

import json
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class XboxGame:
    folder: str          # directory name under the Xbox games root
    name: str            # display name from MicrosoftGame.config
    identity: str        # package identity name, e.g. BethesdaSoftworks.ProjectTitan
    aumid: str           # PackageFamilyName!AppId, used to launch via shell:AppsFolder
    content_dir: Path    # <root>/<folder>/Content
    install_dir: str     # Appx InstallLocation (a WindowsApps junction to content_dir)
    exes: list           # executable file names from the ExecutableList


def _strip_ns(root):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _installed_package_families():
    """Map lowercased package identity name -> (PackageFamilyName, InstallLocation)."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-AppxPackage | Select-Object Name, PackageFamilyName, InstallLocation | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out) if out.strip() else []
    if isinstance(data, dict):
        data = [data]
    return {p["Name"].lower(): (p["PackageFamilyName"], p["InstallLocation"] or "") for p in data}


def _app_id(content_dir: Path, game_config):
    """The Appx <Application Id>, falling back to the first Executable's Id, then 'Game'."""
    manifest = content_dir / "appxmanifest.xml"
    if manifest.exists():
        app = _strip_ns(ET.parse(manifest).getroot()).find("./Applications/Application")
        if app is not None and app.get("Id"):
            return app.get("Id")
    exe = game_config.find("./ExecutableList/Executable")
    return (exe.get("Id") if exe is not None else None) or "Game"


def find_games(root: Path, exclude=()):
    """Return full games (not DLC, saves, or other content) installed under `root`.

    A folder is a full game when its Content/MicrosoftGame.config declares an
    <ExecutableList>. DLC packages declare no executables and point at their parent
    via <MainPackageDependency>; folders like GameSave have no config at all.
    """
    exclude = {e.lower() for e in exclude}
    families = _installed_package_families()
    games = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        content = folder / "Content"
        config_path = content / "MicrosoftGame.config"
        if not config_path.exists():
            continue
        cfg = _strip_ns(ET.parse(config_path).getroot())
        if cfg.find("./ExecutableList/Executable") is None:
            continue
        if cfg.find("./DesktopRegistration/MainPackageDependency") is not None:
            continue
        identity = cfg.find("./Identity").get("Name")
        visuals = cfg.find("./ShellVisuals")
        name = visuals.get("DefaultDisplayName") if visuals is not None else None
        if not name or name.startswith("ms-resource:"):
            name = folder.name
        if {folder.name.lower(), identity.lower(), name.lower()} & exclude:
            continue
        family, install_dir = families.get(identity.lower(), (None, None))
        if not family:
            print(f"  ! {name}: package {identity} is not registered with Windows, skipping")
            continue
        games.append(XboxGame(
            folder=folder.name,
            name=name,
            identity=identity,
            aumid=f"{family}!{_app_id(content, cfg)}",
            content_dir=content,
            install_dir=install_dir,
            exes=[Path(e.get("Name").replace("\\", "/")).name
                  for e in cfg.findall("./ExecutableList/Executable") if e.get("Name")],
        ))
    return games
