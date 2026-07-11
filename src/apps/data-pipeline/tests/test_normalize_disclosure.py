"""normalize_disclosure 스텝 테스트 — 공급계약 본문 파싱 + doc_type 라우팅 + 게이트 +
canonical 멱등 병합 + quality_log (ALPHA-345).

각도 H: malformed 입력(비객체 행·본문 결측·테이블 없음·비날짜 rcept_dt)이 crash 없이
사유와 함께 격리되고 게이트를 우회하지 않는지. Rule 9: 멱등·최신우선 병합의 WHY 를 고정.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from data_pipeline.lake import (
    LocalStorage,
    canonical_supply_contract_fact_partition,
    raw_disclosure_document_key,
    raw_disclosure_partition,
)
from data_pipeline.steps import normalize_disclosure

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "disclosure"

SOURCE = "dart"
MARKET = "KR"
INGEST_DATE = "2026-06-23"


def _doc_zip(html: str, rcept_no: str, *, encoding: str = "euc-kr") -> bytes:
    """document.xml ZIP 모사 — 내부 {rcept_no}.xml 에 euc-kr HTML 을 담는다(실측 형태)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"{rcept_no}.xml", html.encode(encoding))
    return buf.getvalue()


def _supply_record(rcept_no: str, **over) -> dict:
    """공급계약 raw 메타 행(list.json 행 + ingest provenance)."""
    rec = {
        "report_nm": "단일판매ㆍ공급계약체결              ",  # 실측 패딩·ㆍ 포함
        "rcept_no": rcept_no,
        "corp_code": "00406727",
        "corp_name": "테스트기업",
        "stock_code": "123456",
        "our_ticker": "123456",
        "rcept_dt": "20260623",
        "market": MARKET,
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        "fetched_at": "2026-06-23T00:00:00+00:00",
        "body_format": "zip/html;charset=euc-kr",
    }
    rec.update(over)
    return rec


def _supply_html(counterparty="한화에어로스페이스(주)", amount="1,200,000,000원",
                 ratio="12.5", corp="테스트기업") -> str:
    return f"""
    <html>
      <head><title>{corp}/단일판매ㆍ공급계약체결</title></head>
      <body>
        <table>
          <tr><td>계약상대방</td><td>{counterparty}</td></tr>
          <tr><td>체결계약명</td><td>샘플 공급계약</td></tr>
          <tr><td>계약금액</td><td>{amount}</td></tr>
          <tr><td>매출액 대비</td><td>{ratio}</td></tr>
          <tr><td>계약기간</td><td>2024.01.02 ~ 2025.03.04</td></tr>
        </table>
      </body>
    </html>
    """


def _write_run(storage, records_and_bodies, *, run_id="R1", ingest_date=INGEST_DATE):
    """(record, body_bytes|None) 목록을 raw 메타 ndjson + 문서 ZIP 으로 적재한다.
    body_bytes 가 있으면 document_raw_path 를 채워 메타↔본문을 잇는다(ingest 규약 모사)."""
    meta_rows = []
    for record, body in records_and_bodies:
        if body is not None:
            doc_key = raw_disclosure_document_key(
                SOURCE, MARKET, ingest_date, run_id, record["rcept_no"]
            )
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
    prefix = canonical_supply_contract_fact_partition(report_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_disclosure._read_parquet_rows(storage.get_bytes(key)))
    return rows


# ── 핵심 경로 ────────────────────────────────────────────
def test_real_fixture_parses_and_lands_in_canonical(tmp_path):
    # WHY: 정제의 존재 이유 — raw 본문(euc-kr HTML)을 파싱해 공통 fact 로 만들고, 메타
    #      provenance(rcept_no·ticker·report_date)를 조인해 canonical 에 놓는다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623900750"
    html = (FIXTURES_DIR / "supply_fiberpro_20260623900750.html").read_text(encoding="utf-8")
    rec = _supply_record(rcept_no, corp_name="파이버프로", our_ticker="368770")
    _write_run(storage, [(rec, _doc_zip(html, rcept_no, encoding="utf-8"))])

    assert normalize_disclosure.run(storage, "D1") == 0
    rows = _canonical_rows(storage, "2026-06-23")
    assert len(rows) == 1
    (row,) = rows
    assert row["rcept_no"] == rcept_no
    assert row["ticker"] == "368770"
    assert row["corp_name"] == "파이버프로"  # raw 메타 provenance 권위
    assert row["counterparty"] == "한화에어로스페이스(주)"
    assert row["amount_krw"] == 17_899_464_000
    assert row["ratio_pct"] == 92.33
    assert row["report_date"] == "2026-06-23"
    assert row["parser_version"] == "supply-v1"
    assert row["counterparty_withheld"] is False

    log = _quality_log(storage)
    assert (log["records_routed_supply"], log["records_passed"], log["records_failed"]) == (1, 1, 0)


def test_synthetic_supply_euckr_body_passes(tmp_path):
    # WHY: 실측상 본문은 euc-kr — utf-8 우선·cp949 폴백 디코딩이 파이프라인에서 동작해야 한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800001"
    _write_run(storage, [(_supply_record(rcept_no), _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 0
    rows = _canonical_rows(storage, "2026-06-23")
    assert len(rows) == 1
    assert rows[0]["amount_krw"] == 1_200_000_000
    assert rows[0]["ratio_pct"] == 12.5


# ── doc_type 라우팅 ──────────────────────────────────────
def test_non_supply_report_is_skipped_not_failed(tmp_path):
    # WHY: 공급계약만 이 스텝 소관 — 사업보고서 등은 스킵(실패 아님, segment fact 는 후속).
    storage = LocalStorage(tmp_path / "lake")
    rec = _supply_record("20260623800002", report_nm="사업보고서 (2025.12)")
    _write_run(storage, [(rec, _doc_zip(_supply_html(), "20260623800002"))])

    assert normalize_disclosure.run(storage, "D1") == 0
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_skipped_type"] == 1
    assert log["records_routed_supply"] == 0
    assert log["records_failed"] == 0


def test_gijae_jeongjeong_prefix_still_routes_as_supply(tmp_path):
    # WHY: 정정 공시는 [기재정정] 접두가 붙지만 여전히 공급계약이다 — 부분일치가 이를 잡아야 한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800003"
    rec = _supply_record(rcept_no, report_nm="[기재정정]단일판매ㆍ공급계약체결")
    _write_run(storage, [(rec, _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 0
    assert len(_canonical_rows(storage, "2026-06-23")) == 1


# ── 각도 H: malformed 격리 ───────────────────────────────
def test_missing_document_body_is_failure_not_crash(tmp_path):
    # WHY: 수집 시 본문 fetch 실패로 document_raw_path 가 없는 공급계약 — canonical 못 넣고
    #      사유로 격리한다(crash 없이 quality_log 에 남김).
    storage = LocalStorage(tmp_path / "lake")
    rec = _supply_record("20260623800004", document_raw_path=None)
    _write_run(storage, [(rec, None)])

    assert normalize_disclosure.run(storage, "D1") == 0
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["missing_document_body"]


def test_non_object_row_is_isolated(tmp_path):
    # WHY: 유효 JSON 이지만 비객체 행(배열·스칼라)은 record.get 에서 런을 죽인다 — 행 단위 격리.
    storage = LocalStorage(tmp_path / "lake")
    key = f"{raw_disclosure_partition(SOURCE, MARKET, INGEST_DATE, 'R1')}/part-00000.ndjson"
    storage.put_bytes(key, (json.dumps([1, 2, 3]) + "\n").encode("utf-8"))

    assert normalize_disclosure.run(storage, "D1") == 0
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["non_object_row"]


def test_empty_parse_body_is_blocked(tmp_path):
    # WHY: 테이블 없는 malformed 본문 — 계약을 하나도 못 뽑으면 게이트가 empty_parse 로 막는다
    #      (coerce-to-passing 방지). crash 없이 사유로 격리.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800005"
    body = _doc_zip("<html><head><title>x/공급</title></head><body>표없음</body></html>", rcept_no)
    _write_run(storage, [(_supply_record(rcept_no), body)])

    assert normalize_disclosure.run(storage, "D1") == 0
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert "empty_parse" in log["failures"][0]["reasons"]


def test_bad_rcept_dt_is_blocked(tmp_path):
    # WHY: 비달력일 rcept_dt('20260231')는 report_date 파티션을 못 만든다 — bad_report_date 로 막음.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800006"
    rec = _supply_record(rcept_no, rcept_dt="20260231")
    _write_run(storage, [(rec, _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert "missing_report_date" in log["failures"][0]["reasons"]


def test_withheld_counterparty_passes_with_warning(tmp_path):
    # WHY: 계약상대방 유보는 정상 공시 — 통과시키되 경고로 드러낸다(탈락 아님).
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800007"
    body = _doc_zip(_supply_html(counterparty="경영상 비밀유지 요청에 따른 공시유보"), rcept_no)
    _write_run(storage, [(_supply_record(rcept_no), body)])

    assert normalize_disclosure.run(storage, "D1") == 0
    rows = _canonical_rows(storage, "2026-06-23")
    assert len(rows) == 1
    assert rows[0]["counterparty_withheld"] is True
    log = _quality_log(storage)
    assert log["records_passed"] == 1
    assert log["records_warned"] == 1
    assert "withheld_counterparty" in log["warnings"][0]["reasons"]


# ── 멱등·최신우선 병합 ───────────────────────────────────
def test_idempotent_rerun_stable_bytes(tmp_path):
    # WHY: canonical 은 run_id 없이 멱등이어야 한다 — 같은 raw 재정제 시 파티션 바이트가 안정.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800008"
    _write_run(storage, [(_supply_record(rcept_no), _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 0
    key = f"{canonical_supply_contract_fact_partition('2026-06-23')}/part-00000.parquet"
    first = storage.get_bytes(key)
    assert normalize_disclosure.run(storage, "D2") == 0
    assert storage.get_bytes(key) == first


def test_latest_fetched_at_wins_on_reingest(tmp_path):
    # WHY: 같은 rcept_no 재수집(정정본)은 최신 fetched_at 이 대표가 된다(정정 반영).
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800009"
    old = _supply_record(rcept_no, fetched_at="2026-06-23T00:00:00+00:00")
    new = _supply_record(rcept_no, fetched_at="2026-06-24T00:00:00+00:00")
    _write_run(storage, [(old, _doc_zip(_supply_html(ratio="10.0"), rcept_no))], run_id="R1")
    _write_run(storage, [(new, _doc_zip(_supply_html(ratio="20.0"), rcept_no))],
               run_id="R2", ingest_date="2026-06-24")

    assert normalize_disclosure.run(storage, "D1") == 0
    rows = _canonical_rows(storage, "2026-06-23")
    assert len(rows) == 1
    assert rows[0]["ratio_pct"] == 20.0  # 최신 fetched_at 이 이김


# ── 스코프 실행 ──────────────────────────────────────────
def test_scoped_run_revalidates_without_writing_canonical(tmp_path):
    # WHY: --input-run-id 스코프는 재검증(quality_log)만 — canonical 은 전체 런이 authoritative.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800010"
    _write_run(storage, [(_supply_record(rcept_no), _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1", input_run_id="R1") == 0
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["canonical_written"] is False
    assert log["records_passed"] == 1
