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

from ..observability import record
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
  "outcome": "수익률", "sign": 1,
  "reduction_note": "이 셀의 무엇을 이 타입으로 읽었는가 한 줄"
}}, ...]}}

규칙:
- 가설 **최대 {n}개**, 서로 다른 채널로. 근거 없는 채널을 채우느니 2개가 낫다 -
  제출 수 m 이 늘면 확증 임계가 α/m 으로 좁아져 **좋은 가설까지 같이 죽는다**
- 부호는 오늘 수익률 부호에 맞추지 마라. 메커니즘이 정하는 것이고, 검정은 양측이라
  부호를 맞춰도 이득이 없다 (틀린 부호는 환원 검사만 오염시킨다)
- 도구가 보여준 격자 축은 **노출** 슬롯에 넣어라 (그 축이 용량-반응을 만든다).
  취약성은 다른 계열족에서 - 같은 피처면 동어반복으로 거부된다
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
        outcome=str(h.get("outcome", "")), sign=int(h.get("sign", 0)),
        reduction_note=str(h.get("reduction_note", ""))[:200])


_EXPLORE = """도구를 불러 이 셀을 조사한다. **여기 없는 도구는 존재하지 않는다.**
한 턴에 **여러 개**를 부를 수 있다 - 서로 안 기다려도 되는 것은 같이 불러라:
  {{"tools": [{{"tool": "이름", "arg": "인자(없으면 생략)"}}, ...]}}
조건을 채우면 다음 단계 메뉴가 그 턴 안에서 열린다. 메뉴가 비면 조사가 끝난 것이다.

{menu}

지금까지 본 것:
{seen}"""


def explore(ask: Ask, machine, *, facts: str, max_turns: int = 4) -> str:
    """상태기계를 돌려 관측을 모은다. 반환: 가설 단계에 실릴 관측 기록.

    브리핑(셀 좌표·커버리지)은 묻지 않고 먼저 준다 - 결정론이라 물어볼 값이 없다.
    도구 이름을 지어내면 그 사실을 응답으로 돌려준다 - 오류도 관측이고, 무엇을
    부르려다 막혔는지가 표면의 결함 목록이 된다(STORM dyn2 의 실패 양식).
    """
    seen: list[str] = [machine.brief()]
    for _ in range(max_turns):
        if machine.done:
            break
        user = _EXPLORE.format(menu=machine.menu(),
                               seen="\n".join(seen[-8:]) or "  (아직 없음)")
        try:
            pick = ask("너는 관측자다. 부를 도구를 JSON 으로만 답한다.",
                       facts + "\n\n" + user)
        except Exception as e:                     # noqa: BLE001 - 실패도 관측
            seen.append(f"[호출 실패] {type(e).__name__}: {e}")
            break
        # 배치 · 단건 어느 모양으로 와도 받는다 - 형식 실수로 턴을 태우지 않는다.
        batch = pick.get("tools") or ([pick] if pick.get("tool") else [])
        if not batch:
            seen.append("[빈 선택] 도구 이름이 없다")
            continue
        for one in batch[:4]:
            name = str(one.get("tool", "")).strip()
            if not name:
                continue
            seen.append(f"[{name}] {machine.observe(name, str(one.get('arg', '')).strip())}")
            if machine.done:
                break
    return "\n".join(seen)


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
    for turn in range(MAX_ASKS):
        out = ask(system, user)
        valid, seen_ch = [], set()
        for i, h in enumerate(out.get("hypotheses") or [], 1):
            def _kill(why: str) -> None:
                # 거부는 **원문과 함께** 남긴다 - 왜 죽었는지가 정성 디버깅의 본체다
                # (18R: 지금까지 stdout 에 3건·60자로 잘려 나가고 사라졌다).
                rejected.append(f"[{i}] {why}")
                record("tuple.rejected", turn=turn + 1, idx=i, why=why, raw=h)
            try:
                t = _parse(h)
            except (VocabError, TypeError, ValueError, KeyError) as e:
                _kill(f"{type(e).__name__}: {e}")
                continue
            if t.trigger.kind == "점" and t.trigger.ident not in event_types:
                _kill(f"접지 밖 사건타입 날조: {t.trigger.ident!r}")
                continue
            if t.trigger.kind == "계열" and t.trigger.ident not in series_families:
                # 점의 접지 = 셀 사건 목록, 계열의 접지 = 오늘 발화(|z|≥2) 목록.
                # 발화 안 한 계열로 오늘을 설명하는 가설은 방아쇠 날조다.
                _kill(f"미발화 계열 방아쇠 날조: {t.trigger.ident!r} - "
                      f"오늘 발화: {sorted(series_families) or '없음'}")
                continue
            if t.exposure.kind == "속성" and any(
                    v.family == t.exposure.ident and v.transform == t.exposure.transform
                    for v in t.vulnerabilities):
                # 6차 라이브 실측: 같은 피처를 취약성과 노출에 쓰면 INUS 내용이 0이고
                # 조건화가 노출 상위만 남겨 용량-반응 자체를 파괴한다 (n=23·6·6 전멸).
                _kill(f"취약성이 노출과 같은 피처({t.exposure.ident}/{t.exposure.transform}) - "
                      "조건이 아니라 동어반복이다. 다른 계열족으로 세워라")
                continue
            if t.channel in seen_ch:
                _kill(f"채널 중복: {t.channel} - 같은 채널은 같은 가설의 변주다")
                continue
            seen_ch.add(t.channel)
            valid.append(t)
            record("tuple.accepted", turn=turn + 1, idx=i, channel=t.channel,
                   trigger=f"{t.trigger.kind}:{t.trigger.ident}",
                   exposure=f"{t.exposure.ident}/{t.exposure.transform}",
                   vulnerabilities=[f"{v.family}/{v.transform}{v.comparator}p{v.percentile:.0%}"
                                    for v in t.vulnerabilities],
                   sign=t.sign, reduction_note=t.reduction_note)
        if len(valid) >= 2:
            break
        user = (facts + "\n\n직전 제출의 거부 사유 - 고쳐서 다시 내라:\n"
                + "\n".join(rejected[-6:]))
    return valid, rejected


__all__ = ["Ask", "MAX_ASKS", "explore", "propose"]
