import gc

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

# Mirror main.py import pressure before first connect.
import board
from app_state import AppState
from compat import mem_free, mem_alloc, ticks_diff, ticks_ms
from config.store import ConfigNotReadyError, load_config
from services.time_service import TimeService
from services.wifi_service import WifiService
from ui.display_manager import DisplayManager


def heap_mark(tag):
    gc.collect()
    print("[DIAG] %s heap=%d" % (tag, gc.mem_free()))


async def main():
    heap_mark("after imports")
    cfg = load_config("config.json")
    heap_mark("after load_config")

    wifi_svc = WifiService(cfg)
    heap_mark("after WifiService")

    ok = await wifi_svc.ensure_connected(allow_scan_retry=False)
    print("[DIAG] ensure_connected=%s last_status=%s" % (ok, wifi_svc.last_status))
    if ok:
        print("[DIAG] ip=%s" % wifi_svc.ip())


asyncio.run(main())
