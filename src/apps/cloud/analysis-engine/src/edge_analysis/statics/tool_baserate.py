"""기저율 검정 — "오늘 이 움직임이 **얼마나 드문 일인가**" 하나만 답한다.

감사가 산문을 볼 때 첫 질문은 늘 같다: *"그게 특별한 겁니까?"* 산문이 "실적 발표로
+3% 올랐다" 라고 쓸 때, 그 종목이 **아무 일 없는 날에도** 절반의 날은 3% 넘게
움직인다면 그 문장은 인과가 아니라 잡음의 이름 바꾸기다. 검정 도구들(`edge_test`·
`run_trial`)은 **타입 수준 횡단면**을 재므로 이 질문에 구조적으로 답하지 못한다 -
"이 사건타입이 평균적으로 몇 %p 를 만드나" 와 "오늘 이 종목의 이 움직임이 자기
자신의 과거에 비해 드문가" 는 다른 질문이고, 후자가 없으면 산문은 **평범한 날에
사건 이름을 붙이는 일**을 무한히 할 수 있다.

왜 종목 자신의 과거 분포인가: 변동성은 종목마다 자릿수가 다르다. 레이크 실측
(2026-08-04) 으로 `layers_daily` 는 stock 856 종목이고 `pit_daily` 는 268 거래일뿐이라
횡단면 z 하나로 전 종목을 재면 고변동 종목의 평범한 날이 상시 '이상' 으로 뜬다.
자기 과거와 비교하면 그 척도 문제가 사라진다 - 대신 상장이 얕거나 거래정지가 길었던
종목은 표본 자체가 없어 `MIN_N`(=30) 관문이 실질적인 구속이 된다.

왜 순위(비모수)인가: 일간 고유수익은 꼬리가 두껍다. 정규 가정으로 p 를 내면 꼬리
사건의 확률을 체계적으로 과소평가해 "1만일에 한 번" 같은 문장이 나오는데, 표본이
861 일(000660 실측, 2026-06-01 기준)이면 그 수는 데이터가 아니라 가정에서 나온
것이다. 경험분포의 초과비율은 표본이 말할 수 있는 것만 말한다 - 하한이 `1/n`
(여기선 0.12%)이고 그것이 정직한 해상도다.

**오늘 값은 분포에서 뺀다** (`trade_date < day`). 넣으면 자기 자신으로 자기를
검정하는 것이고, 극단일수록 오염이 커져(자기 순위가 항상 최상위) 초과확률이
`1/(n+1)` 쪽으로 눌린다 - 즉 드묾을 과장한다.
"""
from __future__ import annotations

import numpy as np

from .paneltest import MIN_N, MIN_OPPOSITE, _base
from .surface import register

# 조건부(사건일) 셀의 최소 표본. **새 상수를 만들지 않고** `MIN_OPPOSITE`(=5) 를 쓴다:
# 둘 다 "이 칸이 이보다 얇으면 말하지 않는다" 는 같은 규율이고, 종목 하나의 사건일은
# 구조적으로 얇다 - 실측: 000660 은 861 거래일에 실적발표일이 12 일(수익 있는 날 10),
# 임원변경 8 일이다. 여기에 `MIN_N`(=30) 을
# 걸면 조건부는 사실상 영구 판정불가가 되고, 새 임계를 발명하면 도구마다 기준이
# 갈린다. 얇으면 조건부만 침묵하고 `cond_n` 은 그대로 보고한다 - 부재를 감추지 않는다.
MIN_COND = MIN_OPPOSITE

# 사건일 집합. `v_event` 는 상시 뷰가 아니라 `_base(day)` 가 만드는 PIT 클램프 CTE 다.
# `< day` 인 이유는 무조건 분포와 같다: 오늘 사건일이 섞이면 오늘로 오늘을 잰다.
_EV = """, ev AS (
    SELECT DISTINCT e.trade_date AS d
    FROM v_event e
    WHERE e.instrument_id = '{iid}' AND e.event_type_code = '{etype}'
      AND e.trade_date < DATE '{day}'
)"""

# `g` 의 `ar` = `v_daily.ar_ind` (시장·산업 이중차감 고유수익). 층을 고르지 않는
# 이유: 이 도구가 답하는 질문은 "이 **종목**에게 드문 일인가" 이고, 시장·섹터 성분이
# 섞인 원수익으로 재면 지수가 3% 빠진 날 전 종목이 동시에 '드문 날' 이 된다.
_Q = """
SELECT g.trade_date, g.ar, {isevt} AS is_evt
FROM g {join}
WHERE g.instrument_id = '{iid}'
  AND g.trade_date <= DATE '{day}'
  AND g.ar IS NOT NULL
ORDER BY g.trade_date
"""


def _abs_rank(v: np.ndarray, t: float) -> float:
    """|과거| 분포에서 |오늘| 의 분위수. 무조건·조건부가 **같은 정의**를 쓴다 -
    두 수를 빼서 `note` 를 쓰는데 정의가 갈리면 그 비교 문장 자체가 거짓이 된다."""
    return float((np.abs(v) <= abs(t)).mean())


def _nope(reason: str, **kw) -> dict:
    """부재는 **사유와 함께**. 빈 dict·0·None 을 조용히 돌려주면 호출자가 그것을
    '드물지 않다'(=설명이 필요 없다) 로 읽는다 - 부재와 기각은 다른 사건이다."""
    out = {"verdict": "판정불가", "reason": reason, "n": 0, "pct_rank": None,
           "exceed_p": None, "cond_n": 0, "cond_pct_rank": None,
           "today": None, "note": ""}
    out.update(kw)
    return out


@register("base_rate",
          "그 종목 오늘의 고유수익이 **자기 과거 분포**에서 몇 분위이고 양측 초과확률"
          " P(|x|≥|오늘|) 이 얼마인지 낸다. 사건타입을 주면 그 사건이 있었던 날들의"
          " 조건부 기저율도 같이 내 '오늘이 정말 특별한가' 를 판정한다.",
          needs=("layers_daily",), vocab=("가격잔차",))
def _base_rate(lake, *, day: str, instrument_id: str, etype: str = "",
               **kw) -> dict:
    """오늘의 고유수익을 그 종목 과거 분포에 대고 순위로 환산한다.

    왜 초과확률까지 내는가: 분위수만 주면 방향(상·하위)은 알아도 **드묾**을 못 읽는다.
    상위 5% 는 20일에 한 번인데 산문은 그것을 "이례적" 이라고 쓴다 - 한 달에 한 번
    일어나는 일이다. 양측 `exceed_p` 는 그 문장을 숫자로 반박할 수 있게 만든다.

    왜 조건부를 같이 내는가: 무조건 기저율만으로는 "드물다" 까지만 말한다. 산문이
    귀속하는 것은 **사건**이므로, 그 사건이 있었던 날들에서도 오늘이 여전히 극단인지가
    귀속의 조건이다. 조건부가 무조건보다 극단이 아니면 - 즉 사건일이라는 정보가
    오늘의 크기를 전혀 설명하지 않으면 - 그 사실을 `note` 로 말한다. 이 비교가 없으면
    "사건 때문에 컸다" 와 "큰 날에 마침 사건이 있었다" 가 같은 출력으로 보인다.

    결정론: 순열도 표집도 없다. 같은 레이크·같은 인자면 같은 수가 나온다.
    """
    if not instrument_id:
        return _nope("instrument_id 가 비었다 - 기저율은 종목 자신의 과거 분포다")
    ev, join, isevt = "", "", "0"
    if etype:
        ev = _EV.format(iid=instrument_id, etype=etype, day=day)
        join = "LEFT JOIN ev ON ev.d = g.trade_date"
        isevt = "CASE WHEN ev.d IS NULL THEN 0 ELSE 1 END"
    sql = _base(day) + ev + _Q.format(iid=instrument_id, day=day,
                                      join=join, isevt=isevt)
    try:
        rows = lake.sql(sql)
    except Exception as e:                  # noqa: BLE001 - 사유를 남긴다
        # 조용히 삼키면 터널·카탈로그 사망이 '오늘은 평범했다' 로 위장된다.
        return _nope(f"레이크 질의 실패: {type(e).__name__}: {str(e)[:120]}")

    hist = [r for r in rows if str(r[0]) < day]
    now = [r for r in rows if str(r[0]) == day]
    if not now or now[-1][1] is None:
        return _nope(f"{day} 의 고유수익 부재 - 비거래일이거나 그날 시세가 없다",
                     n=len(hist))
    today = float(now[-1][1])
    if len(hist) < MIN_N:
        return _nope(f"과거 표본 n={len(hist)} < MIN_N={MIN_N} - 이 종목의 "
                     f"{day} 이전 고유수익이 분포를 만들 만큼 없다",
                     n=len(hist), today=today)

    x = np.array([float(r[1]) for r in hist], dtype=float)
    pct_rank = float((x <= today).mean())
    exceed_p = float((np.abs(x) >= abs(today)).mean())
    uncond_abs = _abs_rank(x, today)

    cond = np.array([float(r[1]) for r in hist if r[2]], dtype=float)
    cond_n, cond_rank, note = len(cond), None, ""
    # `supports` = 이 도구가 **사건 귀속 주장을 지지하는가**. None = 판정 안 함.
    # 신뢰성 검사(`trust`)가 이 값을 읽어 집계한다 - 도구별 특수 규칙을 그쪽에 박으면
    # 결합이 생기므로 판단은 여기서 한다. 실측 교훈: 이 신고가 없던 동안 신뢰성
    # 검사가 "사건 귀속의 근거가 되지 못한다" 는 note 를 못 읽고 주장을 통과시켰다.
    supports: bool | None = None
    if not etype:
        note = "사건타입 미지정 - 무조건 기저율만 냈다"
    elif cond_n < MIN_COND:
        note = (f"{etype} 사건일 n={cond_n} < {MIN_COND} - 조건부 기저율 침묵"
                f"(부재이지 '사건이 무관하다'는 뜻이 아니다)")
    else:
        cond_rank = _abs_rank(cond, today)
        gap = cond_rank - uncond_abs
        if gap > 0:
            note = (f"{etype} 사건일 분포에서 오늘은 상위 {(1 - cond_rank) * 100:.1f}% "
                    f"(무조건 상위 {(1 - uncond_abs) * 100:.1f}%) - 사건일 중에서도 더 극단")
            supports = True
        else:
            note = (f"{etype} 사건일 분포에서 오늘은 상위 {(1 - cond_rank) * 100:.1f}% 로 "
                    f"무조건 분포(상위 {(1 - uncond_abs) * 100:.1f}%) 보다 극단이 아니다 - "
                    f"사건 귀속의 근거가 되지 못한다")
            supports = False

    return {"verdict": "계산됨", "reason": "", "n": len(hist), "supports": supports,
            "pct_rank": round(pct_rank, 6), "exceed_p": round(exceed_p, 6),
            "cond_n": cond_n,
            "cond_pct_rank": None if cond_rank is None else round(cond_rank, 6),
            "today": today, "note": note}


__all__ = ["MIN_COND"]
