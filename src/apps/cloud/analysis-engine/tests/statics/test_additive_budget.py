"""가법 제약 — 폐기된 SEM 과대식별 검산의 대체를 계약으로 고정한다.

    모순 ⟺ Σₖ |ATTₖ| > |B| + 1.96·σ̂_ε

SEM 이 마지막으로 하던 일은 '구조 추정 구간이 항등식 상한을 넘으면 모형이
틀렸다' 는 공짜 반증이었다. 그것을 지우면서 같은 반증을 잃지 않으려면 대체
장치가 **실제로 실패할 수 있어야** 한다. 이 파일이 그 실패 가능성을 검사한다:

  (a) 합이 상한 안이면 모순이 아니다                    — 옳은 주장을 죽이지 않는다
  (b) 넘으면 모순이고 산문이 **초과 백분율**을 말한다   — 얼마나 틀렸는지 센다
  (c) 개별로는 전부 예산 안인데 합으로 모순             — SEM 의 구멍을 막았는가
  (d) σ̂_ε 를 못 재면 모순도 통과도 아닌 **미계산**     — 부재 ≠ 통과
"""
from datetime import datetime

import pytest

from edge_analysis.statics import Row, Share
from edge_analysis.statics.narrate import (
    ADDITIVE_Z, AdditiveBudget, Edge, NarrationError, additive_say, narrate)
from edge_analysis.statics.windows import Window

B = 0.010          # 그 층의 예산 (고유 +1.00%p)
SIG = 0.004        # σ̂_ε (0.40%p) → 상한 = 1.00 + 1.96×0.40 = 1.784%p


def _row(log_ret: float) -> Row:
    w = Window("잔여1", datetime(2026, 6, 1, 9), datetime(2026, 6, 1, 11),
               "residual", ())
    return Row(Share(w, log_ret))


def _say(a: AdditiveBudget) -> str:
    """산문 실물. narrate 를 거쳐야 '문단이 실제로 나오는가' 까지 검사된다."""
    return narrate(ticker="T", name="N", day="d", route=None, rows=[_row(B)],
                   grounded={}, additive=a)


# ── (a) 상한 안 = 모순 아니다 ───────────────────────────────────────────────
def test_sum_within_budget_is_not_a_contradiction():
    a = AdditiveBudget(claims=(("계약체결", 0.006), ("실적", -0.004)),
                       budget=B, sigma=SIG)
    assert a.ceiling == pytest.approx(abs(B) + ADDITIVE_Z * SIG)
    assert a.claimed == pytest.approx(0.010)     # 절댓값 합 - 부호 상쇄 없음
    assert a.verdict == "통과" and a.excess_pct is None
    txt = _say(a)
    assert "[가법]" in txt and "가법 제약 통과" in txt
    # 통과가 '크기가 맞다' 로 읽히면 그것도 과대주장이다.
    assert "예산과 모순은 아니다" in txt
    assert "인용 금지" not in txt


def test_absolute_sum_blocks_sign_cancellation():
    """+2%p 와 −2%p 를 같이 주장하면 순합은 0 이지만 예산은 4%p 를 쓴다.

    순합으로 재면 반대 부호 주장을 무한히 쌓아 예산 검산을 통째로 피할 수 있다.
    """
    a = AdditiveBudget(claims=(("A", 0.020), ("B", -0.020)), budget=B, sigma=SIG)
    assert a.claimed == pytest.approx(0.040) and a.verdict == "모순"


# ── (b) 넘으면 모순 + 초과 백분율 ──────────────────────────────────────────
def test_over_budget_is_contradiction_and_prose_states_the_excess():
    a = AdditiveBudget(claims=(("계약체결", 0.030),), budget=B, sigma=SIG)
    ceil = abs(B) + ADDITIVE_Z * SIG                      # 0.01784
    assert a.verdict == "모순"
    assert a.excess_pct == pytest.approx((0.030 / ceil - 1.0) * 100.0)
    txt = _say(a)
    # **몇 % 넘는지**가 산문에 있어야 한다 - '넘었다' 만으로는 크기를 모른다.
    assert f"{a.excess_pct:.1f}% 초과" in txt
    assert "과대식별 모순" in txt and "전부 인용 금지" in txt
    assert "Σ|ATT| = 3.00%p" in txt and "예산 상한 1.78%p" in txt
    assert "σ̂_ε=0.40%p" in txt                            # 상한의 재료를 숨기지 않는다


def test_contradiction_suppresses_channel_magnitude_quotes():
    """모순이면 [채널] 문단도 크기를 인용하지 못한다 — 한 산문 안의 자기모순 방지."""
    e = Edge(channel="P판가", event_type="T.X", verdict="성립", applied=True,
             iset_lo=0.0003, iset_hi=0.0128)
    bad = AdditiveBudget(claims=(("A", 0.030),), budget=B, sigma=SIG)
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=[_row(B)],
                  grounded={}, edges=(e,), additive=bad)
    assert "크기는 **보류** — 가법 제약 위반" in txt
    assert "식별집합 [" not in txt
    # 통과일 때는 같은 엣지가 크기를 말할 수 있다 - 금지가 무조건이면 검산이 아니다.
    ok = AdditiveBudget(claims=(("A", 0.004),), budget=B, sigma=SIG)
    txt2 = narrate(ticker="T", name="N", day="d", route=None, rows=[_row(B)],
                   grounded={}, edges=(e,), additive=ok)
    assert "식별집합 [" in txt2 and "가법 제약 위반" not in txt2


def test_additive_paragraph_precedes_the_channel_verdicts():
    """자격을 정하는 문단이 크기를 인용하는 문단보다 앞선다.

    뒤에 있으면 독자가 이미 숫자를 읽은 뒤에 금지를 만난다 - SEM 시절 블록이
    '기여 먼저, 모순 나중' 으로 찍혀 실제로 그렇게 읽혔다.
    """
    e = Edge(channel="P판가", event_type="T.X", verdict="성립", applied=True,
             iset_lo=0.0003, iset_hi=0.0128)
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=[_row(B)],
                  grounded={}, edges=(e,),
                  additive=AdditiveBudget(claims=(("A", 0.004),),
                                          budget=B, sigma=SIG))
    assert txt.index("[가법]") < txt.index("[채널]")


# ── (c) SEM 의 구멍: 개별 통과 · 합 모순 ───────────────────────────────────
def test_three_edges_each_half_budget_pass_alone_but_contradict_together():
    """엣지 3개가 각각 0.5B 를 주장한다. SEM 은 엣지마다 따로 [구간] ∩ (0,B] 를
    봤으므로 셋 다 통과했다 - 합 1.5B 가 예산의 1.5 배인데 아무도 반대하지 않았다.

    척도가 여기서 중요하다. 상한은 |B| + 1.96·σ̂_ε 이므로 **σ̂_ε 가 B 에 비해
    작아야** 합산 검사가 문다: 1.5B > B + 1.96σ̂ ⟺ σ̂ < 0.255·B. 셀은 큰 이상
    수익으로 선정되므로(고유 +8%p 급) 그 조건이 실제로 성립한다 - 아래 숫자는
    KRX 대형주의 60일 ar_ind 표준편차 규모(1.8%p)를 그대로 쓴다.

    반대로 B 가 σ̂ 급으로 작은 조용한 날에는 이 검산이 물지 않는다. 그건 결함이
    아니라 설계다 - 그런 날의 예산은 잡음과 구별되지 않으므로 어떤 크기 주장도
    이미 반증되지 않는다. **못 가르는 것을 가른 척하지 않는다.**
    """
    big_b, big_sig = 0.080, 0.018          # 고유 +8.00%p · σ̂_ε 1.80%p
    half = 0.5 * big_b                     # 각 엣지 +4.00%p
    each = [AdditiveBudget(claims=((n, half),), budget=big_b, sigma=big_sig)
            for n in ("A", "B", "C")]
    assert [x.verdict for x in each] == ["통과", "통과", "통과"]  # 개별로는 예산 안
    for x in each:
        assert x.claimed <= abs(big_b)                 # SEM 의 개별 교차도 통과한다

    together = AdditiveBudget(claims=(("A", half), ("B", half), ("C", half)),
                              budget=big_b, sigma=big_sig)
    assert together.claimed == pytest.approx(1.5 * big_b)
    assert together.verdict == "모순"                  # 합에서 죽는다
    txt = additive_say(together)
    assert "주장 3건" in txt and "전부 인용 금지" in txt
    # 어느 것이 거짓인지 못 가른다는 사실을 숨기면 독자가 하나만 버린다.
    assert "어느 것인지는 이 검산이 못 가른다" in txt


def test_noise_slack_is_the_constraints_known_ceiling():
    """σ̂_ε 가 예산에 비해 크면 합산 검사가 무력해진다 - 알려진 한계를 고정한다.

    SEM 의 개별 교차는 |ATT| ≤ |B| 를 엣지마다 요구했으니 이 구간에서는 **SEM 이
    더 엄격했다**. 그 대가로 SEM 은 기울기를 수준으로 읽는 오독원이었고, 그래서
    바꿨다. 이 검사는 그 교환을 문서가 아니라 코드로 남긴다.
    """
    quiet = AdditiveBudget(claims=(("A", 0.014),), budget=0.004, sigma=0.018)
    assert quiet.claimed > abs(quiet.budget)   # SEM 의 개별 교차라면 모순이었다
    assert quiet.verdict == "통과"             # 가법 제약은 잡음 안이라 못 문다


def test_single_claim_at_full_budget_still_passes_within_noise():
    """참 효과의 합이 예산과 같아도 실현치 잡음 때문에 조금 넘을 수 있다.

    1.96·σ̂_ε 를 얹지 않으면 옳은 주장이 절반쯤 모순으로 기각된다 - 상한이
    실현치 하나에만 매달리는 것을 막는 항이다.
    """
    a = AdditiveBudget(claims=(("A", B * 1.2),), budget=B, sigma=SIG)
    assert a.verdict == "통과"                          # 예산의 1.2 배지만 잡음 안
    tight = AdditiveBudget(claims=(("A", B * 1.2),), budget=B, sigma=0.0)
    assert tight.verdict == "모순"                      # σ̂=0 이면 예산이 곧 상한


# ── (d) 못 재면 미계산 ─────────────────────────────────────────────────────
def test_unmeasured_sigma_is_neither_contradiction_nor_pass():
    a = AdditiveBudget(claims=(("A", 0.030),), budget=B, sigma=None,
                       reason="ar_ind 표본 12 < 40 - σ̂_ε 가 표본잡음이다")
    assert a.verdict == "미계산" and a.ceiling is None and a.excess_pct is None
    txt = _say(a)
    assert "가법 제약 **미계산**" in txt
    assert "부재는 통과가 아니다" in txt
    assert a.reason in txt                              # 사유 = 백필 좌표
    # 통과·모순 어느 쪽으로도 위장되지 않는다.
    assert "가법 제약 통과" not in txt and "과대식별 모순" not in txt


def test_unmeasured_budget_is_also_uncomputed():
    a = AdditiveBudget(claims=(("A", 0.030),), budget=None, sigma=SIG,
                       reason="고유 예산 미계측 (ar_ind 없음)")
    assert a.verdict == "미계산"
    assert "미계산" in _say(a)


def test_uncomputed_without_reason_dies_at_generation():
    """사유 없는 미계산은 침묵이고, 침묵은 통과처럼 읽힌다 — 생성 시점에 죽는다."""
    with pytest.raises(NarrationError, match="사유가 없다"):
        additive_say(AdditiveBudget(claims=(("A", 0.03),), budget=B, sigma=None))


def test_zero_ceiling_contradiction_says_so_without_dividing_by_zero():
    """예산 0 · σ̂ 0 인데 주장이 있으면 초과 비율이 없다 - 그래도 모순이다."""
    a = AdditiveBudget(claims=(("A", 0.001),), budget=0.0, sigma=0.0)
    assert a.verdict == "모순" and a.excess_pct is None
    assert "상한이 0%p 인데 주장이 있다" in additive_say(a)


def test_no_claims_means_no_paragraph():
    """주장이 없으면 검산할 것도 없다 - 셀 러너가 additive=None 을 넘긴다."""
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=[_row(B)],
                  grounded={})
    assert "[가법]" not in txt


# ── 예산을 쓰는 것은 인용되는 주장뿐 (attribute.run_cell 의 수확 규칙) ──────
def test_only_credible_implications_consume_the_budget():
    """접힌 함의(사전추세·균형 실패)는 산문에 안 나가므로 예산도 쓰지 않는다.

    전부 세면 폐기된 주장이 살아 있는 주장을 모순으로 만든다 - 검산이 자기가
    이미 버린 것에 발목을 잡는다. `run_cell` 이 이 규칙으로 claims 를 모은다.
    """
    from edge_analysis.statics.verifier import Implication

    ok = Implication("CONTRACT.SIGNING(MOU) 가 고유층 수익을 +1.130%p 움직였다",
                     0.0113, 0.001, 120, None, "통과", True, True)
    folded = Implication("CONTRACT.SIGNING(확정) 가 고유층 수익을 +9.000%p 움직였다",
                         0.090, 0.001, 120, None, "통과", False, True)
    assert ok.credible and not folded.credible

    # run_cell 의 수확 규칙 그대로: credible + att 가 있는 것만, 이름은 문장 머리.
    harvested = [(i.claim.split(" 가 ", 1)[0][:40], i.att)
                 for i in (ok, folded) if i.credible and i.att is not None]
    assert harvested == [("CONTRACT.SIGNING(MOU)", 0.0113)]

    a = AdditiveBudget(claims=tuple(harvested), budget=B, sigma=SIG)
    assert a.verdict == "통과"                  # 접힌 +9.00%p 를 세면 모순이었다
    assert "CONTRACT.SIGNING(MOU) +1.13%p" in additive_say(a)


# ── σ̂_ε 측정: 창은 당일 제외, 얇으면 부재 선언 ─────────────────────────────
def test_resid_sigma_excludes_today_and_declares_thin_samples():
    """검산이 검산 대상에게 매수당하지 않는다: 오늘의 큰 충격이 σ̂ 를 부풀려
    상한을 저절로 넓히면 그 상한은 아무것도 반증하지 못한다.
    """
    from edge_analysis.statics.attribute import (
        SIGMA_MIN_N, SIGMA_N, resid_sigma)

    class Lake:
        """`ar_ind` 창 쿼리만 받는다. SQL 이 당일을 배제하는지 문자열로 확인한다."""
        def __init__(self, sigma, n):
            self.sigma, self.n, self.q = sigma, n, ""

        def sql(self, q):
            self.q = q
            return [(self.sigma, self.n)]

    lake = Lake(0.0041, SIGMA_N)
    s, why = resid_sigma(lake, "i0", "2026-06-01")
    assert s == pytest.approx(0.0041) and why == ""
    assert "trade_date < DATE '2026-06-01'" in lake.q      # **당일 제외**
    assert f"LIMIT {SIGMA_N}" in lake.q

    s2, why2 = resid_sigma(Lake(0.0041, SIGMA_MIN_N - 1), "i0", "2026-06-01")
    assert s2 is None and str(SIGMA_MIN_N) in why2         # 얇으면 판정불가

    s3, why3 = resid_sigma(Lake(None, 0), "i0", "2026-06-01")
    assert s3 is None and "백필" in why3                   # 부재는 사유와 함께

    class Broken:
        def sql(self, q):
            raise RuntimeError("no ar_ind column")

    s4, why4 = resid_sigma(Broken(), "i0", "2026-06-01")
    assert s4 is None and "RuntimeError" in why4          # 실패도 사유다


def test_sem_symbols_are_gone_from_the_source():
    """폐기가 **삭제**여야 한다 - 남아 있으면 다음 검정기가 조용히 되살린다.

    AST 로 본다: 도크스트링·주석의 '무엇을 왜 지웠나' 기록은 남아야 하고(그게
    다음 사람이 되살리지 않을 유일한 근거다), 살아 있는 식별자·문자열만 금지다.
    """
    import ast
    import pathlib

    import edge_analysis.statics as pkg
    banned = {"exposure_slope", "clip_to_share", "EdgeEstimate", "_iset",
              "_clip", "contradiction"}

    def live(src: str) -> set[str]:
        tree = ast.parse(src)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
                and ast.get_docstring(n, clean=False) is not None}
        out: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                out.add(n.id)
            elif isinstance(n, ast.Attribute):
                out.add(n.attr)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.arg):
                out.add(n.arg)
            elif isinstance(n, ast.keyword) and n.arg:
                out.add(n.arg)
            elif isinstance(n, ast.alias):
                out.add(n.name.split(".")[-1])
                if n.asname:
                    out.add(n.asname)
            elif (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docs):
                out.add(n.value)
        return out

    root = pathlib.Path(pkg.__file__).parent
    for f in sorted(root.glob("*.py")):
        hit = banned & live(f.read_text(encoding="utf-8"))
        assert not hit, f"{f.name}: SEM 심볼 {sorted(hit)} 가 살아 있다"
