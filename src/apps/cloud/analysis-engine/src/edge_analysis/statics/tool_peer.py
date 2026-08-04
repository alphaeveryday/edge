"""동종 대비 위치 — "그래서 오늘 같은 업종 안에서 몇 등인가".

층 분해(`layers.decompose`)와 패널의 섹터층은 **시계열** β 로 몫을 가른다
(`_base` 의 `beta_s` = 60일 롤링, KRX 업종지수 대비). 그것은 "이 종목이 업종에
얼마나 민감한가" 이지 "오늘 그 업종 안에서 어디 서 있나" 가 아니다. 둘은 같은
날에도 어긋난다: 섹터층 몫이 양(+)이어도 그 종목이 같은 업종에서 꼴찌였다면
"반도체가 좋아서 올랐어요" 는 **거짓**이고, 참인 문장은 "업종은 올랐는데 이
종목만 못 따라갔다" 다. 지금 배선에는 그 반증을 낼 자리가 없었다 — β 는 양수이고
p 는 유의하니 게이트가 전부 통과시킨다.

그래서 이 도구는 아무것도 추정하지 않는다. 오늘 하루 횡단면을 그대로 세어
**순위 · 분위수 · 업종 중앙값 · 중앙값과의 격차 · 부호 일치**만 낸다. 추정량이
없으니 순열·다중검정 이야기가 붙지 않고, 그 대신 어떤 층 분해와도 독립인 반증을
준다 — `note` 에 "동종과 반대로 움직였다" 가 뜨는 날이 정확히 산문과 데이터가
갈리는 지점이다.

**왜 `sector_code`(KRX 업종지수)로 묶는가.** 후보는 둘이었고 실측이 골랐다:
  · `v_daily.industry_name`(RDB `instrument_classification`)은 이 레이크에서
    **전부 NULL** 이다 — 2026-06-01 횡단면 212종목 중 non-null 0건. 이 키로 묶으면
    도구가 매일 판정불가만 낸다. 덤으로 `ar_ind` 의 산업 차감(`count(*) OVER di
    >= 5`)도 걸리지 않으므로 '고유' 수익은 사실상 시장 차감뿐이다.
  · `sector_code`(`v_sector` ← `sector_member`)는 채워져 있고, 무엇보다 **반증
    대상과 같은 축**이다: 섹터층 β 를 재는 상대가 바로 이 업종지수(`v_sector_ret`)다.
    다른 축으로 묶은 순위는 반증이 아니라 또 하나의 주장일 뿐이다.

모집단은 `_base(day)` 의 `g` 다 — 패널 게이트가 쓰는 바로 그 표면이라야 순위와
층 분해가 같은 종목 집합을 두고 말한다(실측 2026-06-01 그날 `g` 211종목).
"""
from __future__ import annotations

import numpy as np

from .krxsector import sector_name
from .paneltest import MIN_TODAY, _base
from .surface import register

# 동종 표본 최소치. 새 임계를 발명하지 않고 `paneltest.MIN_TODAY`(환원 검사의
# **오늘 횡단면** 최소 표본)를 그대로 쓴다 - 같은 하루 횡단면을 두 기준으로
# 자르면 한 산출물 안에서 "표본 충분" 의 뜻이 둘이 된다. 저장소가 오늘 횡단면에
# 요구하는 최소치는 이미 이 값 하나뿐이고(`sql_surface` 의 산업 차감 게이트도
# 같은 5다), 4종목짜리 업종에서 "동종 중 1위" 를 말하면 그 강한 문장이 실제로는
# 비교 대상 3개에서 나온다.
MIN_PEERS = MIN_TODAY

# 오늘 업종 횡단면. 수익은 `y_시장`(= `v_daily.lr`, 원수익)이다.
#
# **왜 원수익인가**: 같은 날 같은 업종이면 시장 성분이 전원 공통이라 순위는 시장
# 차감 여부와 무관하다. 그러나 중앙값과 격차는 달라지고, 검증받아야 하는 문장
# ("올랐다")은 초과수익이 아니라 실제 수익이다. 시장 차감값으로 중앙값을 내면
# 시장이 3% 빠진 날 전 종목이 "올랐다" 로 보인다.
#
# PIT: `_base(day)` 의 as_of 클램프(자정)와 trade_date 클램프만 쓴다. 오늘 종가는
# 오늘 정보이고 이 도구가 재는 것이 바로 그 오늘 횡단면이므로 당일을 배제하지
# 않는다 - 노출 피처의 당일 제외 규율은 '처치가 결과를 보면 안 된다' 는 것이고,
# 여기에는 처치도 예측도 없다(집계 하나뿐).
_PEER_SQL = """, _pr AS (
    SELECT instrument_id, y_시장 AS lr, sector_code
    FROM g
    WHERE trade_date = DATE '{day}'
      AND y_시장 IS NOT NULL
      AND sector_code IS NOT NULL
)
SELECT instrument_id, lr, sector_code FROM _pr
WHERE sector_code = (SELECT sector_code FROM _pr
                     WHERE instrument_id = '{iid}')
"""


def _no(reason: str, *, n_peers: int = 0, industry: str = "") -> dict:
    """판정불가는 **사유와 함께만** 나간다. 키 모양은 성공 경로와 같게 유지한다 -
    소비자가 `.get("rank")` 로 0 이나 빈 dict 을 받고 '동종 대비 보통' 으로 읽는
    것이 이 저장소가 가장 싫어하는 실패다. 없는 값은 없다고 명시된 None 이다."""
    return {"verdict": "판정불가", "reason": reason, "n_peers": n_peers,
            "rank": None, "pct_rank": None, "peer_median": None,
            "spread": None, "same_sign": None, "supports": None,
            "note": "", "industry": industry}


@register("peer_rank", "그 종목 그날의 수익이 **같은 업종(KRX 업종지수) 안에서** 몇 "
                       "등인지와 업종 중앙값 대비 격차를 낸다. 층 분해(시계열 β)가 "
                       "못 내는 오늘 횡단면 반증 — 동종과 반대로 움직였으면 그렇게 적는다.",
          needs=("layers_daily",), vocab=("섹터",))
def _peer_rank(lake, *, day: str, instrument_id: str, **kw) -> dict:
    """오늘 업종 횡단면에서의 순위·격차·부호 일치.

    **왜 파이썬에서 정렬하는가**: `_panel_rows` 와 같은 규율이다. 정렬을 SQL 에
    맡기면 동점 처리와 NULL 위치가 엔진 판이 되고, 그러면 같은 입력에 다른 등수가
    나올 수 있다. 여기서는 (수익 내림차순, instrument_id) 로 못 박는다 - 순열도
    난수도 없으므로 결정론은 이 정렬 하나에 달려 있다.

    **부호 일치의 자리**: 대상과 업종 중앙값의 곱이 음수면 방향이 갈린 것이고,
    그것이 층 분해 서사와 정면으로 부딪히는 유일한 관측이다. 0 은 '같은 방향'이
    아니라 '방향 없음'으로 따로 말한다 - 0 을 같은 부호로 접으면 무변동 업종이
    조용히 서사를 승인한다.
    """
    try:
        rows = lake.sql(_base(day) + _PEER_SQL.format(day=day, iid=instrument_id))
    except Exception as exc:                    # noqa: BLE001 - 부재는 **사유와 함께**
        # 조용히 빈 목록을 돌려주면 터널 사망이 '동종 없음'으로 위장된다.
        return _no(f"횡단면 질의 실패: {type(exc).__name__}: {str(exc)[:120]}")

    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return _no(f"{day} 그날 대상 종목의 수익 또는 업종 분류가 없다 — 동종 집합 "
                   "자체를 정의할 수 없다(부재이지 '동종 대비 보통'이 아니다)")

    industry = sector_name(rows[0][2])
    ranked = sorted(rows, key=lambda r: (-float(r[1]), str(r[0])))
    ids = [str(r[0]) for r in ranked]
    n = len(ranked)
    if instrument_id not in ids:
        return _no(f"{day} 그날 {industry} 횡단면에 대상 종목이 없다 — 업종은 "
                   "잡혔으나 그 종목의 수익이 없다", n_peers=n, industry=industry)
    if n < MIN_PEERS:
        return _no(f"{industry} 동종 표본 {n}종목 < {MIN_PEERS} — 이 크기에서 낸 "
                   "등수는 비교 대상이 손에 꼽혀 순위라 부를 수 없다",
                   n_peers=n, industry=industry)

    idx = ids.index(instrument_id)
    lrs = np.array([float(r[1]) for r in ranked])
    tgt, med = float(lrs[idx]), float(np.median(lrs))
    prod = tgt * med
    note = ("동종과 반대로 움직였다" if prod < 0 else
            "" if prod > 0 else "대상 또는 업종 중앙값이 0 — 방향 비교 불가")
    # 분위수는 `_pctile`(argsort 두 번)을 **쓰지 않는다**: 동점일 때 argsort 의
    # 안정 정렬 순서와 여기서 못 박은 (수익↓, id) 순서가 갈려 "1등인데 분위수는
    # 아래" 같은 자기모순이 난다. 규약(0=최하위 · 1=최상위)은 같게 두되 등수에서
    # 직접 만든다 - 값이 하나면 두 곳에서 갈릴 수 없다.
    return {"verdict": "계산됨", "reason": "", "n_peers": n, "rank": idx + 1,
            "pct_rank": (n - idx - 1) / (n - 1), "peer_median": med,
            "spread": tgt - med, "same_sign": prod > 0, "note": note,
            # `supports` = 업종 서사를 지지하는가. 동종과 **반대**로 움직였으면
            # "업종이 좋아서 올랐다" 는 거짓이다 - 신뢰성 검사가 이 값을 읽는다.
            "supports": prod > 0,
            "industry": industry}


__all__ = ["MIN_PEERS"]
