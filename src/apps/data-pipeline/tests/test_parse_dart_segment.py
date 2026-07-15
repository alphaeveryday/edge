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
    # 표가 '(단위 : 백만원)'을 선언 — 표시값(82,540)을 KRW(×10^6)로 스케일해야 한다.
    assert [s["revenue_krw"] for s in segments] == [82_540_000_000, 314_435_000_000, 131_645_000_000, 7_669_000_000]


def test_unit_scaling_reads_table_unit() -> None:
    """표 단위 선언(백만원/억원)을 감지해 revenue_krw 를 KRW 로 스케일한다(Codex P1).
    단위 미선언 표(원)는 무변형(factor 1)."""
    baseline = """
    <title>4. 매출 및 수주상황</title>
    <p>4. 매출 및 수주상황</p><p>가. 사업부문별 매출 현황</p>
    <table>
      <tr><th>부문</th><th>매출액</th><th>비중</th></tr>
      <tr><td>A</td><td>60</td><td>60</td></tr>
      <tr><td>B</td><td>40</td><td>40</td></tr>
    </table>
    """
    seg_won, _ = parse_segments(baseline)
    assert [s["revenue_krw"] for s in seg_won] == [60, 40]  # 단위 미선언 → 무변형

    scaled = baseline.replace("<th>부문</th>", "<th>부문 (단위 : 억원, %)</th>")
    seg_eok, _ = parse_segments(scaled)
    assert [s["revenue_krw"] for s in seg_eok] == [60 * 10**8, 40 * 10**8]  # 억원 → ×10^8


def test_pharmaresearch_fixture_keeps_reported_shares() -> None:
    segments, meta = parse_segments(PHARMA_HTML)
    assert meta["share_basis"] == "reported"
    assert meta["share_sum"] == pytest.approx(100.0)
    assert [s["revenue_share_pct"] for s in segments] == [15.4, 58.6, 24.6, 1.4]
    assert all("reported_share" not in s for s in segments)


def test_low_share_sum_is_unreliable() -> None:
    html = """
    <title>2. 주요 제품 및 서비스</title>
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
    <title>4. 매출 및 수주상황</title>
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


def test_combined_amount_share_column_parses_both() -> None:
    """결합 헤더 `매출액(비율)`(셀 `1,234 (56.7)`)에서 금액·비중을 각각 뽑아야 한다 — share_column
    이 결합 헤더를 먼저 매치해도 금액을 비율로 오독(>100)해 행을 버리지 않게(Codex P2)."""
    html = """
    <p>사업부문별 매출</p>
    <table>
      <tr><th>부문</th><th>매출액(비율)</th></tr>
      <tr><td>완성품</td><td>1,234 (56.7)</td></tr>
      <tr><td>부품</td><td>900 (43.3)</td></tr>
    </table>
    """
    segments, meta = parse_segments(html)
    assert [s["segment_name"] for s in segments] == ["완성품", "부품"]
    assert [s["revenue_krw"] for s in segments] == [1234, 900]
    assert [s["revenue_share_pct"] for s in segments] == [56.7, 43.3]


def test_combined_cell_negative_adjustment_dropped_by_sign() -> None:
    """결합 셀의 △ 음수(조정·제거 행)가 부호를 잃고 양수로 뒤집혀 canonical 에 새지 않게 한다
    — 음수 비중은 <=0 가드로 드롭돼야 한다(Codex P2). 정상 양수 행만 남는다."""
    html = """
    <p>사업부문별 매출</p>
    <table>
      <tr><th>부문</th><th>매출액(비율)</th></tr>
      <tr><td>완성품</td><td>1,234 (56.7)</td></tr>
      <tr><td>내부조정</td><td>△301,146 (△8.9)</td></tr>
    </table>
    """
    segments, _ = parse_segments(html)
    assert [s["segment_name"] for s in segments] == ["완성품"]  # 음수 조정행은 드롭


def test_no_segment_table_returns_empty_unreliable() -> None:
    """각도 H: 사업부문 표가 없는 본문은 crash 없이 빈 rows·unreliable stats 로 나온다."""
    segments, meta = parse_segments("<html><body><p>본문</p></body></html>")
    assert segments == []
    assert meta["rows"] == 0
    assert meta["share_basis"] == "unreliable"


# ── ALPHA-354 표 선택 게이트 회귀 (라이브 사업보고서 실측으로 도출) ──────────────
# 헤더군(A∧B)만 맞으면 관계사 지분표·주주현황표·손익계산서도 후보가 돼 garbage 를 부문으로 certify
# 하던 결함을 4 신호로 막는다: 섹션 배제 / 합계 밴드 / 섹션 게이트 / 손익 이름 배제 + read_html 격리.

_SEGMENT_TABLE = """
<table>
  <tr><th>사업부문</th><th>매출액</th><th>비중</th></tr>
  <tr><td>반도체</td><td>600</td><td>60</td></tr>
  <tr><td>디스플레이</td><td>400</td><td>40</td></tr>
</table>
"""


def test_rejects_table_under_nonsegment_section() -> None:
    """같은 표라도 주주·회사개요 섹션 아래면 사업부문표가 아니다 — DART <TITLE> 로 배제한다.
    (삼성전자 관계사 지분표·SK하이닉스 주주현황표가 부문으로 뽑히던 결함.)"""
    banned, _ = parse_segments("<title>1. 회사의 개요</title>" + _SEGMENT_TABLE)
    assert banned == []
    kept, _ = parse_segments("<title>2. 주요 제품 및 서비스</title>" + _SEGMENT_TABLE)
    assert [s["segment_name"] for s in kept] == ["반도체", "디스플레이"]


def test_rejects_share_sum_far_from_100() -> None:
    """관계사 지분율표는 각 지분율의 단순 합이 100 을 크게 넘는다 — 사업부문 분할이 아니므로 rescale
    로 100 에 맞추기 전에 밴드(>130) 밖이면 탈락한다(삼성전자 154%·신한 16007% 결함)."""
    html = """
    <title>2. 주요 제품 및 서비스</title>
    <table>
      <tr><th>부문</th><th>비중</th></tr>
      <tr><td>계열사A</td><td>80</td></tr>
      <tr><td>계열사B</td><td>74</td></tr>
    </table>
    """
    segments, _ = parse_segments(html)
    assert segments == []


def test_weak_basis_requires_segment_section() -> None:
    """computed(금액→비중 파생)는 증거가 약해 사업부문 섹션 밖이면 인정 안 한다 — 금융사 손익계산서가
    합 100 으로 부문 오인되던 결함(삼성생명). 사업부문 섹션 안에서는 인정한다."""
    amount_only = """
    <table>
      <tr><th>부문</th><th>금액</th></tr>
      <tr><td>사업A</td><td>60</td></tr>
      <tr><td>사업B</td><td>40</td></tr>
    </table>
    """
    outside, _ = parse_segments("<title>3. 연결재무제표 주석</title>" + amount_only)
    assert outside == []
    inside, _ = parse_segments("<title>2. 주요 제품 및 서비스</title>" + amount_only)
    assert [s["segment_name"] for s in inside] == ["사업A", "사업B"]


def test_drops_income_statement_line_names() -> None:
    """손익계산서 라인(이자수익·보험금융비용 등)은 부문명이 아니다 — 사업부문 섹션 안이어도 이름으로
    배제해 빈 결과가 된다(삼성생명 '재무상태 및 영업실적' 손익표가 부문으로 뽑히던 결함)."""
    html = """
    <title>2. 주요 제품 및 서비스</title>
    <table>
      <tr><th>구분</th><th>금액</th></tr>
      <tr><td>이자수익</td><td>60</td></tr>
      <tr><td>보험금융비용</td><td>40</td></tr>
    </table>
    """
    segments, _ = parse_segments(html)
    assert segments == []


def test_section_gate_reads_viewer_html_heading_form() -> None:
    """섹션 근거는 두 HTML 형태를 모두 읽는다 — 프로덕션 raw 는 DART-XML `<TITLE>`, DART 뷰어 HTML 은
    `<P class='section-N'>` 로 섹션을 표시한다(픽스처 형태). 뷰어 형태에서도 주주 섹션 표를 배제하고
    사업부문 섹션 표를 골라야 한다(Codex 리뷰: 뷰어 HTML 에서 게이트 무력화 방지)."""
    html = (
        "<TITLE></TITLE>"  # 뷰어 HTML 의 빈 문서 TITLE — 섹션 제목 아님
        "<p class='section-2'>1. 회사의 개요</p>"
        "<table><tr><th>사업부문</th><th>매출액</th><th>비중</th></tr>"
        "<tr><td>대주주</td><td>600</td><td>60</td></tr>"
        "<tr><td>기타주주</td><td>400</td><td>40</td></tr></table>"
        "<p class='section-2'>2. 주요 제품 및 서비스</p>" + _SEGMENT_TABLE
    )
    segments, _ = parse_segments(html)
    assert [s["segment_name"] for s in segments] == ["반도체", "디스플레이"]


def test_unparseable_table_does_not_kill_document() -> None:
    """read_html 로 파싱 안 되는 표(빈 <table>·DART-XML 잔재)가 있어도 문서 전체가 죽지 않고 나머지
    표에서 추출한다 — 표별 격리(각도 H·Rule 12). 옛 코드는 첫 junk 표에서 예외로 문서 전량 유실."""
    html = (
        "<title>2. 주요 제품 및 서비스</title>"
        "<p>부문별 매출 비중</p><table></table>"  # 헤더군 매치(context) but read_html 실패 → 격리
        + _SEGMENT_TABLE
    )
    segments, _ = parse_segments(html)
    assert [s["segment_name"] for s in segments] == ["반도체", "디스플레이"]
