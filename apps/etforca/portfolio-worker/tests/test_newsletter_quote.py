import asyncio
from datetime import date

import pytest

from edge_portfolio import market_data, newsletter_quote


@pytest.mark.parametrize('fails', [False, True])
async def test_newsletter_quotes_share_price_admission_and_release_after_failure(monkeypatch, fails):
    started = []
    async def quote(*args):
        started.append(args)
        if fails:
            raise ValueError('NEWSLETTER_QUOTE_UNAVAILABLE')
        return {'id': 'price'}
    monkeypatch.setattr(newsletter_quote, '_load_quote', quote)
    assert market_data._PRICE_SLOTS.acquire(blocking=False)
    try:
        task = asyncio.create_task(newsletter_quote.load_quote('US:VOO', date(2026, 10, 21)))
        await asyncio.sleep(.06)
        assert not started  # Dashboard/analysis prices already own the worker's one child slot.
    finally:
        market_data._PRICE_SLOTS.release()
    if fails:
        with pytest.raises(ValueError, match='NEWSLETTER_QUOTE_UNAVAILABLE'):
            await task
    else:
        assert await task == {'id': 'price'}
    assert len(started) == 1
    assert market_data._PRICE_SLOTS.acquire(blocking=False)
    market_data._PRICE_SLOTS.release()
