"""KIS 업종지수 분봉 어댑터 테스트 (ALPHA-887).

응답 payload 는 2026-08-08~09 라이브 실측(1005·2118·1001·2001 등 45종 전건 프로브)에서
구성했다. 여기서 고정하는 건 "우리가 실측으로 안다고 적어둔 것"이다.

고정하는 계약 일곱:

1. ⭐ **`stck_cntg_hour` 는 구간의 시작** — 주식 당일 TR(구간 **끝**)과 **반대 축**이다.
   1005 의 1분봉 5개를 5분봉에 합성 대조해 확정했다(시작 가설 13건 연속 일치, 끝 가설
   13건 전건 불일치). 뒤집히면 전 구간이 1분 밀린 채 커밋되는데 봉 수는 그대로라
   어떤 게이트도 안 걸린다.
2. **센티넬 행이 섞인다** — `999999`·`888888` 은 봉이 아니라 그날 요약이고 **날짜마다**
   한 쌍 붙는다. 안 거르면 하루에 두 번, 있지도 않은 시각의 봉이 실린다.
3. **응답이 거래일 경계를 넘는다** — 장 초반 페이지는 직전 거래일 꼬리까지 뻗는다.
   남의 날 행 하나의 정합 하자가 오늘 수집을 INVALID 로 만들면 안 된다.
4. **필수 질의 파라미터** — `FID_ETC_CLS_CODE`·`FID_PW_DATA_INCU_YN` 이 빠지면 벤더가
   `rt_cd=2 INPUT FIELD NOT FOUND` 로 전건 튕긴다. `FID_INPUT_HOUR_1` 은 **간격(초)** 다.
5. **필드명이 주식과 다르다**(`bstp_nmix_*`) — 그래서 주식 파서를 못 쓴다.
6. **코드 번역이 어댑터 안에서 끝난다** — 들어오고 나가는 것은 KRX 업종코드, KIS 코드는
   URL 안에서만 산다. 이 축이 새면 남의 지수를 받거나 canonical 이 조인 불가가 된다.
7. 🔴 **`cntg_vol` 은 "거래가 있었나"의 답이 아니다** — 거래량 0 인데 지수가 움직이는
   봉이 실측 4,500 중 175(3.9%)다. 주식용 4분류를 그대로 쓰면 매 window 가 INVALID 다.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.sources.kis_minute import KisUnitError, parse_minute_row
from data_pipeline.sources.kis_sector_index import (
    LANE_INTERVAL_SEC,
    SENTINEL_LABELS,
    KisSectorIndexClient,
    parse_index_row,
)

KST = timezone(timedelta(hours=9))
DAY = "20260807"
# window_start=10:30 인 window. 라벨이 시작이므로 이 window 의 라벨은 "103000" 이다.
WINDOW_END = datetime(2026, 8, 7, 10, 31, tzinfo=KST)


def row(label: str = "103000", *, day: str = DAY, close: str = "5047.39",
        volume: str = "80") -> dict:
    """실측 output2 행 그대로 — 전부 문자열이고 필드가 `bstp_nmix_*` 다."""
    return {
        "stck_bsop_date": day,
        "stck_cntg_hour": label,
        "bstp_nmix_prpr": close,
        "bstp_nmix_oprc": "5040.00",
        "bstp_nmix_hgpr": "5050.00",
        "bstp_nmix_lwpr": "5039.00",
        "cntg_vol": volume,
        "acml_tr_pbmn": "213892",
    }


def sentinel(label: str, *, day: str = DAY) -> dict:
    """봉이 아닌 요약 행 — OHLC 넷이 전부 그날 종가로 같다(실측)."""
    return {
        "stck_bsop_date": day,
        "stck_cntg_hour": label,
        "bstp_nmix_prpr": "5047.39", "bstp_nmix_oprc": "5047.39",
        "bstp_nmix_hgpr": "5047.39", "bstp_nmix_lwpr": "5047.39",
        "cntg_vol": "80", "acml_tr_pbmn": "213892",
    }


def ok(rows: list[dict]) -> str:
    return json.dumps({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리",
                       "output1": {"hts_kor_isnm": "음식료·담배"}, "output2": rows})


TOKEN = json.dumps({"access_token": "tok-1", "expires_in": 86400})


class FakeClient:
    """응답을 순서대로 돌려주는 PoliteClient 대역 (`test_kis_minute.FakeClient` 와 같다)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.slept: list[float] = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        # 헤더를 버리면 `tr_id` 축이 통째로 관측 밖이 된다 — 서브클래스의 존재 이유 중
        # 하나가 그 상수다(엔드포인트·질의·행 형상 셋 중 하나).
        self.calls.append((url, dict(headers or {})))
        if not self.responses:
            raise AssertionError(f"예상 밖 추가 호출: {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _sleep(self, seconds):
        self.slept.append(seconds)


# 우리 축(KRX 업종코드) → KIS 질의 코드. 실제 표의 한 줄 그대로다 — 두 값이 **달라야**
# 번역이 일어났는지 아닌지를 테스트가 가릴 수 있다.
UNIT_ID, KIS_CODE = "1005", "0005"
INDEX_MAP = {UNIT_ID: KIS_CODE}


def make_client(responses, index_map=None, **kwargs):
    fake = FakeClient(responses)
    client = KisSectorIndexClient("app-key", "app-secret", fake,
                                  INDEX_MAP if index_map is None else index_map, **kwargs)
    return client, fake


def query_of(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


class TestParse:
    def test_hour_label_is_window_start_unlike_stock(self):
        """⭐ 라벨이 구간의 **시작**이다 — 주식 파서와 정확히 1분 어긋나야 한다.

        같은 행을 두 파서에 넣어 대조한다. 한쪽만 단언하면 "우연히 맞는 값"을 고정할 수
        있는데, 두 파서의 **차이**는 이 축이 뒤집힐 때만 사라진다.
        """
        candle = parse_index_row(row("103000"), UNIT_ID, interval_sec=LANE_INTERVAL_SEC)
        assert candle.window_start == datetime(2026, 8, 7, 10, 30, tzinfo=KST)
        assert candle.window_end == datetime(2026, 8, 7, 10, 31, tzinfo=KST)

        stock = parse_minute_row(
            {"stck_bsop_date": DAY, "stck_cntg_hour": "103000", "stck_prpr": "100",
             "stck_oprc": "100", "stck_hgpr": "100", "stck_lwpr": "100",
             "cntg_vol": "1"}, "005930")
        # 같은 라벨인데 지수 봉이 주식 봉보다 정확히 한 칸 뒤에 앉는다
        assert candle.window_end - stock.window_end == timedelta(seconds=LANE_INTERVAL_SEC)

    def test_values_are_decimal_from_index_fields(self):
        candle = parse_index_row(row(close="5047.39"), UNIT_ID,
                                 interval_sec=LANE_INTERVAL_SEC)
        # float 를 거치면 5047.389999… 가 된다 — 정밀도가 곧 값이다
        assert candle.close == Decimal("5047.39")
        assert candle.open == Decimal("5040.00")
        assert candle.volume == Decimal("80")
        assert candle.currency is None  # 지수에 통화가 없다 — 지어내지 않는다

    def test_zero_volume_with_moving_price_is_a_valid_index_bar(self):
        """🔴 거래량 0 인데 OHLC 가 움직이는 봉이 **정상이다** — 지수의 성질이다.

        지수는 자기가 체결되지 않고 구성종목이 체결된다. 실측(45종 × 100봉): 4,500 중
        175 봉(3.9%)이 이 형상이고 저유동 업종에서는 상시다.

        ⚠️ `price_collect.collect_units` 는 같은 형상을 `invalid` 로 접고 `status_of` 가
        window 전체를 INVALID 로 만든다 — 그 4분류를 그대로 물리면 이 dataset 은 **단 한
        window 도 확정되지 않는다**. 이 단언이 그 배선을 막는 표지다.
        """
        moved = {**row(volume="0"), "bstp_nmix_oprc": "215.15",
                 "bstp_nmix_hgpr": "215.42", "bstp_nmix_lwpr": "215.04",
                 "bstp_nmix_prpr": "215.30"}   # 1007 종이·목재 15:04 실측
        candle = parse_index_row(moved, UNIT_ID, interval_sec=LANE_INTERVAL_SEC)
        assert candle.volume == Decimal(0)
        assert candle.open != candle.close      # flat 이 아니다 — 주식이면 invalid 다
        # `traded` 는 volume>0 이라 False 를 주는데, 이 dataset 에서 그건 "거래 없음"이
        # 아니다. 그 뜻으로 읽는 소비자를 붙이지 마라(모듈 도크스트링 7번).
        assert candle.traded is False

    @pytest.mark.parametrize("short_label, silently_becomes", [
        ("1030", "10:03:00"),    # 4자리 — 날짜 자리를 훔쳐 **다른 분**이 된다
        ("30000", "03:00:00"),   # 선행 0 잘림(`kis_inav._time_stamp` 의 그 함정)
        # 5자리는 선행 0 을 되붙이면 **맞는 값이 나온다** — 우연이 아니다. 그래도 막는
        # 이유는 4자리와 구분할 근거가 없어서다(소스 주석에 논거를 적어 뒀다).
        ("93000", "09:30:00"),
    ])
    def test_short_time_label_is_rejected_before_parsing(self, short_label, silently_becomes):
        """🔴 라벨 자리수도 막아야 한다 — 안 막으면 **값이 조용히 틀린다**.

        `strptime("%Y%m%d%H%M%S")` 는 연접 문자열 하나를 보므로 짧은 라벨이 날짜 자리를
        훔쳐 간다(`"20260807"+"1030"` → 10:03:00). 결과의 `second` 가 0 이라 격자 가드도
        안 걸리고, 그 봉은 멀쩡한 다른 window 에 앉아 VALID 로 확정된다.

        날짜 자리수만 막았다가 Codex 리뷰(PR #645)에서 잡혔다 — 한 함정의 양쪽이다.
        """
        # 가드가 막는 값이 **무엇이 되는지**를 여기서 못박는다 — 주석으로만 적으면 낡는다.
        stolen = datetime.strptime(row()["stck_bsop_date"] + short_label, "%Y%m%d%H%M%S")
        assert stolen.strftime("%H:%M:%S") == silently_becomes
        with pytest.raises(ValueError, match="자리수가 다르다"):
            parse_index_row({**row(), "stck_cntg_hour": short_label}, UNIT_ID,
                            interval_sec=LANE_INTERVAL_SEC)

    def test_space_padded_label_is_rejected_even_though_it_reads_right(self):
        """자리수만 재면 공백 패딩이 6자리째 샌다 — `strptime` 이 `%H` 에 `" 9"` 를 받는다.

        값은 맞게 읽히지만(`" 93000"` → 09:30:00) 조용히 흡수하면 벤더가 패딩 규약을
        바꾼 것을 못 본다. 이 가드는 값이 아니라 **형상 변화의 신호**를 지킨다.
        """
        with pytest.raises(ValueError, match="자리수가 다르다"):
            parse_index_row({**row(), "stck_cntg_hour": " 93000"}, UNIT_ID,
                            interval_sec=LANE_INTERVAL_SEC)

    def test_non_ascii_digit_label_is_rejected(self):
        """`isdecimal()` 만으로는 안 된다 — `%H` 의 `\\d` 가 유니코드 Nd 라 같은 집합이다.

        `"1٠3000"` 은 `isdecimal()` 도 `strptime` 도 통과한다. 막는 건 `isascii()` 다.
        """
        with pytest.raises(ValueError, match="자리수가 다르다"):
            parse_index_row({**row(), "stck_cntg_hour": "1٠3000"}, UNIT_ID,
                            interval_sec=LANE_INTERVAL_SEC)

    def test_well_formed_but_impossible_time_is_a_format_error(self):
        """자리수 가드를 통과한 뒤 `strptime` 이 잡는 분기 — 메시지로 갈라 둔다.

        가드가 넓어지면 이 분기가 조용히 죽는다. `pytest.raises(ValueError)` 만으로는
        어느 문에서 걸렸는지 구분이 안 된다.
        """
        with pytest.raises(ValueError, match="시각 형식 오류"):
            parse_index_row({**row(), "stck_cntg_hour": "999999"}, UNIT_ID,
                            interval_sec=LANE_INTERVAL_SEC)

    # 짧은 쪽만 재면 `!=` 를 `<` 로 바꿔도 안 갈린다 — 파서에서도 초과 길이를 같이 잰다.
    @pytest.mark.parametrize("field, bad_length", [
        ("stck_bsop_date", "2026087"), ("stck_bsop_date", "202608071"),
        ("stck_cntg_hour", "1030000"),
    ])
    def test_date_length_drift_is_rejected_at_the_parser_too(self, field, bad_length):
        """창 필터가 먼저 걸러도 파서 단독 호출은 막아야 한다(공개 함수다).

        재는 축은 자리수가 **8·6 이 아니다**이지 "짧다"가 아니다.
        """
        with pytest.raises(ValueError, match="자리수가 다르다"):
            parse_index_row({**row(), field: bad_length}, UNIT_ID,
                            interval_sec=LANE_INTERVAL_SEC)

    @pytest.mark.parametrize("broken", [
        {**row(), "stck_cntg_hour": "999999"},        # 센티넬이 파서까지 새면 형식 오류
        {**row(), "stck_bsop_date": None},            # 날짜 결측
        {**row(), "cntg_vol": "-1"},                  # 음수 거래량
        {**row(), "bstp_nmix_lwpr": "99999"},         # OHLC 정합 위반(low > high)
        {**row(), "bstp_nmix_prpr": "0"},             # 양수 아님
        {**row(), "bstp_nmix_oprc": None},            # 필드 결측
        {**row(), "stck_cntg_hour": "103030"},        # 분 격자 밖(초가 붙었다)
    ])
    def test_broken_row_raises(self, broken):
        # 조용히 기본값으로 접으면 그 window 가 '정상 수집'으로 커밋된다(Rule 12)
        with pytest.raises(ValueError):
            parse_index_row(broken, UNIT_ID, interval_sec=LANE_INTERVAL_SEC)

    def test_stock_field_names_are_not_accepted(self):
        """주식 필드명(`stck_prpr`)으로 온 행은 값을 못 찾아 실패해야 한다.

        두 TR 이 `stck_bsop_date`·`stck_cntg_hour`·`cntg_vol` 을 공유해서, 값 필드만
        다르다. 파서를 한 벌로 합치려는 다음 사람이 여기서 걸린다.
        """
        stock_shaped = {"stck_bsop_date": DAY, "stck_cntg_hour": "103000",
                        "stck_prpr": "100", "stck_oprc": "100", "stck_hgpr": "100",
                        "stck_lwpr": "100", "cntg_vol": "1"}
        with pytest.raises(ValueError):
            parse_index_row(stock_shaped, UNIT_ID, interval_sec=LANE_INTERVAL_SEC)


class TestQuery:
    def test_endpoint_is_the_index_tr_not_the_stock_one(self):
        """엔드포인트·`tr_id` 를 고정한다 — 이 서브클래스가 존재하는 이유 중 하나다.

        주식 것(`FHKST03010200` / `inquire-time-itemchartprice`)이 남으면 `rt_cd` 는
        0 인데 `bstp_nmix_*` 가 없는 응답이 와서 전 지수가 INVALID 로 접힌다.
        """
        client, fake = make_client([TOKEN, ok([row()])])
        client.candles(UNIT_ID, window_end=WINDOW_END)
        url, headers = fake.calls[-1]
        assert "inquire-time-indexchartprice" in url
        assert "inquire-time-itemchartprice" not in url
        assert headers["tr_id"] == "FHKUP03500200"

    def test_required_params_present(self):
        """넷 다 있어야 한다 — 빠지면 벤더가 `INPUT FIELD NOT FOUND` 로 전건 튕긴다."""
        client, fake = make_client([TOKEN, ok([row()])])
        client.candles(UNIT_ID, window_end=WINDOW_END)
        query = query_of(fake.calls[-1][0])
        assert query["FID_COND_MRKT_DIV_CODE"] == "U"   # 주식의 "J" 가 아니다
        # 질의에는 **KIS 코드**가 실린다 — 우리 축을 그대로 실으면 남의 지수가 온다
        assert query["FID_INPUT_ISCD"] == KIS_CODE
        assert query["FID_INPUT_ISCD"] != UNIT_ID
        assert query["FID_ETC_CLS_CODE"] == "0"         # 값 축이다 — 다른 지수 클래스를 부르면 안 된다
        assert query["FID_PW_DATA_INCU_YN"]

    def test_input_hour_carries_interval_not_a_clock_time(self):
        """`FID_INPUT_HOUR_1` 은 간격(초)이다 — 주식처럼 HHMMSS 를 실으면 안 된다.

        **window 를 바꿔도 질의가 같아야** 이 축이 고정된다. 상수 하나만 대조하면
        `!= window_end.strftime(...)` 같은 단언은 실패할 입력이 없어 아무것도 안 막는다
        — 반대로 window 가 질의에 새면 두 URL 이 갈려 여기서 잡힌다. 새어도 벤더는
        무시하고 최신 100봉을 주므로 **응답으로는 끝내 티가 안 난다**.
        """
        client, fake = make_client([TOKEN, ok([row()]), ok([row()])])
        client.candles(UNIT_ID, window_end=WINDOW_END)
        client.candles(UNIT_ID, window_end=WINDOW_END + timedelta(minutes=17))
        first, second = fake.calls[-2][0], fake.calls[-1][0]
        assert query_of(first)["FID_INPUT_HOUR_1"] == str(LANE_INTERVAL_SEC)
        assert first == second

    def test_non_lane_interval_is_rejected_at_startup(self):
        # 다른 간격이면 라벨이 1분 격자에 안 얹혀 전 지수가 매 window missing 인데,
        # 원장에는 "벤더가 안 준다"로 보인다 — 기동에서 막는다
        with pytest.raises(SystemExit):
            KisSectorIndexClient("k", "s", FakeClient([]), INDEX_MAP, interval_sec=300)


class TestCandles:
    def test_query_uses_vendor_code_but_candle_carries_our_code(self):
        """🔴 번역의 양쪽 끝을 한 번에 고정한다 — 어느 쪽이 새도 조용히 틀린다.

        질의에 우리 축이 실리면 **남의 지수**가 오고(KIS 는 `rt_cd=0` 을 준다), 봉에
        벤더 축이 남으면 canonical 의 `unit_id` 가 KIS 코드가 돼 일봉 `sector_index`
        와 조인이 안 된다. 두 코드가 다른 표를 써야 이 단언이 축을 가린다.
        """
        client, fake = make_client([TOKEN, ok([row("103000")])])
        candles = client.candles(UNIT_ID, window_end=WINDOW_END)
        assert query_of(fake.calls[-1][0])["FID_INPUT_ISCD"] == KIS_CODE
        assert [c.symbol for c in candles] == [UNIT_ID]

    def test_unit_outside_the_map_is_not_a_vendor_failure(self):
        """맵에 없는 unit 은 **설정이 갈린 것**이다 — 재시도 축(missing)으로 접지 않는다.

        접으면 벤더가 안 준 것처럼 보여 매 window 반복되는데 고칠 건 config 한 줄이다
        (`inav_collect._rows_for` 가 같은 조건을 같은 축으로 낸다).
        """
        client, fake = make_client([])
        with pytest.raises(ValueError, match="index_map 에 없다"):
            client.candles("9999", window_end=WINDOW_END)
        assert fake.calls == []   # 모르는 코드로 벤더를 두드리지 않는다

    def test_empty_index_map_is_rejected_at_startup(self):
        with pytest.raises(SystemExit):
            make_client([], index_map={})

    def test_sentinel_rows_are_dropped(self):
        """`999999`·`888888` 은 봉이 아니다 — 남기면 없는 시각의 봉이 하루 두 번 실린다."""
        rows = [sentinel(label) for label in sorted(SENTINEL_LABELS)] + [row("103000")]
        client, _ = make_client([TOKEN, ok(rows)])
        candles = client.candles(UNIT_ID, window_end=WINDOW_END)
        assert [c.window_start.strftime("%H%M%S") for c in candles] == ["103000"]

    def test_sentinels_of_other_days_are_dropped_too(self):
        """센티넬은 **날짜마다** 붙는다 — 위치(맨 앞 2행)로 자르면 뒷날 것이 샌다."""
        rows = [sentinel("999999"), sentinel("888888"), row("103000"),
                sentinel("999999", day="20260806"), sentinel("888888", day="20260806")]
        client, _ = make_client([TOKEN, ok(rows)])
        assert len(client.candles(UNIT_ID, window_end=WINDOW_END)) == 1

    def test_other_trading_day_rows_are_not_parsed(self):
        """남의 날 행은 **파싱조차 안 한다** — 그 행의 하자가 오늘을 죽이면 안 된다.

        장 초반 페이지가 직전 거래일까지 뻗는 실측 성질 때문이다. 여기서는 전날 행을
        일부러 깨뜨려 둔다: 걸러지지 않으면 ValueError 로 오늘 window 가 통째로 죽는다.
        """
        broken_yesterday = {**row("152900", day="20260806"), "bstp_nmix_oprc": None}
        client, _ = make_client([TOKEN, ok([broken_yesterday, row("103000")])])
        candles = client.candles(UNIT_ID, window_end=WINDOW_END)
        assert [c.window_start.date().isoformat() for c in candles] == ["2026-08-07"]

    def test_same_window_with_different_values_is_rejected(self):
        # dict 로 접으면 **벤더 행 순서가 값을 정한다** — 어느 쪽이 참인지 우리가 못 고른다
        client, _ = make_client([TOKEN, ok([row("103000", close="5047.39"),
                                            row("103000", close="5048.00")])])
        with pytest.raises(ValueError, match="유일성"):
            client.candles(UNIT_ID, window_end=WINDOW_END)

    def test_identical_duplicate_row_passes(self):
        # 값이 같은 재등장은 데이터 충돌이 아니다 — 막으면 겹쳐 주는 벤더에서 전건 실패한다
        client, _ = make_client([TOKEN, ok([row("103000"), row("103000")])])
        assert len(client.candles(UNIT_ID, window_end=WINDOW_END)) == 1

    def test_envelope_violation_is_transient_not_invalid(self):
        """봉투 이상은 **재시도 축**이다 — INVALID 로 올리면 window 전체가 죽는다.

        `status_of` 는 invalid 하나로 window 를 INVALID 로 만들고 INVALID 는 재청구
        대상이 아닌데, 이 소스는 소급이 불가하다 → 잘린 본문 1건이 45종의 그 1분을
        영구히 지운다. `inav_collect._rows_for` 가 같은 이유로 봉투를 재시도 축에 뒀다.
        """
        envelope = json.dumps({"rt_cd": "0", "output1": {}, "output2": {"안": "list"}})
        client, _ = make_client([TOKEN, envelope])
        with pytest.raises(KisUnitError):
            client.candles(UNIT_ID, window_end=WINDOW_END)

    @pytest.mark.parametrize("broken_date", [None, 20260807, ["20260807"]])
    def test_non_string_trade_date_is_not_silently_another_day(self, broken_date):
        """거래일 형상 위반이 "남의 날"로 흘러선 안 된다 — 전건이 조용히 사라진다.

        벤더가 이 필드를 개명하면 102행 전부가 날짜 필터에 떨어져 `bars` 가 비고, 그건
        예외도 ERROR 도 없이 45종 전건 missing 인 INCOMPLETE 로 굳는다. 원장에는
        "벤더가 안 줬다"로 보인다.
        """
        rows = [{**row(label), "stck_bsop_date": broken_date}
                for label in ("103000", "103100")]
        client, _ = make_client([TOKEN, ok(rows)])
        with pytest.raises(ValueError, match="거래일 형상이 아니다"):
            client.candles(UNIT_ID, window_end=WINDOW_END)

    # 짧은 쪽만 재면 `!=` 를 `<` 로 바꿔도 안 갈린다(변이로 확인) — 초과 길이도 같이 잰다.
    @pytest.mark.parametrize("bad_length", ["2026087", "202608071"])
    def test_trade_date_length_drift_does_not_eat_the_time_label(self, bad_length):
        """날짜 자리수가 짧으면 라벨이 조용히 다른 분으로 읽힌다 — `strptime` 은 연접을 본다."""
        client, _ = make_client([TOKEN, ok([{**row("103000"), "stck_bsop_date": bad_length}])])
        with pytest.raises(ValueError, match="거래일 형상이 아니다"):
            client.candles(UNIT_ID, window_end=WINDOW_END)

    def test_token_json_damage_is_not_relabelled_as_an_envelope_fault(self):
        """토큰 발급 응답의 JSON 손상까지 봉투로 되감으면 안 된다.

        `_call` 이 `_headers()` 를 try 안에서 평가하고 그 경로가 `KisAuth.token()` →
        `json.loads` 라, `except ValueError` 가 넓으면 **인증 축 사고**가 unit 단위
        재시도로 접힌다 — 메시지가 엉뚱한 엔드포인트를 가리키고 45 unit 이 매 window
        토큰 발급을 다시 두드린다.
        """
        client, _ = make_client(["<html>proxy error</html>"])
        with pytest.raises(ValueError) as caught:
            client.candles(UNIT_ID, window_end=WINDOW_END)
        assert not isinstance(caught.value, KisUnitError)

    def test_empty_page_is_not_an_error(self):
        """빈 페이지는 실패가 아니다 — 개장 직후 첫 봉 전이 정상적으로 여기다.

        ⚠️ **틀린 지수코드도 같은 모양으로 온다**(`rt_cd=0` + 빈 output2, 실측). 둘은
        지속 여부로만 갈리므로 어댑터가 판정하지 않는다.
        """
        client, _ = make_client([TOKEN, ok([])])
        assert client.candles(UNIT_ID, window_end=WINDOW_END) == ()

    def test_whole_session_page_yields_every_minute(self):
        """페이지의 봉이 하나도 안 새는지 — 격자 전체로 센다(표본 한 칸이 아니라)."""
        labels = [f"{9 + (m // 60):02d}{m % 60:02d}00" for m in range(100)]
        rows = [sentinel("999999")] + [row(label) for label in labels]
        client, _ = make_client([TOKEN, ok(rows)])
        candles = client.candles(UNIT_ID, window_end=datetime(2026, 8, 7, 9, 1, tzinfo=KST))
        assert sorted(c.window_start.strftime("%H%M%S") for c in candles) == sorted(labels)


class TestConfigMap:
    """`[minute_sector_index.index_map]` 이 실측 정본과 갈리지 않는지."""

    @staticmethod
    def _config():
        from data_pipeline.config.loader import load_settings
        settings = load_settings(
            Path(__file__).resolve().parents[1]
            / "src/data_pipeline/config/sources.toml")
        assert settings.minute_sector_index is not None, "[minute_sector_index] 가 없다"
        return settings.minute_sector_index

    def test_forty_five_sectors(self):
        # 정본은 45종이다(레이크 sector_index 코드 집합 · sector_member 스냅샷 둘 다)
        index_map = self._config().index_map
        assert len(index_map) == 45
        assert sum(1 for k in index_map if k.startswith("1")) == 24   # KOSPI
        assert sum(1 for k in index_map if k.startswith("2")) == 21   # KOSDAQ

    def test_kis_codes_are_not_derivable_from_krx_codes(self):
        """🔴 규칙으로 유도할 수 있으면 다음 사람이 표를 지우고 산술을 쓴다.

        KOSPI 는 −1000 이 1045~1047 에서 깨지고 KOSDAQ 은 아예 관계가 없다. 이 단언이
        실패한다는 건 표가 규칙과 같아졌다는 뜻이고, 그때는 표가 아니라 **매핑이 틀린
        것**이다(KRX 코드를 그대로 넣으면 KIS 가 남의 지수를 조용히 준다).
        """
        index_map = self._config().index_map
        assert index_map["1005"] == "0005"      # KOSPI 앞부분은 −1000 이 맞다
        assert index_map["1045"] != "0045"      # 여기서 꺾인다
        assert index_map["1045"] == "0028"
        assert index_map["2012"] != "1012"      # KOSDAQ 은 산술 관계가 없다
        assert index_map["2012"] == "1006"
        assert index_map["2118"] == "1033"      # 229종목짜리 — 오류를 갈라낸 그 코드
        # 선행 0 이 의미를 가진다. 정수로 접히면 KOSPI 전건이 다른 지수를 부른다
        assert all(len(v) == 4 for v in index_map.values())

    def test_no_two_sectors_share_a_kis_code(self):
        values = list(self._config().index_map.values())
        assert len(set(values)) == len(values)


class TestConfigValidator:
    """검증기를 **직접** 태운다 — 실물 설정만 보면 검증기를 지워도 초록이다.

    실제로 그랬다: 위 `TestConfigMap` 은 커밋된 표에 하자가 없다는 것만 말하고, 표에
    하자를 넣었을 때 **로드가 거부되는지**는 한 줄도 증명하지 않았다(변이로 확인).
    """

    @staticmethod
    def _make(index_map):
        from data_pipeline.config.models import MinuteSectorIndexConfig
        return MinuteSectorIndexConfig(index_map=index_map)

    def test_two_sectors_pointing_at_one_index_is_rejected(self):
        # 통과시키면 두 unit 이 같은 값을 싣고 window 는 정상 VALID 다 — 층 분해가 그
        # 둘을 독립 후보로 보면 같은 계열이 두 번 선다. 오타 한 줄치고 조용하다.
        with pytest.raises(ValueError, match="같은 KIS 지수코드"):
            self._make({"1005": "0005", "2056": "0005"})

    @pytest.mark.parametrize("index_map, expected", [
        ({"105": "0005"}, "KRX 업종코드"),        # 3자리
        ({"10050": "0005"}, "KRX 업종코드"),      # 5자리
        ({"100A": "0005"}, "KRX 업종코드"),       # 숫자 아님
        ({"1005": "5"}, "KIS 지수코드"),          # 선행 0 이 잘렸다 — 정수화의 흔적
        ({"1005": "00050"}, "KIS 지수코드"),
    ])
    def test_malformed_code_is_rejected(self, index_map, expected):
        with pytest.raises(ValueError, match=expected):
            self._make(index_map)

    def test_swapped_key_and_value_is_rejected(self):
        """🔴 한 줄을 뒤집어 적어도 자리수 검사는 전부 통과한다 — 대역이 겹치기 때문이다.

        뒤집힌 줄은 개수도 값 중복도 정상이라 로드가 통과하는데, canonical 의 `unit_id`
        가 벤더 코드가 돼 일봉 `sector_index` 와 **어떤 조인에도 안 걸린다**. 동시에 그
        업종은 표에서 사라진다.
        """
        with pytest.raises(ValueError, match="뒤집혔나"):
            self._make({"0005": "1005"})

    def test_kospi200_namespace_code_is_rejected_as_a_value(self):
        """값 대역도 따로 막는다 — 키 대역 검사만으로는 이 오타가 안 걸린다.

        KIS 의 `2xxx` 는 업종이 아니라 KOSPI200 계열이다. 거기 코드를 붙여 넣으면
        (형태는 완벽한 숫자 4자리다) 그 업종 자리에 **KOSPI200 하위지수**가 실린다 —
        이번 트랙을 헤매게 한 그 오류의 모양 그대로다.
        """
        with pytest.raises(ValueError, match="뒤집혔나"):
            self._make({"1005": "2001"})

    def test_kosdaq_kis_code_band_overlaps_krx_kospi_band(self):
        """위 단언이 자리수로 못 잡는 이유를 못박는다 — 대역이 실제로 겹친다.

        겹치지 않는다면 자리수 검사만으로 충분하고 대역 검사는 죽은 코드다. 겹치는
        한에서만 그 가드가 산다 — 표가 바뀌어 겹침이 사라지면 여기서 알게 된다.
        """
        index_map = TestConfigMap._config().index_map
        kis_values = set(index_map.values())
        krx_keys = set(index_map)
        assert kis_values & {k for k in krx_keys if k.startswith("1")}

    def test_untranslated_row_is_rejected(self):
        """🔴 `"1008" = "1008"` — 자리수·대역·중복을 전부 통과하는 "번역 누락"이다.

        KIS `1008` 은 KOSDAQ 지수라 그 자리에 남의 지수가 조용히 실린다. KOSPI 24행 중
        10행이 이 구멍에 있었다(대역이 겹치는 코드들).
        """
        with pytest.raises(ValueError, match="번역이 빠졌나"):
            self._make({"1008": "1008"})

    def test_empty_map_is_rejected(self):
        # 빈 맵은 "받을 게 없다"가 아니라 배선 누락이다 — Worker 가 매 window 를 빈
        # 성공으로 확정해 그날이 통째로 조용히 지나간다
        with pytest.raises(ValueError):
            self._make({})

    def test_valid_map_passes(self):
        # 거부 단언만 있으면 검증기가 전건 거부로 망가져도 초록이다
        assert self._make({"1005": "0005", "2118": "1033"}).index_map["2118"] == "1033"
