"""Tests for WeatherService — parsing, aggregation, day naming.

HTTP calls are mocked; no network access required.
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWeatherServiceDayName(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.WeatherService = WeatherService

    def test_known_dates(self):
        # 2025-01-06 is a Monday
        self.assertEqual(self.WeatherService._day_name("2025-01-06"), "Mon")
        # 2025-01-12 is a Sunday
        self.assertEqual(self.WeatherService._day_name("2025-01-12"), "Sun")
        # 2026-04-23 is a Thursday
        self.assertEqual(self.WeatherService._day_name("2026-04-23"), "Thu")

    def test_returns_three_char_string(self):
        day = self.WeatherService._day_name("2026-01-01")
        self.assertEqual(len(day), 3)
        self.assertIn(day, ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))


class TestWeatherServiceAggregate(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.svc = WeatherService({})

    def _entry(self, date_str, time_str="06:00:00", temp=15.0, t_min=10.0,
               t_max=20.0, humidity=60, cond="Clouds", cond_id=801):
        return {
            "dt_txt": "%s %s" % (date_str, time_str),
            "main": {
                "temp": temp, "temp_min": t_min, "temp_max": t_max,
                "humidity": humidity,
            },
            "weather": [{"main": cond, "id": cond_id}],
        }

    def test_groups_by_date(self):
        entries = [
            self._entry("2026-04-24", "06:00:00"),
            self._entry("2026-04-24", "12:00:00"),
            self._entry("2026-04-25", "06:00:00"),
        ]
        result = self.svc._aggregate(entries)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2026-04-24")
        self.assertEqual(result[1]["date"], "2026-04-25")

    def test_min_max_tracking(self):
        entries = [
            self._entry("2026-04-24", temp=15.0, t_min=10.0, t_max=18.0),
            self._entry("2026-04-24", temp=20.0, t_min=13.0, t_max=25.0),
        ]
        result = self.svc._aggregate(entries)
        self.assertAlmostEqual(result[0]["temp_min"], 10.0)
        self.assertAlmostEqual(result[0]["temp_max"], 25.0)

    def test_noon_sample_preferred(self):
        entries = [
            self._entry("2026-04-24", "06:00:00", cond="Clouds", cond_id=801),
            self._entry("2026-04-24", "12:00:00", cond="Rain",   cond_id=500),
        ]
        result = self.svc._aggregate(entries)
        self.assertEqual(result[0]["condition"], "Rain")
        self.assertEqual(result[0]["condition_id"], 500)

    def test_capped_at_five_days(self):
        entries = []
        for day in range(1, 9):
            entries.append(self._entry("2026-04-%02d" % day))
        result = self.svc._aggregate(entries)
        self.assertLessEqual(len(result), 5)

    def test_result_structure(self):
        entries = [self._entry("2026-04-24")]
        result = self.svc._aggregate(entries)
        required_keys = {"date", "day", "temp_min", "temp_max", "humidity",
                         "condition", "condition_id"}
        self.assertEqual(set(result[0].keys()), required_keys)

    def test_short_dt_txt_skipped(self):
        entries = [{"dt_txt": "bad", "main": {}, "weather": [{}]}]
        result = self.svc._aggregate(entries)
        self.assertEqual(result, [])


class TestWeatherServiceFetchCurrent(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.WeatherService = WeatherService

    def test_fetch_current_parses_response(self):
        cfg = {
            "weather": {
                "api_key": "testkey",
                "city": "London",
                "country": "GB",
            }
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "weather": [{"main": "Clear", "id": 800}],
            "main": {
                "temp": 18.5, "feels_like": 17.0, "humidity": 65,
            },
            "wind": {"speed": 3.2},
        }

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(cfg)
            result = svc.fetch_current()

            self.assertTrue(result["valid"])
            self.assertAlmostEqual(result["temp_c"], 18.5)
            self.assertAlmostEqual(result["feels_like_c"], 17.0)
            self.assertEqual(result["humidity"], 65)
            self.assertEqual(result["condition"], "Clear")
            self.assertEqual(result["condition_id"], 800)
            self.assertAlmostEqual(result["wind_ms"], 3.2)
        finally:
            ws_module.requests = original_requests


class TestWeatherServiceForecastBundle(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.WeatherService = WeatherService
        self.cfg = {
            "weather": {
                "api_key": "testkey",
                "city": "London",
                "country": "GB",
            }
        }

    @staticmethod
    def _forecast_entry(date_str, time_str, temp_c, rain_3h=0.0, snow_3h=0.0):
        return {
            "dt_txt": "%s %s" % (date_str, time_str),
            "main": {
                "temp": temp_c,
                "temp_min": temp_c - 1.0,
                "temp_max": temp_c + 1.0,
                "humidity": 60,
            },
            "weather": [{"main": "Clouds", "id": 801}],
            "rain": {"3h": rain_3h},
            "snow": {"3h": snow_3h},
        }

    def test_fetch_forecast_bundle_trend_shape_precip_cap_and_first_day(self):
        entries = []
        for i in range(10):
            hour = (i * 3) % 24
            entries.append(
                self._forecast_entry(
                    "2026-04-24",
                    "%02d:00:00" % hour,
                    10.0 + i,
                    rain_3h=0.2 * i,
                    snow_3h=0.1 * i,
                )
            )

        # Second day should not appear in trend output.
        entries.append(self._forecast_entry("2026-04-25", "00:00:00", 99.0, rain_3h=9.0, snow_3h=9.0))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"list": entries}

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(self.cfg)
            daily, trend = svc.fetch_forecast_bundle()

            self.assertIsInstance(daily, list)
            self.assertIsInstance(trend, list)
            self.assertEqual(len(trend), 8)

            for point in trend:
                self.assertEqual(set(point.keys()), {"hour", "temp_c", "precip_mm"})

            # rain + snow accumulation is used.
            self.assertAlmostEqual(trend[3]["precip_mm"], (0.2 * 3) + (0.1 * 3))

            # Trend is only extracted from the first forecast date.
            self.assertLessEqual(max(p["hour"] for p in trend), 23)
            self.assertNotIn(99.0, [p["temp_c"] for p in trend])
        finally:
            ws_module.requests = original_requests

    def test_fetch_forecast_compat_returns_daily_list_shape(self):
        entries = [
            self._forecast_entry("2026-04-24", "06:00:00", 12.0),
            self._forecast_entry("2026-04-24", "12:00:00", 14.0),
            self._forecast_entry("2026-04-25", "06:00:00", 16.0),
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"list": entries}

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(self.cfg)
            daily = svc.fetch_forecast()

            self.assertIsInstance(daily, list)
            self.assertGreaterEqual(len(daily), 1)
            required_keys = {
                "date",
                "day",
                "temp_min",
                "temp_max",
                "humidity",
                "condition",
                "condition_id",
            }
            self.assertEqual(set(daily[0].keys()), required_keys)
        finally:
            ws_module.requests = original_requests

    def test_fetch_current_raises_on_http_error(self):
        cfg = {"weather": {"api_key": "k", "city": "X", "country": "Y"}}
        mock_response = MagicMock()
        mock_response.status_code = 401

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(cfg)
            with self.assertRaises(RuntimeError):
                svc.fetch_current()
        finally:
            ws_module.requests = original_requests


class TestWeatherServiceGetRetry(unittest.TestCase):
    """Tests that _get() retries on transient DNS errors (-202, -203)."""

    def setUp(self):
        from services.weather_service import WeatherService
        import services.weather_service as ws_module
        self.WeatherService = WeatherService
        self.ws_module = ws_module

    def _make_svc(self):
        return self.WeatherService({
            "weather": {"api_key": "k", "city": "X", "country": "YY"}
        })

    def test_retry_on_eai_memory_succeeds_second_attempt(self):
        """OSError(-203) on first call → succeed on second → no exception raised."""
        good_response = MagicMock()
        good_response.status_code = 200
        good_response.json.return_value = {
            "weather": [{"main": "Clear", "id": 800}],
            "main": {"temp": 10.0, "feels_like": 9.0, "humidity": 50},
            "wind": {"speed": 1.0},
        }

        original = self.ws_module.requests
        mock_requests = MagicMock()
        mock_requests.get.side_effect = [OSError(-203), good_response]
        self.ws_module.requests = mock_requests
        try:
            svc = self._make_svc()
            result = svc.fetch_current()
            self.assertTrue(result["valid"])
            self.assertEqual(mock_requests.get.call_count, 2)
        finally:
            self.ws_module.requests = original

    def test_retry_on_eai_fail_succeeds_third_attempt(self):
        """OSError(-202) twice → succeed on third attempt."""
        good_response = MagicMock()
        good_response.status_code = 200
        good_response.json.return_value = {
            "weather": [{"main": "Clouds", "id": 801}],
            "main": {"temp": 5.0, "feels_like": 3.0, "humidity": 80},
            "wind": {"speed": 0.5},
        }

        original = self.ws_module.requests
        mock_requests = MagicMock()
        mock_requests.get.side_effect = [OSError(-202), OSError(-202), good_response]
        self.ws_module.requests = mock_requests
        try:
            svc = self._make_svc()
            result = svc.fetch_current()
            self.assertTrue(result["valid"])
            self.assertEqual(mock_requests.get.call_count, 3)
        finally:
            self.ws_module.requests = original

    def test_all_retries_exhausted_raises_last_exc(self):
        """Three consecutive OSError(-203) should raise the last one."""
        original = self.ws_module.requests
        mock_requests = MagicMock()
        mock_requests.get.side_effect = [OSError(-203), OSError(-203), OSError(-203)]
        self.ws_module.requests = mock_requests
        try:
            svc = self._make_svc()
            with self.assertRaises(OSError) as ctx:
                svc.fetch_current()
            self.assertEqual(ctx.exception.args[0], -203)
            self.assertEqual(mock_requests.get.call_count, 3)
        finally:
            self.ws_module.requests = original

    def test_non_dns_oserror_not_retried(self):
        """OSError(111) ECONNREFUSED must NOT be retried."""
        original = self.ws_module.requests
        mock_requests = MagicMock()
        mock_requests.get.side_effect = OSError(111)
        self.ws_module.requests = mock_requests
        try:
            svc = self._make_svc()
            with self.assertRaises(OSError) as ctx:
                svc.fetch_current()
            self.assertEqual(ctx.exception.args[0], 111)
            self.assertEqual(mock_requests.get.call_count, 1)  # no retry
        finally:
            self.ws_module.requests = original

    def test_mixed_dns_errors_eventually_raises(self):
        """-202 then -203 then -203: all DNS, all retried, final -203 is raised."""
        original = self.ws_module.requests
        mock_requests = MagicMock()
        mock_requests.get.side_effect = [OSError(-202), OSError(-203), OSError(-203)]
        self.ws_module.requests = mock_requests
        try:
            svc = self._make_svc()
            with self.assertRaises(OSError) as ctx:
                svc.fetch_current()
            self.assertEqual(ctx.exception.args[0], -203)
            self.assertEqual(mock_requests.get.call_count, 3)
        finally:
            self.ws_module.requests = original


class TestWeatherServiceDnsFailStreak(unittest.TestCase):
    """dns_fail_streak increments on DNS errors, resets on success."""

    def setUp(self):
        from services.weather_service import WeatherService
        import services.weather_service as ws_module
        self.WeatherService = WeatherService
        self.ws_module = ws_module

    def _cfg(self):
        return {"weather": {"api_key": "k", "city": "X", "country": "YY"}}

    def _good_response(self):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            "weather": [{"main": "Clear", "id": 800}],
            "main": {"temp": 10.0, "feels_like": 9.0, "humidity": 50},
            "wind": {"speed": 1.0},
        }
        return r

    def test_streak_starts_at_zero(self):
        svc = self.WeatherService(self._cfg())
        self.assertEqual(svc.dns_fail_streak, 0)

    def test_streak_increments_on_eai_memory(self):
        """After exhausting all _get() retries with -203, streak = 1."""
        original = self.ws_module.requests
        mock_req = MagicMock()
        mock_req.get.side_effect = [OSError(-203), OSError(-203), OSError(-203)]
        self.ws_module.requests = mock_req
        try:
            svc = self.WeatherService(self._cfg())
            with self.assertRaises(OSError):
                svc.fetch_current()
            self.assertEqual(svc.dns_fail_streak, 1)
        finally:
            self.ws_module.requests = original

    def test_streak_increments_on_eai_fail(self):
        """After exhausting all _get() retries with -202, streak = 1."""
        original = self.ws_module.requests
        mock_req = MagicMock()
        mock_req.get.side_effect = [OSError(-202), OSError(-202), OSError(-202)]
        self.ws_module.requests = mock_req
        try:
            svc = self.WeatherService(self._cfg())
            with self.assertRaises(OSError):
                svc.fetch_current()
            self.assertEqual(svc.dns_fail_streak, 1)
        finally:
            self.ws_module.requests = original

    def test_streak_resets_on_success(self):
        """Successful fetch resets streak to 0 regardless of previous value."""
        original = self.ws_module.requests
        mock_req = MagicMock()
        # First call fails with DNS, second call succeeds
        mock_req.get.side_effect = [OSError(-203), OSError(-203), OSError(-203),
                                    self._good_response()]
        self.ws_module.requests = mock_req
        try:
            svc = self.WeatherService(self._cfg())
            with self.assertRaises(OSError):
                svc.fetch_current()
            self.assertEqual(svc.dns_fail_streak, 1)
            # Now simulate a successful call
            mock_req.get.side_effect = [self._good_response()]
            result = svc.fetch_current()
            self.assertTrue(result["valid"])
            self.assertEqual(svc.dns_fail_streak, 0)
        finally:
            self.ws_module.requests = original

    def test_streak_accumulates_across_multiple_failures(self):
        """Each call that fully fails DNS increments by 1."""
        original = self.ws_module.requests
        mock_req = MagicMock()
        self.ws_module.requests = mock_req
        try:
            svc = self.WeatherService(self._cfg())
            # Two rounds of full DNS failures
            mock_req.get.side_effect = [OSError(-203)] * 3
            with self.assertRaises(OSError):
                svc.fetch_current()
            self.assertEqual(svc.dns_fail_streak, 1)

            mock_req.get.side_effect = [OSError(-202)] * 3
            with self.assertRaises(OSError):
                svc.fetch_current()
            self.assertEqual(svc.dns_fail_streak, 2)
        finally:
            self.ws_module.requests = original

    def test_non_dns_error_does_not_increment_streak(self):
        """OSError(111) ECONNREFUSED must NOT increment dns_fail_streak."""
        original = self.ws_module.requests
        mock_req = MagicMock()
        mock_req.get.side_effect = OSError(111)
        self.ws_module.requests = mock_req
        try:
            svc = self.WeatherService(self._cfg())
            with self.assertRaises(OSError):
                svc.fetch_current()
            self.assertEqual(svc.dns_fail_streak, 0)
        finally:
            self.ws_module.requests = original

    def test_reset_dns_cache_is_callable(self):
        """reset_dns_cache() must exist and be callable without arguments."""
        svc = self.WeatherService(self._cfg())
        # Should not raise
        svc.reset_dns_cache()


class TestWeatherServiceGetNoSleep(unittest.TestCase):
    """_get() must not block via time.sleep — all retries must be CPU-only."""

    def setUp(self):
        from services.weather_service import WeatherService
        import services.weather_service as ws_module
        self.WeatherService = WeatherService
        self.ws_module = ws_module

    def test_get_does_not_import_time(self):
        """weather_service module must not import time (no blocking sleep)."""
        import services.weather_service as ws_module
        # time must not be a module-level name in weather_service
        self.assertFalse(hasattr(ws_module, 'time'),
                         "weather_service must not import 'time' (it would enable time.sleep)")

    def test_default_timeout_is_3s(self):
        """_get() default timeout_s must be 3 (not 5)."""
        import inspect
        from services.weather_service import WeatherService
        sig = inspect.signature(WeatherService._get)
        default = sig.parameters.get("timeout_s")
        self.assertIsNotNone(default, "_get must have timeout_s parameter")
        self.assertEqual(default.default, 3, "_get default timeout_s must be 3s (not 5s)")


class TestWeatherServiceRobustness(unittest.TestCase):
    """WeatherService handles partial / missing API response fields gracefully."""

    def setUp(self):
        from services.weather_service import WeatherService
        import services.weather_service as ws_module
        self.WeatherService = WeatherService
        self.ws_module = ws_module
        self.cfg = {"weather": {"api_key": "k", "city": "X", "country": "YY"}}

    def _mock_get(self, payload, status=200):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = payload
        mock_req = MagicMock()
        mock_req.get.return_value = r
        return mock_req

    def test_missing_weather_array_uses_defaults(self):
        """Empty 'weather' list in OWM response must not raise."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get({"weather": [], "main": {"temp": 5.0}, "wind": {}})
        try:
            svc = self.WeatherService(self.cfg)
            result = svc.fetch_current()
            self.assertTrue(result["valid"])
            self.assertEqual(result["condition"], "---")
        finally:
            self.ws_module.requests = original

    def test_missing_main_uses_defaults(self):
        """Missing 'main' key in OWM response must not raise."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get(
            {"weather": [{"main": "Clear", "id": 800}], "main": {}, "wind": {}}
        )
        try:
            svc = self.WeatherService(self.cfg)
            result = svc.fetch_current()
            self.assertTrue(result["valid"])
            self.assertAlmostEqual(result["temp_c"], 0.0)
            self.assertEqual(result["humidity"], 0)
        finally:
            self.ws_module.requests = original

    def test_missing_wind_uses_zero(self):
        """Missing 'wind' key results in wind_ms=0.0."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get(
            {"weather": [{"main": "Rain", "id": 500}], "main": {"temp": 10.0}, "wind": {}}
        )
        try:
            svc = self.WeatherService(self.cfg)
            result = svc.fetch_current()
            self.assertAlmostEqual(result["wind_ms"], 0.0)
        finally:
            self.ws_module.requests = original

    def test_http_429_rate_limit_raises_runtime_error(self):
        """HTTP 429 (rate limit) must raise RuntimeError, not hang."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get({}, status=429)
        try:
            svc = self.WeatherService(self.cfg)
            with self.assertRaises(RuntimeError):
                svc.fetch_current()
        finally:
            self.ws_module.requests = original

    def test_http_500_raises_runtime_error(self):
        """HTTP 500 must raise RuntimeError."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get({}, status=500)
        try:
            svc = self.WeatherService(self.cfg)
            with self.assertRaises(RuntimeError):
                svc.fetch_current()
        finally:
            self.ws_module.requests = original

    def test_fetch_current_result_has_fetched_ms(self):
        """Successful result must include 'fetched_ms' integer key."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get({
            "weather": [{"main": "Clear", "id": 800}],
            "main": {"temp": 15.0, "feels_like": 14.0, "humidity": 60},
            "wind": {"speed": 2.0},
        })
        try:
            svc = self.WeatherService(self.cfg)
            result = svc.fetch_current()
            self.assertIn("fetched_ms", result)
            self.assertIsInstance(result["fetched_ms"], int)
        finally:
            self.ws_module.requests = original

    def test_forecast_empty_list_returns_empty_daily(self):
        """OWM forecast with empty 'list' must return empty daily list (no crash)."""
        original = self.ws_module.requests
        self.ws_module.requests = self._mock_get({"list": []})
        try:
            svc = self.WeatherService(self.cfg)
            daily, trend = svc.fetch_forecast_bundle()
            self.assertEqual(daily, [])
            self.assertEqual(trend, [])
        finally:
            self.ws_module.requests = original


class TestWeatherBootstrapConfig(unittest.TestCase):
    """startup_bootstrap must be True in DEFAULT_CONFIG for weather data at boot."""

    def test_startup_bootstrap_enabled_by_default(self):
        """DEFAULT_CONFIG weather.startup_bootstrap must be True."""
        from config.defaults import DEFAULT_CONFIG
        weather_cfg = DEFAULT_CONFIG.get("weather", {})
        self.assertTrue(
            weather_cfg.get("startup_bootstrap", False),
            "DEFAULT_CONFIG['weather']['startup_bootstrap'] must be True — "
            "without it, weather data is not fetched at boot and OWM DNS "
            "failures cause persistent -203 errors from a stuck lwIP DNS slot."
        )

    def test_weather_has_dns_retry_ms(self):
        """DEFAULT_CONFIG weather must have dns_retry_initial_ms."""
        from config.defaults import DEFAULT_CONFIG
        weather_cfg = DEFAULT_CONFIG.get("weather", {})
        self.assertIn("dns_retry_initial_ms", weather_cfg,
                      "dns_retry_initial_ms must be in DEFAULT_CONFIG['weather']")
        self.assertGreater(weather_cfg["dns_retry_initial_ms"], 0)


if __name__ == "__main__":
    unittest.main()
