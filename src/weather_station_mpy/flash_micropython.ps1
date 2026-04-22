param(
    [string]$Port = "COM13",
    [string]$FirmwarePath = "",
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($FirmwarePath)) {
    throw "FirmwarePath is required. Download an ESP32_GENERIC .bin from micropython.org and pass -FirmwarePath."
}

if (-not (Test-Path $FirmwarePath)) {
    throw "Firmware file not found: $FirmwarePath"
}

Write-Host "[FLASH] Using firmware: $FirmwarePath"
Write-Host "[FLASH] Erasing flash on $Port"
& $PythonExe -m esptool --chip esp32 --port $Port erase_flash

Write-Host "[FLASH] Writing MicroPython firmware"
& $PythonExe -m esptool --chip esp32 --port $Port --baud 460800 write_flash -z 0x1000 $FirmwarePath

Write-Host "[FLASH] Done"
Write-Host "[FLASH] Next: run deploy.ps1 to copy application files"
