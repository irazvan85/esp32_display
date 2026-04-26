"""Tests for board.py — constants and pin map sanity checks."""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import board


class TestBoardConstants(unittest.TestCase):
    def test_display_dimensions(self):
        self.assertEqual(board.DISPLAY_W, 240)
        self.assertEqual(board.DISPLAY_H, 135)
        self.assertEqual(board.DISPLAY_NATIVE_W, 135)
        self.assertEqual(board.DISPLAY_NATIVE_H, 240)

    def test_page_constants(self):
        self.assertEqual(board.PAGE_PC_MONITOR, 4)
        self.assertEqual(board.PAGE_ESP_STATUS, 5)
        self.assertEqual(board.TOTAL_PAGES, 6)
        # Every page index must fit within TOTAL_PAGES
        self.assertLess(board.PAGE_PC_MONITOR, board.TOTAL_PAGES)
        self.assertLess(board.PAGE_ESP_STATUS, board.TOTAL_PAGES)

    def test_btn_debounce_positive(self):
        self.assertGreater(board.BTN_DEBOUNCE_MS, 0)

    def test_pin_map_valid_gpio(self):
        """All GPIO numbers must be valid ESP32-WROOM-32 pins (0–39)."""
        pins = [
            board.LCD_MOSI, board.LCD_SCLK, board.LCD_CS,
            board.LCD_DC, board.LCD_RST, board.LCD_BLK, board.BTN_PIN,
        ]
        for pin in pins:
            self.assertGreaterEqual(pin, 0)
            self.assertLessEqual(pin, 39)

    def test_pin_map_no_duplicates(self):
        pins = [
            board.LCD_MOSI, board.LCD_SCLK, board.LCD_CS,
            board.LCD_DC, board.LCD_RST, board.LCD_BLK, board.BTN_PIN,
        ]
        self.assertEqual(len(pins), len(set(pins)), "Duplicate pin assignment detected")

    def test_color_values_are_rgb565(self):
        """Colors must fit in a 16-bit value (0x0000–0xFFFF)."""
        colors = [
            board.COL_BG, board.COL_CLOCK, board.COL_DATE, board.COL_TEMP,
            board.COL_STATUS, board.COL_ONLINE, board.COL_OFFLINE,
            board.COL_TITLE, board.COL_METRIC_GPU,
            board.COL_TREND_TEMP, board.COL_TREND_PRECIP, board.COL_TREND_AXIS,
        ]
        for c in colors:
            self.assertGreaterEqual(c, 0)
            self.assertLessEqual(c, 0xFFFF)

    def test_spi_baudrate_reasonable(self):
        self.assertGreaterEqual(board.SPI_BAUDRATE, 1_000_000)
        self.assertLessEqual(board.SPI_BAUDRATE, 80_000_000)


if __name__ == "__main__":
    unittest.main()
