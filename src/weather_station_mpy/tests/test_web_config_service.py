"""Tests for WebConfigService form parsing, apply, and HTML rendering."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.web_config_service import WebConfigService


class TestWebConfigServiceParseForm(unittest.TestCase):
    def test_repeated_page_keys_become_list(self):
        svc = WebConfigService({})
        parsed = svc.parse_form("theme=retro&page=1&page=3&page=5")
        self.assertEqual(parsed["theme"], "retro")
        self.assertEqual(parsed["page"], ["1", "3", "5"])

    def test_url_decoding_plus_and_percent(self):
        svc = WebConfigService({})
        parsed = svc.parse_form("pc_url=http%3A%2F%2Fmy+host%3A8765%2Fapi%2Fsystem%2Fmetrics")
        self.assertEqual(parsed["pc_url"], "http://my host:8765/api/system/metrics")


class TestWebConfigServiceApplyForm(unittest.TestCase):
    def _base_cfg(self):
        return {
            "ui": {"theme": "light", "enabled_pages": [0, 1, 2, 3, 4, 5]},
            "metrics": {"pc_url": "http://old-host:8765/api/system/metrics"},
        }

    def test_invalid_theme_falls_back_to_retro(self):
        cfg = self._base_cfg()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WebConfigService(cfg, config_path=tmp_path)
            result = svc.apply_form({"theme": "invalid", "page": ["0", "2"]})
            self.assertEqual(result["theme"], "retro")
            self.assertEqual(cfg["ui"]["theme"], "retro")
        finally:
            os.unlink(tmp_path)

    def test_page_normalization_dedupes_and_filters(self):
        cfg = self._base_cfg()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WebConfigService(cfg, config_path=tmp_path)
            result = svc.apply_form(
                {"theme": "retro", "page": ["2", "2", "x", "9", "-1", "0"]}
            )
            self.assertEqual(result["enabled_pages"], [0, 2])
            self.assertEqual(cfg["ui"]["enabled_pages"], [0, 2])
        finally:
            os.unlink(tmp_path)

    def test_blank_pc_url_preserves_existing_value(self):
        cfg = self._base_cfg()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WebConfigService(cfg, config_path=tmp_path)
            result = svc.apply_form({"theme": "retro", "page": ["1"], "pc_url": "   "})
            self.assertEqual(
                result["pc_url"],
                "http://old-host:8765/api/system/metrics",
            )
            self.assertEqual(
                cfg["metrics"]["pc_url"],
                "http://old-host:8765/api/system/metrics",
            )
        finally:
            os.unlink(tmp_path)

    def test_persistence_updates_file_and_in_memory_cfg(self):
        cfg = self._base_cfg()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WebConfigService(cfg, config_path=tmp_path)
            result = svc.apply_form(
                {
                    "theme": "high_contrast",
                    "page": ["1", "4"],
                    "pc_url": "http://new-host:8765/api/system/metrics",
                }
            )

            self.assertEqual(result["theme"], "high_contrast")
            self.assertEqual(result["enabled_pages"], [1, 4])
            self.assertEqual(result["pc_url"], "http://new-host:8765/api/system/metrics")

            self.assertEqual(cfg["ui"]["theme"], "high_contrast")
            self.assertEqual(cfg["ui"]["enabled_pages"], [1, 4])
            self.assertEqual(cfg["metrics"]["pc_url"], "http://new-host:8765/api/system/metrics")

            with open(tmp_path, "r", encoding="utf-8") as handle:
                on_disk = json.load(handle)
            self.assertEqual(on_disk["ui"]["theme"], "high_contrast")
            self.assertEqual(on_disk["ui"]["enabled_pages"], [1, 4])
            self.assertEqual(on_disk["metrics"]["pc_url"], "http://new-host:8765/api/system/metrics")
        finally:
            os.unlink(tmp_path)


class TestWebConfigServiceRenderHtml(unittest.TestCase):
    def test_render_html_contains_ip_and_selected_checked_controls(self):
        cfg = {
            "ui": {"theme": "light", "enabled_pages": [1, 3]},
            "metrics": {"pc_url": "http://pc:8765/api/system/metrics"},
        }
        svc = WebConfigService(cfg)
        html = svc.render_html("192.168.1.77", cfg["ui"], cfg["metrics"])

        self.assertIn("Current IP: 192.168.1.77", html)
        self.assertIn("<option value='light' selected>light</option>", html)
        self.assertIn("name='page' value='1' checked", html)
        self.assertIn("name='page' value='3' checked", html)
        self.assertIn("name='pc_url'", html)
        self.assertIn("value='http://pc:8765/api/system/metrics'", html)


def test_timeout_matching_variants_for_web_diag_logic():
    # Mirrors main.web_config_task::_is_timeout_error behavior for diagnostics.
    # Keep explicit variants here because the production helper is closure-scoped.
    def _matches_timeout(exc):
        if len(exc.args) > 0 and exc.args[0] in (110, 116, "timed out"):
            return True
        low = str(exc).lower()
        return "timed out" in low or "etimedout" in low

    assert _matches_timeout(OSError(110, "ETIMEDOUT"))
    assert _matches_timeout(OSError(116, "ETIMEDOUT"))
    assert _matches_timeout(OSError("timed out"))
    assert _matches_timeout(OSError("ETIMEDOUT"))
    assert not _matches_timeout(OSError(111, "ECONNREFUSED"))


if __name__ == "__main__":
    unittest.main()
