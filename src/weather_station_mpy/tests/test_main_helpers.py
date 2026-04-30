"""Tests for module-level helper functions in main.py."""

import os
import sys
import tempfile
import unittest

# Make the project root importable so main.py can find board, app_state, etc.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# main.py already has try/except fallbacks for all MicroPython-specific modules
# (uasyncio → asyncio, machine.Pin → inline stub).  No manual stubs needed.

from main import (  # noqa: E402
    _assoc_fail_backoff_ms,
    _assoc_fail_state_clear,
    _assoc_fail_state_load_count,
    _assoc_fail_state_save_count,
    _is_enobufs,
    _is_transport_error,
)


class TestIsTransportError(unittest.TestCase):
    def test_transport_error_minus202(self):
        self.assertTrue(_is_transport_error(OSError(-202)))

    def test_transport_error_118(self):
        self.assertTrue(_is_transport_error(OSError(118)))

    def test_transport_error_113(self):
        self.assertTrue(_is_transport_error(OSError(113)))

    def test_transport_error_minus203_not_transport(self):
        """−203 was removed from the list; ENOBUFS is not a WiFi transport failure."""
        self.assertFalse(_is_transport_error(OSError(-203)))

    def test_transport_error_105_not_transport(self):
        """ENOBUFS (105) is a local PCB exhaustion — not a WiFi path failure."""
        self.assertFalse(_is_transport_error(OSError(105)))

    def test_transport_error_text_ehostunreach(self):
        self.assertTrue(_is_transport_error(OSError("EHOSTUNREACH")))

    def test_transport_error_enobufs_text_not_transport(self):
        self.assertFalse(_is_transport_error(OSError("ENOBUFS no buffer")))


class TestIsEnobufs(unittest.TestCase):
    def test_enobufs_errno_105(self):
        self.assertTrue(_is_enobufs(OSError(105)))

    def test_enobufs_text_enobufs(self):
        self.assertTrue(_is_enobufs(OSError("ENOBUFS")))

    def test_enobufs_text_no_buffer(self):
        self.assertTrue(_is_enobufs(OSError("no buffer space")))

    def test_enobufs_other_errno(self):
        self.assertFalse(_is_enobufs(OSError(111)))

    def test_enobufs_transport_errno(self):
        self.assertFalse(_is_enobufs(OSError(-202)))

    def test_enobufs_not_transport_error(self):
        """ENOBUFS (105) should be identified by _is_enobufs, NOT _is_transport_error."""
        from main import _is_enobufs, _is_transport_error
        exc = OSError(105)
        self.assertTrue(_is_enobufs(exc))
        self.assertFalse(_is_transport_error(exc))


class TestNormalizeEnabledPages(unittest.TestCase):
    """_normalize_enabled_pages() must always return a sorted, valid list."""

    def setUp(self):
        from main import _normalize_enabled_pages
        self.fn = _normalize_enabled_pages

    def test_valid_list_preserved(self):
        self.assertEqual(self.fn([0, 1, 2, 3, 4, 5]), [0, 1, 2, 3, 4, 5])

    def test_single_page(self):
        self.assertEqual(self.fn([2]), [2])

    def test_deduplicates(self):
        result = self.fn([0, 0, 1, 1])
        self.assertEqual(result, [0, 1])

    def test_sorts_output(self):
        self.assertEqual(self.fn([5, 3, 1]), [1, 3, 5])

    def test_non_list_returns_default(self):
        self.assertEqual(self.fn(None), [0, 1, 2, 3, 4, 5])

    def test_out_of_range_filtered(self):
        result = self.fn([-1, 0, 6, 7, 5])
        self.assertNotIn(-1, result)
        self.assertNotIn(6, result)
        self.assertIn(0, result)
        self.assertIn(5, result)

    def test_empty_list_returns_default(self):
        self.assertEqual(self.fn([]), [0, 1, 2, 3, 4, 5])

    def test_non_int_items_filtered(self):
        result = self.fn([0, "bad", 2, None, 4])
        self.assertNotIn("bad", result)
        self.assertNotIn(None, result)
        self.assertEqual(result, [0, 2, 4])


class TestNextEnabledPage(unittest.TestCase):
    """_next_enabled_page() must cycle through enabled pages correctly."""

    def setUp(self):
        from main import _next_enabled_page
        self.fn = _next_enabled_page

    def test_advance_to_next(self):
        self.assertEqual(self.fn(0, [0, 1, 2]), 1)

    def test_wraps_at_end(self):
        self.assertEqual(self.fn(2, [0, 1, 2]), 0)

    def test_single_page_stays(self):
        self.assertEqual(self.fn(3, [3]), 3)

    def test_current_not_in_pages_returns_first(self):
        self.assertEqual(self.fn(9, [0, 1, 2]), 0)

    def test_skips_disabled_pages(self):
        # Pages 1, 3, 5 are enabled; from 1 → 3
        self.assertEqual(self.fn(1, [1, 3, 5]), 3)
        # From 3 → 5
        self.assertEqual(self.fn(3, [1, 3, 5]), 5)
        # From 5 → wraps to 1
        self.assertEqual(self.fn(5, [1, 3, 5]), 1)


class TestAssocFailBackoffMs(unittest.TestCase):
    def test_default_steps_and_long_cooldown_threshold(self):
        self.assertEqual(_assoc_fail_backoff_ms(1, {}), (120_000, False))
        self.assertEqual(_assoc_fail_backoff_ms(2, {}), (240_000, False))
        self.assertEqual(_assoc_fail_backoff_ms(3, {}), (720_000, True))
        self.assertEqual(_assoc_fail_backoff_ms(4, {}), (720_000, True))
        self.assertEqual(_assoc_fail_backoff_ms(9, {}), (720_000, True))

    def test_cap_lower_bound_enforced_to_120_seconds(self):
        cfg = {
            "wifi": {
                "assoc_fail_backoff_max_s": 60,
                "assoc_fail_long_cooldown_after_n": 10,
            }
        }
        # 120 * 3 would be 360s, but cap is clamped up to minimum 120s.
        self.assertEqual(_assoc_fail_backoff_ms(3, cfg), (120_000, False))

    def test_long_after_n_lower_bound_enforced_to_2(self):
        cfg = {
            "wifi": {
                "assoc_fail_long_cooldown_after_n": 1,
            }
        }
        # Lower bound is 2, so count=1 must not enter long cooldown.
        self.assertEqual(_assoc_fail_backoff_ms(1, cfg), (120_000, False))
        self.assertEqual(_assoc_fail_backoff_ms(2, cfg), (720_000, True))

    def test_long_cooldown_lower_bound_enforced_to_cap(self):
        cfg = {
            "wifi": {
                "assoc_fail_backoff_max_s": 500,
                "assoc_fail_long_cooldown_s": 300,
            }
        }
        self.assertEqual(_assoc_fail_backoff_ms(4, cfg), (500_000, True))


class TestAssocFailStatePersistence(unittest.TestCase):
    def _cfg(self, path, enabled=True):
        return {
            "wifi": {
                "assoc_fail_state_enabled": enabled,
                "assoc_fail_state_path": path,
            }
        }

    def test_load_missing_file_returns_zero(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        os.unlink(path)

        count = _assoc_fail_state_load_count(self._cfg(path))
        self.assertEqual(count, 0)

    def test_save_then_load_count_roundtrip(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        try:
            cfg = self._cfg(path)
            self.assertTrue(_assoc_fail_state_save_count(cfg, 7))
            self.assertEqual(_assoc_fail_state_load_count(cfg), 7)
        finally:
            os.unlink(path)

    def test_clear_resets_count_to_zero(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        try:
            cfg = self._cfg(path)
            _assoc_fail_state_save_count(cfg, 5)
            self.assertEqual(_assoc_fail_state_load_count(cfg), 5)
            _assoc_fail_state_clear(cfg)
            self.assertEqual(_assoc_fail_state_load_count(cfg), 0)
        finally:
            os.unlink(path)

    def test_disabled_state_ignores_save_and_load(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        try:
            cfg = self._cfg(path, enabled=False)
            self.assertFalse(_assoc_fail_state_save_count(cfg, 9))
            self.assertEqual(_assoc_fail_state_load_count(cfg), 0)
        finally:
            os.unlink(path)


class TestAssocFailSourceShape(unittest.TestCase):
    def _load_main_source(self):
        main_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(main_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_startup_assoc_fail_defers_retries_to_wifi_task(self):
        source = self._load_main_source()

        startup_loop_start = source.find("for attempt in range(startup_retries):")
        startup_loop_end = source.find("synced = await time_svc.sync_ntp()", startup_loop_start)
        self.assertGreater(startup_loop_start, 0, "startup connect loop not found")
        self.assertGreater(startup_loop_end, startup_loop_start, "startup connect loop boundary not found")

        startup_section = source[startup_loop_start:startup_loop_end]
        self.assertIn("if wifi_svc.assoc_fail:", startup_section)
        self.assertIn("startup_assoc_fail_count += 1", startup_section)
        self.assertIn("startup_assoc_fail_pending = True", startup_section)
        self.assertIn(
            "startup ASSOC_FAIL detected - deferring retries to wifi_task policy",
            startup_section,
        )
        self.assertIn("break", startup_section)

    def test_wifi_task_initial_assoc_fail_pending_branch_and_wiring(self):
        source = self._load_main_source()

        wifi_task_start = source.find("async def wifi_task(")
        weather_task_start = source.find("async def weather_task(", wifi_task_start)
        self.assertGreater(wifi_task_start, 0, "wifi_task definition not found")
        self.assertGreater(weather_task_start, wifi_task_start, "wifi_task boundary not found")

        wifi_task_section = source[wifi_task_start:weather_task_start]
        self.assertIn("initial_assoc_fail_count=0", wifi_task_section)
        self.assertIn("initial_assoc_fail_pending=False", wifi_task_section)
        self.assertIn("if _assoc_fail_pending and not state.wifi_online:", wifi_task_section)
        self.assertIn(
            "await _handle_assoc_fail_backoff(state, wifi_svc, cfg, _assoc_fail_count)",
            wifi_task_section,
        )

        self.assertIn("initial_assoc_fail_count=startup_assoc_fail_count", source)
        self.assertIn("initial_assoc_fail_pending=startup_assoc_fail_pending", source)

    def test_display_manager_import_after_startup_connect(self):
        source = self._load_main_source()

        app_main_start = source.find("async def app_main():")
        self.assertGreater(app_main_start, 0, "app_main definition not found")

        pre_app_main = source[:app_main_start]
        self.assertIn(
            "from ui.display_manager import DisplayManager",
            pre_app_main,
            "DisplayManager import should be module-level for clean-heap load",
        )


class TestBootstrapMemoryRequirements(unittest.TestCase):
    """Startup bootstrap must not fetch forecast bundle — prevents MemoryError.

    Acceptance criteria:
    - DisplayManager is initialised BEFORE weather bootstrap HTTP fetch
    - forecast bootstrap is NOT present in the bootstrap code path
    - bootstrap guard is present (70_000 is sufficient — display already up)
    - startup_bootstrap default is True (weather shown on first boot)
    - no 'drop threshold' block before DisplayManager init (boot order fix)
    """

    def _load_main_source(self):
        import os
        main_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(main_path, "r") as f:
            return f.read()

    def test_forecast_bundle_not_fetched_at_startup(self):
        """fetch_forecast_bundle must NOT be called in the startup bootstrap block.

        Calling it at boot fragments the heap with 40 JSON entries.
        Since the boot order fix (display init first), the bootstrap section
        comes AFTER the DisplayManager import — the section boundary is the
        web-socket pre-bind comment that follows the bootstrap in app_main.
        """
        source = self._load_main_source()
        bootstrap_start = source.find("Startup weather bootstrap")
        # Bootstrap ends at the web socket pre-bind section (always after bootstrap)
        bootstrap_end = source.find("Web socket pre-bind", bootstrap_start)
        self.assertGreater(bootstrap_start, 0, "Bootstrap section marker not found")
        self.assertGreater(
            bootstrap_end, bootstrap_start,
            "Web socket pre-bind marker not found after bootstrap section"
        )

        bootstrap_section = source[bootstrap_start:bootstrap_end]
        self.assertNotIn(
            "startup_forecast, startup_trend = _wx.fetch_forecast_bundle()",
            bootstrap_section,
            "startup bootstrap must NOT call fetch_forecast_bundle() "
            "(comment mentions are acceptable, executable call is not)."
        )

    def test_display_init_before_weather_bootstrap(self):
        """display.init() must be called AFTER the weather bootstrap HTTP fetch.

        display.init() allocates SPI DMA buffers from DMA-capable internal RAM.
        lwIP's MEMP_NETDB DNS allocator draws from the same pool.  If display
        init runs first, DNS queries fail with EAI_MEMORY (-203) because there
        is no DMA-capable contiguous block left.  Doing OWM bootstrap first lets
        DNS succeed while DMA memory is plentiful; display.init() then runs after
        gc.collect() reclaims the OWM transient socket+JSON allocations (~7 KB).
        """
        source = self._load_main_source()
        # display.init() is the actual DMA allocation — not the module-level import
        display_init_call = source.rfind("display.init()")
        bootstrap_fetch = source.find("startup fetch attempt")
        self.assertGreater(display_init_call, 0, "display.init() call not found in source")
        self.assertGreater(bootstrap_fetch, 0, "Bootstrap fetch attempt log not found")
        self.assertGreater(
            display_init_call,
            bootstrap_fetch,
            "display.init() (SPI DMA allocation) must be called AFTER the weather "
            "bootstrap HTTP fetch — display init before OWM exhausts DMA-capable "
            "internal RAM and causes EAI_MEMORY (-203) on every DNS query."
        )

    def test_no_drop_threshold_before_display_init(self):
        """The 'drop bootstrap data' heap-check block must NOT exist before DisplayManager.

        This block was a workaround for the wrong boot order.  With display init
        moved before bootstrap it is redundant and its removal is an acceptance
        requirement for the correct boot sequence.
        """
        source = self._load_main_source()
        display_init = source.rfind("from ui.display_manager import DisplayManager")
        self.assertGreater(display_init, 0)

        # The 800 chars before the DisplayManager import in app_main should NOT
        # contain a gc.mem_free() drop-threshold that discards bootstrap data.
        pre_display = source[max(0, display_init - 800):display_init]
        self.assertNotIn(
            "bootstrap payload dropped",
            pre_display,
            "The 'bootstrap payload dropped' drop-threshold block must not appear "
            "before DisplayManager init — display is now initialised first, making "
            "the drop check obsolete."
        )

    def test_startup_bootstrap_default_is_true(self):
        """startup_bootstrap must default to True so weather shows on first boot."""
        from config.defaults import DEFAULT_CONFIG
        self.assertTrue(
            DEFAULT_CONFIG["weather"].get("startup_bootstrap", False),
            "DEFAULT_CONFIG['weather']['startup_bootstrap'] must be True so weather "
            "is displayed on first boot rather than showing OWM error -202/-203."
        )

    def test_gc_collect_before_display_manager_import(self):
        """A gc.collect() call must appear before display.init().

        This ensures heap fragmentation from bootstrap is cleaned up before
        the SPI DMA buffer allocation in display.init().
        """
        source = self._load_main_source()
        # display.init() is the actual DMA allocation, not the module-level import
        display_init_call = source.rfind("display.init()")
        self.assertGreater(display_init_call, 0, "display.init() call not found in source")

        # Check the 300 chars before display.init() for gc.collect()
        pre_display = source[max(0, display_init_call - 300):display_init_call]
        self.assertIn(
            "gc.collect()",
            pre_display,
            "gc.collect() must appear within 300 chars before display.init() to clean "
            "up heap fragmentation before the SPI DMA buffer allocation."
        )

    def test_bootstrap_breaks_early_on_dns_error(self):
        """Bootstrap must break out of the retry loop immediately on -202/-203.

        After the first DNS error, the lwIP DNS table slot is stuck in FAILED
        state.  Retrying only adds more failed entries, worsening exhaustion for
        all subsequent socket calls (including PC metrics to a direct IP).
        The fix: catch OSError with code -202/-203 separately and break.
        """
        source = self._load_main_source()
        bootstrap_start = source.find("Startup weather bootstrap")
        bootstrap_end = source.find("Web socket pre-bind", bootstrap_start)
        self.assertGreater(bootstrap_start, 0, "Bootstrap section marker not found")
        self.assertGreater(bootstrap_end, bootstrap_start, "Web socket pre-bind not found")

        section = source[bootstrap_start:bootstrap_end]
        # Must have a separate OSError catch that checks -202/-203
        self.assertIn(
            "except OSError",
            section,
            "Bootstrap loop must catch OSError separately to detect DNS error codes"
        )
        self.assertIn(
            "-202, -203",
            section,
            "Bootstrap loop must check for DNS error codes -202 and -203"
        )
        self.assertIn(
            "_bootstrap_dns_failed",
            section,
            "Bootstrap loop must set _bootstrap_dns_failed flag on DNS error"
        )

    def test_force_wifi_reconnect_after_bootstrap_dns_failure(self):
        """Bootstrap DNS failure is logged; weather_task retries with backoff.

        force_wifi_reconnect was removed because deliberately dropping WiFi
        caused ASSOC_FAIL on rapid re-association.  The _bootstrap_dns_failed
        flag is still checked and logged so the condition is observable, but
        no WiFi reconnect is triggered — weather_task's own backoff handles it.
        """
        source = self._load_main_source()
        # The check must still appear after AppState() and before tasks start
        state_init = source.find("state = AppState()")
        tasks_start = source.find("tasks = [", state_init)
        self.assertGreater(state_init, 0, "state = AppState() not found")
        self.assertGreater(tasks_start, state_init, "tasks = [ not found after AppState")

        section = source[state_init:tasks_start]
        self.assertIn(
            "_bootstrap_dns_failed",
            section,
            "_bootstrap_dns_failed must be checked after AppState() creation"
        )
        self.assertNotIn(
            "state.force_wifi_reconnect = True",
            section,
            "force_wifi_reconnect must NOT be set on bootstrap DNS failure (causes ASSOC_FAIL)"
        )


class TestWeatherCacheFallbackWiring(unittest.TestCase):
    """Source checks for startup cache fallback and weather_task cache writes."""

    def _load_main_source(self):
        import os
        main_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(main_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_startup_cache_restore_block_exists_when_startup_weather_missing(self):
        source = self._load_main_source()

        block_start = source.find("if weather_enabled and startup_weather is None:")
        block_end = source.find("Web socket pre-bind", block_start)

        self.assertGreater(block_start, 0, "startup cache fallback guard not found")
        self.assertGreater(block_end, block_start, "cache fallback block boundary not found")

        section = source[block_start:block_end]
        self.assertIn("startup weather unavailable; trying cache", section)
        self.assertIn("cached = weather_cache_svc.load()", section)
        self.assertIn("if cached is not None:", section)
        self.assertIn("cached_weather = cached.get(\"weather\")", section)
        self.assertIn("cached_weather.get(\"valid\", False)", section)
        self.assertIn("startup_weather = cached_weather", section)
        self.assertIn("startup_forecast = cached.get(\"forecast\") or []", section)
        self.assertIn("startup_trend = cached.get(\"trend\") or []", section)
        self.assertIn("startup weather restored from cache", section)

    def test_weather_task_saves_cache_after_successful_current_fetch(self):
        source = self._load_main_source()

        func_start = source.find("async def weather_task(")
        forecast_call = source.find("forecast, trend = weather_svc.fetch_forecast_bundle()", func_start)
        self.assertGreater(func_start, 0, "weather_task function not found")
        self.assertGreater(forecast_call, func_start, "forecast fetch call not found")

        current_section = source[func_start:forecast_call]
        save_call = "cache_svc.save("
        self.assertIn(save_call, current_section)
        self.assertIn("weather=state.weather", current_section)
        self.assertIn("forecast=state.forecast", current_section)
        self.assertIn("trend=state.weather_trend", current_section)

    def test_weather_task_saves_cache_after_successful_forecast_fetch(self):
        source = self._load_main_source()

        forecast_call = source.find("forecast, trend = weather_svc.fetch_forecast_bundle()")
        except_forecast = source.find("except (OSError, ValueError, RuntimeError) as exc:", forecast_call)
        self.assertGreater(forecast_call, 0, "forecast fetch call not found")
        self.assertGreater(except_forecast, forecast_call, "forecast exception block not found")

        forecast_section = source[forecast_call:except_forecast]
        self.assertIn("cache_svc.save(", forecast_section)
        self.assertIn("weather=state.weather", forecast_section)
        self.assertIn("forecast=state.forecast", forecast_section)
        self.assertIn("trend=state.weather_trend", forecast_section)


if __name__ == "__main__":
    unittest.main()
