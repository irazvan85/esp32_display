import gc
try:
    import usocket as socket
except ImportError:
    import socket

def _probe_tcp(host, port):
    addr = None
    s = None
    try:
        addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(8)
        s.connect(addr)
        req = "GET /data/2.5/weather?q=Timisoara,RO&appid=ba8120071e9709b862c98ff82e97a5b5&units=metric HTTP/1.0\r\nHost: api.openweathermap.org\r\nConnection: close\r\n\r\n"
        s.send(req.encode())
        resp = s.recv(256)
        first_line = resp.split(b"\r\n")[0].decode("utf-8", "ignore")
        print("[PROBE] TCP connect to %s:%d OK, HTTP: %s" % (host, port, first_line))
    except Exception as e:
        print("[PROBE] TCP connect to %s:%d FAIL: %r" % (host, port, e))
    finally:
        if s:
            try: s.close()
            except: pass
        gc.collect()

def _probe_dns(host):
    try:
        info = socket.getaddrinfo(host, 80)
        print("[PROBE] DNS %s -> %s" % (host, info[0][-1][0] if info else "no result"))
    except Exception as e:
        print("[PROBE] DNS %s FAIL: %r" % (host, e))

_probe_dns("api.openweathermap.org")
_probe_dns("8.8.8.8")
_probe_tcp("api.openweathermap.org", 80)
print("[PROBE] done")
