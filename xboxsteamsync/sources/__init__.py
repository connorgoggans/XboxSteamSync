"""Where games come from: the Xbox app, the Epic Games Launcher and Battle.net.

Each source module has `detect(cfg) -> Detection`. Epic and Battle.net also have
`find(game_id)`, which the launcher uses to look a game up again at launch time.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


SOURCES = ("xbox", "epic", "battlenet")
LABELS = {"xbox": "Xbox", "epic": "Epic Games", "battlenet": "Battle.net"}


@dataclass
class Game:
    source: str          # one of SOURCES
    id: str              # xbox: AUMID (PackageFamilyName!AppId); epic: AppName; battlenet: product code
    name: str            # display name
    folder: str          # install folder name
    identity: str        # xbox: package name; epic: AppName; battlenet: product code
    dirs: list           # folders the game's processes run from (falsy entries are ignored)
    exes: list = field(default_factory=list)  # executable names, for processes outside `dirs`
    launch: str = ""     # epic/battlenet: launch URI
    art: dict = field(default_factory=dict)   # local art: {"config": Content dir} or image URLs
                                              # under "wide", "tall", "logo", "icon"

    @property
    def key(self):
        """Identifies the game's shortcut, see sync.managed_key."""
        return f"{self.source}:{self.id}"

    @property
    def setting_keys(self):
        """(folder, identity, name): what `exclude`, `games` and `steamgriddb_ids` match."""
        return self.folder, self.identity, self.name

    # Xbox names, kept for readability where only Xbox games appear.
    @property
    def aumid(self):
        return self.id

    @property
    def content_dir(self):
        return self.dirs[0] if self.dirs else None


@dataclass
class Detection:
    source: str
    found: bool                 # the launcher (or for Xbox, an install folder) is on this PC
    games: list                 # every installed game, before `exclude`
    detail: str = ""            # where the games were read from, for `xss doctor`
    error: str = ""             # set when the launcher's data couldn't be read: nothing is removed
    keep_missing: object = None  # (shortcut, exclude) -> why a shortcut whose game wasn't found
                                 # should stay, or None to remove it


def shortcut_options(shortcut, flag):
    """Every value of `flag` (e.g. "--dir") in a shortcut's launch options."""
    pattern = re.escape(flag) + r' (?:"([^"]*)"|(\S+))'
    return [a or b for a, b in re.findall(pattern, shortcut.get("LaunchOptions", ""))]


def shortcut_dirs(shortcut):
    """The --dir folders in a shortcut's launch options (for Xbox, the Content folder first)."""
    return [Path(d) for d in shortcut_options(shortcut, "--dir")]


def is_excluded(exclude, *names):
    return bool({n.lower() for n in names if n} & {e.lower() for e in exclude or ()})


def included(games, exclude):
    return [g for g in games if not is_excluded(exclude, *g.setting_keys)]


def collection_name(cfg, source):
    """Steam collection for a source. `collection_name` stays the Xbox one."""
    if source == "xbox":
        return cfg["collection_name"]
    return (cfg.get("collections") or {}).get(source) or LABELS[source]


def _has_program(folder: Path, depth=2):
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() == ".exe":
                return True
            if depth > 1 and p.is_dir() and _has_program(p, depth - 1):
                return True
    except OSError:
        pass
    return False


def folder_kept_reason(install_dir, exclude, *names):
    """For launchers: whether a game that's gone from the launcher's data is really gone.

    It's kept while its drive is unavailable, or while its install folder still has a
    program in it (Epic and Battle.net leave saves and mods folders behind). `exclude`
    always removes it.
    """
    if not install_dir or is_excluded(exclude, install_dir.name, *names):
        return None
    if not Path(install_dir.anchor).exists():
        return f"{install_dir.anchor} isn't available"
    if install_dir.is_dir() and _has_program(install_dir):
        return f"{install_dir} still has the game in it"
    return None


def module(source):
    from xboxsteamsync.sources import battlenet, epic, xbox
    return {"xbox": xbox, "epic": epic, "battlenet": battlenet}[source]


def detect(source, cfg) -> Detection:
    try:
        return module(source).detect(cfg)
    except Exception as e:  # a broken launcher install shouldn't stop the others
        return Detection(source, True, [], error=f"{type(e).__name__}: {e}")


def detect_all(cfg):
    return {s: detect(s, cfg) for s in SOURCES}
