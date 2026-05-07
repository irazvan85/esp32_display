"""Tests for tools/analyze_uart_log.py -- analyze_log() function.

analyze_log(lines) processes a list of strings in memory; no filesystem,
serial port, or network access occurs.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# Module loader (same pattern as test_capture_display_tool.py)
# ---------------------------------------------------------------------------

def _load_analyze_uart_log():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "tools" / "analyze_uart_log.py"
    spec = importlib.util.spec_from_file_location("analyze_uart_log", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_mod = _load_analyze_uart_log()
analyze_log = _mod.analyze_log


# ---------------------------------------------------------------------------
# Helper: a single-traceback block that can be duplicated
# ---------------------------------------------------------------------------

_TB_BLOCK = [
    "Traceback (most recent call last):",
    "  File 'main.py', line 10, in func",
    "RuntimeError: something failed",
    "",
]


# ---------------------------------------------------------------------------
# Happy path -- clean log
# ---------------------------------------------------------------------------

class TestCleanLog(unittest.TestCase):
    def test_clean_log(self):
        """All-informational log produces zero criticals and zero warnings."""
        lines = [
            "[ESP] RAM 64kB free  CPU 160MHz",
            "[ESP] RAM 60kB free  CPU 160MHz",
        ]
        result = analyze_log(lines)
        self.assertEqual(result["criticals"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)


# ---------------------------------------------------------------------------
# Critical detection
# ---------------------------------------------------------------------------

class TestCriticalDetection(unittest.TestCase):
    def test_detects_traceback(self):
        result = analyze_log(_TB_BLOCK)
        self.assertEqual(len(result["criticals"]), 1)
        entry = result["criticals"][0]
        self.assertEqual(entry["pattern"], "traceback")
        self.assertEqual(entry["occurrences"], 1)
        self.assertIn("context", entry)

    def test_detects_memory_error(self):
        result = analyze_log(["MemoryError"])
        patterns = [c["pattern"] for c in result["criticals"]]
        self.assertIn("MemoryError", patterns)

    def test_detects_watchdog(self):
        result = analyze_log(["WDT reset"])
        patterns = [c["pattern"] for c in result["criticals"]]
        self.assertIn("wdt_reset", patterns)

    def test_deduplicates_criticals(self):
        """Two identical tracebacks: occurrence count sums to 2."""
        lines = _TB_BLOCK + _TB_BLOCK
        result = analyze_log(lines)
        total = sum(
            c["occurrences"]
            for c in result["criticals"]
            if c["pattern"] == "traceback"
        )
        self.assertEqual(total, 2)


# ---------------------------------------------------------------------------
# Warning detection
# ---------------------------------------------------------------------------

class TestWarningDetection(unittest.TestCase):
    def test_detects_oserror_net(self):
        result = analyze_log(["OSError: -202"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["warnings"][0]["pattern"], "oserror_net")

    def test_detects_fetch_error(self):
        result = analyze_log(["[OWM] fetch error: -202"])
        patterns = [w["pattern"] for w in result["warnings"]]
        self.assertIn("fetch_error", patterns)

    def test_detects_backoff(self):
        result = analyze_log(["[PC] backoff 20s after 1 consecutive failures"])
        patterns = [w["pattern"] for w in result["warnings"]]
        self.assertIn("backoff", patterns)

    def test_deduplicates_warnings(self):
        """Same fetch-error line five times: occurrences sums to 5."""
        lines = ["[OWM] fetch error: -202"] * 5
        result = analyze_log(lines)
        total = sum(
            w["occurrences"]
            for w in result["warnings"]
            if w["pattern"] == "fetch_error"
        )
        self.assertEqual(total, 5)


# ---------------------------------------------------------------------------
# Memory tracking
# ---------------------------------------------------------------------------

class TestMemoryTracking(unittest.TestCase):
    def test_heap_tracking(self):
        lines = [
            "[MEM] Free heap: 65000 bytes",
            "[MEM] Free heap: 40000 bytes",
        ]
        result = analyze_log(lines)
        heap = result["mem_heap"]
        self.assertIsNotNone(heap)
        self.assertEqual(heap["min"], 40000)
        self.assertEqual(heap["max"], 65000)
        self.assertEqual(heap["last"], 40000)

    def test_heap_absent_when_no_mem_lines(self):
        result = analyze_log(["[ESP] RAM 64kB free  CPU 160MHz"])
        self.assertIsNone(result["mem_heap"])

    def test_ram_tracking(self):
        lines = [
            "[ESP] RAM 64kB free  CPU 160MHz",
            "[ESP] RAM 41kB free  CPU 160MHz",
        ]
        result = analyze_log(lines)
        ram = result["esp_ram"]
        self.assertIsNotNone(ram)
        self.assertEqual(ram["min"], 41)
        self.assertEqual(ram["max"], 64)

    def test_ram_absent_when_no_esp_lines(self):
        result = analyze_log(["[MEM] Free heap: 65000 bytes"])
        self.assertIsNone(result["esp_ram"])


# ---------------------------------------------------------------------------
# Timestamp stripping
# ---------------------------------------------------------------------------

class TestTimestampStripping(unittest.TestCase):
    def test_timestamped_lines_parsed(self):
        """[HH:MM:SS.mmm] prefix is stripped; warning still detected."""
        result = analyze_log(["[12:34:56.789] [OWM] fetch error: -202"])
        patterns = [w["pattern"] for w in result["warnings"]]
        self.assertIn("fetch_error", patterns)

    def test_timestamped_traceback_detected(self):
        lines = [
            "[12:34:56.789] Traceback (most recent call last):",
            "[12:34:56.790]   File 'main.py', line 5, in run",
            "[12:34:56.791] ValueError: bad value",
            "",
        ]
        result = analyze_log(lines)
        patterns = [c["pattern"] for c in result["criticals"]]
        self.assertIn("traceback", patterns)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_empty_log(self):
        result = analyze_log([])
        self.assertEqual(result["total_lines"], 0)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)
        self.assertEqual(result["criticals"], [])
        self.assertEqual(result["warnings"], [])

    def test_total_lines_counted(self):
        lines = ["[ESP] RAM 64kB free  CPU 160MHz"] * 3
        result = analyze_log(lines)
        self.assertEqual(result["total_lines"], 3)

    def test_no_tags_seen(self):
        lines = ["hello world", "no tags here"]
        result = analyze_log(lines)
        self.assertEqual(result["tags_seen"], [])

    def test_tags_collected(self):
        lines = [
            "[ESP] RAM 64kB free  CPU 160MHz",
            "[OWM] fetch ok",
            "[MEM] Free heap: 60000 bytes",
        ]
        result = analyze_log(lines)
        tags = result["tags_seen"]
        self.assertIn("ESP", tags)
        self.assertIn("OWM", tags)
        self.assertIn("MEM", tags)

    def test_blank_only_log(self):
        result = analyze_log(["", "", ""])
        self.assertEqual(result["criticals"], [])
        self.assertEqual(result["warnings"], [])

    def test_warning_fields_present(self):
        result = analyze_log(["[OWM] fetch error: timeout"])
        self.assertEqual(len(result["warnings"]), 1)
        w = result["warnings"][0]
        for field in ("pattern", "occurrences", "first_line"):
            self.assertIn(field, w, "missing field '%s' in warning dict" % field)

    def test_critical_fields_present(self):
        result = analyze_log(_TB_BLOCK)
        self.assertEqual(len(result["criticals"]), 1)
        c = result["criticals"][0]
        for field in ("pattern", "occurrences", "first_line", "context"):
            self.assertIn(field, c, "missing field '%s' in critical dict" % field)


if __name__ == "__main__":
    unittest.main()
