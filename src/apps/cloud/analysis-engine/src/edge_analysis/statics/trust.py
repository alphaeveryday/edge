"""신뢰성 검사 — 이미 쓴 주장에 **근거가 실제로 붙는지** 확인하고, 부족하면 도구를 더 부른다.

금융권 납품에서 산문이 깨지는 방식은 셋뿐이다. 셋 다 여기서 막는다.

1. **없는 근거를 인용한다.** 접지 밖 id -> 즉사(`narrate` 가 이미 막는다).
2. **있는 근거가 그 주장을 지지하지 않는다.** 근거는 '못 가름' 인데 산문은 방향을
   말한다. 부호 가드가 일부를 막지만, "이 사건이 원인이다" 처럼 **강도**가 근거를
   넘는 경우는 못 막았다 - 그게 이 모듈의 자리다.
3. **그 주장을 지지할 통계를 아예 안 재봤다.** 가장 위험하다: 감사는 "왜 이건 안
   봤나" 를 찍고, 우리는 "볼 수 있었는데 안 봤다" 와 "볼 수 없었다" 를 구분해 답할
   수 있어야 한다. 그래서 주장 유형별로 **요구 도구**를 못박고, 빠진 것은 부른다
   (피드백 루프). 부를 수 없으면 사유가 남는다.

세 에이전트(가설·검정·신뢰성)는 `surface.TOOLS` 라는 **같은 표면**만 본다. 신뢰성이
새 통계를 발명할 길이 없으므로, 세 번째 에이전트가 첫 번째의 접지를 넘어설 수 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .surface import SurfaceError, available, call

# 라운드 상한. 무한 루프를 막기 위한 값이 아니라 **결정론**을 위한 값이다:
# 상한이 없으면 같은 셀이 두 번 다른 근거 묶음을 낼 수 있고 재현이 깨진다.
MAX_ROUNDS = 2

# 주장 유형 -> 그 주장을 지지하려면 **반드시 재봐야 하는** 도구.
#
# 왜 유형별로 다른가: "실적 발표로 올랐다" 는 사건 주장이라 (a) 그 관계가 패널에서
# 성립하는지 (b) 이 크기가 애초에 드문 일인지 (c) 기간을 갈라도 재현되는지 셋이 다
# 필요하다. 반면 "해외가 내려서" 는 거시 발화와 동종 대비만 있으면 된다 - 거기에
# 사건 패널을 요구하면 판정불가가 쏟아지고 산문이 이유 없이 침묵한다.
NEED: dict[str, tuple[str, ...]] = {
    # `edge_test` 를 안 쓴다: 그건 **튜플**(사건타입×노출×취약성)을 요구하고, 신뢰성
    # 에이전트는 튜플을 만들지 않는다(만들면 가설 에이전트가 둘이 된다). 사건 주장을
    # 지지하는 최소 단위는 매칭 대조군 ATT 다 - 그건 사건타입 하나로 돌아간다.
    "사건": ("run_trial", "base_rate", "stability"),
    "거시": ("macro_z", "peer_rank"),
    "수급": ("flow_detail",),
    "사업": ("business_mix",),
    "계열": ("series_z", "base_rate"),
    # 크기 주장은 **회계**다(시간 항등식). 검정 도구를 요구하지 않는다 - 요구하면
    # 합이 맞는 사실에 p 를 붙이라는 말이 되고, 그게 §11 위반이다.
    "크기": (),
}


@dataclass(frozen=True, slots=True)
class Claim:
    """검사 대상 주장 하나. `kind` 는 `NEED` 의 키여야 한다 - 자유 문자열이 아니다."""

    text: str
    kind: str
    refs: tuple[str, ...] = ()
    sign: int = 0                    # -1 내림 · 0 방향 없음 · +1 오름
    # 사건 주장이 **어느 사건타입**을 말하는지. 없으면 `run_trial`·`stability` 가
    # 필수 인자 부재로 막히고 그 사실이 사유로 남는다 - 아무 타입이나 끼워 넣어
    # 검정을 통과시키는 길을 열지 않는다.
    etype: str = ""


@dataclass(slots=True)
class Finding:
    """주장 하나에 대한 판정. `supported` 는 **셋 다 통과**했을 때만 True."""

    claim: Claim
    supported: bool
    why: str
    used: dict[str, dict] = field(default_factory=dict)
    missing: tuple[str, ...] = ()


def _direction_ok(claim: Claim, used: dict[str, dict]) -> tuple[bool, str]:
    """방향 주장은 **부호 있는 근거 하나 이상**이 지지해야 한다.

    도구는 `signed` 키로 '부호가 뜻을 갖는 양' 을 스스로 신고한다. 키 이름을 추측하면
    안 되는 이유가 실측으로 나왔다: `macro_z` 의 `z` 는 설계상 **절댓값 최대**(발화
    크기)라서 늘 양수인데, 그것을 방향 근거로 읽어 "내렸어요" 를 부호 불일치로
    기각했다. 발화 크기는 '얼마나 이례적인가' 이고 방향은 다른 질문이다.

    부호 있는 근거가 하나도 없으면 **미뒷받침**이다. 통과시키면 방향 주장이 크기
    근거만으로 서게 되고, 그게 금융권 감사가 찍는 지점이다.
    """
    if claim.sign == 0:
        return True, ""
    seen = []
    for name, res in used.items():
        v = str(res.get("verdict", ""))
        if v not in ("성립", "계산됨"):
            continue                        # 못 가른 근거는 방향을 못 준다
        x = res.get("signed")
        if x is None:
            continue
        try:
            val = float(x)
        except (TypeError, ValueError):
            continue
        if val == 0.0:
            continue
        if (val > 0) != (claim.sign > 0):
            return False, (f"{name}: 주장 방향({claim.sign:+d})이 근거 "
                           f"{val:+.4g} 의 부호와 다르다")
        seen.append(name)
    if not seen:
        return False, ("방향을 지지할 **부호 있는 근거가 없다** - 재본 도구들은 크기나 "
                       "발화만 말하고 방향을 말하지 않는다")
    return True, ""


def check(lake, claims: list[Claim], *, day: str, instrument_id: str,
          grounded: dict[str, dict] | None = None,
          probe: dict[str, dict] | None = None,
          rounds: int = MAX_ROUNDS) -> list[Finding]:
    """주장들을 검사하고, 요구 도구가 빠졌으면 **부른다**(최대 `rounds` 회).

    `probe` 는 이번 셀에서 이미 돈 도구 결과 캐시다 - 같은 도구를 주장마다 다시 부르면
    같은 답에 왕복만 늘고, 무엇보다 순열검정 비용이 주장 수만큼 곱해진다.
    """
    got = dict(probe or {})
    have = {t.name for t in available(lake)}
    out: list[Finding] = []

    for c in claims:
        if c.kind not in NEED:
            out.append(Finding(c, False, f"주장 유형 '{c.kind}' 이 어휘에 없다 - "
                                         f"쓸 수 있는 것은 {sorted(NEED)}"))
            continue

        # ① 접지: 인용한 id 가 실제로 있는가
        bad = [r for r in c.refs if grounded is not None and r not in grounded]
        if bad:
            out.append(Finding(c, False, f"접지 밖 근거를 인용했다: {bad}"))
            continue

        # ② 요구 도구를 모은다. 없으면 부른다 - 이게 피드백 루프다.
        need, miss, used = NEED[c.kind], [], {}
        for name in need:
            if name in got:
                used[name] = got[name]
                continue
            if name not in have:
                miss.append(name)               # 데이터 부재 - 부를 수 없다
                continue
            if rounds <= 0:
                miss.append(name)
                continue
            try:
                got[name] = used[name] = call(
                    lake, name, day=day, instrument_id=instrument_id,
                    etype=c.etype)
                rounds -= 1
            except (SurfaceError, TypeError) as exc:
                # 인자가 안 맞으면 그건 배선 결함이다 - 사유를 남기고 넘어간다.
                miss.append(f"{name}({type(exc).__name__})")

        why: list[str] = []
        # ③ **판정불가 도구는 미충족이다.** 이걸 빼먹어 실측에서 최악의 통과가 났다:
        # `base_rate` 가 "사건일 분포에서 오늘은 상위 90% 로 무조건 분포보다 극단이
        # 아니다 - 사건 귀속의 근거가 되지 못한다" 고 반증했고 `stability` 는 판정불가
        # 인데, `run_trial` 의 ATT 부호 하나만 보고 "실적 발표가 상승을 이끌었어요" 를
        # **뒷받침**으로 찍었다. 도구를 부르고도 그 답을 안 읽은 것이다.
        for name, res in used.items():
            v = str(res.get("verdict", ""))
            if v not in ("성립", "계산됨"):
                why.append(f"{name}: {v or '판정 없음'} — "
                           f"{str(res.get('reason') or '사유 없음')[:80]}")
            # 도구가 스스로 **반증**을 신고할 수 있다(`supports=False`). 도구별 특수
            # 규칙을 여기 박으면 결합이 생기므로, 판단은 도구가 하고 집계만 한다.
            elif res.get("supports") is False:
                why.append(f"{name}: 이 도구는 주장을 **지지하지 않는다** — "
                           f"{str(res.get('note') or res.get('reason') or '')[:90]}")

        # ④ 강도: 방향 주장은 부호 있는 근거가 필요하다
        ok, msg = _direction_ok(c, used)
        if not ok:
            why.append(msg)

        if miss:
            why.append("재보지 못한 도구: " + " · ".join(miss)
                       + " — 이 축은 **검토되지 않았다**(효과 없음이 아니다)")
        out.append(Finding(c, not why, " / ".join(why) or "요구 도구가 모두 지지한다",
                           used, tuple(miss)))
    return out


def report(findings: list[Finding]) -> str:
    """감사가 읽을 표. **미뒷받침을 먼저** 쓴다 - 통과한 것부터 쓰면 아래를 안 읽는다."""
    bad = [f for f in findings if not f.supported]
    good = [f for f in findings if f.supported]
    out = [f"-- 신뢰성 검사: 주장 {len(findings)}건 · 미뒷받침 {len(bad)}건 --"]
    for f in bad:
        out.append(f"  [미뒷받침] {f.claim.text[:60]}")
        out.append(f"            {f.why}")
    for f in good:
        out.append(f"  [뒷받침] {f.claim.text[:60]}"
                   f" · 근거 도구 {sorted(f.used) or '없음(크기 주장)'}")
    return "\n".join(out)


__all__ = ["Claim", "Finding", "MAX_ROUNDS", "NEED", "check", "report"]
