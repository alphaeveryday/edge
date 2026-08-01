"""가설 에이전트 — 닫힌 어휘에서 튜플 슬롯 채우기. 산문은 검정할 수 없다.

설계 §17 의 계약을 코드로:
  허용   슬롯 채우기 · 환원 후보 ≥2 (서로 다른 채널 - 단일 가설은 확증만 한다)
  금지   id 생성 · 수치(임계 백분위 제외) · 자유 텍스트 · 새 어휘
  검증   vocab.py 가 한다 - 어휘 밖은 VocabError. **자동 보정 금지**(보정이 곧
         대필이다). 거부는 전부 되물음이다(P2 계보): 사유를 그대로 돌려주고
         한 번 다시 묻는다. 그래도 모자라면 모자란 채로 낸다 - 억지 가설보다
         빈손이 낫다.
  접지   점 방아쇠의 사건 타입은 **그날 실재한 목록**에서만. 목록 밖 = 날조
         (STORM base 의 EVT_KR_… 실측 재발 방지 - 이번엔 타입 층위에서).
  어포던스  지금 잴 수 있는 노출 조합을 사실로 알린다(STORM 교훈: 다음 수를
         알려라). 첫 라이브 실행(2026-08-01)에서 3/3 튜플이 못 재는 노출을
         골라 패널이 전부 공회전한 것이 근거다. 제약이 아니라 정보 - 어휘는
         열린 채 두되, 선택의 결과(판정불가)를 생성 전에 안다.
"""
from __future__ import annotations

import json  # noqa: F401 — 호출자 편의 재노출
from typing import Callable

from .vocab import (CHANNELS, ExposureSource, HypothesisTuple, SERIES_FAMILIES,
                    TRANSFORMS, Trigger, VocabError, Vulnerability)

Ask = Callable[[str, str], dict]    # (system, user) -> 파싱된 JSON 객체
MAX_ASKS = 2                        # 최초 1 + 되물음 1. 결정론적 실패 반복 금지(감사 2R)

_SYSTEM = """너는 인과 가설 에이전트다. 아래 **닫힌 어휘**의 값만 쓸 수 있다 - 목록 밖 값은 거부된다.

채널 8: {channels}
계열족 9: {families}
변환 5: {transforms}
비교: [">=", "<="] · 결과종류: ["수익률", "전이"] · 부호: 1 | -1
방아쇠: {{"kind": "점", "ident": <아래 접지 목록의 사건타입>}} 또는 {{"kind": "계열", "ident": <오늘 발화 계열족>}}
이 셀에 접지된 사건 타입 (점 방아쇠는 이 목록에서만): {event_types}
오늘 |z|≥2 로 발화한 계열족 (계열 방아쇠는 이 목록에서만): {series_families}
지금 패널로 잴 수 있는 노출 (계열족, 변환): {measurable}
  - 다른 조합도 어휘상 합법이지만 판정불가로 남는다. 검정되길 원하면 여기서 골라라.

가설 = 튜플. JSON 하나만:
{{"hypotheses": [{{
  "vulnerabilities": [{{"family": 계열족, "transform": 변환, "comparator": ">=", "percentile": 0.9}}],
  "trigger": {{"kind": "점|계열", "ident": "..."}},
  "channel": "...",
  "exposure": {{"kind": "속성", "ident": 계열족, "transform": 변환}},
  "from_role": "...", "to_role": "...", "outcome": "수익률", "sign": 1,
  "reduction_note": "이 셀의 무엇을 이 타입으로 읽었는가 한 줄"
}}, ...]}}

규칙:
- 가설 정확히 {n}개, **서로 다른 채널**로 (같은 채널 = 같은 가설의 변주다)
- 사건 id·수치 생성 금지 (백분위 임계만 예외)
- 셀의 시간 알리바이와 모순 금지 - 알리바이로 배제된 사건을 원인으로 세우지 마라
- 취약성은 "왜 이 종목이·얼마나"(느린 조건), 방아쇠는 "왜 오늘"(빠른 원인)이다
- 취약성 피처는 노출 피처와 **달라야 한다** - 같으면 조건이 아니라 동어반복이고 표본만 죽는다"""


def _parse(h: dict) -> HypothesisTuple:
    """모델 산출 → 튜플. 실패는 예외 - 사유가 그대로 되물음 문장이 된다."""
    return HypothesisTuple(
        vulnerabilities=tuple(Vulnerability(**v) for v in h.get("vulnerabilities") or ()),
        trigger=Trigger(**(h.get("trigger") or {})),
        channel=str(h.get("channel", "")),
        exposure=ExposureSource(**(h.get("exposure") or {})),
        from_role=str(h.get("from_role", "")), to_role=str(h.get("to_role", "")),
        outcome=str(h.get("outcome", "")), sign=int(h.get("sign", 0)),
        reduction_note=str(h.get("reduction_note", ""))[:200])


def propose(ask: Ask, *, facts: str, event_types: list[str],
            measurable: list[tuple[str, str]] = (),
            series_families: list[str] = (),
            n: int = 3) -> tuple[list[HypothesisTuple], list[str]]:
    """튜플 후보를 받는다. 반환: (유효 튜플들, 거부 사유들 - 감사용).

    거부된 것은 폐기하고 사유를 되물음에 싣는다. 유효 < 2 면 한 번 다시 묻는다 -
    같은 프롬프트의 반복이 아니라 **거부 사유가 추가된** 프롬프트다(요청을 바꿔
    재시도, 감사 2R 교훈).
    """
    system = _SYSTEM.format(channels=sorted(CHANNELS), families=sorted(SERIES_FAMILIES),
                            transforms=sorted(TRANSFORMS), event_types=event_types,
                            series_families=sorted(series_families),
                            measurable=sorted(measurable), n=n)
    rejected: list[str] = []
    valid: list[HypothesisTuple] = []
    user = facts
    for _ in range(MAX_ASKS):
        out = ask(system, user)
        valid, seen_ch = [], set()
        for i, h in enumerate(out.get("hypotheses") or [], 1):
            try:
                t = _parse(h)
            except (VocabError, TypeError, ValueError, KeyError) as e:
                rejected.append(f"[{i}] {type(e).__name__}: {e}")
                continue
            if t.trigger.kind == "점" and t.trigger.ident not in event_types:
                rejected.append(f"[{i}] 접지 밖 사건타입 날조: {t.trigger.ident!r}")
                continue
            if t.trigger.kind == "계열" and t.trigger.ident not in series_families:
                # 점의 접지 = 셀 사건 목록, 계열의 접지 = 오늘 발화(|z|≥2) 목록.
                # 발화 안 한 계열로 오늘을 설명하는 가설은 방아쇠 날조다.
                rejected.append(f"[{i}] 미발화 계열 방아쇠 날조: {t.trigger.ident!r} - "
                                f"오늘 발화: {sorted(series_families) or '없음'}")
                continue
            if t.exposure.kind == "속성" and any(
                    v.family == t.exposure.ident and v.transform == t.exposure.transform
                    for v in t.vulnerabilities):
                # 6차 라이브 실측: 같은 피처를 취약성과 노출에 쓰면 INUS 내용이 0이고
                # 조건화가 노출 상위만 남겨 용량-반응 자체를 파괴한다 (n=23·6·6 전멸).
                rejected.append(f"[{i}] 취약성이 노출과 같은 피처"
                                f"({t.exposure.ident}/{t.exposure.transform}) - "
                                "조건이 아니라 동어반복이다. 다른 계열족으로 세워라")
                continue
            if t.channel in seen_ch:
                rejected.append(f"[{i}] 채널 중복: {t.channel} - 같은 채널은 같은 가설의 변주다")
                continue
            seen_ch.add(t.channel)
            valid.append(t)
        if len(valid) >= 2:
            break
        user = (facts + "\n\n직전 제출의 거부 사유 - 고쳐서 다시 내라:\n"
                + "\n".join(rejected[-6:]))
    return valid, rejected


__all__ = ["Ask", "MAX_ASKS", "propose"]
