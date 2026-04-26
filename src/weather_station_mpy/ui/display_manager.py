"""ST7789 display manager — improved UI.

Layout (landscape, 240×135 px):

  Page 0 – Current conditions
  ┌──────────────────────────────────────────┐
  │ HH:MM:SS  (×2 scale, 240 px wide)  y=0  │
  │ Wed  22 Apr 2025  (centered)       y=17  │
  ├──────────────────────────────────────────┤ y=26
  │ [icon 18×18] temp (big)  feels/hum y=28  │
  │              condition             y=46  │
  ├──────────────────────────────────────────┤ y=118
    │ ● wifi  age label            [1/6] y=120 │
  └──────────────────────────────────────────┘

  Page 1 – Tomorrow
  Page 2 – 5-Day forecast (5 columns)
  Page 3 – Solar
    Page 4 – PC Metrics
"""

import board

from compat import ticks_diff

try:
    _machine = __import__("machine")
    Pin = _machine.Pin
    SPI = _machine.SPI
except ImportError:
    Pin = None
    SPI = None

try:
    st7789 = __import__("st7789")
except ImportError:
    try:
        st7789 = __import__("st7789py")
    except ImportError:
        st7789 = None

try:
    font_small = __import__("vga1_8x8")
except ImportError:
    font_small = None

try:
    font_medium = __import__("vga1_8x16")
except ImportError:
    font_medium = None


_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# ── UI zones ─────────────────────────────────────────────────────────────────
_Z_CLOCK  = 0    # 3× scaled clock (24 px tall)
_Z_DATE   = 26   # date line (8 px)
_Z_DIV1   = 43   # divider below date (after 16 px date text at y=26)
_Z_WX     = 45   # weather block start
_Z_DIV2   = 117  # divider above status bar
_Z_STATUS = 119  # status bar (16 px font ends exactly at display bottom)

# ── Scaled char sizes ─────────────────────────────────────────────────────────
_CHAR_W2 = 16
_CHAR_H2 = 16
_CHAR_W3 = 24
_CHAR_H3 = 24

# ── Icon sizes ────────────────────────────────────────────────────────────────
_ICON_W = 18

# ── Page 0 trend graph area ───────────────────────────────────────────────────
_TREND_X = 150
_TREND_Y = 72
_TREND_W = 89
_TREND_H = 43

# ── 5-day column x positions ─────────────────────────────────────────────────
_C_DAY  =  2
_C_ICON = 32
_C_HI   = 56
_C_LO   = 88
_C_COND = 116


# ── Tile layout constants (PC Monitor page) ──────────────────────────────────
_TILE_Y      = 21          # top of tile area (below title divider at y=19)
_TILE_H      = 95          # total tile height (_Z_DIV2 - _TILE_Y - 1)
_TILE_LABEL  = 16          # pixels reserved at bottom for label (medium font)
_TILE_FILL_H = _TILE_H - _TILE_LABEL - 1  # 78 px drawable fill area
_TILE_W      = 54          # tile width (4*54 + 3*4 + 2*6 = 240 exact)
_TILE_GAP    = 4           # gap between tiles
_TILE_MARGIN = 6           # left margin (mirrors to right: 6 + 3*58 + 54 = 234... no: 6+54+4+54+4+54+4+54+6=240)
_TILE_XS     = (
    _TILE_MARGIN,
    _TILE_MARGIN + _TILE_W + _TILE_GAP,
    _TILE_MARGIN + 2 * (_TILE_W + _TILE_GAP),
    _TILE_MARGIN + 3 * (_TILE_W + _TILE_GAP),
)


class DisplayManager:
    def __init__(self):
        self.ready = False
        self._tft = None
        self._last_page = -1
        self._last_second = -1
        self._last_clock_text = ""
        self._last_clock_x = 0
        self._last_date_text = ""
        self._last_date_x = 0
        self._last_icon_anim_second = -1
        self._syncing_drawn = False
        # Per-tile value cache: None forces a full redraw on first render
        self._last_metric_vals = {
            "cpu_pct": None, "ram_pct": None, "disk_pct": None, "temp_c": None,
            "gpu_pct": None, "gpu_temp_c": None,
        }
        self._metrics_subpage_last = -1  # detects subpage flip to force tile redraw

    def init(self):
        if Pin is None or SPI is None:
            print("[DISP] machine module not found; running in headless mode")
            return

        Pin(board.LCD_BLK, Pin.OUT).value(1)

        if st7789 is None:
            print("[DISP] st7789 module not found; running in headless mode")
            return

        try:
            spi = SPI(
                board.SPI_BUS,
                baudrate=board.SPI_BAUDRATE,
                polarity=0,
                phase=0,
                sck=Pin(board.LCD_SCLK),
                mosi=Pin(board.LCD_MOSI),
            )

            self._tft = st7789.ST7789(
                spi,
                board.DISPLAY_NATIVE_W,
                board.DISPLAY_NATIVE_H,
                reset=Pin(board.LCD_RST, Pin.OUT),
                cs=Pin(board.LCD_CS, Pin.OUT),
                dc=Pin(board.LCD_DC, Pin.OUT),
                rotation=board.DISPLAY_ROTATION,
            )
            self._tft.init()
        except Exception as _spi_err:
            # SPI bus initialisation failed — most likely a stale IDF driver
            # state after a MicroPython soft reset.  A hard reset (SW_CPU_RESET)
            # fully reinitialises the IDF SPI host and always succeeds.
            # Printing the reason before resetting preserves the error in UART
            # logs without letting a Guru Meditation crash swallow the context.
            print("[DISP] SPI init failed: %s — triggering hard reset" % _spi_err)
            try:
                import machine as _machine
                _machine.reset()
            except Exception:
                pass  # if machine.reset() is unavailable (host tests), fall through
            return

        tft = self._tft
        if tft is None:
            return
        tft.fill(board.COL_BG)
        self.ready = True
        print("[DISP] ST7789 initialized (improved UI)")

    # ── Public helpers ────────────────────────────────────────────────────────

    def draw_boot(self, text):
        if not self.ready:
            print("[DISP] %s" % text)
            return
        tft = self._tft
        if tft is None:
            return
        tft.fill(board.COL_BG)
        self._hline(0, _Z_DIV1, board.DISPLAY_W, board.COL_DIVIDER)
        self._text_m(text, 4, _Z_STATUS, board.COL_COND, board.COL_BG)

    def draw_config_error(self, text):
        if not self.ready:
            print("[CFG] %s" % text)
            return
        tft = self._tft
        if tft is None:
            return
        tft.fill(board.COL_BG)
        self._text_m("Config required", 4, 20, board.COL_OFFLINE, board.COL_BG)
        self._text_m("Edit config.json", 4, 40, board.COL_COND, board.COL_BG)
        self._text_m(text, 4, 60, board.COL_STATUS, board.COL_BG)

    def render(self, state, now_local, stale_ms, now_ms):
        if self._last_page != state.page or state.page_dirty:
            self._last_page = state.page
            state.page_dirty = False
            self._fill(board.COL_BG)
            if state.page == 0:
                state.weather_dirty = True
                state.status_dirty = True
                self._draw_page0_static()
                self._last_second = -1
                self._last_clock_text = ""
                self._last_date_text = ""
                self._last_icon_anim_second = -1
                self._syncing_drawn = False
            elif state.page == 1:
                self._draw_title("Tomorrow", "[2/6]")
                self._draw_tomorrow(state)
            elif state.page == 2:
                self._draw_title("5-Day Forecast", "[3/6]")
                self._draw_5day_header()
                self._draw_5day(state)
            elif state.page == 3:
                self._draw_title("Solar", "[4/6]")
                self._draw_solar(state)
            elif state.page == board.PAGE_PC_MONITOR:
                self._last_metric_vals = {
                    "cpu_pct": None, "ram_pct": None, "disk_pct": None, "temp_c": None,
                    "gpu_pct": None, "gpu_temp_c": None,
                }
                self._metrics_subpage_last = -1
                state.metrics_subpage = 0
                self._draw_title("PC Monitor", "[5/6]")
                self._draw_metrics(state, now_ms)
            elif state.page == board.PAGE_ESP_STATUS:
                state.esp_status_dirty = False
                self._draw_title("ESP32 Status", "[6/6]")
                self._draw_esp_status(state)

        if state.page == 0:
            self._draw_page0_dynamic(state, now_local, stale_ms, now_ms)
        elif state.page == 1 and state.forecast_dirty:
            state.forecast_dirty = False
            self._draw_tomorrow(state)
        elif state.page == 2 and state.forecast_dirty:
            state.forecast_dirty = False
            self._draw_5day(state)
        elif state.page == 3 and state.solar_dirty:
            state.solar_dirty = False
            self._draw_solar(state)
        elif state.page == 4 and (state.metrics_dirty or state.status_dirty):
            state.metrics_dirty = False
            state.status_dirty = False
            self._draw_metrics(state, now_ms)
        elif state.page == board.PAGE_ESP_STATUS and getattr(state, "esp_status_dirty", False):
            state.esp_status_dirty = False
            self._draw_esp_status(state)

    # ── Page 0 ────────────────────────────────────────────────────────────────

    def _draw_page0_static(self):
        self._hline(0, _Z_DIV1, board.DISPLAY_W, board.COL_DIVIDER)
        self._hline(0, _Z_DIV2, board.DISPLAY_W, board.COL_DIVIDER)

    def _draw_page0_dynamic(self, state, now_local, stale_ms, now_ms):
        # ── Clock + date (updates every second) ──
        if now_local is not None:
            sec = now_local[5]
            if sec != self._last_second:
                self._last_second = sec
                clock_text = "%02d:%02d:%02d" % (now_local[3], now_local[4], now_local[5])
                cx = max(0, (board.DISPLAY_W - len(clock_text) * _CHAR_W3) // 2)
                self._draw_clock_delta(clock_text, cx)
                day = _DAYS[now_local[6]] if 0 <= now_local[6] < 7 else "?"
                month_idx = now_local[1] - 1
                month = _MONTHS[month_idx] if 0 <= month_idx < 12 else "?"
                date_text = "%s  %02d %s %04d" % (day, now_local[2], month, now_local[0])
                date_x = max(0, (board.DISPLAY_W - len(date_text) * 8) // 2)
                self._draw_date_delta(date_text, date_x)
                self._syncing_drawn = False
        elif not state.time_synced:
            if not self._syncing_drawn:
                self._fill_rect(0, _Z_CLOCK, board.DISPLAY_W, _Z_DIV1, board.COL_BG)
                self._text_m("Syncing...", 4, _Z_CLOCK + 4, board.COL_STATUS, board.COL_BG)
                self._syncing_drawn = True
                self._last_clock_text = ""
                self._last_date_text = ""

        icon_second = (now_local[5] if now_local is not None else (now_ms // 1000))
        icon_frame = icon_second & 0x03

        # ── Weather block ──
        if state.weather_dirty or state.status_dirty:
            state.weather_dirty = False
            state.status_dirty = False
            self._fill_rect(0, _Z_WX, board.DISPLAY_W, _Z_DIV2 - _Z_WX, board.COL_BG)
            self._fill_rect(0, _Z_STATUS, board.DISPLAY_W, board.DISPLAY_H - _Z_STATUS, board.COL_BG)

            if state.weather["valid"]:
                w = state.weather
                wx_id = w.get("condition_id", 800)
                self._draw_weather_icon(2, _Z_WX + 2, wx_id, size=_ICON_W, frame=icon_frame)
                self._last_icon_anim_second = icon_second

                # Temperature — 2× scaled
                temp_str = "%+.1fC" % w["temp_c"]
                tx = _ICON_W + 6
                self._text2x(temp_str, tx, _Z_WX, board.COL_TEMP, board.COL_BG)

                # Feels + humidity right of temp or below
                fh_str = "F:%.1f H:%d%%" % (w["feels_like_c"], w["humidity"])
                fh_x = tx + len(temp_str) * _CHAR_W2 + 6
                if fh_x + len(fh_str) * 8 <= board.DISPLAY_W:
                    self._text_m(fh_str, fh_x, _Z_WX + 4, board.COL_FEELS, board.COL_BG)
                else:
                    self._text_m(fh_str, tx, _Z_WX + _CHAR_H2 + 2, board.COL_FEELS, board.COL_BG)

                # Condition
                cond = w["condition"]
                max_chars = (board.DISPLAY_W - 4) // 8
                if len(cond) > max_chars:
                    cond = cond[:max_chars - 1]
                self._text_m(cond, 4, _Z_WX + _CHAR_H2 + 12, board.COL_COND, board.COL_BG)

                # Wind
                wind = w.get("wind_ms", None)
                if wind is not None:
                    self._text_m("Wind:%.1fm/s" % wind, 4, _Z_WX + _CHAR_H2 + 22, board.COL_STATUS, board.COL_BG)

                # Stale badge
                if state.last_weather_fetch_ms:
                    age = ticks_diff(now_ms, state.last_weather_fetch_ms)
                    if age > stale_ms:
                        self._text_m("[stale]", 184, _Z_WX, board.COL_STALE, board.COL_BG)

                self._draw_trend_graph(state.weather_trend)
            else:
                msg = ("No Network" if not state.wifi_online
                       else ("Syncing..." if not state.time_synced
                             else "Fetching wx..."))
                self._text_m(msg, 4, _Z_WX + 14, board.COL_STATUS, board.COL_BG)
                self._draw_trend_graph([])

            self._draw_status_bar(state, now_ms, "[1/6]", state.last_weather_fetch_ms, "wx")

        if state.weather["valid"] and icon_second != self._last_icon_anim_second:
            wx_id = state.weather.get("condition_id", 800)
            self._fill_rect(2, _Z_WX + 2, _ICON_W, _ICON_W, board.COL_BG)
            self._draw_weather_icon(2, _Z_WX + 2, wx_id, size=_ICON_W, frame=icon_frame)
            self._last_icon_anim_second = icon_second

    def _draw_clock_delta(self, new_text, new_x):
        old_text = self._last_clock_text
        old_x = self._last_clock_x

        if (not old_text) or (len(old_text) != len(new_text)) or (old_x != new_x):
            if old_text:
                self._fill_rect(old_x, _Z_CLOCK, len(old_text) * _CHAR_W3, _CHAR_H3, board.COL_BG)
            self._fill_rect(new_x, _Z_CLOCK, len(new_text) * _CHAR_W3, _CHAR_H3, board.COL_BG)
            self._text3x(new_text, new_x, _Z_CLOCK, board.COL_CLOCK, board.COL_BG)
        else:
            for idx, ch in enumerate(new_text):
                if idx < len(old_text) and ch == old_text[idx]:
                    continue
                x = new_x + idx * _CHAR_W3
                self._fill_rect(x, _Z_CLOCK, _CHAR_W3, _CHAR_H3, board.COL_BG)
                self._text3x(ch, x, _Z_CLOCK, board.COL_CLOCK, board.COL_BG)

        self._last_clock_text = new_text
        self._last_clock_x = new_x

    def _draw_date_delta(self, new_text, new_x):
        old_text = self._last_date_text
        old_x = self._last_date_x
        if new_text == old_text and new_x == old_x:
            return

        if old_text:
            self._fill_rect(old_x, _Z_DATE, len(old_text) * 8, 16, board.COL_BG)

        self._fill_rect(new_x, _Z_DATE, len(new_text) * 8, 16, board.COL_BG)
        self._text_m(new_text, new_x, _Z_DATE, board.COL_DATE, board.COL_BG)
        self._last_date_text = new_text
        self._last_date_x = new_x

    # ── Page 1 – Tomorrow ─────────────────────────────────────────────────────

    def _draw_tomorrow(self, state):
        self._fill_rect(0, 21, board.DISPLAY_W, _Z_DIV2 - 21, board.COL_BG)
        self._fill_rect(0, _Z_STATUS, board.DISPLAY_W, board.DISPLAY_H - _Z_STATUS, board.COL_BG)

        if len(state.forecast) < 2:
            self._text_m("Forecast pending", 4, 60, board.COL_STATUS, board.COL_BG)
        else:
            fc = state.forecast[1]
            wx_id = fc.get("condition_id", 800)
            date_raw = fc["date"]
            try:
                m_idx = int(date_raw[5:7]) - 1
                m_str = _MONTHS[m_idx] if 0 <= m_idx < 12 else "?"
                date_fmt = "%s  %s %s" % (fc["day"], date_raw[8:10], m_str)
            except (ValueError, IndexError):
                date_fmt = "%s  %s" % (fc["day"], date_raw)
            self._text_m(date_fmt, 4, 21, board.COL_DATE, board.COL_BG)
            self._draw_weather_icon(4, 40, wx_id, size=24)
            self._text2x("Hi:%+.0fC" % fc["temp_max"], 34, 38, board.COL_HI, board.COL_BG)
            self._text2x("Lo:%+.0fC" % fc["temp_min"], 34, 38 + _CHAR_H2 + 2, board.COL_LO, board.COL_BG)
            self._text_m(fc["condition"], 4, 76, board.COL_COND, board.COL_BG)
            self._text_m("Humidity: %d%%" % fc["humidity"], 4, 93, board.COL_FEELS, board.COL_BG)

        self._draw_status_bar(state, 0, "[2/6]")

    # ── Page 2 – 5-Day ───────────────────────────────────────────────────────

    def _draw_5day_header(self):
        self._text("Day",  _C_DAY,  21, board.COL_DIVIDER, board.COL_BG)
        self._text("Hi",   _C_HI,   21, board.COL_DIVIDER, board.COL_BG)
        self._text("Lo",   _C_LO,   21, board.COL_DIVIDER, board.COL_BG)
        self._text("Cond", _C_COND, 21, board.COL_DIVIDER, board.COL_BG)
        self._hline(0, 30, board.DISPLAY_W, board.COL_DIVIDER)

    def _draw_5day(self, state):
        self._fill_rect(0, 31, board.DISPLAY_W, _Z_DIV2 - 31, board.COL_BG)
        self._fill_rect(0, _Z_STATUS, board.DISPLAY_W, board.DISPLAY_H - _Z_STATUS, board.COL_BG)

        if not state.forecast:
            self._text_m("Forecast pending", 4, 60, board.COL_STATUS, board.COL_BG)
        else:
            row_h = 17
            for idx, fc in enumerate(state.forecast[0:5]):
                y = 31 + idx * row_h
                wx_id = fc.get("condition_id", 800)
                self._text(fc["day"][:3], _C_DAY, y, board.COL_DATE, board.COL_BG)
                self._draw_weather_icon(_C_ICON, y - 1, wx_id, size=8)
                self._text("%+.0f" % fc["temp_max"], _C_HI, y, board.COL_HI, board.COL_BG)
                self._text("%+.0f" % fc["temp_min"], _C_LO, y, board.COL_LO, board.COL_BG)
                self._text(fc["condition"][:10], _C_COND, y, board.COL_COND, board.COL_BG)
                if idx < 4:
                    self._hline(0, y + row_h - 2, board.DISPLAY_W, board.COL_DIVIDER)

        self._draw_status_bar(state, 0, "[3/6]")

    # ── Page 3 – Solar ────────────────────────────────────────────────────────

    def _draw_solar(self, state):
        self._fill_rect(0, 21, board.DISPLAY_W, _Z_DIV2 - 21, board.COL_BG)
        self._fill_rect(0, _Z_STATUS, board.DISPLAY_W, board.DISPLAY_H - _Z_STATUS, board.COL_BG)

        if not state.solar["valid"]:
            self._text_m("Solar pending", 4, 60, board.COL_STATUS, board.COL_BG)
        else:
            s = state.solar
            gen_kw  = s["generation_w"] / 1000.0
            grid_kw = s["grid_w"] / 1000.0
            bat_pct = s["battery_soc"]

            self._text_m("Generation:", 4, 21, board.COL_STATUS, board.COL_BG)
            self._text2x("%.2fkW" % gen_kw, 4, 39, board.COL_SOLAR_GEN, board.COL_BG)
            self._text_m("Grid: %.2fkW" % grid_kw, 4, 57, board.COL_SOLAR_GRID, board.COL_BG)
            self._text_m("Battery: %.0f%%" % bat_pct, 4, 75, board.COL_SOLAR_BAT, board.COL_BG)

            # Battery bar
            bar_x, bar_y, bar_w, bar_h = 4, 93, board.DISPLAY_W - 8, 10
            filled = max(0, int(bar_w * bat_pct / 100))
            self._fill_rect(bar_x, bar_y, bar_w, bar_h, board.COL_BAR_EMPTY)
            if filled > 0:
                self._fill_rect(bar_x, bar_y, filled, bar_h, board.COL_SOLAR_BAT)
            self._hline(bar_x, bar_y, bar_w, board.COL_STATUS)
            self._hline(bar_x, bar_y + bar_h - 1, bar_w, board.COL_STATUS)

        self._draw_status_bar(state, 0, "[4/6]")

    # ── Page 4 – PC Metrics ───────────────────────────────────────────────────

    def _draw_metrics(self, state, now_ms):
        """Draw PC metrics as 4 filled square tiles with incremental updates.

        Tiles (left→right): CPU | RAM | DSK | TEMP
        Each tile:  fill rises from bottom proportional to usage %;
                    value text centred inside; label centred at bottom.
        Only tiles whose value changed by ≥0.5 are redrawn to prevent flicker.
        """
        # ── Status bar is always refreshed (cheap, 8px) ────────────────────
        self._fill_rect(0, _Z_STATUS, board.DISPLAY_W, board.DISPLAY_H - _Z_STATUS, board.COL_BG)

        if not state.metrics["valid"]:
            # On first invalid render: clear tile area and show message
            self._fill_rect(0, _TILE_Y, board.DISPLAY_W, _TILE_H, board.COL_BG)
            self._last_metric_vals = {
                "cpu_pct": None, "ram_pct": None, "disk_pct": None, "temp_c": None,
                "gpu_pct": None, "gpu_temp_c": None,
            }
            self._metrics_subpage_last = -1
            if not state.wifi_online:
                msg = "No Network"
            elif state.last_metrics_fetch_ms:
                msg = "PC service offline"
            else:
                msg = "Waiting PC data..."
            self._text_m(msg, 4, _TILE_Y + _TILE_H // 2 - 8, board.COL_STATUS, board.COL_BG)
            self._draw_status_bar(state, now_ms, "[5/6]", state.last_metrics_fetch_ms, "pc")
            return

        m = state.metrics
        temp_c = m.get("temp_c", None)

        # ── Build per-tile descriptors ──────────────────────────────────────
        # (key, pct, label, value_str, base_color)
        if temp_c is None:
            temp_pct = 0.0
            temp_val = "N/A"
        else:
            temp_pct = max(0.0, min(100.0, temp_c))  # 0-100°C → 0-100%
            temp_val = "%.0fC" % temp_c

        tiles = (
            ("cpu_pct",  float(m["cpu_pct"]),   "CPU", "%d%%" % int(m["cpu_pct"]),  board.COL_METRIC_CPU),
            ("ram_pct",  float(m["ram_pct"]),   "RAM", "%d%%" % int(m["ram_pct"]),  board.COL_METRIC_RAM),
            ("disk_pct", float(m["disk_pct"]),  "DSK", "%d%%" % int(m["disk_pct"]), board.COL_METRIC_DISK),
            ("temp_c",   temp_pct,              "TEMP", temp_val,                   board.COL_METRIC_CPU),
        )

        # ── Detect subpage change → force full tile redraw ──────────────────
        subpage = getattr(state, "metrics_subpage", 0)
        if subpage != self._metrics_subpage_last:
            self._fill_rect(0, _TILE_Y, board.DISPLAY_W, _TILE_H, board.COL_BG)
            for k in self._last_metric_vals:
                self._last_metric_vals[k] = None
            self._metrics_subpage_last = subpage

        if subpage == 1:
            # ── View B: GPU% | GPU_T | CPU% | CPU_T ─────────────────────────
            gpu_pct_val = m.get("gpu_pct")
            gpu_temp_val = m.get("gpu_temp_c")
            if gpu_pct_val is None and gpu_temp_val is None:
                self._fill_rect(0, _TILE_Y, board.DISPLAY_W, _TILE_H, board.COL_BG)
                self._text_m("No GPU data", 4, _TILE_Y + _TILE_H // 2 - 8, board.COL_STATUS, board.COL_BG)
                state.metrics_subpage = 0
            else:
                if gpu_pct_val is None:
                    gpu_pct_draw, gpu_pct_str = 0.0, "N/A"
                else:
                    gpu_pct_draw = float(gpu_pct_val)
                    gpu_pct_str = "%d%%" % int(gpu_pct_val)
                if gpu_temp_val is None:
                    gpu_t_draw, gpu_t_str = 0.0, "N/A"
                else:
                    gpu_t_draw = max(0.0, min(100.0, float(gpu_temp_val)))
                    gpu_t_str = "%.0fC" % gpu_temp_val

                active_tiles = (
                    ("gpu_pct",   gpu_pct_draw,              "GPU%",  gpu_pct_str,              board.COL_METRIC_GPU),
                    ("gpu_temp_c", gpu_t_draw,               "GPU_T", gpu_t_str,                board.COL_METRIC_GPU),
                    ("cpu_pct",   float(m["cpu_pct"]),       "CPU",   "%d%%" % int(m["cpu_pct"]), board.COL_METRIC_CPU),
                    ("temp_c",    temp_pct,                  "CPU_T", temp_val,                 board.COL_METRIC_CPU),
                )
                for idx, (key, pct, label, val_str, base_col) in enumerate(active_tiles):
                    cached = self._last_metric_vals.get(key)
                    if cached is not None and abs(pct - cached) < 0.5:
                        continue
                    col = (board.COL_OFFLINE if pct >= 80.0
                           else board.COL_METRIC_TEMP_WARN if pct >= 60.0
                           else base_col)
                    self._draw_metric_tile(_TILE_XS[idx], pct, label, val_str, col)
                    self._last_metric_vals[key] = pct
        else:
            # ── View A: CPU% | RAM% | DSK% | CPU_T (original layout) ────────
            for idx, (key, pct, label, val_str, base_col) in enumerate(tiles):
                cached = self._last_metric_vals.get(key)
                # Redraw if first render (cached is None) or value changed enough
                if cached is not None and abs(pct - cached) < 0.5:
                    continue  # no visible change — skip to avoid flicker

                # Pick threshold color
                if pct >= 80.0:
                    col = board.COL_OFFLINE            # red
                elif pct >= 60.0:
                    col = board.COL_METRIC_TEMP_WARN   # yellow
                else:
                    col = base_col

                self._draw_metric_tile(_TILE_XS[idx], pct, label, val_str, col)
                self._last_metric_vals[key] = pct

        uptime_str = self._format_uptime(m.get("uptime_s", 0))
        self._draw_status_bar(state, now_ms, "[5/6]", state.last_metrics_fetch_ms, "up:" + uptime_str)

    def _draw_metric_tile(self, x, pct, label, val_str, color):
        """Draw one square tile at x.

        Layout (top-down, height=_TILE_H):
          ┌──────────────┐  ← _TILE_Y
          │  empty (bg)  │
          │──────────────│  ← fill start
          │  filled zone │
          │  (val_str)   │
          ├──────────────┤  ← _TILE_Y + _TILE_H - _TILE_LABEL - 1
          │    label     │  ← 10px label zone
          └──────────────┘  ← _TILE_Y + _TILE_H
        """
        pct = max(0.0, min(100.0, float(pct)))
        tw = _TILE_W
        ty = _TILE_Y
        th = _TILE_H
        fh_max = _TILE_FILL_H   # 92px
        fill_h = int((fh_max * pct) / 100.0)
        empty_h = fh_max - fill_h

        # Full tile background (clears previous state)
        self._fill_rect(x, ty, tw, th, board.COL_BG)

        # Outer border (1px)
        self._fill_rect(x, ty, tw, 1, board.COL_DIVIDER)                     # top
        self._fill_rect(x, ty + th - 1, tw, 1, board.COL_DIVIDER)            # bottom
        self._fill_rect(x, ty, 1, th, board.COL_DIVIDER)                     # left
        self._fill_rect(x + tw - 1, ty, 1, th, board.COL_DIVIDER)            # right

        # Empty zone (top of fill area, above the fill)
        if empty_h > 0:
            self._fill_rect(x + 1, ty + 1, tw - 2, empty_h, board.COL_BG)

        # Filled zone (bottom of fill area)
        fill_y = ty + 1 + empty_h
        if fill_h > 0:
            self._fill_rect(x + 1, fill_y, tw - 2, fill_h, color)

        # Separator line between fill area and label zone
        sep_y = ty + fh_max + 1
        self._hline(x, sep_y, tw, board.COL_DIVIDER)

        # Value text — 2x font for <=3 chars (fits 54 px tile), medium otherwise
        # Always on black background for maximum readability on any bar colour
        val_y = ty + max(1, fh_max // 2 - 8)
        if len(val_str) <= 3:
            val_x = x + max(0, (tw - len(val_str) * 16) // 2)
            self._text2x(val_str, val_x, val_y, board.COL_COND, board.COL_BG)
        else:
            val_x = x + max(0, (tw - len(val_str) * 8) // 2)
            self._text_m(val_str, val_x, val_y, board.COL_COND, board.COL_BG)

        # Label — centred at bottom in medium font
        label_x = x + max(0, (tw - len(label) * 8) // 2)
        label_y = sep_y + 1
        self._text_m(label, label_x, label_y, board.COL_STATUS, board.COL_BG)

    @staticmethod
    def _format_uptime(total_seconds):
        total_seconds = int(total_seconds)
        if total_seconds < 0:
            total_seconds = 0
        days = total_seconds // 86_400
        hours = (total_seconds % 86_400) // 3_600
        minutes = (total_seconds % 3_600) // 60
        if days > 0:
            return "%dd %02dh" % (days, hours)
        return "%02dh %02dm" % (hours, minutes)

    # ── Page 5 – ESP32 System Status ──────────────────────────────────────────

    def _draw_esp_status(self, state):
        """Draw ESP32 resource/network stats: RAM, CPU, flash, FS, IP, WiFi, uptime."""
        self._fill_rect(0, 21, board.DISPLAY_W, _Z_DIV2 - 21, board.COL_BG)
        self._fill_rect(0, _Z_STATUS, board.DISPLAY_W, board.DISPLAY_H - _Z_STATUS, board.COL_BG)

        s = state.esp_status
        if not s:
            self._text_m("Collecting...", 4, 60, board.COL_STATUS, board.COL_BG)
            self._draw_status_bar(state, 0, "[6/6]")
            return

        lbl_x = 4
        val_x = 48  # wide enough for "WiFi:" label (5 chars × 8 px + 8 gap)

        # ── RAM (y=21) ────────────────────────────────────────────────────────
        ram_free = s.get("ram_free_kb", 0)
        ram_used = s.get("ram_used_kb", -1)
        if ram_free > 50:
            ram_col = board.COL_ONLINE
        elif ram_free > 20:
            ram_col = board.COL_METRIC_TEMP_WARN
        else:
            ram_col = board.COL_OFFLINE
        self._text_m("RAM:", lbl_x, 21, board.COL_STATUS, board.COL_BG)
        if ram_used >= 0:
            self._text_m("%dkB free %dkB used" % (ram_free, ram_used), val_x, 21, ram_col, board.COL_BG)
        else:
            self._text_m("%dkB free" % ram_free, val_x, 21, ram_col, board.COL_BG)

        # ── CPU + Flash (y=37) ────────────────────────────────────────────────
        cpu_mhz = s.get("cpu_mhz", 0)
        flash_kb = s.get("flash_kb", 0)
        flash_str = "%dMB" % (flash_kb // 1024) if flash_kb >= 1024 else "%dkB" % flash_kb
        self._text_m("CPU:", lbl_x, 37, board.COL_STATUS, board.COL_BG)
        self._text_m("%dMHz Flash:%s" % (cpu_mhz, flash_str), val_x, 37, board.COL_COND, board.COL_BG)

        # ── Filesystem (y=53) ─────────────────────────────────────────────────
        fs_free = s.get("fs_free_kb", 0)
        fs_total = s.get("fs_total_kb", 0)
        self._text_m("FS:", lbl_x, 53, board.COL_STATUS, board.COL_BG)
        self._text_m("%dkB/%dkB free" % (fs_free, fs_total), val_x, 53, board.COL_COND, board.COL_BG)

        # ── IP address (y=69) ─────────────────────────────────────────────────
        ip = s.get("ip", "?")
        ip_col = board.COL_ONLINE if state.wifi_online else board.COL_OFFLINE
        self._text_m("IP:", lbl_x, 69, board.COL_STATUS, board.COL_BG)
        self._text_m(ip, val_x, 69, ip_col, board.COL_BG)

        # ── WiFi RSSI + channel (y=85) ────────────────────────────────────────
        rssi = s.get("rssi", 0)
        channel = s.get("channel", 0)
        self._text_m("WiFi:", lbl_x, 85, board.COL_STATUS, board.COL_BG)
        if state.wifi_online:
            if rssi > -70:
                rssi_col = board.COL_ONLINE
            elif rssi > -85:
                rssi_col = board.COL_METRIC_TEMP_WARN
            else:
                rssi_col = board.COL_OFFLINE
            self._text_m("%ddBm Ch:%d" % (rssi, channel), val_x, 85, rssi_col, board.COL_BG)
        else:
            self._text_m("offline", val_x, 85, board.COL_OFFLINE, board.COL_BG)

        # ── Uptime (y=101) ────────────────────────────────────────────────────
        uptime_s = s.get("uptime_s", 0)
        up_d = uptime_s // 86400
        up_h = (uptime_s % 86400) // 3600
        up_m = (uptime_s % 3600) // 60
        self._text_m("Up:", lbl_x, 101, board.COL_STATUS, board.COL_BG)
        if up_d > 0:
            self._text_m("%dd %02dh %02dm" % (up_d, up_h, up_m), val_x, 101, board.COL_COND, board.COL_BG)
        else:
            self._text_m("%02dh %02dm" % (up_h, up_m), val_x, 101, board.COL_COND, board.COL_BG)

        self._draw_status_bar(state, 0, "[6/6]")

    # ── Shared widgets ────────────────────────────────────────────────────────

    def _draw_status_bar(self, state, now_ms, page_label, last_fetch_ms=0, age_prefix="wx"):
        dot_col = board.COL_ONLINE if state.wifi_online else board.COL_OFFLINE
        self._fill_circle(5, _Z_STATUS + 7, 3, dot_col)

        if now_ms and last_fetch_ms:
            age_s = ticks_diff(now_ms, last_fetch_ms) // 1000
            age_label = ("%s %ds" % (age_prefix, age_s) if age_s < 60 else "%s %dm" % (age_prefix, age_s // 60))
        else:
            if not state.wifi_online:
                age_label = "offline"
            elif age_prefix == "pc":
                age_label = "no pc"
            else:
                age_label = "no wx"

        self._text_m(age_label, 12, _Z_STATUS, board.COL_STATUS, board.COL_BG)
        self._text_m(page_label,
                     board.DISPLAY_W - len(page_label) * 8 - 2, _Z_STATUS,
                     board.COL_STATUS, board.COL_BG)

    def _draw_title(self, title, page_label):
        self._fill_rect(0, 0, board.DISPLAY_W, 20, board.COL_BG)
        self._text_m(title, 4, 2, board.COL_TITLE, board.COL_BG)
        self._text_m(page_label,
                     board.DISPLAY_W - len(page_label) * 8 - 2, 2,
                     board.COL_STATUS, board.COL_BG)
        self._hline(0, 19, board.DISPLAY_W, board.COL_DIVIDER)
        self._hline(0, _Z_DIV2, board.DISPLAY_W, board.COL_DIVIDER)

    # ── Weather icon ──────────────────────────────────────────────────────────
    # OWM condition id groupings:
    #   2xx=thunder  3xx=drizzle  5xx=rain  6xx=snow  7xx=mist  800=clear  80x=clouds

    def _draw_weather_icon(self, x, y, wx_id, size=_ICON_W, frame=0):
        tft = self._tft
        if tft is None or not self.ready:
            return

        s = size
        cx = x + s // 2
        cy = y + s // 2
        r = max(2, s // 4)

        if 200 <= wx_id < 300:
            # Thunder
            self._icon_cloud(x, y, s, board.COL_ICON_CLOUD)
            bx = cx
            by = y + s // 2
            tft.draw_line(bx + 2, by, bx - 1, by + s // 4, board.COL_ICON_THUNDER)
            tft.draw_line(bx - 1, by + s // 4, bx + 1, by + s // 4, board.COL_ICON_THUNDER)
            tft.draw_line(bx + 1, by + s // 4, bx - 2, by + s // 2, board.COL_ICON_THUNDER)

        elif 300 <= wx_id < 600:
            # Drizzle or rain
            self._icon_cloud(x, y, s, board.COL_ICON_CLOUD)
            drop_y = y + s * 2 // 3 + (frame % 3)
            step = max(2, s // 3)
            for dx in range(0, s, step):
                tft.draw_line(x + dx, drop_y, x + dx - 1, drop_y + max(2, s // 4), board.COL_ICON_RAIN)

        elif 600 <= wx_id < 700:
            # Snow
            self._icon_cloud(x, y, s, board.COL_ICON_CLOUD)
            dot_y = y + s * 2 // 3
            step = max(2, s // 3)
            for dx in range(0, s, step):
                tft.pixel(x + dx, dot_y, board.COL_ICON_SNOW)
                if dot_y + s // 5 < board.DISPLAY_H:
                    tft.pixel(x + dx, dot_y + s // 5, board.COL_ICON_SNOW)

        elif 700 <= wx_id < 800:
            # Mist
            wobble = frame - 1
            col = board.COL_ICON_MIST
            for i in range(3):
                ly = y + s // 5 + i * (s // 4)
                lx0 = x + (i % 2) * (s // 6) + wobble
                self._hline(lx0, ly, s - (i % 2) * (s // 6), col)

        elif wx_id == 800:
            # Clear sun
            tft.fill_circle(cx, cy, r, board.COL_ICON_SUN)
            ray_r = r + 2
            ray_len = max(2, s // 6) + (frame & 1)
            for ax, ay in ((1, 0), (0, 1), (-1, 0), (0, -1),
                           (1, 1), (-1, 1), (1, -1), (-1, -1)):
                if ax != 0 and ay != 0 and ((frame + (1 if ax > 0 else 0)) & 1):
                    continue
                norm = 1 if ax == 0 or ay == 0 else 2
                rx0 = cx + ax * ray_r // norm
                ry0 = cy + ay * ray_r // norm
                rx1 = cx + ax * (ray_r + ray_len) // norm
                ry1 = cy + ay * (ray_r + ray_len) // norm
                tft.draw_line(rx0, ry0, rx1, ry1, board.COL_ICON_SUN)

        else:
            # Clouds (801-804)
            wobble = frame - 1
            self._icon_cloud(x + wobble, y, s, board.COL_ICON_CLOUD)
            if wx_id == 801:
                tft.fill_circle(x + s - r, y + r, max(1, r - 1), board.COL_ICON_SUN)

    def _draw_trend_graph(self, trend):
        gx = _TREND_X
        gy = _TREND_Y
        gw = _TREND_W
        gh = _TREND_H

        self._fill_rect(gx, gy, gw, gh, board.COL_BG)
        self._draw_rect_outline(gx, gy, gw, gh, board.COL_TREND_AXIS)
        self._text("T", gx + 3, gy + 2, board.COL_TREND_TEMP, board.COL_BG)
        self._text("P", gx + 12, gy + 2, board.COL_TREND_PRECIP, board.COL_BG)

        if not trend:
            self._text("trend...", gx + 24, gy + 2, board.COL_STATUS, board.COL_BG)
            return

        data = trend[:8]
        n = len(data)
        if n <= 0:
            self._text("trend...", gx + 24, gy + 2, board.COL_STATUS, board.COL_BG)
            return

        px0 = gx + 2
        py0 = gy + 12
        pw = gw - 4
        ph = gh - 14

        temps = [float(p.get("temp_c", 0.0)) for p in data]
        precips = [float(p.get("precip_mm", 0.0)) for p in data]

        t_min = min(temps)
        t_max = max(temps)
        if t_max <= t_min:
            t_max = t_min + 1.0
        p_max = max(precips)

        if n == 1:
            x_points = [px0 + pw // 2]
        else:
            step = (pw - 1) / (n - 1)
            x_points = [px0 + int(i * step) for i in range(n)]

        if p_max > 0.0:
            for idx in range(n):
                bar_h = int((precips[idx] / p_max) * (ph - 1))
                if bar_h <= 0:
                    continue
                x = x_points[idx]
                y = py0 + ph - bar_h
                self._fill_rect(x, y, 2, bar_h, board.COL_TREND_PRECIP)

        tft = self._tft
        if tft is None:
            return

        prev_x = None
        prev_y = None
        for idx in range(n):
            x = x_points[idx]
            temp = temps[idx]
            y = py0 + ph - 1 - int(((temp - t_min) / (t_max - t_min)) * (ph - 1))
            if prev_x is None:
                self._fill_rect(x, y, 2, 2, board.COL_TREND_TEMP)
            else:
                tft.draw_line(prev_x, prev_y, x, y, board.COL_TREND_TEMP)
            prev_x = x
            prev_y = y

    def _draw_rect_outline(self, x, y, w, h, color):
        if w <= 1 or h <= 1:
            return
        self._hline(x, y, w, color)
        self._hline(x, y + h - 1, w, color)
        self._fill_rect(x, y, 1, h, color)
        self._fill_rect(x + w - 1, y, 1, h, color)

    def _icon_cloud(self, x, y, size, color):
        tft = self._tft
        if tft is None:
            return
        s = size
        base_y = y + s // 2
        tft.fill_rect(x, base_y, s, s // 2, color)
        r1 = s // 4
        tft.fill_circle(x + r1, base_y, r1, color)
        tft.fill_circle(x + s // 2, base_y - s // 8, r1 + 1, color)
        tft.fill_circle(x + s - r1, base_y, r1, color)

    # ── 2× scaled text ────────────────────────────────────────────────────────

    def _text2x(self, text, x, y, color, bg):
        """Render text at 2× scale (16×16 px per char) using built-in framebuf font."""
        if not self.ready:
            return
        try:
            import framebuf  # type: ignore[import]
        except ImportError:
            self._text(text, x, y, color, bg)
            return

        tft = self._tft
        if tft is None:
            return

        text = str(text)
        n = len(text)
        if n == 0:
            return

        src_w = n * 8
        src_h = 8
        bpr = (src_w + 7) // 8
        buf = bytearray(bpr * src_h)
        fb = framebuf.FrameBuffer(buf, src_w, src_h, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(text, 0, 0, 1)

        dst_w = n * _CHAR_W2
        dst_h = _CHAR_H2

        if x >= board.DISPLAY_W or y >= board.DISPLAY_H:
            return

        draw_w = min(dst_w, board.DISPLAY_W - x)
        draw_h = min(dst_h, board.DISPLAY_H - y)
        if draw_w <= 0 or draw_h <= 0:
            return

        fg_hi = (color >> 8) & 0xFF
        fg_lo = color & 0xFF
        bg_hi = (bg >> 8) & 0xFF
        bg_lo = bg & 0xFF

        tft._set_window(x, y, x + draw_w - 1, y + draw_h - 1)
        pixels = bytearray(draw_w * draw_h * 2)
        pi = 0
        for dr in range(draw_h):
            src_row = dr // 2
            for dc in range(draw_w):
                src_col = dc // 2
                bit = (buf[src_row * bpr + src_col // 8] >> (7 - src_col % 8)) & 1 if src_col < src_w else 0
                if bit:
                    pixels[pi] = fg_hi
                    pixels[pi + 1] = fg_lo
                else:
                    pixels[pi] = bg_hi
                    pixels[pi + 1] = bg_lo
                pi += 2
        tft._dc(1)
        tft._cs(0)
        tft.spi.write(pixels)
        tft._cs(1)

    def _text3x(self, text, x, y, color, bg):
        """Render text at 3× scale (24×24 px per char) using built-in framebuf font."""
        if not self.ready:
            return
        try:
            import framebuf  # type: ignore[import]
        except ImportError:
            self._text(text, x, y, color, bg)
            return
        tft = self._tft
        if tft is None:
            return
        text = str(text)
        n = len(text)
        if n == 0:
            return
        src_w = n * 8
        src_h = 8
        bpr = (src_w + 7) // 8
        buf = bytearray(bpr * src_h)
        fb = framebuf.FrameBuffer(buf, src_w, src_h, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(text, 0, 0, 1)
        dst_w = n * _CHAR_W3
        dst_h = _CHAR_H3
        if x >= board.DISPLAY_W or y >= board.DISPLAY_H:
            return
        draw_w = min(dst_w, board.DISPLAY_W - x)
        draw_h = min(dst_h, board.DISPLAY_H - y)
        if draw_w <= 0 or draw_h <= 0:
            return
        fg_hi = (color >> 8) & 0xFF
        fg_lo = color & 0xFF
        bg_hi = (bg >> 8) & 0xFF
        bg_lo = bg & 0xFF
        tft._set_window(x, y, x + draw_w - 1, y + draw_h - 1)
        pixels = bytearray(draw_w * draw_h * 2)
        pi = 0
        for dr in range(draw_h):
            src_row = dr // 3
            for dc in range(draw_w):
                src_col = dc // 3
                bit = (buf[src_row * bpr + src_col // 8] >> (7 - src_col % 8)) & 1 if src_col < src_w else 0
                if bit:
                    pixels[pi] = fg_hi
                    pixels[pi + 1] = fg_lo
                else:
                    pixels[pi] = bg_hi
                    pixels[pi + 1] = bg_lo
                pi += 2
        tft._dc(1)
        tft._cs(0)
        tft.spi.write(pixels)
        tft._cs(1)

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _text(self, text, x, y, color, bg):
        if not self.ready:
            return
        tft = self._tft
        if tft is None or not hasattr(tft, "text"):
            return
        tft.text(font_small, str(text), x, y, color, bg)

    def _text_m(self, text, x, y, color, bg):
        """Render text using the medium font (vga1_8x16: 8 px wide x 16 px tall per char)."""
        if not self.ready:
            return
        tft = self._tft
        if tft is None or not hasattr(tft, "text"):
            return
        if font_medium is None:
            tft.text(font_small, str(text), x, y, color, bg)
            return
        tft.text(font_medium, str(text), x, y, color, bg)

    def _hline(self, x, y, length, color):
        if not self.ready:
            return
        tft = self._tft
        if tft is not None and hasattr(tft, "hline"):
            tft.hline(x, y, length, color)

    def _fill_rect(self, x, y, w, h, color):
        if not self.ready:
            return
        tft = self._tft
        if tft is not None and hasattr(tft, "fill_rect"):
            tft.fill_rect(x, y, w, h, color)

    def _fill_circle(self, cx, cy, r, color):
        if not self.ready:
            return
        tft = self._tft
        if tft is not None and hasattr(tft, "fill_circle"):
            tft.fill_circle(cx, cy, r, color)

    def _fill(self, color):
        if not self.ready:
            return
        tft = self._tft
        if tft is not None and hasattr(tft, "fill"):
            tft.fill(color)

