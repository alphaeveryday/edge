"""ETF 가격변동 트리거 적재 — 구성종목 가중 proxy 게이트 (ALPHA-406 → 411 정본화).

분석 SFN 의 RDS 영속 전제 체인 `price_movement_trigger → etf_contribution_observation →
explanation_route` 의 첫 고리이자, 이 테이블의 **단일 writer** 다(ALPHA-411 — 분석엔진의
트리거 쓰기는 소비 전환으로 제거된다).

**게이트 정책 정본은 분석엔진 L0 다**(결정 2026-07-17): observed_return 은 ETF 자체 종가
수익률이 아니라 **구성종목 가중 proxy 수익률** — canonical holdings 의 가중치와 구성종목
일봉 수익률로 `Σ(weight·ret) / Σ(weight)` (가격이 있는 부분집합에 한정한 coverage 정규화,
analysis-engine daily_pipeline.compute_decomposition 과 같은 산식) — 이고 임계값은
3%(`l0-abs-v1`, 설정 [price_triggers])다. detection_reason 은 엔진 포맷을 **접두로** 두고
뒤에 `|coverage=…` 를 덧붙인다(ALPHA-452 — 판정에 쓴 가격 coverage 를 행에 남긴다).
**coverage 로 막지는 않는다**: 최소 하한은 실측 분포와 엔진 정책 합의가 선행이라 ALPHA-453
소관이고, 엔진에는 아직 coverage 게이트가 없다.

detected_at·ID·멱등은 구현 소관이라 파이프라인 방식을 유지한다: 장 마감(KST 15:30) 고정
(엔진의 런타임 시계는 재실행마다 uq 세 번째 키를 흔든다), `pmt_<ULID>`(ADR-0027),
(etf, trade_date) 존재 시 skip.

**정책 이행(자동)**: 기존 행의 detection_policy_version 이 현재 정책과 다르면 —
observation 참조가 **없을 때만** 지우고 새 정책으로 재평가한다(0.5% 잠정 계열이 여기서
정리된다). 참조가 있으면 분석 계보 보존이 우선이라 남기고 센다(stale_policy_kept).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from decimal import Decimal

from ..config import DbConfig, PriceTriggersConfig
from ..db import connect, domain_id, ensure_etf_profile
from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_price_daily_partition,
    quality_log_key,
)

logger = logging.getLogger(__name__)

JOB_NAME = "load_price_triggers"
DATASET = "price_movement_trigger"

# 적재 대상 시장 → MIC(ISO 10383). load_etf_nav 와 같은 매핑 — instrument 마스터는
# market_code 를 MIC 로 들고 있어, ticker 만으로 조회하면 시장이 모호했다(구 ALPHA-448).
# 시장을 고정하면 (market_code, ticker) 자연키로 ticker 가 유일해 그 모호성이 사라진다.
_MIC_BY_MARKET = {"KR": "XKRX"}
_MICS_BY_MARKET = {"KR": ("XKRX", "XKOS", "XKON")}

# 결정적 detected_at — KRX 정규장 마감. 시각을 런타임 시계로 찍으면 재실행마다
# uq(etf, trade_date, detected_at)의 세 번째 키가 달라져 중복 행이 쌓인다.
_MARKET_CLOSE_KST = "T15:30:00+09:00"

_PARTIAL_EXIT_CODE = 2


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_values(storage: Storage, marker: str) -> list[str]:
    """마커 프리픽스 뒤의 파티션 값 목록(오름차순). 경로는 빌더로만 만든다(레이크 규약)."""
    values: set[str] = set()
    for key in storage.list_keys(marker):
        value = key[len(marker):].split("/", 1)[0]
        if value:
            values.add(value)
    return sorted(values)


def _num(value: object) -> float | None:
    """수치 위생 — normalize 의 _to_number 와 같은 기준(bool 차단 + isfinite).

    inf 는 비교 게이트를 조용히 통과해 CHECK 위반(런 전체 롤백)이나 가짜 수익률 커밋을
    만든다 — 결측 취급이 유일하게 안전하다(coerce-to-passing 금지).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    return float(value) if math.isfinite(value) else None


def _closes(storage: Storage, market: str, trade_date: str) -> dict[str, float]:
    """해당 거래일 파티션의 ticker → 종가(양수·유한만)."""
    out: dict[str, float] = {}
    prefix = canonical_price_daily_partition(market, trade_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            ticker = row.get("ticker")
            close = _num(row.get("close"))
            if ticker and close is not None and close > 0:
                out[str(ticker)] = close
    return out


def _holdings_by_etf(
    storage: Storage, market: str, as_of_date: str,
    expected_etfs: frozenset[str] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """해당 기준일 스냅샷 중 **유니버스 뿌리 안** ETF 를 etf_id → [(구성종목 티커, 가중치
    fraction)] 로 그룹핑(`expected_etfs`=None 이면 전량).

    파티션 parquet 을 **한 번만** 읽고 ETF 별로 나눈다 — ETF 마다 다시 읽으면 유니버스가
    31종일 때 같은 파일을 31번 재파싱한다(스냅샷당 O(ETF)).
    """
    by_etf: dict[str, list[tuple[str, float]]] = {}
    prefix = canonical_etf_holdings_partition(market, as_of_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            etf_id = row.get("etf_id")
            if not etf_id or not row.get("constituent_ticker"):
                continue
            if expected_etfs is not None and etf_id not in expected_etfs:
                # 유니버스 뿌리 밖(폐지분·참조 계열)은 트리거 대상이 아니다. 여기서 안 빼면
                # 아래 `unknown = holdings_etf_ids - master.keys()` 가 매 런 그만큼을
                # skipped_unknown_etf 로 세고 원장이 영구 INCOMPLETE 가 된다.
                continue
            weight_pct = _num(row.get("weight_pct"))
            if weight_pct is None or weight_pct < 0:
                continue
            by_etf.setdefault(str(etf_id), []).append(
                (str(row["constituent_ticker"]), weight_pct / 100.0))
    return by_etf


def _proxy_return(
    holdings: list[tuple[str, float]],
    closes: dict[str, float],
    prev_closes: dict[str, float],
) -> tuple[float | None, float]:
    """구성종목 가중 proxy 수익률과 **가격 coverage** — 엔진 compute_decomposition 과 같은 산식.

    가격이 있는 부분집합에 한정해 Σ(weight·ret)/Σ(weight) 로 정규화한다.
    가격이 하나도 없으면 proxy 는 None(트리거 판단 불능 — 결측으로 센다).

    **coverage 를 함께 돌려주는 이유**(ALPHA-452): 부분집합 재정규화는 표본이 작아도 값을 낸다 —
    1% 비중 종목 하나만 가격이 있고 그게 10% 오르면 proxy 도 10% 라 3% 게이트를 통과하고,
    나머지 99% 결손이 트리거 어디에도 안 남는다. `covered_weight / total_weight` 를 남겨야
    "ETF 가 움직였다"와 "가격 있는 한 종목이 움직였다"를 사후에 구분할 수 있다.

    산식·이름은 엔진 `compute_decomposition`(analysis-engine daily_pipeline.py) 을 그대로
    따른다 — 엔진은 이 값을 이미 설명 문구("가격 커버리지 87%")에 쓰고 있어, 새로 정의하면
    같은 이름의 두 수치가 생긴다. total_weight 가 0 이면 coverage 0.0(엔진과 동일).

    **coverage 로 막지는 않는다** — 최소 하한은 실측 분포와 엔진 정책 합의가 선행이라
    ALPHA-453 소관이다. 여기서는 계측만 한다.
    """
    total_weight = sum(weight for _t, weight in holdings)
    num = den = 0.0
    for ticker, weight in holdings:
        close = closes.get(ticker)
        prev_close = prev_closes.get(ticker)
        if close is None or prev_close is None:
            continue
        num += weight * (close / prev_close - 1.0)
        den += weight
    coverage = (den / total_weight) if total_weight > 0 else 0.0
    return ((num / den) if den > 0 else None), coverage


def _etf_instrument_ids(conn, mic: str) -> dict[str, str]:
    """(그 시장의) ETF ticker → instrument_id. load_etf_nav._etf_instrument_ids 와 동형.

    `(market_code, ticker)` 가 식별 자연키라 시장(MIC)을 고정하면 ticker 로 유일하다 —
    구 `_resolve_etf_instrument_ids` 가 ticker 만으로 조회해 복수 후보 모호성을 fail-loud 로
    막아야 했던 것과 달리(ALPHA-448), MIC 를 조회에 넣으면 그 모호성 자체가 없어진다.
    holdings 에 있으나 이 dict 에 없는 ETF 는 마스터 미등록 — 호출부가 그 ETF 만 skip 한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument"
            " WHERE market_code = %s AND instrument_type = 'ETF'",
            (mic,),
        )
        return {str(t): str(i) for t, i in cur.fetchall()}


def _market_instrument_ids(
    conn, mics: tuple[str, ...]
) -> tuple[dict[str, str], set[str]]:
    """시장 MIC 집합의 ticker→instrument_id와 모호 ticker.

    price manifest는 지역(KR)만 주므로 세 MIC를 함께 본다. 같은 ticker가 서로 다른
    instrument에 걸치면 어느 가격인지 고를 수 없어 그 ticker만 격리한다.
    """
    ids: dict[str, str] = {}
    ambiguous: set[str] = set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument WHERE market_code = ANY(%s)",
            (list(mics),),
        )
        for ticker, instrument_id in cur.fetchall():
            ticker, instrument_id = str(ticker), str(instrument_id)
            if ticker in ids and ids[ticker] != instrument_id:
                ambiguous.add(ticker)
            ids[ticker] = instrument_id
    return ids, ambiguous


def _manifest_targets(
    storage: Storage, input_run_id: str,
) -> tuple[int, int, int, int, dict[str, frozenset[str]]]:
    """NormalizePrice manifest의 전체/선택 계측과 KR 거래일별 winner ticker."""
    # LoadPriceDaily와 같은 manifest 검증을 공유한다. direct key·SHA 형식·정렬/고유 ID와
    # KR·US 전체 구조까지 검증하되 parquet는 GET하지 않는다 — 가격은 선행 loader가 DB에
    # 적재했고 이 스텝의 정상 입력은 DB 원장이다.
    from .load_price_daily import _manifest_partitions as price_manifest_partitions

    partition_total, winner_total, selected = price_manifest_partitions(storage, input_run_id)
    targets: dict[str, frozenset[str]] = {}
    for _market, trade_date, _key, _sha256, winner_ids in selected:
        targets[trade_date] = winner_ids
    return (
        partition_total,
        len(selected),
        winner_total,
        sum(len(winner_ids) for winner_ids in targets.values()),
        targets,
    )


def _latest_good_holdings(
    conn, etf_instrument_ids: list[str], trade_date: str,
) -> dict[str, tuple[str, list[tuple[str, float]]]]:
    """ETF별 ``trade_date`` 이하 최신-good DB holdings.

    good은 loader가 canonical 전체·유효 행을 보존한 status에서 유효 행이 과반인
    스냅샷이다. holdings는 그 status의 정확한 data_version만 읽어, 교정 시
    제거된 구성종목이 옛 version에 남아도 선택되지 않는다.
    미래 스냅샷 폴백은 look-ahead라 정상 manifest 경로에서는 쓰지 않는다.
    """
    if not etf_instrument_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "WITH ranked_dates AS ("
            " SELECT etf_instrument_id, trade_date, data_version,"
            " row_number() OVER (PARTITION BY etf_instrument_id"
            "                    ORDER BY trade_date DESC) AS rn"
            " FROM etf_holding_snapshot_status"
            " WHERE etf_instrument_id = ANY(%s) AND trade_date <= %s"
            "   AND 2 * valid_row_count >= input_row_count"
            ")"
            " SELECT h.etf_instrument_id, h.constituent_instrument_id, h.weight_ratio,"
            " h.trade_date"
            " FROM ranked_dates d JOIN etf_holding_snapshot h"
            "   ON h.etf_instrument_id = d.etf_instrument_id"
            "  AND h.trade_date = d.trade_date"
            "  AND h.data_version = d.data_version"
            " WHERE d.rn = 1 AND h.weight_ratio IS NOT NULL",
            (etf_instrument_ids, trade_date),
        )
        rows = cur.fetchall()
    dates: dict[str, str] = {}
    holdings: dict[str, list[tuple[str, float]]] = {}
    for etf_id, constituent_id, weight, as_of in rows:
        etf_id = str(etf_id)
        dates[etf_id] = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
        holdings.setdefault(etf_id, []).append((str(constituent_id), float(weight)))
    return {etf_id: (dates[etf_id], rows) for etf_id, rows in holdings.items()}


def _db_closes(
    conn, instrument_ids: list[str], trade_date: str, data_version: str,
) -> tuple[dict[str, float], dict[str, float]]:
    """현재 loader 성공 행과 instrument별 직전 거래일 종가를 PK 범위 조회한다."""
    if not instrument_ids:
        return {}, {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current.instrument_id, current.close_price, previous.close_price"
            " FROM price_daily current"
            " LEFT JOIN LATERAL ("
            "   SELECT close_price FROM price_daily previous"
            "   WHERE previous.instrument_id = current.instrument_id"
            "     AND previous.trade_date < current.trade_date"
            "   ORDER BY previous.trade_date DESC LIMIT 1"
            " ) previous ON TRUE"
            " WHERE current.instrument_id = ANY(%s) AND current.trade_date = %s"
            "   AND current.data_version = %s",
            (instrument_ids, trade_date, data_version),
        )
        rows = cur.fetchall()
    current: dict[str, float] = {}
    previous: dict[str, float] = {}
    for instrument_id, close, prev_close in rows:
        close_num, prev_num = _num(close), _num(prev_close)
        if close_num is not None and close_num > 0:
            current[str(instrument_id)] = close_num
        if prev_num is not None and prev_num > 0:
            previous[str(instrument_id)] = prev_num
    return current, previous


def _existing_triggers(
    conn, etf_instrument_ids: list[str]
) -> dict[str, dict[str, list[tuple[str, str, bool]]]]:
    """etf_instrument_id → {trade_date → [(policy_version, trigger_id, 계보 참조 여부), …]}.

    (etf, trade_date) 존재가 멱등의 근거이고, 정책·참조 여부가 이행 판단의 근거다.
    **etf 축을 키에 두는 이유**: 유니버스가 다중 ETF 라 date 단일 맵으로 접으면 ETF 끼리
    같은 날짜 행이 조용히 덮인다. **날짜당 리스트인 이유**: uq 는 (etf, date, detected_at)
    3키라 같은 날짜에 행이 여럿일 수 있다(엔진 writer 가 소비 전환 전까지 런타임 detected_at
    으로 씀) — 날짜당 1행으로 접으면 어느 행을 보느냐가 조회 순서에 달려 이행이 비결정적이 된다.
    `= ANY(%s)` 로 유니버스 전체를 **한 번에** 조회한다 — ETF 마다 호출하면 EXISTS 서브쿼리 2개가
    31번 돈다. 참조 검사는 트리거를 참조하는 **모든** 테이블을 덮어야 한다(현재 FK 2개:
    etf_contribution_observation·price_observation — 후자는 ON DELETE CASCADE 라
    빠뜨리면 stale 정리가 가격분석 계보를 조용히 연쇄 삭제한다).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.etf_instrument_id, t.trade_date, t.detection_policy_version,"
            " t.price_movement_trigger_id,"
            " EXISTS (SELECT 1 FROM etf_contribution_observation o"
            "         WHERE o.price_movement_trigger_id = t.price_movement_trigger_id)"
            " OR EXISTS (SELECT 1 FROM price_observation p"
            "            WHERE p.price_movement_trigger_id = t.price_movement_trigger_id)"
            " FROM price_movement_trigger t WHERE t.etf_instrument_id = ANY(%s)",
            (etf_instrument_ids,),
        )
        out: dict[str, dict[str, list[tuple[str, str, bool]]]] = {}
        for eid, d, p, i, o in cur.fetchall():
            key = d.isoformat() if hasattr(d, "isoformat") else str(d)
            out.setdefault(str(eid), {}).setdefault(key, []).append((str(p), str(i), bool(o)))
        return out


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    expected_etfs: frozenset[str] | None = None,
    config: PriceTriggersConfig,
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """가격 manifest·DB 원장 → proxy 게이트 → 트리거 적재. 성공 0, 장애 시 비0.

    input_run_id는 현재 NormalizePrice winner만 DB에서 평가한다. from/to와 인자 미지정은
    기존 명시 복구 호출자가 선택한 canonical 경로다.
    """
    started_at = datetime.now(timezone.utc)
    considered = missing_holdings = missing_price = gated_out = 0
    future_asof_used = 0  # 최초 스냅샷 이전 날짜에 미래 as_of 폴백을 쓴 (ETF×날짜) 횟수(ALPHA-418)
    already = created = replaced_stale_policy = stale_policy_kept = 0
    skipped_unknown_etf = 0  # holdings 에 있으나 마스터 미등록 — 그 ETF 만 skip(런은 계속)
    universe: list[str] = []  # try 밖에서도 로그 블록이 참조 — 예외 시 빈 목록
    created_rows: list[dict] = []
    failures: list[dict] = []
    # etf_ticker → {거래일 → 가격 coverage} (ALPHA-452, 판정 무관). ETF 축을 키에 둬야 다중
    # ETF 가 같은 날짜 coverage 로 서로 덮지 않는다.
    coverage_by_etf_date: dict[str, dict[str, float]] = {}
    exit_code = 0
    manifest_partitions_total = manifest_partitions_selected = 0
    manifest_winners_total = manifest_winners_selected = 0
    target_tickers_resolved = holdings_rows_read = logical_targets = 0
    current_prices_read = previous_prices_read = 0
    unknown_target_tickers: set[str] = set()
    ambiguous_target_tickers: set[str] = set()
    manifest_scope: dict[
        str,
        tuple[
            dict[str, tuple[str, list[tuple[str, float]]]],
            dict[str, float],
            dict[str, float],
        ],
    ] = {}

    closes_cache: dict[str, dict[str, float]] = {}

    def closes_of(date: str) -> dict[str, float]:  # ETF 무관 — ETF 루프 전반에서 재사용
        """그 거래일의 종가 맵 — 날짜당 1회만 읽는 캐시."""
        if date not in closes_cache:
            closes_cache[date] = _closes(storage, config.market, date)
        return closes_cache[date]

    holdings_cache: dict[str, dict[str, list[tuple[str, float]]]] = {}

    def holdings_of(as_of: str) -> dict[str, list[tuple[str, float]]]:
        """as_of 스냅샷의 etf_id → holdings. 파티션은 스냅샷당 1회만 파싱(다중 ETF 재파싱 방지)."""
        if as_of not in holdings_cache:
            holdings_cache[as_of] = _holdings_by_etf(
                storage, config.market, as_of, expected_etfs)
        return holdings_cache[as_of]

    def remove_stale_without_replacement(
        conn, trigger_ids: list[str], etf_ticker: str, trade_date: str,
    ) -> bool:
        """replacement가 없는 stale 삭제도 한 셀 단위로 격리한다."""
        nonlocal replaced_stale_policy, exit_code
        if not trigger_ids:
            return True
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT price_trigger_stale")
            try:
                for trigger_id in trigger_ids:
                    cur.execute(
                        "DELETE FROM price_movement_trigger"
                        " WHERE price_movement_trigger_id = %s",
                        (trigger_id,),
                    )
            except Exception as exc:
                cur.execute("ROLLBACK TO SAVEPOINT price_trigger_stale")
                cur.execute("RELEASE SAVEPOINT price_trigger_stale")
                failures.append({
                    "reasons": ["row_load_error"],
                    "etf_ticker": etf_ticker,
                    "trade_date": trade_date,
                    "error": str(exc),
                })
                exit_code = _PARTIAL_EXIT_CODE
                return False
            cur.execute("RELEASE SAVEPOINT price_trigger_stale")
        replaced_stale_policy += len(trigger_ids)
        return True

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date와 to_date는 함께 써야 한다")
        manifest_targets: dict[str, frozenset[str]] = {}
        if input_run_id is not None:
            (
                manifest_partitions_total,
                manifest_partitions_selected,
                manifest_winners_total,
                manifest_winners_selected,
                manifest_targets,
            ) = _manifest_targets(storage, input_run_id)
            targets = sorted(manifest_targets)
            prev_by_date: dict[str, str] = {}
            holdings_dates: list[str] = []
        else:
            price_marker = canonical_price_daily_partition(config.market, "")
            dates = _partition_values(storage, price_marker)
            # canonical 최초 날짜는 분모(직전 거래일)가 레이크에 없다 — 구조적 제외라 결손
            # 신호에 섞지 않는다(매 런 +1 상수 노이즈가 실제 결손을 묻는다).
            prev_by_date = {dates[i]: dates[i - 1] for i in range(1, len(dates))}
            targets = [d for d in dates[1:]
                       if (from_date is None or d >= from_date)
                       and (to_date is None or d <= to_date)]
            holdings_dates = _partition_values(
                storage, canonical_etf_holdings_partition(config.market, ""))

        with connect(db) as conn:
            # 마스터 해소는 시장(MIC) 고정 — (market_code, ticker) 자연키라 ticker 가 유일하다.
            # 구 ticker-만-조회의 복수 후보 fail-loud 가 여기서 소멸한다(ALPHA-448 → ALPHA-465).
            mic = _MIC_BY_MARKET.get(config.market)
            master = _etf_instrument_ids(conn, mic) if mic else {}

            # 트리거 대상 유니버스 = 전 holdings 스냅샷의 etf_id ∩ **유니버스 뿌리**(expected_etfs —
    # `_holdings_by_etf` 가 선필터) ∩ 마스터. holdings 가
            # 있어야 proxy 분해가 되고, 마스터에 있어야 FK(etf_instrument_id→etf_profile)가
            # 꽂힌다. holdings 에 있으나 마스터에 없는 ETF 는 그 ETF 만 skip+카운트한다 —
            # 1종 시드 누락이 나머지 유니버스를 죽이면 안 된다(구 missing_etf_master 는 런
            # 전체 exit 1 이었다).
            if input_run_id is not None:
                mics = _MICS_BY_MARKET.get(config.market, ())
                price_ids, ambiguous = _market_instrument_ids(conn, mics)
                candidate_tickers = set(master)
                if expected_etfs is not None:
                    candidate_tickers &= expected_etfs
                if config.etf_ticker is not None:
                    if config.etf_ticker not in master:
                        raise ValueError(f"instrument 마스터에 없는 ETF 필터: {config.etf_ticker}")
                    if (expected_etfs is not None
                            and config.etf_ticker not in expected_etfs):
                        raise ValueError(f"유니버스 뿌리 밖 ETF 필터: {config.etf_ticker}")
                    candidate_tickers &= {config.etf_ticker}
                etf_ids = [master[ticker] for ticker in sorted(candidate_tickers)]
                related_tickers: set[str] = set()
                id_to_ticker = {instrument_id: ticker for ticker, instrument_id in master.items()}
                for date, winner_tickers in manifest_targets.items():
                    ambiguous_here = winner_tickers & ambiguous
                    unknown_here = winner_tickers - price_ids.keys() - ambiguous
                    ambiguous_target_tickers |= ambiguous_here
                    unknown_target_tickers |= unknown_here
                    resolved_ids = {
                        price_ids[ticker] for ticker in winner_tickers
                        if ticker in price_ids and ticker not in ambiguous
                    }
                    target_tickers_resolved += len(resolved_ids)
                    holdings_by_id = _latest_good_holdings(conn, etf_ids, date)
                    missing_ids = sorted(set(etf_ids) - holdings_by_id.keys())
                    if missing_ids:
                        missing_holdings += len(missing_ids)
                        failures.append({
                            "reasons": ["missing_holdings"],
                            "trade_date": date,
                            "etf_instrument_ids": missing_ids,
                        })
                        exit_code = _PARTIAL_EXIT_CODE
                    related_by_ticker: dict[str, tuple[str, list[tuple[str, float]]]] = {}
                    for etf_id, (as_of, holdings) in holdings_by_id.items():
                        if any(constituent_id in resolved_ids for constituent_id, _ in holdings):
                            ticker = id_to_ticker[etf_id]
                            related_by_ticker[ticker] = (as_of, holdings)
                            related_tickers.add(ticker)
                    # manifest의 물리 winner가 곧 DB 가격 범위다. 관련 ETF가 없는 winner도
                    # 현재/직전 가격 결손을 검증해야 하므로 holdings 교집합으로 더 줄이지 않는다.
                    current, previous = _db_closes(
                        conn, sorted(resolved_ids), date, input_run_id)
                    missing_current = sorted(resolved_ids - current.keys())
                    missing_previous = sorted(resolved_ids - previous.keys())
                    missing_price_ids = set(missing_current) | set(missing_previous)
                    if missing_price_ids:
                        missing_price += len(missing_price_ids)
                        failures.append({
                            "reasons": ["missing_db_price"],
                            "trade_date": date,
                            "missing_current_instrument_ids": missing_current,
                            "missing_previous_instrument_ids": missing_previous,
                        })
                        exit_code = _PARTIAL_EXIT_CODE
                    holdings_rows_read += sum(
                        len(holdings) for _as_of, holdings in holdings_by_id.values()
                    )
                    logical_targets += len(related_by_ticker)
                    current_prices_read += len(current)
                    previous_prices_read += len(previous)
                    manifest_scope[date] = (related_by_ticker, current, previous)
                universe = sorted(related_tickers)
                if ambiguous_target_tickers or unknown_target_tickers:
                    failures.append({
                        "reasons": ["unresolved_manifest_tickers"],
                        "unknown_tickers": sorted(unknown_target_tickers),
                        "ambiguous_tickers": sorted(ambiguous_target_tickers),
                    })
                    exit_code = _PARTIAL_EXIT_CODE
            elif config.etf_ticker is not None:
                # 옵션 단일 실행 필터(dev 검증·백필) — 그 한 종만 해소한다. 전 스냅샷을 미리
                # 스캔하지 않는다: 필터 밖 ETF·과거 파티션의 손상 parquet 이 대상 ETF 실행까지
                # 롤백시키면 필터가 격리를 못 하는 셈이 된다(홀딩스는 아래 date 루프가 대상
                # ETF 것만 지연 로드). 마스터에 없으면 fail-loud — 오타난 티커가 빈 성공 실행으로
                # 조용히 끝나면 요청한 백필이 수행 안 된 걸 탐지 못 한다(Rule 12).
                # 유니버스 뿌리 밖도 같은 처방이다 — 마스터엔 있는데 뿌리 밖인 ETF 를 지목하면
                # `_holdings_by_etf` 가 그 ETF 를 걸러 holdings 가 빈다. 그러면 매 대상일이
                # `missing_holdings`(유실 아님)로 잡혀 **exit 0 · 트리거 0건**으로 끝난다 —
                # 요청한 백필이 수행 안 된 것을 탐지 못 하는, 오타와 정확히 같은 실패다.
                # 참조 계열 47종이 바로 운영자가 --etf-ticker 로 지목할 법한 집합이다.
                # **마스터 미등록을 먼저 본다.** 오타 티커는 정의상 뿌리에도 없으므로, 뿌리
                # 검사를 앞에 두면 모든 오타가 "유니버스 뿌리 밖"으로 보고된다 — 그 처방은
                # "etf_map 에 추가하라"라, 존재하지도 않는 티커를 config 에 넣으라는 말이 된다.
                # 마스터 미등록이 더 구체적이고 더 실행가능한 신호다.
                out_of_root = expected_etfs is not None and config.etf_ticker not in expected_etfs
                if config.etf_ticker not in master:
                    reason = "unknown_etf_filter"
                elif out_of_root:
                    reason = "etf_filter_out_of_universe_root"
                else:
                    reason = None
                if reason is None:
                    universe = [config.etf_ticker]
                else:
                    logger.error(
                        "etf_ticker 필터=%s 를 실행할 수 없다(%s) — 티커 오타·마스터 미적재이거나"
                        " 유니버스 뿌리(krx_etf.source.etf_map) 밖이다. 빈 실행으로 끝내지 않고"
                        " fail-loud 한다", config.etf_ticker, reason)
                    failures.append({"reasons": [reason], "etf_ids": [config.etf_ticker]})
                    exit_code = 1
                    universe = []
            else:
                # 유니버스 탐색용 스냅샷 범위. to_date 백필이면 **그 이후 첫 스냅샷까지만** 본다 —
                # 그 뒤 스냅샷은 창 안 어떤 거래일의 as_of(≤date 최신 / 가장 이른 미래)로도
                # 선택될 수 없어, 읽으면 창 밖 obsolete 파티션의 손상·무관 ETF 만 백필에
                # 끌어들인다(Codex P2). 과거 스냅샷은 상한을 못 둔다: holdings 에서 빠진 ETF 가
                # 옛 스냅샷을 최신 as_of 로 쓰므로(stale holdings 정합) 전부 봐야 한다.
                scan_dates = holdings_dates
                if to_date is not None:
                    after = [hd for hd in holdings_dates if hd > to_date]
                    if after:
                        scan_dates = [hd for hd in holdings_dates if hd <= after[0]]
                holdings_etf_ids: set[str] = set()
                for hd in scan_dates:
                    holdings_etf_ids |= holdings_of(hd).keys()
                unknown = sorted(holdings_etf_ids - master.keys())
                skipped_unknown_etf = len(unknown)
                if unknown:
                    logger.warning(
                        "holdings 에 있으나 instrument 마스터에 없는 ETF %d종 skip: %s"
                        " — 마스터 적재(load-instruments) 누락 여부 확인",
                        len(unknown), ", ".join(unknown))
                    failures.append({"reasons": ["skipped_unknown_etf"], "etf_ids": unknown})
                universe = sorted(holdings_etf_ids & master.keys())
                # 평가할 거래일(targets)은 있는데 유니버스가 비면 — 가격은 들어왔으나 트리거
                # 대상 ETF 가 하나도 없다. holdings 전량 결손이거나 전부 마스터 미등록인
                # 상황으로, considered=0·exit 0 만 남기면 "정상적으로 0종 처리"와 구분이 안
                # 된다. 결손 신호를 로그에 남긴다(Rule 12). 이른 시스템 상태일 수도 있어
                # 런을 fail 시키지는 않는다 — skipped_unknown_etf 가 원인을 가른다.
                if targets and not universe:
                    logger.warning(
                        "평가 대상 거래일 %d 개가 있으나 트리거 유니버스가 비었다"
                        " (holdings∩마스터=∅, skipped_unknown_etf=%d)",
                        len(targets), skipped_unknown_etf)
                    failures.append({"reasons": ["empty_universe"],
                                     "targets": len(targets),
                                     "skipped_unknown_etf": skipped_unknown_etf})

            existing = (_existing_triggers(conn, [master[t] for t in universe])
                        if universe and targets else {})
            for etf_ticker in universe:
                etf_instrument_id = master[etf_ticker]
                etf_existing = existing.get(etf_instrument_id, {})
                etf_coverage = coverage_by_etf_date.setdefault(etf_ticker, {})
                # etf_profile 선행 보장은 ETF 당 최대 1회(첫 INSERT 직전) — 루프 안 매 행마다
                # 부르지 않는다. 트리거를 하나도 안 만든 ETF(전량 gated_out)엔 불필요.
                profile_ensured = False
                for date in targets:
                    if input_run_id is not None:
                        related, current_closes, previous_closes = manifest_scope[date]
                        selected_holdings = related.get(etf_ticker)
                        if selected_holdings is None:
                            continue
                    considered += 1
                    rows = etf_existing.get(date, [])
                    kept_for_date = False
                    stale_trigger_ids: list[str] = []
                    for policy, trigger_id, has_lineage in rows:
                        if policy == config.policy_version:
                            continue  # 현행 정책 행 — 아래에서 already 판정
                        if has_lineage:
                            # 구정책 행이지만 다운스트림 계보(contribution observation 또는
                            # price_observation)가 매달려 있다 — 지우면 설명·가격분석 이력이
                            # 끊긴다(후자는 CASCADE 라 소리 없이). 보존하고 수치로 드러낸다.
                            stale_policy_kept += 1
                            kept_for_date = True
                            continue
                        # 구정책·무참조 — 판정 결과가 replacement면 INSERT와 같은 savepoint에서
                        # 지운다. 먼저 지우면 INSERT 실패를 삼킨 뒤 삭제만 commit된다.
                        stale_trigger_ids.append(trigger_id)
                    if any(policy == config.policy_version for policy, _i, _l in rows):
                        if not remove_stale_without_replacement(
                                conn, stale_trigger_ids, etf_ticker, date):
                            continue
                        already += 1
                        continue
                    if kept_for_date:
                        # 계보 때문에 남긴 구정책 행이 이 날짜를 대표한다 — 옆에 새 행을 더 꽂으면
                        # 소비자가 (etf, date)로 두 행을 보게 된다.
                        if not remove_stale_without_replacement(
                                conn, stale_trigger_ids, etf_ticker, date):
                            continue
                        continue

                    if input_run_id is not None:
                        as_of, holdings = selected_holdings
                        proxy_ret, coverage = _proxy_return(
                            holdings, current_closes, previous_closes)
                    else:
                        # 명시 복구는 역사적 정책을 보존한다: 과거(≤date) 최신, 없으면 최초 미래.
                        # 정상 manifest 경로는 위 DB latest-good 쿼리로 look-ahead를 금지한다.
                        as_of_eligible = [x for x in holdings_dates if x <= date]
                        as_of = next(
                            (x for x in reversed(as_of_eligible)
                             if holdings_of(x).get(etf_ticker)), None)
                        if as_of is None:
                            as_of = next(
                                (x for x in holdings_dates
                                 if x > date and holdings_of(x).get(etf_ticker)), None)
                            if as_of is not None:
                                future_asof_used += 1
                        holdings = holdings_of(as_of).get(etf_ticker, []) if as_of else []
                        if not holdings:
                            missing_holdings += 1
                            continue
                        proxy_ret, coverage = _proxy_return(
                            holdings, closes_of(date), closes_of(prev_by_date[date]))
                    # 게이트 판정과 **무관하게** 남긴다 — 최소 하한(ALPHA-453)을 정하려면 트리거가
                    # 난 날뿐 아니라 걸러진 날의 분포도 필요하다.
                    #
                    # 단 이 지점은 현행 정책 행이 **이미 있는 날짜에는 닿지 않는다**(위 already
                    # continue). 그래서 로그의 이 맵은 "아직 트리거가 없는 날"의 분포이고, 트리거가
                    # 난 날의 coverage 는 그 행의 detection_reason 에 남는다 — 둘을 합쳐야 전체다.
                    # 남는 구멍은 ALPHA-452 이전에 만들어진 기존 행뿐이다(그 행들은 어느 쪽에도
                    # 없다). 일회성 과거 구멍이라 여기서 메우지 않는다 — already 앞으로 옮기면 매 런
                    # 전체 거래일의 가격 파티션을 다시 읽어 비용이 히스토리에 비례해 는다(Codex #149).
                    etf_coverage[date] = round(coverage, 4)
                    if proxy_ret is None:
                        if not remove_stale_without_replacement(
                                conn, stale_trigger_ids, etf_ticker, date):
                            continue
                        if input_run_id is not None:
                            exit_code = _PARTIAL_EXIT_CODE
                        else:
                            missing_price += 1
                        continue
                    if abs(proxy_ret) < config.abs_threshold:
                        if not remove_stale_without_replacement(
                                conn, stale_trigger_ids, etf_ticker, date):
                            continue
                        gated_out += 1
                        continue
                    trigger_id = domain_id("pmt")
                    with conn.cursor() as cur:
                        cur.execute("SAVEPOINT price_trigger_row")
                        try:
                            for stale_trigger_id in stale_trigger_ids:
                                cur.execute(
                                    "DELETE FROM price_movement_trigger"
                                    " WHERE price_movement_trigger_id = %s",
                                    (stale_trigger_id,),
                                )
                            # price_movement_trigger.etf_instrument_id 는 etf_profile 을 참조한다.
                            # 자기 FK 선행과 행 INSERT를 같은 savepoint에 둬 한 ETF 실패가 앞선
                            # 성공 ETF를 롤백시키지 않게 한다.
                            if not profile_ensured:
                                ensure_etf_profile(conn, etf_instrument_id)
                                profile_ensured = True
                            cur.execute(
                                "INSERT INTO price_movement_trigger (price_movement_trigger_id,"
                                " etf_instrument_id, trade_date, detected_at, observed_return,"
                                " market_relative_return, absolute_gate_triggered,"
                                " relative_gate_triggered, detection_policy_version, detection_reason)"
                                " VALUES (%s, %s, %s, %s, %s, NULL, TRUE, FALSE, %s, %s)",
                                (
                                    trigger_id,
                                    etf_instrument_id,
                                    date,
                                    f"{date}{_MARKET_CLOSE_KST}",
                                    proxy_ret,
                                    config.policy_version,
                                    f"abs|{proxy_ret:.4f}|>={config.abs_threshold}"
                                    f"|coverage={coverage:.4f}",
                                ),
                            )
                        except Exception as exc:
                            cur.execute("ROLLBACK TO SAVEPOINT price_trigger_row")
                            cur.execute("RELEASE SAVEPOINT price_trigger_row")
                            profile_ensured = False
                            failures.append({
                                "reasons": ["row_load_error"],
                                "etf_ticker": etf_ticker,
                                "trade_date": date,
                                "error": str(exc),
                            })
                            exit_code = _PARTIAL_EXIT_CODE
                            continue
                        cur.execute("RELEASE SAVEPOINT price_trigger_row")
                    replaced_stale_policy += len(stale_trigger_ids)
                    created += 1
                    created_rows.append({"etf_ticker": etf_ticker, "trade_date": date,
                                         "observed_return": proxy_ret,
                                         "price_movement_trigger_id": trigger_id,
                                         "holdings_as_of": as_of, "coverage": round(coverage, 4)})
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("트리거 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, created_rows = 0, []
        replaced_stale_policy = 0
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "market": config.market, "etf_tickers": universe, "etf_filter": config.etf_ticker,
        "input_run_id": input_run_id, "from_date": from_date, "to_date": to_date,
        "manifest_partitions_total": manifest_partitions_total,
        "manifest_partitions_selected": manifest_partitions_selected,
        "manifest_winners_total": manifest_winners_total,
        "manifest_winners_selected": manifest_winners_selected,
        "target_tickers_resolved": target_tickers_resolved,
        "physical_price_targets": target_tickers_resolved,
        "unknown_target_tickers": sorted(unknown_target_tickers),
        "ambiguous_target_tickers": sorted(ambiguous_target_tickers),
        "holdings_rows_read": holdings_rows_read,
        "logical_etf_targets": logical_targets,
        "current_prices_read": current_prices_read,
        "previous_prices_read": previous_prices_read,
        "skipped_unknown_etf": skipped_unknown_etf,
        "abs_threshold": config.abs_threshold, "policy_version": config.policy_version,
        "considered": considered, "missing_holdings": missing_holdings,
        "future_asof_used": future_asof_used,
        "missing_price": missing_price, "gated_out": gated_out,
        "already_present": already, "replaced_stale_policy": replaced_stale_policy,
        "stale_policy_kept": stale_policy_kept,
        "created": created, "created_rows": created_rows,
        # 가격 coverage 계측(ALPHA-452) — **아직 트리거가 없는 (ETF, 거래일)**의 분포다(현행
        # 정책 행이 있는 셀은 already 로 먼저 빠진다). 트리거가 난 셀의 coverage 는 그 행의
        # detection_reason 에 있으니, 하한(ALPHA-453)은 둘을 합쳐 봐야 한다. ETF 축을 키에 둬야
        # 다중 ETF 가 같은 날짜 coverage 로 서로 덮지 않는다.
        # min 은 "가장 얇은 근거로 평가한 셀"이라 하한 후보의 출발점이고, 전체 맵이 있어야
        # 그 값이 이상치인지 상시인지 구분된다.
        # ponytail: 창 미지정(기본 전체 스캔)이면 트리거가 안 난 과거 (ETF,날짜)가 매 런 다시
        # 담겨 로그당 O(ETF×거래일)·누적 O(N²)이다(Codex #148). 이 맵이 곧 ALPHA-453 의 근거라
        # 지금은 그대로 둔다 — 커지면 --from/--to 로 창을 좁히거나 히스토그램으로 축약한다.
        "coverage_by_etf_date": coverage_by_etf_date,
        "coverage_min": min((c for m in coverage_by_etf_date.values() for c in m.values()),
                            default=None),
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). ⚠️ `gated_out`·`missing_price`·`missing_holdings` 는
        # **유실이 아니다** — 임계 미달이라 트리거가 안 난 것이거나 평가 대상이 아직 없는 것이다
        # (트리거는 입력 행마다 1:1로 나오지 않는다). 유실은 마스터가 모르는 ETF 뿐이다.
        # `already`(현행 정책 행이 이미 있어 재평가 없이 건너뛴 셀)도 산출로 세지 않는다 —
        # 재판정하지 않은 것을 이 런의 근거로 쓰면 유실 카운터와 스코프가 어긋난다.
        "ops": {
            "records_out": created,
            "failed_records": (
                missing_holdings + missing_price
                + len(unknown_target_tickers) + len(ambiguous_target_tickers)
                + sum(1 for failure in failures
                      if set(failure.get("reasons", ()))
                      & {"load_error", "row_load_error"})
                if input_run_id is not None
                else len(failures) + skipped_unknown_etf
            ),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_price_triggers: etfs=%d skipped_unknown_etf=%d considered=%d missing_holdings=%d"
        " missing_price=%d gated_out=%d already=%d replaced_stale=%d stale_kept=%d created=%d"
        " failures=%d manifest=%d/%d winners=%d/%d holdings_rows=%d logical_targets=%d"
        " prices=%d/%d",
        len(universe), skipped_unknown_etf, considered, missing_holdings, missing_price,
        gated_out, already, replaced_stale_policy, stale_policy_kept, created, len(failures),
        manifest_partitions_selected, manifest_partitions_total,
        manifest_winners_selected, manifest_winners_total, holdings_rows_read, logical_targets,
        current_prices_read, previous_prices_read,
    )
    return exit_code
