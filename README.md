# XboxSteamSync

Adds games installed through the Xbox app to Steam as non-Steam shortcuts, in a collection
called **Xbox**, and makes Steam Input work in them. Run it again after installing or
removing games: new games are added and uninstalled ones are removed. It only ever touches
shortcuts it created.

Steam can't apply Steam Input to games started through the Xbox app, so each shortcut starts
[SISR](https://github.com/Alia5/SISR) (Steam Input System Redirector), which turns your
controller's Steam Input layout into a virtual Xbox controller that the game sees.

## Install

You need Windows 10 or 11 (64-bit), Steam (signed in at least once), and the Xbox app with at
least one game installed. You don't need Python.

Open PowerShell and run:

```powershell
irm https://github.com/OWNER/XboxSteamSync/releases/latest/download/install.ps1 | iex
```

This installs XboxSteamSync to `%LOCALAPPDATA%\XboxSteamSync\app`, adds it to your PATH as
`xss`, and runs `xss setup`, which:

1. Installs SISR, VIIPER and the USBIP driver if they're missing (see below). Windows asks for
   admin rights for the driver.
2. Asks you to restart Windows if the driver was just installed. Setup carries on by itself after
   the restart.
3. Adds your Xbox games to Steam. Steam is closed and reopened if it was running.

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
xss doctor           # check Steam, SISR, VIIPER, the USBIP driver and the Xbox folders
xss uninstall        # remove everything XboxSteamSync added to Steam
```

Each sync that changes something first backs up the Steam files it edits to
`%LOCALAPPDATA%\XboxSteamSync\backups\<timestamp>\`.

## Update

Run the install command again. It won't update while a game started from the Xbox collection
is running, because that keeps the install folder in use.

## Uninstall

```powershell
xss uninstall --purge
```

This removes the shortcuts, their artwork, the collection entries and the Steam Input settings
from Steam, then deletes `%LOCALAPPDATA%\XboxSteamSync` (the program, settings, logs and
backups). Without `--purge` only the Steam changes are undone.

SISR, VIIPER and the USBIP driver stay installed. To remove them, run
`%LOCALAPPDATA%\SISR\uninstall.ps1` or uninstall SISR from Windows' installed apps.

## Settings

Optional. Create `%LOCALAPPDATA%\XboxSteamSync\config.json` (see
[config.example.json](config.example.json)):

| Key | Purpose |
| --- | --- |
| `xbox_games_dirs` | Xbox install folders. Empty (the default) finds them on every drive from its `.GamingRoot` file. |
| `collection_name` | Steam collection name (default `Xbox`) |
| `steamgriddb_api_key` | Optional [SteamGridDB](https://www.steamgriddb.com/profile/preferences/api) key for better artwork (or set `STEAMGRIDDB_API_KEY`) |
| `exclude` | Folder names, package names or display names to skip |
| `steamgriddb_ids` | `{ "<folder name>": <SteamGridDB game id> }` when the name search picks the wrong game |
| `sisr_path` / `sisr_args` | SISR executable and its arguments (default `%LOCALAPPDATA%\SISR\SISR.exe`, `--window.show --window.fullscreen`). Set `sisr_path` to `""` to launch games without SISR. |

SISR settings are read each time a game starts, so changing them doesn't need a sync. Other
changes take effect on the next `xss sync`.

## How it works

- **Finding games:** each drive's `.GamingRoot` file lists its Xbox install folders. A folder
  is a game when `Content\MicrosoftGame.config` has an `<ExecutableList>`. DLC has none and
  declares a `MainPackageDependency`. Folders like `GameSave` have no config.
- **Launching:** shortcuts run `xss-launch.exe`, which:
  1. starts SISR under the shortcut's Steam Input layout;
  2. launches the game through `shell:AppsFolder\<AppID>`;
  3. waits while the game runs (watching its install folders and executable names), so Steam
     keeps the layout active;
  4. closes SISR when the game exits, or if the launcher itself is closed.

  Steam Input is also turned on for each shortcut in `localconfig.vdf`. Logs are in
  `%LOCALAPPDATA%\XboxSteamSync\logs` (`launcher.log`, `sisr.log`).
- **Collection:** written to `userdata\<id>\config\cloudstorage\cloud-storage-namespace-1.json`
  and flagged as modified so Steam uploads it to the cloud. Games you add to the collection
  yourself are kept.
- **Artwork:** from SteamGridDB when a key is set, otherwise made from the splash and logo
  images that ship with each Xbox game. Local art is replaced with SteamGridDB art once a key
  is added. Art you set yourself in Steam is left alone.

## Known issues

- **Minecraft: Java Edition:** the Xbox entry opens the Minecraft Launcher, and Java runs from
  outside the install folder, so the launcher may think the game has exited and close SISR
  mid-game. Check `logs\launcher.log`.
- **Mouse-and-keyboard games** still get a virtual Xbox controller; you may want a different
  Steam Input layout for those.

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
GitHub Actions and attaches it and `install.ps1` to a GitHub Release.

To move to a new SISR release, test it, then update `INSTALL_VERSION`, `INSTALL_SCRIPT_SHA256`
and `TESTED_VERSIONS` in `xboxsteamsync/sisr.py`.

### Upgrading from the script version

`xss sync` moves shortcuts that point at the old `launcher.py` over to `xss-launch.exe`,
keeping their Steam app ids, so artwork, collection membership and playtime carry over.
`config.json` and `art_state.json` are copied from the old folder.

## License

XboxSteamSync is released into the public domain under [the Unlicense](LICENSE).

It doesn't include SISR, VIIPER or usbip-win2; they're downloaded from their own projects at
setup time under their own licenses.
