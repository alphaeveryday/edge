"""BigKinds 뉴스 어댑터 테스트 — search.do POST·raw 보존·fail-loud (네트워크 없음).

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9). BigKinds 는 키
없는 웹 JSON 엔드포인트지만, raw 원본 보존과 저부하 POST 계약을 FakeClient 로 잠근다.
카테고리 주도 전체 수집(ALPHA-417) — 검색어 없이 카테고리가 수집 범위를 정한다.
"""

import json

import pytest
from pydantic import ValidationError

from data_pipeline.config import BigKindsNewsSource as BigKindsNewsSourceConfig
from data_pipeline.sources.bigkinds import BigKindsNewsSource
from data_pipeline.sources.http import StopFetch


class FakeClient:
    def __init__(self, responses):
        self.responses = responses  # {startNo: body}
        self.requests: list[dict] = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        body = json.loads(data.decode("utf-8"))
        self.requests.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        return self.responses.get(body["startNo"], json.dumps({"resultList": []}, ensure_ascii=False))


def _row(news_id: str = "01100101.20260707153000000", **extra) -> dict:
    return {
        "NEWS_ID": news_id,
        "TITLE": "삼성전자 실적 개선",
        "CONTENT": "BigKinds가 준 CONTENT 원본",
        "PROVIDER": "테스트신문",
        "CATEGORY_NAMES": "경제>증권",
        **extra,
    }


def _ok(rows: list[dict], **extra) -> str:
    return json.dumps({"resultList": rows, **extra}, ensure_ascii=False)


def _source(responses, *, enabled=True, page_size=2, max_pages=3,
            category_codes=("002000000",)):
    config = BigKindsNewsSourceConfig(
        enabled=enabled,
        page_size=page_size,
        max_pages=max_pages,
        category_codes=list(category_codes),
    )
    return BigKindsNewsSource(config, FakeClient(responses))


def test_fetch_posts_category_search_and_preserves_raw_fields():
    # WHY: BigKinds search.do 는 POST body 계약으로 동작한다. 수집 row 는 원본 필드(NEWS_ID,
    #      CONTENT 등)를 그대로 보존하고 provenance 만 덧붙여야 한다. 검색어는 없다 —
    #      카테고리가 수집 범위를 정하고(ALPHA-417), 종목 연결(our_ticker)은 수집 provenance
    #      가 아니라 정규화 탐지(ALPHA-416)의 산출물이다.
    src = _source({1: _ok([_row(CONTENT="자르지 않는 원문 필드")])})
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 1
    rec = records[0]
    assert rec["NEWS_ID"] == "01100101.20260707153000000"
    assert rec["CONTENT"] == "자르지 않는 원문 필드"
    assert rec["TITLE"] == "삼성전자 실적 개선"
    assert "our_ticker" not in rec
    assert rec["market"] == "KR"
    assert rec["bigkinds_query"] == "category:002000000"
    assert rec["fetched_at"]

    req = src.client.requests[0]
    assert req["method"] == "POST"
    assert req["body"]["searchKey"] == ""
    assert req["body"]["startDate"] == "2026-07-07"
    assert req["body"]["endDate"] == "2026-07-07"
    assert req["body"]["resultNumber"] == 2


def test_symbols_are_ignored_single_category_fetch():
    # WHY: 수집 대상은 심볼이 아니라 카테고리다 — 유니버스가 몇 종목이든 요청 수는 날짜창
    #      페이지 수에만 비례해야 한다(종목 수 × 페이지로 늘면 부하·비용이 유니버스에 결합).
    src = _source({1: _ok([_row()])})
    records = list(src.fetch(["005930", "000660", "042700"], "2026-07-07", "2026-07-07"))

    assert len(records) == 1
    # 요청 수 = 페이지 수(1건 페이지 + 빈 종료 페이지) — 심볼 3개가 3배수를 만들지 않는다.
    assert len(src.client.requests) == 2


def test_category_codes_narrow_the_search_to_configured_categories():
    # WHY: 검색어가 없으므로 카테고리가 유일한 수집 범위다. 설정한 카테고리가 **요청 본문에
    #      실려 서버에서** 걸러져야 한다 — 받아서 우리가 버리는 게 아니라. 소비자(tag-news)는
    #      경제 사건만 쓰고, 온톨로지에 없는 유형은 버려지는 게 아니라 가장 가까운 라벨로
    #      굴절돼 조용히 오분류된다(ALPHA-360).
    src = _source({1: _ok([_row()])}, category_codes=["002000000"])
    list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert src.client.requests[0]["body"]["categoryCodes"] == ["002000000"]
    # 언론사는 안 좁힌다 — 결정은 "경제 카테고리 한정, **전체 언론사**"였다.
    assert src.client.requests[0]["body"]["providerCodes"] == []


@pytest.mark.parametrize("bad", ["002", "abc", "0020000000", "00200000x", ""])
def test_malformed_category_code_fails_at_config_load(bad):
    # WHY: BigKinds 는 잘못된 코드에 에러를 안 준다 — HTTP 200 에 빈 resultList 를 준다
    #      (라이브 실측: "002"·"999000000"·"abc" 전부 totalCount=0). 그러면 수집이 0행이
    #      되는데 상태 판정은 real_failures 만 보므로 **success 로 기록된다** — 오타 하나가
    #      뉴스 수집을 통째로 죽이면서 파이프라인은 초록불이 된다(Rule 12 위반, ALPHA-368 이
    #      몇 주 잠복한 것과 같은 모양). 그래서 형식 오류는 첫 호출 전에, 로드 시점에 터져야 한다.
    with pytest.raises(ValidationError):
        BigKindsNewsSourceConfig(category_codes=[bad])


def test_valid_category_code_loads_and_strips_whitespace():
    # WHY: 위 검증이 정상 코드까지 막으면 수집이 아예 안 된다 — 게이트가 참을 거짓이라
    #      하지 않는지 함께 고정한다(실측으로 동작 확인된 경제 대분류 코드).
    #      공백은 TOML 서식 부산물이지 의미 오류가 아니라 NonBlankStr 과 같이 strip 해
    #      통과시킨다 — 안 그러면 코드는 맞는데 들여쓰기 때문에 수집이 0 이 된다.
    config = BigKindsNewsSourceConfig(category_codes=["002000000", " 002006000 "])
    assert config.category_codes == ["002000000", "002006000"]


def test_empty_category_codes_rejected_at_config_load():
    # WHY: 검색어가 없으므로(ALPHA-417) 카테고리마저 비면 BigKinds 전체 뉴스 firehose 다 —
    #      의도한 환경이 없고, 조용히 전량 수집이 시작되면 부하·비용이 폭주한다. 로드 시점에
    #      거부한다(fail loud).
    with pytest.raises(ValidationError):
        BigKindsNewsSourceConfig(category_codes=[])
    with pytest.raises(ValidationError):
        BigKindsNewsSourceConfig()


def test_pagination_uses_bigkinds_page_number_not_row_offset():
    # WHY: BigKinds startNo 는 row offset 이 아니라 page number 다. page_size=50 에서
    #      2페이지를 51 로 호출하면 하루 50건 초과 뉴스가 조용히 유실된다.
    first_page = [_row(f"01100101.2026070715{i:02d}00000") for i in range(50)]
    src = _source({
        1: _ok(first_page),
        2: _ok([_row("01100101.20260707165000000")]),
    }, page_size=50, max_pages=3)

    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 51
    # 마지막 부분 페이지(1건) 뒤에도 한 번 더 요청해 빈 페이지로 종료를 확인한다 —
    # len(rows) < page_size 조기종료는 서버 soft cap 에서 미수집을 은폐하므로 쓰지 않는다.
    assert [r["body"]["startNo"] for r in src.client.requests] == [1, 2, 3]


def test_partial_page_does_not_end_pagination():
    # WHY: page_size 가 API 상한(100)에 붙어 있어, 서버가 요청 수보다 적게 채워 줘도(soft
    #      cap·서버측 dedup) 뒤 페이지가 남아 있을 수 있다 — 부분 페이지에서 멈추면 그
    #      나머지가 조용히 유실되고 status=success 로 위장된다(Rule 12). 종료는 빈 페이지나
    #      isLimitPage 명시 신호로만 한다.
    src = _source({
        1: _ok([_row("01100101.20260707153000000")]),  # page_size=2 인데 1건만 반환
        2: _ok([_row("01100101.20260707153100000"), _row("01100101.20260707153200000")]),
    }, page_size=2, max_pages=5)

    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 3  # 부분 페이지 뒤 페이지도 수집됐다
    assert not src.fetch_failures


def test_is_limit_page_signal_ends_pagination():
    # WHY: isLimitPage 는 BigKinds 의 명시적 마지막 페이지 신호다 — 이 신호에서 멈추면
    #      불필요한 추가 요청 없이 종료되고, 절단(truncation)으로 오판되지 않아야 한다.
    src = _source({
        1: _ok([_row(), _row("01100101.20260707153100000")], isLimitPage=True),
    }, page_size=2, max_pages=5)

    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 2
    assert len(src.client.requests) == 1
    assert not src.fetch_failures


def test_non_dict_row_isolated_and_noted():
    # WHY: resultList 에 dict 아닌 원소가 섞여도 그 행만 격리하고 나머지는 수집해야 한다 —
    #      한 이상치가 페이지 전체를 죽이면 안 되고, 버린 행은 기록으로 드러낸다(Rule 12).
    src = _source({1: _ok([_row(), "not-a-dict", _row("01100101.20260707153100000")])})

    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 2
    assert any("malformed row" in f["error"] for f in src.fetch_failures)


def test_one_sided_date_window_is_not_collapsed_to_one_day():
    # WHY: run.py 는 사용자가 한쪽 날짜만 지정하면 그대로 소스에 넘긴다. BigKinds 가 반대쪽
    #      bound 를 같은 날짜로 채우면 open-ended backfill 이 하루 수집으로 위장된다.
    src = _source({1: _ok([])})
    list(src.fetch([], from_date="2026-06-01"))

    req = src.client.requests[0]
    assert req["body"]["startDate"] == "2026-06-01"
    assert req["body"]["endDate"] == ""


def test_malformed_success_missing_result_list_fails_loud():
    # WHY: resultList 가 없으면 BigKinds 응답 계약이 깨진 것이다. 빈 페이지처럼 넘기면
    #      success 0건으로 위장되므로 실패로 surface 해야 한다(ingest_raw 가
    #      real_failures 로 상태 판정).
    src = _source({1: json.dumps({"totalCount": 1})})
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))
    assert records == []
    assert [f["symbol"] for f in src.fetch_failures] == ["category:002000000"]


def test_bad_json_noted_as_failure_not_crash():
    # WHY: 깨진 JSON 응답이 예외로 런을 죽이면 부분 수집분 저장·상태 기록 없이 끝난다 —
    #      실패를 fetch_failures 로 남겨 ingest_raw 의 "결과는 항상 collection_log" 계약에 태운다.
    src = _source({1: "{broken"})
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))
    assert records == []
    assert len(src.fetch_failures) == 1 and "json" in src.fetch_failures[0]["error"]


def test_stop_fetch_propagates():
    # WHY: HTTP 4xx/429 는 IP 차단·쿼터 같은 소스 전체 문제라 즉시 중단해야 한다.
    class BlockedClient(FakeClient):
        def request(self, method, url, *, headers=None, data=None, decode=True):
            raise StopFetch("HTTP 429")

    config = BigKindsNewsSourceConfig(category_codes=["002000000"])
    src = BigKindsNewsSource(config, BlockedClient({}))
    with pytest.raises(StopFetch):
        list(src.fetch([], "2026-07-07", "2026-07-07"))


def test_max_pages_truncation_is_noted():
    # WHY: 검색 결과가 max_pages 를 초과하면 뒷부분이 절단될 수 있다. 조용히 버리지 않고
    #      kind=truncation 으로 기록해 로그엔 남기되(fail loud), 스텝은 성공으로 본다
    #      (ALPHA-351 — 다음 창에서 이어받음). 진짜 실패와 구분되는 태그다.
    src = _source({
        1: _ok([_row("01100101.20260707153000000")]),
        2: _ok([_row("01100101.20260707153100000")]),
    }, page_size=1, max_pages=2)
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))
    assert len(records) == 2
    trunc = [f for f in src.fetch_failures if "MAX_PAGES" in f["error"]]
    assert trunc and all(f["kind"] == "truncation" for f in trunc)


def test_disabled_depends_only_on_config():
    # WHY: BigKinds 는 키가 없으므로 enabled 는 config 플래그만 따른다.
    assert _source({}, enabled=True).enabled is True
    assert _source({}, enabled=False).enabled is False
