"""Read-only loopback/SSM smoke; never print administrator records."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    base = 'http://127.0.0.1:18080'
    with urlopen(base + '/portfolio/admin.html', timeout=30) as response:
        assert response.status == 200 and b'/portfolio/assets/' in response.read()
    for path in ('/overview', '/records?kind=analyses', '/records?kind=drafts', '/records?kind=reports'):
        with urlopen(base + '/api/portfolio/admin' + path, timeout=30) as response:
            assert response.headers['Cache-Control'] == 'no-store'
            result = json.load(response)
            if path == '/overview':
                assert isinstance(result['active_visitors'], int)
            else:
                assert isinstance(result['items'], list) and len(result['items']) <= 50
    for headers in ({'Origin': 'https://etforca.com'}, {'Host': 'evil.example:18080'}):
        try:
            urlopen(Request(base + '/api/portfolio/admin/overview', headers=headers), timeout=30)
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError('Foreign origin or host was accepted')
    print('SSM admin smoke passed: private UI, reads, foreign origin/host denied.')


if __name__ == '__main__':
    main()
