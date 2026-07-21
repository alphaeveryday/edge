"""이벤트 조립·threading — 분석엔진 추출 체인의 이식 (ALPHA-412, ADR-0028).

분석엔진 daily_pipeline 의 추출 구간(제목 분류 → document/assertion/source_event 계보
조립 → event_thread threading)을 feature 페이즈 스텝으로 옮긴다. **로직은 엔진이
정본이라 무변경 이식**이다(결정 2026-07-17) — 프롬프트·배치 40·mentions 티커 해소·
검증 규칙·결정적 ID 산식 전부 엔진과 동일하고, 바뀐 것은 실행 위치와 실행부 관례
(Storage 레이크 접근·psycopg3·LLM complete_fn 주입·quality log)뿐이다.

⚠️ **PIPELINE_ID 는 엔진과 반드시 동일해야 한다**(db.PIPELINE_ID) — 결정적 ID 의 재료라,
다르면 같은 이벤트가 다른 source_event_id/thread_id 로 갈려 이행기(엔진이 아직 자체
조립을 하는 동안)의 멱등 수렴이 깨진다. 엔진 축소(PR D) 후에도 기존 행과의 수렴을
위해 유지한다.

tag-news 와의 관계: tag-news 는 다중 assertion·역할 추출로 feature 존을 만들고(문서/
assertion 사슬), 이 스텝은 엔진 분류기(기사당 1건, 제목만)로 **event 계보**를 만든다.
두 분류기의 단일화는 로직 소유자 결정 사안(후속) — 그동안 양쪽 다 자연키/결정적 ID
멱등이라 공존이 안전하다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..events.ontology import Registry, load_registry
from ..lake import Storage, canonical_news_articles_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "assemble_events"
DATASET = "source_event"

CLASSIFY_BATCH = 40
TITLE_EVIDENCE_TYPE = "TITLE"
_KST = timezone(timedelta(hours=9))

# canonical 뉴스의 언어 파티션 축(벤더 고정: bigkinds=ko·fmp=en). 분류 대상은 전 언어지만
# 프롬프트가 한국어 제목 전제라 실질 판정은 ko 에서 나온다(엔진도 전 파티션을 읽었다).
LANGUAGES = ("ko", "en")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# 결정적 ID 산식은 db.stable_domain_id 가 소유한다(ALPHA-456) — load-assertions 와 **같은
# 함수**를 써야 같은 자연키에 같은 assertion_id 가 나온다. 지역 별칭만 남긴다(이 파일의
# _stable_id 호출 6곳을 치환하는 것보다 diff 가 작고 호출부 의미도 그대로다).
_stable_id = stable_domain_id


def _iso(value: object) -> str:
    if value is None:
        return _utcnow_iso()
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, language: str) -> list[str]:
    marker = canonical_news_articles_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def _mention_tickers(mentions: object) -> list[str]:
    if not mentions:
        return []
    if isinstance(mentions, str):
        try:
            mentions = json.loads(mentions)
        except json.JSONDecodeError:
            return []
    out: list[str] = []
    for item in mentions or []:
        ticker = item.get("ticker") if isinstance(item, dict) else None
        if ticker:
            out.append(str(ticker))
    return out


def read_daily_news(storage: Storage, published_date: str) -> list[dict]:
    """해당 발행일의 canonical 뉴스(제목 축 필드만) — 엔진 read_daily_news 이식."""
    rows: list[dict] = []
    seen: set[str] = set()
    for language in LANGUAGES:
        prefix = canonical_news_articles_partition(language, published_date)
        for key in storage.list_keys(prefix + "/"):
            if not key.endswith(".parquet"):
                continue
            for rec in _read_parquet_rows(storage.get_bytes(key)):
                title = (rec.get("title") or "").strip()
                article_id = rec.get("article_id")
                if not title or not article_id:
                    continue
                article_id = str(article_id)
                if article_id in seen:
                    continue
                seen.add(article_id)
                rows.append({
                    "article_id": article_id,
                    "title": title,
                    "published_at": rec.get("published_at"),
                    "publisher": rec.get("publisher"),
                    "source_vendor": rec.get("source_vendor") or "bigkinds",
                    "language": language,  # 파티션 축 — document.language_code 로 실린다
                    "tickers": _mention_tickers(rec.get("mentions")),
                })
    return rows


# ── 분류 (엔진 verbatim — 프롬프트·검증 규칙 무변경) ─────────────────────────
def _classify_system(registry: Registry) -> str:
    types = "\n".join(
        f"- {tid} | pred:{','.join(spec.predicates)} | req:{','.join(spec.required_roles)}"
        for tid, spec in sorted(registry.types.items())
    )
    return (
        "너는 한국어 금융 뉴스 제목만 보고 시장 이벤트를 판정하는 분류기다. 제목 외 정보는 없다.\n"
        "각 항목에 대해 아래 JSON 스키마의 오브젝트를 만든다.\n"
        '{"items":[{"id": <입력 id 그대로>, "is_event": true/false, '
        '"event_type_code": <아래 목록 중 하나 또는 "">, "predicate_code": <해당 타입의 pred 중 하나 또는 "">, '
        '"primary_ticker": <입력 tickers 중 하나 또는 "">, "lifecycle_stage": <"" 또는 짧은 단계표지>, '
        '"confidence": 0~1}]}\n'
        "규칙: 확정된 사실 행동/결과(실적·수주·계약·인수·출시·공시·증설·판결·인사·가격변동)만 is_event=true. "
        "논평·전망·홍보·단순안내는 is_event=false. event_type_code/predicate_code는 반드시 아래 목록에서만 고른다. "
        "primary_ticker는 입력 tickers 목록에서만 고른다(없으면 \"\"). 목록에 없는 값은 만들지 마라.\n"
        f"[이벤트 타입 목록]\n{types}"
    )


def _complete_json(complete_fn, system: str, user: str) -> dict:
    """엔진 DeepSeekClient.complete_json 의 3회 재시도 계약 — 어댑터는 llm.py 주입물."""
    last: Exception | None = None
    for _attempt in range(3):
        try:
            return json.loads(complete_fn(system, user))
        except Exception as exc:  # llm.py 는 실패를 예외로 올린다(조용한 폴백 금지)
            last = exc
    raise RuntimeError(f"분류 LLM 호출이 재시도 후에도 실패: {last}")


def classify_titles(complete_fn, rows: list[dict], registry: Registry,
                    entity_index: dict[str, str]) -> dict[str, dict]:
    """article_id → 검증된 분류(EVENT + 해소 가능한 엔티티만)."""
    system = _classify_system(registry)
    results: dict[str, dict] = {}
    for start in range(0, len(rows), CLASSIFY_BATCH):
        chunk = rows[start:start + CLASSIFY_BATCH]
        items = [{"id": r["article_id"], "title": r["title"],
                  "tickers": [t for t in r["tickers"] if t in entity_index]}
                 for r in chunk]
        allowed_by_id = {i["id"]: set(i["tickers"]) for i in items}
        payload = _complete_json(complete_fn, system, json.dumps({"items": items}, ensure_ascii=False))
        for item in payload.get("items", []):
            validated = _validate_classification(item, registry, entity_index, allowed_by_id)
            if validated is not None:
                results[validated["article_id"]] = validated
    return results


def _validate_classification(item: dict, registry: Registry, entity_index: dict[str, str],
                             allowed_by_id: dict[str, set[str]]) -> dict | None:
    article_id = item.get("id")
    if not article_id or not item.get("is_event"):
        return None
    event_type = str(item.get("event_type_code") or "")
    predicate = str(item.get("predicate_code") or "")
    ticker = str(item.get("primary_ticker") or "")
    if event_type not in registry.types:
        return None
    if not predicate:
        return None
    if registry.validate(event_type, predicate):
        return None
    if ticker not in allowed_by_id.get(str(article_id), set()):
        # 프롬프트는 "입력 tickers 중에서만"을 이미 명시하지만 엔진 검증은 전역 유니버스만
        # 봤다 — 모델이 무관한 유니버스 종목을 반환하면 엉뚱한 회사에 이벤트·스레드가 선다
        # (Codex #137). 프롬프트 규칙의 코드 강제이지 분류 시맨틱 변경이 아니다.
        return None
    entity_id = entity_index.get(ticker)
    if entity_id is None:
        return None
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        confidence = None
    spec = registry.types[event_type]
    role_code = spec.required_roles[0] if spec.required_roles else "ISSUER"
    return {
        "article_id": str(article_id),
        "event_type_code": event_type,
        "predicate_code": predicate,
        "primary_ticker": ticker,
        "entity_id": entity_id,
        "role_code": role_code,
        "lifecycle_stage": (str(item.get("lifecycle_stage") or "") or None),
        "confidence": confidence,
    }


def load_entity_index(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, instrument_id FROM instrument")
        return {str(t): str(i) for t, i in cur.fetchall()}


def assembled_source_ids(conn, source_ids: list[str]) -> set[str]:
    """이미 **조립된** 기사의 article_id 집합 — 증분(재분류 방지)의 근거.

    엔진의 원래 규칙(document 존재=정규화됨)은 엔진이 document 의 유일한 생산자일 때만
    성립했다. 통합 SFN 에선 LoadDocuments 가 이 스텝보다 먼저 **모든** 기사에 document
    를 깔아 주므로, 그 기준이면 todo 가 항상 비어 이벤트가 영영 안 생긴다(Codex #137).
    조립만 남기는 자국인 document_entity(persist_normalization 전용 산출)로 판정한다 —
    비이벤트 기사는 자국이 없어 재실행 시 재분류되는데, 이는 엔진 원 동작과 같다.
    """
    if not source_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.source_document_id FROM document d"
            " JOIN document_entity de ON de.document_id = d.document_id"
            " WHERE d.source_document_id = ANY(%s)",
            (source_ids,),
        )
        return {str(r[0]) for r in cur.fetchall()}


# ── 계보 조립 (엔진 persist_normalization 이식 — 양방향 자연키 브리지 포함) ──
def persist_normalization(conn, rows: list[dict], classifications: dict[str, dict],
                          event_date: str) -> list[dict]:
    """document + canonical-event 계보 적재; 생성한 source event 목록 반환.

    엔진과 같은 자연키 브리지 2개를 그대로 유지한다(ALPHA-409·#133): document 와
    document_assertion 의 실제 행 ID 를 자연키로 다시 읽어 종속 계보를 그 ID 로 건다 —
    load-documents/load-assertions 가 먼저 적재한 행과도 FK 가 산다.
    psycopg2 execute_values → psycopg3 executemany 는 실행부 어댑트일 뿐 SQL 시맨틱 동일.

    엔진과 다른 지점 하나: source_code 를 'bigkinds' 로 하드코딩하지 않고 **기사 행의
    source_vendor** 를 쓴다 — LoadDocuments 가 (fmp, article_id)로 적재한 en 기사를
    bigkinds 키로 다시 넣으면 벤더 어긋난 중복 document 가 생긴다(Codex #137). 엔진은
    실질 ko/bigkinds 만 다뤄 잠복했던 결함이라, 정본 로직이 아니라 실행부 결함 수정이다.
    """
    created: list[dict] = []
    documents: list[tuple] = []
    news_docs: list[tuple] = []
    doc_entities: list[tuple] = []
    assertions: list[tuple] = []
    assertion_args: list[tuple] = []
    source_events: list[tuple] = []
    event_args: list[tuple] = []
    evidences: list[tuple] = []

    by_id = {r["article_id"]: r for r in rows}
    pending: list[tuple[str, dict, dict, str, str]] = []
    for article_id, cls in classifications.items():
        row = by_id.get(article_id)
        if row is None:
            continue
        available_at = _iso(row["published_at"])
        source_code = row["source_vendor"]
        documents.append((
            _stable_id("doc", source_code, article_id), "NEWS", source_code, article_id,
            row["title"], row.get("language") or "ko", available_at, available_at,
        ))
        pending.append((article_id, row, cls, available_at, source_code))

    if not documents:
        return created

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO document (document_id, document_type, source_code, source_document_id,"
            " title, language_code, published_at, available_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_code, source_document_id) DO NOTHING",
            documents,
        )
        article_ids_by_source: dict[str, list[str]] = {}
        for article_id, _r, _c, _at, source_code in pending:
            article_ids_by_source.setdefault(source_code, []).append(article_id)
        doc_id_by_key: dict[tuple[str, str], str] = {}
        for source_code, ids in sorted(article_ids_by_source.items()):
            cur.execute(
                "SELECT source_document_id, document_id FROM document"
                " WHERE source_code = %s AND source_document_id = ANY(%s)",
                (source_code, ids),
            )
            for sdi, did in cur.fetchall():
                doc_id_by_key[(source_code, sdi)] = did

    for article_id, row, cls, available_at, source_code in pending:
        document_id = doc_id_by_key[(source_code, article_id)]
        news_docs.append((document_id,))
        doc_entities.append((document_id, cls["entity_id"], row["title"], "mention",
                             cls["confidence"]))
        assertions.append((
            _stable_id("asrt", document_id, cls["event_type_code"], cls["predicate_code"]),
            document_id, cls["event_type_code"], cls["predicate_code"],
            cls["confidence"], cls["lifecycle_stage"], available_at,
        ))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO news_document (document_id) VALUES (%s)"
            " ON CONFLICT (document_id) DO NOTHING",
            news_docs,
        )
        cur.executemany(
            "INSERT INTO document_entity (document_id, entity_id, matched_text, link_method,"
            " confidence) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (document_id, entity_id) DO NOTHING",
            doc_entities,
        )
        cur.executemany(
            "INSERT INTO document_assertion (assertion_id, document_id, event_type_code,"
            " predicate_code, confidence, lifecycle_stage, available_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (document_id, event_type_code, predicate_code) DO NOTHING",
            assertions,
        )
        cur.execute(
            "SELECT document_id, event_type_code, predicate_code, assertion_id"
            " FROM document_assertion WHERE document_id = ANY(%s)",
            ([doc_id_by_key[(sc, a)] for a, _r, _c, _at, sc in pending],),
        )
        asrt_id_by_key = {(d, e, p): a for d, e, p, a in cur.fetchall()}

    for article_id, row, cls, available_at, source_code in pending:
        document_id = doc_id_by_key[(source_code, article_id)]
        entity_id = cls["entity_id"]
        assertion_id = asrt_id_by_key[(document_id, cls["event_type_code"], cls["predicate_code"])]
        assertion_args.append((assertion_id, cls["role_code"], entity_id, cls["confidence"]))

        source_event_id = _stable_id("evt", assertion_id, entity_id)
        source_events.append((
            source_event_id, "NEWS", cls["event_type_code"], event_date,
            cls["lifecycle_stage"], "ACTIVE", available_at,
        ))
        event_args.append((source_event_id, cls["role_code"], entity_id, cls["confidence"]))
        evidence_id = _stable_id("evd", source_event_id, assertion_id, TITLE_EVIDENCE_TYPE)
        evidences.append((evidence_id, source_event_id, assertion_id, TITLE_EVIDENCE_TYPE,
                          row["title"], cls["confidence"]))
        created.append({
            "source_event_id": source_event_id,
            "evidence_id": evidence_id,
            "event_type_code": cls["event_type_code"],
            "entity_id": entity_id,
            "ticker": cls["primary_ticker"],
            "available_at": available_at,
            "title": row["title"],
            "confidence": cls["confidence"],
        })

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO assertion_argument (assertion_id, role_code, entity_id, confidence)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING",
            assertion_args,
        )
        cur.executemany(
            "INSERT INTO source_event (source_event_id, source_class, event_type_code, event_date,"
            " lifecycle_stage, event_status, available_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO NOTHING",
            source_events,
        )
        cur.executemany(
            "INSERT INTO event_argument (source_event_id, role_code, entity_id, confidence)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (source_event_id, role_code, entity_id) DO NOTHING",
            event_args,
        )
        cur.executemany(
            "INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type,"
            " evidence_text, link_confidence) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (evidence_id) DO NOTHING",
            evidences,
        )
    return created


def thread_events(conn, events: list[dict]) -> None:
    """event_thread 계보 — 엔진 thread_events 이식(novelty FIRST/FOLLOW_UP 판정).

    알려진 천장(엔진 정본 그대로): prior 판정이 기존 링크 **총수** 기준이라, 이미 처리된
    날짜보다 **오래된** 날짜를 나중에 백필하면 novelty 가 역전된다(옛 이벤트가 FOLLOW_UP).
    런 내부는 available_at 정렬이라 안전 — 런 간 역순 백필만 해당. 회피는 운영 지침
    (백필은 과거→현재 순)이고, 시각 기준 novelty 재판정은 threading 로직 소유자 안건.
    """
    if not events:
        return
    threads: dict[str, tuple] = {}
    links: list[tuple] = []
    snapshots: list[tuple] = []
    evaluated_at = _utcnow_iso()

    thread_keys = {f"{e['event_type_code']}||{e['entity_id']}": None for e in events}
    prior_counts = _thread_prior_counts(conn, list(thread_keys))

    per_thread_seen: dict[str, int] = {}
    for event in sorted(events, key=lambda e: e["available_at"]):
        thread_key = f"{event['event_type_code']}||{event['entity_id']}"
        thread_id = _stable_id("thr", thread_key)
        prior = prior_counts.get(thread_key, 0) + per_thread_seen.get(thread_key, 0)
        novelty = "FIRST_IN_THREAD" if prior == 0 else "FOLLOW_UP_STAGE"
        per_thread_seen[thread_key] = per_thread_seen.get(thread_key, 0) + 1
        # 같은 배치에 같은 스레드 이벤트가 여럿이면 opened_at 은 **첫**(가장 이른) 이벤트,
        # last_state_at 만 갱신 — 마지막 대입으로 덮으면 opened_at 이 멤버보다 늦어진다.
        prev_row = threads.get(thread_key)
        opened_at = prev_row[3] if prev_row else event["available_at"]
        threads[thread_key] = (thread_id, thread_key, event["event_type_code"],
                               opened_at, event["available_at"])
        links.append((event["source_event_id"], thread_id, "NEWS", novelty, "TITLE_EVENT",
                      evaluated_at))
        snapshots.append((event["source_event_id"], thread_id, prior, None, prior == 0,
                          evaluated_at))

    with conn.cursor() as cur:
        # 백필(--from/--to)이 기존 스레드보다 **오래된** 이벤트를 넣을 수 있다 — 단순
        # 대입이면 last_state_at 이 역행하고, opened_at 보다 앞서면 ck_event_thread_time
        # 위반으로 백필 전체가 롤백된다. 시각은 단조로 유지한다(엔진은 '오늘'만 돌아
        # 이 경로가 없었다 — 백필 능력이 생기며 필요해진 실행부 보강).
        cur.executemany(
            "INSERT INTO event_thread (thread_id, thread_key, event_type_code, opened_at,"
            " last_state_at) VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (thread_key) DO UPDATE SET"
            " opened_at = LEAST(event_thread.opened_at, EXCLUDED.opened_at),"
            " last_state_at = GREATEST(event_thread.last_state_at, EXCLUDED.last_state_at)",
            list(threads.values()),
        )
        cur.executemany(
            "INSERT INTO event_thread_link (source_event_id, thread_id, source_class,"
            " novelty_status, link_type, evaluated_at) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO UPDATE SET thread_id = EXCLUDED.thread_id,"
            " novelty_status = EXCLUDED.novelty_status, evaluated_at = EXCLUDED.evaluated_at",
            links,
        )
        cur.executemany(
            "INSERT INTO thread_discovery_snapshot (source_event_id, thread_id, prior_event_count,"
            " days_since_previous_stage, is_novel, evaluated_at) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO UPDATE SET"
            " prior_event_count = EXCLUDED.prior_event_count, is_novel = EXCLUDED.is_novel,"
            " evaluated_at = EXCLUDED.evaluated_at",
            snapshots,
        )


def fetch_unthreaded_events(conn, event_date: str) -> list[dict]:
    """그 event_date 의 아직 event_thread_link 가 없는 NEWS source_event 전체.

    threading 대상을 '이번 run 신규분(created)'이 아니라 '미연결 전체'로 잡는다 — 분류는
    비싸 이미 조립된 기사를 건너뛰지만(assembled_source_ids), threading 은 싸고 멱등이라
    재실행마다 미연결분을 채워야 한다. 안 그러면 배포 전 KODEX-only 로 조립돼 미연결로 남은
    과거 이벤트가 영영 계보 없이 남고, _thread_prior_counts 가 그걸 못 세 같은 스레드 새
    이벤트의 novelty 까지 오염된다(ALPHA-468 edge-review). 이미 엮인 이벤트는 제외라
    prior_count 와 겹치지 않아 novelty 판정이 안전하다. source_event 는 in-universe 로만
    조립되므로 별도 유니버스 필터가 필요 없다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (se.source_event_id)"
            " se.source_event_id, se.event_type_code, ea.entity_id, se.available_at"
            " FROM source_event se"
            " JOIN event_argument ea ON ea.source_event_id = se.source_event_id"
            " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
            # event_status='ACTIVE' 는 엔진 read 경로(fetch_kodex_events)와 같은 필터 — 비활성
            # (REJECTED 등) 이벤트를 엮으면 prior_count 가 그걸 세 같은 스레드 첫 ACTIVE 를
            # 잘못 FOLLOW_UP 으로 판정한다(edge-review). 설명이 읽는 집합과 threading 집합을 맞춘다.
            " WHERE se.event_date = %s AND se.source_class = 'NEWS'"
            " AND se.event_status = 'ACTIVE' AND etl.source_event_id IS NULL"
            " ORDER BY se.source_event_id, ea.entity_id",
            (event_date,),
        )
        rows = cur.fetchall()
    return [
        {"source_event_id": str(r[0]), "event_type_code": r[1],
         "entity_id": str(r[2]), "available_at": _iso(r[3])}
        for r in rows
    ]


def _thread_prior_counts(conn, thread_keys: list[str]) -> dict[str, int]:
    if not thread_keys:
        return {}
    thread_ids = {_stable_id("thr", tk): tk for tk in thread_keys}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thread_id, COUNT(*) FROM event_thread_link WHERE thread_id = ANY(%s)"
            " GROUP BY thread_id",
            (list(thread_ids),),
        )
        counts = {str(tid): int(n) for tid, n in cur.fetchall()}
    return {thread_ids[tid]: n for tid, n in counts.items()}


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    complete_fn,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 뉴스 → 분류 → event 계보 조립 → threading. 성공 0, 장애 시 비0.

    창(from/to) 미지정이면 **오늘(Asia/Seoul) 하루**다 — 엔진의 일일 시맨틱을 따른다
    (tag-news 류의 전체 스캔 기본과 다름: 분류 LLM 비용이 기사 수에 비례한다). 과거
    구간은 창으로 백필한다. 이미 정규화된 기사(document 존재)는 건너뛰므로 멱등이다.
    """
    started_at = datetime.now(timezone.utc)
    news_read = in_universe_count = already_normalized = 0
    classified = events_created = threaded = 0
    failures: list[dict] = []
    exit_code = 0

    if from_date is None and to_date is None:
        today = datetime.now(_KST).date().isoformat()
        from_date = to_date = today

    try:
        registry = load_registry()
        all_dates = sorted(set().union(*[set(_partition_dates(storage, lang))
                                         for lang in LANGUAGES]) if LANGUAGES else set())
        targets = [d for d in all_dates
                   if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]

        with connect(db) as conn:
            entity_index = load_entity_index(conn)
            for date in targets:
                news = read_daily_news(storage, date)
                news_read += len(news)
                in_universe = [n for n in news if any(t in entity_index for t in n["tickers"])]
                in_universe_count += len(in_universe)
                already = assembled_source_ids(conn, [n["article_id"] for n in in_universe])
                todo = [n for n in in_universe if n["article_id"] not in already]
                already_normalized += len(in_universe) - len(todo)
                classifications = (classify_titles(complete_fn, todo, registry, entity_index)
                                   if todo else {})
                classified += len(classifications)
                created = persist_normalization(conn, todo, classifications, date)
                events_created += len(created)
                # 유니버스(entity_index=holdings 파생 마스터) 전 구성종목 이벤트를 threading 한다
                # (ALPHA-468). 과거 KODEX 9종 한정은 엔진이 KODEX 반도체만 설명하던 잔재였고,
                # 다중 ETF 설명(ALPHA-465·467)은 계보·신규성이 유니버스 전체에 필요하다.
                # in_universe/entity 해소가 이미 유니버스 필터라 별도 파생이 없다. 대상은
                # created 가 아니라 그 날짜의 미연결 전체 — 재실행이 과거 미연결분을 self-heal.
                to_thread = fetch_unthreaded_events(conn, date)
                thread_events(conn, to_thread)
                threaded += len(to_thread)
    except Exception as exc:
        # 커밋 경계는 런 전체 — connect() 가 예외면 롤백이라 부분 적재가 없다(Rule 12).
        logger.exception("이벤트 조립 실패(롤백)")
        failures.append({"reasons": ["assemble_error"], "error": str(exc)})
        events_created = threaded = 0
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date, "to_date": to_date, "languages": list(LANGUAGES),
        "news_read": news_read, "in_universe": in_universe_count,
        "already_normalized": already_normalized, "classified": classified,
        "events_created": events_created, "threaded": threaded,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "assemble_events: read=%d in_universe=%d already=%d classified=%d created=%d"
        " threaded=%d failures=%d",
        news_read, in_universe_count, already_normalized, classified, events_created,
        threaded, len(failures),
    )
    return exit_code
