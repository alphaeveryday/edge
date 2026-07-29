"""CLI composition root: 인자 파싱 → 어댑터 조립 → 파이프라인 실행."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from .adapters.classification import (
    connect,
    latest_industry_csv,
    load_classification,
    read_industry_csv,
    source_stamp,
)
from .adapters.eventstore import EventStore
from .adapters.lake import LakeReader, make_s3_client
from .adapters.llm import DeepSeekClient
from .config import PipelineError, _load_pg, load_settings, parse_trade_date
from .observability import log
from .pipeline import run


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """CLI 인자(--trade-date·--request-id)와 서브커맨드를 파싱한다."""
    parser = argparse.ArgumentParser(
        prog="python -m edge_analysis",
        description="Explain the target ETF's daily move.",
    )
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD (Asia/Seoul); default today")
    parser.add_argument("--request-id", default=None, help="caller correlation id")
    # 서브커맨드를 주지 않으면 종전처럼 설명 파이프라인이 돈다 - Step Functions 의 기동
    # 커맨드를 바꾸지 않기 위해서다.
    sub = parser.add_subparsers(dest="command")
    loader = sub.add_parser(
        "load-classification",
        help="Load the FMP industry map into instrument_classification.",
    )
    loader.add_argument("--path", required=True,
                        help="industry map CSV, or a directory to pick the latest from")
    loader.add_argument("--as-of-date", default=None,
                        help="YYYY-MM-DD classification as-of; default today (Asia/Seoul)")
    loader.add_argument("--source", default="FMP", help="origin tag stored on every row")
    loader.add_argument("--available-at", default=None,
                        help="ISO timestamp; default the CSV filename stamp")
    return parser.parse_args(list(argv) if argv is not None else None)


def load_classification_command(args: argparse.Namespace) -> int:
    """산업분류 원장 적재: CSV 읽기 → 티커 해소 → UPSERT → 건수 로그.

    ``load_settings`` 를 거치지 않는다 - 그건 DEEPSEEK_API_KEY 를 요구하고, 원장 적재는
    LLM 키가 없는 운영 환경에서도 돌아야 한다. 필요한 설정은 Postgres 접속뿐이다.
    """
    path = Path(args.path)
    if path.is_dir():
        path = latest_industry_csv(path)
    rows = read_industry_csv(path)
    stamp = source_stamp(path)
    as_of = parse_trade_date(args.as_of_date)
    available_at = args.available_at or (stamp or datetime.now(timezone.utc)).isoformat()

    conn = connect(_load_pg())
    try:
        counts = load_classification(
            conn, rows,
            as_of_date=as_of,
            source=args.source,
            data_version=path.stem,
            available_at=available_at,
        )
        conn.commit()  # 한 스냅샷 = 한 트랜잭션. 어댑터는 커밋하지 않는다.
    finally:
        conn.close()

    log("classification.loaded", path=str(path), rows=len(rows),
        as_of_date=as_of.isoformat(), available_at=available_at, **counts)
    if rows and not (counts["loaded"] + counts["updated"]):
        # 한 건도 붙지 않은 적재는 성공이 아니다. 0 으로 끝내면 원장이 빈 채로 넘어가고
        # 그 뒤 인과 셀이 전부 UNCERTAIN 으로 떨어진 이유를 여기서 찾지 못한다.
        log("error", message=f"{path}: no rows loaded ({counts['unresolved']} unresolved)")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """설정 로드·어댑터 조립·실행. 실패(PipelineError)는 로그 + 비0 종료."""
    args = parse_args(argv)
    try:
        if args.command == "load-classification":
            return load_classification_command(args)
        settings = load_settings(trade_date=args.trade_date, request_id=args.request_id)
        s3 = make_s3_client(settings)
        lake = LakeReader(s3, settings.lake_bucket)
        client = DeepSeekClient(settings.deepseek_api_key, settings.deepseek_model)
        store = EventStore.connect(settings)
        try:
            return run(settings, lake=lake, store=store, client=client, s3=s3)
        finally:
            store.close()
    except PipelineError as exc:
        log("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
