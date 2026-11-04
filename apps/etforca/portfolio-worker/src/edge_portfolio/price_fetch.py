"""The collector's warm price process (market_preload.PriceSession) around Yahoo's synchronous client."""
import json
import sys
from datetime import date

import pandas as pd

from .market_data import load_prices, positive_price, price_failure, price_status, with_actual_prices


def load_preload_prices(symbol, end, start, on_raw, repair_since=None, failure=None, on_fallback=None):
    """`failure`: the primary already failed this way (the parent's deadline killed it), so only the fallback runs.
    `on_fallback(failure)` tells the parent the primary has failed and the fallback is running."""
    captured = None
    pending = []
    expected = 'KRW' if symbol == 'KRW=X' or symbol.endswith(('.KS', '.KQ')) else 'USD'
    def usable(record):
        valid = record['frame']['Close'][record['frame']['Close'].map(positive_price)]
        return record['currency'] == expected and not valid.empty and 0 <= (end - valid.index[-1].date()).days <= 7
    def capture(record):
        nonlocal captured, pending
        pending = record.get('pending_splits', [])
        if usable(record):
            captured = record
            # Only current closes in the expected currency may stand in for the record at the parent's deadline.
            on_raw(record)
    if failure is None:
        try:
            record = load_prices(symbol, end, history_start=start, repair_since=repair_since, on_raw=capture)
            failure = price_status(record, end)
            if failure is None:
                return record
            capture(record)  # The repaired closes replace the raw ones, here and at the parent's deadline.
            if record.get('price_recovery', {}).get('reason') == 'PRICE_PROVIDER_RATE_LIMIT':
                failure = 'PRICE_PROVIDER_RATE_LIMIT'
        except Exception as error:
            failure = price_failure(error)
        if on_fallback:
            on_fallback(failure)
    from .data_providers import load_fallback_prices
    try:
        record = load_fallback_prices(symbol, end)
    except Exception:
        if failure == 'PRICE_PROVIDER_RATE_LIMIT':
            raise ValueError(failure)
        raise
    if captured:
        record = with_actual_prices(record, captured)
    elif start:
        record = {**record, 'frame': record['frame'].loc[record['frame'].index >= pd.Timestamp(start)]}  # Its series starts in 1970.
    return {**record, 'pending_splits': sorted(set(record.get('pending_splits', [])) | set(pending)),
            'primary_failure': failure, 'history_start': str(start) if start else None}


def serialize(record):
    frame = record['frame'].copy()
    frame.index.name = 'date'
    frame = frame.reset_index()
    frame['date'] = frame['date'].dt.strftime('%Y-%m-%d')
    return json.dumps({**record, 'frame': json.loads(frame.to_json(orient='records'))}, allow_nan=False)


if __name__ == '__main__':
    # A bounded parent reuses imports/cookies, and kills this process on a deadline.
    for line in sys.stdin:
        try:
            request = json.loads(line)
            record = load_preload_prices(request['symbol'], date.fromisoformat(request['end']),
                date.fromisoformat(request['start']) if request.get('start') else None,
                lambda raw: print(serialize({**raw, '_checkpoint': True}), flush=True),
                date.fromisoformat(request['repair_since']) if request.get('repair_since') else None, request.get('failure'),
                lambda failure: print(json.dumps({'_fallback': failure}), flush=True))
            print(serialize(record), flush=True)
        except Exception as error:
            print(json.dumps({'error': price_failure(error)}), flush=True)
