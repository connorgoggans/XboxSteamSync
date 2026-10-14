"""`xss setup`: install SISR, VIIPER and the USBIP driver if needed, then sync.

The USBIP driver needs a Windows restart. Setup saves its progress and adds a
RunOnce entry so it carries on by itself after the restart.
"""

import json
import subprocess
import tempfile
import winreg
from pathlib import Path

from xboxsteamsync import paths, sisr, steam
from xboxsteamsync.sync import run_sync

RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_NAME = "XboxSteamSyncSetup"

SIDE_EFFECTS = """\
SISR's install script will:
  - install SISR to {sisr} and VIIPER to {viiper}
  - install the USBIP driver (asks for admin rights; needs a Windows restart)
  - add VIIPER to the Windows startup list
  - create SISR shortcuts on the desktop and in the Start Menu
  - turn on Steam's debugging flag ({flag}), which SISR uses to read Steam Input
To remove them later, run {uninstall}."""


def _missing(steam_path):
    """What the install script would fix, as readable strings."""
    missing = []
    version = sisr.sisr_version() if sisr.SISR_EXE.exists() else None
    if version is None:
        missing.append("SISR")
    elif version < tuple(int(x) for x in sisr.INSTALL_VERSION.split(".")):
        missing.append(f"SISR update (v{sisr.version_str(version)} -> v{sisr.INSTALL_VERSION})")
    if not sisr.VIIPER_EXE.exists():
        missing.append("VIIPER")
    if not sisr.usbip_version():
        missing.append("USBIP driver")
    if steam_path and not sisr.cef_flag_path(steam_path).exists():
        missing.append("Steam debugging flag")
    return missing


def _save_state(state):
    paths.SETUP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.SETUP_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_state():
    try:
        return json.loads(paths.SETUP_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _schedule_resume():
    cmd = subprocess.list2cmdline([*paths.xss_command(), "setup", "--resume"])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY) as k:
        winreg.SetValueEx(k, RUNONCE_NAME, 0, winreg.REG_SZ, cmd)


def _cancel_resume():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUNONCE_NAME)
    except OSError:
        pass


def _confirm(prompt, assume_yes):
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


def _install(steam_path, assume_yes) -> str:
    """Run SISR's install script. Returns "ok", "restart" or "failed"."""
    uninstall = sisr.SISR_EXE.parent / "uninstall.ps1"
    flag = sisr.cef_flag_path(steam_path) if steam_path else f"<Steam>\\{sisr.CEF_FLAG_FILE}"
    print(SIDE_EFFECTS.format(sisr=sisr.SISR_EXE.parent, viiper=sisr.VIIPER_EXE.parent, flag=flag,
                              uninstall=uninstall))
    if not _confirm(f"\nInstall SISR v{sisr.INSTALL_VERSION} now?", assume_yes):
        print("Skipped. Games will launch without Steam Input until SISR is installed.")
        return "failed"

    print(f"\nDownloading {sisr.INSTALL_SCRIPT_URL} ...")
    with tempfile.TemporaryDirectory(prefix="xss-") as tmp:
        try:
            script = sisr.download_install_script(Path(tmp) / "install.ps1")
        except sisr.ScriptHashMismatch as e:
            print(e)
            return "failed"
        except OSError as e:
            print(f"Download failed: {e}")
            return "failed"
        print("Checksum OK. Running SISR's install script:")
        code, _ = sisr.run_install_script(script, paths.LOG_DIR / "sisr-install.log")

    if code != 0 or not sisr.SISR_EXE.exists():
        print(f"\nSISR's install script failed (exit code {code}); see {paths.LOG_DIR / 'sisr-install.log'}.")
        return "failed"

    sig = sisr.signature()
    if not sig.ok:
        print(f"\n! SISR.exe's signature isn't valid ({sig.status}, {sig.signer or 'no signer'}). "
              "Not using it; delete it and try again.")
        return "failed"

    # The script reports VIIPER/USBIP problems as warnings and still exits 0.
    if not sisr.usbip_version():
        print("\nThe USBIP driver didn't install (the admin prompt may have been declined). "
              "Run `xss setup` again to retry.")
        return "failed"
    if not sisr.usbip_driver_running():
        return "restart"
    return "ok"


def run_setup(resume=False, assume_yes=False) -> int:
    if resume:
        _cancel_resume()
        print("Continuing XboxSteamSync setup after the restart.\n")
    try:
        steam_path = steam.steam_dir()
    except steam.SteamNotFound as e:
        print(f"{e}\nInstall Steam, sign in, then run `xss setup` again.")
        return 1
    if not steam.user_config_dirs(steam_path):
        print(steam.NO_USERS_MESSAGE)
        return 1

    state = _load_state()
    flag_was_missing = not sisr.cef_flag_path(steam_path).exists() or state.get("restart_steam", False)
    after_restart = resume and state.get("stage") == "restart"

    missing = _missing(steam_path)
    if missing:
        if after_restart:
            print("Still missing after the restart: " + ", ".join(missing))
            print("Run `xss doctor` for details, or `xss setup` to try again.")
            paths.SETUP_STATE_PATH.unlink(missing_ok=True)
            return _finish(1, resume, assume_yes)
        print("Needs: " + ", ".join(missing) + "\n")
        if _install(steam_path, assume_yes) == "failed":
            return 1
    else:
        print("SISR, VIIPER and the USBIP driver are already installed.")

    # A freshly installed USBIP driver only loads after Windows restarts.
    if not sisr.usbip_driver_running():
        if after_restart:
            print("The USBIP driver still isn't running after the restart. Run `xss doctor` for details.")
            paths.SETUP_STATE_PATH.unlink(missing_ok=True)
            return _finish(1, resume, assume_yes)
        _save_state({"stage": "restart", "restart_steam": flag_was_missing})
        _schedule_resume()
        print("\nWindows needs to restart to finish installing the USBIP driver.\n"
              "Setup will continue by itself after you restart and sign in.")
        return 0

    paths.SETUP_STATE_PATH.unlink(missing_ok=True)
    print("\nSyncing games into Steam...\n")
    code = run_sync(restart_steam=flag_was_missing, assume_yes=assume_yes)
    if code == 0:
        print("\nSetup complete. Your games are in Steam, in a collection per launcher (\"Xbox\", "
              "\"Epic Games\", \"Battle.net\"). Run `xss sync` after installing or removing games.")
    return _finish(code, resume, assume_yes)


def _finish(code, resume, assume_yes):
    """After a restart we run in our own console window; keep it open so the result can be read."""
    if resume and not assume_yes:
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass
    return code
