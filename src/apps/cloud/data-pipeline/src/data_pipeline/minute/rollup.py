"""1분 canonical → 5분봉(intraday_5m) parquet 파생 (ALPHA-750).

분석엔진(analysis-engine)이 설명을 5분봉으로 계산하는데 엔진 쪽 파생이 당장 불가라,
수집 워커가 커밋 후크에서 1분 canonical artifact 를 5분봉으로 롤업해 레이크에 공급한다.
판정기(트리거)는 1분 그대로다 — 이 산출물은 분석 전용 표면이다. 파일 계약(경로·컬럼·
타입·ts 시맨틱)은 lake/storage.py 의 canonical_intraday_5m_key docstring 이 정본이다.

진입점이 둘이고 **집계는 하나**다(`_rollup_day`):

- `maybe_rollup` — 수집 워커 커밋 후크. 장중 즉시성 담당. 버킷이 닫힐 때만 발화한다
- `rollup_session` — 거래일 마감 후 1회 확정(ALPHA-839). 후크가 **지나간 날을 영영 안
  채우기** 때문에 필요하다: 발화 조건이 "방금 커밋된 window" 라, 그날 마지막 버킷 뒤에
  도착한 정정이나 통째로 안 돈 거래일에는 다음 커밋이 없다

둘을 같은 내부 함수로 묶는 이유는 나중에 후크를 지울 때(엔진이 당일 5분봉을 1분으로
재구성하면 후크는 소비자를 잃는다) **배치가 딸려 나가지 않게** 하는 것이다. 후크를
지워도 5분 파생의 생산자는 남아야 한다 — β 패널이 과거 약 60거래일 5분봉 위에 서 있어서
하루라도 안 생기면 그만큼 썩는다.

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
≤ 362종×78봉 ≈ 2.8만 행이라 통재작성이 싸고, 우리 파일 안에서는 부분 병합·순서 문제가
없다. ⚠️ **파티션 안에서는 다르다**(2026-08-07 정정) — 같은 파티션에 다른 writer 가 다른
파일명으로 쓰고 소비자는 글롭으로 읽으므로, 나란히 놓이면 겹치는 봉이 두 번 세어진다.
`_rollup_day` 가 타 writer 파일을 발견하면 산출하지 않는 이유다.
# ponytail: 매 버킷 마감마다 그날 1분 artifact 전부를 다시 읽는다(장 후반 ~390 GET)
# — 인프로세스 증분 캐시는 이 비용이 실측으로 문제가 될 때 붙인다.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..lake.storage import (
    Storage,
    canonical_intraday_5m_key,
    canonical_intraday_5m_prefix,
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

    여기는 **발화 게이트만** 본다 — 집계·쓰기는 `_rollup_day` 다(EOD 배치와 공유).
    """
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
    return _rollup_day(
        storage, market=market, session_date=session_date,
        planned=planned, committed=committed,
    )


def rollup_session(
    storage: Storage,
    ledger,
    *,
    session_id: str,
    market: str,
    session_date: str,
) -> str | None:
    """거래일 마감 후 그날 5분봉을 한 번 확정한다 (ALPHA-839 EOD 진입점).

    후크와 달리 **버킷 게이트가 없다** — 세션 전체를 대상으로 부르는 자리라 "방금 닫힌
    버킷"이라는 개념이 없고, 닫힌 버킷만 싣는 규칙은 `_rollup_day` 의 커밋 지평이 그대로
    건다. 산출은 후크의 마지막 산출과 **같은 바이트여야 한다**(같은 커밋 세대 집합).

    ⚠️ 계획·커밋을 **원장에서 읽는다** — `plan_session_windows` 를 다시 부르지 않는다.
    그 함수의 창 범위는 universe JSON 이 정하는데(정규장 390 vs 시간외 720), 그 파일은
    수동 편집 대상이라 마감 후 시점의 파일이 세션이 실제로 돈 계획과 갈릴 수 있다.
    갈리면 배치가 없는 분을 결손으로 세거나 있는 분을 계획 밖으로 버린다. 원장은
    `plan_session` 이 그날 고정한 것이라 갈리지 않는다.
    """
    rows = ledger.session_window_rows(session_id=session_id)
    # 원장 시각은 tz-aware UTC 로 온다. `plan_session_windows` 산출은 KST 라
    # (`_bucket_of`·`strftime("%H%M")` 이 그 축을 전제한다) 여기서 축을 맞춘다 —
    # 안 맞추면 UTC 의 HHMM 으로 커밋을 찾아 **전건 결손**이 된다.
    planned = [
        (window_start.astimezone(KST), window_end.astimezone(KST))
        for window_start, window_end, *_ in rows
    ]
    # `_committed_generations` 와 같은 판정축(checksum 이 있으면 커밋된 것)이다 —
    # 두 곳이 갈리면 후크와 배치가 다른 세대 집합을 보고 다른 바이트를 낸다.
    committed = {
        window_start.astimezone(KST).strftime("%H%M"): generation
        for window_start, _, _, generation, checksum in rows
        if checksum is not None
    }
    return _rollup_day(
        storage, market=market, session_date=session_date,
        planned=planned, committed=committed,
    )


def _rollup_day(
    storage: Storage,
    *,
    market: str,
    session_date: str,
    planned: Sequence[tuple[datetime, datetime]],
    committed: dict[str, int],
) -> str | None:
    """그날 전체를 재집계해 파티션 파일을 통째로 덮어쓴다. 반환은 PUT 한 키(또는 None).

    후크와 EOD 배치가 공유하는 유일한 집계 경로다 — 규칙이 두 벌이 되면 장중 산출과
    마감 산출이 갈리고, 갈린 날은 어느 쪽이 맞는지 물어볼 곳이 없다.
    """
    if session_date < WRITER_SINCE:  # ISO 문자열이라 사전순 = 시간순
        logger.warning(
            "5분 롤업 %s: 다른 벤더의 정본 파티션(< %s) — 덮어쓰지 않는다",
            session_date, WRITER_SINCE,
        )
        return None
    day_key = canonical_intraday_5m_key(market, session_date)
    # ⚠️ 같은 파티션을 **다른 writer 가 다른 파일명으로** 쓸 수 있다(토스 백필의
    # `part-toss-backfill.parquet`, ALPHA-828). 소비자는 파티션을 `*.parquet` 글롭으로
    # 읽으므로 우리 `part-0` 을 나란히 놓으면 겹치는 (ticker, ts) 가 **두 번 세어진다**
    # (거래량 이중계상 → β 패널 오염). 백필은 "쓰는 시점의 part-0" 만 대조하므로 나중에
    # 우리가 쓰면 그 비겹침 보장이 깨진다. 덮어쓸 수도 지울 수도 없으니 사람이 정한다.
    foreign = [k for k in storage.list_keys(
        canonical_intraday_5m_prefix(market, session_date)) if k != day_key]
    if foreign:
        logger.error(
            "5분 롤업 %s: 파티션에 다른 writer 의 파일이 있다 %s — 나란히 쓰면 행이 두 번 "
            "세어진다. 산출하지 않는다(소유자를 정한 뒤 재실행)", session_date, foreign,
        )
        return None

    # ── 그날 전체 재집계: **닫힌 버킷**의 커밋된 window 만, window 오름차순 ──
    # (bucket, symbol) → {open,high,low,close,volume}
    aggregates: dict[tuple[datetime, str], dict] = {}
    gaps: list[str] = []
    horizons = [start for start, _ in planned if start.strftime("%H%M") in committed]
    if not horizons:
        # 후크 경로에선 안 온다(방금 커밋된 window 가 committed 에 있다). 배치 경로에선
        # 온다 — 통째로 안 돈 세션·계획만 있고 수집이 0건인 날이다. `max()` 가 빈
        # 시퀀스로 ValueError 를 내던 자리이므로 사유를 밝혀 스킵한다.
        # ⚠️ 빈 파일을 쓰지 **않는다**. 아래 `aggregates` 가 빈 경우와 다른 사실이다:
        # 거긴 "커밋은 있는데 봉이 없다"(정정이 그날 봉을 지웠을 수 있다)라 덮어쓰는 게
        # 맞지만, 여긴 "원장에 커밋 자체가 없다"라 그날에 대해 아는 게 없다. 그걸로
        # 덮으면 다른 writer 가 채워 둔 멀쩡한 파티션을 지운다.
        logger.warning(
            "5분 롤업 %s: 커밋된 1분 window 가 0건 — 산출 없이 건너뛴다(파일 유지)",
            session_date,
        )
        return None
    horizon = max(horizons)
    bucket_last: dict[datetime, datetime] = {}  # bucket → 그 버킷의 마지막 계획 분
    for start, _ in planned:  # planned 는 오름차순이라 마지막 대입이 이긴다
        bucket_last[_bucket_of(start)] = start
    if all(last > horizon for last in bucket_last.values()):
        # 커밋은 있는데 **닫힌 버킷이 하나도 없다**(예: 09:00 만 커밋되고 세션이 죽었다).
        # 위 `horizons` 가드는 커밋 0건만 막으므로 여기가 안 막으면 아래 집계가 전부
        # 걸러져 `aggregates` 가 비고, 그 아래 "빈 파일이라도 쓴다" 경로로 떨어져
        # **다른 writer 가 채운 파티션을 0행 parquet 로 덮는다**(실측 1,665B).
        # 그 경로는 "정정이 그날 봉을 지웠다"는 후크 전제용이라 여기 오면 안 된다 —
        # 닫힌 봉이 없는 것과 봉이 폐기된 것은 다른 사실이다.
        logger.warning(
            "5분 롤업 %s: 커밋 지평 %s 까지 닫힌 버킷이 0개 — 산출 없이 건너뛴다(파일 유지)",
            session_date, horizon.strftime("%H%M"),
        )
        return None
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
    storage.put_bytes(day_key, sink.getvalue())
    return day_key


def unfilled_settled_days(
    storage: Storage, ledger, *, market: str, dataset: str, source_group: str,
    before: date,
) -> tuple[list[str], int]:
    """1분 원장이 멈춘 거래일인데 5분 파티션이 비어 있는 날 (ALPHA-839).

    5분 파생에는 원장이 없다 — 어느 거래일이 어느 세대집합에서 나왔는지 기록하는 표가
    없어서, 배치가 조용히 안 돌아도 분석은 계속 답을 낸다(낡은 답을). 세대집합 표를
    만드는 대신 **파티션 부재**를 물어보는 것으로 1차를 대신한다: 재료(1분)가 더 이상
    변하지 않는 날에 파생이 없으면 그건 결손이고, 그 판정에 새 표가 필요 없다.

    ⚠️ 판정 축이 `FINALIZED` 가 **아니다**. 의미상으론 그게 정확해 보이지만 dev 실측
    (2026-08-07)에서 `FINALIZED` 세션은 **0건**이다 — 전 세션이 `DRAINED` 에 멈춰 있고
    `qc-minute-session` 이 돌지 않는다. `FINALIZED` 로 물으면 이 판정은 영영 빈 목록을
    내면서 "구멍 없음"으로 보인다(안 본 것을 0건으로 확정하는 자리다). 축은 **원장이 더
    이상 움직이지 않는 phase 집합**이고, 그 정의는 `session_ops` 가 이미 갖고 있다 —
    여기서 다시 세면 두 곳이 갈린다.

    ⚠️ 파티션 판정 축도 `part-0.parquet` 존재가 **아니다**. 같은 파티션을 다른 writer 가
    다른 파일명으로 채우고(토스 백필 `part-toss-backfill.parquet`, ALPHA-828) 소비자는
    파티션 글롭으로 읽으므로, `part-0` 만 보면 이미 채워진 날을 구멍으로 보고한다
    (실측 2026-08-03).

    `WRITER_SINCE` 이전은 애초에 이 writer 의 소관이 아니라 대상에서 뺀다 — 넣으면
    영영 안 지워지는 결손 목록이 되고, 그러면 아무도 안 읽는다.

    ⚠️ 그래도 **자가 해소되지 않는 항목이 남는다** — 워커가 하루 통째로 안 돈 날은 커밋이
    0건이라 재실행해도 파생이 안 생기고, 매 실행마다 같은 날을 다시 보고한다. 억제 축을
    두지 않은 것은 의도다: 그건 진짜 구멍이고, 지우려면 1분 재수집이 필요하다.

    반환은 **(결손 거래일, 후보 일수)** 다. 분모를 같이 주는 이유는 빈 목록이 "구멍
    없음"과 "본 게 없음" 둘 다이기 때문이다 — dataset·source_group·phase·날짜 하한 넷 중
    하나만 빗나가도 후보가 0이 되고, 그때 목록만 보면 초록으로 읽힌다.
    """
    settled = _settled_session_dates(
        ledger, dataset=dataset, source_group=source_group, before=before
    )
    unfilled = [
        trade_date for trade_date in settled
        if not storage.list_keys(canonical_intraday_5m_prefix(market, trade_date))
    ]
    return unfilled, len(settled)


def _settled_session_dates(
    ledger, *, dataset: str, source_group: str, before: date
) -> list[str]:
    """원장이 더 이상 진행하지 않는 세션의 거래일 — `WRITER_SINCE` 이후·`before` 이전.

    ⚠️ phase 집합에 `DRAINING` 을 **포함한다**. `DRAINED_PHASES` 만 쓰면 `stop` 이 상한
    초과로 exit 1 한 날이 `DRAINING` 에 영구 고착하는데(ack 할 워커가 이미 없고 다음날
    start 는 새 session_id 를 만든다), 그날은 rollup 도 실패할 확률이 가장 높다 —
    **가장 위험한 날이 판정에서 구조적으로 빠진다.**

    ⚠️ 그래서 `before`(보통 오늘 KST)로 **오늘을 뺀다**. 안 빼면 마감 전에 도는 실행이
    진행 중인 `DRAINING` 을 고착으로 오인해 매일 거짓 양성으로 시작한다. 오늘의 결손은
    이 판정이 아니라 그 실행 자신의 exit code 와 `key` 가 말한다.
    """
    from .session_ops import DRAINED_PHASES

    with ledger.connect_fn(ledger.db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT session_date FROM minute_ingestion_session "
            "WHERE dataset = %s AND source_group = %s AND phase = ANY(%s) "
            "AND session_date >= %s AND session_date < %s ORDER BY session_date",
            (dataset, source_group, sorted(DRAINED_PHASES | {"DRAINING"}),
             date.fromisoformat(WRITER_SINCE), before),
        )
        return [session_date.isoformat() for (session_date,) in cur.fetchall()]


# ⚠️ 1분 트랙은 KR 전용이라 market 을 CLI 인자로 열지 않는다 — 오타 하나가 **없는
# prefix 를 훑고 빈 목록을 "구멍 없음"으로 확정**한다. 사유 전문은 `eod.py:_MARKET`.
_MARKET = "KR"


def rollup_session_cli(
    settings, *, dataset: str | None, source_group: str | None, session_date: str | None
) -> int:
    """`run rollup-minute-session --dataset … --source-group … [--session-date …]`.

    EOD 스케줄이 부를 자리다(스케줄 자체는 terraform — 별 PR). 그날 5분 파생을 마감 후
    한 번 확정하고, 지나간 거래일의 결손을 함께 보고한다.

    exit: 0=확정했다(또는 비거래일 no-op) / 1=확정하지 않았다(세션 없음·커밋 0건·닫힌
    버킷 0·다른 writer 파일 존재·`WRITER_SINCE` 이전 — 사유는 로그에) / 2=판정 자체를
    못 함(설정·인자 결손·DB/S3 장애). `qc-minute-session`·`plan-minute-session` 과 같은
    규약이다 — **인자 결손도 2** 다(`_resolve` 의 `SystemExit`(1) 을 쓰지 않고 여기서
    검증하는 이유. 1 은 "판정은 됐는데 결과가 나쁘다"라 SFN·운영자가 처방을 못 가른다).

    ⚠️ 세션 phase 를 게이트로 걸지 **않는다**. drain 이 상한 초과로 안 끝난 날에도 파생은
    나와야 하고(닫힌 버킷만 싣는 규칙이 부분 노출을 이미 막는다), 걸면 stop 이 막힌 하루가
    파생까지 통째로 잃는다. 대신 phase 를 출력에 실어 드러낸다.
    🔴 그 대가로 **후크와의 배타성이 코드에 없다** — 20:05(stop) 이전에 부르면 recovery
    레인의 늦은 커밋과 같은 `part-0` 을 두고 경합하고, 배치가 나중에 착지하면 그 커밋이
    빠진 산출이 남는다. 배타성은 스케줄 시각이 진다(terraform PR).
    """
    import json
    from datetime import datetime

    from ..db import stable_domain_id
    from ..lake import make_storage
    from ..ops.trading_calendar import is_trading_day
    from .repository import MinuteLedger
    from .states import DATASET_PRICE_MINUTE, SOURCE_GROUPS_BY_DATASET

    if settings.db is None:
        logger.error(
            "db 설정 없음 — rollup-minute-session 은 세션 원장 필수(DATA_PIPELINE_DB__* 주입)"
        )
        return 2
    # ⚠️ dataset 을 1분 원장 어휘 전체(`MINUTE_DATASETS`)로 열면 안 된다 — `_rollup_day`
    # 가 읽는 artifact 키는 **가격 분봉 전용**이고(`canonical_price_minute_artifact_key`)
    # 뉴스 세션도 390 window 를 계획하므로 `committed` 가 안 빈다. 그러면 뉴스 레인의
    # 커밋 지평으로 잘린 5분봉이 **가격 레인이 만든 온전한 파일을 덮는다**. `eod.py` 가
    # orphan 스캔에서 같은 이유로 같은 가드를 둔다(`_PRICE_DATASET`).
    if dataset != DATASET_PRICE_MINUTE:
        logger.error(
            "--dataset 은 %s 여야 한다: %s — 5분 파생은 가격 분봉 canonical 전용 경로다",
            DATASET_PRICE_MINUTE, dataset,
        )
        return 2
    if source_group not in SOURCE_GROUPS_BY_DATASET[DATASET_PRICE_MINUTE]:
        # 오타는 없는 session_id 를 유도해 "세션 없음"으로 보인다 — 지목이 틀린 것과
        # 그날이 안 돈 것은 처방이 다르다.
        logger.error(
            "--source-group 이 %s 의 어휘 밖이다: %s (아는 값 %s)",
            DATASET_PRICE_MINUTE, source_group,
            sorted(SOURCE_GROUPS_BY_DATASET[DATASET_PRICE_MINUTE]),
        )
        return 2
    if session_date is None:
        # 마감 후에 도는 스케줄이라 오늘(KST)이 곧 그 거래일이다. 스케줄러가 넘기는
        # 시각은 UTC 라 KST 저녁이 전날로 읽힌다 — 그래서 인자가 아니라 KST 로 잡는다
        # (`start_session_cli` 와 같은 단서).
        day = datetime.now(KST).date()
    else:
        try:
            day = datetime.strptime(session_date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("--session-date 가 YYYY-MM-DD 가 아니다: %s", session_date)
            return 2

    session_id = stable_domain_id("msn", dataset, source_group, day.isoformat())
    ledger = MinuteLedger(db=settings.db)
    storage = make_storage(settings.storage)
    result = {
        "session_id": session_id, "session_date": day.isoformat(),
        "trading_day": is_trading_day(day), "phase": None, "key": None,
        "unfilled_settled_days": None, "settled_day_count": None,
    }
    # ⚠️ 구멍 판정을 **rollup 과 다른 try 로 분리한다**. 같은 블록에 두면 PUT 실패
    # (AccessDenied 등)에서 판정이 계산도 출력도 안 되는데, 이 레인엔 exit≠0 백스톱이
    # 없어(스케줄러는 RunTask 제출까지만 본다) 그 목록이 유일한 신호다 — 실패한 날에
    # 꺼지는 감시는 감시가 아니다.
    exit_code = 0
    try:
        unfilled, candidates = unfilled_settled_days(
            storage, ledger, market=_MARKET, dataset=dataset,
            source_group=source_group, before=day,
        )
        result["unfilled_settled_days"], result["settled_day_count"] = unfilled, candidates
        if unfilled:
            # 확정을 막지 않는다 — 오늘 산출과 다른 날의 결손은 별개 사실이고, 여기서
            # 막으면 과거 구멍 하나가 오늘 파생을 무기한 세운다.
            logger.warning(
                "1분 원장이 멈춘 거래일인데 5분 파티션이 빈 날 %d건 / 후보 %d일: %s",
                len(unfilled), candidates, unfilled,
            )
        elif not candidates:
            # 빈 목록이 "구멍 없음"인지 "본 게 없음"인지 출력만으로는 같다 — 분모 0 은
            # 판정 축이 빗나갔다는 신호이므로 조용히 넘기지 않는다.
            logger.warning(
                "구멍 판정 후보가 0일이다(dataset=%s source_group=%s, %s 이후·%s 이전) — "
                "판정 축이 원장과 안 맞을 수 있다", dataset, source_group, WRITER_SINCE, day,
            )
    except Exception:
        logger.exception("5분 파생 구멍 판정 실패(rollup 은 계속한다)")
        exit_code = 2

    if not result["trading_day"]:
        # 스케줄이 MON-FRI 라 휴장일에도 뜬다. 그날은 세션 자체가 없어 1(확정 안 됨)이
        # 되는데, 그건 결손이 아니라 정상이다 — 매 휴장일이 빨간 런이 되면 진짜 결손이
        # 그 소음에 묻힌다. ⚠️ 출력 **형상은 정상 경로와 같게** 유지한다(`eod.py` 규약):
        # 키가 빠지면 소비자가 없는 키를 0 으로 읽어 "결손 없음"으로 오독한다.
        logger.info("5분 롤업 %s: 비거래일 — no-op", day.isoformat())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return exit_code

    try:
        snapshot = ledger.session_snapshot(session_id=session_id)
        result["phase"] = None if snapshot is None else snapshot.get("phase")
        if snapshot is None:
            logger.error(
                "5분 롤업 %s: 거래일인데 1분 세션이 없다 — Premarket 계획이 안 돌았다"
                "(session=%s)", day.isoformat(), session_id,
            )
        else:
            result["key"] = rollup_session(
                storage, ledger, session_id=session_id,
                market=_MARKET, session_date=day.isoformat(),
            )
    except Exception:
        logger.exception("5분 롤업 실행 실패: %s", session_id)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code or (0 if result["key"] else 1)
