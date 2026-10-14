"""Launch a game from a Steam shortcut, with Steam Input via SISR.

Steam can't inject Steam Input into a game started through the Xbox app, the Epic Games
Launcher or Battle.net (the game isn't in the process tree Steam launched). Instead
this wrapper:

  1. starts SISR (Steam Input System Redirector) as a child, so it runs under this
     shortcut's Steam Input configuration and exposes it as a system-wide virtual
     Xbox controller the game can see;
  2. launches the game: Xbox games through shell:AppsFolder, Epic games through the
     launcher's URL, Battle.net games with `Battle.net.exe --exec="launch <code>"`;
  3. stays alive while the game runs, so Steam keeps this shortcut's config active;
  4. closes SISR when the game exits.

Usage: xss-launch --aumid <PFN!AppId> --dir <dir> [--dir <dir>...] [--exe <name>...]
       xss-launch --source epic|battlenet --id <id> --dir <dir> [--exe <name>...]

SISR's path and arguments, and the game's input mode, come from config.json at launch
time, so changing them doesn't need a re-sync. In keyboard-mouse mode SISR isn't started:
Steam Input then sends keyboard and mouse input from this shortcut's layout itself.
"""

import argparse
import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import psutil

from xboxsteamsync import paths
from xboxsteamsync.config import KEYBOARD_MOUSE, game_entry, input_mode, load_config
from xboxsteamsync.sources import battlenet, epic
from xboxsteamsync.sources.xbox import read_game_config

STARTUP_TIMEOUT = 180           # seconds to wait for an Xbox game to start
LAUNCHER_STARTUP_TIMEOUT = 300  # Epic and Battle.net: sign-in, updates, pre-launchers waiting for a click
SETTLE = 20            # seconds a game process must run before the game counts as started
HANDOFF_GRACE = 15     # seconds with no game process before we consider it exited
SISR_WARMUP = 3        # seconds for SISR to create its virtual controller before the game starts
POLL_INTERVAL = 2
BATTLENET_RESEND = 10  # seconds before asking Battle.net to launch the game again

LOG_PATH = paths.LOG_DIR / "launcher.log"


def _game_running(prefixes, exe_names):
    for proc in psutil.process_iter(["exe", "name"]):
        exe = proc.info.get("exe")
        if exe and os.path.normcase(exe).startswith(prefixes):
            return proc
        if (proc.info.get("name") or "").lower() in exe_names:
            return proc
    return None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


def _kill_with_us(proc: subprocess.Popen):
    """Tie `proc`'s lifetime to ours: Windows kills it when our last handle to the job
    closes, i.e. whenever this process exits, even if it's killed or crashes. Otherwise
    an orphaned SISR would keep its virtual controller attached for every other game.
    Returns the job handle, which must stay referenced for the life of the process."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (job
            and kernel32.SetInformationJobObject(wintypes.HANDLE(job), _JobObjectExtendedLimitInformation,
                                                 ctypes.byref(info), ctypes.sizeof(info))
            and kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle)))):
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def _stop(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def game_keys(aumid, dirs):
    """(folder, package name, display name) of the game, to look up its settings.

    The shortcut only passes the AUMID and install folders, so the folder name comes
    from the Content dir and the display name from its MicrosoftGame.config.
    """
    identity = aumid.split("!", 1)[0].rsplit("_", 1)[0]
    for d in dirs:
        config_path = Path(d) / "MicrosoftGame.config"
        if not config_path.exists():
            continue
        try:
            info = read_game_config(config_path)
        except Exception:
            info = None
        return config_path.parent.parent.name, identity, (info.name or "") if info else ""
    return "", identity, ""


def startup_timeout(source):
    return STARTUP_TIMEOUT if source == "xbox" else LAUNCHER_STARTUP_TIMEOUT


def _open(program):
    """Start a program through Explorer, so it isn't our child process: a launcher started
    from here would otherwise sit in the process tree Steam watches."""
    subprocess.Popen(["explorer.exe", str(program)])


def _open_uri(uri):
    """Open a launcher URI. Not through Explorer: it ignores URIs with a query string (Epic's
    `?action=launch`). rundll32 hands it to the protocol handler and exits, so a launcher it
    starts isn't left as our child; it also leaves our job object if Steam's job allows it."""
    args = ["rundll32.exe", "url.dll,FileProtocolHandler", uri]
    try:
        subprocess.Popen(args, creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(args)


class BattlenetLaunch:
    """`Battle.net.exe --exec="launch <code>"`, sent once Battle.net is up.

    Battle.net drops --exec while it's starting, so if it isn't running we start it and
    send the command once its main window shows (or after BATTLENET_RESEND seconds). The
    window can show before Battle.net takes commands, and a launch sent just after the game
    closed can be ignored too, so until the game appears the command is sent again every
    BATTLENET_RESEND seconds.
    """

    def __init__(self, code, log):
        self.code, self.log = code, log
        self.exe, self.started, self.sent = None, None, None

    def start(self):
        self.exe = battlenet.running_exe()
        if self.exe:
            self._send()
        else:
            self.exe = battlenet.battlenet_exe()
            _open(self.exe)
            self.started = time.monotonic()
            self.log.info("started %s; waiting for its main window", self.exe)

    def poll(self):
        now = time.monotonic()
        if self.sent is None:
            if battlenet.main_window_shown() or now - self.started >= BATTLENET_RESEND:
                self._send()
        elif now - self.sent >= BATTLENET_RESEND:
            self.log.info("game hasn't started yet; asking Battle.net again")
            self._send()

    def _send(self):
        subprocess.Popen([str(self.exe), f"--exec=launch {self.code}"])
        self.sent = time.monotonic()
        self.log.info("sent launch %s to %s", self.code, self.exe)


def find_game(source, game_id, log):
    """The installed Epic/Battle.net game, looked up again for its name and launch URI."""
    try:
        return {"epic": epic.find, "battlenet": battlenet.find}[source](game_id)
    except Exception as e:
        log.warning("couldn't look up %s:%s: %s", source, game_id, e)
        return None


def start_game(source, game_id, game, cfg, keys, log):
    """Launch the game. Returns a callable to run while waiting for it to appear, or None."""
    if source == "xbox":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{game_id}"])
        return None
    if source == "epic":
        _open_uri(game.launch if game else f"com.epicgames.launcher://apps/{game_id}?action=launch&silent=true")
        return None
    code = game_entry(cfg, *keys)[1].get("battlenet_code") or battlenet.EXEC_CODES.get(game_id)
    if not code:
        log.warning("no Battle.net launch code known for %s; opening Battle.net instead (press Play there, "
                    "or set \"battlenet_code\" for the game in config.json)", game_id)
        _open_uri(game.launch if game else f"battlenet://game/{game_id}")
        return None
    launch = BattlenetLaunch(code, log)
    launch.start()
    return launch.poll


def wait_for_game(prefixes, exe_names, timeout, log, poll=None):
    """Wait while the game runs. Returns whether it ever appeared.

    The game counts as started once one of its processes has run for SETTLE seconds, and
    as exited once none has run for HANDOFF_GRACE seconds after that. Something that runs
    only briefly (a pre-launcher handing off to a launcher outside the game's folder, like
    Cyberpunk's REDprelauncher) keeps us waiting, up to the startup timeout.
    """
    deadline = time.monotonic() + timeout
    first_seen = last_seen = None
    started = handoff_logged = False
    while True:
        proc = _game_running(prefixes, exe_names)
        now = time.monotonic()
        if proc:
            if first_seen is None:
                first_seen = now
                log.info("game running: %s pid=%s", proc.info.get("exe") or proc.info.get("name"), proc.pid)
            last_seen = now
            started = started or now - first_seen >= SETTLE
        elif last_seen is None:
            if now > deadline:
                log.warning("game never appeared (prefixes=%s exes=%s)", list(prefixes), sorted(exe_names))
                return False
            if poll:
                poll()
        elif now - last_seen >= HANDOFF_GRACE:
            if started or now > deadline:
                break
            if not handoff_logged:
                log.info("game exited within %s s; waiting up to the startup timeout for it to start", SETTLE)
                handoff_logged = True
        time.sleep(POLL_INTERVAL)
    log.info("game exited")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--aumid", help="Xbox app game to start through shell:AppsFolder")
    parser.add_argument("--source", choices=("epic", "battlenet"), help="launcher of a non-Xbox game")
    parser.add_argument("--id", help="the game's id in its launcher (Epic AppName, Battle.net product code)")
    parser.add_argument("--dir", action="append", default=[], help="install dir(s) the game's processes run from")
    parser.add_argument("--exe", action="append", default=[], help="game executable name(s)")
    args = parser.parse_args(argv)
    if not args.aumid and not (args.source and args.id):
        parser.error("needs --aumid, or --source and --id")
    source, game_id = ("xbox", args.aumid) if args.aumid else (args.source, args.id)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s [%(process)d] %(message)s")
    log = logging.getLogger()
    log.info("launch %s", game_id if source == "xbox" else f"{source}:{game_id}")
    try:
        cfg = load_config()
    except Exception as e:  # a broken config.json shouldn't stop the game from starting
        log.error("could not read %s: %s; using defaults", paths.CONFIG_PATH, e)
        cfg = load_config(read_file=False)
    sisr_path, sisr_args = cfg["sisr_path"], cfg["sisr_args"]
    game = None
    if source == "xbox":
        keys = game_keys(game_id, args.dir)
    else:
        game = find_game(source, game_id, log)
        keys = (Path(args.dir[0]).name if args.dir else "", game_id, game.name if game else "")
    mode = input_mode(cfg, *keys)
    log.info("input mode: %s", mode)

    prefixes = tuple(os.path.normcase(os.path.abspath(d)).rstrip("\\") + "\\" for d in args.dir if d)
    exe_names = {e.lower() for e in args.exe}

    sisr = None
    if mode == KEYBOARD_MOUSE:
        log.info("not starting SISR; Steam Input sends keyboard and mouse input")
    elif sisr_path and Path(sisr_path).exists():
        sisr_log = LOG_PATH.with_name("sisr.log")
        sisr = subprocess.Popen([sisr_path, *sisr_args, f"--log.file={sisr_log}"])
        log.info("started SISR pid=%s args=%s", sisr.pid, sisr_args)
        try:
            sisr_job = _kill_with_us(sisr)  # noqa: F841 - keeps the job handle open
        except OSError as e:
            log.warning("could not tie SISR to launcher lifetime: %s", e)
        time.sleep(SISR_WARMUP)
        if sisr.poll() is not None:
            log.error("SISR exited early with code %s (80 = bad arguments); see %s", sisr.returncode, sisr_log)
            sisr = None
    elif sisr_path:
        log.warning("SISR not found at %s; continuing without it (run `xss doctor`)", sisr_path)

    try:
        poll = start_game(source, game_id, game, cfg, keys, log)
        wait_for_game(prefixes, exe_names, startup_timeout(source), log, poll)
    finally:
        if sisr:
            _stop(sisr)
            log.info("stopped SISR")


if __name__ == "__main__":
    main()
