# Install (or repair / update) the terminal on Windows 10/11. Safe to re-run. Normally started by Install.cmd.
#   Install.cmd               main V5 backend (default) from config\requirements-v5.lock.txt
#   Install.cmd --with-v4     also install the optional legacy V4 backend (FMP, Finviz, EconDB, Intrinio)
#   Install.cmd --upgrade     latest compatible releases, then rewrites the lock files
# This script only makes sure uv is present (installing it for the current user if needed);
# the install itself is scripts\launcher.py, shared with macOS/Linux install.sh.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in "$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe") { if (Test-Path $p) { return $p } }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host "uv is not installed. uv is a small, free tool (by Astral) that downloads Python and this project's packages." -ForegroundColor Cyan
    Write-Host "Installing it for your Windows user only (no administrator rights needed) from https://astral.sh/uv ..." -ForegroundColor Cyan
    try {
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
    $uv = Find-Uv
    if (-not $uv) {
        Write-Host "Could not install uv automatically. Install it from https://docs.astral.sh/uv/getting-started/installation/ then run this again." -ForegroundColor Red
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
# uv finds (or downloads) Python 3.12 just to run the installer; the environments themselves are created by the launcher.
& $uv run --no-project --python 3.12 "$root\scripts\launcher.py" @launcherArgs
exit $LASTEXITCODE
