"""Bounded subprocess entry point for Yahoo's synchronous client."""
import json
import sys
from datetime import date
from pathlib import Path

from .market_data import load_prices, price_failure


def serialize(record):
    frame = record['frame'].copy()
    frame.index.name = 'date'
    frame = frame.reset_index()
    frame['date'] = frame['date'].dt.strftime('%Y-%m-%d')
    return json.dumps({**record, 'frame': json.loads(frame.to_json(orient='records'))}, allow_nan=False)


if __name__ == '__main__':
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
            record = load_prices(sys.argv[1], date.fromisoformat(sys.argv[2]), on_raw=checkpoint)
    except Exception as error:
        print(json.dumps({'error': price_failure(error)}))
        raise SystemExit(1)
    print(serialize(record))
