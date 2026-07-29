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
from .engine import STRATA, EdgeDesign

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

처치 컬럼: ticker · trade_date · event_type_code · predicate_code · role_code
          · lifecycle_stage · sector_name · industry_name · market_cap · listing_market
대조 컬럼: ticker · sector_name · industry_name · market_cap · listing_market

**종목을 지목할 때는 `ticker` 를 쓴다.** `instrument_id` 도 쓸 수 있지만 그건 티커가 아니라
불투명 식별자(`inst_01K...`)다 - 거기에 `'000660'` 같은 티커를 넣으면 0건이 나온다.

**대조를 무엇 안에서 골랐으면 strata 도 그것이어야 한다.** 같은 날 같은 산업에서
골랐으면 `date_industry`, 같은 날에서만 골랐으면 `date`.

## 규칙

1. 처치와 대조는 **겹치지 않는 대비**여야 한다. "사건이 났다 vs 안 났다"에 대비가
   없으면(모두에게 걸렸으면) 다른 자리로 옮겨라 - 지명된 종목 vs 커버되지만 미지명 등.
2. `timing` 을 반드시 선언한다: scheduled(예정된 일정) · unscheduled(예고 없음) ·
   price_responsive(**가격을 보고 쓰인 것** - 역인과라 통계 주장 불가) · n/a
3. 브리프의 [산술] 줄에서 이미 죽은 후보는 제안하지 마라.
4. 원인을 못 찾으면 빈 간선 목록을 내라. **억지 설계는 UNCERTAIN 보다 나쁘다.**
5. **가격 계열끼리 잇지 마라.** 두 가격은 시장 요인에 함께 흔들려 그 간선이 인과가 아니다.
   꼭 이어야 하면 둘 중 하나를 해라: 노드 id 가 정확히 `MARKET@t±N` 인 노드를 만들어
   **출발 노드의 부모로** 넣거나(이름을 번역하지 마라 - `시장_지수` 는 인식되지 않는다),
   출발 노드에 `"residualized": true` 를 선언해라(시장 성분을 이미 제거한 계열이라는 뜻).
   둘 다 없으면 구조 게이트에서 기각된다.

JSON 하나만:
{"nodes": {"id": {"kind": "...", "unit": "stock", "measure": "무엇을 재는가",
                   "member_events": ["브리프의 event_id"], "tau": "available_at",
                   "residualized": true,
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
          candidates: list[dict], industry: dict | None = None) -> str:
    """셀 브리프. **타입 모집단·분포 사전·무게를 항상 싣는다.**"""
    L = [f"셀: {etf_name} {trade_date}",
         f"관측 등락 {_pct(observed)} · 시장·피어 제거 후 잔차 {_pct(residual)}"
         f" · route={route_code}"]
    if contributors:
        L.append("기여 상위: " + ", ".join(f"{n}({_pct(c)})" for n, c in contributors[:5]))
    L.append("")
    L.append(f"후보 사건 {len(candidates)}건:")
    for c in candidates:
        pred = f" predicate_code={c['predicate_code']}" if c.get("predicate_code") else ""
        L.append(f"  [{c['event_type_code']}{pred}] {c.get('label', '')} "
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
    if industry:
        # 실제 값을 싣는다. 원장의 industry_name 은 원천 원문(영어)이라, 어휘를 보여주지
        # 않으면 모델이 한국어로 추측하고(`sector_name = '반도체'`) 대조군이 0건이 된다.
        vocab = sorted({v for v in industry.values() if v})
        L += ["", f"쓸 수 있는 industry_name 값 ({len(vocab)}종, 원문 그대로 써라):",
              "  " + " · ".join(vocab[:40])]
    L += ["", "상승 비율이 50% 근처인 타입은 방향을 못 쓴다. 크기는 분위수로 판단해라.",
          "잔차를 설명할 수 없다면 빈 간선 목록을 내라."]
    return "\n".join(L)


def parse(out: dict) -> tuple[dict, list[EdgeDesign], list[str]]:
    """모델 산출을 (nodes, designs, missing) 으로. **어휘 밖 값은 fail-loud.**"""
    nodes = out.get("nodes")
    if not isinstance(nodes, dict):
        raise PipelineError(f"제안에 nodes 가 없다: {sorted(out)[:6]}")
    designs: list[EdgeDesign] = []
    for i, e in enumerate(out.get("edges") or []):
        if e.get("kind") == "bidirected":
            continue                      # 양방향은 설계가 아니라 가정이다
        for key in ("from", "to", "treated", "control"):
            if not (e.get(key) or "").strip():
                raise PipelineError(f"간선 {i} 에 {key} 가 없다")
        strata = e.get("strata") or "date"
        if strata not in STRATA:
            raise PipelineError(f"간선 {i} strata={strata!r} 는 어휘 밖이다: {STRATA}")
        designs.append(EdgeDesign(
            src=e["from"], dst=e["to"], treated=e["treated"], control=e["control"],
            strata=strata, scope=e.get("scope") or "type",
            because=e.get("because") or "", false_if=e.get("false_if") or "",
            timing=e.get("timing") or "unscheduled",
            cause_label=e.get("cause_label") or e["from"]))
    missing = [str(m) for m in (out.get("missing") or [])]
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
