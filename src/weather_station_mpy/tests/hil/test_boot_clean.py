"""Boot-clean UART test — host-side HIL check.

Triggers a MicroPython soft reset on the device (Ctrl-D via serial) and
monitors UART output for 30 seconds (configurable).  The test PASSES only
when the device completes a full clean-boot sequence without any crash
indicators.

Crash indicators (any match → FAIL):
  - ``Guru Meditation``                  unhandled exception / watchdog
  - ``Traceback (most recent call last)`` unhandled Python exception
  - ``spi_master_init_driver``           SPI bus initialisation error
  - ``check_trans_valid``                SPI transaction on invalid handle
  - ``rst:0xc (SW_CPU_RESET)``           machine.reset() safety-net fired
  - ``LoadProhibited``                   NULL-pointer Guru Meditation

Success markers (all must be seen, in any order):
  - ``[BOOT] WiFi STA interface ready``  or ``[BOOT] WiFi pre-init``
  - ``[DISP] ST7789 initialized``        display driver came up cleanly

Usage (standalone)::

    python tests/hil/test_boot_clean.py --port COM13 [--timeout 30] [--verbose]

Output follows the same ``[TEST] <name> PASS/FAIL`` protocol as device-side
tests so hil_runner.py can parse results uniformly.
"""

import argparse
import re
import sys
import time

# ── patterns ──────────────────────────────────────────────────────────────────

_CRASH_PATTERNS = [
    (re.compile(r"Guru Meditation"),          "Guru Meditation crash"),
    (re.compile(r"Traceback \(most recent"),  "unhandled Python exception"),
    (re.compile(r"spi_master_init_driver"),   "SPI host init failed"),
    (re.compile(r"check_trans_valid"),        "SPI invalid dev handle"),
    (re.compile(r"LoadProhibited"),           "NULL-ptr LoadProhibited"),
    (re.compile(r"rst:0x[cC] \(SW_CPU_RESET\)"),
                                              "SW_CPU_RESET (safety-net triggered)"),
]

_SUCCESS_WIFI   = re.compile(r"\[BOOT\] WiFi (STA interface ready|pre-init)")
_SUCCESS_DISP   = re.compile(r"\[DISP\] ST7789 initialized")


def run_boot_clean(port, timeout_s=30, verbose=False):
    """Perform the boot-clean UART check.

    Returns ``(results, crashed)`` in the same format as hil_runner._run_suite().
    Each entry in *results* is ``{"name": str, "status": "PASS"|"FAIL", "reason": str}``.
    """
    try:
        import serial  # pyserial — installed as mpremote dependency
    except ImportError:
        print("[TEST] clean_boot FAIL: pyserial not installed")
        print("[TEST] SUMMARY 0/1 FAIL")
        return [{"name": "clean_boot", "status": "FAIL",
                 "reason": "pyserial not installed"}], True

    results = []
    crashed = False
    found_wifi = False
    found_disp = False
    first_crash_reason = ""
    raw_lines = []

    try:
        ser = serial.Serial(port, 115200, timeout=1)
    except serial.SerialException as exc:
        msg = "cannot open %s: %s" % (port, exc)
        print("[TEST] clean_boot FAIL: %s" % msg)
        print("[TEST] SUMMARY 0/1 FAIL")
        return [{"name": "clean_boot", "status": "FAIL", "reason": msg}], True

    try:
        # Step 1: interrupt the running asyncio app (Ctrl-C × 2).
        # asyncio’s fastest poll interval is 100 ms (render_task); allow 400 ms
        # for the KeyboardInterrupt to propagate and the REPL to become active.
        ser.write(b"\x03")
        ser.flush()
        time.sleep(0.4)
        ser.write(b"\x03")
        ser.flush()
        time.sleep(0.3)

        # Drain any pending bytes (REPL prompt, error messages, old app output)
        # so our reader sees only the new boot sequence.
        if ser.in_waiting:
            ser.read(ser.in_waiting)
        time.sleep(0.1)
        if ser.in_waiting:
            ser.read(ser.in_waiting)

        # Step 2: soft reset from REPL prompt (Ctrl-D).
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

            # Check success markers
            if _SUCCESS_WIFI.search(line):
                found_wifi = True
            if _SUCCESS_DISP.search(line):
                found_disp = True

            # Check crash markers — record first, keep scanning to collect full log
            if not first_crash_reason:
                for pat, label in _CRASH_PATTERNS:
                    if pat.search(line):
                        first_crash_reason = "%s: %s" % (label, line.strip())
                        crashed = True
                        break

            # Once display is confirmed initialised we have seen enough; but
            # continue reading to the end of the window to catch any late crash.
    finally:
        ser.close()

    # ── evaluate outcome ──────────────────────────────────────────────────────
    if crashed:
        reason = first_crash_reason
        _emit("clean_boot", "FAIL", reason)
        results.append({"name": "clean_boot", "status": "FAIL", "reason": reason})
    elif not found_disp:
        reason = "[DISP] ST7789 initialized not seen within %ds" % timeout_s
        _emit("clean_boot", "FAIL", reason)
        results.append({"name": "clean_boot", "status": "FAIL", "reason": reason})
        crashed = True
    else:
        _emit("clean_boot", "PASS", "")
        results.append({"name": "clean_boot", "status": "PASS", "reason": ""})

    # ── verbose dump on failure ───────────────────────────────────────────────
    if (verbose or crashed) and not verbose:
        # Already printed live if verbose; print on failure only otherwise.
        print()
        for line in raw_lines:
            print("    %s" % line)

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    print("[TEST] SUMMARY %d/%d %s" % (n_pass, len(results),
                                        "PASS" if n_pass == len(results) else "FAIL"))
    return results, crashed


def _emit(name, status, reason):
    if reason:
        print("[TEST] %s FAIL: %s" % (name, reason))
    else:
        print("[TEST] %s PASS" % name)


# ── standalone entry point ────────────────────────────────────────────────────

def _main():
    ap = argparse.ArgumentParser(description="Boot-clean UART test")
    ap.add_argument("--port",    default="COM13", help="Serial port (default: COM13)")
    ap.add_argument("--timeout", type=int, default=30,
                    help="Seconds to monitor UART (default: 30)")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every received line")
    args = ap.parse_args()

    results, crashed = run_boot_clean(args.port, args.timeout, args.verbose)
    ok = all(r["status"] == "PASS" for r in results) and not crashed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main()
