"""종목 상세 "오늘의 한 줄" 카드 생성기.

한 종목의 일일 분석 dict를 카드가 바인딩하는 필드로 변환한다:
``claim1``/``claim2`` (두 줄 헤드라인), ``claim`` (한 문장),
``direction`` (긍정|부정|중립), ``strength`` (1..5), ``horizon`` (단기|중기|장기).
LLM이 우선이고, 키가 없거나 실패하면 결정적 템플릿으로 폴백한다 -- 카드는 절대 비지 않는다.
"""
from __future__ import annotations

import json
import re

from . import llm

DIRECTIONS = ("긍정", "부정", "중립")
HORIZONS = ("단기", "중기", "장기")
KEYS = ("claim1", "claim2", "claim", "direction", "strength", "horizon")
_NEUTRAL_BAND = 0.003  # |예측 변동| < 0.3% -> 방향 중립
_STRENGTH_BREAKS = (0.005, 0.01, 0.02, 0.04)  # 변동 크기 구간 -> 1..5


def _pct(x) -> str:
    return f"{(x or 0.0) * 100:+.1f}%"


def _messages(a: dict) -> list[dict]:
    heads = "\n".join(f"- {h}" for h in (a.get("top_headlines") or [])) or "- (당일 관련 뉴스 없음)"
    pd_ = a.get("predicted_direction") or 0
    move = "상승" if pd_ > 0 else ("하락" if pd_ < 0 else "보합")
    sys_p = (
        "역할: 너는 개인투자자용 '종목 상세' 화면의 \"오늘의 한 줄\" 카드를 쓰는 애널리스트다. "
        "이 카드는 그 종목을 한 줄로 설명하는 '가장 강한 이야기' 하나만 보여준다.\n\n"
        "입력은 그날의 모델 예측치와 당일 헤드라인이다(실현 종가 아님).\n"
        "- 정상 변동: 뉴스를 빼고 시장 흐름만 반영한 예측.\n"
        "- 모델 예측 변동: 뉴스까지 반영한 최종 예측. 정상 변동과의 차이가 '뉴스 기여분'이다.\n\n"
        "판단:\n"
        "- direction: 모델 예측 변동과 뉴스 기여분이 함께 +면 '긍정', -면 '부정', 미미하면 '중립'.\n"
        "- strength(1~5): 예측 변동 크기·뉴스 기여분·이벤트성 급변동이 클수록 높다.\n"
        "- horizon: 일회성·수급이면 '단기', 실적·가이던스면 '중기', 구조적 전환이면 '장기'.\n\n"
        "문장:\n"
        "- claim1, claim2: 카드에 두 줄로 표시. 각 12자 이내. claim1은 쉼표(,)로 끝낸다. claim1=핵심 사건, claim2=그 함의.\n"
        "- claim: 두 줄을 한 문장으로 합친 명사형 종결(마침표 없이, 30자 내외).\n"
        "- 뉴스가 방향을 설명하면 그 사건을 claim1에 쓰고, 설명 못 하면 시장 흐름 중심으로 쓴다.\n"
        "- 주어진 헤드라인·수치 외 사실 지어내기 금지. 통계용어·매수/매도 권유 금지.\n\n"
        "출력: 아래 JSON 객체 하나만, 코드펜스·다른 텍스트 없이.\n"
        '{"claim1":"...","claim2":"...","claim":"...","direction":"긍정|부정|중립","strength":1-5,"horizon":"단기|중기|장기"}\n'
        '예: {"claim1":"양산 6개월 앞당겨,","claim2":"내년 이익 눈높이 상향",'
        '"claim":"양산 일정을 6개월 앞당기며 내년 이익 추정치 상향 흐름","direction":"긍정","strength":4,"horizon":"단기"}'
    )
    user_p = (
        f"종목: {a.get('company')}({a.get('ticker')})\n"
        f"예측 대상일: {a.get('trade_date')}\n"
        f"정상 변동(시장 흐름만): {_pct(a.get('normal_return'))}\n"
        f"모델 예측 변동(뉴스 반영): {_pct(a.get('predicted_return'))} ({move})\n"
        f"뉴스 기여분(둘의 차이): {_pct(a.get('abnormal_return'))}\n"
        f"이벤트성 급변동: {'있음' if a.get('is_event') else '없음'}\n"
        f"당일 뉴스 {a.get('news_count') or 0}건, 최신 헤드라인:\n{heads}\n\n"
        "위 입력만 근거로 '오늘의 한 줄' JSON을 출력하라."
    )
    return [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}]


def direction_of(a: dict) -> str:
    r = a.get("predicted_return") or 0.0
    if abs(r) < _NEUTRAL_BAND:
        return "중립"
    return "긍정" if r > 0 else "부정"


def strength_of(a: dict) -> int:
    mag = max(abs(a.get("predicted_return") or 0.0), abs(a.get("abnormal_return") or 0.0))
    s = 1 + sum(mag >= b for b in _STRENGTH_BREAKS) + (1 if a.get("is_event") else 0)
    return max(1, min(5, s))


def template(a: dict) -> dict:
    """Deterministic card from the signals alone (LLM-free fallback)."""
    direction = direction_of(a)
    head = (a.get("top_headlines") or [None])[0]
    if head:
        claim1 = head.strip()[:18].rstrip("., ") + ","
        claim2 = {"긍정": "상승 모멘텀 부각", "부정": "하락 압력 요인", "중립": "주가 영향 주목"}[direction]
    else:
        claim1 = "뚜렷한 재료 없이,"
        claim2 = {"긍정": "시장 흐름에 동조", "부정": "시장 약세에 동조", "중립": "큰 변동 없는 흐름"}[direction]
    return {"claim1": claim1, "claim2": claim2, "claim": f"{claim1[:-1]} {claim2}",
            "direction": direction, "strength": strength_of(a), "horizon": "단기", "source": "template"}


def parse(text: str) -> dict | None:
    """Parse + validate the LLM JSON; None if malformed or incomplete."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        c1, c2 = str(d["claim1"]).strip(), str(d["claim2"]).strip()
        direction = d.get("direction")
        strength = max(1, min(5, int(d.get("strength", 3))))
    except Exception:
        return None
    if not c1 or not c2 or direction not in DIRECTIONS:
        return None
    claim = str(d.get("claim") or "").strip() or f"{c1.rstrip(',')} {c2}"
    horizon = d.get("horizon") if d.get("horizon") in HORIZONS else "단기"
    return {"claim1": c1, "claim2": c2, "claim": claim, "direction": direction,
            "strength": strength, "horizon": horizon, "source": "llm"}


def generate_oneliner(a: dict) -> dict:
    """'오늘의 한 줄' 카드 필드(항상 완성)를 반환한다 -- LLM 우선, 실패 시 템플릿."""
    text, _model = llm.chat(_messages(a), max_tokens=250)
    return (parse(text) if text else None) or template(a)
