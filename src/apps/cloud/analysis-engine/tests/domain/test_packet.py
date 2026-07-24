"""분석 패킷 구성 테스트.

프롬프트 문구 자체는 다른 곳이 소유하는 계약이다. 여기서는 버그를 막는 동작만
고정한다: proxy 부재가 포매팅을 깨뜨리지 않을 것, 이벤트가 없으면 플레이스홀더,
큰 holdings 가 프롬프트를 부풀리지 않도록 멤버 줄 수 상한.
"""

from datetime import date

from edge_analysis.domain.models import Decomposition, Member, PriceTrigger
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
        etf_ticker="091160", trade_date=date(2026, 7, 16),
        decomp=_decomp([], proxy=None, top3=None), gate=_GATE,
        route_code="COMMON_FACTOR", events=[])

    assert "산출 불가" in packet


def test_packet_uses_placeholder_when_no_events():
    _system, packet = build_packet(
        etf_ticker="091160", trade_date=date(2026, 7, 16),
        decomp=_decomp([_member("A", 1)]), gate=_GATE,
        route_code="CONCENTRATED", events=[])

    assert "(해당 없음)" in packet


def test_packet_caps_member_lines_at_eight():
    members = [_member(f"T{i}", i) for i in range(1, 13)]  # 12 종목

    _system, packet = build_packet(
        etf_ticker="091160", trade_date=date(2026, 7, 16),
        decomp=_decomp(members), gate=_GATE, route_code="CONCENTRATED", events=[])

    member_lines = [line for line in packet.splitlines() if line.startswith("  T")]
    assert len(member_lines) == 8
