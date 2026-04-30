"""UART capture tool for ESP32 device logs.

Usage:
    python capture_uart.py [--port PORT] [--duration SECONDS] [--output FILE] [--reset]

Options:
    --port PORT       Serial port (default: COM13)
    --duration SECS   Capture duration in seconds (default: 90)
    --output FILE     Output log file path (default: src/weather_station_mpy/logs/uart_fix_loop_latest.txt)
    --reset           Send Ctrl+D soft-reset at start of capture (default: off)
"""

import argparse
import serial
import time

def main():
    parser = argparse.ArgumentParser(description="Capture ESP32 UART output")
    parser.add_argument("--port", default="COM13", help="Serial port")
    parser.add_argument("--duration", type=int, default=90, help="Capture duration in seconds")
    parser.add_argument("--output", default="src/weather_station_mpy/logs/uart_fix_loop_latest.txt",
                        help="Output log file path")
    parser.add_argument("--reset", action="store_true",
                        help="Send Ctrl+D soft-reset at start of capture")
    args = parser.parse_args()

    lines = []
    with serial.Serial(args.port, 115200, timeout=1) as s:
        if args.reset:
            s.write(b"\x04")  # Ctrl+D soft reset
            s.flush()
        start = time.time()
        while time.time() - start < args.duration:
            line = s.readline()
            if line:
                text = line.decode("utf-8", errors="replace").rstrip()
                lines.append(text)
                print(text)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("LOG_WRITTEN:", args.output)

if __name__ == "__main__":
    main()
