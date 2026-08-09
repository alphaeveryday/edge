"""CLI composition root: 인자 파싱 → 어댑터 조립 → 파이프라인 실행."""
from __future__ import annotations

import argparse
import logging
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
from .adapters.eventstore import EventStore, minute_route_id
from .adapters.lake import LakeReader, make_s3_client
from .adapters.llm import DeepSeekClient
from .adapters.price_daily import (
    COMMIT_BATCH_FILES,
    load_price_daily,
    read_daily_bars,
    source_version,
)
from .adapters.readonly import connect_readonly, emit, run_query
from .adapters.universe import load_universe
from .config import PipelineError, _env, _load_pg, load_settings, parse_trade_date
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
    parser.add_argument("--trigger-id", default=None,
                        help="분봉 트리거 단건 입력(ALPHA-709) — 대상 ETF·trade_date 를 "
                             "트리거 행에서 유도한다(--trade-date 무시). 설명 실행의 "
                             "필수 입력이다(ALPHA-806)")
    # 서브커맨드를 주지 않으면 설명 파이프라인이 돈다. `required=True` 로 못 박지 않는
    # 이유는 이 인자가 상위 파서에 있어 적재 서브커맨드까지 함께 막기 때문이다 —
    # 계약은 `pipeline.run` 한 곳이 진다(트리거 없으면 PipelineError).
    sub = parser.add_subparsers(dest="command")
    consume = sub.add_parser(
        "consume-triggers",
        help="분봉 트리거 큐(price-explanation-realtime) 상주 소비(ALPHA-719).",
    )
    consume.add_argument("--queue-url", default=None,
                         help="SQS 큐 URL(미지정=EDGE_EXPLANATION_QUEUE_URL env)")
    consume.add_argument("--max-polls", type=int, default=None,
                         help="receive 횟수 상한(미지정=상주). 로컬·검증용 — 봉투 계약 "
                              "위반이 있었으면 exit 1")
    window = sub.add_parser(
        "run-window-universe",
        help="기존 분봉 계보가 있는 당일 ETF를 요청창으로 Cloud-only 재분석.",
    )
    window.add_argument("--window-start", required=True, help="HH:MM")
    window.add_argument("--window-end", required=True, help="HH:MM")
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

    # 전 종목 원장. 분류·일봉보다 먼저 돌아야 한다 - 둘 다 instrument_id 에 FK 가 걸려 있어
    # 원장이 없으면 전 종목이 미해소로 떨어진다.
    universe = sub.add_parser(
        "load-universe",
        help="Create entity/instrument rows for every ticker in the industry map.",
    )
    universe.add_argument("--path", required=True,
                          help="industry map CSV, or a directory to pick the latest from")
    universe.add_argument("--source", default="FMP", help="origin tag recorded in the log")
    universe.add_argument("--market-code", default="XKRX",
                          help="MIC of the listing venue; XKRX matches the entity seed")

    # 일봉 가격 원장. load-universe 뒤에 돌아야 한다 - instrument_id 해소가 전제다.
    prices = sub.add_parser(
        "load-price-daily",
        help="Aggregate 5-minute parquet bars into price_daily rows.",
    )
    prices.add_argument("--path", required=True,
                        help="5-minute parquet file, or a directory of *.parquet")
    prices.add_argument("--source", default="FMP", help="origin tag recorded in the log")
    prices.add_argument("--before", default=None,
                        help="YYYY-MM-DD exclusive upper bound; skip bars on or after it")

    # 읽기전용 원장 질의. 파이프라인과 같은 이미지에 얹는다 - 질의 전용 이미지를 따로
    # 두면 스키마를 아는 코드가 두 곳으로 갈라진다.
    querier = sub.add_parser("query", help="Run one read-only SQL query against the ledger.")
    source = querier.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql", help="a single SELECT/WITH statement")
    source.add_argument("--file", help="path to a file holding one statement")

    # 발화 스냅샷 대시보드 수동 재생성(ALPHA-894). 런마다 자동 재생성되지만, 배선
    # 이전 데이터·재생성 실패 뒤 복구는 이 경로로 멱등하게 다시 만든다.
    reporter = sub.add_parser(
        "report", help="발화 스냅샷 대시보드(단일 HTML)를 전량 재조회로 재생성.")
    reporter.add_argument("--out-dir", default=None,
                          help="로컬 출력 디렉터리(미지정=S3 reports/dashboard.html 덮어쓰기)")
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


def load_universe_command(args: argparse.Namespace) -> int:
    """전 종목 원장 적재: CSV 읽기 → 티커 정규화 → entity·instrument UPSERT → 건수 로그.

    ``load_settings`` 를 거치지 않는다 - 원장 적재는 LLM 키가 없는 운영 환경에서도 돌아야
    한다(``load_classification_command`` 과 같은 이유).

    분류와 **같은 CSV 를 같은 파서로** 읽는다. 원천이 갈라지면 원장에 있는 종목과 분류가
    붙는 종목이 달라지고, 그 차이는 미해소 건수로만 드러난다.
    """
    path = Path(args.path)
    if path.is_dir():
        path = latest_industry_csv(path)
    rows = read_industry_csv(path)
    log("universe.read", path=str(path), rows=len(rows), market_code=args.market_code)

    conn = connect(_load_pg())
    try:
        counts = load_universe(
            conn, rows,
            source=args.source,
            data_version=path.stem,
            market_code=args.market_code,
        )
        conn.commit()  # 한 스냅샷 = 한 트랜잭션. 어댑터는 커밋하지 않는다.
    finally:
        conn.close()

    log("universe.loaded", path=str(path), rows=len(rows), source=args.source,
        market_code=args.market_code, data_version=path.stem, **counts)
    if rows and not (counts["loaded"] + counts["updated"]):
        # 한 건도 붙지 않은 적재는 성공이 아니다. 0 으로 끝내면 일봉도 분류도 붙을 자리가
        # 없는 채로 다음 단계가 돌고, 인과 셀이 전부 UNCERTAIN 이 된 이유를 여기서 못 찾는다.
        log("error", message=f"{path}: no instruments loaded ({counts['unresolved']} rejected)")
        return 1
    return 0


def load_price_daily_command(args: argparse.Namespace) -> int:
    """일봉 원장 적재: 5분봉 parquet → 일봉 집산 → 티커 해소 → UPSERT → 건수 로그.

    ``load_settings`` 를 거치지 않는다 - 원장 적재는 LLM 키가 없는 운영 환경에서도 돌아야
    한다. 필요한 설정은 Postgres 접속뿐이다.

    종목당 커밋하지 않는다 - 파일이 1272개라 fsync 가 그 수만큼 늘어난다. 대신
    ``COMMIT_BATCH_FILES`` 개마다 한 트랜잭션으로 밀어 커밋한다. 가격은 분류 스냅샷과 달라
    한 파일이 곧 한 종목의 독립된 시계열이라, 배치 경계가 원장의 시점을 섞지 않는다.
    """
    path = Path(args.path)
    paths = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
    if not paths:
        raise PipelineError(f"{path}: no parquet files")
    # 스냅샷 식별자는 배치 전체가 하나여야 한다 - 파일마다 다르면 재적재 대조가 불가능하다.
    data_version = source_version(max(paths, key=lambda item: item.stat().st_mtime))
    before = parse_trade_date(args.before) if args.before else None

    totals = {"loaded": 0, "updated": 0, "unresolved": 0, "duplicate": 0}
    skipped: list[str] = []
    conn = connect(_load_pg())
    try:
        batch: list = []
        for done, source_path in enumerate(paths, start=1):
            try:
                bars = read_daily_bars(source_path)
                # 상한이 있으면 **집산 뒤에** 자른다. 수익률은 직전 거래일 종가로 계산되므로
                # 원봉을 먼저 자르면 경계일의 수익률이 사라진다. 이 상한이 필요한 이유는
                # 정식 일봉 파이프라인이 이미 채운 최근 구간을 5분봉 파생값으로 덮지 않기
                # 위해서다 - 두 원천의 종가는 완전히 일치하지 않는다.
                batch.extend(b for b in bars if before is None or b.trade_date < before)
            except PipelineError as exc:
                # 심볼이 티커가 아닌 파일(지수 프록시 등) 하나가 1272개 적재를 죽이면 안 된다.
                skipped.append(source_path.name)
                log("price_daily.skipped", path=source_path.name, reason=str(exc))
            if done % COMMIT_BATCH_FILES and done != len(paths):
                continue
            counts = load_price_daily(
                conn, batch, source=args.source, data_version=data_version,
            )
            conn.commit()  # 어댑터는 커밋하지 않는다 - 배치 경계는 호출자가 정한다.
            for key, value in counts.items():
                totals[key] += value
            log("price_daily.progress", files=done, of=len(paths), rows=len(batch), **counts)
            batch = []
    finally:
        conn.close()

    log("price_daily.loaded", path=str(path), files=len(paths), skipped=len(skipped),
        data_version=data_version, **totals)
    if not (totals["loaded"] + totals["updated"]):
        # 한 건도 붙지 않은 적재는 성공이 아니다. 0 으로 끝내면 원장이 빈 채로 넘어가고
        # 그 뒤 인과 셀이 전부 UNCERTAIN 으로 떨어진 이유를 여기서 찾지 못한다.
        log("error", message=f"{path}: no rows loaded ({totals['unresolved']} unresolved)")
        return 1
    return 0


def report_command(args: argparse.Namespace) -> int:
    """대시보드 재생성 1건: 읽기전용 접속 → 전량 조회 → 렌더 → 덮어쓰기.

    ``load_settings`` 를 거치지 않는다 — 대시보드 재생성에 LLM 키가 필요하지 않다.
    필요한 것은 Postgres 읽기와 S3 뿐이다(`query_command` 와 같은 결).
    """
    import os
    from types import SimpleNamespace

    from .adapters.archive import _DEFAULT_PREFIX
    from .adapters.lake import make_s3_client
    from .report import regenerate_dashboard

    prefix = os.environ.get("ALPHAMALE_RESULT_S3_PREFIX", "").strip() or (
        f"s3://{_env('ALPHAMALE_LAKE_BUCKET', 'edge-dev-pipeline-lake')}/"
        f"{_DEFAULT_PREFIX}")
    s3 = make_s3_client(SimpleNamespace(
        aws_profile=_env("AWS_PROFILE"),
        region=_env("AWS_REGION", "ap-northeast-2")))
    conn = connect_readonly(_load_pg())
    try:
        location = regenerate_dashboard(
            conn=conn, s3=s3, result_prefix=prefix, out_dir=args.out_dir)
    finally:
        conn.close()
    log("report.done", location=location)
    return 0


def query_command(args: argparse.Namespace) -> int:
    """읽기전용 질의 1건: 가드 → 상한 실행 → JSON Lines 출력.

    ``load_settings`` 를 거치지 않는다 - 원장을 들여다보는 일에 LLM 키가 필요하지 않다.
    """
    sql = Path(args.file).read_text(encoding="utf-8") if args.file else args.sql
    conn = connect_readonly(_load_pg())
    try:
        columns, rows = run_query(conn, sql)
    finally:
        conn.close()
    emit(columns, rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    """설정 로드·어댑터 조립·실행. 실패(PipelineError)는 로그 + 비0 종료."""
    # 루트 로거 기본은 WARNING 이라, 설정이 없으면 `logger.info` 가 통째로 삼켜진다 —
    # 상주 소비자가 ReturnsNotReady **사유**를 info 로 찍으므로 실패 709건이 로그에
    # `start` 만 남긴 실측이 있다(08-05, ALPHA-747). 진단 가능성이 관측의 최소 조건이다.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True)
    args = parse_args(argv)
    try:
        if args.command == "consume-triggers":
            import os

            from .consumer import consume_triggers
            queue_url = args.queue_url or os.environ.get("EDGE_EXPLANATION_QUEUE_URL", "").strip()
            if not queue_url:
                # 조용히 기본 큐를 추측하면 오배선이 빈 폴링으로 위장된다(fail loud)
                raise PipelineError(
                    "큐 URL 이 없다 — --queue-url 또는 EDGE_EXPLANATION_QUEUE_URL 주입"
                )
            return consume_triggers(queue_url, max_polls=args.max_polls)
        if args.command == "run-window-universe":
            from .window_batch import run_window_universe
            return run_window_universe(
                args.trade_date, args.window_start, args.window_end, args.request_id)
        if args.command == "load-classification":
            return load_classification_command(args)
        if args.command == "load-universe":
            return load_universe_command(args)
        if args.command == "load-price-daily":
            return load_price_daily_command(args)
        if args.command == "query":
            return query_command(args)
        if args.command == "report":
            return report_command(args)
        settings = load_settings(trade_date=args.trade_date, request_id=args.request_id,
                                 trigger_id=args.trigger_id)
        s3 = make_s3_client(settings)
        lake = LakeReader(s3, settings.lake_bucket)
        client = DeepSeekClient(settings.deepseek_api_key, settings.deepseek_model)
        store = EventStore.connect(settings)
        try:
            if args.trigger_id and not store.try_lock_route(minute_route_id(args.trigger_id)):
                # 소비자가 같은 트리거를 쥐고 있다 — 수동 실행이 그 위에 겹치면 LLM 이
                # 이중 과금된다(ALPHA-779). 소비자 쪽만 락을 잡으면 이 문서화된 경로가
                # 그대로 뚫려 있다. 재실행 자체를 막지는 않는다: 경합이 없으면 잡힌다.
                raise PipelineError(
                    f"소비자가 처리 중인 트리거다 — 끝난 뒤 재실행하라: {args.trigger_id}")
            return run(settings, lake=lake, store=store, client=client, s3=s3)
        finally:
            store.close()
    except PipelineError as exc:
        log("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
