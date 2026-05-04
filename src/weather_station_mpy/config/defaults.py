"""Default configuration for first boot.

On first run, config/store.py writes these defaults to config.json and
stops execution until placeholders are replaced.
"""

DEFAULT_CONFIG = {
    "wifi": {
        "ssid": "your_wifi_ssid",
        "password": "your_wifi_password",
        "dns": "",
        "reconnects": 0,
        "pm": "performance",
        "prefer_bssid_scan": False,
        "min_heap_for_scan_bytes": 98_000,
        "assoc_fail_scan_min_heap_bytes": 40_000,
        "bssid": "",
        "connect_timeout_ms": 10_000,
        "transport_error_reconnect_threshold": 3,
        "check_interval_ms": 30_000,
        "startup_retries": 4,
        "startup_retry_ms": 2_000,
        "assoc_fail_startup_quick_retry_ms": 15_000,
        "offline_retry_ms": 5_000,
        "assoc_fail_backoff_max_s": 360,
        "assoc_fail_backoff_jitter_pct": 0,
        "assoc_fail_long_cooldown_after_n": 3,
        "assoc_fail_long_cooldown_s": 720,
        "assoc_fail_state_enabled": True,
        "assoc_fail_state_path": "wifi_assoc_fail_state.json",
        "legacy_first_connect_no_reset": True,
    },
    "weather": {
        "enabled": True,
        "startup_bootstrap": True,
        "api_key": "your_openweathermap_api_key",
        "city": "Bucharest",
        "country": "RO",
        "refresh_ms": 600_000,
        "retry_ms": 30_000,
        "offline_retry_ms": 5_000,
        "stale_ms": 1_800_000,
        "startup_retries": 3,
        "startup_retry_ms": 5_000,
        "dns_retry_initial_ms": 10_000,
    },
    "time": {
        "ntp_server": "pool.ntp.org",
        "ntp_retry_ms": 60_000,
        "clock_refresh_ms": 1_000,
        "tz_mode": "romania_eet_eest",
    },
    "solar": {
        "enabled": False,
        "app_id": "your_app_id",
        "app_secret": "your_app_secret",
        "email": "your@email.com",
        "pass_sha256": "sha256_hex_of_your_password",
        "station_id": 0,
        "refresh_ms": 300_000,
    },
    "metrics": {
        "enabled": False,
        "pc_url": "http://192.168.1.100:8765/api/system/metrics",
        "refresh_ms": 10_000,
        "stale_ms": 120_000,
        "timeout_ms": 3_000,
    },
    "ui": {
        "theme": "retro",
        "enabled_pages": [0, 1, 2, 3, 4, 5],
    },
    "web": {
        "enabled": False,
        "port": 80,
    },
    "display": {
        "backlight_on": True,
    },
}
