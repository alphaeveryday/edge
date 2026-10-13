"""Verify public admin authorization after deployment without printing credentials or user data."""
import http.cookiejar
import json
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import boto3


def main():
    secret = json.loads(boto3.client('secretsmanager', region_name='ap-northeast-2').get_secret_value(SecretId='edge/portfolio/admin')['SecretString'])
    base = 'https://etforca.com/api/portfolio/admin'
    jar = http.cookiejar.CookieJar()
    browser = build_opener(HTTPCookieProcessor(jar))

    def call(path, body=None):
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Origin': 'https://etforca.com', 'Content-Type': 'application/json'})
        with browser.open(request, timeout=30) as response:
            assert response.headers['Cache-Control'] == 'no-store'
            return json.load(response)

    def blocked():
        try:
            call('/overview')
        except HTTPError as error:
            assert error.code == 401
            return
        raise AssertionError('Unauthenticated administrator data was accessible')

    blocked()
    assert call('/login', secret)['authenticated']
    assert all(cookie.secure for cookie in jar)
    try:
        result = call('/overview')
        assert isinstance(result['active_visitors'], int)
        for kind in ('analyses', 'drafts', 'reports'):
            result = call('/records?kind=' + kind)
            assert isinstance(result['items'], list) and len(result['items']) <= 50
    finally:
        call('/logout', {})
    blocked()
    print('Admin smoke passed: anonymous denied, secure login, private reads, logout denied.')


if __name__ == '__main__':
    main()
