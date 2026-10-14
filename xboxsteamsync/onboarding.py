"""Choosing which launchers' games go into Steam: the questions the first sync or setup
asks, and `xss sources`.

The answers are saved in config.json as `sources` ({"xbox": true, "epic": false, ...}),
plus `exclude` entries for games not picked. A detected launcher with no `sources` entry
is undecided; Xbox counts as on. Nothing here restarts Steam: the sync that follows does
that, only if something changed.
"""

import sys

from xboxsteamsync import update
from xboxsteamsync.config import load_config, load_raw, save_raw, source_state
from xboxsteamsync.sources import LABELS, SOURCES, detect, is_excluded

ASK, DRY_RUN, YES, QUIET = "ask", "dry-run", "yes", "quiet"
ALL, SKIP = "all", "skip"  # answers; picking gives the list of picked games instead

ALIASES = {"xbox": "xbox", "epic": "epic", "epicgames": "epic", "battlenet": "battlenet",
           "battle.net": "battlenet", "bnet": "battlenet"}


def mode_for(dry_run=False, assume_yes=False):
    """ASK when someone can answer, otherwise what to do instead (see choose)."""
    if dry_run:
        return DRY_RUN
    if assume_yes:
        return YES
    return ASK if sys.stdin is not None and sys.stdin.isatty() else QUIET


def is_first_run(raw):
    """No `sources` saved and never synced: a new install, asked about every launcher."""
    return "sources" not in raw and not update.load_state().get("last_synced_version")


def _count(n):
    return f"{n} game{'s' if n != 1 else ''}"


def _ask(prompt, choices, default):
    """One of `choices` (first letters), or None when no one can answer (EOF)."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return None
        if not answer:
            return default
        if answer[0] in choices:
            return answer[0]


def pick_games(games):
    """Ask about each game; returns the picked ones, or None on EOF."""
    picked = []
    for g in games:
        answer = _ask(f"  Add {g.name}? [Y/n] ", "yn", "y")
        if answer is None:
            return None
        if answer == "y":
            picked.append(g)
    return picked


def ask(candidates, detections, link_everything):
    """{source: ALL | SKIP | [picked games]}, or None when no one can answer."""
    print("Found games from:")
    for source in candidates:
        print(f"  {LABELS[source]}: {_count(len(detections[source].games))}")
    if link_everything:
        answer = _ask("Add all of them to Steam? Games installed later are added by later syncs. [Y/n] ", "yn", "y")
        if answer is None:
            return None
        if answer == "y":
            return {source: ALL for source in candidates}
    answers = {}
    for source in candidates:
        games = detections[source].games
        answer = _ask(f"{LABELS[source]} ({_count(len(games))}): add [a]ll, [p]ick games, or [s]kip? [A/p/s] ",
                      "aps", "a")
        if answer is None:
            return None
        if answer == "p":
            answer = pick_games(games)
            if answer is None:
                return None
            answers[source] = answer
        else:
            answers[source] = ALL if answer == "a" else SKIP
    return answers


def set_picks(raw, games, picked):
    """Rewrite one launcher's `exclude` entries: drop those matching any of its games, then
    exclude the games not picked. Games installed later aren't excluded, so syncs add them."""
    exclude = [e for e in raw.get("exclude") or [] if not any(is_excluded([e], *g.setting_keys) for g in games)]
    picked_keys = {g.key for g in picked}
    exclude += [g.name for g in games if g.key not in picked_keys]
    raw["exclude"] = exclude


def apply_answers(raw, answers, detections):
    """Save answers into `raw` (config.json's contents), in place."""
    sources = raw.setdefault("sources", {})
    for source, answer in answers.items():
        sources[source] = answer != SKIP
        if isinstance(answer, list):
            set_picks(raw, detections[source].games, answer)


def _hint(sources):
    names = ", ".join(f"{LABELS[s]} ({_count(n)})" for s, n in sources)
    return names


def choose(detections, cfg, mode, raw=None):
    """Settle undecided launchers, asking if `mode` is ASK. Returns {source: True/False/None}.

    - ASK: new installs are asked about every launcher with games ("add all?", then
      all/pick/skip each); existing ones only about launchers they haven't decided on.
    - YES (`setup --yes`): every launcher asked about is turned on.
    - DRY_RUN: never asks; undecided launchers stay off for this run.
    - QUIET (no one to answer): undecided launchers stay off, with a hint.
    """
    raw = load_raw() if raw is None else raw
    states = {source: source_state(cfg, source) for source in SOURCES}
    undecided = [s for s in SOURCES if states[s] is None and detections[s].games]
    first_run = is_first_run(raw)
    xbox_unsaved = "xbox" not in (raw.get("sources") or {})
    candidates = (["xbox"] if first_run and xbox_unsaved and detections["xbox"].games else []) + undecided
    if not undecided:
        return states  # a new install with only Xbox games just gets them, as before

    counts = [(s, len(detections[s].games)) for s in undecided]
    if mode == DRY_RUN:
        print(f"Not set up yet: {_hint(counts)}. `xss sync` will ask whether to add their games.\n")
        return states
    if mode == QUIET:
        print(f"Not set up yet: {_hint(counts)}; their games aren't added. Run `xss sources` to choose, "
              "e.g. `xss sources epic --on`.\n")
        return states
    if mode == YES:
        answers = {source: ALL for source in candidates}
    else:
        answers = ask(candidates, detections, link_everything=first_run)
        print()
        if answers is None:
            print(f"No answer; {_hint(counts)} left off for now. Run `xss sources` to choose.\n")
            return states
    apply_answers(raw, answers, detections)
    save_raw(raw)
    for source, answer in answers.items():
        states[source] = answer != SKIP
    return states


# ---------------------------------------------------------------------------
# xss sources
# ---------------------------------------------------------------------------


def _state_text(state):
    return {True: "on", False: "off", None: "not decided"}[state]


def run_sources(name=None, on=None, pick=False) -> int:
    cfg = load_config()
    if name is None:
        print("Launchers (turn one on or off with `xss sources <launcher> --on|--off`):")
        for source in SOURCES:
            d = detect(source, cfg)
            excluded = sum(is_excluded(cfg["exclude"], *g.setting_keys) for g in d.games)
            detail = f"{_count(len(d.games))}" + (f", {excluded} excluded" if excluded else "")
            found = "installed" if d.found else "not found"
            if source == "xbox":
                found = "install folders found" if d.found else "no install folders"
            print(f"  {source:<10} {LABELS[source]:<11} {found:<22} {_state_text(source_state(cfg, source)):<12} "
                  f"{detail}")
            if d.error and d.found:
                print(f"             ! {d.error}")
        return 0

    source = ALIASES.get(name.lower())
    if not source:
        print(f"Unknown launcher {name!r}; use one of: {', '.join(SOURCES)}.")
        return 1
    raw = load_raw()
    if pick:
        detection = detect(source, cfg)
        if not detection.games:
            print(f"No installed {LABELS[source]} games found.")
            return 1
        print(f"Choose which {LABELS[source]} games to add:")
        picked = pick_games(detection.games)
        if picked is None:
            print("No answer; nothing changed.")
            return 1
        apply_answers(raw, {source: picked}, {source: detection})
        save_raw(raw)
        print(f"Saved: {len(picked)} of {_count(len(detection.games))} added; the rest are in `exclude`.")
    elif on is not None:
        raw.setdefault("sources", {})[source] = on
        save_raw(raw)
        print(f"{LABELS[source]} is turned {'on' if on else 'off'}.")
    else:
        print(f"{LABELS[source]}: {_state_text(source_state(cfg, source))}")
        return 0
    print("Run `xss sync` to update Steam.")
    return 0
