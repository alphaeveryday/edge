"""ingest_raw_disclosure 스텝 테스트 — 메타 ndjson + 본문 ZIP 이중 저장·뉴스형 상태기계.

핵심 계약: 공시 raw 도 ingest_date/run_id 파티션에 **유니버스 행 메타를 전부** 보존하고, 본문(document.xml
ZIP)은 rcept_no 별 객체로 무변형 저장한다. 특히 특정 corp 의 빈 날짜창은 정상(뉴스형) —
재무의 '0행=error' 가드를 두지 않는다. 각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9).
"""

import json

import pytest

from data_pipeline.config import load_settings
from data_pipeline.lake import LocalStorage, raw_disclosure_document_key
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


def _rec(rcept_no, report_nm="단일판매ㆍ공급계약체결", market="KR", is_target=True) -> dict:
    # `is_target` 은 실 소스가 매 행에 다는 플래그다(ALPHA-865) — 픽스처가 이걸 빼면 스텝의
    # 본문 분기가 **한 번도 안 밟히는** 경로가 되어, 비대상 행을 어떻게 다루는지 아무 테스트도
    # 검증하지 못한다(픽스처는 운영 산출 모양을 그대로 흉내내야 한다).
    return {
        "market": market, "our_ticker": "005930", "stock_code": "005930",
        "corp_code": "00126380", "report_nm": report_nm, "rcept_no": rcept_no,
        "is_target": is_target,
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        "fetched_at": "2026-07-10T00:00:00+00:00",
    }


class FakeSource:
    """스텝의 상태기계·이중 저장을 HTTP 없이 검증하기 위한 덕타이핑 소스."""

    source_name = "dart"

    def __init__(self, records=(), *, enabled=True, planned=1,
                 doc_fail=frozenset(), doc_bytes=b"PK\x03\x04body", stop=False,
                 list_total_count=None, list_rows_seen=0, resolved_window=None,
                 universe_matched=0, type_matched=0, dropped_malformed=0,
                 report_name_filters=("공급계약", "사업보고서")):
        self._records = list(records)
        self.enabled = enabled
        self.planned_symbols = planned
        self.fetch_failures: list[dict] = []
        # 창 규모 관측 — 실 소스가 fetch 중에 채운다. 스텝은 이걸 로그로 옮기기만 한다.
        self.list_total_count = list_total_count
        self.list_rows_seen = list_rows_seen
        # 감쇠 두 축(ALPHA-865) — 마찬가지로 소스가 채우고 스텝은 옮기기만 한다.
        self.universe_matched = universe_matched
        self.type_matched = type_matched
        self.dropped_malformed = dropped_malformed
        # 실 소스는 설정에서 받는다 — 스텝이 이걸 로그로 옮겨 `is_target` 의 기준을 남긴다.
        self.report_name_filters = list(report_name_filters)
        # 실제로 수집한 창 — 인자와 다를 수 있다(시작일만 준 창은 소스가 끝을 확정한다)
        self.resolved_window = resolved_window or (None, None)
        self._doc_fail = doc_fail
        self._doc_bytes = doc_bytes
        self._stop = stop
        # 실제로 본문을 **요청한** rcept_no — 틱 멱등(재다운로드 회피)의 유일한 관측점이다.
        self.doc_requests: list[str] = []

    def fetch(self, symbols, from_date=None, to_date=None):
        if self._stop:
            raise StopFetch("DART status=020 (일 사용한도 초과)")
        yield from self._records

    def fetch_document(self, rcept_no: str) -> bytes:
        self.doc_requests.append(rcept_no)
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


def _meta_rows(storage, run_id) -> dict[str, dict]:
    """그 run 의 메타 ndjson 행 → {rcept_no: row}."""
    [key] = [k for k in storage.list_keys("raw")
             if k.endswith(".ndjson") and f"/run_id={run_id}/" in k]
    rows = [json.loads(x) for x in storage.get_bytes(key).decode().strip().splitlines()]
    return {r["rcept_no"]: r for r in rows}


def test_second_run_reuses_documents_but_keeps_full_meta(tmp_path):
    # WHY(ALPHA-720): 이 소스엔 증분 커서가 없어 매 실행이 날짜창 전체를 다시 읽는다 — 장중
    #      레인은 같은 날 10슬롯이 돌므로 장치가 없으면 **같은 document.xml ZIP 을 10번**
    #      내려받는다. 없애야 할 것은 그 재다운로드 하나뿐이다.
    #      메타(ndjson)는 반대로 **계속 전건 저장돼야 한다**: 매 런이 자기 run_id 파티션에
    #      창 전체 관측을 남기는 것이 이 소스의 유일한 완전성 근거이고(런 사이 rcept_no 집합
    #      비교), 메타까지 접으면 그 근거를 스스로 없앤다. 두 축을 한 테스트에 묶어 고정한다 —
    #      "재다운로드를 줄인다"는 최적화가 메타 보존을 함께 깎는 것이 가장 그럴듯한 회귀다.
    storage = LocalStorage(tmp_path / "lake")
    first = FakeSource(records=[_rec("A1"), _rec("B2", report_nm="사업보고서")])
    assert _run(tmp_path, first, storage=storage, run_id="r1")[0] == 0

    second = FakeSource(records=[_rec("A1"), _rec("B2", report_nm="사업보고서")])
    code, _ = _run(tmp_path, second, storage=storage, run_id="r2")

    assert code == 0
    assert second.doc_requests == []  # 본문은 한 건도 다시 요청하지 않았다
    log2 = _log(storage, "r2")
    assert log2["documents_saved"] == 0 and log2["documents_reused"] == 2
    assert log2["records_saved"] == 2  # ← 메타는 그대로 2건(창 전체 관측 보존)

    # 2회차 메타는 1회차가 저장한 **기존 키**를 가리킨다 — 정제가 그 ZIP 을 그대로 연다.
    rows1, rows2 = _meta_rows(storage, "r1"), _meta_rows(storage, "r2")
    assert {k: v["document_raw_path"] for k, v in rows2.items()} \
        == {k: v["document_raw_path"] for k, v in rows1.items()}
    assert all(storage.get_bytes(r["document_raw_path"]) for r in rows2.values())
    assert all(r["body_format"] for r in rows2.values())
    # 본문 객체는 늘지 않았다(2회차가 자기 파티션에 사본을 만들지 않는다).
    assert len([k for k in storage.list_keys("raw") if k.endswith(".zip")]) == 2


def test_seen_map_spans_two_ingest_dates_and_newest_key_wins(tmp_path):
    # WHY(edge-review · Rule 9): 2일 lookback 은 편의가 아니라 **설계의 근거**다. `ingest_date`
    #      가 UTC 날짜라 KST 09:00 = UTC 00:00 에서 갈리고, 한 KR 영업일의 슬롯들이 두 UTC
    #      날짜에 흩어진다 — 하루만 보면 오전 슬롯이 받아 둔 본문을 오후 슬롯이 못 찾아
    #      매일 한 번씩 창 전체를 재다운로드한다. 실행 시각이 곧 `started_date` 라 런 두 번으로는
    #      이 경계를 밟을 수 없어(같은 UTC 날짜) `_existing_documents` 를 직접 부른다.
    #      `_DOC_LOOKBACK_DAYS` 가 1 로 줄거나 순회가 뒤집히면 여기서 깨진다.
    storage = LocalStorage(tmp_path / "lake")

    def _put(ingest_date, run_id, rcept_no) -> str:
        key = raw_disclosure_document_key("dart", "KR", ingest_date, run_id, rcept_no)
        storage.put_bytes(key, b"PK\x03\x04body")
        return key

    _put("2026-08-02", "r0", "OLD")            # 창 밖(2일 초과)
    yday = _put("2026-08-03", "r1", "YDAY")    # 어제 = KST 오전 슬롯이 받아 둔 본문
    _put("2026-08-03", "r1", "BOTH")
    both_today = _put("2026-08-04", "r2", "BOTH")

    found = ingest_raw_disclosure._existing_documents(storage, "dart", "KR", "2026-08-04")

    assert "OLD" not in found          # 상한이 실제로 상한이다
    assert found["YDAY"] == yday       # UTC 날짜 경계를 넘어 찾는다
    assert found["BOTH"] == both_today  # 같은 문서가 양일에 있으면 최신 키가 남는다


def test_seen_map_only_accepts_keys_the_document_builder_produces(tmp_path):
    # WHY(edge-review): seen-map 은 **읽는 쪽**이고 `raw_disclosure_document_key` 는 쓰는 쪽이다.
    #      둘의 집합이 어긋나면 히트가 곧 "본문이 있다"는 거짓 주장이 된다 — 히트한 순간
    #      document API 호출을 건너뛰고 그 키를 document_raw_path 로 박으므로, 정제가 본문이
    #      아닌 ZIP 을 열게 되고 로그는 success·failed_records=0 으로 남는다(조용한 오염).
    #      "이 프리픽스 아래 아무 .zip" 으로 잡으면 이 파티션에 다른 객체를 두는 변경 하나로
    #      그 경로가 열린다 — 수용 집합을 빌더 출력 형태로 못박는다.
    storage = LocalStorage(tmp_path / "lake")
    assert _run(tmp_path, FakeSource(records=[_rec("A1")]), storage=storage, run_id="r1")[0] == 0

    day_prefix = next(k for k in storage.list_keys("raw") if k.endswith(".zip")).split("/documents/")[0]
    storage.put_bytes(f"{day_prefix}/quarantine/B2.zip", b"PK\x03\x04nope")  # 본문 아님
    storage.put_bytes(f"{day_prefix}/documents/.zip", b"PK\x03\x04empty")    # 빈 rcept_no

    second = FakeSource(records=[_rec("A1"), _rec("B2")])
    code, _ = _run(tmp_path, second, storage=storage, run_id="r2")

    assert code == 0
    assert second.doc_requests == ["B2"]  # A1 만 재사용, B2 는 격리본을 본문으로 오인하지 않는다
    log2 = _log(storage, "r2")
    assert log2["documents_saved"] == 1 and log2["documents_reused"] == 1


def test_failed_document_is_retried_by_next_run(tmp_path):
    # WHY(ALPHA-720): seen-map 의 출처가 **저장된 객체**라서 얻는 성질이다 — 본문 fetch 가
    #      실패한 건은 객체가 없어 seen-map 에 안 들어가고, 다음 실행이 그것만 다시 받는다.
    #      별도 재시도 목록을 들고 다니지 않는 이유이자, seen-set 을 DB(`document` 테이블)에서
    #      뽑지 않은 이유이기도 하다(적재까지 못 간 공시는 DB 에 영영 없어 영구 재다운로드가 된다).
    storage = LocalStorage(tmp_path / "lake")
    first = FakeSource(records=[_rec("OK1"), _rec("BAD2")], doc_fail={"BAD2"})
    assert _run(tmp_path, first, storage=storage, run_id="r1")[0] == 1  # partial

    second = FakeSource(records=[_rec("OK1"), _rec("BAD2")])
    code, _ = _run(tmp_path, second, storage=storage, run_id="r2")

    assert code == 0
    assert second.doc_requests == ["BAD2"]  # 실패했던 것만 재시도
    log2 = _log(storage, "r2")
    assert log2["documents_saved"] == 1 and log2["documents_reused"] == 1


def test_same_rcept_no_twice_in_one_window_downloads_once(tmp_path):
    # WHY: 목록이 수집 중에도 자라(접수 피크 16시) 같은 rcept_no 의 **서로 다른 관측**
    #      (rm ""→"정")이 한 창에 둘 다 올 수 있다 — 소스는 내용이 완전히 같은 행만 접으므로
    #      여기까지 온다. 저장 성공분을 색인에 되먹이지 않으면 같은 본문을 한 런 안에서 두 번
    #      받는다(같은 키를 두 번 덮어쓰므로 조용히 지나간다).
    dup = _rec("A1")
    dup["rm"] = "정"  # 같은 문서의 다른 관측 — 소스의 완전동일 접기를 통과한다
    source = FakeSource(records=[_rec("A1"), dup])

    code, storage = _run(tmp_path, source)

    assert code == 0
    assert source.doc_requests == ["A1"]
    log = _log(storage, "r1")
    assert log["records_saved"] == 2  # 두 관측 다 보존(bronze)
    assert log["documents_saved"] == 1 and log["documents_reused"] == 1


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


def test_no_failure_kind_is_tolerated_anymore(tmp_path):
    # WHY(Rule 12): 관용 어휘가 비었다. 종전 두 관용(`unmapped`·`truncation`)은 목록 질의가
    #      종목별이던 시절의 판단이었다 — `unmapped` 는 발생 지점이 사라졌고, `truncation` 의
    #      근거("다음 증분 창이 이어받는다")는 축이 창 전체로 바뀌며 무너졌다(상한 도달 = ~5만
    #      행 미수집, 운영자 지정 백필 창은 이어받을 창이 없다).
    #      죽은 어휘를 관용 목록에 남기면 **그 이름을 쓰는 새 실패가 조용히 성공으로 통과한다** —
    #      관용은 "이 실패는 런을 죽일 근거가 없다"는 판단이지 이름에 대한 영구 면제가 아니다.
    for kind in ("truncation", "unmapped"):
        source = FakeSource(records=[_rec("A1")])
        source.fetch_failures = [{"symbol": None, "our_ticker": None, "page": 500,
                                  "error": f"{kind} 을 자칭하는 실패", "kind": kind}]

        case_dir = tmp_path / kind
        case_dir.mkdir()
        code, storage = _run(case_dir, source)

        assert code == 1, kind
        log = _log(storage, "r1")
        assert log["status"] == "partial", kind  # 저장분이 있으니 error 가 아니라 partial
        assert log["records_failed_targets"] == 1
        assert log["ops"]["failed_records"] == 1


def test_log_records_the_window_actually_collected(tmp_path):
    # WHY: 소스가 창 끝을 스스로 확정하는 경우가 있다(`--from` 만 준 백필 → 끝은 KST 오늘).
    #      로그에 호출 인자(None)만 남기면 **어떤 창을 수집했는지 사후에 복원되지 않고**,
    #      이 설계의 완전성 근거인 "런 사이 rcept_no 집합 비교"가 성립하지 않는다. 같은 명령도
    #      자정을 사이에 두면 다른 창이 되므로 실제 값이어야 한다.
    source = FakeSource(records=[_rec("A1")], resolved_window=("2026-01-01", "2026-08-03"))

    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["window_from"] == "2026-01-01"
    assert log["window_to"] == "2026-08-03"  # 호출은 창 없이 했는데 실제 창이 남는다


def test_window_scale_observed_in_collection_log(tmp_path):
    # WHY: 창 규모(소스가 신고한 total_count vs 실제로 훑은 행 수)는 페이지 완전성을 사후에
    #      들여다볼 **유일한 관측값**이다. 판정에 쓰지 않기로 했으므로(목록이 자라는 중엔 절단과
    #      유입이 구분되지 않는다) 아무 게이트도 이 값을 지켜주지 않는다 — 기록이 끊기거나
    #      None/0 으로 회귀해도 다른 테스트는 전부 통과한다. 그래서 여기서 계약을 고정한다.
    source = FakeSource(records=[_rec("A1")], list_total_count=1069, list_rows_seen=1069)

    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["list_total_count"] == 1069
    assert log["list_rows_seen"] == 1069


def test_non_target_meta_saved_without_downloading_body(tmp_path):
    # WHY: 이 스텝이 유형 플래그로 지키는 것은 **비용** 하나다 — 메타는 전량 남기되(나중에
    #      대상을 넓힐 때 재수집 불필요) 본문은 대상만 받는다(행당 1콜이라 안 막으면 하루
    #      ~11콜이 universe_matched 만큼(수십~100/일)이 된다). 둘을 따로 묻는다: 저장은 2행인가(보존), 본문 요청은
    #      대상 하나뿐인가(비용). 저장만 보면 본문을 다 받아도 통과하고, 요청만 보면 비대상
    #      메타를 버려도 통과한다.
    source = FakeSource(records=[
        _rec("KEEP"),
        _rec("SKIP", report_nm="주주총회소집결의", is_target=False),
    ])

    code, storage = _run(tmp_path, source)

    assert code == 0
    assert source.doc_requests == ["KEEP"]  # 비대상 본문은 요청조차 하지 않는다
    key = next(k for k in storage.list_keys("raw") if k.endswith(".ndjson"))
    rows = [json.loads(line) for line in storage.get_bytes(key).decode().splitlines()]
    assert [r["rcept_no"] for r in rows] == ["KEEP", "SKIP"]  # 메타는 전량
    # 두 필드를 **따로** 묻는다 — 하나만 보면 나머지 한 줄을 지우는 변이가 안 잡힌다.
    # `[...]` 첨자라 키가 아예 없으면 KeyError 로 죽는다: "부재 vs 명시적 None"까지 가린다
    # (README 가 명문화한 계약이 정확히 그 구분이다).
    assert rows[1]["document_raw_path"] is None  # 안 받은 것을 받은 척하지 않는다
    assert rows[1]["body_format"] is None


def test_non_target_row_is_not_a_partial_run(tmp_path):
    # WHY: 비대상 행은 본문이 없는 게 **정상**이다. 이걸 본문 수집 실패와 같은 신호로 묶으면
    #      유형 필터를 푼 순간 모든 런이 partial(exit 1)이 되어 SFN 이 매 슬롯 raw 부분실패를
    #      알린다 — 알림이 상시 켜지면 진짜 실패가 묻힌다.
    source = FakeSource(records=[
        _rec("SKIP1", report_nm="주주총회소집결의", is_target=False),
        _rec("SKIP2", report_nm="현금ㆍ현물배당결정", is_target=False),
    ])

    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["status"] == "success"
    assert log["ops"]["failed_records"] == 0
    assert log["documents_saved"] == 0
    assert log["records_saved"] == 2


def test_step_survives_malformed_rcept_no_on_non_target_row(tmp_path):
    # WHY: 소스가 형상을 보장하게 됐지만 스텝이 그 보장에 **의존하지 않는지**를 따로 묻는다.
    #      한때 `rcept_no = (record.get("rcept_no") or "").strip()` 이 is_target 게이트 위에
    #      있어, truthy 비문자열(int)이 `or ""` 를 통과해 `.strip()` 에서 터졌다 — 그 한 건이
    #      status=error·exit 1 로 **창의 남은 페이지를 통째로 못 받게** 만들었다.
    #      계층별로 따로 물어야 한다: 소스 테스트는 소스만 증명한다. 백필·다른 생산자가 이
    #      스텝에 먹이는 순간 소스 가드는 경로에 없다.
    source = FakeSource(records=[
        _rec(12345, report_nm="주주총회소집결의", is_target=False),  # truthy 비문자열
        _rec("OK"),
    ])

    code, storage = _run(tmp_path, source)

    assert code == 0                       # 창이 죽지 않는다
    log = _log(storage, "r1")
    assert log["status"] == "success"
    assert log["records_saved"] == 2       # 결함 행도 메타는 남는다
    assert source.doc_requests == ["OK"]   # 본문은 대상만


def test_missing_is_target_key_is_treated_as_non_target(tmp_path):
    # WHY: 스텝 게이트(`not record.get("is_target")`)는 False·None·**키 부재**를 한 값으로
    #      접는다. 지금 소스가 항상 플래그를 채우므로 무해하지만, 그건 **우연이지 의도가
    #      아니고** 우연은 다음 수정이 조용히 뒤집는다. 방향도 안전하지만은 않다 — 플래그가
    #      통째로 사라지면 전 행이 비대상이 되어 본문 0건인데 status=success 로 끝난다
    #      ("안 받는 쪽"이 아니라 "아무것도 안 하고 성공이라 말하는 쪽").
    #      그때 `type_matched>0` 인데 `documents_saved+reused==0` 이 사람에게 드러나는
    #      유일한 신호라, 이 취급이 의도임을 여기서 고정한다.
    rec = _rec("NOFLAG")
    del rec["is_target"]
    source = FakeSource(records=[rec])

    code, storage = _run(tmp_path, source)

    assert code == 0
    assert source.doc_requests == []       # 본문을 안 받는다
    log = _log(storage, "r1")
    assert log["records_saved"] == 1       # 메타는 남는다
    assert log["ops"]["records_out"] == 0  # 산출로는 안 센다


def test_unknown_filter_basis_is_empty_not_invented(tmp_path):
    # WHY: 이 필드의 존재 이유는 "이 런이 어느 기준이었나"의 **감사성**이다. 소스가 기준을
    #      안 주면 빈 목록이어야 한다 — 폴백이 그럴듯한 목록을 지어내면 기준 미상인 런이
    #      **거짓 기준을 찍고**, 나중에 필터를 넓혔을 때 그 런을 잘못 분류한다(모르는 것을
    #      아는 것처럼 적는 쪽이 값이 없는 것보다 나쁘다).
    #      기준을 가진 소스만 테스트하면 폴백 경로를 한 번도 안 밟는다.
    source = FakeSource(records=[_rec("A1")])
    del source.report_name_filters

    code, storage = _run(tmp_path, source)

    assert code == 0
    assert _log(storage, "r1")["report_name_filters"] == []


def test_ops_records_out_counts_targets_not_universe(tmp_path):
    # WHY: 메타를 전량 보존하게 되면서 `saved` 가 유니버스 전량이 됐는데 `failed_records` 는
    #      대상 스코프 그대로다. 둘을 섞으면 ops/entry.py 가 명문화한 "산출과 유실은 같은
    #      스코프에서 와야 한다(비대칭 금지)"가 깨져 유실 비율이 축소돼 보이고, 콘솔 그리드가
    #      실제 1건인 날 "산출 N"을 띄운다. **두 값이 서로 다른 수**여야 이 계약이 고정된다 —
    #      같으면 어느 쪽을 실었는지 이 테스트가 못 가린다.
    source = FakeSource(records=[
        _rec("KEEP"),
        _rec("S1", report_nm="주주총회소집결의", is_target=False),
        _rec("S2", report_nm="현금ㆍ현물배당결정", is_target=False),
    ])

    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["records_saved"] == 3          # 보존은 전량
    assert log["records_saved_target"] == 1   # 대상은 하나
    assert log["ops"]["records_out"] == 1     # 원장이 세는 산출 = 대상 스코프


def test_all_targets_failed_is_error_even_with_non_target_meta(tmp_path):
    # WHY: `saved == 0 → status="error"` 분기가 비대상 메타 때문에 **도달 불가**가 됐었다 —
    #      대상이 전건 실패한 런이 "일부 실패(partial)"라고 말한다. 판정 기준을 대상 스코프로
    #      되돌렸는지 확인한다. 이 어휘가 죽으면 "수집이 통째로 실패했다"를 표현할 수단이
    #      영영 없어진다(이 스텝이 status 어휘를 길게 정당화해 둔 그 어휘다).
    # 대상 행이 **소스에서** 전부 떨어진 모양 — 벤더가 rcept_no 를 드리프트시키면 실제로
    # 이렇게 된다(본문 fetch 실패는 메타를 보존하므로 이 경로가 아니다).
    # 비대상 행을 **둘** 넣어 saved(2)와 실패 건수(1)를 갈라 놓는다 — 같은 값이면 문구의
    # 숫자를 `saved` 로 바꾸는 변이가 통과한다(전체 문자열 일치도 그건 못 가린다).
    source = FakeSource(records=[
        _rec("SKIP1", report_nm="주주총회소집결의", is_target=False),
        _rec("SKIP2", report_nm="현금ㆍ현물배당결정", is_target=False),
    ])
    source.fetch_failures = [
        {"symbol": "005930", "rcept_no": None, "error": "대상 공시인데 rcept_no 결측/비문자열"},
    ]

    code, storage = _run(tmp_path, source)

    assert code == 1
    log = _log(storage, "r1")
    assert log["records_saved"] == 2          # 비대상 메타는 저장됐다
    assert log["records_saved_target"] == 0   # 대상은 하나도 못 건졌다
    assert log["status"] == "error"           # → partial 이 아니라 error 다
    # 문구도 단언한다 — 실패 목록엔 유형 판정 앞에서 잡히는 남의 행도 섞이므로 "모든 대상
    # 실패"는 대상 0건인 정상 날에 거짓이 된다. 두 사실(대상 저장 0 · 실패 N)을 그대로 적는다.
    assert log["error"] == "대상 저장 0건 · 실패 1건"


def test_attenuation_axes_recorded_in_collection_log(tmp_path):
    # WHY: 두 감쇠 축(유니버스·유형)은 소스가 세고 스텝은 옮기기만 하는데, 옮기는 자리가
    #      끊겨도 다른 테스트는 전부 통과한다(판정에 안 쓰는 관측값이라 게이트가 없다).
    #      list_rows_seen 과 **다른 값**으로 넣어 세 축이 각각 제 자리에 실리는지 고정한다.
    # 필터 목록도 **기본값이 아닌 값**으로 넘긴다 — 기대값이 픽스처 기본과 같으면
    # `getattr` 폴백을 그 목록으로 바꾸거나 소스를 안 읽고 상수를 박아도 통과한다
    # (다른 축에 이미 쓰는 원칙인데 이 축에만 빠져 있었다).
    source = FakeSource(records=[_rec("A1")], list_rows_seen=867,
                        universe_matched=97, type_matched=1, dropped_malformed=5,
                        report_name_filters=["공급계약", "사업보고서", "배당"])

    code, storage = _run(tmp_path, source)

    assert code == 0
    log = _log(storage, "r1")
    assert log["list_rows_seen"] == 867
    assert log["universe_matched"] == 97
    assert log["type_matched"] == 1
    # "버리되 0건 아님"(Rule 12)이 이 수정의 요지인데, 그 수치가 로그까지 가는 경로도
    # 같은 무증 지대에 있다 — 상수 0 으로 박아도 나머지는 전부 초록이었다. 다른 셋과
    # **다른 값**이라야 어느 필드를 실었는지 이 단언이 가린다.
    assert log["rows_dropped_malformed"] == 5
    # `is_target` 을 정한 기준도 함께 — 없으면 나중에 필터를 넓혔을 때 어느 런이 어느
    # 기준이었는지 복원되지 않아 "보존해 뒀다가 재파싱"이라는 이 티켓의 전제가 깨진다.
    assert log["report_name_filters"] == ["공급계약", "사업보고서", "배당"]


def test_log_carries_ingest_lane_and_list_truncated(tmp_path):
    # WHY: 두 레인이 같은 collection_log 프리픽스를 쓰고 run_id 접두는 규약이 아니라,
    #      이 두 필드가 없으면 배치 워터마크(ALPHA-987)가 1분 레인 로그를 배치 완주로
    #      오인한다. "필드 부재 = 워터마크 자격 없음" 규칙의 전제가 "새 로그엔 반드시
    #      있다"이므로, 이 진입점(run = 배치 레인)이 두 값을 로그에 싣는지 고정한다.
    code, storage = _run(tmp_path, FakeSource(records=[_rec("A1")]))
    assert code == 0
    log = _log(storage, "r1")
    assert log["ingest_lane"] == "batch"
    assert log["list_truncated"] is False

    # 절단이 실제로 로그에 실리는지 — 상수 False 를 박아도 위만으로는 초록이다.
    truncated = FakeSource(records=[_rec("A1")])
    truncated._segment_truncated = True
    _, storage2 = _run(tmp_path, truncated, storage=LocalStorage(tmp_path / "lake2"),
                       run_id="r2")
    assert _log(storage2, "r2")["list_truncated"] is True

    # minute 레인이 로그에 그대로 실리는지 — 상수 "batch" 를 박아도 위만으로는 초록이다.
    storage3 = LocalStorage(tmp_path / "lake3")
    ingest_raw_disclosure.collect(
        _settings(tmp_path), storage3, FakeSource(records=[_rec("A1")]), "r3",
        ingest_lane="minute",
    )
    assert _log(storage3, "r3")["ingest_lane"] == "minute"


def test_window_meta_lands_in_collection_log(tmp_path):
    # WHY: 창 결정 관측(window_source 등)은 run 엔트리가 정하고 이 스텝은 받아 적는다 —
    #      이 전달이 끊겨도 수집은 멀쩡히 돌아서, 그림자 대조가 조용히 사라진 채 두 달 반
    #      뒤 컷오버 검증 근거가 없는 걸 그때야 알게 된다(ALPHA-987 완료 조건).
    storage = LocalStorage(tmp_path / "lake")
    code = ingest_raw_disclosure.run(
        _settings(tmp_path), storage, FakeSource(records=[_rec("A1")]), "r1",
        window_meta={"window_source": "watermark", "recovered_days": 2},
    )
    assert code == 0
    log = _log(storage, "r1")
    assert log["window_source"] == "watermark"
    assert log["recovered_days"] == 2

    # 임의 dict 가 로그 정체성(run_id·ingest_lane 등)을 덮지 못한다 — 워터마크 술어와
    # run_id 기반 로그 소비가 그 정체성을 믿는다(명시 필드가 병합보다 뒤 = 항상 이긴다).
    storage2 = LocalStorage(tmp_path / "lake2")
    ingest_raw_disclosure.run(
        _settings(tmp_path), storage2, FakeSource(records=[_rec("A1")]), "r2",
        window_meta={"ingest_lane": "minute", "run_id": "evil"},
    )
    log2 = _log(storage2, "r2")
    assert log2["ingest_lane"] == "batch" and log2["run_id"] == "r2"


def test_skipped_log_still_carries_lane_fields(tmp_path):
    # WHY: "필드 부재 = PR1 이전 로그" 판별은 **새 로그 전부**에 두 필드가 있어야 성립한다.
    #      skip 경로만 빠지면 키 미주입기의 새 로그가 레거시로 오인돼 판별자가 둘로 갈린다.
    code, storage = _run(tmp_path, FakeSource(enabled=False))
    assert code == 0
    log = _log(storage, "r1")
    assert log["status"] == "skipped"
    assert log["ingest_lane"] == "batch"
    assert log["list_truncated"] is False


def test_unknown_ingest_lane_rejected(tmp_path):
    # WHY: 오타난 레인이 로그에 실리면 수집은 성공하는데 워터마크는 그 런을 어느 레인에도
    #      귀속하지 못한다 — 생산자에서 죽어야(Rule 12) 오기록이 로그로 새지 않는다.
    with pytest.raises(ValueError, match="ingest_lane"):
        ingest_raw_disclosure.collect(
            _settings(tmp_path), LocalStorage(tmp_path / "lake"),
            FakeSource(records=[_rec("A1")]), "r1", ingest_lane="minuite",
        )
