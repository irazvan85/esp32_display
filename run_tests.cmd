@echo off
setlocal

cd /d "%~dp0"

echo ============================================================
echo  Installing / verifying test dependencies
echo ============================================================
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] pip install failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Running host tests
echo ============================================================
python -m pytest src\weather_station_mpy\tests ^
    --ignore=src\weather_station_mpy\tests\device ^
    --ignore=src\weather_station_mpy\tests\hil ^
    -v %*

if errorlevel 1 (
    echo.
    echo [FAIL] One or more tests failed.
    exit /b 1
) else (
    echo.
    echo [PASS] All tests passed.
    exit /b 0
)
