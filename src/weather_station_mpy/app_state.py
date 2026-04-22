"""Mutable runtime state shared by asyncio tasks."""


class AppState:
    def __init__(self):
        self.page = 0
        self.page_dirty = True
        self.status_dirty = True
        self.weather_dirty = True
        self.forecast_dirty = True
        self.solar_dirty = True
        self.metrics_dirty = True

        self.wifi_online = False
        self.time_synced = False

        self.last_weather_fetch_ms = 0
        self.last_forecast_fetch_ms = 0
        self.last_solar_fetch_ms = 0
        self.last_metrics_fetch_ms = 0
        self.metrics_stale_ms = 120_000

        self.clock_dirty = True
        self.date_dirty = True

        self.weather = {
            "valid": False,
            "temp_c": 0.0,
            "feels_like_c": 0.0,
            "humidity": 0,
            "condition": "---",
            "condition_id": 800,
        }

        self.forecast = []

        self.solar = {
            "valid": False,
            "generation_w": 0.0,
            "grid_w": 0.0,
            "battery_soc": 0.0,
            "last_update_ts": 0,
        }

        self.metrics_subpage = 0  # 0 = View A (CPU/RAM/DSK/TEMP), 1 = View B (GPU)
        self.metrics = {
            "valid": False,
            "cpu_pct": 0.0,
            "ram_pct": 0.0,
            "disk_pct": 0.0,
            "temp_c": None,
            "gpu_pct": None,
            "gpu_temp_c": None,
            "uptime_s": 0,
            "ts": 0,
        }

    def mark_all_dirty(self):
        self.page_dirty = True
        self.status_dirty = True
        self.weather_dirty = True
        self.forecast_dirty = True
        self.solar_dirty = True
        self.metrics_dirty = True
        self.clock_dirty = True
        self.date_dirty = True
