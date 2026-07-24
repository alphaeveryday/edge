"""assertion 적재 — feature 뉴스 assertion → document_assertion·assertion_argument (ALPHA-376).

"canonical → feature → DB" 사슬의 마지막 칸이다. tag-news(ALPHA-365)가 만든 feature
`news/assertions` 를 읽어 주장을 Cloud Event Store 에 세운다 — 분석이 feature 산출물만
읽는 최종 구조(ADR-0028)의 전제.

**엔티티 해소(ALPHA-375)가 이 스텝의 본체다**: `assertion_argument.entity_id` 는
NOT NULL + FK 라 해소 없이는 한 건도 못 넣는다. entity_resolution 의 완전일치 축으로
argument `text` 를 instrument 로 해소하고, 미해소·충돌은 **수치로 남기고 뺀다**(Rule 12).
argument 가 전무 해소된 assertion 은 assertion 도 넣지 않는다 — 엔티티 연결 없는 주장은
event 조립 소비자에게 죽은 행이다.

**멱등**: 논리 자연키 = `uq_document_assertion_natural (document_id, event_type_code,
predicate_code)` 에 ON CONFLICT DO NOTHING(원자적 — #130 교훈). 신규면 그 자연키에서
**결정적으로** 파생한 `asrt_<해시>`(`db.stable_domain_id`, ALPHA-456), 이미 있으면(분석엔진
선적재 포함) 그 행의 assertion_id 를 자연키로 읽어 arguments 만 union 한다
(`uq_assertion_argument` DO NOTHING). 같은 런 내 같은 키 주장은 arguments 를 접는다.

⚠️ **컬럼 소유권(ALPHA-538)** — 같은 자연키 행을 두 스텝(이 스텝·assemble-events)이 만들 수
있지만, 컬럼별 공급자는 하나다: `confidence` 는 **이 스텝 소유**(assertion-grain 판정,
행이 이미 있으면 UPDATE 로 확정 착지), `available_at` 은 **document 파생**(양쪽이 같은 값),
`lifecycle_stage` 는 event grain(`source_event`) 소유라 여기선 싣지 않는다. 그래서 두 스텝이
어느 순서로 돌아도 최종 행이 같다 — "먼저 쓴 쪽이 남는" 순서 의존은 ALPHA-538 로 제거됐다.

ADR-0027 대비: 도메인 ID 는 `<접두사>_<ULID>` 가 기본이지만 이 계열은 **hex 해시**라 시간
정렬이 안 된다. 불투명성(ID 를 파싱해 의미를 얻지 못함)은 유지되고, ADR 이 자연키 파생을
배격한 이유는 *가변 외부 식별자*(티커) 인코딩이었는데 여기 자연키는 전부 내부·불변값이라
그 위험이 없다. 시간 정렬이 필요한 소비자는 `available_at` 을 쓴다.

**document 연결**: feature 행에 source_vendor 가 없어 언어→벤더 고정(ko=bigkinds,
분석엔진과 같은 가정)으로 자연키 SELECT 해소. document 행이 없으면 결손으로 세고
건너뛴다 — load-documents 가 선행 스텝이라 다음 런이 자연 회복한다.

`modality_code` 는 비운다 — 어휘 미정의(ALPHA-361). 값을 발명하면 그게 계약이 된다.
`available_at` 은 **document 의 가용 시각**이다 — 주장의 PIT 기준은 원문 발행·수집이지
추출 프로세스 시각(tagged_at)이 아니다(ALPHA-538 로 tagged_at 사용 폐지).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..entity_resolution import load_resolution_index, resolve
from ..lake import Storage, feature_news_assertions_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_assertions"
DATASET = "document_assertion"

# tag-news 의 태깅 대상과 같은 축(TAGGED_LANGUAGES=ko) — 언어는 벤더 고정이라 source_code 도
# 여기서 정해진다. 영어 태깅이 열리면 ("en", "fmp") 를 추가한다.
_SOURCE_CODE_BY_LANGUAGE = {"ko": "bigkinds"}

_CREATED_SAMPLE_LIMIT = 50


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, language: str) -> list[str]:
    """이 언어의 feature published_date 목록(오름차순). 경로는 빌더로만 만든다(레이크 규약)."""
    marker = feature_news_assertions_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def _confidence(value: object) -> float | None:
    """CHECK(0~1) 를 어길 값은 NULL 로 — 게이트 통과값으로 강제하지 않는다(coerce 금지)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= float(value) <= 1.0 else None


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """feature 뉴스 assertion → document_assertion·assertion_argument 적재. 성공 0, 장애 시 비0."""
    started_at = datetime.now(timezone.utc)
    rows_read = rows_not_ok = rows_no_assertion = rows_malformed = 0
    folded = missing_document = skipped_incomplete = skipped_partial = 0
    skipped_no_resolved_argument = 0
    created = already = arguments_inserted = 0
    args_total = 0
    args_by_reason: dict[str, int] = {"resolved": 0, "unresolved": 0, "ambiguous": 0}
    unresolved_texts: dict[str, int] = {}
    created_sample: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        # (source_code, article_id, event_type, predicate) → {assertion 스칼라 + arguments set}
        candidates: dict[tuple[str, str, str, str], dict] = {}
        for language, source_code in _SOURCE_CODE_BY_LANGUAGE.items():
            dates = [d for d in _partition_dates(storage, language)
                     if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]
            for date in dates:
                prefix = feature_news_assertions_partition(language, date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        rows_read += 1
                        if row.get("status") != "ok":
                            rows_not_ok += 1
                            continue
                        article_id = row.get("article_id")
                        try:
                            assertions = json.loads(row.get("assertions") or "[]")
                            if not isinstance(assertions, list):
                                raise ValueError("assertions 가 리스트가 아님")
                        except (ValueError, TypeError):
                            # 행 단위 격리 — 한 이상치가 잡을 무너뜨리지 않는다.
                            rows_malformed += 1
                            continue
                        if not article_id or not assertions:
                            rows_no_assertion += 1
                            continue
                        for assertion in assertions:
                            if not isinstance(assertion, dict):
                                rows_malformed += 1
                                continue
                            event_type = assertion.get("event_type_code")
                            predicate = assertion.get("predicate_code")
                            arguments = assertion.get("arguments")
                            if not event_type or not predicate or not isinstance(arguments, list):
                                # 자연키 결손 주장 — 넣으면 NOT NULL 위반이거나 멱등 축이 사라진다.
                                skipped_incomplete += 1
                                continue
                            if assertion.get("completeness") != "complete":
                                # 추출기가 필수 역할 결손을 표시한 주장(partial) — 스키마에
                                # 완결성 컬럼이 없어 실으면 확정 주장과 구분 불가가 된다.
                                # feature 존에 원본이 남으니 어휘/컬럼 합의 후 재적재한다.
                                skipped_partial += 1
                                continue
                            nk = (source_code, article_id, str(event_type), str(predicate))
                            entry = candidates.get(nk)
                            if entry is None:
                                entry = candidates[nk] = {
                                    "confidence": _confidence(assertion.get("confidence")),
                                    "arguments": [],
                                }
                            else:
                                # 같은 문서·사건유형·서술의 재주장 — 자연키가 하나면 주장도
                                # 하나다. 스칼라는 첫 주장이 대표, arguments 는 union.
                                folded += 1
                            entry["arguments"].extend(a for a in arguments if isinstance(a, dict))

        with connect(db) as conn:
            index = load_resolution_index(conn)

            article_ids_by_source: dict[str, set[str]] = {}
            for source_code, article_id, _e, _p in candidates:
                article_ids_by_source.setdefault(source_code, set()).add(article_id)
            doc_by_key: dict[tuple[str, str], tuple[str, object]] = {}
            with conn.cursor() as cur:
                for source_code, ids in article_ids_by_source.items():
                    cur.execute(
                        "SELECT source_document_id, document_id, available_at FROM document"
                        " WHERE source_code = %s AND source_document_id = ANY(%s)",
                        (source_code, sorted(ids)),
                    )
                    for sdi, did, avail in cur.fetchall():
                        doc_by_key[(source_code, sdi)] = (did, avail)

            for (source_code, article_id, event_type, predicate), entry in sorted(candidates.items()):
                doc_row = doc_by_key.get((source_code, article_id))
                if doc_row is None:
                    missing_document += 1
                    continue
                document_id, doc_available_at = doc_row

                resolved_args: dict[tuple[str, str], float | None] = {}
                for argument in entry["arguments"]:
                    args_total += 1
                    role_code = argument.get("role_code") or "ISSUER"
                    entity_id, reason = resolve(index, argument.get("text"))
                    args_by_reason[reason] = args_by_reason.get(reason, 0) + 1
                    if entity_id is None:
                        text = argument.get("text")
                        if isinstance(text, str) and text.strip():
                            # 미해소 상위 표현이 별칭 축 도입 판단의 근거다(티켓 완료 조건).
                            unresolved_texts[text.strip()] = unresolved_texts.get(text.strip(), 0) + 1
                        continue
                    resolved_args.setdefault((str(role_code), entity_id), entry["confidence"])

                if not resolved_args:
                    skipped_no_resolved_argument += 1
                    continue

                with conn.cursor() as cur:
                    # 자연키에서 결정적으로 뽑는다(ALPHA-456) — 랜덤 ULID 였을 때는 이 테이블의
                    # 다른 writer(assemble-events)와 값이 갈렸고, 먼저 도는 이 스텝의 랜덤값이
                    # 남아 그걸 재료로 쓰는 source_event_id 까지 랜덤을 상속했다. 산식은
                    # assemble-events 와 **같은 함수**여야 한다(각자 구현하면 salt·구분자
                    # 하나에 다시 갈린다).
                    assertion_id = stable_domain_id(
                        "asrt", document_id, event_type, predicate)
                    cur.execute(
                        "INSERT INTO document_assertion (assertion_id, document_id,"
                        " event_type_code, predicate_code, confidence, available_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (document_id, event_type_code, predicate_code) DO NOTHING",
                        (assertion_id, document_id, event_type, predicate,
                         entry["confidence"], doc_available_at),
                    )
                    if cur.rowcount == 0:
                        # 이미 있다(assemble-events 의 FK 비계 선생성 포함) — 소유 컬럼
                        # confidence 를 UPDATE 로 확정 착지시키고 그 행의 ID 로 arguments 를
                        # union 한다. 행 생성 경주에서 져도 이 스텝의 판정이 유실되지 않는다.
                        already += 1
                        cur.execute(
                            "UPDATE document_assertion SET confidence = %s"
                            " WHERE document_id = %s AND event_type_code = %s"
                            " AND predicate_code = %s RETURNING assertion_id",
                            (entry["confidence"], document_id, event_type, predicate),
                        )
                        assertion_id = cur.fetchone()[0]
                    else:
                        created += 1
                        if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                            created_sample.append({"assertion_id": assertion_id,
                                                   "document_id": document_id,
                                                   "event_type_code": event_type,
                                                   "predicate_code": predicate})
                    for (role_code, entity_id), confidence in sorted(resolved_args.items()):
                        cur.execute(
                            "INSERT INTO assertion_argument (assertion_id, role_code,"
                            " entity_id, confidence) VALUES (%s, %s, %s, %s)"
                            " ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING",
                            (assertion_id, role_code, entity_id, confidence),
                        )
                        arguments_inserted += cur.rowcount
    except Exception as exc:
        # 커밋 경계는 런 전체 — connect() 가 예외면 롤백이라 부분 적재가 없다(Rule 12).
        logger.exception("assertion 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created = already = arguments_inserted = 0
        created_sample = []
        exit_code = 1

    resolution_denominator = args_total
    resolution_rate = (args_by_reason["resolved"] / resolution_denominator
                       if resolution_denominator else None)
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "languages": list(_SOURCE_CODE_BY_LANGUAGE), "from_date": from_date, "to_date": to_date,
        "rows_read": rows_read, "rows_not_ok": rows_not_ok,
        "rows_no_assertion": rows_no_assertion, "rows_malformed": rows_malformed,
        "assertions_considered": len(candidates), "assertions_folded": folded,
        "missing_document": missing_document, "skipped_incomplete": skipped_incomplete,
        "skipped_partial": skipped_partial,
        "skipped_no_resolved_argument": skipped_no_resolved_argument,
        "created": created, "already_present": already,
        "arguments_inserted": arguments_inserted,
        # 해소율 실측(ALPHA-375 완료 조건) — 분모·분자·사유 분포 + 미해소 상위 표현.
        "argument_resolution": {
            "total": args_total, **args_by_reason, "rate": resolution_rate,
            "top_unresolved": sorted(unresolved_texts.items(), key=lambda kv: -kv[1])[:20],
        },
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_assertions: rows=%d considered=%d missing_doc=%d no_resolved=%d created=%d"
        " already=%d args_inserted=%d resolution=%s",
        rows_read, len(candidates), missing_document, skipped_no_resolved_argument,
        created, already, arguments_inserted,
        f"{resolution_rate:.3f}" if resolution_rate is not None else "n/a",
    )
    return exit_code
