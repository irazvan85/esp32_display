# AGENTS.md

> **Active branch**: `dev_micropython` — MicroPython firmware path is in active development. The Arduino path (`src/weather_station/`) remains the stable reference.

## Project Scope
This is a dual-firmware weather and solar monitoring station running on the ideaspark ESP32 1.14-inch TFT LCD board (ESP32-WROOM-32 + ST7789 + CH340). It fetches data from OpenWeatherMap, SolarMan Cloud, NTP, and a local PC metrics API and renders a multi-page UI on the 240×135 display.

## Repository Structure

| Path | Purpose |
|------|---------|
| `src/weather_station/` | Arduino/C++ firmware (stable, production-ready) — [README](src/weather_station/README.md) |
| `src/weather_station_mpy/` | MicroPython firmware (phase 1/2, async architecture) — [README](src/weather_station_mpy/README.md) |
| `solarmann/` | PC-side backend: PC metrics HTTP API (`pc_metrics_api.py`) and SolarMan token reference (`api.py`) |
| `doc/` | Board specs, pinout, Arduino IDE setup guides (source of truth for hardware details) |
| `docs/` | Extracted board images and supplementary documentation |

## Source of Truth for Hardware
- [Board overview and specs](doc/spec.md)
- [Board pinout diagram](doc/pinlayout.md)
- [Arduino setup steps 1-4](doc/arduino_ide.md)
- [Arduino setup steps 5-8](doc/arduino_ide_lib.md)
- [Arduino sample code](doc/arduino_ide_samplecode.md)
- [Short board description](doc/readme.txt)

## Firmware: Arduino (`src/weather_station/`)
- Arduino board target: ESP32 Dev Module
- Serial port: COM13 (USB-SERIAL CH340), 115200 baud
- Required libraries (via Arduino Library Manager):
  - Adafruit GFX Library
  - Adafruit ST7735 and ST7789 Library (≥ 1.10.3)
  - Adafruit BusIO (dependency)
  - ArduinoJson **6.x or 7.x** (v5.x API differs significantly)
- Credentials: copy `config.h.template` → `config.h` (gitignored), fill SSID/password/OWM key
- Display initialization: `Adafruit_ST7789(CS, DC, RST)` → `init(135, 240)` in `setup()`
- Flash: Arduino IDE → Sketch → Upload (Ctrl+U); monitor at 115200 baud
- See [src/weather_station/README.md](src/weather_station/README.md) for full setup steps

## Firmware: MicroPython (`src/weather_station_mpy/`)
- Runtime: MicroPython 1.28.0 (ESP32_GENERIC firmware)
- Async runtime: `uasyncio`; display driver: bundled `st7789.py` (must be deployed — not in firmware)
- Required font modules: `vga1_16x32`, `vga1_8x16`, `vga1_8x8` (bundled in firmware or deploy manually)

**Flash firmware (one-time):**
```powershell
.\src\weather_station_mpy\flash_micropython.ps1 -Port COM13 -FirmwarePath <path\ESP32_GENERIC.bin>
```

**Deploy app:**
```powershell
cd src\weather_station_mpy
.\deploy.ps1 -Port COM13          # copies all .py files + config/, services/, ui/ and soft-resets
```
> Use `mpremote connect COM13 soft-reset fs cp ...` for manual copies — plain `fs cp` is unreliable on this board.

**First-boot config workflow:**
1. On first boot, `config.json` is auto-created from defaults and app halts with instructions.
2. Edit `config.json` on the device (`mpremote connect COM13 fs cat :/config.json`), fill all `your_*`/`changeme` placeholders.
3. Reboot to start the app. See [src/weather_station_mpy/README.md](src/weather_station_mpy/README.md) for full steps.

**Architecture:**

| Module | Role |
|--------|------|
| `board.py` | Pin map, display constants, RGB565 color palette — source of truth |
| `app_state.py` | Shared mutable state (pages, API data, dirty flags) |
| `compat.py` | Bridges MicroPython and CPython APIs for linting |
| `config/store.py` | Config load, merge with defaults, placeholder validation |
| `services/` | Stateless API clients: `WeatherService`, `SolarService`, `MetricsService`, `TimeService`, `WiFiService` |
| `ui/display_manager.py` | Display init + 5-page renderer; zones defined by Y-coordinate constants |

**Async task intervals:**

| Task | Interval | Notes |
|------|----------|-------|
| `render_task` | polls dirty flags | updates display only when data changes |
| `button_task` | ~20 ms poll | GPIO0 (BOOT), active LOW, debounced |
| `wifi_task` | 30 s | re-syncs NTP on reconnect |
| `weather_task` | 10 min | current + forecast (if WiFi) |
| `solar_task` | 5 min | realtime (if enabled + WiFi) |
| `metrics_task` | 10 s | PC metrics (if enabled + WiFi) |
| `heap_task` | 10 s | logs free memory |

**MicroPython pitfalls:**
- `st7789.py` must be explicitly deployed — it is **not** built into the firmware.
- All async tasks must use `await asyncio.sleep_ms()` — no blocking I/O in a task.
- No TLS/HTTPS support; all external APIs use HTTP only.
- Memory is constrained (~100–150 KB free heap); avoid holding full JSON responses.
- Recursive dir copy: `fs cp -r config :/` (target is root `/`, not `:/config`).

## Backend: PC Metrics API (`solarmann/`)

Exposes a local HTTP endpoint that the ESP32 polls for PC system metrics.

**Install and start (Windows):**
```powershell
pip install -r solarmann/requirements-pc-monitor.txt
python solarmann/pc_metrics_api.py --host 0.0.0.0 --port 8765 --disk-path C:\
```

**Endpoint:** `GET /api/system/metrics` → `{ cpu_pct, ram_pct, disk_pct, temp_c, uptime_s, ts, valid }`

- LAN-only, **no authentication** — do not expose to untrusted networks.
- ESP32 config: set `metrics.pc_url` to `http://<LAN_IP>:8765/api/system/metrics` in `config.json`.
- `wmi` package required on Windows for temperature readings.
- See [solarmann/](solarmann/) for `api.py` (SolarMan token reference) and `start_pc_monitor.ps1`.

## Agent Behavior for Code Changes
- Keep pin definitions centralized: `board.py` (MicroPython) or `#define` block (Arduino). Do not scatter pin assignments.
- Do not change the LCD pin map unless explicitly requested.
- Keep backlight support on GPIO32 in examples (on/off or PWM dimming).
- Prefer non-blocking loop patterns (millis-based for Arduino; `await asyncio.sleep_ms()` for MicroPython).
- Preserve display responsiveness when adding Wi-Fi, BLE, or sensor polling.
- Keep serial logs enabled for boot and runtime diagnostics. Use `[TAG]` prefix convention (e.g., `[WiFi]`, `[OWM]`, `[Solar]`).
- Services are stateless API clients; all runtime data lives in `app_state.py`. Do not add state to service classes.
- Display updates follow a dirty-flag pattern: data tasks set `app_state.dirty_*` flags; only `render_task` writes to the display.
- Include a smoke-test path in firmware examples:
  - Serial boot banner
  - Screen clear/fill
  - One visible text or primitive render
- Do not hardcode credentials or API secrets in committed source files.
- To add a new display page: follow the 5-page pattern in `ui/display_manager.py`, add a `PAGE_*` constant to `board.py`, and update the button task page count.

## Hardware Profile
Use this profile unless the user explicitly asks for different pins or a different display stack.

- MCU: ESP32-WROOM-32 (dual-core Xtensa, up to 240 MHz)
- Display: ST7789, 135x240, SPI, 1.14-inch IPS
- USB serial: CH340 (USB Type-C board connection)
- Logic voltage: 3.3V
- LCD wiring from vendor docs:
  - MOSI: GPIO23
  - SCLK: GPIO18
  - CS: GPIO15
  - DC: GPIO2
  - RST: GPIO4
  - BLK (backlight): GPIO32

## GPIO and Peripheral Notes
- GPIO34-GPIO39 are input-only.
- TX0/RX0 are used for USB serial programming.
- GPIO0 is the BOOT button (active LOW, internal pull-up) — used for page cycling in firmware; debounce required.
- Prefer the documented SPI pin mapping first, then remap only if required.
- Check pin conflicts against the board pinout before assigning peripherals.

## Deliverable Expectations for New Firmware Apps
- Add a short app-level README section with purpose, required libraries, and flash/run steps.
- Call out memory/performance tradeoffs when graphics, Wi-Fi, and BLE are combined.
- If behavior is uncertain, cite a Source of Truth document rather than guessing.