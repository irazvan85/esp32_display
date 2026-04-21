# Retro Weather Clock

Displays local time (Romania/Bucharest timezone), date, and live OpenWeatherMap weather on the ideaspark ESP32 1.14" TFT LCD board.

## Required Libraries

Install all via Arduino IDE → Sketch → Include Library → Manage Libraries:

| Library | Author | Version |
|---------|--------|---------|
| Adafruit GFX Library | Adafruit | any |
| Adafruit ST7735 and ST7789 Library | Adafruit | ≥ 1.10.3 |
| ArduinoJson | Benoit Blanchon | 6.x |

## Board Settings

| Setting | Value |
|---------|-------|
| Board | ESP32 Dev Module |
| Upload Speed | 921600 |
| CPU Frequency | 240MHz (WiFi/BT) |
| Flash Size | 4MB (32Mb) |
| Partition Scheme | Default 4MB with spiffs |
| PSRAM | Disabled |
| Port | COM13 (USB-SERIAL CH340) |

See [doc/arduino_ide_lib.md](../../doc/arduino_ide_lib.md) for full setup steps.

## Credentials Setup

1. Copy `config.h.template` → `config.h` in the same folder.
2. Fill in your Wi-Fi SSID, password, and OpenWeatherMap API key.
3. `config.h` is `.gitignored` and will never be committed.

Get a free OpenWeatherMap API key at https://openweathermap.org/api (Current Weather endpoint).

## Flash & Run

1. Open `weather_station.ino` in Arduino IDE.
2. Select board and port as above.
3. Click Upload (Ctrl+U).
4. Open Serial Monitor at 115200 baud to see boot banner and runtime logs.

## Serial Monitor Output

```
================================================
  Retro Weather Clock — ideaspark ESP32 1.14"
  Build: Apr 21 2026 12:00:00
  Free heap at boot: 290000 bytes
================================================
[WiFi] Connecting to MySSID
[WiFi] Connected, IP: 192.168.1.x
[NTP]  Time synced: 14:22:05
[OWM]  19.3°C  Clouds  Hum:67%
[BOOT] Setup complete

[MEM]  Free heap: 248000 bytes      ← logged every 60 s
```

## Display Layout (240×135, landscape)

```
┌────────────────────────────────────────┐
│           14:22:05                     │  ← cyan, textSize 5
│     Mon  21 Apr 2026                   │  ← gray, textSize 2
├────────────────────────────────────────┤
│ ☁  19.3°C   Feels 17.1°C              │  ← orange temp, white cond
│     Clouds  Hum:67%                    │
│                              Bucharest │  ← status bar
└────────────────────────────────────────┘
  ●  wx 3m ago                            ← green dot = online
```

## Offline Behavior

| Condition | Display |
|-----------|---------|
| No Wi-Fi | Red dot, "Offline" in status bar, "No Net" in weather zone |
| Wi-Fi up, no weather yet | "Fetching weather..." in weather zone |
| Weather fetch failed / stale > 30 min | `[stale]` tag on weather panel |
| NTP not yet synced | "Syncing..." in clock zone |
