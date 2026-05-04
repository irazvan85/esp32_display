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
_STAT_CONNECTING = getattr(network, "STAT_CONNECTING", 1001) if network else 1001
_STAT_GOT_IP = getattr(network, "STAT_GOT_IP", 1010) if network else 1010

# Common ESP32 reason/status fallbacks used by MicroPython ports.
_STAT_NO_AP_FOUND = getattr(network, "STAT_NO_AP_FOUND", 201) if network else 201
_STAT_WRONG_PASSWORD = getattr(network, "STAT_WRONG_PASSWORD", 202) if network else 202
_STAT_CONNECT_FAIL = getattr(network, "STAT_CONNECT_FAIL", 203) if network else 203
_STAT_ASSOC_FAIL = getattr(network, "STAT_ASSOC_FAIL", _STAT_CONNECT_FAIL) if network else _STAT_CONNECT_FAIL
_STAT_HANDSHAKE_TIMEOUT = getattr(network, "STAT_HANDSHAKE_TIMEOUT", 204) if network else 204

_DEFAULT_RECONNECTS = 0
_MAX_RECONNECTS = 20


class WifiService:
    def __init__(self, cfg, preallocated=False):
        self._cfg = cfg
        self._preallocated = bool(preallocated)
        self.assoc_fail = False
        self.last_status = _STAT_IDLE
        self._wlan_reconnects = _DEFAULT_RECONNECTS
        self._wlan_pm = None
        self._connect_attempts = 0
        self._load_wlan_tuning()
        if network is None:
            self._wlan = None
            return

        # Attach to the STA interface that boot.py pre-initialised.
        gc.collect()
        self._wlan = network.WLAN(network.STA_IF)
        if not self._wlan.active():
            self._wlan.active(True)
        self._apply_wlan_tuning()

    def _load_wlan_tuning(self):
        wifi_cfg = self._cfg.get("wifi", {}) if isinstance(self._cfg, dict) else {}

        try:
            reconnects = int(wifi_cfg.get("reconnects", _DEFAULT_RECONNECTS))
        except Exception:
            reconnects = _DEFAULT_RECONNECTS

        if reconnects < -1:
            reconnects = -1
        if reconnects > _MAX_RECONNECTS:
            reconnects = _MAX_RECONNECTS
        self._wlan_reconnects = reconnects

        self._wlan_pm = self._resolve_pm_value(wifi_cfg.get("pm", "performance"))

    def _resolve_pm_value(self, pm_value):
        if network is None:
            return None

        if isinstance(pm_value, int):
            return pm_value

        if pm_value is None:
            return None

        text = str(pm_value).strip().lower()
        if not text:
            return None

        if text in ("none", "off", "disabled"):
            resolved = getattr(network, "PM_NONE", None)
        elif text in ("powersave", "power_save", "save"):
            resolved = getattr(network, "PM_POWERSAVE", None)
        elif text in ("performance", "perf", "on"):
            resolved = getattr(network, "PM_PERFORMANCE", None)
        else:
            try:
                return int(text)
            except Exception:
                return None

        if isinstance(resolved, int):
            return resolved
        return None

    def _apply_wlan_tuning(self):
        if self._wlan is None:
            return

        reconnects = getattr(self, '_wlan_reconnects', _DEFAULT_RECONNECTS)
        try:
            self._wlan.config(reconnects=reconnects)
        except Exception:
            pass

        pm = getattr(self, '_wlan_pm', None)
        if pm is not None:
            try:
                self._wlan.config(pm=pm)
            except Exception:
                pass

    def _is_assoc_fail_status(self, status):
        if status is None:
            return False

        return status in (
            _STAT_ASSOC_FAIL,
            _STAT_CONNECT_FAIL,
            _STAT_HANDSHAKE_TIMEOUT,
            15,
            203,
            204,
        )

    def _is_terminal_connect_status(self, status):
        if status is None:
            return False

        if status in (_STAT_IDLE, _STAT_CONNECTING, _STAT_GOT_IP):
            return False

        if self._is_assoc_fail_status(status):
            return True

        if status in (_STAT_NO_AP_FOUND, _STAT_WRONG_PASSWORD):
            return True

        if isinstance(status, int) and status >= 200:
            return True

        return False

    def _should_scan_retry(self, last_status, timed_out):
        # Keep ASSOC_FAIL-specific retry always enabled, and allow broader
        # timeout/terminal retries when prefer_bssid_scan is enabled.
        if self._is_assoc_fail_status(last_status):
            return True

        wifi_cfg = self._cfg.get("wifi", {}) if isinstance(self._cfg, dict) else {}
        prefer_scan = bool(wifi_cfg.get("prefer_bssid_scan", False))
        if not prefer_scan:
            return False

        if timed_out:
            return True

        return self._is_terminal_connect_status(last_status)

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

        if bool(getattr(self, "_preallocated", False)):
            # On boot-preallocated STA interfaces this board is most stable with
            # the legacy flow: tune + disconnect + short settle, without an
            # active(False/True) cycle before each connect.
            self._apply_wlan_tuning()
            try:
                self._wlan.disconnect()
            except Exception:
                pass
            await asyncio.sleep_ms(300)
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

        self._apply_wlan_tuning()

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

        self._connect_attempts = int(getattr(self, "_connect_attempts", 0)) + 1

        wifi_cfg = self._cfg.get("wifi", {}) if isinstance(self._cfg, dict) else {}
        legacy_no_reset = bool(wifi_cfg.get("legacy_first_connect_no_reset", True))
        use_legacy_first_connect = (
            bool(getattr(self, "_preallocated", False))
            and legacy_no_reset
            and (not force)
            and cfg_bssid is None
            and self._connect_attempts == 1
        )

        print("[WiFi] Connecting to %s" % ssid)
        if use_legacy_first_connect:
            # Match wifitest behavior for the first boot attempt when STA is
            # already active from boot preallocation: connect directly.
            self._apply_wlan_tuning()
        else:
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
            timed_out = (not self._wlan.isconnected()) and (
                ticks_diff(ticks_ms(), started) >= timeout_ms
            )
            return last_status, timed_out

        last_status, timed_out = await _wait_for_connect()

        if (
            allow_scan_retry
            and (not self._wlan.isconnected())
            and cfg_bssid is None
            and self._should_scan_retry(last_status, timed_out)
        ):
            # After ASSOC_FAIL the radio is in an error state.  wlan.scan() returns
            # ESP_ERR_INVALID_STATE (0x0102) unless we cycle active(False/True) first.
            # A minimal radio cycle is sufficient to clear the state.
            try:
                self._wlan.disconnect()
            except Exception:
                pass
            await asyncio.sleep_ms(250)
            try:
                self._wlan.active(False)
            except Exception:
                pass
            await asyncio.sleep_ms(500)
            try:
                self._wlan.active(True)
            except Exception:
                pass
            await asyncio.sleep_ms(250)
            scanned_bssid = self._scan_best_bssid(ssid, allow_low_heap=True)
            if scanned_bssid is not None:
                print("[WiFi] BSSID scan found AP, retrying with pinned BSSID")
                await self._prepare_sta_for_connect()
                try:
                    try:
                        self._wlan.connect(ssid, password, bssid=scanned_bssid)
                    except TypeError:
                        self._wlan.connect(ssid, password)
                except OSError as exc:
                    print("[WiFi] bssid retry connect() error: %s" % exc)
                else:
                    last_status, timed_out = await _wait_for_connect()
            else:
                print("[WiFi] BSSID scan: AP not visible, cannot pin BSSID")

        if self._is_assoc_fail_status(last_status):
            self.assoc_fail = True
            print(
                "[WiFi] status=%s ASSOC_FAIL — AP rejected association, longer backoff needed"
                % (str(last_status),)
            )
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
