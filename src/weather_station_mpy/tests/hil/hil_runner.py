"""Hardware-in-the-Loop (HIL) test runner for the ESP32 weather station.

Usage
-----
    python tests/hil/hil_runner.py [options]

Options
-------
    --port    COM port (default: COM13)
    --suite   hardware | network | app | boot_clean | weather_visible | menu_visible | all  (default: all)
    --timeout seconds per test suite (default: 90)
    --verbose show full UART log for each suite

How it works
------------
1. Concatenates tests/device/runner.py + tests/device/test_<suite>.py into a
   temporary file.
2. Runs: mpremote connect <port> run <tempfile>
3. Parses every line for:
     [TEST] <name> PASS
     [TEST] <name> FAIL: <reason>
     [TEST] SUMMARY <n>/<total> PASS|FAIL
     Traceback (most recent call last):  → fatal device crash
4. Prints a colour-coded summary table.
5. Exits with code 0 (all pass) or 1 (any failure or crash).
"""

import argparse
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

# ── ANSI colours (degraded gracefully on Windows without VT) ─────────────────
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(
        ctypes.windll.kernel32.GetStdHandle(-11), 7
    )
    _COLOUR = True
except Exception:
    _COLOUR = os.environ.get("TERM") == "xterm-256color"

_GREEN  = "\033[92m" if _COLOUR else ""
_RED    = "\033[91m" if _COLOUR else ""
_YELLOW = "\033[93m" if _COLOUR else ""
_CYAN   = "\033[96m" if _COLOUR else ""
_DIM    = "\033[2m"  if _COLOUR else ""
_RESET  = "\033[0m"  if _COLOUR else ""

# ── paths ─────────────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT  = os.path.normpath(os.path.join(_HERE, "..", ".."))
_DEVICE    = os.path.join(_APP_ROOT, "tests", "device")
_RUNNER_PY = os.path.join(_DEVICE, "runner.py")

# boot_clean is a host-side test; path=None signals special dispatch.
_SUITES = {
    "hardware":   os.path.join(_DEVICE, "test_hardware.py"),
    "network":    os.path.join(_DEVICE, "test_network.py"),
    "app":        os.path.join(_DEVICE, "test_app.py"),
    "boot_clean": None,
    "weather_visible": None,
    "menu_visible": None,
}

# Patterns for UART log lines
_RE_PASS    = re.compile(r"\[TEST\]\s+(\S+)\s+PASS")
_RE_FAIL    = re.compile(r"\[TEST\]\s+(\S+)\s+FAIL:\s*(.*)")
_RE_SUMMARY = re.compile(r"\[TEST\]\s+SUMMARY\s+(\d+)/(\d+)\s+(PASS|FAIL)")
_RE_INFO    = re.compile(r"\[TEST:info\]\s+(.*)")
_RE_TB      = re.compile(r"Traceback \(most recent call last\)")
_RE_ESP_ERR = re.compile(r"^E \(\d+\)")  # ESP-IDF error log


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _build_combined(suite_path):
    """Concatenate runner.py + test file into a temporary script."""
    runner_src = _read_file(_RUNNER_PY)
    test_src   = _read_file(suite_path)
    combined   = runner_src + "\n\n# === test file: %s ===\n" % os.path.basename(suite_path)
    combined  += test_src
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    tmp.write(combined)
    tmp.close()
    return tmp.name


def _run_suite(port, suite_name, suite_path, timeout_s, verbose):
    """Deploy and run one test suite; return (results_list, crashed)."""
    print("\n%s%s Running: %s %s" % (_CYAN, "━" * 4, suite_name.upper(), _RESET))

    tmp_path = _build_combined(suite_path)
    results  = []   # list of {"name": str, "status": "PASS"|"FAIL", "reason": str}
    crashed  = False
    summary_found = False
    raw_lines = []
    proc = None

    cmd = ["mpremote", "connect", port, "run", tmp_path]

    # Use a reader thread so readline() never blocks the timeout loop.
    line_q = queue.Queue()

    def _reader(pipe, q):
        try:
            for ln in iter(pipe.readline, ""):
                q.put(ln)
        except Exception:
            pass
        q.put(None)  # EOF sentinel

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        reader_thread = threading.Thread(target=_reader,
                                         args=(proc.stdout, line_q),
                                         daemon=True)
        reader_thread.start()

        deadline = time.time() + timeout_s
        while True:
            try:
                item = line_q.get(timeout=0.5)
            except queue.Empty:
                if time.time() > deadline:
                    proc.kill()
                    print("  %s[TIMEOUT] suite %s exceeded %ds%s"
                          % (_RED, suite_name, timeout_s, _RESET))
                    crashed = True
                    break
                if proc.poll() is not None:
                    # Process exited; drain remaining queue items
                    while True:
                        try:
                            item = line_q.get_nowait()
                            if item is None:
                                break
                            line = item.rstrip("\r\n")
                            raw_lines.append(line)
                            _process_line(line, results)
                            if _RE_SUMMARY.search(line):
                                summary_found = True
                            if _RE_TB.search(line):
                                crashed = True
                        except queue.Empty:
                            break
                    break
                continue

            if item is None:  # EOF sentinel from reader thread
                break

            line = item.rstrip("\r\n")
            raw_lines.append(line)
            _process_line(line, results)

            if _RE_SUMMARY.search(line):
                summary_found = True
            if _RE_TB.search(line):
                crashed = True

        proc.wait(timeout=5)

    except KeyboardInterrupt:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        print("  %s[INTERRUPTED] suite %s interrupted by user%s"
              % (_YELLOW, suite_name, _RESET))
        raise
    except FileNotFoundError:
        print("%s[ERROR] mpremote not found — install it: pip install mpremote%s"
              % (_RED, _RESET))
        crashed = True
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not summary_found and not crashed:
        print("  %s[WARN] No SUMMARY line received — device may have crashed%s"
              % (_YELLOW, _RESET))
        crashed = True

    # Print verbose log if requested OR if there were failures
    any_fail = any(r["status"] == "FAIL" for r in results)
    if verbose or any_fail or crashed:
        print()
        for line in raw_lines:
            prefix = ""
            if _RE_ESP_ERR.match(line):
                prefix = _RED
            elif "[TEST]" in line and "FAIL" in line:
                prefix = _RED
            elif "[TEST]" in line and "PASS" in line:
                prefix = _GREEN
            elif line.startswith("Traceback") or "Error" in line:
                prefix = _RED
            elif line.startswith("[TEST:info]"):
                prefix = _DIM
            else:
                prefix = _DIM
            print("  %s%s%s" % (prefix, line, _RESET))

    return results, crashed


def _process_line(line, results):
    m = _RE_PASS.search(line)
    if m:
        results.append({"name": m.group(1), "status": "PASS", "reason": ""})
        return
    m = _RE_FAIL.search(line)
    if m:
        results.append({"name": m.group(1), "status": "FAIL",
                        "reason": m.group(2).strip()})


def _print_table(suite_name, results, crashed):
    """Print a compact pass/fail table for a suite."""
    if not results and crashed:
        print("  %s[CRASH] Device crashed before any test output%s" % (_RED, _RESET))
        return

    col_w = max((len(r["name"]) for r in results), default=10) + 2
    for r in results:
        icon   = ("%sPASS%s" % (_GREEN, _RESET)) if r["status"] == "PASS" \
                 else ("%sFAIL%s" % (_RED, _RESET))
        reason = ("  ← %s" % r["reason"]) if r["reason"] else ""
        print("  %s  %s%s" % (r["name"].ljust(col_w), icon, reason))

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    colour = _GREEN if n_fail == 0 and not crashed else _RED
    print("  %s─── %d/%d PASS%s" % (colour, n_pass, len(results), _RESET))


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="ESP32 HIL test runner — deploys MicroPython tests via mpremote"
    )
    ap.add_argument("--port",    default="COM13",
                    help="Serial port (default: COM13)")
    ap.add_argument("--suite",   default="all",
                    choices=list(_SUITES.keys()) + ["all"],
                    help="Test suite to run (default: all)")
    ap.add_argument("--timeout", type=int, default=90,
                    help="Timeout in seconds per suite (default: 90)")
    ap.add_argument("--verbose", action="store_true",
                    help="Always print full UART log")
    args = ap.parse_args()

    suites = list(_SUITES.items()) if args.suite == "all" \
             else [(args.suite, _SUITES[args.suite])]

    print("%s%s" % (_CYAN, "━" * 60))
    print("  ESP32 HIL Test Runner")
    print("  Port: %s   Suite: %s" % (args.port, args.suite))
    print("━" * 60 + _RESET)

    total_pass = 0
    total_fail = 0
    total_crash = 0

    try:
        for suite_name, suite_path in suites:
            if suite_path is None:
                # host-side tests — use pyserial directly
                sys.path.insert(0, _HERE)
                if suite_name == "boot_clean":
                    from test_boot_clean import run_boot_clean
                    boot_timeout = max(args.timeout, 30)  # always give at least 30 s
                    results, crashed = run_boot_clean(
                        args.port, timeout_s=boot_timeout, verbose=args.verbose
                    )
                elif suite_name == "weather_visible":
                    from test_weather_visible import run_weather_visible
                    weather_timeout = max(args.timeout, 60)  # allow network/cache path
                    results, crashed = run_weather_visible(
                        args.port, timeout_s=weather_timeout, verbose=args.verbose
                    )
                elif suite_name == "menu_visible":
                    from test_menu_visible import run_menu_visible

                    menu_timeout = max(args.timeout, 90)
                    results, crashed = run_menu_visible(
                        args.port, timeout_s=menu_timeout, verbose=args.verbose
                    )
                else:
                    print("%s[ERROR] Unknown host-side suite: %s%s"
                          % (_RED, suite_name, _RESET))
                    results, crashed = [], True
            else:
                results, crashed = _run_suite(
                    args.port, suite_name, suite_path, args.timeout, args.verbose
                )
            _print_table(suite_name, results, crashed)

            total_pass  += sum(1 for r in results if r["status"] == "PASS")
            total_fail  += sum(1 for r in results if r["status"] == "FAIL")
            if crashed:
                total_crash += 1
    except KeyboardInterrupt:
        print()
        print("%s[ABORTED] Test run interrupted by user%s" % (_YELLOW, _RESET))
        sys.exit(130)

    # ── overall summary ────────────────────────────────────────────────────────
    print()
    print("%s%s" % (_CYAN, "━" * 60))
    overall_ok = total_fail == 0 and total_crash == 0
    colour = _GREEN if overall_ok else _RED
    status = "ALL PASS" if overall_ok else "FAILURES DETECTED"
    print("  %s%s%s   (%d pass, %d fail, %d crash)"
          % (colour, status, _RESET, total_pass, total_fail, total_crash))
    print(_CYAN + "━" * 60 + _RESET)

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
