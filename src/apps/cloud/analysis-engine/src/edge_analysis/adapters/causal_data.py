"""인과 설계용 데이터 접근 — **전부 클라우드 저장소(Postgres)에서 소비한다.**

실험판(`experiments/storm`)은 DuckDB + 로컬 절대경로를 읽었다. 그래서 같은 로직이
클라우드에서 돌 수 없었다. 여기서 그 표면을 클라우드 원장으로 옮긴다:

    storm v_event        -> source_event
    storm v_event_entity -> event_argument
    storm v_daily        -> price_daily          (simple_return·volume)
    storm v_hold         -> etf_holding_snapshot
    storm v_entity.industry -> instrument_classification  (V202607291720 신설)

ID 공간은 하나다. `event_argument.entity_id` 가 곧 `instrument.instrument_id` 이고
`price_daily.instrument_id`·`etf_holding_snapshot.constituent_instrument_id` 와 같다
(기존 `fetch_event_contexts` 가 그 조인을 이미 쓴다).

**두 가지를 코드가 강제한다.**

1. PIT. 술어에 `available_at` 을 쓸 수 없고, 시점 절은 코드가 주입한다. 한 단어를
   빠뜨리면 미래를 보는데 그건 사후에 탐지되지 않는다 - 결과가 그냥 좋아진다.
2. 정렬. `ar`·`mom`·`vol` 은 입력 `pairs` 순서를 그대로 지킨다. 단위를 고르는 건
   설계(에이전트)이고 정렬은 배관(코드)이다 - 실험판에서 에이전트가 배열 정렬에
   5턴을 태웠다.

초과수익은 **날짜별 횡단면 평균 대비**다(설명 프레임워크 §0 기준선). 창은 **거래일**
기준이다 - 달력일로 자르면 연휴가 표본을 깎는다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import numpy as np

from ..config import PipelineError

# 술어가 참조할 수 있는 컬럼. 이 목록 밖은 SQL 에러로 드러난다(조용히 비지 않는다).
COHORT_COLUMNS = (
    "instrument_id", "trade_date", "source_event_id", "event_type_code",
    "predicate_code", "role_code", "lifecycle_stage",
    "sector_name", "industry_name", "market_cap", "listing_market", "ticker",
)
UNIVERSE_COLUMNS = ("instrument_id", "sector_name", "industry_name", "market_cap",
                    "listing_market", "ticker")

# PIT 를 우회하거나 문장을 갈아탈 수 있는 토큰. 순수 WHERE 조건만 받는다.
_BANNED = re.compile(
    r"(--|/\*|;)|\b(available_at|data_version|insert|update|delete|drop|create|alter|"
    r"grant|copy|union|intersect|except|pg_sleep|pg_read_file|current_setting|set_config)\b",
    re.I,
)

_MIN_TRADING_DAYS = 5


def _guard(where: str, columns: tuple[str, ...]) -> str:
    """술어를 검사한다. **PIT 는 협상 대상이 아니다.**"""
    w = (where or "").strip()
    if not w:
        raise PipelineError(
            "코호트 술어가 비었다. 예: \"industry_name LIKE '%Semiconductor%'\"\n"
            f"쓸 수 있는 컬럼: {', '.join(columns)}")
    hit = _BANNED.search(w)
    if hit:
        raise PipelineError(
            f"코호트 술어에 쓸 수 없는 토큰: {hit.group()!r}. "
            "세미콜론·주석·DDL·집합연산은 금지이고 available_at 은 코드가 주입한다.\n"
            f"쓸 수 있는 컬럼: {', '.join(columns)}")
    return w


class CausalData:
    """인과 설계가 필요한 코호트·정렬열·가중을 클라우드 원장에서 공급한다."""

    def __init__(self, conn) -> None:
        """psycopg2 커넥션을 보관한다(수명은 호출자 소유)."""
        self._conn = conn

    # ── 내부 ────────────────────────────────────────────────────────────
    def _rows(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    @staticmethod
    def _split(pairs) -> tuple[list[str], list[str]]:
        ids = [str(i) for i, _ in pairs]
        ds = [d.isoformat() if isinstance(d, date) else str(d)[:10] for _, d in pairs]
        return ids, ds

    def _aligned(self, pairs, sql: str, params: list) -> np.ndarray:
        """(instrument_id, trade_date) -> 값. **입력 순서를 지킨다.** 없으면 nan."""
        if not pairs:
            return np.array([], dtype=float)
        got = {(str(a), str(b)[:10]): c for a, b, c in self._rows(sql, params)}
        ids, ds = self._split(pairs)
        return np.array([float(got[(i, d)]) if got.get((i, d)) is not None else np.nan
                         for i, d in zip(ids, ds)], dtype=float)

    # ── 코호트 ──────────────────────────────────────────────────────────
    def cohort(self, where: str, *, as_of: str,
               w0: date | str | None = None, w1: date | str | None = None,
               limit: int = 20000) -> list[tuple[str, date]]:
        """**사건 기반** (instrument_id, trade_date). 처치군의 조작적 정의다.

        술어가 곧 설계다 - 문자열로 남아 감사·재현되고, 셀 간 같은 설계인지 비교된다.

        `as_of` 는 **필수**다. 선택사항으로 두면 잊고, 잊으면 미래를 보는데 그건
        결과를 좋아지게 만들어 사후에 탐지되지 않는다. 빠뜨리면 TypeError 로 죽는다.
        """
        if not as_of:
            raise PipelineError("cohort 에 as_of 가 필요하다 - PIT 없이 코호트를 만들 수 없다")
        cl = _guard(where, COHORT_COLUMNS)
        params: list[Any] = []
        extra = ""
        if w0 is not None:
            extra += " AND c.trade_date >= %s"
            params.append(w0.isoformat() if isinstance(w0, date) else w0)
        if w1 is not None:
            extra += " AND c.trade_date <= %s"
            params.append(w1.isoformat() if isinstance(w1, date) else w1)
        extra += " AND c.available_at <= %s"              # 코드가 주입한다. 항상.
        params.append(as_of)
        sql = f"""
            WITH c AS ({self._cohort_cte()})
            SELECT DISTINCT c.instrument_id, c.trade_date
            FROM c WHERE ({cl}){extra}
            ORDER BY c.trade_date, c.instrument_id LIMIT {int(limit)}"""
        return [(str(a), b) for a, b in self._rows(sql, params)]

    @staticmethod
    def _cohort_cte() -> str:
        """사건 × 금융상품 × 분류를 한 표면으로. 분류는 PIT 상 최신 1건을 붙인다."""
        return """
            SELECT ea.entity_id            AS instrument_id,
                   se.event_date           AS trade_date,
                   se.source_event_id, se.event_type_code, se.predicate_code,
                   se.lifecycle_stage, se.available_at,
                   ea.role_code,
                   i.ticker,
                   cls.sector_name, cls.industry_name, cls.market_cap, cls.listing_market
            FROM source_event se
            JOIN event_argument ea ON ea.source_event_id = se.source_event_id
            JOIN instrument i ON i.instrument_id = ea.entity_id
            LEFT JOIN LATERAL (
                SELECT sector_name, industry_name, market_cap, listing_market
                FROM instrument_classification ic
                WHERE ic.instrument_id = ea.entity_id
                  AND ic.as_of_date <= se.event_date
                ORDER BY ic.as_of_date DESC LIMIT 1
            ) cls ON TRUE
            WHERE se.event_status = 'ACTIVE' AND se.event_date IS NOT NULL"""

    def universe(self, where: str, dates, *, exclude=None,
                 limit: int = 80000) -> list[tuple[str, date]]:
        """**금융상품 기반 × 날짜.** 대조군을 만든다. 거래 기록 있는 쌍만 남는다.

        `exclude` 에 처치 쌍을 주면 뺀다 - 대조에 처치가 섞이면 효과가 희석된다.
        """
        cl = _guard(where, UNIVERSE_COLUMNS)
        ds = sorted({d.isoformat() if isinstance(d, date) else str(d)[:10] for d in dates})
        if not ds:
            return []
        sql = f"""
            WITH u AS (
                SELECT p.instrument_id, p.trade_date,
                       i.ticker, cls.sector_name, cls.industry_name,
                       cls.market_cap, cls.listing_market
                FROM price_daily p
                JOIN instrument i ON i.instrument_id = p.instrument_id
                LEFT JOIN LATERAL (
                    SELECT sector_name, industry_name, market_cap, listing_market
                    FROM instrument_classification ic
                    WHERE ic.instrument_id = p.instrument_id
                      AND ic.as_of_date <= p.trade_date
                    ORDER BY ic.as_of_date DESC LIMIT 1
                ) cls ON TRUE
                WHERE p.trade_date = ANY(%s) AND p.simple_return IS NOT NULL
            )
            SELECT u.instrument_id, u.trade_date FROM u WHERE ({cl})
            ORDER BY u.trade_date, u.instrument_id LIMIT {int(limit)}"""
        out = [(str(a), b) for a, b in self._rows(sql, [ds])]
        if exclude:
            ex = {(str(a), str(b)[:10]) for a, b in exclude}
            out = [(a, b) for a, b in out if (a, str(b)[:10]) not in ex]
        return out

    # ── 정렬된 열 ───────────────────────────────────────────────────────
    _EXCESS = """
        xs AS (SELECT trade_date, avg(simple_return) mkt, count(*) n
               FROM price_daily WHERE simple_return IS NOT NULL
               GROUP BY trade_date HAVING count(*) >= %s),
        ex AS (SELECT p.instrument_id, p.trade_date,
                      p.simple_return - xs.mkt AS ar, p.simple_return AS r
               FROM price_daily p JOIN xs ON xs.trade_date = p.trade_date
               WHERE p.simple_return IS NOT NULL)"""

    def ar(self, pairs, *, min_cross: int = 50) -> np.ndarray:
        """쌍마다 **당일 초과수익**(횡단면 평균 대비). 순서 유지, 없으면 nan."""
        if not pairs:
            return np.array([], dtype=float)
        ids, ds = self._split(pairs)
        sql = f"""WITH {self._EXCESS}
            SELECT k.i, k.d, ex.ar FROM unnest(%s::text[], %s::date[]) AS k(i, d)
            JOIN ex ON ex.instrument_id = k.i AND ex.trade_date = k.d"""
        return self._aligned(pairs, sql, [min_cross, ids, ds])

    def _windowed(self, pairs, agg: str, days: int, lag: int, min_cross: int) -> np.ndarray:
        if not pairs:
            return np.array([], dtype=float)
        if days < 1 or lag < 0:
            raise PipelineError(f"창이 잘못됐다: days={days} lag={lag}")
        ids, ds = self._split(pairs)
        sql = f"""WITH {self._EXCESS},
            cal AS (SELECT trade_date, row_number() OVER (ORDER BY trade_date) rn
                    FROM (SELECT DISTINCT trade_date FROM price_daily) t),
            k AS (SELECT k.i, k.d, cal.rn FROM unnest(%s::text[], %s::date[]) AS k(i, d)
                  JOIN cal ON cal.trade_date = k.d)
            SELECT k.i, k.d, {agg} FROM k
            JOIN cal c2 ON c2.rn BETWEEN k.rn - %s AND k.rn - %s
            JOIN ex ON ex.instrument_id = k.i AND ex.trade_date = c2.trade_date
            GROUP BY k.i, k.d HAVING count(*) >= %s"""
        return self._aligned(pairs, sql,
                             [min_cross, ids, ds, days + lag - 1, lag, _MIN_TRADING_DAYS])

    def mom(self, pairs, *, days: int = 20, lag: int = 1,
            min_cross: int = 50) -> np.ndarray:
        """쌍마다 사건 `lag` **거래일** 전까지 `days` 거래일 누적 초과수익."""
        return self._windowed(pairs, "sum(ex.ar)", days, lag, min_cross)

    def vol(self, pairs, *, days: int = 20, lag: int = 1,
            min_cross: int = 50) -> np.ndarray:
        """쌍마다 사건 전 `days` 거래일 수익률 표준편차."""
        return self._windowed(pairs, "stddev_samp(ex.r)", days, lag, min_cross)

    # ── 크기 정합의 재료 ────────────────────────────────────────────────
    def weight(self, etf_instrument_id: str, trade_date: date,
               units: list[str] | None = None) -> dict:
        """ETF 내 비중. **무게 없는 원인은 산술로 죽는다** - 가장 싼 게이트의 입력이다."""
        rows = self._rows(
            "SELECT constituent_instrument_id, weight_ratio FROM etf_holding_snapshot"
            " WHERE etf_instrument_id = %s AND trade_date = %s AND weight_ratio IS NOT NULL",
            (etf_instrument_id, trade_date.isoformat()))
        w = {str(a): float(b) for a, b in rows}
        total = sum(w.values())
        out: dict[str, Any] = {"n_hold": len(w), "total_raw": round(total, 6)}
        if not total:
            out["share"] = None
            return out
        if units is None:
            out["all"] = {k: v / total for k, v in w.items()}
            return out
        got = {u: w.get(u, 0.0) / total for u in dict.fromkeys(units)}
        out["members"] = got
        out["share"] = sum(got.values())
        return out

    def required_effect(self, residual: float, share: float | None) -> float | None:
        """관측 잔차를 이 무게로 설명하려면 필요한 초과수익. **무료 게이트다.**

        `share` 가 0 이면 어떤 효과로도 설명이 안 된다 - None 을 돌려 산술 기각을 알린다.
        """
        if not share:
            return None
        return residual / share

    # ── 타입 사전 (분포 사실. 검정이 아니다) ────────────────────────────
    def type_population(self, event_type_code: str) -> dict:
        """같은 설계를 몇 번 쌓을 수 있나. 유효 n 은 **금융상품 군집** 기준이다."""
        r = self._rows(
            "SELECT count(DISTINCT se.source_event_id), count(DISTINCT ea.entity_id),"
            " count(DISTINCT se.event_date), min(se.event_date), max(se.event_date)"
            " FROM source_event se JOIN event_argument ea"
            " ON ea.source_event_id = se.source_event_id"
            " WHERE se.event_type_code = %s AND se.event_status = 'ACTIVE'",
            (event_type_code,))
        if not r:
            return {}
        ev, ent, dys, d0, d1 = r[0]
        return {"events": ev or 0, "instruments": ent or 0, "dates": dys or 0,
                "first": d0, "last": d1, "effective_n": ent or 0}

    def prior(self, event_type_code: str, *, need: float | None = None,
              min_cross: int = 50) -> dict:
        """사건 타입의 **분포 사실**. p값도 유의성 판정도 주지 않는다.

        쓰임은 하나다 - 이 타입이 이만한 움직임을 낼 수 있나. 분위수가 답한다.
        `need` 를 주면 그 크기 이상이 과거 몇 건이었는지 **빈도**로 답한다.
        """
        sql = f"""WITH {self._EXCESS},
            j AS (SELECT ex.ar FROM source_event se
                  JOIN event_argument ea ON ea.source_event_id = se.source_event_id
                  JOIN ex ON ex.instrument_id = ea.entity_id AND ex.trade_date = se.event_date
                  WHERE se.event_type_code = %s AND se.event_status = 'ACTIVE')
            SELECT count(*), avg(CASE WHEN ar > 0 THEN 1.0 ELSE 0.0 END),
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY abs(ar)),
                   percentile_cont(0.75) WITHIN GROUP (ORDER BY abs(ar)),
                   percentile_cont(0.90) WITHIN GROUP (ORDER BY abs(ar)),
                   max(abs(ar)),
                   sum(CASE WHEN abs(ar) >= %s THEN 1 ELSE 0 END)
            FROM j"""
        need_abs = abs(need) if need is not None else float("inf")
        r = self._rows(sql, [min_cross, event_type_code, need_abs])
        n, pos, q50, q75, q90, mx, k = r[0] if r else (0, None, None, None, None, None, 0)
        out = {"type": event_type_code, "n": int(n or 0), **self.type_population(event_type_code)}
        if not n:
            return out
        out.update(up_ratio=float(pos or 0), abs_q50=float(q50 or 0),
                   abs_q75=float(q75 or 0), abs_q90=float(q90 or 0),
                   abs_max=float(mx or 0))
        if need is not None:
            out.update(need=need_abs, n_at_least=int(k or 0),
                       freq_at_least=float((k or 0) / n))
        return out
