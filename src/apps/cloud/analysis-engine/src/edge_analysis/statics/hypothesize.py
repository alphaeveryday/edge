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

import json
import time
from dataclasses import replace
from typing import Callable

from ..observability import record
from .hypothesis_preview import MAX_DISTRIBUTION_PREVIEWS
from .model_contract import ModelContractError, ask_checked, list_field, object_field
from .vocab import (CHANNELS, COMPARATORS, Condition, ExposureSource, HypothesisTuple,
                    LAYERS, OUTCOME_KINDS, SERIES_FAMILIES, TRANSFORMS,
                    Trigger, VocabError)

Ask = Callable[[str, str], dict]    # (system, user) -> 파싱된 JSON 객체
MAX_ASKS = 2                        # 최초 1 + 되물음 1. 결정론적 실패 반복 금지(감사 2R)
MAX_SQL_ROUNDS = 4                  # propose 한 번당 sql 왕복 상한 (ALPHA-886 2단계)
SQL_TIMEBOX_S = 120.0               # sql 탐색 전체 벽시계 상한 — 상한 초과는 정직 종료
MAX_OBJECT_ROUNDS = 6               # 무한 루프 금지. 6 = list_options 1 + preview 3(사건
                                    # 분포 상한, ALPHA-938) + 재조회·거부 재시도 여유 2
# 최종 제출 상한 - preview 도구 상한(hypothesis_preview.MAX_DISTRIBUTION_PREVIEWS)과
# 단일 출처다: 두 게이트(도구 실행·최종 제출)가 갈리면 프롬프트 계약("최대 3개")이
# 한쪽에서만 강제된다. 초과분은 사유와 함께 기각(수용분은 유지).
MAX_PREVIEW_SUBMISSIONS = MAX_DISTRIBUTION_PREVIEWS

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
- 가설 **최대 {n}개**, 서로 다른 채널로. 근거 없는 후보를 채우느니 2개가 낫다 -
  제출 수 m 이 늘면 확증 임계가 α/m 으로 좁아져 **좋은 가설까지 같이 죽는다**
- **방향을 선언하지 마라.** 우리가 찾는 것은 유효한 CATE 이고 방향은 그 추정량이
  낸다(상위−하위). 검정은 양측이므로 방향을 맞춰도 이득이 없고, 틀리면 환원 검사만
  오염시킨다
- proxy 후보는 **뉴스의 기제와 위 측정 스키마만** 보고 노출 슬롯에 넣어라.
  패널 결과·표본수·p값은 후보 선택 뒤 검정기가 처음 본다
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


def _trace_safe(value: object) -> object:
    """trace에 넣을 후보를 JSON scalar/container로 고정한다."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return repr(value)


def render_hypothesis(t: HypothesisTuple) -> str:
    """가설을 사람이 읽는 자연스러운 한 문단으로 결정론적으로 렌더한다.

    이 문장은 감사/trace 표시용이다. 고객 설명이나 검정 결과를 대신하지 않는다.
    """
    return "preview_required"


_PREVIEW_SYSTEM = """당신은 인과 가설 에이전트다. 서버가 제공한 ObjectSet과 hypothesis 도구만 사용한다.

이 실행의 사건 집합은 서버가 이미 고정했다. 먼저 `hypothesis.list_options`를 빈 arguments 객체로 호출해 이 실행에서 선택 가능한 어휘를 확인한다. 사건 집합을 얻기 위해 `objectset.create`를 호출하지 마라.
조건을 쓸 때는 고른 exposure_id를 넣어 `hypothesis.list_options`를 다시 호출하고, 그 결과의 modifier ID만 쓴다. 검정하려는 설계마다 `hypothesis.preview`를 호출한다. READY인 preview만 최종 제출할 수 있다.

최종 제출은 {"hypotheses": [{"preview_handle": "...", "intent": "..."}]} 형태의 JSON 하나다.
`preview_handle`은 READY `hypothesis.preview` 결과의 `handle` 값을 그대로 써야 한다.
서버가 preview에 고정한 설계만 실행한다.
"""

_EVENT_DISTRIBUTION_PREVIEW_SYSTEM = """당신은 사건 설명 가설 에이전트다. 서버가 제공한 hypothesis 도구만 사용한다.

이 실행의 사건 집합은 서버가 이미 고정했다. hypothesis 도구 외에는(`objectset.*`·`news.*` 등)
호출하지 마라 — 호출해도 거부되고 도구 예산만 소모된다.
먼저 `hypothesis.list_options`를 빈 arguments 객체로 호출한다. 여기의 event_candidates 중
서로 다른 사건을 **최대 3개까지** 골라, 각 사건마다 `사건 당일 시장 초과수익률` outcome으로
`hypothesis.preview`를 호출한다. READY preview만 제출할 수 있다 — READY인 것들을 모두 모아
{"hypotheses": [{"preview_handle": "...", "intent": "..."}]} 형태의 JSON 하나로 제출한다.
`preview_handle`은 READY `hypothesis.preview` 결과의 `handle` 값만 유효하다 — 사건 id 를
핸들로 쓰지 마라. `intent`에는 그 검정을 왜 확인할지 쓴다. 서버가 고정한 사건과
동일 사건 유형의 과거 분포를 바꾸거나, 조건·노출·채널을 새로 만들지 마라.
"""

# 분포 preview 모드의 도구 오퍼 - _OBJECT_OFFER 와 달리 objectset 예시를 광고하지
# 않는다(ALPHA-970). 스펙도 호출부가 hypothesis.* 만 남겨 걸러 넣는다.
_PREVIEW_OFFER = """

[hypothesis 도구 · 필수] 도구 호출은 {{"tool": "hypothesis.list_options", "arguments": {{}}}} 모양의
JSON 하나로 답한다. 실행 결과를 다음 호출에 쓴다. preview 가 끝나면 hypotheses JSON을 답한다.
도구 계약:
{specs}"""

# 분포 preview 모드에서 hypothesis 외 도구 호출을 실행 없이 거부할 때의 사유 - 모델을
# list_options→preview 경로로 유도하는 교정 신호다(ALPHA-970). 실측: 프롬프트 금지문
# 없이 도구가 정상 실행되자 모델이 라운드 6회 전부를 `objectset.create` 반복으로
# 소진하고 사건 id 를 핸들로 위조 제출해 분포 문장이 하루 전멸했다(2026-08-12 10/10).
# objectset 만 막으면 같은 낭비가 위임 경로의 news.* 등으로 우회된다(Codex P2).
_NON_HYPOTHESIS_REFUSAL = {
    "ok": False,
    "error": "TOOL_NOT_AVAILABLE",
    "message": ("이 실행의 사건 집합은 서버가 고정했다 - hypothesis 도구만 제공된다. "
                "hypothesis.list_options 로 event_candidates 를 확인하고 "
                "hypothesis.preview 를 호출하라."),
}


def _resolve_preview_hypotheses(hyps: list[object], resolver: Callable[[str], object]
                                ) -> tuple[list[HypothesisTuple], list[str], list[dict[str, object]]]:
    """Accept only a current runtime's READY preview and preserve its frozen recipe."""
    valid: list[HypothesisTuple] = []
    rejected: list[str] = []
    rendered: list[dict[str, object]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(hyps, 1):
        if not isinstance(raw, dict) or set(raw) != {"preview_handle", "intent"}:
            why = "최종 가설은 preview_handle과 intent만 포함해야 합니다"
            rejected.append(f"[{idx}] {why}")
            record("tuple.rejected", idx=idx, why=why, raw=raw)
            continue
        handle = raw["preview_handle"]
        intent = raw["intent"]
        if (not isinstance(handle, str) or not handle or not isinstance(intent, str)
                or not intent.strip() or len(intent) > 240):
            why = "preview_handle 또는 intent 형식이 올바르지 않습니다"
            rejected.append(f"[{idx}] {why}")
            record("tuple.rejected", idx=idx, why=why, raw=raw)
            continue
        if handle in seen:
            why = "같은 preview_handle을 중복 제출할 수 없습니다"
            rejected.append(f"[{idx}] {why}")
            record("tuple.rejected", idx=idx, why=why, raw=raw)
            continue
        seen.add(handle)
        try:
            preview = resolver(handle)
            recipe = getattr(preview, "hypothesis")
            summary = getattr(preview, "summary")
            if not isinstance(recipe, HypothesisTuple) or not isinstance(summary, str):
                raise ValueError("PREVIEW_RESOLUTION_INVALID")
        except Exception as exc:  # noqa: BLE001 - resolver is the run-scope trust boundary
            code = str(getattr(exc, "code", "PREVIEW_HANDLE_REJECTED"))
            why = f"preview_handle을 실행할 수 없습니다: {code}"
            rejected.append(f"[{idx}] {why}")
            record("tuple.rejected", idx=idx, why=why, raw=raw)
            continue
        valid.append(replace(recipe, intent=intent.strip(), preview_handle=handle))
        rendered.append({"text": summary, "llm_intent": intent.strip(),
                         "status": "ready", "preview_handle": handle})
    return valid, rejected, rendered


def _tool_result_summary(name: str, result: object) -> dict[str, object]:
    """ObjectSet 결과 중 대시보드에 필요한 안전한 식별·요약만 보존한다."""
    raw = result if isinstance(result, dict) else {}
    out: dict[str, object] = {"tool": name, "ok": bool(raw.get("ok"))}
    for key in ("handle", "lineage_id", "kind", "as_of", "row_count"):
        value = raw.get(key)
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
    if "row_count" not in out and isinstance(raw.get("count"), int):
        out["row_count"] = raw["count"]
    pit = raw.get("pit")
    if isinstance(pit, dict):
        gaps = pit.get("gaps", [])
        out["has_gaps"] = bool(gaps)
        out["gap_count"] = len(gaps) if isinstance(gaps, (list, tuple, set)) else 0
        out["pit_clamped"] = (pit.get("clamp")
                              if isinstance(pit.get("clamp"), bool) else None)
    return out


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
            pick = ask_checked(ask, "너는 관측자다. 부를 도구를 JSON 으로만 답한다.",
                               facts + "\n\n" + user)
        except ModelContractError:
            raise
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
    feats = None if measurable is None else {tuple(m) for m in measurable}
    valid: list[HypothesisTuple] = []
    rejected: list[str] = []
    seen_ch: set[str] = set()
    for i, hd in enumerate(hyps or [], 1):
        def _kill(why: str) -> None:
            rejected.append(f"[{i}] {why}")
            record("tuple.rejected", idx=i, why=why, raw=hd)
        try:
            t = _parse(hd if "layer" in hd else {**hd, "layer": layer})
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
        # ── 층 자격: 튜플이 선언한 층의 y 를 설명할 수 있는 노출인가 ──
        allowed = LAYER_EXPOSURES[t.layer]
        if allowed is not None and t.exposure.kind == "속성" and (
                (t.exposure.ident, t.exposure.transform) not in allowed):
            _kill(f"{t.layer}층을 설명할 수 없는 노출"
                  f"({t.exposure.ident}/{t.exposure.transform}) - "
                  f"{t.layer}층에 유효한 노출: {sorted(allowed)}")
            continue
        if t.channel in seen_ch:
            _kill(f"채널 중복: {t.channel}")
            continue
        seen_ch.add(t.channel)
        valid.append(t)
    return valid, rejected


_SQL_OFFER = """

[탐색 도구 · 선택] 가설을 내기 전에 데이터를 직접 조회할 수 있다.
{{"sql": "SELECT ..."}} 하나만 담은 JSON 으로 답하면 실행 결과가 다음 메시지에 실린다.
최대 {cap}회. 거부되면 reason 을 읽고 고쳐라. 조회가 끝나면(필요 없으면 즉시)
hypotheses JSON 으로 답하라 — sql 과 hypotheses 를 한 응답에 섞지 마라.
{desc}"""

_SQL_DONE = ("\n\n[sql] 왕복 상한 소진 — 추가 조회 없이 지금 아는 것만으로 "
             "hypotheses JSON 을 내라.")

_OBJECT_OFFER = """

[ObjectSet 탐색 도구 · 선택] 가설을 내기 전에 시점 고정 객체 집합을 조사할 수 있다.
도구 호출은 {{"tool": "objectset.create", "arguments": {{"kind": "..."}}}} 모양의
JSON 하나로 답한다. 실행 결과의 handle을 다음 호출에 쓴다. 모델이 기준시각이나 저장소
이름을 정하지 않는다. 조회가 끝나면 hypotheses JSON을 답한다.
도구 계약:
{specs}"""

_OBJECT_DONE = ("\n\n[ObjectSet] 왕복 상한 소진 — 추가 탐색 없이 지금 아는 것만으로 "
                "hypotheses JSON 을 내라.")


def _sql_loop(ask: Ask, system: str, user: str, call: Callable[[str], dict],
              budget: int, deadline: float) -> tuple[dict, str, int, int]:
    """모델이 hypotheses 를 낼 때까지 sql 왕복을 돌린다.

    반환: (마지막 응답, 결과가 누적된 user, 왕복 수, 거부 수). 상한·타임박스가 다하면
    소진을 **알리고 한 번 더** 묻는다 — 조용한 절단은 모델이 답을 안 낸 것과 못
    가른다(정직 종료). 질의 실패는 reason 그대로 되먹인다 — 오류도 관측이다.
    """
    used = rejects = 0
    out = ask_checked(ask, system, user)
    while isinstance(out, dict) and str(out.get("sql") or "").strip():
        if used >= budget or time.monotonic() >= deadline:
            user += _SQL_DONE
            out = ask_checked(ask, system, user)
            break
        used += 1
        sql = str(out["sql"]).strip()
        res = call(sql)
        if not res.get("ok"):
            rejects += 1
        user += (f"\n\n[sql 결과 {used}/{budget}] 질의: {sql}\n"
                 + json.dumps(res, ensure_ascii=False))
        out = ask_checked(ask, system, user)
    return out, user, used, rejects


def _object_loop(ask: Ask, system: str, user: str, call: Callable[[str, dict], dict],
                 budget: int) -> tuple[dict, str, int, int, list[dict[str, object]], bool]:
    """Run bounded structured tool calls; executable text is never an argument shape."""
    used = rejects = 0
    tool_summaries: list[dict[str, object]] = []
    previewable_options = False
    out = ask_checked(ask, system, user)
    while isinstance(out, dict):
        name = str(out.get("tool") or "").strip()
        if not name:
            break
        if used >= budget:
            user += _OBJECT_DONE
            out = ask_checked(ask, system, user)
            break
        used += 1
        arguments = object_field(out, "arguments")
        res = call(name, arguments)
        if name == "hypothesis.list_options":
            generic_options = (res.get("triggers") and res.get("outcomes")
                               and res.get("layers") and res.get("exposures"))
            distribution_options = res.get("event_candidates") and res.get("outcomes")
            previewable_options = previewable_options or bool(
                res.get("ok") and (generic_options or distribution_options))
        summary = _tool_result_summary(name, res)
        tool_summaries.append(summary)
        record("hypothesis.tool_result", **summary)
        if not res.get("ok"):
            rejects += 1
        # Do not echo the raw model arguments. The validated result is the observation.
        user += (f"\n\n[ObjectSet 결과 {used}/{budget}] 도구: {name or 'schema'}\n"
                 + json.dumps(res, ensure_ascii=False))
        out = ask_checked(ask, system, user)
    return out, user, used, rejects, tool_summaries, previewable_options


def propose(ask: Ask, *, facts: str, event_types: list[str],
            measurable: list[tuple[str, str]] = (),
            series_families: list[str] = (),
            n: int = 3, sql_tool: dict | None = None,
            object_tools: dict | None = None,
            ) -> tuple[list[HypothesisTuple], list[str]]:
    """튜플 후보를 받는다. 반환: (유효 튜플들, 거부 사유들 - 감사용).

    거부된 것은 폐기하고 사유를 되물음에 싣는다. 유효 < 2 면 한 번 다시 묻는다 -
    같은 프롬프트의 반복이 아니라 **거부 사유가 추가된** 프롬프트다(요청을 바꿔
    재시도, 감사 2R 교훈).

    sql_tool (`sqltool.tool_spec` 모양: description·call) 을 주면 모델이 제안 전에
    {"sql": ...} 왕복으로 레이크를 조회할 수 있다(상한 MAX_SQL_ROUNDS·타임박스).
    툴은 **제안 재료 탐색용**이다 — 심사(screen_tuples)·검정 계약은 그대로고,
    안 주면(구형 호출자) 현행 주입식 단발과 동일하게 돈다.
    """
    # 개수·목록 리터럴은 **어휘에서 파생**된다 (21R). 손으로 적은 리터럴은 낡는다 -
    # 실측: 프롬프트가 '계열족 9 · 변환 5' 라고 말하는 동안 어휘는 17·6 이었고,
    # 결과종류는 하드코딩 2종이라 '되돌림' 축을 모델이 고를 수조차 없었다.
    preview_resolver = (object_tools or {}).get("resolve_preview")
    preview_mode = callable(preview_resolver)
    system = ((object_tools or {}).get("preview_system", _PREVIEW_SYSTEM)
              if preview_mode else _SYSTEM.format(
        channels=sorted(CHANNELS), families=sorted(SERIES_FAMILIES),
        transforms=sorted(TRANSFORMS), n_ch=len(CHANNELS), n_fam=len(SERIES_FAMILIES),
        n_tr=len(TRANSFORMS), comparators=sorted(COMPARATORS),
        outcomes=sorted(OUTCOME_KINDS), event_types=event_types,
        layers=sorted(LAYERS), n_ly=len(LAYERS), layer_exposures=_layer_menu(),
        series_families=sorted(series_families), measurable=sorted(measurable), n=n))
    if sql_tool and object_tools:
        raise ValueError("sql_tool and object_tools cannot be enabled together")
    if object_tools and object_tools.get("preview_system"):
        # 사건 분포 모드(ALPHA-970) - 사건 집합은 서버가 고정했다. 세 겹으로 막는다:
        # ① 스펙에서 objectset.* 를 걸러 어포던스 자체를 없앤다(금지문 뒤에서 도구
        # 계약이 다시 objectset 을 광고하면 모델이 그쪽을 따른다), ② 오퍼 예시도
        # hypothesis 도구로 바꾼다, ③ 그래도 호출하면 실행 없이 사유로 거부한다
        # (프롬프트만으로는 게이트가 아니다 - ok=true 로 실행되면 모델이 반복한다).
        specs = [s for s in object_tools["specs"]
                 if str(s.get("name", "")).startswith("hypothesis.")]
        system += _PREVIEW_OFFER.format(specs=json.dumps(
            specs, ensure_ascii=False, separators=(",", ":")))
        # 거부는 hypothesis.* 외 전부다 - objectset 만 막으면 위임 경로의 news.*
        # 등으로 같은 낭비가 우회된다(Codex P2).
        inner_call = object_tools["call"]
        object_tools = {**object_tools, "call": (
            lambda name, arguments: inner_call(name, arguments)
            if str(name).startswith("hypothesis.")
            else dict(_NON_HYPOTHESIS_REFUSAL))}
    elif object_tools:
        system += _OBJECT_OFFER.format(specs=json.dumps(
            object_tools["specs"], ensure_ascii=False, separators=(",", ":")))
    elif sql_tool:
        system += _SQL_OFFER.format(cap=MAX_SQL_ROUNDS, desc=sql_tool["description"])
    rejected: list[str] = []
    valid: list[HypothesisTuple] = []
    base = facts
    sql_used = sql_rejects = 0
    object_used = object_rejects = 0
    tool_summaries: list[dict[str, object]] = []
    previewable_options = False
    deadline = time.monotonic() + SQL_TIMEBOX_S
    for turn in range(MAX_ASKS):
        user = base
        if object_tools:
            out, base, u, rj, summaries, available = _object_loop(
                ask, system, user, object_tools["call"], MAX_OBJECT_ROUNDS - object_used)
            object_used += u
            object_rejects += rj
            tool_summaries.extend(summaries)
            previewable_options = previewable_options or available
        elif sql_tool:
            out, base, u, rj = _sql_loop(ask, system, user, sql_tool["call"],
                                         MAX_SQL_ROUNDS - sql_used, deadline)
            sql_used += u
            sql_rejects += rj
        else:
            out = ask_checked(ask, system, user)
        raw_hypotheses = list_field(out, "hypotheses")
        if (preview_mode and not raw_hypotheses
                and isinstance(out, dict) and "preview_handle" in out):
            # 낱개 {"preview_handle","intent"} 최종 제출 - 프롬프트 지시("preview_handle
            # 과 intent 로 제출")를 따른 유효 제출이 래핑 부재로 증발하던 실측
            # (ALPHA-935, READY_NOT_SUBMITTED 전건의 원인). 검증은 _resolve 가
            # exact-set 으로 그대로 한다 - 여기서 키를 거르지 않는다.
            raw_hypotheses = [out]
        record("hypothesis.raw", turn=turn + 1,
               hypotheses=_trace_safe(raw_hypotheses))
        if preview_mode:
            overflow: list[str] = []
            # 상한은 사건 분포 모드(전용 preview_system 을 넘긴 호출자)에만 건다 -
            # 일반 preview 모드는 상한 계약이 없어(프롬프트가 "설계마다 preview")
            # 여기서 자르면 유효 가설이 손실되는 회귀다.
            distribution_mode = bool((object_tools or {}).get("preview_system"))
            if (distribution_mode
                    and len(raw_hypotheses) > MAX_PREVIEW_SUBMISSIONS):
                # 상한은 서버가 강제한다(프롬프트 계약만으로는 게이트가 아니다) -
                # 제출 순서 앞 3개는 유지하고 초과분은 사유째 원장 행이 된다.
                # 사유는 요약 1행이다: 건별로 늘어놓으면 재시도 지시문의
                # rejected[-6:] 창에서 실제 형식·handle 실패 사유를 밀어낸다.
                dropped = len(raw_hypotheses) - MAX_PREVIEW_SUBMISSIONS
                overflow = [f"제출 상한 {MAX_PREVIEW_SUBMISSIONS}개 초과 - "
                            f"제출 순서 뒤의 {dropped}건을 기각합니다"]
                raw_hypotheses = raw_hypotheses[:MAX_PREVIEW_SUBMISSIONS]
            valid, rej, rendered_hypotheses = _resolve_preview_hypotheses(
                raw_hypotheses, preview_resolver)
            rej += overflow
            if previewable_options and not raw_hypotheses:
                # 형식 오독과 진짜 미제출을 가른다(Rule 12) - "READY preview 필요"
                # 사유가 형식 불일치까지 덮으면 원장만 봐서는 모델이 제출을 안 한
                # 것으로 오독된다(ALPHA-935 가 정확히 그 오독이었다). 빈
                # {"hypotheses": []} 만 미제출이고, hypotheses 키가 없는 응답({}
                # 포함)은 전부 형식 불일치다.
                if isinstance(out, dict) and "hypotheses" not in out:
                    rej.append('최종 응답에 hypotheses 배열이 없습니다 - '
                               '{"hypotheses": [{"preview_handle": "...", '
                               '"intent": "..."}]} 형태로 제출해야 합니다')
                else:
                    rej.append("최종 제출에는 READY preview가 하나 이상 필요합니다")
            for rendered, hypothesis in zip(rendered_hypotheses, valid):
                rendered["tool_results"] = list(tool_summaries)
                rendered["trigger"] = f"{hypothesis.trigger.kind}:{hypothesis.trigger.ident}"
        else:
            valid, rej = screen_tuples(raw_hypotheses,
                                       event_types=event_types,
                                       series_families=list(series_families),
                                       measurable=(measurable or None))
            rendered_hypotheses = [{"text": render_hypothesis(t),
                                    "llm_intent": t.intent,
                                    "status": "preview_required",
                                    "tool_results": list(tool_summaries),
                                    "trigger": f"{t.trigger.kind}:{t.trigger.ident}"}
                                   for t in valid]
        rejected += rej
        record("hypothesis.rendered", turn=turn + 1,
               hypotheses=rendered_hypotheses)
        for t in valid:
            record("tuple.accepted", turn=turn + 1, channel=t.channel,
                   trigger=f"{t.trigger.kind}:{t.trigger.ident}",
                   exposure=f"{t.exposure.ident}/{t.exposure.transform}",
                   reduction_note=t.reduction_note, intent=t.intent)
        if valid and (preview_mode or len(valid) >= 2):
            break
        retry = "\n\n직전 제출의 거부 사유 - 고쳐서 다시 내라:\n" + "\n".join(rejected[-6:])
        if (preview_mode and previewable_options and
                (not raw_hypotheses or any("PREVIEW_HANDLE" in reason for reason in rej))):
            # 이미 READY handle 을 받아 놓고 형식·검증에서 떨어진 모델에게 "preview 를
            # 호출하라"고 강제하면 재시도까지 어긋난다(ALPHA-935) - 형식부터 알려주고,
            # handle 이 없을 때만 preview 호출을 요구한다.
            retry += ('\n\n[서버 재시도 지시] 최종 제출은 {"hypotheses": '
                      '[{"preview_handle": "...", "intent": "..."}]} 형태의 JSON 하나다. '
                      "`preview_handle`은 READY `hypothesis.preview` 결과의 handle 값이어야 "
                      "한다 - 아직 READY handle 이 없으면 먼저 `hypothesis.preview` 도구 호출로 "
                      "받아라(option ID 는 hypothesis.list_options 결과의 것만).")
        base += retry
    if sql_tool:
        # 왕복 수·거부 수 한 줄 — 질의 원문·결과는 sqltool 이 이미 record 로 남긴다.
        record("hypothesize.sql_rounds", rounds=sql_used, rejected=sql_rejects)
    if object_tools:
        record("hypothesize.objectset_rounds", rounds=object_used,
               rejected=object_rejects)
    return valid, rejected
__all__ = ["Ask", "MAX_ASKS", "MAX_OBJECT_ROUNDS", "MAX_SQL_ROUNDS",
           "explore", "propose", "render_hypothesis", "screen_tuples"]
