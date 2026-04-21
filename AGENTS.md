# AGENTS.md

## Project Scope
This repository is for firmware development on the ideaspark ESP32 1.14-inch TFT LCD board (ESP32-WROOM-32 + ST7789 + CH340).

## Source of Truth
- [Board overview and specs](doc/spec.md)
- [Board pinout diagram](doc/pinlayout.md)
- [Arduino setup steps 1-4](doc/arduino_ide.md)
- [Arduino setup steps 5-8](doc/arduino_ide_lib.md)
- [Arduino sample code](doc/arduino_ide_samplecode.md)
- [Short board description](doc/readme.txt)

## Hardware Profile
Use this profile unless the user explicitly asks for different pins or a different display stack.

- MCU: ESP32-WROOM-32 (dual-core Xtensa, up to 240 MHz)
- Display: ST7789, 135x240, SPI, 1.14-inch IPS
- USB serial: CH340 (USB Type-C board connection)
- Logic voltage: 3.3V
- LCD wiring from vendor docs:
  - MOSI: GPIO23
  - SCLK: GPIO18
  - CS: GPIO15
  - DC: GPIO2
  - RST: GPIO4
  - BLK (backlight): GPIO32

## Firmware Defaults
- Preferred baseline: Arduino framework with ESP32 board package
- Arduino board target: ESP32 Dev Module
- Current local serial port: COM13 (USB-SERIAL CH340)
- Required display libraries:
  - Adafruit GFX Library
  - Adafruit ST7735 and ST7789 Library
  - Adafruit BusIO (dependency)
- Display initialization baseline:
  - Include Adafruit_GFX and Adafruit_ST7789
  - Construct Adafruit_ST7789 with CS/DC/RST
  - Call init(135, 240) in setup

## Local Development Notes
- The board is currently connected to the PC on COM13 (USB-SERIAL CH340).
- Use COM13 as the default upload/monitor port unless the user reports a different port.

## Agent Behavior for Code Changes
- Keep pin definitions centralized in one config block or header.
- Do not change the LCD pin map unless explicitly requested.
- Keep backlight support on GPIO32 in examples (on/off or PWM dimming).
- Prefer non-blocking loop patterns (millis-based timing) for multi-feature demos.
- Preserve display responsiveness when adding Wi-Fi, BLE, or sensor polling.
- Keep serial logs enabled for boot and runtime diagnostics.
- Include a smoke-test path in firmware examples:
  - Serial boot banner
  - Screen clear/fill
  - One visible text or primitive render
- Do not hardcode credentials or API secrets in committed source files.

## GPIO and Peripheral Notes
- GPIO34-GPIO39 are input-only.
- TX0/RX0 are used for USB serial programming.
- Prefer the documented SPI pin mapping first, then remap only if required.
- Check pin conflicts against the board pinout before assigning peripherals.

## Deliverable Expectations for New Firmware Apps
- Add a short app-level README section with purpose, required libraries, and flash/run steps.
- Call out memory/performance tradeoffs when graphics, Wi-Fi, and BLE are combined.
- If behavior is uncertain, cite a Source of Truth document rather than guessing.