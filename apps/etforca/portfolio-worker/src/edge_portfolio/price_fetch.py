"""Bounded subprocess entry point for Yahoo's synchronous client."""
import json
import sys
from datetime import date
from pathlib import Path

from .market_data import load_prices, positive_price, price_failure, price_status, with_actual_prices


def load_preload_prices(symbol, end, start, on_raw):
    captured = None
    pending = []
    def capture(record):
        nonlocal captured, pending
        pending = record.get('pending_splits', [])
        valid = record['frame']['Close'][record['frame']['Close'].map(positive_price)]
        expected = 'KRW' if symbol == 'KRW=X' or symbol.endswith('.KS') else 'USD'
        if record['currency'] == expected and not valid.empty and 0 <= (end - valid.index[-1].date()).days <= 7:
            captured = record
        on_raw(record)
    try:
        record = load_prices(symbol, end, history_start=start, on_raw=capture)
        failure = price_status(record, end)
        if failure is None:
            return record
        if record.get('price_recovery', {}).get('reason') == 'PRICE_PROVIDER_RATE_LIMIT':
            failure = 'PRICE_PROVIDER_RATE_LIMIT'
    except Exception as error:
        failure = price_failure(error)
    from .data_providers import load_fallback_prices
    try:
        record = load_fallback_prices(symbol, end)
    except Exception:
        if failure == 'PRICE_PROVIDER_RATE_LIMIT':
            raise ValueError(failure)
        raise
    if captured:
        record = with_actual_prices(record, captured)
    return {**record, 'pending_splits': sorted(set(record.get('pending_splits', [])) | set(pending)),
            'primary_failure': failure, 'history_complete': False}


def serialize(record):
    frame = record['frame'].copy()
    frame.index.name = 'date'
    frame = frame.reset_index()
    frame['date'] = frame['date'].dt.strftime('%Y-%m-%d')
    return json.dumps({**record, 'frame': json.loads(frame.to_json(orient='records'))}, allow_nan=False)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--serve':
        # A bounded parent reuses imports/cookies, and kills this process on a deadline.
        for line in sys.stdin:
            try:
                request = json.loads(line)
                record = load_preload_prices(request['symbol'], date.fromisoformat(request['end']),
                    date.fromisoformat(request['start']) if request.get('start') else None,
                    lambda raw: print(serialize({**raw, '_checkpoint': True}), flush=True))
                print(serialize(record), flush=True)
            except Exception as error:
                print(json.dumps({'error': price_failure(error)}), flush=True)
        raise SystemExit(0)
    def checkpoint(record):
        if len(sys.argv) > 3:
            target = Path(sys.argv[3])
            temporary = target.with_suffix('.tmp')
            temporary.write_text(serialize(record), encoding='utf-8')
            temporary.replace(target)
    try:
        if len(sys.argv) > 4 and sys.argv[4] == 'fallback':
            from .data_providers import load_fallback_prices
            record = load_fallback_prices(sys.argv[1], date.fromisoformat(sys.argv[2]))
        else:
            record = load_prices(sys.argv[1], date.fromisoformat(sys.argv[2]), on_raw=checkpoint,
                                 repair_since=date.fromisoformat(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != '-' else None,
                                 history_start=date.fromisoformat(sys.argv[6]) if len(sys.argv) > 6 else None)
    except Exception as error:
        print(json.dumps({'error': price_failure(error)}))
        raise SystemExit(1)
    print(serialize(record))
