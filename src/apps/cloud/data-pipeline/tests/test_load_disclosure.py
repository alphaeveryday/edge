"""load_disclosure 스텝 테스트 — canonical 공시 → 이벤트 스토어 (ALPHA-476).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다
(레포 관례: CI 무-Postgres, 실 RDS e2e 는 수동). 각 테스트는 **왜 그 동작이 중요한지**를
검사한다: issuer 미해소 행을 넣으면 FK RESTRICT 로 런 전체가 롤백되고, CHECK 위반 fact 한 건이
배치를 통째로 죽이며, 멱등이 깨지면 재실행이 fact_id 를 바꿔 설명이 인용할 계보가 끊긴다.
"""

import hashlib
import io
import json
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.lake import (
    LocalStorage,
    canonical_business_segment_fact_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    canonical_supply_contract_fact_partition,
)
from data_pipeline.steps import load_disclosure

_SUPPLY_COLS = {
    "rcept_no": pa.string(), "source_vendor": pa.string(), "corp_code": pa.string(),
    "ticker": pa.string(), "corp_name": pa.string(), "counterparty": pa.string(),
    "counterparty_raw": pa.string(), "counterparty_withheld": pa.bool_(), "object": pa.string(),
    "amount_krw": pa.int64(), "ratio_pct": pa.float64(), "contract_start": pa.string(),
    "contract_end": pa.string(), "confidence": pa.string(), "report_date": pa.string(),
    "source_url": pa.string(), "parser_version": pa.string(), "fetched_at": pa.string(),
}
_SEGMENT_COLS = {
    "rcept_no": pa.string(), "source_vendor": pa.string(), "corp_code": pa.string(),
    "ticker": pa.string(), "corp_name": pa.string(), "segment_ordinal": pa.int64(),
    "segment_name": pa.string(), "revenue_krw": pa.int64(), "revenue_share_pct": pa.float64(),
    "share_basis": pa.string(), "period": pa.string(), "report_date": pa.string(),
    "source_url": pa.string(), "parser_version": pa.string(), "fetched_at": pa.string(),
}


def _write(storage, builder, cols, date, rows):
    schema = pa.schema([(c, t) for c, t in cols.items()])
    table = pa.Table.from_pylist([{c: r.get(c) for c in cols} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(f"{builder(date)}/part-00000.parquet", buf.getvalue())


def _supply(rcept_no, **over):
    row = {"rcept_no": rcept_no, "source_vendor": "dart", "corp_code": "00126380",
           "ticker": "005930", "corp_name": "삼성전자", "counterparty": "고객사",
           "counterparty_raw": "고객사 주식회사", "counterparty_withheld": False,
           "object": "반도체 공급", "amount_krw": 1000000, "ratio_pct": 12.5,
           "contract_start": "2026-01-01", "contract_end": "2026-12-31", "confidence": "high",
           "report_date": "2026-06-30", "source_url": "https://dart.fss.or.kr/x/1",
           "parser_version": "supply-v2", "fetched_at": "2026-07-16T01:00:00+00:00"}
    row.update(over)
    return row


def _segment(rcept_no, ordinal, **over):
    row = {"rcept_no": rcept_no, "source_vendor": "dart", "corp_code": "00126380",
           "ticker": "005930", "corp_name": "삼성전자", "segment_ordinal": ordinal,
           "segment_name": "반도체", "revenue_krw": 5000000, "revenue_share_pct": 60.0,
           "share_basis": "reported", "period": "2026Q1", "report_date": "2026-06-30",
           "source_url": "https://dart.fss.or.kr/x/2", "parser_version": "segments-v2",
           "fetched_at": "2026-07-16T01:00:00+00:00"}
    row.update(over)
    return row


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._log, self._profiles = conn.log, conn._profiles
        self._existing, self._actors = conn._existing, conn._actors
        self.rowcount = 1
        self._result: list = []

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._log.append((norm, params))
        upper = norm.upper()
        if upper.startswith("INSERT INTO DISCLOSURE_LOAD_PENDING"):
            rcept_no, dtype, rows, digest, source_fetched_at, first_run, last_run = params
            old = self._conn.pending.get(rcept_no)
            accepted = not old or source_fetched_at >= old["source_fetched_at"]
            self.rowcount = int(accepted)
            if not accepted:
                return
            attempts = old["attempt_count"] if old and old["payload_sha256"] == digest else 0
            self._conn.pending[rcept_no] = {
                "disclosure_type": dtype, "rows": rows, "payload_sha256": digest,
                "source_fetched_at": source_fetched_at,
                "attempt_count": attempts, "first_run": old["first_run"] if old else first_run,
                "last_run": last_run,
            }
        elif upper.startswith("SELECT RCEPT_NO, DISCLOSURE_TYPE, CANONICAL_ROWS"):
            self._result = [(r, v["disclosure_type"], v["rows"], v["payload_sha256"],
                             v["attempt_count"]) for r, v in sorted(self._conn.pending.items())]
        elif upper.startswith("UPDATE DISCLOSURE_LOAD_PENDING"):
            code, error, rcept_no, digest = params
            item = self._conn.pending.get(rcept_no)
            if item and item["payload_sha256"] == digest:
                item["attempt_count"] += 1
                item["last_error_code"] = code
                item["last_error"] = error
        elif upper.startswith("DELETE FROM DISCLOSURE_LOAD_PENDING"):
            rcept_no, digest = params
            item = self._conn.pending.get(rcept_no)
            if rcept_no in self._conn.conflict_on_delete and item:
                item["payload_sha256"] = "newer-payload"
            self.rowcount = int(bool(item and item["payload_sha256"] == digest))
            if self.rowcount:
                del self._conn.pending[rcept_no]
        elif upper.startswith("SELECT COUNT(*) FROM DISCLOSURE_LOAD_PENDING"):
            self._result = [(len(self._conn.pending),)]
        elif upper.startswith("SELECT DART_CORP_CODE"):
            codes = params[0]
            self._result = [(c, self._profiles[c]) for c in codes if c in self._profiles]
        elif upper.startswith("SELECT A.ACTOR_ID, E.DISPLAY_NAME"):
            self._result = list(self._actors)
        elif upper.startswith("INSERT INTO DOCUMENT "):
            if params[2] in self._conn.fail_docs:
                raise RuntimeError(f"temporary failure: {params[2]}")
            self.rowcount = 0 if (params[1], params[2]) in self._existing else 1
        else:
            self.rowcount = 1

    def executemany(self, sql, rows):
        for params in rows:
            self.execute(sql, params)

    def fetchall(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, profiles=None, existing_docs=None, actors=(), fail_docs=(),
                 conflict_on_delete=()):
        self.log: list = []
        self._profiles = profiles if profiles is not None else {"00126380": "actor_samsung"}
        self._existing = existing_docs or set()
        self._actors = actors
        self.fail_docs = set(fail_docs)
        self.conflict_on_delete = set(conflict_on_delete)
        self.pending: dict[str, dict] = {}

    def cursor(self):
        return _FakeCursor(self)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db():
    return DbConfig(password="x")


def _inserts(conn, table):
    prefix = f"INSERT INTO {table} ".upper()
    return [p for sql, p in conn.log if sql.upper().startswith(prefix)]


def _run(storage, conn, monkeypatch, **kw):
    monkeypatch.setattr(load_disclosure, "connect", _fake_connect(conn))
    return load_disclosure.run(storage, "R1", db=_db(), **kw)


def _log(storage):
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _write_manifest(storage, run_id, dataset, producer, builder, date=None, rcept_nos=()):
    partitions = []
    if date is not None:
        key = f"{builder(date)}/part-00000.parquet"
        data = storage.get_bytes(key)
        key = canonical_run_partition_key(dataset, run_id, date)
        storage.put_bytes(key, data)
        if producer == "normalize_disclosure_segment":
            winner_ids = [
                {"rcept_no": row["rcept_no"], "segment_ordinal": row["segment_ordinal"]}
                for row in load_disclosure._read_parquet_rows(data)
                if row["rcept_no"] in rcept_nos
            ]
        else:
            winner_ids = [{"rcept_no": value} for value in sorted(rcept_nos)]
        partitions.append({
            "report_date": date, "key": key, "sha256": hashlib.sha256(data).hexdigest(),
            "winner_ids": sorted(winner_ids, key=lambda item: tuple(item.values())),
        })
    storage.put_bytes(canonical_run_manifest_key(dataset, run_id), json.dumps({
        "run_id": run_id, "producer": producer, "canonical_written": True,
        "canonical_partitions": partitions,
    }).encode())


def _dual_manifests(storage, run_id, *, supply=(), segment=()):
    _write_manifest(
        storage, run_id, "supply_contract_fact", "normalize_disclosure",
        canonical_supply_contract_fact_partition,
        "2026-06-30" if supply else None, supply,
    )
    _write_manifest(
        storage, run_id, "business_segment_fact", "normalize_disclosure_segment",
        canonical_business_segment_fact_partition,
        "2026-06-30" if segment else None, segment,
    )


def test_manifest_winner_is_durable_before_load_and_success_removes_it(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): process가 적재 직전에 죽어도 current winner가 먼저 DB 원장에 있어야
    다음 정상 실행이 회수한다. 성공 삭제는 typed fact와 같은 transaction 안에서만 일어난다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    _dual_manifests(storage, "T1", supply=("R1",))
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 0
    assert conn.pending == {}
    statements = [sql for sql, _ in conn.log]
    assert next(i for i, sql in enumerate(statements)
                if sql.startswith("INSERT INTO disclosure_load_pending")) < next(
                    i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO document "))
    assert _log(storage)["pending_ledger"] == {
        "before": 1, "after": 0, "succeeded": 1, "failed": 0, "failed_items": [],
        "retention": "until_success", "retry": "once_per_eligible_run_no_lifetime_cutoff",
    }


def test_manifest_read_and_enqueue_are_serialized(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 과거 런이 canonical을 먼저 읽고 최신 런 뒤에 upsert하면 최신
    correction을 덮는다. manifest read 자체가 enqueue advisory lock 안에서 일어나야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    conn = _FakeConn()
    monkeypatch.setattr(load_disclosure, "connect", _fake_connect(conn))
    observed = []

    def fake_winners(actual_storage, actual_run_id):
        observed.append((actual_storage, actual_run_id, conn.log[-1][0]))
        return []

    monkeypatch.setattr(load_disclosure, "_manifest_winners", fake_winners)
    load_disclosure._enqueue_winners(_db(), storage, "T1")

    assert observed == [(storage, "T1", "SELECT pg_advisory_xact_lock(%s)")]


def test_older_source_revision_cannot_replace_newer_pending(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): lock 획득 순서는 source 최신순이 아니다. 늦게 도착한 과거 fetched_at이
    최신 correction을 덮지 못하게 실제 source revision으로 조건부 upsert해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    conn = _FakeConn()
    monkeypatch.setattr(load_disclosure, "connect", _fake_connect(conn))
    winner = {
        "rcept_no": "R1", "disclosure_type": "SUPPLY_CONTRACT", "rows": [_supply("R1")],
        "payload": "new", "payload_sha256": "a" * 64,
        "source_fetched_at": datetime.fromisoformat("2026-07-16T02:00:00+00:00"),
    }
    monkeypatch.setattr(load_disclosure, "_manifest_winners", lambda *args: [winner])
    load_disclosure._enqueue_winners(_db(), storage, "new-run")

    stale = {**winner, "payload": "old", "payload_sha256": "b" * 64,
             "source_fetched_at": datetime.fromisoformat("2026-07-16T01:00:00+00:00")}
    monkeypatch.setattr(load_disclosure, "_manifest_winners", lambda *args: [stale])
    with pytest.raises(ValueError, match="source revision"):
        load_disclosure._enqueue_winners(_db(), storage, "old-run")

    assert conn.pending["R1"]["payload_sha256"] == "a" * 64


def test_equal_source_revision_accepts_later_parser_correction(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 같은 raw 관측을 새 파서로 재정규화하면 fetched_at은 같고 payload만
    바뀐다. enqueue lock의 뒤 실행을 거부하면 correction과 무관한 winner까지 함께 막힌다."""
    storage = LocalStorage(tmp_path / "lake")
    conn = _FakeConn()
    monkeypatch.setattr(load_disclosure, "connect", _fake_connect(conn))
    revision = datetime.fromisoformat("2026-07-16T02:00:00+00:00")
    winner = {
        "rcept_no": "R1", "disclosure_type": "SUPPLY_CONTRACT", "rows": [_supply("R1")],
        "payload": "old-parser", "payload_sha256": "a" * 64,
        "source_fetched_at": revision,
    }
    monkeypatch.setattr(load_disclosure, "_manifest_winners", lambda *args: [winner])
    load_disclosure._enqueue_winners(_db(), storage, "parser-v1")
    corrected = {**winner, "payload": "new-parser", "payload_sha256": "b" * 64}
    monkeypatch.setattr(load_disclosure, "_manifest_winners", lambda *args: [corrected])

    load_disclosure._enqueue_winners(_db(), storage, "parser-v2")

    assert conn.pending["R1"]["payload_sha256"] == "b" * 64


def test_setup_failure_marks_pending_count_unknown(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): enqueue commit 뒤 canonical scan이 실패했는데 backlog 0으로 기록하면
    운영자가 durable winner가 없다고 오판한다. 실제 COUNT 전 실패는 unknown이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    _dual_manifests(storage, "T1", supply=("R1",))
    conn = _FakeConn()
    monkeypatch.setattr(load_disclosure, "connect", _fake_connect(conn))

    def _fail_scan(*args, **kwargs):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(load_disclosure, "_read_facts", _fail_scan)

    assert load_disclosure.run(storage, "R1", db=_db(), input_run_id="T1") == 1
    assert set(conn.pending) == {"R1"}
    ledger = _log(storage)["pending_ledger"]
    assert ledger["before"] is None and ledger["after"] is None


def test_segment_manifest_validates_composite_winners_then_groups_receipt(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 사업부문 winner의 정체성은 (rcept_no, ordinal)이다. 한 공시의
    여러 부문을 rcept_no 중복으로 오인하면 정상 manifest가 원장 진입 전에 실패한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_business_segment_fact_partition, _SEGMENT_COLS, "2026-06-30",
           [_segment("R1", 0), _segment("R1", 1, segment_name="디스플레이")])
    _dual_manifests(storage, "T1", segment=("R1",))
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 0
    assert len(_inserts(conn, "business_segment_fact")) == 2
    assert conn.pending == {}


def test_run_scoped_manifest_survives_shared_canonical_overwrite(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 다른 normalize 런이 report-date canonical을 덮어써도 먼저 완료된
    manifest의 immutable 바이트는 남아야 그 winner를 durable ledger에 넣을 수 있다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1", amount_krw=1)])
    _dual_manifests(storage, "T-old", supply=("R1",))
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1", amount_krw=2,
                                   fetched_at="2026-07-16T02:00:00+00:00")])
    _dual_manifests(storage, "T-new", supply=("R1",))
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, input_run_id="T-old") == 0
    assert _inserts(conn, "supply_contract_fact")[0][5] == 1


def test_source_revision_matches_producer_leniency():
    """WHY(ALPHA-1045): producer가 허용한 결측·오류·naive fetched_at을 consumer가 더 엄격하게
    막으면 completed manifest 전체가 enqueue되지 않는다."""
    oldest = datetime.min.replace(tzinfo=load_disclosure.timezone.utc)
    assert load_disclosure._source_revision([_supply("R1", fetched_at=None)]) == oldest
    assert load_disclosure._source_revision([_supply("R1", fetched_at="bad")]) == oldest
    assert load_disclosure._source_revision([
        _supply("R1", fetched_at="2026-07-16T01:00:00")
    ]).tzinfo is not None


def test_unresolved_issuer_stays_pending_and_next_normal_run_recovers(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): issuer master가 늦게 채워지는 것이 기존 full scan의 자동 회수 이유였다.
    원장은 성공 전에는 보존되고, 다음 정상 실행에서 같은 payload를 다시 시도해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1", corp_code="99999999")])
    _dual_manifests(storage, "T1", supply=("R1",))
    conn = _FakeConn(profiles={})

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 2
    assert conn.pending["R1"]["attempt_count"] == 1
    assert conn.pending["R1"]["last_error_code"] == "unresolved_issuer"

    # 같은 run 중복 실행도 INSERT를 늘리거나 attempt 이력을 초기화하지 않는다.
    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 2
    assert set(conn.pending) == {"R1"}
    assert conn.pending["R1"]["attempt_count"] == 2

    _dual_manifests(storage, "T2")
    conn._profiles["99999999"] = "actor_late"
    assert load_disclosure.run(storage, "T2", db=_db(), input_run_id="T2") == 0
    assert conn.pending == {}


def test_one_temporary_failure_does_not_rollback_other_pending_items(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 한 공시의 DB 일시 실패 때문에 앞뒤 성공 공시와 원장 삭제까지
    롤백되면 backlog가 매번 통째로 재처리된다. savepoint는 실패 ID만 남겨야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1"), _supply("R2"), _supply("R3")])
    _dual_manifests(storage, "T1", supply=("R1", "R2", "R3"))
    conn = _FakeConn(fail_docs={"R2"})

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 2
    assert set(conn.pending) == {"R2"}
    assert [params[2] for sql, params in conn.log
            if sql.startswith("INSERT INTO document ")] == ["R1", "R2", "R3"]
    ledger = _log(storage)["pending_ledger"]
    assert (ledger["succeeded"], ledger["failed"], ledger["after"]) == (2, 1, 1)
    assert ledger["failed_items"][0]["rcept_no"] == "R2"


def test_full_scan_item_failure_returns_partial(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): legacy full scan 항목도 DB 실패를 성공으로 보고하면 하류가 불완전한
    disclosure를 조립한다. pending 여부와 무관하게 항목 실패는 비0이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    conn = _FakeConn(fail_docs={"R1"})

    assert _run(storage, conn, monkeypatch) == 2
    assert _log(storage)["failures"][0]["rcept_no"] == "R1"


def test_rejected_segment_keeps_receipt_pending(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 한 receipt의 일부 segment만 유효해도 reject가 있으면 winner 전체가
    적재된 것이 아니다. 원장을 지우면 거절 행의 durable 재시도 증거가 사라진다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_business_segment_fact_partition, _SEGMENT_COLS, "2026-06-30",
           [_segment("R1", 0), _segment("R1", 1, revenue_krw=-1)])
    _dual_manifests(storage, "T1", segment=("R1",))
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 2
    assert conn.pending["R1"]["last_error_code"] == "rejected_fact"
    assert len(_inserts(conn, "business_segment_fact")) == 1


def test_conditional_delete_conflict_is_partial(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 동시 실행이 더 최신 payload로 원장을 교체했으면 stale loader의
    조건부 DELETE 0건은 성공이 아니다. 하류 publish를 막고 최신 winner를 남겨야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    _dual_manifests(storage, "T1", supply=("R1",))
    conn = _FakeConn(conflict_on_delete={"R1"})

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 2
    assert set(conn.pending) == {"R1"}
    assert _log(storage)["pending_ledger"]["failed_items"] == [
        {"rcept_no": "R1", "reason": "payload_conflict"}
    ]


def test_non_finite_payload_is_retained_without_blocking_valid_winner(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): canonical은 NaN/Infinity를 담을 수 있지만 PostgreSQL JSONB는 못 담는다.
    원장 직렬화가 전체 enqueue를 죽이지 않고 그 ID만 검증 실패 pending으로 남겨야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R-bad", ratio_pct=float("inf")), _supply("R-ok")])
    _dual_manifests(storage, "T1", supply=("R-bad", "R-ok"))
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 2
    assert set(conn.pending) == {"R-bad"}
    assert conn.pending["R-bad"]["last_error_code"] == "no_valid_fact"
    assert [params[2] for sql, params in conn.log
            if sql.startswith("INSERT INTO document ")] == ["R-ok"]


def test_corrupt_or_incomplete_manifest_never_enqueues_or_falls_back(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): manifest 손상을 full scan으로 확대하면 incomplete canonical PUT을
    정상 winner로 오인한다. 원장 commit 전 fail-loud하고 pending을 만들지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    _dual_manifests(storage, "T1", supply=("R1",))
    key = canonical_run_manifest_key("supply_contract_fact", "T1")
    manifest = json.loads(storage.get_bytes(key))
    manifest["canonical_written"] = False
    storage.put_bytes(key, json.dumps(manifest).encode())
    conn = _FakeConn()

    assert _run(storage, conn, monkeypatch, input_run_id="T1") == 1
    assert conn.pending == {}
    assert _inserts(conn, "document") == []


def test_supply_becomes_document_and_typed_fact(tmp_path, monkeypatch):
    """세로 슬라이스 — 공급계약 canonical 이 document→disclosure_document→disclosure_fact→
    supply_contract_fact 로 흘러야 설명 엔진(explanation_run_disclosure_fact)이 읽는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R100")])
    conn = _FakeConn(actors=(("actor_customer", "고객사"),))
    assert _run(storage, conn, monkeypatch) == 0

    [(doc_id, dtype, source_code, rcept_no, published_at, available_at, uri)] = \
        [(p[0], "DISCLOSURE", p[1], p[2], p[3], p[4], p[5]) for p in _inserts(conn, "document")]
    assert doc_id == stable_domain_id("doc", "dart", "R100")
    assert (source_code, rcept_no) == ("dart", "R100")
    assert available_at == "2026-07-16T01:00:00+00:00"  # fetched_at
    assert published_at == "2026-06-30"

    [dd] = _inserts(conn, "disclosure_document")
    assert dd[1] == "actor_samsung"          # issuer_actor_id 해소
    assert dd[2] == "SUPPLY_CONTRACT"        # disclosure_type

    [sc] = _inserts(conn, "supply_contract_fact")
    fact_id = stable_domain_id("dfact", doc_id, "SUPPLY_CONTRACT", None)
    assert sc[0] == fact_id
    assert sc[1] == "actor_customer"           # 정규화된 상대방 actor
    assert sc[2] == "고객사 주식회사"          # counterparty_raw_name 보존
    assert sc[4] == stable_domain_id("concept", "반도체공급")
    assert sc[5] == 1000000 and sc[6] == 12.5  # amount, ratio
    assert _inserts(conn, "entity")             # concept FK 비계를 typed fact 전에 만든다
    assert _inserts(conn, "concept")


def test_ambiguous_counterparty_is_not_guessed(tmp_path, monkeypatch):
    """동명 actor가 둘이면 아무거나 고르는 순간 DART와 뉴스가 잘못된 계약 thread로 합쳐진다.
    원문명은 보존하되 actor 연결은 NULL이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R101")])
    conn = _FakeConn(actors=(("actor_a", "고객사"), ("actor_b", "고객사")))
    assert _run(storage, conn, monkeypatch) == 0
    [sc] = _inserts(conn, "supply_contract_fact")
    assert sc[1] is None
    assert sc[2] == "고객사 주식회사"


def test_segments_carry_ordinal_and_normalize_share_basis(tmp_path, monkeypatch):
    """사업부문은 한 문서에 여럿 — fact_id 에 ordinal 이 없으면 두 번째 부문이 첫 부문을 덮는다.
    share_basis 는 소문자 canonical 을 대문자 CHECK 어휘로 맞추고, 범위 밖은 NULL(값은 유지)."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_business_segment_fact_partition, _SEGMENT_COLS, "2026-06-30",
           [_segment("R200", 0, segment_name="반도체", share_basis="reported"),
            _segment("R200", 1, segment_name="디스플레이", share_basis="weird")])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0

    rows = _inserts(conn, "business_segment_fact")
    doc_id = stable_domain_id("doc", "dart", "R200")
    ids = {r[0] for r in rows}
    assert ids == {stable_domain_id("dfact", doc_id, "BUSINESS_SEGMENT", 0),
                   stable_domain_id("dfact", doc_id, "BUSINESS_SEGMENT", 1)}
    basis = {r[1]: r[5] for r in rows}  # segment_name → share_basis
    assert basis["반도체"] == "REPORTED"
    assert basis["디스플레이"] is None    # 범위 밖 → NULL


def test_unresolved_issuer_is_skipped_not_inserted(tmp_path, monkeypatch):
    """issuer_actor_id FK 는 company_profile 을 RESTRICT 로 요구한다 — 마스터에 없는 corp_code
    를 넣으면 런 전체가 롤백된다. 세고 뺀다(9→309 확장은 ALPHA-491 별건)."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1", corp_code="00126380"),
            _supply("R2", corp_code="99999999")])  # 미시드
    conn = _FakeConn(profiles={"00126380": "actor_samsung"})
    assert _run(storage, conn, monkeypatch) == 0

    assert [p[2] for p in _inserts(conn, "document")] == ["R1"]
    assert _log(storage)["skipped_unresolved_issuer"] == 1


def test_counterparty_check_is_prevalidated(tmp_path, monkeypatch):
    """비공개가 아닌데 원문명이 없으면 supply_contract_fact CHECK 위반 — canonical 정제는 값
    이상을 경고로만 통과시키므로 로더가 선검증해 그 fact 만 뺀다(배치 전체가 안 죽게)."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1"),
            _supply("R2", counterparty=None, counterparty_raw=None, counterparty_withheld=False)])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0

    # R2 는 유효 fact 0 → 문서도 안 실린다(설명은 fact 를 읽는다).
    assert {p[2] for p in _inserts(conn, "document")} == {"R1"}
    log = _log(storage)
    assert log["rejected_facts"] == 1 and log["skipped_no_valid_fact"] == 1
    assert log["rejected_facts_sample"][0]["reason"] == "counterparty_missing"


def test_withheld_counterparty_has_null_actor(tmp_path, monkeypatch):
    """상대방 비공개 공시는 actor 연결 없이 withheld=True 로 적재된다(CHECK 통과)."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1", counterparty=None, counterparty_raw=None, counterparty_withheld=True)])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0

    [sc] = _inserts(conn, "supply_contract_fact")
    assert sc[1] is None and sc[3] is True  # counterparty_actor_id NULL, withheld True


def test_bad_contract_dates_are_prevalidated(tmp_path, monkeypatch):
    """종료일 < 시작일이면 supply_contract_fact CHECK 위반 — 그 fact 만 뺀다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1", contract_start="2026-12-31", contract_end="2026-01-01")])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn, "supply_contract_fact") == []
    log = _log(storage)
    assert log["rejected_facts"] == 1
    assert log["rejected_facts_sample"][0]["reason"] == "reversed_period"


def test_existing_document_is_not_recreated(tmp_path, monkeypatch):
    """멱등 — 재실행이 document_id 를 갈아치우면 이 문서를 인용할 설명 계보가 끊긴다.
    ON CONFLICT DO NOTHING(rowcount 0)이면 created 가 아니라 already 로 세어져야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    conn = _FakeConn(existing_docs={("dart", "R1")})
    assert _run(storage, conn, monkeypatch) == 0
    log = _log(storage)
    assert log["documents_created"] == 0
    assert log["documents_already_present"] == 1


def test_window_prunes_partitions(tmp_path, monkeypatch):
    """--from/--to 는 report_date 파티션 프루닝 — 창 밖을 읽으면 백필/증분 분리가 깨진다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-29", [_supply("R-old", report_date="2026-06-29")])
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R-in", report_date="2026-06-30")])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch, from_date="2026-06-30", to_date="2026-06-30") == 0
    assert [p[2] for p in _inserts(conn, "document")] == ["R-in"]


def test_window_does_not_consume_pending_outside_event_assembly_scope(tmp_path, monkeypatch):
    """WHY(ALPHA-1045): 창 밖 pending을 typed fact로 성공·삭제하면 같은 창으로 도는 event
    assembler가 그 fact를 못 보고 durable recovery가 조용히 끊긴다. 무창 실행이 회수해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _dual_manifests(storage, "T1")
    conn = _FakeConn()
    old = _supply("R-old", report_date="2026-06-29")
    payload = json.dumps([old], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    conn.pending["R-old"] = {
        "disclosure_type": "SUPPLY_CONTRACT", "rows": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "source_fetched_at": datetime.fromisoformat(old["fetched_at"]),
        "attempt_count": 0, "first_run": "old", "last_run": "old",
    }

    assert _run(storage, conn, monkeypatch, input_run_id="T1",
                from_date="2026-06-30", to_date="2026-06-30") == 0

    assert set(conn.pending) == {"R-old"}
    assert _inserts(conn, "document") == []


def test_db_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 트레이스백이 아니라 비0 종료 + 로그로 드러나야 한다(Rule 12). 롤백된 런이
    만들었다고 주장하면 다음 사람이 DB 에 있다고 믿는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_disclosure, "connect", _boom)
    assert load_disclosure.run(storage, "R1", db=_db()) == 1
    log = _log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["documents_created"] == 0 and log["facts_written"] == 0


def test_non_finite_ratio_is_prevalidated(tmp_path, monkeypatch):
    """NaN/Infinity 비율은 `< 0` 선검증을 조용히 통과하지만(NaN 비교는 전부 False) DB 유한성
    CHECK(`< 'Infinity'`)에 걸려 정상 행까지 배치 전체를 롤백시킨다 — 여기서 명시적으로 뺀다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1", ratio_pct=float("inf"))])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn, "supply_contract_fact") == []
    assert _log(storage)["rejected_facts_sample"][0]["reason"] == "non_finite_ratio"


def test_whitespace_rcept_no_is_skipped(tmp_path, monkeypatch):
    """공백뿐인 rcept_no 를 두면 서로 다른 공시가 같은 document_id 로 접혀 충돌한다 — 정체성
    결손으로 세고 뺀다(`if not rcept_no` 만으로는 ' ' 를 못 잡는다)."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1"), _supply("   ")])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    assert [p[2] for p in _inserts(conn, "document")] == ["R1"]
    assert _log(storage)["skipped_missing_identity"] == 1


def test_missing_report_date_is_skipped(tmp_path, monkeypatch):
    """report_date 는 공시의 시간축이자 파티션 정체성 — 결측이면 DB 에서 날짜 없는 문서가 돼
    날짜 기반 조회에서 샌다. fetched_at 이 있어도 적재하지 않고 계측한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS, "2026-06-30",
           [_supply("R1", report_date=None)])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    assert _inserts(conn, "document") == []
    assert _log(storage)["skipped_no_report_date"] == 1


def test_invalid_fact_is_counted_even_when_document_still_loads(tmp_path, monkeypatch):
    """유효 fact 와 함께 있는 무효 fact 를 조용히 버리면 데이터 유실이 success 로 위장된다
    (Rule 12) — 문서는 실리되 거절된 fact 는 사유와 함께 계측돼야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_business_segment_fact_partition, _SEGMENT_COLS, "2026-06-30",
           [_segment("R1", 0, segment_name="반도체"),
            _segment("R1", 1, segment_name="디스플레이", revenue_krw=-5)])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    log = _log(storage)
    assert log["facts_written"] == 1        # 정상 부문은 실린다
    assert log["rejected_facts"] == 1       # 음수 매출 부문은 계측된다
    assert log["documents_created"] == 1
    assert log["rejected_facts_sample"][0]["reason"] == "negative_value"


def test_duplicate_ordinal_is_rejected_not_silently_lost(tmp_path, monkeypatch):
    """같은 (rcept_no, ordinal)이 같은 fact_id 를 만든다 — ON CONFLICT DO NOTHING 에 기대면
    둘째 부문이 조용히 사라지고 런은 성공으로 끝난다. 런 안 중복은 거절로 표면화한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_business_segment_fact_partition, _SEGMENT_COLS, "2026-06-30",
           [_segment("R1", 0, segment_name="반도체"),
            _segment("R1", 0, segment_name="디스플레이")])  # ordinal 중복(오염)
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    assert len(_inserts(conn, "business_segment_fact")) == 1
    log = _log(storage)
    assert log["rejected_facts"] == 1
    assert log["rejected_facts_sample"][0]["reason"] == "duplicate_fact_id"


def test_corrected_facts_update_on_conflict(tmp_path, monkeypatch):
    """정정본 재수집·파서 재실행으로 같은 rcept_no 의 canonical 값(금액·부문 등)이 바뀌면 DB 도
    갱신돼야 한다 — DO NOTHING 이면 설명이 옛 값을 읽는다. 다른 canonical→DB 로더처럼
    ON CONFLICT DO UPDATE(값이 실제로 바뀔 때만)를 쓴다."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    child_sql = next(sql for sql, _ in conn.log
                     if sql.upper().startswith("INSERT INTO SUPPLY_CONTRACT_FACT "))
    assert "DO UPDATE" in child_sql.upper()
    assert "IS DISTINCT FROM" in child_sql.upper()  # 같은 값 재적재는 no-op(WHERE 가 거른다)


def test_run_log_records_what_happened(tmp_path, monkeypatch):
    """조용한 0건 금지 — 몇 건 읽고 몇 건 걸렀고 몇 건 만들었는지 남아야 한다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write(storage, canonical_supply_contract_fact_partition, _SUPPLY_COLS,
           "2026-06-30", [_supply("R1")])
    _write(storage, canonical_business_segment_fact_partition, _SEGMENT_COLS,
           "2026-06-30", [_segment("R2", 0)])
    conn = _FakeConn()
    assert _run(storage, conn, monkeypatch) == 0
    log = _log(storage)
    assert log["supply_rows_read"] == 1 and log["segment_rows_read"] == 1
    assert log["documents_created"] == 2
    assert log["facts_written"] == 2
    assert log["created_rows_sample"][0]["rcept_no"] in {"R1", "R2"}
