"""Games installed by Battle.net.

What's installed comes from %ProgramData%\\Battle.net\\Agent\\product.db, a protobuf file
Battle.net itself keeps current: per product, its uid (`fenris`, `heroes`), product code
(`fenris`, `hero`) and install folder. Names, launch URIs and art come from aggregate.json
next to it (undocumented; written for the Xbox app's launcher integration and sometimes
stale), with the uninstall entries in the registry as a fallback for names. Both files are
hidden.

Launching: `battlenet://game/<code>` only opens Battle.net. `Battle.net.exe --exec="launch
<code>"` starts the game, but only once Battle.net is up (it's dropped while Battle.net is
starting), and the code is neither the product code nor case-insensitive, so it comes from
EXEC_CODES or a game's `battlenet_code` setting.
"""

import ctypes
import json
import os
import re
import winreg
from ctypes import wintypes
from pathlib import Path

from xboxsteamsync.sources import Detection, Game, folder_kept_reason, shortcut_dirs, shortcut_options

AGENT_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Battle.net" / "Agent"
UNINSTALL_KEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
DEFAULT_EXE = Path(r"C:\Program Files (x86)\Battle.net\Battle.net.exe")
NOT_GAMES = {"agent", "bna", "battle.net"}
MAIN_WINDOW = "Battle.net"  # its sign-in window is "Battle.net Login"

# Product code -> `--exec="launch <code>"` code. Only fenris -> Fen has been tested; the rest
# are the commonly used codes. A game's "battlenet_code" setting overrides this.
EXEC_CODES = {
    "fenris": "Fen",    # Diablo IV
    "hero": "Hero",     # Heroes of the Storm
    "pro": "Pro",       # Overwatch 2
    "wow": "WoW",       # World of Warcraft
    "d3": "D3",         # Diablo III
    "s2": "S2",         # StarCraft II
    "s1": "S1",         # StarCraft Remastered
    "hs_beta": "WTCG",  # Hearthstone
    "w3": "W3",         # Warcraft III: Reforged
    "osi": "OSI",       # Diablo II: Resurrected
    "viper": "VIPR",    # Call of Duty: Black Ops 4
    "odin": "ODIN",     # Call of Duty: Modern Warfare (2019)
    "zeus": "ZEUS",     # Call of Duty: Black Ops Cold War
    "fore": "FORE",     # Call of Duty: Vanguard
    "lazr": "LAZR",     # Call of Duty: MW2 Campaign Remastered
    "auks": "AUKS",     # Call of Duty
    "wlby": "WLBY",     # Crash Bandicoot 4
    "rtro": "RTRO",     # Blizzard Arcade Collection
    "anbs": "ANBS",     # Diablo Immortal
}


# ---------------------------------------------------------------------------
# product.db (protobuf)
# ---------------------------------------------------------------------------


def _varint(buf, i):
    value = shift = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _fields(buf):
    """(field number, value) for each field of a protobuf message: an int for varints,
    bytes for length-delimited fields. Fixed-size fields are skipped."""
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = bytes(buf[i:i + size]), i + size
        elif wire == 1:
            i += 8
            continue
        elif wire == 5:
            i += 4
            continue
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, value


def parse_product_db(data: bytes):
    """[(uid, product code, install path)] for each product in product.db."""
    products = []
    for number, value in _fields(data):
        if number != 1 or not isinstance(value, bytes):
            continue
        uid = code = path = ""
        for n, v in _fields(value):
            if n == 1:
                uid = v.decode("utf-8", "replace")
            elif n == 2:
                code = v.decode("utf-8", "replace")
            elif n == 3:
                path = next((s.decode("utf-8", "replace") for k, s in _fields(v) if k == 1), "")
        products.append((uid, code, path))
    return products


# ---------------------------------------------------------------------------
# Names and art
# ---------------------------------------------------------------------------


def parse_aggregate(data: dict):
    """{product id: entry} from aggregate.json's "installed" list."""
    return {e["product_id"]: e for e in data.get("installed") or [] if e.get("product_id")}


def _aggregate():
    try:
        return parse_aggregate(json.loads((AGENT_DIR / "aggregate.json").read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, AttributeError):
        return {}


def _uninstall_entries():
    """{uid: (DisplayName, InstallLocation)} from Blizzard's uninstall entries."""
    entries = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)
    except OSError:
        return entries
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                    m = re.search(r"--uid=(\S+)", winreg.QueryValueEx(k, "UninstallString")[0])
                    if not m:
                        continue
                    name = _value(k, "DisplayName")
                    entries[m.group(1)] = (name, _value(k, "InstallLocation"))
            except OSError:
                continue
    return entries


def _value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return ""


def make_games(products, aggregate=None, uninstall=None):
    """Games from product.db entries whose install folder exists."""
    aggregate, uninstall = aggregate or {}, uninstall or {}
    games = []
    for uid, code, path in products:
        if not code or not path or code in NOT_GAMES or uid in NOT_GAMES:
            continue
        install = Path(path)
        if not install.is_dir():
            continue
        info = aggregate.get(code, {})
        name = info.get("name") or uninstall.get(uid, ("", ""))[0] or install.name
        art = {k: info[f] for k, f in (("wide", "box_art_uri"), ("icon", "logo_art_uri")) if info.get(f)}
        games.append(Game(source="battlenet", id=code, name=name, folder=install.name, identity=code,
                          dirs=[install], launch=info.get("launch_uri") or f"battlenet://game/{code}", art=art))
    return games


def keep_missing(shortcut, exclude):
    dirs = shortcut_dirs(shortcut)
    return folder_kept_reason(dirs[0] if dirs else None, exclude, *shortcut_options(shortcut, "--id"),
                              shortcut.get("AppName", ""))


def battlenet_exe():
    uninstall = _uninstall_entries().get("battle.net")
    if uninstall and uninstall[1] and (Path(uninstall[1]) / "Battle.net.exe").exists():
        return Path(uninstall[1]) / "Battle.net.exe"
    return DEFAULT_EXE


def detect(cfg=None) -> Detection:
    db = AGENT_DIR / "product.db"
    if not db.exists():
        found = battlenet_exe().exists()
        return Detection("battlenet", found, [], error=f"{db} is missing" if found else "")
    try:
        products = parse_product_db(db.read_bytes())
    except (OSError, ValueError, IndexError) as e:
        return Detection("battlenet", True, [], detail=str(db), error=f"couldn't read {db}: {e}")
    return Detection("battlenet", True, make_games(products, _aggregate(), _uninstall_entries()),
                     detail=str(db), keep_missing=keep_missing)


def find(code):
    """The installed game with this product code, or None."""
    return next((g for g in detect().games if g.id == code), None)


# ---------------------------------------------------------------------------
# Launching (used by xss-launch.exe)
# ---------------------------------------------------------------------------


def running_exe():
    """Path of a running Battle.net.exe, or None."""
    import psutil
    for p in psutil.process_iter(["name", "exe"]):
        if (p.info["name"] or "").lower() == "battle.net.exe":
            return Path(p.info["exe"]) if p.info["exe"] else DEFAULT_EXE
    return None


def main_window_shown():
    """Whether Battle.net's main window is visible, i.e. it accepts --exec now."""
    import psutil
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "battle.net.exe"}
    if not pids:
        return False
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, buf, 64)
            if buf.value == MAIN_WINDOW:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(check, 0)
    return bool(found)
