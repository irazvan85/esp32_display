"""Tests for ui/theme.py apply_theme behavior and color transitions."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import board
from ui.theme import THEMES, apply_theme


class TestTheme(unittest.TestCase):
    def tearDown(self):
        apply_theme("retro")

    def test_invalid_theme_falls_back_to_retro(self):
        name = apply_theme("does_not_exist")
        self.assertEqual(name, "retro")
        self.assertEqual(board.COL_BG, THEMES["retro"]["COL_BG"])

    def test_apply_theme_restore_sequence(self):
        first = apply_theme("light")
        bg_light = board.COL_BG
        self.assertEqual(first, "light")
        self.assertEqual(bg_light, THEMES["light"]["COL_BG"])

        second = apply_theme("retro")
        bg_retro = board.COL_BG
        self.assertEqual(second, "retro")
        self.assertEqual(bg_retro, THEMES["retro"]["COL_BG"])
        self.assertNotEqual(bg_light, bg_retro)

    def test_bg_color_transitions_between_themes(self):
        apply_theme("light")
        bg_light = board.COL_BG

        apply_theme("high_contrast")
        bg_high_contrast = board.COL_BG

        apply_theme("retro")
        bg_retro = board.COL_BG

        self.assertEqual(bg_light, THEMES["light"]["COL_BG"])
        self.assertEqual(bg_high_contrast, THEMES["high_contrast"]["COL_BG"])
        self.assertEqual(bg_retro, THEMES["retro"]["COL_BG"])
        self.assertNotEqual(bg_light, bg_high_contrast)


if __name__ == "__main__":
    unittest.main()
