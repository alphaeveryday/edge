"""§7 "블록은 항상 나간다 — 없으면 없다고 말한다."

설명 포맷의 고정 블록 4개(헤더는 근거 부재가 없는 항상-존재 블록이라 뺀다) 각각의
부재 문구를 코드로 고정한다. 자유 텍스트로 두면 "그런 걸 안 봤다"(조용한 생략)와
"봤는데 없었다"(이 문구)를 독자가 구분할 수 없다(§7 서문) - 그래서 문구 자체가
닫힌 어휘다.
"""
from __future__ import annotations

BLOCKS = ("가격 기여 분해", "시간 구간 기여", "상대적 비교", "이벤트 병치")

ABSENCE_TEXT = {
    "가격 기여 분해": "구성종목 가격 데이터가 없어 기여를 계산하지 못했습니다.",
    "시간 구간 기여": "움직임이 특정 구간에 몰리지 않고 장 전체에 퍼졌습니다.",
    "상대적 비교": "업종 지수가 없어 요인을 나누지 못했습니다.",
    "이벤트 병치": "이 ETF 와 연결된 공시·뉴스가 오늘 없었습니다.",
}


class UnknownBlock(ValueError):
    """§7 이 정의한 4개 블록 밖의 이름."""


def render_block(block: str, content: str | None) -> str:
    """내용이 있으면 그대로, 없으면(`None`/빈 문자열) 고정 부재 문구를 낸다.

    빈 문자열도 `None` 과 같이 취급한다 - 조립 과정에서 실수로 빈 줄이 만들어졌을 때
    "부재"와 "빈 문자열"을 다른 것으로 다루면 §7 이 막으려는 바로 그 혼동이 다시
    생긴다.
    """
    if block not in BLOCKS:
        raise UnknownBlock(f"{block!r} 는 §7 의 4개 블록 밖이다: {BLOCKS}")
    return content if content and content.strip() else ABSENCE_TEXT[block]


__all__ = ["ABSENCE_TEXT", "BLOCKS", "UnknownBlock", "render_block"]
