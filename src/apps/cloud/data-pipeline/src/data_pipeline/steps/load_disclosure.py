"""공시 fact 적재 — canonical 공시 → 이벤트 스토어 (ALPHA-476).

공시는 그동안 canonical(`disclosures/{supply_contract_fact,business_segment_fact}`)까지만
흐르고 DB 로는 한 건도 안 들어왔다(`disclosure_document` 0행) — 뉴스가 document 까지 적재되는
것과 대조. 이 스텝이 그 세로 슬라이스를 뚫는다: canonical 공시 fact 를 읽어

  document(type='DISCLOSURE') → disclosure_document → disclosure_fact → typed child
  (supply_contract_fact | business_segment_fact)

로 적재한다. 설명 엔진은 `explanation_run_disclosure_fact` 로 이 fact 를 직접 소비한다 —
공시는 threading(`source_class='NEWS'` 전용)을 타지 않고 fact 경로로 설명에 참여한다(원본
정렬: 정준영 계약이 공시를 canonical_event 대신 fact 레이어로 정렬).

**멱등·정정**: 결정적 ID(document 는 자연키 `(source_code, source_document_id)=
('dart', rcept_no)`, fact 는 `fact_id=f(document_id, fact_type[, ordinal])`)라 재실행이 ID 를
바꾸지 않아 설명 계보가 끊기지 않는다(ADR-0027·ALPHA-456). document 정체성 행은
`ON CONFLICT DO NOTHING`(FK 루트 불변, load-documents 와 같은 모델), **값을 담는 행**
(disclosure_document 파서메타·disclosure_fact·typed child)은 `DO UPDATE … WHERE … IS DISTINCT
FROM …` 로 정정본 재수집·파서 재실행의 갱신을 반영한다(다른 canonical→DB 로더와 같은 규약).

**issuer 게이트**: `disclosure_document.issuer_actor_id` FK 는 `company_profile(actor_id)` 를
RESTRICT 로 요구한다. canonical 의 `corp_code`(8자리 dart)를 `company_profile.dart_corp_code`
로 해소한다. 미해소(마스터 미시드)면 그 공시를 **적재 불가라 skip + 계측**한다 — 넣으면 FK
위반으로 런 전체가 롤백된다. 커버리지 9→309 확장은 별건(ALPHA-491·477).

**durable pending ledger(ALPHA-1045)**: 정상 경로는 공급계약·사업부문 completed manifest의
direct key·SHA·winner를 검증하고 canonical 행 자체를 `disclosure_load_pending`에 먼저 commit한다.
성공한 ID만 typed-fact transaction 안에서 삭제한다. issuer 미해소·검증 거절·일시 DB 실패는
다음 eligible 정상 실행이 다시 시도하며, 보존 만료와 lifetime retry cutoff는 없다(날짜 범위
실행은 같은 report_date 범위만, 무창 실행은 전량을 한 ID당 한 실행 1회 시도한다).
항목별 SAVEPOINT라 한 ID 실패가 다른 성공을 롤백하지 않는다. 기존 canonical full scan은 아직
옛 backlog를 회수하므로 유지한다 — 원장만으로 회수가 증명된 뒤 consumer 전환 PR에서 제거한다.

**fact 게이트**: canonical 정제는 값 이상을 경고로만 통과시키므로(blocking 아님), DB CHECK 를
파이썬에서 **선검증**해 위반 fact 만 뺀다 — 한 건이 배치 전체를 롤백시키지 않게. NaN/Infinity
는 `<0` 비교를 조용히 통과하지만 DB 유한성 CHECK 에 걸리므로 여기서 명시적으로 거른다. 거절된
fact 는 사유와 함께 계측한다(조용한 유실 금지, Rule 12).

**창(from/to) 미지정 = report_date 전체 스캔** — 멱등 skip 이라 재실행 비용은 신규분뿐이다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime, timezone

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..entity_resolution import mint_concept
from ..lake import (
    Storage,
    canonical_business_segment_fact_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    canonical_supply_contract_fact_partition,
    quality_log_key,
)

logger = logging.getLogger(__name__)

JOB_NAME = "load_disclosure"
DATASET = "disclosure_document"

# canonical 공시는 단일 벤더(dart) — source_code 는 고정이다(정제도 dart 만 통과시킴).
SOURCE_CODE = "dart"

# business_segment_fact.share_basis CHECK 어휘(대문자). canonical 파서는 소문자
# (reported/computed/rescaled/unreliable, ALPHA-346)라 대문자로 맞추고, 범위 밖은 NULL 로
# 떨군다(값 이상으로 fact 를 통째로 버리지 않는다 — 매출·비중은 살아있다).
_SHARE_BASIS = {"REPORTED", "COMPUTED", "RESCALED", "UNRELIABLE"}

_SAMPLE_LIMIT = 50
_PARTIAL_EXIT_CODE = 2
# mutable canonical을 읽고 pending에 반영하는 순서를 런 간 직렬화한다. PostgreSQL signed
# bigint 범위 안의 고정 namespace key("DISCLOSU")다.
_PENDING_ENQUEUE_LOCK = 0x444953434C4F5355

_MANIFESTS = (
    ("supply_contract_fact", "normalize_disclosure", "SUPPLY_CONTRACT"),
    ("business_segment_fact", "normalize_disclosure_segment", "BUSINESS_SEGMENT"),
)


def _manifest_winners(storage: Storage, run_id: str) -> list[dict]:
    """완료된 dual manifest의 direct key·SHA·winner만 원장 payload로 확정한다."""
    winners: list[dict] = []
    seen: set[str] = set()
    seen_keys: set[str] = set()
    for dataset, producer, disclosure_type in _MANIFESTS:
        manifest = json.loads(storage.get_bytes(
            canonical_run_manifest_key(dataset, run_id)).decode("utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("run_id") != run_id
                or manifest.get("producer") != producer
                or manifest.get("canonical_written") is not True
                or not isinstance(manifest.get("canonical_partitions"), list)):
            raise ValueError(f"완료된 공시 manifest가 아니다: dataset={dataset} run_id={run_id}")
        for part in manifest["canonical_partitions"]:
            report_date = part.get("report_date") if isinstance(part, dict) else None
            try:
                valid_date = (isinstance(report_date, str)
                              and date.fromisoformat(report_date).isoformat() == report_date)
            except ValueError:
                valid_date = False
            expected_key = (canonical_run_partition_key(dataset, run_id, report_date)
                            if valid_date else None)
            key, digest, ids = part.get("key"), part.get("sha256"), part.get("winner_ids")
            if (key != expected_key or key in seen_keys
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                    or not isinstance(ids, list) or not ids):
                raise ValueError(f"공시 manifest partition이 유효하지 않다: {part!r}")
            seen_keys.add(key)
            data = storage.get_bytes(key)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"공시 manifest SHA가 canonical과 다르다: key={key}")
            rows = _read_parquet_rows(data)
            is_segment = disclosure_type == "BUSINESS_SEGMENT"
            def _identity(item):
                if not isinstance(item, dict):
                    return None
                rcept_no = item.get("rcept_no")
                if not isinstance(rcept_no, str) or not rcept_no.strip():
                    return None
                if not is_segment:
                    return rcept_no
                ordinal = item.get("segment_ordinal")
                if not isinstance(ordinal, int) or isinstance(ordinal, bool):
                    return None
                return rcept_no, ordinal

            row_ids = [_identity(row) for row in rows]
            if len([value for value in row_ids if value is not None]) != len(
                    {value for value in row_ids if value is not None}):
                raise ValueError(f"공시 canonical 행키가 중복이다: key={key}")
            rows_by_id = {row_id: row for row_id, row in zip(row_ids, rows) if row_id is not None}
            wanted = [_identity(item) for item in ids]
            if (any(value is None for value in wanted)
                    or wanted != sorted(set(wanted))):
                raise ValueError(f"공시 manifest winner_ids가 유효하지 않다: {part!r}")
            selected: dict[str, list[dict]] = {}
            for winner_id in wanted:
                if winner_id not in rows_by_id:
                    raise ValueError(f"공시 manifest winner가 결손이다: winner_id={winner_id}")
                row = rows_by_id[winner_id]
                selected.setdefault(row["rcept_no"], []).append(row)
            for rcept_no, selected_rows in selected.items():
                if rcept_no in seen:
                    raise ValueError(f"공시 manifest winner가 중복이다: rcept_no={rcept_no}")
                seen.add(rcept_no)
                payload = json.dumps(selected_rows, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"))
                winners.append({"rcept_no": rcept_no, "disclosure_type": disclosure_type,
                                "rows": selected_rows, "payload": payload,
                                "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                                "source_fetched_at": _source_revision(selected_rows)})
    return winners


def _enqueue_winners(db: DbConfig, storage: Storage, run_id: str) -> None:
    """manifest 읽기부터 enqueue commit까지 직렬화해 늦은 과거 런의 덮어쓰기를 막는다."""
    with connect(db) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_PENDING_ENQUEUE_LOCK,))
        winners = _manifest_winners(storage, run_id)
        if not winners:
            return
        for winner in winners:
            cur.execute(
                "INSERT INTO disclosure_load_pending (rcept_no, disclosure_type, canonical_rows,"
                " payload_sha256, source_fetched_at, first_seen_run_id, last_seen_run_id)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (rcept_no) DO UPDATE SET"
                " disclosure_type=EXCLUDED.disclosure_type, canonical_rows=EXCLUDED.canonical_rows,"
                " payload_sha256=EXCLUDED.payload_sha256,"
                " source_fetched_at=EXCLUDED.source_fetched_at,"
                " last_seen_run_id=EXCLUDED.last_seen_run_id,"
                " last_seen_at=now(), attempt_count=CASE WHEN disclosure_load_pending.payload_sha256"
                " = EXCLUDED.payload_sha256 THEN disclosure_load_pending.attempt_count ELSE 0 END,"
                " last_attempted_at=CASE WHEN disclosure_load_pending.payload_sha256"
                " = EXCLUDED.payload_sha256 THEN disclosure_load_pending.last_attempted_at ELSE NULL END,"
                " last_error_code=CASE WHEN disclosure_load_pending.payload_sha256"
                " = EXCLUDED.payload_sha256 THEN disclosure_load_pending.last_error_code ELSE NULL END,"
                " last_error=CASE WHEN disclosure_load_pending.payload_sha256"
                " = EXCLUDED.payload_sha256 THEN disclosure_load_pending.last_error ELSE NULL END"
                # fetched_at 동률은 advisory lock으로 직렬화된 enqueue 순서가 revision이다.
                # 파서 재실행은 같은 raw 관측에서도 payload를 정정할 수 있으므로 뒤 실행이 이긴다.
                " WHERE disclosure_load_pending.source_fetched_at <= EXCLUDED.source_fetched_at",
                (winner["rcept_no"], winner["disclosure_type"], winner["payload"],
                 winner["payload_sha256"], winner["source_fetched_at"], run_id, run_id),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    f"공시 pending source revision이 역행·충돌한다: rcept_no={winner['rcept_no']}"
                )


def _pending_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rcept_no, disclosure_type, canonical_rows, payload_sha256, attempt_count"
            " FROM disclosure_load_pending ORDER BY first_seen_at, rcept_no"
        )
        return [{"rcept_no": r, "disclosure_type": t,
                 "rows": json.loads(rows) if isinstance(rows, str) else rows,
                 "payload_sha256": digest, "attempt_count": attempts}
                for r, t, rows, digest, attempts in cur.fetchall()]


def _pending_in_window(pending: list[dict], from_date: str | None,
                       to_date: str | None) -> list[dict]:
    """범위 실행은 그 report_date의 pending만 재시도한다. 무창 실행은 전부 회수한다."""
    if from_date is None and to_date is None:
        return pending
    selected = []
    for item in pending:
        report_dates = {row.get("report_date") for row in item["rows"]
                        if isinstance(row, dict) and isinstance(row.get("report_date"), str)}
        if len(report_dates) != 1:
            continue
        report_date = next(iter(report_dates))
        if ((from_date is None or report_date >= from_date)
                and (to_date is None or report_date <= to_date)):
            selected.append(item)
    return selected


def _mark_pending_failure(conn, pending: dict | None, code: str, error: str | None = None) -> None:
    if pending is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE disclosure_load_pending SET attempt_count=attempt_count+1,"
            " last_attempted_at=now(), last_error_code=%s, last_error=%s"
            " WHERE rcept_no=%s AND payload_sha256=%s",
            (code, error, pending["rcept_no"], pending["payload_sha256"]),
        )


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io

    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _source_revision(rows: list[dict]) -> datetime:
    """canonical fetched_at 최댓값을 payload의 단조 source revision으로 확정한다."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    revisions = []
    for row in rows:
        value = row.get("fetched_at")
        try:
            revision = datetime.fromisoformat(value) if isinstance(value, str) else oldest
        except ValueError:
            revision = oldest
        if revision.tzinfo is None:
            revision = revision.replace(tzinfo=timezone.utc)
        revisions.append(revision)
    return max(revisions)


def _partition_dates(storage: Storage, marker: str) -> list[str]:
    """`.../report_date=` 프리픽스 아래 report_date 목록(오름차순). 경로는 빌더로만 만든다."""
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date_str = key[len(marker):].split("/", 1)[0]
        if date_str:
            dates.add(date_str)
    return sorted(dates)


def _read_facts(storage: Storage, builder, from_date, to_date) -> list[dict]:
    """한 canonical 공시 데이터셋(supply|segment)을 창 안에서 읽어 행 리스트로."""
    marker = builder("")  # ".../report_date="
    rows: list[dict] = []
    dates = [d for d in _partition_dates(storage, marker)
             if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]
    for d in dates:
        for key in storage.list_keys(builder(d) + "/"):
            if key.endswith(".parquet"):
                rows.extend(_read_parquet_rows(storage.get_bytes(key)))
    return rows


def _clean_rcept(rows: list[dict]) -> tuple[list[dict], int]:
    """rcept_no(문서 정체성 키)를 strip 하고 공백·결손 행을 뺀다. 공백뿐인 rcept_no 를 그대로
    두면 서로 다른 공시가 같은 document_id 로 접혀 충돌한다. 뺀 수를 함께 돌려준다(계측)."""
    out, skipped = [], 0
    for row in rows:
        rcept_no = (row.get("rcept_no") or "").strip()
        if not rcept_no:
            skipped += 1
            continue
        out.append({**row, "rcept_no": rcept_no})
    return out, skipped


def _resolve_issuers(conn, corp_codes: set[str]) -> dict[str, str]:
    """corp_code → issuer_actor_id. company_profile.dart_corp_code 가 공시 적재의 조인 키다
    (seed 주석). 없는 corp_code 는 dict 에 안 들어와 호출부가 skip 한다."""
    codes = sorted(c for c in corp_codes if c)
    if not codes:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dart_corp_code, actor_id FROM company_profile"
            " WHERE dart_corp_code = ANY(%s)",
            (codes,),
        )
        return {code: actor for code, actor in cur.fetchall()}


def _prepare_supply_rows(conn, rows: list[dict]) -> list[dict]:
    """기존 actor·concept FK로 공급계약 identity를 해소한다.

    상대방은 파서가 정제한 이름과 원문명의 완전일치만 허용한다. 동명이면 NULL로 남겨
    잘못된 계약 thread 결합을 막는다. 계약대상은 NEWS writer와 같은 채번 함수를 쓴다.
    """
    names = sorted({str(value).strip() for row in rows
                    for value in (row.get("counterparty"), row.get("counterparty_raw"))
                    if value and str(value).strip()})
    actor_by_name: dict[str, str | None] = {}
    if names:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.actor_id, e.display_name FROM actor a"
                " JOIN entity e ON e.entity_id = a.actor_id"
                " WHERE e.display_name = ANY(%s)",
                (names,),
            )
            for actor_id, display_name in cur.fetchall():
                name = str(display_name).strip()
                if name not in actor_by_name:
                    actor_by_name[name] = str(actor_id)
                elif actor_by_name[name] != str(actor_id):
                    actor_by_name[name] = None

    from edge_ontology import role_entity_kind

    concept_kind = role_entity_kind("CONTRACT_OBJECT")
    if concept_kind is None:
        raise ValueError("CONTRACT_OBJECT ontology kind is missing")
    pending_concepts: dict[str, tuple[str, str]] = {}
    prepared: list[dict] = []
    for row in rows:
        actor_id = None
        if not row.get("counterparty_withheld"):
            for value in (row.get("counterparty"), row.get("counterparty_raw")):
                name = str(value).strip() if value else ""
                if name and actor_by_name.get(name):
                    actor_id = actor_by_name[name]
                    break

        object_name = str(row.get("object") or "").strip()
        coined = mint_concept("CONTRACT_OBJECT", object_name) if object_name else None
        concept_id = coined[0] if coined else None
        enriched = {**row, "_counterparty_actor_id": actor_id,
                    "_contract_object_concept_id": concept_id}
        # 거절될 fact 때문에 참조 없는 concept master만 남기지 않는다.
        if concept_id is not None and _supply_child(enriched)[0] is None:
            pending_concepts.setdefault(concept_id, (object_name, concept_kind))
        prepared.append(enriched)

    if pending_concepts:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO entity (entity_id, entity_type, display_name, status)"
                " VALUES (%s,'CONCEPT',%s,'ACTIVE') ON CONFLICT (entity_id) DO NOTHING",
                [(cid, name) for cid, (name, _kind) in sorted(pending_concepts.items())],
            )
            cur.executemany(
                "INSERT INTO concept (concept_id, concept_type) VALUES (%s,%s)"
                " ON CONFLICT (concept_id) DO NOTHING",
                [(cid, kind) for cid, (_name, kind) in sorted(pending_concepts.items())],
            )
    return prepared


def _document(rows: list[dict], fact_type: str) -> dict[str, dict]:
    """rcept_no → document 헤더. 한 rcept 는 한 데이터셋에만 오므로(report_nm 라우팅) fact_type
    이 문서 단위로 결정적이다. available_at 은 fetched_at, 결측이면 report_date 로 보수 근사."""
    docs: dict[str, dict] = {}
    for row in rows:
        docs.setdefault(row["rcept_no"], {
            "corp_code": row.get("corp_code"),
            "report_date": row.get("report_date"),
            "available_at": row.get("fetched_at") or row.get("report_date"),
            "source_uri": row.get("source_url"),
            "parser_version": row.get("parser_version"),
            "disclosure_type": fact_type,
        })
    return docs


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 공시 → 이벤트 스토어 적재. 성공 0, 장애 시 비0."""
    started_at = datetime.now(timezone.utc)
    supply_read = segment_read = 0
    skipped_missing_identity = skipped_unresolved_issuer = 0
    skipped_no_report_date = skipped_no_valid_fact = 0
    rejected_facts = 0
    docs_created = docs_already = facts_written = facts_already = 0
    created_sample: list[dict] = []
    rejected_sample: list[dict] = []
    failures: list[dict] = []
    pending_before = pending_after = None if input_run_id is not None else 0
    pending_succeeded = 0
    pending_failures: list[dict] = []
    exit_code = 0

    try:
        if input_run_id is not None:
            # 별도 connect 경계라 이 commit이 끝난 뒤에만 typed-fact 적재를 시작한다.
            _enqueue_winners(db, storage, input_run_id)
        supply_rows, s1 = _clean_rcept(_read_facts(
            storage, canonical_supply_contract_fact_partition, from_date, to_date))
        segment_rows, s2 = _clean_rcept(_read_facts(
            storage, canonical_business_segment_fact_partition, from_date, to_date))
        supply_read, segment_read = len(supply_rows), len(segment_rows)
        skipped_missing_identity = s1 + s2

        seen_fact_ids: set[str] = set()

        with connect(db) as conn:
            all_pending = _pending_rows(conn) if input_run_id is not None else []
            pending_before = len(all_pending)
            pending = _pending_in_window(all_pending, from_date, to_date)
            pending_by_rcept = {item["rcept_no"]: item for item in pending}
            # 원장 payload가 같은 논리 ID의 canonical full-scan 행보다 우선한다. 그래야 mutable
            # parquet가 다음 정제에 덮여도 enqueue 시점 winner를 정확히 재시도한다.
            pending_supply = {item["rcept_no"]: item["rows"] for item in pending
                              if item["disclosure_type"] == "SUPPLY_CONTRACT"}
            pending_segment = {item["rcept_no"]: item["rows"] for item in pending
                               if item["disclosure_type"] == "BUSINESS_SEGMENT"}
            supply_rows = [row for row in supply_rows if row["rcept_no"] not in pending_supply]
            supply_rows.extend(row for rows in pending_supply.values() for row in rows)
            segment_rows = [row for row in segment_rows if row["rcept_no"] not in pending_segment]
            segment_rows.extend(row for rows in pending_segment.values() for row in rows)
            documents = {**_document(supply_rows, "SUPPLY_CONTRACT"),
                         **_document(segment_rows, "BUSINESS_SEGMENT")}
            segment_by_rcept = {}
            for row in segment_rows:
                segment_by_rcept.setdefault(row["rcept_no"], []).append(row)
            issuers = _resolve_issuers(conn, {d["corp_code"] for d in documents.values()})
            supply_by_rcept = {r["rcept_no"]: r for r in supply_rows}

            for rcept_no, doc in sorted(documents.items()):
                pending_item = pending_by_rcept.get(rcept_no)
                issuer_actor_id = issuers.get(doc["corp_code"])
                if issuer_actor_id is None:
                    # 마스터 미시드 — FK RESTRICT 라 넣으면 런 전체 롤백. 세고 뺀다(9→309 별건).
                    skipped_unresolved_issuer += 1
                    _mark_pending_failure(conn, pending_item, "unresolved_issuer")
                    if pending_item:
                        pending_failures.append({"rcept_no": rcept_no,
                                                 "reason": "unresolved_issuer"})
                    continue
                if not doc["report_date"]:
                    # report_date 는 공시의 시간축이자 파티션 정체성 — 없으면 날짜 조회에서 샌다.
                    skipped_no_report_date += 1
                    _mark_pending_failure(conn, pending_item, "missing_report_date")
                    if pending_item:
                        pending_failures.append({"rcept_no": rcept_no,
                                                 "reason": "missing_report_date"})
                    continue
                with conn.cursor() as cur:
                    cur.execute("SAVEPOINT disclosure_item")
                    try:
                        supply = supply_by_rcept.get(rcept_no)
                        if supply is not None:
                            supply = _prepare_supply_rows(conn, [supply])[0]
                        document_id = stable_domain_id("doc", SOURCE_CODE, rcept_no)
                        pairs, rejects = _fact_inserts(
                            rcept_no, document_id, doc, supply,
                            segment_by_rcept.get(rcept_no, []), seen_fact_ids)
                        for reject in rejects:
                            rejected_facts += 1
                            if len(rejected_sample) < _SAMPLE_LIMIT:
                                rejected_sample.append(reject)
                        if not pairs:
                            cur.execute("ROLLBACK TO SAVEPOINT disclosure_item")
                            cur.execute("RELEASE SAVEPOINT disclosure_item")
                            skipped_no_valid_fact += 1
                            _mark_pending_failure(conn, pending_item, "no_valid_fact")
                            if pending_item:
                                pending_failures.append({"rcept_no": rcept_no,
                                                         "reason": "no_valid_fact"})
                            continue

                        cur.execute(
                            "INSERT INTO document (document_id, document_type, source_code,"
                            " source_document_id, published_at, available_at, source_uri)"
                            " VALUES (%s, 'DISCLOSURE', %s, %s, %s, %s, %s)"
                            " ON CONFLICT (source_code, source_document_id) DO NOTHING",
                            (document_id, SOURCE_CODE, rcept_no, doc["report_date"],
                             doc["available_at"], doc["source_uri"]),
                        )
                        created = cur.rowcount == 1
                        cur.execute(
                            "INSERT INTO disclosure_document (document_id, issuer_actor_id,"
                            " disclosure_type, report_date, parser_version, parsed_result_uri)"
                            " VALUES (%s, %s, %s, %s, %s, %s)"
                            " ON CONFLICT (document_id) DO UPDATE SET"
                            " disclosure_type = EXCLUDED.disclosure_type,"
                            " report_date = EXCLUDED.report_date,"
                            " parser_version = EXCLUDED.parser_version,"
                            " parsed_result_uri = EXCLUDED.parsed_result_uri"
                            " WHERE (disclosure_document.disclosure_type, disclosure_document.report_date,"
                            " disclosure_document.parser_version, disclosure_document.parsed_result_uri)"
                            " IS DISTINCT FROM (EXCLUDED.disclosure_type, EXCLUDED.report_date,"
                            " EXCLUDED.parser_version, EXCLUDED.parsed_result_uri)",
                            (document_id, issuer_actor_id, doc["disclosure_type"],
                             doc["report_date"], doc["parser_version"] or "unknown",
                             doc["source_uri"]),
                        )
                        item_written = item_already = 0
                        for header_sql, header_params, child_sql, child_params in pairs:
                            cur.execute(header_sql, header_params)
                            cur.execute(child_sql, child_params)
                            if cur.rowcount == 1:
                                item_written += 1
                            else:
                                item_already += 1
                        if pending_item is not None and rejects:
                            _mark_pending_failure(conn, pending_item, "rejected_fact")
                            pending_failures.append({"rcept_no": rcept_no,
                                                     "reason": "rejected_fact"})
                        elif pending_item is not None:
                            cur.execute(
                                "DELETE FROM disclosure_load_pending"
                                " WHERE rcept_no=%s AND payload_sha256=%s",
                                (rcept_no, pending_item["payload_sha256"]),
                            )
                            if cur.rowcount == 1:
                                pending_succeeded += 1
                            else:
                                pending_failures.append({"rcept_no": rcept_no,
                                                         "reason": "payload_conflict"})
                        cur.execute("RELEASE SAVEPOINT disclosure_item")
                        facts_written += item_written
                        facts_already += item_already
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT disclosure_item")
                        cur.execute("RELEASE SAVEPOINT disclosure_item")
                        _mark_pending_failure(conn, pending_item, "load_error", str(exc))
                        failures.append({"reasons": ["item_load_error"],
                                         "rcept_no": rcept_no, "error": str(exc)})
                        if pending_item:
                            pending_failures.append({"rcept_no": rcept_no,
                                                     "reason": "load_error", "error": str(exc)})
                        continue

                if created:
                    docs_created += 1
                    if len(created_sample) < _SAMPLE_LIMIT:
                        created_sample.append({"document_id": document_id, "rcept_no": rcept_no})
                else:
                    docs_already += 1
            if input_run_id is not None:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM disclosure_load_pending")
                    pending_after = cur.fetchall()[0][0]
    except Exception as exc:
        # manifest/원장 초기화나 공용 DB 조회처럼 항목 격리 밖의 실패다. 항목 쓰기 실패는 위
        # SAVEPOINT에서 격리되고 여기까지 오지 않는다. 트레이스백 대신 사유도 로그에 태운다.
        logger.exception("공시 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        docs_created = facts_written = facts_already = 0
        created_sample = []
        exit_code = 1

    if (pending_failures or failures) and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date, "to_date": to_date,
        "supply_rows_read": supply_read, "segment_rows_read": segment_read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unresolved_issuer": skipped_unresolved_issuer,
        "skipped_no_report_date": skipped_no_report_date,
        "skipped_no_valid_fact": skipped_no_valid_fact,
        "rejected_facts": rejected_facts, "rejected_facts_sample": rejected_sample,
        "documents_already_present": docs_already, "documents_created": docs_created,
        "facts_written": facts_written, "facts_already_present": facts_already,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        "pending_ledger": {
            "before": pending_before, "after": pending_after, "succeeded": pending_succeeded,
            "failed": len(pending_failures), "failed_items": pending_failures[:_SAMPLE_LIMIT],
            "retention": "until_success",
            "retry": "once_per_eligible_run_no_lifetime_cutoff",
        },
        # 원장 관측용 공통 봉투(ALPHA-181). 산출은 fact 행이다(문서는 그 부속). 발행사 미해소·
        # 보고일 결측·유효 fact 없음·거절은 그 공시가 fact 로 안 남은 유실이다.
        "ops": {
            "records_out": facts_written + facts_already,
            "failed_records": (len(failures) + skipped_missing_identity
                               + skipped_unresolved_issuer + skipped_no_report_date
                               + skipped_no_valid_fact + rejected_facts),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_disclosure: supply=%d segment=%d skipped_identity=%d skipped_issuer=%d"
        " skipped_no_report_date=%d skipped_no_valid_fact=%d rejected_facts=%d"
        " docs_created=%d facts_written=%d failures=%d",
        supply_read, segment_read, skipped_missing_identity, skipped_unresolved_issuer,
        skipped_no_report_date, skipped_no_valid_fact, rejected_facts,
        docs_created, facts_written, len(failures),
    )
    if pending_failures:
        logger.warning("load_disclosure: pending 실패 %d건, 잔여 %d건: %s",
                       len(pending_failures), pending_after,
                       [item["rcept_no"] for item in pending_failures[:_SAMPLE_LIMIT]])
    return exit_code


def _finite(value) -> bool:
    """None 또는 유한 수면 True. NaN/Infinity 는 `<0` 비교를 조용히 통과하지만 DB 유한성
    CHECK(`< 'Infinity'`)에 걸려 배치 전체를 롤백시키므로 여기서 명시적으로 거른다."""
    return value is None or not (isinstance(value, float) and not math.isfinite(value))


def _valid_iso_date(value) -> bool:
    """None 또는 파싱 가능한 ISO(YYYY-MM-DD) 문자열이면 True. 파싱 검증을 통과한 두 날짜는
    유효 ISO 라 문자열 사전순 비교가 시간순과 일치한다(미패딩 '2026-2-01' 은 여기서 걸린다)."""
    if not value:
        return True
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def _fact_inserts(rcept_no, document_id, doc, supply, segments, seen_fact_ids):
    """이 문서의 (INSERT pair 목록, 거절 사유 목록). DB CHECK 를 파이썬에서 선검증해 위반 fact 는
    아예 안 보낸다(한 fact 의 CHECK 위반이 런 전체를 롤백시키지 않게). 거절은 조용히 버리지 않고
    사유와 함께 돌려준다(Rule 12). 같은 fact_id 가 런 안에서 재생성되면(예: ordinal 중복) 조용한
    DO NOTHING 유실 대신 거절로 표면화한다."""
    available_at = doc["available_at"]
    pairs, rejects = [], []

    def _emit(fact_type, ordinal, built):
        reason, cols_vals = built
        if reason is not None:
            rejects.append({"rcept_no": rcept_no, "fact_type": fact_type,
                            "ordinal": ordinal, "reason": reason})
            return
        fact_id = stable_domain_id("dfact", document_id, fact_type, ordinal)
        if fact_id in seen_fact_ids:
            rejects.append({"rcept_no": rcept_no, "fact_type": fact_type,
                            "ordinal": ordinal, "reason": "duplicate_fact_id"})
            return
        seen_fact_ids.add(fact_id)
        cols, vals = cols_vals
        # 정정본 재수집·파서 재실행으로 canonical 값이 바뀌면 DB 도 갱신한다(다른 canonical→DB
        # 로더와 같은 규약, ALPHA-406·411 계열). WHERE … IS DISTINCT FROM … 가 같은 값 재적재를
        # 걸러 no-op 로 만든다. 다른 rcept_no 정정본 supersession 은 범위 밖(정체성 해소 문제).
        header = ("INSERT INTO disclosure_fact (fact_id, document_id, fact_type, available_at)"
                  " VALUES (%s, %s, %s, %s) ON CONFLICT (fact_id) DO UPDATE SET"
                  " available_at = EXCLUDED.available_at"
                  " WHERE disclosure_fact.available_at IS DISTINCT FROM EXCLUDED.available_at")
        table = "supply_contract_fact" if fact_type == "SUPPLY_CONTRACT" else "business_segment_fact"
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        cur_tuple = ", ".join(f"{table}.{c}" for c in cols)
        exc_tuple = ", ".join(f"EXCLUDED.{c}" for c in cols)
        child = (f"INSERT INTO {table} (fact_id, {', '.join(cols)})"
                 f" VALUES ({', '.join(['%s'] * (1 + len(vals)))})"
                 f" ON CONFLICT (fact_id) DO UPDATE SET {set_clause}"
                 f" WHERE ({cur_tuple}) IS DISTINCT FROM ({exc_tuple})")
        pairs.append((header, (fact_id, document_id, fact_type, available_at),
                      child, (fact_id, *vals)))

    if supply is not None:
        _emit("SUPPLY_CONTRACT", None, _supply_child(supply))
    for seg in segments:
        _emit("BUSINESS_SEGMENT", seg.get("segment_ordinal"), _segment_child(seg))
    return pairs, rejects


def _supply_child(row):
    """canonical supply → (거절사유|None, (cols, vals)). counterparty·금액·비율·계약기간을 DB
    CHECK 대로 선검증한다."""
    withheld = bool(row.get("counterparty_withheld"))
    raw_name = (row.get("counterparty_raw") or row.get("counterparty") or "").strip() or None
    if not withheld and not raw_name:
        return "counterparty_missing", None  # CHECK: 비공개가 아니면 원문명 필수
    amount, ratio = row.get("amount_krw"), row.get("ratio_pct")
    if not _finite(ratio):
        return "non_finite_ratio", None  # NaN/inf → DB 유한성 CHECK 롤백 회피
    if (amount is not None and amount < 0) or (ratio is not None and ratio < 0):
        return "negative_value", None  # CHECK: 금액·비율 음수 불가
    start, end = row.get("contract_start"), row.get("contract_end")
    if not _valid_iso_date(start) or not _valid_iso_date(end):
        return "bad_date_format", None  # 비-ISO 는 사전순 비교·DB DATE 캐스팅을 깬다
    if start and end and end < start:
        return "reversed_period", None  # CHECK: 종료일 >= 시작일 (둘 다 유효 ISO 라 안전)
    cols = ("counterparty_actor_id", "counterparty_raw_name", "counterparty_withheld",
            "contract_object_concept_id", "contract_amount_krw", "revenue_ratio_pct",
            "contract_start_date", "contract_end_date")
    vals = (row.get("_counterparty_actor_id"), raw_name, withheld,
            row.get("_contract_object_concept_id"), amount, ratio, start, end)
    return None, (cols, vals)


def _segment_child(row):
    """canonical segment → (거절사유|None, (cols, vals)). segment_name(NOT NULL)·매출·비중 선검증.
    share_basis 는 대문자화 후 범위 밖이면 NULL(값은 유지)."""
    segment_name = (row.get("segment_name") or "").strip() or None
    if segment_name is None:
        return "missing_segment_name", None  # NOT NULL
    revenue, share = row.get("revenue_krw"), row.get("revenue_share_pct")
    if not _finite(share):
        return "non_finite_share", None  # NaN/inf → DB 유한성 CHECK 롤백 회피
    if (revenue is not None and revenue < 0) or (share is not None and share < 0):
        return "negative_value", None  # CHECK: 음수 불가
    basis = (row.get("share_basis") or "").upper() or None
    if basis not in _SHARE_BASIS:
        basis = None
    cols = ("segment_name", "period_label", "revenue_krw", "revenue_share_pct", "share_basis")
    vals = (segment_name, row.get("period"), revenue, share, basis)
    return None, (cols, vals)
