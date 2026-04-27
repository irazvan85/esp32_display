try:
    import usocket as socket
except ImportError:
    import socket

HOSTS = ["0.0.0.0", "", None, "192.168.1.31"]
PORT = 8089

for host in HOSTS:
    print("[Probe] host=%r port=%d" % (host, PORT))

    try:
        info = socket.getaddrinfo(host, PORT)
        print("[Probe] getaddrinfo ok: %r" % (info,))
    except Exception as e:
        print("[Probe] getaddrinfo err: %r" % (e,))

    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, PORT))
        s.listen(1)
        print("[Probe] bind/listen ok")
    except Exception as e:
        print("[Probe] bind/listen err: %r" % (e,))
    finally:
        if s is not None:
            try:
                s.close()
            except Exception as e:
                print("[Probe] close err: %r" % (e,))

    print("[Probe] ---")
