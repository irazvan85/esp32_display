"""Collect raw UART output from an ESP32 device and save to a timestamped log file.

Usage:
    python collect_uart_log.py [--port PORT] [--baud BAUD] [--duration SECS]
                               [--output FILE] [--reset]

Options:
    --port PORT       Serial port (default: COM13)
    --baud BAUD       Baud rate (default: 115200)
    --duration SECS   Capture duration in seconds (default: 300)
    --output FILE     Output log file path; if omitted, auto-named as
                      tools/logs/uart_raw_YYYYMMDD_HHMMSS.log
    --reset           Send Ctrl+D soft-reset before capture starts, then wait 2s
"""

import argparse
import os
import sys
import time
import datetime
import serial


LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
PROGRESS_INTERVAL = 30  # seconds


def _timestamp():
    """Return wall-clock timestamp string HH:MM:SS.mmm."""
    now = datetime.datetime.now()
    return "%02d:%02d:%02d.%03d" % (now.hour, now.minute, now.second, now.microsecond // 1000)


def _auto_output_path():
    now = datetime.datetime.now()
    filename = "uart_raw_%s.log" % now.strftime("%Y%m%d_%H%M%S")
    return os.path.join(LOGS_DIR, filename)


def main():
    parser = argparse.ArgumentParser(description="Collect ESP32 UART output to a timestamped log")
    parser.add_argument("--port", default="COM13", help="Serial port (default: COM13)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--duration", type=int, default=300,
                        help="Capture duration in seconds (default: 300)")
    parser.add_argument("--output", default=None,
                        help="Output log file path (auto-named if omitted)")
    parser.add_argument("--reset", action="store_true",
                        help="Send Ctrl+D soft-reset before capture, then wait 2s")
    args = parser.parse_args()

    output_path = args.output if args.output else _auto_output_path()
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as exc:
        print("[collect] ERROR: could not open %s: %s" % (args.port, exc))
        return 1

    lines = []
    try:
        if args.reset:
            ser.write(b"\x04")
            ser.flush()
            time.sleep(2)

        start = time.time()
        next_progress = start + PROGRESS_INTERVAL

        while True:
            elapsed = time.time() - start
            if elapsed >= args.duration:
                break

            raw = ser.readline()
            if raw:
                ts = _timestamp()
                text = raw.decode("utf-8", errors="replace").rstrip()
                entry = "[%s] %s" % (ts, text)
                lines.append(entry)
                print(entry)

            now = time.time()
            if now >= next_progress:
                elapsed_int = int(now - start)
                print("[collect] %ds / %ds elapsed — %d lines captured" % (
                    elapsed_int, args.duration, len(lines)))
                next_progress += PROGRESS_INTERVAL

    except KeyboardInterrupt:
        print("\n[collect] Interrupted — saving collected output...")
    finally:
        ser.close()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        if lines:
            f.write("\n")

    print("[collect] Done. %d lines written to %s" % (len(lines), output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
