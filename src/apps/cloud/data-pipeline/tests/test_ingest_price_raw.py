"""ingest_price_raw 스텝 테스트 — raw append(전부 보존, dedup 없음)·collection_log."""

import json

from data_pipeline.config import PriceSource, load_settings
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.fmp_price import FmpPriceSource
from data_pipeline.steps import ingest_price_raw

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[news.sources.fmp.symbol_map]
NVDA = "NVDA"
AAPL = "AAPL"
"005930" = "SSNLF"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[targets]
symbols = ["NVDA", "AAPL", "005930"]
"""

_MAP = {"NVDA": "NVDA", "AAPL": "AAPL", "005930": "SSNLF"}


class FakeClient:
    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = responses  # {fmp_symbol: [bars]}

    def get(self, url: str, *, accept: str = "application/json") -> str:
        symbol = url.split("symbol=")[1].split("&")[0]
        return json.dumps(self.responses.get(symbol, []))


def _bar(date: str, close: float = 10.0) -> dict:
    return {"date": date, "open": 9.0, "high": 11.0, "low": 8.5, "close": close, "volume": 100}


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _run(tmp_path, responses, api_key="k", run_id="20260703T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key=api_key, symbol_map=_MAP)
    source = FmpPriceSource(config, FakeClient(responses))
    code = ingest_price_raw.run(settings, storage, source, run_id)
    return code, storage


def test_saves_ingest_date_partition_and_log(tmp_path):
    # WHY: S004 AC1 — 수집분이 ingest_date 파티션 규약(market 별 1파일)대로 저장되고,
    #      실행 결과가 collection_log 로 남아야 운영에서 수집 여부를 확인할 수 있다.
    #      가격 raw 는 뉴스(published_date)와 달리 수집일(ingest_date)로 파티션한다.
    responses = {
        "NVDA": [_bar("2026-07-01"), _bar("2026-06-30")],  # US
        "SSNLF": [_bar("2026-07-01")],                      # KR
    }
    code, storage = _run(tmp_path, responses)

    assert code == 0
    keys = storage.list_keys("raw")
    # market 별 1파일, ingest_date 는 실행일(오늘) — 날짜 고정 대신 규약 구조로 검증.
    assert len(keys) == 2
    assert all(k.startswith("raw/source=fmp/dataset=price_daily/market=") for k in keys)
    assert all("/ingest_date=" in k and k.endswith("/part-00000.ndjson") for k in keys)
    us_key = next(k for k in keys if "market=US" in k)
    assert len(storage.get_bytes(us_key).decode("utf-8").strip().splitlines()) == 2  # 2 거래일

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "success"
    assert log["records_fetched"] == 3
    assert log["records_saved"] == 3


def test_raw_preserves_all_rows_including_repeated_trade_date(tmp_path):
    # WHY: raw 는 받은 행을 전부 보존한다(전부 append) — FMP 가 같은 거래일을 두 번
    #      줘도(이상치) 조용히 버리지 않는다. (market,ticker,trade_date) 정체성 판정·
    #      upsert 는 후속 canonical 소관이라 raw 단계에서 그 키로 dedup 하지 않는다.
    code, storage = _run(tmp_path, {"NVDA": [_bar("2026-07-01", 10.0), _bar("2026-07-01", 99.0)]})

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert len(lines) == 2  # 둘 다 보존(버리지 않음)
    assert {json.loads(line)["close"] for line in lines} == {10.0, 99.0}

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["records_saved"] == 2
    assert "records_skipped_duplicate" not in log  # dedup 개념 자체가 raw 에 없다


class _PartlyFailingClient(FakeClient):
    """지정한 심볼은 재시도 소진(RuntimeError), 나머지는 정상 응답."""

    def __init__(self, responses, failing):
        super().__init__(responses)
        self.failing = set(failing)

    def get(self, url, *, accept="application/json"):
        symbol = url.split("symbol=")[1].split("&")[0]
        if symbol in self.failing:
            raise RuntimeError("GET 재시도 소진")
        return super().get(url, accept=accept)


def _run_client(tmp_path, client, run_id="20260703T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k", symbol_map=_MAP)
    source = FmpPriceSource(config, client)
    return ingest_price_raw.run(settings, storage, source, run_id), storage


def test_all_symbols_failing_marks_run_error(tmp_path):
    # WHY: 심볼 격리로 남은 심볼은 계속 시도하되, 전 심볼이 실패해 0건 저장이면
    #      status=success 로 남기면 안 된다(조용한 성공 금지 — fail loud).
    code, storage = _run_client(tmp_path, _PartlyFailingClient({}, failing=["NVDA", "AAPL", "SSNLF"]))

    assert code == 1
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert log["records_failed_symbols"] == 3


def test_partial_failure_marks_run_partial(tmp_path):
    # WHY: 일부 심볼만 실패하면 저장분은 있으나 온전치 않다 — partial 로 드러내고
    #      비0 종료로 오케스트레이터에도 손실을 알린다.
    client = _PartlyFailingClient({"NVDA": [_bar("2026-07-01")]}, failing=["AAPL", "SSNLF"])
    code, storage = _run_client(tmp_path, client)

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "partial"
    assert log["records_saved"] == 1
    assert log["records_failed_symbols"] == 2


def test_raw_write_failure_still_writes_collection_log(tmp_path):
    # WHY: "결과는 항상 collection_log" 계약 — raw put_bytes 가 실패(IAM·네트워크)해도
    #      런 흔적이 사라지면 안 된다. status=error·exit 1 로 남고 로그는 남아야 한다.
    class RawFailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            if key.startswith("raw/"):
                raise OSError("S3 raw write denied")
            super().put_bytes(key, data)  # operations_archive 로그는 정상

    settings = _settings(tmp_path)
    storage = RawFailingStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k", symbol_map=_MAP)
    source = FmpPriceSource(config, FakeClient({"NVDA": [_bar("2026-07-01")]}))
    code = ingest_price_raw.run(settings, storage, source, "20260703T000000Z")

    assert code == 1
    [log_key] = storage.list_keys("operations_archive")
    log = json.loads(storage.get_bytes(log_key))
    assert log["status"] == "error"
    assert "denied" in log["error"]


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입(로컬 등)은 실패가 아니라 '명시적 skip' — 조용히 아무것도 안 하고
    #      성공처럼 보이면 안 되고, skip 사실이 로그로 남아야 한다(Rule 12).
    code, storage = _run(tmp_path, {}, api_key=None)

    assert code == 0
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"


def test_enabled_but_no_mapped_targets_marks_skipped(tmp_path):
    # WHY: 활성 소스(키 주입됨)인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑)
    #      수집이 사실상 불가능하다 — success(0건)로 위장하지 않고 skip 으로 드러낸다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k")  # 활성, 심볼맵 0
    source = FmpPriceSource(config, FakeClient({}))  # 매핑 대상 0
    code = ingest_price_raw.run(settings, storage, source, "20260703T000000Z")

    assert code == 0  # 잘못된 설정이지만 크래시는 아님 — 로그로 드러냄
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"
    assert log["reason"] == "no mapped targets"


def test_dateless_bar_is_preserved(tmp_path):
    # WHY: raw 는 하나도 못 버린다 — date 가 없는 봉도(파티션은 ingest_date 라 무관)
    #      수집일 파티션에 그대로 보존돼야 한다(품질 판정은 후속 canonical 소관).
    code, storage = _run(tmp_path, {"NVDA": [{"open": 1.0, "close": 2.0}]})  # date 없음

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    assert "/ingest_date=" in raw_key  # 수집일로 파티션됨(비어 있지 않음)
    assert len(storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()) == 1


def test_unexpected_failure_still_writes_log(tmp_path):
    # WHY: '결과는 항상 collection_log' 계약 — fetch 자체가 죽는 예기치 못한 실패도
    #      로그 없이 죽으면 운영에서 런이 있었는지조차 알 수 없다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k", symbol_map=_MAP)
    source = FmpPriceSource(config, FakeClient({}))
    source.fetch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))

    code = ingest_price_raw.run(settings, storage, source, "20260703T000000Z")

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert "boom" in log["error"]


def _write_holdings(storage, as_of: str, rows: list[tuple[str, str]]) -> None:
    """canonical KR holdings 스냅샷 픽스처 — (constituent_ticker, etf_id) 쌍."""
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_etf_holdings_partition

    schema = pa.schema([("etf_id", pa.string()), ("constituent_ticker", pa.string())])
    table = pa.Table.from_pylist(
        [{"etf_id": e, "constituent_ticker": c} for c, e in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    prefix = canonical_etf_holdings_partition("KR", as_of)
    storage.put_bytes(f"{prefix}/part-00000.parquet", buf.getvalue())


class _UniverseFakeSource:
    """universe_from_holdings 옵트인 소스 — 스텝이 넘긴 symbols 를 기록만 한다."""

    source_name = "kis"
    enabled = True
    universe_from_holdings = True
    fetch_failures: list = []
    planned_symbols = None

    def __init__(self):
        self.received: list[str] | None = None

    def fetch(self, symbols, from_date=None, to_date=None):
        self.received = list(symbols)
        return iter(())


def test_universe_derived_from_latest_holdings_snapshot(tmp_path):
    # WHY: 정적 targets/symbol_map 은 유니버스와 어긋난다(KODEX 구성종목 36개 중 2개만
    #      수집되던 원인 — proxy 커버리지 60%). 옵트인 소스(KIS)는 canonical KR holdings
    #      **최신 스냅샷**의 구성종목·ETF 티커가 수집 대상에 union 돼야 커버리지가
    #      holdings 를 따라간다(ALPHA-419). 더한 수는 로그로 드러난다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-14", [("111111", "091160")])  # 구 스냅샷 — 무시돼야 함
    _write_holdings(storage, "2026-07-15", [("042700", "091160"), ("000660", "091160")])
    source = _UniverseFakeSource()

    assert ingest_price_raw.run(settings, storage, source, "r1") == 0

    assert source.received is not None
    assert {"042700", "000660", "091160"} <= set(source.received)  # 구성종목 + ETF 자신
    assert "111111" not in source.received  # 최신 스냅샷만
    assert "NVDA" in source.received  # 기존 targets 는 유지(union)
    logs = [k for k in storage.list_keys("operations_archive/collection_logs/") if "kis" in k]
    log = json.loads(storage.get_bytes(logs[0]))
    assert log["symbols_from_holdings"] == 3  # 042700·000660·091160 전부 targets 밖


def test_universe_partial_snapshot_filled_from_older_partition(tmp_path):
    # WHY: 최신 스냅샷이 부분(일부 ETF 수집 실패)이면 그게 곧 max(as_of_date)가 되어, 못 받은
    #      ETF 의 구성종목이 다음 수집 유니버스에서 조용히 빠졌다(ALPHA-590 — 단일 ETF 소속
    #      종목 68%, KODEX200 하나=전체 53%). ETF 별 최신 파티션의 합집합이어야 "전량 실패가
    #      부분 성공보다 안전한" 역설이 사라진다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-24", [("005930", "069500"), ("111111", "091160")])
    _write_holdings(storage, "2026-07-27", [("042700", "091160")])  # 069500 이 빠진 부분 스냅샷
    source = _UniverseFakeSource()

    assert ingest_price_raw.run(settings, storage, source, "r1") == 0
    # 069500 구성종목은 직전 파티션(07-24)에서 채워진다 — 부분 스냅샷이 유니버스를 못 줄인다.
    assert {"005930", "069500", "042700", "091160"} <= set(source.received)
    # 같은 ETF(091160)는 최신 파티션이 이긴다 — 구 스냅샷 구성종목을 되살리지 않는다.
    assert "111111" not in source.received


def test_universe_drops_etf_removed_from_config(tmp_path):
    # WHY: ETF 목록의 정본은 config etf_map 이다(ALPHA-590). 파티션은 수집 결과라 폐지·제외된
    #      ETF 행이 남는데, 그걸 되살리면 유령 ETF 의 구성종목을 소급 상한만큼 계속 수집한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-27", [("042700", "091160"), ("999999", "069500")])
    rows = ingest_price_raw._latest_kr_holdings_rows(storage, frozenset({"091160"}))
    assert {r["etf_id"] for r in rows} == {"091160"}


def test_universe_ignores_malformed_partition_keys(tmp_path):
    # WHY: 최신→과거 순회는 "사전순 정렬 = 시간순" 전제 위에 있다 — 비정상 키가 정렬 상위를
    #      차지하면 소급 상한만 갉아먹어 정상 최신 파티션이 스캔에서 밀린다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "9999-99-99", [("111111", "069500")])  # 형태만 맞는 비달력일
    _write_holdings(storage, "99991231", [("222222", "069500")])  # 비정준형(3.11+ 파싱 허용)
    _write_holdings(storage, "2026-07-27", [("042700", "091160")])
    rows = ingest_price_raw._latest_kr_holdings_rows(storage)
    assert {r["etf_id"] for r in rows} == {"091160"}


def test_universe_empty_expected_means_no_etfs(tmp_path):
    # WHY: 빈 etf_map 은 "정본이 0종이라 말함"이고 krx_etf 섹션 부재(None)는 "정본 부재"다 —
    #      둘을 구분하지 못하면 폐지된 ETF 가 파티션 잔재로 되살아난다(라운드2 리뷰 지적).
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-27", [("042700", "091160")])
    assert ingest_price_raw._latest_kr_holdings_rows(storage, frozenset()) == []
    assert len(ingest_price_raw._latest_kr_holdings_rows(storage, None)) == 1


def test_universe_missing_expected_etf_surfaced(tmp_path, caplog):
    # WHY: config etf_map(정본)에 있는데 소급 상한 안 어느 파티션에도 없는 ETF 는 유니버스가
    #      그만큼 좁게 돈다는 뜻이다 — 조용히 넘기면 아무도 모른다(Rule 12).
    import logging

    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-27", [("042700", "091160")])
    with caplog.at_level(logging.WARNING):
        ingest_price_raw._latest_kr_holdings_rows(storage, frozenset({"091160", "069500"}))
    assert any("유니버스 결손" in r.message and "069500" in r.getMessage()
               for r in caplog.records)


def test_universe_includes_alphanumeric_short_codes(tmp_path):
    # WHY: KRX 가 번호를 소진해 신규 상장분 단축코드에는 문자가 섞인다(우리 ETF 38종 중 8종).
    #      숫자로만 거르면 그 8종이 유니버스에서 조용히 빠져 price_daily 에 영원히 안 들어오고,
    #      ETF 종가가 없으니 가격변동 트리거·설명의 대조축이 빈다(ALPHA-463).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-20", [
        ("000660", "0093A0"), ("0005G0", "0005G0"), ("가나다라마바", "0093A0")])
    source = _UniverseFakeSource()

    assert ingest_price_raw.run(settings, storage, source, "r1") == 0
    assert {"0093A0", "0005G0"} <= set(source.received)
    # 넓히되 새지 않는다 — 6자라고 다 KR 코드가 아니다(선두 숫자 + ASCII 영숫자).
    assert "가나다라마바" not in source.received


def test_universe_absent_holdings_keeps_targets_only(tmp_path):
    # WHY: 신규 레이크(holdings 미적재)에서 수집이 죽거나 대상이 비면 안 된다 —
    #      기존 targets 경로 그대로, 더한 수 0 이 로그로 남는다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    source = _UniverseFakeSource()

    assert ingest_price_raw.run(settings, storage, source, "r1") == 0
    assert source.received == sorted(["NVDA", "AAPL", "005930"])
    logs = [k for k in storage.list_keys("operations_archive/collection_logs/") if "kis" in k]
    assert json.loads(storage.get_bytes(logs[0]))["symbols_from_holdings"] == 0


def test_disabled_skip_survives_log_write_failure(tmp_path):
    # WHY: skip 도 collection_log 로 드러나는 것이 계약이다(Rule 12). 스토리지 장애로 그
    #      로그마저 못 남겼는데 exit 0 이면 스케줄러는 성공으로 보고, 감사 레코드가 사라진
    #      사실을 아무도 모른다. 5개 수집기가 이 처리를 3:2 로 달리해 통일한 자리다(ALPHA-451)
    #      — 되돌리면 그 분기가 되살아난다.
    class FailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            raise OSError("storage down")

    settings = _settings(tmp_path)
    storage = FailingStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key=None, symbol_map=_MAP)
    source = FmpPriceSource(config, FakeClient({}))

    assert ingest_price_raw.run(settings, storage, source, "20260703T000000Z") == 1


def _write_price_daily(storage, trade_date: str, tickers: list[str]) -> None:
    """canonical KR 일봉 파티션 픽스처 — 그날 존재하는 티커 집합만 세운다."""
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    table = pa.Table.from_pylist(
        [{"ticker": t, "close": 1.0} for t in tickers],
        schema=pa.schema([("ticker", pa.string()), ("close", pa.float64())]))
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(f"{canonical_price_daily_partition('KR', trade_date)}/part-00000.parquet",
                      buf.getvalue())


class _RecordingSource:
    """옵트인 소스 — fetch 호출을 (symbols, from, to) 로 전부 기록한다.

    실제 어댑터 계약을 그대로 흉내낸다: `fetch` 진입 시 `fetch_failures` 를 **리셋**하고
    `planned_symbols` 를 그 호출의 대상 수로 덮는다(kis_price.fetch L124-126). 이 두 리셋이
    2차 수집을 붙일 때 1차분을 지우는 자리라, 흉내내지 않으면 회귀를 못 잡는다.
    """

    source_name = "kis"
    enabled = True
    universe_from_holdings = True

    def __init__(self, failures_by_call: dict[int, list[dict]] | None = None,
                 planned_by_call: dict[int, int] | None = None):
        self.calls: list[tuple[list[str], str | None, str | None]] = []
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None
        self._failures = failures_by_call or {}
        self._planned = planned_by_call or {}

    def fetch(self, symbols, from_date=None, to_date=None):
        n = len(self.calls)
        self.calls.append((list(symbols), from_date, to_date))
        self.fetch_failures = list(self._failures.get(n, []))       # 진입 리셋
        self.planned_symbols = self._planned.get(n, len(symbols))   # 진입 덮어쓰기
        return iter(({"market": "KR", "our_ticker": s} for s in symbols))


def test_newcomer_gets_history_window_others_keep_incremental(tmp_path):
    # WHY: 유니버스는 holdings 파생이라 ETF 추가에 **즉시** 넓어지는데 수집 창은 5일이다 —
    #      그래서 새 종목은 최근 5일만 채워지고 그 이전 날짜에는 영영 안 들어온다(dev 레이크
    #      절벽 3회 · 결손 1,613셀, ALPHA-989). 유니버스가 넓어진 그 런이 이력을 메워야 한다.
    #      전 종목에 긴 창을 물리는 것은 답이 아니므로 **편입분만** 두 번째 창으로 간다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160"), ("000660", "091160")])
    _write_price_daily(storage, "2026-08-13", ["000660", "091160", "NVDA"])  # 042700 이 없다
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0

    assert len(source.calls) == 2
    (first_syms, first_from, _), (second_syms, second_from, second_to) = source.calls
    assert first_from is None and {"042700", "000660", "091160"} <= set(first_syms)
    assert second_syms == ["042700"]        # 편입분만 — 이미 이력이 있는 종목은 안 간다
    assert second_from == "2025-07-10"      # 2026-08-14 − 400일
    assert second_to == "2026-08-14"

    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["symbols_newcomer"] == 1
    assert log["newcomer_window_from"] == "2025-07-10"


def test_no_newcomer_means_no_second_fetch(tmp_path):
    # WHY: 편입은 드문 사건이다(5주에 3회). 평시 런의 수집량·소요가 이 변경으로 늘면
    #      15:40 시장 레인 전체가 매일 그 대가를 치른다 — 분기 자체가 안 돌아야 한다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["042700", "091160"])
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0

    assert len(source.calls) == 1
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["symbols_newcomer"] == 0
    assert log["newcomer_window_from"] is None


def test_second_fetch_does_not_erase_first_pass_failures(tmp_path):
    # WHY: 어댑터의 `fetch` 는 진입 때 `fetch_failures` 를 리셋한다. 2차 수집을 그냥 붙이면
    #      1차에서 죽은 심볼이 런 상태에서 **사라져** partial(exit 1)이어야 할 런이
    #      success(exit 0)로 마감된다 — 결손을 성공으로 위장하는 그 자리다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])  # 042700 편입
    source = _RecordingSource(failures_by_call={0: [{"symbol": "000660", "error": "boom"}]})

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 1

    assert len(source.calls) == 2
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["status"] == "partial"
    assert log["records_failed_symbols"] == 1
    assert log["failed_symbols"][0]["symbol"] == "000660"
    assert log["ops"]["failed_records"] == 1


def test_second_fetch_planned_count_does_not_flip_run_to_skipped(tmp_path):
    # WHY: "매핑 대상 0 = skip" 은 **런 전체**의 판정이고, 런의 대상 집합을 정하는 것은
    #      1차 수집이다. 2차의 `planned_symbols`(편입분만)를 그대로 읽으면 정상 수집한 런이
    #      대상 0으로 뒤집혀 skip 으로 위장된다 — 수집분이 있는데 skip 은 거짓이다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])
    source = _RecordingSource(planned_by_call={1: 0})  # 2차가 대상 0을 보고

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0

    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["status"] == "success"
    assert log["reason"] is None


def test_explicit_deep_backfill_window_skips_second_fetch(tmp_path):
    # WHY: 운영자가 이미 그만큼 깊은 `--from` 으로 도는 백필 런에서는 2차 수집이 같은 창을
    #      한 번 더 긁는 순수 낭비다(KIS 는 콜당 100건·0.5초 간격이라 413종이면 분 단위).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])
    source = _RecordingSource()

    assert ingest_price_raw.run(
        settings, storage, source, "r1", "2025-01-01", "2026-08-14") == 0

    assert len(source.calls) == 1  # 1차 창(2025-01-01)이 편입 창(2025-07-10)보다 깊다


def test_empty_canonical_price_declares_no_newcomers(tmp_path):
    # WHY: canonical 이 통째로 비면 전 종목이 '신규'라 판정이 뜻을 잃는다. 그때 긴 창을
    #      붙이면 새 레이크의 첫 런이 전 종목 400일 수집으로 부풀고, 그건 이 경로가 아니라
    #      명시적 `--from` 백필이 맡는 일이다(스텝이 백필 정책을 몰래 정하지 않는다).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0
    assert len(source.calls) == 1


def test_newcomer_scan_ignores_malformed_price_partition_keys(tmp_path):
    # WHY: '최신 파티션'은 사전순 max 로 고른다 — 비달력일 키가 상위를 차지하면 그 파티션의
    #      티커 집합을 기준으로 삼아 **정상 종목 전체가 편입으로 잡힌다**(413종 400일 수집).
    #      holdings 쪽이 같은 이유로 같은 판정을 이미 갖고 있다.
    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "9999-99-99", ["111111"])
    _write_price_daily(storage, "2026-08-13", ["042700", "091160"])

    newcomers, since, _ = ingest_price_raw._newcomers(
        storage, ["042700", "091160", "000660"], "2026-08-14")
    assert newcomers == ["000660"]
    assert since == "2025-07-10"


def test_unreadable_latest_partition_is_not_read_as_everyone_new(tmp_path, caplog):
    # WHY: 0과 부재는 대칭이 아니다. 최신 파티션이 있는데 티커를 하나도 못 읽는 것(정제가
    #      컬럼명을 바꿈·빈 parquet·전 행 null)을 "아무도 없다"로 읽으면 유니버스 **전체**가
    #      신규 편입이 되어 413종에 400일 창이 붙고(수집량 400배), 그 런이 success 로 끝나
    #      canonical 손상이 성공 뒤로 숨는다 — 판정을 포기하고 사유를 남겨야 한다(Rule 12).
    import io
    import logging
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(  # ticker 컬럼이 없는 파티션(스키마 드리프트)
        [{"symbol": "042700", "close": 1.0}],
        schema=pa.schema([("symbol", pa.string()), ("close", pa.float64())])), buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", buf.getvalue())

    with caplog.at_level(logging.ERROR):
        newcomers, _, reason = ingest_price_raw._newcomers(
            storage, ["042700", "000660"], "2026-08-14")
    assert newcomers == []  # 전 종목을 신규로 몰지 않는다
    assert reason.startswith("unreadable_latest_partition")
    assert any("티커를 못 읽은 행" in r.getMessage() for r in caplog.records)


def test_malformed_price_partition_key_is_surfaced(tmp_path, caplog):
    # WHY: 판정에서 빼는 것만으로는 부족하다 — canonical 에 비정상 파티션이 생겼다는 사실
    #      자체가 결함 신호인데 조용히 버리면 아무도 모른다. 3줄 위 holdings 스캔이 같은
    #      상황에서 이미 경고한다(Rule 11 — 같은 파일 안에서 관례가 갈리면 안 된다).
    import logging

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "9999-99-99", ["111111"])
    _write_price_daily(storage, "2026-08-13", ["042700"])

    with caplog.at_level(logging.WARNING):
        ingest_price_raw._newcomers(storage, ["042700"], "2026-08-14")
    assert any("비정상 trade_date 파티션 키 무시" in r.getMessage() and "9999-99-99" in r.getMessage()
               for r in caplog.records)


def test_partial_ticker_drift_also_abandons_the_judgment(tmp_path, caplog):
    # WHY: **부분** 드리프트가 진짜 함정이다. 파일 하나만 정상이면 `known` 이 비지 않아
    #      "티커를 하나도 못 읽었나" 식 가드는 그냥 통과하고, 손상 파일에 실려 있던 수백
    #      종목이 전부 신규 편입으로 잡혀 각자 400일 창을 받는다 — 게다가 런은 성공한다.
    #      정상 파티션은 티커당 한 행이라(_merge_partition 이 ticker 키로 병합) 못 읽는
    #      행은 0이어야 한다. 한 행만 못 읽어도 판정을 포기한다.
    import io
    import logging
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    prefix = canonical_price_daily_partition("KR", "2026-08-13")
    _write_price_daily(storage, "2026-08-13", ["091160"])          # 정상 파일
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(                            # 같은 파티션의 드리프트 파일
        [{"symbol": t, "close": 1.0} for t in ("042700", "000660")],
        schema=pa.schema([("symbol", pa.string()), ("close", pa.float64())])), buf)
    storage.put_bytes(f"{prefix}/part-00001.parquet", buf.getvalue())

    with caplog.at_level(logging.ERROR):
        newcomers, _, reason = ingest_price_raw._newcomers(
            storage, ["042700", "000660", "091160"], "2026-08-14")
    assert newcomers == []          # known={091160} 이라 안 걸렀으면 2종이 신규로 잡혔다
    assert reason == "unreadable_latest_partition(trade_date=2026-08-13,rows=2)"
    assert any("읽은 티커 1종" in r.getMessage() for r in caplog.records)


def test_skipped_newcomer_scan_is_distinguishable_in_the_ledger(tmp_path):
    # WHY: 판정을 건너뛴 런과 편입이 없던 런은 밖에서 같은 모양이다(symbols_newcomer=0).
    #      로그 한 줄은 CloudWatch 를 뒤져야 보이고, 원장을 읽는 소비자는 둘을 구분 못 한다
    #      — 손상이 '편입 없음'으로 위장된다(Rule 12). 사유를 collection_log 에 남긴다.
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(
        [{"symbol": "091160", "close": 1.0}],
        schema=pa.schema([("symbol", pa.string()), ("close", pa.float64())])), buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", buf.getvalue())
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0

    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["symbols_newcomer"] == 0
    assert log["newcomer_scan"].startswith("unreadable_latest_partition")  # '편입 없음'과 구분된다


def test_healthy_scan_records_ok_in_the_ledger(tmp_path):
    # WHY: 위 구분이 성립하려면 정상 런이 반드시 "ok"를 남겨야 한다 — 필드가 조건부로만
    #      존재하면 '없음'이 다시 두 가지 뜻(정상/구버전)을 갖는다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["042700", "091160"])
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["newcomer_scan"] == "ok"


def test_ticker_type_drift_is_unreadable_not_absent(tmp_path, caplog):
    # WHY: '읽혔다'를 truthy 로만 판정하면 **타입 드리프트를 못 잡는다**. ticker 가 int64 로
    #      바뀌면 known 은 정수 집합이 되어 문자열 유니버스와 하나도 안 겹치고, unreadable 은
    #      0 이라 앞선 가드도 통과한다 — 전 종목(413)에 400일 2차 fetch 가 붙어 KIS 쿼터를
    #      태운다. 유니버스와 같은 형태일 때만 읽힌 것으로 세야 한다.
    import io
    import logging
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(   # ticker 가 정수 — truthy 지만 유니버스와 남남
        [{"ticker": 42700, "close": 1.0}, {"ticker": 91160, "close": 2.0}],
        schema=pa.schema([("ticker", pa.int64()), ("close", pa.float64())])), buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", buf.getvalue())

    with caplog.at_level(logging.ERROR):
        newcomers, _, reason = ingest_price_raw._newcomers(
            storage, ["042700", "091160"], "2026-08-14")
    assert newcomers == []                       # 전 종목을 신규로 몰지 않는다
    assert reason == "unreadable_latest_partition(trade_date=2026-08-13,rows=2)"
    assert any("읽은 티커 0종" in r.getMessage() for r in caplog.records)


def test_padded_ticker_is_unreadable_too(tmp_path):
    # WHY: 공백이 붙은 티커는 truthy 한 str 이라 타입 검사만으론 통과하는데, 유니버스는
    #      `krx_short_code` 로 정규화된 값이라 매칭이 안 된다 — 그 종목이 조용히 편입으로
    #      잡힌다. 정준형이 아닌 값은 '있다'가 아니라 '못 읽었다'다.
    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-13", ["042700", " 091160"])

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["042700", "091160"], "2026-08-14")
    assert newcomers == []
    assert reason == "unreadable_latest_partition(trade_date=2026-08-13,rows=1)"


def test_newcomer_scan_field_exists_on_every_path(tmp_path):
    # WHY: 필드 부재가 '구버전 로그'·'비활성 런'·'스캔 전 실패' 셋을 한 모양으로 뭉개면
    #      원장 소비자는 손상을 정상과 구분할 수 없다. 조기 종료·비옵트인 경로에서도
    #      값이 있어야 그 구분이 성립한다(Rule 12).
    settings = _settings(tmp_path)

    # ① 비활성 소스 — 스캔에 도달하지 못했다
    storage = LocalStorage(tmp_path / "lake1")
    config = PriceSource(base_url=settings.price.source.base_url, api_key=None, symbol_map=_MAP)
    assert ingest_price_raw.run(
        settings, storage, FmpPriceSource(config, FakeClient({})), "r1") == 0
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["newcomer_scan"] == "not_reached"

    # ② holdings 파생을 안 쓰는 소스(FMP) — 판정 자체가 없는 런
    storage = LocalStorage(tmp_path / "lake2")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k", symbol_map=_MAP)
    assert ingest_price_raw.run(
        settings, storage, FmpPriceSource(config, FakeClient({"NVDA": [_bar("2026-08-14")]})),
        "r2") == 0
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["newcomer_scan"] == "not_applicable"


def test_deep_primary_window_is_distinguishable_from_no_newcomer(tmp_path):
    # WHY: 명시 --from 백필은 편입이 **있어도** 2차를 안 돈다. 그 런과 '편입이 없던 런'이
    #      원장에서 같은 모양(symbols_newcomer=0)이면, 백필이 편입분을 덮었는지 아니면
    #      애초에 없었는지 사후에 못 가린다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])  # 042700 편입
    source = _RecordingSource()

    assert ingest_price_raw.run(
        settings, storage, source, "r1", "2025-01-01", "2026-08-14") == 0
    assert len(source.calls) == 1
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["newcomer_scan"] == "covered_by_primary_window"
    assert log["symbols_newcomer"] == 0
