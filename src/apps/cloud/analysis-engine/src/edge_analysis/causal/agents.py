"""제안 에이전트 — **오늘 이 한 움직임의 인과 사슬을 그린다.**

이 파일에서 가장 많이 한 일은 규칙을 **지운** 것이다. 남은 원칙은 하나다.

    기계가 검사할 수 있는 것은 프롬프트에서 뺀다. 검사할 수 없는 것만 남긴다.

시간 역행·미접지 노드·순환·어휘 위반·표본 부족·그레인 중복은 전부 코드가 잡아 사유와
함께 되돌린다. 그러니 프롬프트에 적을 이유가 없다 - 적으면 지면만 먹고, 모델은 그 목록을
채우는 데 주의를 쓴다. 반대로 **무엇이 원인인가·어떤 경로로 전달되나·어느 계수가
필요한가·무엇이면 죽나**는 코드가 대신할 수 없다. 프롬프트는 그것만 묻는다.

**도메인 예시를 주지 않는다.** 형식(JSON 골격)은 보여주고 내용은 비운다. 예시를 한 번
주면 모델은 그 모양으로만 답한다 - 사슬의 길이·매개의 종류·코호트의 정의를 예시가
결정해버린다. 여기서 필요한 것은 정해진 몇 갈래가 아니라 모델이 가진 도메인 지식이
그대로 나오는 것이므로, 방향을 정해주지 않는 쪽이 정확히 맞다.

수치를 금지하지 않는다는 것이 이전 판과의 가장 큰 차이다. 연역 사슬에서는 수치가 본질
이므로 금지하면 사슬 자체가 불가능하다. 날조는 금지로 막지 않고 **출처 대조로 죽인다** -
`source` 없는 값은 검정이 확인할 수 없어 그 자리에서 기각된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..config import PipelineError
from .chain import KINDS, Edge, Interval, paths
from .engine import EdgeDesign

SYSTEM = """너는 **오늘 이 셀이 이만큼 움직인 이유**를 인과 사슬로 푼다.
일반 법칙을 정리하는 자리가 아니다 - 설명 대상은 브리프에 적힌 잔차 하나다.

## 사슬
사건에서 가격까지는 간접이다. 몇 단계인지, 무엇이 매개인지 정해진 목록은 없다.
아는 만큼 쪼개라 - 실물 지표·회계 항목·기대·수급·경쟁 반응 무엇이든 노드가 된다.
쪼갤수록 검정 지점이 늘어 주장이 강해지고, 뭉갤수록 검정 불가에 가까워진다.
원인은 하나가 아니어도 된다.

간선은 셋 중 하나다. **증명 양식이 달라서 구분한다.**

  identity     항등식. 오차가 없다. `formula` 와 입력 `source` 를 적어라
  elasticity   계수가 필요한 연역. `effect` 에 계수 구간, `source` 에 계수의 근거
  statistical  연역이 안 되는 자리. `exposure` 에 이 경로에 노출된 집합을,
               `reference` 에 비교할 참조집합을 적고 `effect` 는 비워라 -
               검정 세션이 데이터로 채운다. 참조집합 선택이 결론을 바꾸므로
               그 선택도 `invariant_to` 에 걸어라

`effect` 는 부모 변화에 대한 자식 변화의 비다(탄력성). 사슬의 절대 크기는 사건 노드의
`value` 한 곳에서만 들어온다.

## 수치
써라. 단 **모든 수치에 `source` 가 필요하다** - 어느 공시·어느 재무항목·어느 추정치인가.
검정 세션이 그 출처를 조회해 대조한다. 출처 없는 값과 대조에서 어긋난 값은 그 자리에서
죽으므로, 추측을 좁게 쓰는 것이 유일한 치명적 실수다. 모르면 넓은 구간을 써라.
구간 폭은 감점이 아니다 - 무지의 정직한 크기다.

## 예산
브리프의 잔차가 설명할 총량이다. 경로 예측의 합이 그것을 넘으면 그래프가 기각된다.
전부 설명하려 하지 마라 - **미설명분을 남기는 것이 초과보다 낫다.**

## 불변
간선마다 `invariant_to` 에 "이 주장이 의존하지 않는 것"을 적어라. 문턱·기준·기간·정의·
참조집단 무엇이든. 코드가 그것들을 흔들어 예측 분포를 만들고, 흔들었을 때 결론이
뒤집히면 간선이 죽는다. 반증 약속이므로 많이 적을수록 주장이 강해진다.

## 막힌 것
못 재는 간선을 빼지 마라. `needs` 에 무엇이 있어야 서는지 적고 남겨라. 구조가 맞는데
데이터가 없는 것은 실패가 아니라 수집 의제다. 반대로 잔차를 설명할 원인을 정말 못 찾으면
빈 간선 목록을 내라 - 억지 사슬은 침묵보다 나쁘다.

## 모르는 것
산업 구조·공급망·원가 구성·계약 관행을 모르면 추측하지 말고 `lookups` 에 질의를 적어라.
정기보고서 원문에서 찾아 붙이고 **다시 묻는다.** 무엇을 물을지는 네가 정한다 - 몇 개든,
어느 층위든. 아는 것만으로 충분하면 빈 목록을 내라(그러면 조회 없이 바로 넘어간다).

관측이 가격을 보고 만들어졌을 위험이 있으면(사후 해설·가격 언급 기사) `reverse_risk` 에
적어라. 이건 코드가 판별할 수 없고, 적힌 간선은 통계 주장에서 제외된다.

JSON 하나만. 주석·설명 문장을 밖에 붙이지 마라.
{"target": "설명 대상 노드 id",
 "lookups": ["알아야 하는 것을 질의로. 없으면 빈 목록"],
 "nodes": {"<id>": {"says": "이 노드가 무엇이며 무엇을 어떤 단위로 재는가",
                    "observed": "어떻게 관측하나 (못 재면 null)",
                    "value": [lo, hi],
                    "events": ["브리프의 event_id (사건 노드일 때)"]}},
 "edges": [{"from": "", "to": "", "kind": "identity|elasticity|statistical",
            "says": "이 간선이 주장하는 것 한 문장",
            "because": "왜 이 경로로 전달되나",
            "false_if": "무엇이 관측되면 이 간선이 죽나",
            "effect": [lo, hi], "formula": "", "source": "",
            "exposure": "이 경로에 노출된 집합", "reference": "비교할 참조집합",
            "invariant_to": [], "needs": null, "reverse_risk": null}],
 "missing": ["확인에 필요한데 저장소에 없는 것"]}

노드 id 는 `이름@t단계` 다 - 단계 숫자가 시간 순서이고, 그것만으로 비순환이 보장된다.
`exposure` 는 사건 열(`event_type_code`·`predicate_code`·`ticker`·`industry_name` 등)을
쓰고, `reference` 는 종목 속성(`instrument_id`·`ticker`·`sector_name`·`industry_name`·
`market_cap`·`listing_market`)만 쓴다 - 참조집합은 사건이 없는 종목도 포함해야 한다.

아래는 **형태 예시**다(도메인 판단이 아니라 모양만 보라 - 규칙을 통과하는 최소 골격).
```json
{"target": "대상셀@t1",
 "lookups": [],
 "nodes": {
   "지명종목_사건@t0": {"says": "지명 종목의 사건 - 크기를 %로 잰다",
                        "observed": "공시 원문 대비 컨센서스",
                        "value": [0.05, 0.15], "events": ["evt_abc123"]},
   "대상셀@t1": {"says": "대상 셀의 당일 시장초과수익", "observed": "종가 기준 초과수익"}},
 "edges": [{"from": "지명종목_사건@t0", "to": "대상셀@t1", "kind": "statistical",
            "says": "지명 종목의 사건이 같은 산업 종목의 당일 초과수익을 움직인다",
            "because": "같은 수요·가격 전망을 공유해 밸류에이션이 함께 조정된다",
            "false_if": "같은 산업의 미지명 종목이 같은 폭으로 움직였다",
            "effect": null, "formula": "", "source": "",
            "exposure": "event_type_code = 'COMPANY.EARNINGS.RESULT_RELEASE' AND ticker = '000660'",
            "reference": "industry_name = 'Semiconductors' AND ticker != '000660'",
            "invariant_to": ["참조집합 정의", "창 길이"],
            "needs": null, "reverse_risk": null}],
 "missing": []}
```"""


@dataclass(frozen=True, slots=True)
class Proposal:
    """모델 산출을 코드가 쓰는 모양으로. **어휘 밖 값은 여기서 fail-loud 한다.**"""

    target: str = ""
    nodes: dict[str, Any] = field(default_factory=dict)
    chain: list[Edge] = field(default_factory=list)
    anchors: dict[str, Interval] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    reverse: dict[tuple[str, str], str] = field(default_factory=dict)
    lookups: list[str] = field(default_factory=list)   # 알아야 하는 것. 코드가 조회한다

    @property
    def edges(self) -> list[dict]:
        """식별·적합이 보는 간선 목록."""
        return [{"from": e.src, "to": e.dst,
                 "timing": ("price_responsive"
                            if self.reverse.get((e.src, e.dst)) else "unscheduled")}
                for e in self.chain]

    @property
    def designs(self) -> list[EdgeDesign]:
        """검정 세션이 실제로 재야 하는 간선 = **statistical 뿐이다.**

        항등식은 계산이고 탄력성은 출처 대조라, 코호트를 짜서 검정할 대상이 아니다.
        이 구분이 없으면 계산을 검정하거나 추정을 계산으로 위장하게 된다.

        고객 문장에 쓰는 원인 이름은 **사슬의 뿌리**에서 온다. 통계 간선의 부모는 대개
        중간 매개(기대·심리)이고, 그걸 원인이라 쓰면 "주주환원 기대가 원인입니다" 같은
        말이 나간다 - 사람이 읽어야 하는 것은 그 기대를 만든 사건이다.
        """
        return [EdgeDesign(
            src=e.src, dst=e.dst, treated=e.exposure, control=e.reference,
            strata="date", scope="type", claims="L4",
            say=e.says, because=e.because, false_if=e.false_if, needs=e.needs,
            timing=("price_responsive" if self.reverse.get((e.src, e.dst))
                    else "unscheduled"),
            cause_label=self.label(e.src))
            for e in self.chain if e.kind == "statistical"]

    def label(self, node: str) -> str:
        """사슬을 거슬러 올라가 뿌리의 이름. 없으면 노드 id 그대로."""
        seen = {node}
        cur = node
        while True:
            ups = [e.src for e in self.chain if e.dst == cur and e.src not in seen]
            if not ups:
                break
            cur = ups[0]
            seen.add(cur)
        says = str((self.nodes.get(cur) or {}).get("says") or "").strip()
        return says.split(" (")[0].split(" - ")[0] if says else cur

    @property
    def needs(self) -> list[str]:
        """간선을 지우지 않고 남긴 데이터 요청 씨앗."""
        return [e.needs for e in self.chain if e.needs]

    def routes(self) -> list:
        """예산에 들어가는 경로들. `target` 으로 닿는 것만."""
        return paths(self.chain, self.target, self.anchors)


def _iv(raw: Any, where: str) -> Interval | None:
    """`[lo, hi]` 또는 단일 수 → 구간. **모양이 틀리면 조용히 넘기지 않는다.**"""
    if raw is None or raw == []:
        return None
    if isinstance(raw, (int, float)):
        return Interval(float(raw), float(raw))
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            lo, hi = float(raw[0]), float(raw[1])
        except (TypeError, ValueError) as exc:
            raise PipelineError(f"{where}: 구간에 수가 아닌 값 {raw!r}") from exc
        return Interval(min(lo, hi), max(lo, hi))
    raise PipelineError(f"{where}: 구간은 [lo, hi] 여야 한다 - {raw!r}")


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.2f}%"


def brief(*, etf_name: str, trade_date: str, observed: float, residual: float,
          route_code: str, contributors: list[tuple[str, float]],
          candidates: list[dict], industry: dict | None = None) -> str:
    """셀 브리프. **사실만 싣는다 - 어디를 보라는 지시를 넣지 않는다.**

    이전 판은 여기에 "상승 비율이 50% 근처면 방향을 못 쓴다", "되도록 타입 전체로 쌓아라"
    같은 줄을 넣었다. 그건 결론과 참조집합을 미리 정해주는 것이어서, 모델이 자기 도메인
    지식으로 사슬을 세우는 대신 그 지시를 만족시키는 쪽으로 움직였다. 지시를 지우고
    사실(모집단·분포·비중·측정값)만 남긴다.
    """
    L = [f"셀: {etf_name} {trade_date}",
         f"관측 등락 {_pct(observed)} · 시장·피어 제거 후 잔차 {_pct(residual)}"
         f" · route={route_code}",
         f"설명 예산: {abs(residual) * 100:.2f}%p"]
    if contributors:
        L.append("기여 상위: " + ", ".join(f"{n}({_pct(c)})" for n, c in contributors[:5]))
    L.append("")
    L.append(f"후보 사건 {len(candidates)}건:")
    for c in candidates:
        # `predicate_code` 를 보여주는 이유: 술어에 쓸 수 있는 값을 안 보이면 모델이
        # 발명한다 - 실제로 원장에 없는 `predicate_code = 'EARNINGS_MISS'` 를 내 0건이 됐다.
        pred = f" predicate_code={c['predicate_code']}" if c.get("predicate_code") else ""
        L.append(f"  [{c['event_type_code']}{pred}] {c.get('label', '')} "
                 f"{c.get('event_date', '')} 대상 {c.get('ticker', '?')}")
        if c.get("event_id"):
            L.append(f"     event_id={c['event_id']}  available_at={c.get('available_at', '')}")
        for m in c.get("measures") or []:
            L.append(f"     측정: {m.get('role_code', '?')}={m.get('surface') or m.get('value')}"
                     f" {m.get('unit') or ''} basis={m.get('basis', '?')}"
                     f" 출처={m.get('value_source', '?')}")
        p = c.get("prior") or {}
        if p.get("n"):
            L.append(f"     타입 모집단: 사건 {p.get('events', 0)} · 종목 {p.get('instruments', 0)}"
                     f" · 유효n≈{p.get('effective_n', 0)} · {p.get('first')}~{p.get('last')}")
            L.append(f"     분포: 상승 {p.get('up_ratio', 0) * 100:.0f}%"
                     f" · |초과수익| 중위 {p.get('abs_q50', 0) * 100:.1f}%"
                     f" p90 {p.get('abs_q90', 0) * 100:.1f}%"
                     f" 최대 {p.get('abs_max', 0) * 100:.1f}%")
        if c.get("share") is not None:
            L.append(f"     ETF 내 비중 {c['share'] * 100:.2f}%")
        if c.get("killed"):
            L.append(f"     [산술] 이미 기각됨 — {c['killed']}")
    if industry:
        # 실제 값을 싣는다. 원장의 industry_name 은 원천 원문(영어)이라, 어휘를 보여주지
        # 않으면 모델이 한국어로 추측하고(`sector_name = '반도체'`) 대조군이 0건이 된다.
        vocab = sorted({v for v in industry.values() if v})
        L += ["", f"쓸 수 있는 industry_name 값 ({len(vocab)}종, 원문 그대로 써라):",
              "  " + " · ".join(vocab[:40])]
    return "\n".join(L)


def _seq(raw: Any, where: str) -> list:
    """목록이어야 하는 자리. **falsy 비목록을 조용히 빈 목록으로 접지 않는다.**

    `edges: {}` 는 예외조차 없이 "간선 없음"으로 접혀 계약 위반이 정상 산출로 집계된다 -
    그러면 UNCERTAIN 설명이 초록으로 게시된다. 게이트가 거르는 모든 위반은 **한 타입**
    (PipelineError)으로 나와야 `run.explain` 의 되먹임이 알아본다(ALPHA-633).
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise PipelineError(f"{where}: 목록이어야 한다 - {type(raw).__name__}")
    return list(raw)


def parse(out: dict) -> Proposal:
    """모델 산출을 `Proposal` 로. **여기서 잡는 것은 프롬프트에서 빠진 것들이다.**

    유형별로 무엇이 반드시 있어야 하는지를 코드가 검사하므로 프롬프트가 그 목록을 다시
    나열하지 않아도 된다. 어긋나면 사유가 그대로 되먹임 문장이 되어 다음 시도를 고친다.
    """
    nodes = out.get("nodes")
    lookups = [str(q).strip() for q in _seq(out.get("lookups"), "lookups")
               if str(q).strip()]
    if not isinstance(nodes, dict):
        # 조회만 먼저 요청하는 것은 유효한 산출이다 - 모르는 것을 물으려면 사슬을 먼저
        # 그려야 한다는 요구는, 추측으로 그리게 만드는 요구다.
        if lookups:
            return Proposal(lookups=lookups)
        raise PipelineError(f"제안에 nodes 가 없다: {sorted(out)[:6]}")
    target = str(out.get("target") or "").strip()
    if target and target not in nodes:
        raise PipelineError(f"target={target!r} 이 nodes 에 없다")

    anchors: dict[str, Interval] = {}
    for nid, spec in nodes.items():
        if isinstance(spec, dict):
            got = _iv(spec.get("value"), f"노드 {nid} value")
            if got:
                anchors[nid] = got
            # `events` 는 접지 검사(graph.validate)가 **순회하고 집합 원소로 쓴다.**
            # 스칼라·unhashable 원소가 그대로 내려가면 TypeError 로 새어 되먹임을
            # 우회한다 - 게이트가 거르는 위반은 전부 PipelineError 여야 한다(ALPHA-633).
            for x in _seq(spec.get("events"), f"노드 {nid} events"):
                if not isinstance(x, str):
                    raise PipelineError(
                        f"노드 {nid} events 원소가 문자열이 아니다 - "
                        f"{type(x).__name__}. 사건 id 는 브리프의 event_id 다")

    chain: list[Edge] = []
    reverse: dict[tuple[str, str], str] = {}
    for i, e in enumerate(_seq(out.get("edges"), "edges")):
        if not isinstance(e, dict):
            raise PipelineError(f"간선 {i} 가 객체가 아니다 - {type(e).__name__}")
        for key in ("from", "to"):
            if not str(e.get(key) or "").strip():
                raise PipelineError(f"간선 {i} 에 {key} 가 없다")
        tag = f"간선 {i}({e['from']}→{e['to']})"
        kind = str(e.get("kind") or "").strip()
        if kind not in KINDS:
            raise PipelineError(f"{tag} kind={kind!r} 는 {KINDS} 밖이다")
        if not str(e.get("says") or e.get("because") or "").strip():
            raise PipelineError(
                f"{tag} 에 says·because 가 둘 다 없다 - 무엇을 주장하는지 쓰지 않은 "
                "간선은 검정할 수 없다")
        eff = _iv(e.get("effect"), f"{tag} effect")
        src = str(e.get("source") or "").strip()
        if eff and kind != "statistical" and not src:
            raise PipelineError(
                f"{tag} 에 수치({eff})는 있고 source 가 없다 - 출처를 대조할 수 없는 "
                "값은 날조와 구별되지 않는다")
        if kind == "identity" and not str(e.get("formula") or "").strip():
            raise PipelineError(f"{tag} 는 항등식인데 formula 가 없다")
        if kind == "statistical" and not str(
                e.get("exposure") or e.get("needs") or "").strip():
            raise PipelineError(
                f"{tag} 는 통계 간선인데 exposure 도 needs 도 없다 - 무엇을 재야 하는지 "
                "적지 않으면 검정 세션이 할 일이 없다")
        if rv := str(e.get("reverse_risk") or "").strip():
            reverse[(e["from"], e["to"])] = rv
        chain.append(Edge(
            src=e["from"], dst=e["to"], kind=kind,
            says=str(e.get("says") or ""), because=str(e.get("because") or ""),
            false_if=str(e.get("false_if") or ""),
            effect=eff, formula=str(e.get("formula") or ""), source=src,
            exposure=str(e.get("exposure") or ""),
            reference=str(e.get("reference") or ""),
            invariant_to=tuple(str(x) for x in _seq(e.get("invariant_to"),
                                                    f"{tag} invariant_to")),
            needs=str(e.get("needs") or "")))

    if not target and chain:
        # target 을 안 적었으면 아무 간선의 도착점도 아닌 노드가 결론이다.
        sinks = {e.dst for e in chain} - {e.src for e in chain}
        if len(sinks) == 1:
            target = sinks.pop()
        else:
            raise PipelineError(
                f"target 이 없고 종점이 {len(sinks)} 개다({sorted(sinks)[:4]}) - "
                "설명 대상이 하나로 정해지지 않으면 예산을 계산할 수 없다")
    return Proposal(target=target, nodes=nodes, chain=chain, anchors=anchors,
                    missing=[str(m) for m in _seq(out.get("missing"), "missing")],
                    reverse=reverse, lookups=lookups)


def evidence(found: list[tuple[str, list[dict]]]) -> str:
    """조회 결과를 되먹임 문장으로. **출처를 붙여 넘긴다.**

    산문 근거도 수치와 같은 규율을 받는다 - 어느 회사 어느 공시 어느 문단에서 온 말인지
    없으면, 모델이 그걸 읽고 쓴 문장을 사후에 확인할 수 없다. 그래서 도메인·티커·순서를
    같이 싣고, 원문을 자르되 어디서 잘렸는지 알 수 있게 둔다.
    """
    L = ["## 물어본 것에 대한 답 (정기보고서 「사업의 내용」 원문)", ""]
    for q, hits in found:
        L.append(f"### {q}")
        if not hits:
            L.append("  (해당 도메인 문서에서 못 찾았다. 이 대목은 추측하지 말고 "
                     "`needs` 나 `missing` 에 남겨라)")
            L.append("")
            continue
        for h in hits:
            L.append(f"  [{h['domain']} · {h['ticker']}#{h['ord']}] "
                     f"{h['text'][:700].strip()}")
        L.append("")
    L.append("이 근거로 사슬을 다시 그려라. 읽고도 모르는 대목은 `needs` 에 남겨라 - "
             "읽었다고 아는 척하는 것이 가장 나쁘다.")
    return "\n".join(L)


def measured(prop: Proposal, proofs: list) -> Proposal:
    """검정 결과를 사슬에 되꽂는다. **statistical 칸의 구간은 데이터에서 온다.**

    제안은 통계 간선의 배수를 비워서 낸다(모르는 것을 좁게 쓰지 않기 위해). 검정이
    끝나면 추정치와 귀무 산포로 구간을 만들어 그 칸을 채운다 - 그래야 사슬이 끝까지
    곱해져 점 예측이 되고, 예산 정합이 계산된다.

    귀무 산포를 반폭으로 쓰는 이유는 그것이 **이 검정이 실제로 만든 불확실성**이라서다.
    정규 근사 신뢰구간을 따로 만들면 원장에 없는 수가 산출물에 들어간다.
    """
    got = {}
    for r in proofs:
        d = getattr(r, "design", None)
        if d is None or r.effect is None:
            continue
        half = abs(r.null_sd or 0.0) * 1.96
        got[(d.src, d.dst)] = Interval(r.effect - half, r.effect + half)
    if not got:
        return prop
    filled = [replace(e, effect=got[(e.src, e.dst)])
              if (e.kind == "statistical" and not e.measured
                  and (e.src, e.dst) in got) else e
              for e in prop.chain]
    return replace(prop, chain=filled)


def propose(client, brief_text: str, *, feedback: str = "") -> dict[str, Any]:
    """제안. `feedback` 이 있으면 앞선 시도가 왜 안 됐는지 붙여 다시 묻는다.

    도구를 주지 않는 대신 사유를 돌려주고 한 번 더 묻는다 - 실험판에서 에이전트를 살린
    것도 도구 개수가 아니라 교정을 담은 오류 메시지였다.
    """
    if not feedback:
        return client.complete_json(SYSTEM, brief_text)
    return client.complete_json(SYSTEM, brief_text + f"""

## 앞선 제안이 왜 안 됐나

{feedback}

같은 구조를 다시 내지 마라. 데이터가 없어서 막히는 것이면 간선을 지우지 말고 `needs` 에
적어라 - 그건 유효한 산출이다. 정말 아무것도 못 세우면 빈 간선 목록을 내라.""")
