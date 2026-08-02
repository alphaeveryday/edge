"""동적 도구 상태기계 — **어휘를 프롬프트로 주지 않고 도구로 준다.**

## 무엇을 바꾸는가

STORM 의 제안 에이전트는 매 턴 `catalog.SCHEMA`(도구 15개를 한 덩어리로 적은 문자열)를
받고, 어휘가 필요하면 `digest()` 를 불러 **5,600자짜리 사전 전량**을 한 번에 받는다.
문제는 셋이다.

    선택지가 안 준다   15개가 항상 다 보이니 지금 무엇을 해야 하는지는 모델이 정한다
    사전이 통째로 온다 53종·8종·13종을 한 번에 받으면 그 턴의 문맥이 사전으로 덮인다
    순서가 강제 안 된다 접지 없이 구조를 세워도 마지막 검사에서야 걸린다

여기서는 **상태마다 부를 수 있는 것만 보여준다.** 상태 전이는 코드가 조건으로 지킨다 -
사건을 하나도 접지하지 못했으면 구조 단계로 못 넘어간다. 어휘는 계층으로 쪼개 필요한
가지만 준다(가족 7 → 그 가족의 타입 → 그 타입 상세).

## 왜 이게 나을 것이라 보는가

가설은 "선택지를 좁히면 접지가 늘고 계약 위반이 준다"이다. 반대로 나빠질 수도 있다 -
탐색이 막혀 구조가 빈약해질 수 있다. **그걸 재려고 만든 것이다.** 판정은 실행이 한다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# STORM 은 git 에 없고 본 워크트리에만 있다. 실험은 **원본을 고치지 않고** 빌려 쓴다 -
# 같은 도구·같은 계약 검사를 써야 비교가 성립한다.
STORM_SRC = Path("D:/Github/edge/src/apps/cloud/analysis-engine/experiments/storm/src")
if str(STORM_SRC) not in sys.path:
    sys.path.insert(0, str(STORM_SRC))

from storm import catalog as C          # noqa: E402
from storm import scope as SC           # noqa: E402

import os

# v2 스위치. 어휘 단계를 **부재 경로에서도** 강제한다 - v1 의 우회가 계약 위반의 원인이었다.
VOCAB_STRICT = os.environ.get("DYN_VOCAB_STRICT") == "1"

SCOPE, EVIDENCE, VOCAB, STRUCTURE, EMIT = "SCOPE", "EVIDENCE", "VOCAB", "STRUCTURE", "EMIT"
ORDER = (SCOPE, EVIDENCE, VOCAB, STRUCTURE, EMIT)


# ── 어휘를 계층으로 쪼갠다 ────────────────────────────────────────────────
def families() -> str:
    """사건 가족만. 53종을 한 번에 주지 않는다 - 7줄이면 고를 수 있다."""
    td = C._onto()[0]
    fam: dict[str, int] = {}
    for tid in td:
        fam[tid.split(".")[0]] = fam.get(tid.split(".")[0], 0) + 1
    lines = [f"  {k:<18} {v}종" for k, v in sorted(fam.items(), key=lambda x: -x[1])]
    return "사건 가족 - family(이름) 으로 그 안을 본다\n" + "\n".join(lines)


def family(name: str) -> str:
    """한 가족 안의 타입만. 보통 3~24줄이다."""
    td = C._onto()[0]
    hit = [t for t in td if t.split(".")[0] == name.upper()]
    if not hit:
        return f"'{name}' 가족이 없다. families() 로 확인해라"
    return f"[{name}] {len(hit)}종\n" + "\n".join(f"  {t}" for t in sorted(hit))


def kinds() -> str:
    """실체 종별 8종. 이건 짧아서 통째로 준다."""
    ek = C._onto()[3]
    return "실체 종별\n" + "\n".join(f"  {k}" for k in sorted(ek))


# ── 상태별 메뉴 ──────────────────────────────────────────────────────────
# **여기 없는 도구는 그 턴에 존재하지 않는다.** 이름조차 안 보인다.
MENUS: dict[str, tuple[tuple[str, str], ...]] = {
    SCOPE: (
        ("issues", "issues(etf, date)  범주 노드의 이슈 - 무엇을 설명해야 하나"),
        ("facts", "facts(entity, day)  봉 수·절단 여부 - 잴 수 있는 날인가"),
    ),
    EVIDENCE: (
        ("events", "events(entity, w0, w1, as_of, type_like=, role=)  창내 사건 (PIT)"),
        ("args", "args(event_id)  그 사건의 역할인자 전량 - 누가 무슨 역할로"),
        ("ground", "ground(ref, quote)  **접지** - 실재하나 + 인용이 진짜인가"),
    ),
    VOCAB: (
        ("families", "families()  사건 가족 7개만"),
        ("family", "family(name)  그 가족 안의 타입 목록"),
        ("types", "types(q)  타입 하나의 상세 (역할·술어·생애주기)"),
        ("prior", "prior(type_id)  이 타입이 애초에 가격을 움직이나 (실측)"),
        ("kinds", "kinds()  실체 종별 8종"),
    ),
    STRUCTURE: (
        ("paths", "paths(entity)  엮인 엔티티 - 역할·산업"),
        ("peers", "peers(entity, how)  how='industry'|'role' 리스트"),
        ("linked", "linked(a, b)  둘이 어떻게 엮이나. 안 엮이면 안 엮인다고 한다"),
        ("observables", "observables(entity) / observables(want='...')  뭘 잴 수 있나"),
        ("basket", "basket(industry=|sector=)  범주 구성원"),
        ("ground", "ground(ref, quote)  접지"),
    ),
    EMIT: (),
}

# 다음 단계로 넘어가려면 무엇이 있어야 하나. **코드가 지킨다** - 프롬프트로 부탁하지 않는다.
GUARDS: dict[str, str] = {
    SCOPE: "설명할 노드를 정했으면 next() 로 넘어간다",
    EVIDENCE: "사건을 하나 접지하거나, **없다는 것을 확인**해야 넘어간다 "
              "(ground 성공 1회 또는 events 조회 2회가 모두 '사건 없음')",
    VOCAB: "접지한 사건의 타입을 최소 하나 조회해야 넘어갈 수 있다",
    STRUCTURE: "경쟁 구조를 세울 재료를 봤으면 넘어간다 (도구 2회 이상)",
    EMIT: "",
}


@dataclass
class Machine:
    """상태와 그 상태에서 실제로 일어난 일. **진행은 관측으로만 이뤄진다.**"""

    state: str = SCOPE
    grounded: int = 0                 # ground() 성공 횟수
    typed: int = 0                    # 타입 상세 조회 횟수
    empty: int = 0                    # 사건이 **없다**고 확인한 횟수
    calls: list[str] = field(default_factory=list)
    per_state: dict[str, int] = field(default_factory=dict)

    # ── 메뉴 ───────────────────────────────────────────────────────────
    def menu(self) -> str:
        rows = MENUS[self.state]
        if not rows:
            return "도구 없음. 이제 dag 를 내라."
        body = "\n".join(f"  {d}" for _, d in rows)
        return (f"[{self.state}] 지금 부를 수 있는 것 — 여기 없는 이름은 존재하지 않는다\n"
                f"{body}\n  next()  다음 단계로. 조건: {GUARDS[self.state]}")

    def _ns(self) -> dict:
        allowed = {n for n, _ in MENUS[self.state]}
        ns = {k: getattr(C, k) for k in allowed if hasattr(C, k)}
        if "issues" in allowed:
            ns["issues"] = SC.issues
        for name, fn in (("families", families), ("family", family), ("kinds", kinds)):
            if name in allowed:
                ns[name] = fn
        return ns

    # ── 관측 ───────────────────────────────────────────────────────────
    def observe(self, call: str) -> str:
        """도구 호출. **상태 밖 도구를 부르면 그 사실을 알려준다** - 그것도 관측이다."""
        call = (call or "").strip()
        self.calls.append(f"{self.state}:{call}")
        self.per_state[self.state] = self.per_state.get(self.state, 0) + 1

        if call.startswith("next("):
            return self._advance()

        ns = self._ns()
        try:
            out = eval(call, {"__builtins__": {}}, ns)  # noqa: S307 - 메뉴 함수만
        except NameError as e:
            return (f"오류: {e}\n지금 상태({self.state})에서는 그 도구가 없다.\n"
                    f"{self.menu()}")
        except Exception as e:                       # noqa: BLE001 - 실패도 관측
            return f"오류: {type(e).__name__}: {e}\n부를 수 있는 것: {', '.join(ns)}"

        text = str(out)[:2500]
        if call.startswith("ground(") and "접지" in text and "실패" not in text:
            self.grounded += 1
        if call.startswith("events(") and "사건 없음" in text:
            self.empty += 1
        if call.startswith(("types(", "prior(")):
            self.typed += 1
        # **가드를 채웠으면 스스로 넘어간다.** next() 에 턴을 쓰게 하면 순서를 지키는
        # 대가로 예산을 태운다 - 실측으로 12턴 중 4턴이 전이에만 갔다.
        if not self._blocked():
            return text + "\n\n" + self._advance()
        return text

    def _blocked(self) -> str:
        """넘어가지 못하는 사유. 빈 문자열이면 갈 수 있다.

        **부재도 증거다.** 긍정 증거만 조건으로 걸었더니, 사건이 실제로 하나도 없는 셀에서
        모델이 창을 1900~2100 까지 넓혀 가며 8턴을 태우고 구조를 못 세웠다(실측). 이
        도구 묶음의 핵심 기능이 "없으면 없다고 답한다"인데 가드가 그 답을 안 받아줬다.
        """
        if self.state == SCOPE:
            return "" if self.per_state.get(SCOPE, 0) >= 1 else "먼저 무엇을 설명할지 봐라"
        if self.state == EVIDENCE:
            if self.grounded >= 1 or self.empty >= 2:
                return ""
            return ("접지된 사건이 0개다. events() 로 찾아 ground() 하거나, "
                    "다른 창으로 두 번 조회해 **없다는 것**을 확인해라.")
        if self.state == VOCAB:
            # v1 은 "사건이 없으면 볼 타입도 없다"며 통과시켰다. **그게 틀렸다** - 실측
            # 8셀에서 grounded=0 인 셀은 어휘를 한 번도 안 보고 구조를 세워 계약 위반이
            # 났다. 사건이 없을수록 "무슨 종류의 미관측 원인인가"를 어휘로 지목해야 한다.
            if self.typed >= 1:
                return ""
            if not VOCAB_STRICT and self.grounded == 0:
                return ""
            return "타입을 하나도 안 봤다. families()·family()·types() 를 써라."
        if self.state == STRUCTURE:
            return ("" if self.per_state.get(STRUCTURE, 0) >= 2
                    else "구조 재료를 더 봐라(paths·peers·linked·observables).")
        return ""

    def _advance(self) -> str:
        """전이. **조건을 못 채우면 안 넘어간다** - 순서가 프롬프트가 아니라 코드에 있다."""
        if (why := self._blocked()):
            return f"아직 못 넘어간다: {why}"
        nxt = ORDER[min(ORDER.index(self.state) + 1, len(ORDER) - 1)]
        self.state = nxt
        return f"→ {nxt}\n{self.menu()}"

    @property
    def done(self) -> bool:
        return self.state == EMIT

    def stats(self) -> dict:
        return {"state": self.state, "grounded": self.grounded, "typed": self.typed,
                "calls": len(self.calls), "per_state": dict(self.per_state),
                "distinct_tools": len({c.split(":", 1)[1].split("(")[0]
                                       for c in self.calls if "(" in c})}


if __name__ == "__main__":
    m = Machine()
    assert "issues" in m.menu() and "events" not in m.menu(), "SCOPE 에 EVIDENCE 도구가 샌다"
    assert "그 도구가 없다" in m.observe("events('ORG_KR_000660','2026-06-01','2026-06-01')")
    m.observe("next()")
    assert m.state == EVIDENCE
    assert "아직 못 넘어간다" in m.observe("next()"), "접지 없이 넘어갔다"
    assert len(families().splitlines()) < 12, "가족 목록이 길면 쪼갠 뜻이 없다"

    # **부재도 증거다** - 사건이 없는 셀에서 갇히면 안 된다(실측으로 8턴을 태웠다).
    n = Machine(state=EVIDENCE)
    n.empty = 2
    assert n._blocked() == "", "사건 없음을 두 번 확인했는데도 막힌다"
    n2 = Machine(state=EVIDENCE)
    assert n2._blocked(), "아무 증거 없이 통과시킨다"
    print("fsm selfcheck ok", file=sys.stderr)
