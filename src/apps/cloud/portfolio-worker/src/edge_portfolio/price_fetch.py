"""Bounded subprocess entry point for Yahoo's synchronous client."""
import json
import sys
from datetime import date

from .market_data import load_prices


if __name__ == '__main__':
    record = load_prices(sys.argv[1], date.fromisoformat(sys.argv[2]))
    frame = record['frame'].copy()
    frame.index.name = 'date'
    frame = frame.reset_index()
    frame['date'] = frame['date'].dt.strftime('%Y-%m-%d')
    record['frame'] = json.loads(frame.to_json(orient='records'))
    print(json.dumps(record, allow_nan=False))
