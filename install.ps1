# Install or update XboxSteamSync, then run its setup.
#
#   irm https://github.com/connorgoggans/XboxSteamSync/releases/latest/download/install.ps1 | iex
#
# Or, from a downloaded release zip:
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Zip .\XboxSteamSync-windows-x64.zip

param(
    [string]$Zip = "",
    [switch]$NoSetup,      # just install; don't run `xss setup`
    [switch]$NoPath,       # don't add the install folder to the user's PATH
    [int]$WaitForPid = 0   # wait for this process to exit first (xss.exe updating itself)
)

function Install-XboxSteamSync {
    param([string]$Zip, [switch]$NoSetup, [switch]$NoPath, [int]$WaitForPid)

    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much slower with the progress bar

    $repo = "connorgoggans/XboxSteamSync"
    $asset = "XboxSteamSync-windows-x64.zip"
    $root = Join-Path $env:LOCALAPPDATA "XboxSteamSync"
    $app = Join-Path $root "app"

    if ($WaitForPid) {
        Write-Host "$(Get-Date -Format s) Waiting for process $WaitForPid to exit before updating"
        $proc = Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.WaitForExit(3600 * 1000)) {
            Write-Host "Process $WaitForPid is still running after an hour; not updating." -ForegroundColor Red
            return
        }
    }

    # xss-launch.exe runs for as long as a game it started is running, and keeps the
    # install folder locked meanwhile.
    $running = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith("$app\", [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        $names = ($running | ForEach-Object { $_.Name } | Sort-Object -Unique) -join ", "
        Write-Host "XboxSteamSync is in use ($names). Close any game started from Steam's Xbox collection and try again." -ForegroundColor Red
        return
    }

    $temp = Join-Path ([IO.Path]::GetTempPath()) ("xss-install-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temp | Out-Null
    try {
        if (-not $Zip) {
            $Zip = Join-Path $temp $asset
            $url = "https://github.com/$repo/releases/latest/download/$asset"
            Write-Host "Downloading $url"
            Invoke-WebRequest -Uri $url -OutFile $Zip -UseBasicParsing
        }
        $extract = Join-Path $temp "extract"
        Expand-Archive -Path $Zip -DestinationPath $extract
        $exe = Get-ChildItem -Path $extract -Filter xss.exe -Recurse | Select-Object -First 1
        if (-not $exe) { throw "xss.exe not found in $Zip" }

        New-Item -ItemType Directory -Path $root -Force | Out-Null
        $old = "$app.old"
        if (Test-Path $old) { Remove-Item -Recurse -Force $old }
        if (Test-Path $app) { Rename-Item -Path $app -NewName (Split-Path $old -Leaf) }
        try {
            Move-Item -Path $exe.Directory.FullName -Destination $app
        }
        catch {
            if (Test-Path $old) { Rename-Item -Path $old -NewName (Split-Path $app -Leaf) }
            throw
        }
        if (Test-Path $old) { Remove-Item -Recurse -Force $old }
        Get-ChildItem -Path $app -Recurse -File | Unblock-File
        Write-Host "Installed to $app" -ForegroundColor Green

        if (-not $NoPath) {
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $parts = @($userPath -split ";" | Where-Object { $_ })
            if ($parts -notcontains $app) {
                [Environment]::SetEnvironmentVariable("Path", (($parts + $app) -join ";"), "User")
                Write-Host "Added $app to your PATH (open a new terminal to use 'xss')."
            }
        }
    }
    finally {
        Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
    }

    if (-not $NoSetup) {
        Write-Host ""
        & (Join-Path $app "xss.exe") setup
    }
}

Install-XboxSteamSync -Zip $Zip -NoSetup:$NoSetup -NoPath:$NoPath -WaitForPid $WaitForPid
