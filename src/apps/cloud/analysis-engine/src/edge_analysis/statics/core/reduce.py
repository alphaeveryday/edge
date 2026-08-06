"""환원 사전 — 검정 층이 찾은 것을 **가설 어휘로 되돌린다**.

## 왜 필요한가

층을 둘로 나눴다 (사용자 설계):

    가설 어휘   닫힌 계열족 16 × 변환 6    거칠게 낸다
    검정 재료   curated 947 + 재무 692     구체화한다

그런데 검정 층이 `S41B0D1005 104주로그베타(주간)` 에서 신호를 찾으면 그걸 **그대로
보고할 수 없다**. 가설 어휘 밖이라 다음 셀에서 재현 불가하고, 산문도 그 이름을 쓸
수 없다(닫힌 어휘 계약). 찾은 것을 `지수잔차/민감도` 로 되돌려야 비로소 자산이 된다.

환원이 안 되면 그건 **어휘 확장 요청**이고 사람이 판단한다 - 에이전트가 즉석에서
계열족을 만들면 닫힌 어휘가 이름만 남는다.

## 어떻게 - 카테고리 규칙 (947개를 손으로 매핑하지 않는다)

curated 가 이미 `category` 를 준다 (베타 45 · 신용거래 20 · 대차거래 13 ·
차입공매도 12 · 주가배수 20 · 거래량 23 · 투자자별매매 658 …). 카테고리가 곧
계열족의 대응물이라 규칙 열 줄로 947개가 덮인다.

변환은 **이름의 접두/접미**가 정한다: `20일누적…` → 누적, `…평균…` → 수준,
`…증감` → 변화, `…변동성` → 변동성, `로그베타` → 민감도.
"""
from __future__ import annotations

import re

from .vocab import SERIES_FAMILIES, TRANSFORMS

# 카테고리 → 계열족. curated 의 category 가 곧 어휘의 대응물이다.
CAT_FAMILY: dict[str, str] = {
    "베타": "지수잔차",
    "신용거래": "신용",
    "대차거래": "공매도",
    "차입공매도": "공매도",
    "주가배수": "배수",
    "거래량": "거래량",
    "주식수,시가총액": "주식수",
    "가격,수익률": "가격잔차",
    "투자자별매매-수량": "수급",
    "투자자별매매-대금": "수급",
}

# 재무(692) 는 항목 이름이 계열족을 직접 말한다.
FIN_FAMILY: tuple[tuple[str, str], ...] = (
    (r"부채|차입|순부채|레버리지", "레버리지"),
    (r"ROE|ROA|ROIC|이익률|마진|수익성", "수익성"),
    (r"증가율|성장", "성장"),
    (r"이자보상|현금흐름|커버리지", "재무파생"),
    (r"PBR|PER|PSR|PCR|EV|배수|배당수익률", "배수"),
    (r"배당|주주환원|자기주식", "주주"),
)

# 이름 → 변환. 먼저 맞는 것이 이긴다 (구체적인 것 먼저).
NAME_TRANSFORM: tuple[tuple[str, str], ...] = (
    (r"\d+일누적|\d+주누적|누적", "누적"),
    (r"증감|변화|증가율|YoY|QoQ", "변화"),
    (r"변동성|표준편차", "변동성"),
    (r"베타|민감도|R-Square|t-value|알파", "민감도"),
    (r"평균|잔고|비율|율\(|배|수준|시가총액|주식수", "수준"),
)


# 이름에 변환 힌트가 없는 항목의 기본값 (계열족별). `PBR(IFRS-연결)` 처럼
# 스칼라 상태를 그대로 주는 이름들이 여기 걸린다 - 그건 '수준' 이 맞다.
FAMILY_DEFAULT_TRANSFORM: dict[str, str] = {
    "배수": "수준", "레버리지": "수준", "수익성": "수준", "신용": "수준",
    "공매도": "수준", "주식수": "수준", "주주": "수준", "재무파생": "수준",
    "거래량": "수준", "지수잔차": "민감도", "수급": "누적", "가격잔차": "수준",
    "성장": "변화",
}


def reduce_item(name: str, category: str = "", domain: str = "") -> tuple[str, str] | None:
    """(계열족, 변환) 또는 None. None = 환원 실패 = **어휘 확장 요청**.

    실패를 조용히 '기타' 로 밀지 않는다 - 실패가 곧 사람에게 보내는 신호다.
    """
    fam = CAT_FAMILY.get(category)
    if fam is None:
        for pat, f in FIN_FAMILY:
            if re.search(pat, name, re.I):
                fam = f
                break
    if fam is None or fam not in SERIES_FAMILIES:
        return None
    tr = None
    for pat, t in NAME_TRANSFORM:
        if re.search(pat, name, re.I):
            tr = t
            break
    if tr is None:
        tr = FAMILY_DEFAULT_TRANSFORM.get(fam)
    if tr is None or tr not in TRANSFORMS:
        return None
    return (fam, tr)


def coverage(items: list[tuple[str, str, str, str]]) -> dict:
    """사전 커버리지 — 환원되는 항목 수와 **실패 목록**. 실패가 확장 요청이다."""
    ok: dict[tuple[str, str], int] = {}
    bad: list[str] = []
    for _code, name, _dom, cat in items:
        r = reduce_item(name, cat)
        if r is None:
            bad.append(f"{cat}/{name}"[:52])
        else:
            ok[r] = ok.get(r, 0) + 1
    return {"reduced": sum(ok.values()), "failed": len(bad),
            "families": dict(sorted(ok.items(), key=lambda kv: -kv[1])),
            "fail_sample": bad[:12]}


def _selfcheck() -> None:
    assert reduce_item("104주로그베타(주간)", "베타") == ("지수잔차", "민감도")
    assert reduce_item("20일누적 차입공매도수량(주)", "차입공매도") == ("공매도", "누적")
    assert reduce_item("20일평균거래량(주)", "거래량") == ("거래량", "수준")
    assert reduce_item("PBR(IFRS-연결)", "주가배수") == ("배수", "수준")
    assert reduce_item("20일누적대차거래잔고증감(주)", "대차거래") == ("공매도", "누적")
    assert reduce_item("차입금의존도", "") == ("레버리지", "수준")
    assert reduce_item("매출액증가율", "") == ("성장", "변화")
    assert reduce_item("완전히 모르는 것", "없는카테고리") is None    # 실패는 실패로
    print("ok")


if __name__ == "__main__":
    _selfcheck()
