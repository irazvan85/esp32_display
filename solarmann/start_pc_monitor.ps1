#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Starts the PC Metrics API server for the ESP32 weather station.

.PARAMETER Port
    TCP port to listen on. Default: 8765

.PARAMETER DiskPath
    Disk path to monitor for usage percentage. Default: C:\

.PARAMETER Host
    Bind address. Default: 0.0.0.0

.EXAMPLE
    .\start_pc_monitor.ps1
    .\start_pc_monitor.ps1 -Port 9000 -DiskPath D:\
#>
param(
    [int]    $Port     = 8765,
    [string] $DiskPath = "C:\",
    [string] $BindHost = "0.0.0.0"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiScript = Join-Path $ScriptDir "pc_metrics_api.py"

if (-not (Test-Path $ApiScript)) {
    Write-Host "[ERROR] Cannot find: $ApiScript" -ForegroundColor Red
    pause; exit 1
}

# --- Locate Python ---------------------------------------------------------
$PythonExe = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3") {
            $PythonExe = $candidate
            Write-Host "[info]  Found Python: $ver" -ForegroundColor Cyan
            break
        }
    } catch { }
}

if (-not $PythonExe) {
    Write-Host "[ERROR] Python 3 not found in PATH. Please install Python 3." -ForegroundColor Red
    pause; exit 1
}

# --- Ensure required packages -----------------------------------------------
$RequiredPackages = @("psutil")

# Try to install wmi on Windows (best-effort; not fatal if it fails)
if ($IsWindows -or ($env:OS -eq "Windows_NT")) {
    $RequiredPackages += "wmi"
}

foreach ($pkg in $RequiredPackages) {
    $installed = & $PythonExe -c "import $pkg" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[info]  Installing $pkg ..." -ForegroundColor Yellow
        & $PythonExe -m pip install $pkg --quiet
        if ($LASTEXITCODE -ne 0) {
            if ($pkg -eq "wmi") {
                Write-Host "[warn]  Could not install wmi — temperature will show N/A." -ForegroundColor Yellow
            } else {
                Write-Host "[ERROR] Failed to install required package: $pkg" -ForegroundColor Red
                pause; exit 1
            }
        } else {
            Write-Host "[info]  Installed $pkg." -ForegroundColor Green
        }
    }
}

# --- Start server -----------------------------------------------------------
Write-Host ""
Write-Host "=================================================" -ForegroundColor Green
Write-Host "  PC Metrics API" -ForegroundColor Green
Write-Host "  URL:  http://${BindHost}:${Port}/api/system/metrics" -ForegroundColor Green
Write-Host "  Disk: $DiskPath" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop." -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Green
Write-Host ""
Write-Host "[note] Temperature requires hardware sensor access." -ForegroundColor DarkCyan
Write-Host "       If it shows N/A, run LibreHardwareMonitor and" -ForegroundColor DarkCyan
Write-Host "       enable its remote web server on port 8085." -ForegroundColor DarkCyan
Write-Host ""

try {
    & $PythonExe $ApiScript --host $BindHost --port $Port --disk-path $DiskPath
} catch {
    Write-Host "[ERROR] $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "Server stopped." -ForegroundColor Yellow
if ($Host.Name -eq "ConsoleHost") { pause }
