"""BigKinds 1분 feed adapter 테스트 (ALPHA-707).

의도: 이 adapter 의 결함은 두 방향으로 조용하다 — ①형상 밖 응답을 빈 페이지로 접으면
"그 시각엔 기사 없음"이 원장에 남고(안 본 것이 0건), ②차단을 일반 실패로 접으면
운영자가 pacing 을 낮추는 대신 재시도가 차단을 연장한다. 그래서 고정하는 건
**차단 분류의 경계**(400+HTML·403·429 만 차단, 400+JSON 은 코드 결함)와
**형상 밖 fail-loud**, 그리고 feed 계약(1-base page → 0-base 요청) 변환이다.
"""

from __future__ import annotations

import json

import pytest

from data_pipeline.minute.bigkinds_feed import BigKindsMinuteFeed, BlockedFeedError
from data_pipeline.minute.news_overlap import NewsPage
from data_pipeline.sources.http import StopFetch


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(data.decode())})
        if self.error is not None:
            raise self.error
        return json.dumps(self.response, ensure_ascii=False)


def make_feed(client):
    return BigKindsMinuteFeed(client=client, base_url="https://bk/api",
                              category_codes=("002000000",), session_date="2026-08-04")


def test_page_contract_and_date_window():
    """feed 의 1-base page 가 startNo 로, 날짜창이 세션 하루로 고정돼 나간다."""
    client = FakeClient(response={"resultList": [{"NEWS_ID": "a"}], "isLimitPage": False})
    page = make_feed(client).fetch_page(0, 2, 100)

    body = client.calls[0]["body"]
    assert body["startNo"] == 2  # page 2(1-base) → 0-base 1 → startNo 2
    assert body["startDate"] == body["endDate"] == "2026-08-04"
    assert isinstance(page, NewsPage) and page.rows == ({"NEWS_ID": "a"},)
    assert not page.is_last, "미달 페이지는 마지막이 아니다(soft cap 뒤에 더 있을 수 있다)"


def test_vendor_last_page_signal():
    rows = [{"NEWS_ID": str(i)} for i in range(3)]
    client = FakeClient(response={"resultList": rows, "isLimitPage": True})
    assert make_feed(client).fetch_page(0, 1, 3).is_last


@pytest.mark.parametrize("status,body", [
    (403, ""), (429, ""), (400, "<html><body>blocked</body></html>"),
    (400, "  <!DOCTYPE html><html>"),
])
def test_block_signatures_are_classified(status, body):
    """403·429·400+HTML 은 차단이다 — 일반 실패로 접으면 재시도가 차단을 연장한다."""
    client = FakeClient(error=StopFetch("x", status=status, body=body))
    with pytest.raises(BlockedFeedError):
        make_feed(client).fetch_page(0, 1, 100)


def test_json_400_is_not_a_block():
    """400+JSON 은 파라미터 오류(코드 결함)다 — 차단으로 오진하면 pacing 만 낮추고 못 고친다."""
    client = FakeClient(error=StopFetch("x", status=400, body='{"error":"bad param"}'))
    with pytest.raises(StopFetch):
        make_feed(client).fetch_page(0, 1, 100)


def test_malformed_result_list_fails_loud():
    """resultList 가 목록이 아니면 빈 페이지가 아니라 실패다 — 안 본 것이 0건이 되면 안 된다."""
    client = FakeClient(response={"resultList": None})
    with pytest.raises(ValueError):
        make_feed(client).fetch_page(0, 1, 100)


def test_batch_and_minute_share_request_shape():
    """배치(BigKindsNewsSource)와 1분 feed 가 같은 요청 정본(search_page)을 쓴다 —
    갈리면 UA·필드 하나가 한쪽만 고쳐져 한 경로만 400(축약 UA 함정)을 맞는다."""
    from data_pipeline.config.models import BigKindsNewsSource as Cfg
    from data_pipeline.sources.bigkinds import BigKindsNewsSource

    minute_client = FakeClient(response={"resultList": []})
    make_feed(minute_client).fetch_page(0, 1, 100)
    batch_client = FakeClient(response={"resultList": [], "isLimitPage": True})
    source = BigKindsNewsSource(
        Cfg(category_codes=["002000000"]), client=batch_client)
    list(source.fetch([], from_date="2026-08-04", to_date="2026-08-04"))

    minute_call, batch_call = minute_client.calls[0], batch_client.calls[0]
    assert minute_call["headers"] == batch_call["headers"]
    # 날짜·페이지 크기 외의 요청 본문이 동일해야 한다
    for volatile in ("startDate", "endDate", "resultNumber", "startNo"):
        minute_call["body"].pop(volatile), batch_call["body"].pop(volatile)
    assert minute_call["body"] == batch_call["body"]


def test_short_page_is_not_last():
    """미달 페이지를 마지막으로 접으면 soft cap 뒤 기사가 이번 poll 에서 영구히 빠진다
    (배치 _paginate 의 'isLimitPage 나 빈 페이지로만 판정' 계약과 동일해야 한다)."""
    client = FakeClient(response={"resultList": [{"NEWS_ID": "a"}] * 40, "isLimitPage": False})
    assert not make_feed(client).fetch_page(0, 1, 100).is_last


def test_empty_page_is_last():
    client = FakeClient(response={"resultList": [], "isLimitPage": False})
    assert make_feed(client).fetch_page(0, 1, 100).is_last
