"""층 회계 - 시장 · 시장차감 섹터 · 고유. **계수는 전부 1 로 고정한다(ALPHA-862).**

    ETF 구간수익 = 시장수익 + (섹터수익 − 시장수익) + 고유(잔여)
    종목 고유    = 종목수익 − Σ 층 기여

⚠️ **β=1 은 알고 받아들인 오지정이다.** 실측(42거래일 · KODEX반도체) β_시장 1.4~1.8 —
시장 몫을 과소 배정하고 그 차액이 고유로 샌다. 그래도 고정하는 이유: 하나의 상수
β 를 60일 회귀로 추정하는 이전 방식도 하루 안에서 틀리기는 마찬가지였고(β 는 장중에
움직인다 - `kbeta` 서문의 042700 실측), 그 회귀를 유지하는 비용이 실재했다 - 표본
게이트(`MIN_BETA_N`)가 층을 조용히 부재로 만들고, 설명 창이 발화마다 달라진 뒤로는
(ALPHA-854) "지난 60일 같은 clock" 이 발화마다 다른 표본을 뜻했다. 시변 β 는
칼만(ALPHA-803)이 세운다 - 그 전까지 두 벌의 추정을 유지하지 않는다.

같은 이유로 **ρ 게이트·유의성 게이트도 없다.** 층이 서는가를 여기서 판정하지 않는다 -
회계만 하고, 판정은 칼만이 신뢰구간과 함께 가져온다.

섹터 후보는 **우리가 고르지 않는다.** 설명력으로 고르면 섹터가 아니라 '가장 잘 맞는
무엇' 이 된다(042700 07-31 삼성전자가 섹터로 뽑힌 실수). 일 모드는 구성종목 최빈
KRX 업종지수, 구간 모드(KRX 지수는 5분봉이 없다)는 구성 겹침 최대 섹터 ETF 다 -
겹침은 포트폴리오 사실이지 적합이 아니다. 동어반복(겹침 ≥ `TAUTOLOGY_CUT`)과
무근거(겹침 < `MIN_OVERLAP`)는 사유와 함께 뺀다.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from functools import lru_cache

TOP_NAMES = 5         # 고유분을 배정할 종목 수
MARKET_CODE = "069500"
TAUTOLOGY_CUT = 0.30  # 이만큼 겹치면 같은 것이다 - 섹터 후보에서 뺀다 (동어반복 금지)
MIN_OVERLAP = 0.05    # 이만큼도 안 겹치면 "왜 이게 설명하냐"에 답이 없다 (우연 적합 금지)

KRX_PREFIX = "KRX:"   # KRX 업종지수 후보의 코드 접두 (겹침 게이트 면제 표식)

# 장 시각 경계 - 이 밖은 5분봉이 없다. 밤사이는 갭 하나로 뭉쳐지므로 구간이 아니다.
SESSION_OPEN, SESSION_CLOSE = "09:00:00", "15:30:00"


# ── 자료구조 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Layer:
    """설명 한 층. **관측 가능한 포트폴리오여야 한다** - 아니면 이름을 못 붙인다."""

    code: str
    name: str
    kind: str            # '시장' | '섹터'
    ret: float           # 오늘 그 층의 원 수익률
    contribution: float  # 시장: ret 그대로 · 섹터: ret − 시장수익 (β=1 차감)
    overlap: float = 0.0  # 설명 대상과의 구성 겹침. 낮아야 동어반복이 아니다

    def __str__(self) -> str:
        return (f"{self.kind} {self.name}({self.code}) {self.ret * 100:+.2f}% "
                f"= {self.contribution * 100:+.2f}%p")


@dataclass(frozen=True, slots=True)
class Name:
    """고유분을 청구하는 종목 하나."""

    ticker: str
    label: str
    weight: float        # ETF 내 비중 (0~1)
    ret: float
    idio: float          # 층 기여 합을 뺀 잔여
    contribution: float  # weight × idio

    def __str__(self) -> str:
        return (f"{self.label}({self.ticker}) 비중 {self.weight * 100:.1f}% · "
                f"수익 {self.ret * 100:+.2f}% · 고유 {self.idio * 100:+.2f}% "
                f"→ 기여 {self.contribution * 100:+.2f}%p")


@dataclass(frozen=True, slots=True)
class Rollup:
    """ETF 구간 변동의 층 회계. **합이 정확히 맞는다** - 고유가 잔여로 정의되므로."""

    etf: str
    etf_name: str
    day: str
    total: float
    layers: tuple[Layer, ...]
    idio: float
    names: tuple[Name, ...]
    rollup_gap: float | None    # Σ wᵢrᵢ − w_covered·total (추적오차). None = 못 쟀다
    n_names: int                # 귀속에 쓴 구성종목 수
    weight_covered: float       # 그 종목들의 비중 합. 1 에서 멀면 귀속이 부분적이다
    twins: tuple[str, ...]      # 겹쳐서 뺀 ETF (동어반복) - 조용히 빼지 않는다
    alien: tuple[str, ...]      # 안 겹쳐서 뺀 ETF (근거 없음)
    halted: int                 # 거래정지로 뺀 종목 수 - 수익률 0 을 참으로 쓰면 거짓

    @property
    def coverage(self) -> float:
        """설명된 비율 = 1 − |남은 것| / |하루|. **음수가 나올 수 있고 그게 정직하다.**

        `|Σ기여| / |하루|` 로 재면 안 된다: 층이 반대 방향으로 과설명해도 비율이
        커져 "덮었다"가 된다(실측 208% - 하루 -1.13%p 인데 시장 기여 +2.35%p,
        남은 건 -3.48%p 로 더 커졌다). 덮었다는 건 **남은 게 작다**는 뜻이다.
        """
        return 0.0 if abs(self.total) < 1e-9 else 1.0 - abs(self.idio) / abs(self.total)


# ── 자료 ──────────────────────────────────────────────────────────────────
@lru_cache(maxsize=64)
def etf_label(lake, etf: str, fallback: str) -> str:
    """ETF 이름은 **`s3_etf_profile` 이 정본**이다 - `layers_daily` 는 못 믿는다.

    실측: `layers_daily` 의 `symbol='091160'` 이름이 'SK hynix Inc.' 다(백필 소스의
    yfinance 오매핑). 엔진이 그걸 사실로 인쇄해 KODEX 반도체를 하이닉스로 불렀다.
    프로필은 발행사 등록명(`display_name`)을 싣는다 - 부재면 폴백을 그대로 쓴다.
    """
    try:
        rows = lake.sql("SELECT any_value(display_name) FROM s3_etf_profile "
                        f"WHERE market = 'KR' AND etf_id = '{etf}'")
    except Exception:                       # noqa: BLE001 - 프로필 부재는 폴백
        return fallback
    return str(rows[0][0]) if rows and rows[0][0] else fallback


# 이름(한글)은 계열과 별개로 한 번 조회한다 - 봉 집계와 조인할 이유가 없다.
_CLOCK_NAMES = ("SELECT symbol, any_value(name) FROM layers_daily "
                "WHERE kind IN {kinds} GROUP BY symbol")

# **요청일 하루만 읽는다.** β 표본이 필요 없어진 뒤로(ALPHA-862) 과거 이력을 읽을
# 이유가 없다 - 이전 판은 `trade_date <= day` 로 표 전 이력을 읽어 런당 실측
# 376.4MB × 최대 6회 ≈ 2.26GB 를 받았고, 그걸 피하려고 Athena 오프로드까지 얹었다.
# 하루 파티션이면 DuckDB 직독으로 충분하다 - 오프로드도 함께 걷어냈다.
_CLOCK_SQL = r"""
WITH k AS (
    SELECT DISTINCT symbol FROM layers_daily WHERE kind IN {kinds}
)
SELECT regexp_replace(b.symbol, '\.(KS|KQ)$', '') AS sym,
       ln(last(b.close ORDER BY b.ts) / first(b.open ORDER BY b.ts)) AS lr,
       sum(b.volume) AS vol
FROM bars_5m b
WHERE b.trade_date = DATE '{day}' AND b.open > 0 AND b.close > 0
  AND CAST(b.ts AS TIME) >= TIME '{t0}' AND CAST(b.ts AS TIME) < TIME '{t1}'{pick}
  AND regexp_replace(b.symbol, '\.(KS|KQ)$', '') IN (SELECT symbol FROM k)
GROUP BY 1
"""


def _absent(lake, key: str, why: str) -> None:
    """부재 사유를 커버리지에 남긴다 - 이 모듈은 로깅을 모르고, 소비는 호출자 몫이다."""
    notes = getattr(lake, "exists", None)
    if notes is not None:
        notes[key] = f"부재: {why}"


def _series(lake, day: str, kinds: tuple[str, ...],
            *, clock: tuple[str, str] | None = None,
            only: str | tuple[str, ...] | None = None) -> dict[str, tuple]:
    """{symbol: (name, 당일 log수익률, 거래정지 여부)}. **당일 한 점**만 낸다.

    `clock=(t0, t1)` 이면 그 시각 구간의 5분봉 시가→종가, 아니면 일봉의 전일 종가
    대비다. β 표본이 사라진 뒤로(ALPHA-862) 과거 이력은 아무도 안 쓴다.

    거래량 0 인 날은 **정지**다 - 그날 종가는 직전 값이고 수익률 0 은 거짓이다.
    진짜 보합의 고유수익은 정보다: 시장·섹터가 −7% 미는데 안 빠졌으면 그게 그
    종목의 힘이다.
    """
    syms = ((only,) if isinstance(only, str) else tuple(only)) if only else ()
    lit = ", ".join(f"'{x}'" for x in syms)
    if clock is not None:
        names = {str(s_): n for s_, n in lake.sql(_CLOCK_NAMES.format(kinds=kinds))}
        # 5분봉 심볼은 **접미사가 붙는다**(`005930.KS`). 맨 코드로 비교하면 한 번도 안
        # 맞고, 그러면 대상 계열이 비어 `decompose` 가 조용히 None 을 낸다.
        pick = (f" AND regexp_replace(b.symbol, '\\.(KS|KQ)$', '') IN ({lit})"
                if syms else "")
        rows = lake.sql(_CLOCK_SQL.format(
            kinds=kinds, day=day, t0=clock[0], t1=clock[1], pick=pick))
        lake.exists["clock_panel"] = f"DuckDB 당일 직독 ({len(rows)}심볼)"
        return {sym: (names.get(sym, sym), float(lr),
                      vol is not None and float(vol) <= 0)
                for sym, lr, vol in rows if lr is not None}
    pick = f" AND symbol IN ({lit})" if syms else ""
    d0 = dt.date.fromisoformat(day)
    rows = lake.sql(
        "SELECT symbol, any_value(name), list(close ORDER BY date), "
        "       list(CAST(date AS DATE) ORDER BY date), list(volume ORDER BY date) "
        f"FROM layers_daily WHERE kind IN {kinds} AND date <= DATE '{day}'{pick} "
        "GROUP BY symbol")
    out = {}
    for sym, nm, closes, dates, vols in rows:
        # 당일 수익률에는 마지막 두 값이면 된다(분모 = 직전 종가). 당일 행이 없으면
        # 그 계열은 오늘을 모른다 - 어제 값으로 지어내지 않는다.
        if len(closes) < 2 or dates[-1] != d0:
            continue
        c_now, c_prev = float(closes[-1]), float(closes[-2])
        if not (c_now > 0 and c_prev > 0):
            continue
        v_now = vols[-1] if vols else None
        halted = v_now is not None and float(v_now) <= 0
        out[sym] = (nm, math.log(c_now / c_prev), halted)
    return out


def _krx_sector_candidate(lake, etf: str, day: str):
    """ETF 구성종목의 **최빈 KRX 업종**지수를 섹터 후보로. (코드, 이름, 당일 log수익률).

    업종을 우리가 고르지 않는다: 구성종목 다수가 속한 업종이 그 ETF 의 업종이다.
    지수 자체도 KRX 가 산출한다 - 설명력으로 고르면 섹터가 아니라 '가장 잘 맞는
    무엇' 이 된다(042700 07-31 에서 삼성전자가 섹터로 뽑힌 그 실수).
    """
    # 보유는 `holdings()` 를 쓴다 - KRX 우선·FMP 폴백이 이미 그 안에 있고 캐시된다.
    hold = holdings(lake, etf, day)
    tks = {t.split(".")[0][-6:] for t, _n, _w in hold}
    if not tks:
        return None
    try:
        # 당일 수익률 분모까지 마지막 2행이면 된다(β 표본 폐지, ALPHA-862).
        rows = lake.sql(f"""
            WITH latest AS (
              SELECT ticker, code FROM (
                SELECT ticker, code, row_number() OVER (
                  PARTITION BY ticker ORDER BY as_of DESC) AS rn
                FROM sector_member
                WHERE as_of <= DATE '{day}'
                  AND ticker IN ({", ".join(f"'{t}'" for t in sorted(tks))})
              ) WHERE rn = 1
            ), m AS (
              SELECT code, count(*) n FROM latest
              GROUP BY 1 ORDER BY 2 DESC LIMIT 1
            )
            SELECT si.trade_date, any_value(si.close), any_value((SELECT code FROM m))
            FROM sector_index si WHERE si.code = (SELECT code FROM m)
              AND si.trade_date <= DATE '{day}'
            GROUP BY si.trade_date ORDER BY 1 DESC LIMIT 2""")
    except Exception:                               # noqa: BLE001 - 부재는 후보 없음
        return None
    if len(rows) < 2 or rows[0][2] is None:
        return None
    (d_now, c_now, code), (_d_prev, c_prev, _c) = rows[0], rows[1]
    if str(d_now) != day or not (c_now and c_prev and float(c_prev) > 0):
        return None
    from .krxsector import sector_name
    return (KRX_PREFIX + str(code), f"{sector_name(str(code))}(KRX 업종)",
            math.log(float(c_now) / float(c_prev)))


# ── 층 회계 ───────────────────────────────────────────────────────────────
def decompose(lake, etf: str, day: str, *, top: int = TOP_NAMES,
              clock: tuple[str, str] | None = None,
              intraday: dict[str, tuple[float, bool]] | None = None) -> Rollup | None:
    """ETF 구간을 시장·섹터·고유로 가른다. **회계이지 추정이 아니다(β=1, ALPHA-862).**

    설명 대상은 **ETF 자신의 수익률**이다 (관측 가능). 구성종목 가중합과의 차이는
    `rollup_gap` 으로 따로 보고한다 - 추적오차와 비중 노후를 숨기지 않는다.

    `clock=(t0, t1)` 이면 설명 대상이 **그 구간의 수익률**이 된다. 구간 모드의 섹터
    층은 **KR 섹터 ETF** 다(KRX 업종지수는 5분봉이 없다 - 실측 0건). 후보 선정은
    구성 겹침 최대이지 적합이 아니다.

    `intraday` 는 `{맨코드: (구간 log수익률, 정지여부)}` — 호출자가 **커밋된 1분봉**
    에서 같은 clock 구간으로 계산한 값이다(ALPHA-866). 있는 심볼은 레이크 `bars_5m`
    대신 이 값이 선다: 라우팅은 방금 발화를 만든 바로 그 봉으로 판정해야 하고, 그래야
    정본(Iceberg) 스테일 폴백이 시장·대상·종목 축에 낄 자리가 없다. 섹터 후보는 수집
    축이 달라(참조 계열, 1분 수집 밖) 여전히 레이크를 본다 — 후속 PR 의 몫이다.
    """
    # 이번 호출의 판정으로 덮는다. 한 런이 `decompose` 를 두 번 부르므로(라우팅·설명)
    # 앞 호출의 실패가 남으면 뒤 호출이 성공해도 커버리지가 실패를 말한다 — 부재를
    # 안 지우는 것은 부재를 지어내는 것과 같다.
    notes = getattr(lake, "exists", None)
    if notes is not None:
        notes.pop("layers", None)
        notes.pop("market_layer", None)
    ser = _series(lake, day, ("market", "sector"), clock=clock)
    # 1분봉 실측이 레이크 값을 **덮는다** - 이름은 레이크 것을 지키고(수익률만 갈아
    # 끼운다), 레이크에 아예 없는 심볼(스테일 정본)은 코드를 이름 삼아 세운다.
    # 시장 층이 "레이크가 낡아서" 빠지는 일이 없어야 한다.
    #
    # **대상·시장만.** `intraday` 에는 구성종목도 실려 오는데(`_names` 몫), 여기서
    # 전부 덮으면 종목이 섹터 후보 풀(`ser`)에 들어가 '삼성전자가 섹터로 뽑히는'
    # 그 실수로 돌아간다 - 후보 풀은 건드리지 않는다.
    for sym in (etf, MARKET_CODE):
        if intraday and sym in intraday:
            lr, halt = intraday[sym]
            ser[sym] = (ser[sym][0] if sym in ser else sym, lr, halt)
    # 대상이 ETF 가 아니면(개별 종목) **대상만** 주입한다. `kinds` 에 'stock' 을 넣으면
    # 856 종목이 섹터 후보가 되어 겹침 게이트가 종목마다 질의를 돌고, 무엇보다
    # '삼성전자가 섹터로 뽑히는' 그 실수로 돌아간다 - 후보 풀은 건드리지 않는다.
    if etf not in ser:
        tgt = _series(lake, day, ("stock",), clock=clock, only=etf)
        if etf in tgt:
            ser = {**ser, etf: tgt[etf]}
    # **부재를 침묵으로 남기지 않는다.** 재료가 없는 것은 적재 일감이고, 당일이 없는
    # 것은 신선도 문제다 - 처방이 다르므로 사유를 갈라 적는다.
    if etf not in ser:
        # 계열이 아예 없는 것(적재 일감)과 당일 행이 없는 것(신선도)은 `_series` 가
        # 같은 부재로 접는다 - 당일 한 점만 내는 계약이라 여기선 못 가른다. 사유에
        # 둘 다 적어 운영이 원천을 직접 본다.
        _absent(lake, "layers", f"대상 계열 부재 또는 당일 없음 ({etf} {day})")
        return None
    _etf_nm, y_now, _etf_halt = ser[etf]

    layers: list[Layer] = []
    market_now = 0.0
    if MARKET_CODE in ser and etf != MARKET_CODE:
        _nm, m_now, _h = ser[MARKET_CODE]
        market_now = m_now
        layers.append(Layer(MARKET_CODE, ser[MARKET_CODE][0], "시장", m_now, m_now))
    elif etf != MARKET_CODE:
        # **시장 층은 조용히 빠지면 안 된다** - 시장 층이 빠진 런과 시장 기여가 0 인
        # 런이 산출물에서 구분되지 않으면 남은 섹터·고유가 시장 몫까지 떠안는다.
        # **대상이 시장 프록시 자신이면 사유를 적지 않는다** - 그건 부재가 아니라
        # 정상이고, `route.py` 가 `Route("시장", 1.0, …)` 로 정식 처리한다(069500 07-29).
        _absent(lake, "market_layer", f"시장 층 없음 - {MARKET_CODE} 당일 계열 부재")

    # ── 섹터 후보. 자격은 **구성 겹침**이 정한다 - 조용히 빼지 않고 사유를 남긴다.
    #   위: 겹치면 같은 것이다 - "반도체가 왜 빠졌냐"에 "반도체가 빠져서"는 설명이 아니다.
    #   아래: 안 겹치면 근거가 없다 - 게임 ETF 가 2차전지를 "설명"하는 일이 실제로
    #   일어났다. 산술은 맞지만 아무도 안 믿는다.
    twins, alien = set(), set()
    sector = None       # (code, name, ret, overlap)
    meta = {k: v[0] for k, v in ser.items()}
    # KRX 업종지수가 있으면 **그것만** 쓴다 - 지수는 KRX 가 산출하고 업종은 구성종목
    # 최빈이 정하므로 어느 쪽도 우리 선택이 아니다. 구간 모드는 KRX 5분봉이 없어
    # 섹터 ETF 로 간다.
    krx = None if clock is not None else _krx_sector_candidate(lake, etf, day)
    if krx is not None:
        code, nm, s_now = krx
        meta[code] = nm
        sector = (code, nm, s_now, 0.0)
    else:
        best_ov = 0.0
        for s_, (nm, s_now, s_halt) in ser.items():
            if s_ in (etf, MARKET_CODE) or s_halt:
                continue
            ov = overlap(lake, etf, s_, day)
            if ov >= TAUTOLOGY_CUT:
                twins.add(s_)
            elif ov < MIN_OVERLAP:
                alien.add(s_)
            elif ov > best_ov:
                # 겹침 최대 = 그 ETF 의 업종에 가장 가깝다. 포트폴리오 사실이지
                # 적합이 아니다 - 적합으로 고르면 섹터가 아니라 '가장 잘 맞는
                # 무엇'이 된다(ALPHA-862 에서 회귀 선택을 걷어낸 이유와 같다).
                sector, best_ov = (s_, nm, s_now, ov), ov
    # **시장 층이 섰을 때만** 섹터를 세운다. 차감 기준이 없으면 섹터가 시장 몫까지
    # 전액 청구하고, 산문은 "(시장 차감)" 이라 적는다 - 일어나지 않은 차감을 말하는
    # 거짓이다. 대상이 시장 프록시 자신일 때도 같은 이유로 섹터를 안 세운다: 그
    # 경우는 route 가 "시장 100%" 로 정식 처리하는 정상 경로다(069500 07-29).
    if sector is not None and layers:
        code, nm, s_now, ov = sector
        # β=1 시장 차감 - 시장과 겹치는 몫을 빼야 "시장이 민 건지 섹터가 민 건지"
        # 배분 순서 논쟁이 없다. 회귀 직교화의 단위계수판이다.
        layers.append(Layer(code, nm, "섹터", s_now, s_now - market_now, ov))

    idio = y_now - sum(x.contribution for x in layers)
    names, wsum, wtot, halted = _names(lake, etf, day, layers, top, clock=clock,
                                       intraday=intraday)
    return Rollup(etf, etf_label(lake, etf, meta.get(etf, etf)), day, y_now,
                  tuple(layers), idio, names,
                  None if wsum is None else wsum - y_now * wtot, len(names), wtot,
                  tuple(sorted(meta.get(t, t) for t in twins)),
                  tuple(sorted(meta.get(t, t) for t in alien)), halted)


# ── 종목 귀속 ─────────────────────────────────────────────────────────────
def _names(lake, etf: str, day: str, layers: list[Layer], top: int,
           clock: tuple[str, str] | None,
           intraday: dict[str, tuple[float, bool]] | None = None,
           ) -> tuple[tuple[Name, ...], float | None, float, int]:
    """고유분을 청구하는 상위 종목. **비중 × 고유** 로 순위 - 큰 종목의 작은 움직임과
    작은 종목의 큰 움직임을 같은 자로 잰다.

    종목 고유 = 종목수익 − Σ 층 기여 (β=1). 회귀가 없으므로 과거 이력 정렬도 없다 -
    당일 값만 있으면 종목이 귀속에 든다(이전 판은 이력 결손 하나로 종목이 조용히
    빠져 n_names=0 이 나는 실측이 있었다).

    **`clock` 을 그대로 받아 넘긴다.** 구간 모드에서 이걸 빠뜨리면 설명 대상은 구간
    수익률인데 종목만 일봉 수익률이 된다 - 값이 나와도 같은 자로 잰 게 아니다.
    """
    hold = holdings(lake, etf, day)
    if not hold or not layers:
        return (), None, 0.0, 0
    # 여기도 `hold` 만 쓴다 - 전량을 읽고 루프에서 버리면 856종목 스캔이 공짜가 아니다.
    ser = _series(lake, day, ("stock",), only=tuple(tk for tk, _l, _w in hold),
                  clock=clock)
    # 커밋된 1분봉 실측이 레이크를 덮는다(ALPHA-866) - 이름은 `hold` 의 label 이
    # 정본이라 수익률·정지 여부만 쓴다. 보유 밖 심볼(시장 프록시 등)은 안 세운다:
    # 여기는 구성종목 귀속이지 계열 주입 자리가 아니다.
    if intraday:
        held = {tk for tk, _l, _w in hold}
        for sym, (lr, halt) in intraday.items():
            if sym in held:
                ser[sym] = (ser[sym][0] if sym in ser else sym, lr, halt)
    base = sum(x.contribution for x in layers)
    out, wsum, wtot, halted = [], 0.0, 0.0, 0
    for tk, label, w in hold:
        if tk not in ser:
            continue
        _nm, r_now, halt = ser[tk]
        if halt:
            halted += 1
            continue
        e = r_now - base
        out.append(Name(tk, label, w, r_now, e, w * e))
        wsum += w * r_now
        wtot += w
    if not out:
        return (), None, 0.0, halted
    out.sort(key=lambda n: -abs(n.contribution))
    return tuple(out[:top]), wsum, wtot, halted


@lru_cache(maxsize=8192)
def holdings(lake, etf: str, day: str) -> list[tuple[str, str, float]]:
    """[(ticker, name, weight)] - **as_of ≤ day** 최신 스냅샷만 (선견 금지).

    **KRX 우선 · FMP 폴백.** KRX 는 신선하지만(주 단위) KRX 사이트가 죽어 34종에서
    멈췄다. FMP 는 53종을 주되 6개월 낡았다 — 겹침 게이트는 ≥0.30/<0.05 의 거친
    임계라 표류를 견디고, 낡은 것은 **선견이 아니다**(PIT 안전). 비중이 정밀해야
    하는 종목 귀속은 KRX 가 있으면 그것을 쓴다.

    캐시 필수: `overlap()` 이 후보 ETF 마다 두 번 부른다. 셀 하나에 수십 질의,
    배치 736셀이면 수만 질의가 되어 측정 자체가 불가능해진다(실측 - 취소했다).
    같은 (ETF, 날짜) 보유는 안 변하므로 캐시가 정답을 안 바꾼다.
    """
    # **첫 질의가 예외로 죽으면 폴백에 못 간다.** 실측(CI e2e): S3 자격증명이 없는
    # 환경에서 `s3_etf_holdings` 뷰가 미등록이라 `CatalogException` 이 났고, 바로 아래
    # 백필 폴백(`etf_holdings_fmp`)은 한 번도 실행되지 않았다 - 폴백을 써 놓고 도달
    # 불가로 둔 것이다. 표 부재는 사유이지 예외가 아니다.
    rows: list = []
    try:
        rows = lake.sql(
            f"SELECT constituent_ticker, any_value(constituent_name), "
            f"       any_value(weight_pct) "
            f"FROM s3_etf_holdings WHERE market = 'KR' AND etf_id = '{etf}' "
            f"  AND as_of_date = (SELECT max(as_of_date) FROM s3_etf_holdings "
            f"                    WHERE market = 'KR' AND etf_id = '{etf}' "
            f"                      AND as_of_date <= DATE '{day}') "
            f"GROUP BY 1")
    except Exception:                                      # noqa: BLE001 - 뷰 없음
        rows = []
    if not rows:
        try:
            rows = lake.sql(
                f"SELECT constituent_ticker, any_value(constituent_name), "
                f"       any_value(weight_pct) FROM etf_holdings_fmp "
                f"WHERE etf_id = '{etf}' "
                f"  AND CAST(as_of AS DATE) = (SELECT max(CAST(as_of AS DATE)) "
                f"       FROM etf_holdings_fmp WHERE etf_id = '{etf}' "
                f"         AND CAST(as_of AS DATE) <= DATE '{day}') "
                f"GROUP BY 1")
        except Exception:                                  # noqa: BLE001 - 뷰 없음
            return []
    return [(t, n or t, float(w) / 100.0) for t, n, w in rows if w is not None]


@lru_cache(maxsize=32768)
def overlap(lake, a: str, b: str, day: str) -> float:
    """두 ETF 의 비중 중첩 Σ min(wᵢᵃ, wᵢᵇ). 1 이면 같은 포트폴리오다.

    **동어반복을 막는 자다**: KODEX 반도체를 TIGER 반도체로 설명하면 "반도체가 왜
    빠졌냐"에 "반도체가 빠져서"라고 답하는 꼴이다. 산술은 맞지만 설명이 아니다.
    """
    wa = {t: w for t, _n, w in holdings(lake, a, day)}
    wb = {t: w for t, _n, w in holdings(lake, b, day)}
    return sum(min(wa[t], wb[t]) for t in wa.keys() & wb.keys())


__all__ = ["MARKET_CODE", "MIN_OVERLAP", "TAUTOLOGY_CUT", "TOP_NAMES",
           "Layer", "Name", "Rollup", "decompose", "holdings", "overlap"]
