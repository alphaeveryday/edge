"""분석 v2 원천 관측 세 데이터셋의 수집 → 정제 → 적재 공통 경로 (ALPHA-1130).

데이터셋: `macro_observation`(매크로 5계열) · `financial_metric`(DART 재무 지표) ·
`sector_classification`(KIS 지수업종). 계약 정본은 docs/design/etf-data-storage-plan.md §10 이다.
공급자별 요청·해석은 `sources/` 모듈이 하고, 이 모듈은 셋이 같은 저장·계보 규칙을 지키게 한다.

단계와 산출물:

| 단계 | 쓰는 것 | 규칙 |
|---|---|---|
| 수집 `collect` | raw 객체(공급자 바이트 그대로) → raw run manifest → collection_log | 객체 이름은 내용 해시를 담아 불변이다. manifest 에 오른 객체만 입력이다. 완료 manifest 가 이미 있으면 **다시 부르지 않는다** |
| 정제 `normalize` | 실행별 불변 artifact → canonical 현재 상태 → canonical run manifest → quality_log | 입력은 raw manifest 의 직접 키(sha256 대조). canonical 병합은 논리 키마다 **가장 늦게 수신된** 판본을 남긴다(늦게 끝난 옛 실행이 최신을 못 덮는다) |
| 적재 `load` | DB 추가 전용 판본 행 → 소비 마커 → quality_log | 입력은 canonical manifest 의 artifact(sha256 대조). `ON CONFLICT DO NOTHING` 이라 재적재가 행을 늘리지 않는다 |

재시도 책임: 공급자 호출의 일시 오류 재시도는 `PoliteClient`(5xx·네트워크 3회)만 한다. Airflow 는
컨테이너가 업무를 시작하지 않았을 때만 재시도한다(EdgeStep exit 75). 정제·적재 재시도는 raw·artifact 를
다시 읽을 뿐 공급자를 부르지 않는다 — 두 층의 재시도가 곱해지지 않는다.

⚠️ 실행별 artifact 는 `operations_archive/canonical_run_artifacts/` 에 있고 그 프리픽스는 **30일 만료**다
(terraform `pipeline/storage.tf`). 과거 재현의 근거로 약속하는 것은 만료가 없는 raw·manifest 와 DB 판본
이력이다. artifact 가 만료된 실행을 다시 적재하려면 같은 raw 로 정제를 다시 돌린다(결정적 정규화).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_financial_metric_partition,
    canonical_macro_observation_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    canonical_sector_classification_partition,
    collection_log_key,
    quality_log_key,
    raw_observation_partition,
    raw_run_manifest_key,
    run_manifest_consumed_key,
    unconsumed_run_ids,
)
from ..minute.artifacts import put_immutable
from ..parse import krx_short_code
from ..sources import dart_fundamental, kis_sector_master, macro_series

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
PARTIAL_EXIT = 2
CONSUMER = "load_source_observations"


def sha256(data: bytes) -> str:
    """바이트의 sha256 16진 문자열 — manifest·DB 근거 대조의 단위."""
    return hashlib.sha256(data).hexdigest()


# ── 데이터셋 명세 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Column:
    """파일 스키마의 열 하나."""

    name: str
    kind: str   # str · date · ts · int (Parquet 타입과 DB 적재 변환의 기준)


@dataclass(frozen=True)
class DatasetSpec:
    """한 데이터셋의 파일 스키마·논리 키·canonical 파티션·정제·DB 적재."""

    dataset: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]                                   # 파일 안에서 유일한 논리 행 키
    partition: Callable[[dict], str]                       # 행 → canonical 파티션 프리픽스
    normalize: Callable[[list[dict], dict], tuple[list[dict], list[dict]]]
    table: str
    collection_vendor: str                                 # collection_log 의 source= (원장 관측 축)

    def names(self) -> list[str]:
        """열 이름(파일·DB 적재 순서)."""
        return [c.name for c in self.columns]


_PROVENANCE = (Column("received_at", "ts"), Column("available_at", "ts"),
               Column("availability_basis", "str"), Column("raw_run_id", "str"),
               Column("raw_key", "str"), Column("raw_sha256", "str"))


def _schema(spec: DatasetSpec):
    import pyarrow as pa

    types = {"str": pa.string(), "date": pa.date32(), "int": pa.int64(),
             "ts": pa.timestamp("us", tz="UTC")}
    return pa.schema([(c.name, types[c.kind]) for c in spec.columns])


def _to_arrow_value(kind: str, value):
    if value is None:
        return None
    if kind == "date":
        return date.fromisoformat(value) if isinstance(value, str) else value
    if kind == "ts":
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    return value


def write_rows(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """행 → Parquet 바이트. 행 순서를 논리 키로 고정해 같은 입력이 같은 바이트가 되게 한다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ordered = sorted(rows, key=lambda r: tuple(str(r[k]) for k in spec.key))
    table = pa.Table.from_pylist(
        [{c.name: _to_arrow_value(c.kind, r.get(c.name)) for c in spec.columns} for r in ordered],
        schema=_schema(spec))
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def read_rows(spec: DatasetSpec, data: bytes) -> list[dict]:
    """Parquet → 행(날짜·시각은 ISO 문자열). 스키마가 명세와 다르면 거부한다."""
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(data))
    if table.column_names != spec.names():
        raise ValueError(f"{spec.dataset} 파일 스키마가 명세와 다르다: {table.column_names}")
    rows = table.to_pylist()
    for row in rows:
        for c in spec.columns:
            value = row[c.name]
            if value is not None and c.kind in ("date", "ts"):
                row[c.name] = value.isoformat()
    return rows


# ── 수집 ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RawObject:
    """수집한 객체 하나. body 가 None 이면 저장할 응답이 없는 실패 기록이다."""

    source: str
    dimension: str            # market · series_id
    value: str
    stem: str                 # 파일 이름 앞부분(계열·창·종류) — 뒤에 내용 해시가 붙는다
    extension: str
    body: bytes | None
    request: dict             # 인증키 없는 요청 서술
    status: str               # ok · empty · error
    detail: str | None
    fetched_at: str


def _collection_status(counts: dict, skipped_reason: str | None) -> tuple[str, int]:
    """수집 결과 → (collection_log status, exit). 첫 실행과 완료 manifest 재사용이 같은 판정을 내게 한 곳에 둔다."""
    if skipped_reason:
        return "skipped", 0
    if counts.get("error"):
        return ("error", 1) if not (counts.get("ok") or counts.get("empty")) else ("partial", PARTIAL_EXIT)
    return "success", 0


def existing_raw_manifest(storage: Storage, dataset: str, run_id: str) -> dict | None:
    """이 run_id 의 완료된 raw manifest. 있으면 수집을 다시 하지 않는다(재시도가 공급자를 부르지 않게)."""
    data, _ = storage.get_bytes_with_version(raw_run_manifest_key(dataset, run_id))
    if data is None:
        return None
    manifest = json.loads(data.decode("utf-8"))
    return manifest if manifest.get("completed") is True else None


def write_raw_run(
    storage: Storage, spec: DatasetSpec, run_id: str, *, producer: str, objects: list[RawObject],
    started_at: datetime, request_scope: dict, skipped_reason: str | None = None,
) -> int:
    """raw 객체 → raw run manifest → collection_log. 성공 0, 부분 2, 전부 실패·저장 실패 1.

    ok·empty 는 수집 성공이다(empty = 공급자가 "그 기간 데이터 없음"이라 답함 — 휴장·미공표).
    error 만 실패다. 둘을 manifest·로그에 따로 세어 "정상 0건"과 "받지 못함"이 섞이지 않게 한다.
    """
    ingest_date = started_at.date().isoformat()
    entries, saved_rows = [], 0
    exit_code = 0
    try:
        for obj in objects:
            entry = {"source": obj.source, obj.dimension: obj.value, "request": obj.request,
                     "status": obj.status, "detail": obj.detail, "fetched_at": obj.fetched_at}
            if obj.body is not None:
                digest = sha256(obj.body)
                key = (f"{raw_observation_partition(obj.source, spec.dataset, obj.dimension, obj.value, ingest_date, run_id)}"
                       f"/{obj.stem}-{digest[:16]}.{obj.extension}")
                put_immutable(storage, key, obj.body)
                entry.update({"key": key, "sha256": digest, "bytes": len(obj.body)})
            entries.append(entry)
    except Exception:
        logger.exception("raw 저장 실패")
        exit_code = 1

    counts = {s: sum(1 for e in entries if e["status"] == s) for s in ("ok", "empty", "error")}
    if exit_code == 0:
        # 건너뛴 실행(비거래일)도 빈 완료 manifest 를 남긴다 — 정제·적재가 "입력 없음"을 실패가 아니라
        # 할 일 없음으로 읽게 한다.
        manifest = {"run_id": run_id, "producer": producer, "dataset": spec.dataset,
                    "ingest_date": ingest_date, "completed": True, "request_scope": request_scope,
                    "objects": entries, "counts": counts, "skipped_reason": skipped_reason,
                    "started_at": started_at.isoformat()}
        try:
            put_immutable(storage, raw_run_manifest_key(spec.dataset, run_id),
                          json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        except Exception:
            logger.exception("raw run manifest 저장 실패")
            exit_code = 1
    if exit_code == 0:
        status, exit_code = _collection_status(counts, skipped_reason)
    else:
        status = "error"
    saved_rows = counts["ok"]
    log = {"run_id": run_id, "job_name": producer, "source_vendor": spec.collection_vendor,
           "dataset": spec.dataset, "status": status, "reason": skipped_reason,
           "request_scope": request_scope, "counts": counts,
           "failures": [e for e in entries if e["status"] == "error"],
           "records_saved": saved_rows,
           "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
           # 원장 봉투 — 산출 단위는 응답 객체다(행 수는 정제가 센다). empty 는 실패가 아니다.
           "ops": {"records_out": saved_rows, "failed_records": counts["error"],
                   "received_count": saved_rows}}
    try:
        storage.put_bytes(collection_log_key(spec.collection_vendor, spec.dataset, ingest_date, run_id),
                          json.dumps(log, ensure_ascii=False).encode("utf-8"))
    except Exception:
        logger.exception("collection_log 기록 실패")
        exit_code = exit_code or 1
    logger.info("%s 수집: status=%s counts=%s", spec.dataset, status, counts)
    return exit_code


def record_already_collected(storage: Storage, spec: DatasetSpec, run_id: str, producer: str,
                             manifest: dict) -> int:
    """완료 manifest 가 이미 있는 run_id 재실행 — 공급자를 부르지 않고 그 결과를 다시 보고한다."""
    counts = manifest.get("counts") or {}
    logger.info("%s run_id=%s 는 이미 수집 완료 — 공급자 재호출 없음", spec.dataset, run_id)
    started = datetime.now(timezone.utc)
    # 원래 결과(부분 실패·전부 실패·건너뜀)를 그대로 다시 보고한다 — 재실행이 실패를 성공으로 바꾸지 않게.
    status, exit_code = _collection_status(counts, manifest.get("skipped_reason"))
    log = {"run_id": run_id, "job_name": producer, "source_vendor": spec.collection_vendor,
           "dataset": spec.dataset, "status": status, "reason": "already_collected",
           "counts": counts, "records_saved": counts.get("ok", 0),
           "started_at": started.isoformat(), "finished_at": started.isoformat(),
           "ops": {"records_out": counts.get("ok", 0), "failed_records": counts.get("error", 0),
                   "received_count": counts.get("ok", 0)}}
    storage.put_bytes(collection_log_key(spec.collection_vendor, spec.dataset,
                                         started.date().isoformat(), run_id),
                      json.dumps(log, ensure_ascii=False).encode("utf-8"))
    return exit_code


# ── 정제 ─────────────────────────────────────────────────────────────────────

def _load_raw_objects(storage: Storage, dataset: str, input_run_id: str) -> tuple[dict, list[dict]]:
    """완료 raw manifest 와 ok 객체(바이트 sha256 대조). 목록 조회로 입력을 넓히지 않는다."""
    manifest_bytes = storage.get_bytes(raw_run_manifest_key(dataset, input_run_id))
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("run_id") != input_run_id or manifest.get("dataset") != dataset \
            or manifest.get("completed") is not True:
        raise ValueError(f"완료된 {dataset} raw manifest 가 아니다: run_id={input_run_id}")
    objects = []
    for entry in manifest["objects"]:
        if entry.get("status") != "ok":
            continue
        body = storage.get_bytes(entry["key"])
        if sha256(body) != entry["sha256"]:
            raise ValueError(f"raw 바이트가 manifest 와 다르다: {entry['key']}")
        objects.append({**entry, "body": body})
    manifest["manifest_sha256"] = sha256(manifest_bytes)
    return manifest, objects


def _collapse(spec: DatasetSpec, rows: list[dict]) -> tuple[list[dict], list[dict], int]:
    """같은 실행 안의 중복 논리 키: 값이 같으면 하나로 접고(수를 센다), 다르면 그 키를 무효로 격리한다."""
    by_key: dict[tuple, list[dict]] = {}
    for row in rows:
        by_key.setdefault(tuple(row[k] for k in spec.key), []).append(row)
    kept, conflicts, collapsed = [], [], 0
    # 값 비교는 관측 필드만 — 수신시각·raw 키는 요청 창마다 달라도 같은 관측이다.
    provenance = {c.name for c in _PROVENANCE}
    payload = [c.name for c in spec.columns if c.name not in provenance]
    for key, group in by_key.items():
        variants = {json.dumps([r.get(n) for n in payload], default=str) for r in group}
        if len(variants) > 1:
            conflicts.append({"key": dict(zip(spec.key, key)), "reasons": ["conflicting_duplicate"],
                              "variants": len(variants)})
            continue
        collapsed += len(group) - 1
        kept.append(group[0])
    return kept, conflicts, collapsed


def _newer(candidate: dict, current: dict) -> bool:
    """현재 상태 판정 — 더 늦게 **수신된** 판본이 이긴다. 적재 순서가 아니라 수신 순서다."""
    return (candidate["received_at"], candidate["raw_run_id"]) > (current["received_at"], current["raw_run_id"])


def _merge_canonical(storage: Storage, spec: DatasetSpec, rows: list[dict]) -> list[dict]:
    """canonical 현재 상태 파티션에 CAS 병합. 반환: 파티션별 {key, sha256, rows}."""
    by_partition: dict[str, list[dict]] = {}
    for row in rows:
        by_partition.setdefault(spec.partition(row), []).append(row)
    written = []
    for prefix, new_rows in sorted(by_partition.items()):
        key = f"{prefix}/part-00000.parquet"
        for _ in range(5):
            existing_bytes, version = storage.get_bytes_with_version(key)
            merged = {tuple(r[k] for k in spec.key): r
                      for r in (read_rows(spec, existing_bytes) if existing_bytes else [])}
            for row in new_rows:
                k = tuple(row[c] for c in spec.key)
                if k not in merged or _newer(row, merged[k]):
                    merged[k] = row
            data = write_rows(spec, list(merged.values()))
            if storage.put_bytes_if_version(key, data, version):
                written.append({"key": key, "sha256": sha256(data), "rows": len(merged)})
                break
        else:
            raise RuntimeError(f"canonical 병합 경합이 계속된다: {key}")
    return written


def normalize(storage: Storage, spec: DatasetSpec, run_id: str, input_run_id: str | None,
              *, producer: str) -> int:
    """raw manifest → 정규화 → artifact·canonical·manifest·quality_log. 성공 0, 행 거부 2, 실패 1."""
    started_at = datetime.now(timezone.utc)
    manifest_key = canonical_run_manifest_key(spec.dataset, run_id)
    log: dict = {"run_id": run_id, "job_name": producer, "dataset": spec.dataset,
                 "input_run_id": input_run_id, "started_at": started_at.isoformat()}
    if input_run_id is None:
        # 정제 입력은 raw manifest 하나다. 전체 raw 목록 스캔으로 넓히지 않는다.
        raise SystemExit(f"{producer} 는 --input-run-id(수집 run_id)가 필요하다")
    exit_code = 0
    failures: list[dict] = []
    try:
        storage.put_bytes(manifest_key, json.dumps(
            {"run_id": run_id, "producer": producer, "canonical_written": False}).encode("utf-8"))
        raw_manifest, objects = _load_raw_objects(storage, spec.dataset, input_run_id)
        rows, rejects = ([], []) if raw_manifest.get("skipped_reason") else spec.normalize(objects, raw_manifest)
        failures.extend(rejects)
        for row in rows:
            row["raw_run_id"] = input_run_id
        rows, conflicts, collapsed = _collapse(spec, rows)
        failures.extend(conflicts)
        artifact = write_rows(spec, rows)
        artifact_key = canonical_run_partition_key(spec.dataset, run_id, raw_manifest["ingest_date"])
        artifact_sha = put_immutable(storage, artifact_key, artifact)
        partitions = _merge_canonical(storage, spec, rows)
        storage.put_bytes(manifest_key, json.dumps({
            "run_id": run_id, "producer": producer, "dataset": spec.dataset,
            "canonical_written": True, "input_run_id": input_run_id,
            "raw_manifest_sha256": raw_manifest["manifest_sha256"],
            # artifact 의 report_date 는 raw 수집일(ingest_date)이다 — 관측일·공개일이 아니다.
            "artifact": {"key": artifact_key, "sha256": artifact_sha, "rows": len(rows),
                         "partition_date": "ingest_date"},
            "canonical_partitions": partitions, "rows": len(rows),
            "rejected": len(failures), "collapsed_duplicates": collapsed,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        log.update({"rows": len(rows), "collapsed_duplicates": collapsed,
                    "canonical_partitions": len(partitions), "artifact_key": artifact_key,
                    "raw_counts": raw_manifest.get("counts")})
        if failures:
            exit_code = PARTIAL_EXIT
    except Exception as exc:
        logger.exception("%s 정제 실패", spec.dataset)
        failures.append({"reasons": ["normalize_error"], "error": type(exc).__name__})
        log["rows"] = 0
        exit_code = 1
    log.update({"failures": failures[:200], "records_failed": len(failures),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ops": {"records_out": log.get("rows", 0), "failed_records": len(failures)}})
    try:
        storage.put_bytes(quality_log_key(spec.dataset, started_at.date().isoformat(), run_id),
                          json.dumps(log, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        logger.exception("quality_log 기록 실패")
        exit_code = exit_code or 1
    logger.info("%s 정제: rows=%s failures=%d exit=%d", spec.dataset, log.get("rows"), len(failures), exit_code)
    return exit_code


# ── 적재 ─────────────────────────────────────────────────────────────────────

def _completed_manifest(storage: Storage, spec: DatasetSpec, run_id: str) -> dict | None:
    data, _ = storage.get_bytes_with_version(canonical_run_manifest_key(spec.dataset, run_id))
    if data is None:
        return None
    manifest = json.loads(data.decode("utf-8"))
    return manifest if manifest.get("canonical_written") is True else None


def load(storage: Storage, spec: DatasetSpec, db, run_id: str, *, input_run_id: str | None,
         pending: bool, producer: str) -> int:
    """canonical manifest 의 artifact → DB 판본 행. `pending` 이면 소비 마커 없는 완료 manifest 전부.

    소비 마커는 커밋이 끝난 뒤에만 쓴다 — 저장 뒤 적재 전에 죽은 실행은 마커가 없어 `--all` 이 이어 싣는다.
    """
    from ..db import connect

    started_at = datetime.now(timezone.utc)
    if (input_run_id is None) == (not pending):
        raise SystemExit(f"{producer} 는 --input-run-id 또는 --all 중 정확히 하나가 필요하다")
    targets = [input_run_id] if input_run_id else unconsumed_run_ids(storage, "canonical", spec.dataset, CONSUMER)
    loaded, inserted, skipped, failures = [], 0, [], []
    exit_code = 0
    columns = [*spec.names(), "canonical_run_id", "artifact_key", "artifact_sha256"]
    sql = (f"INSERT INTO {spec.table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
           " ON CONFLICT DO NOTHING")
    for canonical_run_id in targets:
        manifest = _completed_manifest(storage, spec, canonical_run_id)
        if manifest is None:
            if input_run_id:
                failures.append({"run_id": canonical_run_id, "reasons": ["manifest_not_completed"]})
                exit_code = 1
            else:
                # 생산자가 아직 끝나지 않았거나 죽은 반쪽 manifest — 마커 없이 남겨 다음 회차가 다시 본다.
                skipped.append(canonical_run_id)
            continue
        try:
            artifact = manifest["artifact"]
            data = storage.get_bytes(artifact["key"])
            if sha256(data) != artifact["sha256"]:
                raise ValueError(f"artifact 바이트가 manifest 와 다르다: {artifact['key']}")
            rows = read_rows(spec, data)
            params = [[*(r[c] for c in spec.names()), canonical_run_id, artifact["key"], artifact["sha256"]]
                      for r in rows]
            with connect(db) as conn, conn.cursor() as cur:
                before = _count(cur, spec.table, canonical_run_id)
                if params:
                    cur.executemany(sql, params)
                inserted += _count(cur, spec.table, canonical_run_id) - before
            storage.put_bytes(run_manifest_consumed_key("canonical", spec.dataset, canonical_run_id, CONSUMER),
                              json.dumps({"consumer": CONSUMER, "rows": len(rows), "loaded_by": run_id,
                                          "at": datetime.now(timezone.utc).isoformat()}).encode("utf-8"))
            loaded.append({"run_id": canonical_run_id, "rows": len(rows)})
        except Exception as exc:
            logger.exception("%s 적재 실패: run_id=%s", spec.dataset, canonical_run_id)
            failures.append({"run_id": canonical_run_id, "reasons": ["load_error"], "error": type(exc).__name__})
            exit_code = 1
    rows_in = sum(item["rows"] for item in loaded)
    log = {"run_id": run_id, "job_name": producer, "dataset": f"{spec.dataset}_load",
           "input_run_id": input_run_id, "pending": pending, "loaded": loaded, "skipped_incomplete": skipped,
           "rows_inserted": inserted, "failures": failures,
           "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
           "ops": {"records_out": rows_in, "failed_records": len(failures)}}
    try:
        storage.put_bytes(quality_log_key(f"{spec.dataset}_load", started_at.date().isoformat(), run_id),
                          json.dumps(log, ensure_ascii=False).encode("utf-8"))
    except Exception:
        logger.exception("quality_log 기록 실패")
        exit_code = exit_code or 1
    logger.info("%s 적재: runs=%d rows=%d inserted=%d failures=%d",
                spec.dataset, len(loaded), rows_in, inserted, len(failures))
    return exit_code


def _count(cur, table: str, canonical_run_id: str) -> int:
    cur.execute(f"SELECT count(*) FROM {table} WHERE canonical_run_id = %s", (canonical_run_id,))
    return cur.fetchone()[0]


# ── 매크로 ───────────────────────────────────────────────────────────────────

def _normalize_macro(objects: list[dict], raw_manifest: dict) -> tuple[list[dict], list[dict]]:
    """ok 응답 → 관측 행. 요청 창 밖·미완 기간은 거부로 센다(값을 버리는 이유를 남긴다)."""
    rows, rejects = [], []
    for obj in objects:
        series_id = obj["series_id"]
        series = macro_series.SERIES[series_id]
        window = raw_manifest["request_scope"]["windows"][series_id]
        try:
            good, bad = macro_series.parse(series_id, obj["body"])
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as exc:
            # 한 응답의 파손이 다른 계열의 정제를 막지 않게 그 응답만 거부한다.
            rejects.append({"series_id": series_id, "raw_key": obj["key"], "reasons": ["unparseable_response"],
                            "error": type(exc).__name__})
            continue
        for item in bad:
            rejects.append({"series_id": series_id, "raw_key": obj["key"], **item})
        for item in good:
            day = item["observation_date"]
            reasons = []
            if not window["from"] <= day <= window["to"]:
                reasons.append("outside_request_window")
            if not macro_series.period_complete(series_id, day, obj["fetched_at"]):
                reasons.append("incomplete_period")
            if reasons:
                rejects.append({"series_id": series_id, "observation_date": day, "raw_key": obj["key"],
                                "reasons": reasons})
                continue
            rows.append({"series_id": series_id, "observation_date": day, "value": item["value"],
                         "unit": series.unit, "source_vendor": series.vendor,
                         "source_series": series.source_series,
                         # 공급자가 공표 시각을 주지 않는다 — 가시시각 = 실제 수신시각(2026-09-30 결정).
                         "received_at": obj["fetched_at"], "available_at": obj["fetched_at"],
                         "availability_basis": "received",
                         "raw_key": obj["key"], "raw_sha256": obj["sha256"]})
    return rows, rejects


MACRO = DatasetSpec(
    dataset="macro_observation",
    columns=(Column("series_id", "str"), Column("observation_date", "date"), Column("value", "str"),
             Column("unit", "str"), Column("source_vendor", "str"), Column("source_series", "str"),
             *_PROVENANCE),
    key=("series_id", "observation_date"),
    partition=lambda r: canonical_macro_observation_partition(r["series_id"], r["observation_date"]),
    normalize=_normalize_macro,
    table="macro_observation",
    collection_vendor="multi",
)


def macro_window(series_id: str, today_kst: date, from_date: str | None, to_date: str | None
                 ) -> tuple[date, date]:
    """정기 수집은 (어제 − 계열 소급일 ~ 어제), 백필은 명시한 관측 기간. 오늘은 넣지 않는다 —
    진행 중 관측은 확정값이 아니다. 백필 끝날짜가 오늘 이후면 거부한다(미래를 과거로 라벨하지 않게)."""
    series = macro_series.SERIES[series_id]
    yesterday = today_kst - timedelta(days=1)
    if from_date is None and to_date is None:
        return yesterday - timedelta(days=series.lookback_days - 1), yesterday
    if from_date is None or to_date is None:
        raise SystemExit("백필은 --from 과 --to 를 함께 준다")
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if end > yesterday:
        raise SystemExit(f"--to({end})가 어제({yesterday}) 이후다 — 진행 중·미래 관측은 수집하지 않는다")
    return start, end


def collect_macro(storage: Storage, source, run_id: str, *, series_ids: list[str],
                  from_date: str | None, to_date: str | None, now: datetime | None = None) -> int:
    """계열별 요청 창을 부르고 응답을 그대로 남긴다. 키 없는 계열은 부르지 않고 실패로 드러낸다."""
    producer = "ingest_raw_macro"
    done = existing_raw_manifest(storage, MACRO.dataset, run_id)
    if done is not None:
        return record_already_collected(storage, MACRO, run_id, producer, done)
    started_at = now or datetime.now(timezone.utc)
    today_kst = started_at.astimezone(KST).date()
    unknown = sorted(set(series_ids) - set(macro_series.SERIES))
    if unknown:
        raise SystemExit(f"모르는 매크로 계열: {unknown}")
    windows = {s: macro_window(s, today_kst, from_date, to_date) for s in series_ids}
    objects: list[RawObject] = []
    missing = set(source.missing_credentials(series_ids))
    for series_id in series_ids:
        series = macro_series.SERIES[series_id]
        start, end = windows[series_id]
        if series_id in missing:
            objects.append(RawObject(series.vendor, "series_id", series_id, series_id, "json", None,
                                     {"vendor": series.vendor}, "error", "missing_credentials",
                                     started_at.isoformat()))
            continue
        for w_start, w_end in macro_series.request_windows(series, start, end):
            result = source.fetch(series_id, w_start, w_end)
            objects.append(RawObject(series.vendor, "series_id", series_id,
                                     f"{series_id}-{w_start:%Y%m%d}-{w_end:%Y%m%d}", "json",
                                     result.body, result.request, result.status, result.detail,
                                     result.fetched_at))
    scope = {"series": series_ids,
             "windows": {s: {"from": a.isoformat(), "to": b.isoformat()} for s, (a, b) in windows.items()},
             "mode": "backfill" if from_date else "regular"}
    return write_raw_run(storage, MACRO, run_id, producer=producer, objects=objects,
                         started_at=started_at, request_scope=scope)


# ── KIS 지수업종 분류 ────────────────────────────────────────────────────────

def _normalize_sector(objects: list[dict], raw_manifest: dict) -> tuple[list[dict], list[dict]]:
    """마스터 두 시장 + 업종코드 표 → 종목별 분류 스냅샷. `0000` 은 분류 없음(NULL 코드)이다.

    as_of_date 는 그 파일을 받은 KST 날짜다(원천이 기준일을 주지 않는다 — 현재값). 이름을 못 찾은 코드는
    코드를 남기고 이름만 비우며 사유를 센다. 업종코드 표를 못 받은 실행도 코드는 적재한다.
    """
    names: dict[str, str] = {}
    rejects: list[dict] = []
    name_files = [o for o in objects if o["request"].get("file") == kis_sector_master.SECTOR_NAME_FILE]
    if name_files:
        try:
            names, warnings = kis_sector_master.parse_sector_names(name_files[0]["body"])
        except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
            # 업종명 표가 깨져도 코드는 싣는다(이름만 비운다) — HTTP 실패 때와 같은 부분 처리.
            names, warnings = {}, [f"sector_name_file_unreadable:{type(exc).__name__}"]
        rejects.extend({"reasons": [w]} for w in warnings)
    else:
        rejects.append({"reasons": ["sector_name_file_missing"]})
    rows, unnamed = [], set()
    for obj in objects:
        file_name = obj["request"].get("file")
        if file_name not in kis_sector_master.MASTER_FILES:
            continue
        try:
            parsed, bad = kis_sector_master.parse_master(file_name, obj["body"])
        except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
            rejects.append({"raw_key": obj["key"], "reasons": ["master_file_unreadable"], "error": type(exc).__name__})
            continue
        rejects.extend({**b, "raw_key": obj["key"]} for b in bad)
        as_of = datetime.fromisoformat(obj["fetched_at"]).astimezone(KST).date().isoformat()
        for item in parsed:
            row = {**item, "as_of_date": as_of, "taxonomy": "KIS_INDEX_SECTOR",
                   "received_at": obj["fetched_at"], "available_at": obj["fetched_at"],
                   "availability_basis": "received", "raw_key": obj["key"], "raw_sha256": obj["sha256"]}
            for level in ("large", "medium", "small"):
                raw_code = item[f"raw_{level}_code"]
                code = None if raw_code == "0000" else raw_code
                row[f"{level}_code"], row[f"{level}_name"] = code, names.get(code) if code else None
                if code and code not in names:
                    unnamed.add(code)
            rows.append(row)
    rejects.extend({"reasons": ["sector_name_not_found"], "code": c} for c in sorted(unnamed))
    return rows, rejects


SECTOR = DatasetSpec(
    dataset="sector_classification",
    columns=(Column("market", "str"), Column("instrument_code", "str"), Column("standard_code", "str"),
             Column("name_kr", "str"), Column("security_group", "str"), Column("as_of_date", "date"),
             Column("large_code", "str"), Column("large_name", "str"), Column("medium_code", "str"),
             Column("medium_name", "str"), Column("small_code", "str"), Column("small_name", "str"),
             Column("raw_large_code", "str"), Column("raw_medium_code", "str"),
             Column("raw_small_code", "str"), Column("taxonomy", "str"), *_PROVENANCE),
    key=("market", "instrument_code", "as_of_date"),
    partition=lambda r: canonical_sector_classification_partition(r["market"], r["as_of_date"]),
    normalize=_normalize_sector,
    table="sector_classification",
    collection_vendor="kis",
)


def collect_sector(storage: Storage, client, base_url: str, run_id: str, *, now: datetime | None = None) -> int:
    """마스터 ZIP 3개를 받아 그대로 남긴다. 현재값만 주는 원천이라 날짜 인자가 없다(백필 불가)."""
    from ..failures import SafeFailureError
    from ..sources.http import StopFetch

    from ..ops.trading_calendar import is_trading_day

    producer = "ingest_raw_sector"
    done = existing_raw_manifest(storage, SECTOR.dataset, run_id)
    if done is not None:
        return record_already_collected(storage, SECTOR, run_id, producer, done)
    started_at = now or datetime.now(timezone.utc)
    if not is_trading_day(started_at.astimezone(KST).date()):
        # 휴장일엔 받지 않는다(원장 kr_trading_calendar=True 의 전제 — 일을 하지 않는 날만 SKIPPED 다).
        return write_raw_run(storage, SECTOR, run_id, producer=producer, objects=[], started_at=started_at,
                             request_scope={"files": []}, skipped_reason="non_trading_day")
    objects = []
    for file_name in (*kis_sector_master.MASTER_FILES, kis_sector_master.SECTOR_NAME_FILE):
        market = kis_sector_master.MASTER_FILES.get(file_name, ("KR", 0))[0]
        fetched_at = datetime.now(timezone.utc).isoformat()
        request = {"vendor": "kis", "file": file_name}
        try:
            data = client.request("GET", f"{base_url}/{file_name}", headers={}, decode=False)
            status, detail = "ok", None
        except (StopFetch, SafeFailureError) as exc:
            data, status = None, "error"
            detail = f"http_{exc.status}" if isinstance(exc, StopFetch) else str(exc)
        objects.append(RawObject("kis", "market", market, file_name.removesuffix(".zip").replace(".", "_"),
                                 "zip", data, request, status, detail, fetched_at))
    return write_raw_run(storage, SECTOR, run_id, producer=producer, objects=objects, started_at=started_at,
                         request_scope={"files": [o.request["file"] for o in objects]})


# ── DART 재무 지표 ───────────────────────────────────────────────────────────

LIST_LOOKBACK_DAYS = 400   # 접수번호→접수일 대조와 Q4 유도(같은 해 3분기) 입력을 덮는 목록 소급 폭
REGULAR_FILING_DAYS = 14   # 정기 수집이 다시 보는 접수일 창(늦은 게시·정정 흡수)


def constituents_between(storage: Storage, etf_ids: list[str], start: date, end: date) -> tuple[list[str], dict]:
    """기간 [start, end] 에 유효했던 구성종목 합집합 — start 시점에 유효한 스냅샷(start 이하 최신) + 기간 안 스냅샷.

    현재 구성을 과거에 소급하지 않는다. start 이전에 스냅샷이 하나도 없으면 그 앞 기간의 구성은 **모른다** —
    `uncovered_before` 로 드러내고 추정하지 않는다.
    """
    from .ingest_price_raw import _is_calendar_date, _read_parquet_rows

    marker = canonical_etf_holdings_partition("KR", "")
    dates = sorted({d for key in storage.list_keys(marker)
                    if _is_calendar_date(d := key[len(marker):].split("/", 1)[0])})

    def rows_of(as_of: str) -> list[dict]:
        """그 날짜 파티션에서 대상 ETF 들의 구성종목 행."""
        prefix = canonical_etf_holdings_partition("KR", as_of)
        return [row for key in storage.list_keys(prefix + "/") if key.endswith(".parquet")
                for row in _read_parquet_rows(storage.get_bytes(key)) if row.get("etf_id") in etf_ids]

    tickers: set[str] = set()
    used: set[str] = set()
    # start 시점에 유효한 스냅샷은 **ETF 마다** 다르다 — 다른 ETF 만 갱신된 날짜를 대상 ETF 의 스냅샷으로 쓰지 않는다.
    pending = set(etf_ids)
    for as_of in reversed([d for d in dates if d <= start.isoformat()]):
        if not pending:
            break
        found = [row for row in rows_of(as_of) if row["etf_id"] in pending]
        if found:
            used.add(as_of)
            pending -= {row["etf_id"] for row in found}
            tickers.update(c for row in found if (c := krx_short_code(row.get("constituent_ticker"))))
    for as_of in (d for d in dates if start.isoformat() < d <= end.isoformat()):
        found = rows_of(as_of)
        if found:
            used.add(as_of)
            tickers.update(c for row in found if (c := krx_short_code(row.get("constituent_ticker"))))
    ordered = sorted(used)
    # start 에 유효한 스냅샷이 없는 ETF 가 있으면 그 앞 기간의 구성은 모른다 — 추정하지 않고 드러낸다.
    coverage = {"snapshots": ordered,
                "uncovered_before": None if not pending else (ordered[0] if ordered else end.isoformat()),
                "etfs_without_snapshot_at_start": sorted(pending)}
    return sorted(tickers), coverage


def _kst_midnight_after(day: str) -> str:
    """접수일 다음날 00:00 KST(UTC ISO). 공개 시각을 모르는 날짜 자료의 보수적 가시 경계 — 시각을 지어내지 않는다."""
    moment = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), tzinfo=KST)
    return moment.astimezone(timezone.utc).isoformat()


def _normalize_financial(objects: list[dict], raw_manifest: dict) -> tuple[list[dict], list[dict]]:
    corps = raw_manifest["request_scope"]["corps"]
    rcept_dates: dict[str, str] = {}
    statements, shares = {}, {}
    rows = []
    # 수집이 계획에서 뺀 보고서(비12월 결산)는 여기서 거부로 드러낸다 — 조용한 누락이 성공이 되지 않게.
    rejects = list(raw_manifest["request_scope"].get("unsupported_reports", []))
    for obj in objects:
        request = obj["request"]
        kind = request["kind"]
        body = json.loads(obj["body"].decode("utf-8"))
        items = body.get("list") if isinstance(body, dict) else None
        if not isinstance(items, list):
            rejects.append({"raw_key": obj["key"], "reasons": ["unexpected_shape"]})
            continue
        items = [item for item in items if isinstance(item, dict)]
        if kind == "list":
            for item in items:
                try:
                    day = datetime.strptime(str(item.get("rcept_dt")), "%Y%m%d").date().isoformat()
                except ValueError:
                    continue            # 접수일을 못 읽으면 그 접수번호는 "모름" — 수신 기준으로 떨어진다
                if item.get("rcept_no"):
                    rcept_dates[item["rcept_no"]] = day
            continue
        if any(ln.get("corp_code") not in (None, request["corp_code"]) for ln in items):
            rejects.append({**{k: request.get(k) for k in ("corp_code", "bsns_year", "reprt_code")},
                            "raw_key": obj["key"], "reasons": ["response_identity_mismatch"]})
            continue
        target = (request["corp_code"], request["bsns_year"], request["reprt_code"])
        if kind == "statement":
            statements[(*target, request["fs_div"])] = {**obj, "body_json": {**body, "list": items}}
        else:
            shares[target] = {**obj, "body_json": {**body, "list": items}}

    def finish(row: dict, sources: list[dict]) -> dict:
        """수신·가시시각·근거 키를 채운다. 접수일을 모두 확인한 행만 공개일 기준 가시시각을 갖는다."""
        received = max(src["fetched_at"] for src in sources)
        # 입력 전부의 접수일을 확인해야 공개일 기준이다 — 접수번호 없는 입력은 "모름"으로 센다.
        release = [rcept_dates.get(i.get("rcept_no")) for i in row["inputs"]]
        row.update({"received_at": received, "raw_key": sources[0]["key"], "raw_sha256": sources[0]["sha256"],
                    "inputs": json.dumps(row["inputs"], ensure_ascii=False, sort_keys=True)})
        if release and all(release):
            row["rcept_date"] = max(release)
            row["available_at"] = min(received, _kst_midnight_after(row["rcept_date"]),
                                      key=datetime.fromisoformat)
            row["availability_basis"] = "provider_release_date"
        else:
            # 접수일을 목록에서 확인 못 한 판본 — 공개일을 추정하지 않고 실제 수신부터 보이게 한다.
            row.update({"rcept_date": None, "available_at": received, "availability_basis": "received"})
        return row

    by_report: dict[tuple, list[tuple[dict, list[dict]]]] = {}
    for (corp_code, year, code, fs_div), statement in sorted(statements.items()):
        share = shares.get((corp_code, year, code))
        extracted, bad = dart_fundamental.extract(
            {"corp_code": corp_code, "stock_code": corps[corp_code]["stock_code"]},
            year, code, fs_div, statement, share["body_json"] if share else None)
        rejects.extend({**b, "raw_key": statement["key"]} for b in bad)
        for row in extracted:
            sources = [statement] + ([share] if share and row["metric"] == "bps" else [])
            for item in row["inputs"]:
                item["raw_key"] = statement["key"] if "account_id" in item else (share or statement)["key"]
            by_report.setdefault((corp_code, year, fs_div), []).append((row, sources))
    for (corp_code, year, fs_div), items in by_report.items():
        fy = [r for r, _ in items if r["fiscal_period"] == "FY"]
        if fy:
            q3 = [r for r, _ in items if r["fiscal_period"] == "Q3"]
            derived, bad = dart_fundamental.derive_q4(fy, q3)
            rejects.extend(bad)
            fy_src = {r["metric"]: src for r, src in items if r["fiscal_period"] == "FY"}
            q3_src = {r["metric"]: src for r, src in items if r["fiscal_period"] == "Q3"}
            items = items + [(r, fy_src[r["metric"]] + q3_src[r["metric"]]) for r in derived]
        rows.extend(finish(dict(row), sources) for row, sources in items)
    return rows, rejects


FINANCIAL = DatasetSpec(
    dataset="financial_metric",
    columns=(Column("corp_code", "str"), Column("instrument_code", "str"), Column("fiscal_year", "int"),
             Column("fiscal_period", "str"), Column("period_end", "date"), Column("metric", "str"),
             Column("period_kind", "str"), Column("fs_basis", "str"), Column("derivation", "str"),
             Column("value", "str"), Column("unit", "str"), Column("formula", "str"), Column("inputs", "str"),
             Column("rcept_no", "str"), Column("rcept_date", "date"), *_PROVENANCE),
    key=("corp_code", "fiscal_year", "fiscal_period", "metric", "period_kind", "fs_basis"),
    partition=lambda r: canonical_financial_metric_partition("KR", r["period_end"]),
    normalize=_normalize_financial,
    table="financial_metric",
    collection_vendor="dart",
)


def filing_window(today_kst: date, from_date: str | None, to_date: str | None) -> tuple[date, date]:
    """정기 수집은 접수일 (오늘 − 14일 ~ 오늘), 백필은 명시한 접수일 기간(오늘 이후 불가)."""
    if from_date is None and to_date is None:
        return today_kst - timedelta(days=REGULAR_FILING_DAYS), today_kst
    if from_date is None or to_date is None:
        raise SystemExit("백필은 --from 과 --to 를 함께 준다")
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if end > today_kst or start > end:
        raise SystemExit(f"접수일 기간이 유효하지 않다: {start}~{end} (오늘 {today_kst} 이후 불가)")
    return start, end


def collect_financial(storage: Storage, source, run_id: str, *, etf_ids: list[str],
                      from_date: str | None, to_date: str | None, now: datetime | None = None) -> int:
    """구성종목(기간별 스냅샷) × 창 안에 접수된 정기보고서 → 목록·전체 재무제표(CFS·OFS)·주식총수 응답을 그대로 남긴다."""
    from ..sources.http import StopFetch

    producer = "ingest_raw_financial_metric"
    done = existing_raw_manifest(storage, FINANCIAL.dataset, run_id)
    if done is not None:
        return record_already_collected(storage, FINANCIAL, run_id, producer, done)
    started_at = now or datetime.now(timezone.utc)
    start, end = filing_window(started_at.astimezone(KST).date(), from_date, to_date)
    tickers, coverage = constituents_between(storage, etf_ids, start, end)
    scope = {"etf_ids": etf_ids, "filing_window": {"from": start.isoformat(), "to": end.isoformat()},
             "constituents": tickers, "holdings_coverage": coverage, "corps": {}, "unmapped": [],
             "mode": "backfill" if from_date else "regular"}
    if not tickers:
        # 그 기간의 구성종목 스냅샷이 없다 — 현재 구성으로 대신하지 않는다.
        return write_raw_run(storage, FINANCIAL, run_id, producer=producer, objects=[
            RawObject("dart", "market", "KR", "universe", "json", None, {"kind": "universe"}, "error",
                      "no_holdings_snapshot", started_at.isoformat())],
            started_at=started_at, request_scope=scope)
    objects: list[RawObject] = []

    def keep(kind: str, stem: str, result) -> None:
        """DART 응답 하나를 raw 객체로 남긴다(상태·요청 서술 포함)."""
        objects.append(RawObject("dart", "market", "KR", stem, "json", result.body,
                                 {"kind": kind, **result.request}, result.status, result.detail, result.fetched_at))
    try:
        corp_map = source.corp_map()
        for ticker in tickers:
            corp = corp_map.get(ticker)
            if corp is None:
                scope["unmapped"].append(ticker)       # 우선주·비신고 종목 — DART 공시 주체가 아니다
                continue
            corp_code = corp["corp_code"]
            scope["corps"][corp_code] = {"stock_code": ticker, "corp_name": corp.get("corp_name")}
            pages = source.filings(corp_code, start - timedelta(days=LIST_LOOKBACK_DAYS), end)
            for number, page in enumerate(pages, start=1):
                keep("list", f"{corp_code}-list-p{number}", page)
            listed = [item for page in pages if page.status == "ok"
                      for item in json.loads(page.body.decode("utf-8")).get("list", [])]
            targets, unsupported = dart_fundamental.plan_reports(listed, start, end)
            scope.setdefault("unsupported_reports", []).extend(unsupported)
            for year, code in sorted(targets):
                for fs_div in ("CFS", "OFS"):
                    keep("statement", f"{corp_code}-{year}-{code}-{fs_div}", source.statement(corp_code, year, code, fs_div))
                keep("shares", f"{corp_code}-{year}-{code}-shares", source.shares(corp_code, year, code))
    except StopFetch as exc:
        # 키·한도·점검 — 남은 호출을 멈추고 받은 것까지만 남긴다(부분 실패로 드러난다).
        objects.append(RawObject("dart", "market", "KR", "stopped", "json", None, {"kind": "stop"}, "error",
                                 str(exc)[:200], datetime.now(timezone.utc).isoformat()))
    return write_raw_run(storage, FINANCIAL, run_id, producer=producer, objects=objects,
                         started_at=started_at, request_scope=scope)
