"""분석 LLM 용 (system, user) 프롬프트 쌍 구성 — 순수.

프롬프트 문구는 분석 계약이라 그대로 이식했고, 데이터 접근만 타입 모델로 바꿨다.
ETF 표시명·구성종목명은 대상 ETF 의 마스터·holdings 에서 파생해 주입받는다(ALPHA-467)
— KODEX 반도체 하드코딩 없이 어느 ETF 든 그 ETF 것으로 말한다.
"""
from __future__ import annotations

from datetime import date

from .models import Decomposition, EventContext, Measure, PriceTrigger

_SYSTEM_RULES = (
    "[가격 분해]의 수치와 [뉴스 이벤트]의 제목만 근거로 판단하며, 없는 사실을 만들지 마라. "
    "가격 커버리지가 부분이면 그 한계를 반영하고 단정하지 마라. 숫자는 제공된 값만 인용한다. "
    "반드시 아래 JSON만 출력한다.\n"
    '{"verdict": <"공식 이벤트 선행"|"시장·섹터 주도"|"가격 선행·설명 후행"|"수급·흐름 추정"|"원인 미확인">, '
    '"headline": <한 문장 존댓말>, "explain": <3~6문장 존댓말, 견인 종목 기여와 이벤트를 연결>, '
    '"confidence": <"높음"|"중간"|"보류">, '
    '"key_evidence": [{"signal": str, "why": str}], "unexplained": str}'
)


def _format_value(value) -> str:
    """Decimal/float 를 고정소수 문자열로(불필요한 소수부 0 제거)."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _measure_text(measure: Measure) -> str:
    """측정값 1개의 프롬프트 표기 — 값이 미해석이면 원문 표면(surface)을 쓴다."""
    value = _format_value(measure.value) if measure.value is not None else (measure.surface or "?")
    unit = f" {measure.unit}" if measure.unit else ""
    basis = f"({measure.basis})" if measure.basis != "UNKNOWN" else ""
    return f"{measure.role_code} {value}{unit}{basis}"


def _event_line(event: EventContext, name_by_ticker: dict[str, str]) -> str:
    """이벤트 1건의 프롬프트 줄 — 참여자·측정값은 있을 때만 덧붙인다(구데이터 무변화)."""
    # 종목명은 이 ETF 의 canonical holdings 에서 온다 — 구 KODEX_CONSTITUENTS
    # 하드코딩 dict 은 다른 ETF 로 돌리면 무관한 이름을 붙였다(ALPHA-467).
    name = name_by_ticker.get(event.ticker, event.ticker)
    line = (
        f"- {name}({event.ticker}) | {event.event_type_code}"
        f" | {event.novelty_status} | 「{event.title}」"
    )
    # 대표 참여자 외 종목 접지 참여자만 — entity ULID 는 LLM 에게 노이즈라 뺀다.
    extras = [
        p for p in event.arguments
        if p.ticker and p.entity_id != event.entity_id
    ]
    if extras:
        line += " | 참여: " + ", ".join(
            f"{p.role_code}:{name_by_ticker.get(p.ticker, p.ticker)}({p.ticker})"
            for p in extras
        )
    if event.measures:
        line += " | 측정: " + ", ".join(_measure_text(m) for m in event.measures)
    return line


def build_packet(
    *,
    etf_ticker: str,
    etf_name: str,
    name_by_ticker: dict[str, str],
    trade_date: date,
    decomp: Decomposition,
    gate: PriceTrigger,
    route_code: str,
    events: list[EventContext],
) -> tuple[str, str]:
    """분석 호출용 ``(system_prompt, user_packet)`` 을 반환한다."""
    proxy = decomp.proxy_ret
    price_lines = [
        f"ETF 프록시 등락(구성종목 기여 합, 가격 커버리지 {decomp.coverage:.0%}): "
        + (f"{proxy * 100:+.2f}%" if proxy is not None else "산출 불가"),
        f"진입 게이트: 절대(|등락|>=3%)={'발화' if gate.abs_gate else '미발화'}"
        " (상대 게이트: 지수 미확보로 미적용)",
        f"라우팅: {route_code}",
    ]
    if decomp.top3 is not None:
        price_lines.append(
            f"상승 {decomp.advancing}/{decomp.total_priced}종목(가격 보유분),"
            f" 상위3 기여집중도 {decomp.top3:.0%}"
        )
    price_lines.append("구성종목 기여(가격 보유분만, 비중×등락):")
    for m in decomp.members[:8]:
        price_lines.append(
            f"  {m.name or m.ticker}({m.ticker}) 비중 {m.weight:.1%}"
            f" | 등락 {m.ret * 100:+.1f}% | 기여 {m.contribution * 100:+.2f}%p"
        )
    price_lines.append(
        f"주의: 구성종목 {decomp.n_constituents}개 중 {decomp.total_priced}개만 가격 확보"
        f"(비중 {decomp.coverage:.0%}). 나머지·NAV·괴리·환율은 미확보이므로 단정 금지."
    )

    event_lines = [
        _event_line(event, name_by_ticker)
        for event in sorted(events, key=lambda e: e.available_at)
    ]
    events_block = "\n".join(event_lines) if event_lines else "  (해당 없음)"

    packet = (
        f"[데이터] {etf_name} ({etf_ticker}) {trade_date.isoformat()}\n\n"
        "[가격 분해]\n" + "\n".join(price_lines) + "\n\n"
        f"[구성종목 뉴스 이벤트 {len(events)}건 (제목 기반)]\n" + events_block
    )
    system = f"너는 {etf_name} ETF의 당일 움직임을 설명하는 분석 에이전트다. " + _SYSTEM_RULES
    return system, packet
