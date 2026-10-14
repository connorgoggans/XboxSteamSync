"""`xss doctor`: check everything XboxSteamSync depends on."""

from dataclasses import dataclass
from pathlib import Path

from xboxsteamsync import paths, sisr, steam
from xboxsteamsync.config import load_config
from xboxsteamsync.sync import is_legacy, managed_aumid
from xboxsteamsync.xbox_games import find_games, gaming_roots

OK, WARN, FAIL = "ok", "warn", "FAIL"
_MARK = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _check_steam():
    checks = []
    try:
        steam_path = steam.steam_dir()
    except steam.SteamNotFound as e:
        return [Check("Steam", FAIL, str(e), "Install Steam from https://store.steampowered.com")], None
    checks.append(Check("Steam", OK, str(steam_path)))

    users = steam.user_config_dirs(steam_path)
    if users:
        checks.append(Check("Steam users", OK, ", ".join(u.parent.name for u in users)))
    else:
        checks.append(Check("Steam users", FAIL, "nobody has signed in to Steam on this PC",
                            "Start Steam and sign in"))

    flag = sisr.cef_flag_path(steam_path)
    if flag.exists():
        checks.append(Check("Steam debugging flag", OK, str(flag)))
    else:
        checks.append(Check("Steam debugging flag", FAIL, f"{flag} is missing (SISR needs it)",
                            "Run `xss setup`, then restart Steam"))

    legacy = stale = 0
    launcher = str(paths.launcher_exe()).lower()
    for user_cfg in users:
        for s in steam.load_shortcuts(user_cfg):
            if not managed_aumid(s):
                continue
            if is_legacy(s):
                legacy += 1
            elif s.get("Exe", "").strip('"').lower() != launcher:
                stale += 1
    if legacy or stale:
        checks.append(Check("Steam shortcuts", WARN,
                            f"{legacy + stale} shortcut(s) point at an old or moved launcher",
                            "Run `xss sync`"))
    return checks, steam_path


def _check_sisr(cfg):
    checks = []
    exe = Path(cfg["sisr_path"]) if cfg["sisr_path"] else None
    if exe is None:
        return [Check("SISR", WARN, "disabled in config (sisr_path is empty); games get no Steam Input")]
    if not exe.exists():
        return [Check("SISR", FAIL, f"not found at {exe}", "Run `xss setup`")]

    version = sisr.sisr_version(exe)
    if version is None:
        checks.append(Check("SISR", WARN, f"{exe}: couldn't read its version from --help"))
    else:
        v = sisr.version_str(version)
        tested = v in sisr.TESTED_VERSIONS
        checks.append(Check("SISR", OK if tested else WARN,
                            f"v{v} at {exe}" + ("" if tested else
                                                f" (untested; tested: {', '.join(sisr.TESTED_VERSIONS)})")))

    if version and version >= sisr.SIGNED_FROM:
        sig = sisr.signature(exe)
        checks.append(Check("SISR signature", OK if sig.ok else FAIL,
                            f"{sig.status}, {sig.signer or 'no signer'}",
                            "" if sig.ok else "Reinstall SISR with `xss setup`"))

    flags = sisr.sisr_run_flags(exe)
    if not flags:
        checks.append(Check("SISR arguments", WARN, "couldn't read `SISR run --help`"))
    elif bad := sisr.unsupported_args(cfg["sisr_args"], flags):
        checks.append(Check("SISR arguments", FAIL, f"this SISR doesn't accept {' '.join(bad)}",
                            f"Fix sisr_args in {paths.CONFIG_PATH}"))
    else:
        checks.append(Check("SISR arguments", OK, " ".join(cfg["sisr_args"]) or "(none)"))
    return checks


def _check_viiper():
    if not sisr.VIIPER_EXE.exists():
        return [Check("VIIPER", FAIL, f"not found at {sisr.VIIPER_EXE}", "Run `xss setup`")]
    checks = [Check("VIIPER", OK, str(sisr.VIIPER_EXE))]
    if not sisr.viiper_autostart():
        checks.append(Check("VIIPER autostart", WARN, "not in the Windows startup list",
                            "Run `xss setup`, or start VIIPER yourself before playing"))
    return checks


def _check_usbip():
    version = sisr.usbip_version()
    if not version:
        return [Check("USBIP driver", FAIL, "not installed", "Run `xss setup` (needs admin and a restart)")]
    if not sisr.usbip_driver_running():
        return [Check("USBIP driver", FAIL, f"{version} installed, but the driver isn't running",
                      "Restart Windows")]
    return [Check("USBIP driver", OK, version)]


def _check_xbox(cfg):
    roots = [Path(d) for d in cfg["xbox_games_dirs"]] or gaming_roots()
    if not roots:
        return [Check("Xbox install folders", FAIL, "none found (no .GamingRoot on any drive)",
                      "Install a game with the Xbox app")]
    checks = [Check("Xbox install folders", OK, ", ".join(map(str, roots)))]
    try:
        games = find_games(roots, cfg["exclude"])
    except Exception as e:
        return checks + [Check("Xbox games", FAIL, f"couldn't list games: {e}")]
    checks.append(Check("Xbox games", OK if games else WARN, f"{len(games)} installed"))
    return checks


def _check_install():
    launcher = paths.launcher_exe()
    if not launcher.exists():
        return [Check("xss-launch.exe", FAIL, f"missing at {launcher}", "Reinstall XboxSteamSync")]
    if paths.FROZEN and not paths.running_from_install_dir():
        return [Check("xss-launch.exe", WARN, f"running from {launcher.parent}, not {paths.APP_DIR}",
                      "Install with the install script so shortcuts don't break")]
    return [Check("xss-launch.exe", OK, str(launcher))]


def collect():
    try:
        cfg = load_config()
    except Exception as e:
        return [Check("config.json", FAIL, f"{paths.CONFIG_PATH}: {e}", "Fix or delete the file")]
    steam_checks, _ = _check_steam()
    return [*_check_install(), *steam_checks, *_check_sisr(cfg), *_check_viiper(), *_check_usbip(),
            *_check_xbox(cfg)]


def run_doctor() -> int:
    checks = collect()
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{_MARK[c.status]} {c.name:<{width}}  {c.detail}")
        if c.fix and c.status != OK:
            print(f"       {'':<{width}}  -> {c.fix}")
    failed = sum(c.status == FAIL for c in checks)
    print(f"\n{failed} problem(s) found." if failed else "\nEverything looks good.")
    return 1 if failed else 0
