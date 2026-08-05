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

from collections import Counter
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
    # `stability` 가 요구하는 노출 축("계열족/변환"). 없으면 안정성이 판정불가로 남고
    # 등급이 **정합에서 멈춘다** - 그건 결함이 아니라 정직이다: 무엇을 안정적으로
    # 재현했는지 말할 수 없으면 확증이라 부를 수 없다. 호출자가 격자·튜플에서 찾은
    # 축을 넘겨야 한다(신뢰성 에이전트가 스스로 고르면 표본을 고르는 것이 된다).
    exposure: str = ""


@dataclass(slots=True)
class Finding:
    """주장 하나에 대한 판정. `tier` 가 **근거의 등급**이다."""

    claim: Claim
    tier: str                        # 확증 | 정합 | 회계 | 반증 | 부재
    why: str
    used: dict[str, dict] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        """설명으로 내보낼 수 있는가. **정합도 설명이다** - 확증만 설명이면 아무 말도
        못 한다(실측: 30일 배치에서 유의 사건 0건). 반증·부재만 내보내지 않는다."""
        return self.tier in ("확증", "정합", "회계")


# 근거 등급 — 금융권 실무의 층과 같다. **모든 주장이 ATT 일 수는 없다.**
#
# 왜 등급이 필요한가: 확증(대조군 ATT + 유의 + 안정)을 유일한 통과 기준으로 두면
# 실측에서 30일 내내 유의 사건이 0 건이라 산출이 전부 '미뒷받침' 이 된다. 그건 정직한
# 게 아니라 **쓸 수 없는 것**이다. 감사가 요구하는 것은 '모든 문장이 인과 확증' 이
# 아니라 '각 문장의 근거 등급이 명시되고 과장되지 않음' 이다.
#
#   확증  대조군 ATT 가 유의하고 방향이 맞고 기간을 갈라도 재현된다
#   정합  관측이 주장과 **방향이 맞고** 반증하는 도구가 없다 (동종·기저율·수급·거시)
#   회계  시간·횡단면 항등식 - 합이 맞는다. 검정 대상이 아니다
#   반증  도구가 주장을 **부정**했다. 이건 다시 쓰라는 신호다
#   부재  못 쟀다. '효과 없음' 이 아니다
TIERS = ("확증", "정합", "회계", "반증", "부재")
ALPHA = 0.05


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
            out.append(Finding(c, "부재", f"주장 유형 '{c.kind}' 이 어휘에 없다 - "
                                          f"쓸 수 있는 것은 {sorted(NEED)}"))
            continue

        # ① 접지: 인용한 id 가 실제로 있는가
        bad = [r for r in c.refs if grounded is not None and r not in grounded]
        if bad:
            out.append(Finding(c, "반증", f"접지 밖 근거를 인용했다: {bad}"))
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
                    etype=c.etype, exposure=c.exposure)
                rounds -= 1
            except (SurfaceError, TypeError) as exc:
                # 인자가 안 맞으면 그건 배선 결함이다 - 사유를 남기고 넘어간다.
                miss.append(f"{name}({type(exc).__name__})")

        refuted = [f"{n}: {str(r.get('note') or r.get('reason') or '')[:80]}"
                   for n, r in used.items() if r.get("supports") is False]
        unmeasured = [f"{n}({str(r.get('reason') or '')[:46]})"
                      for n, r in used.items()
                      if str(r.get("verdict", "")) not in ("성립", "계산됨")]
        ok_dir, dir_msg = _direction_ok(c, used)
        why: list[str] = []

        # 등급 판정 — 순서가 뜻이다. 반증이 있으면 다른 무엇도 그것을 덮지 못한다.
        if refuted:
            tier, why = "반증", ["도구가 주장을 부정한다: " + " / ".join(refuted)]
        elif c.kind == "크기":
            tier = "회계"
        elif not used or len(unmeasured) == len(used):
            tier = "부재"
            why = ["재본 도구가 전부 판정불가: " + " / ".join(unmeasured or miss)
                   + " — 이 축은 **검토되지 않았다**(효과 없음이 아니다)"]
        elif not ok_dir:
            tier, why = "부재", [dir_msg]
        else:
            # 확증 승격: ATT 가 유의하고 안정성이 '재현' 이어야 한다. 둘 중 하나라도
            # 없으면 **정합** 이고, 무엇이 없어서 정합인지 적는다 - 등급만 주면
            # 읽는 사람이 확증과 구별하지 않는다.
            att, stab = used.get("run_trial", {}), used.get("stability", {})
            pv = att.get("p")
            if (str(att.get("verdict", "")) == "계산됨" and pv is not None
                    and float(pv) < ALPHA and str(stab.get("stable", "")) == "재현"):
                tier = "확증"
            else:
                tier = "정합"
                gap = []
                if str(att.get("verdict", "")) != "계산됨" or pv is None:
                    gap.append("대조군 ATT 미계측")
                elif float(pv) >= ALPHA:
                    gap.append(f"ATT 무유의(p={float(pv):.3f})")
                if str(stab.get("stable", "")) != "재현":
                    gap.append(f"안정성 {stab.get('stable') or '미계측'}")
                if gap:
                    why = ["확증까지 남은 것: " + " · ".join(gap)]
        if unmeasured and tier not in ("반증", "부재"):
            why.append("일부 판정불가: " + " / ".join(unmeasured))
        if miss and tier != "반증":
            why.append("재보지 못한 도구: " + " · ".join(miss))

        out.append(Finding(c, tier, " / ".join(why) or "요구 도구가 모두 지지한다",
                           used, tuple(miss)))
    return out


def report(findings: list[Finding]) -> str:
    """감사가 읽을 표. **반증·부재를 먼저** 쓴다 - 통과한 것부터 쓰면 아래를 안 읽는다."""
    order = {"반증": 0, "부재": 1, "정합": 2, "확증": 3, "회계": 4}
    rows = sorted(findings, key=lambda f: order.get(f.tier, 9))
    cnt = Counter(f.tier for f in findings)
    head = " · ".join(f"{k} {cnt[k]}" for k in TIERS if cnt[k])
    out = [f"-- 신뢰성 검사: 주장 {len(findings)}건 · {head} --"]
    for f in rows:
        out.append(f"  [{f.tier}] {f.claim.text[:58]}")
        if f.why and f.why != "요구 도구가 모두 지지한다":
            out.append(f"        {f.why}")
        if f.used:
            out.append(f"        근거 도구: {' · '.join(sorted(f.used))}")
    return "\n".join(out)


__all__ = ["ALPHA", "Claim", "Finding", "MAX_ROUNDS", "NEED", "TIERS",
           "check", "report"]
