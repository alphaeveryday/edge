"""DAG 표현과 검증 - **규칙은 코드다. 산문이 아니다.**

노드는 종별로 타입되고 시간 색인을 갖는다. 대부분의 규칙이 타입 수준이라
코드가 검사한다. 그리고 두 가지가 **계산된다** - 손으로 쓰지 않는다:

  1. 함의 조건부독립  그래프의 경험적 내용 전체. 이게 반증 표면이다
  2. 판별기준        두 경쟁 구조의 함의 대칭차집합

두 구조가 **Markov 동등**이면(같은 skeleton + 같은 비차폐 충돌자, Verma & Pearl 1990)
어떤 관측 데이터로도 구별할 수 없다. 그런 제안은 거부된다 - 판가름이 원리적으로 불가능하다.

**미관측 노드가 든 함의는 검정 불가다.** 그래서 잠재 매개를 넣으면 함의가 줄고,
함의가 0이면 그 구조는 반증 표면이 없다고 코드가 말해준다. "자식 4개 미만 잠재노드는
접어라"는 규칙이 여기서 자동으로 나온다.
"""
from __future__ import annotations

import itertools
import re

# 노드 명세는 자연어(`says`)와 관측 가능성(`observed`)뿐이다. 종별 열거(TARGET·SHOCK·
# MECHANISM…)를 없앤 이유: 사슬의 매개는 회계 항목·기대·수급·경쟁 반응처럼 정해진 칸에
# 안 맞는 것이 대부분이고, 칸을 강제하면 모델이 노드를 만들지 않고 칸을 채운다.
# 함의의 검정 가능성에 필요한 것은 종별이 아니라 관측 여부 한 비트다.
#
# `edges` 상한을 12 에서 32 로 올렸다. 사건에서 가격까지 몇 단계인지 미리 알 수 없고,
# 쪼갤수록 검정 지점이 늘어 주장이 강해지는 구조이므로 상한이 사슬을 자르면 안 된다.
# 남긴 이유는 폭주 방어(토큰·시간)뿐이다.
LIMITS = {"edges": 32, "structures": (2, 3), "fanout": 8, "depth": 12}
_TAG = re.compile(r"^(?P<name>.+)@t(?P<off>[+-]?\d+)$")


def parse(nid: str) -> tuple[str, int]:
    """'sentiment@t-2' -> ('sentiment', -2). 시간 색인은 필수다."""
    m = _TAG.match(nid)
    if not m:
        raise ValueError(f"노드 id 에 시간 색인이 없다: {nid!r} (형식: 이름@t0, 이름@t-2)")
    return m.group("name"), int(m.group("off"))


def _name(nid: str) -> str:
    """시간 색인을 뗀 이름. 색인이 없으면 그대로 - 위반은 규칙 1 이 따로 보고한다."""
    m = _TAG.match(nid)
    return m.group("name") if m else nid


# ── 그래프 기본 ────────────────────────────────────────────────────────
def parents(edges: list, n: str) -> set:
    return {a for a, b in edges if b == n}


def ancestors(edges: list, S: set) -> set:
    out, stack = set(S), list(S)
    while stack:
        x = stack.pop()
        for p in parents(edges, x):
            if p not in out:
                out.add(p)
                stack.append(p)
    return out


def dsep(edges: list, X: str, Y: str, Z: set) -> bool:
    """d-분리 - 조상 부분그래프 도덕화 후 Z 제거하고 연결 여부를 본다.

    Lauritzen 등의 표준 절차다. 경로 열거보다 구현이 짧고 틀릴 데가 적다.
    """
    A = ancestors(edges, {X, Y} | set(Z))
    adj: dict = {n: set() for n in A}
    for a, b in edges:
        if a in A and b in A:
            adj[a].add(b)
            adj[b].add(a)
    # 도덕화: 같은 자식을 가진 부모끼리 잇는다
    for n in A:
        ps = [p for p in parents(edges, n) if p in A]
        for u, v in itertools.combinations(ps, 2):
            adj[u].add(v)
            adj[v].add(u)
    for z in Z:
        if z in adj:
            for k in adj[z]:
                adj[k].discard(z)
            adj[z] = set()
    seen, stack = {X}, [X]
    while stack:
        x = stack.pop()
        if x == Y:
            return False
        for k in adj.get(x, ()):
            if k not in seen:
                seen.add(k)
                stack.append(k)
    return Y not in seen


def skeleton(edges: list) -> set:
    return {frozenset((a, b)) for a, b in edges}


def colliders(edges: list) -> set:
    """비차폐 충돌자 a→c←b (a,b 비인접). Markov 동등성의 두 번째 불변량."""
    sk = skeleton(edges)
    out = set()
    for n in {b for _, b in edges}:
        for a, b in itertools.combinations(sorted(parents(edges, n)), 2):
            if frozenset((a, b)) not in sk:
                out.add((a, n, b))
    return out

# ── ADMG: 양방향 간선 ───────────────────────────────────────────────────
# 강한 인과 가정은 둘이다 - 계수 0(화살표 없음) **과 공분산 0**(양방향 호 없음).
# 우리는 후자를 표현할 문법이 없었다. 그래서 에이전트가 교란을 말하려면 측정 가능한
# 대리물을 CONFOUND 노드로 만들어야 했고, 이름 없는 공통원인은 **조용히 0 으로 선언**됐다.
#
# `A <-> B` 는 "미지의 공통원인"이다. 잠재 노드로 펼치면 기존 d-분리가 그대로 쓰인다:
#   A <-> B   ==>   _L#k -> A,  _L#k -> B   (_L 은 절대 조건집합에 못 들어간다)
_LAT = "_L#"


def split(edges: list) -> tuple[list, list]:
    """간선을 (방향, 양방향) 으로 나눈다. dict 든 튜플이든 받는다."""
    d, b = [], []
    for e in edges:
        if isinstance(e, dict):
            pair = (e.get("from"), e.get("to"))
            (b if e.get("kind") == "bidirected" else d).append(pair)
        else:
            d.append(tuple(e))
    return d, b


def expand(dir_e: list, bi_e: list) -> list:
    """양방향을 잠재 공통원인으로 펼친다. 반환은 순수 DAG 간선."""
    out = list(dir_e)
    for k, (a, b) in enumerate(bi_e):
        L = f"{_LAT}{k}"
        out += [(L, a), (L, b)]
    return out


def msep(dir_e: list, bi_e: list, X: str, Y: str, Z: set) -> bool:
    """m-분리. 잠재는 조건집합에 넣을 수 없다 - 미관측이니까."""
    Z = {z for z in Z if not z.startswith(_LAT)}
    return dsep(expand(dir_e, bi_e), X, Y, Z)


def admg_backdoor_ok(dir_e: list, bi_e: list, X: str, Y: str, Z: set) -> tuple[bool, str]:
    """양방향을 인식하는 뒷문 판정.

    `X <-> Y` 가 있으면 X <- _L -> Y 경로를 관측변수로 막을 수 없다 ->
    **조정으로는 식별 불가.** 구성상 자동으로 거부된다. 그때는 IV·frontdoor 로 간다.
    """
    Z = set(Z)
    if X in Z or Y in Z:
        return False, "조정집합에 X 또는 Y 가 들어 있다"
    if any(z.startswith(_LAT) for z in Z):
        return False, "잠재변수를 조정할 수 없다 (미관측)"
    full = expand(dir_e, bi_e)
    bad = Z & (descendants(full, {X}) - {X})
    if bad:
        return False, f"X 의 후손을 조정했다: {sorted(bad)}"
    cut = [(a, b) for a, b in full if a != X]
    if not dsep(cut, X, Y, Z):
        direct = any({a, b} == {X, Y} for a, b in bi_e)
        return False, ("X <-> Y 미지의 공통원인이 있다 - **조정으로는 식별 불가.** "
                       "IV 나 frontdoor 를 쓰거나 불가를 선언해라" if direct
                       else "뒷문 경로가 안 막혔다")
    return True, "ok"


def admg_minimal_backdoor(dir_e: list, bi_e: list, X: str, Y: str, pool: set) -> list:
    full = expand(dir_e, bi_e)
    cand = sorted(z for z in pool - {X, Y} - (descendants(full, {X}) - {X})
                  if not z.startswith(_LAT))
    out = []
    for k in range(0, min(len(cand), 4) + 1):
        for Z in itertools.combinations(cand, k):
            if admg_backdoor_ok(dir_e, bi_e, X, Y, set(Z))[0]:
                out.append(sorted(Z))
                if len(out) >= 3:
                    return out
        if out:
            return out
    return out


def iv_candidates(dir_e: list, bi_e: list, X: str, Y: str, pool: set) -> list:
    """X→Y 의 도구변수 후보. **배제제약을 알고리즘으로 열거한다.**

    조건 (Brito-Pearl 2002 의 단순형):
      1. Z 가 X 에 방향경로를 갖는다 (관련성)
      2. `X→Y` 를 지운 그래프에서 Z 가 Y 와 m-분리된다 (배제)
    조건 2 가 곧 "Z 는 X 를 통하지 않고 Y 에 닿지 않는다" 이고, IV 의 전부다.
    """
    cut = [(a, b) for a, b in dir_e if (a, b) != (X, Y)]
    anc_x = ancestors(dir_e, {X}) - {X}
    out = []
    for z in sorted(pool - {X, Y}):
        if z.startswith(_LAT) or z not in anc_x:
            continue
        if msep(cut, bi_e, z, Y, set()):
            out.append(z)
    return out




def descendants(edges: list, S: set) -> set:
    return ancestors([(b, a) for a, b in edges], S)


def backdoor_ok(edges: list, X: str, Y: str, Z: set) -> tuple[bool, str]:
    """Z 가 (X,Y) 뒷문 기준을 만족하나. **모델의 그래프 위에서 코드가 판정한다.**

    1. Z 에 X 의 후손이 없다 (있으면 사후변수 조건화)
    2. X 에서 나가는 간선을 지운 그래프에서 Z 가 X 와 Y 를 d-분리한다
    """
    Z = set(Z)
    if X in Z or Y in Z:
        return False, "조정집합에 X 또는 Y 가 들어 있다"
    bad = Z & (descendants(edges, {X}) - {X})
    if bad:
        return False, f"X 의 후손을 조정했다: {sorted(bad)}"
    cut = [(a, b) for a, b in edges if a != X]      # X 에서 나가는 간선 제거
    if not dsep(cut, X, Y, Z):
        return False, "뒷문 경로가 안 막혔다"
    return True, "ok"


def minimal_backdoor(edges: list, X: str, Y: str, pool: set) -> list:
    """뒷문을 막는 최소 조정집합들 (크기 오름차순, 최대 3개)."""
    cand = sorted(pool - {X, Y} - (descendants(edges, {X}) - {X}))
    out = []
    for k in range(0, min(len(cand), 4) + 1):
        for Z in itertools.combinations(cand, k):
            if backdoor_ok(edges, X, Y, set(Z))[0]:
                out.append(sorted(Z))
                if len(out) >= 3:
                    return out
        if out:
            return out
    return out


# ── 함의 조건부독립 ────────────────────────────────────────────────────
def implied_ci(nodes: dict, edges: list, testable_only: bool = True) -> set:
    """함의 조건부독립 기저.

    비인접 쌍 (X,Y) 마다  X ⊥ Y | pa(X) ∪ pa(Y)  - DAG 의 유효한 기저다.
    `testable_only` 면 **관측 변수만 든 함의**만 남긴다. 잠재 매개를 조건집합에
    넣어야 하는 함의는 데이터로 검정할 수 없으므로 반증 표면이 아니다.
    """
    obs = {n for n, m in nodes.items() if (m or {}).get("observed")}
    sk, out = skeleton(edges), set()
    ns = sorted(nodes)
    for X, Y in itertools.combinations(ns, 2):
        if frozenset((X, Y)) in sk:
            continue
        Z = (parents(edges, X) | parents(edges, Y)) - {X, Y}
        if not dsep(edges, X, Y, Z):
            continue                       # 기저가 성립 안 하면 함의가 아니다
        if testable_only and not ({X, Y} | Z) <= obs:
            continue
        out.add((X, Y, frozenset(Z)))
    return out


def fmt_ci(ci: tuple) -> str:
    X, Y, Z = ci
    return f"{X} ⊥ {Y}" + (f" | {', '.join(sorted(Z))}" if Z else "")


def _edges(s: dict) -> list:
    return [(e["from"], e["to"]) for e in s.get("edges") or []]


# ── 검증 1~8 ──────────────────────────────────────────────────────────
def validate(dag: dict, scope: dict | None = None, onset: str = "INTRADAY",
             grounded: set | None = None, *, require_competing: bool = True) -> list[str]:
    """규칙 1~8. 어긴 것을 전부 돌려준다 - 하나 잡고 멈추지 않는다."""
    bad: list[str] = []
    nodes = dag.get("nodes") or {}
    structs = dag.get("structures") or []
    grounded = grounded if grounded is not None else set()

    # 규칙 0 · 1 - 시간 색인. **종별 검사는 없앴다.**
    #
    # 노드가 무엇인지는 `says`(자연어)가 말하고, 관측 가능성만 `observed` 로 받는다.
    # 종별 6종을 열거하면 모델은 자기가 세우려는 노드를 그 6칸에 억지로 넣는다 -
    # 사슬의 매개(회계 항목·기대·수급·경쟁 반응)는 그 칸에 안 맞는 것이 대부분이다.
    off: dict = {}
    for n, m in nodes.items():
        if not isinstance(m, dict):
            bad.append(f"{n}: 노드 명세가 dict 가 아니다 - {type(m).__name__}")
            continue
        try:
            _, off[n] = parse(n)
        except ValueError as e:
            bad.append(str(e))

    all_edges = [(e, s) for s in structs for e in (s.get("edges") or [])]
    for e, s in all_edges:
        a, b = e.get("from"), e.get("to")
        tag = f"{s.get('id')}·{a}→{b}"
        if a not in nodes or b not in nodes:
            bad.append(f"{tag}: nodes 에 선언 안 됨")
            continue
        # 규칙 1 - 시간 선행. 이 한 줄이 비순환을 보장한다
        if off.get(a, 0) > off.get(b, 0):
            bad.append(f"{tag}: 시간 역행 (t{off[a]} → t{off[b]}). 원인이 결과보다 늦다")
        # 규칙 6 - **시점 외생성.** 개입(조작가능성) 열거형을 대체한다.
        #
        # 이전 판은 `intervention: presence|magnitude|timing|none` 을 요구하고
        # `none` 이면 통계 논증 자격을 뺏었다. 그게 "no causation without
        # manipulation" 이고 Bollen-Pearl Myth 3 이 기각한다 - 인과의 본질은
        # **responsiveness** 이고, 변이가 어떻게 생겼는지는 상관없다. 커버리지 여부·
        # 산업·규모 같은 속성도 원인일 수 있다.
        #
        # 남겨야 하는 건 조작가능성이 아니라 **역인과**다: 애널리스트는 가격을 보고
        # 쓴다. 그건 식별에 직결되고 검사 가능한 열거형이다.
        tm = e.get("timing")
        if tm not in ("scheduled", "unscheduled", "price_responsive", "n/a"):
            bad.append(f"{tag}: timing 이 없거나 잘못됨 "
                       "(scheduled|unscheduled|price_responsive|n/a). "
                       "원인의 발생 시점이 결과에 대해 외생인가")
        # CONJECTURE 는 게시 간선을 가질 수 없다 → **삭제.** 노드 종별 열거를 없앴다.
        # 저장소 밖의 노드를 결과에 직접 잇는 것을 금지하던 규칙인데, 이제 그 자리는
        # 예산이 잡는다 - 못 잰 간선은 예측이 없어 설명 몫을 못 가져가므로 문장에도
        # 나오지 않는다. 금지 규칙 하나가 산술 하나로 대체된다.

    # 규칙 2 - onset 게이트. 구성원 노드는 **코드가 준 scope** 로 알아낸다
    # (모델의 종별 선언에 의존하지 않는다 - 선언을 없앴다).
    if onset == "GAP_OPEN":
        keys = {m["key"] for m in ((scope or {}).get("members") or [])}
        targets = {n for n in nodes if _name(n) in keys}
        for e, s in all_edges:
            if e.get("from") in targets and e.get("to") in targets:
                bad.append(f"{s.get('id')}·{e['from']}→{e['to']}: GAP_OPEN 은 구성원 간 "
                           "방향 간선 금지 - 동시호가 안에 관측이 0개다")

    # 규칙 5 - 접지. **`events` 를 적은 노드는 실재하는 사건을 가리켜야 한다.**
    #
    # 이전 판은 `kind == "SHOCK"` 인 노드에 이걸 요구했다. 종별 열거를 없앴으므로
    # 기준을 선언에서 내용으로 옮긴다 - 사건을 참조한 노드만 검사하면 되고, 사건이
    # 아닌 원인(수급·거시·경쟁 반응)은 종별을 고를 필요 없이 그냥 노드가 된다.
    n_grounded = 0
    for n, m in nodes.items():
        ev = (m or {}).get("events") or []
        if not ev:
            continue
        n_grounded += 1
        for x in ev:
            if grounded and x not in grounded:
                bad.append(f"{n}: events {x!r} 접지 실패 - 실재하지 않는 event_id")
    if grounded and not n_grounded:
        bad.append("사건에 접지된 노드가 없다 - 브리프의 event_id 를 참조하는 노드가 "
                   "하나는 있어야 이 셀의 설명이 저장소와 이어진다")

    # 규칙 3·4·6b 삭제. **노드 종별 열거(KINDS)를 없앤 대가로 함께 나갔다.**
    #
    #   3  가격 노드끼리 이으면 MARKET 을 부모로  → 요인 분해가 상류에서 이미 끝난다.
    #      결과는 시장·피어 제거 후 잔차이므로 그래프가 다시 시장을 그릴 이유가 없다
    #   4  TARGET 보존                            → 결론 노드는 `Proposal.target` 하나다
    #   6b 잠재 매개에 CDE/NDE/NIE 선언 강제       → 간선 유형(identity·elasticity·
    #      statistical)이 증명 양식을 정하므로 효과 어휘를 따로 받을 필요가 없다
    #
    # 셋 다 "무엇을 어떤 이름으로 부르라"는 요구였고, 사슬을 세우는 데 기여하지 않으면서
    # 모델의 주의를 어휘 맞추기로 끌어갔다. 순 제약 수는 줄었다.

    # 규칙 7 - 예산
    lo, hi = LIMITS["structures"]
    # 경쟁 구조 2~3개 요구는 **발견 루프의 프로토콜**이다 - 판별기준(함의 대칭차집합)을
    # 계산하려면 비교할 둘이 있어야 한다. 일일 게시 경로는 검정을 통과한 그래프 하나를
    # 내므로 그 규칙이 적용되지 않는다. 간선 단위 검정은 양쪽에서 똑같이 돈다.
    if require_competing and not (lo <= len(structs) <= hi):
        bad.append(f"구조 {len(structs)}개 - {lo}~{hi} 여야 한다")
    for s in structs:
        ed = _edges(s)
        if len(ed) > LIMITS["edges"]:
            bad.append(f"{s.get('id')}: 간선 {len(ed)} > {LIMITS['edges']}")
        for n in {a for a, _ in ed}:
            f = len([1 for a, _ in ed if a == n])
            if f > LIMITS["fanout"]:
                bad.append(f"{s.get('id')}·{n}: fanout {f} > {LIMITS['fanout']}")
    return bad


def report(dag: dict, **kw) -> str:
    """검증 + 계산된 반증 표면을 한 장으로."""
    nodes, structs = dag.get("nodes") or {}, dag.get("structures") or []
    L = []
    bad = validate(dag, **kw)
    L.append(f"[검증] {'통과' if not bad else f'위반 {len(bad)}건'}")
    L += [f"    - {b}" for b in bad]

    L.append("\n[반증 표면] 그래프에서 계산 - 손으로 쓰지 않는다")
    for s in structs:
        ci = implied_ci(nodes, _edges(s))
        allci = implied_ci(nodes, _edges(s), testable_only=False)
        L.append(f"  {s.get('id')}: 검정가능 함의 {len(ci)}개 (전체 {len(allci)}개)")
        for c in sorted(ci, key=str):
            L.append(f"      {fmt_ci(c)}")
        if not ci:
            L.append("      **없음 - 이 구조는 관측으로 반증할 수 없다.** "
                     "잠재노드를 접거나 관측을 추가해라")
    return "\n".join(L)


