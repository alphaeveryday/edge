"""P4 · 식별 또는 경계 — **3값이다. 빈 조정집합은 성공이 아니다.**

이전 구조는 `adjustment | iv | none` 이었고 `adjust=[]` 가 무조건 성공으로 읽혔다. 그
빈 집합은 "뒷문이 없다"가 아니라 "교란을 안 그렸다"였다 - 안 그린 간선과 없는 관계가
같은 표현(부재)으로 붙었기 때문이다. 실측으로 확인됐다: 같은 셀에서 그래프에 `MOM@t-1`
한 줄을 더 그리기만 해도 `adjust=[]` → `adjust=['MOM@t-1']` 로 바뀐다. 그때의
`identify()` 는 세계가 아니라 **제안자의 지식 상태**를 보고하고 있었다.

3값이 그 자리를 메운다.

    identified        조정으로 뒷문이 닫힌다. 가정은 그린 그래프뿐이다
    identified_under  가정 A 를 더하면 닫힌다. **A 를 문장으로 적어 들고 다닌다**
    not_identified    닫히지 않는다. 무엇이 막는지 `Latent.uid` 로 적는다 - 정상 종료다

`not_identified` 가 정상 종료인 이유: 점식별 실패는 답이 없다는 뜻이 아니다. 유계 가정
아래의 Manski 구간은 언제나 있고 그 폭 자체가 정보다(넓으면 주장이 약하다는 정보다).
그래서 실패를 예외로 던지지 않고 `bounds` 를 채워 다음 단계로 내려보낸다.

빈 조정집합은 이제도 나온다 - 다만 뜻이 다르다. P3 가 배정 기제에서 U 를 컴파일해 심고
공통원인 완비를 선언한 뒤(`WorldGraph.completeness`)의 빈 집합이므로 "그릴 의무를 다한
그래프에 뒷문이 없다"는 진술이다. 선언도 U 도 없는 그래프의 빈 집합은 옛 체제와 구별할
수 없으므로 `identified_under` 로 내린다.

모델의 말을 그대로 믿는 자리가 없다. `Latent.blocked_by` 는 제안이고 성립 여부는
`graph.msep`·`graph.admg_backdoor_ok` 로 코드가 판정한다 - 모델의 뒷문 정답률이 실측
78% 인데 코드는 구성상 100% 라는 `verify.py` 의 이유가 여기도 그대로 적용된다.
"""
from __future__ import annotations

import math

from ..config import PipelineError
from ..observability import log
from . import graph as G
from .contracts import Hypothesis, Identification, Latent, WorldGraph

_NO_SUPPORT = ("유계 가정 없이는 경계가 무한하다 — 타입 지지집합 필요. 결과가 초과수익"
               "(연속·유계 아님)이므로 지지집합을 주지 않으면 worst-case 가 (-inf, inf) 다. "
               "이 도메인에서 방어 가능한 유계 가정은 하나뿐이다: 그 사건 타입의 과거 "
               "|초과수익| 최대(`CausalData.prior(...).abs_max`).")
_NO_DECL = ("빈 조정집합인데 그래프에 U 도 완비 선언도 없다 - '뒷문이 없다'와 '아무도 "
            "안 그렸다'가 구별되지 않는다. Hernán-Robins 완비 선언이 있어야 빈 집합이 "
            "진술이 된다. 선언을 채우면 `identified` 로 올라간다.")


def _pool(g: WorldGraph) -> set[str]:
    """조정 후보. **`observed` 가 비면 후보가 아니다 - 조건화할 열이 없다.**

    저장소 규약대로 `observed` 는 "어떻게 관측하나" 문장이고 비었으면 잠재다
    (`graph.implied_ci`·`run.py` 와 같은 판정을 쓴다). 빠진 키를 관측으로 봐주면 잠재를
    조정집합에 넣은 결과가 통과하는데, 그건 이 단계가 걷어내려는 부정직과 같은 종류다.
    """
    return {n for n, m in g.nodes.items() if (m or {}).get("observed")}


def _blockers(dir_e: list, bi_e: list, latents: list[Latent], src: str, dst: str,
              pool: set[str]) -> list[str]:
    """조정을 막는 U 를 **실제로 찾는다** - 이름으로 짐작하지 않는다.

    두 방향으로 묻는다. 하나를 빼서 조정이 열리면 그 U 가 필요조건이고(단독 범인), 그
    U 만 남겨도 여전히 막히면 충분조건이다(공범). 둘 다 보는 이유: U 가 둘 이상이면
    하나를 빼도 남은 하나가 계속 막으므로 전자만으로는 아무도 못 잡고 `blocked_by` 가
    빈 채로 나간다 - 그러면 P5 가 소거할 대상을 못 받고 P8 이 미소거를 못 적는다.

    U 를 전부 빼도 조정이 안 되면(`bare` 가 비면) 범인은 U 가 아니라 그린 방향간선
    구조다. 그때 아무 U 도 지목하지 않는 것이 맞다 - 없는 죄를 씌우면 P5 가 소거될 수
    없는 검정을 설계한다.
    """
    if not bi_e:
        return []
    bare = bool(G.admg_minimal_backdoor(dir_e, [], src, dst, pool))
    out: list[str] = []
    for i, u in enumerate(latents):
        without = [e for j, e in enumerate(bi_e) if j != i]
        alone = [bi_e[i]]
        if G.admg_minimal_backdoor(dir_e, without, src, dst, pool):
            out.append(u.uid)
        elif bare and not G.admg_minimal_backdoor(dir_e, alone, src, dst, pool):
            out.append(u.uid)
    return out


def _asm_latent(u: Latent, src: str, zs: list[str]) -> str:
    return (f"{u.uid} ⊥ {src} | {{{', '.join(zs)}}} — 이 U 의 작용이 그 관측집합을 경유"
            f"한다는 모델 제안이고, 그 가정 아래 뒷문이 닫히는 것은 코드가 확인했다"
            + (f" ({u.says})" if u.says else ""))


def _asm_iv(z: str, src: str, dst: str) -> str:
    return (f"배제제약: {z} 는 {src} 를 통하지 않고 {dst} 에 닿지 않는다. 그린 그래프 위"
            f"에서는 열거로 확인됐고(Brito-Pearl 2002), 남는 가정은 그래프가 {z}–{dst} "
            f"의 미관측 경로를 빠뜨리지 않았다는 것이다. {z} 의 관련성(1단계 강도)은 "
            "여기서 재지 않는다 - 검정은 P5 의 몫이다.")


def _promote(dir_e: list, bi_e: list, latents: list[Latent], src: str, dst: str,
             pool: set[str], blockers: list[str]) -> tuple[list[str], list[str]]:
    """`Latent.blocked_by` 를 **코드가 검증한다.** 통과하면 (조정집합, 가정문장들).

    세 관문을 둔 이유는 하나다 - 이름만 그럴듯한 제안이 승격을 얻으면 3값이 다시 2값이
    되고, `identified_under` 는 `identified` 의 완곡어법이 된다.

      1. 제안한 이름이 그래프의 **관측** 노드여야 한다. 없는 이름은 주장이 아니다
      2. 그 U 들을 지운 그래프에서 그 집합이 뒷문을 실제로 막아야 한다. `msep` 이 아니라
         `admg_backdoor_ok` 를 쓰는 이유: 후자는 X 후손 조건화(사후변수·충돌자) 금지까지
         본다. 뒷문 그래프에서는 X 의 후손이 끊기므로 m-분리만 보면 그 위반이 통과한다
      3. 그 집합이 일을 해야 한다. U 를 지우기만 해도 이미 분리돼 있으면(`msep`) 제안은
         검증된 것이 아니라 무관한 것이다 - 그래프에 있는 아무 노드나 적어 넣으면
         승격되는 구멍이 여기서 막힌다

    이 단계의 대상은 **이 쌍을 막고 있는 U 로 한정한다**(`blockers`). 다른 자리의 U 를
    이 쌍의 가정으로 끌어와 지우면, 지우지도 않은 교란을 지웠다고 적게 된다.
    """
    claims = [(i, u) for i, u in enumerate(latents)
              if u.uid in blockers and u.blocked_by and set(u.blocked_by) <= pool]
    if not claims:
        return [], []
    zs = sorted({z for _, u in claims for z in u.blocked_by})
    assumed = {i for i, _ in claims}
    rest = [e for i, e in enumerate(bi_e) if i not in assumed]
    if not G.admg_backdoor_ok(dir_e, rest, src, dst, set(zs))[0]:
        return [], []
    cut = [(a, b) for a, b in dir_e if a != src]      # 뒷문 그래프
    if G.msep(cut, rest, src, dst, set()):
        return [], []                                 # 조건화 없이도 분리 - 제안이 무관하다
    return zs, [_asm_latent(u, src, zs) for _, u in claims]


def _bounds(support: tuple[float, float] | None,
            p_treated: float | None) -> tuple[tuple[float, float] | None, str]:
    """유계 가정 아래의 worst-case 구간. **점식별 실패가 종료가 아니라는 것의 실체다.**

    계산하는 것: Y(1) 과 Y(0) 가 둘 다 [yl, yh] 에 있으면 그 차는 [yl-yh, yh-yl] 안에
    있다. 지지집합 가정 하나만으로 나오는 유효한 경계이고, 이 단계가 받는 입력으로는 더
    좁힐 수 없다.

    계산하지 않는 것: 표준 Manski 경계(폭 yh-yl, 위 폭의 절반)다. 그 식은 관측 조건부
    평균이 **함께** 있어야 성립한다 - 하한이
    `p·E[Y|T=1] + (1-p)·yl - (1-p)·E[Y|T=0] - p·yh` 이고, P4 의 계약에는 E[Y|T=t] 가
    없다. `p_treated` 만 들고 절반 폭을 내면 그건 계산이 아니라 날조다. 그래서 좁히지
    않고, 무엇이 있으면 좁혀지는지를 `bounds_note` 에 적어 P6·P8 로 넘긴다.

    폭이 배정확률과 무관한 것은 이 경계의 성질이다 - p 는 관측 평균과 짝이 될 때만 폭을
    깎는다. 넓은 구간을 숨기지 않는 것이 `chain.Interval` 과 같은 정직성 장치다.
    """
    if support is None:
        return None, _NO_SUPPORT
    lo, hi = float(support[0]), float(support[1])
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
        raise PipelineError(f"지지집합이 구간이 아니다: [{support[0]}, {support[1]}]")
    note = (f"유계 가정: 결과 지지집합 [{lo:+.4f}, {hi:+.4f}] (그 사건 타입의 과거 "
            f"|초과수익| 최대). Y(1)·Y(0) 가 이 안에 있으면 그 차는 "
            f"[{lo - hi:+.4f}, {hi - lo:+.4f}] 안에 있다 - 지지집합 하나로 나오는 "
            "worst-case 다. 표준 Manski 경계는 폭이 이것의 절반이지만 관측 조건부 평균 "
            "E[Y|T=t] 가 있어야 나오고, P4 는 그것을 받지 않는다 - 없는 입력으로 좁은 "
            "수를 내지 않는다.")
    if isinstance(p_treated, (int, float)) and math.isfinite(p_treated):
        note += f" 배정확률 p={float(p_treated):.4f} 는 그 좁히기의 남은 절반이다."
    return (lo - hi, hi - lo), note


def identify(g: WorldGraph, src: str, dst: str, *, support: tuple[float, float] | None = None,
             p_treated: float | None = None) -> Identification:
    """간선 하나의 식별 상태. **판정 순서가 곧 주장의 강도 순서다.**

    조정 → `blocked_by` 승격 → 도구변수 → 불가. 조정이 되면 가정은 그래프뿐이므로 가장
    강하고, 뒤로 갈수록 문장으로 적어야 하는 가정이 늘어난다. 순서를 뒤집으면(IV 를 먼저
    찾으면) 조정으로 끝날 자리에 배제제약을 얹게 된다 - 공짜로 약해진다.

    승격에 성공하면 도구변수를 찾지 않는다. 한 간선에 두 경로를 동시에 적으면 P5 가
    어느 가정을 소거해야 하는지 모른다 - 경로는 하나만 고르고 나머지는 다음 셀의 몫이다.

    노드가 그래프에 없는 쌍을 막는 이유: 빈 그래프에서 d-분리는 자동 성립하므로
    `admg_minimal_backdoor` 가 빈 조정집합을 돌려주고, 없는 노드가 `identified` 로 나온다.
    """
    dir_e, bi_e, pool = g.directed, g.bidirected, _pool(g)
    if g.nodes and not pool:
        log("causal.p4.no_pool", n_nodes=len(g.nodes))
    adjust: list[str] = []
    alts: list[list[str]] = []
    ivs: list[str] = []
    asm: list[str] = []
    blockers: list[str] = []
    if src not in g.nodes or dst not in g.nodes:
        status, bounds = "not_identified", None
        note = (f"{src}→{dst}: 한쪽이 그래프의 노드가 아니다 - 판정할 구조가 없다. "
                "구조 없이 나오는 빈 조정집합은 식별이 아니라 빈 그래프의 성질이다.")
    else:
        zs = G.admg_minimal_backdoor(dir_e, bi_e, src, dst, pool)
        if zs and (zs[0] or g.latents or g.completeness.strip()):
            status, bounds, note = "identified", None, ""
            adjust, alts = list(zs[0]), [list(z) for z in zs[1:]]
        elif zs:
            status, asm = "identified_under", [_NO_DECL]
            bounds, note = _bounds(support, p_treated)
        else:
            blockers = _blockers(dir_e, bi_e, g.latents, src, dst, pool)
            adjust, asm = _promote(dir_e, bi_e, g.latents, src, dst, pool, blockers)
            if not adjust:
                ivs = G.iv_candidates(dir_e, bi_e, src, dst, pool)
                asm = [_asm_iv(z, src, dst) for z in ivs]
            status = "identified_under" if (adjust or ivs) else "not_identified"
            bounds, note = _bounds(support, p_treated)
    log("causal.p4.edge", src=src, dst=dst, status=status, adjust=len(adjust),
        iv=len(ivs), blocked=len(blockers), bounded=bounds is not None)
    return Identification(src=src, dst=dst, status=status, adjust=adjust, alternatives=alts,
                          iv=ivs, blocked_by=blockers, assumptions=asm,
                          bounds=bounds, bounds_note=note)


def identify_all(g: WorldGraph, *, support: tuple[float, float] | None = None,
                 p_treated: float | None = None) -> list[Identification]:
    """가설마다 `treatment→outcome` 하나. **중복 쌍은 한 번만 - 같은 구조는 같은 답이다.**

    두 가설이 같은 쌍을 주장하면 식별은 구조만 보므로 답이 같다. 두 번 적으면 P8 이
    같은 간선을 두 후보로 세고 회계가 어긋난다.

    `support` 는 결과 노드의 지지집합이므로 한 셀 안에서 쌍마다 다르지 않다 - 그래서
    쌍별 사전이 아니라 값 하나를 받는다.
    """
    seen: set[tuple[str, str]] = set()
    out: list[Identification] = []
    for h in g.hypotheses:
        pair = (h.treatment, h.outcome)
        if not (h.treatment and h.outcome) or h.treatment == h.outcome or pair in seen:
            continue
        seen.add(pair)
        out.append(identify(g, *pair, support=support, p_treated=p_treated))
    log("causal.p4.done", n=len(out),
        identified=sum(1 for i in out if i.status == "identified"),
        under=sum(1 for i in out if i.status == "identified_under"),
        blocked=sum(1 for i in out if i.status == "not_identified"))
    return out


if __name__ == "__main__":
    EVT, AR, MOM, SCH = "EVT@t0", "AR@t0", "MOM@t-1", "SCHED@t-1"
    OBS = {"observed": "종가 기준"}

    def _g(nodes, edges, latents=(), pairs=((EVT, AR),), decl="공통원인 전수 선언") -> WorldGraph:
        return WorldGraph(
            nodes={n: dict(OBS, says=n) for n in nodes},
            edges=[{"from": a, "to": b} for a, b in edges],
            latents=list(latents), completeness=decl,
            hypotheses=[Hypothesis(hid=f"h{k}", says="", treatment=t, outcome=o,
                                   assignment="chosen") for k, (t, o) in enumerate(pairs)])

    U = Latent(uid="U#선택", between=(EVT, AR), says="사적 정보", source="compiled")

    # 1. U 가 하나 걸린 간선 - 조정으로 못 막는다. 범인을 uid 로 지목해야 한다.
    g1 = _g([EVT, AR], [(EVT, AR)], [U])
    i1 = identify(g1, EVT, AR)
    assert i1.status == "not_identified" and i1.blocked_by == ["U#선택"], i1
    assert not i1.point_identified and i1.bounds is None and "무한" in i1.bounds_note

    # 유계 가정을 주면 경계가 나온다. 폭은 지지집합 폭의 두 배다(관측 평균이 없으므로).
    i1b = identify(g1, EVT, AR, support=(-0.08, 0.08), p_treated=0.03)
    assert i1b.bounds == (-0.16, 0.16) and "p=0.0300" in i1b.bounds_note
    for bad in ((0.1, -0.1), (float("nan"), 0.1)):
        try:
            identify(g1, EVT, AR, support=bad)
        except PipelineError:
            pass
        else:
            raise AssertionError(f"구간이 아닌 지지집합을 통과시켰다: {bad}")

    # 2. blocked_by 가 실제로 뒷문을 닫으면 승격된다.
    ok = Latent(uid=U.uid, between=U.between, says=U.says, source="compiled",
                blocked_by=[MOM])
    both = [(EVT, AR), (MOM, EVT), (MOM, AR)]
    i2 = identify(_g([EVT, AR, MOM], both, [ok]), EVT, AR)
    assert i2.status == "identified_under" and i2.adjust == [MOM], i2
    assert i2.blocked_by == ["U#선택"] and f"U#선택 ⊥ {EVT} | {{{MOM}}}" in i2.assumptions[0]

    # 3. 헛소리는 승격되지 않는다 - 없는 이름도, 일 안 하는 이름도.
    ghost = Latent(uid=U.uid, between=U.between, says="", source="compiled",
                   blocked_by=["없는노드@t-9"])
    i3 = identify(_g([EVT, AR, MOM], both, [ghost]), EVT, AR)
    assert i3.status == "not_identified" and not i3.adjust and not i3.assumptions, i3

    # MOM 이 AR 에 닿지 않으면 MOM 은 이 쌍의 뒷문을 막는 일을 하지 않는다 - 승격 없음.
    # (그 그래프에서 MOM 은 EVT 의 도구변수로는 유효하므로 status 는 IV 경로로 올라간다.
    #  검사 대상은 status 가 아니라 **U 를 지웠다고 적지 않았다**는 것이다.)
    idle = Latent(uid=U.uid, between=U.between, says="", source="compiled", blocked_by=[MOM])
    i3b = identify(_g([EVT, AR, MOM], [(EVT, AR), (MOM, EVT)], [idle]), EVT, AR)
    assert not i3b.adjust and not any("⊥" in a for a in i3b.assumptions), i3b

    # 4. U 가 없으면 관측 교란을 조정해서 점식별된다.
    i4 = identify(_g([EVT, AR, MOM], both), EVT, AR)
    assert i4.status == "identified" and i4.adjust == [MOM] and i4.point_identified

    # 5. 빈 조정집합의 뜻은 완비 선언에 달려 있다. 선언이 없으면 옛 체제와 구별 불가다.
    i5 = identify(_g([EVT, AR], [(EVT, AR)]), EVT, AR)
    assert i5.status == "identified" and i5.adjust == []
    i5b = identify(_g([EVT, AR], [(EVT, AR)], decl="  "), EVT, AR)
    assert i5b.status == "identified_under" and "아무도" in i5b.assumptions[0], i5b

    # 6. 조정이 막히고 도구변수가 있으면 배제제약을 문장으로 들고 승격한다.
    i6 = identify(_g([EVT, AR, SCH], [(SCH, EVT), (EVT, AR)], [U]), EVT, AR)
    assert i6.status == "identified_under" and i6.iv == [SCH] and not i6.adjust
    assert "배제제약" in i6.assumptions[0] and i6.blocked_by == ["U#선택"]

    # 7. U 가 둘이면 하나씩 빼는 것만으로는 아무도 못 잡는다 - 공범도 지목한다.
    #    HIDDEN 은 미관측이라 조정집합에 못 들어가므로 EVT←U#경로→HIDDEN→AR 도 안 막힌다.
    #    U#선택 을 빼도 U#경로 가 계속 막고 그 역도 같다 - '하나 빼기'만 보면 둘 다 놓친다.
    g7 = WorldGraph(
        nodes={EVT: dict(OBS, says=EVT), AR: dict(OBS, says=AR),
               "HIDDEN@t0": {"says": "미관측 채널", "observed": None}},
        edges=[{"from": EVT, "to": AR}, {"from": "HIDDEN@t0", "to": AR}],
        latents=[U, Latent(uid="U#경로", between=(EVT, "HIDDEN@t0"), says="",
                           source="declared")],
        completeness="공통원인 전수 선언")
    i7 = identify(g7, EVT, AR)
    assert i7.status == "not_identified" and set(i7.blocked_by) == {"U#선택", "U#경로"}, i7

    # 8. 노드가 없는 쌍은 빈 그래프의 자동 d-분리를 타고 성공으로 나오면 안 된다.
    i8 = identify(_g([EVT, AR], [(EVT, AR)]), "없음@t0", AR)
    assert i8.status == "not_identified" and "노드가 아니다" in i8.bounds_note

    # 9. 같은 쌍을 두 가설이 주장해도 한 번만 판정한다.
    g9 = _g([EVT, AR, MOM], both, [U], pairs=((EVT, AR), (EVT, AR), (MOM, AR)))
    all9 = identify_all(g9, support=(-0.05, 0.05))
    assert [(i.src, i.dst) for i in all9] == [(EVT, AR), (MOM, AR)], all9
    assert all(i.bounds == (-0.1, 0.1) for i in all9 if not i.point_identified)

    print("p4_identify 자체 검사 통과")
