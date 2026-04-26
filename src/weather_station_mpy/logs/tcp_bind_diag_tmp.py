import gc

try:
    import usocket as socket
except ImportError:
    import socket

import network


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


def attempt(target, host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    try:
        s.bind((host, port))
        s.listen(1)
        print("RAW|%s|%s|%d|PASS" % (target, host, port))
    except OSError as exc:
        print("RAW|%s|%s|%d|FAIL|%s" % (target, host, port, err_text(exc)))
    except Exception as exc:
        print("RAW|%s|%s|%d|FAIL|%r" % (target, host, port, exc))
    finally:
        try:
            s.close()
        except Exception:
            pass
        gc.collect()


def main():
    sta = network.WLAN(network.STA_IF)
    ip_cfg = sta.ifconfig() if sta.active() else None
    bind_ip = fmt_ip(ip_cfg) or "0.0.0.0"

    print("STA|active=%s|isconnected=%s|ip=%s" % (sta.active(), sta.isconnected(), bind_ip))

    ports = (80, 8080, 8765)
    hosts = (("bind_ip", bind_ip), ("any", "0.0.0.0"))

    for target, host in hosts:
        for port in ports:
            attempt(target, host, port)


main()
