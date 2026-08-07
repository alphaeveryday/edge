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
from dataclasses import dataclass, replace
from functools import lru_cache
from statistics import NormalDist

import numpy as np

from . import athena as _athena

BETA_WINDOW = 60      # β 추정 창 (거래일). 당일 제외 - 오늘 충격이 β 를 오염시키면 안 된다
MIN_BETA_N = 40       # 창이 이만큼 안 차면 그 층은 후보에서 빠진다 (판정불가지 0 이 아니다)
MAX_LAYERS = 3        # 시장 1 + 섹터 ≤2. 설명 예산의 상한
COVER_TARGET = 0.50   # 누적 기여가 하루 변동의 이만큼을 덮으면 멈춘다
TOP_NAMES = 5         # 고유분을 배정할 종목 수
MARKET_CODE = "069500"
TAUTOLOGY_CUT = 0.30  # 이만큼 겹치면 같은 것이다 - 섹터 후보에서 뺀다 (동어반복 금지)
MIN_OVERLAP = 0.05    # 이만큼도 안 겹치면 "왜 이게 설명하냐"에 답이 없다 (우연 적합 금지)
ALPHA = 0.05          # 층 채택 유의수준. **후보 수로 나눈다** (탐색 보정)
RHO_MARGIN = 0.01     # 층은 잔차 공통상관을 이만큼은 줄여야 한다 - 아니면 요인이 아니다


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
    overlap: float = 0.0  # 설명 대상과의 구성 겹침. 낮아야 동어반복이 아니다

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
    rho_blocked: tuple[str, ...] = ()   # ρ 를 못 줄여 탈락한 층 - 조용히 빼지 않는다

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


def _z(k: int, alpha: float = ALPHA) -> float:
    """후보 K 개 중 최대를 고를 때 필요한 임계 z. **1.96 을 쓰면 안 된다.**

    실측: 섹터 후보를 32 → 80 으로 늘리자 겹침 12% 짜리가 β1.83 으로 뽑히고
    잔차 공통상관 ρ 가 0.276 → 0.523 으로 **악화**했다. 층을 늘렸는데 공통요인이
    늘었다는 건 그 층이 우연을 잡았다는 뜻이다 - 80개 중 하나는 우연히 유의하다.
    Bonferroni: α/K 양측.
    """
    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * max(k, 1)))


def _orth(x: np.ndarray, basis: np.ndarray, x_now: float,
          now: np.ndarray) -> tuple[np.ndarray, float]:
    """basis 에 직교화. **오늘 값도 같은 계수로** 직교화해야 분해가 어긋나지 않는다."""
    if basis.size == 0:
        return x, x_now
    b, *_ = np.linalg.lstsq(basis, x, rcond=None)
    return x - basis @ b, float(x_now - now @ b)


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


# 장 시각 경계 - 이 밖은 5분봉이 없다. 밤사이는 갭 하나로 뭉쳐지므로 구간이 아니다.
SESSION_OPEN, SESSION_CLOSE = "09:00:00", "15:30:00"

# 이름(한글)은 계열과 별개로 한 번 조회한다 - 봉 집계와 조인할 이유가 없다.
_CLOCK_NAMES = ("SELECT symbol, any_value(name) FROM layers_daily "
                "WHERE kind IN {kinds} GROUP BY symbol")

# 시각창 집계는 **심볼 수로 가르지 않는다** - 몇 종목이든 Athena 로 보낸다.
# (예외는 하나뿐이다: 정본이 요청일을 안 담으면 `_series` 가 왕복을 아예 안 친다.
#  받아 봐야 과거만 있는 패널이라 폴백이 확정이다 - 그 판정은 `duck.stale_5m` 이 낸다.)
#
# 가르려 했다가 실측이 막았다(2026-08-05 · `bars_5m` = Glue Iceberg 재배선 이후):
# DuckDB 경로가 읽는 바이트는 심볼 1·4·8·81 에서 **376,395,649 B · 729파일로 한 바이트도
# 안 달랐다**. 술어를 표현식(`regexp_replace(symbol,…) IN`)에서 날것 컬럼(`symbol IN
# ('X','X.KS','X.KQ')`)으로 바꿔도 같았다 - `iceberg_scan` 이 `bucket(16, symbol)`
# 파티션 통계를 아예 안 쓴다. '소수 심볼이면 로컬이 싸다' 는 이 질의에 성립하지 않는다.
# 같은 집계의 Athena 결과는 1심볼 0.048MB · 81심볼 0.379MB 다.
# 오프로드가 남는지의 관문은 심볼 수가 아니라 **결과 크기**이고, 그 상수는 실측 근거와
# 함께 `athena.MAX_RESULT_BYTES` · `athena.MAX_RESULT_ROWS` 에 있다.

# 5분봉 스캔은 **클라우드 레이아웃이 져야 한다.** 로컬 물화(CTAS)는 쓰지 않는다:
# 후보 81심볼 471,389행을 구우면 집계가 100초 -> 0.03초 였지만(실측), 그건 노트북
# 메모리에 레이크를 복제하는 것이고 세션마다 52초를 다시 낸다. 같은 프루닝을
# Iceberg 파티션(`bucket(symbol, N)` + `(symbol, ts)` 정렬)이 S3 에서 해 준다.
#
# 다만 **DuckDB 리더는 그 프루닝을 안 쓴다**(위 실측). 그래서 이 질의는 폴백 경로다 -
# Athena 가 없을 때 결과를 내기는 하되, 바이트는 못 줄인다.
_CLOCK_SQL = r"""
WITH k AS (
    SELECT DISTINCT symbol FROM layers_daily WHERE kind IN {kinds}
), b AS (
    SELECT regexp_replace(symbol, '\.(KS|KQ)$', '') AS sym, CAST(ts AS DATE) AS d,
           ln(last(close ORDER BY ts) / first(open ORDER BY ts)) AS lr,
           sum(volume) AS vol
    FROM bars_5m
    -- `trade_date` 로 자른다. `CAST(ts AS DATE)` 는 하이브 프루닝이 안 걸려
    -- 미래 파티션(롤업 당일분)까지 매번 읽는다.
    WHERE trade_date <= DATE '{day}' AND open > 0 AND close > 0
      AND CAST(ts AS TIME) >= TIME '{t0}' AND CAST(ts AS TIME) < TIME '{t1}'{pick}
      -- **후보 심볼로 먼저 자른다.** 이 선필터가 없으면 1,271 종목 전량을 집계한 뒤
      -- 81개만 조인해 버린다 - 실측 34M 행 물화로 DuckDB 가 OOM 이었다.
      AND regexp_replace(symbol, '\.(KS|KQ)$', '') IN (SELECT symbol FROM k)
    GROUP BY 1, 2
)
SELECT b.sym, list(b.lr ORDER BY b.d),
       list(b.d ORDER BY b.d), list(b.vol ORDER BY b.d)
FROM b JOIN k ON k.symbol = b.sym WHERE b.lr IS NOT NULL GROUP BY b.sym
"""


def _absent(lake, key: str, why: str) -> None:
    """부재 사유를 커버리지에 적는다 — 아래 `_series` 의 `exists["clock_panel"]` 과 같은 자리.

    이 모듈은 로깅을 모른다(`exists` 기록이 관례고 소비는 호출자 몫이다).

    **`unbound` 이 아니라 `exists` 다.** `unbound` 는 `표 → 못 묶은 사유` 라 키가 표 이름이고
    소비자가 그 전제를 쓴다 — `bind_day()` 는 `len(bound) - len(unbound)` 로 바인딩 수를 세고
    `coverage()` 는 "생성 실패" 로 인쇄한다. 합성 키를 넣으면 개수가 어긋나고 표본 부족이
    표 생성 실패로 읽힌다. `exists` 는 이미 표가 아닌 축(`s3`·`rdb`·`clock_panel`)을 담는다.

    대역 레이크는 그 딕셔너리가 없을 수 있어 방어한다 — **관측이 본업을 무너뜨리면 안 된다.**
    """
    notes = getattr(lake, "exists", None)
    if notes is not None:
        notes[key] = why


def _series(lake, day: str, kinds: tuple[str, ...],
            *, clock: tuple[str, str] | None = None,
            only: str | tuple[str, ...] | None = None) -> dict[str, tuple]:
    """{symbol: (name, {date: log수익률}, {거래정지 날짜})}. 당일 포함.

    `clock=(t0, t1)` 이면 **하루 대신 매일의 같은 시각 구간**을 계열로 만든다
    (5분봉 시가->종가). 이게 지평 맞춘 β 의 전부다: 10:30~13:00 을 설명하려면
    과거 60일의 같은 10:30~13:00 에 β 를 추정한다. 새 회귀 코드가 없다 - 계열의
    뜻만 바뀌고 β·게이트·ρ 는 그대로다.

    일간 β 를 구간에 쓰면 Epps 효과(고빈도에서 상관 붕괴)로 시장 몫이 부풀 수 있다.
    실측 091160×069500: 5분 β 1.064 [1.050, 1.078] vs 일간 β 1.052 - 격차 1% 로
    구간 안이었다. 무시할 만했지만 **지평을 맞추는 쪽이 공짜라서** 맞춘다.

    거래량 0 인 날은 **정지**다 - 그날 종가는 직전 값이고 수익률 0 은 거짓이다.
    종가가 전일과 같은 날은 흔하지만(2026년 776일) 대부분 진짜 보합이고
    거래량 0 은 55일(7%)뿐이다 - 그 7% 만 막는다. 진짜 보합의 고유수익은 정보다:
    시장·섹터가 −7% 미는데 안 빠졌으면 그게 그 종목의 힘이다.
    """
    # `only` = 읽을 심볼을 못박는다. **의미 장치이지 성능 장치가 아니다** - "구성종목만
    # 보겠다"는 선언이고, 그래야 `_panel` 이 후보 풀을 안 넓힌다. 예전 주석은 이것을
    # "읽기 전에 좁혀서 싸진다"로 적었는데 실측이 그걸 무효로 만들었다(위 참고:
    # DuckDB 경로 바이트는 심볼 수와 무관하게 평평하다). 바이트를 줄이는 것은 Athena 다.
    syms = ((only,) if isinstance(only, str) else tuple(only)) if only else ()
    lit = ", ".join(f"'{x}'" for x in syms)
    pick = f" AND symbol IN ({lit})" if syms else ""
    # 5분봉 심볼은 **접미사가 붙는다**(`005930.KS`). 맨 코드로 비교하면 한 번도 안
    # 맞고, 그러면 대상 계열이 비어 `decompose` 가 조용히 None 을 낸다 - 실측에서
    # 개별 종목 구간 층 분해가 통째로 '층 미계측' 이었고 원인이 이 한 줄이었다.
    pick_clock = (f" AND regexp_replace(symbol, '\\.(KS|KQ)$', '') IN ({lit})"
                  if syms else "")
    if clock is not None:
        names = {str(s_): n for s_, n in lake.sql(_CLOCK_NAMES.format(kinds=kinds))}
        # `_CLOCK_SQL` 은 `k`(계열 회원)와 조인하므로 실제 대상은 `only ∩ 회원` 이다.
        # Athena 엔 회원 표가 없으니 그 교집합을 **여기서** 만들어 넘긴다.
        want = tuple(sorted(set(syms) & names.keys())) if syms else tuple(sorted(names))
        rows, note, why = None, "", ""
        # **낡은 정본은 예외가 아니라 '과거만 있는 패널'로 온다.** 그걸 성공으로 받으면
        # 아래 폴백이 안 걸리고 요청일 층이 통째로 빠진다 - 실측 2026-08-07: Iceberg 최신
        # 08-05 인데 같은 시각 canonical 엔 당일이 있었고, 산문은 시장·섹터·구성종목을
        # 전부 '미계측'이라고 냈다.
        #
        # 판정을 여기서 다시 하지 않는다. Athena 와 `bars_5m` 은 **같은 물리 표**를 보고
        # (`duck.BARS_ICEBERG` == `athena` 의 DB·TABLE 기본값), 레이크가 바인드할 때
        # `duck.iceberg_covers` 가 이미 그 답을 냈다. 같은 판정이 두 곳에 살면 언젠가
        # 갈리고, 갈린 날 한쪽만 조용히 낡은 표를 쓴다. 그 답을 **읽어서** 쓴다.
        #
        # **이 교환은 비싸다 — 아낀 쪽만 적지 않는다(Rule 12).** 아끼는 것은 버려질 Athena
        # 왕복(고정 2.03초)이고, 무는 것은 그 자리를 메우는 DuckDB 폴백이다: 아래 note 가
        # 적는 실측 376.4MB 를 런마다 최대 6회(`decompose` 가 clock `_series` 를 최대 3회
        # × 한 런이 `decompose` 를 2회) = **약 2.26GB/런**. 정본이 낡은 동안은 그게 정상
        # 상태이므로 이 값은 예외가 아니라 상시다. 그래도 무는 이유는 그 대가가 '층이 있음'
        # 이기 때문이다 - 안 물면 시장·섹터·구성종목이 통째로 미계측이다.
        # 증폭 요인: `_CLOCK_SQL` 은 `trade_date >=` 하한이 없어 표 전 이력을 읽는다.
        # 대체되는 Athena 경로는 `LOOKBACK_DAYS` 로 묶여 있었다 - 하한을 다는 것이 다음 일감.
        #
        # **낡음만 읽는다.** `unbound["bars_5m_iceberg"]` 에는 ATTACH·자격증명 실패도 같이
        # 담기는데 그건 다른 일감이고, Athena 는 boto3 로 따로 붙으므로 그때까지 오프로드를
        # 끌 이유가 없다 - 끄면 위 2.26GB 를 얻는 것 없이 문다.
        #
        # **범위**: `stale_5m` 은 `CausalLake(day=…)` 로 기준일을 받은 레이크에만 선다
        # (`pipeline.py`·`window_batch.py` 둘뿐). 맨생성하는 CLI·탐색 진입점 20여 곳에서는
        # 판정 자체가 없어 이 가드가 안 탄다 - "이제 낡은 정본은 안 쓴다" 가 아니다.
        stale = getattr(lake, "stale_5m", "")
        try:
            # 연결이 없는 레이크(테스트 대역)는 결과 CSV 를 받을 데가 없다. 그것도
            # 부재이므로 **같은 예외로** 말한다 - 조용히 건너뛰면 왜 로컬로 돌았는지가
            # 아무 데도 안 남는다.
            con = getattr(lake, "con", None)
            if con is None:
                raise _athena.AthenaUnavailable("DuckDB 연결 없음 - 결과를 받을 데가 없다")
            if stale:
                raise _athena.AthenaUnavailable(f"정본이 요청일을 안 담는다 - {stale}")
            rows = _athena.clock_panel(day, kinds, clock[0], clock[1], con=con, only=want)
            note = _athena.note()
        except _athena.AthenaUnavailable as e:
            # **조용히 0행으로 넘기지 않는다.** 사유를 남기고 DuckDB 로 돌아간다 -
            # 부재를 기각으로 위장하면 산문이 '섹터 부재'라고 거짓말한다.
            # `ResultTooLarge` 는 일부러 안 잡는다: 폴백해 봐야 더 읽는다(설계 착오다).
            why = str(e)[:160]
        if rows is None:
            rows = lake.sql(_CLOCK_SQL.format(
                kinds=kinds, day=day, t0=clock[0], t1=clock[1], pick=pick_clock))
            note = (f"DuckDB _CLOCK_SQL ({len(want)}심볼 · 실측 376.4MB 수신)"
                    + (f" ← Athena 폴백: {why}" if why else ""))
        # 경로와 폴백 사유를 **한 자리에** 남긴다. `exists` 는 어떤 레이크에도 있고,
        # 커버리지 보고가 곧 '어떻게 쟀는가' 다. 사유를 `unbound` 로 갈라 두면 둘 중
        # 하나만 읽는 소비처가 생기고, 그러면 폴백이 다시 조용해진다.
        lake.exists["clock_panel"] = note
        out = {}
        for sym, lrs, dates, vols in rows:
            # 이미 **구간 수익**이다 - 차분하지 않는다(하니까 첫 날이 사라진다).
            halt = {d for d, v in zip(dates, vols) if v is not None and v <= 0}
            out[sym] = (names.get(sym, sym),
                        dict(zip(dates, [float(x) for x in lrs])), halt)
        return out
    rows = lake.sql(
        "SELECT symbol, any_value(name), list(close ORDER BY date), "
        "       list(CAST(date AS DATE) ORDER BY date), list(volume ORDER BY date) "
        f"FROM layers_daily WHERE kind IN {kinds} AND date <= DATE '{day}'{pick} "
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
          taken: set[str], kind: str, left: float | None,
          z: float = 1.96) -> Layer | None:
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
            # `best_score` 를 갱신하지 않으면 argmin 이 아니라 **초기 임계를 넘긴 마지막
            # 후보**가 남는다(실측: `best_left` 라는 쓰이지 않는 이름에 점수를 넣고 있었다).
            # 층 선택이 순회 순서에 좌우되면 "덜 남기는 층" 이라는 도크스트링이 거짓이다.
            best, best_score = Layer(
                sym, meta.get(sym, sym), kind, float(b[0]),
                float(b[0] - z * se[0]), float(b[0] + z * se[0]),
                xo_now, c, len(y)), score
    return best


def _panel(lake, etf: str, day: str, hist: list,
           *, clock: tuple[str, str] | None = None) -> tuple[np.ndarray, list[float]] | None:
    """구성종목 × 창 수익률 행렬. ρ 게이트와 종목 귀속이 같은 자료를 쓴다."""
    hold = holdings(lake, etf, day)
    if not hold:
        return None
    d0 = dt.date.fromisoformat(day)
    # **구성종목만 읽는다.** 예전엔 856종목 전량을 읽고 이 루프에서 버렸다.
    ser = _series(lake, day, ("stock",), clock=clock,
                  only=tuple(tk for tk, _l, _w in hold))
    rows, nows = [], []
    for tk, _lab, _w in hold:
        if tk not in ser:
            continue
        _nm, m, halt = ser[tk]
        v = _on(m, hist)
        if v is None or d0 not in m or d0 in halt:
            continue
        rows.append(v)
        nows.append(float(m[d0]))
    return (np.vstack(rows), nows) if len(rows) >= 3 else None


def _rho_after(panel: np.ndarray, basis: list[np.ndarray]) -> float | None:
    """이 기저를 뺐을 때 남는 잔차 공통상관. **층의 존재 이유가 이 값을 줄이는 것**이다."""
    if not basis:
        return residual_rho(list(panel))
    B = np.column_stack(basis)
    res = [r - B @ _ols(r, B)[0] for r in panel]
    return residual_rho(res)



KRX_PREFIX = "KRX:"        # 주입된 KRX 업종지수 후보의 코드 접두 (겹침 게이트 면제 표식)


def _krx_sector_candidate(lake, etf: str, day: str, hist: list, d0):
    """ETF 구성종목의 **최빈 KRX 업종**지수를 섹터 후보로. (코드, 이름, 계열, 당일값).

    업종을 우리가 고르지 않는다: 구성종목 다수가 속한 업종이 그 ETF 의 업종이다.
    지수 자체도 KRX 가 산출한다 - 설명력으로 고르면 섹터가 아니라 '가장 잘 맞는
    무엇' 이 된다(042700 07-31 에서 삼성전자가 섹터로 뽑힌 그 실수).
    """
    import math
    # 보유는 `holdings()` 를 쓴다 - KRX 우선·FMP 폴백이 이미 그 안에 있고 캐시된다.
    hold = holdings(lake, etf, day)
    tks = {t.split(".")[0][-6:] for t, _n, _w in hold}
    if not tks:
        return None
    try:
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
            SELECT si.trade_date, si.close, (SELECT code FROM m) AS code
            FROM sector_index si WHERE si.code = (SELECT code FROM m)
              AND si.trade_date <= DATE '{day}' ORDER BY 1""")
    except Exception:                               # noqa: BLE001 - 부재는 후보 없음
        return None
    if not rows or rows[0][2] is None:
        return None
    code = str(rows[0][2])
    lv = {r[0]: float(r[1]) for r in rows if r[1]}
    ds = sorted(lv)
    lr = {ds[i]: math.log(lv[ds[i]] / lv[ds[i - 1]])
          for i in range(1, len(ds)) if lv[ds[i - 1]] > 0}
    v = _on(lr, hist)
    if v is None or d0 not in lr:
        return None
    from .krxsector import sector_name
    return (KRX_PREFIX + code, f"{sector_name(code)}(KRX 업종)", v, float(lr[d0]))


def decompose(lake, etf: str, day: str, *, max_layers: int = MAX_LAYERS,
              cover: float = COVER_TARGET, top: int = TOP_NAMES,
              clock: tuple[str, str] | None = None) -> Rollup | None:
    """ETF 하루를 시장·섹터·고유로 가른다. 층은 최대 `max_layers`, 커버 `cover` 에서 정지.

    설명 대상은 **ETF 자신의 수익률**이다 (관측 가능). 구성종목 가중합과의 차이는
    `rollup_gap` 으로 따로 보고한다 - 추적오차와 비중 노후를 숨기지 않는다.

    `clock=(t0, t1)` 이면 설명 대상이 **그 구간의 수익률**이 된다. 바뀌는 것은 계열의
    뜻뿐이고 β 추정·층 게이트·ρ 게이트·종목 귀속은 글자 하나 안 바뀐다.

    구간 모드의 섹터 층은 **KR 섹터 ETF** 다 - 그게 우리가 쓰는 섹터지수다. 구성종목
    5분봉을 평균해 섹터를 만들지 않는다: 그건 지수의 근사이고, 비중·유동성·거래정지를
    우리가 대신 정하는 것이 된다. 겹침 게이트는 그대로 걸린다(반도체 ETF 로 반도체
    ETF 를 설명하면 동어반복). 후보가 없으면 사유는 'ETF 5분봉 이력 부족' 이지
    '섹터가 없음' 이 아니다 - 호출자가 그 구분을 적어야 한다.
    """
    d0 = dt.date.fromisoformat(day)
    # 이번 호출의 판정으로 덮는다. 한 런이 `decompose` 를 두 번 부르므로(라우팅·설명)
    # 앞 호출의 실패가 남으면 뒤 호출이 성공해도 커버리지가 실패를 말한다 — 부재를
    # 안 지우는 것은 부재를 지어내는 것과 같다.
    notes = getattr(lake, "exists", None)
    if notes is not None:
        notes.pop("layers", None)
        notes.pop("market_layer", None)
    ser = _series(lake, day, ("market", "sector"), clock=clock)
    # 대상이 ETF 가 아니면(개별 종목) **대상만** 주입한다. `kinds` 에 'stock' 을 넣으면
    # 856 종목이 섹터 후보가 되어 겹침 게이트가 종목마다 질의를 돌고, 무엇보다
    # '삼성전자가 섹터로 뽑히는' 그 실수로 돌아간다 - 후보 풀은 건드리지 않는다.
    if etf not in ser:
        tgt = _series(lake, day, ("stock",), clock=clock, only=etf)
        if etf in tgt:
            ser = {**ser, etf: tgt[etf]}
    # **부재를 침묵으로 남기지 않는다.** 아래 `None` 은 둘 다 정상 반환값이지만 처방이
    # 다르다 - 재료가 없는 것은 적재 일감이고, 표본이 모자란 것은 창·유니버스 문제다.
    # 지금은 호출자가 둘을 구분할 방법이 없어 `layer_route=미상` 하나로 뭉개진다
    # (2026-08-06 장중 전 런). 이 모듈은 로깅을 모르므로 커버리지에 적고 소비는 호출자 몫이다.
    if etf not in ser or d0 not in ser[etf][1]:
        _absent(lake, "layers", f"대상 계열 부재 ({etf})" if etf not in ser
                else f"대상 계열에 당일 없음 ({etf} {day})")
        return None
    tmap = ser[etf][1]
    hist = sorted(d for d in tmap if d < d0)[-BETA_WINDOW:]
    if len(hist) < MIN_BETA_N:
        _absent(lake, "layers", f"β 표본 부족 ({etf} {len(hist)} < {MIN_BETA_N})")
        return None
    y, y_now = np.array([tmap[d] for d in hist]), float(tmap[d0])

    xs, nows, meta = {}, {}, {k: v[0] for k, v in ser.items()}
    for sym, (_nm, m, _h) in ser.items():
        v = _on(m, hist)
        if sym != etf and v is not None and d0 in m:
            xs[sym], nows[sym] = v, float(m[d0])

    # **KRX 업종지수를 섹터 후보로 주입한다.** ETF 후보만 쓰면 반도체 ETF 는 반도체
    # ETF 전부가 동어반복(overlap ≥ 컷)으로 빠지고 **섹터 층이 사라진다** - 실측
    # 395160 KODEX AI반도체TOP2플러스 07-31: 동어반복 제외 35개, 섹터 층 0개, 시장
    # 100%. 종목 경로는 이미 KRX 업종지수로 고쳤는데(설명력으로 고르면 섹터가
    # 아니다) ETF 경로만 안 고쳤다.
    #
    # 지수는 **우리가 고르지 않는다**(KRX 가 산출) 그리고 ETF 가 아니라 구성 겹침을
    # 계산할 수 없다 - 그래서 겹침 게이트를 면제한다. 대신 어느 업종인지는 구성종목의
    # **최빈 업종**이 정한다: 그것도 우리가 고르는 게 아니다.
    # 구간 모드: KRX 업종지수는 5분봉이 없다(실측 0건). 섹터는 섹터 ETF 가 맡는다.
    krx = None if clock is not None else _krx_sector_candidate(lake, etf, day, hist, d0)
    if krx is not None:
        code, nm, v, now = krx
        xs[code], nows[code], meta[code] = v, now, nm

    layers: list[Layer] = []
    basis: list[np.ndarray] = []
    basis_now: list[float] = []
    # ρ 게이트 재료: 구성종목 패널과 현재 잔차 공통상관. 층은 이 값을 줄여야 산다.
    panel_pair = _panel(lake, etf, day, hist, clock=clock)
    rho_blocked: list[str] = []

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
    # 시장은 남은 몫을 줄이든 말든 들어간다 - 공통충격을 섹터·종목이 청구하면 거짓이다.
    m = (_pick(y, {MARKET_CODE: xs[MARKET_CODE]}, nows, meta, [], [], set(),
               "시장", None) if MARKET_CODE in xs else None)
    if m is not None:
        add(m)
    elif etf != MARKET_CODE:
        # **시장 층은 조용히 빠지면 안 된다.** 사유가 아무 데도 없어서, 정본이 부분
        # 착지한 날(13종) 시장 층이 사라졌는데 남은 섹터·고유가 시장 몫까지 떠안은
        # 채로 인쇄됐다. 사유는 처방이 다르므로 갈라 적는다: 계열 부재는 적재,
        # 당일 없음은 신선도, 창 결손은 표본(ALPHA-828 백필), 후보 탈락은 계열 자체가
        # 상수라 회귀가 못 서는 경우다. 뭉치면 어느 쪽도 못 고친다.
        #
        # **대상이 시장 프록시 자신이면 사유를 적지 않는다** - 그건 부재가 아니라 정상이다.
        # `xs` 는 대상을 제외하므로(위 `sym != etf`) 069500 을 설명할 때는 시장 층이
        # 없는 것이 맞고, `route.py` 가 그 경우를 `Route("시장", 1.0, …)` 로 이미 정식
        # 처리한다(실측 069500 07-29). 여기서 사유를 적으면 정상 런마다 존재하지 않는
        # 결손을 신고해 **조용한 부재를 시끄러운 오진으로 바꾼다.**
        why = ("계열 부재" if MARKET_CODE not in ser
               else "당일 없음" if d0 not in ser[MARKET_CODE][1]
               else "β 창 결손" if MARKET_CODE not in xs
               else "후보 탈락(분산 0)")
        _absent(lake, "market_layer", f"시장 층 없음 - {MARKET_CODE} {why}")

    # 섹터 후보 자격은 **구성 겹침**이 정한다. 조용히 빼지 않고 사유를 남긴다.
    #   위: 겹치면 같은 것이다 - "반도체가 왜 빠졌냐"에 "반도체가 빠져서"는 설명이 아니다.
    #   아래: 안 겹치면 근거가 없다 - 60일 표본에서 게임 ETF 가 2차전지를 "설명"하는
    #   일이 실제로 일어났다(β0.71 [0.40,1.02]). 산술은 맞지만 아무도 안 믿는다.
    twins, alien, ovs = set(), set(), {}
    for s_ in xs:
        if s_ == MARKET_CODE:
            continue
        if s_.startswith(KRX_PREFIX):
            # KRX 업종지수는 ETF 가 아니라 구성 겹침을 계산할 수 없다. 그리고 지수를
            # 우리가 고르지 않으므로 동어반복 위험이 없다 - 게이트를 면제한다.
            ovs[s_] = 0.0
            continue
        ovs[s_] = ov = overlap(lake, etf, s_, day)
        (twins if ov >= TAUTOLOGY_CUT else alien if ov < MIN_OVERLAP else set()).add(s_)
    sector_pool = {k: v for k, v in xs.items()
                   if k not in twins and k not in alien and k != MARKET_CODE}
    # **KRX 업종지수가 후보에 있으면 그것만 쓴다.** 섹터를 우리가 고르면 섹터가 아니라
    # '가장 잘 맞는 무엇' 이 된다 - 실측 305720 07-31 에서 삼성전자 ETF 가 섹터로
    # 뽑혔다(β 로 −6.90%p). 종목 경로는 이미 KRX 업종으로 못박았는데(042700 07-31 의
    # 그 수술) ETF 경로만 안 했다. 지수는 KRX 가 산출하므로 우리 선택이 아니다.
    krx_only = {k: v for k, v in sector_pool.items() if k.startswith(KRX_PREFIX)}
    if krx_only:
        sector_pool = krx_only
    while len(layers) < max_layers and sector_pool and not covered():
        # 후보 K 개를 훑어 최대를 고르므로 임계를 K 로 보정한다 (탐색 보정).
        pick = _pick(y, sector_pool, nows, meta, basis, basis_now,
                     {x.code for x in layers}, "섹터", left(), _z(len(sector_pool)))
        # β 구간이 0 을 품으면 그 층은 **통계적으로 없다**. 있는 척하지 않는다.
        if pick is None or pick.lo <= 0.0 <= pick.hi:
            break
        # **ρ 게이트**: 층의 존재 이유는 잔차 공통상관을 줄이는 것이다(RHO_MARGIN).
        # 못 줄이면 공통요인이 아니라 우연히 맞는 계열이다. 상수·헬퍼·보고 필드가 다
        # 있는데 호출이 0 이던 자리다 - 선언만 있고 배선이 없으면 그건 게이트가 아니다.
        if panel_pair is not None:
            B0 = np.column_stack(basis) if basis else np.empty((len(y), 0))
            xo = _orth(xs[pick.code], B0, nows[pick.code], np.asarray(basis_now))[0]
            before = _rho_after(panel_pair[0], basis)
            after = _rho_after(panel_pair[0], [*basis, xo])
            # **줄일 것이 있을 때만 잰다.** 잔차 공통상관이 이미 ≈0 이면 어느 층도
            # 그걸 더 줄일 수 없다 - 그때 막으면 '잔차가 이미 독립이다' 와 '이 층은
            # 요인이 아니다' 가 같은 결과로 나온다(부재 ≠ 기각).
            if (before is not None and after is not None
                    and abs(before) > RHO_MARGIN
                    and abs(before) - abs(after) < RHO_MARGIN):
                rho_blocked.append(f"{meta.get(pick.code, pick.code)}"
                                   f" (ρ {before:+.3f}→{after:+.3f})")
                sector_pool.pop(pick.code, None)
                continue
        add(replace(pick, overlap=ovs.get(pick.code, 0.0)))

    idio = y_now - sum(x.contribution for x in layers)
    names, wsum, wtot, rho, used, halted = _names(
        lake, etf, day, hist, basis, basis_now, top)
    return Rollup(etf, etf_label(lake, etf, meta.get(etf, etf)), day, y_now,
                  tuple(layers), idio, names,
                  None if wsum is None else wsum - y_now * wtot, rho, used, wtot,
                  tuple(sorted(meta.get(t, t) for t in twins)),
                  tuple(sorted(meta.get(t, t) for t in alien)), halted,
                  tuple(rho_blocked))


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
    # 여기도 `hold` 만 쓴다 - 전량을 읽고 루프에서 버리면 856종목 스캔이 공짜가 아니다.
    ser = _series(lake, day, ("stock",), only=tuple(tk for tk, _l, _w in hold))
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


def overnight(lake, day: str) -> list[tuple[str, float]]:
    """한국 개장 전에 **이미 확정된** 미국장 수익률 [(이름, 수익률)].

    한국 09:00 KST 개장 시점에 미국 전 세션은 끝나 있다 - 그래서 갭의 정당한 원인
    후보이고, 장중 국내 사건보다 시간적으로 앞선다. 미국 날짜가 한국 날짜보다
    같거나 앞선 마지막 세션만 쓴다(선견 금지).
    """
    rows = lake.sql(
        "SELECT symbol, any_value(name), list(close ORDER BY date), "
        "       list(CAST(date AS DATE) ORDER BY date) "
        f"FROM layers_daily WHERE kind = 'us' AND date < DATE '{day}' GROUP BY symbol")
    out = []
    for _sym, nm, closes, dates in rows:
        if len(closes) < 2 or closes[-2] <= 0:
            continue
        out.append((nm, float(closes[-1] / closes[-2] - 1.0), dates[-1]))
    if not out:
        return []
    last = max(d for *_x, d in out)
    return [(nm, r) for nm, r, d in out if d == last]


def market_source(lake, day: str, proxy: str = "S&P500") -> tuple[float, float] | None:
    """오늘 코스피 수익률 중 **밤사이 미국장으로 설명되는 몫** [lo, hi] (%가 아닌 비율).

    투자자가 실제로 묻는 것은 "코스피가 왜 빠졌나" 다. 밤사이 미국 지수를 보여만
    주고 층에 안 쓰면 그 질문에 답이 없다. 점이 아니라 구간인 이유는 β 추정
    오차를 숨기지 않기 위해서다 - 이 프로젝트의 갭 공변량과 같은 규율이다.
    """
    us = _series(lake, day, ("us",))
    mkt = _series(lake, day, ("market",))
    tgt = next((v for k, v in us.items() if v[0] == proxy), None)
    if tgt is None or MARKET_CODE not in mkt:
        return None
    d0 = dt.date.fromisoformat(day)
    km = mkt[MARKET_CODE][1]
    um = tgt[1]
    # 한국 D 일의 개장 전에 확정된 미국 세션 = 미국 날짜 < D 중 마지막.
    prev = {d: max((u for u in um if u < d), default=None) for d in km if d <= d0}
    pairs = [(km[d], um[prev[d]]) for d in sorted(km)
             if d < d0 and prev.get(d) is not None][-BETA_WINDOW:]
    if len(pairs) < MIN_BETA_N or d0 not in km or prev.get(d0) is None:
        return None
    y = np.array([a for a, _b in pairs])
    x = np.array([b for _a, b in pairs])
    b, se = _ols(y, x.reshape(-1, 1))
    u_now = um[prev[d0]]
    a, c = (b[0] - 1.96 * se[0]) * u_now, (b[0] + 1.96 * se[0]) * u_now
    return (min(a, c), max(a, c))


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
           "holdings", "market_source", "overlap", "overnight", "residual_rho"]
