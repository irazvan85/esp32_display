param(
    [string]$Port = "COM13",
    [switch]$NoReset
)

$ErrorActionPreference = "Stop"

$script:MpremoteRunner = $null
$script:MpremoteModuleMode = $false

if (Get-Command mpremote -ErrorAction SilentlyContinue) {
    $script:MpremoteRunner = "mpremote"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $script:MpremoteRunner = "py"
    $script:MpremoteModuleMode = $true
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $script:MpremoteRunner = "python"
    $script:MpremoteModuleMode = $true
} else {
    throw "Required command not found: mpremote (or python/py with mpremote module)."
}

function Invoke-Mpremote {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )

    if ($script:MpremoteModuleMode) {
        & $script:MpremoteRunner -m mpremote @Args
    } else {
        & $script:MpremoteRunner @Args
    }

    if ($LASTEXITCODE -ne 0) {
        throw "mpremote command failed with exit code $LASTEXITCODE"
    }
}

try {
    if ($script:MpremoteModuleMode) {
        & $script:MpremoteRunner -m mpremote --help *> $null
    } else {
        & $script:MpremoteRunner --help *> $null
    }
} catch {
    throw "Unable to execute mpremote. Install with: python -m pip install mpremote"
}

if ($LASTEXITCODE -ne 0) {
    throw "Unable to execute mpremote. Install with: python -m pip install mpremote"
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $scriptDir

try {
    Write-Host "[DEPLOY] Syncing weather_station_mpy to $Port"

    # Top-level files loaded by main.py imports.
    $topFiles = @(
        "boot.py",
        "main.py",
        "board.py",
        "app_state.py",
        "compat.py",
        "st7789.py"
    )

    foreach ($file in $topFiles) {
        Write-Host "[DEPLOY] copy $file"
        Invoke-Mpremote connect $Port soft-reset fs cp $file (":/" + $file)
    }

    if (Test-Path "config.json") {
        Write-Host "[DEPLOY] copy config.json"
        Invoke-Mpremote connect $Port soft-reset fs cp "config.json" ":/config.json"
    }

    # Package folders.
    $dirs = @("config", "services", "ui")
    foreach ($dir in $dirs) {
        Write-Host "[DEPLOY] copy $dir/"
        Invoke-Mpremote connect $Port soft-reset fs cp -r $dir :/
    }

    Write-Host "[DEPLOY] Done"
    Write-Host "[DEPLOY] If this is first boot, edit :/config.json placeholders then reboot."

    if (-not $NoReset) {
        Write-Host "[DEPLOY] Resetting board"
        Invoke-Mpremote connect $Port reset
    }
}
finally {
    Pop-Location
}
