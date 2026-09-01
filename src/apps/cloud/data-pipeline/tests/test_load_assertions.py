"""load_assertions 스텝 테스트 — feature 뉴스 assertion → document_assertion (ALPHA-376).

실 DB 없이 돈다 — 가짜 커넥션이 SQL·파라미터를 기록한다. 검사하는 WHY:
멱등 축이 깨지면 재실행마다 같은 주장이 새 ULID 로 쌓이고, 엔티티 해소가 침묵하면
계보 없는(또는 틀린) 주장이 event 조립에 흘러들며, 미해소율이 안 남으면 별칭 축
도입 판단(ALPHA-375 완료 조건)이 불가능하다.
"""

import io
import json
from datetime import datetime, timezone

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.entity_resolution import ResolutionIndex
from data_pipeline.lake import (
    LocalStorage,
    feature_run_manifest_key,
    feature_news_assertions_minute_prefix,
    feature_news_assertions_partition,
    run_manifest_consumed_key,
    run_manifest_skipped_key,
)
from data_pipeline.steps import load_assertions

_COLUMNS = ("article_id", "published_at", "title", "input_fingerprint", "doc_class",
            "status", "assertions", "reasons", "ontology_version", "tagger_version",
            "tagged_at")

_INDEX = ResolutionIndex(by_key={
    "삼성전자": "inst_SAMSUNG",
    "005930": "inst_SAMSUNG",
    "SK하이닉스": "inst_HYNIX",
    "충돌이름": None,  # ambiguous
})


# document.available_at — 로더가 assertion 에 실어야 하는 값(ALPHA-538). tagged_at(02:00Z)·
# published_at(09:00+09)과 다른 값으로 둬서 어느 시각이 실렸는지 구분 가능하게 한다.
_DOC_AVAILABLE_AT = "2026-07-15T08:50:00+09:00"

def _write_feature(storage, language: str, date: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{feature_news_assertions_partition(language, date)}/part-00000.parquet", buf.getvalue())


def _lineage_prefix() -> str:
    """manifest 계보 프리픽스 — 경로를 테스트가 다시 조립하지 않게 빌더에서 깎아 쓴다.

    ⚠️ 아래 세 테스트의 `list_calls` 단언이 원래 `== []` 였다(ALPHA-1033: "상위 LIST 는 범위
    회귀"). ALPHA-1052 의 미소비 회수가 LIST 를 **하나** 추가하는데, 그건 feature **데이터**
    프리픽스가 아니라 manifest **계보** 프리픽스다 — 비용이 과거 파티션 수가 아니라 런 수에
    비례하는 다른 축이다. 그래서 단언을 느슨하게 풀지 않고 **어느 프리픽스인지까지** 못박는다.
    데이터 프리픽스를 LIST 하기 시작하면 이 단언이 그대로 깨진다.
    """
    return feature_run_manifest_key("news_assertions", "X").removesuffix("run_id=X/manifest.json")


def _manifest_partition(language: str, date: str, article_ids: list[str]) -> dict:
    return {
        "language": language,
        "published_date": date,
        "key": f"{feature_news_assertions_partition(language, date)}/part-00000.parquet",
        "article_ids": sorted(article_ids),
    }


def _write_feature_manifest(storage, run_id: str, partitions: list[dict]) -> None:
    storage.put_bytes(feature_run_manifest_key("news_assertions", run_id), json.dumps({
        "run_id": run_id,
        "producer": "tag_news",
        "feature_written": True,
        "feature_partitions": partitions,
    }).encode("utf-8"))


class _ReadSpy:
    def __init__(self, inner):
        self.inner = inner
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []

    def list_keys(self, prefix):
        self.list_calls.append(prefix)
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        self.get_calls.append(key)
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        return self.inner.put_bytes(key, data)


def _feature_row(article_id: str, assertions: list | str, **over) -> dict:
    row = {"article_id": article_id, "published_at": "2026-07-15T09:00:00+09:00",
           "title": "삼성전자 수주", "input_fingerprint": "fp", "doc_class": "EVENT",
           "status": "ok",
           "assertions": assertions if isinstance(assertions, str) else json.dumps(assertions),
           "reasons": "[]", "ontology_version": "ont-1", "tagger_version": "tagging-v1",
           "tagged_at": "2026-07-15T02:00:00+00:00"}
    row.update(over)
    return row


def _assertion(text: str = "삼성전자", **over) -> dict:
    a = {"event_type_code": "SUPPLY_CONTRACT", "predicate_code": "WIN",
         "arguments": [{"role_code": "ISSUER", "text": text, "entity_id": None}],
         "confidence": 0.9, "completeness": "complete"}
    a.update(over)
    return a


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list = []
        self._one = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        conn = self._conn
        conn.log.append((" ".join(sql.split()), params))
        upper = sql.lstrip().upper()
        if upper.startswith("SELECT SOURCE_DOCUMENT_ID"):
            wanted = set(params[1])
            self._rows = [(a, d, conn.doc_available_at)
                          for a, d in conn.documents if a in wanted]
        elif upper.startswith("INSERT INTO DOCUMENT_ASSERTION"):
            if conn.fail_on_event is not None and params[2] == conn.fail_on_event:
                raise RuntimeError(f"제약 위반 모사: event_type={params[2]}")
            nk = (params[1], params[2], params[3])
            self.rowcount = 0 if nk in conn.existing_assertions else 1
        elif upper.startswith("UPDATE DOCUMENT_ASSERTION"):
            # 소유 컬럼(confidence) 착지 + RETURNING assertion_id (ALPHA-538)
            self._one = ("asrt_EXISTING",)
        elif upper.startswith("INSERT INTO ASSERTION_ARGUMENT"):
            self.rowcount = 0 if (params[0], params[1], params[2]) in conn.existing_arguments else 1

    def executemany(self, sql, seq):
        # 실제 커서와 같이 **행마다 한 번** 기록한다 — 한 줄로 뭉치면 개념 몇 개가
        # 세워졌는지, 순서가 FK 를 지켰는지를 테스트가 볼 수 없다.
        for params in seq:
            self.execute(sql, params)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTransaction:
    """psycopg3 `conn.transaction()` 의 최소 대역 — **중첩이면 savepoint** 다.

    ⚠️ 예외로 빠져나갈 때 **그 블록 안에서 기록된 문장을 되돌린다**. 되돌리지 않으면
    테스트가 "롤백됐다"를 볼 수 없어, 격리 단언이 실제로는 아무것도 재지 않는 초록이
    된다(ALPHA-1053). 예외는 삼키지 않는다 — 호출부가 격리 여부를 정한다.

    ⚠️ 이건 제어 흐름 대역일 뿐 실제 savepoint 의미가 아니다. 실 Postgres 위 검증은
    `tests/e2e/test_carried_scope_isolation.py` 가 진다.
    """

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._mark = len(self._conn.log)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            del self._conn.log[self._mark:]
        return False


class _FakeConn:
    def __init__(self, documents=None, existing_assertions=None, existing_arguments=None,
                 fail_on_event=None):
        self.log: list = []
        # 이 event_type_code 를 INSERT 하려 하면 터진다 — 회수 그룹만 오염시키는 수단이다.
        self.fail_on_event = fail_on_event
        self.documents = documents or []          # (source_document_id, document_id)
        self.doc_available_at = _DOC_AVAILABLE_AT  # document.available_at 로 SELECT 에 실림
        self.existing_assertions = existing_assertions or set()  # (doc_id, event, predicate)
        self.existing_arguments = existing_arguments or set()

    def transaction(self):
        return _FakeTransaction(self)

    def cursor(self):
        return _FakeCursor(self)


def _setup(monkeypatch, conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    monkeypatch.setattr(load_assertions, "connect", _c)
    monkeypatch.setattr(load_assertions, "load_resolution_index", lambda c: _INDEX)


def _setup_carry(monkeypatch, conn):
    """Freeze only ALPHA-1052's historical carry-forward scenario clock."""
    _setup(monkeypatch, conn)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # 08-25 manifests are intentionally inside this scenario's 7-day window.
            frozen = cls(2026, 8, 28, tzinfo=timezone.utc)
            return frozen.astimezone(tz) if tz is not None else frozen.replace(tzinfo=None)

    monkeypatch.setattr(load_assertions, "datetime", _Clock)


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn, table: str) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith(f"INSERT INTO {table.upper()} ")]


def _log(storage) -> dict:
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_manifest_reads_direct_keys_and_reports_physical_and_logical_rows(tmp_path, monkeypatch):
    """WHY(ALPHA-1033): 누적 part의 과거 행은 물리 읽기에만 남고 현재 manifest ID만
    적재·실패 계측에 들어가야 한다. 상위 LIST나 manifest 밖 파티션 GET은 범위 회귀다."""
    inner = LocalStorage(tmp_path / "lake")
    _write_feature(inner, "ko", "2026-08-25", [
        _feature_row("old-same-partition", [_assertion()],
                     published_at="2026-08-25T08:00:00+09:00"),
        _feature_row("current-yesterday", [_assertion()],
                     published_at="2026-08-25T23:59:00+09:00"),
    ])
    _write_feature(inner, "ko", "2026-08-26", [
        _feature_row("current-today", [_assertion()],
                     published_at="2026-08-26T00:01:00+09:00"),
    ])
    _write_feature(inner, "ko", "2026-08-24", [_feature_row("old-partition", [_assertion()])])
    partitions = [
        _manifest_partition("ko", "2026-08-25", ["current-yesterday"]),
        _manifest_partition("ko", "2026-08-26", ["current-today"]),
    ]
    _write_feature_manifest(inner, "T1", partitions)
    storage = _ReadSpy(inner)
    conn = _FakeConn(documents=[("current-yesterday", "doc_D1"), ("current-today", "doc_D2")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    assert storage.list_calls == [_lineage_prefix()]
    assert storage.get_calls == [
        feature_run_manifest_key("news_assertions", "T1"),
        partitions[0]["key"], partitions[1]["key"],
    ]
    log = _log(inner)
    assert log["physical_rows_read"] == 3
    assert log["logical_rows_read"] == 2
    assert log["assertions_considered"] == 2
    assert log["ops"] == {
        "records_out": 2, "failed_records": 0,
        "entity_resolution_arguments_total": 2,
        "entity_resolution_arguments_resolved": 2,
    }


def test_same_feature_manifest_run_is_idempotent(tmp_path, monkeypatch):
    """WHY(ALPHA-1033): 실패한 동일 TagNews run 재실행은 같은 논리 범위를 다시 읽고,
    자연키 기존 행을 신규로 세지 않아야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("a1", [_assertion()], published_at="2026-08-26T00:01:00+09:00")])
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["a1"])])
    conn = _FakeConn(documents=[("a1", "doc_D1")],
                     existing_assertions={("doc_D1", "SUPPLY_CONTRACT", "WIN")})
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    log = _log(storage)
    assert log["logical_rows_read"] == 1
    assert log["created"] == 0 and log["already_present"] == 1


def test_missing_document_recovers_by_replaying_the_same_manifest(tmp_path, monkeypatch):
    """WHY(ALPHA-1033): 정상 다음 런을 과거 feature 재독에 쓰지 않는다. LoadDocuments와의
    일시 결손은 같은 TagNews manifest를 재실행해 정확히 그 ID만 회수해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("a1", [_assertion()], published_at="2026-08-26T00:01:00+09:00")])
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["a1"])])

    _setup(monkeypatch, _FakeConn())
    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    first = _log(storage)
    assert first["missing_document"] == 1
    assert first["ops"]["records_out"] == 0 and first["ops"]["failed_records"] == 1

    recovered = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, recovered)
    assert load_assertions.run(storage, "L2", db=_db(), input_run_id="T1") == 0
    logs = storage.list_keys("operations_archive/data_quality_logs/")
    second = json.loads(storage.get_bytes([key for key in logs if "run_id=L2/" in key][0]))
    assert second["logical_rows_read"] == 1
    assert second["created"] == 1 and second["ops"]["failed_records"] == 0


def test_empty_completed_feature_manifest_does_not_list_or_get_parquet(tmp_path, monkeypatch):
    """WHY(ALPHA-1033): 생산자가 0건을 증명한 완료 manifest는 결손이 아니며, 과거
    feature를 회수하지 않는 0건 성공이어야 한다."""
    inner = LocalStorage(tmp_path / "lake")
    _write_feature(inner, "ko", "2026-08-25", [_feature_row("old", [_assertion()])])
    _write_feature_manifest(inner, "T1", [])
    storage = _ReadSpy(inner)
    _setup(monkeypatch, _FakeConn())

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    assert storage.list_calls == [_lineage_prefix()]
    assert storage.get_calls == [feature_run_manifest_key("news_assertions", "T1")]
    assert _log(inner)["logical_rows_read"] == 0


def test_missing_feature_manifest_fails_without_full_scan(tmp_path):
    """WHY(ALPHA-1033): 계보 결손을 상위 feature 풀스캔으로 숨기면 정상 비용과 현재 실행
    실패 집계가 다시 과거 전체에 비례한다."""
    inner = LocalStorage(tmp_path / "lake")
    _write_feature(inner, "ko", "2026-08-26", [_feature_row("old", [_assertion()])])
    storage = _ReadSpy(inner)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="missing") == 1
    assert storage.list_calls == []
    assert storage.get_calls == [feature_run_manifest_key("news_assertions", "missing")]


@pytest.mark.parametrize("damage", [
    "incomplete", "wrong_producer", "wrong_run", "wrong_key", "duplicate_ids",
])
def test_corrupt_feature_manifest_fails_before_parquet_read(tmp_path, damage):
    """WHY(ALPHA-1033): manifest 전량을 먼저 검증해야 손상 계보의 앞 파티션 일부를
    적재한 뒤 뒤늦게 실패하는 부분 착지가 없다."""
    inner = LocalStorage(tmp_path / "lake")
    partitions = [
        _manifest_partition("ko", "2026-08-25", ["a1"]),
        _manifest_partition("ko", "2026-08-26", ["a2"]),
    ]
    _write_feature_manifest(inner, "T1", partitions)
    key = feature_run_manifest_key("news_assertions", "T1")
    manifest = json.loads(inner.get_bytes(key))
    if damage == "incomplete":
        manifest["feature_written"] = False
    elif damage == "wrong_producer":
        manifest["producer"] = "normalize_news"
    elif damage == "wrong_run":
        manifest["run_id"] = "T2"
    elif damage == "wrong_key":
        manifest["feature_partitions"][0]["key"] = "feature/news/wrong.parquet"
    else:
        manifest["feature_partitions"][0]["article_ids"] = ["a1", "a1"]
    inner.put_bytes(key, json.dumps(manifest).encode("utf-8"))
    storage = _ReadSpy(inner)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 1
    assert storage.list_calls == []
    assert storage.get_calls == [key]


def test_manifest_missing_logical_id_fails_loud(tmp_path):
    """WHY(ALPHA-1033): 직접 파일이 있어도 현재 ID가 없으면 손상 계보다. 일부 성공으로
    두면 records_out·failed_records가 현재 실행 완전성을 거짓 보고한다."""
    inner = LocalStorage(tmp_path / "lake")
    _write_feature(inner, "ko", "2026-08-26", [
        _feature_row("present", [_assertion()], published_at="2026-08-26T00:01:00+09:00")])
    partition = _manifest_partition("ko", "2026-08-26", ["missing"])
    _write_feature_manifest(inner, "T1", [partition])
    storage = _ReadSpy(inner)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 1
    assert storage.list_calls == [_lineage_prefix()]
    assert storage.get_calls == [feature_run_manifest_key("news_assertions", "T1"), partition["key"]]
    log = _log(inner)
    assert log["logical_rows_read"] == 0
    assert log["ops"]["records_out"] == 0 and log["ops"]["failed_records"] == 1


@pytest.mark.parametrize("published_at", [
    "2026-08-26T99:99:99", "2026-08-26", "not-a-timestamp",
])
def test_manifest_scope_rejects_invalid_or_naive_feature_timestamp(tmp_path, published_at):
    """WHY(ALPHA-1033): 날짜 접두사만 맞는 손상 시각이 manifest 검증을 우회하면 현재 ID가
    정상 범위로 인증되어 DB까지 도달한다. 전체 ISO 시각과 timezone까지 유효해야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("a1", [_assertion()], published_at=published_at)])
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["a1"])])

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 1
    log = _log(storage)
    assert log["logical_rows_read"] == 0
    assert log["ops"]["records_out"] == 0 and log["ops"]["failed_records"] == 1


def _moved_article_lake(storage, *, old_tagged_at: str, new_tagged_at: str) -> None:
    """한 기사가 두 파티션에 있는 레이크 — 08-27 판정과 08-28 판정.

    벤더가 같은 원문 URL 을 다른 날짜로 재등록한 실측 형태다(ALPHA-1051, 2026-08-28 8건).
    사건유형을 갈라 둬 **어느 파티션의 판정이 실렸는지**가 적재 행으로 드러나게 한다.
    """
    _write_feature(storage, "ko", "2026-08-27", [
        _feature_row("moved", [_assertion(event_type_code="OLD_PARTITION_JUDGEMENT")],
                     published_at="2026-08-27T11:14:07+09:00", tagged_at=old_tagged_at)])
    _write_feature(storage, "ko", "2026-08-28", [
        _feature_row("moved", [_assertion()],
                     published_at="2026-08-28T23:32:51+09:00", tagged_at=new_tagged_at)])


def test_moved_article_loads_the_latest_judgement_not_both(tmp_path, monkeypatch):
    """WHY(ALPHA-1051): `article_id` 는 원문 URL 해시라 불변인데 파티션 키 `published_date`
    는 벤더 재등록으로 **이동한다** — 옛 파티션 행은 아무도 안 지우므로 한 기사가 두
    파티션에 남는다. 이걸 manifest 손상으로 보고 죽으면 그 런의 범위가 통째로 유실된다
    (ALPHA-1052). 최신 판정만 싣고 계속 돌아야 한다."""
    inner = LocalStorage(tmp_path / "lake")
    _moved_article_lake(inner, old_tagged_at="2026-08-27T02:18:18+00:00",
                        new_tagged_at="2026-08-27T15:14:45+00:00")
    _write_feature_manifest(inner, "T1", [
        _manifest_partition("ko", "2026-08-27", ["moved"]),
        _manifest_partition("ko", "2026-08-28", ["moved"]),
    ])
    storage = _ReadSpy(inner)
    conn = _FakeConn(documents=[("moved", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    [(_, _, event_type, _, _, _)] = _inserts(conn, "document_assertion")
    assert event_type == "SUPPLY_CONTRACT"  # 08-28 판정 — 옛 파티션 판정은 안 실린다
    log = _log(storage)
    assert log["rows_moved_partitions"] == 1
    assert log["logical_rows_read"] == 1  # 두 물리 행이 논리 한 기사로 접혔다


def test_date_window_scope_folds_a_moved_article_by_the_same_rule(tmp_path, monkeypatch):
    """WHY(ALPHA-1051): 복구용 `--from/--to` 경로에는 파티션 간 처리가 없어, 두 판정이
    날짜와 무관한 자연키 fold 에서 조용히 섞였다(순회가 날짜 오름차순이라 **낡은 쪽**
    confidence 가 대표가 된다). 두 경로가 다른 사실을 만들면 안 된다(Rule 7).

    **여기선 늦은 파티션의 판정이 더 낡다** — 벤더가 미래 날짜를 되돌린 정정 형태다.
    이 배치라야 승자 축이 순회 순서가 아니라 `tagged_at` 임이 드러난다."""
    storage = LocalStorage(tmp_path / "lake")
    _moved_article_lake(storage, old_tagged_at="2026-08-28T09:00:00+00:00",
                        new_tagged_at="2026-08-27T15:14:45+00:00")
    conn = _FakeConn(documents=[("moved", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db(),
                               from_date="2026-08-27", to_date="2026-08-28") == 0

    [(_, _, event_type, _, _, _)] = _inserts(conn, "document_assertion")
    assert event_type == "OLD_PARTITION_JUDGEMENT"  # 늦은 파티션이어도 판정이 낡으면 진다
    assert _log(storage)["rows_moved_partitions"] == 1


def test_moved_article_tie_prefers_the_later_partition(tmp_path, monkeypatch):
    """WHY(ALPHA-1051): 한 런이 두 파티션을 다 바꾸면 배치가 찍는 `tagged_at`(런 시작
    시각)이 균일해 동률이 난다. 그때 승자가 순회에 흔들리면 같은 입력이 런마다 다른 행을
    만든다 — 날짜 오름차순 순회 + `>=` 로 **늦은 파티션**이 결정적으로 이겨야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _moved_article_lake(storage, old_tagged_at="2026-08-27T15:14:45+00:00",
                        new_tagged_at="2026-08-27T15:14:45+00:00")
    conn = _FakeConn(documents=[("moved", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db(),
                               from_date="2026-08-27", to_date="2026-08-28") == 0

    [(_, _, event_type, _, _, _)] = _inserts(conn, "document_assertion")
    assert event_type == "SUPPLY_CONTRACT"


def test_manifest_array_order_does_not_decide_the_tie(tmp_path, monkeypatch):
    """WHY(ALPHA-1051): 동률 승자가 manifest 배열 순서에 걸리면 **생산자 구현이 적재 행을
    정한다** — `_manifest_partitions` 는 날짜·키·파티션 중복만 보고 정렬은 검증하지 않으니
    뒤집힌 배열이 오면 같은 입력에 다른 주장이 실린다. 소비 순서는 이 파일이 세워야 한다."""
    inner = LocalStorage(tmp_path / "lake")
    _moved_article_lake(inner, old_tagged_at="2026-08-27T15:14:45+00:00",
                        new_tagged_at="2026-08-27T15:14:45+00:00")
    _write_feature_manifest(inner, "T1", [           # 배열이 날짜 내림차순 — 생산자가 뒤집어 썼다
        _manifest_partition("ko", "2026-08-28", ["moved"]),
        _manifest_partition("ko", "2026-08-27", ["moved"]),
    ])
    conn = _FakeConn(documents=[("moved", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(inner, "L1", db=_db(), input_run_id="T1") == 0

    [(_, _, event_type, _, _, _)] = _inserts(conn, "document_assertion")
    assert event_type == "SUPPLY_CONTRACT"  # 배열 순서와 무관하게 늦은 파티션이 이긴다


def _consumed_key(run_id: str) -> str:
    return run_manifest_consumed_key("feature", "news_assertions", run_id, "load_assertions")


def _two_runs_lake(storage, *, older_written: bool = True, older_started_at: str | None = None):
    """앞선 런 T0 과 이번 런 T1 의 manifest·feature — T0 은 아직 미소비다."""
    _write_feature(storage, "ko", "2026-08-25", [
        _feature_row("older", [_assertion(event_type_code="OLDER_RUN")],
                     published_at="2026-08-25T09:00:00+09:00")])
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("current", [_assertion()], published_at="2026-08-26T09:00:00+09:00")])
    storage.put_bytes(feature_run_manifest_key("news_assertions", "T0"), json.dumps({
        "run_id": "T0", "producer": "tag_news", "feature_written": older_written,
        "started_at": older_started_at or "2026-08-25T15:00:00+00:00",
        "feature_partitions": [_manifest_partition("ko", "2026-08-25", ["older"])],
    }).encode("utf-8"))
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["current"])])


def _carry_conn():
    return _FakeConn(documents=[("older", "doc_OLD"), ("current", "doc_CUR")])


def test_unconsumed_manifest_from_a_failed_run_is_carried_forward(tmp_path, monkeypatch):
    """WHY(ALPHA-1052): 이 스텝이 실패하면 그 범위는 다음 런 manifest 에 안 들어온다 —
    이미 태깅돼 생산자의 `changed_ids` 밖이기 때문이다. 아무도 다시 안 실으면 그 하루가
    영구 유실된다(2026-08-28 실발화). 다음 런이 미소비 범위를 **이어 실어야** 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    conn = _carry_conn()
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert loaded == {"OLDER_RUN", "SUPPLY_CONTRACT"}  # 앞선 런 범위까지 실렸다
    carry = _log(storage)["manifest_carry_forward"]
    assert (carry["pending"], carry["carried"]) == (1, 1)
    assert storage.list_keys(_consumed_key("T0")) and storage.list_keys(_consumed_key("T1"))


def test_overlapping_scopes_read_the_part_file_once(tmp_path, monkeypatch):
    """WHY(ALPHA-1052): 자기 범위와 회수 범위가 **같은 파티션**을 가리키는 것이 정상이다
    (인접 날짜, 그리고 파티션을 옮긴 기사 — ALPHA-1051). 소유자별로 따로 읽으면서 캐시가
    없으면 GET 도 `physical_rows_read` 도 두 배가 되어, 물리/논리 분리(ALPHA-1033)가
    말하려는 수가 거짓이 된다 — "누적 part 에 과거 행이 몇 개였나"를 못 읽는다."""
    inner = LocalStorage(tmp_path / "lake")
    _write_feature(inner, "ko", "2026-08-26", [
        _feature_row("older", [_assertion(event_type_code="OLDER_RUN")],
                     published_at="2026-08-26T08:00:00+09:00"),
        _feature_row("current", [_assertion()], published_at="2026-08-26T09:00:00+09:00"),
    ])
    inner.put_bytes(feature_run_manifest_key("news_assertions", "T0"), json.dumps({
        "run_id": "T0", "producer": "tag_news", "feature_written": True,
        "started_at": "2026-08-25T15:00:00+00:00",
        # ⚠️ `current` 가 **두 manifest 에 다 있다** — tag_news 가 두 런에서 같은 기사를
        # 다시 태깅하면(미러 흡수 뒤 재태깅) 정상으로 생기는 모양이다. 이게 없으면 두 범위가
        # 같은 파티션을 가리켜도 행이 안 겹쳐 겹침 처리가 시험되지 않는다.
        "feature_partitions": [_manifest_partition("ko", "2026-08-26", ["older", "current"])],
    }).encode("utf-8"))
    _write_feature_manifest(
        inner, "T1", [_manifest_partition("ko", "2026-08-26", ["current"])])
    storage = _ReadSpy(inner)
    _setup_carry(monkeypatch, _carry_conn())

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    part_key = _manifest_partition("ko", "2026-08-26", [])["key"]
    assert storage.get_calls.count(part_key) == 1     # 두 범위가 같은 part 를 한 번만 GET
    log = _log(inner)
    assert log["physical_rows_read"] == 2             # 4 면 이중 계수다
    # ⚠️ GET 만 한 번이면 충분하지 않다 — 같은 행을 두 범위가 각각 담으면 언어 단위 접기가
    # 그 차이를 **파티션 이동으로 계수해** ALPHA-1051 신호가 거짓이 된다. 이동은 없었다.
    assert log["rows_moved_partitions"] == 0
    assert log["rows_superseded"] == 0
    assert log["logical_rows_read"] == 2


def test_a_failing_carried_group_does_not_roll_back_this_run(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): ALPHA-1052 는 읽기·검증만 격리했고 **적재는 한 트랜잭션**이었다 —
    회수 행이 터지면 온전한 이번 런까지 롤백됐다. 그러면 같은 미소비 manifest 가 다음 런에도
    다시 들어와 창 만료(7일)까지 레인이 반복 정지한다. "회수는 보조 작업이라 이번 런을 안
    죽인다"는 ALPHA-1052 의 선언이 적재 단계에서 거짓이었다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    conn = _FakeConn(documents=[("older", "doc_OLD"), ("current", "doc_CUR")],
                     fail_on_event="OLDER_RUN")   # 회수 범위(T0)의 주장만 터진다
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert loaded == {"SUPPLY_CONTRACT"}          # 자기 범위는 살아서 커밋된다
    carry = _log(storage)["manifest_carry_forward"]
    assert carry["load_failed"] == 1 and carry["carried"] == 0
    assert storage.list_keys(_consumed_key("T0")) == []   # 다음 런이 다시 집는다
    assert storage.list_keys(_consumed_key("T1"))         # 자기 범위는 소비 완료


def test_one_poisoned_carried_run_does_not_block_the_others(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): 회수 전체를 savepoint 하나로 묶으면 오염된 manifest 하나가 건강한
    회수분까지 매 런 되돌려 **그쪽도 영영 수렴 못 한다** — ALPHA-1052 3라운드에서 상한을
    앞에서 잘랐을 때와 같은 굶주림이다. savepoint 는 회수 run 마다여야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    _write_feature(storage, "ko", "2026-08-24", [
        _feature_row("healthy", [_assertion(event_type_code="HEALTHY_RUN")],
                     published_at="2026-08-24T09:00:00+09:00")])
    storage.put_bytes(feature_run_manifest_key("news_assertions", "T00"), json.dumps({
        "run_id": "T00", "producer": "tag_news", "feature_written": True,
        "started_at": "2026-08-25T15:00:00+00:00",
        "feature_partitions": [_manifest_partition("ko", "2026-08-24", ["healthy"])],
    }).encode("utf-8"))
    conn = _FakeConn(
        documents=[("older", "doc_OLD"), ("current", "doc_CUR"), ("healthy", "doc_HEA")],
        fail_on_event="OLDER_RUN")
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert loaded == {"SUPPLY_CONTRACT", "HEALTHY_RUN"}   # 성한 회수분은 실린다
    assert storage.list_keys(_consumed_key("T00"))        # 그 run 은 소비 완료
    assert storage.list_keys(_consumed_key("T0")) == []   # 오염된 run 만 남는다


def test_a_rolled_back_group_claims_none_of_its_counters(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): **savepoint 는 DB 쓰기만 되돌린다 — 카운터는 파이썬 변수다.** 롤백된
    그룹이 이미 올린 `created`·`arguments_inserted`·해소율 분모가 그대로 남으면
    `ops.records_out` 이 실제 커밋 행보다 커진다. 그룹이 착지 못 했으면 그 그룹의 수는
    아무것도 안 실어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    # 회수 범위에 주장 둘 — 앞 하나는 INSERT 성공, 뒤에서 터진다. savepoint 가 앞 것도
    # 되돌리므로 카운터도 앞 것까지 되돌아가야 한다.
    _write_feature(storage, "ko", "2026-08-25", [
        _feature_row("older", [_assertion(event_type_code="LANDS_FIRST"),
                               _assertion(event_type_code="OLDER_RUN")],
                     published_at="2026-08-25T09:00:00+09:00")])
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("current", [_assertion()], published_at="2026-08-26T09:00:00+09:00")])
    storage.put_bytes(feature_run_manifest_key("news_assertions", "T0"), json.dumps({
        "run_id": "T0", "producer": "tag_news", "feature_written": True,
        "started_at": "2026-08-25T15:00:00+00:00",
        "feature_partitions": [_manifest_partition("ko", "2026-08-25", ["older"])],
    }).encode("utf-8"))
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["current"])])
    conn = _FakeConn(documents=[("older", "doc_OLD"), ("current", "doc_CUR")],
                     fail_on_event="OLDER_RUN")
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    log = _log(storage)
    # 자기 범위 한 건만 실렸다 — 롤백된 그룹의 LANDS_FIRST 는 어느 수에도 안 든다
    assert log["created"] == 1
    assert log["ops"]["records_out"] == 1
    assert log["arguments_inserted"] == 1
    assert log["argument_resolution"]["total"] == 1
    assert len(log["created_rows_sample"]) == 1


def test_a_rolled_back_group_blocks_every_manifest_claiming_its_articles(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): 후보는 한 그룹에만 들어가지만 **기사는 여러 manifest 가 주장한다**
    (재태깅으로 정상 생긴다). 겹치는 기사를 앞 그룹이 가져갔는데 그 그룹이 롤백되면, 뒤
    manifest 의 그룹은 비어 있어 "성공"으로 보이고 마커를 받는다 — 그러면 그 기사는 어느
    manifest 로도 다시 안 실린다(앞 manifest 는 창 만료 때 skipped 로 닫힌다)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-08-25", [
        _feature_row("shared", [_assertion(event_type_code="OLDER_RUN")],
                     published_at="2026-08-25T09:00:00+09:00")])
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("current", [_assertion()], published_at="2026-08-26T09:00:00+09:00")])
    for run_id in ("T0", "T00"):        # 둘 다 같은 기사를 주장한다
        storage.put_bytes(feature_run_manifest_key("news_assertions", run_id), json.dumps({
            "run_id": run_id, "producer": "tag_news", "feature_written": True,
            "started_at": "2026-08-25T15:00:00+00:00",
            "feature_partitions": [_manifest_partition("ko", "2026-08-25", ["shared"])],
        }).encode("utf-8"))
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["current"])])
    conn = _FakeConn(documents=[("shared", "doc_SHR"), ("current", "doc_CUR")],
                     fail_on_event="OLDER_RUN")
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    # 후보를 안 가진 쪽도 그 기사를 주장하므로 미소비로 남는다
    assert storage.list_keys(_consumed_key("T0")) == []
    assert storage.list_keys(_consumed_key("T00")) == []
    assert storage.list_keys(_consumed_key("T1"))      # 자기 범위는 온전하다

    # 차단 사유는 섞이면 안 된다 — 문서 결손과 그룹 롤백은 대응이 다르다(전자는 상류
    # LoadDocuments 재실행, 후자는 그 manifest 의 데이터 문제). 결손은 한 건도 없었다.
    # ⚠️ 이 단언은 **T00 이라야 도달한다**: 적재가 터진 T0 는 `failed_carried` 로 이미
    # eligible 에서 빠져, 겹치는 기사를 주장하는 다른 manifest 가 없으면 이 분기가 안 돈다.
    log = _log(storage)
    assert log["missing_document"] == 0
    carry = log["manifest_carry_forward"]
    assert carry["blocked_by_missing_document"] == 0
    assert carry["blocked_by_rollback"] == 1
    assert carry["load_failed"] == 1


def test_a_concept_minted_in_two_groups_is_counted_once(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): 채번을 그룹 안으로 옮기면서 `concepts_minted` 가 그룹별 **개수의 합**이
    됐다. 같은 MINT 대상이 자기 범위와 회수 범위에 다 있으면 DB 에는 `ON CONFLICT DO NOTHING`
    으로 한 행인데 로그는 둘이라 한다 — 전역 dict 였을 땐 그게 접혔다. 그룹 분리가 만든 회귀다."""
    storage = LocalStorage(tmp_path / "lake")
    same_metric = [{"role_code": "METRIC", "text": "매출", "entity_id": None},
                   {"role_code": "ISSUER", "text": "삼성전자", "entity_id": None}]
    _write_feature(storage, "ko", "2026-08-25", [
        _feature_row("older", [_assertion(event_type_code="OLDER_RUN",
                                          arguments=same_metric)],
                     published_at="2026-08-25T09:00:00+09:00")])
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("current", [_assertion(arguments=same_metric)],
                     published_at="2026-08-26T09:00:00+09:00")])
    storage.put_bytes(feature_run_manifest_key("news_assertions", "T0"), json.dumps({
        "run_id": "T0", "producer": "tag_news", "feature_written": True,
        "started_at": "2026-08-25T15:00:00+00:00",
        "feature_partitions": [_manifest_partition("ko", "2026-08-25", ["older"])],
    }).encode("utf-8"))
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["current"])])
    conn = _carry_conn()
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert loaded == {"SUPPLY_CONTRACT", "OLDER_RUN"}   # 두 그룹 다 커밋됐다(전제)
    minted = {params[0] for params in _inserts(conn, "concept")}
    assert len(minted) == 1, "같은 MINT 대상인데 개념 행이 둘이다"
    assert _log(storage)["concepts_minted"] == 1, "그룹마다 세어 DB 행보다 크게 보고했다"


def test_a_rolled_back_group_does_not_claim_its_minted_concepts(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): 채번(entity·concept)이 그룹 밖에 있으면 롤백된 그룹의 개념이 로그에
    남아 **만들지 않은 마스터를 만들었다고 주장한다**(ALPHA-830 과 같은 자리). 이번 세션에서
    계측 거짓을 네 번 잡았다 — 채번도 롤백 단위 안이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-08-25", [
        _feature_row("older", [_assertion(event_type_code="OLDER_RUN", arguments=[
            # ⚠️ 채번되는 역할이어야 한다 — ISSUER/미등록회사는 **미해소**일 뿐 채번이
            # 아니라서, 그걸로는 이 테스트가 채번 경로를 한 번도 안 밟고 통과한다.
            {"role_code": "METRIC", "text": "매출", "entity_id": None},
            {"role_code": "ISSUER", "text": "삼성전자", "entity_id": None},
        ])], published_at="2026-08-25T09:00:00+09:00")])
    _write_feature(storage, "ko", "2026-08-26", [
        _feature_row("current", [_assertion()], published_at="2026-08-26T09:00:00+09:00")])
    storage.put_bytes(feature_run_manifest_key("news_assertions", "T0"), json.dumps({
        "run_id": "T0", "producer": "tag_news", "feature_written": True,
        "started_at": "2026-08-25T15:00:00+00:00",
        "feature_partitions": [_manifest_partition("ko", "2026-08-25", ["older"])],
    }).encode("utf-8"))
    _write_feature_manifest(
        storage, "T1", [_manifest_partition("ko", "2026-08-26", ["current"])])
    conn = _FakeConn(documents=[("older", "doc_OLD"), ("current", "doc_CUR")],
                     fail_on_event="OLDER_RUN")
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    # 롤백된 그룹이 세운 개념은 로그가 주장하지 않는다
    log = _log(storage)
    assert log["concepts_minted"] == 0        # 롤백된 그룹의 채번을 주장하지 않는다
    assert _inserts(conn, "concept") == []    # savepoint 가 그 INSERT 도 되돌린다


def test_own_scope_failure_still_fails_the_whole_run(tmp_path, monkeypatch):
    """WHY(ALPHA-1053): 격리는 **회수 범위에만**이다. 자기 범위 실패까지 삼키면 이 런의
    계약이 깨진 채 exit 0 이 되고, 소비 마커까지 남아 그 범위가 영영 안 실린다 — 이 PR 이
    막으려는 유실을 정반대 방향으로 만든다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    conn = _FakeConn(documents=[("older", "doc_OLD"), ("current", "doc_CUR")],
                     fail_on_event="SUPPLY_CONTRACT")   # 자기 범위(T1)의 주장이 터진다
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 1
    assert storage.list_keys(_consumed_key("T1")) == []
    assert storage.list_keys(_consumed_key("T0")) == []
    assert _log(storage)["failures"][0]["reasons"] == ["load_error"]


def test_consumed_manifest_is_not_carried_again(tmp_path, monkeypatch):
    """WHY(ALPHA-1052): 마커가 곧 "소비됐다"의 증거다. 이걸 안 보면 매 런이 과거 전체를
    다시 실어 비용이 런 수에 비례해 자란다 — manifest 범위 제한(ALPHA-1033)이 무의미해진다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    storage.put_bytes(_consumed_key("T0"), b"{}")
    conn = _carry_conn()
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert loaded == {"SUPPLY_CONTRACT"}  # T0 은 이미 소비됐다
    assert _log(storage)["manifest_carry_forward"]["pending"] == 0


def test_failed_load_writes_no_consumed_marker(tmp_path, monkeypatch):
    """WHY(ALPHA-1052): **이 단언이 장치 전체의 안전핀이다.** 실패한 런이 마커를 남기면 그
    범위가 "소비됨"으로 굳어 다시는 안 실린다 — 회수 장치가 스스로 영구 유실을 만든다."""
    from contextlib import contextmanager

    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    _setup_carry(monkeypatch, _FakeConn())

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_assertions, "connect", _boom)
    monkeypatch.setattr(load_assertions, "load_resolution_index", lambda c: _INDEX)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 1
    assert storage.list_keys(_consumed_key("T1")) == []
    assert storage.list_keys(_consumed_key("T0")) == []


@pytest.mark.parametrize("door", ["manifest_json", "vanished_article", "wrong_partition_date"])
def test_a_broken_carried_scope_never_kills_this_run(tmp_path, monkeypatch, door):
    """WHY(ALPHA-1052): 회수 범위가 깨지는 **문이 하나가 아니다**. manifest JSON 손상만
    막았더니 나머지가 그대로 열려 있었다(edge-review 3라운드) — manifest 는 멀쩡한데 그
    범위의 parquet 검증이 죽는 경로다. 옛 manifest 가 가리키는 article_id 는 그 사이
    되쓰기로 part 에서 사라질 수 있고(ALPHA-982), 그러면 매 후속 런이 같은 자리에서 죽어
    **레인이 창 만료까지 정지한다** — 회수 장치가 고치려던 것보다 나쁜 정지를 만든다.

    문마다 이번 런은 살아야 하고, 그 회수 범위는 마커를 못 받아 다음 런이 다시 집어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    if door == "manifest_json":
        storage.put_bytes(feature_run_manifest_key("news_assertions", "T0"), b"{ not json")
    elif door == "vanished_article":
        # manifest 는 "older" 를 가리키는데 그 파티션의 part 가 되쓰여 사라졌다
        _write_feature(storage, "ko", "2026-08-25", [
            _feature_row("someone-else", [_assertion()],
                         published_at="2026-08-25T09:00:00+09:00")])
    else:
        # part 는 있는데 그 행의 published_at 이 파티션 날짜와 어긋난다
        _write_feature(storage, "ko", "2026-08-25", [
            _feature_row("older", [_assertion()], published_at="2026-08-24T09:00:00+09:00")])
    conn = _carry_conn()
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0  # 제 범위는 산다
    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert loaded == {"SUPPLY_CONTRACT"}
    carry = _log(storage)["manifest_carry_forward"]
    assert (carry["pending"], carry["carried"], carry["failed"]) == (1, 0, 1)
    # 읽기 실패는 문서 결손이 아니다 — 섞으면 두 사유가 로그에서 안 갈린다
    assert carry["blocked_by_missing_document"] == 0
    assert storage.list_keys(_consumed_key("T0")) == []   # 다음 런이 다시 집는다
    assert storage.list_keys(_consumed_key("T1"))         # 제 범위는 소비 완료다


def test_stale_manifest_is_closed_so_discovery_converges(tmp_path, monkeypatch):
    """WHY(ALPHA-1052): 창 밖으로 밀린 manifest 는 영원히 소비되지 않는다 — 닫지 않으면 매
    런이 그걸 다시 GET 하고 다시 세, 탐색 비용과 `pending` 이 영원히 자란다(비수렴).
    창 밖 판정은 단조(시간은 한 방향)라 한 번만 하면 된다. 마커 이름이 `consumed` 가
    아니라 `skipped` 인 것이 핵심이다 — "안 싣고 닫았다"가 "실었다"로 둔갑하면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage, older_started_at="2026-01-01T00:00:00+00:00")
    _setup_carry(monkeypatch, _carry_conn())

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    assert _log(storage)["manifest_carry_forward"]["stale"] == 1
    skipped = run_manifest_skipped_key("feature", "news_assertions", "T0", "load_assertions")
    assert storage.list_keys(skipped)
    assert storage.list_keys(_consumed_key("T0")) == []   # 실은 것이 아니다

    body = json.loads(storage.get_bytes(skipped).decode("utf-8"))
    assert body["reason"] == "lookback_expired"

    # 다음 런은 이걸 다시 안 본다 — 그게 수렴이다
    storage2 = _ReadSpy(storage)
    _write_feature_manifest(
        storage, "T2", [_manifest_partition("ko", "2026-08-26", ["current"])])
    _setup_carry(monkeypatch, _carry_conn())
    assert load_assertions.run(storage2, "L2", db=_db(), input_run_id="T2") == 0
    [second] = [k for k in storage.list_keys("operations_archive/") if "run_id=L2" in k]
    assert json.loads(storage.get_bytes(second))["manifest_carry_forward"]["pending"] == 0
    assert feature_run_manifest_key("news_assertions", "T0") not in storage2.get_calls


@pytest.mark.parametrize("case,expect", [("unfinished", "unfinished")])
def test_uncarryable_manifest_is_counted_not_silently_dropped(tmp_path, monkeypatch, case, expect):
    """WHY(ALPHA-1052): 못 싣는 두 부류가 있다 — 생산자가 안 끝낸 manifest(`feature_written`
    이 false 로 남았다)와 되돌아보기 창 밖의 것(그 사이 part 가 여러 번 되쓰여 `missing`
    검증이 죽을 수 있다). 둘 다 **안 싣는 게 맞지만 조용히 버리면 안 된다** — 사유별 수치가
    없으면 "회수가 도는데 왜 그 범위는 안 오나"를 로그만으로 설명할 수 없다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(
        storage,
        older_written=(case != "unfinished"),
        older_started_at="2026-01-01T00:00:00+00:00" if case == "stale" else None,
    )
    conn = _carry_conn()
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0
    carry = _log(storage)["manifest_carry_forward"]
    assert (carry["pending"], carry["carried"], carry[expect]) == (1, 0, 1)
    assert storage.list_keys(_consumed_key("T0")) == []


@pytest.mark.parametrize("missing,unconsumed,consumed", [
    ("current", "T1", "T0"),   # 이번 런 범위가 결손 — 회수한 T0 은 온전히 실렸다
    ("older", "T0", "T1"),     # 회수 범위가 결손 — 이번 런 T1 은 온전히 실렸다
])
def test_missing_document_blocks_only_its_own_manifest(
    tmp_path, monkeypatch, missing, unconsumed, consumed
):
    """WHY(ALPHA-1052): `missing_document` 는 **exit 0 인 결손**이고, 그 회수 수단이 바로
    "같은 input_run_id 재실행"이다(ALPHA-1033). exit 0 만 보고 마커를 남기면 그 재실행
    자격이 사라진다 — 그래서 결손이 있는 manifest 는 미소비로 남아야 한다.

    **그런데 판정은 manifest 별이어야 한다.** 전역 카운터로 막으면 한 범위의 결손이 온전히
    실린 다른 범위의 마커까지 막아, 그 범위가 매 런 다시 실리고 pending 이 무한히 자란다 —
    수렴과 온전한 회수를 동시에 못 준다. 두 방향을 다 건다: 결손이 이번 런 쪽일 때와
    회수 쪽일 때, 막히는 것은 **그쪽 하나뿐**이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    present = "older" if missing == "current" else "current"
    conn = _FakeConn(documents=[(present, f"doc_{present.upper()}")])
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    log = _log(storage)
    assert log["missing_document"] == 1
    assert log["manifest_carry_forward"]["blocked_by_missing_document"] == 1
    assert storage.list_keys(_consumed_key(unconsumed)) == []
    assert storage.list_keys(_consumed_key(consumed))


def test_uncarryable_manifests_do_not_starve_the_queue(tmp_path, monkeypatch):
    """WHY(ALPHA-1052): 상한을 `pending[:N]` 으로 앞에서 자르면, 영구히 못 싣는 것이 앞자리를
    차지했을 때 그 뒤는 **검사조차 안 돼 영원히 굶는다**. run_id 는 해시라 사전순이 시간순도
    아니어서 어느 것이 앞에 올지 정해져 있지도 않다 — 상한은 실제로 실은 수에만 걸려야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _two_runs_lake(storage)
    for i in range(6):  # _CARRY_MAX_RUNS 보다 많은, 영원히 못 싣는 manifest 가 앞에 깔린다
        storage.put_bytes(feature_run_manifest_key("news_assertions", f"A{i}"), json.dumps({
            "run_id": f"A{i}", "producer": "tag_news", "feature_written": False,
            "started_at": "2026-08-25T15:00:00+00:00", "feature_partitions": [],
        }).encode("utf-8"))
    conn = _carry_conn()
    _setup_carry(monkeypatch, conn)

    assert load_assertions.run(storage, "L1", db=_db(), input_run_id="T1") == 0

    loaded = {event for _, _, event, _, _, _ in _inserts(conn, "document_assertion")}
    assert "OLDER_RUN" in loaded  # 뒤에 있던 정상 manifest 가 굶지 않았다
    carry = _log(storage)["manifest_carry_forward"]
    assert (carry["unfinished"], carry["carried"], carry["over_limit"]) == (6, 1, 0)


def test_new_assertion_lands_with_resolved_argument(tmp_path, monkeypatch):
    """주장 1건 = document_assertion 1행 + 해소된 argument — document_id 는 자연키로
    해소된 실제 행이어야 FK 가 살고, available_at 은 **그 document 의 가용 시각**이다.
    추출 시각(tagged_at)을 실으면 주장의 PIT 가 프로세스 시각으로 오염된다(ALPHA-538)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    [(assertion_id, document_id, event_type, predicate, confidence, available_at)] = \
        _inserts(conn, "document_assertion")
    assert assertion_id.startswith("asrt_")  # ADR-0027
    assert (document_id, event_type, predicate) == ("doc_D1", "SUPPLY_CONTRACT", "WIN")
    assert confidence == 0.9
    assert available_at == _DOC_AVAILABLE_AT  # tagged_at(02:00Z) 아님 — document 파생
    [(arg_assertion_id, role, entity_id, arg_conf)] = _inserts(conn, "assertion_argument")
    assert arg_assertion_id == assertion_id
    assert (role, entity_id) == ("ISSUER", "inst_SAMSUNG")


def test_existing_assertion_unions_arguments_under_existing_id(tmp_path, monkeypatch):
    """멱등 + 소유 컬럼 착지(ALPHA-538) — 자연키가 이미 있으면(assemble-events 의 비계
    선생성 포함) 새 행을 만들지 않고 **그 행의 assertion_id** 로 arguments 만 union 하되,
    소유 컬럼 confidence 는 UPDATE 로 확정 착지한다. 아니면 행 생성 경주에서 진 쪽의
    판정이 조용히 유실돼 최종 행이 실행 순서를 탄다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")],
                     existing_assertions={("doc_D1", "SUPPLY_CONTRACT", "WIN")})
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    log = _log(storage)
    assert log["created"] == 0 and log["already_present"] == 1
    [(upd_conf, upd_doc, upd_event, upd_pred)] = [
        p for sql, p in conn.log if sql.upper().startswith("UPDATE DOCUMENT_ASSERTION")]
    assert (upd_conf, upd_doc, upd_event, upd_pred) == (0.9, "doc_D1", "SUPPLY_CONTRACT", "WIN")
    [(arg_assertion_id, _, entity_id, _)] = _inserts(conn, "assertion_argument")
    assert arg_assertion_id == "asrt_EXISTING"
    assert entity_id == "inst_SAMSUNG"


def test_unresolved_only_assertion_is_skipped_and_counted(tmp_path, monkeypatch):
    """argument 가 전무 해소된 주장은 넣지 않는다 — 엔티티 연결 없는 행은 event 조립에
    죽은 행이고, skip 은 사유별 수치로 남아야 한다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [
        _assertion(text="미등록회사"),
        _assertion(text="충돌이름", event_type_code="LITIGATION"),
    ])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "document_assertion") == []
    log = _log(storage)
    assert log["skipped_no_resolved_argument"] == 2
    res = log["argument_resolution"]
    assert res["total"] == 2 and res["unresolved"] == 1 and res["ambiguous"] == 1
    assert res["rate"] == 0.0
    assert ["미등록회사", 1] in [list(x) for x in res["top_unresolved"]] or \
           ("미등록회사", 1) in [tuple(x) for x in res["top_unresolved"]]


def test_partial_assertion_is_not_persisted_as_confirmed(tmp_path, monkeypatch):
    """completeness='partial'(필수 역할 결손 표시)은 적재하지 않는다 — 스키마에 완결성
    컬럼이 없어 실으면 확정 주장과 구분 불가가 된다(Codex #133 P1). feature 존에 원본이
    남고, skip 은 수치로 남는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [
        _assertion(),
        _assertion(event_type_code="LITIGATION", completeness="partial",
                   missing_required_roles=["COUNTERPARTY"]),
    ])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "document_assertion")) == 1
    assert _log(storage)["skipped_partial"] == 1


def test_missing_document_is_skipped_not_a_broken_fk(tmp_path, monkeypatch):
    """document 행이 없으면(로더 선행 전) FK 위반으로 죽는 대신 결손으로 세고 넘어간다 —
    정상 manifest 경로는 같은 input_run_id 재실행으로 회수한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a-없음", [_assertion()])])
    conn = _FakeConn(documents=[])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert _inserts(conn, "document_assertion") == []
    assert _log(storage)["missing_document"] == 1


def test_not_ok_and_malformed_rows_are_isolated(tmp_path, monkeypatch):
    """status!=ok 행·깨진 assertions JSON 은 행 단위로 격리된다 — 한 이상치가 배치를
    무너뜨리면(crash-before-gate) 그 날 주장 전체가 안 선다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [
        _feature_row("a1", [_assertion()]),
        _feature_row("a2", [], status="llm_error"),
        _feature_row("a3", "{broken json"),
        _feature_row("a4", []),  # 사건 없는 기사
    ])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "document_assertion")) == 1
    log = _log(storage)
    assert log["rows_not_ok"] == 1
    assert log["rows_malformed"] == 1
    assert log["rows_no_assertion"] == 1


def test_same_assertion_twice_is_folded_once(tmp_path, monkeypatch):
    """같은 문서·사건유형·서술의 재주장은 자연키가 하나라 주장도 하나 — 두 번 넣으면
    uq_document_assertion_natural 에 걸리기 전에 런 안에서 접혀야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [
        _assertion(text="삼성전자"),
        _assertion(text="SK하이닉스"),  # 같은 자연키, 다른 argument → union
    ])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn, "document_assertion")) == 1
    args = {(p[1], p[2]) for p in _inserts(conn, "assertion_argument")}
    assert args == {("ISSUER", "inst_SAMSUNG"), ("ISSUER", "inst_HYNIX")}
    assert _log(storage)["assertions_folded"] == 1


def test_db_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 비0 종료 + 로그 — 롤백된 런의 created 를 로그가 주장하면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_assertions, "connect", _boom)

    assert load_assertions.run(storage, "R1", db=_db()) == 1, "실패가 성공으로 위장됐다"
    log = _log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["created"] == 0 and log["arguments_inserted"] == 0


def test_assertion_id_is_derived_from_the_natural_key(tmp_path, monkeypatch):
    """적재되는 assertion_id 가 자연키에서 결정적으로 나온다(ALPHA-456).

    WHY: 이 테이블은 writer 가 둘이다(load-assertions·assemble-events). 산식이 갈리면
    ON CONFLICT DO NOTHING 때문에 **먼저 도는 이 스텝의 값이 남는데**, 그게 랜덤 ULID 면
    source_event_id = f(assertion_id, entity_id) 가 랜덤을 상속해 모듈이 선언한 "결정적 ID"
    계약이 조용히 깨진다 — document_assertion 을 재구축하면 계보 ID 가 전부 갈린다.

    두 스텝이 **같은 함수**를 쓰는지까지 함께 고정한다. 각자 같은 모양의 해시를 따로
    구현하면 salt·구분자 하나 차이로 다시 갈리기 때문이다.
    """
    from data_pipeline.db import stable_domain_id
    from data_pipeline.steps import assemble_events

    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    [(assertion_id, *_rest)] = _inserts(conn, "document_assertion")
    natural_key = ("doc_D1", "SUPPLY_CONTRACT", "WIN")
    assert assertion_id == stable_domain_id("asrt", *natural_key)
    # 같은 자연키에 assemble-events 도 같은 값을 낸다 — 공유가 끊기면 여기서 깨진다
    assert assertion_id == assemble_events._stable_id("asrt", *natural_key)
    # 구분자가 재료 경계를 고정한다("ab"+"c" 와 "a"+"bc" 가 같은 값이 되면 안 된다)
    assert stable_domain_id("asrt", "ab", "c") != stable_domain_id("asrt", "a", "bc")


def _args(*pairs) -> list[dict]:
    return [{"role_code": r, "text": t, "entity_id": None} for r, t in pairs]


def test_non_entity_roles_are_out_of_the_resolution_denominator(tmp_path, monkeypatch):
    """해소율 분모는 **실체 역할만** 센다(ALPHA-802).

    WHY: 온톨로지 `role_kinds.non_entity`(TIME·VALUE·TEXT)는 실체를 가리키지 않는
    자리다(온톨로지가 "적재하지 않는다"고 정한 대상은 `event_argument` — 여기선 분모에서만
    뺀다. assertion_argument 에는 아직 실리고, 그건 아래 네 번째 테스트가 못박는다). 그걸 미해소로 세면 분모가 30.7%(08-05 실측) 부풀고, 그 부푼 분모
    위에서는 뒤따르는 마스터 확대(ALPHA-830)가 실제로 몇 %를 회수했는지 잴 수 없다 —
    개선과 분모 변화가 같은 숫자 안에서 섞인다.

    "2분기"가 별칭 후보 목록에 오르지 않는 것까지 함께 고정한다. 비실체 표현이 섞이면
    top_unresolved 가 마스터 확대 판단의 근거로 못 쓰인다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"),
                        ("REPORTING_PERIOD", "2분기"),
                        ("ACTUAL_VALUE", "1조원")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    assert res["total"] == 1 and res["resolved"] == 1 and res["rate"] == 1.0
    assert res["role_kinds"] == {"entity": 1, "non_entity": 2, "out_of_vocabulary": 0}
    assert [t for t, _ in res["top_unresolved"]] == []
    # 로그가 자기 분모 정의를 들고 있어야 ALPHA-802 이전 로그와 나란히 놓고 비교할 수 있다
    assert res["denominator"] == "entity_roles_only"


def test_out_of_vocabulary_role_is_named_not_just_counted(tmp_path, monkeypatch, caplog):
    """어휘 밖 역할은 **이름까지** 남긴다(ALPHA-802).

    WHY: 어휘 밖은 추출단과 온톨로지가 갈렸다는 신호인데, 이 자리는 전부 분모 밖으로
    빠져 **해소율을 대표성 없는 수로 만든다**(어느 방향으로 틀리는지는 로그로 알 수
    없다 — 근거는 로더의 경고 블록 주석). 개수만 세면 어느 역할이 샜는지 몰라 고칠 수가
    없다 — 표본을 20개로 자르던 것과 같은 실패 양식이다(Rule 12).

    어휘 밖이 `non_entity_resolved` 에 섞이지 않는 것도 함께 고정한다. 그 수는 역할별
    해소 분기(ALPHA-831)가 **걷어낼 적재량**의 근거인데, 어휘 밖은 걷어낼 계약이 없는
    행이라 섞이면 다음 티켓이 틀린 크기를 보고 계획한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 어휘 밖 역할에 **해소되는** 텍스트를 준다 — 안 그러면 두 카운터의 차이가 안 드러난다
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"), ("NOT_A_ROLE", "SK하이닉스")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    import logging
    with caplog.at_level(logging.WARNING, logger=load_assertions.logger.name):
        assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    assert res["role_kinds"] == {"entity": 1, "non_entity": 0, "out_of_vocabulary": 1}
    assert res["out_of_vocabulary_roles"] == {"NOT_A_ROLE": 1}
    # 어휘 밖은 분모 밖이라 이 픽스처에선 rate 가 1.0 그대로다 — 드리프트가 rate 에
    # 안 나타나는 자리가 있다는 뜻이고, 그래서 개수가 아니라 **이름**이 필요하다.
    # (일반적으로 어느 방향으로 틀리는지는 정해져 있지 않다 — 로더의 경고 블록 주석)
    assert res["total"] == 1 and res["rate"] == 1.0
    # 어휘 밖 역할은 해소 축을 모른다 — 계약이 없으면 해소도 없다(ALPHA-831).
    # ISSUER 쪽 1행만 실린다.
    assert len(_inserts(conn, "assertion_argument")) == 1
    assert _inserts(conn, "assertion_argument")[0][1] == "ISSUER"
    assert res["non_entity_resolved"] == 0
    # 로그 파일을 열어야 보이는 수치로 두지 않는다 — 런 로그에서 드러나야 한다(Rule 12).
    # 이 단언이 없으면 WARNING 을 통째로 지워도 위 단언들이 전부 통과한다
    # 로거 이름으로도 좁힌다 — caplog 은 root 에 붙어 남의 WARNING 까지 담으므로, 이름을
    # 안 거르면 무관한 라이브러리 경고 하나에 언팩이 터져 엉뚱한 이유로 실패한다
    [warned] = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and r.name == load_assertions.logger.name]
    assert "NOT_A_ROLE" in warned


def test_missing_role_is_dropped_not_invented_as_issuer(tmp_path, monkeypatch, caplog):
    """역할이 비면 채우지 않고 탈락시킨다(ALPHA-802).

    WHY: `role_code or "ISSUER"` 는 해소되는 텍스트를 만나면 **없던 역할을 만들어**
    적재한다. 이 테이블엔 출처 컬럼이 없고, `resolved_args` 가 `(역할, entity_id)` 로
    접기 때문에 지어낸 ISSUER 는 같은 엔티티의 진짜 ISSUER 와 **한 행으로 합쳐진다** —
    합쳐진 뒤에는 feature 원본과 대조해도 못 갈라진다. 미해소로 남는 편이 틀린 주체로
    적재되는 것보다 낫다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args((None, "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    import logging
    with caplog.at_level(logging.WARNING, logger=load_assertions.logger.name):
        assert load_assertions.run(storage, "R1", db=_db()) == 0

    # 텍스트는 해소되는 값이다 — 폴백이 살아 있으면 여기서 ISSUER 행이 실린다
    assert _inserts(conn, "assertion_argument") == []
    assert _inserts(conn, "document_assertion") == []
    log = _log(storage)
    assert log["argument_resolution"]["role_missing"] == 1
    assert log["skipped_no_resolved_argument"] == 1
    # 이 스텝에서 **적재 행 집합이 달라지는 유일한 경로**다. 그 사실이 런 로그에도
    # 떠야 한다 — 이 단언이 없으면 WARNING 을 통째로 지워도 스위트가 초록이다(Rule 12)
    [warned] = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and r.name == load_assertions.logger.name]
    assert "역할 없는 argument" in warned


def test_broken_ontology_fails_before_the_transaction_opens(tmp_path, monkeypatch):
    """온톨로지가 깨져 있으면 **커넥션을 열기 전에** 실패로 끝난다.

    WHY: 이 스텝은 계약 자원을 둘 읽는다 — 역할표(`load_relations`)와 기관 명부
    (`load_authority_registry`). 둘 중 하나라도 트랜잭션 안에서 처음 건드리면 깨진 어휘가
    **열린 트랜잭션 한복판**에서 터진다. 그러면 부분 적재를 되감아야 하고, 그 롤백이
    실패하는 경로까지 새로 생긴다. 경계 밖에서 죽으면 되감을 것이 애초에 없다.

    두 자원 **각각**을 깨뜨린다 — 한쪽만 검사하면 나머지 하나가 트랜잭션 안으로
    미끄러져도 초록이다(실제로 명부가 그 상태였다).
    """
    for resource in ("load_relations", "load_authority_registry"):
        # ⚠️ **매 회차 패치를 되돌린다.** 한 monkeypatch 를 루프 내내 쓰면 2회차가
        # 1회차의 깨진 자원을 그대로 물려받아, 두 번째 축이 **첫 번째 이유로** 초록이
        # 된다(이 테스트가 실제로 그랬다). 축을 하나씩만 깨야 축이 하나씩 고정된다.
        with monkeypatch.context() as mp:
            storage = LocalStorage(tmp_path / f"lake-{resource}")
            _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
            conn = _FakeConn(documents=[("a1", "doc_D1")])
            _setup(mp, conn)

            def _boom(*a, **k):
                raise ValueError("어휘가 깨졌다")

            mp.setattr(load_assertions, resource, _boom)
            # 커넥션이 열렸는지를 기록한다 — 열렸다면 그 예외는 트랜잭션 안에서 난 것이다
            opened: list = []
            real_connect = load_assertions.connect
            mp.setattr(load_assertions, "connect",
                       lambda cfg: (opened.append(cfg), real_connect(cfg))[1])

            assert load_assertions.run(storage, "R1", db=_db()) != 0, \
                f"{resource} 가 깨졌는데 적재가 성공으로 끝났다"
            assert opened == [], f"{resource} 검증이 트랜잭션 안으로 미끄러졌다"
            assert conn.log == [], "커넥션을 열지도 않았는데 SQL 이 돌았다"


def test_a_leaking_non_entity_branch_is_warned_not_only_counted(tmp_path, monkeypatch,
                                                                 caplog):
    """비실체가 해소되면 **경고로 운다** — 계약 위반은 품질 로그를 열어야 보이면 안 된다.

    WHY: ALPHA-831 이 이 경로를 닫아 `non_entity_resolved == 0` 이 **계약**이 됐다. 0 이
    아니면 역할별 분기가 샌 것이고, 그건 전망 문구가 종목 참조로 둔갑해 계보가 거짓이
    된다는 뜻이다. 그런 위반이 S3 품질 로그의 한 필드로만 남으면 아무도 안 본다 —
    옆자리 두 형제(`unknown_roles`·`role_missing`)와 같은 자리에서 울어야 한다(Rule 12).

    분기를 닫아 놨으므로 공개 경로로는 이 상태를 만들 수 없다. 해소기를 갈아 끼워
    **가드가 실제로 우는지**만 본다 — 백스톱은 도달 불가라고 안 검사하면 조용히 죽는다.
    """
    import logging

    from data_pipeline.entity_resolution import resolve

    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"), ("OUTLOOK", "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)
    # 역할과 무관하게 붙이던 옛 해소기 — ALPHA-831 이전의 그 동작이다
    monkeypatch.setattr(load_assertions, "plan_resolution",
                        lambda index, role, text: (*resolve(index, text), None))

    with caplog.at_level(logging.WARNING, logger=load_assertions.logger.name):
        assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert _log(storage)["argument_resolution"]["non_entity_resolved"] == 1
    warned = [r.getMessage() for r in caplog.records
              if r.levelno >= logging.WARNING and r.name == load_assertions.logger.name]
    assert any("비실체가 인덱스로 해소" in m for m in warned), warned


def test_non_entity_role_is_not_loaded_even_if_the_text_matches_a_ticker(tmp_path, monkeypatch):
    """비실체 자리는 텍스트가 인덱스에 걸려도 **적재하지 않는다**(ALPHA-831).

    WHY: 역할이 해소 축을 정한다. `OUTLOOK`(자유서술)에 우연히 "삼성전자"가 들어왔다고
    그걸 삼성전자 주식으로 해소하면, 전망 문구가 종목 참조로 둔갑해 계보가 거짓이 된다.
    예전엔 역할과 무관하게 instrument 인덱스 하나에 때려서 이게 실제로 적재됐다 —
    ALPHA-802 가 `non_entity_resolved` 로 그 수를 세어 뒀고(전량 재실행 실측 4건),
    이 티켓이 그 경로를 닫는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("OUTLOOK", "삼성전자")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert _inserts(conn, "assertion_argument") == []      # ← 더는 실리지 않는다
    assert _inserts(conn, "document_assertion") == []      # 유일한 인자였으므로 주장도 빠진다
    res = _log(storage)["argument_resolution"]
    assert res["total"] == 0 and res["rate"] is None       # 분모 밖인 것은 그대로
    assert res["non_entity_resolved"] == 0


def test_sample_keys_are_trimmed_so_frequency_ranking_survives(tmp_path, monkeypatch):
    """표본 키는 공백을 턴다 — 안 그러면 빈도 순위가 갈린다(ALPHA-857).

    WHY: 이 표본의 **유일한** 용도가 빈도 순위다(무엇을 먼저 붙일 수 있게 만들까). 같은
    표현이 앞뒤 공백 유무로 별개 행이 되면 빈도가 쪼개져, 상위에 와야 할 표현이 밀린다.
    수치는 멀쩡해 보이고 순위만 틀리는 형태라 로그만 봐서는 못 잡는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 같은 표현을 공백 변형으로 세 번 — 턴다면 3, 안 턴다면 1/1/1 로 갈린다
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        event_type_code=f"E{i}",
        arguments=_args(("ISSUER", v), ("ISSUER", "삼성전자")))
        for i, v in enumerate(("미등록회사", " 미등록회사", "미등록회사 "))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    sample = dict(_log(storage)["argument_resolution"]["top_unresolved"])
    assert sample == {"미등록회사": 3}, sample


def test_unresolved_sample_keeps_the_long_tail(tmp_path, monkeypatch):
    """미해소 상위 표현을 200개까지 남긴다(ALPHA-802).

    WHY: 상한이 20 이던 동안 롱테일 구성이 관측되지 않아, 로그만 보고는 "마스터를
    넓혀야 하는가(표기가 맞는데 종목이 없다), 별칭을 넣어야 하는가(종목은 있는데 표기가
    다르다)"를 가릴 수 없었다. 그 판단이 이 트랙 전체의 분기점이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    # 상한보다 **많이**(250종) 넣어야 상한이 고정된다. 그리고 **빈도가 서로 달라야** 한다 —
    # 전부 1회면 "내림차순인가"가 어떤 정렬에서도 참인 항등식이 되어 정렬 키를 지워도
    # 통과한다. 250종에 1~5회를 고루 준다(각 빈도 50종).
    unknowns = [_assertion(event_type_code=f"E{i}",
                           arguments=_args(*[("ISSUER", f"미등록{i}")] * (i % 5 + 1)))
                for i in range(250)]
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", unknowns)])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    top = _log(storage)["argument_resolution"]["top_unresolved"]
    assert len(top) == 200
    counts = [c for _, c in top]
    assert counts == sorted(counts, reverse=True)
    # 상한이 자르는 것은 **빈도 하위**여야 한다 — 5·4·3·2회(각 50종)가 살고 1회는 전멸한다
    assert counts[0] == 5 and counts[-1] == 2
    # ⭐이 두 줄이 "빈도순 상위 200"과 "먼저 본 200"을 가른다. 삽입 순서는 event_type_code
    # 사전순(E0·E1·E10·E100…)이라 인덱스 순서와 다르다 — 늦게 나오지만 빈도 높은 것이
    # 살아남고, 먼저 나오지만 빈도 1인 것이 잘려야 정렬 키가 실제로 일한 것이다.
    # ⚠️ 인덱스가 크다고 삽입 순서가 늦지 않다: E249 는 사전순 168번째라 선두 200 안에 들어
    # "먼저 본 200" 구현에서도 살아남는다 — 아무것도 가르지 못한다. 사전순 240번째인 E9 를 쓴다.
    kept = dict(top)
    assert "미등록9" in kept         # 삽입 순서 240/250 · 5회 → 빈도순에서만 살아남는다
    assert "미등록0" not in kept     # 삽입 순서 1/250 · 1회 → 빈도순에서만 잘린다


# ── 역할별 해소 3단 사슬 (ALPHA-831) ──────────────────────────────────────

def test_every_recoverable_axis_stays_in_the_unresolved_sample(tmp_path, monkeypatch):
    """회수 가능한 축 **셋 전부**가 미해소 표본에 남는다(ALPHA-857 → 861 로 이관).

    WHY: 이 표본의 유일한 용도는 "무엇을 더 붙일 수 있게 만들까"의 근거다. 축 하나가
    조용히 빠져도 로그는 멀쩡해 보이고 순위만 틀린다 — 특히 `ambiguous` 는 마스터에
    이름은 있는데 대는 곳이 둘이라는 뜻이라 **회수 가능성이 가장 높은** 축이고, 그게
    빠지면 표본이 존재 이유를 잃는다.

    ⚠️ 이 단언은 원래 ALPHA-857 의 정책 제외 테스트 안에 함께 있었는데, ALPHA-861 이
    그 테스트를 통째로 지우면서 **정책과 무관한 이 절반까지 같이 갔다**. 그때 리뷰가
    변이로 잡았다(회수 축 둘을 표본에서 빼도 스위트가 초록이었다). 정책은 사라져도
    "회수 축은 전부 남는다"는 계약은 남는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "미등록회사"),        # unresolved
                        ("ISSUER", "충돌이름"),          # ambiguous — 인덱스에 두 번 온 이름
                        ("AUTHORITY", "없는기관"),       # registry_miss
                        ("ISSUER", "삼성전자")))])])     # 적재가 되도록 하나는 붙인다
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    # 세 자리가 각자 다른 사유를 탔는지 먼저 — 안 그러면 아래가 "그 경로를 안 밟아서" 통과한다
    assert res["unresolved"] == 1 and res["ambiguous"] == 1 and res["registry_miss"] == 1
    sample = {t for t, _ in res["top_unresolved"]}
    assert sample == {"미등록회사", "충돌이름", "없는기관"}, "회수 축 하나가 표본에서 빠졌다"


def test_no_writer_local_policy_narrows_minting(tmp_path, monkeypatch):
    """채번 여부는 **온톨로지만** 정한다 — 이 writer 가 따로 좁히지 않는다(ALPHA-861).

    WHY: 한때 여기에 척도 역할 제외(`MEASURE_ROLES`)와 개념 길이 상한
    (`MAX_CONCEPT_CHARS`)이 있었다. 둘 다 온톨로지에 근거가 없었고 — `role_kinds` 는
    척도 4역할을 **명시적으로 나열해** `PRODUCT_OR_CONCEPT`(MINT)에 넣었고 `concept_key`
    에는 상한이 아예 없다 — **`assemble_events` 에는 안 걸려 있었다**. 그래서 같은 멘션이
    EOD 배치로 오면 미해소, event 조립·1분 레인으로 오면 개념이 됐다. 08-08 dev 실측에서
    `매출`·`영업이익` 은 이미 `concept` 행으로 서서 `event_argument` 580건이 참조 중이었다
    — 막은 것은 한쪽 경로의 연결뿐이었고, 정작 ALPHA-831 이 없애려던 "두 writer 가 갈린다"를
    정책 쪽에 다시 만들어 놓은 셈이었다.

    정책이 필요한지는 ALPHA-859 가 판단하고, 필요하면 **`concept_key`(온톨로지)** 에 둔다 —
    두 writer 가 다 그 함수를 부르므로 한 곳이면 전부 덮는다. 이 테스트는 그 규칙이
    다시 **호출부로 내려오는 것**을 막는다.
    """
    from data_pipeline.entity_resolution import mint_concept, plan_resolution

    # ⚠️ **길이 축의 사거리를 적어 둔다.** 고정 길이 하나만 태우면 이 테스트가 막는 것은
    # "정책의 호출부 복귀"가 아니라 "상한 ≤ 그 길이"다 — 60 만 태우면 상한 61 로 부활시켜도
    # 통과한다(ALPHA-861 리뷰가 변이로 실증). 자릿수를 벌려 현실적 상한을 전부 덮는다.
    cases = [("METRIC", "매출"), ("METRIC", "영업이익"), ("INDICATOR", "소비자물가지수"),
             ("POLICY_RATE", "기준금리"), ("CURRENCY_PAIR", "원/달러"),
             ("PROJECT", "가" * 60), ("PROJECT", "가" * 500)]
    for role, mention in cases:
        # 온톨로지가 채번 대상으로 보는 것부터 확인한다 — 아니면 아래 단언이
        # "그 경로를 안 밟아서" 통과한다
        coined = mint_concept(role, mention)
        # ⚠️ 이 줄은 **온톨로지도 함께 못박는다**. ALPHA-859 가 위 독스트링이 지정한
        # 자리(`concept_key`)에 상한을 넣으면 여기서 깨진다 — 그건 오탐이 아니라
        # "정책이 옳은 자리로 옮겨졌다"는 신호다. 그때 이 테스트의 길이 케이스를 뺀다.
        assert coined is not None, f"{role}/{mention} 이 온톨로지에서 채번 대상이 아니다"
        entity_id, reason, minted = plan_resolution(_INDEX, role, mention)
        assert reason == "minted", f"{role}/{mention} → {reason} (writer 가 따로 좁혔다)"
        assert entity_id == coined[0]
        assert minted[0] == mention

    # 사유 어휘에서도 사라졌는지 — 상수만 지우고 분기를 남기면 다른 이름으로 되살아난다
    import data_pipeline.entity_resolution as er
    for gone in ("MEASURE_ROLES", "MEASURE_SKIPPED", "MAX_CONCEPT_CHARS", "TOO_LONG",
                 "POLICY_EXCLUDED_REASONS"):
        assert not hasattr(er, gone), f"{gone} 이 남아 있다"


def test_minted_concept_id_matches_assemble_events_exactly(tmp_path, monkeypatch):
    """같은 멘션·같은 역할이면 **두 writer 가 같은 concept ID** 를 낸다(ALPHA-831 최대 함정).

    WHY: `document_assertion` 계보를 쓰는 writer 가 둘이다(이 스텝·assemble-events). 채번
    산식이 갈리면 `매출` 이 event 쪽에선 A, assertion 쪽에선 B 가 되어 **같은 개념에 ID 가
    둘** 생기고, 그 둘을 잇는 조인이 조용히 끊긴다. ALPHA-456 이 assertion_id 에서 이미
    겪은 실패 양식이라, 여기선 **같은 한 함수**(`mint_concept`)를 부르는지 자체를 고정한다.
    "같은 원시함수를 각자 조립"으로는 부족했다 — 접두사 하나만 달라도 다시 갈린다.
    """
    from data_pipeline.entity_resolution import mint_concept, plan_resolution
    from data_pipeline.steps import assemble_events

    for role, mention in (("PRODUCT", "zHBM"), ("GEOGRAPHY", "미국"),
                          ("FACILITY", "용인  클러스터")):
        entity_id, reason, minted = plan_resolution(_INDEX, role, mention)
        assert reason == "minted"
        # 채번 결과는 공유 함수가 낸 값 그대로여야 한다
        assert entity_id == mint_concept(role, mention.strip())[0]
        # display_name 도 같은 식 — 다르면 먼저 쓴 writer 가 컬럼을 이긴다(ALPHA-538 동형).
        # 내부 공백이 둘인 값을 일부러 넣는다(양쪽 식이 갈리면 여기서 깨진다).
        assert minted[0] == mention.strip()

    # ⭐**두 writer 가 같은 함수를 부르는지를 구조로 확인한다.** 위 값 비교만으로는
    # 부족하다 — 각자 조립한 산식이 우연히 같으면 통과하고, 한쪽 접두사가 바뀌는 순간
    # 갈린다(ALPHA-456 실패 양식). 문면(`getsource`) 대조는 쓰지 않는다: 이 저장소에서
    # **"없어야 한다"를 소스 텍스트로 검사하면 없앤 이유를 밝힌 주석이 걸려** 여섯 번
    # 재발했다. 이름공간은 주석이 못 바꾼다.
    assert assemble_events.mint_concept is mint_concept, "sibling 이 다른 채번 함수를 쓴다"
    # 채번 키 함수가 sibling 스코프에 없으면 산식을 **다시 조립할 수단 자체가 없다**.
    assert not hasattr(assemble_events, "concept_key"), "sibling 이 채번 키를 다시 만든다"


def test_registry_role_is_looked_up_never_minted(tmp_path, monkeypatch):
    """명부 역할은 **찾기만** 한다 — 못 찾아도 채번하지 않는다(ALPHA-831).

    WHY: 온톨로지가 AUTHORITY 를 REGISTRY 로 둔 근거가 "채번하면 같은 기관이 표기마다
    다른 엔티티가 된다"이다. 미등재거나 '당국' 같은 모호어를 채번하면 조용한 오해소가
    되고, 시드된 명부(기관 68곳)의 의미가 사라진다.
    """
    from data_pipeline.entity_resolution import plan_resolution

    hit_id, hit_reason, hit_minted = plan_resolution(_INDEX, "AUTHORITY", "공정거래위원회")
    assert hit_reason == "registry_hit" and hit_id and hit_minted is None

    miss_id, miss_reason, miss_minted = plan_resolution(_INDEX, "AUTHORITY", "듣도보도못한청")
    assert (miss_id, miss_reason, miss_minted) == (None, "registry_miss", None)

    # ⭐`mint_fallback` 이 선 역할은 예외다 — 온톨로지가 EXCHANGE·MARKET 에만 그걸 켰다.
    # 미등재 해외 거래소(나스닥)를 채번으로 건지되, 등재분은 명부 ID 로 간다. 이 갈래가
    # 없으면 같은 거래소가 AUTHORITY 자리와 EXCHANGE 자리에서 다른 엔티티가 된다.
    krx_id, krx_reason, _ = plan_resolution(_INDEX, "EXCHANGE", "한국거래소")
    assert krx_reason == "registry_hit" and krx_id and not krx_id.startswith("concept_")
    nasdaq_id, nasdaq_reason, nasdaq_minted = plan_resolution(_INDEX, "EXCHANGE", "나스닥")
    assert nasdaq_reason == "minted" and nasdaq_id.startswith("concept_")
    assert nasdaq_minted[1] == "INDEX_OR_EXCHANGE"


def test_minted_concept_rows_are_written_before_the_argument_that_needs_them(
        tmp_path, monkeypatch):
    """개념 행이 **argument 보다 먼저** 선다 — FK 순서(ALPHA-831).

    WHY: `assertion_argument.entity_id` 는 `entity` FK 다. 순서가 뒤집히면 없는 부모를
    가리켜 INSERT 가 터지고, 그 시장의 트랜잭션이 통째로 롤백된다. entity(CONCEPT) →
    concept → assertion_argument 가 계약이다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("PRODUCT", "zHBM")))])])
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    order = [sql.split()[2].upper() for sql, _p in conn.log if sql.upper().startswith("INSERT INTO ")]
    assert order.index("ENTITY") < order.index("CONCEPT") < order.index("ASSERTION_ARGUMENT")
    [(_, role, entity_id, _c)] = _inserts(conn, "assertion_argument")
    assert role == "PRODUCT" and entity_id.startswith("concept_")
    assert _log(storage)["concepts_minted"] == 1


def test_resolution_rate_counts_every_axis_that_actually_attached(tmp_path, monkeypatch):
    """해소율 분자는 **붙은 것 전부**다 — 티커·명부·채번(ALPHA-831).

    WHY: 분모(`args_total`)는 축과 무관하게 실체 역할 전부를 센다. 분자가 `resolved`
    하나만 세면 명부·채번으로 붙은 argument 가 분모엔 들고 분자엔 안 들어, **이 티켓이
    성공할수록 해소율이 떨어진다**. 회수를 재려고 만든 지표가 회수를 반대로 보고하는 것이라,
    ALPHA-802 가 분모를 정직하게 만든 작업이 통째로 무의미해진다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("ISSUER", "삼성전자"),        # 티커 축
                        ("AUTHORITY", "공정거래위원회"),  # 명부 축
                        ("PRODUCT", "zHBM"),          # 채번 축
                        ("ISSUER", "미등록회사")))])])   # 미해소
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)
    monkeypatch.setenv("OPS_LEDGER_ATTEMPT_ID", "attempt-current")

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    res = _log(storage)["argument_resolution"]
    assert res["total"] == 4
    assert res["resolved"] == 1 and res["registry_hit"] == 1 and res["minted"] == 1
    assert res["resolved_any"] == 3
    assert res["rate"] == 0.75          # 분자가 resolved 하나면 0.25 가 된다
    # 원장에는 ticker 전용 resolved가 아니라 실제로 붙은 세 축 전체가 같은 pair로 흘러야 한다.
    assert _log(storage)["ops"]["entity_resolution_arguments_total"] == 4
    assert _log(storage)["ops"]["entity_resolution_arguments_resolved"] == 3
    assert _log(storage)["ops_attempt_id"] == "attempt-current"
    # 분자가 **어느 사유로** 붙었는지는 위 세 줄이 그대로 말한다(1+1+1=3). 예전엔 사유
    # 허용목록을 로그에 싣고 그 목록을 여기서 대조했는데, 그건 코드가 아는 사실의 사본이라
    # 축이 늘 때 같이 드리프트한다 — 지금은 붙은 것을 직접 세므로 목록 자체가 없다.


def test_rollback_does_not_claim_minted_concepts(tmp_path, monkeypatch):
    """롤백되면 채번 카운터도 0 이다(ALPHA-831).

    WHY: 개념 행은 argument 와 **같은 트랜잭션**이다. `connect()` 가 예외에 rollback 하므로
    한 행도 안 남는데, 카운터를 안 되돌리면 로그가 "개념 마스터를 1,210개 늘렸다"고 주장한다.
    운영자는 그 로그를 보고 마스터가 확장된 줄 알고 재확인을 안 한다 — ALPHA-830 에서
    `created` 로 똑같이 겪은 자리다(Rule 12).
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("PRODUCT", "zHBM")))])])

    class _Boom(_FakeConn):
        def cursor(self):
            cur = super().cursor()
            inner = cur.execute

            def execute(sql, params=None):
                inner(sql, params)
                if sql.lstrip().upper().startswith("INSERT INTO ASSERTION_ARGUMENT"):
                    raise RuntimeError("DB 죽음")
            cur.execute = execute
            return cur

    conn = _Boom(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) != 0
    log = _log(storage)
    assert log["concepts_minted"] == 0, "롤백됐는데 개념을 세웠다고 로그가 주장한다"
    assert log["created"] == 0 and log["arguments_inserted"] == 0
    assert log["ops"]["entity_resolution_arguments_total"] is None
    assert log["ops"]["entity_resolution_arguments_resolved"] is None


def test_no_concept_is_minted_for_an_assertion_that_cannot_be_loaded(tmp_path, monkeypatch):
    """적재 안 될 주장에는 채번하지 않는다 — 고아 개념 금지(ALPHA-831).

    WHY: 채번 루프와 적재 루프가 둘로 나뉘어 있다. 문서 행이 없으면 그 주장은 안 실리는데
    (`missing_document` — 같은 manifest 재실행으로 회수하는 **정상 결손 경로**다),
    채번 루프가 그 조건을 안 보면 참조 없는 개념 마스터만 남는다. 두 루프의 거르는 조건이
    같아야 한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion(
        arguments=_args(("PRODUCT", "zHBM")))])])
    conn = _FakeConn(documents=[])          # load-documents 가 아직 안 돌았다
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert _inserts(conn, "entity") == [], "참조 없는 개념 마스터가 남았다"
    assert _inserts(conn, "concept") == []
    log = _log(storage)
    assert log["missing_document"] == 1 and log["concepts_minted"] == 0


def test_stale_judgement_in_the_same_partition_is_not_loaded(tmp_path, monkeypatch):
    """정정된 기사의 **옛 판정**이 DB 에 남지 않는다 (ALPHA-900).

    한 파티션에 같은 기사의 판정이 둘 있으면(part 파일이 여럿인 경우) 사건 자연키가 갈려
    둘 다 INSERT 되고, `ON CONFLICT DO NOTHING` 이라 나중에 덮이지도 않아 **존재한 적 없는
    사건이 영구히 남는다**. `tag_news` 의 압축과 같은 규칙으로 기사마다 최신만 싣는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row(
        "a1", [_assertion(event_type_code="SUPPLY_CONTRACT")],
        input_fingerprint="fp-old", tagged_at="2026-07-15T02:00:00+00:00")])
    # 더 최신 판정이 같은 파티션의 다른 part 파일에 있다(계약 수주 → 해지)
    storage.put_bytes(
        f"{feature_news_assertions_partition('ko', '2026-07-15')}/part-00001.parquet",
        _feature_parquet([_feature_row(
            "a1", [_assertion(event_type_code="SUPPLY_CONTRACT", predicate_code="CANCEL")],
            input_fingerprint="fp-new", tagged_at="2026-07-15T05:00:00+00:00")]))
    conn = _FakeConn(documents=[("a1", "doc_D1")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    # 옛 판정(WIN)은 안 실린다 — 실리면 취소된 계약이 DB 에 영구히 남는다
    assert [row[3] for row in _inserts(conn, "document_assertion")] == ["CANCEL"]
    assert _log(storage)["rows_superseded"] == 1


def test_unabsorbed_minute_mirror_is_not_loaded(tmp_path, monkeypatch):
    """⭐ 흡수 전 장중 미러는 **아직 확정이 아니다** (ALPHA-900).

    SFN 은 TagNews 뒤에 LoadAssertions 를 돌리는데 그 사이에도 1분 Consumer 는 미러를
    쓴다. 여기서 바로 읽으면 `tag_news` 가 거는 게이트(canonical mentions 판정 — 배치가
    일부러 feature 집합 밖에 두는 기사)를 **한 번도 안 거친 판정**이 DB 로 간다.
    건너뛰어도 지연이 없다 — 같은 런의 TagNews 가 흡수한 분은 이미 part 파일에 있다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [_feature_row("a1", [_assertion()])])
    storage.put_bytes(
        f"{feature_news_assertions_minute_prefix('ko', '2026-07-15')}a2.fp-x.parquet",
        _feature_parquet([_feature_row("a2", [_assertion(event_type_code="LAYOFF")])]))
    conn = _FakeConn(documents=[("a1", "doc_D1"), ("a2", "doc_D2")])
    _setup(monkeypatch, conn)

    assert load_assertions.run(storage, "R1", db=_db()) == 0

    assert [row[1] for row in _inserts(conn, "document_assertion")] == ["doc_D1"]
    assert _log(storage)["minute_mirrors_unabsorbed"] == 1   # 유실이 아니라 대기다


def test_rows_without_article_id_are_not_folded_into_one(tmp_path, monkeypatch):
    """article_id 결손 행을 기사 키로 접으면 여러 결손이 하나로 뭉쳐 `rows_no_assertion`
    이 실제보다 작게 보고된다 — 결손 규모가 조용히 줄어든다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_feature(storage, "ko", "2026-07-15", [
        _feature_row("", [_assertion()]), _feature_row("", [_assertion()])])
    conn = _FakeConn(documents=[])
    _setup(monkeypatch, conn)

    load_assertions.run(storage, "R1", db=_db())

    log = _log(storage)
    assert (log["rows_read"], log["rows_no_assertion"]) == (2, 2)
    assert log["rows_superseded"] == 0


def _feature_parquet(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()
