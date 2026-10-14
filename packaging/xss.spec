# PyInstaller build: one folder holding xss.exe (console) and xss-launch.exe (windowed),
# sharing one Python runtime. Build with: uv run pyinstaller packaging/xss.spec
# Output: dist/XboxSteamSync/

import os

HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)

cli = Analysis([os.path.join(HERE, "xss_entry.py")], pathex=[ROOT])
launch = Analysis([os.path.join(HERE, "xss_launch_entry.py")], pathex=[ROOT])

cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="xss", console=True,
              upx=False)
launch_exe = EXE(PYZ(launch.pure), launch.scripts, [], exclude_binaries=True, name="xss-launch",
                 console=False, upx=False)

COLLECT(cli_exe, cli.binaries, cli.datas,
        launch_exe, launch.binaries, launch.datas,
        name="XboxSteamSync", upx=False)
