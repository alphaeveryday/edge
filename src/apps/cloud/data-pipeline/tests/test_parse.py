"""parse 테스트 — article_id 규약(SSOT)이 흔들리면 중복 제거·canonical 병합이 깨진다."""

from datetime import timedelta, timezone


from data_pipeline.parse import (
    bigkinds_date,
    make_article_id,
    news_article_id,
    normalize_url,
    parse_datetime,
    url_hash,
)


def test_normalize_url_collapses_tracking_variants():
    # WHY: 추적 파라미터(utm 등)·대소문자·끝 슬래시만 다른 같은 기사가 같은 키로
    #      모여야 중복 저장이 생기지 않는다(S002 AC).
    variants = [
        "https://Example.com/news/article-1?utm_source=x&fbclid=y",
        "https://example.com/news/article-1/",
        "https://example.com/news/article-1#top",
    ]
    normalized = {normalize_url(u) for u in variants}
    assert normalized == {"https://example.com/news/article-1"}


def test_normalize_url_preserves_identifying_query():
    # WHY: 쿼리가 기사 식별자인 매체(?id=1 vs ?id=2)에서 쿼리를 지우면 별개
    #      기사가 같은 article_id 로 붕괴해 두 번째 기사가 유실된다.
    a = normalize_url("https://example.com/news?id=1")
    b = normalize_url("https://example.com/news?id=2")
    assert a != b
    assert a == "https://example.com/news?id=1"


def test_normalize_url_query_order_insensitive():
    # WHY: 파라미터 순서만 다른 같은 URL 이 다른 해시가 되면 중복 저장이 생긴다.
    assert normalize_url("https://e.com/n?a=1&b=2") == normalize_url(
        "https://e.com/n?b=2&a=1"
    )


def test_normalize_url_rejects_non_url():
    # WHY: URL 아닌 문자열을 그대로 해시하면 무의미한 키가 조용히 생긴다.
    assert normalize_url("not a url") is None


def test_normalize_url_malformed_returns_none_not_raises():
    # WHY: 문법이 깨진 URL(닫히지 않은 IPv6 대괄호)은 urlsplit 이 ValueError 를 낸다.
    #      여기서 새어 나가면 기사 하나가 수집 런 전체를 죽인다 — None 폴백이어야 한다.
    assert normalize_url("http://[::1") is None
    # make_article_id 는 그 경우 title|published 폴백으로 안정 id 를 만든다.
    assert len(make_article_id("http://[::1", "제목", "2026-07-01")) == 64
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


def test_make_article_id_fallback_is_normalized():
    # WHY: URL 없는 폴백이 원본 문자열로 해시하면, 같은 기사가 날짜 표기·제목 공백만
    #      달라도 다른 id 로 갈려 dedup 을 빠져나가 중복 저장된다. 폴백도 정규화한다.
    base = make_article_id(None, "삼성 실적 발표", "2026-07-01 09:00:00")
    assert base == make_article_id(None, "삼성 실적 발표", "2026-07-01T09:00:00")  # 날짜 표기
    assert base == make_article_id(None, "  삼성   실적 발표 ", "2026-07-01 09:00:00")  # 제목 공백


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


def test_bigkinds_date_prefers_date_then_falls_back_to_news_id():
    # WHY: BigKinds 발행일 파생은 ingest(파티션)와 normalize(published_at)가 공유하는 SSOT다 —
    #      DATE 우선, 없으면 NEWS_ID 임베드 타임스탬프. 두 소비자가 같은 규약을 써야 발행일이
    #      드리프트하지 않는다.
    assert bigkinds_date({"DATE": "20260701"}) == "2026-07-01"
    assert bigkinds_date({"NEWS_ID": "01100101.20260701153000000"}) == "2026-07-01"  # DATE 없음
    assert bigkinds_date({"DATE": "2026-07-01 15:30"}) == "2026-07-01"  # 비숫자 섞여도 슬라이스


def test_bigkinds_date_non_string_and_missing_return_none_not_crash():
    # WHY: 비문자열 DATE/NEWS_ID(list·dict·int)나 결측이 str() 강제 없이 크래시하면 한 이상치
    #      행이 검증 배치를 무너뜨린다 — falsy 는 None, int 는 자릿수로 취급(달력 검증은 parse_datetime).
    assert bigkinds_date({"DATE": [], "NEWS_ID": {}}) is None
    assert bigkinds_date({}) is None
    assert bigkinds_date({"DATE": 20260701}) == "2026-07-01"  # int → str 강제


def test_news_article_id_uses_original_url_hash_across_vendors():
    # WHY: canonical 통합 정체성 — 원문 URL 해시는 소스 무관이다. 같은 원문 URL 이면 FMP `url` 이든
    #      BigKinds `PROVIDER_LINK_PAGE` 든 같은 article_id → canonical 이 소스를 흡수해 통합 dedup.
    fmp = news_article_id({"url": "https://press.com/a", "title": "무시됨", "publishedDate": "2026-07-02"})
    bk = news_article_id({"PROVIDER_LINK_PAGE": "https://press.com/a", "NEWS_ID": "01100101.20260702100000000"})
    assert fmp == bk == make_article_id("https://press.com/a", "", None)  # 원문 URL 해시, NEWS_ID 무시


def test_news_article_id_falls_back_news_id_then_title_when_no_url():
    # WHY: 우선순위 url → NEWS_ID → title|date. URL 없으면 BigKinds NEWS_ID(벤더 식별자)로 별개
    #      기사를 가르고(제목·날짜 붕괴 방지), NEWS_ID 도 없으면 최후로 제목|발행일 폴백.
    base = {"TITLE": "같은 제목", "DATE": "20260702"}  # URL 없음
    a = news_article_id({**base, "NEWS_ID": "01100101.20260702100000000"})
    b = news_article_id({**base, "NEWS_ID": "01100101.20260702100100000"})
    assert a != b and len(a) == len(b) == 64  # 별개 NEWS_ID → 별개 id
    # NEWS_ID 도 URL 도 없으면 title|date 최후 폴백.
    assert news_article_id(base) == make_article_id(None, "같은 제목", "20260702")


def test_normalize_url_non_string_returns_none_not_crash():
    # WHY: 비문자열 입력(int·list)이 .strip() 에서 크래시하면 URL 후보 필터로 쓰는 news_article_id 가
    #      한 이상치 행에 죽는다(BigKinds preserve_all_rows 수집 중단) — SSOT 에서 None 방어(Codex P2).
    assert normalize_url(123) is None
    assert normalize_url([]) is None
    assert normalize_url(None) is None


def test_bigkinds_datetime_recovers_seconds_from_news_id():
    # WHY: 인과귀속의 시간 분해는 τ 초 단위로 하루를 자른다 — 날짜 해상도로는 하루의
    #      모든 사건이 09:00 한 창에 뭉쳐 퇴화한다(2026-08-01 실측, 설계 블로커 4).
    #      NEWS_ID 가 시각을 이미 갖고 있으므로 여기서 복원돼야 한다.
    from data_pipeline.parse import bigkinds_datetime
    r = {"DATE": "20260601", "NEWS_ID": "01100701.20260601060314001"}
    assert bigkinds_datetime(r) == "2026-06-01 06:03:14"
    assert parse_datetime(bigkinds_datetime(r), naive_tz=timezone(timedelta(hours=9))) == \
        "2026-06-01T06:03:14+09:00"


def test_bigkinds_datetime_keeps_partition_invariant_on_date_mismatch():
    # WHY: published_at[:10] 이 published_date 파티션과 어긋나면 멱등 병합이 같은
    #      기사를 두 파티션에 만든다 — DATE 가 날짜 SSOT 이므로 NEWS_ID 시각은 버린다.
    from data_pipeline.parse import bigkinds_date, bigkinds_datetime
    r = {"DATE": "20260602", "NEWS_ID": "01100701.20260601060314001"}
    assert bigkinds_datetime(r) == "2026-06-02"           # 자정 폴백
    assert bigkinds_datetime(r)[:10] == bigkinds_date(r)  # 불변식


def test_bigkinds_datetime_invalid_clock_falls_back_to_date_not_none():
    # WHY: 쓰레기 시각이 parse_datetime 에서 None 이 되면 게이트가 행 전체를 죽인다 —
    #      시각을 잃는 것과 기사를 잃는 것은 다른 사고다.
    from data_pipeline.parse import bigkinds_datetime
    assert bigkinds_datetime({"NEWS_ID": "01100701.20260601256199001"}) == "2026-06-01"
    assert bigkinds_datetime({"DATE": "20260601"}) == "2026-06-01"   # 시각 원천 없음
    assert bigkinds_datetime({}) is None
