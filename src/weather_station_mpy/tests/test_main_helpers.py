"""Tests for module-level helper functions in main.py."""

import os
import sys
import unittest

# Make the project root importable so main.py can find board, app_state, etc.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# main.py already has try/except fallbacks for all MicroPython-specific modules
# (uasyncio → asyncio, machine.Pin → inline stub).  No manual stubs needed.

from main import _is_enobufs, _is_transport_error  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
