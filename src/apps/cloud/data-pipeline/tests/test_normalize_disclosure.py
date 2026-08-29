"""normalize_disclosure 스텝 테스트 — 공급계약 본문 파싱 + doc_type 라우팅 + 게이트 +
canonical 멱등 병합 + quality_log (ALPHA-345).

각도 H: malformed 입력(비객체 행·본문 결측·테이블 없음·비날짜 rcept_dt)이 crash 없이
사유와 함께 격리되고 게이트를 우회하지 않는지. Rule 9: 멱등·최신우선 병합의 WHY 를 고정.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from data_pipeline.lake import (
    LocalStorage,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    canonical_supply_contract_fact_partition,
    raw_disclosure_document_key,
    raw_disclosure_partition,
)
from data_pipeline.steps import disclosure_raw_manifest, normalize_disclosure

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
    storage.put_bytes(
        disclosure_raw_manifest.key(run_id),
        disclosure_raw_manifest.bytes_for(run_id, True, [key]),
    )


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


def _manifest(storage, run_id: str) -> dict:
    return json.loads(storage.get_bytes(
        canonical_run_manifest_key("supply_contract_fact", run_id)
    ).decode("utf-8"))


class _FailCompletedManifestStorage:
    """완료 marker PUT만 실패시키고 incomplete 무효화는 허용한다."""

    def __init__(self, inner, manifest_key: str):
        self.inner = inner
        self.manifest_key = manifest_key

    def list_keys(self, prefix):
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        if key == self.manifest_key and json.loads(data).get("canonical_written") is True:
            raise OSError("completed manifest write failed")
        return self.inner.put_bytes(key, data)


def test_backfill_window_filters_raw_by_filing_date(tmp_path):
    """기간 백필이 raw 전체를 canonical로 다시 쓰면 요청 밖 정정본까지 함께 반영된다.
    --from/--to는 수집일이 아니라 DART 접수일을 inclusive로 제한해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    old = _supply_record("20260622900001", rcept_dt="20260622")
    current = _supply_record("20260623900001", rcept_dt="20260623")
    _write_run(storage, [(old, _doc_zip(_supply_html(), old["rcept_no"])),
                         (current, _doc_zip(_supply_html(), current["rcept_no"]))])

    assert normalize_disclosure.run(
        storage, "B1", from_date="2026-06-23", to_date="2026-06-23") == 0

    assert _canonical_rows(storage, "2026-06-22") == []
    assert [row["rcept_no"] for row in _canonical_rows(storage, "2026-06-23")] == [
        "20260623900001"]
    assert _quality_log(storage)["records_skipped_window"] == 1


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
    assert row["source_vendor"] == "dart"  # provenance 컬럼(파티션 아님) — 감사·다소스 대비
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


def test_termination_filing_is_not_routed_as_supply(tmp_path):
    # WHY: raw 필터가 "공급계약" 부분일치라 해지(공급계약해지)도 raw 에 들어온다 — 라우팅이
    #      "체결" 을 요구하지 않으면 해지가 새 계약 fact 로 오적재돼 체결과 구분 불가(Codex P2).
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800014"
    rec = _supply_record(rcept_no, report_nm="단일판매ㆍ공급계약해지")
    _write_run(storage, [(rec, _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 0
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_skipped_type"] == 1
    assert log["records_routed_supply"] == 0


def test_correction_and_original_both_project_as_per_filing_facts(tmp_path):
    # WHY: 정정 supersession(point-in-time)은 이 스텝 범위 밖 — 원본과 정정본([기재정정]…체결)은
    #      서로 다른 rcept_no 라 각각 파일링당 fact 로 남는다(정정↔원본 collapse 는 링크 데이터가
    #      필요한 정체성 해소/SCD 문제라 후속 소관). 이 스텝은 파일링당 fact 를 충실히 투영한다.
    storage = LocalStorage(tmp_path / "lake")
    original = _supply_record("20260623800020", rm="유")
    correction = _supply_record("20260624800021", report_nm="[기재정정]단일판매ㆍ공급계약체결",
                                rcept_dt="20260624", fetched_at="2026-06-24T00:00:00+00:00")
    _write_run(storage, [(original, _doc_zip(_supply_html(), "20260623800020"))], run_id="R1")
    _write_run(storage, [(correction, _doc_zip(_supply_html(), "20260624800021"))],
               run_id="R2", ingest_date="2026-06-24")

    assert normalize_disclosure.run(storage, "D1") == 0
    assert [r["rcept_no"] for r in _canonical_rows(storage, "2026-06-23")] == ["20260623800020"]
    assert [r["rcept_no"] for r in _canonical_rows(storage, "2026-06-24")] == ["20260624800021"]
    log = _quality_log(storage)
    assert log["records_routed_supply"] == 2


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

    assert normalize_disclosure.run(storage, "D1") == 2
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["missing_document_body"]


def test_non_object_row_is_isolated(tmp_path):
    # WHY: 유효 JSON 이지만 비객체 행(배열·스칼라)은 record.get 에서 런을 죽인다 — 행 단위 격리.
    storage = LocalStorage(tmp_path / "lake")
    key = f"{raw_disclosure_partition(SOURCE, MARKET, INGEST_DATE, 'R1')}/part-00000.ndjson"
    storage.put_bytes(key, (json.dumps([1, 2, 3]) + "\n").encode("utf-8"))

    assert normalize_disclosure.run(storage, "D1") == 2
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

    assert normalize_disclosure.run(storage, "D1") == 2
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_failed"] == 1
    assert "empty_parse" in log["failures"][0]["reasons"]


def test_oversized_amount_does_not_kill_canonical_batch(tmp_path):
    # WHY: 각도 H — int64 초과 금액(단위 곱)은 게이트가 blocking 으로 막아야 한다. 안 막으면
    #      passed 로 인증된 그 한 행이 pyarrow int64 적재에서 OverflowError 로 같은 런의 정상
    #      행 canonical 적재 전체를 죽인다(비원자적 부분 쓰기). poison 행은 격리되고 정상 행은
    #      canonical 에 남아야 한다.
    storage = LocalStorage(tmp_path / "lake")
    poison = "20260623800011"
    good = "20260623800012"
    _write_run(storage, [
        (_supply_record(poison), _doc_zip(_supply_html(amount="10000000조원"), poison)),
        (_supply_record(good), _doc_zip(_supply_html(amount="1,200,000,000원"), good)),
    ])

    assert normalize_disclosure.run(storage, "D1") == 2
    rows = _canonical_rows(storage, "2026-06-23")
    assert {r["rcept_no"] for r in rows} == {good}  # 정상 행만 적재, poison 격리
    log = _quality_log(storage)
    assert log["records_passed"] == 1
    assert log["records_failed"] == 1
    assert "amount_out_of_range" in log["failures"][0]["reasons"]


def test_malformed_report_nm_is_failure_not_silent_skip(tmp_path):
    # WHY: 각도 H — 비문자열 report_nm(오염 메타)은 라우팅 판정 불가다. skipped_type(정상 비대상)
    #      으로 침묵 흡수하면 malformed 공급계약이 사유 없이 유실된다 — 사유로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800013"
    rec = _supply_record(rcept_no, report_nm=None)
    _write_run(storage, [(rec, _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 2
    assert _canonical_rows(storage, "2026-06-23") == []
    log = _quality_log(storage)
    assert log["records_skipped_type"] == 0
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["malformed_report_nm"]


def test_bad_rcept_dt_is_blocked(tmp_path):
    # WHY: 비달력일 rcept_dt('20260231')는 report_date 파티션을 못 만든다 — bad_report_date 로 막음.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800006"
    rec = _supply_record(rcept_no, rcept_dt="20260231")
    _write_run(storage, [(rec, _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 2
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
def test_scoped_run_writes_canonical(tmp_path):
    # WHY: SFN 이 --input-run-id 로 도는 경로다(ALPHA-389) — 그 런의 raw 만 읽되 canonical 은
    #      쓴다. 안 쓰면 파이프라인이 아무것도 적재하지 못한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800010"
    _write_run(storage, [(_supply_record(rcept_no), _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1", input_run_id="R1") == 0
    assert [r["rcept_no"] for r in _canonical_rows(storage, "2026-06-23")] == [rcept_no]
    log = _quality_log(storage)
    assert log["canonical_written"] is True
    assert log["records_passed"] == 1


def test_manifest_records_current_winner_with_direct_key_and_sha(tmp_path):
    # WHY(ALPHA-1044): 하류가 날짜 prefix를 LIST하지 않으려면 producer가 직접 parquet key와
    # 무결성, 이번 실행의 논리 winner를 함께 확정해야 한다. 같은 값 재확정도 변경분이 아니라
    # 현재 winner이므로 manifest에서 빠지면 안 된다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800030"
    record = _supply_record(rcept_no)
    body = _doc_zip(_supply_html(), rcept_no)
    _write_run(storage, [(record, body), (record, body)])

    assert normalize_disclosure.run(storage, "D1", input_run_id="R1") == 0
    manifest = _manifest(storage, "D1")
    assert manifest["producer"] == "normalize_disclosure"
    assert manifest["canonical_written"] is True
    assert len(manifest["canonical_partitions"]) == 1
    part = manifest["canonical_partitions"][0]
    assert part["winner_ids"] == [{"rcept_no": rcept_no}]
    assert part["key"] == canonical_run_partition_key(
        "supply_contract_fact", "D1", "2026-06-23")
    assert part["sha256"] == hashlib.sha256(storage.get_bytes(part["key"])).hexdigest()
    assert _quality_log(storage)["ops"]["records_out"] == 1


def test_empty_run_commits_empty_manifest_and_same_run_is_stable(tmp_path):
    # WHY(ALPHA-1044): 0건 정상 실행도 "미실행"과 구분되는 완료 증거여야 하고, 같은 run 재시도는
    # 동일 바이트를 남겨야 consumer가 빈 완료를 오류나 새 범위로 오독하지 않는다.
    storage = LocalStorage(tmp_path / "lake")

    assert normalize_disclosure.run(storage, "D1") == 0
    key = canonical_run_manifest_key("supply_contract_fact", "D1")
    first = storage.get_bytes(key)
    assert _manifest(storage, "D1")["canonical_partitions"] == []
    assert _manifest(storage, "D1")["canonical_written"] is True
    assert normalize_disclosure.run(storage, "D1") == 0
    assert storage.get_bytes(key) == first


def test_partial_failure_preserves_success_manifest_and_returns_2(tmp_path):
    # WHY(ALPHA-1044): 한 공시 실패 때문에 같은 run의 정상 winner 계보까지 폐기하면 후속 ledger가
    # 성공분을 회수할 수 없다. 성공 manifest는 완료하되 worker에는 부분 실패를 크게 알린다.
    storage = LocalStorage(tmp_path / "lake")
    good = "20260623800031"
    bad = _supply_record("20260623800032", document_raw_path=None)
    _write_run(storage, [(_supply_record(good), _doc_zip(_supply_html(), good)), (bad, None)])

    assert normalize_disclosure.run(storage, "D1") == 2
    assert _manifest(storage, "D1")["canonical_written"] is True
    assert _manifest(storage, "D1")["canonical_partitions"][0]["winner_ids"] == [
        {"rcept_no": good}
    ]


def test_canonical_failure_leaves_manifest_incomplete(tmp_path, monkeypatch):
    # WHY(ALPHA-1044): canonical 저장이 실패했는데 이전/빈 완료 marker가 남으면 하류가 없는
    # 산출을 승인한다. 같은 run 시작 시 쓴 incomplete marker가 그대로 남아야 한다.
    storage = LocalStorage(tmp_path / "lake")
    rcept_no = "20260623800033"
    _write_run(storage, [(_supply_record(rcept_no), _doc_zip(_supply_html(), rcept_no))])
    monkeypatch.setattr(normalize_disclosure, "_write_canonical",
                        lambda *args: (_ for _ in ()).throw(OSError("write failed")))

    assert normalize_disclosure.run(storage, "D1") == 1
    assert _manifest(storage, "D1") == {
        "canonical_partitions": [], "canonical_written": False,
        "producer": "normalize_disclosure", "run_id": "D1",
    }


def test_completed_manifest_write_failure_is_invalidated(tmp_path):
    # WHY(ALPHA-1044): 완료 marker 저장 실패 뒤 pending에 winner가 남더라도 canonical_written=false
    # 여야 하류가 부분 기록을 승인하지 않는다. 무효화 PUT까지 막히지 않는 실스토리지 실패를 모사한다.
    inner = LocalStorage(tmp_path / "lake")
    manifest_key = canonical_run_manifest_key("supply_contract_fact", "D1")
    storage = _FailCompletedManifestStorage(inner, manifest_key)
    rcept_no = "20260623800034"
    _write_run(storage, [(_supply_record(rcept_no), _doc_zip(_supply_html(), rcept_no))])

    assert normalize_disclosure.run(storage, "D1") == 1
    manifest = _manifest(inner, "D1")
    assert manifest["canonical_written"] is False
    assert manifest["canonical_partitions"][0]["winner_ids"] == [{"rcept_no": rcept_no}]
