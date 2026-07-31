"""고객 문장 — **수치는 코드가 만들고, 문구는 슬롯에 끼운다.**

왜 LLM 에게 문장 전체를 맡기지 않나. 실험판에서 모델이 보고한 수치는 날조였다
(`p=0.37` 인데 검정을 한 번도 안 불렀고, 다른 실행에서는 퇴화한 귀무의 `p=1/1001` 을
버리고 `p=0.077` 을 손으로 써 넣었다). 문장을 생성하게 하면 그 자리가 다시 열린다.

그래서 여기서는:
  · 숫자·부호·판정은 **전부 계산된 값**에서 온다. 포맷팅만 한다.
  · 메커니즘 문구는 DAG 의 `because` 를 그대로 인용한다 - 이미 반증층을 통과한 문장이다.
  · 검정되지 않은 것은 **문장에 넣지 않는다.** 대신 무엇이 없어서 못 했는지 말한다.

톤은 고객용이다. `estimand`·`backdoor`·`p-value` 같은 말을 쓰지 않는다. 다만 확신을
과장하지도 않는다 - "설명하지 못했다"가 일급 산출이다(explanation-framework §0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 판정 어휘는 domain.models._VERDICT_TO_TYPE 의 키와 **정확히** 같아야 한다.
# 다른 문자열을 쓰면 조용히 UNCERTAIN 으로 떨어진다.
VERDICT_EVENT = "공식 이벤트 선행"
VERDICT_MIXED = "시장·섹터 주도"
VERDICT_LAGGED = "가격 선행·설명 후행"
VERDICT_FLOW = "수급·흐름 추정"
VERDICT_NONE = "원인 미확인"

# 확신도 어휘. `높음`은 **표본외 확증이 있을 때만** 쓴다 - 단일 패스에서는 나오지 않는다
# (`_verdict` 참고). 어휘를 지우지 않는 이유는 재현 검정이 붙으면 그 자리가 되기 때문이다.
CONF_HIGH, CONF_MID, CONF_LOW = "높음", "중간", "보류"


def _pct(x: float | None, sign: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x * 100:+.2f}%" if sign else f"{x * 100:.2f}%"


def _updown(x: float) -> str:
    return "올랐" if x >= 0 else "내렸"


def _josa(word: str, with_jong: str, without_jong: str) -> str:
    """받침에 따라 조사를 고른다. 고객이 읽는 문장이라 "모멘텀가" 를 낼 수 없다."""
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return with_jong if (ord(ch) - 0xAC00) % 28 else without_jong
        if ch.isalnum():
            return with_jong if ch.isdigit() or ch.lower() in "lmnr" else without_jong
    return without_jong


@dataclass(frozen=True, slots=True)
class EdgeFinding:
    """검정을 마친 간선 하나. **수치는 원장에서 온 것만 담는다.**"""

    cause: str                      # 고객이 읽을 원인 이름 (종목명·사건 요약)
    because: str                    # DAG 의 메커니즘 문구. 반증층을 통과한 것
    effect: float | None            # 단위당 추정 효과
    p: float | None                 # placebo 가 낸 값
    n: int                          # 검정 표본
    share: float | None = None      # ETF 내 비중
    contribution: float | None = None   # share * effect
    survived: bool = False          # 게이트 전부 통과 + 유의
    killed_by: str | None = None    # 죽은 이유 (산술·검정력·유의성)


@dataclass(frozen=True, slots=True)
class CausalReport:
    """당일 한 셀의 인과 산출 전체. 서술은 이것만 읽는다."""

    etf_name: str
    trade_date: str
    observed: float                 # 관측 등락
    residual: float                 # 시장·피어 제거 후 남은 것
    top_contributors: list[tuple[str, float]] = field(default_factory=list)
    findings: list[EdgeFinding] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    local_violations: list[str] = field(default_factory=list)
    spec_sensitive: bool = False    # 원장의 p 가 α 를 가로지른다 = 결론이 사양에 달렸다
    missing: list[str] = field(default_factory=list)   # 무엇이 없어서 못 했나
    route_code: str | None = None
    # ── 감사 흔적. 고객 문장에는 안 들어가지만 **아카이브에는 반드시 남는다** ──
    proofs: list[dict[str, Any]] = field(default_factory=list)          # 설계·산문·원장
    falsification_surface: list[str] = field(default_factory=list)      # 함의 조건부독립
    data_requests: list[dict[str, Any]] = field(default_factory=list)   # 못 잰 것의 요청


def _headline(r: CausalReport, live: list[EdgeFinding]) -> str:
    move = f"{r.etf_name} {_pct(r.observed)}"
    if live:
        return f"{move} — {live[0].cause}"
    if r.top_contributors:
        return f"{move} — {r.top_contributors[0][0]} 기여"
    return f"{move} — 원인 미확인"


def _verdict(r: CausalReport, live: list[EdgeFinding]) -> tuple[str, str]:
    """판정과 확신도. **검정을 통과한 것이 없으면 확신을 만들지 않는다.**

    단일 패스에는 **표본외 확증이 없다.** 그래서 유의한 간선이 있어도 확신도는 '중간'을
    넘기지 않는다 - '높음'은 같은 설계가 다른 표본에서 재현됐을 때 쓸 말이고, 지금
    파이프라인은 그 단계를 돌지 않는다. 감사 블록에 `status: 미확증`으로 남는다.

    원장의 p 가 α 를 가로지르면(`spec_sensitive`) 확신도를 한 칸 더 내린다. 같은 간선을
    여러 사양으로 재서 어떤 칸은 유의하고 어떤 칸은 아니었다면, 보고된 유의는 사양 선택의
    산물일 수 있다. 게이트로 죽이지 않고 확신도로 반영하는 이유는, 여러 사양을 시도하는
    것 자체가 정직한 탐색이어서다 - 막으면 모델이 한 번만 재고 끝낸다.
    """
    if live:
        return VERDICT_EVENT, (CONF_LOW if r.spec_sensitive else CONF_MID)
    if abs(r.residual) < abs(r.observed) * 0.5:
        # 잔차가 관측의 절반도 안 되면 움직임의 대부분이 시장·섹터에서 왔다
        return VERDICT_MIXED, CONF_MID
    if r.route_code and "FLOW" in r.route_code.upper():
        return VERDICT_FLOW, CONF_LOW
    return VERDICT_NONE, CONF_LOW


def _body(r: CausalReport, live: list[EdgeFinding], dead: list[EdgeFinding]) -> str:
    L = [f"{r.trade_date} {r.etf_name}가 {_pct(r.observed)} {_updown(r.observed)}습니다."]

    explained = abs(r.observed) - abs(r.residual)
    if abs(r.observed) > 0 and explained > 0:
        L.append(f"이 중 시장·업종 흐름으로 설명되는 부분을 빼면 "
                 f"{_pct(r.residual)}가 이 ETF 고유의 움직임입니다.")
    if r.top_contributors:
        top = ", ".join(f"{name}({_pct(c)})" for name, c in r.top_contributors[:3])
        L.append(f"등락에 가장 크게 기여한 종목은 {top} 입니다.")

    for f in live:
        piece = f"{f.cause}{_josa(f.cause, '이', '가')} 원인으로 확인됐습니다."
        if f.because:
            piece += f" {f.because.rstrip('.')}."
        if f.contribution is not None:
            piece += (f" 이 경로가 설명하는 폭은 약 {_pct(f.contribution)} 입니다"
                      f" (비중 {_pct(f.share, sign=False)} × 효과 {_pct(f.effect)}).")
        L.append(piece)

    if not live:
        L.append("공개된 뉴스·공시 가운데 이 움직임을 설명할 수 있는 원인은 "
                 "확인되지 않았습니다.")
        for f in dead[:2]:
            if f.killed_by:
                L.append(f"{f.cause}{_josa(f.cause, '을', '를')} 검토했습니다. "
                         f"{f.killed_by}")
    # 무엇이 없어서 못 했는지는 **고객 문장에도** 남긴다. "확인되지 않았다"와
    # "자료가 없어 확인하지 못했다"는 다른 말이고, 후자는 다음 수집 의제이기도 하다.
    wants = list(r.missing) or [str(q.get("need")) for q in r.data_requests if q.get("need")]
    if wants:
        L.append("확인에 필요했지만 확보하지 못한 자료: " + ", ".join(wants[:3]) + ".")
    return " ".join(L)


def narrate(r: CausalReport) -> dict[str, Any]:
    """`Explanation.raw` 계약에 맞는 dict 를 만든다.

    반환 키는 `domain.models.Explanation` 이 읽는 것과 **정확히** 맞춘다:
    `verdict`·`explain`·`headline`·`confidence`. 나머지는 런 아카이브가 보존한다
    (DB 매핑이 버리는 필드를 남기는 게 raw 의 목적이다).

    `causal` 블록은 **감사 흔적**이다. 여기에 설계(술어·층화·조정집합)·산문(주장·메커니즘·
    반증조건)·원장(placebo 호출 전량)·에이전트가 쓴 코드·반증 표면·데이터 요청이 다 들어
    간다. 이게 없으면 게이트를 통과했다는 사실만 남고 **통과의 증거가 사라진다.**
    """
    live = [f for f in r.findings if f.survived]
    dead = [f for f in r.findings if not f.survived]
    verdict, confidence = _verdict(r, live)
    return {
        "verdict": verdict,
        "confidence": confidence,
        "headline": _headline(r, live),
        "explain": _body(r, live, dead),
        # 감사용. 고객 문장에 안 들어가지만 아카이브에 남는다.
        "causal": {
            "residual": r.residual,
            "route_code": r.route_code,
            # 단일 패스에는 표본외 확증이 없다. 있는 척하지 않는다.
            "status": ("미확증(표본외 검정 없음)" if live else "게시 가능한 인과 주장 없음"),
            "survived": [{"cause": f.cause, "because": f.because, "effect": f.effect,
                          "p": f.p, "n": f.n, "share": f.share,
                          "contribution": f.contribution} for f in live],
            "rejected": [{"cause": f.cause, "killed_by": f.killed_by} for f in dead],
            "proofs": r.proofs,
            "falsification_surface": r.falsification_surface,
            "data_requests": r.data_requests,
            "budget": r.budget,
            "local_violations": r.local_violations,
            "spec_sensitive": r.spec_sensitive,
            "missing": r.missing,
        },
    }
