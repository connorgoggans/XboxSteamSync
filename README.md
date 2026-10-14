# XboxSteamSync

Adds games installed through the Xbox app, the Epic Games Launcher and Battle.net to Steam as
non-Steam shortcuts, in one collection per launcher (**Xbox**, **Epic Games**, **Battle.net**),
and makes Steam Input work in them. Run it again after installing or removing games: new
games are added and uninstalled ones are removed. It only ever touches shortcuts it created.

A game is only removed once its files are gone. If a game is still on disk but can't be
detected (for example while the Xbox app repairs it, or when its drive is disconnected),
`xss sync` keeps its shortcut and says why. If no Xbox install folders are found at all, or a
launcher's list of installed games can't be read, none of that launcher's shortcuts are removed.

Steam can't apply Steam Input to games started through another launcher, so each shortcut
starts [SISR](https://github.com/Alia5/SISR) (Steam Input System Redirector), which turns your
controller's Steam Input layout into a virtual Xbox controller that the game sees.

## Install

You need Windows 10 or 11 (64-bit) and Steam (signed in at least once), plus at least one of
the Xbox app, the Epic Games Launcher or Battle.net, installed and signed in, with games
installed. You don't need Python.

Open PowerShell and run:

```powershell
irm https://github.com/connorgoggans/XboxSteamSync/releases/latest/download/install.ps1 | iex
```

This installs XboxSteamSync to `%LOCALAPPDATA%\XboxSteamSync\app`, adds it to your PATH as
`xss`, and runs `xss setup`, which:

1. Installs SISR, VIIPER and the USBIP driver if they're missing (see below). Windows asks for
   admin rights for the driver.
2. Asks you to restart Windows if the driver was just installed. Setup carries on by itself after
   the restart.
3. Asks which launchers' games to add (see [Launchers](#launchers)), then adds them to Steam.
   Steam is closed and reopened if it was running.

To install from a downloaded zip instead, unzip it and run
`powershell -ExecutionPolicy Bypass -File install.ps1 -Zip <path to the zip>`.

### "Windows protected your PC"

XboxSteamSync isn't code-signed, so if you run `xss.exe` from a downloaded zip, Windows
SmartScreen may block it. Click **More info**, then **Run anyway**. The install command above
doesn't trigger SmartScreen.

### What setup installs

XboxSteamSync doesn't bundle SISR or its dependencies. Setup downloads SISR's official
install script for a tested SISR version, checks the script's SHA-256 against the one stored in
XboxSteamSync, runs it, and then checks that `SISR.exe` is signed by its author. The script:

- installs SISR to `%LOCALAPPDATA%\SISR` and VIIPER to `%LOCALAPPDATA%\VIIPER`;
- installs the USBIP driver (usbip-win2), which needs admin rights and a restart;
- adds VIIPER to the Windows startup list;
- creates SISR shortcuts on the desktop and in the Start Menu;
- creates `.cef-enable-remote-debugging` in the Steam folder, which SISR uses to talk to Steam.
  Steam is restarted afterwards.

## Usage

```powershell
xss sync --dry-run   # show what would change
xss sync             # add/remove shortcuts (closes and reopens Steam only if something changed)
xss doctor           # check Steam, SISR, VIIPER, the USBIP driver and each launcher
xss sources          # which launchers' games are added; see below
xss game --list      # show each game's input mode
xss game "<name>" --input keyboard-mouse   # or controller; see below
xss update           # update XboxSteamSync now (it also updates itself; see below)
xss uninstall        # remove everything XboxSteamSync added to Steam
```

Each sync that changes something first backs up the Steam files it edits to
`%LOCALAPPDATA%\XboxSteamSync\backups\<timestamp>\`.

## Launchers

The first `xss setup` or `xss sync` lists the launchers it found and how many games each has,
then asks whether to add all of them. If you say no, it asks about each launcher:

- **all:** add all its games;
- **pick:** go through its games one by one; the ones you don't pick go into `exclude`;
- **skip:** don't add its games.

Games installed later are added by later syncs, also in a launcher you picked games from:
`exclude` only lists the games you said no to. After an update, you're only asked about
launchers you haven't decided on yet; Xbox games stay as they are.

When nobody can answer, nothing is asked: `xss sync --dry-run` says which launchers it would
ask about, `xss setup --yes` adds every launcher, and any other run without a terminal (such as
setup carrying on after a restart without one) leaves new launchers off until you choose.

```powershell
xss sources                  # each launcher: found or not, on or off, how many games
xss sources epic --off       # stop adding Epic games; the next sync removes them from Steam
xss sources battlenet --on   # add Battle.net games (launchers: xbox, epic, battlenet)
xss sources epic --pick      # choose Epic games one by one again
```

Run `xss sync` afterwards to update Steam. A launcher's collection is deleted when it's turned
off, unless you've put other games in it.

The launchers have to be installed and signed in, as they start the games:

- **Epic Games:** shortcuts start the game through the Epic Games Launcher without showing its
  window. Some games start their own launcher first; see [Known issues](#known-issues).
- **Battle.net:** shortcuts start Battle.net if it isn't running, wait for it, then ask it to
  start the game. That needs a launch code for each game. XboxSteamSync knows the codes of
  most Blizzard and Call of Duty games (only Diablo IV's has been tested); for any other game
  it opens Battle.net instead, and you press Play. You can set a game's code yourself:
  `"games": {"<name>": {"battlenet_code": "<code>"}}`.

## Mouse-and-keyboard games

By default every game gets a virtual Xbox controller from SISR. Games that only support mouse
and keyboard, or handle a controller badly, can use Steam Input's keyboard and mouse output
instead:

1. Run `xss game "<name>" --input keyboard-mouse`. The name can be the game's display name,
   install folder name, or Xbox package name / Epic app name / Battle.net product code, or
   part of one (`xss game mythology ...`). This doesn't need a sync or a Steam restart.
2. In Steam, open the game's shortcut, go to its controller settings, and pick a keyboard-and-mouse
   layout, such as **Keyboard (WASD) and Mouse**. XboxSteamSync can't choose the layout for
   you; Steam keeps that choice in its own cloud-synced settings.

The shortcut then starts the game without SISR, and Steam turns your controller into keyboard
and mouse input from that layout while the game runs. `xss game "<name>" --input controller`
switches back. `xss sync` and `xss doctor` show each game's mode.

## Update

XboxSteamSync updates itself. `xss sync`, `xss doctor` and `xss setup` check GitHub for a new
release at most once a day. When there is one, they download it, check it against the
release's SHA-256 checksum, and install it once the command finishes; the next run uses the new
version. Game launches never check, so they stay fast and work offline. If there's no network,
the check is skipped and the command carries on.

An update waits while a game started from one of XboxSteamSync's shortcuts is running, because
that keeps the install folder in use; it's installed on a later run instead.

```powershell
xss update           # update now
xss update --check   # only say whether a new version exists
```

To turn automatic updates off, set `"auto_update": false` in `config.json`. Running the install
command again also updates. Update attempts are logged to `logs\update.log`.

If an update changes what the Steam shortcuts contain, the next `xss sync` updates them, and
`xss doctor` reminds you to run it.

## Uninstall

```powershell
xss uninstall --purge
```

This removes the shortcuts (from every launcher), their artwork, the collections and the Steam
Input settings from Steam, then deletes `%LOCALAPPDATA%\XboxSteamSync` (the program, settings,
logs and backups). Without `--purge` only the Steam changes are undone.

SISR, VIIPER and the USBIP driver stay installed. To remove them, run
`%LOCALAPPDATA%\SISR\uninstall.ps1` or uninstall SISR from Windows' installed apps.

## Settings

Optional. Create `%LOCALAPPDATA%\XboxSteamSync\config.json` (see
[config.example.json](config.example.json)):

| Key | Purpose |
| --- | --- |
| `sources` | Which launchers' games are added, e.g. `{"xbox": true, "epic": true, "battlenet": false}`. Set by the first sync and `xss sources`; a launcher with no entry hasn't been decided yet (Xbox counts as on). |
| `xbox_games_dirs` | Xbox install folders. Empty (the default) finds them on every drive from its `.GamingRoot` file. |
| `collection_name` | Name of the Xbox games' Steam collection (default `Xbox`) |
| `collections` | Names of the other collections: `{"epic": "Epic Games", "battlenet": "Battle.net"}` (the defaults) |
| `steamgriddb_api_key` | Optional [SteamGridDB](https://www.steamgriddb.com/profile/preferences/api) key for better artwork (or set `STEAMGRIDDB_API_KEY`) |
| `exclude` | Games to skip, by display name, install folder name, or Xbox package name / Epic app name / Battle.net product code |
| `steamgriddb_ids` | `{ "<name>": <SteamGridDB game id> }` when the name search picks the wrong game; names as for `exclude` |
| `sisr_path` / `sisr_args` | SISR executable and its arguments (default `%LOCALAPPDATA%\SISR\SISR.exe`, `--window.show --window.fullscreen`). Set `sisr_path` to `""` to launch games without SISR. |
| `games` | Per-game settings, keyed by any name `exclude` accepts. `"input"` is `"controller"` (the default) or `"keyboard-mouse"` (see [Mouse-and-keyboard games](#mouse-and-keyboard-games)); `xss game` sets it for you. `"battlenet_code"` sets a Battle.net game's launch code (see [Launchers](#launchers)). |
| `auto_update` | Check for and install new versions once a day (default `true`; see [Update](#update)) |

SISR settings, input modes and Battle.net launch codes are read each time a game starts, so
changing them doesn't need a sync. Other changes take effect on the next `xss sync`.

For example:

```json
{
  "games": {
    "Age of Mythology- Retold Standard Edition": { "input": "keyboard-mouse" }
  }
}
```

## How it works

- **Finding games:**
  - **Xbox:** each drive's `.GamingRoot` file lists its Xbox install folders. A folder is a game
    when `Content\MicrosoftGame.config` has an `<ExecutableList>`. DLC has none and declares a
    `MainPackageDependency`. Folders like `GameSave` have no config.
  - **Epic Games:** the launcher's manifests (`C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests`,
    one per installed item). Add-ons, extras such as mod tools, and unfinished installs are
    skipped.
  - **Battle.net:** `C:\ProgramData\Battle.net\Agent\product.db` lists installed games and their
    folders; names and art come from `aggregate.json` next to it, or from Windows' list of
    installed programs.
- **Launching:** shortcuts run `xss-launch.exe`, which:
  1. starts SISR under the shortcut's Steam Input layout (not for `keyboard-mouse` games);
  2. starts the game: Xbox games through `shell:AppsFolder\<AppID>`, Epic games through the
     launcher's `com.epicgames.launcher://` link, Battle.net games with
     `Battle.net.exe --exec="launch <code>"`;
  3. waits while the game runs (watching its install folders and executable names), so Steam
     keeps the layout active. A program that only runs briefly, like a pre-launcher handing over
     to another launcher, doesn't count as the game ending;
  4. closes SISR when the game exits, or if the launcher itself is closed.

  Staying alive is also what keeps Steam Input active for `keyboard-mouse` games.

  Steam Input is also turned on for each shortcut in `localconfig.vdf`. Logs are in
  `%LOCALAPPDATA%\XboxSteamSync\logs` (`launcher.log`, `sisr.log`).
- **Collections:** written to `userdata\<id>\config\cloudstorage\cloud-storage-namespace-1.json`
  and flagged as modified so Steam uploads them to the cloud. Games you add to a collection
  yourself are kept.
- **Artwork:** from SteamGridDB when a key is set, otherwise made from the game's own images:
  the splash and logo images that ship with each Xbox game, the box art in the Epic Games
  Launcher's catalog cache, and Battle.net's key art. Generated art is replaced with SteamGridDB
  art once a key is added. Art you set yourself in Steam is left alone.

## Known issues

- **Cyberpunk 2077 (Epic)** starts CD Projekt's REDlauncher, which waits for you to press Play.
  The wrapper waits up to 5 minutes for the game to start.
- **Anti-cheat:** SISR doesn't inject anything into games, and its virtual controller looks like
  a USB device, so anti-cheat shouldn't mind. If a game's anti-cheat complains, set its input to
  `keyboard-mouse` (no SISR) and please report it.
- **Minecraft: Java Edition:** the Xbox entry opens the Minecraft Launcher, and Java runs from
  outside the install folder, so the launcher may think the game has exited and close SISR
  mid-game. Check `logs\launcher.log`.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run pytest
uv run xss sync --dry-run                      # shortcuts point at .venv\Scripts\xss-launch.exe
uv run pyinstaller --noconfirm packaging/xss.spec   # build to dist\XboxSteamSync
```

Set `XSS_HOME` to use a different data folder than `%LOCALAPPDATA%\XboxSteamSync`.

Pushing a `v*` tag that matches `__version__` in `xboxsteamsync/__init__.py` builds the zip on
GitHub Actions and attaches it, its `.sha256` file and `install.ps1` to a GitHub Release. The zip
also contains `install.ps1`; that's the copy `xss update` runs. Installed copies update to the
new release on their next daily check.

When a release changes what Steam shortcuts contain (launch options or target), set
`SHORTCUTS_CHANGED_IN` in `xboxsteamsync/update.py` to its version, so `xss doctor` tells users
whose shortcuts are older to run `xss sync`.

To move to a new SISR release, test it, then update `INSTALL_VERSION`, `INSTALL_SCRIPT_SHA256`
and `TESTED_VERSIONS` in `xboxsteamsync/sisr.py`.

## License

XboxSteamSync is released into the public domain under [the Unlicense](LICENSE).

It doesn't include SISR, VIIPER or usbip-win2; they're downloaded from their own projects at
setup time under their own licenses.
