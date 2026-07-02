"""quality 게이트 테스트 — S003 AC2: 실패 항목은 사유가 드러나야 한다."""

from data_pipeline.quality import check_record

GOOD = {
    "title": "제목",
    "url": "https://example.com/a",
    "publishedDate": "2026-07-01 09:00:00",
    "site": "Reuters",
}


def test_complete_record_passes():
    # WHY: 스토리 필수 필드(제목·발행시각·언론사·URL)가 다 있으면 통과해야
    #      정상 데이터가 canonical 에서 누락되지 않는다.
    assert check_record(GOOD) == []


def test_each_missing_field_reports_its_reason():
    # WHY: AC2 는 '실패'가 아니라 '실패 사유' 로깅이다 — 어떤 필드가 왜
    #      막혔는지 사유 코드로 구분돼야 운영에서 소스 문제를 추적한다.
    assert check_record({**GOOD, "title": " "}) == ["missing_title"]
    assert check_record({**GOOD, "url": "not a url"}) == ["invalid_url"]
    assert check_record({**GOOD, "publishedDate": "미상"}) == ["unparseable_published_at"]
    assert check_record({**GOOD, "site": ""}) == ["missing_publisher"]


def test_multiple_failures_all_reported():
    # WHY: 첫 실패에서 멈추면 남은 문제가 숨는다 — 사유는 전부 수집돼야 한다.
    assert check_record({}) == [
        "missing_title",
        "invalid_url",
        "unparseable_published_at",
        "missing_publisher",
    ]
