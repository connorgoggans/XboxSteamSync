"""`xss` command line."""

import argparse
import sys

from xboxsteamsync import __version__, paths


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="xss", description="Add games installed by the Xbox app to Steam, with Steam Input.")
    parser.add_argument("--version", action="version", version=f"XboxSteamSync {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("sync", help="add/remove Steam shortcuts to match the installed Xbox games")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing anything")

    sub.add_parser("doctor", help="check Steam, SISR, VIIPER, the USBIP driver and the Xbox folders")

    p = sub.add_parser("setup", help="install SISR, VIIPER and the USBIP driver if needed, then sync")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask before installing")
    p.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser("uninstall", help="remove our shortcuts, collection entries, artwork and settings from Steam")
    p.add_argument("--dry-run", action="store_true", help="show what would be removed")
    p.add_argument("--purge", action="store_true", help=f"also delete {paths.DATA_DIR} (program, settings, logs)")

    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            from xboxsteamsync.sync import run_sync
            return run_sync(dry_run=args.dry_run)
        if args.command == "doctor":
            from xboxsteamsync.doctor import run_doctor
            return run_doctor()
        if args.command == "setup":
            from xboxsteamsync.setup import run_setup
            return run_setup(resume=args.resume, assume_yes=args.yes)
        if args.command == "uninstall":
            from xboxsteamsync.uninstall import run_uninstall
            return run_uninstall(dry_run=args.dry_run, purge=args.purge)
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
