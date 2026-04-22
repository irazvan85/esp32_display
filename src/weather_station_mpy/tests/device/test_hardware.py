"""Hardware HIL tests — run on device via mpremote.

Requirements covered:
  REQ-MEM-01  Free heap after boot must be >= 80 KB
  REQ-WIFI-01 WiFi STA interface must be active (boot.py pre-init)
  REQ-DISP-01 ST7789 driver and DisplayManager must be importable
  REQ-BTN-01  GPIO 0 (BOOT button) must be readable without error
  REQ-HW-01   Flash size must be >= 4 MB (4096 KB)
  REQ-FS-01   Root filesystem must be mounted and accessible
  REQ-FS-02   Filesystem must have writable free space

No network connection required.  All tests are self-contained hardware checks.
"""

# ── helpers are injected by the HIL runner (runner.py concatenated above) ──


def test_heap_minimum():
    """REQ-MEM-01: free heap >= 80 KB after boot."""
    import gc
    gc.collect()
    free = gc.mem_free()
    assert_gt(free, 80 * 1024, "heap too low: %d bytes" % free)


def test_wifi_sta_active():
    """REQ-WIFI-01: STA interface must be active from boot.py pre-init."""
    import network
    wlan = network.WLAN(network.STA_IF)
    assert_true(wlan.active(), "WiFi STA interface not active after boot")


def test_display_importable():
    """REQ-DISP-01: st7789 driver and DisplayManager must load without error."""
    import st7789  # noqa: F401 — side-effect import check
    from ui.display_manager import DisplayManager  # noqa: F401
    assert_true(True)  # import succeeded


def test_gpio_button_readable():
    """REQ-BTN-01: GPIO 0 (BOOT button) must be readable."""
    import board
    from machine import Pin
    btn = Pin(board.BTN_PIN, Pin.IN, Pin.PULL_UP)
    val = btn.value()
    assert_true(val in (0, 1), "unexpected GPIO value: %r" % val)


def test_flash_4mb():
    """REQ-HW-01: Flash >= 4 MB."""
    import esp
    size_kb = esp.flash_size() // 1024
    assert_ge(size_kb, 4096, "flash only %d KB" % size_kb)


def test_filesystem_mounted():
    """REQ-FS-01: Root FS accessible and has reasonable total size."""
    import uos
    sv = uos.statvfs("/")
    block_size = sv[0]
    total_blocks = sv[2]
    free_blocks = sv[3]
    total_kb = (block_size * total_blocks) // 1024
    assert_gt(total_kb, 0, "filesystem reports 0 bytes total")
    assert_ge(free_blocks, 0, "negative free blocks")


def test_filesystem_writable():
    """REQ-FS-02: Filesystem must accept a small write."""
    import uos
    path = "/_hil_probe_.tmp"
    try:
        with open(path, "w") as f:
            f.write("hil")
        with open(path) as f:
            content = f.read()
        uos.remove(path)
        assert_equal(content, "hil", "write-read mismatch")
    except OSError as e:
        raise AssertionError("filesystem write failed: %s" % e)


def test_board_constants():
    """REQ-PAGE-01: board.py constants must be internally consistent."""
    import board
    assert_equal(board.TOTAL_PAGES, 6, "TOTAL_PAGES != 6")
    assert_equal(board.PAGE_PC_MONITOR, 4, "PAGE_PC_MONITOR != 4")
    assert_equal(board.PAGE_ESP_STATUS, 5, "PAGE_ESP_STATUS != 5")
    assert_lt(board.PAGE_ESP_STATUS, board.TOTAL_PAGES, "PAGE_ESP_STATUS >= TOTAL_PAGES")
    assert_gt(board.BTN_DEBOUNCE_MS, 0, "BTN_DEBOUNCE_MS must be positive")
    assert_equal(board.DISPLAY_W, 240, "DISPLAY_W != 240")
    assert_equal(board.DISPLAY_H, 135, "DISPLAY_H != 135")


def test_spi_bus_config():
    """REQ-HW-02: SPI bus settings must match vendor pinout."""
    import board
    assert_equal(board.LCD_MOSI, 23)
    assert_equal(board.LCD_SCLK, 18)
    assert_equal(board.LCD_CS,   15)
    assert_equal(board.LCD_DC,    2)
    assert_equal(board.LCD_RST,   4)
    assert_equal(board.LCD_BLK,  32)


# ── register and run ──────────────────────────────────────────────────────────

print("[TEST] === Hardware Tests ===")
run("heap_minimum",        test_heap_minimum)
run("wifi_sta_active",     test_wifi_sta_active)
run("display_importable",  test_display_importable)
run("gpio_button_readable",test_gpio_button_readable)
run("flash_4mb",           test_flash_4mb)
run("filesystem_mounted",  test_filesystem_mounted)
run("filesystem_writable", test_filesystem_writable)
run("board_constants",     test_board_constants)
run("spi_bus_config",      test_spi_bus_config)
summary()
