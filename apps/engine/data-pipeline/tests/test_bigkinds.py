"""BigKinds 뉴스 어댑터 테스트 — search.do POST·raw 보존·fail-loud (네트워크 없음).

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9). BigKinds 는 키
없는 웹 JSON 엔드포인트지만, raw 원본 보존과 저부하 POST 계약을 FakeClient 로 잠근다.
카테고리 주도 전체 수집(ALPHA-417) — 검색어 없이 카테고리가 수집 범위를 정한다.
"""

import json
import logging

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


def _ok(rows: list[dict], *, total: int | None = None, **extra) -> str:
    # 실물 응답은 매 페이지에 `totalCount`(그 창의 전체 건수)를 싣는다 — 절단 판정의 정본이다.
    # 픽스처가 이걸 빼면 어댑터가 '판정 불가' 경로로 새 버려 정상 경로를 아예 안 밟는다.
    # 여러 페이지짜리 시나리오는 total 을 명시한다(페이지 하나만 보고는 합을 알 수 없다).
    return json.dumps(
        {"resultList": rows, "totalCount": len(rows) if total is None else total, **extra},
        ensure_ascii=False,
    )


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
        1: _ok([_row("01100101.20260707153000000")], total=3),  # page_size=2 인데 1건만 반환
        2: _ok([_row("01100101.20260707153100000"), _row("01100101.20260707153200000")], total=3),
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


def test_truncation_reports_exact_loss_count():
    # WHY: 절단은 조용히 버리지 않고 kind=truncation 으로 남기되 스텝은 성공으로 본다
    #      (ALPHA-351). 종전 경고는 "MAX_PAGES 도달 — 창 절단 **가능**"이라 추측이었고,
    #      그래서 매일 울려도 아무도 안 봤다(ALPHA-541: 2주 방치). 알람으로 쓰려면 몇 건을
    #      잃었는지가 경고 안에 있어야 한다 — totalCount 가 그 답을 준다.
    src = _source({
        1: _ok([_row("01100101.20260707153000000")], total=5),
        2: _ok([_row("01100101.20260707153100000")], total=5),
    }, page_size=1, max_pages=2)
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 2
    assert [f["kind"] for f in src.fetch_failures] == ["truncation"]
    # 존재가 아니라 **값**을 묻는다 — 5건 중 2건 수집이면 유실은 3이다. 어느 하나라도
    # 어긋나면(총량·수집분·차이) 경고가 알람으로서 쓸모없어진다.
    error = src.fetch_failures[0]["error"]
    assert "5건 중 2건 수집" in error and "3건 유실" in error
    assert "MAX_PAGES(2) 소진" in error  # 왜 멈췄는지 = 캡을 올려 될 일인지 가른다


def test_reaching_total_count_is_not_truncation():
    # WHY: 절단 판정을 '페이지 소진'이 아니라 totalCount 도달로 바꾼 핵심. 캡에 딱 맞게
    #      끝나도 전량을 받았으면 경고가 없어야 한다 — 안 그러면 알람이 매일 울려 다시
    #      아무도 안 보는 상태로 돌아간다(종전 구현은 여기서 무조건 경고를 냈다).
    src = _source({
        1: _ok([_row("01100101.20260707153000000")], total=2),
        2: _ok([_row("01100101.20260707153100000")], total=2),
    }, page_size=1, max_pages=2)
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 2
    assert not src.fetch_failures


def test_missing_total_count_is_noted_as_undecidable():
    # WHY: totalCount 가 판정의 유일한 근거라, 벤더가 그 필드를 빼면 완주했는지 알 길이
    #      없다. 그 경우를 '완주'로 넘기면 절단이 조용해진다 — 절단이 아니라 **판정 불가**로
    #      남긴다(Rule 12). 판정 근거가 사라진 것 자체가 알려야 할 사건이다.
    src = _source({1: json.dumps({"resultList": [_row()]}, ensure_ascii=False)}, max_pages=1)
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 1
    assert [f["kind"] for f in src.fetch_failures] == ["truncation"]
    assert "totalCount 없음" in src.fetch_failures[0]["error"]


@pytest.mark.parametrize("bad_total", [True, -1], ids=["bool", "negative"])
def test_unusable_total_count_does_not_certify_completion(bad_total):
    # WHY: bool 은 파이썬에서 int 의 서브클래스다. 소박한 `isinstance(x, int)` 게이트에
    #      `totalCount: true` 가 들어오면 total=1 이 되어 `served < 1` 이 거짓 → 비어 있지
    #      않은 모든 런이 '완주'로 인증된다. 절단도 판정 불가도 **둘 다** 조용해지는, 결측
    #      필드보다 나쁜 상태다(벤더가 이 필드를 "더 있음" 플래그로 바꾸면 실제 그 모양).
    #      쓸 수 없는 값은 완주가 아니라 판정 불가로 흘러야 한다.
    src = _source({
        1: json.dumps({"resultList": [_row()], "totalCount": bad_total}, ensure_ascii=False),
    }, page_size=1, max_pages=1)
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 1
    assert [f["kind"] for f in src.fetch_failures] == ["truncation"]
    assert "totalCount 없음" in src.fetch_failures[0]["error"]


def test_vendor_side_early_stop_below_total_is_truncation():
    # WHY: 절단은 우리 캡에 걸릴 때만 나는 게 아니다 — 벤더가 자기 상한(isLimitPage)이나 빈
    #      페이지로 **totalCount 보다 적게 주고 끝내는** 경로가 있고, 종전 구현은 그 두 신호를
    #      무조건 '완주'로 읽어 유실을 통째로 놓쳤다. 캡을 아무리 올려도 안 잡히는 자리라
    #      이 테스트가 없으면 "알람이 시끄럽다"는 이유로 판정을 MAX_PAGES 로 좁히는 수정이
    #      전 테스트 초록인 채 통과한다.
    src = _source({
        1: _ok([_row("01100101.20260707153000000")], total=500, isLimitPage=True),
    }, page_size=1, max_pages=99)
    records = list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert len(records) == 1
    assert len(src.client.requests) == 1  # 상한 신호에서 멈춘다(추가 요청 없음)
    assert [f["kind"] for f in src.fetch_failures] == ["truncation"]
    error = src.fetch_failures[0]["error"]
    assert "500건 중 1건 수집" in error and "499건 유실" in error
    assert "isLimitPage" in error  # 캡이 아니라 벤더가 끊었다 = 캡 상향으로 못 고친다


@pytest.mark.parametrize(
    "responses, page_size, max_pages",
    [
        ({1: _ok([_row()], total=5)}, 1, 1),                       # 유실 건수를 아는 경우
        ({1: json.dumps({"resultList": [_row()]}, ensure_ascii=False)}, 1, 1),  # 판정 불가
    ],
    ids=["known_loss", "undecidable"],
)
def test_truncation_log_line_carries_the_alarm_token(caplog, responses, page_size, max_pages):
    # WHY: 이 경고의 소비자는 사람이 아니라 **CloudWatch 메트릭 필터**다
    #      (`infra/terraform/modules/data-pipeline/tasks.tf` 의 collection_truncated,
    #      pattern = "수집 절단"). 필터는 fetch_failures 가 아니라 **로그 라인**을 읽으므로,
    #      문구를 바꾸거나 _note_failure 가 reason 대신 kind 를 찍도록 리팩터링하면 알람이
    #      영구히 0 이 되는데 나머지 단언은 전부 초록이다 — ALPHA-541 이 2주 방치된 그 모양
    #      그대로 되돌아간다. 토큰이 실제 로그 레코드에 실리는지를 여기서 못박는다.
    src = _source(responses, page_size=page_size, max_pages=max_pages)
    with caplog.at_level(logging.WARNING, logger="data_pipeline.sources.bigkinds"):
        list(src.fetch([], "2026-07-07", "2026-07-07"))

    assert any("수집 절단" in message for message in caplog.messages)


def test_disabled_depends_only_on_config():
    # WHY: BigKinds 는 키가 없으므로 enabled 는 config 플래그만 따른다.
    assert _source({}, enabled=True).enabled is True
    assert _source({}, enabled=False).enabled is False
