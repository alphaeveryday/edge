"""normalize_disclosure_segment 스텝 테스트 — 사업보고서 사업부문 파싱 + doc_type 라우팅 +
게이트 + canonical 멱등 병합 (ALPHA-346).

1 문서 → N 부문(fan-out), 행키 (rcept_no, segment_ordinal). 각도 H: malformed 격리(비객체·본문
결측·표 없음). Rule 9: 멱등·ordinal 키의 WHY 고정.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from data_pipeline.lake import (
    LocalStorage,
    canonical_business_segment_fact_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    raw_disclosure_document_key,
    raw_disclosure_partition,
)
from data_pipeline.steps import normalize_disclosure_segment as seg

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "disclosure"
PHARMA_HTML = (FIXTURES_DIR / "segments_pharmaresearch_20260319.html").read_text(encoding="utf-8")

SOURCE = "dart"
MARKET = "KR"
INGEST_DATE = "2026-03-19"


def _doc_zip(html: str, rcept_no: str, *, encoding: str = "utf-8") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"{rcept_no}.xml", html.encode(encoding))
    return buf.getvalue()


def _report_record(rcept_no: str, **over) -> dict:
    rec = {
        "report_nm": "사업보고서 (2025.12)",
        "rcept_no": rcept_no,
        "corp_code": "00123456",
        "corp_name": "파마리서치",
        "stock_code": "214450",
        "our_ticker": "214450",
        "rcept_dt": "20260319",
        "market": MARKET,
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        "fetched_at": "2026-03-19T00:00:00+00:00",
        "body_format": "zip/html;charset=euc-kr",
    }
    rec.update(over)
    return rec


def _write_run(storage, records_and_bodies, *, run_id="R1", ingest_date=INGEST_DATE):
    meta_rows = []
    for record, body in records_and_bodies:
        if body is not None:
            doc_key = raw_disclosure_document_key(SOURCE, MARKET, ingest_date, run_id, record["rcept_no"])
            storage.put_bytes(doc_key, body)
            record = {**record, "document_raw_path": doc_key}
        meta_rows.append(record)
    key = f"{raw_disclosure_partition(SOURCE, MARKET, ingest_date, run_id)}/part-00000.ndjson"
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in meta_rows)
    storage.put_bytes(key, body.encode("utf-8"))


def _quality_log(storage) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _canonical_rows(storage, report_date: str) -> list[dict]:
    prefix = canonical_business_segment_fact_partition(report_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(seg._read_parquet_rows(storage.get_bytes(key)))
    return rows


def _manifest(storage, run_id: str) -> dict:
    return json.loads(storage.get_bytes(
        canonical_run_manifest_key("business_segment_fact", run_id)
    ).decode("utf-8"))


def test_backfill_window_filters_business_reports_by_filing_date(tmp_path):
    """사업부문 백필도 공급계약과 같은 접수일 창을 써야 두 canonical dataset의 범위가
    갈리지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    old = _report_record("20260318000001", rcept_dt="20260318")
    current = _report_record("20260319000001", rcept_dt="20260319")
    _write_run(storage, [(old, _doc_zip(PHARMA_HTML, old["rcept_no"])),
                         (current, _doc_zip(PHARMA_HTML, current["rcept_no"]))])

    assert seg.run(storage, "B1", from_date="2026-03-19", to_date="2026-03-19") == 0

    assert _canonical_rows(storage, "2026-03-18") == []
    assert len(_canonical_rows(storage, "2026-03-19")) == 4
    assert _quality_log(storage)["records_skipped_window"] == 1


def test_business_report_fans_out_to_segment_facts(tmp_path):
    # WHY: 정제의 존재 이유 — 사업보고서 본문(표)을 파싱해 부문당 fact 로 펼치고 provenance 조인.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000001"
    _write_run(storage, [(_report_record(rcept_no), _doc_zip(PHARMA_HTML, rcept_no))])

    assert seg.run(storage, "S1") == 0
    rows = _canonical_rows(storage, "2026-03-19")
    assert [r["segment_name"] for r in rows] == ["의약품", "의료기기", "화장품", "기타"]
    assert [r["segment_ordinal"] for r in rows] == [0, 1, 2, 3]
    assert all(r["rcept_no"] == rcept_no and r["source_vendor"] == "dart" for r in rows)
    assert all(r["ticker"] == "214450" and r["corp_name"] == "파마리서치" for r in rows)
    assert all(r["share_basis"] == "reported" and r["parser_version"] == "segments-v3" for r in rows)
    log = _quality_log(storage)
    assert (log["records_routed_business_report"], log["records_passed"]) == (1, 4)
    assert log["segments_extracted"] == 4


def test_supply_report_is_skipped(tmp_path):
    # WHY: 사업부문만 이 스텝 소관 — 공급계약체결은 normalize-disclosure 소관이라 스킵.
    storage = LocalStorage(tmp_path / "lake")
    rec = _report_record("20260319000002", report_nm="단일판매ㆍ공급계약체결")
    _write_run(storage, [(rec, _doc_zip(PHARMA_HTML, "20260319000002"))])

    assert seg.run(storage, "S1") == 0
    assert _canonical_rows(storage, "2026-03-19") == []
    log = _quality_log(storage)
    assert log["records_skipped_type"] == 1 and log["records_routed_business_report"] == 0


def test_no_segment_table_is_isolated_failure(tmp_path):
    # WHY: 각도 H — 사업부문 표가 없는 본문은 crash 없이 no_segments_parsed 로 격리(canonical 제외).
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000003"
    body = _doc_zip("<html><body><p>사업의 내용 없음</p></body></html>", rcept_no)
    _write_run(storage, [(_report_record(rcept_no), body)])

    assert seg.run(storage, "S1") == 2
    assert _canonical_rows(storage, "2026-03-19") == []
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["no_segments_parsed"]


def test_non_object_row_is_isolated(tmp_path):
    # WHY: 비객체 raw 행이 배치를 죽이지 않고 행 단위로 격리된다(각도 H).
    storage = LocalStorage(tmp_path / "lake")
    key = f"{raw_disclosure_partition(SOURCE, MARKET, INGEST_DATE, 'R1')}/part-00000.ndjson"
    storage.put_bytes(key, (json.dumps("scalar") + "\n").encode("utf-8"))

    assert seg.run(storage, "S1") == 2
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["non_object_row"]


def test_duplicate_segment_names_keyed_by_ordinal(tmp_path):
    # WHY: 한 문서에 같은 segment_name 이 여러 번(제품/용역 sub-row) 나올 수 있어 (rcept_no,
    #      segment_name)는 행키가 못 된다 — 파스 순서 ordinal 로 키를 잡아 모든 부문을 보존한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000004"
    html = """
    <p>연결 기준 주요 제품 등의 현황</p>
    <table>
      <tr><th>사업부문</th><th>매출유형</th><th>품목</th><th>매출액</th><th>비율</th></tr>
      <tr><td>바이오의약품</td><td>제품</td><td>바이오의약품 등</td><td>932</td><td>93.2</td></tr>
      <tr><td>바이오의약품</td><td>용역</td><td>서비스 등</td><td>1</td><td>0.1</td></tr>
      <tr><td>케미컬의약품</td><td>제품</td><td>케미컬의약품 등</td><td>65</td><td>6.5</td></tr>
      <tr><td>케미컬의약품</td><td>용역</td><td>기타 서비스</td><td>2</td><td>0.2</td></tr>
    </table>
    """
    _write_run(storage, [(_report_record(rcept_no), _doc_zip(html, rcept_no))])

    assert seg.run(storage, "S1") == 0
    rows = _canonical_rows(storage, "2026-03-19")
    assert [r["segment_name"] for r in rows] == ["바이오의약품", "바이오의약품", "케미컬의약품", "케미컬의약품"]
    assert [r["segment_ordinal"] for r in rows] == [0, 1, 2, 3]  # 중복 이름도 ordinal 로 구분 보존


def test_idempotent_rerun_stable_bytes(tmp_path):
    # WHY: canonical 은 멱등 — 같은 raw 재정제 시 파티션 바이트가 안정(run_id 없음).
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000005"
    _write_run(storage, [(_report_record(rcept_no), _doc_zip(PHARMA_HTML, rcept_no))])

    assert seg.run(storage, "S1") == 0
    key = f"{canonical_business_segment_fact_partition('2026-03-19')}/part-00000.parquet"
    first = storage.get_bytes(key)
    assert seg.run(storage, "S2") == 0
    assert storage.get_bytes(key) == first


def test_scoped_run_writes_canonical(tmp_path):
    # WHY: SFN 이 --input-run-id 로 도는 경로다(ALPHA-389) — 그 런의 raw 만 읽되 canonical 은
    #      쓴다. 안 쓰면 파이프라인이 아무것도 적재하지 못한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000006"
    _write_run(storage, [(_report_record(rcept_no), _doc_zip(PHARMA_HTML, rcept_no))])

    assert seg.run(storage, "S1", input_run_id="R1") == 0
    assert len(_canonical_rows(storage, "2026-03-19")) == 4
    log = _quality_log(storage)
    assert log["canonical_written"] is True and log["records_passed"] == 4


def test_manifest_records_segment_winners_with_direct_key_and_sha(tmp_path):
    # WHY(ALPHA-1044): 사업부문은 rcept_no 하나가 여러 fact로 fan-out하므로 ordinal까지 winner
    # 정체성에 있어야 하며, direct key와 SHA가 없으면 하류가 다시 prefix를 추측·LIST해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000020"
    _write_run(storage, [(_report_record(rcept_no), _doc_zip(PHARMA_HTML, rcept_no))])

    assert seg.run(storage, "S1", input_run_id="R1") == 0
    part = _manifest(storage, "S1")["canonical_partitions"][0]
    assert part["winner_ids"] == [
        {"rcept_no": rcept_no, "segment_ordinal": ordinal} for ordinal in range(4)
    ]
    assert part["key"] == canonical_run_partition_key(
        "business_segment_fact", "S1", "2026-03-19")
    assert part["sha256"] == hashlib.sha256(storage.get_bytes(part["key"])).hexdigest()


def test_partial_segment_failure_keeps_completed_empty_manifest(tmp_path):
    # WHY(ALPHA-1044): 사업부문 파싱 실패가 공급계약 manifest와 뒤섞이지 않아야 한다. 이
    # dataset은 성공 winner 0건을 완료로 확정하면서 종료 코드 2로 전체 window만 INCOMPLETE다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260319000021"
    _write_run(storage, [(_report_record(rcept_no), _doc_zip("<html/>", rcept_no))])

    assert seg.run(storage, "S1") == 2
    assert _manifest(storage, "S1")["canonical_written"] is True
    assert _manifest(storage, "S1")["canonical_partitions"] == []
