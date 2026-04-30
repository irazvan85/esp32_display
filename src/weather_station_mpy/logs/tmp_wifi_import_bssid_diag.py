import gc
import time

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import network
from config.store import load_config
from services.wifi_service import WifiService
from ui.display_manager import DisplayManager

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
    print('[DIAG] heap=%d' % gc.mem_free())
    cfg = load_config('config.json')
    cfg['wifi']['bssid'] = '86:93:b2:1a:12:0e'
    svc = WifiService(cfg)
    ok = await svc.ensure_connected(allow_scan_retry=False)
    print('[DIAG] ensure_connected=%s status=%s' % (ok, svc.last_status))
    if ok:
        print('[DIAG] ip=%s' % svc.ip())

asyncio.run(main())
