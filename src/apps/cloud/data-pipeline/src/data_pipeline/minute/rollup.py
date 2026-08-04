"""1분 canonical → 5분봉(intraday_5m) parquet 파생 (ALPHA-750).

분석엔진(analysis-engine)이 설명을 5분봉으로 계산하는데 엔진 쪽 파생이 당장 불가라,
수집 워커가 커밋 후크에서 1분 canonical artifact 를 5분봉으로 롤업해 레이크에 공급한다.
판정기(트리거)는 1분 그대로다 — 이 산출물은 분석 전용 표면이다. 파일 계약(경로·컬럼·
타입·ts 시맨틱)은 lake/storage.py 의 canonical_intraday_5m_key docstring 이 정본이다.

# ponytail: 소재(수집 워커 커밋 후크)는 "당장" 임시 확정 — 정식 자리는 파생 스텝이다.

입력은 메모리 버퍼가 아니라 **원장이 확정한 세대의 canonical 1분 artifact(NDJSON)** 다
— 재시작 안전·멱등·결정적(같은 커밋 세대 집합이면 같은 산출)이 이유고, S3 의 더 높은
세대를 집지 않는 이유이기도 하다: PUT 후 DB commit 전에 죽은 orphan artifact 를 최신
이라고 읽으면 원장에 확정되지 않은 가격이 파생에 실린다(find_orphan_artifacts 와 같은
"커밋 세대가 정본" 축).

롤업 규칙: open=구간 첫 1분봉 open · high=max · low=min · close=마지막 close ·
volume=합. 정렬은 **window 축**이다 — 도착 순서도, record 의 ts 도 아니다(stale 봉의
ts 는 직전 분이라 ts 로 정렬하면 open/close 가 뒤바뀐다). 결손 분은 있는 봉만으로
집계하고 로그로 남긴다(산출 스키마는 기존 fmp 파일과 동일해야 해서 컬럼로는 안 싣는다).

쓰기는 **그날 전체 재집계 → part-0.parquet 통째 overwrite** 다 — 하루 5분봉은
≤ 362종×78봉 ≈ 2.8만 행이라 통재작성이 싸고, 부분 파일 병합·순서 문제가 아예 없다.
# ponytail: 매 버킷 마감마다 그날 1분 artifact 전부를 다시 읽는다(장 후반 ~390 GET)
# — 인프로세스 증분 캐시는 이 비용이 실측으로 문제가 될 때 붙인다.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from ..lake.storage import (
    Storage,
    canonical_intraday_5m_key,
    canonical_price_minute_artifact_key,
)
from .models import KST, Universe, plan_session_windows

logger = logging.getLogger(__name__)

BUCKET_MINUTES = 5

# 소비자의 안정 필터 축 — 벤더명을 넣지 않는다. 분봉 레인의 벤더는 설정 축이라
# (MinutePriceWorkerConfig.source: kis|toss) 교체 중에도 이 값이 흔들리면 특정
# 값을 필터하는 소비자가 그날 데이터를 통째로 놓친다. 벤더 축의 정본은 1분
# canonical 의 record 컬럼(ALPHA-705)이고, 여기 값은 "fmp 원본이 아니라 1분
# 롤업 파생"임만 드러낸다.
SOURCE_VENDOR = "1m_rollup"

# 이 날짜부터의 trade_date 파티션만 쓴다 — 그 전은 fmp 백필(~2026-07-31)의
# 정본 파티션이라, 과거 --session-date 재실행이 part-0.parquet 를 덮으면 벤더
# 원본이 유실된다. 비겹침은 주석이 아니라 코드로 강제한다.
WRITER_SINCE = "2026-08-04"


def _committed_generations(ledger, session_id: str) -> dict[str, int]:
    """window(HHMM, KST 축) → 원장이 확정한 현재 세대. commit.py orphan 스캔과 같은 축."""
    with ledger.connect_fn(ledger.db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT window_start, generation FROM minute_ingestion_window "
            "WHERE session_id = %s AND checksum IS NOT NULL",
            (session_id,),
        )
        return {
            window_start.astimezone(KST).strftime("%H%M"): generation
            for window_start, generation in cur.fetchall()
        }


def _bucket_of(start: datetime) -> datetime:
    local = start.astimezone(KST)
    return local.replace(
        minute=local.minute - local.minute % BUCKET_MINUTES, second=0, microsecond=0
    )


def maybe_rollup(
    storage: Storage,
    ledger,
    *,
    session_id: str,
    market: str,
    session_date: str,
    universe: Universe,
    window_start: datetime,
) -> str | None:
    """방금 커밋된 1분 window 의 버킷이 닫혔으면 그날 5분봉 parquet 를 재작성한다.

    발화 조건 — 다음 둘 중 하나(둘 다 아니면 None — 버킷 미완):
    ① 커밋된 window 가 버킷의 마지막 계획 분이다(분%5==4, 또는 세션 마지막 window —
       잔여 버킷은 plan_session_windows 실물이 정한다).
    ② 버킷 마지막 분이 **이미 커밋돼 있다** — recovery lane 은 최고령부터 재청구하므로
       backlog 소진 중엔 버킷 마지막 분이 앞 분보다 먼저 커밋될 수 있다. 이때 늦게
       도착한 앞 분의 커밋이 그날 파일을 재작성해 결손을 채운다(안 하면 backlog 구간
       의 5분봉이 조용히 부분본으로 남는다). 정정(세대 증가) 재커밋도 같은 경로로
       파생에 반영된다.

    산출에는 **닫힌 버킷만** 싣는다 — 커밋 지평(가장 최신 커밋 window)이 버킷의
    마지막 계획 분을 지나야 닫힌 것이다. 안 거르면 recovery 재작성이 다른 버킷의
    부분 관측(예: 09:10 만 커밋된 09:10~09:14)을 완성 봉처럼 노출한다.

    반환은 PUT 한 키. 같은 커밋 세대 집합이면 같은 바이트라 재PUT 은 멱등이다.
    """
    if session_date < WRITER_SINCE:  # ISO 문자열이라 사전순 = 시간순
        logger.warning(
            "5분 롤업 %s: fmp 백필 정본 파티션(< %s) — 덮어쓰지 않는다",
            session_date, WRITER_SINCE,
        )
        return None
    planned = plan_session_windows(
        datetime.strptime(session_date, "%Y-%m-%d").date(), universe=universe
    )
    bucket = _bucket_of(window_start)
    members = [
        start
        for start, _ in planned
        if bucket <= start < bucket + timedelta(minutes=BUCKET_MINUTES)
    ]
    if not members:
        # Worker 는 _session_ready 로 planner 와 같은 universe 를 보증한다 — 여기
        # 도달하면 계획·커밋 축이 갈린 것이라 조용히 넘기지 않는다(Rule 12).
        raise ValueError(f"window {window_start.isoformat()} 는 세션 계획 밖이다")
    committed = _committed_generations(ledger, session_id)
    if window_start != members[-1] and members[-1].strftime("%H%M") not in committed:
        return None  # 버킷 미완 — 마지막 분이 아직 관측 전이다

    # ── 그날 전체 재집계: **닫힌 버킷**의 커밋된 window 만, window 오름차순 ──
    # (bucket, symbol) → {open,high,low,close,volume}
    aggregates: dict[tuple[datetime, str], dict] = {}
    gaps: list[str] = []
    horizon = max(
        start for start, _ in planned if start.strftime("%H%M") in committed
    )  # 비지 않는다 — 방금 커밋된 window 가 committed 에 있다
    bucket_last: dict[datetime, datetime] = {}  # bucket → 그 버킷의 마지막 계획 분
    for start, _ in planned:  # planned 는 오름차순이라 마지막 대입이 이긴다
        bucket_last[_bucket_of(start)] = start
    for start, _ in planned:
        if bucket_last[_bucket_of(start)] > horizon:
            continue  # 아직 안 닫힌 버킷 — 부분 관측을 완성 봉처럼 노출하지 않는다
        hhmm = start.strftime("%H%M")
        generation = committed.get(hhmm)
        if generation is None:
            gaps.append(hhmm)  # 닫힌 버킷의 구멍 — 결손 분(재청구 대상)
            continue
        artifact = storage.get_bytes(
            canonical_price_minute_artifact_key(market, session_date, hhmm, generation)
        )
        for line in artifact.decode("utf-8").splitlines():
            record = json.loads(line)
            key = (_bucket_of(start), record["unit_id"])
            # 실 record 는 정밀도 때문에 값을 문자열로 싣는다(price_collect.record_of)
            # — Decimal 경유로 fake 의 int 와 같은 축으로 접는다.
            high, low, close = (
                Decimal(str(record[field])) for field in ("high", "low", "close")
            )
            volume = Decimal(str(record["volume"]))
            aggregate = aggregates.get(key)
            if aggregate is None:
                aggregates[key] = {
                    "open": Decimal(str(record["open"])),
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            else:
                aggregate["high"] = max(aggregate["high"], high)
                aggregate["low"] = min(aggregate["low"], low)
                aggregate["close"] = close  # window 축 마지막 봉이 이긴다
                aggregate["volume"] += volume
    if gaps:
        logger.warning(
            "5분 롤업 %s: 커밋 없는 결손 분 %d개 %s — 있는 봉만 집계(도착 시 재작성)",
            session_date, len(gaps), gaps[:10],
        )
    if not aggregates:
        # 크게 남기되 **파일은 그래도 쓴다**(빈 스키마) — 여기서 반환하면 정정이
        # 그날 봉을 전부 지웠을 때 직전 산출본이 남아 폐기된 가격을 계속 서빙한다.
        logger.error("5분 롤업 %s: 집계할 1분봉 0건 — 빈 파일로 재작성", session_date)

    import pyarrow as pa  # 지연 import — pyproject 의 parquet 규약과 동일
    import pyarrow.parquet as pq

    # 결정적 행 순서 — 같은 커밋 세대 집합이면 같은 바이트
    ordered = sorted(aggregates, key=lambda key: (key[1], key[0]))
    volumes = [aggregates[key]["volume"] for key in ordered]
    if any(volume != int(volume) for volume in volumes):
        # 소수 volume 은 우리가 아는 형상이 아니다 — int64 절삭으로 조용히 접지 않는다
        raise ValueError(f"소수 volume 관측 — session_date={session_date}")
    # ts 는 naive KST(구간 시작), available_at = ts + 5분 — 기존 fmp 파일 실측과 동형
    ts_values = [bucket_start.replace(tzinfo=None) for bucket_start, _ in ordered]
    table = pa.table(
        {
            "ticker": pa.array([symbol for _, symbol in ordered], pa.string()),
            # 1분 롤업의 벤더 심볼은 bare 단축코드 그대로다(fmp 의 .KS 접미는 벤더 표기)
            "source_symbol": pa.array([symbol for _, symbol in ordered], pa.string()),
            "ts": pa.array(ts_values, pa.timestamp("us")),
            "open": pa.array([float(aggregates[k]["open"]) for k in ordered], pa.float64()),
            "high": pa.array([float(aggregates[k]["high"]) for k in ordered], pa.float64()),
            "low": pa.array([float(aggregates[k]["low"]) for k in ordered], pa.float64()),
            "close": pa.array([float(aggregates[k]["close"]) for k in ordered], pa.float64()),
            "volume": pa.array([int(v) for v in volumes], pa.int64()),
            "source_vendor": pa.array([SOURCE_VENDOR] * len(ordered), pa.string()),
            "available_at": pa.array(
                [ts + timedelta(minutes=BUCKET_MINUTES) for ts in ts_values],
                pa.timestamp("us"),
            ),
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    key = canonical_intraday_5m_key(market, session_date)
    storage.put_bytes(key, sink.getvalue())
    return key
