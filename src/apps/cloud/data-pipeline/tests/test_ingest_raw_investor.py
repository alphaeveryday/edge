"""ingest_raw_investor 스텝 테스트 — dataset·raw append·collection_log·holdings 유니버스 파생.

스텝 본체(격리·상태전이·로그 계약)는 ingest_price_raw 와 동일 코드라 그 테스트가 커버한다.
여기선 투자자 트랙 고유 배선만 잠근다: dataset=investor_flow_daily 파티션·유니버스 파생(가격과
같은 축)·skip.
"""

import json
from collections import defaultdict

from data_pipeline.config import KisInvestorSource as KisInvestorSourceConfig, load_settings
from data_pipeline.lake import LocalStorage, raw_investor_estimate_partition
from data_pipeline.sources.kis_investor import KisInvestorSource
from data_pipeline.steps import ingest_raw_investor

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[news.sources.fmp.symbol_map]
NVDA = "NVDA"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[targets]
symbols = ["NVDA", "005930"]
"""

_TOKEN = json.dumps({"access_token": "tok"})
_EMPTY = json.dumps({"rt_cd": "0", "output2": []})


def _row(date: str) -> dict:
    return {
        "stck_bsop_date": date,
        "prsn_ntby_qty": "-70203", "prsn_ntby_tr_pbmn": "-3190",
        "frgn_ntby_qty": "39367", "frgn_ntby_tr_pbmn": "1713",
        "orgn_ntby_qty": "11941", "orgn_ntby_tr_pbmn": "660",
    }


def _ok(rows: list[dict]) -> str:
    return json.dumps({"rt_cd": "0", "output2": rows})


class FakeClient:
    _sleep = staticmethod(lambda secs: None)

    def __init__(self, chunk_responses: dict[str, list[str]]):
        self.chunk_responses = chunk_responses
        self._idx: dict[str, int] = defaultdict(int)

    def request(self, method, url, *, headers=None, data=None, decode=True):
        if method == "POST":
            return _TOKEN
        sym = url.split("FID_INPUT_ISCD=")[1].split("&")[0]
        pages = self.chunk_responses.get(sym, [])
        idx = self._idx[sym]
        self._idx[sym] += 1
        return pages[idx] if idx < len(pages) else _EMPTY


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _source(chunk_responses, *, app_key="k", app_secret="s"):
    config = KisInvestorSourceConfig(env="prod", app_key=app_key, app_secret=app_secret)
    return KisInvestorSource(config, FakeClient(chunk_responses))


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


def test_saves_investor_flow_partition_and_log(tmp_path):
    # WHY: 수집분이 dataset=investor_flow_daily 파티션 규약(market 별 1파일·ingest_date)대로
    #      저장되고 collection_log 로 남아야 한다 — 가격과 다른 dataset 이라 파티션이 안 섞인다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    # 005930 은 KR 6자리라 항등 매핑돼 수집된다(NVDA 는 국내 API 대상 아님 — plan 에서 빠짐).
    source = _source({"005930": [_ok([_row("20260703"), _row("20260702")])]})

    assert ingest_raw_investor.run(settings, storage, source, "r1") == 0
    keys = storage.list_keys("raw")
    assert len(keys) == 1
    assert keys[0].startswith("raw/source=kis/dataset=investor_flow_daily/market=KR")
    assert "/ingest_date=" in keys[0] and keys[0].endswith("/part-00000.ndjson")
    assert len(storage.get_bytes(keys[0]).decode("utf-8").strip().splitlines()) == 2

    logs = storage.list_keys("operations_archive/collection_logs/")
    log = json.loads(storage.get_bytes(logs[0]))
    assert "dataset=investor_flow_daily" in logs[0]
    assert log["status"] == "success" and log["records_saved"] == 2


def test_dataset_partition_swap_writes_intraday_zone(tmp_path):
    # WHY: 장중 추정(ALPHA-767)은 이 스텝을 dataset·partition 인자만 갈아끼워 재사용한다.
    #      인자가 실제로 갈리지 않으면 **장중 raw 가 EOD 파티션·EOD collection_log 에 조용히
    #      섞여**, 잠정(가집계)과 확정이 한 데이터셋에 들어간다. 그 오염은 사후에 되돌릴 수
    #      없으므로(어느 행이 어느 소스였는지 구분 불가) 갈림 자체를 여기서 잠근다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    source = _source({"005930": [_ok([_row("20260703")])]})

    assert ingest_raw_investor.run(
        settings, storage, source, "r1",
        dataset="investor_flow_intraday", partition=raw_investor_estimate_partition,
        job_name="ingest_raw_investor_estimate",
    ) == 0

    keys = storage.list_keys("raw")
    assert keys[0].startswith("raw/source=kis/dataset=investor_flow_intraday/market=KR")
    logs = storage.list_keys("operations_archive/collection_logs/")
    assert "dataset=investor_flow_intraday" in logs[0]
    assert json.loads(storage.get_bytes(logs[0]))["job_name"] == "ingest_raw_investor_estimate"


def test_universe_derived_from_latest_holdings_snapshot(tmp_path):
    # WHY: 대상이 ETF 가 아니라 그 구성종목이라 유니버스는 canonical KR holdings 최신 스냅샷에서
    #      파생돼야 한다(가격과 같은 축, ALPHA-419·482). 구성종목 수만큼 KIS 질의가 나가야 한다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-14", [("111111", "091160")])  # 구 스냅샷 — 무시
    _write_holdings(storage, "2026-07-15", [("042700", "091160"), ("000660", "091160")])
    source = _source({
        "042700": [_ok([_row("20260703")])],
        "000660": [_ok([_row("20260703")])],
        "091160": [_ok([_row("20260703")])],
    })

    assert ingest_raw_investor.run(settings, storage, source, "r1") == 0
    [raw_key] = storage.list_keys("raw")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    tickers = {json.loads(line)["our_ticker"] for line in lines}
    assert {"042700", "000660", "091160"} <= tickers  # 구성종목 + ETF 자신
    assert "111111" not in tickers  # 최신 스냅샷만
    logs = storage.list_keys("operations_archive/collection_logs/")
    assert json.loads(storage.get_bytes(logs[0]))["symbols_from_holdings"] == 3


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입(로컬 등)은 실패가 아니라 명시적 skip — 조용히 성공처럼 보이면 안 되고
    #      skip 사실이 로그로 남아야 한다(Rule 12).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    source = _source({}, app_key=None)

    assert ingest_raw_investor.run(settings, storage, source, "r1") == 0
    assert storage.list_keys("raw") == []
    logs = storage.list_keys("operations_archive/collection_logs/")
    assert json.loads(storage.get_bytes(logs[0]))["status"] == "skipped"


def test_symbol_error_marks_run_partial(tmp_path):
    # WHY: 일부 종목만 실패(KIS 오류코드)하면 저장분은 있으나 온전치 않다 — partial 로 드러내고
    #      비0 종료로 오케스트레이터에도 손실을 알린다(격리≠은폐).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-15", [("042700", "091160"), ("000660", "091160")])
    err = json.dumps({"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "조회 오류"})
    source = _source({"042700": [_ok([_row("20260703")])], "000660": [err], "091160": [err]})

    assert ingest_raw_investor.run(settings, storage, source, "r1") == 1
    logs = storage.list_keys("operations_archive/collection_logs/")
    log = json.loads(storage.get_bytes(logs[0]))
    assert log["status"] == "partial"
    assert log["records_failed_symbols"] == 2
