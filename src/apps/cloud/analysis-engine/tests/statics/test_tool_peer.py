"""peer_rank — 오늘 업종 횡단면 순위가 층 분해 서사를 반증하는가.

두 검사만 둔다. 둘 다 **이 도구가 없으면 통과해버리는 거짓 설명**을 하나씩 막는다.

(a) 순위·중앙값·부호: 업종 중앙값은 +2% 인데 대상은 -2% 인 날을 만든다. 섹터층
    β 가 양수면 층 분해는 이 날 섹터 몫을 양(+)으로 내고, 그것만 보면 "전기전자가
    좋아서 올랐다" 가 게이트를 전부 통과한다. 도구가 rank=5/5 · same_sign=False ·
    note 를 내야 그 문장이 데이터와 부딪힌다. 결정론도 여기서 본다: 레이크 대역이
    행을 뒤섞어 돌려주므로 파이썬 정렬을 빠뜨리면 등수가 입력 순서를 따라간다.

(b) 동종 부족: 4종목짜리 업종에서 "동종 중 1위" 는 사실상 비교 대상 3개에서 나온
    문장이다. 부재는 **사유와 함께 판정불가**여야 하고 rank 0 이나 빈 dict 이
    되어서는 안 된다 - 그러면 소비자가 그것을 '동종 대비 보통' 으로 읽는다.
"""
from __future__ import annotations

import edge_analysis.statics.tool_peer  # noqa: F401 - register 부수효과
from edge_analysis.statics.surface import TOOLS
from edge_analysis.statics.tool_peer import MIN_PEERS

DAY = "2026-06-01"
TGT = "inst_target"


class _Lake:
    """업종 횡단면 한 판만 돌려주는 대역. 질의가 실제로 오늘 하루 · 업종 코드 ·
    대상 종목을 걸었는지 문자열로 확인한다 - 안 걸면 라이브에서 전 종목을 '동종'
    이라 부른다."""

    def __init__(self, rows):
        self.rows = rows

    def sql(self, q):
        assert f"trade_date = DATE '{DAY}'" in q, q      # 오늘 횡단면만
        assert "sector_code" in q, q                     # 업종으로 묶는다
        assert f"instrument_id = '{TGT}'" in q, q        # 대상의 업종을 집는다
        return self.rows


def _call(rows):
    return TOOLS["peer_rank"].fn(_Lake(rows), day=DAY, instrument_id=TGT)


def test_rank_median_and_sign_contradict_the_sector_story():
    # 전기전자(1013) 5종목: +5% +3% +2% +1% / 대상 -2%. 중앙값 +2%, 대상은 꼴찌.
    rows = [("inst_c", 0.02, "1013"), (TGT, -0.02, "1013"),
            ("inst_a", 0.05, "1013"), ("inst_d", 0.01, "1013"),
            ("inst_b", 0.03, "1013")]
    r = _call(rows)

    assert r["verdict"] == "계산됨" and r["reason"] == ""
    assert r["industry"] == "전기전자(코스피)" and r["n_peers"] == 5
    assert r["rank"] == 5                                # 1=최상위 → 꼴찌
    assert r["pct_rank"] == 0.0                          # 0=최하위 규약
    assert abs(r["peer_median"] - 0.02) < 1e-12
    assert abs(r["spread"] - (-0.04)) < 1e-12            # 대상 - 중앙값
    assert r["same_sign"] is False
    assert r["note"] == "동종과 반대로 움직였다"          # 서사와 부딪히는 자리

    # 같은 입력, 다른 행 순서 → 같은 출력 (결정론은 파이썬 정렬 하나에 달려 있다).
    assert _call(list(reversed(rows))) == r


def test_thin_peer_set_is_unmeasurable_with_a_reason():
    rows = [(f"inst_{k}", 0.01 * k, "1005") for k in range(MIN_PEERS - 2)]
    rows.append((TGT, 0.09, "1005"))
    assert len(rows) == MIN_PEERS - 1

    r = _call(rows)
    assert r["verdict"] == "판정불가"
    assert str(MIN_PEERS) in r["reason"] and "음식료·담배(코스피)" in r["reason"]
    assert r["n_peers"] == MIN_PEERS - 1                 # 얼마나 얇은지는 말한다
    # 부재를 '보통' 으로 읽히게 만드는 값이 하나도 없어야 한다.
    assert r["rank"] is None and r["pct_rank"] is None
    assert r["peer_median"] is None and r["same_sign"] is None


def test_registration_declares_its_table_and_vocab():
    t = TOOLS["peer_rank"]
    assert t.needs == ("layers_daily",) and t.vocab == ("섹터",)
