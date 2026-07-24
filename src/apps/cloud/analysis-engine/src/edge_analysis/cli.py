"""CLI composition root: 인자 파싱 → 어댑터 조립 → 파이프라인 실행."""
from __future__ import annotations

import argparse
from collections.abc import Sequence

from .adapters.eventstore import EventStore
from .adapters.lake import LakeReader, make_s3_client
from .adapters.llm import DeepSeekClient
from .config import PipelineError, load_settings
from .observability import log
from .pipeline import run


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """CLI 인자(--trade-date·--request-id)를 파싱한다."""
    parser = argparse.ArgumentParser(
        prog="python -m edge_analysis",
        description="Explain the target ETF's daily move.",
    )
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD (Asia/Seoul); default today")
    parser.add_argument("--request-id", default=None, help="caller correlation id")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: list[str] | None = None) -> int:
    """설정 로드·어댑터 조립·실행. 실패(PipelineError)는 로그 + 비0 종료."""
    args = parse_args(argv)
    try:
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
