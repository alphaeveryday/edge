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

**fact 게이트**: canonical 정제는 값 이상을 경고로만 통과시키므로(blocking 아님), DB CHECK 를
파이썬에서 **선검증**해 위반 fact 만 뺀다 — 한 건이 배치 전체를 롤백시키지 않게. NaN/Infinity
는 `<0` 비교를 조용히 통과하지만 DB 유한성 CHECK 에 걸리므로 여기서 명시적으로 거른다. 거절된
fact 는 사유와 함께 계측한다(조용한 유실 금지, Rule 12).

**창(from/to) 미지정 = report_date 전체 스캔** — 멱등 skip 이라 재실행 비용은 신규분뿐이다.
"""

from __future__ import annotations

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


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


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
    exit_code = 0

    try:
        supply_rows, s1 = _clean_rcept(_read_facts(
            storage, canonical_supply_contract_fact_partition, from_date, to_date))
        segment_rows, s2 = _clean_rcept(_read_facts(
            storage, canonical_business_segment_fact_partition, from_date, to_date))
        supply_read, segment_read = len(supply_rows), len(segment_rows)
        skipped_missing_identity = s1 + s2

        documents = {**_document(supply_rows, "SUPPLY_CONTRACT"),
                     **_document(segment_rows, "BUSINESS_SEGMENT")}
        segment_by_rcept: dict[str, list[dict]] = {}
        for r in segment_rows:
            segment_by_rcept.setdefault(r["rcept_no"], []).append(r)

        seen_fact_ids: set[str] = set()

        with connect(db) as conn:
            issuers = _resolve_issuers(conn, {d["corp_code"] for d in documents.values()})
            supply_rows = _prepare_supply_rows(conn, supply_rows)
            supply_by_rcept = {r["rcept_no"]: r for r in supply_rows}

            for rcept_no, doc in sorted(documents.items()):
                issuer_actor_id = issuers.get(doc["corp_code"])
                if issuer_actor_id is None:
                    # 마스터 미시드 — FK RESTRICT 라 넣으면 런 전체 롤백. 세고 뺀다(9→309 별건).
                    skipped_unresolved_issuer += 1
                    continue
                if not doc["report_date"]:
                    # report_date 는 공시의 시간축이자 파티션 정체성 — 없으면 날짜 조회에서 샌다.
                    skipped_no_report_date += 1
                    continue

                document_id = stable_domain_id("doc", SOURCE_CODE, rcept_no)
                pairs, rejects = _fact_inserts(
                    rcept_no, document_id, doc, supply_by_rcept.get(rcept_no),
                    segment_by_rcept.get(rcept_no, []), seen_fact_ids)
                for rej in rejects:
                    rejected_facts += 1
                    if len(rejected_sample) < _SAMPLE_LIMIT:
                        rejected_sample.append(rej)
                if not pairs:
                    # 유효 fact 가 하나도 없으면 문서만 적재할 이유가 없다(설명은 fact 를 읽는다).
                    skipped_no_valid_fact += 1
                    continue

                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO document (document_id, document_type, source_code,"
                        " source_document_id, published_at, available_at, source_uri)"
                        " VALUES (%s, 'DISCLOSURE', %s, %s, %s, %s, %s)"
                        " ON CONFLICT (source_code, source_document_id) DO NOTHING",
                        (document_id, SOURCE_CODE, rcept_no, doc["report_date"],
                         doc["available_at"], doc["source_uri"]),
                    )
                    created = cur.rowcount == 1
                    # 파서 메타(parser_version·report_date·parsed_result_uri)는 재파싱 시 바뀔 수
                    # 있어 최신으로 갱신한다(같은 값 재적재는 WHERE 가 걸러 no-op). issuer 는
                    # 정체성이라 갱신하지 않는다.
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
                    for header_sql, header_params, child_sql, child_params in pairs:
                        cur.execute(header_sql, header_params)
                        cur.execute(child_sql, child_params)
                        if cur.rowcount == 1:
                            facts_written += 1
                        else:
                            # 변경 없음 = 이미 같은 값으로 적재돼 있다(멱등 재실행). 실패가
                            # 아니라 정상 산출이라 따로 센다 — 안 세면 재실행이 0건으로 보인다.
                            facts_already += 1

                if created:
                    docs_created += 1
                    if len(created_sample) < _SAMPLE_LIMIT:
                        created_sample.append({"document_id": document_id, "rcept_no": rcept_no})
                else:
                    docs_already += 1
    except Exception as exc:
        # 커밋 경계는 런 전체(connect) — 예외면 롤백이라 부분 적재가 없다. 트레이스백 대신
        # 사유를 로그에 태운다(Rule 12).
        logger.exception("공시 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        docs_created = facts_written = facts_already = 0
        created_sample = []
        exit_code = 1

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
