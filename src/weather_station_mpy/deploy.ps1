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

    # Compile main.py to _main.mpy (pre-compiled bytecode avoids heap fragmentation at boot).
    $mpyCross = Join-Path (Split-Path $scriptDir -Parent) "..\\.venv\\Scripts\\mpy-cross"
    if (-not (Test-Path ($mpyCross + ".exe")) -and -not (Test-Path $mpyCross)) {
        # Try one level up (repo root .venv)
        $mpyCross = Join-Path (Split-Path (Split-Path $scriptDir -Parent) -Parent) ".venv\\Scripts\\mpy-cross"
    }
    if (-not (Test-Path ($mpyCross + ".exe")) -and -not (Test-Path $mpyCross)) {
        $mpyCross = "mpy-cross"
    }
    Write-Host "[DEPLOY] Compiling main.py -> _main.mpy"
    & $mpyCross -march=xtensa main.py -o _main.mpy
    if ($LASTEXITCODE -ne 0) {
        throw "mpy-cross compilation failed"
    }

    # Compile all .py files in package directories to .mpy so the deployed
    # bytecode always reflects the latest Python source changes.
    # MicroPython prefers .mpy over .py; stale .mpy files would hide source fixes.
    $packageDirs = @("config", "services", "ui")
    foreach ($pkgDir in $packageDirs) {
        Get-ChildItem -Path $pkgDir -Filter "*.py" -Recurse | ForEach-Object {
            $srcPy = $_.FullName
            $outMpy = [System.IO.Path]::ChangeExtension($srcPy, ".mpy")
            $label = $pkgDir + "/" + $_.Name
            Write-Host "[DEPLOY] Compiling $label"
            & $mpyCross -march=xtensa $srcPy -o $outMpy
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "[DEPLOY] mpy-cross failed for $label — .py will be used as fallback"
            }
        }
    }

    # Deploy boot.py.
    Write-Host "[DEPLOY] copy boot.py"
    Invoke-Mpremote connect $Port soft-reset fs cp "boot.py" ":/boot.py"

    # Deploy pre-compiled app bytecode.
    Write-Host "[DEPLOY] copy _main.mpy"
    Invoke-Mpremote connect $Port soft-reset fs cp "_main.mpy" ":/_main.mpy"

        # Deploy the checked-in ASCII stub as main.py. Keep it BOM-free so
        # MicroPython can execute it directly at boot.
        Write-Host "[DEPLOY] copy main_stub.py -> main.py"
        Invoke-Mpremote connect $Port soft-reset fs cp "main_stub.py" ":/main.py"

    # Other top-level source files.
    $topFiles = @(
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
