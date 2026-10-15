"""Bounded retries for temporary connection failures and rate limits."""
import asyncio
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx


class ExaTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport=None, *, api_key=None):
        self.transport = transport or httpx.AsyncHTTPTransport()
        self.api_key = api_key

    async def handle_async_request(self, request):
        if self.api_key and request.url.scheme == 'https' and request.url.host == 'mcp.exa.ai' and request.url.port in (None, 443):
            request.headers['x-api-key'] = self.api_key
        else:
            request.headers.pop('x-api-key', None)
        await request.aread()
        for attempt in range(3):
            try:
                response = await self.transport.handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt == 2 or request.url.host != 'mcp.exa.ai' or request.method not in {'GET', 'POST'}:
                    raise
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            if response.status_code != 429 or attempt == 2 or request.url.host != 'mcp.exa.ai' or request.method not in {'GET', 'POST'}:
                return response
            raw = response.headers.get('Retry-After', '')
            delay = 2 ** (attempt + 1)
            try:
                delay = float(raw)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pass
            if not math.isfinite(delay):
                delay = 2 ** (attempt + 1)
            if delay > 10:
                return response
            await response.aclose()
            await asyncio.sleep(max(0, delay))

    async def aclose(self):
        await self.transport.aclose()
