"""샌드박스 테스트 — **여기서 지키는 것은 편의가 아니라 경계다.**

검정 에이전트가 파이썬을 쓴다는 것은 LLM 이 쓴 코드를 우리가 실행한다는 뜻이고, 그
입력에는 외부 사건 제목이 섞인다(프롬프트 주입 표면). 그래서 다음을 고정한다:

    PIT      모델은 `as_of` 를 만질 문법이 없다. 창은 셀 당일로 잘린다
    탈출     던더·import·파일/네트워크는 막힌다. 도구의 커넥션에 닿을 수 없다
    복구     모델이 도구명을 변수로 덮어도 다음 턴에 되살아난다
    원장     placebo 호출은 **전부** 남는다 - 유리한 칸만 보고할 수 없다
"""

from datetime import date
import threading

import numpy as np

from edge_analysis.causal import sandbox as SB

TRADE_DATE = date(2026, 7, 16)
W0, W1 = date(2026, 5, 18), TRADE_DATE
AS_OF = "2026-07-16T15:40:00+09:00"


class _Cd:
    """도구가 닿는 데이터 표면의 최소 스텁. 호출 인자를 기록한다."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.secret = "커넥션 대신 이 문자열이 새는지 본다"

    def cohort(self, where, *, as_of, w0=None, w1=None, limit=20000):
        assert as_of, "PIT 없이 코호트를 만들 수 없다"
        self.seen.append({"where": where, "as_of": as_of, "w0": w0, "w1": w1})
        return [("i1", date(2026, 7, 14)), ("i2", date(2026, 7, 14))]

    def universe(self, where, dates, *, exclude=None, limit=80000):
        return [("c1", dates[0]), ("c2", dates[0])]

    def ar(self, pairs, **kw):
        return np.arange(len(pairs), dtype=float)

    def mom(self, pairs, **kw):
        return np.zeros(len(pairs))

    def vol(self, pairs, **kw):
        return np.ones(len(pairs))

    def weight(self, etf, trade_date, units=None):
        return {"share": 0.24, "n_hold": 10}

    def prior(self, code, *, need=None, min_cross=50):
        return {"type": code, "n": 12}


def _ns(cd=None):
    tools, led = SB.tools(cd or _Cd(), as_of=AS_OF, w0=W0, w1=W1, trade_date=TRADE_DATE,
                          etf_instrument_id="inst_ETF")
    return SB.namespace(tools), led


def test_model_cannot_pass_as_of_so_pit_is_not_negotiable():
    """`cohort` 표면에 `as_of` 인자가 없다 - 우회할 문법 자체를 없앤다."""
    cd = _Cd()
    ns, _ = _ns(cd)

    out = SB.observe("print(len(cohort(\"event_type_code = 'X'\")))", ns)

    assert out.strip().endswith("2"), out
    assert cd.seen[0]["as_of"] == AS_OF


def test_future_window_is_clipped_to_the_cell_day():
    """미래 관측을 표본에 넣을 수 없다 - w1 은 셀 당일로 잘린다."""
    cd = _Cd()
    ns, _ = _ns(cd)

    SB.observe("cohort(\"event_type_code = 'X'\", w0='2026-01-05', w1='2027-01-01')", ns)

    assert cd.seen[0]["w0"] == date(2026, 1, 5)
    assert cd.seen[0]["w1"] == TRADE_DATE


def test_dunder_access_is_refused_before_execution():
    """도구는 바운드 메서드다 - 던더를 열면 `cohort.__self__._conn` 으로 DB 에 닿는다."""
    cd = _Cd()
    ns, _ = _ns(cd)

    out = SB.observe("print(cohort.__self__.secret)", ns)

    assert out.startswith("거부:")
    assert cd.secret not in out


def test_a_synthesized_dunder_name_cannot_reach_bound_internals():
    """`__` 문자열 검사만으로는 못 막는다.

    WHY: `getattr(x, '_'*2 + 'class' + '_'*2)` 는 소스에 `__` 를 담지 않으면서 던더에 닿고,
    도구의 바인딩(`__self__`)을 지나 DB 커넥션까지 간다. 샌드박스는 **LLM 이 쓴 코드**를
    실행하고 입력에 외부 사건 제목이 섞이므로 프롬프트 주입 표면이다 - 문자열 검사 하나에
    기대면 안 된다. 이름을 빌트인에서 빼고 AST 로 참조를 본다(두 겹).
    """
    cd = _Cd()
    ns, _ = _ns(cd)

    for attempt in ("print(getattr(cohort, '_' * 2 + 'self' + '_' * 2))",
                    "print(type(cohort).__mro__)",
                    "print(vars(cohort))",
                    "print(cohort._tools)"):
        out = SB.observe(attempt, ns)
        assert out.startswith("거부:"), attempt
        assert cd.secret not in out, attempt


def test_blocked_imports_and_missing_builtins_are_observations_not_crashes():
    ns, _ = _ns()

    assert "막혀 있다" in SB.observe("import os", ns)
    assert "막혀 있다" in SB.observe("import socket", ns)
    # 반사·파일 접근 이름은 **실행 전에** 거부된다 - 소스에 있으면 그 자체가 우회 시도다.
    assert SB.observe("open('x')", ns).startswith("거부:")
    # 그냥 없는 이름은 관측이다 - 모델이 고쳐 쓴다. 하네스가 죽으면 안 된다.
    assert "NameError" in SB.observe("print(nonexistent_helper(1))", ns)
    assert "3" in SB.observe("print(sum([1, 2]))", ns)


def test_timeout_returns_guidance_and_the_runaway_thread_is_stopped(monkeypatch):
    """시간초과 스레드를 살려두면 남은 검정 전부가 CPU 를 나눠 쓴다.

    실측: 이 테스트가 스레드를 남겼을 때 전체 스위트가 1.6초 -> 138초가 됐다.
    ECS 태스크에서는 같은 일이 조용히 일어난다.
    """
    monkeypatch.setattr(SB, "EXEC_TIMEOUT", 1)
    ns, _ = _ns()
    before = threading.active_count()

    out = SB.observe("x = 0\nwhile True:\n    x += 1", ns)

    assert "시간초과" in out and "위약 표본" in out
    assert "중단시키지 못했다" not in out
    assert threading.active_count() <= before, "폭주 스레드가 살아 있다"


def test_oversized_code_is_refused():
    ns, _ = _ns()

    assert SB.observe("x = 1  # " + "가" * SB.MAX_CODE_CHARS, ns).startswith("거부:")


def test_tools_survive_being_shadowed_by_a_model_variable():
    """실측: 모델이 도구명을 변수로 덮어 6턴을 통째로 날렸다. 되돌릴 방법이 없었다."""
    ns, _ = _ns()

    SB.observe("ar = [1, 2, 3]", ns)
    out = SB.observe("print(len(ar([('i1', dt.date(2026, 7, 14))])))", ns)

    assert out.strip().endswith("1"), out


def test_every_placebo_call_is_recorded_so_spec_shopping_is_visible():
    """보고된 하나가 아니라 **시도 전부**가 남는다."""
    ns, led = _ns()

    SB.observe("""
x = np.array([1.0] * 6 + [0.0] * 6)
y = np.arange(12, dtype=float)

def beta(w):
    A = np.column_stack([np.ones(len(w['x'])), w['x']])
    return float(np.linalg.lstsq(A, y, rcond=None)[0][1])

for n in (30, 60):
    placebo(beta, {'x': x}, permute(x, n=n), null_kind='label')
""", ns)

    assert [c["n"] for c in led.calls] == [1, 2]
    assert {c["n_null"] for c in led.calls} == {30, 60}
    assert all(c["null_kind"] == "label" for c in led.calls)


def test_r_echo_summarizes_arrays_and_never_kills_the_observation():
    """실측: R 에 date·ndarray 가 들어오면 json 이 터져 관측 전체를 날렸다."""
    ns, _ = _ns()

    out = SB.observe("R = {'x': np.arange(5), 'd': dt.date(2026, 7, 16), 'effect': 0.123456789}",
                     ns)

    assert "[R]" in out and "<array n=5>" in out and "2026-07-16" in out
    assert "0.123457" in out
