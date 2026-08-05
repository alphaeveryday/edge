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
from .vocab import (CHANNELS, COMPARATORS, Condition, ExposureSource, HypothesisTuple,
                    LAYERS, OUTCOME_KINDS, SERIES_FAMILIES, TRANSFORMS,
                    Trigger, VocabError)

Ask = Callable[[str, str], dict]    # (system, user) -> 파싱된 JSON 객체
MAX_ASKS = 2                        # 최초 1 + 되물음 1. 결정론적 실패 반복 금지(감사 2R)

_SYSTEM = """너는 인과 가설 에이전트다. 아래 **닫힌 어휘**의 값만 쓸 수 있다 - 목록 밖 값은 거부된다.

채널 {n_ch}: {channels}
계열족 {n_fam}: {families}
변환 {n_tr}: {transforms}
비교: {comparators} · 결과종류: {outcomes}
단위 {n_ly}: {layers}
  - **이 뉴스가 무엇을 움직였다는 주장인가.** 시장 = 지수 전체 · 섹터 = 그 산업 ·
    고유 = 이 종목만(시장·산업을 빼고 남은 몫). 층마다 종속변수가 다르므로 이것이
    가설의 핵심 선택이다. 업황·정책·금리 뉴스를 '고유' 로 걸면 시장·산업이 이미
    차감된 잔차를 설명해야 해서 기각된다.
  - 층별 허용 노출: {layer_exposures}
방아쇠: {{"kind": "점", "ident": <아래 접지 목록의 사건타입>}} 또는 {{"kind": "계열", "ident": <오늘 발화 계열족>}}
이 셀에 접지된 사건 타입 (점 방아쇠는 이 목록에서만): {event_types}
오늘 |z|≥2 로 발화한 계열족 (계열 방아쇠는 이 목록에서만): {series_families}
지금 패널로 잴 수 있는 노출 (계열족, 변환): {measurable}
  - 다른 조합도 어휘상 합법이지만 판정불가로 남는다. 검정되길 원하면 여기서 골라라.

가설 = 튜플. JSON 하나만:
{{"hypotheses": [{{
  "conditions": [{{"family": 계열족, "transform": 변환, "comparator": ">=", "percentile": 0.9}}],
  "trigger": {{"kind": "점|계열", "ident": "..."}},
  "channel": "...",
  "exposure": {{"kind": "속성", "ident": 계열족, "transform": 변환}},
  "outcome": "수익률",
  "layer": "고유",
  "reduction_note": "이 셀의 무엇을 이 타입으로 읽었는가 한 줄",
  "intent": "이 튜플로 검정하려는 인과 주장 한 문장 - 무엇이 사실이면 성립인가"
}}, ...]}}

규칙:
- 가설 **최대 {n}개**, 서로 다른 채널로. 근거 없는 채널을 채우느니 2개가 낫다 -
  제출 수 m 이 늘면 확증 임계가 α/m 으로 좁아져 **좋은 가설까지 같이 죽는다**
- **방향을 선언하지 마라.** 우리가 찾는 것은 유효한 CATE 이고 방향은 그 추정량이
  낸다(상위−하위). 검정은 양측이므로 방향을 맞춰도 이득이 없고, 틀리면 환원 검사만
  오염시킨다
- 도구가 보여준 격자 축은 **노출** 슬롯에 넣어라 (그 축이 용량-반응을 만든다).
  조건은 다른 계열족에서 - 같은 피처면 동어반복으로 거부된다
- 사건 id·수치 생성 금지 (백분위 임계만 예외)
- 셀의 시간 알리바이와 모순 금지 - 알리바이로 배제된 사건을 원인으로 세우지 마라
- 조건은 "왜 이 종목이·얼마나"(느린 조건), 방아쇠는 "왜 오늘"(빠른 원인)이다
- 조건 피처는 노출 피처와 **달라야 한다** - 같으면 조건이 아니라 동어반복이고 표본만 죽는다"""


def _layer_menu() -> str:
    """층 → 그 층을 설명할 자격이 있는 노출. 프롬프트에 **실제 게이트를 그대로** 보인다.

    이걸 안 보이면 모델이 어휘상 합법이지만 층 게이트에서 죽는 조합을 계속 낸다 -
    거절 사유를 되물음으로 배우게 하는 것보다 메뉴로 미리 닫는 것이 싸다.
    """
    from .paneltest import LAYER_EXPOSURES
    out = []
    for ly in sorted(LAYERS):
        allow = LAYER_EXPOSURES.get(ly)
        out.append(f"{ly}=전부" if allow is None else
                   f"{ly}=" + ",".join(f"{f}/{t}" for f, t in sorted(allow)))
    return " · ".join(out)


def _cond(v: dict) -> Condition:
    """조건 dict → Condition. `family` 는 구 키(상태 전용)라 `ident` 로 받아준다 -
    어휘가 넓어져 슬롯이 계열족만 담지 않게 됐다."""
    v = dict(v)
    if "family" in v:
        v["ident"] = v.pop("family")
    return Condition(**v)


def _parse(h: dict) -> HypothesisTuple:
    """모델 산출 → 튜플. 실패는 예외 - 사유가 그대로 되물음 문장이 된다."""
    return HypothesisTuple(
        conditions=tuple(_cond(v) for v in h.get("conditions") or ()),
        trigger=Trigger(**(h.get("trigger") or {})),
        channel=str(h.get("channel", "")),
        exposure=ExposureSource(**(h.get("exposure") or {})),
        outcome=str(h.get("outcome", "")),
        layer=str(h.get("layer", "고유")),
        reduction_note=str(h.get("reduction_note", ""))[:200],
        intent=str(h.get("intent", ""))[:240])


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


def screen_tuples(hyps: list[dict], *, event_types: list[str],
                  series_families: list[str] = (),
                  measurable: list[tuple[str, str]] | None = None,
                  layer: str = "고유",
                  ) -> tuple[list[HypothesisTuple], list[str]]:
    """모델 산출 목록 → (유효 튜플, 거부 사유). propose 와 하네스 CLI 가 공유한다 -
    가설을 누가 내든(원격 모델이든 하네스 에이전트든) **심사는 같은 코드**여야 한다.

    measurable 를 주면 **못 재는 슬롯을 여기서 죽인다**. 이전에는 이 목록을 프롬프트로
    알려주기만 하고 강제하지 않았다 - 8셀 71튜플 중 55개(77%)가 쓰는 순간 n=0 확정인
    조합이었고, 그걸 패널이 돌고 나서야 알았다. 어포던스는 말이 아니라 관문이어야 한다.

    layer 는 **그 층을 설명할 자격이 있는 노출**을 강제한다. 시장층 y 는 원수익이고
    시장 수익은 전 종목 공통이라, 종목 고유 피처로는 종목 간 차이를 만들 수 없다.
    """
    from .paneltest import LAYER_EXPOSURES
    if layer not in LAYER_EXPOSURES:
        raise ValueError(f"층은 {sorted(LAYER_EXPOSURES)} 중 하나다: {layer!r}")
    allowed = LAYER_EXPOSURES[layer]
    feats = None if measurable is None else {tuple(m) for m in measurable}
    valid: list[HypothesisTuple] = []
    rejected: list[str] = []
    seen_ch: set[str] = set()
    for i, hd in enumerate(hyps or [], 1):
        def _kill(why: str) -> None:
            rejected.append(f"[{i}] {why}")
            record("tuple.rejected", idx=i, why=why, raw=hd)
        try:
            t = _parse(hd)
        except (VocabError, TypeError, ValueError, KeyError) as e:
            _kill(f"{type(e).__name__}: {e}")
            continue
        if t.trigger.kind == "점" and t.trigger.ident not in event_types:
            _kill(f"접지 밖 사건타입 날조: {t.trigger.ident!r}")
            continue
        if t.trigger.kind == "계열" and t.trigger.ident not in series_families:
            _kill(f"미발화 계열 방아쇠 날조: {t.trigger.ident!r} - "
                  f"오늘 발화: {sorted(series_families) or '없음'}")
            continue
        # ── 동어반복: 조건이 처치의 다른 슬롯을 되풀이하면 조건이 아니다 ──
        if t.exposure.kind == "속성" and any(
                v.kind == "상태" and v.ident == t.exposure.ident
                and v.transform == t.exposure.transform for v in t.conditions):
            _kill(f"조건이 노출과 같은 피처({t.exposure.ident}/{t.exposure.transform}) - "
                  "조건이 아니라 동어반복이다")
            continue
        if any(v.kind == "사건" and v.ident == t.trigger.ident for v in t.conditions):
            _kill(f"조건이 방아쇠와 같은 사건타입({t.trigger.ident}) - "
                  "'오늘 났고 최근에도 났다'는 조건이 아니다")
            continue
        if t.exposure.kind == "관계" and any(
                v.kind == "관계" and v.ident == t.exposure.ident for v in t.conditions):
            _kill(f"조건이 노출과 같은 관계({t.exposure.ident}) - 동어반복이다")
            continue
        # ── 접지: 사건 조건도 점 방아쇠와 같은 접지 규율을 받는다 ──
        if bad := [v.ident for v in t.conditions
                   if v.kind == "사건" and v.ident not in event_types]:
            _kill(f"접지 밖 사건 조건 날조: {bad}")
            continue
        # ── 측정 가능성: 못 재는 조합은 가설이 아니라 소원이다 ──
        if feats is not None:
            if t.exposure.kind == "속성" and (t.exposure.ident, t.exposure.transform) not in feats:
                _kill(f"못 재는 노출({t.exposure.ident}/{t.exposure.transform}) - "
                      f"재는 것: {sorted(feats)}")
                continue
            if bad := [v.key for v in t.conditions
                       if v.kind == "상태" and (v.ident, v.transform) not in feats]:
                _kill(f"못 재는 조건{bad} - 재는 것: {sorted(feats)}")
                continue
        # ── 층 자격: 그 층의 y 를 설명할 수 있는 노출인가 ──
        if allowed is not None and t.exposure.kind == "속성" and (
                (t.exposure.ident, t.exposure.transform) not in allowed):
            _kill(f"{layer}층을 설명할 수 없는 노출"
                  f"({t.exposure.ident}/{t.exposure.transform}) - "
                  f"{layer}층에 유효한 노출: {sorted(allowed)}")
            continue
        if t.channel in seen_ch:
            _kill(f"채널 중복: {t.channel}")
            continue
        seen_ch.add(t.channel)
        valid.append(t)
    return valid, rejected


def propose(ask: Ask, *, facts: str, event_types: list[str],
            measurable: list[tuple[str, str]] = (),
            series_families: list[str] = (),
            n: int = 3) -> tuple[list[HypothesisTuple], list[str]]:
    """튜플 후보를 받는다. 반환: (유효 튜플들, 거부 사유들 - 감사용).

    거부된 것은 폐기하고 사유를 되물음에 싣는다. 유효 < 2 면 한 번 다시 묻는다 -
    같은 프롬프트의 반복이 아니라 **거부 사유가 추가된** 프롬프트다(요청을 바꿔
    재시도, 감사 2R 교훈).
    """
    # 개수·목록 리터럴은 **어휘에서 파생**된다 (21R). 손으로 적은 리터럴은 낡는다 -
    # 실측: 프롬프트가 '계열족 9 · 변환 5' 라고 말하는 동안 어휘는 17·6 이었고,
    # 결과종류는 하드코딩 2종이라 '되돌림' 축을 모델이 고를 수조차 없었다.
    system = _SYSTEM.format(channels=sorted(CHANNELS), families=sorted(SERIES_FAMILIES),
                            transforms=sorted(TRANSFORMS),
                            n_ch=len(CHANNELS), n_fam=len(SERIES_FAMILIES),
                            n_tr=len(TRANSFORMS), comparators=sorted(COMPARATORS),
                            outcomes=sorted(OUTCOME_KINDS), event_types=event_types,
                            layers=sorted(LAYERS), n_ly=len(LAYERS),
                            layer_exposures=_layer_menu(),
                            series_families=sorted(series_families),
                            measurable=sorted(measurable), n=n)
    rejected: list[str] = []
    valid: list[HypothesisTuple] = []
    user = facts
    for turn in range(MAX_ASKS):
        out = ask(system, user)
        valid, rej = screen_tuples(out.get("hypotheses") or [],
                                   event_types=event_types,
                                   series_families=list(series_families))
        rejected += rej
        for t in valid:
            record("tuple.accepted", turn=turn + 1, channel=t.channel,
                   trigger=f"{t.trigger.kind}:{t.trigger.ident}",
                   exposure=f"{t.exposure.ident}/{t.exposure.transform}",
                   reduction_note=t.reduction_note, intent=t.intent)
        if len(valid) >= 2:
            break
        user = (facts + "\n\n직전 제출의 거부 사유 - 고쳐서 다시 내라:\n"
                + "\n".join(rejected[-6:]))
    return valid, rejected


__all__ = ["Ask", "MAX_ASKS", "explore", "propose", "screen_tuples"]
