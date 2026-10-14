"""Keep XboxSteamSync up to date from its GitHub releases.

`xss sync`, `xss doctor` and `xss setup` check at most once a day; xss-launch.exe never
does, so game launches stay fast and work offline. A new release's zip is checked
against the release's .sha256 file, then the install.ps1 inside it is started detached.
It waits for this process to exit before swapping the app folder, so the command carries
on with the current version and the next run uses the new one.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

import psutil

from xboxsteamsync import __version__, paths
from xboxsteamsync.config import load_config
from xboxsteamsync.winutil import POWERSHELL, powershell_env

REPO = "connorgoggans/XboxSteamSync"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/download/{{tag}}/{{name}}"
ASSET = "XboxSteamSync-windows-x64.zip"

CHECK_INTERVAL = 24 * 3600  # seconds between automatic checks, and between attempts at one version
CHECK_TIMEOUT = 5
DOWNLOAD_TIMEOUT = 120

# The last release that changed what our Steam shortcuts contain (launch options, target).
# Bump it with such a change: shortcuts last synced by an older version then need `xss sync`.
SHORTCUTS_CHANGED_IN = "0.2.0"
FIRST_RELEASE = "0.2.0"  # assumed for installs that synced before this was recorded

LOG_PATH = paths.LOG_DIR / "update.log"

started = None  # tag whose installer this process started; it runs once we exit


class UpdateError(Exception):
    pass


class ChecksumMismatch(UpdateError):
    pass


def _log():
    log = logging.getLogger("xboxsteamsync.update")
    if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:-?([0-9A-Za-z][0-9A-Za-z.-]*))?")


def parse_version(text):
    """A sortable key for a version or tag ("v0.4.0", "0.4.0-beta.2"), or None.

    Follows semver: a pre-release sorts before its release, and its dot-separated parts
    compare numerically when they're numbers.
    """
    m = _VERSION_RE.fullmatch((text or "").strip())
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    core = (int(major), int(minor), int(patch or 0))
    if not pre:
        return (*core, (1,))
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (*core, (0, *parts))


def is_newer(tag, current=None) -> bool:
    new, old = parse_version(tag), parse_version(current or __version__)
    return bool(new and old and new > old)


# ---------------------------------------------------------------------------
# State (update_state.json)
# ---------------------------------------------------------------------------


def load_state():
    try:
        state = json.loads(paths.UPDATE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    paths.UPDATE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.UPDATE_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _within(since, now, interval=CHECK_INTERVAL) -> bool:
    try:
        return 0 <= now - float(since) < interval
    except (TypeError, ValueError):
        return False


def check_due(state, now) -> bool:
    """Whether the once-a-day check is due (also when the clock went backwards)."""
    return not _within(state.get("last_check"), now)


def record_sync():
    """Remember that this version synced the shortcuts (see resync_needed)."""
    state = load_state()
    if state.get("last_synced_version") != __version__:
        state["last_synced_version"] = __version__
        save_state(state)


def resync_needed():
    """The version that last synced, if a later release changed the shortcuts; else None."""
    last = load_state().get("last_synced_version") or FIRST_RELEASE
    old, changed = parse_version(last), parse_version(SHORTCUTS_CHANGED_IN)
    return last if old and changed and old < changed else None


# ---------------------------------------------------------------------------
# Download, verify, install
# ---------------------------------------------------------------------------


def _get(url, timeout) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"XboxSteamSync/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_latest_tag():
    """Tag of the latest GitHub release (drafts and pre-releases excluded)."""
    data = json.loads(_get(LATEST_URL, CHECK_TIMEOUT))
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not parse_version(tag):
        raise UpdateError(f"unexpected release tag {tag!r}")
    return tag


def verify(data: bytes, sha256_file: str):
    """Check `data` against a .sha256 file ("<hex>  <name>", or just the hex)."""
    fields = sha256_file.lstrip("﻿").split()
    expected = fields[0].lower() if fields else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ChecksumMismatch("the release's .sha256 file doesn't contain a SHA-256")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ChecksumMismatch(f"the downloaded zip's SHA-256 is {actual}, but the release says {expected}")


def download(tag, dest: Path) -> Path:
    """Download and verify the release zip into `dest` (emptied first)."""
    sha256_file = _get(DOWNLOAD_URL.format(tag=tag, name=ASSET + ".sha256"), CHECK_TIMEOUT)
    data = _get(DOWNLOAD_URL.format(tag=tag, name=ASSET), DOWNLOAD_TIMEOUT)
    verify(data, sha256_file.decode("utf-8", errors="replace"))
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    zip_path = dest / ASSET
    zip_path.write_bytes(data)
    return zip_path


def extract_installer(zip_path: Path, dest: Path) -> Path:
    """Copy the install.ps1 that ships inside the release zip to `dest`."""
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.replace("\\", "/").rsplit("/", 1)[-1].lower() == "install.ps1"]
        if not names:
            raise UpdateError("the release zip has no install.ps1")
        script = dest / "install.ps1"
        script.write_bytes(z.read(min(names, key=len)))
    return script


def app_in_use():
    """Names of other processes running from the app folder (e.g. xss-launch.exe while a
    game runs); install.ps1 can't swap the folder while there are any."""
    prefix = os.path.normcase(str(paths.APP_DIR.resolve())).rstrip("\\") + "\\"
    names = set()
    for proc in psutil.process_iter(["pid", "exe", "name"]):
        exe = proc.info.get("exe")
        if proc.info["pid"] != os.getpid() and exe and os.path.normcase(exe).startswith(prefix):
            names.add(proc.info["name"])
    return sorted(names)


def _start_installer(script: Path, zip_path: Path):
    args = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Zip", str(zip_path), "-NoSetup", "-NoPath", "-WaitForPid", str(os.getpid())]
    # A hidden console of its own, so it outlives ours. Not DETACHED_PROCESS: Windows
    # PowerShell started without any console exits at once without running the script.
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(LOG_PATH, "a", encoding="utf-8") as out:
        kwargs = dict(env=powershell_env(), cwd=paths.DATA_DIR, stdin=subprocess.DEVNULL, stdout=out,
                      stderr=subprocess.STDOUT)
        try:
            # Outlive us even if we run inside a job that kills its processes when we exit.
            subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            subprocess.Popen(args, creationflags=flags, **kwargs)


def start_update(tag):
    """Download and verify `tag`, then start its installer, which waits for us to exit."""
    log = _log()
    log.info("downloading %s", tag)
    zip_path = download(tag, paths.UPDATES_DIR)
    script = extract_installer(zip_path, paths.UPDATES_DIR)
    log.info("verified %s; starting %s to install it after pid %s exits", zip_path, script, os.getpid())
    _start_installer(script, zip_path)
    global started
    started = tag


def can_update():
    """Only an installed build updates itself: not when running from source, from
    somewhere other than the install folder, or with XSS_HOME (install.ps1 always
    installs to %LOCALAPPDATA%)."""
    return paths.FROZEN and not paths.CUSTOM_HOME and paths.running_from_install_dir()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def auto_update(now=None):
    """The daily check at the start of sync/doctor/setup. Never raises; problems go to
    logs\\update.log and the command carries on."""
    try:
        _auto_update(time.time() if now is None else now)
    except Exception as e:
        try:
            _log().exception("automatic update failed: %s", e)
        except Exception:
            pass


def _auto_update(now):
    if not can_update():
        return
    if not load_config().get("auto_update", True):
        return
    state = load_state()
    if check_due(state, now):
        state["last_check"] = now
        try:
            state["latest"] = fetch_latest_tag()
        except Exception as e:
            _log().info("update check failed: %s", e)
        save_state(state)

    tag = state.get("latest")
    if not tag or not is_newer(tag):
        shutil.rmtree(paths.UPDATES_DIR, ignore_errors=True)  # an installed update's leftovers
        return
    attempt = state.get("attempt") or {}
    if attempt.get("version") == tag and _within(attempt.get("time"), now):
        return  # tried today already; don't download it again
    if busy := app_in_use():
        _log().info("%s is running; leaving the update to %s for a later run", ", ".join(busy), tag)
        return
    state["attempt"] = {"version": tag, "time": now}
    save_state(state)
    try:
        start_update(tag)
    except Exception as e:
        _log().error("update to %s failed: %s", tag, e)
        return
    print(f"XboxSteamSync {tag} is available; it will be installed when this command finishes.\n")


def run_update(check_only=False) -> int:
    """`xss update [--check]`: check now, ignoring the daily limit and auto_update."""
    try:
        tag = fetch_latest_tag()
    except Exception as e:
        print(f"Couldn't check for updates: {e}")
        return 1
    state = load_state()
    state.update(last_check=time.time(), latest=tag)
    save_state(state)

    if not is_newer(tag):
        print(f"XboxSteamSync {__version__} is up to date.")
        if last := resync_needed():
            print(f"Your Steam shortcuts were last synced by v{last}; run `xss sync` to update them.")
        return 0
    print(f"XboxSteamSync {tag} is available (you have {__version__}).")
    if check_only:
        return 0
    if not can_update():
        print(f"This copy can't update itself (it isn't installed in {paths.APP_DIR}). "
              "Install with the install script, or update your checkout.")
        return 1
    if busy := app_in_use():
        print(f"XboxSteamSync is in use ({', '.join(busy)}). Close any game started from Steam's Xbox "
              "collection and try again.")
        return 1
    print(f"Downloading {tag}...")
    try:
        start_update(tag)
    except Exception as e:
        _log().error("update to %s failed: %s", tag, e)
        print(f"Update failed: {e}")
        return 1
    print(f"Checksum OK. {tag} will be installed when this command finishes; see {LOG_PATH}.")
    return 0
