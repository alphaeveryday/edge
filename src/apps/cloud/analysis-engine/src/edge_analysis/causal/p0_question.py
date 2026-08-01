"""P0 · 질문 고정 — **그래프보다 먼저 온다.**

개입을 문장으로 못 쓰면 그래프를 그릴 자격이 없다. 이전 구조에는 이 단계가 아예 없었고
`target: "AR@t+0"` 한 줄이 전부였다 - 그래서 무엇의 반사실인지 정하지 않은 채 추정이
돌았고, 산출물은 "이 사건이 오늘 이 움직임을 일으켰다"로 읽혔지만 실제로 잰 것은
"이 타입 사건 240건의 평균 효과"였다.

**두 가지를 여기서 못 박는다.**

1. 개입의 정의. "공시가 발생하지 않은 세계"는 정의 가능하다 - 공시 시점은 조작 가능한
   대상이고, 그 세계의 기업은 여전히 같은 기업이다. "이사회가 다른 결정을 한 세계"는
   정의 불가다 - 기업 상태가 달라지므로 다른 기업이다. 이 구분이 P3 의 교란 구조를
   미리 결정한다(전자는 발표 경로만, 후자는 기업 상태 전체가 U 가 된다).

2. 답의 형태. 우리 물음은 causes of effects 다 - Holland(1986) 가 통계의 약한 방향으로
   지목한 쪽이고, Dawid 의 결론은 외생성·단조성 없이는 **점 답이 없고 구간만 있다**는
   것이다. 그래서 답의 형태를 먼저 선언한다. 나중에 점추정이 나오면 그건 형태 위반이다.

잔차를 여기서 계산하는 이유: 파이프라인의 `Decomposition.proxy_ret` 은 ETF 자체 등락이다.
그걸 설명 예산으로 쓰면 설명해야 할 폭이 부풀고 산술 게이트가 헐거워진다.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from ..observability import log
from .contracts import Question

# 트리거 종류별 반사실 문장. **개입은 사건이 아니라 그 사건의 공표에 건다.**
_INTERVENTION = (
    "{when} {label} 공시가 발생하지 않은 세계 (기업 상태는 동일, 공표만 없음). "
    "이사회 결정 자체가 없는 세계가 아니다 - 그건 다른 기업이고 반사실로 정의되지 않는다."
)
_NO_CANDIDATE = (
    "설명 후보가 없다. 개입을 정의할 대상이 없으므로 이 셀의 답은 "
    "'어느 후보로도 설명되지 않는다'의 형태만 가능하다."
)
ANSWER_FORM = (
    "구간과 상한. 점추정 금지 - causes of effects 는 외생성·단조성 없이 점 답이 없다. "
    "산출은 (기여 구간 · 주장 상한 · 미소거 교란 목록 · 미설명분)이다."
)


# 다중검정 보정의 스캔 크기. 매일 이만큼의 (ETF, 날) 셀을 훑는다는 뜻이고, 이 값이
# 없으면 "p=0.005 니까 이례적" 이 매일 하나씩 참이 된다.
SCAN_CELLS = 200
# 80% 검정력@양측 5% 의 정규 근사 상수: (z_{0.975} + z_{0.80}) = 1.96 + 0.8416.
_MDE_Z = 2.8016
MIN_HISTORY = 60        # 이보다 짧으면 귀무분포를 만들지 않는다 - 없는 정밀을 만들지 않는다


def _power(cd, instrument_id: str, trade_date: date,
           residual: float) -> tuple[dict, str]:
    """이 셀 **자신의** 귀무분포에서 오늘이 어디인가.

    세 겹이다.

    1. **경험분위.** 정규분포를 쓰지 않는다 - 일별 초과수익은 꼬리가 두꺼워 정규 근사가
       하필 극단에서 p 를 낮게 준다.
    2. **스캔 보정** (Šidák). 200 ETF 를 매일 훑으면 p=0.005 는 매일 하나씩 나온다.
       보정 없는 극단성은 발견이 아니라 표본 크기다.
    3. **검출 하한.** 실측 잡음 sd 에서 80% 검정력을 내려면 얼마가 필요한가. 잔차가 그
       아래면 "유의하지 않다"가 정보가 아니고 어떤 서사도 반증 불가능하다 - 그러면
       P8 이 주장 상한을 내린다.

    실패는 침묵하지 않는다. 이력이 짧으면 전부 None 이고 `null_note` 가 그 사실을 적는다.
    """
    try:
        hist = cd.ar_history(instrument_id, trade_date)
    except Exception as exc:  # noqa: BLE001 - 검정력을 못 재도 설명은 계속된다
        log("causal.p0.null_failed", error=f"{type(exc).__name__}: {exc}"[:300])
        return {}, "귀무분포 조회 실패 - 검정력을 재지 못했다"
    if hist.size < MIN_HISTORY:
        return {}, (f"이력 {hist.size}일 < {MIN_HISTORY}일 - 귀무분포를 만들지 않았다. "
                    "이 셀의 잔차가 이례적인지 말할 수 없다")
    sd = float(np.std(hist, ddof=1))
    a = abs(residual)
    # 양측 경험 p: 과거 |초과수익| 중 오늘 이상인 비율. +1/+1 은 0 을 내지 않기 위한
    # 보수적 보정이다 - 그리고 그 때문에 p 의 **분해능 하한이 1/(n+1)** 이 된다.
    # 그 바닥에 닿았다는 것은 덜 극단이라는 뜻이 아니라 이 검정이 더 못 본다는 뜻이고,
    # `Question.at_resolution_floor` 가 그 구분을 한다.
    p_emp = float((np.sum(np.abs(hist) >= a) + 1) / (hist.size + 1))
    p_scan = 1.0 - (1.0 - p_emp) ** SCAN_CELLS
    floor = 1.0 / (hist.size + 1)
    note = (f"자기 이력 {hist.size}일 경험분포 · sd {sd * 100:.2f}%p · "
            f"스캔 {SCAN_CELLS}셀 Šidák 보정 (p 분해능 하한 {floor:.4f})")
    if p_emp <= 1.5 * floor:
        note += " · 분해능 바닥 - 이 이력으로는 더 극단임을 보일 수 없다"
    return ({"resid_sd": sd, "mde80": _MDE_Z * sd, "n_history": int(hist.size),
             "p_empirical": p_emp, "p_scan": p_scan}, note)


def ask(cd, *, etf_name: str, etf_instrument_id: str, trade_date: date, as_of: str,
        observed: float, route_code: str, contributors: list[tuple[str, float]],
        candidates: list[dict]) -> Question:
    """설명 대상과 반사실을 고정한다. **잔차는 여기서 계산한다.**

    잔차 조회가 실패해도 설명을 멈추지 않는다 - `missing` 에 적고 관측 등락을 예산으로
    쓴다. 그 경우 예산이 넉넉해지므로 산술 게이트가 헐거워지는데, 그 사실이 `missing`
    으로 산출물에 남아 사후에 구별된다.
    """
    residual = observed
    missing: list[str] = []
    if etf_instrument_id:
        try:
            ex = cd.ar([(etf_instrument_id, trade_date)])
            if len(ex) and np.isfinite(ex[0]):
                residual = float(ex[0])
            else:
                missing.append("ETF 당일 시장대비 초과수익 - 관측 등락을 예산으로 쓴다")
        except Exception as exc:  # noqa: BLE001 - 잔차 실패가 설명을 막지 않는다
            log("causal.p0.residual_failed", error=f"{type(exc).__name__}: {exc}")
            missing.append("ETF 당일 시장대비 초과수익 - 관측 등락을 예산으로 쓴다")
    else:
        missing.append("ETF instrument_id")

    power, null_note = ({}, "ETF instrument_id 가 없다")
    if etf_instrument_id:
        power, null_note = _power(cd, etf_instrument_id, trade_date, residual)

    q = Question(
        etf_instrument_id=etf_instrument_id, etf_name=etf_name, trade_date=trade_date,
        as_of=as_of, observed=observed, residual=residual, route_code=route_code,
        explanandum=(f"r⊥[{etf_name}, {trade_date.isoformat()}] = {residual * 100:+.2f}% "
                     f"(관측 등락 {observed * 100:+.2f}% 중 시장 대비 고유분)"),
        intervention=_intervention(candidates),
        answer_form=ANSWER_FORM,
        contributors=list(contributors), missing=missing,
        null_note=null_note, **power)
    log("causal.p0.asked", residual=round(residual, 4), candidates=len(candidates),
        missing=len(missing), mde80=power.get("mde80"), p_scan=power.get("p_scan"),
        underpowered=q.underpowered, no_explanandum=q.no_explanandum)
    return q


def _intervention(candidates: list[dict]) -> str:
    """후보에서 반사실 세계를 만든다. 여러 건이면 전부 적는다 - 하나만 고르는 것은 P8 이다."""
    live = [c for c in candidates if not c.get("killed")]
    if not live:
        return _NO_CANDIDATE
    return "\n".join(
        _INTERVENTION.format(when=str(c.get("event_date") or "")[:10],
                             label=c.get("label") or c.get("event_type_code") or "?")
        for c in live)


def brief(q: Question) -> str:
    """P1·P2 프롬프트 머리에 붙는 질문 선언. **모델이 답의 형태를 먼저 본다.**"""
    L = [f"설명 대상: {q.explanandum}",
         f"셀: {q.etf_name} {q.trade_date.isoformat()} · route={q.route_code}",
         f"설명 예산: {q.budget * 100:.2f}%p — 귀속의 합이 이걸 넘으면 그래프가 틀렸다",
         "",
         "반사실 정의:",
         *[f"  {ln}" for ln in q.intervention.splitlines()],
         "",
         f"답의 형태: {q.answer_form}"]
    if q.contributors:
        L += ["", "기여 상위: " + ", ".join(f"{n}({v * 100:+.2f}%)" for n, v in q.contributors)]
    if q.missing:
        L += ["", "확보하지 못한 입력: " + " · ".join(q.missing)]
    return "\n".join(L)


__all__ = ["ANSWER_FORM", "ask", "brief"]
