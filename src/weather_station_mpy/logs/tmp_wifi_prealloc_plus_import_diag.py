import gc
import time

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import network
from config.store import load_config
from services.wifi_service import WifiService

def prealloc_like_boot():
    wlan = network.WLAN(network.STA_IF)
    if wlan.active():
        try:
            wlan.active(False)
        except Exception:
            pass
        time.sleep_ms(300)
    wlan.active(True)
    try:
        wlan.config(reconnects=0)
    except Exception:
        pass
    try:
        wlan.disconnect()
    except Exception:
        pass
    time.sleep_ms(300)

async def main():
    prealloc_like_boot()
    gc.collect()
    print('[DIAG] heap after prealloc=%d' % gc.mem_free())
    from ui.display_manager import DisplayManager
    gc.collect()
    print('[DIAG] heap after display import=%d' % gc.mem_free())
    cfg = load_config('config.json')
    svc = WifiService(cfg)
    ok = await svc.ensure_connected(allow_scan_retry=False)
    print('[DIAG] ensure_connected=%s status=%s' % (ok, svc.last_status))
    if ok:
        print('[DIAG] ip=%s' % svc.ip())

asyncio.run(main())
