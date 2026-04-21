# Install the Python Requests library:
# `pip install requests`

import requests
import json

def send_request():
    # 2.1 Obtain Token for B-END
    # POST https://api.solarmanpv.com/account/v1.0/token

    try:
        response = requests.post(
            url="https://api.solarmanpv.com/account/v1.0/token",
            params={
                "appId": "appId",
                "language": "en",
            },
            headers={
                "Content-Type": "application/json",
                "User-Agent":"Paw/3.3.1 (Macintosh; OS X/12.0.1) GCDHTTPRequest"
            },
            data=json.dumps({
                "email": "email",
                "password": "password",
                "orgId": "orgId",
                "appSecret": "appSecret"
            })
        )
        print('Response HTTP Status Code: {status_code}'.format(
            status_code=response.status_code))
        print('Response HTTP Response Body: {content}'.format(
            content=response.content))
    except requests.exceptions.RequestException:
        print('HTTP Request failed')