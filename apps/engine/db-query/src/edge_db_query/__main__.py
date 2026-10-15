"""Run a single operational database query."""
import argparse
from pathlib import Path

from .config import PipelineError, _load_pg
from .logging import log
from .readonly import connect_readonly, emit, run_query


def main() -> int:
    """Execute SQL from one argument or UTF-8 file and print JSON Lines."""
    parser = argparse.ArgumentParser(description="Read-only PostgreSQL query")
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--sql")
    query.add_argument("--file")
    args = parser.parse_args()
    sql = Path(args.file).read_text(encoding="utf-8") if args.file else args.sql
    try:
        conn = connect_readonly(_load_pg())
        try:
            columns, rows = run_query(conn, sql)
        finally:
            conn.close()
    except PipelineError as exc:
        log("error", message=str(exc))
        return 1
    emit(columns, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
