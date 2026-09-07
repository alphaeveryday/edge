"""세 minute 레인의 DB 승자·확정 이력 대사와 불변 논리 격리. 객체를 삭제/이동하지 않는다."""

from __future__ import annotations

from datetime import datetime
import json
import logging
import re

from ..lake.storage import (
    canonical_price_minute_artifact_key, canonical_etf_inav_minute_artifact_key,
    canonical_sector_index_minute_artifact_key, minute_content_artifact_key,
    minute_content_manifest_key, minute_artifact_quarantine_key,
)
from .artifact_reader import ArtifactReadError, read_window_artifact
from .artifacts import parse_manifest, put_immutable, serialize_manifest, sha256_bytes
from .models import KST
from .states import SOURCE_GROUPS_BY_DATASET

logger = logging.getLogger(__name__)
MINUTE_ARTIFACT_DATASETS = frozenset({"price_minute", "etf_inav_minute", "sector_index_minute"})
CLOSED_ARTIFACT_PHASES = frozenset({"DRAINED", "QC_RUNNING", "FINALIZED", "FAILED"})
_LEGACY_KEYS = {
    "price_minute": canonical_price_minute_artifact_key,
    "etf_inav_minute": canonical_etf_inav_minute_artifact_key,
    "sector_index_minute": canonical_sector_index_minute_artifact_key,
}
_COORDINATES = ("window_start", "window_end", "generation", "artifact_checksum",
                "manifest_uri", "manifest_checksum")


def _verification_failure(report, error, context):
    # 읽기 경계가 감싼 원인을 보존한다. 404는 참조 무결성 결손이지만 권한/timeout은
    # 내용을 판정하지 못한 운영 장애다. 두 경우 모두 격리는 금지한다.
    if isinstance(error, ArtifactReadError):
        infrastructure = error.code in ("MANIFEST_NOT_FOUND", "ARTIFACT_NOT_FOUND")
        cause = error.__cause__ if infrastructure and error.__cause__ is not None else error
    else:
        cause = error
        infrastructure = not isinstance(error, (ValueError, TypeError, KeyError, UnicodeError))
    missing = isinstance(cause, FileNotFoundError) or getattr(cause, "response", {}).get(
        "Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")
    if infrastructure and not missing:
        report["errors"].append(f"{context}: {type(cause).__name__}: {cause}")
    else:
        report["integrity_error"].append({**context, "error": str(error)})
    logger.error("minute 객체 검증 실패 session=%s context=%s", report["session_id"], context, exc_info=True)


def _snapshot(ledger, session_id):
    # 같은 snapshot에서 현재/이력을 읽는다. ACTIVE 결과는 S3와 원자적이지 않아 provisional.
    with ledger.connect_fn(ledger.db) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur.execute("""SELECT dataset, source_group, session_date, phase
                       FROM minute_ingestion_session WHERE session_id=%s""", (session_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"세션 없음: {session_id}")
        session = dict(zip(("dataset", "source_group", "session_date", "phase"), row, strict=True))
        if session["dataset"] not in MINUTE_ARTIFACT_DATASETS:
            raise ValueError(f"minute artifact 대사 미지원 dataset={session['dataset']}")
        cur.execute("""SELECT window_start, window_end, generation, checksum,
                              manifest_uri, manifest_checksum
                       FROM minute_ingestion_window WHERE session_id=%s
                         AND (generation > 0 OR checksum IS NOT NULL OR manifest_uri IS NOT NULL)
                       ORDER BY window_start""", (session_id,))
        current = [dict(zip(_COORDINATES, row, strict=True)) for row in cur.fetchall()]
        cur.execute("""SELECT h.window_start, w.window_end, h.generation, h.artifact_checksum,
                              h.manifest_uri, h.manifest_checksum, h.artifact_uri
                       FROM minute_window_artifact_commit h JOIN minute_ingestion_window w
                         ON w.session_id=h.session_id AND w.window_start=h.window_start
                       WHERE h.session_id=%s ORDER BY h.window_start, h.generation""", (session_id,))
        history = [dict(zip((*_COORDINATES, "artifact_uri"), row, strict=True))
                   for row in cur.fetchall()]
    return session, current, history


def _verify_reference(storage, session_id, dataset, row):
    # 공통 reader와 같은 검증 경로여야 EOD만 손상된 승자를 정상으로 인증하지 않는다.
    read_window_artifact(
        storage, dataset=dataset, market="KR", session_id=session_id,
        window_start=row["window_start"], window_end=row["window_end"],
        generation=row["generation"], checksum=row["artifact_checksum"],
        manifest_uri=row["manifest_uri"], manifest_checksum=row["manifest_checksum"],
    )
    if row["manifest_uri"] is not None:
        data = storage.get_bytes(row["manifest_uri"])
        if sha256_bytes(data) != row["manifest_checksum"]:
            raise ValueError(f"manifest checksum 불일치: {row['manifest_uri']}")
        manifest = parse_manifest(data)
        key = manifest["artifact_key"]
        if manifest.get("schema_version") == 2:
            local = row["window_start"].astimezone(KST)
            expected = minute_content_manifest_key(dataset, "KR", local.date().isoformat(),
                session_id, local.strftime("%H%M"), row["generation"], row["manifest_checksum"])
            if row["manifest_uri"] != expected:
                raise ValueError("신형 확정 manifest URI가 identity/내용 주소와 다르다")
    else:
        local = row["window_start"].astimezone(KST)
        key = _LEGACY_KEYS[dataset]("KR", local.date().isoformat(), local.strftime("%H%M"),
                                    row["generation"])
    if "artifact_uri" in row and row["artifact_uri"] != key:
        raise ValueError(f"확정 이력 artifact URI가 manifest와 다르다: {row['artifact_uri']}")
    return {**row, "artifact_uri": key}


def _candidate(storage, key, *, dataset, session_id, session_date):
    data = storage.get_bytes(key)
    checksum = sha256_bytes(data)
    parts = dict(segment.split("=", 1) for segment in key.split("/") if "=" in segment)
    hhmm = parts["window"]
    if key.endswith("/manifest.json"):
        manifest = parse_manifest(data)
        if (manifest.get("schema_version") != 2 or manifest["dataset"] != dataset
                or manifest["session_id"] != session_id):
            raise ValueError("후보 manifest identity 불일치")
        start = datetime.fromisoformat(manifest["window_start"]).astimezone(KST)
        if start.date().isoformat() != session_date or start.strftime("%H%M") != hhmm:
            raise ValueError("후보 manifest window 불일치")
        expected = minute_content_manifest_key(
            dataset, "KR", session_date, session_id, hhmm, manifest["generation"], checksum,
        )
        # manifest만 온 것이 아니라 그 재료도 온전한지 확인한다.
        artifact = storage.get_bytes(manifest["artifact_key"])
        if sha256_bytes(artifact) != manifest["artifact_checksum"]:
            raise ValueError("후보 artifact checksum 불일치")
        kind = "manifest"
    else:
        expected = minute_content_artifact_key(dataset, "KR", session_date, session_id, hhmm, checksum)
        kind = "artifact"
    if key != expected:
        raise ValueError("후보 경로와 저장 바이트 checksum 불일치")
    return {"uri": key, "checksum": checksum, "kind": kind,
            "reason": "DB 현재 승자와 확정 이력 어느 쪽에도 참조되지 않은 내용 주소 후보"}


def reconcile_minute_artifacts(*, ledger, storage, session_id: str,
                               quarantine: bool = False, actor: str | None = None,
                               reason: str | None = None) -> dict:
    """읽기 전용 snapshot 대사. 닫힌 세션의 검증된 미확정 후보만 논리 격리한다.

    scan_complete는 DB/LIST/객체 검증을 실제로 끝냈다는 뜻이다. 실패를 빈 clean으로
    숨기지 않는다. 종료 phase는 writer commit을 거부하지만 늦은 PUT까지 막지는 않으므로
    결과는 실행 시점의 목록이며, 늦은 후보는 재실행에서 추가로 발견할 수 있다.
    """
    report = {"session_id": session_id, "dataset": None, "phase": None,
              "provisional": True, "committed_current": [], "committed_history": [],
              "uncommitted_candidate": [], "legacy_unverified": [], "integrity_error": [],
              "scan_complete": False, "errors": [], "quarantine_records": [],
              "quarantine_complete": False, "ok": False}
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("session_id 형식 오류")
        if quarantine and (not (actor or "").strip() or not (reason or "").strip()):
            raise ValueError("격리는 actor와 reason이 필요하다")
        session, current, history = _snapshot(ledger, session_id)
        dataset, day = session["dataset"], session["session_date"].isoformat()
        report.update(dataset=dataset, phase=session["phase"], session_date=day,
                      provisional=session["phase"] not in CLOSED_ARTIFACT_PHASES)
        # 보호 집합은 검증 전에 수집한다. 손상된 승자 참조를 패자로 격리하지 않는다.
        protected = {row[k] for row in (*current, *history)
                     for k in ("manifest_uri", "artifact_uri") if row.get(k)}
        history_by_window = {(r["window_start"], r["generation"]): r for r in history}
        current_by_window = {r["window_start"]: r for r in current}
        for category, rows in (("committed_current", current), ("committed_history", history)):
            for row in rows:
                try:
                    verified = _verify_reference(storage, session_id, dataset, row)
                    protected.add(verified["artifact_uri"])
                    latest = current_by_window.get(row["window_start"])
                    if latest is None or row["generation"] > latest["generation"]:
                        raise ValueError("확정 이력이 현재 window 세대를 앞선다")
                    if category == "committed_current" and row["manifest_uri"] and "/content=" in row["manifest_uri"]:
                        if history_by_window.get((row["window_start"], row["generation"])) != verified:
                            raise ValueError("신형 현재 승자의 확정 이력 누락/좌표 충돌")
                    if category == "committed_history" and row["generation"] == latest["generation"]:
                        if any(row[k] != latest[k] for k in _COORDINATES):
                            raise ValueError("현재 window와 같은 세대의 확정 이력이 다르다")
                        continue  # 현재 승자는 current에서만 보고한다.
                    report[category].append(verified)
                except Exception as error:
                    _verification_failure(report, error, {"window_start": row["window_start"],
                        "generation": row["generation"], "uri": row["manifest_uri"]})

        canonical_prefix = f"canonical/market_data/{dataset}/market=KR/session_date={day}/"
        content_prefix = (f"operations_archive/minute_manifests/dataset={dataset}/market=KR/"
                          f"session_date={day}/session_id={session_id}/")
        prefixes = [canonical_prefix, content_prefix]
        # legacy manifest에는 session_id 파티션이 없다. 세대 번호로 과거 확정을 추측하지
        # 않고, DB 참조 밖 legacy는 전부 unverified로 보존한다.
        sources = SOURCE_GROUPS_BY_DATASET[dataset] | {session["source_group"]}
        prefixes.extend(f"operations_archive/minute_manifests/dataset={dataset}/source={source}/"
                        f"market=KR/session_date={day}/" for source in sorted(sources))
        keys = set()
        for prefix in prefixes:
            keys.update(storage.list_keys(prefix))  # LIST 실패면 scan_complete는 끝까지 false.
        for key in sorted(keys - protected):
            parts = dict(segment.split("=", 1) for segment in key.split("/") if "=" in segment)
            if "session_id" in parts and parts["session_id"] != session_id:
                continue
            if "session_id" not in parts:
                report["legacy_unverified"].append({"uri": key, "checksum": None,
                    "reason": "DB 참조 없는 legacy 객체 — 과거 확정 여부를 인증할 수 없어 보존"})
                continue
            try:
                report["uncommitted_candidate"].append(_candidate(
                    storage, key, dataset=dataset, session_id=session_id, session_date=day,
                ))
            except Exception as error:
                _verification_failure(report, error, {"uri": key})
        report["scan_complete"] = not report["integrity_error"] and not report["errors"]
        if quarantine:
            if report["provisional"]:
                raise ValueError(f"열린 세션은 격리할 수 없다: phase={session['phase']}")
            if not report["scan_complete"]:
                raise ValueError("무결성 검사를 완료하지 못해 격리하지 않는다")
            for candidate in report["uncommitted_candidate"]:
                record = {"schema_version": 1, "session_id": session_id, "dataset": dataset,
                          "object_uri": candidate["uri"], "object_checksum": candidate["checksum"],
                          "kind": candidate["kind"], "detection_reason": candidate["reason"],
                          "actor": actor.strip(), "reason": reason.strip()}
                data = serialize_manifest(record)
                uri = minute_artifact_quarantine_key(session_id, sha256_bytes(data))
                put_immutable(storage, uri, data)
                report["quarantine_records"].append(uri)
            report["quarantine_complete"] = True
        report["ok"] = report["scan_complete"]
    except Exception as error:
        logger.exception("minute 대사 실패 dataset=%s session=%s", report["dataset"], session_id)
        report["errors"].append(str(error))
    return json.loads(json.dumps(report, default=str))


def reconcile_artifacts_cli(settings, *, session_id: str | None, quarantine: bool = False,
                            actor: str | None = None, reason: str | None = None) -> int:
    """0=완전한 대사/요청 격리, 1=무결성 위반, 2=자격·설정·조회/기록 실패."""
    from ..lake import make_storage
    from .repository import MinuteLedger

    if settings.db is None or not session_id:
        logger.error("reconcile-minute-artifacts는 db 설정과 --session-id가 필요하다")
        return 2
    report = reconcile_minute_artifacts(
        ledger=MinuteLedger(db=settings.db), storage=make_storage(settings.storage),
        session_id=session_id, quarantine=quarantine, actor=actor, reason=reason,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    if report["errors"]:
        return 2
    return 0 if report["ok"] else 1
