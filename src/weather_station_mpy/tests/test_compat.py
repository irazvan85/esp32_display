"""Tests for compat.py — CPython bridge helpers."""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compat import ticks_ms, ticks_diff, mem_free, mem_alloc


class TestCompatHelpers(unittest.TestCase):
    def test_ticks_ms_returns_int(self):
        t = ticks_ms()
        self.assertIsInstance(t, int)

    def test_ticks_ms_monotonic(self):
        t1 = ticks_ms()
        t2 = ticks_ms()
        self.assertGreaterEqual(t2, t1)

    def test_ticks_diff_positive(self):
        t1 = ticks_ms()
        t2 = ticks_ms()
        self.assertGreaterEqual(ticks_diff(t2, t1), 0)

    def test_ticks_diff_value(self):
        self.assertEqual(ticks_diff(1000, 500), 500)

    def test_ticks_diff_large_span(self):
        # Must not overflow or wrap incorrectly on CPython
        self.assertEqual(ticks_diff(100_000, 0), 100_000)

    def test_mem_free_returns_int(self):
        result = mem_free()
        self.assertIsInstance(result, int)

    def test_mem_alloc_returns_int(self):
        result = mem_alloc()
        self.assertIsInstance(result, int)

    def test_mem_free_cpython_sentinel(self):
        # On CPython (no gc.mem_free) must return -1
        result = mem_free()
        self.assertEqual(result, -1)

    def test_mem_alloc_cpython_sentinel(self):
        result = mem_alloc()
        self.assertEqual(result, -1)


if __name__ == "__main__":
    unittest.main()
