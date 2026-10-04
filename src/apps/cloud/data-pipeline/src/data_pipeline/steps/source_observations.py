"""분석 v2 원천 관측 세 데이터셋의 수집 → 정제 → 적재 공통 경로 (ALPHA-1130).

데이터셋: `macro_observation`(매크로 5계열) · `financial_metric`(DART 재무 지표) ·
`sector_classification`(KIS 지수업종). 계약 정본은 docs/design/etf-data-storage-plan.md §10 이다.
공급자별 요청·해석은 `sources/` 모듈이, 데이터셋별 업무 규칙(정규화·수집 창·명세)은 `source_observations_<데이터셋>`
모듈이 하고, 이 모듈은 셋이 같은 저장·계보 규칙을 지키게 한다.

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
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    collection_log_key,
    quality_log_key,
    raw_observation_partition,
    raw_run_manifest_key,
    run_manifest_consumed_key,
    unconsumed_run_ids,
)
from ..minute.artifacts import put_immutable

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
    # 같은 정제가 함께 만드는 둘째 행 집합(재무: 보고서 판본). 같은 manifest·같은 적재 트랜잭션에 실린다 —
    # 지표 행 없이 판본만, 판본 없이 지표만 실리는 상태가 없다.
    companion: "DatasetSpec | None" = None
    # 거부 가운데 실행 장애가 아닌 '분류된 결손'을 가리는 판별자(재무만 준다). 결손은 품질 로그·manifest 에 건수와 사유로
    # 남지만 정제를 부분 실패(exit 2)로 만들지 않는다. 없으면 모든 거부가 실패다(매크로·업종은 그대로).
    # 원장에는 결손 건수 칸이 없다 — 원장의 failed_records 는 실패만 세고, 결손은 품질 로그·manifest·판본 표에서 본다.
    is_gap: Callable[[dict], bool] | None = None

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


def ensure_same_request(dataset: str, run_id: str, manifest: dict, requested: dict) -> None:
    """완료된 run_id 를 **다른 요청 범위**로 다시 부르면 거부한다. 수동 백필을 같은 분에 여럿 trigger 하면 슬롯이
    같아 run_id 가 겹친다 — 그대로 '이미 수집'으로 성공하면 뒤 범위는 영영 수집되지 않는다."""
    recorded = (manifest.get("request_scope") or {}).get("requested")
    if recorded != requested:
        raise SystemExit(f"{dataset} run_id={run_id} 는 다른 요청 범위로 이미 수집됐다 "
                         f"(기록 {recorded}, 요청 {requested}) — 1분 이상 간격을 두고 다시 trigger 한다")


def code_version() -> str:
    """이 정제 코드의 Git SHA(이미지가 `GIT_SHA` 로 주입). 카탈로그 버전(`OPS_CATALOG_VERSION` 우선)은 코드 판이 아니다."""
    return os.environ.get("GIT_SHA") or "unknown"


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
                    "started_at": started_at.isoformat(),
                    # 복구 계약(§10.3): 같은 raw 를 이 코드 판으로 정제해야 같은 정본이 나온다.
                    # 이미지가 GIT_SHA 를 주입하기 전엔 'unknown' — 그때는 정확 재현을 주장하지 않는다.
                    "code_version": code_version()}
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
        exit_code = 1   # PARTIAL(2) 로 남기면 하류가 '충족'으로 넘어가 수집 감사 기록 없이 정제·적재가 돈다
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
    done = _completed_manifest(storage, spec, run_id)
    if done is not None:
        # 이미 끝난 정제를 미완료 표지로 덮으면 유효한 결과가 `load --all` 에서 영영 빠진다 — 덮지 않는다.
        if done.get("input_run_id") != input_run_id:
            raise SystemExit(f"{producer} run_id={run_id} 는 다른 입력({done.get('input_run_id')})으로 이미 정제됐다")
        logger.info("%s run_id=%s 는 이미 정제 완료 — 다시 쓰지 않는다", spec.dataset, run_id)
        return PARTIAL_EXIT if done.get("rejected") else 0
    exit_code = 0
    failures: list[dict] = []
    gaps: list[dict] = []
    completed: bytes | None = None
    try:
        storage.put_bytes(manifest_key, json.dumps(
            {"run_id": run_id, "producer": producer, "canonical_written": False}).encode("utf-8"))
        raw_manifest, objects = _load_raw_objects(storage, spec.dataset, input_run_id)
        result = ([], []) if raw_manifest.get("skipped_reason") else spec.normalize(objects, raw_manifest)
        rows, rejects = result[0], result[1]
        companion_rows = list(result[2]) if len(result) > 2 else []
        for reject in rejects:
            (gaps if spec.is_gap is not None and spec.is_gap(reject) else failures).append(reject)
        for row in [*rows, *companion_rows]:
            row["raw_run_id"] = input_run_id
        rows, conflicts, collapsed = _collapse(spec, rows)
        failures.extend(conflicts)
        artifact = write_rows(spec, rows)
        artifact_key = canonical_run_partition_key(spec.dataset, run_id, raw_manifest["ingest_date"])
        artifact_sha = put_immutable(storage, artifact_key, artifact)
        partitions = _merge_canonical(storage, spec, rows)
        companion = None
        if spec.companion is not None:
            # 판본 사실은 실행별 artifact 와 DB 에만 둔다(현재 상태 파티션 없음 — 실행마다 새 사실이지 갱신이 아니다).
            data = write_rows(spec.companion, companion_rows)
            key = canonical_run_partition_key(spec.companion.dataset, run_id, raw_manifest["ingest_date"])
            companion = {"dataset": spec.companion.dataset, "key": key, "sha256": put_immutable(storage, key, data),
                         "rows": len(companion_rows)}
        # 완료 manifest 는 quality_log 가 남은 뒤에 쓴다(아래) — 검증 기록 없이 완료로 보이면 `load --all` 이
        # 그 실행을 싣고 소비 마커까지 남겨 빠진 검증 기록이 영영 드러나지 않는다(normalize_price 와 같은 순서).
        completed = json.dumps({
            "run_id": run_id, "producer": producer, "dataset": spec.dataset,
            "canonical_written": True, "input_run_id": input_run_id,
            "raw_manifest_sha256": raw_manifest["manifest_sha256"],
            "code_version": code_version(),
            # artifact 의 report_date 는 raw 수집일(ingest_date)이다 — 관측일·공개일이 아니다.
            "artifact": {"key": artifact_key, "sha256": artifact_sha, "rows": len(rows),
                         "partition_date": "ingest_date"},
            "canonical_partitions": partitions, "rows": len(rows),
            # rejected 는 실행 실패만 센다(재실행 시 exit 2 판정의 근거). 분류된 결손은 gaps 로 따로 남긴다.
            "rejected": len(failures), "gaps": len(gaps), "collapsed_duplicates": collapsed,
            "companion": companion,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
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
                "gaps": gaps[:200], "records_gap": len(gaps),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ops": {"records_out": log.get("rows", 0), "failed_records": len(failures)}})
    quality_written = True
    try:
        storage.put_bytes(quality_log_key(spec.dataset, started_at.date().isoformat(), run_id),
                          json.dumps(log, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        logger.exception("quality_log 기록 실패")
        quality_written = False
        exit_code = 1
    if completed is not None and quality_written and exit_code != 1:
        try:
            storage.put_bytes(manifest_key, completed)
        except Exception:
            logger.exception("canonical run manifest 기록 실패")
            exit_code = 1
    logger.info("%s 정제: rows=%s failures=%d gaps=%d exit=%d", spec.dataset, log.get("rows"), len(failures),
                len(gaps), exit_code)
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

    소비 마커는 커밋과 검증 기록(quality_log)이 끝난 뒤에만 쓴다 — 그 전에 죽은 실행은 마커가 없어 `--all` 이 이어 싣는다.
    """
    from ..db import connect

    started_at = datetime.now(timezone.utc)
    if (input_run_id is None) == (not pending):
        raise SystemExit(f"{producer} 는 --input-run-id 또는 --all 중 정확히 하나가 필요하다")
    targets = [input_run_id] if input_run_id else unconsumed_run_ids(storage, "canonical", spec.dataset, CONSUMER)
    loaded, inserted, skipped, failures, markers = [], 0, [], [], []
    exit_code = 0
    sql = _insert_sql(spec)
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
            companion_params = _companion_params(storage, spec, manifest, canonical_run_id)
            with connect(db) as conn, conn.cursor() as cur:
                raw_run_id = manifest["input_run_id"]
                # raw 실행 단위 직렬화 — 같은 raw 의 두 적재가 나란히 검사를 통과해 서로 다른 정제 결과를 섞지 않게.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{spec.table}:{raw_run_id}",))
                # 같은 raw 를 다른 규칙으로 다시 정제한 결과는 싣지 않는다 — 판본·지표 정체성은 raw 실행이라, 옛 행에
                # 새 정제의 일부만 덧붙는 혼합을 ON CONFLICT 로는 못 막는다. 같은 내용(같은 artifact 해시)의 재적재만 통과.
                shas = {spec.table: artifact["sha256"]}
                if spec.companion is not None:
                    shas[spec.companion.table] = manifest["companion"]["sha256"]
                conflicts = _reload_conflicts(cur, spec, raw_run_id, shas)
                if conflicts:
                    raise ValueError(f"같은 raw 의 정제 결과가 이미 다른 내용으로 적재돼 있다: {conflicts}")
                before = _count(cur, spec.table, canonical_run_id)
                if params:
                    cur.executemany(sql, params)
                if companion_params:
                    # 지표 행과 판본 행은 한 트랜잭션이다 — 한쪽만 실린 상태가 조회에 보이지 않게.
                    cur.executemany(_insert_sql(spec.companion), companion_params)
                inserted += _count(cur, spec.table, canonical_run_id) - before
            markers.append((run_manifest_consumed_key("canonical", spec.dataset, canonical_run_id, CONSUMER),
                            json.dumps({"consumer": CONSUMER, "rows": len(rows), "loaded_by": run_id,
                                        "at": datetime.now(timezone.utc).isoformat()}).encode("utf-8")))
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
        # 소비 마커를 쓰지 않는다 — 쓰면 `--all` 이 이 실행을 빼서 검증 기록 없는 적재가 영영 남는다.
        # 다음 회차가 같은 artifact 를 다시 싣는다(같은 내용의 재적재는 멱등이다).
        return 1
    for key, marker in markers:
        storage.put_bytes(key, marker)
    logger.info("%s 적재: runs=%d rows=%d inserted=%d failures=%d",
                spec.dataset, len(loaded), rows_in, inserted, len(failures))
    return exit_code


def _insert_sql(spec: DatasetSpec) -> str:
    columns = [*spec.names(), "canonical_run_id", "artifact_key", "artifact_sha256"]
    return (f"INSERT INTO {spec.table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
            " ON CONFLICT DO NOTHING")


def _companion_params(storage: Storage, spec: DatasetSpec, manifest: dict, canonical_run_id: str) -> list[list]:
    """manifest 의 companion artifact → 적재 파라미터. companion 이 있어야 하는 데이터셋에 없으면 적재하지 않는다
    (판본 없는 지표는 조회 계약 밖이다 — 옛 형태의 manifest 를 조용히 싣지 않는다)."""
    if spec.companion is None:
        return []
    companion = manifest.get("companion")
    if not companion:
        raise ValueError(f"{spec.dataset} manifest 에 companion({spec.companion.dataset}) artifact 가 없다")
    data = storage.get_bytes(companion["key"])
    if sha256(data) != companion["sha256"]:
        raise ValueError(f"companion artifact 바이트가 manifest 와 다르다: {companion['key']}")
    return [[*(r[c] for c in spec.companion.names()), canonical_run_id, companion["key"], companion["sha256"]]
            for r in read_rows(spec.companion, data)]


def _reload_conflicts(cur, spec: DatasetSpec, raw_run_id: str, shas: dict[str, str]) -> list[dict]:
    """같은 raw 실행의 행이 이미 있는데 **다른 artifact**(내용 해시가 다른 정제 결과)에서 왔으면 그 표들.

    정규화는 결정적이라 같은 코드·같은 raw 는 같은 바이트다 — artifact sha256 이 곧 정제 내용의 정체성이라 값·근거·
    가시시각까지 전부 덮는다(열 몇 개를 골라 비교하면 빠진 열이 새는 문이다).
    """
    conflicts = []
    for table, sha in shas.items():
        cur.execute(f"SELECT DISTINCT artifact_sha256 FROM {table} WHERE raw_run_id = %s", (raw_run_id,))
        existing = {row[0] for row in cur.fetchall()}
        if existing and existing != {sha}:
            conflicts.append({"table": table, "loaded": sorted(existing), "incoming": sha})
    return conflicts


def _count(cur, table: str, canonical_run_id: str) -> int:
    cur.execute(f"SELECT count(*) FROM {table} WHERE canonical_run_id = %s", (canonical_run_id,))
    return cur.fetchone()[0]
