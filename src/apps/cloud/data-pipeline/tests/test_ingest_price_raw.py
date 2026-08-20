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
                 planned_by_call: dict[int, int] | None = None, emit: bool = True):
        self.calls: list[tuple[list[str], str | None, str | None]] = []
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None
        self._failures = failures_by_call or {}
        self._planned = planned_by_call or {}
        self._emit = emit

    def fetch(self, symbols, from_date=None, to_date=None):
        n = len(self.calls)
        self.calls.append((list(symbols), from_date, to_date))
        self.fetch_failures = list(self._failures.get(n, []))       # 진입 리셋
        self.planned_symbols = self._planned.get(n, len(symbols))   # 진입 덮어쓰기
        if not self._emit or n in getattr(self, "_emit_calls", set()):
            return iter(())   # 전 심볼 실패 = 저장분 0
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

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 0

    assert len(source.calls) == 2
    (first_syms, first_from, _), (second_syms, second_from, second_to) = source.calls
    assert first_from == "2026-08-09"                 # 나머지는 증분 창 그대로
    assert {"000660", "091160"} <= set(first_syms)
    assert "042700" not in first_syms      # 편입분은 증분 창에서 **빠진다**
    assert second_syms == ["042700"]       # 이력 창으로만 받는다
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

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 0

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

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1

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

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 0

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

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 0
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
    assert reason == "no_usable_partition(scanned=1)"
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
    assert reason == "no_usable_partition(scanned=1)"
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

    # 판정 근거를 못 찾은 런은 **조용히 성공하면 안 된다** — 같은 런의 1차 수집이 신규
    # 티커를 canonical 에 넣어 다음 런부터 '이미 있음'으로 보이므로, 놓친 편입 종목의
    # 이력은 영구 결손이 된다. 저장분은 있으니 error 가 아니라 partial 이다.
    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1

    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["status"] == "partial"
    assert log["symbols_newcomer"] == 0
    assert log["newcomer_scan"] == "no_usable_partition(scanned=1)"  # '편입 없음'과 구분된다
    assert log["ops"]["failed_records"] == 0   # 심볼 실패가 아니다 — 원장을 영구 INCOMPLETE 로 만들지 않는다


def test_healthy_scan_records_ok_in_the_ledger(tmp_path):
    # WHY: 위 구분이 성립하려면 정상 런이 반드시 "ok"를 남겨야 한다 — 필드가 조건부로만
    #      존재하면 '없음'이 다시 두 가지 뜻(정상/구버전)을 갖는다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["042700", "091160"])
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 0
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
    assert reason == "no_usable_partition(scanned=1)"
    assert any("읽은 티커 0종" in r.getMessage() for r in caplog.records)


def test_padded_ticker_is_tidied_not_discarded(tmp_path, caplog):
    # WHY: 가드가 **상류 게이트보다 엄격하면 그 차이가 곧 결함**이다.
    #      `normalize_price._blank` 는 strip 후 비지 않으면 canonical 로 보내므로
    #      ' 091160' 은 정상 통과한 행이다. 그걸 '못 읽음'으로 치면 정상 파티션을 통째로
    #      버리고, 그 값이 계속 있으면 편입 판정이 영영 멈춰 이력이 영구 결손된다.
    #      정체성은 정돈한 코드다(`parse.krx_short_code` 와 같은 축) — 정돈해 읽되
    #      드러낸다.
    import logging

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-13", ["042700", " 091160"])

    with caplog.at_level(logging.WARNING):
        newcomers, since, reason = ingest_price_raw._newcomers(
            storage, ["042700", "091160", "000660"], "2026-08-14")
    assert reason == ""                  # 파티션을 버리지 않는다
    assert newcomers == ["000660"]       # ' 091160' 이 091160 으로 읽혀 편입이 아니다
    assert since == "2025-07-10"
    assert any("공백이 붙은 ticker" in r.getMessage() for r in caplog.records)


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


def test_same_symbol_failing_in_both_fetches_counts_once(tmp_path):
    # WHY: `failed_symbols` 의 축은 **심볼**이다(records_failed_symbols·ops.failed_records).
    #      편입 종목이 1차(증분 창)와 2차(이력 창) 모두에서 죽으면 실패 심볼은 1종인데,
    #      두 목록을 그냥 이으면 2로 보고돼 원장이 실제보다 나쁜 상태를 가리킨다 —
    #      failed_records 는 원장 완전성 판정의 입력이라 부풀면 런이 영구 INCOMPLETE 다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])  # 042700 편입
    fail = [{"symbol": "042700", "our_ticker": "042700", "error": "boom"}]
    source = _RecordingSource(failures_by_call={0: fail, 1: fail})  # 같은 심볼이 두 번

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1

    assert len(source.calls) == 2
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["records_failed_symbols"] == 1      # 2종이 아니다
    assert log["ops"]["failed_records"] == 1
    assert log["status"] == "partial"


def test_real_failure_beats_truncation_for_the_same_symbol(tmp_path):
    # WHY: 중복 제거가 **절단을 남기면** 더 나쁘다. 절단(kind=truncation)은 성공으로 치는
    #      종류라(ALPHA-351 — 데이터는 유효하고 다음 창이 이어받는다), 실제 실패를 절단이
    #      덮으면 partial 이어야 할 런이 success·exit 0 으로 끝난다. 심각한 쪽이 이겨야 한다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])
    source = _RecordingSource(failures_by_call={
        0: [{"symbol": "042700", "our_ticker": "042700", "kind": "truncation", "error": "잘림"}],
        1: [{"symbol": "042700", "our_ticker": "042700", "error": "boom"}],
    })

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["records_failed_symbols"] == 1
    assert log["failed_symbols"][0].get("kind") != "truncation"  # 실제 실패가 남았다
    assert log["status"] == "partial"                            # 절단만 남았으면 success 였다


def test_scan_failure_is_not_reported_as_never_reached(tmp_path):
    # WHY: 스캔에 **못 간 런**과 **갔다가 죽은 런**은 진단이 다르다(전자는 비활성·설정,
    #      후자는 레이크 장애·비달력일 창). 둘 다 not_reached 로 남으면 원장만 보고는
    #      어느 쪽인지 못 가린다 — '모든 경로에 값이 있다'가 '값이 사실이다'는 아니다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    source = _RecordingSource()

    # 비달력일 창 끝 → _newcomers 안에서 date.fromisoformat 이 죽는다
    _write_price_daily(storage, "2026-08-13", ["091160"])
    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-13-99") == 1

    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["status"] == "error"
    assert log["newcomer_scan"] == "scan_failed"   # not_reached 가 아니다


def test_empty_latest_partition_falls_back_instead_of_abandoning(tmp_path, caplog):
    # WHY: **판정을 포기하면 원 결함이 되살아난다.** `normalize_price._write_canonical` 은
    #      병합 결과가 0행이어도 part-00000 을 쓴다(벤더 교차 충돌이 그날 키를 전부 지우면
    #      그렇다). 그 빈 파티션을 만난 런이 판정을 포기하면, 같은 런의 1차 수집이 신규
    #      티커의 최근 5일을 canonical 에 넣어 다음 런부터는 '이미 있음'으로 보인다 —
    #      400일 이력은 영영 안 붙는다(ALPHA-989 그 자체). 기준을 하루 물리는 대가는
    #      편입 후보가 조금 넓어지는 것뿐이고, 그건 과수집 = 안전한 방향이다.
    import io
    import logging
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-12", ["091160", "000660"])   # 쓸 수 있는 기준
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(                              # 최신인데 0행
        [], schema=pa.schema([("ticker", pa.string()), ("close", pa.float64())])), buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", buf.getvalue())

    with caplog.at_level(logging.WARNING):
        newcomers, since, reason = ingest_price_raw._newcomers(
            storage, ["091160", "000660", "042700"], "2026-08-14")
    assert reason == ""                 # 포기하지 않는다
    assert newcomers == ["042700"]      # 08-12 기준으로 정상 판정
    assert since == "2025-07-10"
    assert any("파티션이 비었다" in r.getMessage() for r in caplog.records)


def test_drifted_latest_partition_falls_back_to_clean_one(tmp_path):
    # WHY: 위와 같은 이유가 스키마 드리프트에도 적용된다 — 최신이 못 쓸 뿐이지 이전 기준이
    #      멀쩡하면 판정은 계속돼야 한다. 손상 파티션을 기준으로 삼지 않는 것과, 판정
    #      자체를 포기하는 것은 다른 일이다.
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-12", ["091160"])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(
        [{"symbol": "091160", "close": 1.0}],
        schema=pa.schema([("symbol", pa.string()), ("close", pa.float64())])), buf)
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", buf.getvalue())

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""
    assert newcomers == ["042700"]


def test_all_reference_partitions_unusable_abandons_with_reason(tmp_path):
    # WHY: 물러나기에도 바닥이 있어야 한다 — 상한 안 전부가 못 쓸 파티션이면 그때는 정말
    #      판정할 근거가 없다. 그 사실이 원장에 남아야 '편입 없음'과 구분된다.
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    empty = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(
        [], schema=pa.schema([("ticker", pa.string()), ("close", pa.float64())])), empty)
    for d in ("2026-08-11", "2026-08-12", "2026-08-13"):
        storage.put_bytes(
            f"{canonical_price_daily_partition('KR', d)}/part-00000.parquet", empty.getvalue())

    newcomers, _, reason = ingest_price_raw._newcomers(storage, ["042700"], "2026-08-14")
    assert newcomers == []
    assert reason == "no_usable_partition(scanned=3)"


def test_scan_incomplete_does_not_mask_symbol_failures(tmp_path):
    # WHY: partial 판정이 두 축(판정 불가·심볼 실패)에서 오는데, 한쪽이 먼저 status 를
    #      바꾸면 뒤 판정이 `status == "success"` 조건에 걸려 통째로 건너뛸 수 있다 —
    #      그러면 실패 심볼이 런 상태에 반영되지 않고 failed_records 도 안 오른다.
    #      두 사실은 서로를 가리지 않아야 한다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", [])  # 빈 파티션 → 판정 근거 없음
    source = _RecordingSource(failures_by_call={0: [{"symbol": "000660", "error": "boom"}]})

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["status"] == "partial"
    assert log["newcomer_scan"] == "no_usable_partition(scanned=1)"
    assert log["records_failed_symbols"] == 1     # 심볼 실패가 판정 불가에 가려지지 않는다
    assert log["ops"]["failed_records"] == 1


def test_scan_incomplete_must_not_soften_a_total_collection_failure(tmp_path):
    # WHY: partial 을 만드는 축이 둘이라(판정 불가·심볼 실패) **먼저 온 쪽이 뒤를 가린다.**
    #      판정 불가가 status 를 partial 로 올려 두면, 뒤의 실패 판정이 `status ==
    #      "success"` 조건에 걸려 통째로 건너뛴다 — 전 심볼이 죽어 저장분이 0인 런이
    #      error 가 아니라 partial 로 마감된다. 덜 심각하게 보고하는 것도 위장이다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", [])   # 판정 근거 없음 → scan_incomplete
    source = _RecordingSource(                       # 그리고 전 심볼 실패 → 저장분 0
        failures_by_call={0: [{"symbol": "042700", "error": "boom"}]}, emit=False)

    assert ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["records_saved"] == 0
    assert log["status"] == "error"        # partial 이면 실패가 판정 불가에 가려진 것이다
    assert "모든 수집 심볼 실패" in log["error"]


def test_corrupt_parquet_does_not_stop_the_days_collection(tmp_path):
    # WHY: 이 판정은 1차 `source.fetch` **앞**에 있다. 깨진 parquet 하나나 S3 일시 오류가
    #      예외로 올라가면 그날 가격 수집이 통째로 안 돈다 — 그리고 새 raw 가 안 생기니
    #      그 파티션이 계속 최신으로 남아 **매 런이 같은 자리에서 죽는다**(수동 복구 전까지
    #      영구 정지). 보조 판정이 본 수집을 죽이면 안 된다.
    from data_pipeline.lake import canonical_price_daily_partition

    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    storage.put_bytes(                                   # parquet 이 아닌 바이트
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", b"not-parquet")
    source = _RecordingSource()

    code = ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14")

    assert len(source.calls) == 1                        # 1차 수집이 **돌았다**
    assert storage.list_keys("raw") != []                # 그리고 raw 가 저장됐다
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["records_saved"] > 0
    assert log["newcomer_scan"] == "no_usable_partition(scanned=1)"
    assert log["status"] == "partial" and code == 1      # 판정 불가는 여전히 드러난다


def test_corrupt_latest_partition_falls_back_to_a_readable_one(tmp_path):
    # WHY: 깨진 파일은 '그 파티션을 못 씀'이지 '판정 불가'가 아니다 — 이전 파티션이 멀쩡하면
    #      편입 판정은 계속돼야 한다. 여기서 포기하면 그 런의 편입 종목 이력이 영구 결손된다.
    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-12", ["091160"])
    storage.put_bytes(
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00000.parquet", b"corrupt")

    newcomers, since, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""
    assert newcomers == ["042700"]
    assert since == "2025-07-10"


def test_malformed_window_end_is_not_swallowed(tmp_path):
    # WHY: 비달력일 창 끝은 **입력 오류**이고 1차 fetch 도 같은 값을 쓴다 — 읽기 실패처럼
    #      격리해 삼키면 창이 틀린 채로 수집이 돈다. 파티션을 하나도 못 읽는 상황에서도
    #      이 오류는 그대로 올라와야 한다(격리 대상이 아니다).
    import pytest

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-13", ["091160"])
    with pytest.raises(ValueError):
        ingest_price_raw._newcomers(storage, ["091160"], "2026-13-99")


def test_partition_with_one_corrupt_file_is_not_accepted_partially(tmp_path):
    # WHY: 파티션에 정상 파일과 깨진 파일이 섞이면 `known` 이 비지 않는다 — 그걸 그대로
    #      기준으로 삼으면 **깨진 파일에 실려 있던 종목이 통째로 신규 편입**으로 잡혀 각자
    #      400일 창을 받는다. 부분 스키마 드리프트와 같은 함정이 손상 경로로 재현되는 자리다.
    #      못 읽은 파일이 하나라도 있으면 그 파티션은 기준에서 뺀다.
    from data_pipeline.lake import canonical_price_daily_partition

    storage = LocalStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-12", ["091160", "042700"])       # 멀쩡한 이전 기준
    _write_price_daily(storage, "2026-08-13", ["091160"])                 # 최신의 정상 파일
    storage.put_bytes(                                                     # 같은 파티션의 깨진 파일
        f"{canonical_price_daily_partition('KR', '2026-08-13')}/part-00001.parquet", b"corrupt")

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""
    # 08-13 을 부분 뷰로 받아들였으면 042700 이 편입으로 잡혔다. 08-12 로 물러나야 0종이다.
    assert newcomers == []


def test_unbounded_primary_window_covers_newcomers(tmp_path):
    # WHY: 하한 없는 창(`--to` 만 준 백필)은 어댑터가 종목 이력 끝까지 페이지네이션하므로
    #      400일 이력 창보다 **깊다**. 그때 2차 수집은 순수 낭비다 — 'from_date 가 None 이면
    #      증분'이라고 읽으면 편입분을 두 번 받는다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])   # 042700 편입
    source = _RecordingSource()

    assert ingest_price_raw.run(settings, storage, source, "r1", None, "2026-08-14") == 0
    assert len(source.calls) == 1
    assert "042700" in source.calls[0][0]                   # 1차 창이 이미 덮는다
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["newcomer_scan"] == "covered_by_primary_window"


def test_failed_history_fetch_leaves_no_rows_so_it_retries(tmp_path):
    # WHY: **partial 로 보고했는데 결손이 영구가 되는** 자리다. 편입 종목을 증분 창에서도
    #      받으면 이력 fetch 가 실패해도 5일치는 남고, SFN 은 partial 런도 정제로 계속
    #      보내므로(statemachine.tf NotifyRawPartial) 그 5일치가 canonical 에 들어간다.
    #      그러면 다음 런의 존재 기반 판정이 '이미 있음'으로 보고 400일 이력은 영영
    #      재시도되지 않는다. 편입분을 증분 창에서 빼면 실패한 종목은 행이 하나도 안 남아
    #      계속 편입으로 잡힌다 — 새 상태 저장 없이 성공할 때까지 자격이 유지된다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    _write_price_daily(storage, "2026-08-13", ["091160"])
    source = _RecordingSource(                       # 2차(이력) 창에서 편입 종목이 죽는다
        failures_by_call={1: [{"symbol": "042700", "our_ticker": "042700", "error": "boom"}]})
    source._emit_calls = {1}                          # 그 창은 행을 안 낸다

    assert ingest_price_raw.run(
        settings, storage, source, "r1", "2026-08-09", "2026-08-14") == 1

    [raw_key] = storage.list_keys("raw")
    tickers = {json.loads(line)["our_ticker"]
               for line in storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()}
    assert "042700" not in tickers   # 실패한 편입분의 행이 **하나도 없다** → 다음 런이 다시 잡는다
    assert {"091160", "005930"} <= tickers   # 나머지는 증분 창으로 정상 수집됐다
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["status"] == "partial"


def test_partition_listing_failure_is_isolated_too(tmp_path):
    # WHY: 파일 읽기만 격리하면 절반이다 — 목록 조회(S3 ListObjects)도 실패한다. 그게
    #      예외로 올라가면 파일 손상과 똑같이 그날 수집 전체가 죽고, 새 raw 가 안 생겨
    #      매 런이 같은 자리에서 죽는다. 봇이 "per candidate/file" 이라 한 candidate 쪽이다.
    from data_pipeline.lake import canonical_price_daily_partition

    class ListFailingStorage(LocalStorage):
        def list_keys(self, prefix):
            if prefix.startswith(canonical_price_daily_partition("KR", "2026-08-13")):
                raise OSError("S3 ListObjects 실패")
            return super().list_keys(prefix)

    storage = ListFailingStorage(tmp_path / "lake")
    _write_price_daily(storage, "2026-08-12", ["091160"])   # 멀쩡한 이전 기준
    _write_price_daily(storage, "2026-08-13", ["091160"])   # 이 파티션의 목록 조회가 죽는다

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""                  # 예외가 아니라 물러나기다
    assert newcomers == ["042700"]       # 08-12 기준으로 판정이 계속된다


def _write_depth_history(storage, tickers: list[str], days: int = 65, end: str = "2026-08-13"):
    """`days` 개 거래일 파티션에 같은 티커 집합을 깔아 깊이 판정의 배경을 만든다."""
    from datetime import date, timedelta

    d = date.fromisoformat(end)
    for _ in range(days):
        _write_price_daily(storage, d.isoformat(), tickers)
        d -= timedelta(days=1)


def test_shallow_ticker_stays_eligible_until_history_lands(tmp_path):
    # WHY: **존재는 이력을 증명하지 못한다.** 티커는 얕게도 들어온다 — ① 판정 불가 런에서
    #      증분 5일치만 ② 이력 fetch 가 실패해도 어댑터가 모은 봉을 그대로 냄 ③ MAX_PAGES
    #      절단. 셋 다 결과가 같다: '이미 있음'이 되어 400일 이력이 영영 재시도되지 않는다.
    #      SFN 이 partial 런도 정제로 보내므로 그 얕은 행은 실제로 canonical 에 들어간다.
    #      최신뿐 아니라 **과거 파티션**도 봐야 얕게 들어온 티커가 자격을 유지한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_depth_history(storage, ["091160"])                      # 091160 은 깊은 이력
    for d in ("2026-08-11", "2026-08-12", "2026-08-13"):           # 042700 은 최근 3일만
        _write_price_daily(storage, d, ["091160", "042700"])

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""
    assert newcomers == ["042700"]   # 최신에 '있는데도' 편입 — 얕기 때문이다


def test_deep_ticker_is_not_flagged(tmp_path):
    # WHY: 깊이 판정이 정상 종목을 계속 편입으로 잡으면 매일 412종에 400일 창이 붙는다 —
    #      깊이가 채워진 티커는 반드시 빠져야 한다(그게 '성공할 때까지'의 종료 조건이다).
    storage = LocalStorage(tmp_path / "lake")
    _write_depth_history(storage, ["091160", "042700"])

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""
    assert newcomers == []


def test_shallow_canonical_cannot_answer_depth_so_presence_only(tmp_path):
    # WHY: canonical 자체가 얕으면(새 레이크·부트스트랩) '오래전에도 있었나'에 답할 수 없다.
    #      답할 수 없는 것을 '아니오'로 읽으면 **전 종목이 편입**이 되어 412종에 400일 창이
    #      붙는다. 모르는 것을 아는 척하지 않고 존재 판정만 쓴다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_depth_history(storage, ["091160"], days=5)   # 깊이 요건에 한참 못 미친다

    newcomers, _, reason = ingest_price_raw._newcomers(
        storage, ["091160", "042700"], "2026-08-14")
    assert reason == ""
    assert newcomers == ["042700"]   # 091160 을 얕다고 몰지 않는다


def test_partition_discovery_listing_failure_still_collects(tmp_path):
    # WHY: 파티션 **발견** 조회(`list_keys(marker)`)는 파티션별 조회와 달리 물러날 곳이
    #      없다 — 그래서 감싸지 않으면 S3 일시 오류 한 번에 1차 fetch 전에 런이 죽고,
    #      새 raw 가 안 생겨 매 런이 같은 자리에서 죽는다. 판정만 포기하고 수집은 계속한다.
    settings = _settings(tmp_path)

    class DiscoveryFailingStorage(LocalStorage):
        def list_keys(self, prefix):
            if prefix.endswith("/trade_date="):
                raise OSError("S3 ListObjects 실패")
            return super().list_keys(prefix)

    storage = DiscoveryFailingStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-08-13", [("042700", "091160")])
    source = _RecordingSource()

    code = ingest_price_raw.run(settings, storage, source, "r1", "2026-08-09", "2026-08-14")

    assert len(source.calls) == 1              # 1차 수집이 **돌았다**
    assert storage.list_keys("raw") != []
    log = json.loads(storage.get_bytes(
        [k for k in storage.list_keys("operations_archive") if "kis" in k][0]))
    assert log["newcomer_scan"] == "scan_failed(list_partitions)"
    assert log["status"] == "partial" and code == 1   # 불확실성은 드러난다
