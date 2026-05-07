# tools/ — Host-Side Diagnostic Scripts

`tools/` contains host-side Python scripts for capturing and diagnosing the ESP32 device state over UART. Run these from the repository root on your development machine (not on the device).

**Requirements:** `pip install pyserial` (plus `pytest` for tests).

---

## Tools Summary

| Script | Purpose |
|--------|---------|
| `capture_display.py` | Capture display state over UART; render HTML snapshot reports |
| `collect_uart_log.py` | Collect raw UART output for a configurable duration (default 5 min) |
| `analyze_uart_log.py` | Parse UART logs for errors, tracebacks, and memory issues; produce Markdown issue report |
| `diag_run.py` | Full diagnostic run: display capture + UART collection + analysis in one command |

---

## Quick Start: Full Diagnostic Run

```
python tools/diag_run.py --port COM13 --duration 300
```

Opens `COM13` at 115200 baud, captures all display pages, collects 5 minutes of UART output, runs the analyzer, and writes timestamped files to `tools/logs/`.

**Output files** (all named `*_YYYYMMDD_HHMMSS.*`):

| File | Contents |
|------|---------|
| `display_status_<ts>.log` | Page list, per-page ok/error, text summary |
| `display_snapshot_<ts>.html` | HTML sweep report with rendered page snapshots |
| `uart_raw_<ts>.log` | Raw UART log |
| `issue_report_<ts>.md` | Markdown analysis report |

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `COM13` | Serial port |
| `--baud` | `115200` | Baud rate |
| `--duration` | `300` | UART capture duration (seconds) |
| `--output-dir` | `tools/logs` | Directory for all output files |
| `--reset` | off | Send Ctrl+D soft-reset before UART capture |

---

## Individual Tool Usage

### capture_display.py

Connects to the device, cycles through display pages via the BOOT button, and captures a rendered HTML snapshot of each.

```
python tools/capture_display.py --port COM13 --all-pages
python tools/capture_display.py --port COM13 --all-pages --pages 0,1,2
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | _(required)_ | Serial port |
| `--baud` | `115200` | Baud rate |
| `--output` | `snapshot.html` | Output HTML file |
| `--timeout` | `15` | Seconds to wait per page snapshot |
| `--all-pages` | off | Capture and validate all enabled pages |
| `--pages` | _(all)_ | Comma-separated page indices to capture |

---

### collect_uart_log.py

Reads raw UART output and saves it to a timestamped file for later analysis.

```
python tools/collect_uart_log.py --port COM13 --duration 300
python tools/collect_uart_log.py --port COM13 --duration 60 --reset
```

`--reset` sends a Ctrl+D soft-reset before capture starts, then waits 2 seconds for the device to boot.

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `COM13` | Serial port |
| `--baud` | `115200` | Baud rate |
| `--duration` | `300` | Capture duration (seconds) |
| `--output` | _(auto-named)_ | Output file path; defaults to `tools/logs/uart_raw_<ts>.log` |
| `--reset` | off | Send Ctrl+D before capture |

---

### analyze_uart_log.py

Parses a raw UART log and produces a Markdown issue report classifying findings by severity.

```
python tools/analyze_uart_log.py tools/logs/uart_raw_20260505_120000.log
python tools/analyze_uart_log.py tools/logs/uart_raw_20260505_120000.log --output tools/logs/report.md
```

Can also be imported programmatically:

```python
from tools.analyze_uart_log import analyze_log
result = analyze_log(lines)
```

`analyze_log()` returns a dict with keys: `total_lines`, `error_count`, `warning_count`, `tracebacks`, `criticals`, `warnings`, `informationals`, `mem_heap`, `esp_ram`, `tags_seen`, `first_ts`, `last_ts`, `repetitions`.

---

## Issue Severity Levels

### Critical
Requires immediate attention — device crash, panic, or heap exhaustion.

| Pattern | Meaning |
|---------|---------|
| `Traceback (most recent call last)` | Unhandled exception in an async task |
| `MemoryError` | Heap exhausted |
| `WDT reset` / `watchdog` | Watchdog timer fired — a task blocked on I/O |

### Warning
Indicates a recoverable failure that may degrade functionality.

| Pattern | Meaning |
|---------|---------|
| `OSError: -202` / `-203` | Network unreachable or connection refused |
| `fetch error:` | API fetch failure |
| `bind/listen error:` | Web config socket could not bind |

### Informational
Noteworthy but not indicative of a fault.

| Pattern | Meaning |
|---------|---------|
| `[stale]` | Weather cache is stale — OWM fetch failing |
| `backoff` | Exponential backoff active on a failing task |
| `[Errno N]` | System errno raised — review context for details |

---

## Common Findings and Fixes

| Pattern | Likely Cause | Fix |
|---------|-------------|-----|
| `OSError: -202` / `-203` | PC metrics API or OWM not reachable | Check firewall; verify `solarmann/pc_metrics_api.py` is running |
| `bind/listen error:` | Web config socket binding failed (ENOBUFS) | Disable `web.enabled` in `config.json` if web config is not needed |
| `MemoryError` | Heap exhausted | Check for large buffers; trigger GC earlier in the task loop |
| `WDT reset` | Async task blocked on I/O | Add `await asyncio.sleep_ms(0)` yield points in long-running tasks |
| `fetch error: -202` | OWM API unreachable | Check network, OWM API key in `config.json`, and DNS resolution |

---

## Test Coverage

`analyze_uart_log.py` has unit tests at [src/weather_station_mpy/tests/test_analyze_uart_log.py](../src/weather_station_mpy/tests/test_analyze_uart_log.py).

```
python -m pytest src/weather_station_mpy/tests/test_analyze_uart_log.py -v
```
