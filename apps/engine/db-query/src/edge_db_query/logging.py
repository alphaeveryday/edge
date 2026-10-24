"""Emit operational query audit events to CloudWatch."""
import json
from datetime import datetime, timezone


def log(event: str, **fields: object) -> None:
    """Write a timestamped JSON event to standard output."""
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
