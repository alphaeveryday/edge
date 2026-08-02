"""동적 도구 컨텍스트 상태기계 — 순서를 프롬프트가 아니라 코드가 지킨다.

STORM dyntool 실험의 이식. 두 규율만 가져온다:

1. **여기 없는 도구는 그 턴에 존재하지 않는다.** 이름조차 안 보인다 - 어휘를 통째로
   주면 모델은 고를 수 있는 것만 고르고, 못 고른 이유가 기록에 안 남는다.
2. **진행은 관측으로만 이뤄진다.** 가드를 코드가 검사한다 - "먼저 X 를 보라"고
   프롬프트로 부탁하면 지켜지는 날과 안 지켜지는 날이 생기고, 그 차이가 세션 분산이다.

튜플 체계용 상태 넷 (STORM 의 SCOPE→EVIDENCE→VOCAB→STRUCTURE→EMIT 를 이 문제에
맞춰 접었다):

    SCOPE     무엇을 설명해야 하나 - 셀·분할 경계·측정 가능 축
    GROUND    무엇이 실재하나 - 사건·근거 문서·스레드, **또는 없다는 확인**
    SCREEN    역사가 무엇을 아나 - 발견 표본 격자·계열 발화
    EMIT      튜플 제출

가드는 STORM 의 교훈을 그대로 담는다: **부재도 증거다.** 사건이 0인 셀에서 긍정
증거만 요구하면 모델이 창을 넓혀 가며 턴을 태운다(실측). 그래서 GROUND 는 '접지 1회
또는 부재 확인 1회'로 통과한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..observability import record as trace

SCOPE, GROUND, SCREEN, EMIT = "SCOPE", "GROUND", "SCREEN", "EMIT"
ORDER = (SCOPE, GROUND, SCREEN, EMIT)

MENUS: dict[str, tuple[tuple[str, str], ...]] = {
    SCOPE: (
        ("cell", "cell()  셀 좌표·발견/확증 분할 경계·측정 가능한 노출축"),
        ("vocab", "vocab(부분)  닫힌 어휘. 부분 = 채널|계열족|변환"),
    ),
    GROUND: (
        ("events", "events(타입일부)  이 셀의 장중 사건 타입. 없으면 없다고 답한다"),
        ("news", "news()  사건의 근거 문서 제목·도달 시각 (접지의 뿌리)"),
        ("thread", "thread()  스레드 단계·신규성 - 신규 보도인가 후속인가"),
    ),
    SCREEN: (
        ("screen", "screen()  발견 표본 격자 (타입×노출 전수). 탐색이지 확증이 아니다"),
        ("series", "series()  오늘 계열 혁신 z - 계열 방아쇠 자격 판정"),
        ("peers", "peers()  같은 산업 피어 수 - 관계 노출·위약군 재료"),
    ),
    EMIT: (),
}

GUARDS: dict[str, str] = {
    SCOPE: "셀을 한 번 봤으면 넘어간다",
    GROUND: "사건을 하나 확인하거나 **없다는 것**을 확인해야 넘어간다",
    SCREEN: "역사를 봐야 노출축을 고를 수 있다 (screen 또는 series 1회)",
    EMIT: "",
}
MAX_TURNS = 8


@dataclass
class Machine:
    """상태와 그 상태에서 실제로 일어난 일. 도구 응답이 곧 전이 조건이다."""

    catalog: object
    state: str = SCOPE
    grounded: int = 0          # 사건 존재를 확인한 횟수
    absent: int = 0            # **없다**는 것을 확인한 횟수 (부재도 증거다)
    screened: int = 0          # 역사 조회 횟수
    calls: list[str] = field(default_factory=list)

    def menu(self) -> str:
        rows = MENUS[self.state]
        if not rows:
            return "도구 없음. 이제 튜플을 내라."
        body = "\n".join(f"  {d}" for _, d in rows)
        return (f"[{self.state}] 지금 부를 수 있는 것 — 여기 없는 이름은 존재하지 않는다\n"
                f"{body}\n  조건: {GUARDS[self.state]}")

    def observe(self, name: str, arg: str = "") -> str:
        """도구 호출. 상태 밖 도구는 **그 사실을 알려준다** - 그것도 관측이다."""
        allowed = {n for n, _ in MENUS[self.state]}
        if name not in allowed:
            return (f"[{self.state}] 에는 {name!r} 이 없다. "
                    f"지금 부를 수 있는 것: {', '.join(sorted(allowed)) or '없음'}")
        out = self.catalog.call(name, arg)
        self.calls.append(f"{self.state}:{name}({arg})")
        if name == "events":
            if out.startswith("사건 없음"):
                self.absent += 1
            else:
                self.grounded += 1
        if name in ("news", "thread") and not out.startswith(("오류", "근거 문서 없음", "스레드 없음")):
            self.grounded += 1
        if name in ("screen", "series"):
            self.screened += 1
        if not self._blocked():
            return out + "\n\n" + self._advance()
        return out

    def _blocked(self) -> str:
        if self.state == SCOPE:
            return "" if self.calls else "먼저 cell() 로 무엇을 설명할지 봐라"
        if self.state == GROUND:
            return ("" if self.grounded or self.absent else
                    "사건을 확인하지 않았다. events() 로 있는지 없는지 확인해라")
        if self.state == SCREEN:
            return "" if self.screened else "역사를 안 봤다. screen() 또는 series() 를 써라"
        return ""

    def _advance(self) -> str:
        if (why := self._blocked()):
            return f"아직 못 넘어간다: {why}"
        self.state = ORDER[min(ORDER.index(self.state) + 1, len(ORDER) - 1)]
        trace("fsm.advance", state=self.state, calls=len(self.calls))
        return f"→ {self.state}\n{self.menu()}"

    @property
    def done(self) -> bool:
        return self.state == EMIT

    def stats(self) -> dict:
        return {"state": self.state, "grounded": self.grounded, "absent": self.absent,
                "screened": self.screened, "calls": list(self.calls)}
