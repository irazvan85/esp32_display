import gc

try:
    import usocket as socket
except ImportError:
    import socket

import uasyncio as asyncio

from config.store import load_config
from services.wifi_service import WifiService


def _err_text(exc):
    eno = getattr(exc, "errno", None)
    args = getattr(exc, "args", ())
    if eno is not None:
        return "errno=%s" % (eno,)
    if args:
        return "args=%s" % (args,)
    return repr(exc)


def _fmt_ip(value):
    if value is None:
        return None
    if isinstance(value, tuple) and value:
        return value[0]
    return value


def _raw_bind_listen(label, host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        sock.bind((host, port))
        sock.listen(1)
        print("RAW|%s|%s:%d|PASS" % (label, host, port))
    except Exception as exc:
        print("RAW|%s|%s:%d|FAIL|%s" % (label, host, port, _err_text(exc)))
    finally:
        try:
            sock.close()
        except Exception:
            pass
        gc.collect()


async def _noop_handler(reader, writer):
    try:
        await writer.aclose()
    except Exception:
        pass


async def _asyncio_start_server(label, host, port):
    server = None
    try:
        server = await asyncio.start_server(_noop_handler, host, port, backlog=1)
        print("ASYNC|%s|%s:%d|PASS" % (label, host, port))
    except Exception as exc:
        print("ASYNC|%s|%s:%d|FAIL|%s" % (label, host, port, _err_text(exc)))
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
            try:
                await server.wait_closed()
            except Exception:
                pass
        await asyncio.sleep_ms(20)
        gc.collect()


async def _main():
    cfg = load_config("config.json")
    wifi = WifiService(cfg)

    print("[DIAG] WiFi connect start")
    connected = await wifi.ensure_connected()
    ip = _fmt_ip(wifi.ip()) if wifi.is_connected() else None
    print("WIFI|connected=%s|ip=%s" % (connected, ip))

    hosts = [("any", "0.0.0.0")]
    if ip:
        hosts.append(("ip", ip))
    else:
        print("[DIAG] No station IP available; skipping explicit IP host tests")

    for label, host in hosts:
        _raw_bind_listen(label, host, 80)

    for label, host in hosts:
        await _asyncio_start_server(label, host, 80)

    print("[DIAG] done")


asyncio.run(_main())
