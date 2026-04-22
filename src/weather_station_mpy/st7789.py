"""Minimal ST7789 driver for MicroPython.

This fallback driver covers init and basic drawing primitives used by the
weather_station_mpy renderer: fill, fill_rect, and hline.
"""

import time

try:
    _mp = __import__("micropython")
    const = _mp.const
except ImportError:
    def const(value):
        return value


def _sleep_ms(ms):
    try:
        time.sleep_ms(ms)  # type: ignore[attr-defined]
    except AttributeError:
        time.sleep(ms / 1000.0)


_SWRESET = const(0x01)
_SLPOUT = const(0x11)
_COLMOD = const(0x3A)
_MADCTL = const(0x36)
_CASET = const(0x2A)
_RASET = const(0x2B)
_RAMWR = const(0x2C)
_INVON = const(0x21)
_NORON = const(0x13)
_DISPON = const(0x29)


class ST7789:
    def __init__(
        self,
        spi,
        width,
        height,
        reset=None,
        cs=None,
        dc=None,
        rotation=0,
        color_order=0x00,
        inversion=True,
    ):
        self.spi = spi
        self.reset_pin = reset
        self.cs_pin = cs
        self.dc_pin = dc

        self._base_width = int(width)
        self._base_height = int(height)
        self._rotation = int(rotation) & 0x03
        self._color_order = int(color_order) & 0x08
        self._inversion = bool(inversion)

        self.width = self._base_width
        self.height = self._base_height
        self._xstart = 0
        self._ystart = 0

        self._set_rotation(self._rotation)

    def init(self):
        self._hardware_reset()

        self._write_cmd(_SWRESET)
        _sleep_ms(150)

        self._write_cmd(_SLPOUT)
        _sleep_ms(120)

        self._write_cmd(_COLMOD, b"\x55")
        _sleep_ms(10)

        madctl = self._madctl_for_rotation(self._rotation) | self._color_order
        self._write_cmd(_MADCTL, bytes([madctl]))

        if self._inversion:
            self._write_cmd(_INVON)
            _sleep_ms(10)

        self._write_cmd(_NORON)
        _sleep_ms(10)

        self._write_cmd(_DISPON)
        _sleep_ms(120)

    def fill(self, color):
        self.fill_rect(0, 0, self.width, self.height, color)

    def hline(self, x, y, length, color):
        self.fill_rect(x, y, length, 1, color)

    def fill_rect(self, x, y, width, height, color):
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)

        if width <= 0 or height <= 0:
            return

        if x >= self.width or y >= self.height:
            return

        if x < 0:
            width += x
            x = 0
        if y < 0:
            height += y
            y = 0

        if x + width > self.width:
            width = self.width - x
        if y + height > self.height:
            height = self.height - y

        if width <= 0 or height <= 0:
            return

        self._set_window(x, y, x + width - 1, y + height - 1)

        hi = (color >> 8) & 0xFF
        lo = color & 0xFF

        # Push in manageable chunks to avoid large heap allocations.
        pixel_count = width * height
        chunk_pixels = 256
        chunk = bytes([hi, lo]) * chunk_pixels

        self._dc(1)
        self._cs(0)
        while pixel_count > 0:
            count = chunk_pixels if pixel_count > chunk_pixels else pixel_count
            if count == chunk_pixels:
                self.spi.write(chunk)
            else:
                self.spi.write(bytes([hi, lo]) * count)
            pixel_count -= count
        self._cs(1)

    def _hardware_reset(self):
        if self.reset_pin is None:
            return
        self.reset_pin.value(1)
        _sleep_ms(20)
        self.reset_pin.value(0)
        _sleep_ms(20)
        self.reset_pin.value(1)
        _sleep_ms(120)

    def _set_window(self, x0, y0, x1, y1):
        x0 += self._xstart
        x1 += self._xstart
        y0 += self._ystart
        y1 += self._ystart

        self._write_cmd(
            _CASET,
            bytes([(x0 >> 8) & 0xFF, x0 & 0xFF, (x1 >> 8) & 0xFF, x1 & 0xFF]),
        )
        self._write_cmd(
            _RASET,
            bytes([(y0 >> 8) & 0xFF, y0 & 0xFF, (y1 >> 8) & 0xFF, y1 & 0xFF]),
        )
        self._write_cmd(_RAMWR)

    def _write_cmd(self, cmd, data=None):
        self._dc(0)
        self._cs(0)
        self.spi.write(bytes([cmd]))
        self._cs(1)

        if data is not None and len(data):
            self._dc(1)
            self._cs(0)
            self.spi.write(data)
            self._cs(1)

    def _dc(self, value):
        if self.dc_pin is not None:
            self.dc_pin.value(value)

    def _cs(self, value):
        if self.cs_pin is not None:
            self.cs_pin.value(value)

    def _set_rotation(self, rotation):
        self._rotation = int(rotation) & 0x03

        if self._base_width == 135 and self._base_height == 240:
            # Common 1.14" 135x240 ST7789 panel offsets.
            rot = self._rotation
            if rot == 0:
                self.width, self.height = 135, 240
                self._xstart, self._ystart = 52, 40
            elif rot == 1:
                self.width, self.height = 240, 135
                self._xstart, self._ystart = 40, 53
            elif rot == 2:
                self.width, self.height = 135, 240
                self._xstart, self._ystart = 53, 40
            else:
                self.width, self.height = 240, 135
                self._xstart, self._ystart = 40, 52
        else:
            if self._rotation in (0, 2):
                self.width = self._base_width
                self.height = self._base_height
            else:
                self.width = self._base_height
                self.height = self._base_width
            self._xstart = 0
            self._ystart = 0

    def text(self, font, s, x, y, fg=0xFFFF, bg=0x0000):
        """Render text string at (x, y) with fg/bg RGB565 colors.

        font: a russhughes-style font module with FONT/WIDTH/HEIGHT/FIRST attrs,
              or None to use the built-in framebuf 8x8 font.
        """
        s = str(s)
        if not s:
            return
        if font is not None:
            self._text_font(font, s, x, y, fg, bg)
        else:
            self._text_framebuf(s, x, y, fg, bg)

    def _text_font(self, font, s, x, y, fg, bg):
        font_w = font.WIDTH
        font_h = font.HEIGHT
        first = getattr(font, "FIRST", 0x20)
        bpr = (font_w + 7) // 8
        bpc = bpr * font_h
        data = font.FONT

        fg_hi = (fg >> 8) & 0xFF
        fg_lo = fg & 0xFF
        bg_hi = (bg >> 8) & 0xFF
        bg_lo = bg & 0xFF

        cx = x
        for ch in s:
            if cx >= self.width:
                break
            idx = (ord(ch) - first) * bpc
            if idx < 0 or idx + bpc > len(data):
                cx += font_w
                continue
            dw = min(font_w, self.width - cx)
            dh = min(font_h, self.height - y)
            if dw <= 0 or dh <= 0:
                cx += font_w
                continue
            self._set_window(cx, y, cx + dw - 1, y + dh - 1)
            pixels = bytearray(dw * dh * 2)
            pi = 0
            for row in range(dh):
                for col in range(dw):
                    bit = (data[idx + row * bpr + col // 8] >> (7 - col % 8)) & 1
                    if bit:
                        pixels[pi] = fg_hi
                        pixels[pi + 1] = fg_lo
                    else:
                        pixels[pi] = bg_hi
                        pixels[pi + 1] = bg_lo
                    pi += 2
            self._dc(1)
            self._cs(0)
            self.spi.write(pixels)
            self._cs(1)
            cx += font_w

    def _text_framebuf(self, s, x, y, fg, bg):
        try:
            import framebuf  # type: ignore[import]
        except ImportError:
            return
        w = len(s) * 8
        h = 8
        if x >= self.width or y >= self.height:
            return
        bpr = (w + 7) // 8
        buf = bytearray(bpr * h)
        fb = framebuf.FrameBuffer(buf, w, h, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(s, 0, 0, 1)

        dw = min(w, self.width - x)
        dh = min(h, self.height - y)
        self._set_window(x, y, x + dw - 1, y + dh - 1)

        fg_hi = (fg >> 8) & 0xFF
        fg_lo = fg & 0xFF
        bg_hi = (bg >> 8) & 0xFF
        bg_lo = bg & 0xFF

        pixels = bytearray(dw * dh * 2)
        pi = 0
        for row in range(dh):
            for col in range(dw):
                bit = (buf[row * bpr + col // 8] >> (7 - col % 8)) & 1
                if bit:
                    pixels[pi] = fg_hi
                    pixels[pi + 1] = fg_lo
                else:
                    pixels[pi] = bg_hi
                    pixels[pi + 1] = bg_lo
                pi += 2
        self._dc(1)
        self._cs(0)
        self.spi.write(pixels)
        self._cs(1)

    def pixel(self, x, y, color):
        """Draw a single pixel."""
        x = int(x)
        y = int(y)
        if 0 <= x < self.width and 0 <= y < self.height:
            self._set_window(x, y, x, y)
            self._dc(1)
            self._cs(0)
            self.spi.write(bytes([(color >> 8) & 0xFF, color & 0xFF]))
            self._cs(1)

    def fill_circle(self, cx, cy, r, color):
        """Draw a filled circle using the midpoint algorithm."""
        cx = int(cx)
        cy = int(cy)
        r = int(r)
        for y in range(-r, r + 1):
            x_span = int((r * r - y * y) ** 0.5)
            self.fill_rect(cx - x_span, cy + y, x_span * 2 + 1, 1, color)

    def draw_line(self, x0, y0, x1, y1, color):
        """Draw a line using Bresenham's algorithm."""
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    @staticmethod
    def _madctl_for_rotation(rotation):
        table = (0x00, 0x60, 0xC0, 0xA0)
        return table[int(rotation) & 0x03]
