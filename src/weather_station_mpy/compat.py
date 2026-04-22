"""Compatibility helpers for running MicroPython-oriented code in tooling.

These helpers keep runtime behavior correct on-device while reducing noise from
desktop static analysis.
"""

import gc
import time


def ticks_ms():
    try:
        return time.ticks_ms()  # type: ignore[attr-defined]
    except AttributeError:
        return int(time.time() * 1000)


def ticks_diff(now_ms, then_ms):
    try:
        return time.ticks_diff(now_ms, then_ms)  # type: ignore[attr-defined]
    except AttributeError:
        return now_ms - then_ms


def mem_free():
    if hasattr(gc, "mem_free"):
        return gc.mem_free()
    return -1