"""제안 에이전트 — **모델이 내는 것은 설계뿐이다.**

모델에게 묻지 않는 것과 그 이유:

    조정집합    그래프에서 유도된다. 실측 정답률 78%, 코드는 구성상 100%
    통계량      데이터를 보기 전에 정할 수 없다. 실측: 스칼라를 8관측에 회귀하는 명세
    모집단 크기  코호트 술어가 정한다
    수치·p값    **날조가 전부 여기서 났다.** 쓸 자리를 두지 않는다
    귀무 종류    주장이 정한다(귀속이면 label). 층화는 선언만 받고 코드가 만든다

모델에게 묻는 것:

    처치·대조 술어   무엇을 무엇과 비교하나 — 이게 설계다
    메커니즘        왜 그 경로인가. 반증층이 공격하는 표면
    반증조건        무엇이 보이면 죽나
    시점 외생성      원인의 발생 시점이 결과에 대해 외생인가(역인과 차단)

프롬프트에 **타입 모집단과 분포 사전을 항상 싣는다.** 실측: 모집단 한 줄을 보여주면
인과 간선 5/5 가 타입 전체로 풀링했고, 안 보여주면 0/4 가 셀에 갇혔다(n=8 검정).
"""
from __future__ import annotations

from typing import Any

from ..config import PipelineError
from . import graph as G
from .engine import NMIN, STRATA, EdgeDesign

SYSTEM = """너는 ETF 당일 등락의 인과 설계자다. **설계만** 낸다 - 수치는 코드가 만든다.

## 무엇을 내나

노드와 간선으로 된 그래프, 그리고 인과 간선마다 **처치·대조 코호트 SQL 술어**.

노드 id 는 `이름@t±N` 형식(N=거래일 오프셋). 시간 역행 간선 금지.
노드 종별: TARGET(설명 대상) · SHOCK(사건) · OBSERVABLE(관측계열) ·
          MECHANISM(잠재 매개, effect=CDE 선언 필수) · CONFOUND

**SHOCK 노드는 접지돼야 한다.** `member_events` 에 브리프의 `event_id` 를 그대로 적고,
`tau` 에 그 사건의 `available_at` 을 적어라. 브리프에 없는 id 를 쓰면 구조 게이트에서
기각된다 - 존재하지 않는 사건을 원인으로 세울 수 없다.

간선 종류:
  directed    보통 인과 간선
  bidirected  **미지의 공통원인.** 조정으로 식별이 불가능해지고 검정 가능한 함의도
              줄어든다 - 공짜가 아니다. 진짜 모를 때만 써라

## 술어

처치는 사건 기반, 대조는 금융상품 기반이다. 순수 WHERE 조건만 쓴다
(세미콜론·주석·available_at 금지 - 시점 절은 코드가 넣는다).

처치 컬럼: instrument_id · trade_date · event_type_code · predicate_code · role_code
          · lifecycle_stage · sector_name · industry_name · market_cap · listing_market · ticker
대조 컬럼: instrument_id · sector_name · industry_name · market_cap · listing_market · ticker

**대조를 무엇 안에서 골랐으면 strata 도 그것이어야 한다.** 같은 날 같은 산업에서
골랐으면 `date_industry`, 같은 날에서만 골랐으면 `date`.

## 규칙

1. 처치와 대조는 **겹치지 않는 대비**여야 한다. "사건이 났다 vs 안 났다"에 대비가
   없으면(모두에게 걸렸으면) 다른 자리로 옮겨라 - 지명된 종목 vs 커버되지만 미지명 등.
2. `timing` 을 반드시 선언한다: scheduled(예정된 일정) · unscheduled(예고 없음) ·
   price_responsive(**가격을 보고 쓰인 것** - 역인과라 통계 주장 불가) · n/a
3. 브리프의 [산술] 줄에서 이미 죽은 후보는 제안하지 마라.
4. 원인을 못 찾으면 빈 간선 목록을 내라. **억지 설계는 UNCERTAIN 보다 나쁘다.**

JSON 하나만:
{"nodes": {"id": {"kind": "...", "unit": "stock", "measure": "무엇을 재는가",
                   "member_events": ["브리프의 event_id"], "tau": "available_at",
                   "effect": "CDE (MECHANISM 일 때만)"}},
 "edges": [{"from": "...", "to": "...", "kind": "directed|bidirected",
            "cause_label": "고객이 읽을 원인 이름",
            "treated": "SQL 술어", "control": "SQL 술어",
            "strata": "date|date_industry|none",
            "timing": "...", "because": "메커니즘", "false_if": "무엇이면 죽나"}],
 "missing": ["확인에 필요한데 저장소에 없는 것"]}"""


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.2f}%"


def brief(*, etf_name: str, trade_date: str, observed: float, residual: float,
          route_code: str, contributors: list[tuple[str, float]],
          candidates: list[dict]) -> str:
    """셀 브리프. **타입 모집단·분포 사전·무게를 항상 싣는다.**"""
    L = [f"셀: {etf_name} {trade_date}",
         f"관측 등락 {_pct(observed)} · 시장·피어 제거 후 잔차 {_pct(residual)}"
         f" · route={route_code}"]
    if contributors:
        L.append("기여 상위: " + ", ".join(f"{n}({_pct(c)})" for n, c in contributors[:5]))
    L.append("")
    L.append(f"후보 사건 {len(candidates)}건:")
    for c in candidates:
        L.append(f"  [{c['event_type_code']}] {c.get('label', '')} "
                 f"{c.get('event_date', '')} 대상 {c.get('ticker', '?')}")
        if c.get("event_id"):
            L.append(f"     event_id={c['event_id']}  available_at={c.get('available_at', '')}")
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
    L += ["", "상승 비율이 50% 근처인 타입은 방향을 못 쓴다. 크기는 분위수로 판단해라.",
          "잔차를 설명할 수 없다면 빈 간선 목록을 내라."]
    return "\n".join(L)


def _as_list(out: dict, key: str) -> list:
    """목록 필드. **falsy 비목록을 `[]` 로 접지 않는다** — `edges: {}` 를 "간선 없음"으로
    읽으면 계약 위반이 정상 산출로 집계돼 되먹임 없이 UNCERTAIN 이 나간다."""
    value = out.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise PipelineError(f"제안의 {key} 가 목록이 아니다: {type(value).__name__}")
    return value


def _opt_str(e: dict, i: int, key: str, default: str) -> str:
    """선택 문자열 필드. 비문자열이면 여기서 거부한다 — 그대로 `EdgeDesign` 에 실으면
    `engine` 의 `NMIN.get`·`narrate` 의 `.strip()` 에서 터지고, 그건 parse 밖이라
    되먹임이 못 잡는다(ALPHA-633)."""
    value = e.get(key)
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise PipelineError(f"간선 {i} 의 {key} 가 문자열이 아니다: {type(value).__name__}")
    return value


def parse(out: dict) -> tuple[dict, list[EdgeDesign], list[str]]:
    """모델 산출을 (nodes, designs, missing) 으로. **어휘 밖 값은 fail-loud.**

    형태가 어긋난 산출(`edges: [null]`·스칼라 루트 등)도 여기서 전부 `PipelineError` 로
    정규화한다. 타입이 갈리면 호출부(`run.explain`)의 되먹임이 그 예외를 못 알아보고
    그대로 새어 나가, AnalyzeOne 하나가 유니버스 전체 런을 죽인다(ALPHA-633).
    """
    if not isinstance(out, dict):
        raise PipelineError(f"제안이 객체가 아니다: {type(out).__name__}")
    nodes = out.get("nodes")
    if not isinstance(nodes, dict):
        raise PipelineError(f"제안에 nodes 가 없다: {sorted(out)[:6]}")
    for node, meta in nodes.items():
        # 노드 메타는 graph.validate 가 `m.get("kind")` 로 읽는다 - 여기서 안 거르면
        # 그쪽에서 AttributeError 가 나고, 그건 parse 밖이라 되먹임이 못 잡는다.
        if not isinstance(node, str) or not isinstance(meta, dict):
            raise PipelineError(f"nodes 항목이 (문자열, 객체)가 아니다: {node!r}")
        # 시간 색인(@t±N)까지 여기서 본다. graph.validate 는 첫 순회의 ValueError 만
        # 위반으로 담고(graph.py:314), MARKET 목록을 만들며 **다시** 파싱할 때는 안 감싼다
        # (graph.py:356) - 그 ValueError 는 parse 밖이라 되먹임이 못 잡는다. 형식 정본은
        # graph.parse 하나다(정규식을 여기 복제하면 둘이 갈린다).
        try:
            G.parse(node)
        except ValueError as exc:
            raise PipelineError(f"nodes 의 {node!r}: {exc}") from exc
    edges = _as_list(out, "edges")
    designs: list[EdgeDesign] = []
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            raise PipelineError(f"간선 {i} 가 객체가 아니다: {type(e).__name__}")
        if e.get("kind") == "bidirected":
            continue                      # 양방향은 설계가 아니라 가정이다
        for key in ("from", "to", "treated", "control"):
            value = e.get(key)
            # 문자열이 아닌 값(숫자·객체)도 여기서 걸러야 한다 - `.strip()` 이 터지면
            # PipelineError 가 아니라 AttributeError 라 되먹임 대상이 못 된다.
            if not isinstance(value, str) or not value.strip():
                raise PipelineError(f"간선 {i} 에 {key} 가 없다")
        strata = _opt_str(e, i, "strata", "date")
        if strata not in STRATA:
            raise PipelineError(f"간선 {i} strata={strata!r} 는 어휘 밖이다: {STRATA}")
        scope = _opt_str(e, i, "scope", "type")
        # 어휘를 안 보면 미지 scope 가 engine 의 `NMIN.get(scope, 8)` 에서 **가장 관대한**
        # 최소 표본(8)으로 떨어져, type(30) 이면 기각될 설계가 통과한다. NMIN 이 곧 어휘다.
        if scope not in NMIN:
            raise PipelineError(f"간선 {i} scope={scope!r} 는 어휘 밖이다: {sorted(NMIN)}")
        designs.append(EdgeDesign(
            src=e["from"], dst=e["to"], treated=e["treated"], control=e["control"],
            strata=strata, scope=scope,
            because=_opt_str(e, i, "because", ""),
            false_if=_opt_str(e, i, "false_if", ""),
            timing=_opt_str(e, i, "timing", "unscheduled"),
            cause_label=_opt_str(e, i, "cause_label", e["from"])))
    missing = [str(m) for m in _as_list(out, "missing")]
    return nodes, designs, missing


def propose(client, brief_text: str, *, feedback: str = "") -> dict[str, Any]:
    """제안. `feedback` 이 있으면 앞선 시도가 왜 안 됐는지 붙여 다시 묻는다.

    프로덕션 에이전트는 **도구를 쓰지 않는다**(샌드박스 exec 없음). 그래서 제안 전에
    자기 술어가 실제로 무언가를 맞히는지 확인할 방법이 없다 - 아무것도 못 맞히면
    LLM 호출 1회가 통째로 낭비된다. 도구를 주는 대신 **사유를 돌려주고 한 번 더 묻는다.**
    실험판에서 에이전트를 살린 것도 도구 개수가 아니라 교정을 담은 오류 메시지였다.
    """
    if not feedback:
        return client.complete_json(SYSTEM, brief_text)
    return client.complete_json(SYSTEM, brief_text + f"""

## 앞선 제안이 왜 안 됐나

{feedback}

같은 술어를 다시 내지 마라. 술어를 넓히거나(산업 -> 섹터, 타입 정확일치 -> LIKE),
대비를 다른 자리로 옮겨라. 넓혀도 안 되면 빈 간선 목록을 내라 - 억지 설계는
UNCERTAIN 보다 나쁘다.""")
