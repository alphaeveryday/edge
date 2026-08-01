"""P8 · 처분 — **검토한 전건에 판정을 남긴다. 침묵은 판정이 아니다.**

NTSB 사고보고서 형식을 그대로 쓴다. Probable Cause 는 단수, Contributing 은 복수,
그리고 **not contributing 도 명시 판정**이다. 사고 조사가 이 형식을 고집하는 이유는
결론이 아니라 **조사 범위**를 복원 가능하게 하려는 것이다 - 보고서에 없는 후보는
"검토했는데 기여하지 않았다"인지 "아예 안 봤다"인지 구분되지 않고, 그 구분이 다음
조사의 출발점이기 때문이다.

우리 산출이 정확히 그 실패 모드였다. 산술 게이트에서 죽은 후보는 로그에만 남고 문장에서
사라졌고, 지문이 못 잰 축은 어디에도 남지 않았다. 그래서 "원인 미확인" 한 문장이
*찾아봤는데 없다* 와 *볼 자료가 없었다* 를 같은 말로 덮었다. 여기서는 셋으로 가른다:
contributing / not_contributing / undetermined - **검토한 것은 반드시 셋 중 하나다.**

두 번째 일은 **주장 상한**(`ClaimCeiling`)이다. 검정을 통과한 것과 원인으로 확인된 것은
다르다. 미소거 U 가 하나라도 남아 있으면 그 간선이 아무리 유의해도 결론은 "확인"이 아니라
"양립"이고, 고객 문장이 그 차이를 반영해야 한다. 이전 서술층은 유의한 간선을 그대로
"원인으로 확인됐습니다"로 옮겼고 그 문장은 교란이 몇 개 남았든 똑같이 나갔다. 그래서
여기서는 상한을 문장 생성의 **입력**으로 넣고(`_cause_sentence`), 위반이면 예외로 막는다
(`narrate`) - 규칙을 주석으로 적어두면 다음 사람이 지운다.

수치는 전부 입력에서 유도된 것만 쓴다. 실험판에서 모델이 보고한 `p=0.37` 은 검정을 한 번도
부르지 않고 나온 값이었다. 이 모듈은 포맷팅만 한다.

    dispose      전건 처분 -> `Findings`
    audit_block  감사 재료 -> dict. `Findings` 계약에 자리가 없어서 분리했다
    narrate      고객 문장 + `causal` 블록. audit 없이도 유효하고, 있으면 두꺼워진다

세 함수 전부 **무상태**다. 한 프로세스에서 셀 여러 개가 동시에 돌 수 있으므로 모듈 슬롯에
직전 산출을 들고 있으면 서로 갈린다.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..config import PipelineError
from ..observability import log
from .contracts import (
    ClaimCeiling,
    ConfoundingScreen,
    DiscriminationPlan,
    DOMAIN_SAY,
    DomainCoverage,
    Disposition,
    Findings,
    Fingerprint,
    Identification,
    Latent,
    Modality,
    NegativeControl,
    Question,
    Sensitivity,
    Verdict,
    WorldGraph,
)
from .verify import EdgeProof

# 판정 어휘는 domain.models._VERDICT_TO_TYPE 의 키와 **정확히** 같아야 한다.
# 다른 문자열을 쓰면 조용히 UNCERTAIN 으로 떨어진다.
VERDICT_EVENT = "공식 이벤트 선행"
VERDICT_MIXED = "시장·섹터 주도"
# `가격 선행·설명 후행` 은 지금 나오지 않는다 - 그 판정의 근거인 사전표류 축은 P1 지문에만
# 있고 `narrate` 는 `Findings` 만 받는다. 감사 블록(선택 인자)으로 판정을 가르면 같은
# Findings 가 호출 방식에 따라 다른 verdict 를 내므로, 어휘만 남기고 배선하지 않는다.
VERDICT_LAGGED = "가격 선행·설명 후행"
VERDICT_FLOW = "수급·흐름 추정"
VERDICT_NONE = "원인 미확인"

# 확신도 어휘. `높음`은 **표본외 확증이 있을 때만** 쓴다 - 단일 패스에서는 나오지 않는다.
# 어휘를 지우지 않는 이유는 재현 검정이 붙으면 그 자리가 되기 때문이다.
CONF_HIGH, CONF_MID, CONF_LOW = "높음", "중간", "보류"

CONFIRMED_PHRASE = "확인됐습니다"


# ── 포맷 ────────────────────────────────────────────────────────────────
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


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _iv(v: Any) -> list[float] | None:
    """구간을 JSON 으로. **폭을 접지 않는다** - mid 만 남기면 무지의 크기가 사라진다."""
    lo, hi = getattr(v, "lo", None), getattr(v, "hi", None)
    if lo is None or hi is None:
        try:
            lo, hi = float(v[0]), float(v[1])
        except (TypeError, IndexError, ValueError, KeyError):
            return None
    return [float(lo), float(hi)]


def _mid(v: Any) -> float | None:
    b = _iv(v)
    return (b[0] + b[1]) / 2 if b else None


def _plain(v: Any) -> Any:
    """직렬화 가능한 값만 남긴다. 지문 축의 `value` 는 배열·날짜 무엇이든 올 수 있고,
    아카이브에서 json 이 터지면 **감사 흔적이 통째로 사라진다** - 문자열로라도 남긴다."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return str(v)


def _put(**kv: Any) -> dict[str, Any]:
    """`None`·빈 값은 키째로 뺀다. P9 가 `None` 을 '쟀는데 비었다'로 읽지 않게 한다."""
    return {k: v for k, v in kv.items() if v is not None and v != "" and v != []}


# ── 색인 ────────────────────────────────────────────────────────────────
def _by_edge(proofs: list[EdgeProof]) -> dict[tuple[str, str], EdgeProof]:
    """간선당 증명 하나. **통과한 것을 대표로 삼는다.**

    같은 간선을 여러 사양으로 잰 원장에서 실패본을 대표로 잡으면 판정이 사양 선택으로
    뒤집힌다. 골라 쓰는 것 자체는 `spec_sensitive` 가 이미 표시하므로, 대표는 통과본으로
    두고 사양 의존은 확신도에서 깎는다(`_verdict`).
    """
    out: dict[tuple[str, str], EdgeProof] = {}
    for p in proofs or ():
        key = (p.design.src, p.design.dst)
        cur = out.get(key)
        if cur is None or (p.passed and not cur.passed):
            out[key] = p
    return out


def _sens_for(sens: list[Sensitivity], src: str, dst: str) -> Sensitivity | None:
    """P6 은 간선 키를 `"src->dst"` (ASCII) 로 낸다. 못 맞으면 포함 검사로 떨어진다."""
    key = f"{src}->{dst}"
    return next((s for s in sens or () if s.edge == key or (src in s.edge and dst in s.edge)),
                None)


def _excludes_zero(ident: Identification | None) -> bool:
    """Manski 구간이 0 을 배제하나. 점식별이 없어도 **부호는 주장할 수 있다.**"""
    b = ident.bounds if ident else None
    return bool(b) and (min(b) > 0 or max(b) < 0)


def _faults(controls: list[NegativeControl], screen: ConfoundingScreen | None) -> list[str]:
    """설계 결함 - **`confirmed` 를 막지만 기여 판정 자체는 못 뒤집는다.**

    음성 대조는 비대칭이다(P7). 통과는 검정력 한계일 수 있어 상한을 올릴 근거가 못 되고,
    **시끄러운 것만** 내린다. 그리고 `p is None` 은 미실행이지 반증이 아니다 - 실행조차
    못 한 대조를 설계 결함으로 세면 자료 부재가 증거로 둔갑한다.

    오염 검사 미실행(`checked=False`)도 같은 자리다. 사건창에 다른 공시를 낀 기업을
    걸러냈는지 모르는 상태의 "확인"은 Kothari-Warner 절차를 건너뛴 주장이다.
    """
    out: list[str] = []
    noisy = [c for c in controls or () if c.p is not None and not c.passed]
    if noisy:
        out.append(f"음성 대조 {len(noisy)}건이 시끄럽다: " + ", ".join(c.name for c in noisy[:2]))
    if screen is None or not screen.checked:
        why = f" - {screen.note}" if screen is not None and screen.note else ""
        out.append(f"사건창 오염 검사를 못 했다{why}")
    return out


# ── 판정 ────────────────────────────────────────────────────────────────
def _verdict_of(proof: EdgeProof | None, ident: Identification | None,
                *, over_budget: bool, residual: float = 0.0) -> tuple[Verdict, str]:
    """가설 하나의 처분. **'못 갈랐다'와 '아니다'를 같은 칸에 넣지 않는다.**

    둘을 합치면 다음 조사가 어디서 시작할지 알 수 없다. 검정이 성립조차 안 한 것은
    기여하지 않는다가 아니라 **모른다**다 - 게이트 실패·표본 부족·자료 부재가 전부
    여기 들어간다. 반대로 유의하지 않게 **재어졌다면** 그것은 판정이다.

    예산 초과가 `undetermined` 인 이유: 합이 잔차를 넘었다는 것은 어느 하나가 틀렸다는
    뜻이지 **어느 것이 틀렸는지**가 아니다. 그 상태에서 개별 간선을 살리면 상쇄 요인이
    기각된 뒤 남은 경로가 잔차를 혼자 넘긴 채 게시된다.
    """
    if proof is None:
        return "undetermined", "검정을 돌리지 못했다"
    if proof.status == "불가":
        need = (proof.data_request or {}).get("need") or "필요한 자료가 없다"
        return "undetermined", f"검정 불가 - {need}"
    if not proof.passed:
        return "undetermined", "검정이 성립하지 않았다: " + ("; ".join(proof.gate_fail[:2])
                                                        or proof.status)
    if not proof.significant:
        return "not_contributing", f"귀무와 구분되지 않았다 (p={proof.p:.3f}, n={proof.n})"
    # **부호가 잔차와 반대면 원인이 아니다.** 설명해야 할 것은 오늘의 움직임 하나이고,
    # 반대로 민 경로는 그 움직임을 만든 것이 아니라 상쇄한 것이다. 그걸 기여로 세면
    # 미설명분이 잔차보다 커지면서(상쇄분만큼) "원인으로 확인" 문장이 나간다 - 예산
    # 정합은 절댓값 합만 보므로 이 자리를 잡지 못한다.
    if residual and proof.effect is not None and proof.effect * residual < 0:
        return "not_contributing", (
            f"방향이 반대다 - 효과 {_pct(proof.effect)} 인데 설명 대상은 {_pct(residual)} 다. "
            "이 경로는 오늘의 움직임을 만든 것이 아니라 상쇄한 쪽이다")
    if over_budget:
        return "undetermined", "귀속 합이 잔차를 넘었다 - 어느 경로가 틀렸는지 여기서 못 가른다"
    if ident is None or ident.status == "not_identified":
        if _excludes_zero(ident):
            b = ident.bounds if ident else (0.0, 0.0)
            return "contributing", (f"점식별은 안 되나 구간이 0 을 배제한다 "
                                    f"[{_pct(min(b))}, {_pct(max(b))}]")
        blocked = ", ".join(ident.blocked_by[:2]) if ident and ident.blocked_by else ""
        return "undetermined", ("식별되지 않는다 - " + (f"뒷문이 열려 있다 ({blocked})" if blocked
                                                  else "뒷문을 막을 조정집합이 없다"))
    return "contributing", f"유의하게 재어졌다 (p={proof.p:.3f}, n={proof.n})"


def _ceiling(ident: Identification | None, proof: EdgeProof | None, uncleared: list[Latent],
             *, over_budget: bool, budget_note: str,
             faults: list[str]) -> tuple[ClaimCeiling, str]:
    """주장 상한 하나 + **무엇 때문에 강등됐는지 문장**. 그 문장이 산출물에 나간다.

    `confirmed` 의 필요조건은 넷이다: 예산이 맞고, 검정이 유의하게 통과했고, 간선이 가정
    없이 식별되고, **미소거 U 가 하나도 없다**. 하나라도 깨지면 강등한다. 설계 결함
    (`_faults`)도 같은 자리에서 막는다 - 필요조건을 더 거는 것이므로 P8 규칙과 충돌하지
    않는다.
    """
    if over_budget:
        return "undetermined", f"예산 초과 - {budget_note or '귀속 합이 잔차를 넘었다'}"
    if proof is None:
        return "undetermined", "검정을 돌리지 못했다"
    if not proof.passed:
        return "undetermined", "검정이 성립하지 않았다: " + ("; ".join(proof.gate_fail[:2])
                                                        or proof.status)
    if not proof.significant:
        return "undetermined", f"귀무와 구분되지 않았다 (p={proof.p:.3f})"

    caps: list[str] = []
    if ident is None or ident.status == "not_identified":
        if not _excludes_zero(ident):
            return "undetermined", "식별되지 않는다 - 조정으로도 도구변수로도 뒷문이 안 막힌다"
        b = ident.bounds if ident else (0.0, 0.0)
        caps.append(f"점식별이 안 된다 - 남는 것은 구간 [{_pct(min(b))}, {_pct(max(b))}] 다.")
    elif ident.status == "identified_under":
        caps.append("가정 하에서만 식별된다: " + ("; ".join(ident.assumptions[:2]) or "미기재") + ".")
    if uncleared:
        says = "; ".join(f"{u.uid} {u.says}" for u in uncleared[:2])
        caps.append(f"미소거 교란 {len(uncleared)}건을 배제하지 못했다: {says}.")
    caps += [f"{x}." for x in faults]
    if not caps:
        return "confirmed", "미소거 교란이 없고 간선이 식별됐으며 검정을 통과했다."
    return "mechanism_compatible", " ".join(caps)


_CEIL_RANK = {"confirmed": 0, "mechanism_compatible": 1, "undetermined": 2}


def _is_exclusive(graph: WorldGraph, a: str, b: str) -> bool:
    if not a or not b:
        return False
    r = graph.relation(a, b)
    return r is not None and r.kind == "mutually_exclusive"


def _bump(fingerprint: Fingerprint, graph: WorldGraph) -> list[str]:
    """역할 신고가 지문의 정규성과 어긋나는 자리. **거부가 아니라 기록이다.**

    Halpern-Hitchcock 이 배경조건과 촉발원을 가르는 형식적 장치를 준다 - 배경조건은
    실제값이 그 참조류에서 default 인 것이고 촉발원은 deviant 인 것이다. 그런데 같은
    저자들이 정규성 순서를 고르는 것만으로 어떤 주장이든 참·거짓으로 만들 수 있다고
    경고한다. 그래서 순서는 자료(P1)에서만 나오고, 어긋남은 기각이 아니라 소견이 된다.
    """
    return graph.role_violations(fingerprint)


def _budget_forgiven(graph: WorldGraph, live_hids: list[str]) -> str:
    """예산 초과가 **회계 오류**인가 그래프 오류인가.

    현재 예산 산술은 살아 있는 몫을 평탄하게 더한다 - 즉 **모든 쌍을 coincident 로 가정**한
    산술이다. Zaks 의 판정으로는 `share`(relative causal force) 자체가 coincident 에서만
    정의되므로, 나머지 관계에서 합산한 결과로 초과를 선언하는 것은 정의되지 않은 양을
    더해 놓고 그 합이 크다고 말하는 것이다.

    구체적 피해: 배타적인 두 가설을 더해 `over_budget` 이 서면 `_verdict_of` 가 **둘 다**
    `undetermined` 로 떨어뜨린다. 정상 그래프를 산술이 죽인다.

    그래서 초과가 비-coincident 쌍에서 왔으면 사면하고, 무엇 때문에 사면했는지 적는다.
    사면은 초과를 없던 일로 하는 것이 아니라 **판정 근거에서 빼는 것**이다.
    """
    if len(live_hids) < 2:
        return ""
    excuse: list[str] = []
    for i, a in enumerate(live_hids):
        for b in live_hids[i + 1:]:
            r = graph.relation(a, b)
            if r is None or r.kind == "coincident":
                continue
            if r.kind == "mutually_exclusive":
                excuse.append(f"{a}·{b} 는 배타적이다 - 같은 예산 슬롯을 두고 경쟁하므로 "
                              "더하면 안 된다")
            elif r.kind == "congruent":
                excuse.append(f"{a}·{b} 는 증거가 연동된다 - 몫이 겹치므로 단순 합은 중복 계상이다")
            elif r.kind == "inclusive":
                excuse.append(f"{a}·{b} 는 한쪽이 다른 쪽의 확장이다 - 두 항이 아니라 한 항이다")
            elif r.kind == "causal":
                excuse.append(f"{a}->{b} 는 직렬이다 - 매개분을 두 번 셌다 "
                              "(총효과가 아니라 NDE/NIE 로 갈라야 한다)")
    return " / ".join(list(dict.fromkeys(excuse))[:2])


def _power_cap(q: Question) -> str:
    """검정력이 없으면 상한을 내린다. **E-value 보다 먼저 오는 축이다.**

    실측: 일별 특이잡음 sd 1.68% 에서 80% 검정력@5% 에 필요한 효과는 4.71%/일이고,
    ETF 분산은 sd(u) = σ·sqrt(1/N + ρ(1-1/N)) 이라 ρ=0.25 면 종목 수와 무관하게 검출
    하한이 2.4%/일에서 정체한다. 그 아래에서 "유의하지 않다"는 정보가 아니며 어떤
    서사도 반증 불가능하다 - 확인했다고 말할 자격이 없다.
    """
    caps: list[str] = []
    if q.underpowered:
        caps.append(f"검정력 미달 - 잔차 {_pct(q.residual)} 가 이 셀의 검출 하한 "
                    f"{_pct(q.mde80 or 0)} 아래다. 이 크기는 잡음과 구분되지 않는다")
    if q.scan_unresolved:
        # 이례성 자체가 다중검정 뒤에 안 남는다. 설명은 계속하되 "이 움직임이 특별하다"는
        # 전제가 성립하지 않으므로 확인 문구는 못 나간다.
        caps.append(f"다중검정 보정 후 이례성이 남지 않는다 (보정 p={q.p_scan:.2f}) - "
                    "매일 여러 셀을 훑으므로 이 크기는 우연히도 나온다")
    return " / ".join(caps)


def _coverage(graph: WorldGraph, fingerprint: Fingerprint) -> list[DomainCoverage]:
    """메커니즘 영역 커버리지 원장. **열지 않은 영역에 침묵하지 않는다.**

    후보 목록은 공시·뉴스에서 오므로 가설을 그대로 두면 `information` 으로 쏠린다.
    브리핑의 진단("뉴스만 검색하면 첫 번째 영역으로 편향된다")이 정확히 이것이고,
    그 편향은 열지 않았다는 사실을 적지 않으면 산출물에서 보이지 않는다.
    """
    hids: dict[str, list[str]] = {}
    for h in graph.hypotheses:
        hids.setdefault(h.domain, []).append(h.hid)
    # 원장이 원리적으로 못 여는 영역. P1 의 측정 불가 축이 그 증거다.
    blocked = {a.name: a.missing_input for a in fingerprint.axes if not a.available}
    out: list[DomainCoverage] = []
    for dom in DOMAIN_SAY:
        if dom in hids:
            out.append(DomainCoverage(domain=dom, status="opened", hids=hids[dom]))
        elif dom == "microstructure":
            out.append(DomainCoverage(
                domain=dom, status="unavailable",
                why="호가·스프레드·깊이가 원장에 없다. 거래량은 유동성이 아니다"))
        elif dom == "feedback" and "intraday_shape" in blocked:
            out.append(DomainCoverage(
                domain=dom, status="unavailable",
                why="분봉이 없어 하락->추가매도의 시간 전개를 볼 수 없다"))
        else:
            out.append(DomainCoverage(domain=dom, status="not_considered"))
    return out


def _modality(d: Disposition) -> Modality:
    """소견 하나의 양상 어휘. **처분 전체가 아니라 소견마다 등급이 붙는다** (NTSB 규약).

    3단 사다리만으로는 "요인이 아니었다"(부정 확언)와 "모른다"가 같은 칸에 들어간다.
    보고서에서 부정 주장이 긍정 주장보다 강한 경우가 많다는 것이 정확히 이 구분이다.
    """
    if d.verdict == "not_contributing":
        return "not_a_factor"
    if d.verdict == "undetermined":
        return "may_have"
    return "was" if d.ceiling == "confirmed" else "likely"


def _strength(d: Disposition) -> tuple[float, float, float]:
    """Probable Cause 정렬. 상한이 높은 것 > p 가 작은 것 > 효과가 큰 것."""
    return (0.0 if d.ceiling == "confirmed" else 1.0,
            float(d.evidence.get("p", 1.0)),
            -abs(float(d.evidence.get("effect", 0.0))))


# ── P8 본체 ─────────────────────────────────────────────────────────────
def dispose(*, question: Question, fingerprint: Fingerprint, graph: WorldGraph,
            idents: list[Identification], plan: DiscriminationPlan,
            proofs: list[EdgeProof], budget: dict[str, Any],
            sensitivities: list[Sensitivity], controls: list[NegativeControl],
            screen: ConfoundingScreen | None,
            screened_candidates: list[dict[str, Any]]) -> Findings:
    """전건 처분. **원장에 들어오지 않은 후보는 검토하지 않은 것으로 간주된다.**

    처분 대상은 다섯 갈래고 하나도 빠지지 않는다:

        산술 게이트에서 죽은 후보   무게 없는 원인. 가장 싼 게이트가 가장 세다
        각 가설                     검정·식별 결과에 따라 셋 중 하나
        각 U                        소거됐으면 not_contributing, 아니면 undetermined
        지문의 측정 불가 축         undetermined. **무엇이 없어서인지 함께**
        예산 미설명분               undetermined. 남은 몫에 이름을 붙이지 못했다

    귀속 몫(`contribution`)은 유도 가능할 때만 채운다. 측정된 경로가 하나뿐이고 살아남은
    원인도 하나면 예산의 설명 폭이 곧 그 경로의 몫이다. 둘 이상이면 나눌 근거가 입력에
    없다 - **없는 수를 만들지 않는다.**
    """
    uncleared = plan.uncleared(graph.latents)
    over = bool(budget.get("over_budget"))
    note = str(budget.get("reason") or "")
    # 초과가 비-coincident 쌍에서 왔으면 사면한다. 정의되지 않은 합을 근거로 정상 그래프를
    # 죽이지 않기 위해서다 (`_budget_forgiven`).
    forgiven = _budget_forgiven(graph, [h.hid for h in graph.hypotheses]) if over else ""
    if forgiven:
        note = (note + " / " if note else "") + f"관계 회계로 사면: {forgiven}"
        over = False
    unexplained = _num(budget.get("unexplained"))
    if unexplained is None:
        # 아무것도 귀속되지 않았으면 잔차 전부가 미설명분이다. 0 으로 두면 침묵이 된다.
        unexplained = question.residual
    faults = _faults(controls, screen)
    # 자기 처치가 결과와 역방향인 가설. **쌍 판별로는 절대 안 나오는 기각이다** -
    # Menkveld-Yueshen 이 공식 서사를 이걸로 흔들었다.
    dose_dead = {d.target: d for d in plan.dose_failures()}
    power_cap = _power_cap(question)
    if power_cap:
        faults = [*faults, power_cap]
    p_of, i_of = _by_edge(proofs), {(i.src, i.dst): i for i in idents or ()}
    ucl_ids = [u.uid for u in uncleared]

    live: list[Disposition] = []
    dead: list[Disposition] = []
    open_: list[Disposition] = []

    # 1. 산술 게이트에서 죽은 후보. **로그에만 남기지 않는다.**
    labels = {h.cause_label for h in graph.hypotheses if h.cause_label}
    for c in screened_candidates or ():
        label = str(c.get("label") or c.get("event_type_code") or "이름 없는 후보")
        killed = str(c.get("killed") or "")
        if killed:
            dead.append(Disposition(
                candidate=label, verdict="not_contributing", why=killed,
                evidence=_put(killed_by=killed, gate="arithmetic", share=_num(c.get("share")),
                              prior=_plain(c.get("prior"))),
                share=_num(c.get("share"))))
        elif label not in labels:
            open_.append(Disposition(
                candidate=label, verdict="undetermined",
                why="산술 게이트는 통과했으나 가설로 서지 못했다",
                evidence=_put(gate="arithmetic", share=_num(c.get("share"))),
                share=_num(c.get("share"))))

    # 2. 각 가설.
    for h in graph.hypotheses:
        key = (h.treatment, h.outcome)
        # 통계 간선이 사슬의 **중간 칸**이면 (treatment, outcome) 로는 안 잡힌다. 사건→매개
        # →결과처럼 쪼갠 사슬이 예산에는 들어가면서 처분에서는 미결로 침묵하는 자리였다 -
        # 쪼갤수록 주장이 강해진다는 P2 의 약속과 정반대로 움직인다. 이 가설이 그린 간선
        # 위에 놓인 증명을 뒤에서부터(결과에 가까운 칸부터) 찾는다.
        pr, idt = p_of.get(key), i_of.get(key)
        if pr is None:
            for e in reversed(h.edges or ()):
                pr = p_of.get((e.get("from"), e.get("to")))
                if pr is not None:
                    break
        sn = _sens_for(sensitivities, *key)
        # `h.hid in d.target` 은 H1 이 "H10|H2" 에 걸린다. 구분자로 잘라서 정확히 맞춘다.
        disc = next((d for d in plan.discriminators
                     if d.kind == "pair" and h.hid in d.target.split("|")), None)
        verdict, why = _verdict_of(pr, idt, over_budget=over, residual=question.residual)
        ceiling, cwhy = _ceiling(idt, pr, uncleared, over_budget=over, budget_note=note,
                                 faults=faults)
        if h.hid in dose_dead:
            dd = dose_dead[h.hid]
            verdict, ceiling = "not_contributing", "undetermined"
            why = (f"자기 처치가 결과와 역방향이다 ({dd.woe_db:+d} dB) - {dd.observation}. "
                   "제안된 원인이 강한 자리에서 결과가 오히려 작다")
            cwhy = why
        d = Disposition(
            candidate=(h.cause_label or (pr.design.cause_label if pr else "") or h.hid),
            verdict=verdict, why=why, ceiling=ceiling, role=h.role, domain=h.domain,
            evidence=_put(
                hid=h.hid, treatment=h.treatment, outcome=h.outcome,
                effect=_num(pr.effect) if pr else None, p=_num(pr.p) if pr else None,
                n=(pr.n if pr else None),
                identification=(idt.status if idt else None),
                assumptions=(list(idt.assumptions) if idt else []),
                uncleared=list(ucl_ids),
                discriminator_executable=(disc.executable if disc else None),
                bounds=(list(idt.bounds) if idt and idt.bounds else []),
                because=(pr.design.because if pr else "") or h.says,
                assignment=h.assignment, ceiling_why=cwhy,
                gate_fail=(list(pr.gate_fail) if pr else []),
                spec_sensitive=(pr.spec_sensitive if pr else None),
                # e_value 1.0 은 P6 의 '산출 못 함' 표시다. 수치인 척 싣지 않는다.
                e_value=(_num(sn.e_value) if sn and sn.e_value > 1.0 else None)))
        (live if verdict == "contributing" else
         dead if verdict == "not_contributing" else open_).append(d)

    # 3. 각 U. 소거는 판별 관측이 **실행 가능**할 때만 인정된다(`plan.uncleared`).
    for u in graph.latents:
        disc = plan.for_latent(u.uid)
        name = f"[{u.uid}] {u.says}"
        base = _put(uid=u.uid, treatment=u.between[0], outcome=u.between[1], source=u.source,
                    observation=(disc.observation if disc else ""),
                    discriminator_executable=(disc.executable if disc else False))
        if u.uid in ucl_ids:
            if disc is None:
                why = "이 U 를 가를 관측을 적지 못했다"
            elif disc.common_prediction:
                why = "두 세계가 같은 것을 예측한다 - 이 관측으로는 갈리지 않는다"
            else:
                why = disc.why_not or "판별 관측이 실행 불가다"
            open_.append(Disposition(
                candidate=name, verdict="undetermined", why=f"미소거 교란 - {why}",
                evidence=_put(**base, uncleared=[u.uid], needs=(disc.why_not if disc else ""))))
        else:
            dead.append(Disposition(
                candidate=name, verdict="not_contributing",
                why=f"판별 관측으로 소거됐다: {disc.observation if disc else ''}",
                evidence=base))

    # 4. 지문의 측정 불가 축. **못 쟀다는 사실이 산출물이다.**
    for a in fingerprint.axes:
        if a.available:
            continue
        open_.append(Disposition(
            candidate=f"지문 {a.name}", verdict="undetermined",
            why=f"측정 불가 - {a.missing_input or '입력이 없다'}",
            evidence=_put(axis=a.name, missing_input=a.missing_input or "입력이 없다")))

    # 5. 예산 미설명분. 잔차에 이름을 다 붙이지 못했다는 것도 판정이다.
    if abs(unexplained) >= 1e-4:
        open_.append(Disposition(
            candidate="미설명분", verdict="undetermined",
            why=(f"잔차 {_pct(question.residual)} 가운데 {_pct(unexplained)} 에 "
                 f"붙일 원인을 찾지 못했다"),
            evidence=_put(unexplained=unexplained, residual=question.residual,
                          n_blocked=budget.get("n_blocked")),
            contribution=unexplained))

    live.sort(key=_strength)
    # ── Probable Cause 는 **복수다.** ──────────────────────────────────
    # NTSB Writing Guide: "The probable cause can be a series of events or **a listing
    # of separate causal factors**." 실제 Asiana 214 의 PC 는 4개 병렬이다. 그리고
    # modified HP 의 원인도 연언 집합일 수 있다(L=1 ∧ MD=1) - 두 문헌이 같은 곳을 가리킨다.
    #
    # 가르는 축은 **강도가 아니라 역할**이다. 촉발원이 인과 연쇄를 시작한 것이고, 증폭·
    # 전달·배경은 그것을 크게·멀리·가능하게 만든 것이다. "대규모 매도가 원인" 으로
    # 끝내면 유동성 고갈과 거래정지가 같은 칸에 들어가 서사가 무너진다.
    causes = [d for d in live if d.role == "trigger"]
    rest = [d for d in live if d.role != "trigger"]
    if not causes and live:
        # 촉발원으로 신고된 것이 하나도 살아남지 않았다. 그래도 PC 를 비우지 않는다 -
        # 가장 강한 것 하나를 올리고, 역할이 어긋난다는 사실은 role_violations 가 적는다.
        causes, rest = [live[0]], live[1:]
    # 배타적인 두 촉발원이 동시에 PC 일 수는 없다. 약한 쪽을 contributing 으로 내린다.
    keep: list[Disposition] = []
    for d in causes:
        hid = str(d.evidence.get("hid") or "")
        if any(_is_exclusive(graph, hid, str(k.evidence.get("hid") or "")) for k in keep):
            rest.append(d)
        else:
            keep.append(d)
    causes = keep
    if len(causes) == 1 and not rest and int(budget.get("n_measured") or 0) == 1:
        causes = [replace(causes[0], contribution=_mid(budget.get("explained")))]

    if causes:
        # 상한은 **가장 약한 원인**을 따른다 - PC 가 복수면 그 전부가 성립해야 서사가 산다.
        weakest = max(causes, key=lambda d: _CEIL_RANK[d.ceiling])
        ceiling = weakest.ceiling
        ceiling_why = str(weakest.evidence.get("ceiling_why") or "")
    elif over:
        ceiling, ceiling_why = "undetermined", f"예산 초과 - {note}"
    else:
        ceiling, ceiling_why = "undetermined", "검정을 통과해 기여로 판정된 간선이 없다."

    unjudged = graph.unjudged_pairs()
    if unjudged and ceiling == "confirmed":
        # 관계가 미판정인 쌍이 남으면 share 배분을 신뢰할 수 없고, 배분을 못 믿으면
        # "이만큼 설명했다"를 확인이라 부를 수 없다 (Zaks: share 는 coincident 전용).
        ceiling = "mechanism_compatible"
        ceiling_why = (ceiling_why + " " if ceiling_why else "") + (
            f"가설 관계가 미판정인 쌍이 {len(unjudged)}건 남아 몫 배분을 확정할 수 없다.")
    roles = _bump(fingerprint, graph)

    log("causal.p8.done", ceiling=ceiling,
        causes=[d.candidate for d in causes], n_causes=len(causes),
        n_live=len(live), n_dead=len(dead), n_open=len(open_), uncleared=len(uncleared),
        unexplained=round(unexplained, 5), over_budget=over, faults=len(faults),
        role_violations=len(roles), unjudged=len(unjudged))
    return Findings(
        question=question,
        probable_cause=[replace(d, modality=_modality(d)) for d in causes],
        contributing=[replace(d, modality=_modality(d)) for d in rest],
        not_contributing=[replace(d, modality=_modality(d)) for d in dead],
        undetermined=[replace(d, modality=_modality(d)) for d in open_],
        unexplained=unexplained, over_budget=over, budget_note=note,
        uncleared_latents=uncleared, ceiling=ceiling, ceiling_why=ceiling_why,
        coverage=_coverage(graph, fingerprint), role_violations=roles,
        unjudged_pairs=unjudged)


def audit_block(*, question: Question, fingerprint: Fingerprint, graph: WorldGraph,
                idents: list[Identification], plan: DiscriminationPlan,
                proofs: list[EdgeProof], budget: dict[str, Any],
                sensitivities: list[Sensitivity], controls: list[NegativeControl],
                screen: ConfoundingScreen | None,
                screened_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """감사 흔적. **게이트를 통과했다는 사실만 남고 통과의 증거가 사라지면 안 된다.**

    `dispose` 와 같은 인자를 받는 별도 함수인 이유는 `Findings` 계약에 감사 블록을 담을
    자리가 없어서다(다른 8개 모듈이 같은 계약을 쓰므로 못 바꾼다). 모듈에 직전 산출을
    들고 있다가 되돌려주는 방법도 있었지만 - 한 프로세스에서 셀 여러 개가 돌면 서로
    갈린다. 인자 중복이 은닉 상태보다 싸다.

    지문은 **available 인 축까지 전부** 싣는다 - 무엇을 쟀는지 모르면 무엇을 못 쟀는지도
    의미가 없다. 증명은 원장(placebo 호출 전량)·순열·코드까지 남긴다. 재현 못 하는 수치는
    날조와 구분되지 않는다.
    """
    return {
        "question": _put(explanandum=question.explanandum, intervention=question.intervention,
                         answer_form=question.answer_form, as_of=question.as_of,
                         observed=question.observed, residual=question.residual,
                         budget=question.budget, missing=list(question.missing),
                         # 검정력은 감사 대상이다. 없으면 "유의하지 않았다"와 "잴 수 없었다"가
                         # 사후에 같은 모양이 된다.
                         resid_sd=question.resid_sd, mde80=question.mde80,
                         p_empirical=question.p_empirical, p_scan=question.p_scan,
                         null_note=question.null_note)
                     | {"underpowered": question.underpowered,
                        "scan_unresolved": question.scan_unresolved},
        "graph": _put(completeness=graph.completeness, violations=list(graph.violations),
                      n_nodes=len(graph.nodes), n_edges=len(graph.edges),
                      queries=list(graph.queries),
                      latents=[{"uid": u.uid, "between": list(u.between), "says": u.says,
                                "source": u.source} for u in graph.latents],
                      # 관계 판정이 예산 회계를 정한다 - 아카이브에 없으면 왜 그렇게
                      # 나눴는지 사후에 재구성할 수 없다.
                      relations=[{"a": r.a, "b": r.b, "kind": r.kind,
                                  "because": r.because, "direction": r.direction}
                                 for r in graph.relations],
                      unjudged_pairs=[list(p) for p in graph.unjudged_pairs()],
                      role_violations=graph.role_violations(fingerprint)),
        "fingerprint": [_put(name=a.name, says=a.says, value=_plain(a.value),
                             kills=list(a.kills), missing_input=a.missing_input)
                        | {"available": a.available} for a in fingerprint.axes],
        "identification": [_put(edge=f"{i.src}->{i.dst}", status=i.status, adjust=list(i.adjust),
                                alternatives=[list(x) for x in i.alternatives], iv=list(i.iv),
                                blocked_by=list(i.blocked_by), assumptions=list(i.assumptions),
                                bounds=(list(i.bounds) if i.bounds else []),
                                bounds_note=i.bounds_note)
                           for i in idents or ()],
        "discriminators": [_put(kind=d.kind, target=d.target, observation=d.observation,
                                predicts=dict(d.predicts), sql=d.sql, why_not=d.why_not,
                                woe_because=d.woe_because)
                           | {"executable": d.executable, "woe_db": d.woe_db,
                              "common_prediction": d.common_prediction}
                           for d in plan.discriminators],
        "sensitivities": [_put(edge=s.edge, effect=_num(s.effect), e_value=_num(s.e_value),
                               e_value_lower=_num(s.e_value_lower), gamma=_num(s.gamma),
                               observed_strongest=_num(s.observed_strongest), says=s.says)
                          for s in sensitivities or ()],
        # `passed=False` 와 `p=None` 은 다른 말이다(시끄러움 vs 미실행). 둘 다 남긴다.
        "negative_controls": [_put(kind=c.kind, name=c.name, n=c.n, effect=_num(c.effect),
                                   p=_num(c.p), says=c.says) | {"passed": c.passed,
                                                                "executed": c.p is not None}
                              for c in controls or ()],
        "confounding_screen": (_put(n_before=screen.n_before, n_dropped=screen.n_dropped,
                                    dropped=_plain(screen.dropped), note=screen.note)
                               | {"checked": screen.checked} if screen else None),
        "budget": _put(residual=_num(budget.get("residual")), share=_num(budget.get("share")),
                       explained=_iv(budget.get("explained")),
                       unexplained=_num(budget.get("unexplained")),
                       reason=budget.get("reason"), n_paths=budget.get("n_paths"),
                       n_measured=budget.get("n_measured"), n_blocked=budget.get("n_blocked"),
                       blocked=_plain(budget.get("blocked")))
                  | {"over_budget": bool(budget.get("over_budget"))},
        "proofs": [_put(edge=f"{p.design.src}->{p.design.dst}", cause_label=p.design.cause_label,
                        because=p.design.because, false_if=p.design.false_if, status=p.status,
                        n=p.n, effect=_num(p.effect), p=_num(p.p), null_sd=_num(p.null_sd),
                        null_kind=p.null_kind, unit=p.unit, strategy=p.strategy,
                        adjust=list(p.adjust), iv=list(p.iv), units=list(p.units),
                        gate_fail=list(p.gate_fail), turns=p.turns,
                        strata_reason=p.strata_reason, data_request=_plain(p.data_request),
                        ledger=_plain(p.ledger), perms=_plain(p.perms), code=list(p.code))
                   | {"strata_declared": p.strata_declared, "spec_sensitive": p.spec_sensitive}
                   for p in proofs or ()],
        "screened": [_plain(c) for c in screened_candidates or ()],
        "queries": list(plan.queries),
    }


# ── 서술 ────────────────────────────────────────────────────────────────
def _headline(f: Findings) -> str:
    q = f.question
    move = f"{q.etf_name} {_pct(q.observed)}"
    if f.probable_cause:
        return f"{move} — " + " · ".join(d.candidate for d in f.probable_cause[:2])
    if q.contributors:
        return f"{move} — {q.contributors[0][0]} 기여"
    return f"{move} — 원인 미확인"


def _verdict(f: Findings) -> tuple[str, str]:
    """판정과 확신도. **`높음` 은 나오지 않는다** - 단일 패스에 표본외 확증이 없다.

    상한이 `undetermined` 인 원인은 판정을 만들지 못한다. 유의한 간선이 하나 있어도
    사양 의존(`spec_sensitive`)이면 한 칸 내린다 - 여러 사양 중 유의한 칸만 보고됐다면
    그 유의는 사양 선택의 산물일 수 있다. 게이트로 죽이지 않는 이유는 여러 사양을
    시도하는 것 자체가 정직한 탐색이어서다.
    """
    q = f.question
    if f.probable_cause and f.ceiling != "undetermined":
        spec = any(bool(d.evidence.get("spec_sensitive")) for d in f.probable_cause)
        return VERDICT_EVENT, (CONF_LOW if (spec or f.over_budget) else CONF_MID)
    if abs(q.residual) < abs(q.observed) * 0.5:
        # 잔차가 관측의 절반도 안 되면 움직임의 대부분이 시장·섹터에서 왔다
        return VERDICT_MIXED, CONF_MID
    if q.route_code and "FLOW" in q.route_code.upper():
        return VERDICT_FLOW, CONF_LOW
    return VERDICT_NONE, CONF_LOW


def _cause_sentence(d: Disposition, f: Findings) -> str:
    """원인 문장. **상한이 `confirmed` 가 아니면 확인 문구를 만들 수 없다.**

    이 모듈에서 가장 위험한 자리다. 상한을 문장 뒤에 덧붙이는 경고로 두면 고객은 첫
    문장만 읽고 "확인"으로 받는다. 그래서 상한이 **어느 동사를 쓸지**를 정하게 했다.
    """
    name = d.candidate
    if d.ceiling == "confirmed":
        s = f"{name}{_josa(name, '이', '가')} 원인으로 {CONFIRMED_PHRASE}."
    elif d.ceiling == "mechanism_compatible":
        s = f"{name}{_josa(name, '은', '는')} 관측된 움직임과 양립합니다."
    else:
        s = f"{name}{_josa(name, '은', '는')} 검토했으나 확정하지 못했습니다."

    because = str(d.evidence.get("because") or "")
    if because:
        s += f" {because.rstrip('.')}."
    for u in f.uncleared_latents[:2]:
        s += f" 다만 {u.says}{_josa(u.says, '을', '를')} 배제하지 못했습니다."
    if d.evidence.get("identification") == "identified_under":
        a = str((d.evidence.get("assumptions") or ["기재되지 않은 가정"])[0])
        s += f" 이 결론은 {a}{_josa(a, '을', '를')} 가정할 때만 성립합니다."
    b = d.evidence.get("bounds") or []
    if d.evidence.get("identification") == "not_identified" and len(b) == 2:
        s += f" 크기는 점으로 잡히지 않고 {_pct(min(b))} ~ {_pct(max(b))} 구간으로만 남습니다."
    if d.contribution is not None:
        s += f" 이 경로가 설명하는 폭은 약 {_pct(d.contribution)} 입니다."
    return s


def _body(f: Findings, audit: dict[str, Any]) -> str:
    """고객 문장. **본문의 모든 퍼센트는 입력에서 유도된 값이다.**

    미설명분을 항상 싣는 이유: "설명하지 못했다"가 일급 산출이기 때문이다. 빼면 남은
    문장들이 잔차 전체를 설명한 것처럼 읽힌다.
    """
    q = f.question
    L = [f"{q.trade_date} {q.etf_name}가 {_pct(q.observed)} {_updown(q.observed)}습니다."]
    if abs(q.observed) > abs(q.residual):
        L.append(f"이 중 시장·업종 흐름으로 설명되는 부분을 빼면 {_pct(q.residual)}가 "
                 f"이 ETF 고유의 움직임입니다.")
    if q.contributors:
        top = ", ".join(f"{n}({_pct(c)})" for n, c in q.contributors[:3])
        L.append(f"등락에 가장 크게 기여한 종목은 {top} 입니다.")

    if f.probable_cause:
        # PC 가 복수여도 첫 문장은 가장 강한 것으로 연다 - 그 다음 줄이 나머지를 세운다.
        L.append(_cause_sentence(f.probable_cause[0], f))
        if len(f.probable_cause) > 1:
            L.append("같은 자격으로 원인에 오른 것: "
                     + ", ".join(d.candidate for d in f.probable_cause[1:]) + ".")
        # 역할을 갈라서 적는다. **이 분리가 없으면 "범인이 누구인가" 로 되돌아간다** -
        # 촉발원과 증폭요인을 같은 목록에 넣으면 개입 설계가 달라진다는 사실을 잃는다.
        for role, head in (("trigger", "같은 자리를 두고 경쟁했으나 원인 칸에서 내려간 것"),
                           ("transmission", "충격이 전달된 경로"),
                           ("amplifier", "결과를 키운 것"),
                           ("background", "이 움직임이 나기 쉬웠던 배경"),
                           ("terminator", "움직임을 멈추거나 되돌린 것")):
            # `trigger` 가 여기 남아 있다는 것은 배타 관계로 PC 에서 강등됐다는 뜻이다.
            # 빼면 그 후보가 고객 문장에서 통째로 침묵한다 - 처분 폐쇄를 서술이 어긴다.
            got = [d for d in f.by_role(role) if d not in f.probable_cause]
            if got:
                L.append(f"{head}: " + ", ".join(d.candidate for d in got[:3]) + ".")
    else:
        L.append("공개된 뉴스·공시 가운데 이 움직임을 설명할 수 있는 원인은 확인되지 않았습니다.")

    # 미설명분은 **항상** 나간다. 잔차 대비 몫도 잔차와 미설명분에서 바로 유도된다.
    rest = f" (잔차의 {abs(f.unexplained / q.residual):.0%})" if q.residual else ""
    L.append(f"설명하지 못하고 남은 몫은 {_pct(f.unexplained)}{rest} 입니다.")
    if f.over_budget and f.budget_note:
        L.append(f"귀속의 합이 설명 예산을 넘어 이 셀의 배분은 신뢰할 수 없습니다: {f.budget_note}.")

    if f.not_contributing:
        cut = f" 외 {len(f.not_contributing) - 3}건" if len(f.not_contributing) > 3 else ""
        L.append("검토했으나 기여하지 않는 것으로 판정한 것: "
                 + "; ".join(f"{d.candidate} - {d.why.rstrip('.')}"
                             for d in f.not_contributing[:3]) + cut + ".")
    opens = [d for d in f.undetermined if d.candidate != "미설명분"]
    if opens:
        more = f" 외 {len(opens) - 3}건" if len(opens) > 3 else ""
        L.append("자료가 없거나 갈리지 않아 판단을 보류한 것: "
                 + "; ".join(f"{d.candidate} - {d.why.rstrip('.')}" for d in opens[:3])
                 + more + ".")

    # 실행 못 한 판별 검정. **무엇이 있으면 갈리는지**를 적어야 다음 수집 의제가 된다.
    needs = [str(d.get("why_not")) for d in audit.get("discriminators", ())
             if not d.get("executable") and d.get("why_not")]
    needs += [str(d.evidence["needs"]) for d in f.undetermined if d.evidence.get("needs")]
    seen = list(dict.fromkeys(needs))
    if seen:
        L.append("판별에 필요했지만 없는 것: " + ", ".join(seen[:2])
                 + ". 이것이 확보되면 남은 대안을 갈라낼 수 있습니다.")
    if q.missing:
        L.append("확인에 필요했지만 확보하지 못한 자료: " + ", ".join(q.missing[:3]) + ".")
    return " ".join(L)


def narrate(f: Findings, audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """`Explanation.raw` 계약에 맞는 dict.

    반환 키는 `domain.models.Explanation` 이 읽는 것과 **정확히** 맞춘다:
    `verdict`·`confidence`·`headline`·`explain`. `causal` 은 감사 블록이고 DB 매핑이
    버리는 것을 런 아카이브가 보존한다.

    `audit` 은 선택이다. 없으면 `Findings` 만으로 만들 수 있는 것(처분 전건·상한·미소거
    U·미설명분)까지만 싣는다 - 감사 재료가 없다고 서술을 못 내면 아카이브 배선 하나가
    고객 산출을 막는다. `audit_block(...)` 을 넘기면 그 위에 얹힌다.

    마지막 검사가 이 모듈의 존재 이유다: 상한이 `confirmed` 가 아닌데 본문에 확인 문구가
    있으면 **예외로 막는다.** 규칙을 문서에만 적어두면 다음 사람이 문장을 고치면서 조용히
    깬다 - 그때 나가는 것은 고객이 원인으로 읽는 한 문장이다.
    """
    a = audit or {}
    verdict, confidence = _verdict(f)
    body = _body(f, a)
    if f.ceiling != "confirmed" and CONFIRMED_PHRASE in body:
        raise PipelineError(f"상한 {f.ceiling} 인데 확인 문구가 본문에 있다: {f.ceiling_why}")

    causal: dict[str, Any] = {
        "residual": f.question.residual,
        "route_code": f.question.route_code,
        "ceiling": f.ceiling,
        "ceiling_why": f.ceiling_why,
        "unexplained": f.unexplained,
        "over_budget": f.over_budget,
        "budget_note": f.budget_note,
        "probable_cause": [d.candidate for d in f.probable_cause],
        "dispositions": [_put(candidate=d.candidate, why=d.why, share=_num(d.share),
                              contribution=_num(d.contribution), evidence=d.evidence)
                         | {"verdict": d.verdict, "ceiling": d.ceiling,
                            "role": d.role, "domain": d.domain, "modality": d.modality}
                         for d in f.all_dispositions],
        # 커버리지 원장. **안 연 영역을 적지 않으면 "안 봤다"와 "보고 좁혔다"가 산출물에서
        # 같은 모양(부재)이 된다** - P3 의 완비 선언과 같은 성격의 폐쇄 장치다.
        "coverage": [_put(domain=c.domain, why=c.why, hids=list(c.hids))
                     | {"status": c.status} for c in f.coverage],
        "unopened_domains": list(f.unopened_domains()),
        "role_violations": list(f.role_violations),
        "unjudged_pairs": [list(p) for p in f.unjudged_pairs],
        "uncleared_latents": [{"uid": u.uid, "between": list(u.between), "says": u.says,
                               "source": u.source} for u in f.uncleared_latents],
        "missing": list(f.question.missing),
    }
    causal.update(a)      # 감사 재료가 있으면 얹는다. 위 키와 이름이 겹치지 않는다.
    return {
        "verdict": verdict,
        "confidence": confidence,
        "headline": _headline(f),
        "explain": body,
        # 감사용. 고객 문장에 안 들어가지만 아카이브에 남는다.
        "causal": causal,
    }


def demo() -> None:
    """자체 점검. **미소거 U 하나면 확인 문구가 못 나온다**를 실제로 돌려서 확인한다."""
    from datetime import date

    from ..domain.models import Explanation
    from .contracts import Axis, Discriminator, Hypothesis, Latent
    from .engine import EdgeDesign

    q = Question(
        etf_instrument_id="091160", etf_name="테스트 ETF", trade_date=date(2026, 7, 16),
        as_of="2026-07-16", observed=0.0421, residual=0.0300, route_code="EVENT",
        explanandum="r⊥[091160, 2026-07-16] = +3.00%", intervention="공시가 없던 세계",
        answer_form="구간", contributors=[("A종목", 0.012)], missing=["분봉 체결"])
    fp = Fingerprint(axes=[
        Axis("사전표류", True, 0.001, says="공시 전 3일 누적 +0.10%"),
        Axis("장중경로", False, missing_input="분봉 자료가 원장에 없다")])
    u = Latent("U1", ("E", "Y"), "기업이 이 사건을 고르게 만든 사적 정보", "compiled")
    h = Hypothesis(hid="H1", says="공시가 가격을 밀었다", treatment="E", outcome="Y",
                   assignment="chosen", cause_label="자사주 취득 공시")
    g = WorldGraph(nodes={}, edges=[{"from": "E", "to": "Y"}], latents=[u], hypotheses=[h])
    idents = [Identification(src="E", dst="Y", status="identified", adjust=["MOM"])]
    pr = EdgeProof(design=EdgeDesign(src="E", dst="Y", because="공시가 기대 현금흐름을 올린다",
                                     cause_label="자사주 취득 공시"),
                   status="통과", n=120, effect=0.021, p=0.004)
    bud = {"residual": 0.03, "explained": [0.018, 0.024], "unexplained": 0.009,
           "over_budget": False, "reason": "", "n_paths": 1, "n_measured": 1, "n_blocked": 0}
    kw = dict(
        question=q, fingerprint=fp, graph=g, idents=idents, proofs=[pr], budget=bud,
        sensitivities=[Sensitivity(edge="E->Y", effect=0.021, e_value=2.1)],
        # 미실행 대조(p=None)는 설계 결함이 아니다. 시끄러운 것만 상한을 내린다.
        controls=[NegativeControl(kind="outcome", name="사건 전 5일", n=80, effect=0.0,
                                  p=0.61, passed=True),
                  NegativeControl(kind="exposure", name="위약 노출", n=0, effect=None,
                                  p=None, passed=False, says="실행 불가: 표본 없음")],
        screen=ConfoundingScreen(n_before=42, n_dropped=5),
        screened_candidates=[{"label": "배당락",
                              "killed": "비중 0.3% 로는 잔차 3.00% 를 만들 수 없다"}])

    # (1) 판별 관측이 실행 불가 -> U 미소거 -> 확인 문구 금지.
    blocked = DiscriminationPlan(discriminators=[
        Discriminator(kind="latent", target="U1", observation="공시 직전 내부자 매수",
                      executable=False, why_not="내부자 거래 신고 자료")])
    f1 = dispose(plan=blocked, **kw)
    thin = narrate(f1)
    assert f1.uncleared_latents and f1.ceiling == "mechanism_compatible", f1.ceiling
    assert CONFIRMED_PHRASE not in thin["explain"], thin["explain"]
    assert "양립합니다" in thin["explain"] and "배제하지 못했습니다" in thin["explain"]
    assert Explanation(thin).is_valid and Explanation(thin).explanation_type == "EVENT_SUPPORTED"
    assert "배당락" in [d.candidate for d in f1.not_contributing]
    assert "지문 장중경로" in [d.candidate for d in f1.undetermined]
    assert any(d.candidate == "미설명분" for d in f1.undetermined)
    assert "+0.90%" in thin["explain"], thin["explain"]        # 미설명분은 예산에서 유도된다
    assert "내부자 거래 신고 자료" in thin["explain"]
    assert thin["causal"]["dispositions"] and "proofs" not in thin["causal"]

    thick = narrate(f1, audit_block(plan=blocked, **kw))
    assert thick["explain"] == thin["explain"]
    assert thick["causal"]["fingerprint"][0]["available"] is True
    assert thick["causal"]["proofs"] and thick["causal"]["budget"]["n_measured"] == 1
    assert thick["causal"]["negative_controls"][1]["executed"] is False

    # (2) 같은 입력에서 판별 관측이 실행 가능해지고 **증거의 무게가 붙으면** U 가 소거되고
    #     확인 문구가 열린다. `woe_db` 없이는 소거되지 않는다 - 3 dB(JND) 미만은 두 세계를
    #     가르지 못하고, 무게를 안 적은 것은 0 dB 이므로 무용으로 기록된다.
    cleared = DiscriminationPlan(discriminators=[
        Discriminator(kind="latent", target="U1", observation="공시 직전 내부자 매수",
                      sql="select 1", executable=True, woe_db=12,
                      woe_because="사적 정보가 있었다면 직전 매수가 보여야 하는데 0건이다",
                      predicts={"H1": "직전 매수 없음", "U1": "직전 매수 있음"})])
    f2 = dispose(plan=cleared, **kw)
    raw2 = narrate(f2, audit_block(plan=cleared, **kw))
    assert f2.ceiling == "confirmed" and not f2.uncleared_latents, f2.ceiling_why
    assert CONFIRMED_PHRASE in raw2["explain"]
    assert "+2.10%" in raw2["explain"], raw2["explain"]        # 단일 경로 몫 = 예산 설명 폭
    assert any(d.candidate.startswith("[U1]") and d.verdict == "not_contributing"
               for d in f2.all_dispositions)

    # (3) 오염 검사를 못 했으면 소거가 끝나도 `confirmed` 가 아니다.
    f3 = dispose(plan=cleared, **{**kw, "screen": ConfoundingScreen(
        n_before=0, n_dropped=0, checked=False, note="처치 표본이 없다")})
    assert f3.ceiling == "mechanism_compatible", f3.ceiling_why
    assert CONFIRMED_PHRASE not in narrate(f3)["explain"]
    print("p8_findings demo OK:", f1.ceiling, "/", f2.ceiling, "/", f3.ceiling)


if __name__ == "__main__":
    demo()
