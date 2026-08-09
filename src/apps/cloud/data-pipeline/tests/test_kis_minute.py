"""KIS 분봉 어댑터 테스트 (ALPHA-735).

⚠️ 응답 payload 는 **녹화가 아니라 실측 필드 목록**(ALPHA-644 스파이크 메모, 2026-08-03)에서
구성했다 — 토스 fixture(`tests/fixtures/toss/`, 실호출 녹화)와 신뢰 등급이 다르다. 그래서
여기서 고정하는 건 "우리가 실측으로 안다고 적어둔 것"이고, 그 목록 밖(예: 토큰 만료 응답
형상)은 테스트가 증명하지 않는다.

고정하는 계약:
1. **`stck_cntg_hour` 는 구간의 끝** — `window_start = 라벨 − 1분`, 시간대는 KST 고정.
   뒤집히면 전 구간이 한 칸 밀린 채 커밋되는데 봉 수는 그대로라 어떤 게이트도 안 걸린다.
2. **무거래 분도 행이 온다**(`cntg_vol=0`) — 행 부재(missing)와 다른 축이다.
   ⚠️ 이건 **당일 TR 한정**이다. 소급 TR(`FHKST03010230`)은 그 분을 아예 안 주고,
   어댑터가 직전 종가 flat 으로 채워 같은 축으로 만든다(`TestHistoricalCandles`).
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

    def test_off_grid_bar_does_not_raise_in_the_parser(self):
        """⛔ 격자 가드를 파서로 올리지 마라 — 형제(`kis_sector_index`)를 따라 옮겼다가
        되돌린 자리다. 이 테스트가 그 회귀를 막는다.

        가르는 것은 **window 키와 충돌할 수 있느냐**다. 짧은 라벨은 `second == 0` 으로
        파싱될 수 있어 계획 키와 정확히 일치할 수 있으니 파서에서 막아야 한다. 격자 밖 봉은
        분 정렬된 어떤 키와도 못 만나 `select_window_candle` 이 그냥 안 뽑는다 —
        여기서 raise 하면 **남의** 행 하나로 그 window 를 INVALID 로 만들 뿐이고,
        30봉 페이지라 ~30 window 가 그렇게 된다. 소급은 합성이 봉에 앵커돼 사정이
        다르므로 가드가 `_fetch_day` 에 있다(`TestHistoricalCandles` 가 잰다).
        """
        candle = parse_minute_row({**row(), "stck_cntg_hour": "103030"}, "005930")
        assert candle.window_end == datetime(2026, 8, 3, 10, 30, 30, tzinfo=KST)

    def test_zero_volume_row_is_not_traded(self):
        # 무거래 분: 행은 있고 거래량만 0 — collector 가 no_trade 로 센다(missing 아님)
        assert parse_minute_row(flat_row(), "439870").traded is False

    @pytest.mark.parametrize("short_label, silently_becomes", [
        ("1030", "10:03:00"),    # 4자리 — 날짜 자리를 훔쳐 **다른 분**이 된다
        ("30000", "03:00:00"),   # 선행 0 잘림(`kis_inav._time_stamp` 의 그 함정)
        # 5자리는 선행 0 을 되붙이면 **맞는 값이 나온다** — 우연이 아니다. 그래도 막는
        # 이유는 4자리와 구분할 근거가 없어서다(소스 주석에 논거를 적어 뒀다).
        ("93000", "09:30:00"),
    ])
    def test_short_time_label_is_rejected_before_parsing(self, short_label, silently_becomes):
        """🔴 자리수를 안 막으면 **값이 조용히 틀린다** — 형식 오류로 안 드러난다.

        `strptime("%Y%m%d%H%M%S")` 는 연접 문자열 하나를 보므로 짧은 라벨이 날짜 자리를
        훔쳐 간다. 결과의 `second` 가 0 이라 어느 가드에도 안 걸리고, 그 봉은 멀쩡한
        다른 window 에 앉아 확정된다. `kis_sector_index`·`kis_inav` 와 같은 문이다.
        """
        # 가드가 막는 값이 **무엇이 되는지**를 여기서 못박는다 — 주석으로만 적으면 낡고,
        # 그러면 이 가드가 왜 있는지 다음 사람이 모른다. 날짜는 아래 `row()` 가 실제로
        # 먹이는 그 값을 써야 한다(따로 적으면 픽스처가 바뀔 때 조용히 갈라진다).
        stolen = datetime.strptime(row()["stck_bsop_date"] + short_label, "%Y%m%d%H%M%S")
        assert stolen.strftime("%H:%M:%S") == silently_becomes
        with pytest.raises(ValueError, match="날짜·시각 형상이 아니다"):
            parse_minute_row({**row(), "stck_cntg_hour": short_label}, "005930")

    # 짧은 쪽만 재면 `!=` 를 `<` 로 바꿔도 안 갈린다 — 파서에서도 초과 길이를 같이 잰다.
    @pytest.mark.parametrize("field, bad_length", [
        ("stck_bsop_date", "2026087"), ("stck_bsop_date", "202608031"),
        ("stck_cntg_hour", "1030000"),
    ])
    def test_date_length_drift_is_rejected_too(self, field, bad_length):
        """자리수가 **8·6 이 아니다**를 재는 것이지 "짧다"를 재는 게 아니다."""
        with pytest.raises(ValueError, match="날짜·시각 형상이 아니다"):
            parse_minute_row({**row(), field: bad_length}, "005930")

    def test_space_padded_label_is_rejected_even_though_it_reads_right(self):
        """자리수만 재면 공백 패딩이 6자리째 샌다 — `strptime` 이 `%H` 에 `" 9"` 를 받는다.

        값은 **맞게** 읽힌다(`" 93000"` → 09:30:00). 그래서 더 막아야 한다: 조용히
        흡수하면 벤더가 패딩 규약을 바꾼 것을 아무도 못 본다. 이 가드의 목적이 값 보호가
        아니라 **형상 변화의 신호 보존**이라는 것이 여기서 갈린다.
        """
        assert datetime.strptime("20260803" + " 93000", "%Y%m%d%H%M%S").hour == 9
        with pytest.raises(ValueError, match="날짜·시각 형상이 아니다"):
            parse_minute_row({**row(), "stck_cntg_hour": " 93000"}, "005930")

    def test_non_ascii_digit_label_is_rejected(self):
        """`isdecimal()` 만으로는 안 된다 — `%H` 의 `\\d` 가 유니코드 Nd 라 같은 집합이다.

        아랍-인도 숫자가 섞인 `"1٠3000"` 은 `isdecimal()` 도 `strptime` 도 통과해
        10:30:00 으로 **맞게** 읽힌다. 막는 건 `isascii()` 쪽이다.
        """
        weird = "1٠3000"
        assert weird.isdecimal() and not weird.isascii()
        assert datetime.strptime("20260803" + weird, "%Y%m%d%H%M%S").minute == 30
        with pytest.raises(ValueError, match="날짜·시각 형상이 아니다"):
            parse_minute_row({**row(), "stck_cntg_hour": weird}, "005930")

    # 🔴 **날짜 쪽**도 같이 잰다 — 라벨만 검사하면 이 두 문이 그대로 열린다(변이로
    # 확인: `(day+hour)` 를 `hour` 로 좁히면 아무 테스트도 안 갈렸다).
    @pytest.mark.parametrize("bad_date", [
        "202608 3",    # 공백 패딩. `%d` 의 `" [1-9]"` 대안으로 08-03 이 된다
        "٢٠٢٦0803",   # 비-ASCII 숫자. `%Y` 의 `\d` 가 Nd 라 2026 이 된다
    ])
    def test_date_side_leniency_is_rejected_too(self, bad_date):
        """가드가 연접 문자열 **전체**를 봐야 하는 이유 — 관대함이 날짜 쪽에도 있다."""
        assert datetime.strptime(bad_date + "103000", "%Y%m%d%H%M%S") \
            == datetime(2026, 8, 3, 10, 30)
        with pytest.raises(ValueError, match="\uB0A0\uC9DC\u00B7\uC2DC\uAC01 \uD615\uC0C1\uC774 \uC544\uB2C8\uB2E4"):
            parse_minute_row({**row(), "stck_bsop_date": bad_date}, "005930")

    def test_well_formed_but_impossible_time_is_a_format_error(self):
        """자리수 가드를 통과한 뒤 `strptime` 이 잡는 분기 — 메시지로 갈라 둔다.

        가드가 넓어지면 이 분기가 조용히 죽는다(전에 `"99:99:99"` 8자가 그랬다).
        `pytest.raises(ValueError)` 만으로는 어느 문에서 걸렸는지 구분이 안 된다.
        """
        with pytest.raises(ValueError, match="시각 형식 오류"):
            parse_minute_row({**row(), "stck_cntg_hour": "999999"}, "005930")

    @pytest.mark.parametrize("broken", [
        # 자리수는 맞고 값만 깨진 라벨 — 위 자리수 가드가 아니라 `strptime` 이 잡는
        # 경로다. 8자리(`"99:99:99"`)로 두면 자리수 가드에 먼저 걸려 이 분기가 죽는다.
        {**row(), "stck_cntg_hour": "999999"},     # 시각 형식
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


def test_paging_bounds_track_the_session_clock():
    """`DAY_FIRST/LAST_HHMMSS` 는 `models.SESSION_OPEN/CLOSE` 의 두 번째 사본이다.

    레이어상 `sources/` 가 `minute/` 를 import 할 수 없어 상수를 합칠 수 없다. 갈리면
    조용하지 않고 **무한 재청구**가 된다: planner 가 새 범위로 window 를 만들고, 소급
    클라이언트는 그 window 를 범위 밖으로 거부하며, 그 `KisSourceError` 는 `_process` 의
    catch-all 에 window 실패로 접혀 lease 만료 → 재청구 → 재실패로 세션이 안 마른다.
    기동 게이트는 `extended_hours_ids` 만 보므로 이 축은 못 잡는다.
    """
    from data_pipeline.minute.models import SESSION_CLOSE, SESSION_OPEN
    from data_pipeline.sources.kis_minute import DAY_FIRST_HHMMSS, DAY_LAST_HHMMSS

    assert DAY_FIRST_HHMMSS == SESSION_OPEN.strftime("%H%M%S")
    assert DAY_LAST_HHMMSS == SESSION_CLOSE.strftime("%H%M%S")


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
        """당일 TR 로는 과거를 못 받는다 — 날짜 축이 없어 오늘 봉이 오늘 라벨로 돌아온다.

        ⚠️ 쿼리를 **통째로** 못박는다. 파라미터 하나만 골라 단언하면 나머지는 단언
        표면 밖이라 조용히 뒤집힌다 — `FID_FAKE_TICK_INCU_YN=Y` 는 허봉(체결 없는 가상
        틱)을 진짜 봉으로 들이는데, 그 봉은 거래량 0·flat 이라 `no_trade`(정상)로 집계돼
        관측하지 않은 값이 canonical 에 실린다.
        """
        client, fake = self.hist([TOKEN, ok([row("153000"), self.other_day("152900")])])
        client.candles("005930", window_end=self.at("1530"))
        url, headers = fake.calls[-1]
        assert headers["tr_id"] == "FHKST03010230"
        assert url.endswith(
            "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
            "?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=005930"
            "&FID_INPUT_HOUR_1=153000&FID_INPUT_DATE_1=20260803"
            "&FID_PW_DATA_INCU_YN=Y&FID_FAKE_TICK_INCU_YN=N"
        ), url

    @pytest.mark.parametrize("broken", [
        "not-a-dict",                              # output2 원소가 객체가 아님
        {k: v for k, v in row().items() if k != "stck_cntg_hour"},   # 시각 키 누락
        {**row(), "stck_cntg_hour": 103000},       # 시각이 int
    ])
    def test_broken_row_stays_a_shape_error(self, broken):
        """손상 행은 **`ValueError`** 여야 한다 — collector 가 unit INVALID 로 접는 축.

        원시 dict 를 `parse_minute_row` 앞에서 만지면 `AttributeError`·`KeyError`·
        `TypeError` 로 새는데, `KisPriceCollector._candle_for` 는 그 셋을 안 잡는다.
        그러면 종목 하나의 손상 행이 **362종 window 를 통째로** 죽이고, lease 만료
        재청구가 같은 응답에 같은 예외를 결정적으로 반복해 그 window 가 영영 안 커밋된다.
        """
        client, _ = self.hist([TOKEN, ok([broken])])
        with pytest.raises(ValueError):
            client.candles("005930", window_end=self.at("1030"))

    def test_broken_row_of_another_trading_day_is_not_our_problem(self):
        """어차피 버릴 07-31 행의 하자로 08-03 수집이 죽으면 안 된다.

        경계 페이지에는 늘 남의 날 행이 섞여 온다(그게 종료 신호다). 그 행까지 파싱하면
        `build_candle` 의 정합(가격 > 0 · low ≤ open/close ≤ high)을 전부 통과해야 하고,
        하나만 깨져도 이 종목이 INVALID 로 접힌다 — 우리가 쓰지도 않을 데이터 때문에.
        """
        broken_foreign = {**self.other_day("152900"), "stck_lwpr": "99999"}
        client, _ = self.hist([TOKEN, ok([row("091000"), broken_foreign])])
        assert len(client.candles("005930", window_end=self.at("0910"))) == 1

    def test_duplicate_minute_is_a_shape_error(self):
        """같은 분이 두 번 오면 실패한다 — 조용히 마지막 것을 고르지 않는다.

        당일 경로는 이 경우를 `select_window_candle` 이 INVALID 로 낸다. 소급 경로가
        분 단위 dict 로 접으면 그 가드가 **구조적으로 도달 불가**가 되고, 어느 값이
        canonical 에 실릴지를 벤더의 행 순서가 정한다.
        """
        client, _ = self.hist([TOKEN, ok([row("103000"), row("103000", volume="7")])])
        with pytest.raises(ValueError, match="유일성 위반"):
            client.candles("005930", window_end=self.at("1030"))

    def test_identical_repeat_of_a_minute_is_not_a_conflict(self):
        # 값이 같은 재등장은 페이지 겹침일 뿐 데이터 충돌이 아니다 — 막으면 창을
        # 겹쳐 주는 벤더에서 전 종목이 실패한다(같은 분 **다른 값**만이 충돌이다)
        client, _ = self.hist([
            TOKEN, ok([row("103000"), row("103000")]), ok([self.other_day("102900")]),
        ])
        assert len(client.candles("005930", window_end=self.at("1030"))) == 1

    def test_extended_hours_window_fails_loud(self):
        # 페이징이 09:00–15:30 고정이라 시간외 window 는 구조적으로 안 나온다.
        # ()(=missing) 로 돌려주면 세션이 수렴하지 않아 그날 EOD 파생이 통째로 안 돈다 —
        # 그리고 그건 종목 하나가 아니라 이 백필 전체의 배선 오류다
        client, _ = self.hist([TOKEN])
        with pytest.raises(KisSourceError, match="범위 밖"):
            client.candles("005930", window_end=datetime(2026, 8, 3, 18, 0, tzinfo=KST))

    def test_no_trade_minutes_are_synthesized_flat(self):
        """벤더가 빼먹은 분을 직전 종가 flat·거래량 0 으로 채운다.

        채우지 않으면 같은 시장 사실(무거래)이 당일 경로에선 `no_trade`(성공), 소급
        경로에선 `missing`(실패)이 된다 — 439870 은 390분 중 176행뿐이라 하루의 절반이
        실패로 쌓이고 그 window 는 영원히 INCOMPLETE 다.
        """
        client, _ = self.hist([
            TOKEN, ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        # ⚠️ 갭의 **모든** 분을 묻는다. 하나만 묻으면 끝 경계(`range` 상한)가 단언 표면
        # 밖이라, 갭마다 마지막 1분이 안 채워져도 초록이다 — 176행 종목이면 하루
        # ~175 window 가 그 한 분 때문에 INCOMPLETE 로 남는다.
        for hhmm in ("1028", "1029"):
            filled = client.candles("005930", window_end=self.at(hhmm))
            assert len(filled) == 1, hhmm
            candle = filled[0]
            assert candle.volume == Decimal(0)
            assert candle.traded is False
            # 직전가 flat — 10:27 봉의 **종가**여야 한다(시가·고가를 쓰면 조용히 다른 값)
            assert (candle.open, candle.high, candle.low, candle.close) == (
                Decimal("72500"), Decimal("72500"), Decimal("72500"), Decimal("72500")), hhmm
        # 관측된 봉은 그대로다 — 합성이 실측을 덮어쓰지 않는다
        assert client.candles("005930", window_end=self.at("1027"))[0].volume == Decimal("1200")

    def test_fill_does_not_reach_before_the_first_observation(self):
        # 첫 체결 앞은 그 종목의 그날 가격을 아직 모른다(직전가가 없다) — 채우면 없는
        # 값을 지어내는 것이고, missing 이 사실에 가장 가깝다
        client, _ = self.hist([
            TOKEN, ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        assert client.candles("005930", window_end=self.at("1026")) == ()

    def test_fill_reaches_the_session_close(self):
        """마지막 관측 뒤는 **채운다** — 마감된 하루에선 "그 위에 봉이 없다"가 관측이다.

        페이징이 15:30 부터 내려오므로 첫 페이지가 그걸 증명한다. 내부 갭과 인식론적으로
        같은 자리다. 안 채우면 그 window 들이 362종 중 한 종목 때문에 INCOMPLETE 로
        확정되고, `claim_due_window` 는 DUE·만료 CLAIMED 만 보므로 재청구도 안 돼 굳는다
        (08-03 실측: 3종이 15:15·15:17·15:19 에 끝나 합계 39분).
        """
        client, _ = self.hist([
            TOKEN, ok([row("152800"), row("152700")]), ok([self.other_day("152600")]),
        ])
        for hhmm in ("1529", "1530"):
            [candle] = client.candles("005930", window_end=self.at(hhmm))
            assert candle.volume == Decimal(0), hhmm
            assert candle.close == Decimal("72500"), hhmm

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

    def test_empty_page_is_not_a_finished_day(self):
        """빈 응답은 "하루가 끝났다"가 아니라 **아무 정보도 못 얻었다**이다.

        조기 종료하면 잘린 하루가 나오는데, 합성이 사이를 메우므로 그 결손은 4분류에서
        `no_trade` 로 위장돼 window 가 VALID 로 확정된다 — 못 받은 구간이 "거래가 없었다"
        가 된다. 빈 하루를 돌려주는 것도 같은 위장이라 실패로 나간다.

        (실패를 프로세스 안에서 어떻게 기억하는지는
        `test_failed_day_is_cached_so_it_does_not_reprice_every_window` 가 못박는다.)
        """
        client, _ = self.hist([TOKEN, *[ok([]) for _ in range(MAX_DAY_PAGES)]])
        with pytest.raises(KisUnitError, match="페이지 예산"):
            client.candles("005930", window_end=self.at("1530"))

    def test_reaching_the_session_open_ends_paging(self):
        """종료 증거는 둘이다 — 경계를 넘은 페이지, 그리고 **09:00 도달**.

        신규 테스트가 전부 경계 arm 으로만 끝나면 09:00 arm 은 무증거다. 벤더가 그 종목
        첫 봉 아래에서 이전 거래일을 안 섞어 주는 세계에서는 이 arm 이 **유일한** 종료
        경로라, 깨지면 362종 전건이 예산 소진 실패로 나간다.
        """
        client, fake = self.hist([TOKEN, ok([row("091000"), row("090000")])])
        assert len(client.candles("005930", window_end=self.at("0910"))) == 1
        assert len(fake.calls) == 2  # 토큰 1 + 페이지 1 — 09:00 을 봤으니 더 안 묻는다

    def test_failed_day_is_cached_so_it_does_not_reprice_every_window(self):
        """실패한 하루도 캐시한다 — 안 하면 window 마다 페이징을 처음부터 다시 태운다.

        못 받는 종목 하나가 390 window × 최대 8페이지 = 3,121콜을 산출 0으로 쓰고, 그
        예산은 앱키 전역이라 다른 KIS 스텝에서 빠진다. 재시도 경계는 window 재청구가
        아니라 프로세스 재기동이다(백필은 일회성 실행이라 같은 응답이 반복될 뿐이다).

        ⚠️ `KisUnitError` 자체는 여러 경로가 낸다 — **호출 수**를 같이 못박는다.
        캐시가 없으면 두 번째 window 가 페이지를 다시 받아 호출이 늘어난다.
        """
        client, fake = self.hist([TOKEN, *[ok([]) for _ in range(MAX_DAY_PAGES)]])
        with pytest.raises(KisUnitError):
            client.candles("005930", window_end=self.at("1530"))
        after_first = len(fake.calls)
        for hhmm in ("1529", "1528", "1527"):
            with pytest.raises(KisUnitError):
                client.candles("005930", window_end=self.at(hhmm))
        assert len(fake.calls) == after_first, "실패한 하루를 다시 받았다"

    # ⚠️ 문자열이되 형식만 다른 값(`"2026-08-03"`)은 못 잡는다 — 파싱 없이는 "남의 날"과
    # 구분되지 않는다. 여기서 막는 건 **형이 아예 다른** 경우다.
    @pytest.mark.parametrize("bad_date", [20260803, None, ["20260803"]])
    def test_non_string_trade_date_is_a_shape_error_not_another_day(self, bad_date):
        """거래일 필드의 형상 위반을 "남의 날"로 흘리면 **조용히 하루를 잃는다**.

        그 행이 빠지면 `len(same_day) < len(page)` 가 성립하고, 그건 경계를 넘었다는
        신호라 페이징이 끝나며, 빈 하루가 **성공으로** 캐시된다 — 예외도 ERROR 로그도
        없이 362종 전건 missing 인 window 390개가 커밋되고 하루가 "벤더가 안 줬다"로
        남는다. 원장이 관대해지는 쪽으로 틀리는 바로 그 방향이다.
        """
        client, _ = self.hist([TOKEN, ok([{**row("1030" + "00"), "stck_bsop_date": bad_date}])])
        with pytest.raises(ValueError, match="거래일 형상이 아니다"):
            client.candles("005930", window_end=self.at("1030"))

    # 짧은 쪽만 재면 `!=` 를 `<` 로 바꿔도 안 갈린다(변이로 확인) — 초과 길이도 같이 잰다.
    @pytest.mark.parametrize("bad_length", ["2026083", "202608031"])
    def test_trade_date_length_drift_does_not_eat_the_time_label(self, bad_length):
        """자리수도 **여기서** 봐야 한다 — 파서의 가드는 이 경로에 영영 안 닿는다.

        아래 날짜 필터는 8자리 `_ymd` 와의 정확 일치라 짧은 날짜는 구조적으로 "남의 날"이
        되어 `continue` 로 빠진다. 그러면 `len(same_day) < len(page)` 가 성립해 "경계를
        넘었다 = 하루가 끝났다"로 읽히고, **절단된 하루가 성공으로 캐시된다** — 형이 맞는
        형상 위반이라 위 가드에도 안 걸린다. 형제 `kis_sector_index` 와 같은 조건이다.
        """
        client, _ = self.hist(
            [TOKEN, ok([{**row("103000"), "stck_bsop_date": bad_length}])])
        with pytest.raises(ValueError, match="거래일 형상이 아니다"):
            client.candles("005930", window_end=self.at("1030"))

    def test_transport_failure_is_not_cached(self):
        """전송·유량 실패는 **캐시하지 않는다** — 다음 window 가 다시 받아야 한다.

        EGW00201 소진·5xx·JSON 손상·토큰 만료는 전부 재시도로 풀리는 축이다. 캐시하면
        15:40 배치와 겹친 몇 초가 그 종목의 **하루 전체**를 죽이고, 되돌릴 길이 없다 —
        INCOMPLETE 로 커밋된 window 는 재청구 대상(DUE·만료 CLAIMED)이 아니고 재계획도
        `DO NOTHING` 이라 재기동해도 구멍이 그대로다.

        ⚠️ 두 번째 호출이 **성공하는 것**으로 못박는다. 예외 타입만 보면 캐시된 값을
        되던지는 것과 구분되지 않는다.
        """
        client, _ = self.hist([
            TOKEN, err("40580000", "일시 거절"),
            ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        with pytest.raises(KisUnitError):
            client.candles("005930", window_end=self.at("1030"))
        assert len(client.candles("005930", window_end=self.at("1030"))) == 1

    def test_envelope_shape_error_is_transient_not_cached(self):
        """봉투 수준 형상 위반은 **전송 사고 축**이다 — 캐시하면 안 된다.

        `output2 이상`·`응답이 객체가 아님` 은 잘린 본문·프록시 오류 페이지에서 나는데,
        깨진 JSON 은 이미 `KisUnitError` 로 갈린다. 이 둘만 `ValueError` 로 남아 있어
        하루 캐시가 행 형상 위반과 구분하지 못하면, 몇 초의 전송 사고가 그 종목의
        하루 전체를 죽이고 되돌릴 길이 없다(INCOMPLETE 는 재청구 대상이 아니다).

        ⚠️ 두 번째 호출이 **성공하는 것**으로 못박는다 — 예외 타입만 보면 캐시된 값을
        되던지는 것과 구분되지 않는다.
        """
        client, _ = self.hist([
            TOKEN, json.dumps({"rt_cd": "0", "output2": {}}),
            ok([row("103000"), row("102700")]), ok([self.other_day("102600")]),
        ])
        with pytest.raises(KisUnitError, match="봉투 이상"):
            client.candles("005930", window_end=self.at("1030"))
        assert len(client.candles("005930", window_end=self.at("1030"))) == 1

    def test_bar_off_the_minute_grid_is_a_shape_error(self):
        """분 격자를 벗어난 봉 하나가 그 뒤 합성 전부를 밀어 하루를 조용히 죽인다.

        09:03:30 봉이 하나 오면 이후 합성이 통째로 :30 오프셋으로 생성돼 계획된 390
        window 중 **388개**가 키 불일치로 `missing` 이 된다(적중은 격자 밖 봉
        앞의 09:01·09:02 둘뿐. 그 봉이 유일한 관측이면 390개 전부다) — 예외도 ERROR 로그도 없이.
        """
        client, _ = self.hist([TOKEN, ok([row("090100"), {**row(), "stck_cntg_hour": "090330"}])])
        with pytest.raises(ValueError, match="분 격자 밖"):
            client.candles("005930", window_end=self.at("0901"))

    def test_cached_failure_is_reraised_fresh_not_the_same_object(self):
        # 저장한 예외 객체를 다시 raise 하면 traceback 이 raise 마다 2프레임씩 자란다 —
        # `_candle_for` 가 ValueError 를 logger.exception 으로 찍으므로 390 window 면
        # 종목 하나가 로그를 15만 줄 쓴다
        client, _ = self.hist([TOKEN, ok([row("103000"), row("103000", volume="7")])])
        depths = []
        for _ in range(3):
            with pytest.raises(ValueError) as caught:
                client.candles("005930", window_end=self.at("1030"))
            depth = 0
            tb = caught.value.__traceback__
            while tb is not None:
                depth += 1
                tb = tb.tb_next
            depths.append(depth)
        # 첫 raise 는 `_fetch_day` 안에서 나 한 프레임 깊다 — 그 뒤가 **자라지 않는 것**이
        # 계약이다(객체를 되던지면 raise 마다 2프레임씩 늘어난다)
        assert len(set(depths[1:])) == 1, f"traceback 이 자란다: {depths}"
        assert depths[-1] <= depths[0], depths

    def test_repeated_page_is_not_a_finished_day(self):
        # 같은 창을 반복하는 벤더도 "하루를 끝냈다는 증거"가 없다 — 조용히 잘린 하루를
        # 돌려주지 않고 예산까지 가서 실패한다(관대한 조기 종료를 두지 않는 이유)
        client, _ = self.hist([TOKEN, *[ok([row("153000")]) for _ in range(MAX_DAY_PAGES)]])
        with pytest.raises(KisUnitError, match="페이지 예산"):
            client.candles("005930", window_end=self.at("1530"))
