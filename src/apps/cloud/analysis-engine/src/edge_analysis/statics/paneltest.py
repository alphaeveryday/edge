"""검정 에이전트 — 튜플의 결정론 전개와 타입 수준 패널 게이트. LLM 없음.

설계 §17: 검정자는 고르지 않는다. 창·컷·표본·유의수준은 전역 상수(vocab)이고,
대상군·위약군은 튜플의 노출원에서 **유도**된다.

이 판이 구현하는 명시 항목 전부:
  점 방아쇠 (§6)      과거 그 타입 사건일 패널
  계열 방아쇠 (§6)    그 계열족 혁신의 |z|≥2 이상일 패널 (z 창 60d, 전역 상수)
  조건 = INUS (§6)  술어로 패널을 **조건화**한다 - 조건 미충족 표본에서의
                      용량-반응은 이 가설의 검정이 아니다. 오늘 셀의 충족 여부가
                      적용(applies_today)을 정한다: 성립해도 오늘 미충족이면 부적용
  반사실 쌍 (§14)     조건 미충족 부류의 효과를 함께 낸다. 반대 사례 < 5 면
                      침묵(positivity) - 외삽 금지
  환원 검사 (§8)      오늘 같은 타입 사건의 횡단면이 패널과 같은 방향인가
  용량-반응 (§4)      노출 상위(≥컷) vs 하위, 층=사건일 순열 귀무

감사에서 배운 계약: PIT(과거 패널은 day 미만 - 오늘이 자기 패널에 못 들어간다) ·
결정론(SEED 고정 = 재실행 동일 판정) · 부재 선언(못 재는 조합 = 판정불가+사유) ·
선언=배선(이 도크스트링의 모든 항목이 아래 코드에 있다).

지금 잴 수 있는 피처는 price_daily(3.7년) 파생 4종이다. 관계 노출(전이 패널,
§16)은 다음 판이다 - 산업쌍 위약(같은 속성 ∧ 관계 없음) 설계가 필요하다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..observability import record
from .gates import EdgeVerdict, edge_gate
from .vocab import ALPHA, EXPOSURE_CUT, MIN_N, RELATIONS, HypothesisTuple

PERMS = 1000        # 전역 상수 - 가설별 지정 금지 (§13)
SEED = 0
Z_ANOM = 2.0        # 계열 방아쇠의 이상 임계 (혁신 z, 60d 창)
MIN_OPPOSITE = 5    # positivity: 반대(미충족) 사례가 이보다 적으면 반사실 침묵
MIN_TODAY = 5       # 환원 검사의 오늘 횡단면 최소 표본

# (계열족, 변환) → 피처 컬럼. **여기 없는 조합은 아직 못 잰다** (부재 선언).
# screen_tuples(measurable=…) 가 이 목록으로 생성 시점에 관문을 친다 - 못 재는
# 조합은 패널이 n=0 을 내기 전에 죽는다.
FEATURES = {
    ("가격잔차", "누적"): "cum20",
    ("가격잔차", "변동성"): "vol20",
    ("거래량", "수준"): "tv20",
    # 노출은 PIT 판(전일까지). 당일 판 tv_chg 는 **방아쇠 전용** - 섞으면 동시성이다.
    ("거래량", "변화"): "tv_chg_pit",
    # ── PIT 스냅샷(v_pit)이 연 축. 전부 **어제 값** = 느린 상태 ──
    ("주주", "수준"): "fr_lvl",       # 외국인지분율
    ("주주", "변화"): "fr_chg",       # 외국인지분율 20일 변화(%p)
    ("신용", "수준"): "cr_lvl",       # 신용융자잔고율
    ("공매도", "수준"): "sr_lvl",     # 차입공매도잔고비율
    ("배수", "수준"): "pbr_lvl",      # PBR(IFRS-별도)
    ("주식수", "변화"): "sh_chg",     # 상장주식수 20일 변화율 - S주식수 채널의 관측변수
    ("주식수", "수준"): "tr_lvl",     # 자기주식수 / 상장주식수 (자사주 보유 비율)
    ("수급", "누적"): "fl_cum20",     # 20일 누적 외국인 순매수 / 시총
    # ── 민감도: 공통 계열을 종목 축으로 옮기는 유일한 변환 (60일 롤링 기울기) ──
    ("지수잔차", "민감도"): "beta_m",
    # **국면** = 시장 수익의 20일 변동성. "왜 **지금**" 의 최대 답이 축에 없었다:
    # 2026 sd 5.46% vs 2022-25 1.3~2.1% (KRX 독립 소스 확인). 패널의 84%가 다른
    # 국면인데 하나의 τ 를 추정해 오늘에 적용했다. 조절자로 넣으면 국면별 CATE 가
    # 나온다 - 하루에 값이 하나라 횡단면은 못 가르지만 **날짜 사이**를 가른다.
    ("국면", "수준"): "regime_lvl",  # 시장 로그수익률에 대한 β
    ("거시", "민감도"): "fx_beta",     # 원/달러 변화율에 대한 β - FX환 채널의 관측변수
    ("금리", "민감도"): "rate_beta",   # 국고 10년 일변화(%p)에 대한 β - 할인율 채널
    ("섹터", "민감도"): "beta_s",      # 산업 평균 초과수익에 대한 β - 섹터층의 노출
    # ── 재무제표(v_fin, ASOF). 회계연도 값이라 가장 느린 상태 = 조건 자리 ──
    ("레버리지", "수준"): "lev_lvl",   # 차입금의존도
    ("레버리지", "변화"): "lev_chg",   # 차입금의존도 YoY 변화(%p)
    ("수익성", "수준"): "prof_lvl",    # ROE
    ("수익성", "변화"): "prof_chg",    # ROE YoY 변화(%p)
    ("성장", "수준"): "grow_lvl",      # 매출액증가율 YoY
    ("재무파생", "수준"): "icov_lvl",  # 이자보상배율 - 금리 충격 내성
}
# 계열 방아쇠의 혁신값 (z 를 재는 대상). 수급은 **아직 없다** - flow_z 는 3개 투자자
# 유형의 최댓값으로 발화를 정하는데 패널 프레임은 외국인 하나만 담는다. 기준이 두
# 곳에서 갈리면 접지가 무너지므로(series_z 도크스트링), 통합 전까지는 노출·조건으로만 쓴다.
_INNOVATION = {"가격잔차": "ar", "거래량": "tv_chg", "거시": "macro"}

# 층 → 결과변수. 설명 대상이 층마다 다르므로 y 도 다르다. 하나로 고정하면 시장·섹터
# 가설이 구조적으로 0 을 받는다(설명하려는 성분을 y 에서 이미 뺐으므로).
LAYER_Y = {"고유": "y_고유", "섹터": "y_섹터", "시장": "y_시장"}

# 층 → **그 층을 설명할 자격이 있는 노출**. 층마다 y 가 다르니 노출도 달라야 한다.
# 배선 없이 두면 "종목 거래량이 시장 전체 수익을 설명한다" 같은 가설이 관문을
# 통과한다 - 어휘가 아니라 열 목록이 되는 지점이다.
#
#   시장 (y=lr, 원수익): 시장 수익은 전 종목 공통이다. 종목 간 차이를 만드는 것은
#       **공통 충격에 대한 민감도**뿐 - 그것이 채널(FX환·K위험)이 뜻하는 바다.
#   섹터 (y=ar, 시장 차감): 시장은 빠지고 산업 공행이 남았다. 산업을 가르는 특성만.
#   고유 (y=ar_ind, 이중차감): 남은 것은 종목 자신 - 전부 허용.
LAYER_EXPOSURES: dict[str, frozenset[tuple[str, str]] | None] = {
    "시장": frozenset({("지수잔차", "민감도"), ("거시", "민감도"), ("금리", "민감도")}),
    "섹터": frozenset({("섹터", "민감도"), ("지수잔차", "민감도"), ("거시", "민감도"),
                      ("금리", "민감도"),
                      ("배수", "수준"), ("주주", "수준"),
                      ("레버리지", "수준"), ("수익성", "수준"), ("성장", "수준")}),
    "고유": None,   # None = 제한 없음
}



def _base(day: str, clock: str = "00:00:00") -> str:
    """공유 표면 + 파생 피처. clock 은 as_of 시각 - 역사 패널은 자정(오늘 정보 차단),
    **환원 검사만** 23:59:59 (오늘 횡단면 반응을 보는 게 목적이라 오늘 사건이 보여야
    한다). 포팅 때 이 인자를 잃어 환원 검사가 오늘 n=0 으로 죽었다 (18R 라이브)."""
    from ..adapters.sql_surface import views_sql
    return "WITH " + views_sql(f"TIMESTAMP '{day} {clock}'", f"DATE '{day}'",
                            "rdb.public.") + """,
_mz AS (
    -- **날짜 수준** 거시 발화 z. macro_z() 와 같은 정의(index·fx·rate 합집합의
    -- 60일 창 z, 최대 |z|)를 쓴다 - 발화 기준이 두 곳에서 갈리면 접지가 무너진다.
    -- 종목 축이 없으므로 날짜로 조인해 전 종목에 같은 값을 싣는다. 이 계열은
    -- **방아쇠로만** 쓴다(노출은 민감도가 담당) - 공통값은 횡단면을 못 가른다.
    SELECT trade_date, max(abs(z)) AS z_macro
    FROM (
        SELECT trade_date, (change_pct - avg(change_pct) OVER r)
                           / NULLIF(stddev_samp(change_pct) OVER r, 0) AS z
        FROM (
            SELECT trade_date, 'index/' || symbol AS series, change_pct FROM s3_index_daily
            UNION ALL SELECT trade_date, 'fx/' || symbol, change_pct FROM s3_fx_daily
            UNION ALL SELECT trade_date, 'rate/10y',
                             year10 - lag(year10) OVER (ORDER BY trade_date)
                      FROM s3_rates_daily
        ) _m
        WINDOW r AS (PARTITION BY series ORDER BY trade_date
                     ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)
    ) _w
    WHERE z IS NOT NULL
    GROUP BY trade_date
),
_rt AS (
    -- 금리 노출의 원천. **수준이 아니라 변화**를 쓴다: 오늘 움직임을 설명하는 것은
    -- 금리 수준이 아니라 그 변화다(수준은 이미 가격에 있다). 곡선 12만기 중 10년을
    -- 쓰는 이유는 밸류에이션 할인율의 관례 대리이고, 단기/기울기는 정책·경기 기대라
    -- 뜻이 다르다 - 하나의 족에 섞으면 계수 해석이 무너지므로 10년만 둔다.
    SELECT trade_date, year10 - lag(year10) OVER (ORDER BY trade_date) AS d10
    FROM s3_rates_daily
),
_fl AS (
    -- 수급 노출 원천 = curated 투자자별 매매(statics.flowhist → flow_daily).
    -- 2022-01~ · 4,336종목. s3_investor_value 는 14개월(2025-06~)뿐이라 20일 창을
    -- 쌓고 나면 패널이 얇았다. v_flow(RDB)는 12일치.
    -- 단위는 **원** (curated 가 만원이라 적재 때 1e4 를 곱했다) - 시총(백만원)과
    -- 나눌 때 1e6 을 곱하는 쪽과 자릿수가 맞는다.
    SELECT i.instrument_id, fl.trade_date, fl.for_net AS fl_val
    FROM flow_daily fl
    JOIN v_instrument i ON i.ticker = fl.ticker
),
f AS (
    -- PIT 상태(v_pit)는 전부 **어제 값**이다. 조건은 느린 상태이고, 오늘 값을 쓰면
    -- 마감 후 회계를 원인 자리에 놓는 것이 된다(판정자들이 8셀 내내 기각한 오류).
    -- 창 규율은 기존과 같다: w20 = [t-20, t-1] · 당일 제외.
    -- 결과변수 셋을 나란히 싣는다. **층마다 설명 대상이 다르다**:
    --   고유 = ar_ind (시장·산업 이중차감) · 섹터 = ar (시장만 차감) · 시장 = lr (원수익)
    -- 하나로 고정하면 다른 두 층의 가설이 구조적으로 0 을 받는다 - 시장층 가설이
    -- 설명하려는 mkt×β 를 ar_ind 가 이미 빼버렸기 때문이다.
    SELECT d.instrument_id, d.trade_date,
           d.ar_ind AS y_고유, d.ar AS y_섹터, d.lr AS y_시장,
           d.ar_ind AS ar,
           sum(d.ar_ind)      OVER w20 AS cum20,
           stddev_samp(d.lr)  OVER w20 AS vol20,
           avg(d.volume)      OVER w20 AS tv20,
           -- **당일** 거래량 비 - 방아쇠 전용이다 (오늘 튀었나는 오늘 값으로 판정).
           d.volume / NULLIF(avg(d.volume) OVER w20, 0) - 1 AS tv_chg,
           -- **노출 전용 PIT 판**: 전일까지의 거래량 비. 당일 거래량을 노출로 쓰면
           -- 처치가 결과와 **동시 결정**이라 역인과를 유의로 읽는다 - 실측
           -- (000660 07-29 C3): '실적일 회전 급증 종목이 더 올랐다 p=0.000 +1.77%p'
           -- 는 '많이 오른 종목이 거래량도 많았다' 였다. 코드 게이트는 p 만 보므로
           -- 이 종류의 결함을 구조적으로 못 잡는다.
           LAG(d.volume, 1) OVER wi
             / NULLIF(avg(d.volume) OVER w20, 0) - 1 AS tv_chg_pit,
           LAG(q.foreign_ratio, 1) OVER wi AS fr_lvl,
           LAG(q.foreign_ratio, 1) OVER wi
             - LAG(q.foreign_ratio, 21) OVER wi AS fr_chg,
           LAG(q.credit_ratio, 1)  OVER wi AS cr_lvl,
           LAG(q.short_ratio, 1)   OVER wi AS sr_lvl,
           LAG(q.pbr, 1)           OVER wi AS pbr_lvl,
           LAG(q.shares, 1) OVER wi
             / NULLIF(LAG(q.shares, 21) OVER wi, 0) - 1 AS sh_chg,
           -- 20일 누적 외국인 순매수 / 20일 평균 일거래대금 = "며칠치 거래대금만큼
           -- 순매수했나". 시총으로 나누면 PIT(2025-07~)에 묶여 이력이 잘린다 -
           -- 거래대금은 v_daily 라 2022-11 까지 깊다. 척도 없는 양인 건 같다.
           sum(x.fl_val) OVER w20
             / NULLIF(avg(d.close_price * d.volume) OVER w20, 0) AS fl_cum20,
           LAG(q.treasury_ratio, 1) OVER wi AS tr_lvl,
           -- 민감도: 공통 계열은 하루에 값이 하나라 그 자체로는 횡단면을 못 가른다.
           -- 종목마다 다른 것은 **민감도**다. 60일 롤링 회귀 기울기, 당일 제외.
           -- mkt_lr·산업평균은 v_daily 가 직접 안 주므로 잔차에서 복원한다
           -- (정의가 두 곳에 있으면 갈리므로 여기서 새로 계산하지 않는다):
           --   mkt_lr   = lr - ar_lr        · 산업평균 = ar_lr - ar_ind
           -- beta_s 는 **KRX 업종지수**(v_sector·v_sector_ret)에 대한 β 다. 산업 평균
           -- 대신 KRX 가 산출하는 지수를 쓴다 - 섹터를 우리가 정의하지 않는다.
           -- x = 업종지수 수익 - 시장 수익 (시장직교) · y = 시장 차감 종목 수익.
           -- isfinite 로 감싼다: regr_slope 는 x 분산이 0 이면 NULL 이 아니라 **NaN**
           -- 을 낸다(산업 표본 <5 · fx 결측 구간). NaN 은 pctile 을 조용히 오염시켜
           -- 상·하위 분할을 어긋나게 만든다 - 부재는 NULL 로 말해야 한다.
           CASE WHEN isfinite(regr_slope(d.lr, d.lr - d.ar_lr) OVER w60)
                THEN regr_slope(d.lr, d.lr - d.ar_lr) OVER w60 END AS beta_m,
           CASE WHEN isfinite(regr_slope(d.ar_lr, sr.sec_lr - (d.lr - d.ar_lr)) OVER w60)
                THEN regr_slope(d.ar_lr, sr.sec_lr - (d.lr - d.ar_lr)) OVER w60 END AS beta_s,
           stddev_samp(d.lr - d.ar_lr) OVER w20 AS regime_lvl,
           sm.code AS sector_code,
           -- 매칭 공변량: 전일 시총 (RCT 근사의 대조군 선택에 쓴다). 당일 값을 쓰면
           -- 매칭 자체가 결과를 본다 - 노출과 같은 PIT 규율이다.
           LAG(q.mktcap, 1) OVER wi AS mcap_pit,
           CASE WHEN isfinite(regr_slope(d.lr, fx.change_pct / 100) OVER w60)
                THEN regr_slope(d.lr, fx.change_pct / 100) OVER w60 END AS fx_beta,
           -- 금리 민감도(듀레이션 대리). x 단위가 %p 이므로 계수는 "금리 1%p 상승 시
           -- 로그수익 몇" 이다 - 성장주는 음수여야 한다(할인율 경로). 이것이 없으면
           -- "금리가 올라서 성장주가 빠졌다" 를 **구조적으로 말할 수 없다**: 공통
           -- 충격은 노출 이질성을 통해서만 설명이 되기 때문이다(vocab 도크스트링).
           CASE WHEN isfinite(regr_slope(d.lr, rt.d10) OVER w60)
                THEN regr_slope(d.lr, rt.d10) OVER w60 END AS rate_beta,
           -- **어제** 값이다. macro_z() 가 `< day` 로 개장 전 확정 정보(밤사이 미국장·
           -- 환율)를 쓰므로 패널도 같이 지연시킨다 - 발화 기준이 갈리면 접지가 무너진다.
           LAG(mz.z_macro, 1) OVER wi AS z_macro,
           -- 재무는 회계연도 값이라 as-of 조인이 필요하다. ASOF 가 '그 시점에 가용한
           -- 가장 최근 회계연도' 를 정확히 집는다 - 손으로 쓰면 어긋난다.
           fn.borrow_dep AS lev_lvl, fn.borrow_dep_chg AS lev_chg,
           fn.roe        AS prof_lvl, fn.roe_chg      AS prof_chg,
           fn.rev_growth AS grow_lvl, fn.int_cover    AS icov_lvl,
           -- **되돌림** = ln(종가/일중고가). '왜 오르다 떨어졌나' 를 일 단위
           -- 스칼라로 환원한다 - 경로 질문을 창 단위로 쪼개면 일 단위 추정량이 다시 범주
           -- 오류에 빠진다(8차). RDB `price_daily` 는 종가만이고(sql_surface L86
           -- 이 자백한다) 레이크 `s3_price_daily` 에 OHLC 가 있는데 안 묶여 있었다.
           CASE WHEN px.high > 0 AND d.close_price > 0
                THEN ln(d.close_price / px.high) END AS rev
    FROM v_daily d
    LEFT JOIN v_instrument vi ON vi.instrument_id = d.instrument_id
    LEFT JOIN s3_price_daily px ON px.ticker = vi.ticker AND px.trade_date = d.trade_date
    LEFT JOIN v_pit q ON q.instrument_id = d.instrument_id AND q.trade_date = d.trade_date
    LEFT JOIN _fl  x ON x.instrument_id = d.instrument_id AND x.trade_date = d.trade_date
    LEFT JOIN fx_usdkrw fx ON CAST(fx.date AS DATE) = d.trade_date
    LEFT JOIN _rt rt ON rt.trade_date = d.trade_date
    LEFT JOIN _mz mz ON mz.trade_date = d.trade_date
    ASOF LEFT JOIN v_fin fn ON fn.instrument_id = d.instrument_id
                           AND d.trade_date >= fn.available_from
    ASOF LEFT JOIN v_sector sm ON sm.instrument_id = d.instrument_id
                              AND d.trade_date >= sm.as_of
    LEFT JOIN v_sector_ret sr ON sr.code = sm.code AND sr.trade_date = d.trade_date
    WHERE d.ar_ind IS NOT NULL
    WINDOW w20 AS (PARTITION BY d.instrument_id ORDER BY d.trade_date
                   ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
           w60 AS (PARTITION BY d.instrument_id ORDER BY d.trade_date
                   ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING),
           wi  AS (PARTITION BY d.instrument_id ORDER BY d.trade_date)
),
g AS (
    SELECT *,
           (ar - avg(ar) OVER w60) / NULLIF(stddev_samp(ar) OVER w60, 0) AS z_ar,
           (tv_chg - avg(tv_chg) OVER w60) / NULLIF(stddev_samp(tv_chg) OVER w60, 0) AS z_tv_chg
    FROM f
    WINDOW w60 AS (PARTITION BY instrument_id ORDER BY trade_date
                   ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)
)
"""


# 패널들은 전부 뷰(v_event·v_daily·v_instrument) 위에서만 돈다. 시점 클램프는
# 뷰 안에 있으므로 여기서 available_at 을 다시 쓰지 않는다 - 두 번 쓰면 한쪽이
# 낡는다. 남는 것은 **표본 선택**(어느 사건일·어느 분할)뿐이다.
# 처치 구체화: 사건타입 뒤에 술어·단계·역할·신규성 필터가 붙는다 (`{refine}`).
# 비면 그 축은 전체다. 이 넷이 없으면 `CONTRACT.SIGNING` 하나에 MOU 와 확정계약이
# 섞이고 ATT 는 서로 다른 두 처치의 평균이라 0 으로 수렴한다.
_POINT_PANEL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e JOIN v_instrument i ON i.instrument_id = e.instrument_id
    WHERE e.event_type_code = '{etype}'
      AND e.trade_date {cmp} DATE '{day}'
      {refine}
)
SELECT g.instrument_id, g.trade_date, g.{y} AS ar, {cols}
FROM ev JOIN g ON g.instrument_id = ev.iid AND g.trade_date = ev.d
"""


def refine_sql(t) -> str:
    """튜플의 구체화 슬롯 → v_event 필터. 빈 슬롯은 조건을 안 만든다.

    `v_event` 가 predicate_code·lifecycle_stage·role_code·novelty_status 를 이미
    싣고 있다 - 튜플이 안 썼을 뿐이다. 노출이 곧 구체화다.
    """
    g = t.trigger
    bits = []
    for val, col in ((getattr(g, "predicate", ""), "predicate_code"),
                     (getattr(g, "stage", ""), "lifecycle_stage"),
                     (getattr(g, "role", ""), "role_code"),
                     (getattr(g, "novelty", ""), "novelty_status")):
        if val:
            bits.append(f"AND e.{col} = '{val}'")
    return "\n      ".join(bits)


_SERIES_PANEL = """
SELECT instrument_id, trade_date, {y} AS ar, {cols}
FROM g WHERE abs(z_{innov}) >= {z} AND trade_date < DATE '{day}'
"""

_TODAY_ROW = """
SELECT {cols} FROM g WHERE instrument_id = '{iid}' AND trade_date = DATE '{day}'
"""

# 오늘 셀의 계열 혁신 z (발화 판정 + 가설 어포던스의 단일 원천).
_TODAY_Z = """
SELECT z_ar, z_tv_chg FROM g WHERE instrument_id = '{iid}' AND trade_date = DATE '{day}'
"""

# 전이 패널 (§16 관계 노출): 사건 종목과 **관계 있는** 종목(rel=1) vs 없는 종목(0).
# 위약이 '같은 날 시장 전체'인 이유: 날짜 층화 순열이 공통충격을 소거하므로,
# 남는 대비가 정확히 '관계가 있느냐'다.
# 관계식은 주입된다 (19R): SAME_INDUSTRY 는 속성 동일성(v_instrument, PIT 분류),
# 나머지는 타입 있는 1홉(v_link). 전에는 산업 하나만 재면서 이름은 '경로형'이었다.
_REL_EXPR = {
    "SAME_INDUSTRY": "CASE WHEN cp.industry_name = ce.industry_name THEN 1 ELSE 0 END",
}
_REL_LINK = """CASE WHEN EXISTS (
        SELECT 1 FROM v_link l
        WHERE l.link_type = '{ident}' AND l.link_date <= ev.d
          AND ((l.src = ev.iid AND l.dst = g.instrument_id)
            OR (l.dst = ev.iid AND l.src = g.instrument_id))
    ) THEN 1 ELSE 0 END"""

_RELATION_PANEL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e
    WHERE e.event_type_code = '{etype}' AND e.trade_date < DATE '{day}' 
)
SELECT g.instrument_id, g.trade_date, g.{y} AS ar, {cols}, {rel} AS rel
FROM ev
JOIN g   ON g.trade_date = ev.d AND g.instrument_id <> ev.iid
JOIN v_instrument cp ON cp.instrument_id = g.instrument_id
JOIN v_instrument ce ON ce.instrument_id = ev.iid
"""


@dataclass(frozen=True, slots=True)
class EdgeReport:
    """엣지 하나의 패널 판정 + 오늘 적용 판정. 수치는 전부 이 모듈이 계산했다.

    **크기는 없다.** SEM 기여(`contribution`·`ci_lo`·`ci_hi`)를 지웠다 - 사건
    고정효과 기울기 τ̂ 는 기울기인데 산문이 수준으로 읽었고, 구간이 하루 총합과
    안 겹치는 날이 나왔다(자기모순). 게이트는 존재를 판정하고(§11), 크기는 ATT
    경로가 주장하며 예산 검산은 `narrate.AdditiveBudget` 의 가법 제약이 한다.
    """
    verdict: EdgeVerdict
    n: int
    p: float | None
    effect_high: float | None        # 조건 조건화된 패널에서 노출 상위 평균 ar
    effect_low: float | None
    today_exposure_pct: float | None
    cond_today: str = ""             # 오늘 셀의 조건 술어별 충족 (예: "수급/누적 p98 충족")
    cond_satisfied: bool | None = None   # None = 조건 없음 (조건이 있는데 못 잰 것은 아래)
    cond_measurable: bool = True     # False = 조건이 있으나 오늘 못 잼/결측 → 판정불가
    counterfactual: str = ""         # 반사실 쌍 (positivity 통과 시에만 채워진다)
    reduction: str = "—"             # 환원 검사: 일치 · 불일치 · 표본부족 · —(미실행)
    assignable: bool = True          # False = 엣지 검정만 유효, 몫 배정 불가 (전이 등)
    moderation: str = ""             # 조절 대비 (조건이 있을 때 - 교호항의 최소형)
    trigger_fired: bool | None = None   # 계열 방아쇠의 오늘 발화 (None = 점 또는 미계측 처리 전)
    trigger_note: str = ""           # 오늘 방아쇠 어법 (계열에서만)
    reason: str = ""
    # **재표집이 곧 주장이다**(21R). 무엇을 섞었는지가 무엇을 검정했는지를 정한다:
    #   "label"  노출 라벨을 날짜 층 안에서 섞는다 → "이 경로가 맞나" (귀속 근거 O)
    #   "pair"   짝 부호를 섞는다 (날짜 층화 내장)  → 처치-대조 대비  (귀속 근거 O)
    #   "date"   날짜를 섞는다 → "이 날이 특별한가" → **순환**: 셀은 큰 이상수익으로
    #            선정됐으므로 이미 특별하다. 그래서 귀속 자격을 박탈한다(아래 게이트).
    # 이름표로만 두면 다음 검정기가 조용히 date 귀무를 들고 온다 - 그래서 읽는다.
    null_kind: str = "label"

    @property
    def applies_today(self) -> bool:
        """오늘 셀에 몫을 배정할 자격. 성립 + 배정가능 + 조건 미위반 +
        환원 미불일치 + (계열이면) 오늘 방아쇠 발화.

        **조건을 못 재면 적용이 아니다** (§11): 결측을 충족으로 세면 부재가 성립을
        위장한다. 실측(042700 07-31 C1): '공매도/수준 오늘 결측' 이 `충족 True` 로
        찍혀 INUS 조건이 있는 엣지가 조건 검사 없이 몫을 받았다."""
        return (self.verdict == "성립" and self.assignable
                and self.null_kind != "date"      # 날짜 귀무는 순환 - 몫 배정 금지
                and self.cond_measurable
                and self.cond_satisfied is not False
                and not self.reduction.startswith("불일치")
                and self.trigger_fired is not False)

    @property
    def line(self) -> str:
        if self.verdict == "판정불가":
            return f"판정불가 (n={self.n}) — {self.reason}"
        hi = f"{self.effect_high * 100:+.2f}%" if self.effect_high is not None else "?"
        lo = f"{self.effect_low * 100:+.2f}%" if self.effect_low is not None else "?"
        te = (f" · 오늘 노출 p{self.today_exposure_pct * 100:.0f}"
              if self.today_exposure_pct is not None else "")
        why = f" — {self.reason}" if self.reason else ""
        return (f"{self.verdict} (n={self.n}, p={self.p:.3f}, "
                f"상위 {hi} vs 하위 {lo}{te}){why}")


def _unmeasurable(reason: str) -> EdgeReport:
    return EdgeReport("판정불가", 0, None, None, None, None, reason=reason)


# 거시 계열의 혁신 (20R). 표현력 측정이 '환율 급등'·'전일 미국 반도체 지수 하락'을
# 방아쇠로 요구했는데 어휘엔 `거시` 가 있고 계산기가 없었다 - 어휘 확장이 아니라
# 계산기 확장이 답이다(상류 온톨로지 무관).
#
# **왜 직전 거래일인가**: 미국 종가의 available_at 은 KST 익일 05:00 이다. 한국 09:00
# 개장 전에 알려지므로 오늘 셀이 쓸 수 있다 - 그리고 오늘자 미국 종가는 아직 없다.
# 환율도 같은 창을 쓴다(뉴욕 세션 종료 기준).
_MACRO_Z = """
WITH m AS (
    SELECT trade_date, 'index/' || symbol AS series, change_pct FROM s3_index_daily
    UNION ALL
    SELECT trade_date, 'fx/' || symbol, change_pct FROM s3_fx_daily
    UNION ALL
    SELECT trade_date, 'rate/10y', year10 - lag(year10) OVER (ORDER BY trade_date)
    FROM s3_rates_daily
),
w AS (
    SELECT series, trade_date, change_pct,
           avg(change_pct)          OVER r AS mu,
           stddev_samp(change_pct)  OVER r AS sd
    FROM m
    WINDOW r AS (PARTITION BY series ORDER BY trade_date
                 ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)
)
SELECT series, trade_date,
       (change_pct - mu) / NULLIF(sd, 0) AS z
FROM w
WHERE trade_date = (SELECT max(trade_date) FROM w WHERE trade_date < DATE '{day}')
  AND sd IS NOT NULL
ORDER BY abs((change_pct - mu) / NULLIF(sd, 0)) DESC
"""


def macro_z(lake, day: str) -> tuple[float, str]:
    """(거시 z, 무엇이 움직였나). 계열족 하나에 여러 계열이 있으므로 **절댓값 최대**를
    그 족의 혁신으로 삼고 **누가 그랬는지 이름을 같이 낸다** - 이름 없이 z 만 주면
    '거시가 튀었다'가 검정 불가능한 문장이 된다.
    """
    try:
        rows = [r for r in lake.sql(_MACRO_Z.format(day=day)) if r[2] is not None]
    except Exception as e:                  # noqa: BLE001 - 부재는 **사유와 함께**
        record("series.unmeasured", family="거시", why=f"{type(e).__name__}: {e}"[:160])
        return 0.0, ""
    if not rows:
        return 0.0, ""
    # 상위 셋을 같이 낸다. 최댓값 하나로 접으면 정보가 죽는다 - 실측에서
    # `fx/EURUSD z=+2.9` 가 `index/SOXX -5.4%` 를 가렸다. 어느 계열이 움직였는지가
    # 곧 가설의 내용이므로, 발화 판정은 최댓값으로 하되 후보는 보여준다.
    top = " · ".join(f"{r[0]} z={float(r[2]):+.1f}" for r in rows[:3])
    return float(rows[0][2]), f"{rows[0][1]} — {top}"


# 수급 계열의 혁신 (20R). **전일** 수급을 쓴다 - 투자자별 집계는 장 마감 후
# 18:00 KST 에 공표되므로 오늘 장중 움직임의 방아쇠로 인용할 수 없다(그건 동시발생이지
# 원인이 아니다). 어제 수급은 오늘 개장 전에 알려져 있으니 방아쇠 자격이 있다 -
# 거시(직전 미국 거래일)와 같은 규율이다.
_FLOW_Z = """
, flow_src AS (
    SELECT iv.trade_date, iv.investor_type, iv.net_value
    FROM s3_investor_value iv
    JOIN v_instrument i ON i.ticker = iv.ticker
    WHERE i.instrument_id = '{iid}'
      AND iv.investor_type IN ('foreign', 'institution_total', 'individual')
),
fw AS (
    SELECT investor_type, trade_date, net_value,
           avg(net_value)         OVER r AS mu,
           stddev_samp(net_value) OVER r AS sd
    FROM flow_src
    WINDOW r AS (PARTITION BY investor_type ORDER BY trade_date
                 ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)
)
SELECT investor_type, trade_date, (net_value - mu) / NULLIF(sd, 0) AS z
FROM fw
WHERE trade_date = (SELECT max(trade_date) FROM fw WHERE trade_date < DATE '{day}')
  AND sd IS NOT NULL
ORDER BY abs((net_value - mu) / NULLIF(sd, 0)) DESC
"""


def flow_z(lake, instrument_id: str, day: str) -> tuple[float, str]:
    """(수급 z, 누가 움직였나). 거시와 같은 계약 - 최댓값으로 발화, 이름은 같이."""
    try:
        rows = [r for r in lake.sql(_base(day) + _FLOW_Z.format(iid=instrument_id, day=day))
                if r[2] is not None]
    except Exception as e:                  # noqa: BLE001 - 부재는 **사유와 함께**
        # 조용히 0 을 돌려주면 터널 사망이 '수급 이상 없음'으로 위장된다.
        record("series.unmeasured", family="수급", why=f"{type(e).__name__}: {e}"[:160])
        return 0.0, ""
    if not rows:
        return 0.0, ""
    top = " · ".join(f"{r[0]} z={float(r[2]):+.1f}" for r in rows[:3])
    return float(rows[0][2]), f"{rows[0][1]} — {top}"


def series_z(lake, instrument_id: str, day: str) -> dict[str, float]:
    """오늘 셀의 계열족별 혁신 z (60d 창, 당일 제외 = PIT).

    계열 방아쇠의 발화 판정(edge_test)과 가설 에이전트의 어포던스(run_cell)가
    이 한 함수를 쓴다 - 발화 기준이 두 곳에서 갈리면 접지가 무너진다.
    """
    rows = lake.sql((_base(day) + _TODAY_Z).format(iid=instrument_id, day=day))
    out: dict[str, float] = {}
    if rows:
        # 컬럼 순서는 _TODAY_Z 의 (z_ar, z_tv_chg) - _INNOVATION 의 (가격잔차, 거래량).
        out = {fam: float(z) for fam, z in zip(("가격잔차", "거래량"), rows[0])
               if z is not None}
    if (mz := macro_z(lake, day)[0]):
        out["거시"] = mz
    if (fz := flow_z(lake, instrument_id, day)[0]):
        out["수급"] = fz
    return out


def _pctile(v: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(v)) / max(len(v) - 1, 1)


def _panel_rows(lake, sql: str, *, strict: bool = True) -> list:
    """패널 행을 **결정론 순서로** 가져온다. 순열 검정의 전제다.

    층 안에서 rng.permutation 이 어느 위치에 True 를 놓느냐가 곧 어느 ar 이
    처리군이 되느냐다. SQL 에 ORDER BY 가 없어 DuckDB 가 순서를 바꾸면 SEED 를
    고정해도 p 가 흔들린다 - 실측 재실행에서 C2 가 p=0.008 → 0.004 로 임계
    0.0056 을 넘나들었다 (§13 재실행 결정론 위반). 정렬은 (날짜, 종목) 이다.

    strict=False 는 격자용: 결과변수만 요구하고 피처 결측은 열별로 뒤에서 가린다
    (한 열이 NULL 이라고 다른 노출의 표본까지 죽일 이유가 없다).
    """
    rows = lake.sql(sql)
    keep = ((lambda r: all(v is not None for v in r[2:])) if strict
            else (lambda r: r[2] is not None))
    return sorted((r for r in rows if keep(r)), key=lambda r: (str(r[1]), str(r[0])))


def _stratified_p(ar: np.ndarray, hi: np.ndarray, dates: np.ndarray,
                  ) -> float:
    """사건일 층화 순열 귀무의 단측 p. SEED 고정 - 재실행 결정론."""
    obs = ar[hi].mean() - ar[~hi].mean()
    rng = np.random.default_rng(SEED)
    null = np.empty(PERMS)
    for k in range(PERMS):
        perm = hi.copy()
        for d in np.unique(dates):
            m = dates == d
            perm[m] = rng.permutation(perm[m])
        null[k] = ar[perm].mean() - ar[~perm].mean()
    return float((null >= obs).mean())


def _cate_interaction(ar: np.ndarray, d: np.ndarray, c: np.ndarray,
                      dates: np.ndarray) -> tuple[float | None, float]:
    """교호항 d 의 관측값과 사건일 층화 순열 양측 p. **표본을 쪼개지 않는다.**

        ar = a + b·D + c·C + d·(D×C)      CATE(C) = b + d·C

    조건별 층화(표본 분할)는 n 을 죽인다 - 실측에서 조건 조건화 후 n=4·5·6·23 이
    반복됐고 판정불가가 전멸의 형태로 나왔다(§14). 매개변수화하면 전체 표본을 쓰면서
    조건부 효과를 얻는다.

    귀무는 **처치 D 를 사건일 안에서 순열**한다: 같은 날의 공통충격은 그대로 두고
    '누가 처치를 받았나' 만 무작위화한다 - 날짜 층화가 요인 오염을 통제하는 것과
    같은 규율. SEED 고정으로 재실행 결정론.
    """
    df, cf = d.astype(float), c.astype(float)

    def fit(dv: np.ndarray) -> float | None:
        X = np.column_stack([np.ones(len(ar)), dv, cf, dv * cf])
        # 상호작용 열이 상수가 되면(한 클래스에 처치가 없음) 식별 불가.
        if np.ptp(dv) == 0 or np.ptp(cf) == 0 or np.ptp(dv * cf) == 0:
            return None
        try:
            beta, *_ = np.linalg.lstsq(X, ar, rcond=None)
        except np.linalg.LinAlgError:
            return None
        return float(beta[3])

    obs = fit(df)
    if obs is None:
        return None, 1.0
    rng = np.random.default_rng(SEED)
    null = np.empty(PERMS)
    for k in range(PERMS):
        perm = df.copy()
        for dd in np.unique(dates):
            m = dates == dd
            perm[m] = rng.permutation(perm[m])
        v = fit(perm)
        null[k] = 0.0 if v is None else v
    p1 = float((null >= obs).mean())
    return obs, _two_sided(p1)




def _cols(t: HypothesisTuple) -> tuple[list[tuple[str, str]], str] | None:
    """튜플이 요구하는 피처 컬럼 목록 (노출 + 조건들). 노출을 못 재면 None."""
    need = [("__x__", FEATURES.get((t.exposure.ident, t.exposure.transform)))]
    for v in t.conditions:
        need.append((v.key, FEATURES.get((v.ident, v.transform))))
    if need[0][1] is None:
        return None
    return ([(k, c) for k, c in need if c is not None],
            ", ".join(f"g.{c}" for _, c in need if c is not None))


def _two_sided(p1: float) -> float:
    """단측 순열 p → 양측. 학술 수리 ② (17차): 확증 게이트의 부호는 오늘 셀을
    본 에이전트가 고른다 - 방향 채굴을 단측 p 로 보상하면 위양성이 최대 2배다.
    부호는 효과 방향 보고에만 쓰고, 게이트는 양측으로 선다 (격자와 동일 규약)."""
    return min(2.0 * min(p1, 1.0 - p1), 1.0)


def edge_test(lake, t: HypothesisTuple, day: str,
              cell_instrument_id: str = "", layer: str = "고유",
              m_tests: int = 1) -> EdgeReport:
    """튜플 → 패널 수치. 표본이 얇으면 판정불가 — **다른 표본을 찾으러 가지 않는다.**

    엣지 존재는 언제나 **전체 패널**로 검정한다. 조건은 표본을 쪼개지 않고
    조절 대비로만 보고되며, 오늘 적용에만 걸린다(INUS). 조건화로 표본을 쪼개면
    검정력이 죽는다 - 라이브 6회의 지배적 실패가 그것이었다(n=23·6·6 판정불가).
    """
    if t.outcome != "수익률":
        return _unmeasurable(f"결과종류 {t.outcome!r} 검정은 아직 못 잰다")
    y = LAYER_Y.get(layer)
    if y is None:
        raise ValueError(f"층은 {sorted(LAYER_Y)} 중 하나다: {layer!r}")
    if t.exposure.kind == "관계":
        return _relation_test(lake, t, day, layer=layer, m_tests=m_tests)
    got = _cols(t)
    if got is None:
        return _unmeasurable(
            f"노출 ({t.exposure.ident},{t.exposure.transform}) 는 아직 못 잰다 - "
            f"재는 것: {sorted(FEATURES)}")
    cols, col_sql = got
    unmeasured_conds = [v.key for v in t.conditions
                        if (v.ident, v.transform) not in FEATURES]

    trigger_fired: bool | None = None   # 점 방아쇠는 접지(셀 사건 목록)가 발화다
    trigger_note = ""
    if t.trigger.kind == "점":
        sql = (_base(day) + _POINT_PANEL).format(
            etype=t.trigger.ident, cmp="<", day=day, cols=col_sql, y=y, refine=refine_sql(t))
    else:
        innov = _INNOVATION.get(t.trigger.ident)
        if innov is None:
            return _unmeasurable(f"계열 방아쇠 {t.trigger.ident!r} 의 혁신값은 아직 못 잰다 - "
                                 f"재는 것: {sorted(_INNOVATION)}")
        sql = (_base(day) + _SERIES_PANEL).format(
            innov=innov, z=Z_ANOM, day=day, cols=col_sql, y=y)
        # 패널은 역사(|z|≥2 였던 날들)이고, 오늘 적용은 오늘도 당겨졌을 때만이다.
        # 점 방아쇠의 접지와 대칭. 미계측 = 부적용 (발화를 지어내지 않는다).
        if cell_instrument_id:
            z = series_z(lake, cell_instrument_id, day).get(t.trigger.ident)
            if z is None:
                trigger_fired, trigger_note = False, \
                    f"오늘 방아쇠: {t.trigger.ident} z 미계측 - 발화 판정 불가 → 부적용"
            else:
                trigger_fired = bool(abs(z) >= Z_ANOM)
                trigger_note = (f"오늘 방아쇠: {t.trigger.ident} z={z:+.1f} → "
                                + ("발화" if trigger_fired else f"미발화 (|z| < {Z_ANOM})"))

    raw = _panel_rows(lake, sql)
    if len(raw) < MIN_N:
        return EdgeReport("판정불가", len(raw), None, None, None, None,
                          reason=f"패널 표본 n={len(raw)} < {MIN_N} - 백필이 필요하다")

    ar = np.array([float(r[2]) for r in raw])
    dates = np.array([str(r[1]) for r in raw])
    feats = {cols[j][0]: np.array([float(r[3 + j]) for r in raw])
             for j in range(len(cols))}
    x = feats["__x__"]
    pctile = _pctile

    def dose(sub: np.ndarray) -> tuple[float | None, float | None, np.ndarray | None]:
        """부분표본의 용량-반응: 노출 상위 vs 하위의 AR 평균."""
        xs, ars = x[sub], ar[sub]
        hi = pctile(xs) >= EXPOSURE_CUT
        if hi.sum() < 3 or (~hi).sum() < 3:
            return None, None, None
        return float(ars[hi].mean()), float(ars[~hi].mean()), hi

    full = np.ones(len(ar), dtype=bool)
    eff_hi, eff_lo, hi = dose(full)
    if hi is None:
        return EdgeReport("판정불가", len(ar), None, None, None, None,
                          reason="노출 분산 부족 - 상·하위가 갈리지 않는다")

    # **부호는 검정 대상이 아니다.** 목표는 "이 방향이 맞나" 가 아니라 "이 처치가
    # 이 조건에서 유의한가" 다 - 부호는 의도(intent)로만 들고 방향은 **산출**이다.
    #
    # 왜 바꿨나 (실측 000660 07-29): 하루가 -9.61% 인 걸 보고 6개 가설의 부호를
    # 전부 -1 로 썼다. 그 결과 "고β·고회전 종목이 **더 올랐다** p=0.000" 이라는
    # 강한 신호가 '방향 반대 -> 불성립' 으로만 기록됐다. 발견이 실패로 위장된다.
    # 부호를 게이트에서 빼면 그 신호가 '유의 + 방향 반대(의도와 불일치)' 로 남는다.
    obs = eff_hi - eff_lo
    p = _two_sided(_stratified_p(ar, hi, dates))
    # 셀 안에서 m 개를 동시에 검정한다 - α 를 나눠야 FWER 이 0.05 에 묶인다.
    # 산문이 Bonferroni 를 주장하면 게이트가 실제로 나눠야 한다 (선언 = 배선).
    verdict = edge_gate(len(ar), p, alpha=ALPHA / max(m_tests, 1))
    # 방향은 **추정량의 산물**이다 - 선언받지 않는다(21R). 가설이 방향을 미리 쓰면
    # 게이트(양측)는 그것을 무시하고 산문만 '반대 → 불성립' 으로 오독할 수 있다.
    dir_note = f"추정 방향: 상위−하위 {obs * 100:+.2f}%p"

    # ── 조건 = 조절자, 추정량은 **CATE 교호항**이다 ────────────────────────
    # 목표가 "이 처치가 이 조건에서 유의한가" 이므로 처음부터 조건부 효과가
    # 추정대상이어야 한다. 표본을 쪼개면 n 이 죽으므로(실측 C2 n=6) 쪼개지 않고
    # 매개변수화한다:
    #     ar = a + b·D + c·C + d·(D×C),   CATE(C) = b + d·C
    # 유의성은 교호항 d 의 양측 순열 p (층화는 사건일 - 공통충격 통제).
    mask = np.ones(len(ar), dtype=bool)
    for v in t.conditions:
        if v.key in feats:
            pv = pctile(feats[v.key])
            mask &= (pv >= v.percentile) if v.comparator == ">=" else (pv <= v.percentile)
    opposite = int((~mask).sum())

    moderation = counterfactual = ""
    if t.conditions:
        d_obs, d_p = _cate_interaction(ar, hi, mask, dates)
        if d_obs is None:
            moderation = (f"CATE 교호항 추정 불가 (충족 n={int(mask.sum())} · "
                          f"미충족 n={opposite}) - 클래스별 노출 분산 부족")
        else:
            base = eff_hi - eff_lo
            moderation = (f"CATE: 미충족 {base * 100:+.2f}%p → 충족 "
                          f"{(base + d_obs) * 100:+.2f}%p · 교호항 d={d_obs * 100:+.2f}%p "
                          f"(양측 p={d_p:.3f}, 임계 {ALPHA / max(m_tests, 1):.4f}) — "
                          + ("**조건이 효과를 바꾼다**" if d_p < ALPHA / max(m_tests, 1)
                             else "조건 효과 미유의: 이 조건은 처치를 조절하지 않는다"))
        if opposite < MIN_OPPOSITE:
            counterfactual = (f"반대(미충족) 사례 {opposite}건 < {MIN_OPPOSITE} - "
                              "반사실 침묵 (positivity)")
        else:
            u_hi, u_lo, _ = dose(~mask)
            if u_hi is not None:
                counterfactual = (f"조건 미충족 부류(n={opposite})에서는 상위 "
                                  f"{u_hi * 100:+.2f}% vs 하위 {u_lo * 100:+.2f}%")

    # ── 오늘 셀: 노출 백분위 + 조건 충족 (INUS 의 적용 판정) ──────────
    today_pct = None
    cond_bits: list[str] = []
    cond_sat: bool | None = None
    # 조건이 있는데 오늘 셀에서 못 재면 판정불가다 (§11) - 결측을 충족으로 세지 않는다.
    # 초기값은 '조건 없음' 만 True: 오늘 행이 아예 없으면 조건은 검사된 적이 없다.
    cond_meas = not t.conditions
    if cell_instrument_id:
        row = lake.sql((_base(day) + _TODAY_ROW).format(iid=cell_instrument_id, day=day, cols=col_sql))
        if row and row[0][0] is not None:
            x_today = float(row[0][0])
            today_pct = float((x <= x_today).mean())
            # 크기는 여기서 만들지 않는다(§11). SEM 기여를 붙였던 자리인데, τ̂·Δx
            # 는 '노출이 평균보다 높으면 이만큼 더' 라는 비교정태이고 '오늘 이
            # 사건이 만든 %p' 가 아니다. 크기는 ATT 경로가 주장한다.
            sat, measurable = True, True
            for v in t.conditions:
                key = v.key
                if key not in feats:
                    cond_bits.append(f"{key} 못잼")
                    measurable = False
                    continue
                # cols 의 이름 순서 = _TODAY_ROW 반환 컬럼 순서. 이름으로 찾는다.
                idx = [k for k, _ in cols].index(key)
                tv = row[0][idx]
                if tv is None:
                    cond_bits.append(f"{key} 오늘 결측")
                    measurable = False
                    continue
                pv = float((feats[key] <= float(tv)).mean())
                ok = (pv >= v.percentile) if v.comparator == ">=" else (pv <= v.percentile)
                sat &= bool(ok)
                cond_bits.append(f"{key} p{pv * 100:.0f} {'충족' if ok else '미충족'}")
            cond_sat = sat if t.conditions else None
            cond_meas = measurable

    # ── 환원 검사 (§8): 오늘 같은 타입의 횡단면이 패널과 같은 방향인가 ──
    reduction = "—"
    if t.trigger.kind == "점":
        tsql = (_base(day, "23:59:59") + _POINT_PANEL).format(etype=t.trigger.ident, cmp="=", day=day, cols=col_sql, y=y, refine=refine_sql(t))
        trows = _panel_rows(lake, tsql)
        if len(trows) < MIN_TODAY:
            reduction = f"표본부족 (오늘 n={len(trows)})"
        else:
            t_ar = np.array([float(r[2]) for r in trows])
            t_x = np.array([float(r[3]) for r in trows])
            t_hi = pctile(t_x) >= EXPOSURE_CUT
            if t_hi.sum() < 2 or (~t_hi).sum() < 2:
                reduction = f"표본부족 (오늘 노출 분산 없음, n={len(trows)})"
            else:
                # §8 은 "오늘 횡단면이 **패널과** 같은 방향인가" 다. 이전엔 의도
                # 부호와 비교했는데 그건 환원이 아니라 의도 확인이다 - 패널이
                # 추정한 방향(obs)과 맞춰야 토큰→타입 환원이 반증 가능해진다.
                t_raw = float(t_ar[t_hi].mean() - t_ar[~t_hi].mean())
                same = t_raw * obs > 0
                reduction = ("일치" if same else "불일치") + (
                    f" (오늘 n={len(trows)}, 오늘 {t_raw * 100:+.2f}%p vs "
                    f"패널 {obs * 100:+.2f}%p)")

    if unmeasured_conds:
        cond_bits.append("패널 미조건화: " + "·".join(unmeasured_conds))

    return EdgeReport(verdict, len(ar), p, eff_hi, eff_lo, today_pct,
                      cond_today=" · ".join(cond_bits), cond_satisfied=cond_sat,
                      cond_measurable=cond_meas,
                      counterfactual=counterfactual, reduction=reduction,
                      moderation=moderation,
                      trigger_fired=trigger_fired, trigger_note=trigger_note,
                      reason=dir_note)


def _relation_test(lake, t: HypothesisTuple, day: str, layer: str = "고유",
                   m_tests: int = 1) -> EdgeReport:
    """전이 패널 (§16): 사건 종목과 **관계 있는** 종목이 관계 없는 종목보다 부호
    방향으로 더 반응했는가. 위약 = 같은 날 비관계 종목 (날짜 층화가 공통충격을
    소거하므로 남는 대비가 정확히 '관계'다). **몫 배정은 비지원**(assignable=False) -
    오늘 셀에 배정하려면 소스-타깃 창 정렬(누가 먼저 움직였나, 5분봉)이 필요하고
    그 판은 다음이다. 엣지의 존재 검정까지가 이 함수의 정직한 범위다.

    19R: 관계 어휘가 닫혔고(RELATIONS 5종) 타입 있는 1홉(`v_link`)을 잰다. 전에는
    산업 동일성 하나만 재면서 나머지를 '아직 못 잰다'로 되돌려보냈다 - 속성 동일성은
    관계가 아니라 대리이므로, 그건 경로형 인과를 한 번도 안 잰 것이었다.
    """
    if t.exposure.ident not in RELATIONS:
        return _unmeasurable(f"관계 노출 {t.exposure.ident!r} 는 어휘 밖 - {sorted(RELATIONS)}")
    if t.trigger.kind != "점":
        return _unmeasurable("계열 방아쇠 × 관계 노출 조합 판은 아직 없다")

    vcols = [(v.key, FEATURES[(v.ident, v.transform)])
             for v in t.conditions if (v.ident, v.transform) in FEATURES]
    col_sql = ", ".join(f"g.{c}" for _, c in vcols) or "1 AS _one"
    rel = _REL_EXPR.get(t.exposure.ident) or _REL_LINK.format(ident=t.exposure.ident)
    sql = (_base(day) + _RELATION_PANEL).format(
        etype=t.trigger.ident, day=day, cols=col_sql, rel=rel, y=LAYER_Y[layer])
    raw = _panel_rows(lake, sql)
    if len(raw) < MIN_N:
        return EdgeReport("판정불가", len(raw), None, None, None, None,
                          reason=f"전이 패널 n={len(raw)} < {MIN_N}")

    ar = np.array([float(r[2]) for r in raw])
    dates = np.array([str(r[1]) for r in raw])
    rel = np.array([int(r[-1]) for r in raw]) == 1
    feats = {vcols[j][0]: np.array([float(r[3 + j]) for r in raw])
             for j in range(len(vcols))}

    mask = np.ones(len(ar), dtype=bool)               # INUS: 피어 측 조건 조건화
    for v in t.conditions:
        key = v.key
        if key not in feats:
            continue
        pv = _pctile(feats[key])
        mask &= (pv >= v.percentile) if v.comparator == ">=" else (pv <= v.percentile)
    if mask.sum() < MIN_N:
        return EdgeReport("판정불가", int(mask.sum()), None, None, None, None,
                          reason=f"조건 조건화 후 n={int(mask.sum())} < {MIN_N}")
    hi = rel[mask]
    if hi.sum() < 3 or (~hi).sum() < 3:
        return EdgeReport("판정불가", int(mask.sum()), None, None, None, None,
                          reason="관계·비관계가 갈리지 않는다 (게이트 A)")

    sub = ar[mask]
    p = _two_sided(_stratified_p(sub, hi, dates[mask]))
    return EdgeReport(edge_gate(int(mask.sum()), p, alpha=ALPHA / max(m_tests, 1)),
                      int(mask.sum()), p,
                      float(sub[hi].mean()), float(sub[~hi].mean()), None,
                      cond_today="전이: 조건은 피어 측 - 오늘 셀 평가 없음",
                      assignable=False,
                      reason="몫 배정 비지원 - 소스-타깃 창 정렬(5분봉)이 다음 판이다")


def grid_screen(lake, day: str, types: list[str],
                max_rows: int = 6) -> list[dict]:
    """결정론 격자 스크린 — 그날 타입 × 측정가능 노출 전수. LLM 무관.

    가설 커버리지의 세션 분산(라이브 2차는 EXECUTIVE_CHANGE 성립을 찾았는데
    3차는 그 튜플을 안 골랐다)을 메우는 이중화다. 닫힌 어휘라서 격자가 유한하고,
    유한해서 전수가 가능하다. **탐색이지 확증이 아니다**: 방향을 사후에 고르므로
    p 는 양측(x2)이고, 결과는 '스크린 발견'으로만 표기한다 - 확증은 튜플 게이트의 몫.

    표본은 **발견 표본**(split_date 이전)만 쓴다 (18R): 확증 게이트는 경계 이후를
    쓰므로 두 표본이 겹치지 않는다. 그래서 이 스크린 결과를
    가설 에이전트에게 어포던스로 줘도 이중 사용이 아니다.
    """
    feat_names = list(dict.fromkeys(FEATURES.values()))
    all_cols = ", ".join(f"g.{c}" for c in feat_names)
    out: list[dict] = []
    label_of = {c: k for k, c in FEATURES.items()}
    for etype in types:
        # 격자는 **구체화 없이** 전수한다 - 술어·단계·역할·신규성까지 곱하면
        # 조합이 폭발하고, 격자의 목적은 탐색(전 격자 훑기)이지 확증이 아니다.
        sql = (_base(day) + _POINT_PANEL).format(etype=etype, cmp="<", day=day,
                                                 cols=all_cols, y=LAYER_Y["고유"], refine="")
        raw = _panel_rows(lake, sql, strict=False)
        if len(raw) < MIN_N:
            out.append({"type": etype, "status": f"표본부족 n={len(raw)}"})
            continue
        ar = np.array([float(r[2]) for r in raw])
        dates = np.array([str(r[1]) for r in raw])
        for j, colname in enumerate(feat_names):
            xv = np.array([float(r[3 + j]) if r[3 + j] is not None else np.nan
                           for r in raw])
            ok = ~np.isnan(xv)
            if ok.sum() < MIN_N:
                continue
            hi = _pctile(xv[ok]) >= EXPOSURE_CUT
            if hi.sum() < 3 or (~hi).sum() < 3:
                continue
            p_pos = _stratified_p(ar[ok], hi, dates[ok])
            p2 = min(min(p_pos, 1.0 - p_pos) * 2, 1.0)
            fam, tr = label_of[colname]
            out.append({"type": etype, "exposure": f"{fam}/{tr}",
                        "n": int(ok.sum()), "p2": round(p2, 3),
                        "direction": "+" if p_pos < 0.5 else "-",
                        "hi": float(ar[ok][hi].mean()), "lo": float(ar[ok][~hi].mean())})
    hits = sorted((o for o in out if "p2" in o), key=lambda o: o["p2"])
    misses = [o for o in out if "p2" not in o]
    return hits[:max_rows] + misses


__all__ = ["EdgeReport", "FEATURES", "MIN_OPPOSITE", "PERMS", "SEED", "Z_ANOM",
           "edge_test", "grid_screen"]


# ── 패널 → 읽을 글 (판정은 위의 게이트가 이미 냈다) ──────────────────────
def panel_text(lake, tup, instrument_id: str, day: str, layer: str = "고유",
               m_tests: int = 1) -> str:
    """타입 수준 패널을 재고 **읽을 글로** 만든다.

    판정은 코드가 한다(`edge_gate`) - 모델은 시행을 설계하고 함의를 조립한다.
    한때 모델이 판정자였고, 그것이 코드 결론을 뒤집어 산출물에서 무엇이 판정인지
    흐렸다(8셀 실측). 자리를 나눴다: **설계는 모델, 판정은 코드**.
    """
    from .paneltest import edge_test
    r = edge_test(lake, tup, day, cell_instrument_id=instrument_id, layer=layer,
                  m_tests=m_tests)
    return report_text(r, tup, m_tests)


def report_text(r, tup, m_tests: int = 1) -> str:
    """EdgeReport → 패널 텍스트. **순수 함수** - 재검정 없이 같은 글을 낸다.

    `panel_text` 에서 갈라냈다: 조립부가 텍스트와 판정 JSON 둘을 원할 때
    edge_test 를 두 번 돌리면 셀당 16분이 두 배가 되고, 두 번의 결과가 갈릴
    여지도 생긴다 (정렬 고정 전에는 실제로 갈렸다).
    """
    rows = [
        f"n={r.n}" + (f" · p={r.p:.3f}" if r.p is not None else " · p 미계산"),
        # 임계는 판정이 아니라 규약이다 - 셀 안 동시검정 m 개를 α 로 나눈 값.
        # 이걸 안 실으면 검정자가 0.05 로 재고, 산문이 주장하는 Bonferroni 는 허구가 된다.
        f"양측 p 임계 {ALPHA / max(m_tests, 1):.4f} (α=0.05 / 셀 동시검정 m={m_tests})",
        (f"노출 상위 {r.effect_high * 100:+.2f}% vs 하위 {r.effect_low * 100:+.2f}%"
         if r.effect_high is not None else "효과 미계산"),
        (f"오늘 노출 백분위 {r.today_exposure_pct * 100:.0f}%"
         if r.today_exposure_pct is not None else "오늘 노출 미계산"),
    ]
    # 방향은 **추정량이 말한다**(21R). 우리가 찾는 것은 유효한 CATE 이고, 그 부호는
    # 결과다 - 가설이 미리 선언하면 게이트는 무시하고 산문만 오독한다.
    if r.effect_high is not None:
        rows.append(f"방향(추정): 상위−하위 "
                    f"{(r.effect_high - r.effect_low) * 100:+.2f}%p")
    if r.cond_today:
        rows.append(f"조건 오늘: {r.cond_today} "
                    + ("(측정불가 → 판정불가. 결측은 충족이 아니다)"
                       if not r.cond_measurable else f"(충족 {r.cond_satisfied})"))
    if r.moderation:
        rows.append(r.moderation)
    if r.counterfactual:
        rows.append(r.counterfactual)
    if r.reduction and r.reduction != "—":
        rows.append(f"환원 검사: {r.reduction}")
    if r.trigger_note:
        rows.append(r.trigger_note)
    if r.reason:
        rows.append(f"사유: {r.reason}")
    return "\n".join("  " + x for x in rows)
