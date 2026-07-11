"""사업부문 파서 회귀 — jy(정준영) 프로토타입 검증 픽스처/케이스를 edge 로 이식해 값 고정.

파서 로직은 jy `filings/dart/segments.py`(segments-v2) 순수 파서 절반 이식이라, jy 가 검증한
실제 사업보고서 2건 + 전략별 synthetic 케이스로 4-전략 추출·share_basis 정규화를 고정한다.
(graph 이식은 안 했으므로 jy 의 segments_to_edges 테스트는 이식 대상 아님.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.parse_dart_segment import parse_segments

FIXTURES = Path(__file__).parent / "fixtures" / "disclosure"
SAMSUNG_HTML = (FIXTURES / "segments_samsung_20260310.html").read_text(encoding="utf-8")
PHARMA_HTML = (FIXTURES / "segments_pharmaresearch_20260319.html").read_text(encoding="utf-8")


def _share_sum(segments: list[dict]) -> float:
    return sum(segment["revenue_share_pct"] or 0.0 for segment in segments)


def _assert_common(segments: list[dict], meta: dict, *, minimum: int) -> None:
    assert meta["tables_seen"] >= 1
    assert meta["rows"] == len(segments) >= minimum
    assert all(0 < segment["revenue_share_pct"] <= 100 for segment in segments)
    assert all("합" not in segment["segment_name"].replace(" ", "") for segment in segments)


def test_samsung_fixture_extracts_business_lines() -> None:
    segments, meta = parse_segments(SAMSUNG_HTML)
    _assert_common(segments, meta, minimum=4)
    assert [s["segment_name"] for s in segments] == ["DX 부문", "DS 부문", "SDC", "Harman"]
    assert all(s["period"] == "2025년" for s in segments)


def test_samsung_fixture_rescales_share_sum() -> None:
    segments, meta = parse_segments(SAMSUNG_HTML)
    assert meta["share_basis"] == "rescaled"
    assert meta["share_sum"] == pytest.approx(108.9)
    assert [s.get("reported_share") for s in segments] == [56.3, 39.0, 8.9, 4.7]
    assert _share_sum(segments) == pytest.approx(100.0)


def test_pharmaresearch_fixture_extracts_current_mix() -> None:
    segments, meta = parse_segments(PHARMA_HTML)
    _assert_common(segments, meta, minimum=4)
    assert [s["segment_name"] for s in segments] == ["의약품", "의료기기", "화장품", "기타"]
    assert [s["revenue_krw"] for s in segments] == [82540, 314435, 131645, 7669]


def test_pharmaresearch_fixture_keeps_reported_shares() -> None:
    segments, meta = parse_segments(PHARMA_HTML)
    assert meta["share_basis"] == "reported"
    assert meta["share_sum"] == pytest.approx(100.0)
    assert [s["revenue_share_pct"] for s in segments] == [15.4, 58.6, 24.6, 1.4]
    assert all("reported_share" not in s for s in segments)


def test_low_share_sum_is_unreliable() -> None:
    html = """
    <table>
      <tr><th>부문</th><th>비중</th></tr>
      <tr><td>완성품</td><td>20</td></tr>
      <tr><td>부품</td><td>20</td></tr>
      <tr><td>합계</td><td>40</td></tr>
    </table>
    """
    segments, meta = parse_segments(html)
    assert meta["share_basis"] == "unreliable"
    assert meta["share_sum"] == pytest.approx(40.0)
    assert [s["revenue_share_pct"] for s in segments] == [20.0, 20.0]


def test_computes_shares_from_amount_only_sections() -> None:
    html = """
    <p>4. 매출 및 수주상황</p>
    <p>가. 사업부문별 매출 현황</p>
    <table>
      <tr><th>구 분</th><th>제64기</th><th>제63기</th></tr>
      <tr><td>1. 항공운송사업</td><td></td><td></td></tr>
      <tr><td>총매출액</td><td>120</td><td>110</td></tr>
      <tr><td>순매출액</td><td>100</td><td>90</td></tr>
      <tr><td>2. 항공우주사업</td><td></td><td></td></tr>
      <tr><td>총매출액</td><td>30</td><td>20</td></tr>
      <tr><td>순매출액</td><td>25</td><td>15</td></tr>
      <tr><td>3. 호텔사업</td><td></td><td></td></tr>
      <tr><td>총매출액</td><td>10</td><td>9</td></tr>
      <tr><td>순매출액</td><td>5</td><td>4</td></tr>
    </table>
    """
    segments, meta = parse_segments(html)
    assert meta["share_basis"] == "computed"
    assert [s["segment_name"] for s in segments] == ["항공운송사업", "항공우주사업", "호텔사업"]
    assert [s["revenue_krw"] for s in segments] == [100, 25, 5]
    assert [s["revenue_share_pct"] for s in segments] == [76.92, 19.23, 3.85]


def test_drops_aggregate_parent_rows() -> None:
    html = """
    <p>서비스별 영업현황</p>
    <table>
      <tr><th>구분</th><th>연결 제27기 금액</th><th>연결 제27기 비중</th><th>연결 제26기 금액</th><th>연결 제26기 비중</th></tr>
      <tr><td>영업수익</td><td>1000</td><td>100.0</td><td>900</td><td>100.0</td></tr>
      <tr><td>- 서치플랫폼</td><td>600</td><td>60.0</td><td>540</td><td>60.0</td></tr>
      <tr><td>- 커머스</td><td>400</td><td>40.0</td><td>360</td><td>40.0</td></tr>
    </table>
    """
    segments, meta = parse_segments(html)
    assert meta["share_basis"] == "reported"
    assert [s["segment_name"] for s in segments] == ["- 서치플랫폼", "- 커머스"]
    assert [s["revenue_share_pct"] for s in segments] == [60.0, 40.0]


def test_no_segment_table_returns_empty_unreliable() -> None:
    """각도 H: 사업부문 표가 없는 본문은 crash 없이 빈 rows·unreliable stats 로 나온다."""
    segments, meta = parse_segments("<html><body><p>본문</p></body></html>")
    assert segments == []
    assert meta["rows"] == 0
    assert meta["share_basis"] == "unreliable"
