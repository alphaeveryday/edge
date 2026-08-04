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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_minute import (
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
