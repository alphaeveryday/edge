"""P3 · 세계 그래프 — **그림에 대한 진술을 세계에 대한 진술로 바꾸는 자리.**

이전 판은 제안 에이전트가 그린 DAG 를 그대로 식별에 먹였다. 그 그래프에서 **안 그린 간선과
없는 관계는 같은 표현(부재)을 가졌다.** 그래서 `identify()` 가 낸 "뒷문 없음"은 세계가
아니라 제안자의 지식 상태를 보고했다. 실측으로 확인됐다 - 같은 셀에서 `MOM@t-1` 한 줄을
더 그리자 `adjust=[]` 가 `adjust=['MOM@t-1']` 로 바뀐다. 세계는 그대로인데 답이 바뀐다.

고치는 방법은 어휘를 좁히는 것이 아니라 **완비 의무**다 (Hernán-Robins): 그래프 위 임의의
두 변수의 공통원인은 전부 그래프에 있어야 하고, 그 조건을 만족해야만 그 DAG 가 causal DAG 다.

**이 의무가 표현력을 깎지 않는 이유**는 의무가 걸리는 범위에 있다. 완비는 *그린 변수에
한해서만* 요구된다 - 무엇을 그릴지는 여전히 자유고(노드 종별도 후보 목록도 골격도 주지
않는다), 그린 것에 대한 정직성만 의무다. 어휘를 제한하는 규칙은 모델이 세우려던 노드를
못 세우게 하지만, 완비 의무는 세우는 것을 하나도 막지 않고 **세운 뒤에 무엇을 더 적어야
하는지**만 정한다. 변수를 늘리면 의무도 같이 늘어 대충 그리는 쪽이 편해 보이지만, 안 그린
변수는 설명 몫도 못 가져간다(P8 의 예산). 넓게 그리고 정직하게 선언하는 것이 우세 전략이다.

이로써 부재의 두 의미가 갈라진다. `latents` 에 없는 공통원인은 이제 "안 봤다"가 아니라
**"없다는 선언"**이고, `completeness` 가 그 선언이 무엇을 훑은 것인지 문장으로 적는다.
선언이 틀렸으면 P5 가 소거에 실패하고 P8 이 상한을 내린다 - 침묵했을 때와 달리 흔적이 남는다.

선택 편의만은 선언에 맡기지 않는다. 기업이 고르는 사건(배당·자사주·가이던스·M&A)은 좋은
사적 정보와 함께 오므로(Bhattacharya 1979 · Miller-Rock 1985) `chosen`·`scheduled` 배정에는
`compile_latents` 가 U 를 **심는다.** 모델 산출 뒤에 무조건 돌고, 같은 자리에 모델이 쓴 약한
문구가 있어도 컴파일된 쪽이 이긴다. 기본값이 무교란이면 안 되기 때문이다.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..config import PipelineError
from ..observability import log
from . import chain
from .contracts import (
    ASSIGNMENT_SAY,
    COMPILED_LATENT,
    OUTCOME_ID,
    Hypothesis,
    Latent,
    Question,
    Relation,
    WorldGraph,
    classify,
)
from .graph import LIMITS, descendants, parse

MAX_TRIES = 3       # 그래프 시도 상한. **조회는 여기 안 센다**
MAX_LOOKUPS = 6     # 원장 조회 상한

SYSTEM = """너는 가설들이 세운 노드·간선을 **하나의 세계 그래프**로 합치고, 그 그래프가
인과 그래프이기 위한 조건을 선언한다. 새 가설을 만드는 자리가 아니다 - 합치고, 빠진
공통원인을 채우고, 무엇을 다 봤는지 적는다.

## 완비 의무 (이 단계의 전부다)
아래 변수 목록의 **임의의 두 변수**에 공통원인이 있다고 생각되면 전부 선언해라. 관측
가능하면 노드로, 아니면 U 로. 남기지 않은 것은 "그런 공통원인은 없다"는 선언이고, 그
선언이 이 그래프를 인과 그래프로 만든다. 완비 의무는 **네가 그린 변수에 한해서만** 걸린다 -
무엇을 그리든 자유고, 그린 것에 대한 정직성만 의무다.

빠뜨린 공통원인은 뒷문을 열어둔 채로 남고, 다음 단계가 조정집합을 계산할 때 그 뒷문은
조용히 "교란 없음"으로 읽힌다. 애매하면 U 로 적어라 - **U 는 감점이 아니다.** 다음 단계가
소거를 설계하고, 못 소거하면 주장의 상한이 내려갈 뿐이다. 적지 않은 U 는 상한을 내리지
않고 결론을 오염시킨다.

## 시간
노드 id 는 `이름@t±N` 이다. N 은 정수 시차이고 원인의 N 은 결과의 N 보다 작거나 같아야
한다. 같으면(동시간) 그 간선에 `simultaneous: true` 와 `simultaneous_why` 를 적어라 - 같은
시각의 두 변수 사이에 왜 방향을 줄 수 있는지. 전부 동시간인 사슬은 시간 정보가 0이다.

## 접지
사건을 가리키는 노드는 `events` 에 아래 브리프의 event_id 를 적어라. 하나도 없으면 이
그래프는 저장소와 이어지지 않는다.

## 조회
원장을 봐야 정할 수 있는 것은 추측하지 말고 물어라. `{"sql": "SELECT ..."}` 하나를 내면
결과를 돌려준다. **조회는 시도 횟수를 쓰지 않는다.**

JSON 하나만. 조회거나 그래프거나 둘 중 하나다.
{"sql": "SELECT ..."}
또는
{"graph": {
  "nodes": {"<이름@t±N>": {"says": "이 노드가 무엇이며 무엇을 어떤 단위로 재는가",
                           "observed": "어떻게 관측하나 (못 재면 null)",
                           "events": ["접지된 event_id"]}},
  "edges": [{"from": "", "to": "", "kind": "statistical|identity|elasticity",
             "says": "이 간선이 주장하는 것 한 문장",
             "because": "왜 이 경로로 전달되나",
             "false_if": "무엇이 관측되면 이 간선이 죽나",
             "simultaneous": false, "simultaneous_why": "",
             "exposure": "이 경로에 노출된 집합 - v_cohort 컬럼 위의 SQL 불리언 식",
             "reference": "비교할 참조집합 - **종목 속성 컬럼만**"}],
  "latents": [{"uid": "U_<이름>", "between": ["<노드 a>", "<노드 b>"],
               "says": "이 미관측 공통원인이 무엇인가",
               "blocked_by": ["이걸로 조건화하면 막힌다고 보는 관측 노드 id"]}],
  "completeness": "어느 변수쌍들을 훑었고 왜 나머지 공통원인이 없다고 보나"}}

`completeness` 를 비우면 거부한다 - 완비 선언 없는 그래프는 인과 그래프가 아니다.
`blocked_by` 에는 네가 그린 노드 id 만 써라. 배정이 `chosen`·`scheduled` 인 처치에는 코드가
U 를 자동으로 심는다 - 네가 안 적어도 들어가고 지울 수 없으니, 그 자리 말고 **네가 아는
다른 공통원인**에 지면을 써라. 위 골격은 모양이지 내용이 아니다 - 예시는 주지 않는다.

## kind 가 그 간선의 운명을 정한다
`statistical` 만 검정 대상이다. 코호트를 짜고 표본을 만들고 위약분포를 붙이는 것은 이
간선들뿐이고, **비워 두면 그 간선은 검정 없이 지나간다** - 어떤 수치도 붙지 않고 예산에도
들어가지 않는다. 실제로 그렇게 돌아 검정 0건으로 끝난 런이 있다.

  statistical  코호트로 재야 아는 것 (사건 → 초과수익, 수급 → 가격)
  identity     정의상 참인 계산 (구성종목 기여의 합 = ETF 기여). 검정 대상이 아니다
  elasticity   외부 출처의 계수를 빌려 쓰는 것 (금리 1%p → 밸류에이션 X%)

`identity` 를 `statistical` 로 적으면 계산을 검정하게 되고, 반대로 적으면 추정치가 정의처럼
보고된다. 애매하면 `statistical` 이다 - 재 보고 아닌 것이 낫다.

## exposure · reference 는 문장이 아니라 술어다 - 그리고 **서로 다른 표면이다**
이 둘은 **그대로 SQL 의 WHERE 에 들어간다.** "삼성전자 실적 발표를 접한 투자자" 같은
산문을 쓰면 실행되지 않고, 그러면 그 간선의 음성대조가 통째로 사라진다.

  exposure   `v_cohort` 의 컬럼 - `instrument_id` · `trade_date` · `source_event_id` ·
             `event_type_code` · `predicate_code` · `role_code` · `lifecycle_stage` ·
             `sector_name` · `industry_name` · `market_cap` · `listing_market` · `ticker`
  reference  **종목 속성만** - `instrument_id` · `sector_name` · `industry_name` ·
             `market_cap` · `listing_market` · `ticker`

참조집합에 `trade_date` 나 사건 컬럼을 쓰면 거부된다. 비교군은 "어떤 종목들인가"이고
**날짜는 코드가 창으로 붙인다** - 네가 날짜를 박으면 그 창이 하루로 접혀 산포를 잴
표본이 사라진다. 실제로 그 술어로 E-value 분모가 여섯 간선 모두 미산출로 나갔다
(2026-08-01 nanfix-20260801-01).

`ticker` 는 **종목 코드**다(`'005930'`). 거기에 종목명을 넣으면 문법은 맞고 결과는 0행이라
"검사했는데 아무것도 없었다"로 조용히 기록된다 - 가장 나쁜 실패다. 이름으로 걸러야 하면
`instrument_id` 를 써라. 어느 컬럼으로도 못 적겠으면 **빈 문자열로 두어라** - 그러면
코드가 접지된 `source_event_id` 로 처치를, 처치 종목의 산업 동종군으로 참조집합을 만든다."""


def _pair(a: str, b: str) -> tuple[str, str]:
    """공통원인은 방향이 없다. `A<->B` 와 `B<->A` 를 같은 자리로 센다."""
    return (a, b) if a <= b else (b, a)


def compile_latents(hypotheses: list[Hypothesis], declared: list[Latent]) -> list[Latent]:
    """배정 기제에서 U 를 **심는다. 모델이 지울 수 없다.**

    `chosen`·`scheduled` 는 처치가 미관측 상태에 반응해 정해졌다는 선언이고, 그러면
    처치와 결과에 공통원인이 있다는 뜻이 그 선언 안에 이미 들어 있다. 그걸 모델의 성실성에
    맡기면 기본값이 무교란이 된다 - 이전 판이 정확히 그랬다.

    **compiled 가 declared 를 이긴다.** 같은 자리에 모델이 약한 문구("경미한 정보 비대칭")를
    써서 덮으면 P5 가 소거 설계를 약하게 잡고 P8 의 상한이 헐거워진다. 같은 자리에
    `scheduled` 와 `chosen` 이 겹치면 `chosen` 이 남는다 - 시점만 외생인 것과 내용까지 고른
    것은 뒷문의 크기가 다르다.
    """
    out: dict[tuple[str, str], Latent] = {_pair(*u.between): u for u in declared}
    for h in sorted(hypotheses, key=lambda x: x.assignment == "chosen"):
        says = COMPILED_LATENT.get(h.assignment)
        if not says:                      # mechanical·natural 은 배정이 기제 밖에 있다
            continue
        out[_pair(h.treatment, h.outcome)] = Latent(
            uid=f"U_{h.treatment}", between=(h.treatment, h.outcome),
            says=says, source="compiled")
    return list(out.values())


def derive_relations(hypotheses: list[Hypothesis]) -> list[Relation]:
    """가설 쌍마다 관계를 유도한다 (Zaks 2017 RAR).

    **왜 여기냐**: 관계는 그래프가 합쳐진 뒤에야 전수로 볼 수 있고, 예산 회계(P8)보다
    먼저 있어야 한다. Zaks 의 요점이 "관계를 먼저 판정하라"인 것은 관계가 *물을 수 있는
    질문 자체*를 바꾸기 때문이다 - relative causal force(= 우리 share)는 coincident 에서만
    정의된다.

    **왜 LLM 이 아니라 코드냐**: 2017판 정의가 증거론적이라 `predicts`/`denies` 대수로
    번역된다. 모델에게 물으면 관계 판정이 원하는 결론으로 가는 손잡이가 된다 - P8 이
    합산할지 말지를 정하는 값이므로 특히 그렇다. `because` 에 실제 집합을 적어 남긴다.

    `causal`(H1 -> H2 -> Y)만은 예측집합으로 안 나온다. 그래프에서 읽는다 - 한 가설의
    처치가 다른 가설의 처치로 가는 방향 경로 위에 있으면 경합이 아니라 직렬이다.
    """
    out: list[Relation] = []
    for i, a in enumerate(hypotheses):
        for b in hypotheses[i + 1:]:
            kind = classify(a, b)
            pa, pb = set(a.predicts), set(b.predicts)
            da, db = set(a.denies), set(b.denies)
            if kind == "mutually_exclusive":
                clash = sorted((pa & db) | (pb & da))
                because = f"한쪽 예측을 다른 쪽이 부정한다: {clash[:2]}"
            elif kind == "inclusive":
                because = "한쪽 예측집합이 다른 쪽을 진부분집합으로 포함한다"
            elif kind == "congruent":
                because = f"예측이 겹치고 갈리지 않는다: {sorted(pa & pb)[:2]}"
            elif kind == "coincident":
                because = "예측이 겹치지도 갈리지도 않는다 - 한쪽 확인이 다른 쪽 검정이 아니다"
            else:
                because = "예측집합이 같거나 비어 판정할 수 없다"
            direction = None
            if kind == "inclusive":
                direction = a.hid if pa > pb else b.hid
            out.append(Relation(a=a.hid, b=b.hid, kind=kind, because=because,
                                direction=direction))
    return out


def validate(g: WorldGraph, *, grounded: set[str]) -> list[str]:
    """어긴 것을 전부 돌려준다 - 하나 잡고 멈추지 않는다.

    되먹임이 목적이므로 첫 위반에서 끊으면 왕복이 위반 수만큼 늘어난다. 여섯 규칙 전부
    기계 검사다 - 산문 판정은 없다.

    `graph.validate` 에서 노드 종별·GAP_ONSET·구조 개수·timing 열거를 안 가져왔다. 종별은
    이미 폐기됐고, 구조 개수는 P2 가 가설 수로 강제하고, timing 은 `Hypothesis.assignment`
    가 대체한다. 가져온 것은 `parse`(시간 색인)·`LIMITS`(폭주 방어)·`descendants` 뿐이다.
    """
    bad: list[str] = []

    # 1 · 시간 색인. 색인 없는 노드는 시간 선행도 비순환도 검사할 수 없다.
    off: dict[str, int] = {}
    for n, m in g.nodes.items():
        if not isinstance(m, dict):
            bad.append(f"{n}: 노드 명세가 dict 가 아니다 - {type(m).__name__}")
        try:
            _, off[n] = parse(n)
        except ValueError as e:
            bad.append(str(e))

    # 2 · 시간 선행. **동시간 간선은 이제 선언을 요구한다.**
    #
    # 이전 규칙은 `off(a) > off(b)` 만 봤다. 그래서 전부 `@t+0` 인 사슬이 무사통과했고
    # (실측 확인됨) 그 그래프는 시간 정보를 0비트 담고도 인과 방향을 주장했다. 동시간
    # 간선 자체를 금지하지 않는 이유는 하루 그레인에서 실제로 같은 날 안에 전달되는 경로가
    # 있기 때문이다 - 금지 대신 **왜 방향을 줄 수 있는지 적게** 한다.
    dir_e: list[tuple[str, str]] = []
    for e in g.edges:
        if not isinstance(e, dict):
            bad.append(f"간선 명세가 dict 가 아니다 - {type(e).__name__}")
            continue
        a, b = str(e.get("from") or ""), str(e.get("to") or "")
        tag = f"{a}→{b}"
        if a not in g.nodes or b not in g.nodes:
            bad.append(f"{tag}: nodes 에 선언 안 됨")
            continue
        # `kind` 없는 간선은 **조용히 검정을 빠져나간다**(`run._designs` 는 statistical 만
        # 고른다). 그러면 그래프가 멀쩡한데 검정 0건·예산 0.0 으로 끝나고, 산출물에는
        # "재 봤는데 아무것도 안 나왔다"와 구분되지 않는 모양이 남는다(2026-08-01 실측).
        if str(e.get("kind") or "") not in chain.KINDS:
            bad.append(f"{tag}: kind 가 없거나 어휘 밖이다({e.get('kind')!r}). "
                       f"{list(chain.KINDS)} 중 하나를 적어라 - statistical 만 검정 대상이라 "
                       "비우면 이 간선은 검정 없이 지나간다")
        if a not in off or b not in off:
            continue                      # 색인 위반은 규칙 1 이 이미 보고했다
        dir_e.append((a, b))
        if off[a] > off[b]:
            bad.append(f"{tag}: 시간 역행 (t{off[a]:+d} → t{off[b]:+d}). 원인이 결과보다 늦다")
        elif off[a] == off[b] and not (
                e.get("simultaneous") and str(e.get("simultaneous_why") or "").strip()):
            bad.append(f"{tag}: 동시간 간선(t{off[a]:+d})인데 simultaneous 선언이 없다 - "
                       "같은 시각의 두 변수에 방향을 주려면 simultaneous=true 와 "
                       "simultaneous_why 로 근거를 적어라")

    # 동시간을 연 대가. 시차가 엄격히 늘던 시절에는 비순환이 공짜였다 - 이제 아니다.
    # 순환이 섞이면 d-분리가 조용히 틀린 조정집합을 낸다(예외 없이, 값만 틀린다).
    for a, b in dir_e:
        if off[a] == off[b] and a in descendants(dir_e, {b}):
            bad.append(f"{a}→{b}: 순환 - {b} 에서 {a} 로 돌아오는 경로가 있다")

    # 3 · 접지. `events` 를 적은 노드는 실재하는 사건을 가리켜야 한다.
    # `grounded` 가 비었으면 접지 요구를 걸지 않는다 - 사건이 0개인 셀에 걸면 모델이 고칠
    # 수 없는 위반이 되고 되먹임 3회를 헛돈다.
    n_grounded = 0
    for n, m in g.nodes.items():
        ev = (m.get("events") or []) if isinstance(m, dict) else []
        if not ev:
            continue
        n_grounded += 1
        for x in ev:
            if grounded and x not in grounded:
                bad.append(f"{n}: events {x!r} 접지 실패 - 실재하지 않는 event_id")
    if grounded and not n_grounded:
        bad.append("사건에 접지된 노드가 0개다 - 브리프의 event_id 를 참조하는 노드가 하나는 "
                   "있어야 이 설명이 저장소와 이어진다")

    # 4 · 결론 노드 유일성. 예산은 잔차 하나에 대해 정의되므로 결론이 갈라지면 귀속의 합을
    # 무엇과 비교할지가 없어진다. 그리지 않은 처치·결론도 여기서 잡는다 - 노드가 없으면
    # 그 가설은 식별도 처분도 못 받고 조용히 사라진다(처분 폐쇄 위반).
    outs = sorted({h.outcome for h in g.hypotheses})
    if len(outs) > 1:
        bad.append(f"결론 노드가 {len(outs)}개다 ({', '.join(outs)}) - 예산이 정의되려면 "
                   "모든 가설의 outcome 이 같아야 한다")
    for h in g.hypotheses:
        for role, nid in (("treatment", h.treatment), ("outcome", h.outcome)):
            if nid not in g.nodes:
                bad.append(f"{h.hid}: {role} {nid!r} 가 nodes 에 없다 - 안 그린 가설은 "
                           "식별도 처분도 못 받는다")

    # 5 · 공통원인 완비. `compile_latents` 가 심으므로 정상 경로에서는 안 걸린다 -
    # 컴파일을 우회해 WorldGraph 를 직접 만든 경로를 여기서 잡는다.
    pairs = {_pair(*u.between) for u in g.latents}
    for h in g.hypotheses:
        if h.assignment in COMPILED_LATENT and _pair(h.treatment, h.outcome) not in pairs:
            bad.append(f"{h.hid}: 배정이 {h.assignment} 인데 {h.treatment}↔{h.outcome} 의 "
                       f"미관측 공통원인이 latents 에 없다 - {COMPILED_LATENT[h.assignment]}")
    # `blocked_by` 는 P4 가 m-분리로 실제로 막히는지 검증한다. 그리지 않은 이름을 적으면
    # 검증이 불가능하므로 여기서 되돌린다 - 조용히 버리면 소거 제안이 증발한다.
    for u in g.latents:
        for x in u.blocked_by:
            if x not in g.nodes:
                bad.append(f"{u.uid}: blocked_by {x!r} 가 nodes 에 없다 - 그리든가 빼든가")

    # 6 · 상한. 폭주 방어만이 이유다 - 사슬 길이를 자르려는 규칙이 아니다.
    if len(g.edges) > LIMITS["edges"]:
        bad.append(f"간선 {len(g.edges)} > {LIMITS['edges']}")
    for n in {a for a, _ in dir_e}:
        fan = sum(1 for a, _ in dir_e if a == n)
        if fan > LIMITS["fanout"]:
            bad.append(f"{n}: fanout {fan} > {LIMITS['fanout']}")
    return bad


def build(client, sql, *, question: Question, hypotheses: list[Hypothesis],
          grounded: set[str]) -> WorldGraph:
    """가설 합집합 위에 그래프를 세우고 **완비 의무를 건다.**

    3회 안에 못 세우면 위반을 단 채로 돌려준다 - **억지로 밀지 않는다.** 위반이 붙은
    그래프는 P4 가 식별을 못 하고 P8 이 `undetermined` 로 처분한다. 위반을 지우고 통과시킨
    그래프가 내는 `adjust=[]` 보다 그쪽이 정직하다.

    조회는 시도 횟수를 쓰지 않는다. 묻는 데 벌점을 주면 모델은 묻는 대신 지어낸다.
    상한(6회)을 다 쓴 뒤에도 계속 물으면 그때는 시도를 쓴다 - 무한 루프 방어다.
    """
    brief = _brief(question, hypotheses, grounded, sql)
    trace: list[tuple[str, str]] = []
    last: WorldGraph | None = None
    # 조회 표면이 없으면 상한이 0이다 - 물어도 답이 없으니 첫 요청부터 시도를 쓴다.
    cap = MAX_LOOKUPS if sql is not None else 0
    lookups = 0
    tries = 0

    while tries < MAX_TRIES:
        try:
            out = client.complete_json(SYSTEM, _user(brief, trace, tries + 1))
        except PipelineError as exc:
            # 한 번의 파싱 실패로 셀 전체를 죽이지 않는다 - 되먹임으로 돌린다.
            tries += 1
            trace.append(("(응답)", f"**거부** - 응답을 JSON 으로 못 읽었다: {exc}"))
            continue

        q = out.get("sql")
        if isinstance(q, str) and q.strip():
            if lookups < cap:
                lookups += 1
                trace.append((q, sql.ask(q)))
                continue
            tries += 1
            trace.append((q, "조회 표면이 없다 - 원장을 못 본다. 아는 것으로 그려라."
                          if sql is None else
                          f"조회 상한 {MAX_LOOKUPS}회를 다 썼다. 이제 graph 를 내라."))
            continue

        tries += 1
        g, why = _parse(out, hypotheses, sql)
        if g is None:
            trace.append(("(그래프)", f"**거부** - {why}"))
            continue
        bad = validate(g, grounded=grounded)
        if not bad:
            log("causal.p3.done", nodes=len(g.nodes), edges=len(g.edges),
                latents=len(g.latents), lookups=lookups, tries=tries)
            return g
        last = replace(g, violations=bad)
        trace.append(("(그래프)", "**거부** - 위반:\n  " + "\n  ".join(bad)))

    if last is None:
        last = WorldGraph(hypotheses=list(hypotheses),
                          latents=compile_latents(hypotheses, []),
                          relations=derive_relations(hypotheses),
                          violations=[f"{MAX_TRIES}회 시도에서 그래프가 안 나왔다: "
                                      + (trace[-1][1][:300] if trace else "응답 없음")],
                          queries=list(sql.ledger.queries) if sql is not None else [])
    log("causal.p3.unresolved", violations=len(last.violations), lookups=lookups)
    return last


def _parse(out: dict[str, Any], hypotheses: list[Hypothesis],
           sql) -> tuple[WorldGraph | None, str]:
    """모델 산출 -> WorldGraph. **거부 사유를 문자열로 돌려준다 - 예외로 죽이지 않는다.**

    `observed` 를 항상 채워서 내보낸다(못 재면 None). P4 는 조정집합 후보를 관측 노드에서만
    고르는데, 키가 없는 것과 못 재는 것이 같은 표현이면 미관측 노드로 조건화하는 계획이
    나온다 - 이 파일이 없애려는 부재의 이중성이 노드 명세에서 되살아나는 자리다.
    """
    payload = out.get("graph")
    if not isinstance(payload, dict):
        return None, 'graph 객체가 없다. {"sql": ...} 로 묻거나 {"graph": {...}} 를 내라.'
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return None, "graph.nodes 가 비었거나 객체가 아니다."
    edges = [e for e in (payload.get("edges") or []) if isinstance(e, dict)]
    if hypotheses and not edges:
        return None, "graph.edges 가 비었다 - 가설이 처치→결론을 세웠는데 간선이 하나도 없다."

    comp = str(payload.get("completeness") or "").strip()
    if not comp:
        return None, ("completeness 가 비었다. 완비 선언 없는 그래프는 인과 그래프가 아니다 - "
                      "어느 변수쌍들을 훑었고 왜 나머지 공통원인이 없다고 보는지 적어라.")

    declared: list[Latent] = []
    for i, u in enumerate(payload.get("latents") or []):
        if not isinstance(u, dict):
            continue
        pair = list(u.get("between") or [])
        if len(pair) != 2:
            return None, f"latents[{i}] 의 between 이 두 노드가 아니다 - [a, b] 로 적어라."
        declared.append(Latent(uid=str(u.get("uid") or f"U#{i}"),
                               between=(str(pair[0]), str(pair[1])),
                               says=str(u.get("says") or ""), source="declared",
                               blocked_by=[str(x) for x in (u.get("blocked_by") or [])]))

    clean: dict[str, dict[str, Any]] = {}
    for k, m in nodes.items():
        if not isinstance(m, dict):
            clean[str(k)] = m             # 규칙 1 이 보고하도록 그대로 둔다
            continue
        obs = m.get("observed")
        clean[str(k)] = {**m, "says": str(m.get("says") or ""),
                         "observed": obs if isinstance(obs, str) and obs.strip() else None,
                         "events": [str(x) for x in (m.get("events") or [])]}
    # 결과 노드는 P2 와 같은 자리에 같은 정의로 있어야 한다 - 합치는 단계에서 빠뜨리면
    # 모든 가설의 outcome 이 nodes 에 없다는 위반으로 되돌아온다.
    if hypotheses and OUTCOME_ID not in clean:
        clean[OUTCOME_ID] = {**hypotheses[0].nodes.get(OUTCOME_ID, {}), "events": []}
    # 합집합은 **줄어들 수 없다.** 이 단계의 일은 합치고 공통원인을 얹는 것이지 가설의
    # 노드를 재타이핑하는 것이 아닌데, 실제로 h3 의 처치 노드가 통째로 빠져 위반 하나로
    # 검정 전체가 스킵됐다(2026-08-01 flash-20260801-01). 빠진 것은 그 가설의 정의
    # 그대로 되돌려 놓는다 - 안 그린 가설은 식별도 처분도 못 받기 때문이다.
    carried = 0
    for h in hypotheses:
        for nid, spec in (h.nodes or {}).items():
            if nid in clean or not isinstance(spec, dict):
                continue
            clean[nid] = {**spec, "says": str(spec.get("says") or ""),
                          "observed": spec.get("observed") or None,
                          "events": [str(x) for x in (spec.get("events") or [])],
                          "carried_over": h.hid}
            carried += 1
    if carried:
        log("causal.p3.carried_over", nodes=carried)
    # 접지 타입도 합집합의 일부다. 모델이 노드를 다시 타이핑하면 이 값이 떨어져 나가고,
    # 그러면 검정이 다시 사람 말에서 코호트를 짜려다 0행이 된다.
    for h in hypotheses:
        for nid, spec in (h.nodes or {}).items():
            code = isinstance(spec, dict) and spec.get("event_type_code")
            if code and isinstance(clean.get(nid), dict) \
                    and not clean[nid].get("event_type_code"):
                clean[nid] = {**clean[nid], "event_type_code": code}

    if sql is None:
        # 조회 없이 한 완비 선언이라는 사실은 선언 자체에 붙어야 한다 - 나중에 이 문장만
        # 읽는 P8·P9 가 근거의 두께를 오해하지 않도록.
        comp += "  (원장 조회 없이 선언 - 이 셀에 조회 표면이 없었다)"

    return WorldGraph(nodes=clean, edges=edges,
                      latents=compile_latents(hypotheses, declared),
                      relations=derive_relations(hypotheses),
                      hypotheses=list(hypotheses), completeness=comp,
                      queries=list(sql.ledger.queries) if sql is not None else []), ""


def _brief(question: Question, hypotheses: list[Hypothesis], grounded: set[str], sql) -> str:
    """합칠 재료. **가설의 노드·간선을 그대로 넘긴다 - 요약하면 합집합이 줄어든다.**"""
    L = ["## 설명 대상", question.explanandum,
         f"반사실: {question.intervention}",
         f"예산(잔차 절대값): {question.budget:.4f}", "",
         "## 가설 - 이것들을 하나의 그래프로 합쳐라"]
    for h in hypotheses:
        L += ["", f"### {h.hid} · 배정 {h.assignment} ({ASSIGNMENT_SAY.get(h.assignment, '')})",
              h.says, f"처치 {h.treatment} → 결론 {h.outcome}"]
        for n, m in (h.nodes or {}).items():
            m = m if isinstance(m, dict) else {}
            ev = m.get("events") or []
            L.append(f"  노드 {n}: {m.get('says', '')}" + (f" · events={ev}" if ev else ""))
        for e in h.edges or []:
            e = e if isinstance(e, dict) else {}
            L.append(f"  간선 {e.get('from')}→{e.get('to')}: {e.get('says', '')}")
    if grounded:
        L += ["", "## 접지 가능한 event_id", ", ".join(sorted(grounded))]
    if sql is not None:
        L += ["", "## 원장 (조회 가능)", sql.schema()]
    else:
        L += ["", "## 원장",
              "조회 표면이 없다 - 이번에는 원장을 못 본다. 아는 것으로 그리고, 원장을 봐야 "
              "정해지는 노드는 세우되 observed 를 null 로 둬라."]
    return "\n".join(L)


def _user(brief: str, trace: list[tuple[str, str]], turn: int) -> str:
    L = [brief]
    for sent, got in trace:
        L += ["", f">>> {sent[:900]}", got[:1600]]
    L += ["", f"[{turn}/{MAX_TRIES}차 시도]"]
    if turn >= MAX_TRIES:
        L.append("마지막이다. 위반을 고친 graph 를 내라 - 여기서 못 고치면 위반을 단 채로 "
                 "다음 단계로 간다.")
    return "\n".join(L)


__all__ = ["MAX_LOOKUPS", "MAX_TRIES", "SYSTEM", "build", "compile_latents", "validate"]


if __name__ == "__main__":
    from datetime import date

    H = Hypothesis(hid="H1", says="자사주 취득 결정이 당일 초과수익을 만든다",
                   treatment="BUYBACK@t+0", outcome="R@t+0", assignment="chosen")

    # 1 · chosen 하나면 U 하나. 모델이 약한 문구로 같은 자리를 덮어도 compiled 가 남는다.
    U = compile_latents([H], [])
    assert len(U) == 1 and U[0].source == "compiled" and U[0].uid == "U_BUYBACK@t+0", U
    WEAK = [Latent(uid="U_x", between=("R@t+0", "BUYBACK@t+0"), says="경미", source="declared")]
    assert [u.source for u in compile_latents([H], WEAK)] == ["compiled"]
    assert compile_latents([Hypothesis(hid="H2", says="", treatment="A@t-1", outcome="R@t+0",
                                       assignment="mechanical")], []) == []

    # 2 · 전부 동시간인데 선언이 없으면 위반. 선언하면 통과.
    NODES = {"BUYBACK@t+0": {"says": "취득 결정", "observed": None, "events": ["e1"]},
             "R@t+0": {"says": "당일 초과수익", "observed": "종가 초과수익", "events": []}}
    G = WorldGraph(nodes=NODES, edges=[{"from": "BUYBACK@t+0", "to": "R@t+0"}],
                   latents=U, hypotheses=[H], completeness="두 변수쌍을 훑었다")
    assert any("동시간" in b for b in validate(G, grounded={"e1"})), validate(G, grounded={"e1"})
    OK = replace(G, edges=[{"from": "BUYBACK@t+0", "to": "R@t+0", "simultaneous": True,
                            "simultaneous_why": "결정 공시와 체결이 같은 장중에 있다"}])
    assert validate(OK, grounded={"e1"}) == [], validate(OK, grounded={"e1"})
    assert any("공통원인" in b for b in validate(replace(OK, latents=[]), grounded={"e1"}))
    assert any("역행" in b for b in validate(
        replace(OK, nodes={**NODES, "BUYBACK@t+1": NODES["BUYBACK@t+0"]},
                edges=[{"from": "BUYBACK@t+1", "to": "R@t+0"}]), grounded={"e1"}))

    # 3 · completeness 가 비면 되묻는다. 두 번째 응답으로 통과.
    class _Client:
        def __init__(self) -> None:
            self.n = 0

        def complete_json(self, system: str, user: str) -> dict[str, Any]:
            self.n += 1
            return {"graph": {"nodes": NODES, "edges": list(OK.edges),
                              "completeness": "" if self.n == 1 else "처치·결과쌍만 있다"}}

    Q = Question(etf_instrument_id="091160", etf_name="테스트", trade_date=date(2026, 7, 16),
                 as_of="2026-07-16T15:40:00Z", observed=0.0421, residual=0.0421,
                 route_code="X", explanandum="r=+4.21%", intervention="공시가 없던 세계",
                 answer_form="구간")
    C = _Client()
    OUT = build(C, None, question=Q, hypotheses=[H], grounded={"e1"})
    assert C.n == 2 and not OUT.violations, (C.n, OUT.violations)
    assert "원장 조회 없이 선언" in OUT.completeness
    assert [u.source for u in OUT.latents] == ["compiled"]
    print("p3_graph 자체검사 통과")
