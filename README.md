# XboxSteamSync

Adds games installed through the Xbox app (`C:\XboxGames`) to Steam as non-Steam shortcuts
in a collection called **Xbox**. Re-running removes shortcuts for games that are no longer
installed. Only shortcuts this tool created are ever touched.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.json config.json   # optional, edit as needed
```

## Usage

```powershell
.\.venv\Scripts\python.exe sync.py --dry-run   # preview
.\.venv\Scripts\python.exe sync.py             # apply (closes and restarts Steam if it's running)
```

Every run backs up the Steam files it modifies to `backups\<timestamp>\`.

## How it works

- **Games vs DLC:** a folder is a game when `Content\MicrosoftGame.config` has an
  `<ExecutableList>`. DLC has none and declares a `MainPackageDependency`. Folders like
  `GameSave` have no config.
- **Launching / Steam Input:** Steam can't inject Steam Input into a game started through
  the Xbox app, so shortcuts run `pythonw launcher.py`, which:
  1. starts [SISR](https://github.com/Alia5/SISR) (`--window.show --window.fullscreen`) under the shortcut's
     Steam Input config, exposing it as a virtual Xbox 360 controller;
  2. launches the game through `shell:AppsFolder\<AppID>`;
  3. stays alive while the game runs (matching both `C:\XboxGames\...` and the
     `WindowsApps` junction path, plus the exe names), so Steam keeps the config active;
  4. closes SISR when the game exits.

  Steam Input is also forced on per shortcut in `localconfig.vdf`. The launcher logs to
  `logs\launcher.log` and SISR to `logs\sisr.log`. Set `sisr_path` to `""` to launch without SISR.
- **Collection:** written to `userdata\<id>\config\cloudstorage\cloud-storage-namespace-1.json`
  and flagged as modified so Steam uploads it to the cloud.
- **Artwork:** pulled from SteamGridDB when `steamgriddb_api_key` is set (or the
  `STEAMGRIDDB_API_KEY` env var). Otherwise it's generated from the splash/logo images that ship with
  each Xbox game. Locally generated art is upgraded to SteamGridDB art once a key is added.
  Art you set yourself in Steam is left alone.

## Config (`config.json`)

| Key | Purpose |
| --- | --- |
| `xbox_games_dir` | Xbox install root (default `C:\XboxGames`) |
| `collection_name` | Steam collection name (default `Xbox`) |
| `steamgriddb_api_key` | Optional SteamGridDB key |
| `exclude` | Folder names, package names or display names to skip |
| `sisr_path` / `sisr_args` | SISR executable and its arguments (default `%LOCALAPPDATA%\SISR\SISR.exe`, `--window.show --window.fullscreen`) |
| `steamgriddb_ids` | `{ "<folder name>": <SteamGridDB game id> }` when the name search picks the wrong game |
