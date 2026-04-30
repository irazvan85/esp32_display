"""Wi-Fi connectivity helper for ESP32 MicroPython."""

import gc

try:
    network = __import__("network")
except ImportError:
    network = None

try:
    asyncio = __import__("uasyncio")
except ImportError:
    import asyncio

from compat import ticks_diff, ticks_ms

_STAT_IDLE = getattr(network, "STAT_IDLE", 1000) if network else 1000


class WifiService:
    def __init__(self, cfg, preallocated=False):
        self._cfg = cfg
        self._preallocated = bool(preallocated)
        self.assoc_fail = False
        self.last_status = _STAT_IDLE
        if network is None:
            self._wlan = None
            return

        # Attach to the STA interface that boot.py pre-initialised.
        gc.collect()
        self._wlan = network.WLAN(network.STA_IF)
        if not self._wlan.active():
            self._wlan.active(True)
        try:
            self._wlan.config(reconnects=0)
        except Exception:
            pass

    def is_connected(self):
        if self._wlan is None:
            return False
        return self._wlan.isconnected()

    def ip(self):
        if self._wlan is None:
            return "0.0.0.0"
        if not self._wlan.isconnected():
            return "0.0.0.0"
        return self._wlan.ifconfig()[0]

    def _normalize_ssid(self, value):
        if value is None:
            return ""
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except Exception:
                return value.decode("latin-1", "ignore")
        return str(value)

    def _parse_bssid(self, value):
        if not value:
            return None
        if isinstance(value, bytes):
            if len(value) == 6:
                return value
            try:
                value = value.decode("ascii")
            except Exception:
                return None
        text = str(value).strip()
        if not text:
            return None
        parts = text.split(":")
        if len(parts) != 6:
            return None
        try:
            octets = [int(part, 16) for part in parts]
        except ValueError:
            return None
        for octet in octets:
            if octet < 0 or octet > 255:
                return None
        return bytes(octets)

    def _scan_best_bssid(self, target_ssid, allow_low_heap=False):
        if self._wlan is None:
            return None

        wifi_cfg = self._cfg.get("wifi", {})
        min_heap = int(wifi_cfg.get("min_heap_for_scan_bytes", 98_000))
        if allow_low_heap:
            assoc_scan_min = int(wifi_cfg.get("assoc_fail_scan_min_heap_bytes", 58_000))
            if assoc_scan_min > 0 and assoc_scan_min < min_heap:
                min_heap = assoc_scan_min

        gc.collect()

        try:
            free_heap = gc.mem_free()
        except Exception:
            free_heap = 0

        if free_heap and free_heap < min_heap:
            print("[WiFi] scan skipped - low heap %d < %d" % (free_heap, min_heap))
            return None

        try:
            entries = self._wlan.scan()
        except Exception as exc:
            print("[WiFi] scan error: %s" % exc)
            return None

        best_bssid = None
        best_rssi = -9999

        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) < 5:
                continue

            essid = self._normalize_ssid(entry[0])
            bssid = entry[1]
            rssi = entry[3]

            if essid != target_ssid:
                continue
            if not isinstance(bssid, (bytes, bytearray)) or len(bssid) != 6:
                continue

            try:
                rssi_val = int(rssi)
            except Exception:
                rssi_val = -9999

            if best_bssid is None or rssi_val > best_rssi:
                best_bssid = bytes(bssid)
                best_rssi = rssi_val

        if best_bssid is not None:
            bssid_txt = ":".join(["%02x" % x for x in best_bssid])
            print("[WiFi] scan selected bssid %s rssi=%d" % (bssid_txt, best_rssi))

        return best_bssid

    async def _prepare_sta_for_connect(self):
        if self._wlan is None:
            return

        try:
            self._wlan.active(False)
        except Exception:
            pass

        await asyncio.sleep_ms(1000)

        try:
            self._wlan.active(True)
        except Exception:
            pass

        try:
            self._wlan.config(reconnects=0)
        except Exception:
            pass

        try:
            self._wlan.disconnect()
        except Exception:
            pass

        await asyncio.sleep_ms(1000)

    def radio_off(self):
        """Power down the WiFi radio to stop sending management frames.

        Called during ASSOC_FAIL backoff so the ESP32 goes completely silent.
        The AP's rate-limit timer then expires without being refreshed by probe
        requests or deauth frames from this MAC.
        _prepare_sta_for_connect() calls active(True) before the next attempt.
        """
        if self._wlan is None:
            return
        try:
            self._wlan.active(False)
            print("[WiFi] radio OFF during backoff")
        except Exception:
            pass

    async def ensure_connected(self, force=False, allow_scan_retry=True):
        if self._wlan is None:
            return False

        self.assoc_fail = False
        self.last_status = _STAT_IDLE
        if self._wlan.isconnected() and not force:
            return True

        if force and self._wlan.isconnected():
            print("[WiFi] forcing reconnect")

        ssid = self._cfg["wifi"]["ssid"]
        password = self._cfg["wifi"]["password"]
        cfg_bssid = self._parse_bssid(self._cfg["wifi"].get("bssid", ""))
        timeout_ms = int(self._cfg["wifi"].get("connect_timeout_ms", 20_000))

        print("[WiFi] Connecting to %s" % ssid)
        await self._prepare_sta_for_connect()
        try:
            if cfg_bssid is not None:
                try:
                    self._wlan.connect(ssid, password, bssid=cfg_bssid)
                except TypeError:
                    print("[WiFi] bssid unsupported, retrying")
                    self._wlan.connect(ssid, password)
            else:
                self._wlan.connect(ssid, password)
        except OSError as exc:
            print("[WiFi] connect() error: %s" % exc)
            return False

        async def _wait_for_connect():
            started = ticks_ms()
            last_status = None
            while (not self._wlan.isconnected()) and (
                ticks_diff(ticks_ms(), started) < timeout_ms
            ):
                try:
                    status = self._wlan.status()
                except OSError:
                    status = _STAT_IDLE

                if status != last_status:
                    print("[WiFi] status=%d" % status)
                    last_status = status
                    self.last_status = status

                await asyncio.sleep_ms(250)
            return last_status

        last_status = await _wait_for_connect()

        if (
            allow_scan_retry
            and (not self._wlan.isconnected())
            and last_status == 15
            and cfg_bssid is None
        ):
            scanned_bssid = self._scan_best_bssid(ssid, allow_low_heap=True)
            if scanned_bssid is not None:
                print("[WiFi] retry with scanned bssid")
                await self._prepare_sta_for_connect()
                try:
                    try:
                        self._wlan.connect(ssid, password, bssid=scanned_bssid)
                    except TypeError:
                        self._wlan.connect(ssid, password)
                except OSError as exc:
                    print("[WiFi] bssid retry connect() error: %s" % exc)
                else:
                    last_status = await _wait_for_connect()

        if last_status == 15:
            self.assoc_fail = True
            print("[WiFi] status=15 ASSOC_FAIL — AP rejected association, longer backoff needed")
        else:
            self.assoc_fail = False

        if self._wlan.isconnected():
            # Override DNS with reliable public resolver if explicitly configured.
            # NOTE: wlan.ifconfig((ip,mask,gw,dns)) switches interface to static mode
            # and can briefly drop the connection. Only apply if user explicitly sets
            # wifi.dns in config. Default empty = rely on DHCP-assigned DNS.
            _dns = self._cfg["wifi"].get("dns", "")
            if _dns:
                try:
                    ip, mask, gw, _old_dns = self._wlan.ifconfig()
                    self._wlan.ifconfig((ip, mask, gw, _dns))
                    print("[WiFi] DNS set to %s" % _dns)
                except Exception as _dns_err:
                    print("[WiFi] DNS config failed (ignored): %s" % _dns_err)
            print("[WiFi] Connected, IP: %s" % self.ip())
            return True

        print("[WiFi] Connect timeout - offline mode")
        return False
