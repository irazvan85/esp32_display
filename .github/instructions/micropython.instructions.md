---
applyTo: "src/weather_station_mpy/**"
description: "MicroPython firmware conventions for the ESP32 ideaspark display project."
---

# MicroPython Firmware Conventions

## Async Task Rules
- Every task body must `await asyncio.sleep_ms(interval)` — no blocking I/O, no `time.sleep()`.
- Tasks are independent; each handles its own error recovery. Never `await` another task's result.
- Blocking HTTP calls during a task will freeze the render loop. If a service call may take >1 s, ensure it has a timeout.

## Service Pattern
- Services in `services/` are **stateless classes**: they accept config + make HTTP calls + return data.
- Do **not** store fetched data on service instances. Write results into `app_state.py` after each successful fetch.
- Service methods return `None` on error; callers check for `None` before writing to state.

## Config Access
- Always load config through `config/store.py` → `load_config()`. Never read `config.json` directly.
- New config keys must have a default in `config/defaults.py`; `load_config()` merges automatically.
- Required non-default fields (e.g., API keys) must use `your_*` or `changeme` as placeholder values so first-boot validation catches them.

## Display Update Flow
- Data tasks set `app_state.dirty_*` flags after writing new data; they must **not** call display functions.
- `render_task` in `main.py` polls dirty flags and calls `ui/display_manager.py` — it is the **only** writer to the display.
- Zone layout constants are defined in `ui/display_manager.py` by Y-coordinate. Add new zones there, not inline.

## Adding a New Display Page
1. Add a `PAGE_*` constant to `board.py`.
2. Add a rendering branch in `ui/display_manager.py` following the existing 5-page pattern.
3. Update `button_task` page count in `main.py`.

## Deploy Commands (MicroPython)
```powershell
# Full deploy (from src/weather_station_mpy/)
.\deploy.ps1 -Port COM13

# Manual file copy (reliable pattern — plain fs cp is flaky on this board)
mpremote connect COM13 soft-reset fs cp <file> :/

# Recursive directory copy (target must be root /, not :/dirname)
mpremote connect COM13 soft-reset fs cp -r config :/

# REPL monitor
mpremote connect COM13 repl
```

## Required Modules (must be deployed — not in ESP32_GENERIC firmware)
- `st7789.py` — display driver (bundled at `src/weather_station_mpy/st7789.py`)
- Font modules `vga1_16x32`, `vga1_8x16`, `vga1_8x8` — bundled in most firmware builds; verify after flashing

## Memory Constraints
- Free heap after boot: ~100–150 KB. Avoid retaining full JSON API responses.
- Call `gc.collect()` after parsing large JSON payloads.
- No TLS/HTTPS: all external API calls must use plain `http://`.

## Serial Logging
Use `[TAG]` prefix for all print statements: `[WiFi]`, `[OWM]`, `[Solar]`, `[Metrics]`, `[Time]`, `[Display]`, `[Boot]`.
