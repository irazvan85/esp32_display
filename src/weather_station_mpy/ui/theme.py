"""UI theme palettes and runtime application helper."""

import board


THEMES = {
    "retro": {
        "COL_BG": 0x0000,
        "COL_CLOCK": 0x07FF,
        "COL_DATE": 0xC618,
        "COL_TEMP": 0xFD20,
        "COL_FEELS": 0xFFE0,
        "COL_COND": 0xFFFF,
        "COL_STATUS": 0x4208,
        "COL_ONLINE": 0x07E0,
        "COL_OFFLINE": 0xF800,
        "COL_DIVIDER": 0x2945,
        "COL_TITLE": 0x07FF,
        "COL_HI": 0xFB60,
        "COL_LO": 0x5CFF,
        "COL_BAR_EMPTY": 0x1082,
        "COL_TREND_TEMP": 0xFD20,
        "COL_TREND_PRECIP": 0x5CFF,
        "COL_TREND_AXIS": 0x4208,
    },
    "light": {
        "COL_BG": 0xFFFF,
        "COL_CLOCK": 0x001F,
        "COL_DATE": 0x4A49,
        "COL_TEMP": 0xF920,
        "COL_FEELS": 0x7BE0,
        "COL_COND": 0x0000,
        "COL_STATUS": 0x528A,
        "COL_ONLINE": 0x05E0,
        "COL_OFFLINE": 0xD800,
        "COL_DIVIDER": 0xAD55,
        "COL_TITLE": 0x001F,
        "COL_HI": 0xF9C0,
        "COL_LO": 0x03FF,
        "COL_BAR_EMPTY": 0xD69A,
        "COL_TREND_TEMP": 0xF920,
        "COL_TREND_PRECIP": 0x039F,
        "COL_TREND_AXIS": 0x7BEF,
    },
    "high_contrast": {
        "COL_BG": 0x0000,
        "COL_CLOCK": 0xFFFF,
        "COL_DATE": 0xFFFF,
        "COL_TEMP": 0xFFE0,
        "COL_FEELS": 0x07E0,
        "COL_COND": 0xFFFF,
        "COL_STATUS": 0xFFFF,
        "COL_ONLINE": 0x07E0,
        "COL_OFFLINE": 0xF800,
        "COL_DIVIDER": 0xFFFF,
        "COL_TITLE": 0xFFFF,
        "COL_HI": 0xFFE0,
        "COL_LO": 0x07FF,
        "COL_BAR_EMPTY": 0x8410,
        "COL_TREND_TEMP": 0xFFE0,
        "COL_TREND_PRECIP": 0x07FF,
        "COL_TREND_AXIS": 0xFFFF,
    },
}


def apply_theme(theme_name):
    name = theme_name if theme_name in THEMES else "retro"
    colors = THEMES[name]
    for key, value in colors.items():
        setattr(board, key, value)
    return name