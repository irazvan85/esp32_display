import uasyncio as asyncio
from config.store import load_config
from services.wifi_service import WifiService


cfg = load_config('config.json')
svc = WifiService(cfg)
print('before ensure is_connected:', svc.is_connected())
res = asyncio.run(svc.ensure_connected())
print('ensure result:', res)
print('after ensure is_connected:', svc.is_connected())
if svc.is_connected():
    print('ip:', svc.ip())