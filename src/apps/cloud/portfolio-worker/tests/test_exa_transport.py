import httpx
import pytest

from edge_portfolio.exa_transport import ExaTransport


async def test_exa_key_only_reaches_the_https_exa_origin_and_survives_retry(monkeypatch):
    requests = []
    async def sleep(delay): pass
    async def respond(request):
        requests.append((str(request.url), request.headers.get('x-api-key')))
        return httpx.Response(429, headers={'Retry-After': '0'}) if len(requests) == 1 else httpx.Response(200)
    monkeypatch.setattr('edge_portfolio.exa_transport.asyncio.sleep', sleep)
    async with httpx.AsyncClient(transport=ExaTransport(httpx.MockTransport(respond), api_key='exa-test')) as client:
        for url in ('https://mcp.exa.ai/mcp', 'https://other.example/mcp', 'http://mcp.exa.ai/mcp', 'https://mcp.exa.ai:444/mcp'):
            await client.post(url, json={'method': 'tools/call'})
    assert [key for _, key in requests] == ['exa-test', 'exa-test', None, None, None]
    assert all('exa-test' not in url for url, _ in requests)


@pytest.mark.parametrize('retry_after,expected_delay', [('2', 2), ('invalid', 2), ('nan', 2)])
async def test_temporary_exa_rate_limit_retries_the_same_request_without_breaking_mcp(monkeypatch, retry_after, expected_delay):
    requests, waits = [], []
    async def sleep(delay): waits.append(delay)
    async def respond(request):
        requests.append((request.method, request.url, await request.aread()))
        return httpx.Response(429, headers={'Retry-After': retry_after}) if len(requests) == 1 else httpx.Response(200, json={'ok': True})
    monkeypatch.setattr('edge_portfolio.exa_transport.asyncio.sleep', sleep)
    async with httpx.AsyncClient(transport=ExaTransport(httpx.MockTransport(respond))) as client:
        response = await client.post('https://mcp.exa.ai/mcp', json={'method': 'tools/call'})
    assert response.status_code == 200
    assert requests[0] == requests[1]
    assert waits == [expected_delay]


@pytest.mark.parametrize('status,retry_after,calls', [(429, '1', 3), (429, '120', 1), (401, '1', 1), (500, '1', 1)])
async def test_retries_are_bounded_and_do_not_ignore_long_retry_after(monkeypatch, status, retry_after, calls):
    attempts = []
    async def sleep(delay): pass
    async def respond(request):
        attempts.append(1)
        return httpx.Response(status, headers={'Retry-After': retry_after})
    monkeypatch.setattr('edge_portfolio.exa_transport.asyncio.sleep', sleep)
    async with httpx.AsyncClient(transport=ExaTransport(httpx.MockTransport(respond))) as client:
        response = await client.post('https://mcp.exa.ai/mcp', json={})
    assert response.status_code == status
    assert len(attempts) == calls
