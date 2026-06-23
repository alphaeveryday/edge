#!/usr/bin/env python3
"""CLI for the EDGE event-driven return model.

    python scripts/run_event_return_model.py --start 2024-07-01 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

MODROOT = Path(__file__).resolve().parent  # analysis_module
if str(MODROOT) not in sys.path:
    sys.path.insert(0, str(MODROOT))

from edge_event_model import config  # noqa: E402
from edge_event_model.pipeline import run  # noqa: E402


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EDGE event-driven return model")
    parser.add_argument("--start", type=_parse_date, default=config.ANALYSIS_START)
    parser.add_argument("--end", type=_parse_date, default=config.ANALYSIS_END)
    parser.add_argument("--tickers", nargs="+", default=list(config.TICKERS))
    parser.add_argument("--db-path", default=str(config.DB_PATH))
    parser.add_argument("--min-obs", type=int, default=config.MIN_OBS)
    parser.add_argument("--news-limit", type=int, default=None)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--save-dir", default=None, help="write trained model artifacts here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(
        start=args.start,
        end=args.end,
        tickers=args.tickers,
        db_path=args.db_path,
        min_obs=args.min_obs,
        persist=not args.no_persist,
        news_limit=args.news_limit,
        save_dir=args.save_dir,
    )
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
