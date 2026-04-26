"""Targeted host tests for DisplayManager weather error messaging."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import board
from app_state import AppState
from ui.display_manager import DisplayManager


class _CaptureDisplayManager(DisplayManager):
    def __init__(self):
        super().__init__()
        self.messages = []

    def _fill_rect(self, *_args, **_kwargs):
        return None

    def _draw_trend_graph(self, *_args, **_kwargs):
        return None

    def _draw_status_bar(self, *_args, **_kwargs):
        return None

    def _hline(self, *_args, **_kwargs):
        return None

    def _draw_weather_icon(self, *_args, **_kwargs):
        return None

    def _text_m(self, text, *_args, **_kwargs):
        self.messages.append(text)

    def _text2x(self, *_args, **_kwargs):
        return None

    def _text3x(self, *_args, **_kwargs):
        return None


class TestDisplayManagerWeatherError(unittest.TestCase):
    def test_invalid_weather_uses_owm_error_message_when_wifi_and_time_ok(self):
        state = AppState()
        state.weather = {
            "valid": False,
            "temp_c": 0.0,
            "feels_like_c": 0.0,
            "humidity": 0,
            "condition": "---",
            "condition_id": 800,
        }
        state.weather_dirty = True
        state.status_dirty = True
        state.wifi_online = True
        state.time_synced = True
        state.weather_error = "timeout"

        display = _CaptureDisplayManager()
        display._draw_page0_dynamic(state, now_local=None, stale_ms=60_000, now_ms=0)

        self.assertTrue(display.messages)
        self.assertTrue(display.messages[0].startswith("OWM err"))
        max_chars = (board.DISPLAY_W - 4) // 8
        self.assertLessEqual(len(display.messages[0]), max_chars)


if __name__ == "__main__":
    unittest.main()
