import usocket, utime

HOST = '192.168.1.25'
PORT = 8765
PATH = '/api/system/metrics'

print('[probe] resolving', HOST, PORT)
try:
    ai = usocket.getaddrinfo(HOST, PORT, 0, usocket.SOCK_STREAM)
    print('[probe] getaddrinfo ok:', ai[0])
    addr = ai[0][-1]

    s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    s.settimeout(5)
    print('[probe] connecting to', addr)
    s.connect(addr)
    print('[probe] connected')

    req = 'GET {} HTTP/1.0\r\nHost: {}:{}\r\nConnection: close\r\n\r\n'.format(PATH, HOST, PORT)
    s.send(req.encode())
    print('[probe] request sent')

    data = s.recv(256)
    first_line = data.split(b'\r\n')[0]
    print('[probe] response:', first_line)
    s.close()
except Exception as e:
    print('[probe] ERROR:', repr(e))
