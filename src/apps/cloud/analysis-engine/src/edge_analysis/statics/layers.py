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

섹터 후보를 **설명력으로 고르면 안 된다.** 그러면 섹터가 아니라 '가장 잘 맞는 무엇' 이
된다(042700 07-31 삼성전자가 섹터로 뽑힌 실수). 섹터의 최종형은 **KRX 업종지수 하나다**
(ALPHA-877) - 지수는 KRX 가 산출하고 업종은 구성종목 최빈이 정하므로 어느 쪽도 우리
선택이 아니다.

⚠️ **그 최종형을 아직 못 쓴다 — 다만 "업종지수는 분봉이 없어서"가 아니다.** ALPHA-887 이
45종 1분봉을 dataset `sector_index_minute` 으로 수집한다. **원천이 아니라 배선이 없다**,
그리고 배선할 자리가 둘이다 - ① 레이크에서 읽는 것은 `bars_5m` 이라 5분 롤업이 있어야
하고, ② 운영 런(`pipeline`)은 레이크가 아니라 **커밋된 1분봉**을 주입하므로 그 주입
집합(`pipeline` 의 `needed`)에 지수가 들어가야 한다. ①만 놓으면 ②가 남는다 - 파이프라인이
그 자리를 "참조 계열은 분석 unit 밖 - 후속 PR" 로 적어 뒀다.

⚠️ **소급은 안 된다** - 소급 TR 이 일봉으로 degrade 한다. 다만 그것이 막는 것은 레인
가동 **이전 날짜의 재설명**이지 당일 운영이 아니다(층 배선이 요구하는 과거는 직전
거래일 하나다 — `_market_beta`).

섹터 후보는 겹침으로 고른 섹터 ETF 다(`select_sector` — ALPHA-877 이 걷었던 프록시
선택을 #659 가 되살렸다). 겹침은 설명력이 아니라 구성 중복이라 위 금지에 걸리지 않지만,
최종형이 아닌 것은 그대로다. 선택은 **층이 서는 필요조건일 뿐이다** - 섹터 기여가 안
나오면(정렬된 경로 부재) 층은 안 서고 사유가 커버리지에 남는다. 후보가 없을 때도 같다.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

TOP_NAMES = 5         # 고유분을 배정할 종목 수
MARKET_CODE = "069500"
TAUTOLOGY_CUT = 0.30  # 이만큼 겹치면 같은 것이다 - 겹침 판정의 계약 임계 (동어반복 금지)
MIN_OVERLAP = 0.05    # 이만큼도 안 겹치면 "왜 이게 설명하냐"에 답이 없다 (우연 적합 금지)
# 대상이 시장과 이만큼 겹치면 광역(broad) ETF 다 - 섹터 층을 접고 시장+고유 2층으로
# 간다(ALPHA-871). 시장의 부분집합에 '섹터'를 세우면 시장 몫을 두 번 나눠 갖는
# 동어반복이고, 그때 섹터가 청구하는 차감은 산술은 맞아도 아무것도 설명하지 않는다.
BROAD_MARKET_CUT = 0.80

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
class SectorSelection:
    code: str | None
    overlap: float
    twins: tuple[str, ...]
    alien: tuple[str, ...]
    broad: bool
    reason: str


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
    # 등락 폭(breadth) - **측정된 전 구성종목** 기준(ALPHA-876). top-N 으로 자른
    # `names` 로 세면 "구성종목 5종목 중" 같은 거짓 분모가 산문에 나간다(실측 08-08).
    advancers: int = 0
    decliners: int = 0
    # 시변 β (칼만, ALPHA-803 2단계) - 구간/커밋 봉 모드에서 시장 기여가
    # Σ β_t·r_m,t 로 섰을 때만 채워진다. 비면 β=1 회계이고, 그 폴백의 사유는
    # `lake.exists["market_beta"]` 에 남는다(조용한 폴백 금지, Rule 12).
    beta_quarters: tuple[float, ...] = ()   # β_t 경로 4분할 평균
    beta_ci: float | None = None            # 시장 기여 신뢰폭 (±, 수익률 단위)

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


def select_sector(lake, etf: str, day: str,
                  available: tuple[str, ...] | list[str] | set[str]) -> SectorSelection:
    """Select one collected sector ETF using only holdings overlap contracts."""
    if etf != MARKET_CODE and overlap(lake, etf, MARKET_CODE, day) >= BROAD_MARKET_CUT:
        return SectorSelection(None, 0.0, (), (), True,
                               f"시장과 구성 겹침 ≥{BROAD_MARKET_CUT:.0%}")
    try:
        sector_codes = {str(code) for code, _name in
                        lake.sql(_CLOCK_NAMES.format(kinds=("sector",)))}
    except Exception:  # noqa: BLE001 - missing candidate catalog is an explicit absence
        sector_codes = set()
    candidates = sorted((sector_codes & set(available)) - {etf, MARKET_CODE})
    scored = [(code, overlap(lake, etf, code, day)) for code in candidates]
    twins = tuple(code for code, score in scored if score >= TAUTOLOGY_CUT)
    alien = tuple(code for code, score in scored if score < MIN_OVERLAP)
    valid = [(code, score) for code, score in scored
             if MIN_OVERLAP <= score < TAUTOLOGY_CUT]
    if not valid:
        return SectorSelection(None, 0.0, twins, alien, False,
                               "겹침 5% 이상 30% 미만 섹터 ETF 없음")
    code, score = max(valid, key=lambda item: (item[1], item[0]))
    return SectorSelection(code, score, twins, alien, False, "선정")


def _series(lake, day: str, kinds: tuple[str, ...],
            *, clock: tuple[str, str],
            only: str | tuple[str, ...] | None = None) -> dict[str, tuple]:
    """{symbol: (name, 구간 log수익률, 거래정지 여부)}."""
    syms = ((only,) if isinstance(only, str) else tuple(only)) if only else ()
    lit = ", ".join(f"'{x}'" for x in syms)
    names = {str(s_): n for s_, n in lake.sql(_CLOCK_NAMES.format(kinds=kinds))}
    # 5분봉 심볼은 접미사가 붙는다(`005930.KS`). 맨 코드로 정규화한다.
    pick = (f" AND regexp_replace(b.symbol, '\\.(KS|KQ)$', '') IN ({lit})"
            if syms else "")
    rows = lake.sql(_CLOCK_SQL.format(
        kinds=kinds, day=day, t0=clock[0], t1=clock[1], pick=pick))
    notes = getattr(lake, "exists", None)
    if notes is not None:
        notes["clock_panel"] = f"DuckDB 당일 직독 ({len(rows)}심볼)"
    return {sym: (names.get(sym, sym), float(lr),
                  vol is not None and float(vol) <= 0)
            for sym, lr, vol in rows if lr is not None}

def _market_beta(lake, etf: str, day: str, paths: dict | None,
                 sector: str | None = None,
                 ) -> tuple[float, float | None, tuple[float, ...], float] | None:
    """칼만 시변 β 시장 기여 (Σ β_t·r_m,t) - 구간/커밋 봉 모드 전용(ALPHA-803 2단계).

    Q·R·β0 은 전부 **전일 5분봉**(레이크 `bars_5m` 직전 파티션)에서 온다(선견 금지,
    `kbeta` 서문의 규율 그대로 - 매일 전일 하루로 재피팅하는 롤링). 어떤 이유로든
    못 서면 None 을 돌려주고 사유를 `lake.exists["market_beta"]` 에 "시장 층 β=1
    폴백 - …" 로 남긴다 - β=1 로 접는 것 자체는 정당하지만 조용히 접으면 시변 β 가
    선 런과 구분되지 않는다(Rule 12).
    """
    def fall(why: str) -> None:
        notes = getattr(lake, "exists", None)
        if notes is not None:
            notes["market_beta"] = f"시장 층 β=1 폴백 - {why}"

    if not paths or etf not in paths or MARKET_CODE not in paths:
        fall("구간 봉 수익 계열 미제공")
        return None
    try:
        symbols = [etf, MARKET_CODE] + ([sector] if sector else [])
        symbol_sql = ", ".join(f"'{symbol}'" for symbol in symbols)
        rows = lake.sql(rf"""
            SELECT regexp_replace(symbol, '\.(KS|KQ)$', '') AS sym, ts, close
            FROM bars_5m
            WHERE trade_date = (SELECT max(trade_date) FROM bars_5m
                                WHERE trade_date < DATE '{day}')
              AND close > 0
              AND regexp_replace(symbol, '\.(KS|KQ)$', '')
                  IN ({symbol_sql})
            ORDER BY ts""")
    except Exception as e:              # noqa: BLE001 - 표 부재·질의 실패는 폴백 사유
        fall(f"전일 5분봉 질의 실패: {type(e).__name__}")
        return None
    closes: dict[str, dict] = {}
    for sym, ts, close in rows:
        closes.setdefault(str(sym), {})[ts] = float(close)
    s_map = closes.get(etf, {})
    m_map = closes.get(MARKET_CODE, {})
    common = sorted(s_map.keys() & m_map.keys())
    if len(common) < 2:
        fall(f"전일 5분봉 부재 (대상·시장 정렬 {len(common)}봉)")
        return None
    import numpy as np
    from .kbeta import wired_beta, wired_beta2
    if sector and sector in paths and sector in closes:
        sec_map = closes[sector]
        common3 = sorted(s_map.keys() & m_map.keys() & sec_map.keys())
        if len(common3) >= 2:
            two = wired_beta2(
                np.array([s_map[t] for t in common3]),
                np.array([m_map[t] for t in common3]),
                np.array([sec_map[t] for t in common3]),
                paths[etf], paths[MARKET_CODE], paths[sector],
            )
            if two.get("verdict") == "성립":
                quarters = tuple(float(np.mean(chunk)) for chunk in
                                 np.array_split(two["beta_m"], 4) if len(chunk))
                return (float(two["market_contribution"]),
                        float(two["sector_contribution"]), quarters,
                        float(two["market_ci"]))
            _absent(lake, "sector_layer", f"2요인 칼만 폴백 - {two.get('reason', '?')}")
    res = wired_beta(np.array([s_map[t] for t in common]),
                     np.array([m_map[t] for t in common]),
                     paths[etf], paths[MARKET_CODE])
    if res.get("verdict") != "성립":
        fall(str(res.get("reason", "?")))
        return None
    quarters = tuple(float(np.mean(chunk))
                     for chunk in np.array_split(res["beta"], 4) if len(chunk))
    return float(res["contribution"]), None, quarters, float(res["ci"])


# ── 층 회계 ───────────────────────────────────────────────────────────────
def decompose(lake, etf: str, day: str, *, clock: tuple[str, str],
              top: int = TOP_NAMES,
              intraday: dict[str, tuple[float, bool]] | None = None,
              paths: dict[str, tuple[float, ...]] | None = None) -> Rollup | None:
    """ETF 구간을 시장·섹터·고유로 가른다. **회계이지 추정이 아니다(β=1, ALPHA-862).**

    설명 대상은 **ETF 자신의 수익률**이다 (관측 가능). 구성종목 가중합과의 차이는
    `rollup_gap` 으로 따로 보고한다 - 추적오차와 비중 노후를 숨기지 않는다.

    `clock=(t0, t1)` 이면 설명 대상이 **그 구간의 수익률**이 된다. 섹터 층은 수집된
    섹터 ETF 중 겹침으로 고른 하나로 선다(`select_sector`) - 다만 선택은 필요조건일
    뿐이고, 섹터 기여가 안 나오면 층은 안 선다. 후보가 없거나 정렬된 경로가 없으면
    사유를 커버리지에 남기고 시장+고유 2층으로 선다. ⚠️ **후보 집합을 호출자가 정한다**
    - `select_sector` 는 `intraday` 로 주입된 심볼에서만 고르므로, 운영 런처럼 대상·
    시장·구성종목만 주입하면 그 안에 든 섹터 ETF 만 후보다. 정본인 KRX 업종지수는
    1분봉을 수집해도(ALPHA-887) 아직 그 주입 집합 밖이다(모듈 도크스트링).

    `intraday` 는 `{맨코드: (구간 log수익률, 정지여부)}` — 호출자가 **커밋된 1분봉**
    에서 같은 clock 구간으로 계산한 값이다(ALPHA-866). 있는 심볼은 레이크 `bars_5m`
    대신 이 값이 선다: 라우팅은 방금 발화를 만든 바로 그 봉으로 판정해야 하고, 그래야
    정본(Iceberg) 스테일 폴백이 시장·대상·종목 축에 낄 자리가 없다.

    `paths` 는 `{맨코드: 구간 봉단위 log수익 경로}` - 구간/커밋 봉 모드에서 시장 층을
    시변 β 로 세우는 재료다(ALPHA-803 2단계). 대상·시장 두 경로가 서고 전일 5분봉이
    있으면 시장 기여가 β=1 의 r_m 대신 **Σ β_t·r_m,t (경로 적분)** 이 된다. 고유는
    잔여 정의라 항등식(층 합 + 고유 = 구간수익)은 그대로다. 못 서면 β=1 로 접되
    사유를 남긴다(`_market_beta`).
    """
    # 이번 호출의 판정으로 덮는다. 한 런이 `decompose` 를 두 번 부르므로(라우팅·설명)
    # 앞 호출의 실패가 남으면 뒤 호출이 성공해도 커버리지가 실패를 말한다 — 부재를
    # 안 지우는 것은 부재를 지어내는 것과 같다.
    notes = getattr(lake, "exists", None)
    if notes is not None:
        notes.pop("layers", None)
        notes.pop("market_layer", None)
        notes.pop("sector_layer", None)
        notes.pop("market_beta", None)
    # 대상·시장 두 심볼만 읽는다 - 섹터 ETF 후보 풀이 사라진 뒤로(ALPHA-877) 전
    # 계열을 읽을 이유가 없다. 대상 ETF 자신이 layers_daily 에 'sector' kind 로
    # 실려 있어 kinds 는 그대로 둔다.
    ser = _series(lake, day, ("market", "sector"), clock=clock)
    # 1분봉 실측이 레이크 값을 **덮는다** - 이름은 레이크 것을 지키고(수익률만 갈아
    # 끼운다), 레이크에 아예 없는 심볼(스테일 정본)은 코드를 이름 삼아 세운다.
    # 시장 층이 "레이크가 낡아서" 빠지는 일이 없어야 한다.
    #
    # **대상·시장만.** `intraday` 에는 구성종목도 실려 오는데 그건 `_names` 몫이다 -
    # `ser` 는 대상·시장 두 심볼의 자리이지 계열 주입 자리가 아니다.
    #
    # 반대 방향도 계약이다: **커밋 봉 모드(intraday is not None)에서 대상·시장이
    # 결손이면 레이크로 내려가지 않는다.** 내려가면 이 배선이 닫은 갈림 - 발화를
    # 만든 봉과 판정이 다른 가격을 보는 날 - 이 결손일마다 조용히 부활하고, 원장
    # 어디에도 "시장 층이 레이크에서 왔다"는 표식이 없다(Rule 12). 결손은 부재
    # 사유로 남는 것이 정직하다 - 원장의 dropped_units 와 아귀가 맞는다.
    if intraday is not None:
        for sym in (etf, MARKET_CODE):
            if sym in intraday:
                lr, halt = intraday[sym]
                ser[sym] = (ser[sym][0] if sym in ser else sym, lr, halt)
            else:
                ser.pop(sym, None)
    selection = select_sector(
        lake, etf, day, tuple(intraday) if intraday is not None else tuple(ser))
    if selection.code and intraday is not None and selection.code in intraday:
        lr, halt = intraday[selection.code]
        ser[selection.code] = (
            ser[selection.code][0] if selection.code in ser else selection.code,
            lr,
            halt,
        )
    # 대상이 ETF 가 아니면(개별 종목) **대상만** 주입한다 - `kinds` 에 'stock' 을
    # 넣어 856 종목을 읽을 이유가 없다. 커밋 봉 모드에서는 이 폴백도 닫는다
    # (위와 같은 이유 - 대상은 intraday 가 정본).
    if etf not in ser and intraday is None:
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
        _absent(lake, "market_layer",
                (f"시장 층 없음 - {MARKET_CODE} 커밋 1분봉 결손 (레이크 폴백 금지)"
                 if intraday is not None
                 else f"시장 층 없음 - {MARKET_CODE} 당일 계열 부재"))

    # ── 시변 β (칼만, ALPHA-803 2단계) - 구간/커밋 봉 모드의 시장 층만 ─────────
    # 시장 기여를 β=1 의 r_m 에서 Σ β_t·r_m,t (경로 적분)로 바꾼다. 섹터 차감
    # 기준(market_now)은 그대로다 - 섹터 층은 877 상태(β=1 차감·구간 모드 정직
    # 부재)를 유지한다. 일 모드(clock·intraday 둘 다 없음)는 닿지 않는다.
    beta_quarters: tuple[float, ...] = ()
    beta_ci: float | None = None
    sector_contribution: float | None = None
    if layers:
        wired = _market_beta(lake, etf, day, paths, selection.code)
        if wired is not None:
            contrib, sector_contribution, beta_quarters, beta_ci = wired
            layers[0] = Layer(MARKET_CODE, layers[0].name, "시장",
                              market_now, contrib)
    if selection.code and sector_contribution is not None:
        _name, sector_now, _halt = ser[selection.code]
        layers.append(Layer(selection.code, _name, "섹터", sector_now,
                            sector_contribution, selection.overlap))

    meta = {k: v[0] for k, v in ser.items()}
    # **광역 ETF 는 섹터 층을 접는다**(ALPHA-871). 시장과 ≥BROAD_MARKET_CUT 겹치는
    # 대상은 시장의 부분집합이라 섹터가 시장 몫을 두 번 나눠 갖는 동어반복이 된다 -
    # 시장+고유 2층으로 가고, 접었다는 사실은 커버리지에 남긴다(조용한 생략 금지).
    broad = bool(layers and selection.broad)
    if broad and notes is not None:
        notes["sector_layer"] = (
            f"생략: 시장과 구성 겹침 ≥{BROAD_MARKET_CUT:.0%} - 시장+고유 2층")
    # ── 섹터 후보는 **KRX 업종지수뿐**이다(ALPHA-877) - 프록시 ETF 겹침 선택은
    # 폐기했다. **시장 층이 섰을 때만** 세운다: 차감 기준이 없으면 섹터가 시장 몫까지
    # 전액 청구하고, 산문은 "(시장 차감)" 이라 적는다 - 일어나지 않은 차감을 말하는
    # 거짓이다. 대상이 시장 프록시 자신일 때도 같은 이유로 안 세운다: 그 경우는
    # route 가 "시장 100%" 로 정식 처리하는 정상 경로다(069500 07-29).
    if layers and not broad and selection.code is None:
        _absent(lake, "sector_layer", selection.reason)
    elif (layers and selection.code is not None and sector_contribution is None
          and notes is not None and "sector_layer" not in notes):
        _absent(lake, "sector_layer", "선정 섹터 ETF의 정렬된 칼만 경로 부재")
    idio = y_now - sum(x.contribution for x in layers)
    names, wsum, wtot, halted, adv, dec = _names(lake, etf, day, layers, top,
                                                 clock=clock, intraday=intraday)
    # twins/alien 은 프록시 ETF 후보 풀과 함께 사라졌다(ALPHA-877) - 스키마(필드)는
    # 남긴다: 소비자가 있고, 빈 튜플이 "겹침 판정으로 뺀 것이 없다"는 사실 그대로다.
    return Rollup(etf, etf_label(lake, etf, meta.get(etf, etf)), day, y_now,
                  tuple(layers), idio, names,
                  None if wsum is None else wsum - y_now * wtot, len(names), wtot,
                  selection.twins, selection.alien, halted, adv, dec,
                  beta_quarters, beta_ci)


# ── 종목 귀속 ─────────────────────────────────────────────────────────────
def _names(lake, etf: str, day: str, layers: list[Layer], top: int,
           clock: tuple[str, str],
           intraday: dict[str, tuple[float, bool]] | None = None,
           ) -> tuple[tuple[Name, ...], float | None, float, int, int, int]:
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
        return (), None, 0.0, 0, 0, 0
    # 커밋 봉 모드(ALPHA-866)에서는 구성종목도 **intraday 만이 정본**이다 - 레이크를
    # 아예 안 읽는다. 원장이 결손으로 떨어뜨린 unit 이 레이크(스테일 폴백 경로)에서
    # 되살아나면 dropped_units 와 귀속이 서로 다른 세계를 말한다. 이름은 `hold` 의
    # label 이 정본이라 수익률·정지 여부만 쓴다. 보유 밖 심볼(시장 프록시 등)은 안
    # 세운다: 여기는 구성종목 귀속이지 계열 주입 자리가 아니다.
    if intraday is not None:
        ser = {tk: (tk, *intraday[tk]) for tk, _l, _w in hold if tk in intraday}
    else:
        # `hold` 만 읽는다 - 전량을 읽고 루프에서 버리면 856종목 스캔이 공짜가 아니다.
        ser = _series(lake, day, ("stock",), only=tuple(tk for tk, _l, _w in hold),
                      clock=clock)
    base = sum(x.contribution for x in layers)
    out, wsum, wtot, halted = [], 0.0, 0.0, 0
    adv = dec = 0
    for tk, label, w in hold:
        if tk not in ser:
            continue
        _nm, r_now, halt = ser[tk]
        if halt:
            halted += 1
            continue
        e = r_now - base
        adv += 1 if r_now > 0 else 0
        dec += 1 if r_now < 0 else 0
        out.append(Name(tk, label, w, r_now, e, w * e))
        wsum += w * r_now
        wtot += w
    if not out:
        return (), None, 0.0, halted, 0, 0
    out.sort(key=lambda n: -abs(n.contribution))
    return tuple(out[:top]), wsum, wtot, halted, adv, dec


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
