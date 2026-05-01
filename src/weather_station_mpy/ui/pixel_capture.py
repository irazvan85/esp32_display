"""Optional test-only RGB565 mirror buffer for UART frame export."""

import gc

from compat import mem_free, ticks_diff, ticks_ms

try:
    framebuf = __import__("framebuf")
except ImportError:
    framebuf = None


class PixelCaptureMirror:
    FORMAT = "RGB565"

    def __init__(self, width, height, min_heap_bytes=96 * 1024, max_fps=2):
        self.width = int(width)
        self.height = int(height)

        self._buffer_size = self.width * self.height * 2
        self._min_heap_bytes = int(min_heap_bytes)
        if self._min_heap_bytes < 0:
            self._min_heap_bytes = 0

        self._max_fps = int(max_fps)
        if self._max_fps < 1:
            self._max_fps = 1
        if self._max_fps > 10:
            self._max_fps = 10

        self._min_frame_interval_ms = 1000 // self._max_fps

        self._buf = None
        self._fb = None
        self._last_error = ""
        self._last_arm_reason = ""
        self._last_disarm_reason = ""
        self._last_draw_ms = 0
        self._last_frame_export_ms = 0
        self._frame_counter = 0

    def is_armed(self):
        return self._fb is not None and self._buf is not None

    def arm(self, reason="manual"):
        self._last_arm_reason = str(reason)

        if framebuf is None:
            self._last_error = "framebuf unavailable"
            return False, self._last_error

        if self.is_armed():
            self._last_error = ""
            return True, "capture already armed"

        free = mem_free()
        needed = self._buffer_size + self._min_heap_bytes
        if free >= 0 and free < needed:
            self._last_error = "low heap (%d < %d)" % (free, needed)
            return False, self._last_error

        gc.collect()
        try:
            self._buf = bytearray(self._buffer_size)
            self._fb = framebuf.FrameBuffer(self._buf, self.width, self.height, framebuf.RGB565)
            self._fb.fill(0)
            self._last_draw_ms = ticks_ms()
            self._last_error = ""
            return True, "capture armed"
        except MemoryError:
            self._buf = None
            self._fb = None
            gc.collect()
            self._last_error = "MemoryError allocating %d bytes" % self._buffer_size
            return False, self._last_error
        except Exception as exc:
            self._buf = None
            self._fb = None
            gc.collect()
            self._last_error = "capture init error: %s" % exc
            return False, self._last_error

    def disarm(self, reason="manual"):
        self._last_disarm_reason = str(reason)
        self._buf = None
        self._fb = None
        gc.collect()
        self._last_error = ""
        return True, "capture disarmed"

    def status(self):
        return {
            "supported": framebuf is not None,
            "armed": self.is_armed(),
            "width": self.width,
            "height": self.height,
            "format": self.FORMAT,
            "min_heap_kb": self._min_heap_bytes // 1024,
            "max_fps": self._max_fps,
            "last_error": self._last_error,
            "last_arm_reason": self._last_arm_reason,
            "last_disarm_reason": self._last_disarm_reason,
            "last_draw_ms": self._last_draw_ms,
            "last_frame_export_ms": self._last_frame_export_ms,
            "frame_counter": self._frame_counter,
        }

    def _touch(self):
        self._last_draw_ms = ticks_ms()

    def _clip_rect(self, x, y, w, h):
        if w <= 0 or h <= 0:
            return None

        x0 = int(x)
        y0 = int(y)
        x1 = x0 + int(w)
        y1 = y0 + int(h)

        if x0 < 0:
            x0 = 0
        if y0 < 0:
            y0 = 0
        if x1 > self.width:
            x1 = self.width
        if y1 > self.height:
            y1 = self.height

        if x0 >= x1 or y0 >= y1:
            return None

        return x0, y0, x1 - x0, y1 - y0

    def fill(self, color):
        fb = self._fb
        if fb is None:
            return
        fb.fill(int(color) & 0xFFFF)
        self._touch()

    def fill_rect(self, x, y, w, h, color):
        fb = self._fb
        if fb is None:
            return

        clipped = self._clip_rect(x, y, w, h)
        if clipped is None:
            return

        cx, cy, cw, ch = clipped
        fb.fill_rect(cx, cy, cw, ch, int(color) & 0xFFFF)
        self._touch()

    def hline(self, x, y, length, color):
        fb = self._fb
        if fb is None:
            return

        yy = int(y)
        if yy < 0 or yy >= self.height:
            return

        x0 = int(x)
        x1 = x0 + int(length)
        if x1 <= x0:
            return

        if x0 < 0:
            x0 = 0
        if x1 > self.width:
            x1 = self.width
        if x0 >= x1:
            return

        fb.hline(x0, yy, x1 - x0, int(color) & 0xFFFF)
        self._touch()

    def pixel(self, x, y, color):
        fb = self._fb
        if fb is None:
            return

        xx = int(x)
        yy = int(y)
        if 0 <= xx < self.width and 0 <= yy < self.height:
            fb.pixel(xx, yy, int(color) & 0xFFFF)
            self._touch()

    def line(self, x0, y0, x1, y1, color):
        fb = self._fb
        if fb is None:
            return

        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)

        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        err = dx + dy

        while True:
            if 0 <= x0 < self.width and 0 <= y0 < self.height:
                fb.pixel(x0, y0, int(color) & 0xFFFF)
            if x0 == x1 and y0 == y1:
                break
            e2 = err * 2
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

        self._touch()

    def fill_circle(self, cx, cy, radius, color):
        fb = self._fb
        if fb is None:
            return

        x0 = int(cx)
        y0 = int(cy)
        r = int(radius)
        if r < 0:
            return

        x = 0
        y = r
        d = 1 - r
        col = int(color) & 0xFFFF

        while x <= y:
            self.hline(x0 - x, y0 + y, 2 * x + 1, col)
            self.hline(x0 - x, y0 - y, 2 * x + 1, col)
            self.hline(x0 - y, y0 + x, 2 * y + 1, col)
            self.hline(x0 - y, y0 - x, 2 * y + 1, col)

            x += 1
            if d < 0:
                d += 2 * x + 1
            else:
                y -= 1
                d += 2 * (x - y) + 1

        self._touch()

    def text_8x8(self, text, x, y, color, bg=None):
        fb = self._fb
        if fb is None:
            return

        text = str(text)
        if not text:
            return

        if bg is not None:
            self.fill_rect(x, y, len(text) * 8, 8, bg)

        fb.text(text, int(x), int(y), int(color) & 0xFFFF)
        self._touch()

    def text_8x16(self, text, x, y, color, bg=None):
        fb = self._fb
        if fb is None or framebuf is None:
            return

        text = str(text)
        if not text:
            return

        if bg is not None:
            self.fill_rect(x, y, len(text) * 8, 16, bg)

        glyph_buf = bytearray(8)
        glyph_fb = framebuf.FrameBuffer(glyph_buf, 8, 8, framebuf.MONO_HLSB)

        for idx, ch in enumerate(text):
            glyph_fb.fill(0)
            glyph_fb.text(ch, 0, 0, 1)
            base_x = int(x) + idx * 8
            for row in range(8):
                row_byte = glyph_buf[row]
                dst_y = int(y) + row * 2
                for col in range(8):
                    bit = (row_byte >> (7 - col)) & 1
                    if bit:
                        self.fill_rect(base_x + col, dst_y, 1, 2, int(color) & 0xFFFF)

        self._touch()

    def blit_rgb565(self, x, y, width, height, pixels):
        fb = self._fb
        if fb is None:
            return

        w = int(width)
        h = int(height)
        if w <= 0 or h <= 0:
            return

        x0 = int(x)
        y0 = int(y)
        if x0 < 0 or y0 < 0 or x0 >= self.width or y0 >= self.height:
            return

        draw_w = w
        draw_h = h
        if x0 + draw_w > self.width:
            draw_w = self.width - x0
        if y0 + draw_h > self.height:
            draw_h = self.height - y0

        if draw_w <= 0 or draw_h <= 0:
            return

        mv = memoryview(pixels)
        row_stride = w * 2
        for row in range(draw_h):
            row_off = row * row_stride
            for col in range(draw_w):
                idx = row_off + col * 2
                color = ((mv[idx] << 8) | mv[idx + 1]) & 0xFFFF
                fb.pixel(x0 + col, y0 + row, color)

        self._touch()

    def frame_dump(self):
        if not self.is_armed():
            self._last_error = "capture not armed"
            return None, None, self._last_error

        now = ticks_ms()
        if (
            self._min_frame_interval_ms > 0
            and self._last_frame_export_ms
            and ticks_diff(now, self._last_frame_export_ms) < self._min_frame_interval_ms
        ):
            self._last_error = "rate limited"
            return None, None, self._last_error

        self._last_frame_export_ms = now
        self._frame_counter += 1
        self._last_error = ""

        meta = {
            "width": self.width,
            "height": self.height,
            "format": self.FORMAT,
            "captured_ms": now,
            "frame_counter": self._frame_counter,
        }
        return meta, self._buf, ""


def _pixel_capture_settings(cfg):
    """Extract and clamp pixel-capture settings from the debug config section."""
    debug_cfg = cfg.get("debug", {}) if isinstance(cfg, dict) else {}
    if not isinstance(debug_cfg, dict):
        debug_cfg = {}

    try:
        min_heap_kb = int(debug_cfg.get("pixel_capture_min_heap_kb", 16))
    except Exception:
        min_heap_kb = 16
    if min_heap_kb < 4:
        min_heap_kb = 4
    if min_heap_kb > 256:
        min_heap_kb = 256

    try:
        max_fps = int(debug_cfg.get("pixel_capture_max_fps", 2))
    except Exception:
        max_fps = 2
    if max_fps < 1:
        max_fps = 1
    if max_fps > 10:
        max_fps = 10

    return {
        "test_mode": bool(debug_cfg.get("test_mode", False)),
        "enabled": bool(debug_cfg.get("pixel_capture_enabled", False)),
        "arm_on_boot": bool(debug_cfg.get("pixel_capture_arm_on_boot", False)),
        "min_heap_kb": min_heap_kb,
        "max_fps": max_fps,
    }


def setup(state, display, cfg):
    """Instantiate PixelCaptureMirror and attach to state/display.

    Called lazily from main only when test_mode is active.
    """
    settings = _pixel_capture_settings(cfg)
    if not settings["test_mode"] or not settings["enabled"]:
        return

    try:
        import board as _board
        width = _board.DISPLAY_W
        height = _board.DISPLAY_H
    except Exception:
        width = 240
        height = 135

    try:
        capture = PixelCaptureMirror(
            width,
            height,
            min_heap_bytes=settings["min_heap_kb"] * 1024,
            max_fps=settings["max_fps"],
        )
        _set_cap = getattr(display, "set_pixel_capture", None)
        if _set_cap is not None:
            _set_cap(capture)
        state.pixel_capture = capture
        print("[CAP] pixel capture ready (test mode)")
    except Exception as exc:
        print("[CAP] pixel capture init failed: %s" % exc)
        return

    if settings.get("arm_on_boot"):
        try:
            ok, message = capture.arm("boot")
            if ok:
                print("[CAP] %s" % message)
            else:
                print("[CAP] arm failed: %s" % message)
        except Exception as exc:
            print("[CAP] arm exception: %s" % exc)
