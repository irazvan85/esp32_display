<#
.SYNOPSIS
    Run HIL tests against the ESP32 over UART/mpremote.

.DESCRIPTION
    Convenience wrapper around tests/hil/hil_runner.py.
    Runs from src/weather_station_mpy/ or from the repo root.

.PARAMETER Port
    Serial port for the ESP32 (default: COM13)

.PARAMETER Suite
    hardware | network | app | boot_clean | weather_visible | menu_visible | all  (default: all)

.PARAMETER Timeout
    Seconds to wait per suite before declaring a timeout (default: 90)

.PARAMETER Verbose
    Print full UART log even when tests pass

.EXAMPLE
    .\tests\hil\run_hil.ps1
    .\tests\hil\run_hil.ps1 -Suite hardware -Verbose
    .\tests\hil\run_hil.ps1 -Suite network -Port COM14

.NOTES
    Prerequisites:
      pip install mpremote
    The full app must be deployed to the device before running network or app suites:
      .\deploy.ps1 -Port COM13
#>

param(
    [string]$Port    = "COM13",
    [ValidateSet("hardware","network","app","boot_clean","weather_visible","menu_visible","all")]
    [string]$Suite   = "all",
    [int]   $Timeout = 90,
    [switch]$Verbose
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── locate hil_runner.py relative to this script ──────────────────────────────
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunnerPath = Join-Path $ScriptDir "hil_runner.py"

if (-not (Test-Path $RunnerPath)) {
    Write-Error "Cannot find hil_runner.py at: $RunnerPath"
    exit 1
}

# ── check mpremote is available ───────────────────────────────────────────────
$mpremote = Get-Command mpremote -ErrorAction SilentlyContinue
if ($null -eq $mpremote) {
    Write-Error "mpremote not found. Install it: pip install mpremote"
    exit 1
}

# ── build argument list ────────────────────────────────────────────────────────
$args_list = @(
    $RunnerPath,
    "--port",    $Port,
    "--suite",   $Suite,
    "--timeout", $Timeout
)
if ($Verbose) { $args_list += "--verbose" }

# ── run ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  ESP32 HIL Tests  |  Port: $Port  |  Suite: $Suite" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan

python @args_list
$exit_code = $LASTEXITCODE

Write-Host ""
if ($exit_code -eq 0) {
    Write-Host "HIL result: PASS" -ForegroundColor Green
} else {
    Write-Host "HIL result: FAIL (exit $exit_code)" -ForegroundColor Red
}

exit $exit_code
