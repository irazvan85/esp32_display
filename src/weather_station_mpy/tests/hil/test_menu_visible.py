"""Menu-visible UART sweep test - host-side HIL check.

Validates that every enabled display page can be selected through the UART
capture control protocol and then observed in a fresh snapshot.

Usage (standalone):

    python tests/hil/test_menu_visible.py --port COM13 [--timeout 90] [--verbose]
"""

import argparse
import json
import re
import sys
import time


_CRASH_PATTERNS = [
    (re.compile(r"Guru Meditation"), "Guru Meditation crash"),
    (re.compile(r"Traceback \(most recent"), "unhandled Python exception"),
    (re.compile(r"LoadProhibited"), "NULL-ptr LoadProhibited"),
]


def _decode_line(raw):
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def _detect_crash(line):
    for pat, label in _CRASH_PATTERNS:
        if pat.search(line):
            return "%s: %s" % (label, line.strip())
    return ""


def _normalize_pages(raw_pages, fallback_page=0):
    pages = []
    if isinstance(raw_pages, list):
        for item in raw_pages:
            if isinstance(item, int) and 0 <= item <= 5 and item not in pages:
                pages.append(item)

    if not pages:
        if isinstance(fallback_page, int) and 0 <= fallback_page <= 5:
            return [fallback_page]
        return [0]

    return pages


def _request_snapshot(ser, timeout_s, raw_lines, verbose=False):
    ser.write(b"!SNAP\r\n")
    ser.flush()

    started = False
    payload_lines = []
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        line = _decode_line(raw)
        raw_lines.append(line)
        if verbose:
            print("    %s" % line)

        crash_reason = _detect_crash(line)
        if crash_reason:
            return None, crash_reason, True

        if line == ">>SNAP_START":
            started = True
            payload_lines = []
            continue

        if line == ">>SNAP_END" and started:
            try:
                return json.loads("".join(payload_lines)), "", False
            except json.JSONDecodeError as exc:
                return None, "snapshot JSON decode error: %s" % exc, False

        if line.startswith(">>SNAP_ERR"):
            return None, line, False

        if started:
            payload_lines.append(line)

    return None, "snapshot timeout", False


def _send_page_command(ser, page, timeout_s, raw_lines, verbose=False):
    command = "!PAGE %d\r\n" % page
    ser.write(command.encode("utf-8"))
    ser.flush()

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        line = _decode_line(raw)
        raw_lines.append(line)
        if verbose:
            print("    %s" % line)

        crash_reason = _detect_crash(line)
        if crash_reason:
            return False, crash_reason, True

        if line.startswith(">>CMD_OK"):
            return True, line, False
        if line.startswith(">>CMD_ERR"):
            return False, line, False

    return False, "command ack timeout for !PAGE %d" % page, False


def run_menu_visible(port, timeout_s=90, verbose=False):
    """Validate that all enabled menu pages are reachable via UART commands."""
    try:
        import serial
    except ImportError:
        print("[TEST] menu_visible FAIL: pyserial not installed")
        print("[TEST] SUMMARY 0/1 FAIL")
        return [{"name": "menu_visible", "status": "FAIL", "reason": "pyserial not installed"}], True

    results = []
    crashed = False
    fail_reason = ""
    pass_reason = ""
    raw_lines = []

    try:
        ser = serial.Serial(port, 115200, timeout=1)
    except serial.SerialException as exc:
        msg = "cannot open %s: %s" % (port, exc)
        print("[TEST] menu_visible FAIL: %s" % msg)
        print("[TEST] SUMMARY 0/1 FAIL")
        return [{"name": "menu_visible", "status": "FAIL", "reason": msg}], True

    try:
        if ser.in_waiting:
            ser.read(ser.in_waiting)
        time.sleep(0.1)
        if ser.in_waiting:
            ser.read(ser.in_waiting)

        initial = None
        init_err = "snapshot timeout"
        init_crash = False
        start_wait = time.time()
        init_deadline = start_wait + timeout_s

        while time.time() < init_deadline:
            remaining_s = int(max(1, init_deadline - time.time()))
            snap_wait_s = 8 if remaining_s > 8 else remaining_s

            initial, init_err, init_crash = _request_snapshot(
                ser, timeout_s=snap_wait_s, raw_lines=raw_lines, verbose=verbose
            )
            if init_crash:
                break
            if initial is not None:
                break

            # During long startup WiFi retries, uart_capture_task may not be
            # running yet. Keep retrying until timeout_s budget is exhausted.
            if verbose:
                print("    [TEST:info] waiting for initial snapshot: %s" % init_err)
            time.sleep(0.5)

        if init_crash:
            fail_reason = init_err
            crashed = True
        elif initial is None:
            fail_reason = "initial snapshot failed: %s" % init_err
        else:
            pages = _normalize_pages(initial.get("enabled_pages"), initial.get("page", 0))
            print("[TEST:info] enabled_pages=%s" % pages)

            page_errors = []
            for page in pages:
                ok_cmd, cmd_msg, cmd_crash = _send_page_command(
                    ser,
                    page,
                    timeout_s=5,
                    raw_lines=raw_lines,
                    verbose=verbose,
                )
                if cmd_crash:
                    page_errors.append("page %d command crash: %s" % (page, cmd_msg))
                    crashed = True
                    break
                if not ok_cmd:
                    page_errors.append("page %d command failed: %s" % (page, cmd_msg))
                    continue

                time.sleep(0.25)

                snap, snap_err, snap_crash = _request_snapshot(
                    ser,
                    timeout_s=10,
                    raw_lines=raw_lines,
                    verbose=verbose,
                )
                if snap_crash:
                    page_errors.append("page %d snapshot crash: %s" % (page, snap_err))
                    crashed = True
                    break
                if snap is None:
                    page_errors.append("page %d snapshot failed: %s" % (page, snap_err))
                    continue

                observed = snap.get("page")
                print("[TEST:info] page %d observed %s" % (page, observed))
                if observed != page:
                    page_errors.append("page %d observed %s" % (page, observed))

            if page_errors:
                fail_reason = "; ".join(page_errors[:3])
                if len(page_errors) > 3:
                    fail_reason += " ..."
            else:
                pass_reason = "captured %d/%d pages" % (len(pages), len(pages))

    finally:
        ser.close()

    if crashed:
        _emit("menu_visible", "FAIL", fail_reason or "crash detected")
        results.append({"name": "menu_visible", "status": "FAIL", "reason": fail_reason or "crash detected"})
    elif fail_reason:
        _emit("menu_visible", "FAIL", fail_reason)
        results.append({"name": "menu_visible", "status": "FAIL", "reason": fail_reason})
        crashed = True
    else:
        _emit("menu_visible", "PASS", pass_reason)
        results.append({"name": "menu_visible", "status": "PASS", "reason": pass_reason})

    if (verbose or crashed) and not verbose:
        print()
        for line in raw_lines:
            print("    %s" % line)

    n_pass = sum(1 for item in results if item["status"] == "PASS")
    summary = "PASS" if n_pass == len(results) else "FAIL"
    print("[TEST] SUMMARY %d/%d %s" % (n_pass, len(results), summary))
    return results, crashed


def _emit(name, status, reason):
    if status == "PASS" and reason:
        print("[TEST] %s PASS: %s" % (name, reason))
    elif reason:
        print("[TEST] %s FAIL: %s" % (name, reason))
    else:
        print("[TEST] %s %s" % (name, status))


def _main():
    ap = argparse.ArgumentParser(description="Menu-visible UART sweep test")
    ap.add_argument("--port", default="COM13", help="Serial port (default: COM13)")
    ap.add_argument("--timeout", type=int, default=90, help="Seconds to monitor UART (default: 90)")
    ap.add_argument("--verbose", action="store_true", help="Print every received line")
    args = ap.parse_args()

    results, crashed = run_menu_visible(args.port, args.timeout, args.verbose)
    ok = all(item["status"] == "PASS" for item in results) and not crashed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main()
