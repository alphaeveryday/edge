"""층 분해 - 시장 · 시장직교 섹터 · 고유. **설명 예산을 유한하게 만드는 장치**다.

시장은 1개고 섹터는 32개뿐이라, ETF 하루 변동을 설명하는 데 필요한 층은 유한하다.
그래서 **탐욕 선택**으로 최대 3층(시장 1 + 섹터 ≤2)만 고르고 커버리지 50% 에서 멈춘다.
남은 고유분은 상위 5종목에만 배정한다. 결과: ETF 하루가 최대 8개 서사로 닫힌다.
시장·섹터 층은 하루 한 번 계산해 **모든 ETF 가 재사용**한다.

왜 차분(r_i − r_sec)이 아니라 회귀인가: 차분은 β=1 을 강제한다. 실측(42거래일 ·
KODEX반도체) β_시장 1.4~1.8 · β_섹터 0.81~1.11 - 시장 층에서 β=1 은 명백히 틀렸고,
차분은 시장에 배정할 양의 40~80% 를 과소 배정해 그 차액을 조용히 고유로 민다.
그게 이 프로젝트가 계속 싸워온 "조용한 부재"와 같은 병이다.

왜 이중 직교인가: 시장과 섹터가 겹치면 "시장이 민 건지 섹터가 민 건지" 배분 순서
논쟁이 생긴다. 섹터를 시장에 직교화하면 Cov(시장항, 섹터항)=0 이라 중복이 없다.
섹터끼리도 순차 직교화한다(Gram-Schmidt) - ETF 32종은 서로 심하게 겹치기 때문이다
(SK하이닉스가 20개 ETF 에 동시에 들어 있다). 직교 기저 위의 계수는 FWL 정리로
다변량 회귀 계수와 같으므로, 층 기여의 합이 곧 적합값이다 - 중복도 누락도 없다.

**고유를 '고유'라 부르지 않는다.** 이름 없는 잔여다 - 공통요인이 남았는지는
`residual_rho()` 로 재고, ρ≈0 일 때만 고유라 부를 자격이 생긴다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

BETA_WINDOW = 60      # β 추정 창 (거래일). 당일 제외 - 오늘 충격이 β 를 오염시키면 안 된다
MIN_BETA_N = 40       # 창이 이만큼 안 차면 그 층은 후보에서 빠진다 (판정불가지 0 이 아니다)
MAX_LAYERS = 3        # 시장 1 + 섹터 ≤2. 설명 예산의 상한
COVER_TARGET = 0.50   # 누적 기여가 하루 변동의 이만큼을 덮으면 멈춘다
TOP_NAMES = 5         # 고유분을 배정할 종목 수
MARKET_CODE = "069500"
TAUTOLOGY_CUT = 0.30  # 이만큼 겹치면 같은 것이다 - 섹터 후보에서 뺀다 (동어반복 금지)
MIN_OVERLAP = 0.05    # 이만큼도 안 겹치면 "왜 이게 설명하냐"에 답이 없다 (우연 적합 금지)


# ── 자료구조 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Layer:
    """설명 한 층. **관측 가능한 포트폴리오여야 한다** - 아니면 이름을 못 붙인다."""

    code: str
    name: str
    kind: str            # '시장' | '섹터'
    beta: float
    lo: float
    hi: float
    ret: float           # 오늘 그 층의 (직교화된) 수익률
    contribution: float  # beta × ret
    n: int

    def __str__(self) -> str:
        return (f"{self.kind} {self.name}({self.code}) {self.ret * 100:+.2f}% "
                f"× β{self.beta:.2f}[{self.lo:.2f},{self.hi:.2f}] "
                f"= {self.contribution * 100:+.2f}%p")


@dataclass(frozen=True, slots=True)
class Name:
    """고유분을 청구하는 종목 하나."""

    ticker: str
    label: str
    weight: float        # ETF 내 비중 (0~1)
    ret: float
    idio: float          # 층을 뺀 잔여
    contribution: float  # weight × idio

    def __str__(self) -> str:
        return (f"{self.label}({self.ticker}) 비중 {self.weight * 100:.1f}% · "
                f"수익 {self.ret * 100:+.2f}% · 고유 {self.idio * 100:+.2f}% "
                f"→ 기여 {self.contribution * 100:+.2f}%p")


@dataclass(frozen=True, slots=True)
class Rollup:
    """ETF 하루 변동의 층 분해. **합이 정확히 맞는다** - 고유가 잔여로 정의되므로."""

    etf: str
    etf_name: str
    day: str
    total: float
    layers: tuple[Layer, ...]
    idio: float
    names: tuple[Name, ...]
    rollup_gap: float | None    # Σ wᵢrᵢ − w_covered·total (추적오차). None = 못 쟀다
    rho: float | None           # 잔차 평균 횡단해 상관. ≈0 이어야 '고유'라 부를 자격
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


# ── 회귀 ──────────────────────────────────────────────────────────────────
def _ols(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """절편 포함 OLS. 절편(α)은 돌려주지 않는다 - 평균 표류는 고유로 간다."""
    d = np.column_stack([np.ones(len(y)), x])
    b, *_ = np.linalg.lstsq(d, y, rcond=None)
    r = y - d @ b
    dof = len(y) - d.shape[1]
    if dof <= 0:
        return b[1:], np.full(d.shape[1] - 1, np.inf)
    try:
        cov = (r @ r / dof) * np.linalg.inv(d.T @ d)
    except np.linalg.LinAlgError:
        return b[1:], np.full(d.shape[1] - 1, np.inf)
    return b[1:], np.sqrt(np.maximum(np.diag(cov)[1:], 0.0))


def _orth(x: np.ndarray, basis: np.ndarray, x_now: float,
          now: np.ndarray) -> tuple[np.ndarray, float]:
    """basis 에 직교화. **오늘 값도 같은 계수로** 직교화해야 분해가 어긋나지 않는다."""
    if basis.size == 0:
        return x, x_now
    b, *_ = np.linalg.lstsq(basis, x, rcond=None)
    return x - basis @ b, float(x_now - now @ b)


# ── 자료 ──────────────────────────────────────────────────────────────────
def _series(lake, day: str, kinds: tuple[str, ...]) -> dict[str, tuple]:
    """{symbol: (name, {date: log수익률}, {거래정지 날짜})}. 당일 포함.

    거래량 0 인 날은 **정지**다 - 그날 종가는 직전 값이고 수익률 0 은 거짓이다.
    종가가 전일과 같은 날은 흔하지만(2026년 776일) 대부분 진짜 보합이고
    거래량 0 은 55일(7%)뿐이다 - 그 7% 만 막는다. 진짜 보합의 고유수익은 정보다:
    시장·섹터가 −7% 미는데 안 빠졌으면 그게 그 종목의 힘이다.
    """
    rows = lake.sql(
        "SELECT symbol, any_value(name), list(close ORDER BY date), "
        "       list(CAST(date AS DATE) ORDER BY date), list(volume ORDER BY date) "
        f"FROM layers_daily WHERE kind IN {kinds} AND date <= DATE '{day}' "
        "GROUP BY symbol")
    out = {}
    for sym, nm, closes, dates, vols in rows:
        c = np.asarray(closes, dtype="float64")
        if len(c) < 2 or not np.all(c > 0):
            continue
        halt = {d for d, v in zip(dates, vols) if v is not None and v <= 0}
        out[sym] = (nm, dict(zip(dates[1:], np.diff(np.log(c)))), halt)
    return out


def _on(m: dict, days: list) -> np.ndarray | None:
    """정확히 그 날짜들의 값. **하나라도 없으면 None** - 없는 날을 0 으로 메우면
    β 가 조용히 어긋난다(거래정지 종목이 정확히 그렇게 된다)."""
    try:
        return np.array([m[d] for d in days])
    except KeyError:
        return None


# ── 탐욕 선택 ─────────────────────────────────────────────────────────────
def _pick(y: np.ndarray, xs: dict[str, np.ndarray], nows: dict[str, float],
          meta: dict[str, str], basis: list[np.ndarray], basis_now: list[float],
          taken: set[str], kind: str, left: float | None) -> Layer | None:
    """**남은 몫을 가장 많이 줄이는** 층 하나. 없으면 None.

    기여 절댓값 최대로 고르면 안 된다 - 부호가 반대인 큰 기여가 남은 몫을 오히려
    키운다. 우리가 찾는 건 큰 층이 아니라 **덜 남기는 층**이다.

    `left=None` 은 무조건 채택(시장 층) - 남은 몫을 줄이든 말든 들어가야 한다.
    """
    B = np.column_stack(basis) if basis else np.empty((len(y), 0))
    BN = np.asarray(basis_now)
    resid = y - B @ np.linalg.lstsq(B, y, rcond=None)[0] if basis else y
    # 점수는 낮을수록 좋다: 섹터는 |남은 몫|, 시장은 −|기여| (즉 무조건 최대 하나).
    best, best_score = None, float("inf") if left is None else abs(left)
    for sym, x in xs.items():
        if sym in taken:
            continue
        xo, xo_now = _orth(x, B, nows[sym], BN)
        if float(xo @ xo) < 1e-12:
            continue                                   # 이미 선택된 층과 사실상 같다
        b, se = _ols(resid, xo.reshape(-1, 1))
        c = float(b[0]) * xo_now
        score = -abs(c) if left is None else abs(left - c)
        if score < best_score:
            best, best_left = Layer(
                sym, meta.get(sym, sym), kind, float(b[0]),
                float(b[0] - 1.96 * se[0]), float(b[0] + 1.96 * se[0]),
                xo_now, c, len(y)), score
    return best


def decompose(lake, etf: str, day: str, *, max_layers: int = MAX_LAYERS,
              cover: float = COVER_TARGET, top: int = TOP_NAMES) -> Rollup | None:
    """ETF 하루를 시장·섹터·고유로 가른다. 층은 최대 `max_layers`, 커버 `cover` 에서 정지.

    설명 대상은 **ETF 자신의 수익률**이다 (관측 가능). 구성종목 가중합과의 차이는
    `rollup_gap` 으로 따로 보고한다 - 추적오차와 비중 노후를 숨기지 않는다.
    """
    d0 = dt.date.fromisoformat(day)
    ser = _series(lake, day, ("market", "sector"))
    if etf not in ser or d0 not in ser[etf][1]:
        return None
    tmap = ser[etf][1]
    hist = sorted(d for d in tmap if d < d0)[-BETA_WINDOW:]
    if len(hist) < MIN_BETA_N:
        return None
    y, y_now = np.array([tmap[d] for d in hist]), float(tmap[d0])

    xs, nows, meta = {}, {}, {k: v[0] for k, v in ser.items()}
    for sym, (_nm, m, _h) in ser.items():
        v = _on(m, hist)
        if sym != etf and v is not None and d0 in m:
            xs[sym], nows[sym] = v, float(m[d0])

    layers: list[Layer] = []
    basis: list[np.ndarray] = []
    basis_now: list[float] = []

    def left() -> float:
        return y_now - sum(x.contribution for x in layers)

    def covered() -> bool:
        return abs(y_now) > 1e-9 and 1.0 - abs(left()) / abs(y_now) >= cover

    def add(pick: Layer) -> None:
        B = np.column_stack(basis) if basis else np.empty((len(y), 0))
        layers.append(pick)
        basis.append(_orth(xs[pick.code], B, nows[pick.code],
                           np.asarray(basis_now))[0])
        basis_now.append(pick.ret)

    # 시장은 후보 경쟁 없이 먼저 - 공통충격이 섹터로 새면 섹터 서사가 거짓이 된다.
    if MARKET_CODE in xs:
        # 시장은 남은 몫을 줄이든 말든 들어간다 - 공통충격을 섹터·종목이 청구하면 거짓이다.
        m = _pick(y, {MARKET_CODE: xs[MARKET_CODE]}, nows, meta, [], [], set(),
                  "시장", None)
        if m is not None:
            add(m)

    # 섹터 후보 자격은 **구성 겹침**이 정한다. 조용히 빼지 않고 사유를 남긴다.
    #   위: 겹치면 같은 것이다 - "반도체가 왜 빠졌냐"에 "반도체가 빠져서"는 설명이 아니다.
    #   아래: 안 겹치면 근거가 없다 - 60일 표본에서 게임 ETF 가 2차전지를 "설명"하는
    #   일이 실제로 일어났다(β0.71 [0.40,1.02]). 산술은 맞지만 아무도 안 믿는다.
    twins, alien = set(), set()
    for s_ in xs:
        if s_ == MARKET_CODE:
            continue
        ov = overlap(lake, etf, s_, day)
        (twins if ov >= TAUTOLOGY_CUT else alien if ov < MIN_OVERLAP else set()).add(s_)
    sector_pool = {k: v for k, v in xs.items()
                   if k not in twins and k not in alien and k != MARKET_CODE}
    while len(layers) < max_layers and sector_pool and not covered():
        pick = _pick(y, sector_pool, nows, meta, basis, basis_now,
                     {x.code for x in layers}, "섹터", left())
        # β 구간이 0 을 품으면 그 층은 **통계적으로 없다**. 있는 척하지 않는다.
        if pick is None or pick.lo <= 0.0 <= pick.hi:
            break
        add(pick)

    idio = y_now - sum(x.contribution for x in layers)
    names, wsum, wtot, rho, used, halted = _names(
        lake, etf, day, hist, basis, basis_now, top)
    return Rollup(etf, meta.get(etf, etf), day, y_now, tuple(layers), idio, names,
                  None if wsum is None else wsum - y_now * wtot, rho, used, wtot,
                  tuple(sorted(meta.get(t, t) for t in twins)),
                  tuple(sorted(meta.get(t, t) for t in alien)), halted)


# ── 종목 귀속 ─────────────────────────────────────────────────────────────
def _names(lake, etf: str, day: str, hist: list, basis: list[np.ndarray],
           basis_now: list[float], top: int
           ) -> tuple[tuple[Name, ...], float | None, float, float | None, int, int]:
    """고유분을 청구하는 상위 종목. **비중 × 고유** 로 순위 - 큰 종목의 작은 움직임과
    작은 종목의 큰 움직임을 같은 자로 잰다.

    종목은 **층과 정확히 같은 날짜**에 정렬한다. 하나라도 빠지면 그 종목은 뺀다.
    """
    hold = holdings(lake, etf, day)
    if not hold or not basis:
        return (), None, 0.0, None, 0, 0
    d0 = dt.date.fromisoformat(day)
    ser = _series(lake, day, ("stock",))
    B, BN = np.column_stack(basis), np.asarray(basis_now)
    out, resid, wsum, wtot, halted = [], [], 0.0, 0.0, 0
    for tk, label, w in hold:
        if tk not in ser:
            continue
        _nm, m, halt = ser[tk]
        v = _on(m, hist)
        if v is None or d0 not in m or d0 in halt:
            halted += d0 in halt
            continue
        b, _se = _ols(v, B)
        r_now = float(m[d0])
        e = r_now - float(BN @ b)
        out.append(Name(tk, label, w, r_now, e, w * e))
        resid.append(v - B @ b)
        wsum += w * r_now
        wtot += w
    if not out:
        return (), None, 0.0, None, 0, halted
    out.sort(key=lambda n: -abs(n.contribution))
    return tuple(out[:top]), wsum, wtot, residual_rho(resid), len(out), halted


def holdings(lake, etf: str, day: str) -> list[tuple[str, str, float]]:
    """[(ticker, name, weight)] - **as_of ≤ day** 최신 스냅샷만 (선견 금지)."""
    return [(t, n or t, float(w) / 100.0) for t, n, w in lake.sql(
        f"SELECT constituent_ticker, any_value(constituent_name), any_value(weight_pct) "
        f"FROM s3_etf_holdings WHERE market = 'KR' AND etf_id = '{etf}' "
        f"  AND as_of_date = (SELECT max(as_of_date) FROM s3_etf_holdings "
        f"                    WHERE market = 'KR' AND etf_id = '{etf}' "
        f"                      AND as_of_date <= DATE '{day}') "
        f"GROUP BY 1") if w is not None]


def overlap(lake, a: str, b: str, day: str) -> float:
    """두 ETF 의 비중 중첩 Σ min(wᵢᵃ, wᵢᵇ). 1 이면 같은 포트폴리오다.

    **동어반복을 막는 자다**: KODEX 반도체를 TIGER 반도체로 설명하면 "반도체가 왜
    빠졌냐"에 "반도체가 빠져서"라고 답하는 꼴이다. 산술은 맞지만 설명이 아니다.
    """
    wa = {t: w for t, _n, w in holdings(lake, a, day)}
    wb = {t: w for t, _n, w in holdings(lake, b, day)}
    return sum(min(wa[t], wb[t]) for t in wa.keys() & wb.keys())


def residual_rho(resid: list[np.ndarray]) -> float | None:
    """잔차 평균 횡단면 상관. **ρ≈0 이어야 '고유'라 부를 자격이 생긴다** -
    남아 있으면 이름 없는 공통요인이 있다는 직접 증거다."""
    if len(resid) < 3:
        return None
    m = np.corrcoef(np.vstack(resid))
    iu = np.triu_indices_from(m, k=1)
    v = m[iu][np.isfinite(m[iu])]
    return float(v.mean()) if len(v) else None


__all__ = ["BETA_WINDOW", "COVER_TARGET", "MARKET_CODE", "MAX_LAYERS", "MIN_BETA_N",
           "MIN_OVERLAP", "TAUTOLOGY_CUT", "TOP_NAMES", "Layer", "Name", "Rollup", "decompose",
           "holdings", "overlap", "residual_rho"]
