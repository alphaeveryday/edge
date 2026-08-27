"""normalize_price 스텝 테스트 — 벤더 이형 흡수 + 정합성 게이트 + quality_log(ALPHA-133)."""

import hashlib
import json

from data_pipeline.lake import (
    LocalStorage,
    canonical_price_daily_partition,
    canonical_run_manifest_key,
)
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


def _quality_log(storage, run_id: str | None = None) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    if run_id is not None:
        keys = [key for key in keys if f"/run_id={run_id}/" in key]
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _manifest(storage, run_id: str) -> dict:
    key = canonical_run_manifest_key("price_daily", run_id)
    return json.loads(storage.get_bytes(key).decode("utf-8"))


class _FailingStorage:
    def __init__(self, inner, *, fail_put: str | None = None, fail_get: str | None = None,
                 fail_list: str | None = None):
        self.inner = inner
        self.fail_put = fail_put
        self.fail_get = fail_get
        self.fail_list = fail_list

    def list_keys(self, prefix):
        if self.fail_list and self.fail_list in prefix:
            raise OSError("의도된 목록 조회 실패")
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        if self.fail_get and self.fail_get in key:
            raise OSError("의도된 읽기 실패")
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        if self.fail_put and self.fail_put in key:
            raise OSError("의도된 쓰기 실패")
        return self.inner.put_bytes(key, data)


def _canonical_rows(storage, market: str, trade_date: str) -> list[dict]:
    from data_pipeline.lake import canonical_price_daily_partition

    prefix = canonical_price_daily_partition(market, trade_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_price._read_parquet_rows(storage.get_bytes(key)))
    return rows


def test_both_vendors_normalize_and_pass(tmp_path):
    # WHY: 정제의 존재 이유는 FMP(숫자)·KIS(문자열·YYYYMMDD) 이형을 하나의 표준 봉으로
    #      수렴시키는 것 — 둘 다 정상 봉이면 통과로 집계돼야 다운스트림이 동형으로 읽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row()])

    assert normalize_price.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert (log["records_read"], log["records_passed"], log["records_failed"]) == (2, 2, 0)


def test_yahoo_normalizes_but_unknown_vendor_still_fails(tmp_path):
    # WHY: 지수 시계열(로컬 전용 yahoo)은 raw 형태가 FMP 와 같아 같은 경로로 정제돼 canonical
    #      에 들어가야 한다 — 벤더 허용목록에서 빠지면 수집은 success 인데 정제가 전량
    #      unsupported_vendor 로 떨어져 분석엔진이 읽을 지수가 영영 안 생긴다(실측 회귀).
    #      동시에 정말 모르는 벤더는 여전히 사유로 걸러져야 한다(조용한 통과 금지, Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("yahoo", "KR"),
               [_fmp_row(our_ticker="KS11", market="KR", yahoo_symbol="^KS11")])
    _write_raw(storage, _raw_key("mystery", "KR"), [_fmp_row(our_ticker="X", market="KR")])

    assert normalize_price.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert (log["records_passed"], log["records_failed"]) == (1, 1)
    assert log["failures"][0]["reasons"] == ["unsupported_vendor"]
    assert [r["ticker"] for r in _canonical_rows(storage, "KR", "2026-07-01")] == ["KS11"]



def test_passing_rows_written_to_canonical(tmp_path):
    # WHY: 정제의 목적은 검증된 표준 봉을 canonical 로 넘기는 것 — 통과 행이 표준 스키마
    #      (market,ticker,trade_date,OHLCV,currency)로 canonical 에 적재돼야 다운스트림이 읽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row()])

    assert normalize_price.run(storage, "N1") == 0
    us = _canonical_rows(storage, "US", "2026-07-01")
    kr = _canonical_rows(storage, "KR", "2026-07-01")
    assert len(us) == 1 and us[0]["ticker"] == "AAPL" and us[0]["currency"] == "USD"
    assert len(kr) == 1 and kr[0]["ticker"] == "005930" and kr[0]["currency"] == "KRW"
    log = _quality_log(storage)
    assert log["canonical_rows_written"] == 2 and log["canonical_partitions_written"] == 2


def test_canonical_idempotent_across_runs(tmp_path):
    # WHY: canonical 은 run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가 같아야 한다 —
    #      두 번 돌려도 파티션은 part-00000 하나, 내용 동일(멱등 재실행이 데이터를 늘리지 않게).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])

    assert normalize_price.run(storage, "N1") == 0
    first = _canonical_rows(storage, "US", "2026-07-01")
    assert normalize_price.run(storage, "N2") == 0
    second = _canonical_rows(storage, "US", "2026-07-01")
    assert first == second
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1  # part 누적 없이 되쓰기


def test_same_vendor_latest_fetched_at_wins(tmp_path):
    # WHY: 같은 (market,ticker,trade_date) 를 같은 벤더가 재적재(정정)하면 최신 수집분이
    #      canonical 을 대표해야 한다 — 오래된 스냅샷이 최신 정정을 덮지 않게.
    storage = LocalStorage(tmp_path / "lake")
    # 두 봉 다 물리적으로 유효(close 가 high=11·low=8.5 안)해야 게이트를 통과 — 정정만 차이.
    old = _fmp_row(close=10.0, fetched_at="2026-07-01T00:00:00+00:00")
    new = _fmp_row(close=10.5, fetched_at="2026-07-02T00:00:00+00:00")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [old])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [new])

    assert normalize_price.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "US", "2026-07-01")
    assert len(rows) == 1 and rows[0]["close"] == 10.5  # 최신 fetched_at 승리


def test_cross_run_correction_updates_canonical(tmp_path):
    # WHY: 뒤 실행이 canonical 로 읽은 기존 행 vs 새 raw 를 병합하는 경로(멱등의 핵심) —
    #      같은 벤더의 정정 raw 가 더 최신 fetched_at 이면, 앞 실행이 이미 적재한 canonical
    #      행을 갱신해야 한다(오래된 값이 남지 않게). 이 경로는 단일 실행 테스트로는 안 탄다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"),
               [_fmp_row(close=10.0, fetched_at="2026-07-01T00:00:00+00:00")])
    assert normalize_price.run(storage, "N1") == 0
    assert _canonical_rows(storage, "US", "2026-07-01")[0]["close"] == 10.0

    # 정정 raw 를 새 수집 런으로 추가하고 다시 정제 — canonical 이 최신으로 갱신돼야.
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"),
               [_fmp_row(close=10.5, fetched_at="2026-07-02T00:00:00+00:00")])
    assert normalize_price.run(storage, "N2") == 0
    rows = _canonical_rows(storage, "US", "2026-07-01")
    assert len(rows) == 1 and rows[0]["close"] == 10.5  # 기존 canonical 위에 정정 반영


def test_cross_vendor_collision_fail_loud(tmp_path):
    # WHY: 같은 정체성 키가 서로 다른 벤더에서 오면 조용히 하나 고르는 건 USD 를 KRW 로
    #      태깅하는 통화 오염이다 — 둘 다 canonical 에서 빼고 fail-loud(비0 종료 + quality_log
    #      의 vendor_collisions)로 드러내야 한다(§6b, Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    # 같은 market=KR·ticker·trade_date 를 fmp·kis 두 벤더가 낸 상황을 강제.
    _write_raw(storage, _raw_key("fmp", "KR"), [_fmp_row(our_ticker="005930", market="KR")])
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row(our_ticker="005930")])

    assert normalize_price.run(storage, "N1") == 2  # 성공 범위 보존 부분 실패
    assert _canonical_rows(storage, "KR", "2026-07-01") == []  # 충돌 키 제외
    log = _quality_log(storage)
    assert len(log["vendor_collisions"]) == 1
    assert log["vendor_collisions"][0]["vendors"] == ["fmp", "kis"]


def test_failed_rows_excluded_from_canonical(tmp_path):
    # WHY: 게이트 탈락 행(high<low)은 canonical 에 들어가면 안 된다 — 잘못된 가격 차단이
    #      이 스토리의 핵심. 통과 행만 적재되고 탈락 행은 quality_log 에만 남는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"),
               [_fmp_row(our_ticker="OK"), _fmp_row(our_ticker="BAD", high=7.0, low=9.0)])

    assert normalize_price.run(storage, "N1") == 2
    rows = _canonical_rows(storage, "US", "2026-07-01")
    assert [r["ticker"] for r in rows] == ["OK"]


def test_ohlcv_violation_isolated_and_logged(tmp_path):
    # WHY: high<low 인 봉은 탈락시키되(잘못된 가격 차단), 같은 파티션의 정상 봉은 통과해야
    #      한다(격리≠은폐) — 그리고 탈락은 사유와 함께 quality_log 에 남아야 추적된다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"),
               [_fmp_row(our_ticker="AAPL"), _fmp_row(our_ticker="BAD", high=7.0, low=9.0)])

    assert normalize_price.run(storage, "N1") == 2
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

    assert normalize_price.run(storage, "N1") == 2
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

    assert normalize_price.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 3
    assert all("non_numeric" in f["reasons"] for f in log["failures"])


def test_kis_malformed_calendar_date_rejected(tmp_path):
    # WHY: KIS 날짜는 8자리·숫자여도 실재 달력일이 아닐 수 있다('20260231'=2월 31일).
    #      validate_ohlcv 는 날짜를 안 보므로, 정규화가 strptime 으로 걸러야 존재하지 않는
    #      거래일이 records_passed 로 인증되지 않는다(Codex P2 회귀 방지).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("kis", "KR"), [_kis_row(stck_bsop_date="20260231")])

    assert normalize_price.run(storage, "N1") == 2
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

    assert normalize_price.run(storage, "N1") == 2
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

    assert normalize_price.run(storage, "N1") == 2  # 성공 범위 보존 부분 실패
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

    assert normalize_price.run(storage, "N1") == 2  # 성공 범위 보존 부분 실패
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
    # WHY: SFN 이 --input-run-id 로 도는 경로다(ALPHA-389) — 그 런의 raw 만 읽어 정제 비용이
    #      여태 쌓인 raw 전체가 아니라 이번 런에 비례한다. 스코프도 canonical 을 쓴다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [_fmp_row()])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [_fmp_row(our_ticker="MSFT")])

    assert normalize_price.run(storage, "N1", input_run_id="R2") == 0
    log = _quality_log(storage)
    assert log["records_read"] == 1  # R1 은 스코프 밖 — 읽지도 않는다
    assert log["canonical_written"] is True
    assert [r["ticker"] for r in _canonical_rows(storage, "US", "2026-07-01")] == ["MSFT"]


def test_manifest_records_kr_us_current_winners_with_direct_key_and_sha(tmp_path):
    # WHY(ALPHA-1037): producer 범위는 변경분이 아니라 이번 run의 성공 winner 전체다. KR·US를
    #      모두 싣고 같은 값 재확정도 보존해야 PR04가 지원 시장만 선택할 수 있다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R0"), [_fmp_row()])
    assert normalize_price.run(storage, "N0", input_run_id="R0") == 0
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [
        _fmp_row(),
        _fmp_row(our_ticker="MSFT", close=10.0, fetched_at="2026-07-01T00:00:00+00:00"),
        _fmp_row(our_ticker="MSFT", close=10.5, fetched_at="2026-07-01T01:00:00+00:00"),
    ])
    _write_raw(storage, _raw_key("kis", "KR", run_id="R1"), [_kis_row()])

    assert normalize_price.run(storage, "N1", input_run_id="R1") == 0
    manifest = _manifest(storage, "N1")
    assert manifest["producer"] == "normalize_price"
    assert manifest["canonical_written"] is True
    assert [part["market"] for part in manifest["canonical_partitions"]] == ["KR", "US"]
    by_market = {part["market"]: part for part in manifest["canonical_partitions"]}
    assert by_market["KR"]["winner_ids"] == [{"ticker": "005930"}]
    assert by_market["US"]["winner_ids"] == [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
    for part in manifest["canonical_partitions"]:
        assert part["key"] == (
            f'{canonical_price_daily_partition(part["market"], part["trade_date"])}/part-00000.parquet'
        )
        assert part["sha256"] == hashlib.sha256(storage.get_bytes(part["key"])).hexdigest()
    msft = next(row for row in _canonical_rows(storage, "US", "2026-07-01")
                if row["ticker"] == "MSFT")
    assert msft["close"] == 10.5


def test_partial_row_failure_preserves_success_manifest_and_returns_2(tmp_path):
    # WHY(ALPHA-1037): 한 행 불량이 다른 성공 winner 계보를 폐기하면 loader가 처리할 수 없다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [
        _fmp_row(our_ticker="OK"),
        _fmp_row(our_ticker="BAD", high=7.0, low=9.0),
    ])

    assert normalize_price.run(storage, "N1", input_run_id="R1") == 2
    [part] = _manifest(storage, "N1")["canonical_partitions"]
    assert part["winner_ids"] == [{"ticker": "OK"}]
    assert _quality_log(storage)["ops"] == {"records_out": 1, "failed_records": 1}


def test_cross_vendor_collision_is_excluded_from_manifest_but_other_winner_survives(tmp_path):
    # WHY(ALPHA-1037): 교차 벤더 키를 manifest에 남기면 canonical에서 제외한 오염 후보를
    #      consumer가 성공 winner로 다시 처리한다. 충돌은 exit 2지만 같은 파티션 성공은 보존한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "KR", run_id="R0"), [
        _fmp_row(our_ticker="005930", market="KR"),
    ])
    assert normalize_price.run(storage, "N0", input_run_id="R0") == 0
    _write_raw(storage, _raw_key("kis", "KR", run_id="R1"), [
        _kis_row(our_ticker="005930"),
        _kis_row(our_ticker="000660"),
    ])

    assert normalize_price.run(storage, "N1", input_run_id="R1") == 2
    [part] = _manifest(storage, "N1")["canonical_partitions"]
    assert part["winner_ids"] == [{"ticker": "000660"}]
    assert [row["ticker"] for row in _canonical_rows(storage, "KR", "2026-07-01")] == [
        "000660"
    ]
    log = _quality_log(storage, "N1")
    assert log["ops"] == {"records_out": 1, "failed_records": 1}
    assert log["vendor_collisions"][0]["ticker"] == "005930"


def test_empty_input_writes_valid_empty_completed_manifest(tmp_path):
    # WHY(ALPHA-1037): 정상 0건과 manifest 결손을 구분해야 consumer가 풀스캔으로 넓히지 않는다.
    storage = LocalStorage(tmp_path / "lake")

    assert normalize_price.run(storage, "N1", input_run_id="empty") == 0
    assert _manifest(storage, "N1") == {
        "run_id": "N1",
        "producer": "normalize_price",
        "canonical_written": True,
        "canonical_partitions": [],
    }


def test_canonical_or_quality_failure_keeps_manifest_incomplete(tmp_path):
    # WHY(ALPHA-1037): canonical 또는 quality가 유실된 실행은 성공 winner 계보로 승인할 수 없다.
    for failing_prefix in ("canonical/market_data/price_daily", "data_quality_logs"):
        inner = LocalStorage(tmp_path / failing_prefix.replace("/", "_"))
        _write_raw(inner, _raw_key("fmp", "US"), [_fmp_row()])
        storage = _FailingStorage(inner, fail_put=failing_prefix)

        assert normalize_price.run(storage, "N1", input_run_id="R1") == 1
        assert _manifest(inner, "N1")["canonical_written"] is False


def test_manifest_integrity_failure_is_fatal_and_invalidates_marker(tmp_path):
    # WHY(ALPHA-1037): 완료 marker 바이트가 손상되면 consumer는 direct key·ID를 신뢰할 수 없다.
    class _CorruptCompletedManifestStorage(_FailingStorage):
        def get_bytes(self, key):
            data = self.inner.get_bytes(key)
            if "canonical_run_manifests" in key and json.loads(data)["canonical_written"] is True:
                return b"{}"
            return data

    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key("fmp", "US"), [_fmp_row()])

    assert normalize_price.run(
        _CorruptCompletedManifestStorage(inner), "N1", input_run_id="R1"
    ) == 1
    assert _manifest(inner, "N1")["canonical_written"] is False


def test_scoped_run_covers_all_vendors_of_a_run(tmp_path):
    # WHY: 스코프가 canonical 을 쓰게 되면서(ALPHA-389) 벤더 교차 충돌 감지는 **스코프가 그
    #      키를 쓰는 모든 벤더를 포함한다**는 전제 위에 선다. SFN 이 그 전제를 만족한다 — 9개
    #      raw 브랜치에 같은 run_id 를 넘기고 raw 키가 `source={vendor}/…/run_id={run_id}` 라
    #      --input-run-id 필터에 두 벤더가 다 걸린다. 이게 깨지면(벤더마다 run_id 를 나누면)
    #      한 벤더만 든 스코프가 충돌을 못 보고 통화 오염을 canonical 에 넣는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "KR", run_id="N1"), [_fmp_row(our_ticker="005930", market="KR")])
    _write_raw(storage, _raw_key("kis", "KR", run_id="N1"), [_kis_row(our_ticker="005930")])

    # 같은 런 스코프 → 두 벤더가 다 보인다 → 충돌 fail-loud(둘 다 제외).
    assert normalize_price.run(storage, "N2", input_run_id="N1") == 2
    assert _canonical_rows(storage, "KR", "2026-07-01") == []


def test_vendors_do_not_overlap_by_design(tmp_path):
    # WHY: 교차 충돌 fail-loud 가 지키는 상황(한 키에 두 벤더)은 **설계상 오지 않는다** —
    #      벤더가 서로 다른 종목을 받기 때문이다. 이 계약이 곧 ALPHA-389 가 price 를 스코프로
    #      돌릴 수 있는 근거다: 충돌이 없으면 '충돌로 비워진 키를 단일벤더 스코프가 되살린다'는
    #      하위 케이스도 선행 조건이 없어 발생하지 않는다.
    #      이 테스트가 깨지면(= 심볼맵이 겹치게 바뀌면) 스코프 정제의 전제가 무너진 것이니
    #      normalize_price 의 canonical 적재 주석을 다시 읽어라.
    #
    #      **한계**: 이건 이 프로세스가 로드한 설정을 볼 뿐이라, 커밋된 sources.toml 은
    #      지키지만 배포된 taskdef 의 env 오버라이드(DATA_PIPELINE_PRICE__SOURCE__SYMBOL_MAP)
    #      까지는 못 본다 — env > file 이므로 CI 초록인 채 prod 만 겹칠 수 있다. 그때도
    #      조용하진 않다: 런타임에 _merge_partition 이 충돌을 fail-loud 로 잡아 exit 2 이고
    #      SFN 이 실패 알림을 낸다. 잃는 건 '충돌 후 단일벤더 스코프 복구가 되살리는' 케이스뿐.
    from data_pipeline import load_settings

    settings = load_settings()
    fmp_tickers = set(settings.price.source.symbol_map)
    kis_tickers = set(settings.kis_price.source.symbol_map) if settings.kis_price else set()
    assert fmp_tickers, "FMP 가격 대상이 있어야 이 계약이 의미 있다"
    # KIS 대상은 이제 정적 맵이 아니라 holdings 파생 + 6자리 항등이다(ALPHA-419) — KIS 는
    # 6자리 KRX 코드만 질의하므로, 겹침은 FMP 가격 맵에 6자리 our_ticker 가 오를 때만
    # 성립한다. 그 키가 없음을 고정한다(+오버라이드 맵을 쓰는 경우의 직접 겹침도 차단).
    six_digit_fmp = {t for t in fmp_tickers if t.isdigit() and len(t) == 6}
    assert not six_digit_fmp, (
        f"FMP 가격 맵에 KR 6자리 our_ticker: {sorted(six_digit_fmp)} — KIS 와 교차 충돌이 가능해졌다"
    )
    assert not (fmp_tickers & kis_tickers), (
        f"벤더 심볼맵이 겹친다: {sorted(fmp_tickers & kis_tickers)} — 교차 충돌이 가능해졌다"
    )


def test_kis_dateless_extra_row_fails_gracefully(tmp_path):
    # WHY: KIS 어댑터는 날짜 없는 이상치 행을 raw 로 보존한다 — 정제는 크래시 없이
    #      missing_field 로 탈락시키고 quality_log 로 드러내야 한다(조용한 드롭 금지).
    storage = LocalStorage(tmp_path / "lake")
    dateless = _kis_row()
    del dateless["stck_bsop_date"]
    _write_raw(storage, _raw_key("kis", "KR"), [dateless])

    assert normalize_price.run(storage, "N1") == 2
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert "missing_field" in log["failures"][0]["reasons"]
