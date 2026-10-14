"""Read and write the Steam files we touch: shortcuts.vdf, localconfig.vdf, collections."""

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

# ---------------------------------------------------------------------------
# Install / users / process control
# ---------------------------------------------------------------------------


def steam_dir() -> Path:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
        return Path(winreg.QueryValueEx(key, "SteamPath")[0])


def user_config_dirs(steam: Path):
    """config/ dirs for every account that has logged into this Steam install."""
    users = vdf.load(open(steam / "config" / "loginusers.vdf", encoding="utf-8"))["users"]
    dirs = []
    for steamid64 in users:
        account_id = int(steamid64) & 0xFFFFFFFF
        cfg = steam / "userdata" / str(account_id) / "config"
        if cfg.is_dir():
            dirs.append(cfg)
    return dirs


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


def _read_cstr(buf, i):
    end = buf.index(b"\x00", i)
    return buf[i:end].decode("utf-8", errors="replace"), end + 1


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
        k = key.encode("utf-8") + b"\x00"
        if isinstance(val, dict):
            out += bytes([_MAP]) + k + _write_map(val)
        elif isinstance(val, int):
            out += bytes([_INT]) + k + struct.pack("<i", val)
        else:
            out += bytes([_STR]) + k + str(val).encode("utf-8") + b"\x00"
    return out + bytes([_END])


def load_shortcuts(cfg: Path):
    path = cfg / "shortcuts.vdf"
    if not path.exists():
        return []
    data, _ = _read_map(path.read_bytes(), 0)
    return list(data.get("shortcuts", {}).values())


def save_shortcuts(cfg: Path, shortcuts):
    body = {"shortcuts": {str(i): s for i, s in enumerate(shortcuts)}}
    (cfg / "shortcuts.vdf").write_bytes(bytes(_write_map(body)))


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


def set_steam_input(cfg: Path, enable_appids, clear_appids):
    path = cfg / "localconfig.vdf"
    if not path.exists():
        return
    data = vdf.load(open(path, encoding="utf-8"), mapper=vdf.VDFDict)
    store = data["UserLocalConfigStore"]
    if "apps" not in store:
        store["apps"] = vdf.VDFDict()
    apps = store["apps"]
    for appid in clear_appids:
        key = str(appid)
        if key in apps:
            del apps[key]
    for appid in enable_appids:
        key = str(appid)
        if key not in apps:
            apps[key] = vdf.VDFDict()
        entry = apps[key]
        if "UseSteamControllerConfig" in entry:
            del entry["UseSteamControllerConfig"]
        entry["UseSteamControllerConfig"] = STEAM_INPUT_ENABLED
    _atomic_write(path, vdf.dumps(data, pretty=True))


# ---------------------------------------------------------------------------
# Collections (cloud-storage-namespace-1.json)
# ---------------------------------------------------------------------------


def sync_collection(cfg: Path, name, member_appids, removed_appids):
    """Create/update a user collection named `name`.

    Adds `member_appids` and drops `removed_appids`, leaving any other games the user
    put in the collection by hand. Bumps the namespace version and flags the key as
    modified so Steam uploads our change instead of overwriting it from the cloud.
    """
    cloud = cfg / "cloudstorage"
    ns_path = cloud / "cloud-storage-namespace-1.json"
    namespaces_path = cloud / "cloud-storage-namespaces.json"
    modified_path = cloud / "cloud-storage-namespace-1.modified.json"
    if not ns_path.exists():
        print(f"  ! {ns_path} not found; open Steam's library once, then re-run")
        return

    entries = json.loads(ns_path.read_text(encoding="utf-8"))
    namespaces = json.loads(namespaces_path.read_text(encoding="utf-8")) if namespaces_path.exists() else []
    modified = json.loads(modified_path.read_text(encoding="utf-8")) if modified_path.exists() else []

    entry = collection = None
    for key, e in entries:
        if not key.startswith("user-collections.") or e.get("is_deleted") or "value" not in e:
            continue
        value = json.loads(e["value"])
        if value.get("name") == name:
            entry, collection = e, value
            break

    if collection is None:
        cid = "uc-" + base64.b64encode(secrets.token_bytes(9)).decode()
        collection = {"id": cid, "name": name, "added": [], "removed": []}
        entry = {"key": f"user-collections.{cid}"}
        entries.append([entry["key"], entry])

    removed = set(removed_appids)
    added = [a for a in collection.get("added", []) if a not in removed]
    added += [a for a in member_appids if a not in added]
    if added == collection.get("added") and "version" in entry:
        return
    collection["added"] = added
    collection["removed"] = [a for a in collection.get("removed", []) if a not in set(member_appids)]

    ns_version = next((int(v) for n, v in namespaces if n == 1), 0)
    max_entry_version = max((int(e.get("version", 0)) for _, e in entries), default=0)
    version = max(ns_version, max_entry_version) + 1
    entry["timestamp"] = int(time.time())
    entry["value"] = json.dumps(collection, separators=(",", ":"))
    entry["version"] = str(version)

    namespaces = [[n, v] for n, v in namespaces if n != 1] + [[1, str(version)]]
    if entry["key"] not in modified:
        modified.append(entry["key"])

    _atomic_write(ns_path, json.dumps(entries, separators=(",", ":")))
    _atomic_write(namespaces_path, json.dumps(namespaces, separators=(",", ":")))
    _atomic_write(modified_path, json.dumps(modified, separators=(",", ":")))


def _atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
