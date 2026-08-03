"""P2 · 가설 생성 — **어휘는 열고, 형식과 독립성만 닫는다.**

이전 구조는 한 세션이 DAG 하나를 그렸다. 그 그래프는 세계에 대한 진술이 아니라 **그림에
대한 진술**이었다 - 안 그린 간선과 없는 관계가 같은 표현(부재)으로 붙어서, 뒤따르는 식별이
"뒷문 없음"을 세계의 성질로 보고했다. 실측: 그래프에 `MOM@t-1` 한 줄을 더 그리면
`adjust=[]` 가 `adjust=['MOM@t-1']` 로 바뀐다. 조정집합이 세계가 아니라 제안자의 지식
상태를 따라 움직인 것이다.

그래서 여기서 좁히는 것은 표현이 아니다. 무엇을 노드로 세우든 자유고, 후보 사건 목록 밖의
원인을 세워도 된다. 골격도 후보도 주지 않는다. 대신 셋을 강제한다.

    형식   treatment·outcome·assignment·predicts·denies 가 없으면 가설이 아니다.
           특히 `denies` - 어떤 관측으로도 죽지 않는 문장은 P5 에서 갈리지 않는다
    출처   수치를 쓰면 `anchor` 와 `anchor_source` 가 붙는다. 출처 없는 값은 거부된다
    독립   세션을 n번 따로 돌린다. 한 세션에 n개를 시키면 첫 번째의 변주가 나온다

`assignment` 를 필수로 만든 것이 이 파일의 핵심이다. 이 값으로 P3 가 미관측 공통원인을
**모델이 지울 수 없게** 심는다(`contracts.COMPILED_LATENT`). 기업이 고르는 사건은 좋은
사적 정보와 함께 오므로(Bhattacharya 1979 · Miller-Rock 1985) 선택 편의는 예외가 아니라
기본값이고, 그 기본값을 켜는 스위치가 여기 있다. 어휘 밖 값은 그 자리에서 거부한다.

거부는 전부 되물음이다. 한 번에 죽이지 않는 이유: 형식 위반은 대개 가설이 틀린 게 아니라
양식을 덜 채운 것이고, 그걸로 세션을 끝내면 남는 것은 침묵이다 - 우리가 없애려는 바로 그
산출물이다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import PipelineError
from ..observability import log
from . import p0_question as P0
from .contracts import (
    ASSIGNMENT_SAY,
    DOMAIN_SAY,
    OUTCOME_ID,
    ROLE_SAY,
    Fingerprint,
    Hypothesis,
    Question,
)
from .graph import parse as _parse_nid

MAX_TRIES = 3        # 가설 제출 시도. 조회는 여기 들지 않는다
MAX_LOOKUPS = 6      # 세션당 조회 상한


def outcome_node(question: Question) -> dict[str, str]:
    """고정 결과 노드의 명세. **정의도 코드가 준다** - 같은 id 를 세션마다 다르게 설명하면
    합칠 때 같은 자리에 다른 것이 들어온다."""
    return {"says": question.explanandum,
            "observed": "v_daily.ar (설명 대상의 횡단면 평균 대비 초과수익)"}

# 단위가 붙은 수만 본다. 연도·건수·`@t-2` 까지 잡으면 정상 서술이 죽고, 되물음이 형식
# 시비로 3회를 태운다. 놓치는 쪽으로 기운 검사이며 실제 방어는 프롬프트가 한다.
_MAGNITUDE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%p|%|배|bp|bps|원|억원|억|조원|조|건)")
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")

_ASSIGN = "\n".join(f"  {k:<11}{v}" for k, v in ASSIGNMENT_SAY.items())
_ROLES = "\n".join(f"  {k:<14}{v}" for k, v in ROLE_SAY.items())
_DOMAINS = "\n".join(f"  {k:<15}{v}" for k, v in DOMAIN_SAY.items())

SYSTEM = f"""너는 **오늘 이 셀의 잔차를 설명할 작업가설 하나**를 세운다.

같은 질문에 여러 세션이 따로 붙는다. 너는 다른 세션의 가설을 보지 못하고 그쪽도 네 것을
보지 못한다. 하나를 잘 세워라 - 여러 개를 나열하지 마라. 애착을 분산시키는 일은 코드가
한다(Chamberlin 1890 multiple working hypotheses · Platt 1964 strong inference).

## 무엇이든 세울 수 있다
노드도 메커니즘도 골격도 주지 않는다. 실물 지표·회계 항목·기대·수급·유동성·경쟁 반응·
지수 규칙 - 무엇이든 노드가 된다. 후보 사건 목록 안에서 원인을 고를 의무도 없다.
쪼갤수록 검정 지점이 늘어 주장이 강해지고, 뭉갤수록 검정 불가에 가까워진다.

노드 id 는 `이름@t±N` 이다. 시간 색인이 없으면 거부된다 (`배당공시@t0` · `수급@t+1` ·
`선반영@t-2`). 간선은 원인에서 결과로 간다.

**결과 노드는 `{OUTCOME_ID}` 로 고정이다.** 이름을 새로 짓지 마라 - 세션마다 다른
이름을 쓰면 합칠 때 결론이 여러 개가 되고 그러면 예산이 정의되지 않아 네 가설의 값이
통째로 버려진다. 정의는 코드가 넣으니 `nodes` 에 적을 필요도 없다. 네 일은 `treatment`
에서 그 id 까지 **방향 경로를 간선으로 잇는 것**이다 - 중간이 비면 매개 노드를 세워라.
개별 종목의 움직임을 원인으로 쓰려면 그 종목 노드에서 `{OUTCOME_ID}` 로 가는 간선을
직접 그리지 말고, **왜 그 종목이 움직였는지**를 먼저 그려라.

## 역할
원인 하나를 고르는 일이 아니다. 사건을 만든 **인과 패키지를 재구성**하고 각 요소가 어떤
역할이었는지 밝히는 일이다. "대규모 매도가 원인이다"는 불충분하다 - 그것은 촉발원이고,
유동성 고갈은 증폭이며, 거래정지는 종료다.
{_ROLES}

역할은 기여도와 **다른 축**이다. 몫이 작아도 촉발원일 수 있고, 몫이 커도 증폭일 수 있다.
**이 값 때문에 가설이 거부되는 일은 없다** - 못 적으면 코드가 그래프 모양에서 유추하고
유추했다고 원장에 적는다. 다만 네가 아는 것을 코드가 추측하는 것보다 낫다.

## 메커니즘 영역
어디서 찾았나.
{_DOMAINS}

**후보 사건 목록만 보면 첫 번째 영역으로 쏠린다.** 그 목록은 공시·뉴스에서 왔으므로
거기에만 원인이 있을 이유가 없다. 수급(`v_flow` - 투자자 13종 순매수가 원장에 있다)·
유동성(`v_liquidity`)·제도(상하한가·공매도 규제 구간)·측정 오류·그리고 **무사건**까지
열려 있다. 이 셀에서 그 영역이 안 열린다고 보면 그렇게 판단한 이유를 `says` 에 적어라.
이것도 거부 사유가 아니다.

## 배정 기제 (필수)
이 처치가 어떻게 배정됐나. 넷 중 하나를 그대로 써라.
{_ASSIGN}

거짓 신고를 하지 마라. 이 값으로 코드가 미관측 공통원인 U 를 심고, 그 U 는 다음 단계에서
소거되거나 "미소거"로 기록된다. **U 는 감점이 아니라 작업 항목이다** - `chosen` 을 피해서
얻는 것은 없고, 틀린 신고는 소거했어야 할 U 를 통째로 숨긴다.

## predicts / denies (각 최소 1개)
  predicts  이 가설이 참이면 **관측되어야** 하는 것
  denies    이 가설이 참이면 **관측되지 않아야** 하는 것
denies 가 이 가설의 목숨이다. 어떤 관측으로도 죽지 않는 문장은 가설이 아니다. 둘 다
관측 가능한 것으로 써라 - 원장에서 조회하거나 계산할 수 있는 양이어야 한다. 다음 단계는
두 세계가 **갈리는 자리**만 검정하므로, 여기 적힌 것이 없으면 네 가설은 검정되지 않는다.

## 수치
써라. 단 **`anchor` 에 구간을, `anchor_source` 에 출처를 적어라.** 어느 공시·어느 재무
항목·어느 선행 사건·브리프 어느 줄인가. 출처 없는 수치는 거부된다. 모르면 넓은 구간을
써라 - 폭은 감점이 아니라 무지의 정직한 크기다.

## 지문
지문은 관측 자신이 이미 말한 것이고 네 가설보다 먼저 있었다. 지문이 죽인 부류를 다시
세우면 거부된다. 그 지문이 이 가설을 죽이지 않는다고 보면 **축 이름을 적고 왜 아닌지**
`says` 에 써라 - 반박은 되지만 침묵은 안 된다.

## 조회
모르는 것은 물어라. `{{"sql": "SELECT ..."}}` 를 내면 결과를 붙여 다시 묻는다.
**조회는 시도 횟수를 쓰지 않는다** - 모르는 것을 묻는 것과 가설을 틀리는 것은 다른 일이다.
세션당 {MAX_LOOKUPS}회까지. 시점 클램프는 뷰 안에 있으므로 미래는 애초에 보이지 않는다.

## 설명되지 않는다도 가설이다
어느 후보로도 설명되지 않는다고 보면 그것을 가설로 내라 - 미확인 충격을 노드로 세우고
그것이 무엇을 예측하고 무엇을 부정하는지 적으면 된다(예: 어느 후보 사건창에도 붙지 않는
잔차, 같은 산업 미노출 종목의 동반 이동). 억지 사슬보다 낫다. 정말 아무것도 세울 수
없을 때만 none 을 내라 - 그때도 무엇이 없어서인지 적어라.

## 앞선 세션과 갈려야 한다
앞선 세션의 `predicts` 를 아래에 준다(서사·노드는 주지 않는다 - 베낄 재료는 막고, 갈릴
재료만 준다). 네 가설이 그것들과 **어떤 관측에서 갈리는지** `distinguishes` 에 적어라.

"둘 다 SK하이닉스가 가장 많이 빠진다고 예측한다" 면 그 둘은 두 가설이 아니라 한 가설이다.
어떤 관측으로도 갈리지 않는 가설을 추가하면 목록만 길어지고 판별은 불가능해진다
(Platt 1964: 대립 없는 다중가설은 강한 추론이 아니다). 못 갈리겠으면 **다른 메커니즘
영역으로 옮겨라** - 정보·공통충격·수급·미시구조·피드백·제도·측정·무사건 중 앞선 세션이
열지 않은 곳이 있다.

JSON 하나만. 셋 중 하나다. 밖에 설명 문장을 붙이지 마라.
  {{"sql": "SELECT ..."}}                조회하고 다시 묻는다
  {{"hypothesis": {{...}}}}                아래 모양
  {{"none": "왜 아무 가설도 못 세우는가"}}

{{"hypothesis": {{
  "says": "이 가설이 주장하는 것 한 문장",
  "cause_label": "가치채널 또는 트리거 이름 - 앞선 세션과 겹치면 거부된다",
  "treatment": "원인 노드 id",
  "assignment": "mechanical|scheduled|natural|chosen",
  "role": "background|trigger|transmission|amplifier|terminator",
  "domain": "information|common_shock|flow|microstructure|feedback|institution|measurement|no_event",
  "nodes": {{"<id>": {{"says": "무엇이며 무엇을 어떤 단위로 재는가",
                      "observed": "어떻게 관측하나 (못 재면 null)"}}}},
  "edges": [{{"from": "<id>", "to": "<id>", "says": "이 간선이 주장하는 것",
             "because": "왜 이 경로로 전달되나"}}],
  "predicts": ["..."], "denies": ["..."],
  "distinguishes": ["앞선 세션의 예측과 **갈리는** 관측. 앞선 예측이 있을 때만 필수"],
  "events": ["후보 목록의 event_id. 접지되는 사건이 없으면 빈 목록"],
  "anchor": [lo, hi], "anchor_source": "수치를 썼다면 그 값이 어디서 왔는가"}}}}"""


def propose(client, sql, *, question: Question, fingerprint: Fingerprint,
            candidates: list[dict], n: int = 3) -> list[Hypothesis]:
    """작업가설 n개. **세션을 n번 따로 돌린다 - 한 세션에 n개를 시키지 않는다.**

    같은 세션에서 3개를 내라고 하면 첫 번째의 변주가 나온다. 두 번째부터는 이미 쓴 문장이
    문맥에 남아 있고 모델은 그것과 정합한 것을 쓴다 - 그건 하나의 가설을 세 번 쓴 것이지
    셋이 아니다. Chamberlin 이 말한 분산은 문맥을 끊어야 생긴다.

    완전한 독립은 아니다. n번째 세션에는 앞선 세션이 쓴 **채널 이름과 `predicts` 만** 준다
    (서사·노드·간선은 주지 않는다). 아무것도 안 주면 세 세션이 가장 눈에 띄는 채널 하나로
    수렴하고, 서사를 주면 그걸 읽고 정합을 맞춘다. 예측만 주는 것이 그 사이다 - **갈릴
    재료는 주고 베낄 재료는 막는다.** 이름만 주던 앞선 규약은 중복은 줄였지만 대립을
    만들지 못했다(2026-07-30 실측: 채널 이름은 셋 다 달랐는데 h2·h3 의 예측이 같아 어떤
    관측으로도 갈리지 않았다). `author` 에 세션 인덱스가 남아 이 절충은 사후 감사된다.

    한 세션이 빈손으로 끝나도 전체는 계속한다. **길이 0 도 유효한 반환이다** - 억지 가설로
    자리를 채우는 것보다 낫다. 그 경우 P8 은 후보를 `undetermined` 로 처분한다.
    """
    if n < 1:
        raise PipelineError(f"가설 세션 수는 1 이상이어야 한다: n={n}")
    events_ok = {str(c["event_id"]) for c in candidates if c.get("event_id")}
    out: list[Hypothesis] = []
    for idx in range(1, n + 1):
        h = _session(client, sql, question=question, fingerprint=fingerprint,
                     candidates=candidates, idx=idx, events_ok=events_ok, prior=out)
        if h is not None:
            out.append(h)
    log("causal.p2.done", asked=n, got=len(out),
        channels=[h.cause_label for h in out], authors=[h.author for h in out])
    return out


def _session(client, sql, *, question: Question, fingerprint: Fingerprint,
             candidates: list[dict], idx: int, events_ok: set[str],
             prior: list[Hypothesis]) -> Hypothesis | None:
    """세션 하나. 가설 하나를 받거나 빈손으로 끝난다.

    시도(가설 제출)와 조회를 따로 센다. 모르는 것을 묻는 데 시도를 물리면 모델은 추측으로
    채우는데, 그게 정확히 우리가 죽이려는 행동이다. 대신 상한이 둘 다 걸려 왕복은
    MAX_TRIES + MAX_LOOKUPS 를 넘지 않는다 - **가설도 아니고 성공한 조회도 아닌 턴은
    전부 시도를 쓴다**(조회 표면이 없는데 계속 묻는 경우가 여기 걸린다).
    """
    system = SYSTEM + ("\n\n## SQL 표면\n" + sql.schema() if sql is not None else
                       "\n\n## SQL 표면\n이 셀에는 조회 표면이 없다. sql 을 내면 시도가 하나 "
                       "깎인다 - 아는 것만으로 세우고, 확인이 필요한 것은 denies 에 적어라.")
    head = _head(question, fingerprint, candidates, idx, prior)
    trace: list[str] = []
    queries: list[str] = []
    tries = 0
    while tries < MAX_TRIES:
        out = client.complete_json(system, _user(head, trace, tries))
        if not isinstance(out, dict):
            tries += 1
            trace.append(f"거부: JSON 객체가 아니다 - {type(out).__name__}")
            continue

        q = str(out.get("sql") or "").strip()
        if q:
            if sql is None:
                tries += 1
                trace.append(f">>> {q}\n조회 불가 - 이 셀에는 SQL 표면이 없다.")
            elif len(queries) >= MAX_LOOKUPS:
                tries += 1
                trace.append(f">>> {q}\n조회 상한 {MAX_LOOKUPS}회를 다 썼다. 이제 가설을 내라.")
            else:
                queries.append(q)
                trace.append(f">>> {q}\n{sql.ask(q)}")
            continue

        if out.get("none"):
            log("causal.p2.none", session=idx, why=str(out["none"])[:120],
                queries=len(queries))
            return None

        if "hypothesis" not in out:
            tries += 1
            trace.append(f"거부: sql·hypothesis·none 중 하나여야 한다 - 받은 키 {sorted(out)[:6]}")
            continue

        tries += 1
        try:
            h = _hypothesis(out["hypothesis"], hid=f"h{idx}", author=f"session{idx}",
                            queries=list(queries), fingerprint=fingerprint,
                            events_ok=events_ok, prior=prior,
                            outcome_spec=outcome_node(question))
        except PipelineError as exc:
            log("causal.p2.reject", session=idx, tries=tries, why=str(exc)[:120])
            trace.append(f"거부: {exc}")
            continue
        log("causal.p2.hypothesis", session=idx, hid=h.hid, channel=h.cause_label,
            assignment=h.assignment, tries=tries, queries=len(queries),
            distinguishes=len(h.distinguishes))
        return h

    log("causal.p2.empty", session=idx, tries=tries, queries=len(queries))
    return None


def _head(question: Question, fingerprint: Fingerprint, candidates: list[dict],
          idx: int, prior: list[Hypothesis]) -> str:
    """세션 머리. 질문 · 지문 · 후보 · 앞선 세션의 채널과 예측 순."""
    L = [P0.brief(question), "", fingerprint.brief(), "", _candidates(candidates),
         "", f"[세션 {idx}]"]
    if prior:
        L.append("앞선 세션이 이미 낸 채널과 그 예측 - **같은 채널 금지, 같은 예측이면 "
                 "같은 가설이다**:")
        for h in prior:
            L.append(f"  [{h.cause_label}]")
            L += [f"    - {p}" for p in h.predicts]
        L.append("네 가설이 위 예측들과 갈리는 관측을 `distinguishes` 에 적어라.")
    return "\n".join(L)


def _user(head: str, trace: list[str], tries: int) -> str:
    L = [head]
    if trace:
        L += ["", *trace]
    L += ["", f"[가설 시도 {tries + 1}/{MAX_TRIES}]"]
    if tries + 1 == MAX_TRIES:
        L.append("마지막이다. 지금 못 내면 이 세션은 빈손으로 끝난다 - 억지 가설보다 none 이 낫다.")
    return "\n".join(L)


def _candidates(cs: list[dict]) -> str:
    """후보 사건. **사실만 싣는다 - 어디를 보라는 지시는 넣지 않는다.**

    `predicate_code` 와 `event_id` 를 원문 그대로 보여주는 이유: 안 보이면 모델이 발명한다
    (실측: 원장에 없는 `predicate_code = 'EARNINGS_MISS'` 를 내 대조군이 0건이 됐다).
    """
    if not cs:
        return "후보 사건 0건. 이 셀로 이어지는 사건이 원장에 없다 - 그래도 가설은 세울 수 있다."
    L = [f"후보 사건 {len(cs)}건 - **고를 의무는 없다. 여기 없는 원인을 세워도 된다.**"]
    for c in cs:
        pred = f" predicate_code={c['predicate_code']}" if c.get("predicate_code") else ""
        L.append(f"  [{c.get('event_type_code', '?')}{pred}] {c.get('label', '')} "
                 f"{c.get('event_date', '')} 대상 {c.get('ticker', '?')}")
        if c.get("event_id"):
            L.append(f"     event_id={c['event_id']}  available_at={c.get('available_at', '')}")
        for m in c.get("measures") or []:
            L.append(f"     측정: {m.get('role_code', '?')}={m.get('surface') or m.get('value')}"
                     f" {m.get('unit') or ''} 출처={m.get('value_source', '?')}")
        p = c.get("prior") or {}
        if p.get("n"):
            L.append(f"     타입 모집단: 사건 {p.get('events', 0)} · 종목 {p.get('instruments', 0)}"
                     f" · {p.get('first')}~{p.get('last')} · 상승 {p.get('up_ratio', 0) * 100:.0f}%"
                     f" · |초과수익| 중위 {p.get('abs_q50', 0) * 100:.1f}%"
                     f" p90 {p.get('abs_q90', 0) * 100:.1f}%")
        if c.get("share") is not None:
            L.append(f"     ETF 내 비중 {c['share'] * 100:.2f}%")
        if c.get("killed"):
            L.append(f"     [산술] 이미 기각됨 - {c['killed']}")
    return "\n".join(L)


def _hypothesis(raw: Any, *, hid: str, author: str, queries: list[str],
                fingerprint: Fingerprint, events_ok: set[str],
                prior: list[Hypothesis], outcome_spec: dict[str, str]) -> Hypothesis:
    """모델 산출 -> `Hypothesis`. **여기서 거부한 사유가 그대로 되먹임 문장이 된다.**

    검사는 전부 뒤에서 갈리는 조건이다: 어휘 밖 `assignment` 는 P3 의 U 삽입을 무력화하고,
    빈 `denies` 는 P5 에서 판별자를 못 만들며, 출처 없는 수치는 P6 이 대조할 것이 없다.
    형태·타입 검사는 프롬프트에 다시 나열하지 않는다 - 코드가 잡아 사유를 되돌린다.
    """
    if not isinstance(raw, dict):
        raise PipelineError(f"hypothesis 가 객체가 아니다 - {type(raw).__name__}")

    says = _text(raw.get("says"), "says", "이 가설이 주장하는 것 한 문장이 필요하다")
    label = _text(raw.get("cause_label"), "cause_label",
                  "채널 이름이 세션 간 중복 판정의 키다")
    used = [h.cause_label for h in prior]
    if _norm(label) in {_norm(u) for u in used}:
        raise PipelineError(f"cause_label={label!r} 는 앞선 세션이 이미 쓴 채널이다. 다른 "
                            f"가치채널이나 다른 트리거로 세워라 - 이미 쓴 것: {' · '.join(used)}")

    treatment = _node(raw.get("treatment"), "treatment")
    # 결과는 받지 않는다 - 코드가 정한다. 모델이 `outcome` 을 보내도 무시한다.
    outcome = OUTCOME_ID
    if treatment == outcome:
        raise PipelineError(f"treatment 가 결과 노드와 같다: {treatment!r}. 결과를 만든 "
                            "원인을 세워라")

    assignment = str(raw.get("assignment") or "").strip()
    if assignment not in ASSIGNMENT_SAY:
        raise PipelineError(f"assignment={assignment!r} 는 어휘 밖이다. {sorted(ASSIGNMENT_SAY)} "
                            "중 하나를 그대로 써라 - 이 값으로 코드가 U 를 심으므로 비우거나 "
                            "지어낼 수 없다")

    nodes = raw.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise PipelineError("nodes 가 비었다 - treatment 를 포함한 객체여야 한다")
    for nid, spec in nodes.items():
        _node(nid, "nodes 의 키")
        # 비객체 명세를 통과시키면 P3 의 접지 순회가 AttributeError 로 새어 되먹임 경로를
        # 우회한다 - 깨진 가설 하나가 유니버스 전체 런을 죽인다(ALPHA-633 과 같은 비대칭).
        if not isinstance(spec, dict):
            raise PipelineError(f"노드 {nid} 명세가 객체가 아니다 - {type(spec).__name__}. "
                                "says·observed 를 담은 객체여야 한다")
    if treatment not in nodes:
        raise PipelineError(f"treatment={treatment!r} 이 nodes 에 없다")
    # 결과 노드는 코드가 심는다(모델이 적었어도 덮어쓴다) - 정의가 세션마다 갈리면 같은
    # id 가 다른 것을 뜻하게 되고, 그건 이름이 갈리는 것과 같은 병이다.
    nodes = {**nodes, OUTCOME_ID: dict(outcome_spec)}

    edges = _edges(raw.get("edges"), nodes)
    if not _reaches(edges, treatment, outcome):
        raise PipelineError(f"{treatment} 에서 결과 노드 `{OUTCOME_ID}` 로 가는 방향 경로가 "
                            "없다. 결과 노드 이름은 고칠 수 없으니 매개 노드를 세워 그 id "
                            "까지 간선으로 이어라 - 닿지 않는 원인은 귀속이 아니다")

    predicts = _lines(raw.get("predicts"), "predicts",
                      "이 가설이 참이면 관측되어야 할 것을 최소 하나 적어라")
    denies = _lines(raw.get("denies"), "denies",
                    "이 가설이 참이면 관측되지 않아야 할 것을 최소 하나 적어라 - 어떤 "
                    "관측으로도 죽지 않는 문장은 가설이 아니다")
    # 앞선 가설이 있으면 **갈리는 관측**이 필수다. 없으면 그건 새 가설이 아니라 같은
    # 가설을 다른 이름으로 쓴 것이고, P5 가 판별자를 만들 자리도 없다. 3회 안에 못 대면
    # 이 세션은 빈손으로 끝난다 - 대립 없는 가설을 목록에 채우는 것보다 낫다.
    distinguishes = _lines(
        raw.get("distinguishes"), "distinguishes",
        "앞선 세션의 예측과 갈리는 관측을 최소 하나 적어라 - 같은 관측을 예측하는 두 "
        "문장은 두 가설이 아니다. 못 갈리겠으면 앞선 세션이 열지 않은 메커니즘 영역으로 "
        "옮겨라", need=1 if prior else 0)

    events = [str(e).strip() for e in _lines(raw.get("events"), "events", "", need=0)]
    bad = [e for e in events if e not in events_ok]
    if bad:
        raise PipelineError(f"events 에 원장에 없는 id 가 있다: {bad[:3]}. 후보 목록의 "
                            "event_id 를 그대로 쓰거나 빈 목록을 내라")

    anchor = _anchor(raw.get("anchor"))
    source = str(raw.get("anchor_source") or "").strip()
    if anchor and not source:
        raise PipelineError("anchor 를 썼으면 anchor_source 가 필요하다 - 그 구간이 어느 "
                            "공시·어느 항목·어느 선행 사건에서 왔는지 적어라")
    blob = json.dumps({k: v for k, v in raw.items() if k not in ("anchor", "anchor_source")},
                      ensure_ascii=False)
    hit = _MAGNITUDE.search(blob)
    if hit and not source:
        raise PipelineError(f"수치 {hit.group(0)!r} 를 썼는데 출처가 없다. anchor 에 구간을, "
                            "anchor_source 에 그 값이 어디서 왔는지 적어라 (브리프에서 인용한 "
                            "것이면 그렇게 적어라)")

    killed = _killed_by(fingerprint, blob, says)
    if killed:
        raise PipelineError(f"지문이 이미 죽인 부류다 - '{killed}'. 다른 가설을 세우거나, 그 "
                            "지문이 이 가설을 죽이지 않는 이유를 축 이름과 함께 says 에 적어라")

    # ★ 강제하지 않는다. 역할·영역은 **분류**이고 이 세션의 일은 **생성**이다.
    # 어휘 밖 값으로 거부하면 좋은 가설이 형식 시비로 3회 예산을 태운다 - 그리고 그건
    # "골격을 주면 모델이 노드를 만들지 않고 칸을 채운다"는 이 파이프라인의 전제를
    # 우리가 어기는 것이다. 못 읽으면 유추하고, 유추했다는 사실을 원장에 적는다.
    role, role_src = _role_of(raw, edges=edges, treatment=treatment, outcome=outcome)
    domain, dom_src = _domain_of(raw, says=says, label=label)

    return Hypothesis(hid=hid, says=says, treatment=treatment, outcome=outcome,
                      assignment=assignment, role=role, domain=domain,
                      role_source=role_src, domain_source=dom_src,
                      nodes=dict(nodes), edges=edges,
                      predicts=predicts, denies=denies, distinguishes=distinguishes,
                      events=events, anchor=anchor,
                      anchor_source=source, cause_label=label, author=author,
                      queries=queries)


# 모델이 실제로 쓰는 표현들. 영어 어휘를 강제하는 대신 받아 준다 - 한국어로 답하라고
# 해 놓고 영어 enum 을 요구하면 형식 시비가 생성을 잡아먹는다.
_ROLE_ALIAS = {
    "배경": "background", "배경조건": "background", "전제": "background",
    "조건": "background", "condition": "background", "precondition": "background",
    "촉발": "trigger", "촉발원": "trigger", "방아쇠": "trigger", "발단": "trigger",
    "cause": "trigger", "initiator": "trigger", "shock": "trigger",
    "전달": "transmission", "전달경로": "transmission", "전파": "transmission",
    "매개": "transmission", "channel": "transmission", "propagation": "transmission",
    "증폭": "amplifier", "증폭요인": "amplifier", "피드백": "amplifier",
    "amplification": "amplifier", "feedback": "amplifier",
    "종료": "terminator", "완화": "terminator", "종료요인": "terminator",
    "회복": "terminator", "damping": "terminator", "recovery": "terminator",
}
_DOMAIN_ALIAS = {
    "정보": "information", "기대": "information", "공시": "information",
    "뉴스": "information", "news": "information", "disclosure": "information",
    "공통충격": "common_shock", "시장": "common_shock", "섹터": "common_shock",
    "매크로": "common_shock", "macro": "common_shock", "market": "common_shock",
    "수급": "flow", "포지션": "flow", "자금": "flow", "flows": "flow",
    "positioning": "flow", "리밸런싱": "flow",
    "미시구조": "microstructure", "유동성": "microstructure", "호가": "microstructure",
    "liquidity": "microstructure", "micro": "microstructure",
    "제도": "institution", "규제": "institution", "규칙": "institution",
    "rule": "institution", "regulation": "institution",
    "측정": "measurement", "데이터": "measurement", "오류": "measurement",
    "data": "measurement", "error": "measurement",
    "무사건": "no_event", "우연": "no_event", "잡음": "no_event", "noise": "no_event",
}
# 영역 유추용 본문 단서. 신고가 없을 때만 본다.
# ★ 전부 한국어 2글자 이상이다. `VI`·`PBR` 같은 짧은 라틴 토큰은 넣지 마라 - 부분
# 일치라 노드 id·영문 서술 아무 데나 걸린다. 실측: `VI` 하나 때문에 배당 공시 가설이
# `institution` 으로 유추됐다. 그리고 **틀린 유추는 기본값보다 나쁘다** - 커버리지
# 원장이 "그 영역을 열었다"고 거짓말을 하게 되고, 그 원장이 침묵을 잡으라고 있는 것이다.
_DOMAIN_HINT = (
    ("flow", ("순매수", "순매도", "기관 매도", "기관 매수", "외국인", "연기금", "패시브",
              "리밸런", "자금유입", "자금유출", "환매", "설정액", "수급")),
    ("microstructure", ("유동성", "거래대금", "호가", "스프레드", "회전율", "체결강도")),
    ("feedback", ("추가 매도", "연쇄", "악순환", "되먹임", "손절", "반대매매")),
    ("institution", ("상한가", "하한가", "공매도", "거래정지", "지수 편입", "정기변경",
                     "서킷브레이커", "변동성완화")),
    ("measurement", ("기준가", "액면분할", "정정공시", "결측")),
    ("common_shock", ("환율", "금리", "유가", "섹터 전반", "업종 전반", "해외 증시")),
)


def _norm_token(s: Any) -> str:
    return re.sub(r"[\s_·\-]+", "", str(s or "")).strip().lower()


def _role_of(raw: Any, *, edges: list[dict[str, Any]], treatment: str,
             outcome: str) -> tuple[str, str]:
    """역할. **거부하지 않는다** - 읽거나, 유추하거나, 기본값을 쓴다.

    유추 규칙은 구조 하나뿐이다: 처치에서 결과까지 매개 노드를 거치면 `transmission`,
    바로 닿으면 `trigger`. 더 영리하게 굴지 않는다 - 유추가 신고보다 그럴듯해 보이면
    모델이 신고를 안 하게 된다.
    """
    got = _norm_token(raw.get("role") if isinstance(raw, dict) else "")
    if got in ROLE_SAY:
        return got, "declared"
    if got in _ROLE_ALIAS:
        return _ROLE_ALIAS[got], "declared"
    direct = any(e.get("from") == treatment and e.get("to") == outcome for e in edges)
    return ("trigger" if direct else "transmission"), "inferred"


def _domain_of(raw: Any, *, says: str, label: str) -> tuple[str, str]:
    """메커니즘 영역. **거부하지 않는다.**

    ★ 뒤지는 것은 `says` 와 `cause_label` 뿐이다 - 모델이 스스로 주장한 한 줄. 직렬화한
    JSON 전체를 뒤지면 노드 id·predicts·denies 의 부수 토큰이 걸린다(실측: `VI`).

    ★ 단서가 **여러 영역에 걸리면 유추하지 않는다.** 순서로 tie-break 하면 목록 배열이
    판정을 정하게 되고, 그건 자료가 아니라 내 코드 순서다. 애매하면 기본값으로 두는 편이
    낫다 - 커버리지 원장이 "정보 영역만 열렸다"를 그대로 보여주는 것이 편향을 숨기는
    것보다 정직하다. **유추로 다양성을 위조하지 않는다.**
    """
    got = _norm_token(raw.get("domain") if isinstance(raw, dict) else "")
    if got in DOMAIN_SAY:
        return got, "declared"
    if got in _DOMAIN_ALIAS:
        return _DOMAIN_ALIAS[got], "declared"
    hay = f"{says} {label}"
    hits = [dom for dom, keys in _DOMAIN_HINT if any(k in hay for k in keys)]
    return (hits[0], "inferred") if len(hits) == 1 else ("information", "inferred")


def _text(raw: Any, where: str, why: str = "") -> str:
    s = str(raw or "").strip()
    if not s:
        raise PipelineError(f"{where} 가 비었다" + (f" - {why}" if why else ""))
    return s


def _node(raw: Any, where: str) -> str:
    """노드 id. **시간 색인이 없으면 거부** - `graph.parse` 가 요구하는 규약이다."""
    nid = str(raw or "").strip()
    if not nid:
        raise PipelineError(f"{where} 가 비었다")
    try:
        _parse_nid(nid)
    except ValueError as exc:
        raise PipelineError(f"{where}: {exc}") from None
    return nid


def _lines(raw: Any, where: str, why: str, *, need: int = 1) -> list[str]:
    """문자열 목록. **falsy 비목록을 조용히 빈 목록으로 접지 않는다.**

    `denies: {}` 가 접히면 반증 약속 없는 가설이 정상 산출로 집계되고, P5 는 갈릴 자리가
    없는 채로 판별 계획을 낸다 - 침묵이 통과하는 경로다.
    """
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise PipelineError(f"{where} 는 목록이어야 한다 - {type(raw).__name__}")
    out = [str(x).strip() for x in items if str(x).strip()]
    if len(out) < need:
        raise PipelineError(f"{where} 가 비었다 - {why}")
    return out


def _edges(raw: Any, nodes: dict[str, Any]) -> list[dict[str, Any]]:
    """간선 목록. **양 끝이 nodes 에 있어야 한다** - 선언 안 된 노드로 가는 간선은 그림이다."""
    if not isinstance(raw, list) or not raw:
        raise PipelineError("edges 가 비었다 - treatment 에서 outcome 까지 간선으로 이어라")
    out: list[dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict):
            raise PipelineError(f"간선이 객체가 아니다 - {type(e).__name__}")
        src, dst = _node(e.get("from"), "edges.from"), _node(e.get("to"), "edges.to")
        miss = [x for x in (src, dst) if x not in nodes]
        if miss:
            raise PipelineError(f"간선 {src}->{dst} 의 {miss} 가 nodes 에 없다")
        out.append({**e, "from": src, "to": dst})
    return out


def _reaches(edges: list[dict[str, Any]], src: str, dst: str) -> bool:
    """방향 경로가 있나. 순환이 있어도 멈춘다 - 순환·시간역행 판정은 P3 몫이다."""
    seen, stack = {src}, [src]
    while stack:
        cur = stack.pop()
        if cur == dst:
            return True
        for e in edges:
            if e["from"] == cur and e["to"] not in seen:
                seen.add(e["to"])
                stack.append(e["to"])
    return False


def _anchor(raw: Any) -> tuple[float, float] | None:
    """`[lo, hi]` -> 구간. **단일 수는 거부한다** - 점추정은 답의 형태 위반이다."""
    if raw is None or raw == [] or raw == "":
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise PipelineError(f"anchor 는 [lo, hi] 두 수다 - {raw!r}. 점추정은 쓸 수 없다")
    try:
        lo, hi = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        raise PipelineError(f"anchor 의 값이 수가 아니다 - {raw!r}") from None
    return (lo, hi) if lo <= hi else (hi, lo)


def _norm(s: str) -> str:
    return re.sub(r"[\s_·-]+", "", s).lower()


def _killed_by(fp: Fingerprint, blob: str, says: str) -> str:
    """지문이 이미 죽인 부류인가. 겹치면 그 문장을, 아니면 빈 문자열.

    판정은 두 글자 어간 일치로 한다. 한국어는 조사·어미가 붙어 정확 일치가 거의 안 맞고
    (`공시가` vs `공시를`, `반영됨` vs `반영된다`) 이 자리에 임베딩을 부를 이유는 없다.
    **놓치는 쪽으로 기울여 놨다** - 거짓 거부는 세션의 3회를 형식 시비로 태우지만 거짓
    통과는 프롬프트에 실린 지문 본문이 한 번 더 막는다. 한계는 명확하다: 어간이 겹치지
    않게 바꿔 쓴 같은 가설은 통과한다. 그게 문제가 되면 P5 의 판별자가 잡는 자리이지
    여기서 문자열을 더 정교하게 만들 자리가 아니다.

    **탈출구를 둔다**: 축 이름을 `says` 에 적으면 통과시킨다. 지문이 틀렸다고 볼 여지는
    늘 있고, 그때 필요한 것은 침묵이 아니라 반박이 기록되는 것이다. 탈출구가 없으면
    모델은 같은 사유로 3회를 태우고 세션이 빈손으로 끝난다.
    """
    words = {w[:2] for w in _TOKEN.findall(blob)}
    for a in fp.axes:
        if a.name and a.name in says:
            continue
        for kill in a.kills:
            stems = {t[:2] for t in _TOKEN.findall(kill)}
            if len(stems) < 2:
                continue
            if len(stems & words) >= max(2, round(len(stems) * 0.6)):
                return kill
    return ""


__all__ = ["MAX_LOOKUPS", "MAX_TRIES", "SYSTEM", "propose"]


if __name__ == "__main__":
    from datetime import date

    from .contracts import Axis

    Q = Question(etf_instrument_id="i1", etf_name="TEST", trade_date=date(2026, 7, 16),
                 as_of="2026-07-16T15:40:00Z", observed=0.041, residual=0.0421,
                 route_code="R1", explanandum="r=+4.21%", intervention="공시가 없던 세계",
                 answer_form="구간과 상한")
    FP = Fingerprint(axes=[Axis(name="선반영", available=True, says="공시 전날 이미 움직였다",
                                kills=("공시 정보 경로",))])
    CAND = [{"event_type_code": "DIV", "event_id": "e1", "label": "배당", "share": 0.1}]
    GOOD = {"says": "지수 편입 규칙이 기계적 매수를 만든다", "cause_label": "지수수급",
            "treatment": "편입@t0", "assignment": "mechanical",
            "nodes": {"편입@t0": {"says": "지수 편입"}, "수급@t+0": {"says": "패시브 매수"}},
            "edges": [{"from": "편입@t0", "to": "수급@t+0"},
                      {"from": "수급@t+0", "to": OUTCOME_ID}],
            "predicts": ["리밸런스일에 거래량이 튄다"], "denies": ["비편입 피어가 같이 오른다"],
            "events": []}

    class _Client:
        """대본대로 답하는 가짜 클라이언트. 대본이 떨어지면 none 을 낸다."""

        def __init__(self, script: list[dict]) -> None:
            self.script, self.seen = list(script), []

        def complete_json(self, system: str, user: str) -> dict:
            self.seen.append(user)
            return self.script.pop(0) if self.script else {"none": "대본 끝"}

    class _Sql:
        def schema(self) -> str:
            return "스키마"

        def ask(self, q: str) -> str:
            return "1행"

    # 1 세션 인덱스가 author 에 남고, 채널이 겹치면 거부된 뒤 다시 물어 통과한다.
    OTHER = {**GOOD, "cause_label": "실적기대",
             "distinguishes": ["편입 경로면 비편입 피어가 조용하지만 실적 경로면 같이 움직인다"]}
    c = _Client([{"hypothesis": GOOD},
                 {"hypothesis": OTHER | {"cause_label": "지수수급"}},     # 채널 중복 -> 거부
                 {"hypothesis": OTHER}])
    hs = propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=2)
    assert [h.author for h in hs] == ["session1", "session2"], hs
    assert [h.cause_label for h in hs] == ["지수수급", "실적기대"], hs
    assert "이미 쓴 채널" in c.seen[2], c.seen[2]
    # 앞선 세션의 예측이 두 번째 세션 머리에 실린다 - 갈릴 재료가 없으면 갈릴 수 없다.
    assert "리밸런스일에 거래량이 튄다" in c.seen[1], c.seen[1]

    # 1b 앞선 가설이 있는데 갈리는 관측을 못 대면 거부된다. **대립 없는 가설은 목록만
    # 길게 한다** - 2026-07-30 실측에서 h2·h3 가 같은 관측을 예측해 판별이 불가능했다.
    c = _Client([{"hypothesis": GOOD}, {"hypothesis": {**GOOD, "cause_label": "다른채널"}}])
    hs = propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=2)
    assert [h.cause_label for h in hs] == ["지수수급"], hs
    assert "distinguishes" in c.seen[-1], c.seen[-1]

    # 2 어휘 밖 assignment · denies 누락 · 출처 없는 수치는 각각 거부되고 되물음이 된다.
    for bad, mark in (({**GOOD, "assignment": "endogenous"}, "어휘 밖"),
                      ({**GOOD, "denies": []}, "denies"),
                      ({**GOOD, "says": "배당이 12% 늘었다"}, "출처가 없다")):
        c = _Client([{"hypothesis": bad}])
        assert propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=1) == []
        assert mark in c.seen[-1], (mark, c.seen[-1])

    # 3 조회는 시도를 쓰지 않는다 - 조회 6회 뒤에도 가설 3회를 온전히 쓴다.
    c = _Client([{"sql": "SELECT 1"}] * MAX_LOOKUPS + [{"hypothesis": GOOD}])
    hs = propose(c, _Sql(), question=Q, fingerprint=FP, candidates=CAND, n=1)
    assert hs and hs[0].queries == ["SELECT 1"] * MAX_LOOKUPS, hs
    # 상한을 넘긴 조회부터는 시도를 쓴다 - 그래야 세션이 끝난다.
    c = _Client([{"sql": "SELECT 1"}] * (MAX_LOOKUPS + MAX_TRIES + 5))
    assert propose(c, _Sql(), question=Q, fingerprint=FP, candidates=CAND, n=1) == []
    assert len(c.seen) == MAX_LOOKUPS + MAX_TRIES

    # 4 지문이 죽인 부류는 거부되고, 축 이름을 적은 반박은 통과한다.
    dead = {**GOOD, "says": "공시 정보 경로로 가격이 움직였다"}
    c = _Client([{"hypothesis": dead},
                 {"hypothesis": {**dead, "says": "선반영 축은 종가 기준이라 장중 공시 정보 "
                                                 "경로를 죽이지 못한다"}}])
    hs = propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=1)
    assert hs and "선반영" in hs[0].says, hs
    assert "지문이 이미 죽인 부류다" in c.seen[1], c.seen[1]

    # 5 접지되지 않는 event_id · 결과에 닿지 않는 사슬 · 시간 색인 없는 노드는 거부된다.
    for bad, mark in (({**GOOD, "events": ["e9"]}, "원장에 없는 id"),
                      ({**GOOD, "edges": [{"from": "편입@t0", "to": "수급@t+0"}]}, "방향 경로"),
                      ({**GOOD, "treatment": "편입"}, "시간 색인")):
        c = _Client([{"hypothesis": bad}])
        assert propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=1) == []
        assert mark in c.seen[-1], (mark, c.seen[-1])

    # 5b 결과 노드는 코드가 정한다 - 모델이 제 이름을 보내도 무시되고, 정의까지 덮어쓴다.
    # 이름이 갈리면 P3 에서 결론이 여러 개가 되어 예산이 정의되지 않는다(2026-07-30 실측).
    c = _Client([{"hypothesis": {**GOOD, "outcome": "내맘대로_결론@t0",
                                 "nodes": {**GOOD["nodes"],
                                           OUTCOME_ID: {"says": "제멋대로 쓴 정의"}}}}])
    hs = propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=1)
    assert hs and hs[0].outcome == OUTCOME_ID, hs
    assert hs[0].nodes[OUTCOME_ID]["says"] == Q.explanandum, hs[0].nodes[OUTCOME_ID]
    assert "내맘대로_결론@t0" not in hs[0].nodes, hs[0].nodes

    # 6 none 은 그 자리에서 세션을 끝낸다 - 시도를 태우지 않는다.
    c = _Client([{"none": "후보가 전부 산술로 죽었다"}, {"hypothesis": GOOD}])
    assert propose(c, None, question=Q, fingerprint=FP, candidates=CAND, n=1) == []
    assert len(c.seen) == 1

    print("p2_hypotheses 자체검사 통과")
