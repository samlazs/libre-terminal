# Install (or repair / update) the terminal on Windows 10/11. Safe to re-run. Normally started by Install.cmd.
#   Install.cmd               main V5 backend (default) from config\requirements-v5.lock.txt
#   Install.cmd --with-v4     also install the optional legacy V4 backend (FMP, Finviz, EconDB, Intrinio)
#   Install.cmd --upgrade     latest compatible releases, then rewrites the lock files
# This script only makes sure uv is present (installing it for the current user if needed);
# the install itself is scripts\launcher.py, shared with macOS/Linux install.sh.
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
try {
    Get-ChildItem -LiteralPath $root -Filter "*.cmd" -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
    Unblock-File -LiteralPath $PSCommandPath -ErrorAction SilentlyContinue
} catch {}

# Windows refuses file paths over 260 characters unless long paths are turned on. The deepest file the install writes
# is $DeepestVenvPath characters below this folder (measured on the 1.0 lock file: .venv-v5\Lib\site-packages\
# openbb_government_us\usda\utils\__pycache__\ers_..._united_states.cpython-312.pyc; scripts\tests\test_install_guard.py
# keeps it in step). Stop here, before anything is downloaded, instead of failing deep inside the package install.
$DeepestVenvPath = 171
$MaxPath = 255
$longPaths = $false
try { $longPaths = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -ErrorAction Stop).LongPathsEnabled -eq 1 } catch {}
if (($root.Length + 1 + $DeepestVenvPath) -gt $MaxPath -and -not $longPaths) {
    $fit = $MaxPath - 1 - $DeepestVenvPath
    Write-Host ""
    Write-Host "This folder's path is too long to install the terminal here ($($root.Length) characters; at most $fit fit):" -ForegroundColor Red
    Write-Host "  $root" -ForegroundColor Red
    Write-Host "Windows limits file paths to 260 characters, and the install writes files up to $DeepestVenvPath characters deep inside this folder."
    Write-Host "Move the Libre-Terminal folder to a short path such as C:\Libre-Terminal and run Install.cmd again." -ForegroundColor Yellow
    Write-Host "Nothing was downloaded or changed."
    exit 1
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in "$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe", "$env:LOCALAPPDATA\Programs\uv\uv.exe") { if (Test-Path $p) { return $p } }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host "First, getting the free download helper 'uv' (by Astral), which fetches Python and the data packages." -ForegroundColor Cyan
    Write-Host "It is installed for your Windows user only; no administrator rights needed. Source: https://astral.sh/uv" -ForegroundColor Cyan
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
    $uv = Find-Uv
    if (-not $uv) {
        Write-Host ""
        Write-Host "Could not download the helper 'uv' from https://astral.sh." -ForegroundColor Red
        Write-Host "This is almost always the network: a company proxy or firewall, a VPN, or antivirus blocking the download." -ForegroundColor Yellow
        Write-Host "Try again on another network (a home connection or phone hotspot), or ask your IT team to allow astral.sh, github.com and pypi.org." -ForegroundColor Yellow
        Write-Host "Nothing was installed in this folder."
        exit 1
    }
}

# Options pass through to the launcher; the older -WithV4 / -SkipV4 / -Upgrade spellings still work.
$launcherArgs = @("install")
foreach ($a in $args) {
    switch ($a) {
        "-WithV4" { $launcherArgs += "--with-v4" }
        "-SkipV4" { $launcherArgs += "--skip-v4" }
        "-Upgrade" { $launcherArgs += "--upgrade" }
        default { $launcherArgs += $a }
    }
}
# OneDrive (and similar sync folders) upload every installed file and can lock files mid-install: warn, but carry on.
$syncRoots = @($env:OneDrive, $env:OneDriveCommercial, $env:OneDriveConsumer) | Where-Object { $_ }
if ($syncRoots | Where-Object { $root.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }) {
    Write-Host ""
    Write-Host "Note: this folder is inside OneDrive, which will try to upload about 1 GB of program files and can block the install." -ForegroundColor Yellow
    Write-Host "If the install fails or the computer slows down, move the Libre-Terminal folder to C:\Libre-Terminal and run Install.cmd again." -ForegroundColor Yellow
    Write-Host ""
}

# uv finds (or downloads) Python 3.12 just to run the installer; the environments themselves are created by the launcher.
& $uv run --no-project --python 3.12 "$root\scripts\launcher.py" @launcherArgs
exit $LASTEXITCODE
