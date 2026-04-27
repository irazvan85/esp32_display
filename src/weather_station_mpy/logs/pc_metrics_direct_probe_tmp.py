import socket

HOST = "192.168.1.25"
PORT = 8765

print("[PROBE] direct socket test", HOST, PORT)

s = None
try:
    s = socket.socket()
    s.settimeout(5)
    s.connect((HOST, PORT))
    req = (
        "GET /api/system/metrics HTTP/1.1\r\n"
        "Host: 192.168.1.25\r\n"
        "Connection: close\r\n\r\n"
    )
    s.send(req.encode("utf-8"))

    data = s.recv(256)
    if data:
        first = data.decode("utf-8", "replace").split("\r\n", 1)[0]
        print("[PROBE] first line:", first)
    else:
        print("[PROBE] no response bytes")
except Exception as exc:
    print("[PROBE] error:", exc)
finally:
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
