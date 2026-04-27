import usocket as socket

for port in [80, 8089]:
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", port))
        s.listen(1)
        print("bind/listen success on", port)
    except Exception as e:
        print("bind/listen failed on", port, repr(e))
    finally:
        try:
            s.close()
        except Exception:
            pass

print("done")
