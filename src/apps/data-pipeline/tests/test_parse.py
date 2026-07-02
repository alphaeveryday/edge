"""parse 테스트 — article_id 규약(SSOT)이 흔들리면 중복 제거·canonical 병합이 깨진다."""

from data_pipeline.parse import make_article_id, normalize_url, parse_datetime, url_hash


def test_normalize_url_collapses_tracking_variants():
    # WHY: 쿼리(utm 등)·대소문자·끝 슬래시만 다른 같은 기사가 같은 키로 모여야
    #      중복 저장이 생기지 않는다(S002 AC).
    variants = [
        "https://Example.com/news/article-1?utm_source=x",
        "https://example.com/news/article-1/",
        "https://example.com/news/article-1#top",
    ]
    normalized = {normalize_url(u) for u in variants}
    assert normalized == {"https://example.com/news/article-1"}


def test_normalize_url_rejects_non_url():
    # WHY: URL 아닌 문자열을 그대로 해시하면 무의미한 키가 조용히 생긴다.
    assert normalize_url("not a url") is None
    assert normalize_url("") is None
    assert normalize_url(None) is None


def test_url_hash_is_stable_sha256():
    # WHY: article_id 는 저장된 데이터와 영구히 묶인다 — 해시 규약이 바뀌면
    #      기존 레이크의 모든 키와 어긋난다.
    h = url_hash("https://example.com/a")
    assert h == url_hash("https://EXAMPLE.com/a/")
    assert len(h) == 64  # sha256 hex


def test_make_article_id_falls_back_without_url():
    # WHY: URL 없는 항목도 안정 id 가 있어야 dedup·병합 대상이 된다(빈 id 금지).
    a = make_article_id(None, "제목", "2026-07-01T00:00:00+00:00")
    b = make_article_id("", "제목", "2026-07-01T00:00:00+00:00")
    assert a == b and len(a) == 64


def test_parse_datetime_fmp_sql_format_to_utc_iso():
    # WHY: FMP publishedDate 는 오프셋 없는 "YYYY-MM-DD HH:MM:SS" — UTC ISO 로
    #      통일돼야 published_date 파티션과 published_at 필드가 결정론적이다.
    assert parse_datetime("2026-07-01 13:45:00") == "2026-07-01T13:45:00+00:00"
    assert parse_datetime("2026-07-01") == "2026-07-01T00:00:00+00:00"


def test_parse_datetime_invalid_returns_none():
    # WHY: 파싱 불가를 예외로 터뜨리면 항목 하나가 런 전체를 죽인다 — None 으로
    #      돌려주고 호출부(파티션 폴백/Step2 품질 게이트)가 처리한다.
    assert parse_datetime("발행일 미상") is None
    assert parse_datetime(None) is None
