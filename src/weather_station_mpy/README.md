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
  - page 5: ESP32 system status (RAM, CPU MHz, flash, FS, WiFi RSSI/channel)
- Runtime web configuration interface over WiFi IP for theme, visible pages, and metrics URL.

## Current migration status

This is phase 1/2 implementation. It establishes architecture and hardware/runtime foundation. Visual parity with Arduino (`weather_station.ino`) and full icon/layout fidelity are not complete yet.

## Project layout

- `boot.py`: minimal boot hook
- `main.py`: app startup + task orchestration
- `board.py`: pin map, display constants, palette
- `app_state.py`: shared mutable runtime state
- `config/`: defaults, validation, and example config
- `services/`: Wi-Fi, time, weather API, SolarMan API, PC metrics API client
- `services/web_config_service.py`: lightweight HTTP config UI service
- `services/weather_cache_service.py`: local weather cache for offline fallback
- `services/uart_capture_service.py`: test-only pixel capture UART command handler
- `ui/display_manager.py`: display init + page rendering
- `ui/theme.py`: runtime theme palette application

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

## Boot Sequence

The correct boot order is required to avoid lwIP PCB/DNS memory exhaustion:

1. WiFi connect (`startup_retries` attempts)
2. NTP sync
3. OWM startup bootstrap (**before** display init — preserves DMA memory for DNS)
4. `gc.collect()` to free OWM transient allocations
5. `DisplayManager.init()` (SPI DMA buffer allocation)
6. Weather cache service (loads fallback data if OWM failed)
7. Web socket pre-bind (if `web.enabled`)
8. Async tasks start

## Config notes

- `solar.enabled` defaults to `false` to avoid blocking startup for users without SolarMan credentials.
- `metrics.enabled` defaults to `false`; enable only when your local PC metrics endpoint is running.
- `ui.theme` defaults to `retro`.
- `ui.enabled_pages` defaults to all pages `[0,1,2,3,4,5]` and is validated as a non-empty integer list within range.
- Config validation checks placeholder values and required fields before normal startup.
- Keep `config.json` out of git; this repository ignores `src/weather_station_mpy/config.json`.

### Config key reference (selected keys)

| Key | Default | Description |
|-----|---------|-------------|
| `web.enabled` | `false` | Enable HTTP config UI (disabled by default — enabling causes lwIP PCB exhaustion on WiFi-only boards) |
| `web.port` | `80` | HTTP server port |
| `ui.theme` | `"retro"` | Display color theme (`retro`, `light`, `high_contrast`) |
| `ui.enabled_pages` | `[0,1,2,3,4,5]` | Pages to cycle through via button |
| `solar.enabled` | `false` | Enable SolarMan solar data polling |
| `metrics.enabled` | `false` | Enable PC metrics polling |
| `wifi.startup_retries` | `4` | Boot-time WiFi connect attempts |
| `wifi.startup_retry_ms` | `2000` | Delay (ms) between boot WiFi retries |
| `wifi.assoc_fail_backoff_max_s` | `360` | Max stepped ASSOC_FAIL cooldown (seconds) before long quiet window |
| `wifi.assoc_fail_long_cooldown_after_n` | `4` | Consecutive ASSOC_FAIL threshold that triggers long quiet window |
| `wifi.assoc_fail_long_cooldown_s` | `720` | Long radio-off quiet window duration in seconds |

### Test-only pixel capture (debug)

The firmware now includes an optional **test-only** pixel mirror and frame dump path.

- Disabled by default (`debug.test_mode=false`, `debug.pixel_capture_enabled=false`).
- Guarded by config + runtime arm command:
  - `debug.test_mode=true`
  - `debug.pixel_capture_enabled=true`
  - UART command `!CAPTURE ARM`
- Frame export command: `!FRAME DUMP`
- Disarm command: `!CAPTURE DISARM`

Debug keys:

| Key | Default | Description |
|-----|---------|-------------|
| `debug.test_mode` | `false` | Master guard for test-only features |
| `debug.pixel_capture_enabled` | `false` | Enables pixel mirror module (requires `test_mode=true`) |
| `debug.pixel_capture_arm_on_boot` | `false` | Arms capture on startup (test benches only) |
| `debug.pixel_capture_min_heap_kb` | `96` | Minimum free heap floor to allow capture arm |
| `debug.pixel_capture_max_fps` | `2` | Max frame dump rate guard |

Host capture usage:

```powershell
python tools/capture_display.py --port COM13 --pixel-frame --pixel-output snapshot.ppm
```

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
- Startup connect exits early on status `15` (`ASSOC_FAIL`) and defers retries to `wifi_task` lockout-safe policy.
- `wifi_task` uses `wifi.offline_retry_ms` (default `5000`) while offline.
- Online WiFi health cadence remains `wifi.check_interval_ms`.

### [WiFi] ASSOC_FAIL lockout-safe retry policy

- `status=15` means `ASSOC_FAIL`: the AP rejected station association, commonly due to temporary anti-spam/rate-limit behavior.
- On ASSOC_FAIL, the firmware powers WiFi radio fully off and waits before retrying, so the ESP32 does not keep refreshing AP lockout timers.
- Cooldown escalates by consecutive failures: `120s`, `240s`, `360s` (capped by `wifi.assoc_fail_backoff_max_s`).
- After `wifi.assoc_fail_long_cooldown_after_n` consecutive ASSOC_FAIL events (default `4`), retries switch to a longer quiet window of `wifi.assoc_fail_long_cooldown_s` (default `720s`).
- If startup sees ASSOC_FAIL, boot retries stop early and `wifi_task` starts with pending ASSOC_FAIL state to apply the same radio-off cooldown first.
- This protects against AP lockout amplification and improves recovery on APs that temporarily reject rapid re-association attempts.

## Runtime Web Configuration

The device exposes a lightweight HTTP config UI over your LAN when WiFi is connected.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `http://<device-ip>/` | GET | Load current config form |
| `http://<device-ip>/save` | POST | Submit updated config |

**How to access**: Connect your PC or phone to the same LAN. Open a browser at the IP shown on the Status page (page 0).

**Features available via the web UI**:
- Theme selection: `retro`, `light`, `high_contrast`
- Enable/disable individual display pages (IDs `0`–`5`)
- Set the PC metrics source URL (`metrics.pc_url`)

**Config persistence**: Changes are written to `config.json` on the device immediately and take effect on the running UI without a reboot.

> Requires WiFi connected; no AP/captive-portal fallback in the current implementation.

### Web socket pre-bind

**Implementation note**: The web socket is pre-bound in `app_main` before any async tasks are created. This is required on ESP32 because the lwIP TCP PCB pool (≈5 slots) is exhausted by concurrent outbound weather/metrics HTTP connections, making `bind()` fail with ENOBUFS if attempted after tasks start.

### [UI] Post-startup menu input

After startup completes, page cycling remains available even during background work.

- Button IRQ queues press events immediately.
- The button loop consumes queued presses with debounce before changing pages.
- Button/menu handling remains active while web config services run so display pages remain navigable.

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

### [OWM] Weather cache fallback

To improve startup reliability when upstream weather is temporarily unavailable, the app keeps a local weather cache in `weather_cache.json` on the device filesystem.

- Purpose: persist the latest known-good current weather and forecast payloads for fallback rendering.
- Cache write timing: the cache is updated after successful weather and forecast fetch/parse cycles.
- Cache restore timing: on startup, if live weather bootstrap is unavailable, the app restores cached weather so page 0 is still populated.
- Offline fallback timing: if startup has no WiFi and no valid cache, the app renders an explicit placeholder weather payload (`Offline`, `0.0C`) so page 0 is still populated.
- Caveat: cached values can be stale and may not match current outside conditions until the next successful live fetch.

### [OWM] Deploy + verify checklist (after weather-cache changes)

1. Deploy firmware files from `src/weather_station_mpy`:

```powershell
.\deploy.ps1 -Port COM13
```

1. Reboot to force a fresh startup weather path (either method is fine):

```powershell
mpremote connect COM13 soft-reset
```

1. Confirm one startup success marker in UART:

- `[OWM] startup fetch OK`
- `[OWM] startup weather restored from cache`
- `[OWM] startup weather fallback: offline placeholder`

> **Do not use `--reset` or `ctrl+D` unless necessary** — rapid resets cause AP rate-limiting (status=15 ASSOC_FAIL). Use `capture_uart.py --duration 180` (no `--reset` flag) to observe an already-running device.

### [HIL] Weather-visible smoke test

Run from `src/weather_station_mpy`.

Standalone test:

```powershell
python tests/hil/test_weather_visible.py --port COM13
```

Runner suite:

```powershell
python tests/hil/hil_runner.py --port COM13 --suite weather_visible
```

Pass/fail semantics (brief): PASS when weather becomes visible in UART (startup fetch OK, startup cache restore, startup offline placeholder fallback, or a live `[OWM] <temp>C` line). FAIL on crash markers or if no weather marker appears before timeout.

### [HIL] Menu-visible sweep test

Run from `src/weather_station_mpy`.

Standalone test:

```powershell
python tests/hil/test_menu_visible.py --port COM13
```

Runner suite:

```powershell
python tests/hil/hil_runner.py --port COM13 --suite menu_visible
```

Pass/fail semantics (brief): PASS when every enabled page can be selected with UART `!PAGE <n>` and each follow-up `!SNAP` reports the requested active page. FAIL on command errors, page mismatch, snapshot timeout, or crash markers.

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

1. Deploy again and switch to page `[5/6]` using the hardware button.

## Memory management

The ESP32 has ~100–150 KB free heap after boot. After display init and the initial weather bootstrap, expect ~75–85 KB free in steady state.

### gc discipline

- Every task calls `gc.collect()` after **both** successful and failed service fetches.
- `gc.collect()` releases parsed JSON payloads and closed socket buffers promptly rather than waiting for the next GC cycle.
- Heap is not expected to trend downward in normal operation; a steady ~77 KB free is healthy.

### [Display] Memory-safe large text rendering

- `_text3x` renders per-character buffers instead of allocating one large frame buffer.
- `render_task` catches `MemoryError`, logs the failure, runs `gc.collect()`, and skips the current frame so the app keeps running.
- The prior render failure mode (`allocating 9216 bytes`) is mitigated by this approach.

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

## Requirement traceability (latest)

- `REQ-WEB-01`: connect via WiFi IP to config UI.
- `REQ-WEB-02`: configure visible pages and metrics source URL.
- `REQ-WEB-03`: configure global theme across pages.
- `REQ-ROB-01`: app survives display render memory pressure without reboot.

## Troubleshooting

**Weather page stuck on `Fetching wx...` / `OWM err -202`**: This indicates the ESP is associated to WiFi, but cannot reach the OpenWeatherMap endpoint from that SSID (DNS, route, or internet upstream issue). Verify that SSID has internet access, router DNS resolution is working, and the configured OWM URL is reachable from another client on the same SSID.

**Repeated `[OWM]` `-202`/`-203` in UART**: With weather cache fallback enabled, repeated transport errors are expected to keep showing cached weather instead of blank weather fields after startup. Confirm `weather_cache.json` exists on-device, then verify internet/DNS reachability from the ESP SSID, check OpenWeatherMap host/port access from another client on the same network, and validate your OWM key and endpoint configuration.

**Web UI unreachable from PC**: If the ESP shows a WiFi IP and `[WEB] Config UI: http://<ip>:<port>/` in UART, but your PC cannot ARP/ping/reach that IP, this is typically AP/client isolation (or VLAN separation). Connect the PC to the same SSID/VLAN as the ESP, or disable client isolation on that SSID.

**PC metrics diagnostics (`[PCDBG]`)**: When `[PC] fetch error: ...` appears, the firmware may emit a short connectivity snapshot. `endpoint host:port` shows the parsed target from `metrics.pc_url`. `ifconfig (...)` is the ESP STA tuple `(ip, netmask, gateway, dns)`. `resolve ok (...)` / `resolve err ...` shows whether DNS or host parsing succeeded. `connect err ...` is the TCP connect result after resolution.

**Common `[PCDBG]` pattern: `resolve ok` + `connect err -203`**: The ESP resolved the target, but could not open a TCP session to that `host:port`. Treat this as a blocked TCP path or closed listener on the PC side: Windows firewall, VLAN or AP/client isolation, wrong PC IP, or the metrics API not actually listening on `8765`.

**Windows checklist for PC metrics on port `8765`**: Confirm the API is listening with `netstat -ano | findstr :8765`. Confirm the PC LAN IP with `ipconfig` and match it to `metrics.pc_url`. Test `http://<PC_LAN_IP>:8765/api/system/metrics` from another LAN client, not just the host PC. If needed, allow inbound TCP `8765` on the Windows Defender Firewall `Private` profile.

**Repeated idle web `accept()` timeout logs**: In current firmware, idle socket `accept()` timeouts are treated as normal and suppressed. UART should not be flooded by timeout-only web accept errors after updating.

**Web bind/listen failures**: If UART shows `[WEB] Pre-bind failed: ...` or `[WEB] bind/listen error: ...`, the config UI socket could not bind yet. Runtime retries now use adaptive backoff and escalate the retry interval up to `120s`, reducing repeated bind spam while the stack recovers.

**First-boot config warning / app halts**: Expected behaviour. The app auto-creates `config.json` from defaults and halts until all `your_*` / `changeme` placeholders are replaced. Read and edit the file with `mpremote connect COM13 fs cat :/config.json`, then reboot.

**Solar page shows no data**: `solar.enabled` is `false` by default. Set it to `true` in `config.json` and supply valid SolarMan credentials.

### OWM always fails with -202 or -203 (EAI_FAIL / EAI_MEMORY)

This is caused by lwIP PCB/DNS memory exhaustion. Known root causes and fixes applied in the current codebase:

- **Web server consuming TCP PCBs**: The web config server repeatedly failing with ENOBUFS exhausts the lwIP PCB pool. Fix: `web.enabled` defaults to `false` in config. Do **not** enable the web server unless needed.
- **DNS server blocking external resolvers**: Setting `wifi.dns` to `8.8.8.8` on restricted LANs causes DNS timeouts that exhaust the lwIP `MEMP_NETDB` slot. Leave `wifi.dns` empty to use DHCP-assigned router DNS.
- **Display init before OWM bootstrap**: `DisplayManager.init()` allocates DMA-capable RAM. If called before OWM's DNS lookup, the lwIP DNS allocator gets `EAI_MEMORY`. The boot sequence does OWM bootstrap first, then display init (see Boot Sequence above).
- **AP rate-limiting after rapid resets**: Repeated soft-resets during development cause the AP to reject re-association (status=15 `ASSOC_FAIL`). Leave the device powered for 5–10 minutes without resetting to allow AP rate-limit to clear.

### ASSOC_FAIL (status=15) operator checklist

- Check UART for this sequence: `[WiFi] status=15 ASSOC_FAIL`, then `[WiFi] ASSOC_FAIL #n - radio off, backoff ...` (or `quiet window ...`), then `[WiFi] radio OFF during backoff`.
- Wait when backoff/quiet window logs are active. Repeated resets during this period usually make AP lockout behavior worse.
- Reboot the AP only after at least one full long quiet window has elapsed (`720s` default) and status 15 still repeats immediately.
- Run an A/B test with a phone hotspot to isolate AP policy issues:
  - Hotspot works but home AP fails: likely AP rate-limit/association policy on the router.
  - Both fail with status 15: re-check WiFi credentials, security mode compatibility, and RF signal quality.

**PC metrics page shows stale/no data**: Verify the PC metrics API is running and reachable at the configured `metrics.pc_url`. Use the `[PCDBG]` lines above to separate name resolution failures from TCP path failures.

## Next implementation targets

- Full icon primitives and geometry parity with Arduino page renderers.
- Non-blocking HTTP strategy to reduce render jitter during API calls (deferred; requires urequests async support or custom socket layer).
- Improved forecast memory profile (selective extraction and payload release — partial; gc.collect() after fetch is in place).
- TLS hardening options for API requests where feasible on MicroPython.

## UART Display Snapshot

The firmware includes a `uart_capture_task` that listens on the serial port and supports these commands:

- `!SNAP` -> emit snapshot JSON between `>>SNAP_START` and `>>SNAP_END`
- `!PAGE <n>` -> switch to enabled page `n` and reply `>>CMD_OK PAGE <n>`
- `!NEXT` -> switch to next enabled page and reply `>>CMD_OK NEXT <n>`
- `!SUBPAGE <0|1>` -> select metrics subpage and reply `>>CMD_OK SUBPAGE <n>`

**Purpose**: diagnose what is shown on the physical display without looking at the device. Works while the main app is running.

`!SNAP` includes runtime fields used to derive display-visible data on the host side (`page`, weather/forecast/solar/metrics state, `local_time`, `snapshot_ms`), so reports are based on page render rules rather than parsing UART log lines.

**Requirements on PC**: `pyserial>=3.5` — install via `pip install -r requirements.txt`

**Usage**:

```powershell
# Capture snapshot and render as HTML
python tools/capture_display.py --port COM13

# Capture all enabled pages and validate page switching
python tools/capture_display.py --port COM13 --all-pages

# Custom output file
python tools/capture_display.py --port COM13 --output my_snapshot.html
```

By default, the script sends `!SNAP\r\n` to COM13, waits up to 15 seconds for a `>>SNAP_START ... >>SNAP_END` block, parses the JSON, derives visible on-screen fields using page render rules, and writes a single-page HTML report.

With `--all-pages`, the script:

1. Captures an initial snapshot to read `enabled_pages`.
1. Sends `!PAGE <n>` for each target page.
1. Captures a new `!SNAP` after each page switch.
1. Writes a multi-page sweep report and exits non-zero if any page fails validation.

CI-friendly host check (no device required, mocked serial path):

```powershell
python -m pytest src/weather_station_mpy/tests/test_capture_display_tool.py -v
```

## GitHub Actions CI (MicroPython)

The repository includes two MicroPython-focused workflows:

- **MicroPython Host Tests** (`.github/workflows/micropython-host-tests.yml`)
  - Runs host-side tests in `src/weather_station_mpy/tests`
  - Excludes `tests/device` and `tests/hil` (hardware-only suites)
  - Executes on `pull_request` and `push` to `main` / `dev_micropython`
  - Uses a Python matrix (`3.10`, `3.12`)

- **MicroPython Sanity Checks** (`.github/workflows/micropython-sanity-checks.yml`)
  - Compiles `src/weather_station_mpy` with `python -m compileall`
  - Runs a CPython import smoke-check for core modules/services/ui
  - Executes on `pull_request` and `push` to `main` / `dev_micropython`

These workflows validate MicroPython firmware logic that can run on host CI, while device/HIL tests remain manual or bench-driven.

### Device-side UART task wiring

**Device side**: `uart_capture_task` is added automatically to the async task list in `main.py` when `services/uart_capture_service.py` is deployed on the device (it is part of the standard deploy set via `deploy.ps1`).

**UART snapshot output format**:

```text
[SNAP] Snapshot triggered
>>SNAP_START
{"page":0,"wifi_online":true,"weather":{"valid":true,"temp_c":7.1,...},...}
>>SNAP_END
>>CMD_OK PAGE 2
```

**Limitations**:

- Not a pixel-level screenshot — the ST7789 SPI driver is write-only; reading back framebuffer pixels is not supported.
- The capture is host-derived structured data from runtime snapshot fields, not raw framebuffer pixels.
- Memory overhead per snapshot: ~1–2 KB for JSON serialization. Safe with normal ≥60 KB free heap.

## OWM Error Reference

| Error code | MicroPython meaning | Cause | Fix |
|---|---|---|---|
| `-202` | `EAI_FAIL` — DNS server returned failure | DNS server unreachable or NXDOMAIN | Retried automatically (3×) with gc.collect() |
| `-203` | `EAI_MEMORY` — DNS resolver out of heap | Fragmented heap after boot | Retried automatically (3×) with gc.collect() |
| `105` | `ENOBUFS` — lwIP PCB pool exhausted | Too many concurrent sockets | Web config UI is disabled by default; fix root cause |
| `118` | `EHOSTUNREACH` | WiFi route unavailable | Auto-heal reconnect triggers after 3 consecutive errors |
