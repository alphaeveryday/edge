"""셀 러너 — 조건·채널 튜플 체계의 전 루프: 분해 → 가설 → 패널 게이트 → 서술.

한 셀에서 도는 것 (설계 §1 세 분리 그대로):
  크기   시간 항등식 (tree)                          — 오늘 · 산술
  인과   튜플 → 패널 게이트 (hypothesize→paneltest)  — 역사 · 3값
  서술   게이트 통과분만 처치로 표기, 나머지 미설명   — narrate 계약

몫 배정 규칙: 사건 창의 타입이 성립 튜플의 점 방아쇠와 일치하면 그 창의
처치가 그 채널이 된다. 계수(est)는 붙이지 않는다 - 게이트는 크기를 만들지
않는다(§11). 크기 주장은 ATT 경로(trial→verifier)가 하고, 그 합이 층 예산을
넘는지는 가법 제약(narrate.AdditiveBudget)이 검산한다 - SEM 은 폐기했다
(기울기를 수준으로 읽히게 하는 구조적 오독원이었다). 검산 전에 숫자를 적으면
그게 바로 우리가 STORM 에서 잰 날조다.

사용:  python -m edge_analysis.statics.attribute <ticker> <instrument_id> <YYYY-MM-DD>
       env: EDGE_RDB_DSN · CAUSAL_BACKFILL_DIR · DEEPSEEK_API_KEY
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from datetime import datetime, time, timedelta, timezone

from ..observability import record as trace   # registry.record 와 이름 충돌 회피
from .duck import CausalLake
from .hypothesize import explore, propose
from .narrate import AdditiveBudget, Edge, additive_say, narrate
from .paneltest import EdgeReport, edge_test
from .render import Row, render
from .tree import Share, decompose
from .vocab import HypothesisTuple
from .windows import build_windows

KST = timezone(timedelta(hours=9))


def _measurable(lake) -> list[tuple[str, str]]:
    """가설 에이전트가 제안할 수 있는 노출 축 = **가용 도구가 실제로 재는 것**.

    `FEATURES` 전량을 그대로 주면 안 되는 이유: 도구가 데이터 부재로 사라져도 가설
    에이전트는 그 축을 계속 제안하고, 검정기는 매번 판정불가를 낸다. 그러면 산출이
    '판정불가' 로 뒤덮여 진짜 부재와 배선 부재를 구분할 수 없다. 세 에이전트가 같은
    표면을 본다는 것의 실질이 이 한 줄이다 - **제안 가능 = 측정 가능**.
    """
    from .paneltest import FEATURES
    from .surface import available
    fams = {v for t in available(lake) for v in t.vocab}
    return [k for k in FEATURES if k[0] in fams]


def _kst(ts) -> datetime:
    return ts.astimezone(KST).replace(tzinfo=None) if ts.tzinfo else ts


def idio_budget(lake, instrument_id: str, day: str) -> tuple[float | None, float | None]:
    """(고유요인 수익률, 시장 수익률) — 인과가 청구할 수 있는 예산과 그 대조군.

    None = 못 쟀다. 못 쟀으면 **예산이 없는 것**이지 원수익으로 대신하지 않는다
    (그게 단위 혼동의 입구다).
    """
    from .paneltest import _base
    try:
        rows = lake.sql(_base(day, "23:59:59") + f"""
            SELECT d.ar_ind, x.mkt_lr FROM v_daily d
            JOIN (SELECT trade_date, avg(lr) AS mkt_lr FROM v_daily GROUP BY 1) x
              ON x.trade_date = d.trade_date
            WHERE d.instrument_id = '{instrument_id}' AND d.trade_date = DATE '{day}'""")
    except Exception:                      # noqa: BLE001 - 못 재면 부재 보고
        return None, None
    return (rows[0][0], rows[0][1]) if rows and rows[0][0] is not None else (None, None)


SIGMA_N = 60        # σ̂_ε 창(일). paneltest w20 과 같은 규율 - **당일 제외**
SIGMA_MIN_N = 40    # 이보다 얇으면 표준편차 자체가 표본잡음이라 판정불가


def resid_sigma(lake, instrument_id: str, day: str) -> tuple[float | None, str]:
    """(σ̂_ε, 미계측 사유) — 그 종목 `ar_ind` 의 60일 표준편차.

    가법 제약 상한 |B| + 1.96·σ̂_ε 의 잡음 항이다. 예산 B 는 **실현된** 하루
    몫이라 자기도 흔들린다 - 참 효과의 합이 정확히 B 여도 실현치가 조금 낮게
    나오면 Σ|ATT| > B 가 되어 옳은 주장이 모순으로 기각된다. σ̂_ε 는 '하루가
    그만큼은 흔들린다' 는 관측된 크기고, `vol20`(paneltest L156 stddev_samp)이
    lr 로 이미 계산하는 것을 결과변수 축(ar_ind)에서 60일 창으로 잰 것이다.

    창은 **당일 제외** [t-60, t-1] 이다. 오늘을 넣으면 오늘의 큰 충격이 σ̂ 를
    부풀려 상한이 저절로 넓어진다 - 검산이 검산 대상에게 매수당한다.

    None 이면 **못 쟀다**. 그러면 가법 제약은 판정불가이고 거저 통과가 아니다.
    """
    from .paneltest import _base
    try:
        rows = lake.sql(_base(day, "23:59:59") + f"""
            SELECT stddev_samp(w.ar_ind), count(w.ar_ind) FROM (
              SELECT d.ar_ind FROM v_daily d
              WHERE d.instrument_id = '{instrument_id}'
                AND d.trade_date < DATE '{day}' AND d.ar_ind IS NOT NULL
              ORDER BY d.trade_date DESC LIMIT {SIGMA_N}) w""")
    except Exception as e:              # noqa: BLE001 - 부재는 사유와 함께
        return None, f"σ̂_ε 조회 실패: {type(e).__name__}: {e}"[:110]
    if not rows or rows[0][0] is None:
        return None, (f"{instrument_id} ar_ind {SIGMA_N}일 창이 비었다 - "
                      "σ̂_ε 미계측 (백필 필요)")
    n = int(rows[0][1])
    if n < SIGMA_MIN_N:
        return None, f"ar_ind 표본 {n} < {SIGMA_MIN_N} - σ̂_ε 가 표본잡음이다"
    return float(rows[0][0]), ""


MIN_BETA_N = 40     # 갭 β 추정의 최소 표본 (60d 창 기준 - 이보다 얇으면 부재 선언)


def _beta_ci(xs, ys) -> tuple[float, float] | None:
    """OLS 기울기의 95% CI. 분산 없으면 None. 순수 함수 - 단위검정 대상."""
    import numpy as np
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    n = len(x)
    sxx = float(((x - x.mean()) ** 2).sum())
    if n < 3 or sxx <= 0.0:
        return None
    beta = float(((x - x.mean()) * (y - y.mean())).sum() / sxx)
    resid = y - y.mean() - beta * (x - x.mean())
    se = float((resid @ resid / (n - 2) / sxx) ** 0.5)
    return beta - 1.96 * se, beta + 1.96 * se


# KRX 업종 → 밤사이 미국 팩터. **사람이 정하는 스키마**다 (적합도로 고르면
# "설명력 최대" 가 섹터를 대신하는 그 실수의 반복 - 섹터를 KRX 업종지수로
# 못박은 것과 같은 규율). 없으면 광의 지수로 떨어진다.
US_FACTOR = {
    "1013": "SOX",   # 전기전자 → 필라델피아반도체
    "1012": "SOX",   # 기계·장비 → 반도체 장비가 이 지수의 주력 구성 (한미반도체 등)
    "2013": "SOX",   # 코스닥 반도체
}
US_FACTOR_DEFAULT = "GSPC"
SECTOR_DOMINANCE = 0.50   # 비중 최빈 업종이 이 미만이면 섹터 펀드가 아니다


def labels_name(lake, ticker: str) -> str:
    """사람이 부르는 이름. ETF 는 프로필, 종목은 계열 이름 - 없으면 티커 그대로."""
    tk6 = ticker.split(".")[0]
    from .layers import etf_label
    nm = etf_label(lake, tk6, "")
    if nm:
        return nm
    try:
        rows = lake.sql("SELECT any_value(name) FROM layers_daily "
                        f"WHERE symbol = '{tk6}'")
    except Exception:                       # noqa: BLE001
        return ticker
    return str(rows[0][0]) if rows and rows[0][0] else ticker


def _us_factor(lake, tk6: str, day: str) -> str:
    """이 종목·ETF 의 KRX 업종에 맞는 미국 팩터 심볼. 업종 미상이면 광의 지수.

    **ETF 도 처리한다**: ETF 는 `sector_member` 에 없으므로 종목 조회가 빈다. 그러면
    구성종목의 **최빈 업종**으로 떨어진다 - 실측 395160(KODEX AI반도체TOP2플러스)이
    GSPC 로 떨어져 '밤사이 미국 세션 수익률 없음' 이 됐고, 정작 밤사이 필라델피아
    반도체가 +8.19% 였다. 팩터를 못 고르면 환원이 통째로 침묵한다.
    """
    # **시장 프록시 자신의 팩터는 광의 지수다** (범주). KODEX 200 은 비중으로 보면
    # 전기전자 68% 라 섹터 게이트를 통과해 SOX 로 갔다 - 그러면 '시장' 층이 섹터
    # 지수로 정의되고 분해가 순환한다. 시장은 시장으로만 환원한다.
    from .layers import MARKET_CODE
    if tk6 == MARKET_CODE:
        return US_FACTOR_DEFAULT
    rows = lake.sql(f"""
        SELECT code FROM sector_member
        WHERE ticker = '{tk6}' AND as_of <= DATE '{day}'
        ORDER BY as_of DESC LIMIT 1""")
    if rows:
        return US_FACTOR.get(str(rows[0][0]), US_FACTOR_DEFAULT)
    try:
        from .layers import holdings
        tks = {t.split(".")[0][-6:] for t, _n, _w in holdings(lake, tk6, day)}
        if tks:
            # **비중 가중** 최빈이다. 개수로 세면 코스닥 소형주 다수가 삼성전자·
            # 하이닉스를 이긴다 - 실측 091160(KODEX 반도체): 개수 최빈이 2072(149종)
            # 이라 US_FACTOR 미등록 → GSPC 로 떨어졌고, 정작 밤사이 필라델피아
            # 반도체가 +8.19% 였다. 팩터는 **돈이 있는 곳**에 맞춰야 한다.
            w = {t.split(".")[0][-6:]: wt for t, _n, wt in holdings(lake, tk6, day)}
            vals = ", ".join(f"('{k}', {v})" for k, v in sorted(w.items()) if v)
            # `sector_member` 는 티커당 as_of 가 여럿이다 - 그대로 조인하면 비중이
            # 행 수만큼 부풀어 최빈이 뒤집힌다(실측: 전부 GSPC 로 붕괴).
            r2 = lake.sql(f"""
                WITH hw(ticker, wt) AS (VALUES {vals}),
                     one AS (SELECT ticker, code FROM (
                       SELECT ticker, code, row_number() OVER (
                                PARTITION BY ticker ORDER BY as_of DESC) AS rn
                       FROM sector_member WHERE as_of <= DATE '{day}') WHERE rn = 1)
                SELECT one.code, sum(hw.wt) / sum(sum(hw.wt)) OVER () AS share
                FROM one JOIN hw ON hw.ticker = one.ticker
                GROUP BY 1 ORDER BY 2 DESC LIMIT 1""") if vals else []
            # **지배해야 섹터다.** 광의 ETF 도 비중 최빈 업종은 있다 - 실측 069500
            # (KODEX 200): 삼성전자·하이닉스 때문에 전기전자가 1위라 SOX 로 갔다.
            # 코스피200 의 밤사이 팩터는 반도체가 아니라 광의 지수다. 한 업종이
            # 절반을 넘지 못하면 그것은 섹터 펀드가 아니다.
            if r2 and float(r2[0][1]) < SECTOR_DOMINANCE:
                return US_FACTOR_DEFAULT
            if r2:
                return US_FACTOR.get(str(r2[0][0]), US_FACTOR_DEFAULT)
    except Exception:                               # noqa: BLE001 - 부재는 기본값
        pass
    return US_FACTOR_DEFAULT


def gap_covariate(lake, ticker: str, day: str, gap_share: float):
    """§9: 갭은 더 잘리지 않으므로 공변량으로만 좁힌다 - 그리고 그 좁힘도
    **부분식별**이다. β 의 CI × 직전 미국 세션 수익률 → 갭 몫의 설명 구간.
    점 β 로 곱하면 크기 층이 다시 점 주장으로 오염된다.

    팩터는 **섹터 매칭**이다: 반도체 종목의 갭을 S&P500 으로 재면 밤사이 미국
    반도체가 +8.5% 인 날(실측 042700 07-31, SOX)을 S&P +0.77% 로 설명하려 들고,
    β 가 3 까지 부풀어 '갭의 12% 만 공통충격' 이라는 오답이 나온다. facts 는
    이미 반도체 지수를 출력하고 있었는데 이 함수만 광의 지수를 봤다 - 바인딩 누락.

    이력도 `us_market`(120일) 대신 `layers_daily`(939일, 2022-11~) 를 쓴다.

    재료가 없으면 GapCovariate(reason=...) 부재 선언 - 침묵 금지. 부재의 사유가
    곧 백필 요청이다 (docs/analysis-engine/data-backfill-requests.md).
    """
    from .narrate import GapCovariate
    tk6 = ticker.split(".")[0]
    try:
        sym = _us_factor(lake, tk6, day)
        rows = lake.sql(f"""
            WITH d AS (
              SELECT CAST(ts AS DATE) AS dt,
                     first(open ORDER BY ts) AS o, last(close ORDER BY ts) AS c
              FROM bars_5m WHERE symbol = '{ticker}' GROUP BY 1
            ),
            gap AS (
              SELECT dt, ln(o / NULLIF(lag(c) OVER (ORDER BY dt), 0)) AS g FROM d
            ),
            us AS (
              SELECT CAST(date AS DATE) AS ud, any_value(name) AS nm,
                     ln(close / NULLIF(lag(close) OVER (ORDER BY date), 0)) AS r
              FROM layers_daily WHERE kind = 'us' AND symbol = '{sym}' GROUP BY date, close
            )
            SELECT g.dt, g.g,
                   (SELECT u.r FROM us u WHERE u.ud < g.dt AND u.r IS NOT NULL
                     ORDER BY u.ud DESC LIMIT 1),
                   (SELECT u.nm FROM us u WHERE u.ud < g.dt AND u.r IS NOT NULL
                     ORDER BY u.ud DESC LIMIT 1)
            FROM gap g WHERE g.dt <= DATE '{day}' AND g.g IS NOT NULL
            ORDER BY g.dt DESC""")
    except Exception as e:                  # noqa: BLE001 - 부재는 사유와 함께
        return GapCovariate(reason=f"밤사이 팩터 조회 실패: {type(e).__name__}: {e}"[:120])
    if not rows:
        return GapCovariate(reason=f"{ticker} 5분봉이 없다 - 갭 계열을 만들 수 없다 "
                                  "(ETF·비상장 등). 미국 팩터 부재와 다른 사유다")
    today = next((r for r in rows if str(r[0]) == day), None)
    hist = [(float(r[2]), float(r[1])) for r in rows
            if str(r[0]) != day and r[2] is not None]
    fname = (today[3] if today and today[3] else sym)
    if today is None or today[2] is None:
        return GapCovariate(factor=fname,
                            reason=f"직전 미국 세션({sym}) 수익률 없음 - 백필 필요")
    if len(hist) < MIN_BETA_N:
        return GapCovariate(factor=fname,
                            reason=f"β 표본 {len(hist)} < {MIN_BETA_N} ({sym}) - 백필 필요")
    ci = _beta_ci([h[0] for h in hist], [h[1] for h in hist])
    if ci is None:
        return GapCovariate(factor=fname, reason="β 추정 불가 (공변량 분산 없음)")
    us_t = float(today[2])
    lo, hi = sorted((ci[0] * us_t, ci[1] * us_t))
    # 갭 몫과의 교차: [lo,hi] ∩ (0 방향 gap_share]. 항등식이 상한이다. 공집합이면
    # β 구간과 갭의 방향이 어긋난 것이고 그때 공통충격 설명은 0 - 구간을 억지로
    # 만들지 않는다. (공용 `_clip` 이었는데 SEM 식별집합과 유일하게 공유했고 그쪽이
    # 사라져 호출자가 하나다 - 한 줄 헬퍼를 남기는 것보다 여기 있는 게 읽기 쉽다.)
    a = max(lo, 0.0) if gap_share >= 0 else max(lo, gap_share)
    b = min(hi, gap_share) if gap_share >= 0 else min(hi, 0.0)
    ok = a <= b
    return GapCovariate(factor=fname, factor_ret=us_t, n=len(hist),
                        beta_lo=ci[0], beta_hi=ci[1],
                        explained=(a, b) if ok else None, opposed=not ok)


def peer_context(lake, ticker: str, day: str) -> tuple[str, int, float, float] | None:
    """(업종명, n, 동종 중위 수익, 이 종목의 백분위). 층 분해를 눈으로 확인시킨다.

    '시장층이 77%' 는 맞지만 추상적이다. **같은 업종 45종목의 중위가 +19% 인데
    이 종목이 +28%** 는 같은 사실을 즉시 납득시킨다 - 층 분해가 계산으로 말한
    것을 횡단면이 눈으로 말한다. 둘이 어긋나면 그것도 알아야 한다.

    수익은 로그다 (층·창과 같은 단위). 업종은 KRX 업종지수 구성원 (statics.krxsector).
    """
    tk6 = ticker.split(".")[0]
    try:
        rows = lake.sql(f"""
            WITH me AS (
              SELECT code FROM sector_member
              WHERE ticker = '{tk6}' AND as_of <= DATE '{day}'
              ORDER BY as_of DESC LIMIT 1
            ),
            mem AS (
              SELECT DISTINCT sm.ticker FROM sector_member sm, me
              WHERE sm.code = me.code AND sm.as_of <= DATE '{day}'
            ),
            px AS (
              SELECT p.ticker, p.trade_date, p.close,
                     lag(p.close) OVER (PARTITION BY p.ticker ORDER BY p.trade_date) pc
              FROM s3_price_daily p
              WHERE p.trade_date <= DATE '{day}'
                AND substr(p.ticker, 1, 6) IN (SELECT ticker FROM mem)
            )
            SELECT substr(ticker, 1, 6), ln(close / pc) FROM px
            WHERE trade_date = DATE '{day}' AND pc > 0 AND close > 0""")
    except Exception:                       # noqa: BLE001 - 부재는 침묵이 아니라 None
        return None
    vals = {t: float(r) for t, r in rows}
    if len(vals) < 5 or tk6 not in vals:
        return None
    name = sector_name_of(lake, tk6, day)
    xs = sorted(vals.values())
    med = xs[len(xs) // 2]
    pct = sum(1 for v in xs if v <= vals[tk6]) / len(xs)
    return (name, len(xs), med, pct)


def sector_name_of(lake, tk6: str, day: str) -> str:
    """이 종목의 KRX 업종명. 코드만 보여주면 사람이 못 읽는다."""
    from .krxsector import sector_name
    # **시장 프록시 자신의 팩터는 광의 지수다** (범주). KODEX 200 은 비중으로 보면
    # 전기전자 68% 라 섹터 게이트를 통과해 SOX 로 갔다 - 그러면 '시장' 층이 섹터
    # 지수로 정의되고 분해가 순환한다. 시장은 시장으로만 환원한다.
    from .layers import MARKET_CODE
    if tk6 == MARKET_CODE:
        return US_FACTOR_DEFAULT
    rows = lake.sql(f"""
        SELECT code FROM sector_member
        WHERE ticker = '{tk6}' AND as_of <= DATE '{day}'
        ORDER BY as_of DESC LIMIT 1""")
    return sector_name(str(rows[0][0])) if rows else "업종 미상"


def _assign_rows(shares, labels: dict[str, str], passing: dict, refuted: set[str]) -> list[Row]:
    """창 행의 3값 배정. 산문의 자기모순을 여기서 막는다 (10차 정정).

    - 성립·적용 튜플의 타입을 담은 창 → 성립 (처치 표기)
    - 창의 접지 타입 **전부**가 패널 기각(불성립)된 창 → 불성립 - '원인이 아니다'
      를 창 수준에서 말할 자격은 모든 후보가 기각됐을 때뿐이다
    - 나머지 사건 창 → 판정불가. 사유는 창이 모른다 - 채널판이 말한다
      (기존엔 불성립 타입의 창까지 '표본이 없어 판정불가'로 뭉개 [아닌 것 먼저]
       의 엣지 문장과 산문 안에서 모순됐다)
    """
    rows: list[Row] = []
    for s in shares:
        wtypes = {labels[e] for e in s.window.event_ids}
        hit = next((passing[w] for w in wtypes if w in passing), None)
        if hit is not None:
            t, _ = hit
            rows.append(Row(s, treatment=f"{t.trigger.ident[:14]}→{t.channel}",
                            verdict="성립"))
        elif wtypes and wtypes <= refuted:
            rows.append(Row(s, treatment=" · ".join(sorted(wtypes))[:44],
                            verdict="불성립"))
        elif s.window.kind == "event" or (s.window.kind == "gap" and s.window.event_ids):
            rows.append(Row(s, treatment=",".join(s.window.event_ids)[:20],
                            verdict="판정불가"))
        else:
            rows.append(Row(s))
    return rows


def _route_gate(lake, instrument_id: str, day: str):
    """귀속 게이트 D (요인 오염) 1단 배선. 광역 ETF(구성 ≥100종목) 내 비중이
    상한을 넘으면 이 셀의 점귀속은 **거절** - 자기 사건이 요인을 움직이는 대형주는
    요인을 빼면 효과가 같이 빠진다 (gates.route 의 저주 그대로).

    비중 미계측(PIT 상 스냅샷 부재)이나 상한 미만은 None - 나머지 게이트(A·B·C·E)
    가 미배선이라 '점추정'을 선언할 자격이 아직 없다. 부분 배선의 정직한 어법:
    거절은 말할 수 있고, 통과는 아직 말할 수 없다.
    """
    # 상한 0.05 = gates.route 의 weight_cap 과 동일 계약 (한 곳이 바뀌면 둘 다).
    try:
        w = lake.sql(f"""
            WITH broad AS (
              SELECT etf_instrument_id, max(trade_date) AS d
              FROM rdb.public.etf_holding_snapshot
              WHERE trade_date <= DATE '{day}'
              GROUP BY 1 HAVING count(DISTINCT constituent_instrument_id) >= 100
            )
            SELECT max(h.weight_ratio)
            FROM rdb.public.etf_holding_snapshot h
            JOIN broad b ON b.etf_instrument_id = h.etf_instrument_id AND b.d = h.trade_date
            WHERE h.constituent_instrument_id = '{instrument_id}'
        """)
    except Exception:
        return None
    weight = w[0][0] if w and w[0] else None
    return "거절" if weight is not None and float(weight) >= 0.05 else None


def load_cell(lake: CausalLake, ticker: str, instrument_id: str, day: str):
    """셀 재료 조립. smoke 와 같은 규약 (마감 동시호가 포함 · 마감 후 = 알리바이)."""
    d = datetime.strptime(day, "%Y-%m-%d")
    o = datetime.combine(d.date(), time(9, 0))
    c = datetime.combine(d.date(), time(15, 35))
    taus, after_close, labels = [], [], {}
    if lake.exists.get("rdb") is True:
        for t, e in lake.taus(instrument_id, day):
            t = _kst(t)
            if t >= c:
                after_close.append(str(e))     # 창이 아니라 알리바이로 간다
            else:
                taus.append((t, str(e)))
        ids = [e for _, e in taus] + after_close
        for eid in ids:
            labels[eid] = eid[:16]
        if ids:
            for eid, code in lake.sql(
                    "SELECT source_event_id, event_type_code FROM rdb.public.source_event "
                    f"WHERE source_event_id IN ({','.join(repr(e) for e in ids)})"):
                labels[str(eid)] = str(code)
    bars = [(ts.replace(tzinfo=None) if ts.tzinfo else ts, float(px))
            for ts, px in lake.bars(ticker, day)]
    shares = decompose(bars, lake.prev_close(ticker, day), build_windows(o, c, taus))
    return shares, labels, after_close


def cell_facts(ticker: str, day: str, shares: list[Share],
               labels: dict[str, str], after_close: list[str],
               overnight: list[tuple[str, float]] = ()) -> tuple[str, list[str]]:
    """가설 에이전트에게 주는 사실 문단 + 접지 타입 목록. **결과의 크기는 주되
    사건의 내용은 타입 분포로만** - 결과를 본 특징 오염(§13)은 τ 이후 문서를
    안 주는 것으로 이미 막혀 있고, 여기는 타입·시각 사실만 싣는다."""
    pct = lambda lr: (math.exp(lr) - 1) * 100  # noqa: E731
    total = sum(s.log_ret for s in shares)
    gap = next(s for s in shares if s.window.kind == "gap")
    big = max(shares, key=lambda s: abs(s.log_ret))
    intraday_types = Counter(labels[e] for s in shares for e in s.window.event_ids)
    ac_types = Counter(labels[e] for e in after_close)
    types = sorted(set(intraday_types))
    L = [f"셀: {ticker} {day}. 하루 {pct(total):+.2f}%p.",
         f"시간 분해(항등식): 갭(밤) {pct(gap.log_ret):+.2f}%p · "
         f"최대 몫 {big.window.name} {pct(big.log_ret):+.2f}%p"
         + (" (사건 없는 구간이다 - 보도 사건 주도 서사는 이 사실과 싸워야 한다)"
            if big.window.kind == "residual" else ""),
         "장중 사건 타입 분포: " + (" · ".join(f"{t} ×{n}" for t, n in
                                              intraday_types.most_common()) or "없음")]
    if overnight:
        # 개장 전 이미 확정된 사실 - 갭의 정당한 원인 후보다. 국내 장중 사건보다 앞선다.
        L.append("밤사이 미국장(개장 전 확정): "
                 + " · ".join(f"{n} {r * 100:+.2f}%" for n, r in overnight))
    if ac_types:
        L.append("시간 알리바이: 마감 후 보도 "
                 + " · ".join(f"{t} ×{n}" for t, n in ac_types.most_common())
                 + " - 오늘 수익률은 장중 실현이라 이것들은 오늘의 원인이 될 수 없다")
    L.append(f"가설 {3}개를 내라.")
    return "\n".join(L), types


def run_cell(lake: CausalLake, ask, ticker: str, instrument_id: str, day: str) -> str:
    import os
    from .registry import recall, record
    shares, labels, after_close = load_cell(lake, ticker, instrument_id, day)
    from .layers import overnight as _overnight
    facts, types = cell_facts(ticker, day, shares, labels, after_close,
                              _overnight(lake, day))
    root = os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill")

    tuples: list[HypothesisTuple] = []
    rejected: list[str] = []
    reports: list[tuple[HypothesisTuple, EdgeReport]] = []
    memory: list[str] = []
    from .paneltest import Z_ANOM, grid_screen, series_z
    # 계열 방아쇠의 접지 = 오늘 발화(|z|≥2, 60d). 점 사건이 없어도 계열 이상이
    # 있으면 가설 단계는 돈다 - 무사건 폭락일이 계열 방아쇠의 존재 이유다
    # (14차 정정: 기계는 e1113ce 부터 있었는데 이 게이트가 점 사건에만 걸려
    # 정확히 그 날들에 절대 발화할 수 없었다 - 선언≠배선).
    zs = series_z(lake, instrument_id, day)
    anomalous = sorted(f for f, z in zs.items() if abs(z) >= Z_ANOM)
    screens: list[dict] = []
    if types or anomalous:
        # 회상이 기록보다 먼저다 (P9 교훈). 과거 셀들의 스크린·게이트 이력은
        # PIT 안전한 사실이고, 가설 에이전트의 어포던스로 들어간다.
        memory = recall(root, day=day, types=types)
        if memory:
            facts += "\n과거 셀 이력 (어포던스 - 확증 아님):\n" + "\n".join(
                f"  - {m}" for m in memory)
        if anomalous:
            facts += ("\n오늘 계열 이상 (계열 방아쇠는 이 계열족에서만): "
                      + " · ".join(f"{f} z={zs[f]:+.1f}" for f in anomalous))
        # 격자는 **도구로만** 준다 (screen). 프롬프트에 쏟으면 (a) 도구 호출 기록이
        # 안 남아 무엇을 봤는지 모르고, (b) 상태기계 가드가 무의미해진다. 여기서는
        # 블록·레지스트리용으로만 계산한다. 발견 표본이라 확증 표본과 겹치지 않는다.
        screens = grid_screen(lake, day, types) if types else []
        # 동적 도구 상태기계로 먼저 **관측**한다 (18R). 어휘·격자를 프롬프트로 쏟지
        # 않고 도구로 준다 - 무엇을 물었고 무엇이 없다고 답했는지가 기록에 남는다.
        from .fsm import Machine
        from .tools import Catalog
        cat = Catalog(lake=lake, ticker=ticker, instrument_id=instrument_id,
                      day=day, types=tuple(types))
        machine = Machine(cat)
        seen = explore(ask, machine, facts=facts)
        if seen:
            facts += "\n\n[도구 관측 기록]\n" + seen
        tuples, rejected = propose(ask, facts=facts, event_types=types,
                                   measurable=_measurable(lake),
                                   series_families=anomalous)
        reports = [(t, edge_test(lake, t, day, cell_instrument_id=instrument_id,
                                 m_tests=len(tuples)))
                   for t in tuples]

    # 몫 배정: 성립 + 오늘 조건 충족 + 환원 미불일치 (INUS 의 적용 판정).
    # 크기는 창 행에 싣지 않는다 - SEM 기여는 **일 단위** 추정량이라(패널이 일간 ar)
    # 15분 창의 몫으로 클립하는 것은 범주 오류다 (8차 정정). 창 행은 존재 판정만,
    # 크기의 식별집합은 튜플 블록에서 일 단위 상한(하루 총합)과 교차한다.
    passing = {t.trigger.ident: (t, r) for t, r in reports
               if t.trigger.kind == "점" and r.applies_today}
    refuted = {t.trigger.ident for t, r in reports
               if t.trigger.kind == "점" and r.verdict == "불성립"}
    rows = _assign_rows(shares, labels, passing, refuted)
    record(root, day=day, cell=f"{ticker}/{day}", reports=reports, screens=screens)

    # 채널판을 산문에 배선한다 - 표·블록·산문이 같은 값에서 나와야 한다는 계약의
    # 채널 확장. 성립-미적용의 사유는 applies_today 의 부정을 그대로 옮긴다.
    day_total = sum(s.log_ret for s in shares)
    # 인과 예산은 **고유요인**이다 (20R). 원수익은 시간 항등식(알리바이)의 대상이고,
    # ATT 는 ar_ind 단위라 원수익을 상한으로 쓰면 단위가 다른 두 수를 비교한다.
    idio, mkt = idio_budget(lake, instrument_id, day)
    cell_route = _route_gate(lake, instrument_id, day)
    edges = []
    for t, r in reports:
        why = ("" if r.applies_today else
               "조건 측정불가 - 판정불가 (부재는 충족이 아니다)" if not r.cond_measurable else
               "조건 미충족 (INUS)" if r.cond_satisfied is False else
               "횡단면 방향 반대 (환원 불일치)" if r.reduction.startswith("불일치") else
               "방아쇠 미발화 (오늘 |z| < 임계)" if r.trigger_fired is False else
               "전이 엣지 - 몫 배정 불가" if not r.assignable else "")
        # 산문이 '조건 충족 · 환원 일치' 를 하드코딩하지 않게, 실제로 무엇을
        # 검사했는지 그대로 싣는다. 부재는 부재라고 말해야 한다.
        cond_state = ("없음" if not t.conditions else
                      "측정불가" if not r.cond_measurable else
                      "충족" if r.cond_satisfied else "미충족")
        edges.append(Edge(channel=t.channel, event_type=t.trigger.ident,
                          verdict=r.verdict, applied=r.applies_today, why_not=why,
                          cond_state=cond_state,
                          reduction_state=r.reduction if r.reduction != "—" else "미실행"))

    gwin = next((s for s in shares if s.window.kind == "gap"), None)
    gcov = gap_covariate(lake, ticker, day, gwin.log_ret) if gwin is not None else None
    # 셀 판정 전량을 trace 에 남긴다 (18R). collect_trace 밖이면 record 는 no-op -
    # 라이브러리 경로·테스트는 영향 없고, __main__ 만 파일로 떨군다.
    trace("cell.inputs", ticker=ticker, day=day, instrument_id=instrument_id,
           day_total=day_total, types=types, series_z=zs, anomalous=anomalous,
           route=cell_route, after_close=len(after_close), windows=len(shares))
    for t, r in reports:
        trace("edge.verdict", channel=t.channel,
               trigger=f"{t.trigger.kind}:{t.trigger.ident}",
               exposure=f"{t.exposure.ident}/{t.exposure.transform}",
               verdict=r.verdict, n=r.n, p2=r.p, applied=r.applies_today,
               moderation=r.moderation, reduction=r.reduction,
               cond_today=r.cond_today, trigger_note=r.trigger_note,
               reason=r.reason)
    for s in screens:
        trace("grid.screen", **s)
    if gcov is not None:
        trace("gap.covariate", factor_ret=gcov.factor_ret, n=gcov.n,
               beta=[gcov.beta_lo, gcov.beta_hi], explained=gcov.explained,
               opposed=gcov.opposed, reason=gcov.reason)

    # ── 검정 층을 **산문보다 먼저** 돌린다: 크기 주장(ATT)이 산문의 입력이다 ──
    # 가법 제약은 Σ|ATT| 를 층 예산과 맞추는 **셀 단위** 검산이라 개별 시행이 다
    # 끝나야 판정된다. 예전 순서(narrate → 검정 층)로는 산문이 크기를 인용할
    # 자격을 모르는 상태에서 문단을 썼다 - SEM 시절 '기여 먼저, 모순 나중' 이
    # 만든 오독이 순서 축에서 반복되는 것이다.
    #
    # 가설 층의 판정은 (타입 × 노출) 수준이라 거칠다. 검정 에이전트가 관측된
    # 슬롯(술어·단계·역할·신규성)으로 쪼개 시행을 설계하고, 위약·사전추세·균형
    # 진단을 통과한 것만 함의로 넘긴다. `probe_brief` 가 다리다 - 가설 층이
    # 이미 본 것을 첫 입력으로 줘서 같은 것을 두 번 탐색하지 않게 한다.
    # 실측(CONTRACT.SIGNING 07-29): 에이전트가 PREFERRED_BIDDER(우선협상대상자)
    # 를 골라 ATT +1.13%p p=0.000 을 냈다 - 코드에 박아둔 단계 목록에는 없던 값이다.
    verif: list[str] = []
    claims: list[tuple[str, float]] = []
    verified_imps = []
    if reports:
        from .trial import probe_brief
        from .verifier import say_implications, verify
        # `reports` 는 (튜플, 보고서) 쌍 목록이다 - `probe_brief` 는 두 평행
        # 목록을 받아 zip 한다. 쌍을 그대로 넘기면 r 이 튜플이 된다(실측 즉사).
        brief = probe_brief([t for t, _ in reports], [r for _, r in reports],
                            screens, memory)
        for et in sorted({t.trigger.ident for t, r in reports
                          if t.trigger.kind == "점" and r.verdict == "성립"}):
            imps, vlog = verify(lake, day, etype=et, layer="고유", ask=ask, brief=brief)
            verif += [vlog, say_implications(imps)]
            verified_imps += [i for i in imps if i.credible]
            # 예산을 쓰는 것은 **설명 층으로 넘어가는** 함의뿐이다. 접힌 함의
            # (사전추세·균형 실패)는 인용되지 않으므로 예산도 쓰지 않는다.
            # 이름은 함의 문장의 머리(타입(시행))다 - 형식이 바뀌어도 잘린 문장이
            # 이름이 될 뿐 깨지지 않는다.
            claims += [(i.claim.split(" 가 ", 1)[0][:40], i.att)
                       for i in imps if i.credible and i.att is not None]

    # ── 가법 제약 (SEM 과대식별 검산의 대체). 판정은 코드가 한다 ──────────
    sigma, sigma_why = resid_sigma(lake, instrument_id, day)
    additive = None
    if claims:
        additive = AdditiveBudget(
            claims=tuple(claims), budget=idio, sigma=sigma,
            reason=("" if idio is not None and sigma is not None else
                    "고유 예산 미계측 (ar_ind 없음) - 청구할 예산 자체가 없다"
                    if idio is None else sigma_why))

    story = narrate(ticker=ticker, name=instrument_id[:20], day=day, route=cell_route,
                    rows=rows, grounded=labels, after_close=tuple(after_close),
                    edges=tuple(edges), gap_cov=gcov, additive=additive,
                    layers=() if idio is None else (("시장", mkt), ("고유", idio)))

    # 신뢰성 검사 — **성립 엣지의 주장을 도구 표면으로 되짚는다**(§납품 등급).
    #
    # 왜 여기인가: 게이트가 '성립' 을 낸 것은 그 튜플의 패널이 유의했다는 뜻일 뿐이고,
    # "그래서 오늘 이 종목이 그것 때문에 움직였다" 는 별개 주장이다. 실측으로 그 간극이
    # 드러났다 - 000660 06-01 은 게이트를 통과한 엣지가 있는데도 기저율로 보면 과거
    # 861일 중 60.5% 가 같은 폭 이상 움직인 평범한 날이고, 동종 대비로는 업종 중앙값과
    # **반대** 방향이었다. 두 사실 중 어느 것도 게이트가 보지 않는다.
    trust_block: list[str] = []
    if edges:
        from .trust import Claim, check, report as trust_report
        # 방향은 **식별집합**에서만 온다. 구간이 0 을 품으면 방향을 주장하지 않는다
        # (sign=0) - 그러면 신뢰성 검사도 부호 있는 근거를 요구하지 않는다.
        def _sign(e) -> int:
            if e.iset_lo is not None and e.iset_lo > 0:
                return 1
            if e.iset_hi is not None and e.iset_hi < 0:
                return -1
            return 0

        cs = [Claim(text=f"{e.channel}·{e.event_type} 가 오늘을 움직였다",
                    kind="사건", refs=(), sign=_sign(e), etype=e.event_type)
              for e in edges if e.applied]
        if cs:
            trust_block = ["", "── 신뢰성 검사 (주장 -> 도구) " + "─" * 26,
                           trust_report(check(lake, cs, day=day,
                                              instrument_id=instrument_id,
                                              grounded=labels))]

    block = ["", "── 튜플 · 패널 게이트 " + "─" * 40]
    if not types and not anomalous:
        # 부재≠판정: z 를 못 잰 것(가격계열 결손)과 조용한 것(|z|<2 관측)은 다르다.
        quiet = ("계열 z 미계측 (가격계열 결손 - 발화 판정 불가)" if not zs
                 else "계열 이상 없음 (" + " · ".join(f"{f} z={z:+.1f}" for f, z in sorted(zs.items())) + ")")
        block.append(f"장중 접지 사건이 없고 {quiet} - 가설 단계를 건너뛴다.")
    if reports:
        block.append(f"검정 규약: 산업층 이중차감 ar · 양측 p₂ · 셀 Bonferroni "
                     f"α=0.05/{len(reports)} (학술 수리 ①②③)")
        block += verif
        # 예산 검산을 블록에도 싣는다 - 산문과 블록이 같은 객체에서 나와야 한다.
        if additive is not None:
            block.append("[가법 제약] " + additive_say(additive))
    for t, r in reports:
        cond = " ∧ ".join(f"{v.ident}/{v.transform}{v.comparator}p{v.percentile:.0%}"[:28]
                          for v in t.conditions) or "—"
        apply_say = ("오늘 적용" if r.applies_today else
                     "오늘 부적용 - " + ("조건 측정불가 (판정불가)" if not r.cond_measurable else
                                        "조건 미충족" if r.cond_satisfied is False else
                                        "환원 불일치" if r.reduction.startswith("불일치") else
                                        "방아쇠 미발화" if r.trigger_fired is False else
                                        "패널 미성립"))
        block += [f"[{t.channel}] {t.trigger.kind}:{t.trigger.ident[:44]}",
                  f"    조건 {cond} · 노출 {t.exposure.ident}/{t.exposure.transform}",
                  f"    환원(가설): {t.reduction_note[:90]}",
                  f"    패널: {r.line}",
                  f"    오늘: {r.cond_today or ('미평가 - 패널이 먼저 서야 한다' if t.conditions else '조건 없음')} → **{apply_say}**",
                  f"    환원 검사: {r.reduction}"]
        if r.trigger_note:
            block.append(f"    {r.trigger_note}")
        if r.moderation:
            block.append(f"    {r.moderation}")
        if r.counterfactual:
            block.append(f"    반사실: {r.counterfactual}")
    if memory:
        block.append("회상(과거 셀): " + " | ".join(memory[:3]))
    if rejected:
        block.append(f"거부된 제출 {len(rejected)}건 (검증기가 죽임): "
                     + " | ".join(x[:60] for x in rejected[:3]))
    if screens:
        block.append("")
        block.append("── 격자 스크린 (탐색 - 방향 사후·p 양측. 확증은 튜플 게이트만) " + "─" * 8)
        for s in screens:
            if "p2" in s:
                block.append(f"  {s['type'][:40]:<40} {s['exposure']:<14} n={s['n']:<5} "
                             f"p₂={s['p2']:.3f} 방향{s['direction']} "
                             f"상위 {s['hi'] * 100:+.2f}% vs 하위 {s['lo'] * 100:+.2f}%")
            else:
                block.append(f"  {s['type'][:40]:<40} {s['status']}")
    # 신뢰성 검사는 **게이트 블록 뒤**에 붙는다: 게이트가 무엇을 성립시켰는지 먼저
    # 보이고, 그 성립이 오늘 셀의 주장을 실제로 지지하는지 그 다음에 보여야 한다.
    honest = (render(rows) + "\n\n" + story + "\n" + "\n".join(block)
              + "\n".join(trust_block))
    if ask is None:
        return honest
    # ── 쉬운 설명 (토스식) ─────────────────────────────────────────────────
    # 사람이 이 화면을 여는 순간은 **가격이 급변했을 때**와 **시장과 다르게 움직일
    # 때**다. 그때 필요한 답은 'p=0.004' 가 아니다. 두 산문은 **같은 사실**에서
    # 나온다 - 토스식은 위에서 확정된 것만 옮기고 새로 주장하지 않는다.
    from .evidence import narrative_allowed, news_objectset, say_save
    from .plain import PlainError, context, dual, narrate_plain, recent_window
    applied = [(t, r) for t, r in reports if r.applies_today]
    # 서사 경로 허가는 **코드가** 낸다 - 모델이 고르면 편한 쪽(서사)으로 도망간다.
    allow_narr, why_narr = narrative_allowed(credible=0, applied_edges=len(applied))
    objs = news_objectset(lake, instrument_id, day)
    stats = [{"ref": f"s{i}", "kind": "튜플 패널", "channel": t.channel,
              "etype": t.trigger.ident, "verdict": r.verdict, "n": r.n,
              "p": None if r.p is None else round(r.p, 4)}
             for i, (t, r) in enumerate(applied, 1)]
    stats += [
        {"ref": f"s{len(applied) + j}", "kind": "일단위 ATT", "claim": i.claim,
         "att": i.att, "p": i.p, "n": i.n_pairs,
         "pretrend_ok": i.pretrend_ok, "balanced": i.balanced,
         "placebo": i.placebo,
         "series_lineage": {
             "dataset": "analysis mart", "view": "v_daily", "field": "ar_ind",
             "entities": [instrument_id], "grain": "daily", "end": day,
             "as_of": day, "transforms": ["matched ATT", "bias adjustment"],
             "filters": {"layer": "고유"},
             "matching": "treated/control matched pairs"}}
        for j, i in enumerate(verified_imps, 1)
    ]
    ctx = context(
        ticker_name=labels_name(lake, ticker) or ticker,
        day_log=day_total, idio_log=(day_total if idio is None else idio),
        route_kind="고유" if idio is not None and abs(idio) >= abs(day_total) * 0.35
        else "시장", market_name="코스피 대형주",
        recent=recent_window(shares, labels),
        established=[f"{t.channel} - {t.trigger.ident.split('.')[-1]}"
                     for t, _r in applied],
        overnight=[], unexplained_top=not rows or max(
            rows, key=lambda r: abs(r.share.log_ret)).share.window.kind == "residual",
        idio_qualified=idio is not None)
    try:
        plain, bundles = narrate_plain(
            ask, ctx, news=objs, stats=stats, cell=ticker, day=day, layer="고유",
            allow_narrative=allow_narr)
        # 묶음은 **만든 그 자리에서** 적재한다. 꼬리표 id 만 산출물로 나가고 본문이
        # 아무 데도 없으면, 나중에 그 문장이 무엇에 근거했는지 되짚을 방법이 없다.
        note = f"\n[서사 경로] {why_narr}"
        if line := say_save(bundles):
            note += f"\n{line}"
        return dual(honest, plain + note, bundles)
    except PlainError as e:
        return dual(honest, f"(쉬운 설명 생성 실패 - 계약 위반: {e})")


if __name__ == "__main__":
    import json
    import os
    from datetime import datetime as _dt
    from pathlib import Path
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    from ..adapters.llm import DeepSeekClient, TracingClient
    from ..observability import collect_trace
    client = TracingClient(DeepSeekClient(
        os.environ["DEEPSEEK_API_KEY"],
        os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")))
    with collect_trace() as tr:
        out = run_cell(CausalLake(), client.complete_json, *sys.argv[1:])
    print(out)
    # 프롬프트·응답 원문은 stdout 금지(observability 계약) - 파일로만 흐른다.
    d = Path(os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill")) / "traces"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sys.argv[1]}_{sys.argv[3]}_{_dt.now():%H%M%S}.jsonl"
    f.write_text("\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in tr),
                 encoding="utf-8")
    print(f"\n[trace] {len(tr)}건 → {f}")
