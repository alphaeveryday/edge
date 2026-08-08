"""문서 마스터 적재 — canonical 뉴스 → document (ALPHA-374).

`document_assertion.document_id` FK 의 선행이다 — assertion 적재(ALPHA-376)는 여기가
풀리지 않으면 INSERT 자체가 불가능하다. canonical `news/news_articles/language=…/
published_date=…` 를 읽어 기사마다 `document` 행(document_type='NEWS')을 만든다.

**멱등**: 자연키 = `uq_document_source (source_code, source_document_id)` =
canonical 의 (source_vendor, article_id). `ON CONFLICT DO NOTHING` 으로 제약 자체가
멱등의 근거다 — 이미 있으면(rowcount 0) 기존 행을 건드리지 않아, 재실행이 ID 를 바꿔
이 문서를 참조할 assertion FK 를 끊는 일이 없다(ADR-0027). 사전 스냅샷 조회 방식
(load-instruments)과 달리 동시 실행에도 원자적이다.

**창(from/to) 미지정 = published_date 전체 스캔** — 멱등 skip 이라 재실행 비용은
신규분뿐이고, 놓친 날짜도 다음 런이 자연 회복한다(load-price-triggers 와 같은 모델).

`available_at`(NOT NULL)은 canonical `fetched_at`(수집 시각)이고, 결측이면
`published_at` 으로 대신한다 — "우리가 이 문서를 쓸 수 있게 된 시각"의 가장 보수적인
근사가 수집 시각이다. 둘 다 없으면 그 행은 적재하지 않고 결손으로 센다.

`news_document.lead_text`(BigKinds 스니펫)와 `publisher`(언론사, ALPHA-695)도 여기서
채운다 — canonical 이 이미 갖고 있고(`normalize_news` 가 `CONTENT`→`lead_text`, 벤더별
PROVIDER/site→`publisher`), 분석엔진 프롬프트가 제목만으로는 사건의 내용을 못 보고,
콘솔 문서 목록의 출처 축은 언론사가 없으면 수집 벤더 하나로 접힌다. `assemble_events` 가
같은 행을 `document_id` 만으로 먼저 넣을 수 있어 **UPSERT** 로 채운다(값이 실제로
달라질 때만 UPDATE — 멱등 집계가 거짓이 되지 않게).
공시(document_type='DISCLOSURE')·`document_entity` 는 범위 밖(별건).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..lake import Storage, canonical_news_articles_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_documents"
DATASET = "document"

# canonical 뉴스의 언어 파티션 축(ALPHA-352, 벤더 고정: bigkinds=ko·fmp=en).
# 문서 마스터는 언어 무관 레지스트리라 둘 다 싣는다 — tag-news 가 ko 만 태깅하는 것과
# 별개로, en 문서도 assertion 이 생기는 순간 FK 대상이 돼야 한다.
LANGUAGES = ("ko", "en")

# quality log 의 created_rows 는 표본만 남긴다 — 백필 런은 수천 행이라 로그가 데이터가
# 된다. 몇 건 만들었는지(created)는 전수, 어떤 행인지는 표본이 답한다. 자르는 걸 숨기지
# 않도록 키 이름에 sample 을 박는다.
_CREATED_SAMPLE_LIMIT = 50


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, language: str) -> list[str]:
    """이 언어의 canonical published_date 목록(오름차순). 경로는 빌더로만 만든다(레이크 규약)."""
    marker = canonical_news_articles_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 뉴스 → document 적재. 성공 0, 장애 시 비0."""
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_no_available_at = 0
    already = created = lead_written = publisher_written = lead_unclaimed = 0
    lead_attempted = 0
    created_sample: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        # (source_code, source_document_id) → 적재 후보. 같은 기사가 여러 파티션에 오면
        # (드묾 — 같은 URL 재게시) 첫 행으로 접는다: 자연키가 하나면 문서도 하나다.
        candidates: dict[tuple[str, str], dict] = {}
        for language in LANGUAGES:
            dates = [d for d in _partition_dates(storage, language)
                     if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]
            for date in dates:
                prefix = canonical_news_articles_partition(language, date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        read += 1
                        source_code = row.get("source_vendor")
                        article_id = row.get("article_id")
                        if not source_code or not article_id:
                            # 자연키 결손 — 넣으면 NOT NULL 위반이거나(즉시 실패) 멱등의
                            # 근거가 사라진다(같은 기사가 매 런 새 행). 세고 뺀다.
                            skipped_missing_identity += 1
                            continue
                        available_at = row.get("fetched_at") or row.get("published_at")
                        if not available_at:
                            # available_at 은 NOT NULL — 시간 축이 없는 문서는 적재 불가.
                            skipped_no_available_at += 1
                            continue
                        candidates.setdefault((source_code, article_id), {
                            "language_code": language,
                            "title": row.get("title"),
                            "published_at": row.get("published_at"),
                            "available_at": available_at,
                            # ⚠️ `available_at` 과 **따로** 싣는다(ALPHA-696). 리드 승자
                            # 판정은 순수한 수집 시각으로만 해야 하는데, 위 `available_at`
                            # 은 `published_at` 폴백이 섞여 있어 미래 시각이 들어올 수
                            # 있다. 그 값을 축으로 쓰면 이 행의 리드 승격이 영구 차단된다.
                            "fetched_at": row.get("fetched_at"),
                            "source_uri": row.get("url"),
                            "lead_text": row.get("lead_text"),
                            "publisher": row.get("publisher"),
                        })

        with connect(db) as conn:
            for (source_code, article_id), doc in sorted(candidates.items()):
                # 멱등의 근거는 사전 스냅샷이 아니라 자연키 제약 자체다 — DO NOTHING 이라
                # 동시 실행이 같은 키를 넣어도 늦은 쪽이 already 로 세어질 뿐 배치가 죽지
                # 않는다. rowcount 0 = 이미 있었다(분석엔진이 먼저 쓴 행 포함).
                # 자연키에서 결정적으로 뽑는다(ALPHA-456) — assemble-events 도 같은 재료로
                # 같은 값을 계산해야 한다(`_stable_id("doc", source_code, article_id)`).
                # 랜덤이면 이 ID 를 재료로 쓰는 assertion_id·source_event_id 가 전부 랜덤을
                # 상속해, 계보 전체의 결정성이 이 한 줄에서 무너진다.
                document_id = stable_domain_id("doc", source_code, article_id)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO document (document_id, document_type, source_code,"
                        " source_document_id, title, language_code, published_at,"
                        " available_at, source_uri)"
                        " VALUES (%s, 'NEWS', %s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (source_code, source_document_id) DO NOTHING",
                        (
                            document_id,
                            source_code,
                            article_id,
                            doc["title"],
                            doc["language_code"],
                            doc["published_at"],
                            doc["available_at"],
                            doc["source_uri"],
                        ),
                    )
                    # 스니펫은 document 가 **이미 있어도** 채운다 — 분석엔진이 제목만 보던
                    # 원인이 여기였다. 값이 실제로 바뀔 때만 UPDATE(멱등 집계 보존).
                    #
                    # ⚠️ id 는 위에서 계산한 `document_id` 가 아니라 **자연키로 되읽은 실제
                    # 행 값**이어야 한다(ALPHA-628). 위 INSERT 가 DO NOTHING 이라 자연키가
                    # 이미 있으면 기존 행의 id 가 남는데, ALPHA-456 이전에 적재된 행은 랜덤
                    # ULID id 를 갖고 있어 계산값과 갈린다 — 계산값으로 넣으면 없는 문서를
                    # 참조해 FK 가 터지고, 커밋 경계가 런 전체라 전량 롤백된다. 서브쿼리로
                    # 넣어 왕복 없이 해결한다(assemble_events·load_assertions 의 자연키
                    # 브리지와 같은 규칙, ALPHA-409).
                    #
                    # ⚠️ **승자 규칙(ALPHA-696).** 이 리드에는 생산자가 둘이다 — 여기와
                    # 1분 `PgNewsCanonicalWriter`. 예전엔 여기가 시각 조건 없이 덮어서,
                    # 1분 경로가 반영한 정정(T2)을 아직 T1 만 있는 레이크 값으로 되돌렸다
                    # (원장은 새 지문을 확정했는데 Consumer 는 옛 본문을 읽는 P1).
                    # 이제 `lead_observed_at` 이 **미주장(IS NULL)이거나 자기 관측이 더
                    # 새로울 때만** 이긴다. 축은
                    # `news_document.lead_observed_at` 이고 계약 전문은 마이그레이션
                    # `V202608071018__add_news_document_lead_observed_at.sql` 에 있다.
                    #
                    # `fetched_at` 이 결손이면 이 배치는 신선도를 **주장하지 않는다** —
                    # 그때 `EXCLUDED.lead_observed_at` 이 NULL 이라 비교절이 UNKNOWN 이
                    # 되어 가드가 `IS NULL`(아무도 안 쓴 자리) 하나로 줄고, SET 이 NULL 을
                    # 도로 넣어 컬럼도 미주장으로 남는다. SQL NULL 의미론이 그대로 계약이다.
                    #
                    # ⚠️ 여기에 `OR news_document.lead_text IS NULL`("빈 자리는 언제나
                    # 채운다") 예외를 넣지 마라. 시각이 찍힌 채 리드가 NULL 인 상태는
                    # **1분 경로가 있던 리드를 의도적으로 지웠을 때**만 생기고, 그게 바로
                    # 덮으면 안 되는 상태다 — 넣으면 옛 리드가 복원되고 시각이 뒤로 밀려
                    # 이후 모든 배치 런이 비교를 통과하는 자기강화 고착이 된다.
                    if doc["lead_text"]:
                        lead_attempted += 1
                        # ⚠️ `or None` 이 없으면 `""` 가 그대로 바인딩돼 PG 가
                        # `invalid input syntax for timestamptz: ""` 로 터지고, 커밋
                        # 경계가 런 전체라 **그날 적재가 통째로 롤백**된다(ALPHA-848).
                        # 아래 가드는 falsy 를 '값 없음'으로 읽는데 바인딩만 '값'으로
                        # 읽던 불일치다. canonical 은 `_text()` 가 str 여부만 보증하고
                        # `_fetched_at()` 은 정렬 키라 빈 값을 걸러 주지 않는다.
                        fetched_at = doc["fetched_at"] or None
                        if not fetched_at:
                            # 신선도를 주장하지 못한 채 시도한 건수. rowcount 로 세면
                            # "값이 같아 안 바뀐 것"과 섞여 뜻이 흐려진다 — 노출 자체를
                            # 센다. 이게 크면 그 벤더에서 축이 조용히 무력화되고 있다는
                            # 뜻인데, 안 세면 볼 계기가 없다(Rule 12).
                            lead_unclaimed += 1
                        with conn.cursor() as lead_cur:
                            lead_cur.execute(
                                "INSERT INTO news_document"
                                " (document_id, lead_text, lead_observed_at)"
                                " SELECT document_id, %s, %s FROM document"
                                " WHERE source_code = %s AND source_document_id = %s"
                                " ON CONFLICT (document_id) DO UPDATE"
                                " SET lead_text = EXCLUDED.lead_text,"
                                "     lead_observed_at = EXCLUDED.lead_observed_at"
                                " WHERE news_document.lead_text"
                                "       IS DISTINCT FROM EXCLUDED.lead_text"
                                "   AND (news_document.lead_observed_at IS NULL"
                                "        OR news_document.lead_observed_at"
                                "           <= EXCLUDED.lead_observed_at)",
                                (doc["lead_text"], fetched_at, source_code, article_id),
                            )
                            if lead_cur.rowcount:
                                lead_written += 1
                    # 언론사도 같은 규칙으로 채운다(ALPHA-695) — 정규화가 살려 온 값이 여기서
                    # 버려지던 것을 승격. lead_text 와 가드를 분리한 이유: 게이트가 둘을 따로
                    # non-blocking 경고로 두므로 한쪽만 있는 문서가 정상적으로 존재한다.
                    if doc["publisher"]:
                        with conn.cursor() as pub_cur:
                            pub_cur.execute(
                                "INSERT INTO news_document (document_id, publisher)"
                                " SELECT document_id, %s FROM document"
                                " WHERE source_code = %s AND source_document_id = %s"
                                " ON CONFLICT (document_id) DO UPDATE"
                                " SET publisher = EXCLUDED.publisher"
                                " WHERE news_document.publisher IS DISTINCT FROM EXCLUDED.publisher",
                                (doc["publisher"], source_code, article_id),
                            )
                            if pub_cur.rowcount:
                                publisher_written += 1
                    if cur.rowcount == 0:
                        already += 1
                        continue
                created += 1
                if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                    created_sample.append({"document_id": document_id,
                                           "source_code": source_code,
                                           "source_document_id": article_id})
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("문서 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        # 롤백됐으니 쓰기 카운터는 전부 0 이다 — lead_written 을 남기면 로그가 실제보다
        # 많이 했다고 말한다(계측은 관대한 방향으로 틀리면 안 된다, Rule 12).
        created, created_sample, lead_written, publisher_written = 0, [], 0, 0
        # ⚠️ `lead_unclaimed` 는 **0 으로 되돌리지 않는다.** 위 셋은 "우리가 쓴 건수"라
        # 롤백이면 0 이 사실이지만, 이건 "신선도를 주장하지 못한 채 시도한 **노출**"이라
        # 롤백과 무관하게 실제로 일어난 일이다. 지우면 canonical `fetched_at` 이 이상해서
        # 죽은 런이 하필 `lead_unclaimed_freshness: 0` 을 보고한다 — 진단이 가장 필요한
        # 런에서 근거가 사라진다(Rule 12).
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "languages": list(LANGUAGES), "from_date": from_date, "to_date": to_date,
        "articles_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_no_available_at": skipped_no_available_at,
        "already_present": already, "created": created,
        # 리드가 있어 UPSERT 를 **시도한** 건수. 아래 두 카운터의 분모다(ALPHA-848) —
        # 없으면 `lead_unclaimed_freshness: 137` 이 137/140(그 벤더에서 축이 죽었다)인지
        # 137/60,000(잡음)인지 못 가른다.
        "lead_attempted": lead_attempted,
        # **이번 런이 값을 바꾼** 건수다 — UPSERT 의 WHERE 가 막으면 안 센다.
        # 그래서 0 은 "canonical 에 스니펫이 없다"가 아니라 "안 바뀌었다"이고, 멱등 재실행·
        # 롤백 런에서도 0 이다. ⚠️ 막는 절이 **둘**이라 안 바뀐 이유도 둘이다(ALPHA-848):
        # ①값이 이미 같다 ②**승자 축에 졌다**(1분 경로가 더 새 리드를 이미 반영했거나 이
        # canonical 이 더 낡았다). `lead_attempted - lead_text_written` 이 그 합이고, 둘을
        # 가르려면 행마다 질의가 하나 더 들어 여기서는 안 가른다. 소스 결손을 보려면
        # canonical 쪽을 봐야 하는 것은 그대로다.
        "lead_text_written": lead_written,
        "publisher_written": publisher_written,
        # canonical `fetched_at` 이 없어 리드 승자 판정에서 신선도를 주장하지
        # 못한 건수(ALPHA-696). 이게 크면 그 벤더에서 배치가 늘 이기는 옛 동작으로
        # 돌아간 것이다 — 축이 무력화된 것을 여기 말고는 볼 자리가 없다.
        # ⚠️ 위 두 키와 달리 **롤백 런에서도 0 이 아니다** — 쓴 건수가 아니라 읽은
        # 노출 수라서다(위 롤백 블록 주석). `exit_code` 가 비0이면 이 값은 터진
        # 지점까지의 **절단값**이고, 그 런에서 배치가 이겼다는 뜻이 아니다.
        "lead_unclaimed_freshness": lead_unclaimed,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). 정체성·시각 결측은 그 기사가 문서 마스터에 안
        # 들어간 유실이다(설명이 인용할 근거가 그만큼 없다).
        "ops": {
            "records_out": already + created,
            "failed_records": len(failures) + skipped_missing_identity + skipped_no_available_at,
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_documents: read=%d skipped_identity=%d skipped_available_at=%d already=%d"
        " created=%d failures=%d",
        read, skipped_missing_identity, skipped_no_available_at, already, created, len(failures),
    )
    return exit_code
