"""Launch an Xbox app game from a Steam shortcut, with Steam Input via SISR.

Steam can't inject Steam Input into a game started through the Xbox app (the game
isn't in the process tree Steam launched). Instead this wrapper:

  1. starts SISR (Steam Input System Redirector) as a child, so it runs under this
     shortcut's Steam Input configuration and exposes it as a system-wide virtual
     Xbox controller the game can see;
  2. launches the game through shell:AppsFolder;
  3. stays alive while the game runs, so Steam keeps this shortcut's config active;
  4. closes SISR when the game exits.

Usage: xss-launch --aumid <PFN!AppId> --dir <dir> [--dir <dir>...] [--exe <name>...]

SISR's path and arguments come from config.json at launch time, so changing them
doesn't need a re-sync.
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
from xboxsteamsync.config import load_config

STARTUP_TIMEOUT = 180  # seconds to wait for the game to appear
HANDOFF_GRACE = 15     # seconds with no game process before we consider it exited
SISR_WARMUP = 3        # seconds for SISR to create its virtual controller before the game starts
POLL_INTERVAL = 2

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--aumid", required=True)
    parser.add_argument("--dir", action="append", default=[], help="install dir(s) the game's processes run from")
    parser.add_argument("--exe", action="append", default=[], help="game executable name(s)")
    args = parser.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s [%(process)d] %(message)s")
    log = logging.getLogger()
    log.info("launch %s", args.aumid)
    try:
        cfg = load_config()
    except Exception as e:  # a broken config.json shouldn't stop the game from starting
        log.error("could not read %s: %s; using defaults", paths.CONFIG_PATH, e)
        cfg = load_config(read_file=False)
    sisr_path, sisr_args = cfg["sisr_path"], cfg["sisr_args"]

    prefixes = tuple(os.path.normcase(os.path.abspath(d)).rstrip("\\") + "\\" for d in args.dir if d)
    exe_names = {e.lower() for e in args.exe}

    sisr = None
    if sisr_path and Path(sisr_path).exists():
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
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{args.aumid}"])

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not (proc := _game_running(prefixes, exe_names)):
            if time.monotonic() > deadline:
                log.warning("game never appeared (dirs=%s exes=%s)", args.dir, sorted(exe_names))
                return
            time.sleep(POLL_INTERVAL)
        log.info("game running: %s pid=%s", proc.info.get("exe") or proc.info.get("name"), proc.pid)

        # Some games start through a bootstrap exe that hands off to the real one, so
        # only exit once nothing from the game has run for HANDOFF_GRACE seconds.
        last_seen = time.monotonic()
        while time.monotonic() - last_seen < HANDOFF_GRACE:
            time.sleep(POLL_INTERVAL)
            if _game_running(prefixes, exe_names):
                last_seen = time.monotonic()
        log.info("game exited")
    finally:
        if sisr:
            _stop(sisr)
            log.info("stopped SISR")


if __name__ == "__main__":
    main()
