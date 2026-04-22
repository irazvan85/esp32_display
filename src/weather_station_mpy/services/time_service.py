"""NTP sync and Romania EET/EEST timezone conversion."""

import time

try:
    ntptime = __import__("ntptime")
except ImportError:
    ntptime = None

try:
    asyncio = __import__("uasyncio")
except ImportError:
    import asyncio


_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class TimeService:
    def __init__(self, cfg):
        self._cfg = cfg

    async def sync_ntp(self, retries=3):
        if ntptime is None:
            print("[NTP] ntptime module not available")
            return False

        ntp_server = self._cfg["time"].get("ntp_server", "pool.ntp.org")
        ntptime.host = ntp_server

        for _ in range(retries):
            try:
                ntptime.settime()
                now_utc = time.localtime()
                print(
                    "[NTP] Time synced (UTC): %02d:%02d:%02d"
                    % (now_utc[3], now_utc[4], now_utc[5])
                )
                return True
            except OSError as exc:
                print("[NTP] Sync failed: %s" % exc)
                await asyncio.sleep(1)

        return False

    def now_localtime(self):
        now_utc = time.localtime()
        utc_epoch = time.mktime(now_utc)
        offset_hours = self._romania_offset_hours(utc_epoch)
        return time.localtime(utc_epoch + (offset_hours * 3600))

    def format_date_line(self, tm):
        return "%s  %02d %s %04d" % (
            _DAYS[tm[6]],
            tm[2],
            _MONTHS[tm[1] - 1],
            tm[0],
        )

    def _romania_offset_hours(self, utc_epoch):
        # EET UTC+2, EEST UTC+3. Transition at 01:00 UTC on last Sundays
        # of March and October.
        utc = time.localtime(utc_epoch)
        year = utc[0]

        start_day = self._last_sunday(year, 3)
        end_day = self._last_sunday(year, 10)

        start_epoch = time.mktime((year, 3, start_day, 1, 0, 0, 0, 0))
        end_epoch = time.mktime((year, 10, end_day, 1, 0, 0, 0, 0))

        if start_epoch <= utc_epoch < end_epoch:
            return 3
        return 2

    @staticmethod
    def _last_sunday(year, month):
        # Iterate backward from the 31st to find a valid date that is Sunday.
        for day in range(31, 24, -1):
            try:
                tm = time.localtime(time.mktime((year, month, day, 12, 0, 0, 0, 0)))
            except ValueError:
                continue
            if tm[6] == 0:
                return day
        return 31
