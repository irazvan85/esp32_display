path = r'c:\WS\esp32_ideaspark_display\src\weather_station_mpy\logs\uart_post_gc_webbind.txt'
with open(path, encoding='utf-8') as f:
    lines = f.read().splitlines()

tags = {
    '[DISP] ST7789 initialized':   {'count': 0, 'first': None},
    '[WEB] Pre-bound port 80':      {'count': 0, 'first': None},
    '[WEB] Config UI:':             {'count': 0, 'first': None},
    '[WEB] Pre-bind failed':        {'count': 0, 'first': None},
    '[WEB] bind/listen error':      {'count': 0, 'first': None},
    '[PC] fetch error':             {'count': 0, 'first': None},
    '[OWM] startup fetch OK':       {'count': 0, 'first': None},
    'MemoryError':                  {'count': 0, 'first': None},
}

for i, line in enumerate(lines, 1):
    for tag, d in tags.items():
        if tag in line:
            d['count'] += 1
            if d['first'] is None:
                d['first'] = (i, line)

print('=== TAG COUNTS & FIRST OCCURRENCES ===')
for tag, d in tags.items():
    count = d['count']
    if d['first']:
        ln, text = d['first']
        first = 'line ' + str(ln) + ': ' + text
    else:
        first = 'NOT FOUND'
    print('  [' + str(count).rjust(2) + 'x] ' + repr(tag))
    print('        ' + first)

print()
print('=== FIRST 90 LINES ===')
for i, line in enumerate(lines[:90], 1):
    print(str(i).rjust(3) + ': ' + line)
