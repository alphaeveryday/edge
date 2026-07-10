"""normalize_price 스텝 테스트 — 벤더 이형 흡수 + 정합성 게이트 + quality_log(ALPHA-133)."""

import json

from data_pipeline.lake import LocalStorage
from data_pipeline.steps import normalize_price


def _raw_key(source: str, market: str, run_id: str = "R1", date: str = "2026-07-01") -> str:
    return (
        f"raw/source={source}/dataset=price_daily/market={market}"
        f"/ingest_date={date}/run_id={run_id}/part-00000.ndjson"
    )


def _write_raw(storage, key: str, rows: list[dict]) -> None:
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    storage.put_bytes(key, body.encode("utf-8"))


def _fmp_row(**over) -> dict:
    row = {"date": "2026-07-01", "open": 9.0, "high": 11.0, "low": 8.5, "close": 10.0,
           "volume": 100, "adjClose": 10.0, "our_ticker": "AAPL", "market": "US",
           "fmp_symbol": "AAPL", "fetched_at": "2026-07-01T00:00:00+00:00"}
    row.update(over)
    return row


def _kis_row(**over) -> dict:
    # KIS 원본은 전부 문자열, 날짜는 YYYYMMDD.
    row = {"stck_bsop_date": "20260701", "stck_oprc": "9", "stck_hgpr": "11",
           "stck_lwpr": "8", "stck_clpr": "10", "acml_vol": "100",
           "our_ticker": "005930", "market": "KR", "kis_symbol": "005930",
           "fetched_at": "2026-07-01T00:00:00+00:00"}
    row.update(over)
    return row


def _quality_log(storage) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_both_vendors_normalize_and_pass(tmp_path):
    # WHY: 정제의 존재 이유는 FMP(숫자)·KIS(문자열·YYYYMMDD) 이형을 하나의 표준 봉으로
    #      수렴시키는 것 — 둘 다 정상 봉이면 통과로 집계돼야 다운스트림이 동형으로 읽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row()])

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert (log["records_read"], log["records_passed"], log["records_failed"]) == (2, 2, 0)


def test_ohlcv_violation_isolated_and_logged(tmp_path):
    # WHY: high<low 인 봉은 탈락시키되(잘못된 가격 차단), 같은 파티션의 정상 봉은 통과해야
    #      한다(격리≠은폐) — 그리고 탈락은 사유와 함께 quality_log 에 남아야 추적된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"),
               [_fmp_row(our_ticker="AAPL"), _fmp_row(our_ticker="BAD", high=7.0, low=9.0)])

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 1
    failure = log["failures"][0]
    assert failure["ticker"] == "BAD" and "high_lt_low" in failure["reasons"]


def test_missing_and_non_numeric_reported(tmp_path):
    # WHY: 결측 필드와 비수치(스키마 드리프트)는 서로 다른 소스 문제다 — 사유를 구분해
    #      남겨야(Rule 12) 운영이 어느 쪽인지 안다. 물리 게이트 이전 단계에서 잡힌다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row(close=None)])           # 결측
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row(stck_clpr="비수치")])   # 비수치

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    reasons = {r for f in log["failures"] for r in f["reasons"]}
    assert "missing_field" in reasons and "non_numeric" in reasons
    assert log["records_passed"] == 0


def test_nan_inf_bool_prices_rejected_not_silently_passed(tmp_path):
    # WHY: json.loads 는 NaN/Infinity 리터럴을 float 로 파싱하고 NaN 비교는 전부 False 라,
    #      막지 않으면 잘못된 봉이 게이트를 '정상'으로 통과한다 — 이 스토리가 막으려는 바로
    #      그 오염이다. bool 도 float(True)=1.0 로 조용히 통과하는 스키마 드리프트다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    # NaN/Infinity 는 벤더 JSON 에 올 수 있는 리터럴 — allow_nan=True 로 그 상황을 재현.
    nan_line = json.dumps(_fmp_row(our_ticker="NANP", high=float("nan")), allow_nan=True)
    inf_line = json.dumps(_fmp_row(our_ticker="INFP", low=float("inf")), allow_nan=True)
    bool_line = json.dumps(_fmp_row(our_ticker="BOOLP", close=True))
    key = _raw_key("fmp", "US")
    storage.put_bytes(key, (nan_line + "\n" + inf_line + "\n" + bool_line + "\n").encode("utf-8"))

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 3
    assert all("non_numeric" in f["reasons"] for f in log["failures"])


def test_kis_malformed_calendar_date_rejected(tmp_path):
    # WHY: KIS 날짜는 8자리·숫자여도 실재 달력일이 아닐 수 있다('20260231'=2월 31일).
    #      validate_ohlcv 는 날짜를 안 보므로, 정규화가 strptime 으로 걸러야 존재하지 않는
    #      거래일이 records_passed 로 인증되지 않는다(Codex P2 회귀 방지).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row(stck_bsop_date="20260231")])

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 1
    assert "bad_trade_date" in log["failures"][0]["reasons"]


def test_unpadded_dates_rejected_both_vendors(tmp_path):
    # WHY: strptime 은 zero-pad 를 강제하지 않아 KIS '202671'·FMP '2026-7-1' 같은 포맷
    #      드리프트도 2026-07-01 로 파싱한다 — 왕복 검증으로 막지 않으면 비정규 포맷 행이
    #      records_passed 로 샌다(Codex P2 회귀 방지). 정상 pad 날짜는 통과해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row(stck_bsop_date="202671")])
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row(date="2026-7-1")])

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 2
    assert all("bad_trade_date" in f["reasons"] for f in log["failures"])


def test_non_object_row_isolated_not_crash(tmp_path):
    # WHY: 유효 JSON 이지만 객체가 아닌 행(null·배열)은 _normalize 의 raw.get 에서 런 전체를
    #      죽여 quality_log 조차 못 남긴다 — 행 단위로 격리해 나머지 검증은 완료돼야 한다
    #      (한 이상치 행이 검증 잡을 무너뜨리지 않게, Codex P2 회귀 방지).
    storage = LocalStorage(tmp_path / "lake")
    key = _raw_key("fmp", "US")
    body = "null\n" + json.dumps(_fmp_row()) + "\n[]\n"
    storage.put_bytes(key, body.encode("utf-8"))

    assert normalize_price.run(storage, "N1") == 0  # 크래시 없이 완료
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 2
    assert all("non_object_row" in f["reasons"] for f in log["failures"])


def test_nonfinite_or_bool_adjclose_nulled_but_row_valid():
    # WHY: adj_close 는 정합성 게이트 대상이 아닌 참고 필드라 NaN/Inf/bool 이어도 행을
    #      탈락시키진 않지만(정상 봉 유실 방지), 못 쓰는 값을 그대로 싣지 않고 null 로
    #      정리해야 다운스트림이 NaN adj_close 를 읽지 않는다(Codex P2 회귀 방지).
    for bad in (float("nan"), float("inf"), True):
        row, reasons = normalize_price._normalize("fmp", _fmp_row(adjClose=bad))
        assert reasons == [], f"{bad!r} 이 행을 탈락시키면 안 된다"
        assert row["adj_close"] is None, f"{bad!r} adjClose 는 null 로 정리돼야 한다"
    # 정상 adjClose 는 그대로 보존.
    ok, _ = normalize_price._normalize("fmp", _fmp_row(adjClose=12.5))
    assert ok["adj_close"] == 12.5


def test_fractional_volume_rejected_not_truncated():
    # WHY: int() 는 -0.5 를 0 으로 잘라 음수 거래량을 게이트(volume<0) 앞에서 숨긴다 —
    #      소수 거래량은 정수 카운트가 아니므로(드리프트) non_numeric 으로 걸러야 물리적으로
    #      불가능한 거래량이 records_passed 로 새지 않는다(Codex P2 회귀 방지).
    for bad in (-0.5, 0.5, 100.5):
        _, reasons = normalize_price._normalize("fmp", _fmp_row(volume=bad))
        assert "non_numeric" in reasons, f"{bad!r} 는 non_numeric 이어야"
    _, kis_reasons = normalize_price._normalize("kis", _kis_row(acml_vol="-0.5"))
    assert "non_numeric" in kis_reasons
    # 정수값(100.0)은 통과하고, whole 음수(-3)는 게이트가 negative_volume 으로 잡는다.
    ok, ok_reasons = normalize_price._normalize("fmp", _fmp_row(volume=100.0))
    assert ok_reasons == [] and ok["volume"] == 100
    neg, neg_reasons = normalize_price._normalize("fmp", _fmp_row(volume=-3))
    assert neg_reasons == [] and neg["volume"] == -3  # 게이트 이전엔 통과, 게이트에서 탈락


def test_missing_identity_ticker_or_market_rejected():
    # WHY: (market,ticker,trade_date) 는 canonical 정체성 키다 — ticker·market 없는 행은
    #      키를 만들 수 없어 canonical 로 못 간다. validate_ohlcv 는 이 둘을 안 보므로,
    #      정규화가 missing_field 로 걸러야 정체성 없는 행이 passed 로 인증되지 않는다(Codex P2).
    no_ticker = _fmp_row()
    del no_ticker["our_ticker"]
    _, r1 = normalize_price._normalize("fmp", no_ticker)
    assert "missing_field" in r1

    no_market = _fmp_row()
    del no_market["market"]
    _, r2 = normalize_price._normalize("fmp", no_market)
    assert "missing_field" in r2

    # 공백만 있는 ticker 도 결측으로 본다('  ' 로 정체성을 위장 못 하게, NonBlankStr 관례).
    _, r3 = normalize_price._normalize("fmp", _fmp_row(our_ticker="   "))
    assert "missing_field" in r3


def test_unhashable_market_isolated_not_crash(tmp_path):
    # WHY: 배열/객체 같은 unhashable market 이 _CURRENCY.get(dict 키)에서 TypeError 로 런
    #      전체를 죽여 quality_log 조차 못 남기던 경로 — 통화는 문자열 market 일 때만 조회하고
    #      비문자열은 missing_field 로 격리해, 나머지 검증이 완료되고 로그가 남아야 한다
    #      (Codex P2 crash-before-gate 회귀 방지).
    storage = LocalStorage(tmp_path / "lake")
    good = json.dumps(_fmp_row())
    bad = json.dumps(_fmp_row(market=[]))  # unhashable market
    storage.put_bytes(_raw_key("fmp", "US"), (good + "\n" + bad + "\n").encode("utf-8"))

    assert normalize_price.run(storage, "N1") == 0  # 크래시 없이 완료
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 1
    assert "missing_field" in log["failures"][0]["reasons"]


def test_unsupported_market_rejected():
    # WHY: 정규화는 US/KR 만 지원한다 — 비어있진 않지만 미지원 market('ZZ')은 통화 계약을
    #      만들 수 없어(currency=None) 표준행이 불완전하다. present 라고 passed 로 인증하지
    #      않고 지원 집합으로 명시 검증한다(Codex P2 회귀 방지).
    _, reasons = normalize_price._normalize("fmp", _fmp_row(market="ZZ"))
    assert "unsupported_market" in reasons


def test_input_run_id_scopes_validation(tmp_path):
    # WHY: 특정 수집 런만 재검증할 수 있어야(멱등·부분 재실행) 전량 재스캔 없이 운영한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [_fmp_row()])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [_fmp_row(), _fmp_row()])

    assert normalize_price.run(storage, "N1", input_run_id="R2") == 0
    assert _quality_log(storage)["records_read"] == 2  # R1(1건) 제외, R2(2건)만


def test_kis_dateless_extra_row_fails_gracefully(tmp_path):
    # WHY: KIS 어댑터는 날짜 없는 이상치 행을 raw 로 보존한다 — 정제는 크래시 없이
    #      missing_field 로 탈락시키고 quality_log 로 드러내야 한다(조용한 드롭 금지).
    storage = LocalStorage(tmp_path / "lake")
    dateless = _kis_row()
    del dateless["stck_bsop_date"]
    _write_raw(storage, _raw_key("kis", "KR"), [dateless])

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert "missing_field" in log["failures"][0]["reasons"]
