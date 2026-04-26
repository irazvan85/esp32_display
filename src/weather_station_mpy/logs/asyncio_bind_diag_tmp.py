import gc

import network
import uasyncio as asyncio


def fmt_ip(cfg):
    if isinstance(cfg, tuple) and len(cfg) >= 1:
        return cfg[0]
    return None


def err_text(exc):
    eno = getattr(exc, "errno", None)
    args = getattr(exc, "args", ())
    if eno is not None:
        return "errno=%s" % (eno,)
    if args:
        return "args=%s" % (args,)
    return repr(exc)


async def client_handler(reader, writer):
    try:
        await writer.aclose()
    except Exception:
        pass


async def attempt(target, host, port):
    server = None
    try:
        server = await asyncio.start_server(client_handler, host, port, backlog=1)
        print("ASYNC|%s|%s|%d|PASS" % (target, host, port))
    except OSError as exc:
        print("ASYNC|%s|%s|%d|FAIL|%s" % (target, host, port, err_text(exc)))
    except Exception as exc:
        print("ASYNC|%s|%s|%d|FAIL|%r" % (target, host, port, exc))
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


async def main():
    sta = network.WLAN(network.STA_IF)
    ip_cfg = sta.ifconfig() if sta.active() else None
    bind_ip = fmt_ip(ip_cfg) or "0.0.0.0"

    print("STA|active=%s|isconnected=%s|ip=%s" % (sta.active(), sta.isconnected(), bind_ip))

    ports = (80, 8080, 8765)
    hosts = (("bind_ip", bind_ip), ("any", "0.0.0.0"))

    for target, host in hosts:
        for port in ports:
            await attempt(target, host, port)


asyncio.run(main())
