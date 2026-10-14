"""Read and write the Steam files we touch: shortcuts.vdf, localconfig.vdf, collections.

Functions that edit a file take `write`; with write=False they only report whether
anything would change, so callers can avoid restarting Steam for nothing.
"""

import base64
import json
import os
import secrets
import struct
import subprocess
import time
import winreg
import zlib
from pathlib import Path

import psutil
import vdf


class SteamNotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# Install / users / process control
# ---------------------------------------------------------------------------


def steam_dir() -> Path:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            path = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        raise SteamNotFound("Steam is not installed (no SteamPath in the registry)") from None
    if not (path / "steam.exe").exists():
        raise SteamNotFound(f"Steam is registered at {path}, but steam.exe isn't there")
    return path


def user_config_dirs(steam: Path):
    """config/ dirs for every account that has logged into this Steam install.

    Empty when nobody has signed in yet (no loginusers.vdf or userdata).
    """
    login_users = steam / "config" / "loginusers.vdf"
    if not login_users.exists():
        return []
    with open(login_users, encoding="utf-8") as f:
        users = vdf.load(f).get("users", {})
    dirs = []
    for steamid64 in users:
        account_id = int(steamid64) & 0xFFFFFFFF
        cfg = steam / "userdata" / str(account_id) / "config"
        if cfg.is_dir():
            dirs.append(cfg)
    return dirs


NO_USERS_MESSAGE = ("Steam is installed, but nobody has signed in to it on this PC yet.\n"
                    "Start Steam, sign in, then run this again.")


def _steam_procs():
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "steam.exe"]


def is_running():
    return bool(_steam_procs())


def shutdown(steam: Path, timeout=60):
    subprocess.Popen([str(steam / "steam.exe"), "-shutdown"])
    deadline = time.monotonic() + timeout
    while _steam_procs():
        if time.monotonic() > deadline:
            raise TimeoutError("Steam did not exit; close it manually and re-run")
        time.sleep(1)
    time.sleep(2)  # let Steam's helpers release file handles


def start(steam: Path):
    subprocess.Popen([str(steam / "steam.exe")], creationflags=subprocess.DETACHED_PROCESS)


# ---------------------------------------------------------------------------
# shortcuts.vdf (binary KeyValues)
# ---------------------------------------------------------------------------

_MAP, _STR, _INT, _END = 0x00, 0x01, 0x02, 0x08


# surrogateescape keeps bytes that aren't valid UTF-8 intact through a round trip.
def _read_cstr(buf, i):
    end = buf.index(b"\x00", i)
    return buf[i:end].decode("utf-8", errors="surrogateescape"), end + 1


def _cstr(s):
    return str(s).encode("utf-8", errors="surrogateescape") + b"\x00"


def _read_map(buf, i):
    out = {}
    while True:
        t = buf[i]
        i += 1
        if t == _END:
            return out, i
        key, i = _read_cstr(buf, i)
        if t == _MAP:
            out[key], i = _read_map(buf, i)
        elif t == _STR:
            out[key], i = _read_cstr(buf, i)
        elif t == _INT:
            out[key] = struct.unpack_from("<i", buf, i)[0]
            i += 4
        else:
            raise ValueError(f"unsupported binary VDF type 0x{t:02x} at offset {i - 1}")


def _write_map(d):
    out = bytearray()
    for key, val in d.items():
        k = _cstr(key)
        if isinstance(val, dict):
            out += bytes([_MAP]) + k + _write_map(val)
        elif isinstance(val, int):
            out += bytes([_INT]) + k + struct.pack("<i", val)
        else:
            out += bytes([_STR]) + k + _cstr(val)
    return out + bytes([_END])


def parse_shortcuts(data: bytes):
    parsed, _ = _read_map(data, 0)
    return list(parsed.get("shortcuts", {}).values())


def encode_shortcuts(shortcuts) -> bytes:
    return bytes(_write_map({"shortcuts": {str(i): s for i, s in enumerate(shortcuts)}}))


def load_shortcuts(cfg: Path):
    path = cfg / "shortcuts.vdf"
    return parse_shortcuts(path.read_bytes()) if path.exists() else []


def save_shortcuts(cfg: Path, shortcuts, write=True) -> bool:
    """Write shortcuts.vdf if its contents would change. Returns whether they did."""
    path = cfg / "shortcuts.vdf"
    new = encode_shortcuts(shortcuts)
    old = path.read_bytes() if path.exists() else encode_shortcuts([])
    if new == old:
        return False
    if write:
        tmp = path.with_suffix(".vdf.tmp")
        tmp.write_bytes(new)
        os.replace(tmp, path)
    return True


def shortcut_appid(exe: str, name: str) -> int:
    """Unsigned 32-bit app id Steam uses for a non-Steam shortcut (grid art, collections)."""
    return zlib.crc32((exe + name).encode("utf-8")) | 0x80000000


def to_signed(appid: int) -> int:
    return struct.unpack("<i", struct.pack("<I", appid))[0]


def to_unsigned(appid: int) -> int:
    return appid & 0xFFFFFFFF


def new_shortcut(appid, name, exe, start_dir, launch_options, icon=""):
    return {
        "appid": to_signed(appid),
        "AppName": name,
        "Exe": exe,
        "StartDir": start_dir,
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "sortas": "",
        "tags": {},
    }


# ---------------------------------------------------------------------------
# localconfig.vdf: per-app Steam Input setting
# ---------------------------------------------------------------------------

STEAM_INPUT_ENABLED = "2"


def set_steam_input(cfg: Path, enable_appids, clear_appids, write=True) -> bool:
    """Force Steam Input on for `enable_appids` and forget `clear_appids`. Returns
    whether localconfig.vdf changed (or would)."""
    path = cfg / "localconfig.vdf"
    if not path.exists():
        return False
    with open(path, encoding="utf-8") as f:
        data = vdf.load(f, mapper=vdf.VDFDict)
    store = data["UserLocalConfigStore"]
    if "apps" not in store:
        store["apps"] = vdf.VDFDict()
    apps = store["apps"]
    changed = False
    for appid in clear_appids:
        key = str(appid)
        if key in apps:
            del apps[key]
            changed = True
    for appid in enable_appids:
        key = str(appid)
        if key not in apps:
            apps[key] = vdf.VDFDict()
        entry = apps[key]
        if entry.get("UseSteamControllerConfig") == STEAM_INPUT_ENABLED:
            continue
        if "UseSteamControllerConfig" in entry:
            del entry["UseSteamControllerConfig"]
        entry["UseSteamControllerConfig"] = STEAM_INPUT_ENABLED
        changed = True
    if changed and write:
        _atomic_write(path, vdf.dumps(data, pretty=True))
    return changed


# ---------------------------------------------------------------------------
# Collections (cloud-storage-namespace-1.json)
# ---------------------------------------------------------------------------


def _collection_paths(cfg: Path):
    cloud = cfg / "cloudstorage"
    return (cloud / "cloud-storage-namespace-1.json",
            cloud / "cloud-storage-namespaces.json",
            cloud / "cloud-storage-namespace-1.modified.json")


def has_collections_file(cfg: Path) -> bool:
    return _collection_paths(cfg)[0].exists()


def update_collection(entries, namespaces, modified, name, member_appids, removed_appids,
                      delete_if_empty=False):
    """Apply a collection change to the parsed cloud-storage JSON, in place.

    Adds `member_appids` and drops `removed_appids`, leaving any other games the user
    put in the collection by hand. Bumps the namespace version and flags the key as
    modified so Steam uploads our change instead of overwriting it from the cloud.
    Returns (changed, namespaces).
    """
    entry = collection = None
    for key, e in entries:
        if not key.startswith("user-collections.") or e.get("is_deleted") or "value" not in e:
            continue
        value = json.loads(e["value"])
        if value.get("name") == name:
            entry, collection = e, value
            break

    if collection is None:
        if not member_appids:
            return False, namespaces
        cid = "uc-" + base64.b64encode(secrets.token_bytes(9)).decode()
        collection = {"id": cid, "name": name, "added": [], "removed": []}
        entry = {"key": f"user-collections.{cid}"}
        entries.append([entry["key"], entry])

    removed = set(removed_appids)
    members = set(member_appids)
    added = [a for a in collection.get("added", []) if a not in removed]
    added += [a for a in member_appids if a not in added]
    excluded = [a for a in collection.get("removed", []) if a not in members]
    delete = delete_if_empty and not added
    if (not delete and added == collection.get("added") and excluded == collection.get("removed", [])
            and "version" in entry):
        return False, namespaces

    ns_version = next((int(v) for n, v in namespaces if n == 1), 0)
    max_entry_version = max((int(e.get("version", 0)) for _, e in entries), default=0)
    version = max(ns_version, max_entry_version) + 1
    entry["timestamp"] = int(time.time())
    entry["version"] = str(version)
    if delete:
        entry.pop("value", None)
        entry["is_deleted"] = True
    else:
        collection["added"] = added
        collection["removed"] = excluded
        entry["value"] = json.dumps(collection, separators=(",", ":"))

    namespaces = [[n, v] for n, v in namespaces if n != 1] + [[1, str(version)]]
    if entry["key"] not in modified:
        modified.append(entry["key"])
    return True, namespaces


def sync_collection(cfg: Path, name, member_appids, removed_appids, write=True,
                    delete_if_empty=False) -> bool:
    """Create/update the user collection `name` on disk (see update_collection).
    Returns whether it changed (or would)."""
    ns_path, namespaces_path, modified_path = _collection_paths(cfg)
    if not ns_path.exists():
        return False

    def load(p):
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    entries, modified = load(ns_path), load(modified_path)
    changed, namespaces = update_collection(entries, load(namespaces_path), modified, name,
                                            member_appids, removed_appids, delete_if_empty)
    if changed and write:
        _atomic_write(ns_path, json.dumps(entries, separators=(",", ":")))
        _atomic_write(namespaces_path, json.dumps(namespaces, separators=(",", ":")))
        _atomic_write(modified_path, json.dumps(modified, separators=(",", ":")))
    return changed


def _atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
