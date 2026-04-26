# Retro Weather Clock (MicroPython)

Initial implementation for migrating the existing Arduino weather station to MicroPython on the ideaspark ESP32 1.14-inch ST7789 board.

## What is implemented now

- Fixed board pin mapping for ideaspark ESP32 + ST7789.
- `uasyncio` app loop with separate tasks for:
  - button page switching
  - periodic Wi-Fi checks/reconnect
  - weather polling
  - solar polling
  - render loop
  - periodic heap logging
- First-boot config flow:
  - if `config.json` is missing, it is created from defaults
  - app halts with on-screen and serial instructions until placeholders are filled
- ST7789 display bootstrap and basic page rendering:
  - page 0: clock/date/weather summary/status
  - page 1: tomorrow summary
  - page 2: 5-day rows
  - page 3: solar summary
  - page 4: PC metrics (CPU/RAM/disk/temp/uptime)

## Current migration status

This is phase 1/2 implementation. It establishes architecture and hardware/runtime foundation. Visual parity with Arduino (`weather_station.ino`) and full icon/layout fidelity are not complete yet.

## Project layout

- `boot.py`: minimal boot hook
- `main.py`: app startup + task orchestration
- `board.py`: pin map, display constants, palette
- `app_state.py`: shared mutable runtime state
- `config/`: defaults, validation, and example config
- `services/`: Wi-Fi, time, weather API, SolarMan API, PC metrics API client
- `ui/display_manager.py`: display init + page rendering

## Required MicroPython modules

Install or bundle these modules in your firmware filesystem:

- `uasyncio`
- `urequests`
- `ntptime`
- `st7789`
- font modules used by `st7789` text rendering:
  - `vga1_16x32`
  - `vga1_8x16`
  - `vga1_8x8`

## Deploy to board (Windows / COM13)

1. Install tooling on your PC:

```powershell
pip install mpremote
```

1. Download an ESP32_GENERIC MicroPython firmware `.bin` from micropython.org.
1. Flash firmware once:

```powershell
.\flash_micropython.ps1 -Port COM13 -FirmwarePath C:\path\ESP32_GENERIC.bin -PythonExe C:\Users\irazv\AppData\Local\Programs\Python\Python313\python.exe
```

1. From this folder, deploy app files to the board:

```powershell
.\deploy.ps1 -Port COM13
```

1. Open serial monitor:

```powershell
mpremote connect COM13 repl
```

1. First run creates `config.json` on-device if missing. Fill placeholders and reboot.

```powershell
mpremote connect COM13 fs cat :/config.json
```

## First run behavior

1. Boot the app.
2. If `config.json` does not exist, it is auto-created.
3. App prints a config warning and pauses.
4. Edit `config.json` on the device and replace placeholders.
5. Reboot to start normal operation.

## Config notes

- `solar.enabled` defaults to `false` to avoid blocking startup for users without SolarMan credentials.
- `metrics.enabled` defaults to `false`; enable only when your local PC metrics endpoint is running.
- Keep `config.json` out of git; this repository ignores `src/weather_station_mpy/config.json`.

## WiFi behavior

### [WiFi/BOOT] Startup order requirement

On this board/firmware, importing `ui.display_manager` before the initial WiFi connection can destabilize the STA handshake (status 15/204 loops). Call `WifiService.ensure_connected()` before importing or initializing `DisplayManager`. Boot-time DMA pre-reservation in `boot.py` must be retained to prevent OOM.

- `wifi.prefer_bssid_scan` (default `false`) enables a pre-connect scan for APs matching `wifi.ssid` and picks the strongest BSSID.
- `wifi.bssid` can optionally pin the AP BSSID (`aa:bb:cc:dd:ee:ff` format); when set, it overrides scan-based selection.
- If scan is unavailable, no matching AP is found, or `connect(..., bssid=...)` is unsupported, connection falls back to plain SSID/password connect.
- **Boot-time DMA reservation** — `boot.py` reserves the WiFi DMA pool on a clean heap using `active(True)` and does not call `disconnect()` in this reservation path.
- **Single connect attempt per call** — `ensure_connected()` makes one `connect()` attempt per invocation, then polls status until timeout.
- **Pre-connect stale-state recovery** — if pre-connect status is non-idle, `ensure_connected()` performs an STA cycle (`active(False)` -> `active(True)`) before `connect()`.
- **No in-call fallback retry** — if that attempt fails, `ensure_connected()` returns offline without a second `connect()` in the same call.
- **Failure cleanup is STA-cycle only** — after a failed attempt, cleanup uses STA cycling only for `STAT_CONNECTING` or terminal error statuses (`200`-`204`); no `disconnect()`-based cleanup.

### [WiFi] Runtime reliability

- `app_main` runs a startup connect retry loop before display init.
- `wifi.startup_retries` (default `4`) controls startup connect attempts.
- `wifi.startup_retry_ms` (default `2000`) controls delay between startup attempts.
- `wifi_task` uses `wifi.offline_retry_ms` (default `5000`) while offline.
- Online WiFi health cadence remains `wifi.check_interval_ms`.

### [UI] Post-startup menu input

After startup completes, page cycling remains available even during background work.

- Button IRQ queues press events immediately.
- The button loop consumes queued presses with debounce before changing pages.

### [OWM] Weather retrieval behavior (runtime)

When `[WiFi]` is connected, the first `[OWM]` fetch can still fail transiently (for example, AP settle time or upstream jitter right after reconnect). The weather task now uses three separate cadences:

- After `[WiFi]` connects during boot, the app performs a startup `[OWM]` bootstrap before display init.
- Startup bootstrap fetches current weather first; forecast is best-effort and does not block current weather display.
- `weather.startup_retries` (default `3`) controls bootstrap retry attempts for current weather.
- `weather.startup_retry_ms` (default `5000`) controls delay between bootstrap retries.
- If bootstrap weather data exists, `weather_task` defers its first periodic fetch by `weather.refresh_ms` to avoid immediate back-to-back memory pressure.

- `weather.refresh_ms` (default `600000`) is the normal success cadence.
- `weather.retry_ms` (default `30000`) is a fast retry cadence when current weather fetch fails while WiFi is online.
- `weather.offline_retry_ms` (default `5000`) is the short polling cadence while WiFi is offline.

Current weather and forecast are fetched in separate steps. If current weather succeeds but forecast fails, page weather values still update and render; forecast data is retried on the next weather cycle.

Practical guidance:

- Keep `retry_ms` significantly lower than `refresh_ms` so transient online `[OWM]` failures recover quickly.
- Use `offline_retry_ms` to control how aggressively weather polling resumes after `[WiFi]` reconnect.
- In serial logs, expect `[OWM] fetch error: ...` for current failures and `[OWM] forecast error: ...` for forecast-only failures.

### [OWM] Page 0 weather UX

- Page 0 includes a subtle per-second animated weather icon.
- Page 0 shows a daily trend graph for temperature and precipitation from the forecast bundle.
- If trend data is unavailable, rendering falls back to summary weather values without the trend graph.

### [OWM/API] Forecast compatibility

- `fetch_forecast_bundle()` returns `(daily_forecast, today_trend)`.
- `today_trend` includes up to 8 points with `hour`, `temp_c`, and `precip_mm`.
- `fetch_forecast()` is retained for backward-compatible callers.

## PC metrics endpoint (Page 5)

The ESP32 fetches PC metrics over LAN HTTP every 10 seconds (configurable).

1. Install dependency on your PC:

```powershell
pip install -r .\solarmann\requirements-pc-monitor.txt
```

1. Start the PC API:

```powershell
python .\solarmann\pc_metrics_api.py --host 0.0.0.0 --port 8765 --disk-path C:\
```

1. Update your device `config.json`:

```json
"metrics": {
  "enabled": true,
  "pc_url": "http://<PC_LAN_IP>:8765/api/system/metrics",
  "refresh_ms": 10000,
  "stale_ms": 120000,
  "timeout_ms": 3000
}
```

1. Deploy again and switch to page `[5/5]` using the hardware button.

## Memory management

The ESP32 has ~100–150 KB free heap after boot. After display init and the initial weather bootstrap, expect ~75–85 KB free in steady state.

### gc discipline

- Every task calls `gc.collect()` after **both** successful and failed service fetches.
- `gc.collect()` releases parsed JSON payloads and closed socket buffers promptly rather than waiting for the next GC cycle.
- Heap is not expected to trend downward in normal operation; a steady ~77 KB free is healthy.

### Metrics task backoff

When the PC metrics endpoint is unreachable, `metrics_task` applies exponential backoff to avoid polling every 10 s against a dead host:

| Consecutive failures | Sleep before next attempt |
| -------------------- | ------------------------- |
| 1 | 20 s (2× `refresh_ms`) |
| 2 | 40 s (4× `refresh_ms`) |
| 3 | 80 s |
| 4 | 160 s |
| ≥ 5 | 300 s (capped, ~5 min) |

Backoff resets immediately on a successful fetch. This eliminates the `[PC] fetch error: -203` log spam when the backend is offline.

### Memory test coverage

Device tests in `tests/device/test_app.py` include:

- **REQ-MEM-01** (`test_heap_stable_after_gc`): heap must remain ≥ 20 KB and must not drop >30% across 5 GC cycles.
- **REQ-MEM-02** (`test_metrics_service_error_no_leak`): a `MetricsService.fetch()` call against an unreachable host must not permanently consume >4 KB.
- **REQ-MEM-03** (`test_appstate_weather_trend_is_list`): `AppState.weather_trend` is an empty list at init.
- **REQ-MEM-04** (`test_service_instantiation_no_leak`): instantiating all 5 services must not permanently consume >8 KB.

## Next implementation targets

- Full icon primitives and geometry parity with Arduino page renderers.
- Non-blocking HTTP strategy to reduce render jitter during API calls (deferred; requires urequests async support or custom socket layer).
- Improved forecast memory profile (selective extraction and payload release — partial; gc.collect() after fetch is in place).
- TLS hardening options for API requests where feasible on MicroPython.
