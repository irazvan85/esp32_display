// weather_station.ino  — v3
// ideaspark ESP32 1.14" TFT  —  Retro Weather Clock
//
// Page 0 : Current conditions  (clock + date + live weather)
// Page 1 : Tomorrow's forecast
// Page 2 : 5-Day forecast
// Page 3 : Solar power  (SolarMan Cloud API)
// Button : GPIO0 (BOOT, active LOW) cycles pages
//
// Required libraries (Arduino Library Manager):
//   - Adafruit GFX Library
//   - Adafruit ST7735 and ST7789 Library
//   - ArduinoJson  (Benoit Blanchon, v7.x)
//
// Board: ESP32 Dev Module  |  Partition: Default 4MB with spiffs
// Upload speed: 921600     |  Port: COM13 (CH340)
//
// Credentials: copy config.h.template → config.h and fill in values.

#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <time.h>
#include "config.h"

// ─────────────────────────────────────────────────────────────────────────────
// Pin definitions  (locked — see doc/pinlayout.md)
// ─────────────────────────────────────────────────────────────────────────────
#define LCD_MOSI  23
#define LCD_SCLK  18
#define LCD_CS    15
#define LCD_DC     2
#define LCD_RST    4
#define LCD_BLK   32
#define BTN_PIN    0    // BOOT button — active LOW, internal pull-up

// ─────────────────────────────────────────────────────────────────────────────
// Display
// ─────────────────────────────────────────────────────────────────────────────
Adafruit_ST7789 lcd = Adafruit_ST7789(LCD_CS, LCD_DC, LCD_RST);

// After setRotation(1): width=240, height=135
#define DISP_W 240
#define DISP_H 135

// ─────────────────────────────────────────────────────────────────────────────
// Retro color palette
// ─────────────────────────────────────────────────────────────────────────────
#define COL_BG        0x0000   // black
#define COL_CLOCK     0x07FF   // cyan
#define COL_DATE      0xC618   // light gray
#define COL_TEMP      0xFD20   // orange
#define COL_FEELS     0xFFE0   // yellow
#define COL_COND      0xFFFF   // white
#define COL_STATUS    0x4208   // dim gray
#define COL_ONLINE    0x07E0   // green
#define COL_OFFLINE   0xF800   // red
#define COL_STALE     0xFD20   // orange
#define COL_DIVIDER   0x2945   // dark gray
#define COL_TITLE     0x07FF   // cyan  (page headers)
#define COL_HI        0xFB60   // warm orange-red  (hi temp)
#define COL_LO        0x5CFF   // light blue       (lo temp)
#define COL_BAR_EMPTY 0x1082   // very dark gray   (unfilled bar segment)

// ─────────────────────────────────────────────────────────────────────────────
// Layout zones — Page 0  (landscape 240×135)
// ─────────────────────────────────────────────────────────────────────────────
//  ZONE_CLOCK_Y   =  0  →  HH:MM:SS  textSize 5 → 40 px tall  (y 0..39)
//  ZONE_DATE_Y    = 43  →  date      textSize 2 → 16 px tall  (y 43..58)
//  ZONE_DIV_Y     = 61  →  1-px divider
//  ZONE_WEATHER_Y = 63  →  weather block                       (y 63..119)
//  ZONE_STATUS_Y  =120  →  status bar                          (y 120..134)
#define ZONE_CLOCK_Y     0
#define ZONE_DATE_Y     43
#define ZONE_DIV_Y      61
#define ZONE_WEATHER_Y  63
#define ZONE_STATUS_Y  120

// Button debounce
#define BTN_DEBOUNCE_MS  200UL

// ─────────────────────────────────────────────────────────────────────────────
// Data structures
// ─────────────────────────────────────────────────────────────────────────────
struct WeatherData {
    float   tempC;
    float   feelsLikeC;
    uint8_t humidity;
    char    condition[24];
    int     conditionId;
    bool    valid;
};

struct ForecastDay {
    char    day[4];        // "Mon"
    char    dateStr[11];   // "YYYY-MM-DD"
    float   tempMin;
    float   tempMax;
    uint8_t humidity;
    int     conditionId;
    char    condition[16];
    bool    valid;
};

struct SolarData {
    float  generationPower;  // W   — current solar generation
    float  gridPower;        // W   — positive = export to grid, negative = import
    float  batterySoc;       // %   — battery state of charge (0 = no battery)
    long   lastUpdateTime;   // unix timestamp from API
    bool   valid;
};

// ─────────────────────────────────────────────────────────────────────────────
// Application state
// ─────────────────────────────────────────────────────────────────────────────
static WeatherData   g_weather       = { 0, 0, 0, "---", 0, false };
static ForecastDay   g_forecast[5];
static bool          g_forecastValid  = false;
static bool          g_wifiOnline     = false;
static bool          g_timeValid      = false;
static unsigned long g_lastWeatherFetch  = 0;
static unsigned long g_lastForecastFetch = 0;

// Solar state
static SolarData     g_solar            = { 0.0f, 0.0f, 0.0f, 0L, false };
static char          g_solarToken[512]  = "";
static long          g_solarUserId      = 0;
static unsigned long t_solar            = 0;
static bool          g_solarTokenValid  = false;
static unsigned long g_lastSolarFetch   = 0;

// Page state
static uint8_t       g_page          = 0;    // 0=current 1=tomorrow 2=5-day 3=solar 4=hw
static bool          dirty_page      = true;
static bool          dirty_forecast  = false;
static bool          dirty_hwinfo    = false;

// Scheduler timestamps
static unsigned long t_clock   = 0;
static unsigned long t_weather = 0;
static unsigned long t_wifi    = 0;
static unsigned long t_hwinfo  = 0;

// Button state
static uint8_t       btn_prev      = HIGH;
static unsigned long btn_lastPress = 0;

// Page-0 dirty flags
static bool dirty_clock   = true;
static bool dirty_date    = true;
static bool dirty_weather = true;
static bool dirty_status  = true;

// Previous rendered values (dirty-detection)
static int  prev_sec  = -1;
static int  prev_min  = -1;
static int  prev_hour = -1;
static int  prev_mday = -1;

// ─────────────────────────────────────────────────────────────────────────────
// Weather icon bitmaps (16×16 monochrome)
// ─────────────────────────────────────────────────────────────────────────────
// Icons drawn with Adafruit GFX primitives to keep flash usage low.
// conditionId ranges: 2xx=Thunderstorm, 3xx=Drizzle, 5xx=Rain,
//                     6xx=Snow, 7xx=Mist/Fog, 800=Clear, 80x=Clouds

static void drawWeatherIcon(int x, int y, int id, uint16_t col) {
    if (id == 800) {
        // Clear — filled circle sun
        lcd.fillCircle(x + 8, y + 8, 5, col);
        // Rays
        for (int a = 0; a < 8; a++) {
            float angle = a * 45.0 * (3.14159f / 180.0f);
            int x1 = x + 8 + (int)(7 * cos(angle));
            int y1 = y + 8 + (int)(7 * sin(angle));
            int x2 = x + 8 + (int)(9 * cos(angle));
            int y2 = y + 8 + (int)(9 * sin(angle));
            lcd.drawLine(x1, y1, x2, y2, col);
        }
    } else if (id >= 801 && id <= 804) {
        // Clouds — two overlapping circles
        lcd.fillCircle(x + 6,  y + 10, 4, col);
        lcd.fillCircle(x + 11, y + 10, 5, col);
        lcd.fillCircle(x + 9,  y + 7,  4, col);
    } else if (id >= 500 && id <= 531) {
        // Rain — cloud + droplets
        lcd.fillCircle(x + 6,  y + 7, 3, col);
        lcd.fillCircle(x + 11, y + 7, 4, col);
        lcd.fillCircle(x + 9,  y + 5, 3, col);
        lcd.fillRect(x + 5, y + 10, 7, 2, col);
        lcd.drawLine(x + 5, y + 13, x + 4, y + 15, col);
        lcd.drawLine(x + 9, y + 13, x + 8, y + 15, col);
        lcd.drawLine(x + 12, y + 12, x + 11, y + 14, col);
    } else if (id >= 600 && id <= 622) {
        // Snow — cloud + asterisk flakes
        lcd.fillCircle(x + 6,  y + 7, 3, col);
        lcd.fillCircle(x + 11, y + 7, 4, col);
        lcd.fillRect(x + 5, y + 10, 7, 2, col);
        // Snowflakes
        lcd.drawPixel(x + 5,  y + 13, col);
        lcd.drawPixel(x + 9,  y + 14, col);
        lcd.drawPixel(x + 13, y + 13, col);
    } else if (id >= 200 && id <= 232) {
        // Thunderstorm — cloud + lightning bolt
        lcd.fillCircle(x + 6,  y + 6, 3, col);
        lcd.fillCircle(x + 11, y + 6, 4, col);
        lcd.fillRect(x + 5, y + 9, 7, 2, col);
        // Bolt
        lcd.drawLine(x + 9, y + 11, x + 6, y + 15, col);
        lcd.drawLine(x + 6, y + 13, x + 10, y + 13, col);
        lcd.drawLine(x + 10, y + 13, x + 7, y + 16, col);
    } else {
        // Mist / unknown — horizontal lines
        for (int i = 0; i < 4; i++) {
            lcd.drawFastHLine(x + 2, y + 5 + i * 3, 12, col);
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Display helpers
// ─────────────────────────────────────────────────────────────────────────────
static void clearZone(int x, int y, int w, int h) {
    lcd.fillRect(x, y, w, h, COL_BG);
}

static void drawDivider() {
    lcd.drawFastHLine(0, ZONE_DIV_Y, DISP_W, COL_DIVIDER);
}

// ─────────────────────────────────────────────────────────────────────────────
// Day/month name tables + date-string → day-name helper
// ─────────────────────────────────────────────────────────────────────────────
static const char* const DAYS[]   = { "Sun","Mon","Tue","Wed","Thu","Fri","Sat" };
static const char* const MONTHS[] = { "Jan","Feb","Mar","Apr","May","Jun",
                                       "Jul","Aug","Sep","Oct","Nov","Dec" };

static const char* dayNameFromDateStr(const char* ds) {
    int y = 0, m = 0, d = 0;
    if (sscanf(ds, "%d-%d-%d", &y, &m, &d) == 3) {
        struct tm t = {};
        t.tm_year = y - 1900;
        t.tm_mon  = m - 1;
        t.tm_mday = d;
        t.tm_hour = 12;
        mktime(&t);
        return DAYS[t.tm_wday];
    }
    return "?";
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 0 — clock  (no-blink: two-arg setTextColor, no clearZone)
// textSize 5 → char 30×40 px; 8 chars × 30 = 240 px — exact fit
// ─────────────────────────────────────────────────────────────────────────────
static void drawClock(const struct tm* t) {
    char buf[10];
    snprintf(buf, sizeof(buf), "%02d:%02d:%02d", t->tm_hour, t->tm_min, t->tm_sec);
    lcd.setTextSize(5);
    lcd.setTextColor(COL_CLOCK, COL_BG);   // two-arg → per-pixel bg fill, no blink
    lcd.setCursor(0, ZONE_CLOCK_Y);
    lcd.print(buf);
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 0 — date strip
// ─────────────────────────────────────────────────────────────────────────────
static void drawDate(const struct tm* t) {
    clearZone(0, ZONE_DATE_Y, DISP_W, ZONE_DIV_Y - ZONE_DATE_Y);
    char buf[32];
    snprintf(buf, sizeof(buf), "%s  %02d %s %04d",
        DAYS[t->tm_wday], t->tm_mday, MONTHS[t->tm_mon], t->tm_year + 1900);
    int startX = (DISP_W - (int)strlen(buf) * 12) / 2;
    if (startX < 0) startX = 0;
    lcd.setTextSize(2);
    lcd.setTextColor(COL_DATE, COL_BG);
    lcd.setCursor(startX, ZONE_DATE_Y);
    lcd.print(buf);
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 0 — weather block
// Row layout (origin = ZONE_WEATHER_Y):
//   y+ 0 : icon 16×16  +  temp textSize 3 (24 px)  +  [stale] right edge
//   y+26 : "F:±XX.X°C  H:XX%"  textSize 1
//   y+36 : condition             textSize 1
// ─────────────────────────────────────────────────────────────────────────────
static void drawWeather() {
    clearZone(0, ZONE_WEATHER_Y, DISP_W, ZONE_STATUS_Y - ZONE_WEATHER_Y);

    if (!g_weather.valid) {
        lcd.setTextSize(1);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(4, ZONE_WEATHER_Y + 10);
        if (!g_wifiOnline)     lcd.print("No Net");
        else if (!g_timeValid) lcd.print("Syncing...");
        else                   lcd.print("Fetching weather...");
        return;
    }

    // Icon
    drawWeatherIcon(4, ZONE_WEATHER_Y, g_weather.conditionId, COL_COND);

    // Temperature (large)
    char tempBuf[14];
    snprintf(tempBuf, sizeof(tempBuf), "%+.1f%cC", g_weather.tempC, (char)247);
    lcd.setTextSize(3);
    lcd.setTextColor(COL_TEMP);
    lcd.setCursor(28, ZONE_WEATHER_Y);
    lcd.print(tempBuf);

    // Feels-like + Humidity on one line
    char feelHumBuf[32];
    snprintf(feelHumBuf, sizeof(feelHumBuf), "F:%+.1f%cC  H:%u%%",
             g_weather.feelsLikeC, (char)247, g_weather.humidity);
    lcd.setTextSize(1);
    lcd.setTextColor(COL_FEELS);
    lcd.setCursor(4, ZONE_WEATHER_Y + 26);
    lcd.print(feelHumBuf);

    // Condition
    lcd.setTextColor(COL_COND);
    lcd.setCursor(4, ZONE_WEATHER_Y + 36);
    lcd.print(g_weather.condition);

    // Stale tag
    bool stale = (g_lastWeatherFetch > 0) &&
                 ((millis() - g_lastWeatherFetch) > STALE_WEATHER_MS);
    if (stale) {
        lcd.setTextColor(COL_STALE);
        lcd.setCursor(DISP_W - 43, ZONE_WEATHER_Y);
        lcd.print("[stale]");
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 0 — status bar
// ─────────────────────────────────────────────────────────────────────────────
static void drawStatus() {
    clearZone(0, ZONE_STATUS_Y, DISP_W, DISP_H - ZONE_STATUS_Y);
    lcd.setTextSize(1);
    lcd.fillCircle(4, ZONE_STATUS_Y + 4, 3, g_wifiOnline ? COL_ONLINE : COL_OFFLINE);

    if (g_lastWeatherFetch > 0) {
        unsigned long ageSec = (millis() - g_lastWeatherFetch) / 1000UL;
        char ageBuf[20];
        if (ageSec < 60) snprintf(ageBuf, sizeof(ageBuf), "wx %lus ago", ageSec);
        else             snprintf(ageBuf, sizeof(ageBuf), "wx %lum ago", ageSec / 60);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(12, ZONE_STATUS_Y);
        lcd.print(ageBuf);
    } else {
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(12, ZONE_STATUS_Y);
        lcd.print(g_wifiOnline ? "no wx yet" : "offline");
    }

    lcd.setTextColor(COL_STATUS);
    lcd.setCursor(DISP_W - 6 * 5 - 2, ZONE_STATUS_Y);
    lcd.print("[1/5]");
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared mini status bar used by Pages 1 & 2
// ─────────────────────────────────────────────────────────────────────────────
static void drawPageStatusBar(const char* pageLabel) {
    clearZone(0, ZONE_STATUS_Y, DISP_W, DISP_H - ZONE_STATUS_Y);
    lcd.setTextSize(1);
    lcd.fillCircle(4, ZONE_STATUS_Y + 4, 3, g_wifiOnline ? COL_ONLINE : COL_OFFLINE);
    if (g_lastForecastFetch > 0) {
        unsigned long ageSec = (millis() - g_lastForecastFetch) / 1000UL;
        char ageBuf[20];
        if (ageSec < 60) snprintf(ageBuf, sizeof(ageBuf), "fc %lus ago", ageSec);
        else             snprintf(ageBuf, sizeof(ageBuf), "fc %lum ago", ageSec / 60);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(12, ZONE_STATUS_Y);
        lcd.print(ageBuf);
    }
    lcd.setTextColor(COL_STATUS);
    lcd.setCursor(DISP_W - (int)strlen(pageLabel) * 6 - 2, ZONE_STATUS_Y);
    lcd.print(pageLabel);
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 1 — Tomorrow's forecast
// ─────────────────────────────────────────────────────────────────────────────
static void drawPageTomorrow() {
    lcd.setTextSize(2);
    lcd.setTextColor(COL_TITLE);
    lcd.setCursor(4, 2);
    lcd.print("Tomorrow");
    lcd.drawFastHLine(0, 22, DISP_W, COL_DIVIDER);

    if (!g_forecastValid || !g_forecast[1].valid) {
        lcd.setTextSize(1);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(4, 50);
        lcd.print(g_wifiOnline ? "Fetching forecast..." : "No Net");
        drawPageStatusBar("[2/5]");
        return;
    }

    ForecastDay& fc = g_forecast[1];

    // Date line
    lcd.setTextSize(1);
    lcd.setTextColor(COL_DATE);
    char dateLine[24];
    snprintf(dateLine, sizeof(dateLine), "%s  %s", fc.day, fc.dateStr);
    lcd.setCursor(4, 26);
    lcd.print(dateLine);

    // Icon
    drawWeatherIcon(4, 38, fc.conditionId, COL_COND);

    // Hi temp
    lcd.setTextSize(3);
    lcd.setTextColor(COL_HI);
    char hiBuf[10];
    snprintf(hiBuf, sizeof(hiBuf), "%+.0f%cC", fc.tempMax, (char)247);
    lcd.setCursor(28, 36);
    lcd.print(hiBuf);

    // Lo temp
    lcd.setTextSize(2);
    lcd.setTextColor(COL_LO);
    char loBuf[12];
    snprintf(loBuf, sizeof(loBuf), "Lo %+.0f%cC", fc.tempMin, (char)247);
    lcd.setCursor(28, 64);
    lcd.print(loBuf);

    // Condition
    lcd.setTextSize(1);
    lcd.setTextColor(COL_COND);
    lcd.setCursor(4, 88);
    lcd.print(fc.condition);

    // Humidity
    char humBuf[12];
    snprintf(humBuf, sizeof(humBuf), "Hum: %u%%", fc.humidity);
    lcd.setTextColor(COL_FEELS);
    lcd.setCursor(4, 100);
    lcd.print(humBuf);

    lcd.drawFastHLine(0, ZONE_STATUS_Y - 2, DISP_W, COL_DIVIDER);
    drawPageStatusBar("[2/5]");
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 2 — 5-Day forecast
// 5 rows × 18 px starting at y=24; cols: day | icon | hi | lo | condition
// ─────────────────────────────────────────────────────────────────────────────
static void drawPage5Day() {
    lcd.setTextSize(2);
    lcd.setTextColor(COL_TITLE);
    lcd.setCursor(4, 2);
    lcd.print("5-Day");
    lcd.drawFastHLine(0, 22, DISP_W, COL_DIVIDER);

    if (!g_forecastValid) {
        lcd.setTextSize(1);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(4, 50);
        lcd.print(g_wifiOnline ? "Fetching forecast..." : "No Net");
        drawPageStatusBar("[3/5]");
        return;
    }

    for (int i = 0; i < 5; i++) {
        if (!g_forecast[i].valid) break;
        int ry = 24 + i * 18;
        ForecastDay& fc = g_forecast[i];

        lcd.setTextSize(1);
        lcd.setTextColor(i == 0 ? COL_STATUS : COL_COND);
        lcd.setCursor(2, ry + 5);
        lcd.print(fc.day);

        drawWeatherIcon(26, ry, fc.conditionId, COL_COND);

        lcd.setTextColor(COL_HI);
        char hiBuf[8];
        snprintf(hiBuf, sizeof(hiBuf), "%+.0f", fc.tempMax);
        lcd.setCursor(46, ry + 5);
        lcd.print(hiBuf);

        lcd.setTextColor(COL_LO);
        char loBuf[8];
        snprintf(loBuf, sizeof(loBuf), "%+.0f", fc.tempMin);
        lcd.setCursor(76, ry + 5);
        lcd.print(loBuf);

        lcd.setTextColor(COL_STATUS);
        char condShort[15];
        strncpy(condShort, fc.condition, 14);
        condShort[14] = '\0';
        lcd.setCursor(106, ry + 5);
        lcd.print(condShort);

        if (i < 4)
            lcd.drawFastHLine(0, ry + 16, DISP_W, COL_DIVIDER);
    }

    lcd.drawFastHLine(0, ZONE_STATUS_Y - 2, DISP_W, COL_DIVIDER);
    drawPageStatusBar("[3/5]");
}

// ─────────────────────────────────────────────────────────────────────────────
// drawVBarSeg — segmented vertical bar (VU-meter style, 8 segments)
// pct 0–100: green (0–62%) → orange (62–87%) → red (87–100%), empty = dark
// ─────────────────────────────────────────────────────────────────────────────
static void drawVBarSeg(int x, int y, int w, int h, float pct) {
    const int NUM_SEG = 8;
    int segH   = (h - (NUM_SEG - 1)) / NUM_SEG;  // 1-px gap between segments
    int filled = (int)(pct / 100.0f * NUM_SEG + 0.5f);
    if (filled > NUM_SEG) filled = NUM_SEG;
    for (int s = 0; s < NUM_SEG; s++) {
        int sy = y + (NUM_SEG - 1 - s) * (segH + 1);  // s=0 = bottom
        uint16_t col;
        if (s < filled) {
            if      (s >= 7) col = COL_OFFLINE;    // red   (top seg)
            else if (s >= 5) col = COL_STALE;      // orange (2 segs)
            else             col = COL_ONLINE;     // green  (5 segs)
        } else {
            col = COL_BAR_EMPTY;
        }
        lcd.fillRect(x, sy, w, segH, col);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 4 — ESP32 hardware info
// Four 60-px columns: RAM | FLASH | WIFI | TEMP
// Vertical segmented bars + live numeric values, refreshed every 2 s.
// ─────────────────────────────────────────────────────────────────────────────
static void drawPageHWInfo() {
    const int SLOT_W  = 60;
    const int BAR_W   = 44;
    const int BAR_OFF = (SLOT_W - BAR_W) / 2;   // 8 px left margin per slot
    const int BAR_TOP = 33;
    const int BAR_H   = 63;                      // 8 segs × 7 px + 7 gaps

    // ── Header (two-arg: bg fill → no flicker on refresh) ────────────────────
    lcd.setTextSize(2);
    lcd.setTextColor(COL_TITLE, COL_BG);
    lcd.setCursor(4, 2);
    lcd.print("ESP32 Info      ");
    lcd.drawFastHLine(0, 22, DISP_W, COL_DIVIDER);

    // ── Gather metrics ────────────────────────────────────────────────────────
    uint32_t heapTotal = ESP.getHeapSize();
    uint32_t heapFree  = ESP.getFreeHeap();
    float    heapPct   = (heapTotal > 0)
                         ? (heapTotal - heapFree) * 100.0f / heapTotal : 0.0f;

    uint32_t sketchSz  = ESP.getSketchSize();
    uint32_t sketchFree= ESP.getFreeSketchSpace();
    float    flashPct  = (sketchSz + sketchFree > 0)
                         ? sketchSz * 100.0f / (sketchSz + sketchFree) : 0.0f;

    int   rssi    = g_wifiOnline ? WiFi.RSSI() : -120;
    float wifiPct = g_wifiOnline
                    ? constrain((rssi + 100) * 2.0f, 0.0f, 100.0f) : 0.0f;

    float tempC   = temperatureRead();
    float tempPct = constrain((tempC - 20.0f) / 70.0f * 100.0f, 0.0f, 100.0f);

    // ── Per-column data ───────────────────────────────────────────────────────
    const char* labels[4] = { "RAM", "FLSH", "WIFI", "TEMP" };
    float       pcts[4]   = { heapPct, flashPct, wifiPct, tempPct };

    for (int i = 0; i < 4; i++) {
        int slotX = i * SLOT_W;
        int barX  = slotX + BAR_OFF;

        // Column label
        lcd.setTextSize(1);
        lcd.setTextColor(COL_DATE, COL_BG);
        int lw = strlen(labels[i]) * 6;
        lcd.setCursor(slotX + (SLOT_W - lw) / 2, 24);
        lcd.print(labels[i]);

        // Segmented bar
        drawVBarSeg(barX, BAR_TOP, BAR_W, BAR_H, pcts[i]);

        // Value below bar
        char vbuf[12];
        switch (i) {
            case 0: snprintf(vbuf, sizeof(vbuf), "%.0f%%",  heapPct);  break;
            case 1: snprintf(vbuf, sizeof(vbuf), "%.0f%%",  flashPct); break;
            case 2:
                if (g_wifiOnline) snprintf(vbuf, sizeof(vbuf), "%ddBm",  rssi);
                else              strncpy (vbuf, "--dBm", sizeof(vbuf));
                break;
            case 3: snprintf(vbuf, sizeof(vbuf), "%.0fC",   tempC);    break;
        }
        lcd.fillRect(slotX, BAR_TOP + BAR_H + 2, SLOT_W, 9, COL_BG);
        int vw = strlen(vbuf) * 6;
        lcd.setTextColor(COL_COND, COL_BG);
        lcd.setCursor(slotX + (SLOT_W - vw) / 2, BAR_TOP + BAR_H + 3);
        lcd.print(vbuf);
    }

    // ── Chip info strip ───────────────────────────────────────────────────────
    lcd.fillRect(0, 110, DISP_W, 8, COL_BG);
    char chipBuf[48];
    snprintf(chipBuf, sizeof(chipBuf), "%s  %dc  %dMHz  free:%uB",
        ESP.getChipModel(), (int)ESP.getChipCores(),
        (int)ESP.getCpuFreqMHz(), (unsigned)heapFree);
    lcd.setTextSize(1);
    lcd.setTextColor(COL_STATUS, COL_BG);
    lcd.setCursor(2, 110);
    lcd.print(chipBuf);

    // ── Status bar ────────────────────────────────────────────────────────────
    lcd.drawFastHLine(0, ZONE_STATUS_Y - 2, DISP_W, COL_DIVIDER);
    clearZone(0, ZONE_STATUS_Y, DISP_W, DISP_H - ZONE_STATUS_Y);
    lcd.setTextSize(1);
    lcd.fillCircle(4, ZONE_STATUS_Y + 4, 3, g_wifiOnline ? COL_ONLINE : COL_OFFLINE);

    unsigned long upSec = millis() / 1000UL;
    char upBuf[18];
    if (upSec < 3600)
        snprintf(upBuf, sizeof(upBuf), "up %lum%02lus", upSec / 60, upSec % 60);
    else
        snprintf(upBuf, sizeof(upBuf), "up %luh%02lum",
                 upSec / 3600, (upSec % 3600) / 60);
    lcd.setTextColor(COL_STATUS, COL_BG);
    lcd.setCursor(12, ZONE_STATUS_Y);
    lcd.print(upBuf);

    lcd.setCursor(DISP_W - 6 * 5 - 2, ZONE_STATUS_Y);
    lcd.print("[5/5]");
}

// ─────────────────────────────────────────────────────────────────────────────
// SolarMan — fetch bearer token
// POST https://globalapi.solarmanpv.com/account/v1.0/token?appId=...
// Returns true on success; caches token in g_solarToken.
// ─────────────────────────────────────────────────────────────────────────────
static bool fetchSolarToken() {
    if (!g_wifiOnline) return false;

    char url[128];
    snprintf(url, sizeof(url),
        "https://globalapi.solarmanpv.com/account/v1.0/token?appId=%s&language=en",
        SOLARMAN_APP_ID);

    char body[320];
    snprintf(body, sizeof(body),
        "{\"appSecret\":\"%s\",\"email\":\"%s\",\"password\":\"%s\"}",
        SOLARMAN_APP_SECRET, SOLARMAN_EMAIL, SOLARMAN_PASS_SHA256);

    WiFiClientSecure secureClient;
    secureClient.setInsecure();   // skip CA cert — acceptable for home device
    HTTPClient http;
    http.begin(secureClient, url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(12000);

    int code = http.POST(body);
    if (code != HTTP_CODE_OK) {
        Serial.printf("[Solar] Token HTTP %d\n", code);
        http.end();
        return false;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, http.getStream());
    http.end();

    if (err) {
        Serial.printf("[Solar] Token JSON: %s\n", err.c_str());
        return false;
    }

    const char* tok = doc["access_token"] | "";
    if (strlen(tok) == 0) {
        Serial.println("[Solar] Token empty");
        return false;
    }

    strncpy(g_solarToken, tok, sizeof(g_solarToken) - 1);
    g_solarToken[sizeof(g_solarToken) - 1] = '\0';
    g_solarUserId    = doc["uid"] | 0L;
    g_solarTokenValid = true;
    Serial.printf("[Solar] Token OK  uid=%ld\n", g_solarUserId);
    return true;
}

// ─────────────────────────────────────────────────────────────────────────────
// SolarMan — fetch plant realtime data (interface 4.5)
// POST https://globalapi.solarmanpv.com/station/v1.0/realTime
// Retries once on HTTP 401 (token expired).
// ─────────────────────────────────────────────────────────────────────────────
static bool fetchSolar() {
    if (!g_wifiOnline) {
        Serial.println("[Solar] Skipped — offline");
        return false;
    }
    if (!g_solarTokenValid) {
        if (!fetchSolarToken()) return false;
    }

    char authHdr[528];
    char body[48];
    snprintf(body, sizeof(body), "{\"stationId\":%lu}", (unsigned long)SOLARMAN_STATION_ID);

    for (int attempt = 0; attempt < 2; attempt++) {
        snprintf(authHdr, sizeof(authHdr), "bearer %s", g_solarToken);

        WiFiClientSecure secureClient;
        secureClient.setInsecure();
        HTTPClient http;
        http.begin(secureClient, "https://globalapi.solarmanpv.com/station/v1.0/realTime");
        http.addHeader("Content-Type", "application/json");
        http.addHeader("Authorization", authHdr);
        http.setTimeout(12000);

        int code = http.POST(body);

        if (code == 401 && attempt == 0) {
            // Token expired — re-auth once
            http.end();
            g_solarTokenValid = false;
            Serial.println("[Solar] 401 — re-auth");
            if (!fetchSolarToken()) return false;
            continue;
        }

        if (code != HTTP_CODE_OK) {
            Serial.printf("[Solar] HTTP %d\n", code);
            http.end();
            return false;
        }

        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, http.getStream());
        http.end();

        if (err) {
            Serial.printf("[Solar] JSON: %s\n", err.c_str());
            return false;
        }

        g_solar.generationPower = doc["generationPower"] | 0.0f;
        g_solar.gridPower       = doc["gridPower"]       | 0.0f;
        g_solar.batterySoc      = doc["batterySoc"]      | 0.0f;
        g_solar.lastUpdateTime  = (long)(doc["lastUpdateTime"] | 0.0f);
        g_solar.valid           = true;
        g_lastSolarFetch        = millis();

        Serial.printf("[Solar] %.0fW gen  %.0fW grid  bat %.0f%%\n",
            g_solar.generationPower, g_solar.gridPower, g_solar.batterySoc);
        return true;
    }
    return false;
}

// ─────────────────────────────────────────────────────────────────────────────
// Page 3 — Solar power
// Layout (240×135):
//   y= 2 : "Solar" header  textSize 2
//   y=22 : divider
//   y=28 : sun icon (16×16) + "X.XX kW" textSize 3  — current generation
//   y=57 : "Grid: ±X.XX kW" textSize 2  (green=export, red=import)
//   y=79 : "Bat: XX%"  textSize 2  — only shown if batterySoc > 0
//   y=100: "Upd: HH:MM"  textSize 1
//   y=118: divider
//   y=120: status bar  "[4/4]"
// ─────────────────────────────────────────────────────────────────────────────
static void drawPageSolar() {
    // Header
    lcd.setTextSize(2);
    lcd.setTextColor(COL_TITLE);
    lcd.setCursor(4, 2);
    lcd.print("Solar");
    lcd.drawFastHLine(0, 22, DISP_W, COL_DIVIDER);

    // Status bar (always drawn)
    clearZone(0, ZONE_STATUS_Y, DISP_W, DISP_H - ZONE_STATUS_Y);
    lcd.setTextSize(1);
    lcd.fillCircle(4, ZONE_STATUS_Y + 4, 3, g_wifiOnline ? COL_ONLINE : COL_OFFLINE);
    if (g_lastSolarFetch > 0) {
        unsigned long ageSec = (millis() - g_lastSolarFetch) / 1000UL;
        char ageBuf[22];
        if (ageSec < 60) snprintf(ageBuf, sizeof(ageBuf), "sol %lus ago", ageSec);
        else             snprintf(ageBuf, sizeof(ageBuf), "sol %lum ago", ageSec / 60);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(12, ZONE_STATUS_Y);
        lcd.print(ageBuf);
    }
    lcd.setTextColor(COL_STATUS);
    lcd.setCursor(DISP_W - 6 * 5 - 2, ZONE_STATUS_Y);
    lcd.print("[4/5]");

    if (!g_solar.valid) {
        lcd.setTextSize(1);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(4, 55);
        lcd.print(g_wifiOnline ? "Fetching solar data..." : "No Net");
        return;
    }

    // Current generation (large)
    drawWeatherIcon(4, 28, 800, COL_TEMP);   // reuse clear-sky/sun icon
    char kwBuf[14];
    snprintf(kwBuf, sizeof(kwBuf), "%.2f kW", g_solar.generationPower / 1000.0f);
    lcd.setTextSize(3);
    lcd.setTextColor(COL_TEMP);
    lcd.setCursor(28, 28);
    lcd.print(kwBuf);

    // Grid power
    float gridKw = g_solar.gridPower / 1000.0f;
    char gridBuf[20];
    if (g_solar.gridPower >= 0.0f)
        snprintf(gridBuf, sizeof(gridBuf), "Grid:+%.2fkW", gridKw);
    else
        snprintf(gridBuf, sizeof(gridBuf), "Grid:%.2fkW",  gridKw);
    lcd.setTextSize(2);
    lcd.setTextColor(g_solar.gridPower >= 0.0f ? COL_ONLINE : COL_OFFLINE);
    lcd.setCursor(4, 57);
    lcd.print(gridBuf);

    // Battery SoC
    int updY = 100;
    if (g_solar.batterySoc > 0.0f) {
        char batBuf[14];
        snprintf(batBuf, sizeof(batBuf), "Bat: %.0f%%", g_solar.batterySoc);
        lcd.setTextSize(2);
        lcd.setTextColor(COL_FEELS);
        lcd.setCursor(4, 79);
        lcd.print(batBuf);
        updY = 103;
    }

    // Last update timestamp
    if (g_solar.lastUpdateTime > 0) {
        time_t ts = (time_t)g_solar.lastUpdateTime;
        struct tm* lt = localtime(&ts);
        char updBuf[18];
        snprintf(updBuf, sizeof(updBuf), "Upd: %02d:%02d", lt->tm_hour, lt->tm_min);
        lcd.setTextSize(1);
        lcd.setTextColor(COL_STATUS);
        lcd.setCursor(4, updY);
        lcd.print(updBuf);
    }

    lcd.drawFastHLine(0, ZONE_STATUS_Y - 2, DISP_W, COL_DIVIDER);
}

// ─────────────────────────────────────────────────────────────────────────────
// Wi-Fi connect / reconnect
// ─────────────────────────────────────────────────────────────────────────────
static void wifiConnect() {
    if (WiFi.status() == WL_CONNECTED) {
        g_wifiOnline = true;
        return;
    }
    Serial.print("[WiFi] Connecting to ");
    Serial.println(WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED &&
           (millis() - start) < WIFI_CONNECT_TIMEOUT_MS) {
        delay(200);
    }

    if (WiFi.status() == WL_CONNECTED) {
        g_wifiOnline = true;
        Serial.print("[WiFi] Connected, IP: ");
        Serial.println(WiFi.localIP());
    } else {
        g_wifiOnline = false;
        Serial.println("[WiFi] Connect timeout — offline mode");
    }
    dirty_status = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// NTP sync
// ─────────────────────────────────────────────────────────────────────────────
static void ntpSync() {
    if (!g_wifiOnline) return;
    configTzTime(TIMEZONE_POSIX, NTP_SERVER);
    struct tm t;
    if (getLocalTime(&t, 5000)) {
        g_timeValid = true;
        Serial.printf("[NTP] Time synced: %02d:%02d:%02d\n",
                      t.tm_hour, t.tm_min, t.tm_sec);
    } else {
        Serial.println("[NTP] Sync failed");
    }
    dirty_clock   = true;
    dirty_date    = true;
    dirty_status  = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// OpenWeatherMap fetch
// ─────────────────────────────────────────────────────────────────────────────
static void fetchWeather() {
    if (!g_wifiOnline) {
        Serial.println("[OWM] Skipped — offline");
        return;
    }

    char url[192];
    snprintf(url, sizeof(url),
        "http://api.openweathermap.org/data/2.5/weather"
        "?q=%s,%s&appid=%s&units=metric",
        OWM_CITY, OWM_COUNTRY, OWM_API_KEY);

    HTTPClient http;
    http.begin(url);
    http.setTimeout(8000);
    int code = http.GET();

    if (code != HTTP_CODE_OK) {
        Serial.printf("[OWM] HTTP error %d\n", code);
        http.end();
        dirty_status = true;
        return;
    }

    // Parse — ArduinoJson v7 (JsonDocument replaces StaticJsonDocument)
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, http.getStream());
    http.end();

    if (err) {
        Serial.printf("[OWM] JSON error: %s\n", err.c_str());
        dirty_status = true;
        return;
    }

    g_weather.tempC      = doc["main"]["temp"]       | 0.0f;
    g_weather.feelsLikeC = doc["main"]["feels_like"] | 0.0f;
    g_weather.humidity   = doc["main"]["humidity"]   | 0;
    g_weather.conditionId = doc["weather"][0]["id"]  | 800;

    const char* cond = doc["weather"][0]["main"] | "---";
    strncpy(g_weather.condition, cond, sizeof(g_weather.condition) - 1);
    g_weather.condition[sizeof(g_weather.condition) - 1] = '\0';
    g_weather.valid = true;

    g_lastWeatherFetch = millis();
    Serial.printf("[OWM] %.1f°C  %s  Hum:%u%%\n",
                  g_weather.tempC, g_weather.condition, g_weather.humidity);

    dirty_weather = true;
    dirty_status  = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// OWM — 5-day/3-hourly forecast, grouped by calendar date
// Uses a JSON filter to reduce peak RAM usage.
// ─────────────────────────────────────────────────────────────────────────────
static void fetchForecast() {
    if (!g_wifiOnline) return;

    char url[192];
    snprintf(url, sizeof(url),
        "http://api.openweathermap.org/data/2.5/forecast"
        "?q=%s,%s&appid=%s&units=metric&cnt=40",
        OWM_CITY, OWM_COUNTRY, OWM_API_KEY);

    HTTPClient http;
    http.begin(url);
    http.setTimeout(10000);
    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        Serial.printf("[OWM-FC] HTTP error %d\n", code);
        http.end();
        return;
    }

    // Filter keeps only the fields we need — cuts RAM by ~70%
    JsonDocument filter;
    filter["list"][0]["dt_txt"]              = true;
    filter["list"][0]["main"]["temp"]        = true;
    filter["list"][0]["main"]["temp_min"]    = true;
    filter["list"][0]["main"]["temp_max"]    = true;
    filter["list"][0]["main"]["humidity"]    = true;
    filter["list"][0]["weather"][0]["id"]    = true;
    filter["list"][0]["weather"][0]["main"]  = true;

    JsonDocument doc;
    DeserializationError err = deserializeJson(
        doc, http.getStream(), DeserializationOption::Filter(filter));
    http.end();
    if (err) {
        Serial.printf("[OWM-FC] JSON error: %s\n", err.c_str());
        return;
    }

    // Reset slots
    for (int i = 0; i < 5; i++) g_forecast[i] = {};

    int dayIdx = -1;
    char curDate[11] = "";

    for (JsonObject entry : doc["list"].as<JsonArray>()) {
        const char* dtTxt = entry["dt_txt"] | "";
        if (strlen(dtTxt) < 10) continue;

        char entryDate[11];
        strncpy(entryDate, dtTxt, 10);
        entryDate[10] = '\0';

        if (strcmp(entryDate, curDate) != 0) {
            dayIdx++;
            if (dayIdx >= 5) break;
            strncpy(curDate, entryDate, sizeof(curDate));
            strncpy(g_forecast[dayIdx].dateStr, entryDate,
                    sizeof(g_forecast[dayIdx].dateStr));
            const char* dn = dayNameFromDateStr(entryDate);
            strncpy(g_forecast[dayIdx].day, dn,
                    sizeof(g_forecast[dayIdx].day) - 1);
            g_forecast[dayIdx].day[sizeof(g_forecast[dayIdx].day) - 1] = '\0';

            float t0 = entry["main"]["temp"] | 0.0f;
            g_forecast[dayIdx].tempMin     = entry["main"]["temp_min"] | t0;
            g_forecast[dayIdx].tempMax     = entry["main"]["temp_max"] | t0;
            g_forecast[dayIdx].humidity    = entry["main"]["humidity"] | 0;
            g_forecast[dayIdx].conditionId = entry["weather"][0]["id"] | 800;
            const char* c = entry["weather"][0]["main"] | "---";
            strncpy(g_forecast[dayIdx].condition, c,
                    sizeof(g_forecast[dayIdx].condition) - 1);
            g_forecast[dayIdx].condition[sizeof(g_forecast[dayIdx].condition) - 1] = '\0';
            g_forecast[dayIdx].valid = true;

        } else {
            float t   = entry["main"]["temp"]     | g_forecast[dayIdx].tempMin;
            float mn  = entry["main"]["temp_min"] | t;
            float mx  = entry["main"]["temp_max"] | t;
            if (mn < g_forecast[dayIdx].tempMin) g_forecast[dayIdx].tempMin = mn;
            if (mx > g_forecast[dayIdx].tempMax) g_forecast[dayIdx].tempMax = mx;
            // Prefer noontime entry for representative condition
            if (strstr(dtTxt, "12:00:00") != nullptr) {
                g_forecast[dayIdx].humidity    = entry["main"]["humidity"] | 0;
                g_forecast[dayIdx].conditionId = entry["weather"][0]["id"] | 800;
                const char* c = entry["weather"][0]["main"] | "---";
                strncpy(g_forecast[dayIdx].condition, c,
                        sizeof(g_forecast[dayIdx].condition) - 1);
            }
        }
    }

    g_forecastValid     = (dayIdx >= 0);
    g_lastForecastFetch = millis();
    dirty_forecast      = true;
    Serial.printf("[OWM-FC] Got %d days\n", dayIdx + 1);
    if (dayIdx >= 1) {
        Serial.printf("[OWM-FC] Tomorrow: %.1f/%.1f°C  %s\n",
            g_forecast[1].tempMin, g_forecast[1].tempMax,
            g_forecast[1].condition);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Scheduler tick — non-blocking, called every loop()
// ─────────────────────────────────────────────────────────────────────────────
static void schedulerTick() {
    unsigned long now = millis();

    // ── Button (GPIO0, active LOW) ────────────────────────────────────────────
    uint8_t btn_now = (uint8_t)digitalRead(BTN_PIN);
    if (btn_prev == HIGH && btn_now == LOW &&
        (now - btn_lastPress) > BTN_DEBOUNCE_MS) {
        btn_lastPress = now;
        g_page = (g_page + 1) % 5;
        dirty_page = true;
        Serial.printf("[BTN] Page → %u\n", g_page);
    }
    btn_prev = btn_now;

    // ── Wi-Fi check ───────────────────────────────────────────────────────────
    if (now - t_wifi >= WIFI_CHECK_MS) {
        t_wifi = now;
        bool wasOnline = g_wifiOnline;
        wifiConnect();
        if (!wasOnline && g_wifiOnline) {
            ntpSync();
            fetchWeather();
            fetchForecast();
            t_weather = now;
        }
    }

    // ── Weather + forecast refresh ────────────────────────────────────────────
    if (g_wifiOnline && (now - t_weather >= WEATHER_REFRESH_MS)) {
        t_weather = now;
        fetchWeather();
        fetchForecast();
    }

    // ── Solar data refresh ────────────────────────────────────────────────────
    if (g_wifiOnline && (now - t_solar >= SOLARMAN_REFRESH_MS)) {
        t_solar = now;
        fetchSolar();
        if (g_page == 3) dirty_page = true;
    }

    // ── HW info refresh every 2 s while on Page 4 ────────────────────────────
    if (g_page == 4 && (now - t_hwinfo >= 2000UL)) {
        t_hwinfo   = now;
        dirty_hwinfo = true;
    }

    // ── Clock tick (Page 0 only) ──────────────────────────────────────────────
    if (now - t_clock >= CLOCK_REFRESH_MS) {
        t_clock = now;
        if (g_timeValid && g_page == 0) {
            struct tm t;
            if (getLocalTime(&t, 100)) {
                if (t.tm_sec  != prev_sec)  dirty_clock  = true;
                if (t.tm_mday != prev_mday) dirty_date   = true;
                if (t.tm_sec == 0) {
                    dirty_status = true;
                    Serial.printf("[MEM] Free heap: %u bytes\n", ESP.getFreeHeap());
                }
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Render — page router
// ─────────────────────────────────────────────────────────────────────────────
static void render() {
    // ── Page change — full clear then redraw ─────────────────────────────────
    if (dirty_page) {
        dirty_page     = false;
        dirty_forecast = false;   // absorbed into the full redraw below
        lcd.fillScreen(COL_BG);
        // Force all Page-0 zones dirty for next visit
        dirty_clock   = true;
        dirty_date    = true;
        dirty_weather = true;
        dirty_status  = true;
        prev_sec = prev_min = prev_hour = prev_mday = -1;

        if (g_page == 1) {
            drawPageTomorrow();
        } else if (g_page == 2) {
            drawPage5Day();
        } else if (g_page == 3) {
            drawPageSolar();
        } else if (g_page == 4) {
            drawPageHWInfo();
        } else {
            drawDivider();   // Page 0 zones flush via dirty flags below
        }
    }

    // ── Pages 1 & 2: refresh when new forecast data arrives ──────────────────
    if (g_page == 1 && dirty_forecast) {
        dirty_forecast = false;
        lcd.fillScreen(COL_BG);
        drawPageTomorrow();
        return;
    }
    if (g_page == 2 && dirty_forecast) {
        dirty_forecast = false;
        lcd.fillScreen(COL_BG);
        drawPage5Day();
        return;
    }
    if (g_page == 4 && dirty_hwinfo) {
        dirty_hwinfo = false;
        drawPageHWInfo();
        return;
    }
    if (g_page != 0) return;

    // ── Page 0 — dirty zone flushes ──────────────────────────────────────────
    struct tm t = {};
    bool gotTime = g_timeValid && getLocalTime(&t, 100);

    if (dirty_clock) {
        dirty_clock = false;
        if (gotTime) {
            drawClock(&t);
            prev_sec  = t.tm_sec;
            prev_min  = t.tm_min;
            prev_hour = t.tm_hour;
        } else {
            // Pre-sync placeholder (two-arg, no blink)
            lcd.setTextSize(2);
            lcd.setTextColor(COL_STATUS, COL_BG);
            lcd.setCursor(4, ZONE_CLOCK_Y + 10);
            lcd.print("Syncing...      ");
        }
    }

    if (dirty_date && gotTime) {
        dirty_date = false;
        drawDate(&t);
        prev_mday = t.tm_mday;
    }

    if (dirty_weather) {
        dirty_weather = false;
        drawWeather();
    }

    if (dirty_status) {
        dirty_status = false;
        drawStatus();
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// setup()
// ─────────────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(200);

    Serial.println("\n================================================");
    Serial.println("  Retro Weather Clock v3 — ideaspark ESP32 1.14\"");
    Serial.println("  Build: " __DATE__ " " __TIME__);
    Serial.printf( "  Free heap at boot: %u bytes\n", ESP.getFreeHeap());
    Serial.println("================================================\n");

    // Button
    pinMode(BTN_PIN, INPUT_PULLUP);

    // Backlight
    pinMode(LCD_BLK, OUTPUT);
    digitalWrite(LCD_BLK, HIGH);

    // Display
    lcd.init(135, 240);
    lcd.setRotation(1);
    lcd.fillScreen(COL_BG);
    drawDivider();

    lcd.setTextSize(1);
    lcd.setTextColor(COL_COND);
    lcd.setCursor(4, ZONE_STATUS_Y);
    lcd.print("Booting...");
    Serial.println("[DISP] Display initialized");

    // Network boot sequence
    wifiConnect();
    if (g_wifiOnline) {
        ntpSync();
        fetchWeather();
        fetchForecast();
        fetchSolarToken();
        fetchSolar();
    }

    unsigned long now = millis();
    t_clock   = now;
    t_wifi    = now;
    t_weather = now;
    t_solar   = now;
    t_hwinfo  = now;

    dirty_clock   = true;
    dirty_date    = true;
    dirty_weather = true;
    dirty_status  = true;
    dirty_page    = true;   // triggers initial full render

    Serial.println("[BOOT] Setup complete\n");
}

// ─────────────────────────────────────────────────────────────────────────────
// loop()
// ─────────────────────────────────────────────────────────────────────────────
void loop() {
    schedulerTick();
    render();
}
