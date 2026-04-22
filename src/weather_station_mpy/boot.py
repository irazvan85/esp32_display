"""MicroPython boot script.

This keeps startup side effects minimal and leaves app logic to main.py.
"""

import gc

print("[BOOT] weather_station_mpy boot.py")
gc.collect()
