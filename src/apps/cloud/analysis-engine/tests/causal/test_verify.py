"""간선 검정 테스트 — **모델이 수치를 말할 자리가 없다는 것**을 고정한다.

실험판에서 날조는 전부 한 자리에서 났다: 모델이 결론 JSON 에 숫자를 적는 자리.
그래서 결론에는 숫자가 없고 값은 샌드박스의 `R` 에서만 읽으며, G4 가 `R` 의 p 를
원장과 대조한다. 그리고 무엇을 주장하느냐(`claims`)가 쓸 수 있는 귀무를 좁힌다 -
셀이 큰 등락으로 선정됐으므로 "다른 날과 다르다"는 귀속 근거가 될 수 없다(선택 순환).
"""

from datetime import date

import numpy as np

from edge_analysis.causal import sandbox as SB
from edge_analysis.causal import verify as V
from edge_analysis.causal.engine import EdgeDesign

TRADE_DATE = date(2026, 7, 16)
W0 = date(2026, 5, 18)
AS_OF = "2026-07-16T15:40:00+09:00"

# `observed` 는 새 노드 계약의 필수 칸이다 - P4 는 관측 노드에서만 조정집합을 고르므로
# 이 칸이 비면 뒷문을 막을 후보가 0개가 되고 계획이 조용히 `strategy=none` 으로 떨어진다.
NODES = {
    "S@t-3": {"kind": "OBSERVABLE", "unit": "stock", "measure": "사전 모멘텀",
              "observed": "직전 3거래일 누적 수익률"},
    "EVT@t-1": {"kind": "SHOCK", "unit": "stock", "measure": "공시 발생 지시자",
                "observed": "공시 원장의 발생 여부"},
    "AR@t0": {"kind": "TARGET", "unit": "stock", "measure": "당일 초과수익",
              "observed": "종가 기준 초과수익"},
}
EDGES = [{"from": "S@t-3", "to": "EVT@t-1"}, {"from": "S@t-3", "to": "AR@t0"},
         {"from": "EVT@t-1", "to": "AR@t0"}]
DESIGN = EdgeDesign(src="EVT@t-1", dst="AR@t0", claims="L4", scope="type",
                    say="공시가 당일 초과수익을 만들었다", because="기대 현금흐름을 올린다",
                    false_if="같은 날 지수 편입이 있었다면 죽는다", cause_label="배당 공시")


def _plan() -> dict:
    return V.plan(NODES, EDGES, DESIGN)


class _Cd:
    """검정이 실제로 돌 만큼의 표본을 주는 스텁. 처치군에만 효과를 심는다."""

    def __init__(self, effect: float = 0.05) -> None:
        rng = np.random.default_rng(3)
        self.days = [date(2026, 7, d) for d in (13, 14, 15)]
        self.t = [(f"T{i}", d) for d in self.days for i in range(6)]
        self.c = [(f"C{i}", d) for d in self.days for i in range(14)]
        self._ar = {p: float(rng.normal(0, 0.01)) + effect for p in self.t}
        self._ar.update({p: float(rng.normal(0, 0.01)) for p in self.c})
        self._mom = {p: float(rng.normal(0, 0.02)) for p in self.t + self.c}

    def cohort(self, where, *, as_of, w0=None, w1=None, limit=20000):
        assert as_of
        return list(self.t)

    def universe(self, where, dates, *, exclude=None, limit=80000):
        return list(self.c)

    def ar(self, pairs, **kw):
        return np.array([self._ar.get((str(i), d), np.nan) for i, d in pairs])

    def mom(self, pairs, **kw):
        return np.array([self._mom.get((str(i), d), np.nan) for i, d in pairs])

    def vol(self, pairs, **kw):
        return np.ones(len(pairs)) * 0.01

    def flow(self, pairs, *, kind: str = "institution_total"):
        # 표면이 있다는 사실만 고정한다 - 없으면 tools 조립이 AttributeError 로 죽는다.
        return self.vol(pairs)

    def ids(self, names):
        return {str(n): f"inst_{n}" for n in (names or [])}

    def weight(self, etf, trade_date, units=None):
        return {"share": 0.2, "n_hold": 20}

    def prior(self, code, *, as_of="", trade_date=None, need=None, min_cross=50):
        return {"type": code, "n": 240, "abs_max": 0.39}


class _Client:
    """대본대로 답하는 검정 세션 스텁. 프롬프트도 함께 모은다."""

    def __init__(self, turns: list[dict]) -> None:
        self._turns = list(turns)
        self.users: list[str] = []
        self.system = ""

    def complete_json(self, system: str, user: str) -> dict:
        self.system = system
        self.users.append(user)
        return self._turns.pop(0) if self._turns else {"thought": "끝", "done": True}


CODE_OK = """
t = cohort("event_type_code = 'X'")
days = sorted({d for _, d in t})
c = universe("industry_name = 'Bio'", days, exclude=t)
pairs = list(t) + list(c)
y = ar(pairs)
z = mom(pairs)
x = np.array([1.0] * len(t) + [0.0] * len(c))
blocks = np.array([str(d)[:10] for _, d in pairs])

def beta(w):
    A = np.column_stack([np.ones(len(w['x'])), w['x'], z])
    return float(np.linalg.lstsq(A, y, rcond=None)[0][1])

res = placebo(beta, {'x': x}, permute(x, strata=blocks, n=200), null_kind='label')
R = {'x': x, 'y': y, 'z': {'S@t-3': z}, 'unit': 'stock', 'effect': beta({'x': x}),
     'test': res, 'null_kind': 'label', 'strata': blocks,
     'units': sorted({i for i, _ in t})}
"""

# 층화를 빼먹은 판. 설계가 날짜를 조건화했는데 귀무는 자유순열이고 사유도 없다 -> G7.
CODE_NO_STRATA = (CODE_OK.replace("permute(x, strata=blocks, n=200)", "permute(x, n=200)")
                         .replace("'strata': blocks", "'strata': None"))

# 무층화를 **사유와 함께** 선언한 판. 게이트는 통과하고 사유가 감사에 남는다.
CODE_NO_STRATA_JUSTIFIED = CODE_NO_STRATA.replace(
    "'strata': None", "'strata': None, 'strata_reason': '대조를 날짜와 무관하게 뽑았다'")

# **더미 순열 우회.** 무층화로 재서 p 를 얻고, 그 뒤에 층화 permute 를 한 번 더 부른다.
# 보고된 p 는 자유순열에서 왔는데 원장의 마지막 순열만 보면 층화로 보인다.
CODE_DUMMY_STRATIFIED_PERM = CODE_NO_STRATA.replace(
    "'strata': None",
    "'strata': blocks").replace(
    "R = {", "_dummy = permute(x, strata=blocks, n=200)\nR = {")


def _verify(client, cd=None) -> V.EdgeProof:
    return V.verify(cd or _Cd(), client, DESIGN, _plan(), as_of=AS_OF, w0=W0,
                    trade_date=TRADE_DATE, w1=TRADE_DATE, etf_instrument_id="inst_ETF")


# --------------------------------------------------------------------------- #
# 브리프 — 코드가 만든다. 모델에게 묻지 않는다
# --------------------------------------------------------------------------- #
def test_plan_derives_the_adjustment_set_and_the_allowed_null_from_the_claim():
    p = _plan()

    assert p["adjust"] == ["S@t-3"], "뒷문을 막는 집합을 코드가 못 찾았다"
    assert p["identified_by_adjustment"] is True
    assert p["null_ok"] == ["label"], "귀속(L4)에 date·time 귀무를 허용했다"
    assert p["n_min"] == 30                     # scope=type
    assert "공시가 당일 초과수익을 만들었다" in V.brief(p)
    assert "허용 null_kind = ['label']" in V.brief(p)


def test_brief_reports_when_adjustment_cannot_identify_and_lists_instruments():
    edges = EDGES + [{"from": "EVT@t-1", "to": "AR@t0", "kind": "bidirected"},
                     {"from": "Z@t-9", "to": "EVT@t-1"}]
    nodes = {**NODES, "Z@t-9": {"kind": "OBSERVABLE", "unit": "stock", "measure": "도구",
                                "observed": "9거래일 전 일정 공표"}}

    p = V.plan(nodes, edges, DESIGN)
    text = V.brief(p)

    assert p["identified_by_adjustment"] is False
    assert p["iv"] == ["Z@t-9"]
    # "뒷문이 열려 있지 않다" 는 삭제됐다 - 빈 조정집합은 세계가 아니라 그래프에 대한
    # 진술이고, 그 문구가 검정 세션에게 교란이 없다고 오인시켰다. 대신 식별 상태와
    # **무엇이 막고 있는지**가 나가야 한다.
    assert "뒷문이 열려 있지 않다" not in text
    assert "식별상태 : identified_under" in text
    assert "막고 있는 미관측 공통원인" in text and "도구변수 후보" in text


def test_a_latent_node_never_enters_the_adjustment_set():
    """미관측 노드로 조건화하는 계획은 실행할 수 없다 - 조건화할 열이 없기 때문이다.

    `observed` 를 안 적은 노드가 조정집합에 들어가면 검정 세션은 그 열을 만들어 내거나
    (날조) 대리물로 갈아치운다(설계 변경). 둘 다 원장에 남지 않는다. 그래서 뒷문을
    실제로 막는 노드라도 미관측이면 후보에서 빠지고, 계획은 식별 실패를 그대로 적어야
    한다 - 억지로 채운 조정집합보다 그쪽이 정직하다.
    """
    nodes = {**NODES, "S@t-3": {**NODES["S@t-3"], "observed": None}}

    p = V.plan(nodes, EDGES, DESIGN)

    assert "S@t-3" not in p["adjust"], "미관측 교란이 조정집합에 들어갔다"
    assert p["status"] == "not_identified" and p["strategy"] == "none"
    assert "조정집합 : 없음" in V.brief(p) and "점식별 불가" in V.brief(p)


# --------------------------------------------------------------------------- #
# 게이트 G1~G7 — 전부 기계 검사다
# --------------------------------------------------------------------------- #
def test_g1_rejects_a_scalar_outcome_regressed_on_many_observations():
    bad = V.gate({"x": [1, 2, 3], "y": 5.0}, SB.Ledger(), _plan())

    assert any(b.startswith("G1") for b in bad), bad


def test_g1_rejects_a_unit_that_contradicts_the_node_declaration():
    led = SB.Ledger()
    R = {"x": [1, 0], "y": [0.1, 0.2], "z": {"S@t-3": [1, 2]}, "unit": "portfolio",
         "null_kind": "label", "strata": None, "test": {"p": 0.01}}

    bad = V.gate(R, led, _plan())

    assert any("unit=" in b and "'stock'" in b for b in bad), bad


def test_g2_and_g3_reject_an_underpowered_test_without_a_null():
    bad = V.gate({"x": [1, 0], "y": [0.1, 0.2], "z": {}, "null_kind": "label",
                  "strata": None}, SB.Ledger(), _plan())

    assert any(b.startswith("G2") for b in bad), bad
    assert any(b.startswith("G3") for b in bad), bad
    assert any(b.startswith("G5") for b in bad), bad     # 조정집합 누락


def test_g4_refuses_swapping_the_null_kind_on_a_real_p_value():
    """**p 만 대조하면 뚫린다.** date 로 돌린 p 를 label 이라 적으면 통과했다.

    G6 가 뒤에서 종류를 보긴 했지만 그 값이 R 의 자기 신고였다. 그래서 (p, null_kind)
    쌍으로 원장을 맞추고, G6 는 매칭된 원장 항목의 종류를 읽어야 한다 - 어느 귀무로 얻은
    p 인지가 주장의 자격을 정하기 때문이다.
    """
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.01, "n_null": 200,
                      "null_sd": 0.01, "null_kind": "date", "two_sided": True})
    R = {"x": [1, 0] * 30, "y": [0.1, 0.2] * 30, "z": {"S@t-3": [1, 2] * 30},
         "unit": "stock", "null_kind": "label", "strata": None,
         "test": {"p": 0.01, "null_kind": "label"}}

    bad = V.gate(R, led, _plan())

    assert any(b.startswith("G4") and "종류를 갈아 끼울 수 없다" in b for b in bad), bad
    # 그리고 G6 는 원장의 date 를 읽어 L4 주장에 못 쓴다고 말해야 한다.
    assert any(b.startswith("G6") and "date" in b for b in bad), bad


def test_g6b_refuses_a_two_sided_p_for_a_directional_claim():
    """양측 p 는 '달랐다'의 p 다. 부호를 주장하면 단측으로 재야 한다."""
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.02, "n_null": 200,
                      "null_sd": 0.01, "null_kind": "label", "two_sided": True})
    design = EdgeDesign(src="EVT@t-1", dst="AR@t0", claims="L3", scope="type",
                        say="공시가 초과수익을 올렸다", because="기대를 올린다")
    R = {"x": [1, 0] * 30, "y": [0.1, 0.2] * 30, "z": {"S@t-3": [1, 2] * 30},
         "unit": "stock", "null_kind": "label", "strata": None,
         "test": {"p": 0.02, "null_kind": "label"}}

    bad = V.gate(R, led, V.plan(NODES, EDGES, design))

    assert any(b.startswith("G6b") for b in bad), bad

    # 단측으로 재면 통과한다 - 규칙이 아니라 자격의 문제다.
    led.calls[0]["two_sided"] = False
    assert not any(b.startswith("G6b") for b in V.gate(R, led, V.plan(NODES, EDGES, design)))


def test_g7b_catches_a_declared_stratification_that_never_happened():
    """선언과 실행이 다른 것은 **원장이 없으면 탐지 불가**였다.

    `R['strata']` 에 층 배열을 담고 `permute(x)` 를 층 없이 부르면 보고된 층화가 사실이
    아니게 된다. G7 은 선언만 봤으므로 이 불일치를 통과시켰다.
    """
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.01, "n_null": 200,
                      "null_sd": 0.01, "null_kind": "label", "two_sided": True})
    led.perms.append({"n": 1, "len_x": 60, "n_null": 200,
                      "stratified": False, "n_strata": 0})
    R = {"x": [1, 0] * 30, "y": [0.1, 0.2] * 30, "z": {"S@t-3": [1, 2] * 30},
         "unit": "stock", "null_kind": "label", "test": {"p": 0.01, "null_kind": "label"},
         "strata": ["d1", "d2"] * 30}

    bad = V.gate(R, led, _plan())

    assert any(b.startswith("G7b") and "층 없이 불렸다" in b for b in bad), bad

    # 반대 방향도 잡는다 - 실제로 층 안에서 섞었으면 R 에 담아야 감사가 재구성된다.
    led.perms[-1] = {"n": 1, "len_x": 60, "n_null": 200,
                     "stratified": True, "n_strata": 3}
    R2 = {**R, "strata": None, "strata_reason": "타입 전체에서 쌓아 날짜 효과가 없다"}
    assert any(b.startswith("G7b") and "R['strata']" in b for b in V.gate(R2, led, _plan()))


def test_g7b_binds_to_the_permutation_that_produced_the_reported_p():
    """더미 순열로 G7b 를 통과시킬 수 없다.

    WHY: 무층화로 재서 p 를 얻고 **그 뒤에** 층화 permute 를 한 번 더 부르면, 원장의
    마지막 순열은 층화다. 마지막만 보는 G7b 는 통과시키고 감사에는 층화로 남는다 -
    보고된 p 는 틀린 교환가능성에서 온 것인데 게이트가 초록을 준다. 원장의 `perms_at` 로
    보고된 p 의 순열에 결속한다.
    """
    client = _Client([{"code": CODE_DUMMY_STRATIFIED_PERM}, {"done": True}])

    r = _verify(client)

    assert r.status == "게이트실패"
    assert any(b.startswith("G7b") and "층 없이 불렸다" in b for b in r.gate_fail), r.gate_fail
    # 우회의 흔적이 원장에 남아야 사후에 재구성된다.
    assert len(r.perms) == 2 and r.perms[-1]["stratified"] is True


def test_g7b_accepts_the_stratified_permutation_bound_to_the_reported_p():
    """반대 방향 - 정상 경로가 결속 검사로 죽으면 게이트가 쓸모없다."""
    client = _Client([{"code": CODE_OK}, {"done": True}])

    r = _verify(client)

    assert r.status == "통과", r.gate_fail
    assert not any(b.startswith("G7b") for b in r.gate_fail)


def test_g1_unit_check_is_live_without_a_declared_node_unit():
    """단위 검사가 **모델 선언에 의존하면 꺼진다.**

    WHY: 새 계약의 노드 메타에는 `unit` 칸이 없다(says·observed·value·events). 선언만
    보면 정상 제안 전부에서 이 검사가 조용히 꺼지고, 검정이 `R['unit']='portfolio'` 로
    셀 단위를 주장해도 배열·p 게이트만 통과하면 증명이 선다. 표본 단위는 도구가 정하므로
    코드가 아는 사실이다 - 그것으로 검사한다.
    """
    led = SB.Ledger()
    led.perms.append({"n": 1, "len_x": 60, "n_null": 200,
                      "stratified": True, "n_strata": 3})
    led.calls.append({"n": 1, "testable": True, "p": 0.01, "n_null": 200, "null_sd": 0.01,
                      "null_kind": "label", "two_sided": False, "perms_at": 1})
    R = {"x": [1, 0] * 30, "y": [0.1, 0.2] * 30, "z": {"S@t-3": [1, 2] * 30},
         "null_kind": "label", "test": {"p": 0.01, "null_kind": "label"},
         "strata": ["d1", "d2"] * 30, "unit": "portfolio"}

    # 새 계약의 노드 메타는 `unit` 칸이 없다 - 그 형태로 계획을 만든다.
    fresh = {n: {"says": m["measure"], "observed": "일간 수익률"} for n, m in NODES.items()}
    p = V.plan(fresh, EDGES, DESIGN)
    assert not (p["nodes"].get(p["to"]) or {}).get("unit"), "픽스처가 단위를 선언해 버렸다"
    assert any(b.startswith("G1 unit") for b in V.gate(R, led, p))
    # 종목 단위를 적으면 통과한다 - 검사가 정상 경로를 막지 않는다.
    assert not any(b.startswith("G1 unit") for b in V.gate({**R, "unit": "stock"}, led, p))


def test_the_ledger_records_permutation_calls_so_execution_can_be_audited():
    """도구를 감싸지 않으면 순열이 어떻게 만들어졌는지가 어디에도 남지 않는다."""
    led = SB.Ledger()
    nulls = led.wrap_permute([1, 0, 1, 0], strata=["a", "a", "b", "b"], n=25)

    assert len(nulls) == 25 and set(nulls[0]) == {"x"}
    assert led.perms[-1] == {"n": 1, "len_x": 4, "n_null": 25,
                             "stratified": True, "n_strata": 2}


def test_specification_sensitivity_is_recorded_not_gated():
    """여러 사양을 시도하는 것은 정직한 탐색이다. 막으면 한 번만 재고 끝낸다.

    그래서 게이트로 죽이지 않고 **원장의 p 가 α 를 가로지르는 사실**을 남겨 확신도를
    깎는다 - 보고된 유의가 사양 선택의 산물일 수 있다는 것을 산출물이 말한다.
    """
    led = SB.Ledger()
    led.calls += [{"n": 1, "testable": True, "p": 0.02},
                  {"n": 2, "testable": True, "p": 0.31}]
    assert led.spec_sensitive() is True

    same = SB.Ledger()
    same.calls += [{"n": 1, "testable": True, "p": 0.01},
                   {"n": 2, "testable": True, "p": 0.03}]
    assert same.spec_sensitive() is False        # 전부 유의하면 사양 의존이 아니다


def test_g4_refuses_a_hand_written_p_value():
    """원장에 없는 p 는 받지 않는다. 실측 날조: `p=0.37`, placebo 0회 호출."""
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.42, "n_null": 200,
                      "null_kind": "label"})
    R = {"x": np.ones(40), "y": np.arange(40.0), "z": {"S@t-3": np.arange(40.0)},
         "unit": "stock", "null_kind": "label", "strata": np.arange(40) % 2,
         "test": {"p": 0.001}}

    bad = V.gate(R, led, _plan())

    assert any(b.startswith("G4") for b in bad), bad


def test_g6_blocks_the_selection_circularity_for_an_attribution_claim():
    """셀은 큰 등락으로 **선정됐다** - "이 날이 특별한가"는 거의 자동으로 유의하다."""
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.01, "n_null": 200,
                      "null_kind": "date"})
    R = {"x": np.ones(40), "y": np.arange(40.0), "z": {"S@t-3": np.arange(40.0)},
         "unit": "stock", "null_kind": "date", "strata": np.arange(40) % 2,
         "test": {"p": 0.01}}

    bad = V.gate(R, led, _plan())

    assert any(b.startswith("G6") and "선택 순환" in b for b in bad), bad


def test_g7_requires_the_null_to_declare_its_exchangeability():
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.01, "n_null": 200,
                      "null_kind": "label"})
    R = {"x": np.ones(40), "y": np.arange(40.0), "z": {"S@t-3": np.arange(40.0)},
         "unit": "stock", "null_kind": "label", "test": {"p": 0.01}}

    bad = V.gate(R, led, _plan())

    assert any(b.startswith("G7") for b in bad), bad


def test_g7_refuses_free_permutation_when_the_design_blocked_on_dates():
    """**선언만으로는 부족하다.** 설계가 날짜를 조건화했는데 자유순열로 섞으면
    귀무 분산이 층 효과로 부푼다(실측 sd 0.0088 vs 0.0077). 사유가 있으면 통과한다."""
    led = SB.Ledger()
    led.calls.append({"n": 1, "testable": True, "p": 0.01, "n_null": 200,
                      "null_kind": "label"})
    base = {"x": np.ones(40), "y": np.arange(40.0), "z": {"S@t-3": np.arange(40.0)},
            "unit": "stock", "null_kind": "label", "test": {"p": 0.01}, "strata": None}

    silent = V.gate(base, led, _plan())
    justified = V.gate({**base, "strata_reason": "대조를 날짜와 무관하게 뽑았다"}, led, _plan())

    assert any(b.startswith("G7") and "무층화" in b for b in silent), silent
    assert not [b for b in justified if b.startswith("G7")], justified


# --------------------------------------------------------------------------- #
# 루프 — 거부는 되먹임이고, 못 재면 요청이다
# --------------------------------------------------------------------------- #
def test_numbers_come_from_the_ledger_and_the_gate_passes():
    client = _Client([{"code": CODE_OK}, {"done": True}])

    r = _verify(client)

    assert r.status == "통과" and r.passed and r.significant
    assert r.n == 60 and r.turns == 2
    assert r.effect is not None and abs(r.effect - 0.05) < 0.01
    assert r.null_kind == "label" and r.strata_declared is True
    assert r.adjust == ["S@t-3"]
    assert r.units == [f"T{i}" for i in range(6)]
    # p 는 원장에 있는 값이다. 모델이 타이핑한 것이 아니다.
    assert r.ledger and r.ledger[0]["p"] == r.p
    assert any("placebo(" in c for c in r.code)


def test_a_failed_gate_is_fed_back_and_the_agent_gets_another_turn():
    """거부는 침묵이 아니라 교정이다 - 실험판에서 에이전트를 살린 것도 오류 메시지였다."""
    client = _Client([{"code": CODE_NO_STRATA}, {"done": True},
                      {"code": CODE_OK}, {"done": True}])

    r = _verify(client)

    assert r.status == "통과", r.gate_fail
    assert any("G7" in u for u in client.users), "층화 누락 사유가 되먹임되지 않았다"
    assert r.turns == 4


def test_a_justified_no_stratification_passes_and_the_reason_is_kept():
    client = _Client([{"code": CODE_NO_STRATA_JUSTIFIED}, {"done": True}])

    r = _verify(client)

    assert r.status == "통과", r.gate_fail
    assert r.strata_declared is False
    assert r.strata_reason == "대조를 날짜와 무관하게 뽑았다"


def test_impossible_becomes_a_structured_data_request_not_a_silent_drop():
    client = _Client([{"impossible": "체결 흐름 원장이 없다",
                       "need": "투자자별 순매수 일별", "grain": "일별",
                       "unlocks": "수급 주도와 사건 주도를 가른다"}])

    r = _verify(client)

    assert r.status == "불가" and r.p is None
    assert r.data_request["need"] == "투자자별 순매수 일별"
    assert r.data_request["grain"] == "일별"
    assert r.data_request["edge"] == "EVT@t-1→AR@t0"
    assert "체결 흐름" in r.data_request["why"]


def test_the_graph_is_never_shown_to_the_verifier():
    """그래프를 보면 자기 검정이 쉬워지도록 구조를 재해석한다(스펙 쇼핑)."""
    client = _Client([{"code": CODE_OK}, {"done": True}])

    _verify(client)

    assert "S@t-3" in client.system, "조정집합은 내려줘야 한다"
    assert "S@t-3 → AR@t0" not in client.system, "다른 간선 구조가 노출됐다"
    assert "structures" not in client.system


def test_gate_failure_after_the_last_turn_reports_no_number():
    """게이트를 통과하지 못하면 수치를 쓰지 않는다 - 반쯤 맞는 p 가 새 나가면 안 된다."""
    client = _Client([{"code": CODE_NO_STRATA}, {"done": True}] * V.MAX_TURNS)

    r = _verify(client)

    assert r.status == "게이트실패"
    assert r.p is None
    assert any(b.startswith("G7") for b in r.gate_fail), r.gate_fail
    assert r.ledger, "원장은 남아야 한다 - 무엇을 시도했는지가 증거다"
