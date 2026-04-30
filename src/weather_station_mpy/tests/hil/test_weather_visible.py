"""Weather-visible UART smoke test — host-side HIL check.

Triggers a MicroPython soft reset on the device (Ctrl-D via serial) and
monitors UART output for weather markers. The test PASSES when weather becomes
visible via either startup fetch, cache restore, or a later live temperature
line.

Pass markers (any match -> PASS):
  - ``[OWM] startup fetch OK``
  - ``[OWM] startup weather restored from cache``
    - ``[OWM] startup weather fallback: offline placeholder``
  - ``[OWM] <temp>C`` (live weather line)

Crash markers (any match -> FAIL fast):
  - ``Guru Meditation``
  - ``Traceback (most recent call last)``
  - ``LoadProhibited``

Usage (standalone)::

    python tests/hil/test_weather_visible.py --port COM13 [--timeout 60] [--verbose]
"""

import argparse
import re
import sys
import time


_CRASH_PATTERNS = [
    (re.compile(r"Guru Meditation"), "Guru Meditation crash"),
    (re.compile(r"Traceback \(most recent"), "unhandled Python exception"),
    (re.compile(r"LoadProhibited"), "NULL-ptr LoadProhibited"),
]

_PASS_PATTERNS = [
    (re.compile(r"\[OWM\]\s+startup fetch OK"), "startup fetch OK"),
    (re.compile(r"\[OWM\]\s+startup weather restored from cache"),
     "startup weather restored from cache"),
    (re.compile(r"\[OWM\]\s+startup weather fallback:\s+offline placeholder"),
     "startup weather fallback"),
    (re.compile(r"\[OWM\]\s*-?\d+(?:\.\d+)?\s*C\b"), "live weather line"),
]


def run_weather_visible(port, timeout_s=60, verbose=False):
    """Perform the weather-visible UART check.

    Returns ``(results, crashed)`` where each result entry has keys:
    ``name``, ``status`` and ``reason``.
    """
    try:
        import serial  # pyserial — installed as mpremote dependency
    except ImportError:
        print("[TEST] weather_visible FAIL: pyserial not installed")
        print("[TEST] SUMMARY 0/1 FAIL")
        return [{"name": "weather_visible", "status": "FAIL",
                 "reason": "pyserial not installed"}], True

    results = []
    crashed = False
    found_weather = False
    pass_reason = ""
    fail_reason = ""
    raw_lines = []

    try:
        ser = serial.Serial(port, 115200, timeout=1)
    except serial.SerialException as exc:
        msg = "cannot open %s: %s" % (port, exc)
        print("[TEST] weather_visible FAIL: %s" % msg)
        print("[TEST] SUMMARY 0/1 FAIL")
        return [{"name": "weather_visible", "status": "FAIL", "reason": msg}], True

    try:
        # Interrupt app execution and soft-reset to start a fresh boot window.
        ser.write(b"\x03")
        ser.flush()
        time.sleep(0.4)
        ser.write(b"\x03")
        ser.flush()
        time.sleep(0.3)

        if ser.in_waiting:
            ser.read(ser.in_waiting)
        time.sleep(0.1)
        if ser.in_waiting:
            ser.read(ser.in_waiting)

        ser.write(b"\x04")
        ser.flush()

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            raw = ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            raw_lines.append(line)
            if verbose:
                print("    %s" % line)

            for pat, label in _CRASH_PATTERNS:
                if pat.search(line):
                    fail_reason = "%s: %s" % (label, line.strip())
                    crashed = True
                    break
            if crashed:
                break

            for pat, label in _PASS_PATTERNS:
                if pat.search(line):
                    found_weather = True
                    pass_reason = label
                    break
            if found_weather:
                break
    finally:
        ser.close()

    if crashed:
        _emit("weather_visible", "FAIL", fail_reason)
        results.append({"name": "weather_visible", "status": "FAIL",
                        "reason": fail_reason})
    elif not found_weather:
        reason = "no weather marker seen within %ds" % timeout_s
        _emit("weather_visible", "FAIL", reason)
        results.append({"name": "weather_visible", "status": "FAIL", "reason": reason})
        crashed = True
    else:
        _emit("weather_visible", "PASS", pass_reason)
        results.append({"name": "weather_visible", "status": "PASS", "reason": pass_reason})

    if (verbose or crashed) and not verbose:
        print()
        for line in raw_lines:
            print("    %s" % line)

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    print("[TEST] SUMMARY %d/%d %s" % (n_pass, len(results),
                                        "PASS" if n_pass == len(results) else "FAIL"))
    return results, crashed


def _emit(name, status, reason):
    if status == "PASS" and reason:
        print("[TEST] %s PASS: %s" % (name, reason))
    elif reason:
        print("[TEST] %s FAIL: %s" % (name, reason))
    else:
        print("[TEST] %s %s" % (name, status))


def _main():
    ap = argparse.ArgumentParser(description="Weather-visible UART smoke test")
    ap.add_argument("--port", default="COM13", help="Serial port (default: COM13)")
    ap.add_argument("--timeout", type=int, default=60,
                    help="Seconds to monitor UART (default: 60)")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every received line")
    args = ap.parse_args()

    results, crashed = run_weather_visible(args.port, args.timeout, args.verbose)
    ok = all(r["status"] == "PASS" for r in results) and not crashed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main()
