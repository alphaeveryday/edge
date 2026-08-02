"""동적 도구 컨텍스트 상태기계 — 순서를 프롬프트가 아니라 코드가 지킨다.

STORM dyntool 실험의 이식. 두 규율:

1. **그 상태에 없는 도구는 존재하지 않는다.** 이름조차 안 보인다 - 어휘를 통째로
   주면 모델은 고를 수 있는 것만 고르고, 못 고른 이유가 기록에 안 남는다.
2. **진행은 관측으로만 이뤄진다.** 가드를 코드가 검사한다 - "먼저 X 를 보라"고
   프롬프트로 부탁하면 지켜지는 날과 안 지켜지는 날이 생기고, 그 차이가 세션 분산이다.

## 최적화 (19R) - 라이브 실측이 시킨 것 셋

- **브리핑은 상태가 아니다.** 셀 좌표·커버리지는 결정론이고 언제나 필요하다.
  물어보고 답을 받는 데 LLM 왕복을 쓸 이유가 없다 → 생성 시 자동 실행해 첫
  프롬프트에 실어 준다. 상태가 넷에서 셋으로 줄었다(SCOPE 소멸).
- **탐색 도구는 게이트 밖.** `peek`·`tables`·`vocab` 은 어느 단계에서도 답이
  달라지지 않는다. 가두면 "표를 보려고 단계를 넘기는" 왜곡이 생긴다.
- **배치 호출.** 한 턴에 여러 도구를 부를 수 있다. 3콜 = 3왕복이던 것이 1왕복이
  된다(실측: 셀당 LLM 왕복 4 → 2).

가드에는 STORM 의 교훈이 그대로 남는다: **부재도 증거다.** 사건이 0인 셀에서
긍정 증거만 요구하면 모델이 창을 넓혀 가며 턴을 태운다(실측).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..observability import record as trace

GROUND, SCREEN, EMIT = "GROUND", "SCREEN", "EMIT"
ORDER = (GROUND, SCREEN, EMIT)

# 어느 단계에서나 부를 수 있다 - 단계에 따라 답이 달라지지 않는 순수 탐색.
FREE: tuple[tuple[str, str], ...] = (
    ("tables", "tables(이름일부)  묶인 표 전량과 그날 행수"),
    ("peek", "peek(표이름)  그 표의 열과 표본 3행 - 탐색의 종점, 아무 표나"),
    ("vocab", "vocab(부분)  닫힌 어휘. 부분 = 채널|계열족|변환"),
)

MENUS: dict[str, tuple[tuple[str, str], ...]] = {
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
    GROUND: "사건을 하나 확인하거나 **없다는 것**을 확인해야 넘어간다",
    SCREEN: "역사를 봐야 노출축을 고를 수 있다 (screen 또는 series 1회)",
    EMIT: "",
}


@dataclass
class Machine:
    """상태와 그 상태에서 실제로 일어난 일. 도구 응답이 곧 전이 조건이다."""

    catalog: object
    state: str = GROUND
    grounded: int = 0          # 사건 존재를 확인한 횟수
    absent: int = 0            # **없다**는 것을 확인한 횟수 (부재도 증거다)
    screened: int = 0          # 역사 조회 횟수
    calls: list[str] = field(default_factory=list)

    def brief(self) -> str:
        """결정론적 사전 관측. 묻지 않고 그냥 준다 - 물어볼 값이 없는 질문이다."""
        return "\n".join(self.catalog.call(n) for n in ("cell", "coverage"))

    def allowed(self) -> tuple[tuple[str, str], ...]:
        return MENUS[self.state] + (FREE if self.state != EMIT else ())

    def menu(self) -> str:
        rows = self.allowed()
        if not rows:
            return "도구 없음. 이제 튜플을 내라."
        body = "\n".join(f"  {d}" for _, d in rows)
        return (f"[{self.state}] 지금 부를 수 있는 것 — 여기 없는 이름은 존재하지 않는다\n"
                f"{body}\n  조건: {GUARDS[self.state]}")

    def observe(self, name: str, arg: str = "") -> str:
        """도구 호출. 상태 밖 도구는 **그 사실을 알려준다** - 그것도 관측이다."""
        names = {n for n, _ in self.allowed()}
        if name not in names:
            return (f"[{self.state}] 에는 {name!r} 이 없다. "
                    f"지금 부를 수 있는 것: {', '.join(sorted(names)) or '없음'}")
        out = self.catalog.call(name, arg)
        self.calls.append(f"{self.state}:{name}({arg})")
        # 캐시 표식(`[이미 본 것…]`)을 벗기고 분류한다 - 접두사가 붙으면 미도달을
        # 접지로 오인한다 (테스트가 잡은 실제 버그).
        body = out.split("]\n", 1)[-1] if out.startswith("[") else out
        blocked = body.startswith(("오류", "미도달", "빈 표"))
        if name == "events":
            # 미도달은 부재가 아니다 - 적재 지평이 셀보다 늦으면 아무것도 확인 못 했다.
            if body.startswith("사건 없음"):
                self.absent += 1
            elif not blocked:
                self.grounded += 1
        elif name in ("news", "thread"):
            if not (blocked or body.startswith(("근거 문서 없음", "스레드 없음"))):
                self.grounded += 1
        elif name in ("screen", "series") and not blocked:
            self.screened += 1
        return out if self._blocked() else out + "\n\n" + self._advance()

    def _blocked(self) -> str:
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
