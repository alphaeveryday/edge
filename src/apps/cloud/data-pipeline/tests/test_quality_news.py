"""뉴스 필수필드 게이트 테스트 — ALPHA-131/S030: 분석에 못 쓰는 뉴스가 canonical 로 새지 않게."""

from data_pipeline.quality import BLOCKING_REASONS, validate_news_meta

# 분석에 쓸 수 있는 정상 메타행 — 제목·발행시각·URL·언론사 모두 존재.
GOOD = {
    "title": "삼성전자 실적 개선",
    "url": "https://e.com/a",
    "normalized_url": "https://e.com/a",
    "published_at": "2026-07-01T09:00:00+00:00",
    "publisher": "Reuters",
}
MAX = "2026-07-03"  # 검증 실행일 + 여유 상한


def test_valid_meta_passes():
    # WHY: 정상 메타행이 게이트에 걸리면 멀쩡한 뉴스가 분석에서 누락된다 — 통과가 기본.
    assert validate_news_meta(GOOD, max_published_date=MAX) == []


def test_missing_title_blocks():
    # WHY: 제목 없는 기사는 식별·표시 불가 — 분석 최소 요건 미달이라 canonical 진입을 막는다.
    assert "missing_title" in validate_news_meta({**GOOD, "title": "  "}, max_published_date=MAX)
    assert "missing_title" in BLOCKING_REASONS


def test_unparseable_published_at_blocks():
    # WHY: 발행시각이 없으면(정규화가 못 만듦) 이벤트를 시간축에 못 놓아 분석 불가 — blocking.
    assert "unparseable_published_at" in validate_news_meta(
        {**GOOD, "published_at": None}, max_published_date=MAX
    )
    assert "unparseable_published_at" in BLOCKING_REASONS


def test_future_and_past_dates_are_implausible():
    # WHY: '20991231'·'19000101' 같은 달력유효-쓰레기 날짜는 파싱은 되지만 엉뚱한
    #      far-future/past 파티션을 만든다 — records_passed 로 인증되지 않게 범위로 잡는다.
    future = validate_news_meta({**GOOD, "published_at": "2099-12-31T00:00:00+00:00"}, max_published_date=MAX)
    past = validate_news_meta({**GOOD, "published_at": "1990-01-01T00:00:00+00:00"}, max_published_date=MAX)
    assert "implausible_published_at" in future
    assert "implausible_published_at" in past
    assert "implausible_published_at" in BLOCKING_REASONS


def test_missing_url_is_warning_not_blocking():
    # WHY: BigKinds 는 URL 없이 NEWS_ID 로 식별 가능하고 AC 가 "URL 또는 기사 식별자"라,
    #      URL 결측으로 벤더 전량을 탈락시키면 안 된다 — 사유는 남기되(감지) blocking 은 아니다.
    reasons = validate_news_meta({**GOOD, "normalized_url": None}, max_published_date=MAX)
    assert reasons == ["missing_url"]
    assert "missing_url" not in BLOCKING_REASONS


def test_missing_publisher_is_warning_not_blocking():
    # WHY: 언론사 결측은 provenance 손실이나 분석 자체는 가능 — 로깅만 하고 통과시킨다(F1:
    #      미확정·가변 필드에 하드 게이트를 걸어 벤더를 조용히 대량 탈락시키지 않는다).
    reasons = validate_news_meta({**GOOD, "publisher": ""}, max_published_date=MAX)
    assert reasons == ["missing_publisher"]
    assert "missing_publisher" not in BLOCKING_REASONS


def test_all_reasons_collected_not_short_circuited():
    # WHY: 첫 위반에서 멈추면 남은 문제가 숨는다(Rule 12) — blocking·경고 사유가 전부 드러나야
    #      운영이 소스 품질 문제를 한 번에 파악한다.
    bad = {"title": " ", "url": None, "normalized_url": None, "published_at": None, "publisher": ""}
    reasons = validate_news_meta(bad, max_published_date=MAX)
    assert set(reasons) == {"missing_title", "unparseable_published_at", "missing_url", "missing_publisher"}
