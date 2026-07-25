"""ingest_raw_disclosure 스텝 테스트 — 메타 ndjson + 본문 ZIP 이중 저장·뉴스형 상태기계.

핵심 계약: 공시 raw 도 ingest_date/run_id 파티션에 메타를 전부 보존하고, 본문(document.xml
ZIP)은 rcept_no 별 객체로 무변형 저장한다. 특히 특정 corp 의 빈 날짜창은 정상(뉴스형) —
재무의 '0행=error' 가드를 두지 않는다. 각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9).
"""

import json

from data_pipeline.config import load_settings
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.http import StopFetch
from data_pipeline.steps import ingest_raw_disclosure

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[targets]
# 운영 sources.toml 과 같은 모양 — KR 개별주·ETF(091160)·US 심볼이 섞여 있다. ETF 를 빼둔
# CONFIG 로 테스트하면 '유니버스에 ETF 가 섞여 들어오는' 실제 경로를 못 밟는다(ALPHA-477).
symbols = ["005930", "091160", "NVDA"]
"""


def _rec(rcept_no, report_nm="단일판매ㆍ공급계약체결", market="KR") -> dict:
    return {
        "market": market, "our_ticker": "005930", "stock_code": "005930",
        "corp_code": "00126380", "report_nm": report_nm, "rcept_no": rcept_no,
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        "fetched_at": "2026-07-10T00:00:00+00:00",
    }


class FakeSource:
    """스텝의 상태기계·이중 저장을 HTTP 없이 검증하기 위한 덕타이핑 소스."""

    source_name = "dart"

    def __init__(self, records=(), *, enabled=True, planned=1,
                 doc_fail=frozenset(), doc_bytes=b"PK\x03\x04body", stop=False):
        self._records = list(records)
        self.enabled = enabled
        self.planned_symbols = planned
        self.fetch_failures: list[dict] = []
        self._doc_fail = doc_fail
        self._doc_bytes = doc_bytes
        self._stop = stop

    def fetch(self, symbols, from_date=None, to_date=None):
        if self._stop:
            raise StopFetch("DART status=020 (일 사용한도 초과)")
        yield from self._records

    def fetch_document(self, rcept_no: str) -> bytes:
        if rcept_no in self._doc_fail:
            raise ValueError(f"document boom: {rcept_no}")
        return self._doc_bytes


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _run(tmp_path, source, storage=None, run_id="r1"):
    settings = _settings(tmp_path)
    storage = storage or LocalStorage(tmp_path / "lake")
    code = ingest_raw_disclosure.run(settings, storage, source, run_id)
    return code, storage


def _log(storage, run_id):
    key = next(k for k in storage.list_keys("operations_archive") if f"run_id={run_id}" in k)
    return json.loads(storage.get_bytes(key))


def test_saves_meta_ndjson_and_document_objects(tmp_path):
    # WHY: 공시 raw 는 메타(ndjson)와 본문(ZIP 객체)을 함께 남긴다 — 메타 행은 document_raw_path
    #      로 본문을 가리켜 둘을 잇고, 파티션은 source=dart/dataset=disclosures/market=KR 이어야
    #      후속이 일관되게 읽는다.
    source = FakeSource(records=[_rec("A1"), _rec("B2", report_nm="사업보고서")])
    code, storage = _run(tmp_path, source)

    assert code == 0
    meta_keys = [k for k in storage.list_keys("raw") if k.endswith(".ndjson")]
    [meta_key] = meta_keys
    assert meta_key.startswith("raw/source=dart/dataset=disclosures/market=KR")
    assert "/ingest_date=" in meta_key and meta_key.endswith("/part-00000.ndjson")
    lines = [json.loads(x) for x in storage.get_bytes(meta_key).decode().strip().splitlines()]
    assert len(lines) == 2
    assert all(ln["document_raw_path"] and ln["body_format"] for ln in lines)

    doc_keys = [k for k in storage.list_keys("raw") if k.endswith(".zip")]
    assert {k.rsplit("/", 1)[1] for k in doc_keys} == {"A1.zip", "B2.zip"}
    # 메타의 document_raw_path 가 실제 본문 객체 키와 일치(메타↔본문 링크 무결)
    assert {ln["document_raw_path"] for ln in lines} == set(doc_keys)

    log = _log(storage, "r1")
    assert log["status"] == "success"
    assert log["records_saved"] == 2 and log["documents_saved"] == 2


def test_documents_written_as_fetched_not_buffered(tmp_path):
    # WHY(Codex #83 P2): 본문 ZIP 은 대용량이라 전량 메모리 버퍼링하면 넓은 백필(사업보고서
    #      다수)에서 raw 를 하나도 못 쓰고 OOM 난다 — 받는 즉시 저장해야 한다. put 순서로
    #      검증: 본문(.zip)들이 메타(.ndjson)보다 먼저 쓰인다(버퍼링으로 되돌리면 메타·본문이
    #      같은 저장 단계에 묶여 이 순서가 깨진다).
    class OrderSpy(LocalStorage):
        def __init__(self, root):
            super().__init__(root)
            self.puts: list[str] = []

        def put_bytes(self, key, data):
            self.puts.append(key)
            super().put_bytes(key, data)

    spy = OrderSpy(tmp_path / "lake")
    settings = _settings(tmp_path)
    ingest_raw_disclosure.run(settings, spy, FakeSource(records=[_rec("A1"), _rec("B2")]), "r1")

    raw_puts = [k for k in spy.puts if k.startswith("raw/")]
    assert len([k for k in raw_puts if k.endswith(".zip")]) == 2  # 본문 2건
    assert raw_puts[-1].endswith(".ndjson")  # 메타는 맨 마지막(본문 즉시 저장 후)
    assert all(k.endswith(".zip") for k in raw_puts[:-1])  # 그 앞은 전부 본문


def test_empty_window_is_success_not_error(tmp_path):
    # WHY(Rule 7 — 스텝별 판정): 매핑 대상이 있는데 공시가 0건인 건 정상 빈 창이다(그날 대상
    #      유형 공시 없음) — 재무제표의 '0행=error' 가드를 복사하면 정상 상태를 오탐한다.
    #      공시는 뉴스형: 빈 창은 success(0 저장)로 남는다.
    source = FakeSource(records=[], planned=1)
    code, storage = _run(tmp_path, source)

    assert code == 0
    assert storage.list_keys("raw") == []
    assert _log(storage, "r1")["status"] == "success"


def test_document_fetch_failure_marks_partial_meta_preserved(tmp_path):
    # WHY: 본문 수집이 실패해도 메타는 보존한다(bronze) — 단, 온전치 않으므로 partial 로 드러내고
    #      비0 종료한다. 실패한 행의 document_raw_path 는 None 이어야(조용히 성공 위장 금지).
    source = FakeSource(records=[_rec("OK1"), _rec("BAD2")], doc_fail={"BAD2"})
    code, storage = _run(tmp_path, source)

    assert code == 1
    [meta_key] = [k for k in storage.list_keys("raw") if k.endswith(".ndjson")]
    by_id = {json.loads(x)["rcept_no"]: json.loads(x)
             for x in storage.get_bytes(meta_key).decode().strip().splitlines()}
    assert by_id["OK1"]["document_raw_path"] is not None
    assert by_id["BAD2"]["document_raw_path"] is None
    log = _log(storage, "r1")
    assert log["status"] == "partial"
    assert log["records_saved"] == 2 and log["documents_saved"] == 1
    assert log["records_failed_targets"] == 1


def test_list_truncation_stays_success_but_logged(tmp_path):
    # WHY(ALPHA-351): MAX_PAGES 목록 절단은 데이터 유효 + 다음 창에서 이어받으므로 SFN 을
    #      죽이면 안 된다 — kind=truncation 은 성공(exit 0)으로 남기되, 절단 자체는 로그에
    #      남겨 fail-loud 를 유지한다. 오케스트레이션이 흔한 절단마다 빨간불이 되던 원인.
    source = FakeSource(records=[_rec("A1")])
    source.fetch_failures.append(
        {"symbol": "005930", "our_ticker": "005930",
         "error": "MAX_PAGES(10) 도달 — 목록 절단 가능", "kind": "truncation"}
    )
    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["status"] == "success"
    assert log["records_failed_targets"] == 1  # 절단도 로그엔 남는다(fail-loud)


def test_truncation_plus_real_failure_still_partial(tmp_path):
    # WHY(ALPHA-351): 절단을 성공 처리해도 같은 런의 진짜 실패(본문 결측)까지 삼키면 안 된다 —
    #      절단은 제외하되 real failure 가 하나라도 있으면 partial/exit 1 을 유지한다.
    source = FakeSource(records=[_rec("OK1"), _rec("BAD2")], doc_fail={"BAD2"})
    source.fetch_failures.append(
        {"symbol": "005930", "our_ticker": "005930",
         "error": "MAX_PAGES(10) 도달 — 목록 절단 가능", "kind": "truncation"}
    )
    code, storage = _run(tmp_path, source)

    assert code == 1
    assert _log(storage, "r1")["status"] == "partial"


def test_stopfetch_marks_stopped(tmp_path):
    # WHY: 쿼터/키 오류(StopFetch)는 조용한 성공이 아니라 stopped 로 드러내고 비0 종료한다.
    source = FakeSource(stop=True)
    code, storage = _run(tmp_path, source)

    assert code == 1
    assert _log(storage, "r1")["status"] == "stopped"


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입은 실패가 아니라 명시적 skip — 조용히 성공처럼 보이면 안 된다(Rule 12).
    source = FakeSource(enabled=False)
    code, storage = _run(tmp_path, source)

    assert code == 0
    assert storage.list_keys("raw") == []
    assert _log(storage, "r1")["status"] == "skipped"


def test_no_mapped_targets_marks_skipped(tmp_path):
    # WHY: 활성인데 매핑 대상 0개면(심볼맵 누락) 수집 불가 설정 — success(0건) 위장 대신 skip.
    source = FakeSource(records=[], planned=0)
    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["status"] == "skipped" and log["reason"] == "no mapped targets"


def test_raw_write_failure_still_writes_collection_log(tmp_path):
    # WHY: "결과는 항상 collection_log" 계약 — raw put_bytes 가 실패(IAM·네트워크)해도 런
    #      흔적이 사라지면 안 된다. status=error·exit 1 로 남고 로그는 남아야 한다.
    class RawFailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            if key.startswith("raw/"):
                raise OSError("S3 raw write denied")
            super().put_bytes(key, data)

    source = FakeSource(records=[_rec("A1")])
    code, storage = _run(tmp_path, source, storage=RawFailingStorage(tmp_path / "lake"))

    assert code == 1
    log = _log(storage, "r1")
    assert log["status"] == "error"
    assert "denied" in log["error"]


def test_disabled_skip_survives_log_write_failure(tmp_path):
    # WHY: skip 도 collection_log 로 드러나는 것이 계약이다(Rule 12). 스토리지 장애로 그
    #      로그마저 못 남겼는데 exit 0 이면 스케줄러는 성공으로 보고, 감사 레코드가 사라진
    #      사실을 아무도 모른다. 이 스텝은 동작은 맞았으나 근거 주석도 테스트도 없어 정리
    #      대상으로 오해받기 쉬웠다 — 5개 통일(ALPHA-451)의 일부로 고정한다.
    class FailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            raise OSError("storage down")

    code, _storage = _run(tmp_path, FakeSource(enabled=False),
                          storage=FailingStorage(tmp_path / "lake"))

    assert code == 1


def _write_holdings(storage, as_of, rows) -> None:
    """rows: [(constituent_ticker, etf_id)] → canonical KR holdings parquet."""
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


class _UniverseFakeSource(FakeSource):
    """universe_from_holdings 옵트인 소스 — 스텝이 넘긴 symbols 를 기록만 한다."""

    universe_from_holdings = True

    def __init__(self, **kw):
        super().__init__(**kw)
        self.received: list[str] | None = None

    def fetch(self, symbols, from_date=None, to_date=None):
        self.received = list(symbols)
        return iter(())


def test_universe_derived_from_holdings_excludes_etf_itself(tmp_path):
    # WHY: 공시 수집이 정적 targets(KR 9)에 묶여 유니버스(309 구성종목)를 못 따라왔다 —
    #      corp_code 를 309 종 채워놔도(ALPHA-491) 그 회사 공시를 애초에 안 가져왔다(ALPHA-477).
    #      단 ETF 자기 티커는 빼야 한다: ETF 는 DART 신고자가 아니라 corpCode.xml 에 없어,
    #      넣으면 31 종이 매 런 '미매핑'으로 잡혀 결측이 아닌 것을 결측으로 센다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-07-14", [("111111", "091160")])  # 구 스냅샷 — 무시돼야 함
    _write_holdings(storage, "2026-07-15", [("042700", "091160"), ("000660", "091160")])
    source = _UniverseFakeSource(records=[])

    code, storage = _run(tmp_path, source, storage=storage)

    assert code == 0
    assert {"042700", "000660"} <= set(source.received)  # 구성종목은 들어온다
    assert "111111" not in source.received  # 최신 스냅샷만
    assert "005930" in source.received  # 기존 targets 는 유지(union)
    # ETF 는 holdings 파생분이든 **정적 targets 에 등재된 것이든** 빠져야 한다. 091160 은 운영
    # sources.toml 의 targets 에도 있어, holdings 쪽만 걸러선 매 런 미매핑으로 남는다.
    assert "091160" not in source.received
    log = _log(storage, "r1")
    assert log["symbols_from_holdings"] == 2
    assert log["symbols_excluded_etf"] == 1  # 091160 — 뺀 사실이 로그로 드러난다


def test_unmapped_targets_stay_success_but_counted(tmp_path):
    # WHY: holdings 유니버스에는 corpCode.xml 에 없는 종목(비상장 편입 등)이 상수로 섞인다.
    #      진짜 실패와 같이 세면 raw 페이즈가 **매 런** partial·exit 1 이 되어 SFN 게이트가
    #      매일 깨진다 — 재시도로 낫지 않는 구조적 결측이라 런을 죽일 근거가 없다(ADR-0030).
    #      그렇다고 지우면 조용한 결측이므로, 원장이 보는 계측에는 그대로 남아야 한다.
    source = FakeSource(records=[_rec("A1")])
    source.fetch_failures = [{"symbol": "999999", "our_ticker": "999999",
                              "error": "corpCode.xml 에 corp_code 없음", "kind": "unmapped"}]

    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["status"] == "success"  # 런은 죽지 않는다
    assert log["records_failed_targets"] == 1  # 그러나 유실은 계속 센다
    assert log["ops"]["failed_records"] == 1
    assert log["failed_targets"][0]["kind"] == "unmapped"
