"""KIS 분봉 어댑터 테스트 (ALPHA-735).

⚠️ 응답 payload 는 **녹화가 아니라 실측 필드 목록**(ALPHA-644 스파이크 메모, 2026-08-03)에서
구성했다 — 토스 fixture(`tests/fixtures/toss/`, 실호출 녹화)와 신뢰 등급이 다르다. 그래서
여기서 고정하는 건 "우리가 실측으로 안다고 적어둔 것"이고, 그 목록 밖(예: 토큰 만료 응답
형상)은 테스트가 증명하지 않는다.

고정하는 계약:
1. **`stck_cntg_hour` 는 구간의 끝** — `window_start = 라벨 − 1분`, 시간대는 KST 고정.
   뒤집히면 전 구간이 한 칸 밀린 채 커밋되는데 봉 수는 그대로라 어떤 게이트도 안 걸린다.
2. **무거래 분도 행이 온다**(`cntg_vol=0`) — 행 부재(missing)와 다른 축이다.
3. **초당한도는 200 본문 `EGW00201`** — HTTP 429 가 아니라 어댑터가 재시도한다.
4. **소스 전역(키·권한) 실패와 종목 단위 실패를 예외 타입으로 가른다** — 섞이면 400종
   missing 인 INCOMPLETE 가 매분 쌓이는데 고칠 건 설정 하나다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_minute import (
    MAX_DAY_PAGES,
    KisHistoricalMinuteClient,
    KisMinuteClient,
    KisSourceError,
    KisUnitError,
    parse_minute_row,
)

KST = timezone(timedelta(hours=9))
WINDOW_END = datetime(2026, 8, 3, 10, 30, tzinfo=KST)


def row(hour: str = "103000", *, volume: str = "1200", close: str = "72500") -> dict:
    """실측 필드 목록 그대로의 output2 행 — 전부 문자열이다."""
    return {
        "stck_bsop_date": "20260803",
        "stck_cntg_hour": hour,
        "stck_prpr": close,
        "stck_oprc": "72400",
        "stck_hgpr": "72600",
        "stck_lwpr": "72300",
        "cntg_vol": volume,
        "acml_tr_pbmn": "123456789",
    }


def flat_row(hour: str = "103000") -> dict:
    """무거래 분 — 행은 있고 `cntg_vol=0`, OHLC 는 직전가로 flat(저유동 ETF 실측)."""
    return {
        "stck_bsop_date": "20260803",
        "stck_cntg_hour": hour,
        "stck_prpr": "9000", "stck_oprc": "9000",
        "stck_hgpr": "9000", "stck_lwpr": "9000",
        "cntg_vol": "0", "acml_tr_pbmn": "0",
    }


def ok(rows: list[dict]) -> str:
    return json.dumps({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리",
                       "output1": {}, "output2": rows})


def err(msg_cd: str, msg1: str = "오류") -> str:
    return json.dumps({"rt_cd": "1", "msg_cd": msg_cd, "msg1": msg1})


TOKEN = json.dumps({"access_token": "tok-1", "expires_in": 86400})
TOKEN2 = json.dumps({"access_token": "tok-2", "expires_in": 86400})


class FakeClient:
    """응답을 순서대로 돌려주는 PoliteClient 대역 — 벽시계 대기 없이 재시도만 관찰한다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.slept: list[float] = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls.append((url, dict(headers or {})))
        if not self.responses:
            raise AssertionError(f"예상 밖 추가 호출: {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _sleep(self, seconds):
        self.slept.append(seconds)


def make_client(responses, **kwargs):
    fake = FakeClient(responses)
    client = KisMinuteClient("app-key", "app-secret", fake, **kwargs)
    return client, fake


class TestParse:
    def test_hour_label_is_window_end(self):
        # 실측: stck_cntg_hour 는 구간의 **끝**이다. window_start 는 1분 앞.
        candle = parse_minute_row(row("103000"), "005930")
        assert candle.window_end == datetime(2026, 8, 3, 10, 30, tzinfo=KST)
        assert candle.window_start == datetime(2026, 8, 3, 10, 29, tzinfo=KST)

    def test_values_are_decimal_not_float(self):
        candle = parse_minute_row(row(close="72500.5"), "005930")
        assert candle.close == Decimal("72500.5")
        assert candle.volume == Decimal("1200")
        # KIS 는 통화를 주지 않는다 — 지어내지 않는다
        assert candle.currency is None

    def test_zero_volume_row_is_not_traded(self):
        # 무거래 분: 행은 있고 거래량만 0 — collector 가 no_trade 로 센다(missing 아님)
        assert parse_minute_row(flat_row(), "439870").traded is False

    @pytest.mark.parametrize("broken", [
        {**row(), "stck_cntg_hour": "99:99:99"},   # 시각 형식
        {**row(), "stck_bsop_date": None},         # 날짜 결측
        {**row(), "cntg_vol": "-1"},               # 음수 거래량
        {**row(), "stck_lwpr": "99999"},           # OHLC 정합 위반(low > high)
        {**row(), "stck_prpr": "0"},               # 양수 아님
        {**row(), "stck_oprc": None},              # 필드 결측
    ])
    def test_broken_row_raises(self, broken):
        # 조용히 기본값으로 접으면 그 window 가 '정상 수집'으로 커밋된다(Rule 12)
        with pytest.raises(ValueError):
            parse_minute_row(broken, "005930")


class TestCandles:
    def test_requests_window_end_as_hour(self):
        # ⚠️ 요청 window 를 창의 끝으로 고정한다 — '최신'으로 부르면 400종 도는 사이
        # 최신 봉이 다음 분으로 넘어가 뒤쪽 종목이 통째로 missing 이 된다
        client, fake = make_client([TOKEN, ok([row()])])
        client.candles("005930", window_end=WINDOW_END)
        url, headers = fake.calls[-1]
        assert "FID_INPUT_HOUR_1=103000" in url
        assert "FID_INPUT_ISCD=005930" in url
        assert headers["tr_id"] == "FHKST03010200"

    def test_returns_all_rows_of_the_call(self):
        # 한 콜이 30분치라 collector 가 그중 자기 window 만 고른다
        client, _ = make_client([TOKEN, ok([row("103000"), row("102900")])])
        candles = client.candles("005930", window_end=WINDOW_END)
        assert [c.window_end.strftime("%H%M") for c in candles] == ["1030", "1029"]

    def test_empty_output2_is_empty_result(self):
        # 빈 list 는 정상 — 그 창에 봉이 없다(collector 가 missing 으로 센다)
        client, _ = make_client([TOKEN, ok([])])
        assert client.candles("005930", window_end=WINDOW_END) == ()

    def test_malformed_success_raises_shape_error(self):
        # rt_cd=0 인데 output2 가 list 가 아니면 '정상 0건'으로 위장하면 안 된다
        client, _ = make_client([TOKEN, json.dumps({"rt_cd": "0", "output2": {}})])
        with pytest.raises(ValueError, match="output2 이상"):
            client.candles("005930", window_end=WINDOW_END)

    def test_vendor_error_is_unit_level(self):
        # 종목 단위 오류는 그 종목만 실패한다(재시도로 풀릴 수 있는 축)
        client, _ = make_client([TOKEN, err("40580000", "종목코드 오류")])
        with pytest.raises(KisUnitError):
            client.candles("999999", window_end=WINDOW_END)

    def test_broken_json_is_unit_level(self):
        client, _ = make_client([TOKEN, "<html>gateway</html>"])
        with pytest.raises(KisUnitError, match="JSON 손상"):
            client.candles("005930", window_end=WINDOW_END)

    def test_transport_exhaustion_is_unit_level(self):
        # PoliteClient 의 5xx·네트워크 재시도 소진 — 한 종목의 전송 실패가 window 를
        # 통째로 죽이면 앞서 모은 종목까지 버려진다
        client, _ = make_client([TOKEN, RuntimeError("GET 재시도 소진: timeout")])
        with pytest.raises(KisUnitError, match="요청 실패"):
            client.candles("005930", window_end=WINDOW_END)


class TestRateLimit:
    def test_egw00201_retries_and_counts(self):
        # 초당한도는 HTTP 429 가 아니라 200 본문으로 온다 — 운반 계층이 모르니 여기서 재시도
        client, fake = make_client([TOKEN, err("EGW00201", "초당 거래건수 초과"), ok([row()])])
        candles = client.candles("005930", window_end=WINDOW_END)
        assert len(candles) == 1
        assert fake.slept  # 실제로 물러났다
        # retry_count 는 window 결과에 실린다 — 0 으로 고정되면 유량 압력이 관측에서 사라진다
        assert client.retry_count == 1

    def test_egw00201_exhausted_is_unit_level(self):
        # 예산을 다 써도 unit 축이다 — 소스 전역으로 올리면 유량 한 번에 window 가 죽는다
        client, _ = make_client([TOKEN] + [err("EGW00201")] * 5)
        with pytest.raises(KisUnitError, match="EGW00201"):
            client.candles("005930", window_end=WINDOW_END)

    def test_sustained_exhaustion_escalates_to_source_level(self):
        # 연속 유량 소진은 종목이 아니라 앱키 전역의 상태다 — 종목별 missing 으로만
        # 접으면 백오프 합(~7초)이 400종에 곱해져 60초 창·claim lease 를 다 태운다
        # (window 폭주). RATE_STREAK_LIMIT 번째 종목에서 소스 전역으로 승격해야 한다.
        from data_pipeline.sources.kis_minute import RATE_STREAK_LIMIT

        client, _ = make_client([TOKEN] + [err("EGW00201")] * (5 * RATE_STREAK_LIMIT))
        for _n in range(RATE_STREAK_LIMIT - 1):
            with pytest.raises(KisUnitError):
                client.candles("005930", window_end=WINDOW_END)
        with pytest.raises(KisSourceError, match="연속"):
            client.candles("005930", window_end=WINDOW_END)

    def test_success_resets_the_exhaustion_streak(self):
        # 유량이 회복되면(성공 1콜) 승격 판정은 리셋 — 산발적 소진이 누적돼 오후에
        # 엉뚱하게 소스 전역으로 승격되면 정상 window 가 통째로 죽는다.
        from data_pipeline.sources.kis_minute import RATE_STREAK_LIMIT

        responses = [TOKEN]
        for _n in range(RATE_STREAK_LIMIT - 1):
            responses += [err("EGW00201")] * 5
        responses += [ok([row()])]
        responses += [err("EGW00201")] * 5
        client, _ = make_client(responses)
        for _n in range(RATE_STREAK_LIMIT - 1):
            with pytest.raises(KisUnitError):
                client.candles("005930", window_end=WINDOW_END)
        assert len(client.candles("005930", window_end=WINDOW_END)) == 1  # 회복
        with pytest.raises(KisUnitError):  # 리셋됐으니 다시 unit 축부터
            client.candles("005930", window_end=WINDOW_END)


class TestAuthAxis:
    def test_source_level_4xx_propagates(self):
        # 키·권한·쿼터는 종목을 바꿔도 안 풀린다 — unit missing 으로 접으면 아무도
        # 그 설정 하나를 고치러 가지 않는다
        client, _ = make_client([TOKEN, StopFetch("HTTP 403", status=403, body="no permission")])
        with pytest.raises(KisSourceError):
            client.candles("005930", window_end=WINDOW_END)

    def test_expired_token_body_reissues_once(self):
        # 상주 워커는 토큰(24h)보다 오래 산다 — 메모리 캐시가 만료를 스스로 못 보므로
        # 만료 신호에 캐시를 버리고 재발급해야 그 뒤 window 들이 산다
        client, fake = make_client([
            TOKEN, err("EGW00123", "기간이 만료된 token 입니다"), TOKEN2, ok([row()]),
        ])
        assert len(client.candles("005930", window_end=WINDOW_END)) == 1
        assert fake.calls[-1][1]["authorization"] == "Bearer tok-2"

    def test_expired_token_4xx_invalidates_cache(self):
        # 4xx 로 오는 만료: 그 종목은 잃지만(원장이 재청구) 캐시는 반드시 버린다 —
        # 안 버리면 남은 유효기간 내내 전 종목이 같은 401 을 맞는다
        client, fake = make_client([
            TOKEN, StopFetch("HTTP 401", status=401, body='{"error_code":"EGW00123"}'),
            TOKEN2, ok([row()]),
        ])
        with pytest.raises(KisUnitError, match="토큰 만료"):
            client.candles("005930", window_end=WINDOW_END)
        assert len(client.candles("000660", window_end=WINDOW_END)) == 1
        assert fake.calls[-1][1]["authorization"] == "Bearer tok-2"

    def test_token_is_reused_across_symbols(self):
        # 종목마다 발급하면 분당 1회 제한에 즉시 걸린다(kis-token-reuse 실측)
        client, fake = make_client([TOKEN, ok([row()]), ok([row()])])
        client.candles("005930", window_end=WINDOW_END)
        client.candles("000660", window_end=WINDOW_END)
        assert sum("tokenP" in url for url, _ in fake.calls) == 1


class TestHistoricalCandles:
    """소급(과거 거래일) 분봉 — `FHKST03010230` (ALPHA-846).

    ⚠️ 이 TR 은 당일 TR 과 **계약이 다르다**(2026-08-08 실측, 08-03 전수):
    무거래 분 행을 아예 주지 않고(`cntg_vol=0` 행 0건), 응답이 거래일 경계를 넘어
    직전 거래일로 계속 내려간다. 둘 다 어댑터가 흡수하지 않으면 원장이 관대해지는 쪽으로
    틀린다 — 전자는 그 분들이 `missing` 이 돼 window 가 영원히 INCOMPLETE 이고,
    후자는 남의 날 봉이 이 날 canonical 에 실린다.
    """

    def hist(self, responses, session_date=date(2026, 8, 3)):
        fake = FakeClient(responses)
        return KisHistoricalMinuteClient(
            "app-key", "app-secret", fake, session_date=session_date
        ), fake

    def at(self, hhmm: str) -> datetime:
        return datetime(2026, 8, 3, int(hhmm[:2]), int(hhmm[2:]), tzinfo=KST)

    def other_day(self, hour: str) -> dict:
        return {**row(hour), "stck_bsop_date": "20260731"}

    def test_uses_historical_tr_and_date_axis(self):
        # 당일 TR 로는 과거를 못 받는다 — 날짜 축이 없어 오늘 봉이 오늘 라벨로 돌아온다
        client, fake = self.hist([TOKEN, ok([row("153000"), self.other_day("152900")])])
        client.candles("005930", window_end=self.at("1530"))
        url, headers = fake.calls[-1]
        assert headers["tr_id"] == "FHKST03010230"
        assert "FID_INPUT_DATE_1=20260803" in url
        assert "inquire-time-dailychartprice" in url

    def test_no_trade_minutes_are_synthesized_flat(self):
        """벤더가 빼먹은 분을 직전 종가 flat·거래량 0 으로 채운다.

        채우지 않으면 같은 시장 사실(무거래)이 당일 경로에선 `no_trade`(성공), 소급
        경로에선 `missing`(실패)이 된다 — 439870 은 390분 중 176행뿐이라 하루의 절반이
        실패로 쌓이고 그 window 는 영원히 INCOMPLETE 다.
        """
        client, _ = self.hist([
            TOKEN, ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        filled = client.candles("005930", window_end=self.at("1028"))
        assert len(filled) == 1
        candle = filled[0]
        assert candle.volume == Decimal(0)
        assert candle.traded is False
        # 직전가 flat — 10:27 봉의 **종가**여야 한다(시가·고가를 쓰면 조용히 다른 값이 된다)
        assert (candle.open, candle.high, candle.low, candle.close) == (
            Decimal("72500"), Decimal("72500"), Decimal("72500"), Decimal("72500"))
        # 관측된 봉은 그대로다 — 합성이 실측을 덮어쓰지 않는다
        assert client.candles("005930", window_end=self.at("1027"))[0].volume == Decimal("1200")

    def test_fill_does_not_extend_past_observed_range(self):
        # 첫 체결 앞은 직전가를 모르고, 마지막 관측 뒤는 더 거래됐는지 모른다 —
        # 채우면 관측하지 않은 구간을 관측한 것처럼 만든다(missing 이 사실에 가깝다)
        client, _ = self.hist([
            TOKEN, ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        assert client.candles("005930", window_end=self.at("1026")) == ()
        assert client.candles("005930", window_end=self.at("1031")) == ()

    def test_other_trading_day_rows_are_cut(self):
        # 경계를 넘은 행을 남기면 07-31 봉이 08-03 canonical 에 실린다
        client, _ = self.hist([
            TOKEN, ok([row("091000")]), ok([self.other_day("090900"), self.other_day("090800")]),
        ])
        assert len(client.candles("005930", window_end=self.at("0910"))) == 1
        assert client.candles("005930", window_end=self.at("0909")) == ()

    def test_boundary_page_ends_paging(self):
        """경계를 넘은 응답이 곧 "그 날은 다 받았다"는 신호다 — 한 페이지에 두 날이
        섞여 있어도 더 부르지 않는다.

        ⚠️ 이 계약은 **혼합 페이지**에서만 갈린다(전건이 남의 날이면 '봉이 없다'와
        구분되지 않는다). 안 멈추면 09:00 아래로 계속 물어 예산까지 헛돈다.
        """
        client, fake = self.hist([TOKEN, ok([row("091000"), self.other_day("090900")])])
        assert len(client.candles("005930", window_end=self.at("0910"))) == 1
        assert len(fake.calls) == 2  # 토큰 1 + 페이지 1

    def test_day_is_fetched_once_for_all_windows(self):
        # window 마다 부르면 390 × 362 = 141k 콜이다 — 앱키 유량은 전역이라 그 차이가
        # 그대로 다른 KIS 스텝의 예산을 먹는다(하루 4콜 × 362종이 설계 근거)
        client, fake = self.hist([
            TOKEN, ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        for hhmm in ("1027", "1028", "1029", "1030"):
            client.candles("005930", window_end=self.at(hhmm))
        # 토큰 1 + 페이지 2. FakeClient 는 응답이 동나면 AssertionError 를 내므로
        # 추가 호출은 이 테스트가 통과하는 것 자체로 배제된다
        assert len(fake.calls) == 3

    def test_other_session_date_is_refused(self):
        # 캐시가 하루에 묶여 있다 — 다른 날짜를 받으면 조용히 남의 날 봉을 준다
        client, _ = self.hist([TOKEN])
        with pytest.raises(ValueError, match="날짜 불일치"):
            client.candles("005930", window_end=datetime(2026, 8, 4, 10, 30, tzinfo=KST))

    def test_page_budget_exhaustion_fails_the_unit(self):
        """하루가 안 끝났는데 예산이 끝나면 **실패**다 — 잘린 하루를 커밋하지 않는다.

        합성이 사이를 메우므로, 여기서 조용히 반환하면 못 받은 구간이 4분류에서
        `no_trade` 로 위장돼 window 가 VALID 로 확정된다.
        """
        # 매 페이지 1분씩만 내려가는 응답 — 경계에도 09:00 에도 못 닿는다
        pages = [ok([row(f"15{29 - i:02d}00")]) for i in range(MAX_DAY_PAGES + 1)]
        client, _ = self.hist([TOKEN, *pages])
        with pytest.raises(KisUnitError, match="페이지 예산"):
            client.candles("005930", window_end=self.at("1529"))

    def test_no_progress_response_stops_paging(self):
        # 벤더가 같은 창을 반복하면 예산까지 헛돈다 — 진전이 없으면 멈춘다
        client, fake = self.hist([TOKEN, ok([row("153000")]), ok([row("153000")])])
        assert len(client.candles("005930", window_end=self.at("1530"))) == 1
        assert len(fake.calls) == 3  # 토큰 1 + 페이지 2 (예산 8 을 다 쓰지 않는다)
