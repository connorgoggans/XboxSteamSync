"""Find, check and install SISR, VIIPER and the USBIP driver.

SISR (https://github.com/Alia5/SISR) and its dependencies are GPLv3 and are never
bundled; they're installed with SISR's own install script, as its author asks.
"""

import hashlib
import re
import subprocess
import urllib.request
import winreg
from dataclasses import dataclass
from pathlib import Path

from xboxsteamsync import paths
from xboxsteamsync.winutil import POWERSHELL, powershell_env

SISR_EXE = paths.LOCALAPPDATA / "SISR" / "SISR.exe"
VIIPER_EXE = paths.LOCALAPPDATA / "VIIPER" / "viiper.exe"

# The SISR release setup installs. Bump all three together after testing a new release:
# download the script, check what it does, and record its SHA-256.
INSTALL_VERSION = "0.6.2"
INSTALL_SCRIPT_URL = f"https://alia5.github.io/SISR/{INSTALL_VERSION}/install.ps1"
INSTALL_SCRIPT_SHA256 = "a70b5d688b3c2470184667c32f54d3342d38b1aa2df4d9f4f37e4cf2345c34c3"

# Releases are signed from v0.6.2 on.
SIGNED_FROM = (0, 6, 2)
EXPECTED_SIGNER = "CN=Peter Repukat"

# SISR versions we've tested, and the `run` flags each accepts that we rely on.
# v0.6.0 dropped the short -w/-f forms for --window.show/--window.fullscreen.
TESTED_VERSIONS = {
    "0.6.2": {"--window.show", "--window.fullscreen", "--log.file"},
}

CEF_FLAG_FILE = ".cef-enable-remote-debugging"

_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def parse_version(text: str):
    """(major, minor, patch) from SISR's --help banner ("Version: v0.6.2 (5cfde20)")."""
    m = re.search(r"Version:\s*v?(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def version_str(version) -> str:
    return ".".join(map(str, version))


def parse_flags(help_text: str):
    """Long flags listed in a --help page."""
    return set(re.findall(r"(?<![\w-])(--[a-z][\w.-]*)", help_text))


def _run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=powershell_env(),
                          creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")


def sisr_version(exe: Path = SISR_EXE):
    try:
        return parse_version(_run([str(exe), "--help"]).stdout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def sisr_run_flags(exe: Path = SISR_EXE):
    try:
        return parse_flags(_run([str(exe), "run", "--help"]).stdout)
    except (OSError, subprocess.TimeoutExpired):
        return set()


def unsupported_args(sisr_args, accepted_flags):
    """The arguments in `sisr_args` whose flag SISR doesn't accept."""
    bad = []
    for arg in sisr_args:
        if arg.startswith("-"):
            flag = arg.split("=", 1)[0]
            if flag not in accepted_flags:
                bad.append(arg)
    return bad


@dataclass
class Signature:
    status: str      # "Valid", "NotSigned", "HashMismatch", ...
    signer: str

    @property
    def ok(self):
        return self.status == "Valid" and self.signer.startswith(EXPECTED_SIGNER)


def signature(exe: Path = SISR_EXE) -> Signature:
    literal = str(exe).replace("'", "''")
    script = (f"$s = Get-AuthenticodeSignature -LiteralPath '{literal}'; "
              "\"$($s.Status)|$($s.SignerCertificate.Subject)\"")
    try:
        out = _run([POWERSHELL, "-NoProfile", "-Command", script], timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return Signature(f"error: {e}", "")
    status, _, signer = out.partition("|")
    return Signature(status or "unknown", signer)


# ---------------------------------------------------------------------------
# Windows state
# ---------------------------------------------------------------------------

_UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def installed_programs():
    """DisplayName -> DisplayVersion for everything in Windows' installed-programs list."""
    out = {}
    for hive, path in _UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        name = winreg.QueryValueEx(k, "DisplayName")[0]
                        try:
                            version = winreg.QueryValueEx(k, "DisplayVersion")[0]
                        except OSError:
                            version = ""
                except OSError:
                    continue
                out[name] = version
    return out


def usbip_version():
    """Version of the USBIP (usbip-win2) driver, or None. Its entry is named
    "USBip <version>" (older releases: "USBip version <version>")."""
    for name, version in installed_programs().items():
        if re.match(r"USBip\b", name, re.I):
            return version or name
    return None


def viiper_autostart():
    """VIIPER's entry in the user's startup list, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            return winreg.QueryValueEx(k, "VIIPER")[0]
    except OSError:
        return None


USBIP_DRIVER = "usbip2_ude"


def usbip_driver_running() -> bool:
    """Whether the USBIP kernel driver is loaded. Right after installing it isn't, until
    Windows restarts."""
    try:
        out = _run(["sc", "query", USBIP_DRIVER]).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    m = re.search(r"STATE\s*:\s*(\d+)", out)
    return bool(m) and m.group(1) == "4"  # SERVICE_RUNNING


def cef_flag_path(steam_dir: Path) -> Path:
    return steam_dir / CEF_FLAG_FILE


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


class ScriptHashMismatch(Exception):
    pass


def download_install_script(dest: Path) -> Path:
    req = urllib.request.Request(INSTALL_SCRIPT_URL, headers={"User-Agent": "XboxSteamSync"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != INSTALL_SCRIPT_SHA256:
        raise ScriptHashMismatch(
            f"SISR's install script at {INSTALL_SCRIPT_URL} has changed (SHA-256 {digest}, expected "
            f"{INSTALL_SCRIPT_SHA256}). Not running it. Update XboxSteamSync, or install SISR yourself "
            "from https://github.com/Alia5/SISR.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def run_install_script(script: Path, log_path: Path) -> tuple[int, str]:
    """Run SISR's install script, echoing and logging its output. Returns (exit code, output)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    with open(log_path, "w", encoding="utf-8") as log, subprocess.Popen(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            env=powershell_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace") as proc:
        for line in proc.stdout:
            print("    " + line.rstrip())
            log.write(line)
            lines.append(line)
    return proc.returncode, "".join(lines)
