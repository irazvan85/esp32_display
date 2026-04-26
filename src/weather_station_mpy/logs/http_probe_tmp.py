import urllib.request, urllib.parse
ip = '192.168.1.26'
base = f'http://{ip}'
post_body = urllib.parse.urlencode({
    'theme': 'high_contrast',
    'page': ['0', '1', '4'],
    'pc_url': 'http://192.168.1.22:8765/api/system/metrics',
}, doseq=True).encode()

def check_get(label, body):
    print(f'{label}_HAS_THEME=' + str("name='theme'" in body))
    print(f'{label}_HAS_PAGE=' + str("name='page'" in body))
    print(f'{label}_HAS_PC_URL=' + str("name='pc_url'" in body))
    print(f'{label}_HAS_HIGH_CONTRAST=' + str('high_contrast' in body and 'selected' in body))
    print(f'{label}_HAS_PAGE0=' + str("value='0' checked" in body or 'value="0" checked' in body))
    print(f'{label}_HAS_PAGE1=' + str("value='1' checked" in body or 'value="1" checked' in body))
    print(f'{label}_HAS_PAGE4=' + str("value='4' checked" in body or 'value="4" checked' in body))

for label, url, data in [
    ('GET1', base + '/', None),
    ('POST', base + '/save', post_body),
    ('GET2', base + '/', None),
]:
    try:
        req = urllib.request.Request(url, data=data, method='POST' if data else 'GET')
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode('utf-8', 'replace')
            print(f'{label}_STATUS={r.status}')
            if label.startswith('GET'):
                check_get(label, body)
    except Exception as e:
        print(f'{label}_ERROR={type(e).__name__}: {e}')
