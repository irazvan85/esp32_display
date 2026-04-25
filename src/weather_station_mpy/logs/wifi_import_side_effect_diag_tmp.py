"""Diagnostic script to detect import-time side effects before WiFi connect."""

print("[Diag] MARKER A")

print("[Diag] importing board")
import board
print("[Diag] imported board")

print("[Diag] importing app_state")
import app_state
print("[Diag] imported app_state")

print("[Diag] importing config.store.load_config")
from config.store import load_config
print("[Diag] imported config.store.load_config")

print("[Diag] importing services.metrics_service")
import services.metrics_service
print("[Diag] imported services.metrics_service")

print("[Diag] importing services.solar_service")
import services.solar_service
print("[Diag] imported services.solar_service")

print("[Diag] importing services.time_service")
import services.time_service
print("[Diag] imported services.time_service")

print("[Diag] importing services.weather_service")
import services.weather_service
print("[Diag] imported services.weather_service")

print("[Diag] importing services.wifi_service.WifiService")
from services.wifi_service import WifiService
print("[Diag] imported services.wifi_service.WifiService")

print("[Diag] importing ui.display_manager")
import ui.display_manager
print("[Diag] imported ui.display_manager")

print("[Diag] MARKER B")

cfg = load_config("config.json")
svc = WifiService(cfg)

import uasyncio as asyncio

ok = asyncio.run(svc.ensure_connected())
print("ensure_connected:", ok)
if ok:
    print("ip:", svc.ip())
