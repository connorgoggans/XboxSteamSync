"""Running Windows PowerShell from Python."""

import os
from pathlib import Path

_PSHOME = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
POWERSHELL = str(_PSHOME / "powershell.exe")


def powershell_env():
    """Our environment with PSModulePath pointing only at Windows PowerShell's modules.

    When PowerShell 7 is installed (or we're started from it), PSModulePath can list
    PowerShell 7's module folders first, and Windows PowerShell then fails to load
    built-in modules ("the module could not be loaded"), e.g. for Get-AuthenticodeSignature.
    """
    env = dict(os.environ)
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    env["PSModulePath"] = f"{_PSHOME / 'Modules'};{Path(program_files) / 'WindowsPowerShell' / 'Modules'}"
    return env
