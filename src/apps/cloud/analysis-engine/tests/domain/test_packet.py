"""분석 패킷 구성 테스트.

프롬프트 문구 자체는 다른 곳이 소유하는 계약이다. 여기서는 버그를 막는 동작만
고정한다: proxy 부재가 포매팅을 깨뜨리지 않을 것, 이벤트가 없으면 플레이스홀더,
큰 holdings 가 프롬프트를 부풀리지 않도록 멤버 줄 수 상한.
"""

from datetime import date
from decimal import Decimal

from edge_analysis.domain.models import (
    Decomposition,
    EventContext,
    Measure,
    Member,
    Argument,
    PriceTrigger,
)
from edge_analysis.domain.packet import build_packet

_GATE = PriceTrigger("pmt_1", 0.05, "abs", abs_gate=True, rel_gate=False)


def _member(ticker: str, rank: int) -> Member:
    return Member(ticker, ticker, 0.1, 0.01, 0.001, rank)


def _decomp(members: list[Member], *, proxy: float | None = 0.02,
            top3: float | None = 0.5) -> Decomposition:
    n = len(members)
    return Decomposition(members=members, proxy_ret=proxy, covered_weight=1.0,
                         total_weight=1.0, coverage=1.0, top1=0.4, top3=top3,
                         advancing=n, total_priced=n, n_constituents=n)


def test_packet_reports_unavailable_proxy_without_crashing():
    _system, packet = build_packet(
        etf_ticker="091160", etf_name="테스트 ETF", name_by_ticker={},
        trade_date=date(2026, 7, 16),
        decomp=_decomp([], proxy=None, top3=None), gate=_GATE,
        route_code="COMMON_FACTOR", events=[])

    assert "산출 불가" in packet


def test_packet_uses_placeholder_when_no_events():
    _system, packet = build_packet(
        etf_ticker="091160", etf_name="테스트 ETF", name_by_ticker={},
        trade_date=date(2026, 7, 16),
        decomp=_decomp([_member("A", 1)]), gate=_GATE,
        route_code="CONCENTRATED", events=[])

    assert "(해당 없음)" in packet


def test_packet_caps_member_lines_at_eight():
    members = [_member(f"T{i}", i) for i in range(1, 13)]  # 12 종목

    _system, packet = build_packet(
        etf_ticker="091160", etf_name="테스트 ETF", name_by_ticker={},
        trade_date=date(2026, 7, 16),
        decomp=_decomp(members), gate=_GATE, route_code="CONCENTRATED", events=[])

    member_lines = [line for line in packet.splitlines() if line.startswith("  T")]
    assert len(member_lines) == 8


def _context(**overrides) -> EventContext:
    base = dict(
        source_event_id="evt_1", event_type_code="NEWS",
        available_at="2026-07-16T09:00:00+09:00", entity_id="ent_A", ticker="005930",
        thread_id=None, novelty_status="NEW", title="배당 결정",
    )
    base.update(overrides)
    return EventContext(**base)


def _packet_for(events, name_by_ticker):
    _system, packet = build_packet(
        etf_ticker="091160", etf_name="테스트 ETF", name_by_ticker=name_by_ticker,
        trade_date=date(2026, 7, 16),
        decomp=_decomp([_member("A", 1)]), gate=_GATE,
        route_code="CONCENTRATED", events=events)
    return packet


def test_event_line_stays_legacy_when_no_extra_arguments_or_measures():
    """백필 전 구데이터(대표 참여자 1명·측정 0건)면 이벤트 줄은 종전과 동일해야 한다 —
    온톨로지 확장이 기존 프롬프트를 흔들면 안 된다."""
    event = _context(arguments=(Argument("ISSUER", None, "ent_A", "005930", None),))

    packet = _packet_for([event], {"005930": "삼성전자"})

    assert "- 삼성전자(005930) | NEWS | NEW | 「배당 결정」" in packet
    assert "참여:" not in packet and "측정:" not in packet


def test_event_line_appends_arguments_and_measures_when_present():
    """대표 외 종목 접지 참여자와 측정값(값·단위·basis)은 줄 끝에 덧붙는다. 비종목
    entity ULID 는 LLM 노이즈라 프롬프트에서 뺀다."""
    event = _context(
        arguments=(
            Argument("ISSUER", "subject", "ent_A", "005930", 0.9),
            Argument("TARGET", "object", "ent_B", "042700", None),
            Argument("REGULATOR", "qualifier", "ent_C", None, None),
        ),
        measures=(
            Measure("DIVIDEND_PER_SHARE", Decimal("361.00000000"), "KRW", "TOTAL", "PARSED",
                    "주당 361원"),
            Measure("STAKE_RATIO", None, None, "UNKNOWN", "UNRESOLVED", "약 5%"),
        ),
    )

    packet = _packet_for([event], {"005930": "삼성전자", "042700": "한미반도체"})

    assert "참여: TARGET:한미반도체(042700)" in packet
    assert "ent_C" not in packet
    # 값은 소수부 0 제거, 미해석 값은 surface 로 남는다.
    assert "측정: DIVIDEND_PER_SHARE 361 KRW(TOTAL), STAKE_RATIO 약 5%" in packet


def test_event_line_appends_snippet_when_present():
    """BigKinds 스니펫(lead_text)이 있으면 줄 끝에 붙는다.

    제목만으로는 사건의 내용(금액·상대·조건)이 프롬프트에 닿지 않는다 — 측정값이 붙어도
    서술 맥락이 없으면 LLM 이 제목을 재진술하는 데 그친다.
    """
    event = _context(lead_text="삼성전자가 16일 주당 361원 배당을 결정했다고 공시했다.")

    packet = _packet_for([event], {"005930": "삼성전자"})

    assert "스니펫: 삼성전자가 16일 주당 361원 배당을 결정했다고 공시했다." in packet


def test_snippet_is_bounded_and_whitespace_collapsed():
    """스니펫은 길이 상한이 있고 개행·연속공백이 접힌다.

    사건이 수십 건인 날 리드문 전문을 그대로 실으면 프롬프트가 폭발하고, 원문 개행이
    그대로 들어가면 이벤트 줄 구조(`|` 구분)가 깨진다.
    """
    event = _context(lead_text="가\n\n나   다\t" + ("라" * 500))

    packet = _packet_for([event], {"005930": "삼성전자"})
    snippet = packet.split("스니펫: ")[1]

    assert "\n" not in snippet
    assert snippet.startswith("가 나 다 ")
    assert len(snippet) <= 180


def test_system_rules_permit_exactly_what_packet_sends():
    """시스템 룰의 허용 근거 목록이 packet 이 싣는 축과 일치해야 한다.

    어긋나면 LLM 은 **받은 근거를 쓰지 말라는 지시**를 받는다 — ALPHA-544 가 측정값을
    붙인 뒤 룰이 "제목만"으로 남아 있던 상태가 정확히 그랬다. 응답은 그럴듯하게 나오므로
    조용한 성능 저하다. 그래서 문구가 아니라 **축 이름**으로 고정한다.
    """
    system, _packet = build_packet(
        etf_ticker="091160", etf_name="테스트 ETF", name_by_ticker={},
        trade_date=date(2026, 7, 16),
        decomp=_decomp([_member("A", 1)]), gate=_GATE,
        route_code="CONCENTRATED", events=[])

    for axis in ("제목", "스니펫", "참여자", "측정값"):
        assert axis in system, f"시스템 룰이 '{axis}' 축을 근거로 허용하지 않는다"
    assert "제목만" not in system, "packet 이 제목 외 축을 싣는데 룰이 '제목만'으로 제한한다"
