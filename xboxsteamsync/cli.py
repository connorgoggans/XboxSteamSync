"""`xss` command line."""

import argparse
import sys

from xboxsteamsync import __version__, paths
from xboxsteamsync.config import INPUT_MODES


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="xss", description="Add games from the Xbox app, the Epic Games Launcher and Battle.net to "
                                "Steam, with Steam Input.")
    parser.add_argument("--version", action="version", version=f"XboxSteamSync {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("sync", help="add/remove Steam shortcuts to match the installed games")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing anything")

    sub.add_parser("doctor", help="check Steam, SISR, VIIPER, the USBIP driver and each launcher")

    p = sub.add_parser("setup", help="install SISR, VIIPER and the USBIP driver if needed, then sync")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask before installing")
    p.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser("game", help="show or set a game's input mode (controller or keyboard-mouse)")
    p.add_argument("name", nargs="?", help="folder, package or display name of an installed game")
    p.add_argument("--input", choices=INPUT_MODES, help="set the game's input mode")
    p.add_argument("--list", action="store_true", help="show every installed game's input mode")

    p = sub.add_parser("sources", help="show or choose which launchers' games are added (xbox, epic, battlenet)")
    p.add_argument("launcher", nargs="?", help="xbox, epic or battlenet")
    on_off = p.add_mutually_exclusive_group()
    on_off.add_argument("--on", dest="on", action="store_const", const=True, help="add this launcher's games")
    on_off.add_argument("--off", dest="on", action="store_const", const=False,
                        help="don't add them; the next sync removes them from Steam")
    on_off.add_argument("--pick", action="store_true", help="choose which of its games to add, one by one")

    p = sub.add_parser("update", help="update XboxSteamSync to the latest release now")
    p.add_argument("--check", action="store_true", help="only report whether a new version exists")

    p = sub.add_parser("uninstall", help="remove our shortcuts, collection entries, artwork and settings from Steam")
    p.add_argument("--dry-run", action="store_true", help="show what would be removed")
    p.add_argument("--purge", action="store_true", help=f"also delete {paths.DATA_DIR} (program, settings, logs)")

    args = parser.parse_args(argv)
    try:
        if args.command in ("sync", "doctor", "setup") and not getattr(args, "dry_run", False):
            from xboxsteamsync.update import auto_update
            auto_update()
        if args.command == "update":
            from xboxsteamsync.update import run_update
            return run_update(check_only=args.check)
        if args.command == "sync":
            from xboxsteamsync.sync import run_sync
            return run_sync(dry_run=args.dry_run)
        if args.command == "doctor":
            from xboxsteamsync.doctor import run_doctor
            return run_doctor()
        if args.command == "setup":
            from xboxsteamsync.setup import run_setup
            return run_setup(resume=args.resume, assume_yes=args.yes)
        if args.command == "game":
            if args.list and (args.name or args.input):
                parser.error("--list can't be combined with a game name or --input")
            if args.input and not args.name:
                parser.error("--input needs a game name")
            from xboxsteamsync.games import run_game
            return run_game(None if args.list else args.name, args.input)
        if args.command == "sources":
            if (args.on is not None or args.pick) and not args.launcher:
                parser.error("--on, --off and --pick need a launcher")
            from xboxsteamsync.onboarding import run_sources
            return run_sources(args.launcher, args.on, args.pick)
        if args.command == "uninstall":
            from xboxsteamsync.uninstall import run_uninstall
            return run_uninstall(dry_run=args.dry_run, purge=args.purge)
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
