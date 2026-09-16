<#
.SYNOPSIS
    GPD-3303S Control — one-line installer for Windows.

.DESCRIPTION
    irm https://raw.githubusercontent.com/nomad9021/GPD-3303S-PSU-Control/main/install.ps1 | iex

    Installs into an isolated environment (uv, pipx, or a private venv),
    puts a `gpd3303s` launcher on PATH, and records the install method so the
    app's built-in updater can upgrade itself later.
#>

[CmdletBinding()]
param(
    [string] $Repo = $(if ($env:GPD3303S_REPO) { $env:GPD3303S_REPO } else { 'nomad9021/GPD-3303S-PSU-Control' }),
    [string] $Ref  = $env:GPD3303S_REF
)

$ErrorActionPreference = 'Stop'
$Package   = 'gpd3303s-control'
$MarkerDir = Join-Path $env:LOCALAPPDATA $Package
$VenvDir   = Join-Path $MarkerDir 'venv'
$BinDir    = Join-Path $env:LOCALAPPDATA 'Programs\GPD3303S'

function Write-Info { param($m) Write-Host "==> $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "warn $m" -ForegroundColor Yellow }
function Test-Command { param($n) [bool](Get-Command $n -ErrorAction SilentlyContinue) }

function Resolve-Ref {
    if ($Ref) { return $Ref }
    # Prefer the newest release; fall back to the default branch if there is none.
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
                                     -Headers @{ 'User-Agent' = 'gpd3303s-installer' } -TimeoutSec 15
        if ($release.tag_name) { return $release.tag_name }
    } catch {
        Write-Warn "Could not read the latest release; installing from the default branch."
    }
    return 'HEAD'
}

function Set-InstallMethod {
    param([string] $Method)
    New-Item -ItemType Directory -Force -Path $MarkerDir | Out-Null
    Set-Content -Path (Join-Path $MarkerDir 'install-method') -Value $Method -NoNewline -Encoding utf8
}

function Add-ToUserPath {
    param([string] $Directory)
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($current -split ';' -notcontains $Directory) {
        [Environment]::SetEnvironmentVariable('Path', "$Directory;$current", 'User')
        Write-Warn "Added $Directory to your PATH. Open a new terminal for it to take effect."
    }
    $env:Path = "$Directory;$env:Path"
}

function Find-Python {
    foreach ($candidate in @('python3.13', 'python3.12', 'python3.11', 'python3.10', 'python', 'python3')) {
        if (Test-Command $candidate) {
            try {
                & $candidate -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) { return $candidate }
            } catch { }
        }
    }
    # The py launcher is the usual way Python lands on Windows.
    if (Test-Command 'py') {
        try {
            & py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return 'py -3' }
        } catch { }
    }
    return $null
}

# ---------------------------------------------------------------------------

if (-not (Test-Command 'git')) {
    throw "git is required to install from GitHub. Install Git for Windows and re-run."
}

$resolved = Resolve-Ref
$spec = "git+https://github.com/$Repo@$resolved"

Write-Host ''
Write-Host 'GPD-3303S Control' -ForegroundColor White -NoNewline
Write-Host " — installing $resolved"
Write-Host ''

if (Test-Command 'uv') {
    Write-Info 'Installing with uv'
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    # uv puts tool launchers in its own bin directory. Point that at the
    # location this script promises, so $BinDir is authoritative either way.
    $env:UV_TOOL_BIN_DIR = $BinDir
    uv tool install --force $spec
    if ($LASTEXITCODE -ne 0) { throw 'uv tool install failed.' }
    Set-InstallMethod 'uv-tool'
    Add-ToUserPath $BinDir
}
elseif (Test-Command 'pipx') {
    Write-Info 'Installing with pipx'
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $env:PIPX_BIN_DIR = $BinDir
    pipx install --force $spec
    if ($LASTEXITCODE -ne 0) { throw 'pipx install failed.' }
    Set-InstallMethod 'pipx'
    Add-ToUserPath $BinDir
}
else {
    Write-Info 'Installing into a private virtual environment'
    $python = Find-Python
    if (-not $python) {
        throw "Python 3.9 or newer is required. Install it from https://python.org and re-run."
    }

    $pythonArgs = $python -split ' '
    & $pythonArgs[0] @($pythonArgs[1..($pythonArgs.Length - 1)]) -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }

    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet --upgrade $spec
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
    Set-InstallMethod 'venv'

    # Shim so `gpd3303s` works without activating the venv.
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $shim = Join-Path $BinDir 'gpd3303s.cmd'
    Set-Content -Path $shim -Encoding ascii -Value @"
@echo off
"$(Join-Path $VenvDir 'Scripts\gpd3303s.exe')" %*
"@
    Add-ToUserPath $BinDir
}

# Every path above must leave a working launcher at the advertised location;
# catching that here turns a silent mis-install into a clear failure.
$launcher = Get-ChildItem -Path $BinDir -Filter 'gpd3303s.*' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $launcher) {
    throw "Installed, but no launcher was created in $BinDir."
}

Write-Host ''
Write-Host 'Installed.' -ForegroundColor Green -NoNewline
Write-Host ' Start it with:'
Write-Host ''
Write-Host '    gpd3303s' -ForegroundColor White
Write-Host ''
Write-Host 'No hardware handy? Try the built-in simulator:'
Write-Host ''
Write-Host '    gpd3303s --simulate'
Write-Host ''
Write-Host 'On Windows the supply appears as a COM port once the GW Instek USB driver is installed.'
Write-Host ''
