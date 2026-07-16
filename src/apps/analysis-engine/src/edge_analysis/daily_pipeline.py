"""KODEX 반도체 daily news normalization + explanation pipeline (single ECS task).

Flow, one Step Functions invocation -> one Fargate task -> RDS writes:

  1. Read the trade day's news TITLES from the S3 canonical lake
     (``canonical/news/news_articles/published_date=<D>/``). Title only; body,
     lead, and URL content are never sent to the model.
  2. Restrict to news that mentions a seeded RDB entity (the tracked universe),
     and to rows that are NOT already normalized (idempotent by source id).
  3. DeepSeek classifies each un-normalized title into an event gate/type using
     the packaged ontology registry, and writes the canonical-event lineage:
     ``document`` -> ``news_document`` -> ``document_entity`` ->
     ``document_assertion``/``assertion_argument`` -> ``source_event``/
     ``event_argument``/``event_evidence``.
  4. All KODEX-constituent source events for the day are threaded into
     ``event_thread``/``event_thread_link``/``thread_discovery_snapshot``.
  5. The analysis agent (DeepSeek) reads the KODEX threads and produces the
     daily explanation. It is persisted to ``explanation_result`` when the FK
     prerequisites (etf_profile, explanation_route, release_bundle) exist,
     otherwise to S3 with a loud log line naming what is missing.

The pipeline runs for "today" (Asia/Seoul) when no ``--trade-date`` is given, so
it still runs on a day with no external trigger.

  uv run alphamale events etf daily-explain --trade-date 2026-07-14
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from edge_analysis.ontology import Registry, load_registry

KST = timezone(timedelta(hours=9))
PIPELINE_ID = "alphamale-etf-daily-v1"
DEFAULT_ETF_TICKER = "091160"
KODEX_SEMI_INSTRUMENT_FALLBACK = "inst_01KXJB6W2EFJF0AGPMWG967ZSZ"
LAKE_NEWS_PREFIX = "canonical/news/news_articles"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
CLASSIFY_BATCH = 40
TITLE_EVIDENCE_TYPE = "TITLE"

# KODEX 반도체 core constituents (weights only used to prioritise the packet).
KODEX_CONSTITUENTS: dict[str, tuple[str, float]] = {
    "000660": ("SK하이닉스", 0.40),
    "005930": ("삼성전자", 0.20),
    "042700": ("한미반도체", 0.05),
    "036930": ("주성엔지니어링", 0.04),
    "240810": ("원익IPS", 0.024),
    "058470": ("리노공업", 0.021),
    "319660": ("피에스케이", 0.020),
    "000990": ("DB하이텍", 0.020),
    "039030": ("이오테크닉스", 0.020),
}

_VERDICT_TO_TYPE = {
    "공식 이벤트 선행": "EVENT_SUPPORTED",
    "시장·섹터 주도": "MIXED",
    "가격 선행·설명 후행": "MIXED",
    "수급·흐름 추정": "PRICE_ONLY",
    "원인 미확인": "UNCERTAIN",
}
_CONFIDENCE_MAP = {"높음": "HIGH", "중간": "MEDIUM", "보류": "LOW"}


class PipelineError(RuntimeError):
    """Fatal pipeline error -> non-zero exit -> Step Functions failure."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Settings:
    trade_date: date
    request_id: str
    region: str
    lake_bucket: str
    etf_ticker: str
    pg: dict[str, Any]
    deepseek_api_key: str
    deepseek_model: str
    release_bundle_version: str | None
    result_s3_prefix: str | None
    aws_profile: str | None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _parse_trade_date(value: str | None) -> date:
    if not value:
        return datetime.now(KST).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # noqa: TRY003
        raise PipelineError(f"invalid --trade-date {value!r}; expected YYYY-MM-DD") from exc


def load_settings(args: argparse.Namespace) -> Settings:
    api_key = _env("DEEPSEEK_API_KEY")
    if not api_key:
        raise PipelineError("DEEPSEEK_API_KEY is not set")
    pg = {
        "host": _env("PGHOST", "127.0.0.1"),
        "port": int(_env("PGPORT", "5432")),
        "dbname": _env("PGDATABASE", "postgres"),
        "user": _env("PGUSER", "postgres"),
        "password": _env("PGPASSWORD"),
        "schema": _env("PGSCHEMA", "public"),
    }
    if pg["schema"] != pg["schema"].strip() or not pg["schema"].replace("_", "").isalnum():
        raise PipelineError(f"invalid PGSCHEMA {pg['schema']!r}")
    return Settings(
        trade_date=_parse_trade_date(args.trade_date),
        request_id=args.request_id or f"local-{datetime.now(KST).strftime('%Y%m%dT%H%M%S')}",
        region=_env("AWS_REGION", "ap-northeast-2"),
        lake_bucket=_env("ALPHAMALE_LAKE_BUCKET", "edge-dev-pipeline-lake"),
        etf_ticker=_env("ALPHAMALE_ETF_TICKER", DEFAULT_ETF_TICKER),
        pg=pg,
        deepseek_api_key=api_key,
        deepseek_model=_env("DEEPSEEK_MODEL", DEFAULT_MODEL),
        release_bundle_version=_env("ALPHAMALE_RELEASE_BUNDLE_VERSION"),
        result_s3_prefix=_env("ALPHAMALE_RESULT_S3_PREFIX"),
        aws_profile=_env("AWS_PROFILE"),
    )


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\u0001".join([PIPELINE_ID, *(str(p) for p in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:26]
    return f"{prefix}_{digest}"


def log(event: str, **fields: object) -> None:
    """Structured stdout log. Never emit titles, prompts, or secrets here."""
    payload = {"ts": _utcnow_iso(), "pipeline": PIPELINE_ID, "event": event}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


# --------------------------------------------------------------------------- #
# AWS + S3
# --------------------------------------------------------------------------- #
def _boto3_client(service: str, settings: Settings):
    import boto3

    if settings.aws_profile:
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.region)
    else:
        session = boto3.Session(region_name=settings.region)
    return session.client(service)


def read_daily_news(s3, bucket: str, trade_date: date) -> list[dict[str, Any]]:
    """Return canonical news rows (title-only fields) for the trade day."""
    import pyarrow.parquet as pq

    prefix = f"{LAKE_NEWS_PREFIX}/published_date={trade_date.isoformat()}/"
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        keys.extend(
            obj["Key"] for obj in resp.get("Contents", []) if obj["Key"].endswith(".parquet")
        )
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    columns = ["article_id", "title", "published_at", "publisher", "source_vendor", "mentions"]
    rows: list[dict[str, Any]] = []
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        table = pq.read_table(io.BytesIO(body), columns=columns)
        for rec in table.to_pylist():
            title = (rec.get("title") or "").strip()
            article_id = rec.get("article_id")
            if not title or not article_id:
                continue
            rows.append(
                {
                    "article_id": str(article_id),
                    "title": title,
                    "published_at": rec.get("published_at"),
                    "publisher": rec.get("publisher"),
                    "source_vendor": rec.get("source_vendor") or "bigkinds",
                    "tickers": _mention_tickers(rec.get("mentions")),
                }
            )
    return rows


def _mention_tickers(mentions: Any) -> list[str]:
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


def _iso(value: Any) -> str:
    if value is None:
        return _utcnow_iso()
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def connect(settings: Settings):
    import psycopg2

    conn = psycopg2.connect(
        host=settings.pg["host"],
        port=settings.pg["port"],
        dbname=settings.pg["dbname"],
        user=settings.pg["user"],
        password=settings.pg["password"],
    )
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {settings.pg['schema']}")
    conn.commit()
    return conn


def load_entity_index(conn) -> dict[str, str]:
    """ticker -> instrument entity_id, for every seeded instrument."""
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, instrument_id FROM instrument")
        return {str(ticker): str(instrument_id) for ticker, instrument_id in cur.fetchall()}


def resolve_etf_instrument(conn, ticker: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instrument_id FROM instrument WHERE ticker = %s AND instrument_type = 'ETF'",
            (ticker,),
        )
        row = cur.fetchone()
    if row:
        return str(row[0])
    return KODEX_SEMI_INSTRUMENT_FALLBACK


def existing_document_source_ids(conn, source_ids: Sequence[str]) -> set[str]:
    if not source_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_document_id FROM document WHERE source_document_id = ANY(%s)",
            (list(source_ids),),
        )
        return {str(row[0]) for row in cur.fetchall()}


# --------------------------------------------------------------------------- #
# DeepSeek
# --------------------------------------------------------------------------- #
class DeepSeekClient:
    def __init__(self, api_key: str, model: str, timeout: int = 180) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 8000,
            }
        ).encode("utf-8")
        last: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    DEEPSEEK_URL,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    payload = json.load(resp)
                return json.loads(payload["choices"][0]["message"]["content"])
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
                last = exc
        raise PipelineError(f"DeepSeek call failed after retries: {last}")


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


def classify_titles(
    client: DeepSeekClient,
    rows: list[dict[str, Any]],
    registry: Registry,
    entity_index: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """article_id -> validated classification (only EVENT rows with a resolvable entity)."""
    system = _classify_system(registry)
    results: dict[str, dict[str, Any]] = {}
    for start in range(0, len(rows), CLASSIFY_BATCH):
        chunk = rows[start : start + CLASSIFY_BATCH]
        items = [
            {
                "id": r["article_id"],
                "title": r["title"],
                "tickers": [t for t in r["tickers"] if t in entity_index],
            }
            for r in chunk
        ]
        user = json.dumps({"items": items}, ensure_ascii=False)
        payload = client.complete_json(system, user)
        for item in payload.get("items", []):
            validated = _validate_classification(item, registry, entity_index)
            if validated is not None:
                results[validated["article_id"]] = validated
    return results


def _validate_classification(
    item: dict[str, Any], registry: Registry, entity_index: dict[str, str]
) -> dict[str, Any] | None:
    article_id = item.get("id")
    if not article_id or not item.get("is_event"):
        return None
    event_type = str(item.get("event_type_code") or "")
    predicate = str(item.get("predicate_code") or "")
    ticker = str(item.get("primary_ticker") or "")
    if event_type not in registry.types:
        return None
    if registry.validate(event_type, predicate or None):
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


# --------------------------------------------------------------------------- #
# Normalization writes
# --------------------------------------------------------------------------- #
def persist_normalization(
    conn,
    rows: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    entity_index: dict[str, str],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Write document + canonical-event lineage; return created source events."""
    from psycopg2.extras import execute_values

    source_code = "bigkinds"
    created: list[dict[str, Any]] = []
    documents: list[tuple] = []
    news_docs: list[tuple] = []
    doc_entities: list[tuple] = []
    assertions: list[tuple] = []
    assertion_args: list[tuple] = []
    source_events: list[tuple] = []
    event_args: list[tuple] = []
    evidences: list[tuple] = []

    by_id = {r["article_id"]: r for r in rows}
    for article_id, cls in classifications.items():
        row = by_id.get(article_id)
        if row is None:
            continue
        available_at = _iso(row["published_at"])
        document_id = _stable_id("doc", source_code, article_id)
        documents.append(
            (
                document_id,
                "NEWS",
                source_code,
                article_id,
                row["title"],
                "ko",
                available_at,
                available_at,
            )
        )
        news_docs.append((document_id,))
        entity_id = cls["entity_id"]
        doc_entities.append((document_id, entity_id, row["title"], "mention", cls["confidence"]))

        assertion_id = _stable_id("asrt", document_id, cls["event_type_code"], cls["predicate_code"])
        assertions.append(
            (
                assertion_id,
                document_id,
                cls["event_type_code"],
                cls["predicate_code"],
                cls["confidence"],
                cls["lifecycle_stage"],
                available_at,
            )
        )
        assertion_args.append((assertion_id, cls["role_code"], entity_id, cls["confidence"]))

        source_event_id = _stable_id("evt", assertion_id, entity_id)
        source_events.append(
            (
                source_event_id,
                "NEWS",
                cls["event_type_code"],
                settings.trade_date.isoformat(),
                cls["lifecycle_stage"],
                "ACTIVE",
                available_at,
            )
        )
        event_args.append((source_event_id, cls["role_code"], entity_id, cls["confidence"]))
        evidence_id = _stable_id("evd", source_event_id, assertion_id, TITLE_EVIDENCE_TYPE)
        evidences.append(
            (evidence_id, source_event_id, assertion_id, TITLE_EVIDENCE_TYPE, row["title"], cls["confidence"])
        )
        created.append(
            {
                "source_event_id": source_event_id,
                "evidence_id": evidence_id,
                "event_type_code": cls["event_type_code"],
                "entity_id": entity_id,
                "ticker": cls["primary_ticker"],
                "available_at": available_at,
                "title": row["title"],
                "confidence": cls["confidence"],
            }
        )

    if not documents:
        return created

    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO document (document_id, document_type, source_code, source_document_id,"
            " title, language_code, published_at, available_at) VALUES %s"
            " ON CONFLICT (source_code, source_document_id) DO NOTHING",
            documents,
        )
        execute_values(
            cur,
            "INSERT INTO news_document (document_id) VALUES %s ON CONFLICT (document_id) DO NOTHING",
            news_docs,
        )
        execute_values(
            cur,
            "INSERT INTO document_entity (document_id, entity_id, matched_text, link_method, confidence)"
            " VALUES %s ON CONFLICT (document_id, entity_id) DO NOTHING",
            doc_entities,
        )
        execute_values(
            cur,
            "INSERT INTO document_assertion (assertion_id, document_id, event_type_code, predicate_code,"
            " confidence, lifecycle_stage, available_at) VALUES %s ON CONFLICT (assertion_id) DO NOTHING",
            assertions,
        )
        execute_values(
            cur,
            "INSERT INTO assertion_argument (assertion_id, role_code, entity_id, confidence) VALUES %s"
            " ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING",
            assertion_args,
        )
        execute_values(
            cur,
            "INSERT INTO source_event (source_event_id, source_class, event_type_code, event_date,"
            " lifecycle_stage, event_status, available_at) VALUES %s ON CONFLICT (source_event_id) DO NOTHING",
            source_events,
        )
        execute_values(
            cur,
            "INSERT INTO event_argument (source_event_id, role_code, entity_id, confidence) VALUES %s"
            " ON CONFLICT (source_event_id, role_code, entity_id) DO NOTHING",
            event_args,
        )
        execute_values(
            cur,
            "INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type,"
            " evidence_text, link_confidence) VALUES %s ON CONFLICT (evidence_id) DO NOTHING",
            evidences,
        )
    conn.commit()
    return created


# --------------------------------------------------------------------------- #
# Threading
# --------------------------------------------------------------------------- #
def thread_events(conn, events: list[dict[str, Any]]) -> None:
    from psycopg2.extras import execute_values

    if not events:
        return
    threads: dict[str, tuple] = {}
    links: list[tuple] = []
    snapshots: list[tuple] = []
    evaluated_at = _utcnow_iso()

    # Existing prior counts per thread for novelty.
    thread_keys = {
        f"{e['event_type_code']}||{e['entity_id']}": None for e in events
    }
    prior_counts = _thread_prior_counts(conn, list(thread_keys))

    per_thread_seen: dict[str, int] = {}
    for event in sorted(events, key=lambda e: e["available_at"]):
        thread_key = f"{event['event_type_code']}||{event['entity_id']}"
        thread_id = _stable_id("thr", thread_key)
        prior = prior_counts.get(thread_key, 0) + per_thread_seen.get(thread_key, 0)
        novelty = "FIRST_IN_THREAD" if prior == 0 else "FOLLOW_UP_STAGE"
        per_thread_seen[thread_key] = per_thread_seen.get(thread_key, 0) + 1
        threads[thread_key] = (
            thread_id,
            thread_key,
            event["event_type_code"],
            event["available_at"],
            event["available_at"],
        )
        links.append(
            (event["source_event_id"], thread_id, "NEWS", novelty, "TITLE_EVENT", evaluated_at)
        )
        snapshots.append(
            (event["source_event_id"], thread_id, prior, None, prior == 0, evaluated_at)
        )
        event["thread_id"] = thread_id
        event["novelty_status"] = novelty

    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO event_thread (thread_id, thread_key, event_type_code, opened_at, last_state_at)"
            " VALUES %s ON CONFLICT (thread_key) DO UPDATE SET last_state_at = EXCLUDED.last_state_at",
            list(threads.values()),
        )
        execute_values(
            cur,
            "INSERT INTO event_thread_link (source_event_id, thread_id, source_class, novelty_status,"
            " link_type, evaluated_at) VALUES %s ON CONFLICT (source_event_id) DO UPDATE SET"
            " thread_id = EXCLUDED.thread_id, novelty_status = EXCLUDED.novelty_status,"
            " evaluated_at = EXCLUDED.evaluated_at",
            links,
        )
        execute_values(
            cur,
            "INSERT INTO thread_discovery_snapshot (source_event_id, thread_id, prior_event_count,"
            " days_since_previous_stage, is_novel, evaluated_at) VALUES %s"
            " ON CONFLICT (source_event_id) DO UPDATE SET prior_event_count = EXCLUDED.prior_event_count,"
            " is_novel = EXCLUDED.is_novel, evaluated_at = EXCLUDED.evaluated_at",
            snapshots,
        )
    conn.commit()


def _thread_prior_counts(conn, thread_keys: list[str]) -> dict[str, int]:
    if not thread_keys:
        return {}
    thread_ids = {_stable_id("thr", tk): tk for tk in thread_keys}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thread_id, COUNT(*) FROM event_thread_link WHERE thread_id = ANY(%s) GROUP BY thread_id",
            (list(thread_ids),),
        )
        counts = {str(tid): int(n) for tid, n in cur.fetchall()}
    return {thread_ids[tid]: n for tid, n in counts.items()}


# --------------------------------------------------------------------------- #
# Analysis + explanation
# --------------------------------------------------------------------------- #
def select_kodex_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["ticker"] in KODEX_CONSTITUENTS]


def analyze(client: DeepSeekClient, settings: Settings, kodex_events: list[dict[str, Any]]) -> dict[str, Any]:
    lines = []
    for event in sorted(kodex_events, key=lambda e: e["available_at"]):
        name, weight = KODEX_CONSTITUENTS.get(event["ticker"], (event["ticker"], 0.0))
        lines.append(
            f"- {name}({event['ticker']}, 비중 {weight:.1%}) | {event['event_type_code']}"
            f" | {event['novelty_status']} | 「{event['title']}」"
        )
    packet = (
        f"[데이터] KODEX 반도체 ({settings.etf_ticker}) {settings.trade_date.isoformat()}\n"
        f"오늘 정규화된 구성종목 이벤트 {len(kodex_events)}건 (제목 기반):\n" + "\n".join(lines)
    )
    system = (
        "너는 KODEX 반도체 ETF의 당일 움직임을 구성종목 이벤트로 설명하는 분석 에이전트다. "
        "아래 [데이터]의 이벤트 제목만 근거로 판단하며, 없는 사실을 만들지 마라. "
        "반드시 아래 JSON만 출력한다.\n"
        '{"verdict": <"공식 이벤트 선행"|"시장·섹터 주도"|"가격 선행·설명 후행"|"수급·흐름 추정"|"원인 미확인">, '
        '"headline": <한 문장 존댓말>, "explain": <3~6문장 존댓말>, '
        '"confidence": <"높음"|"중간"|"보류">, '
        '"key_evidence": [{"signal": str, "why": str}], "unexplained": str}'
    )
    result = client.complete_json(system, packet)
    if "verdict" not in result or not (result.get("explain") or result.get("summary")):
        raise PipelineError("analysis response missing required fields")
    return result


def persist_explanation(
    conn,
    s3,
    settings: Settings,
    etf_instrument_id: str,
    explanation: dict[str, Any],
    kodex_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Insert explanation_result when FK prerequisites exist; else write to S3."""
    prereqs = _explanation_prerequisites(conn, settings, etf_instrument_id)
    explanation_as_of = _utcnow_iso()
    summary = str(explanation.get("explain") or explanation.get("summary") or "")
    etype = _VERDICT_TO_TYPE.get(str(explanation.get("verdict")), "UNCERTAIN")
    confidence = _CONFIDENCE_MAP.get(str(explanation.get("confidence")))
    primary_thread_id = kodex_events[0]["thread_id"] if kodex_events else None
    stage_results = json.dumps({"events": len(kodex_events), "raw": explanation}, ensure_ascii=False)

    missing = [k for k, v in prereqs.items() if not v]
    if missing:
        location = _write_explanation_to_s3(s3, settings, explanation, kodex_events)
        log(
            "explanation_result.skipped",
            reason="missing_prerequisites",
            missing=missing,
            s3=location,
            trade_date=settings.trade_date.isoformat(),
        )
        return {"persisted": "s3", "location": location, "missing": missing}

    run_id = _stable_id(
        "run", etf_instrument_id, settings.trade_date.isoformat(), explanation_as_of, prereqs["route"]
    )
    result_id = _stable_id("res", run_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO explanation_run (explanation_run_id, explanation_route_id, bundle_version,"
            " explanation_as_of, run_reason, run_status, finished_at)"
            " VALUES (%s,%s,%s,%s,%s,'SUCCEEDED',now())"
            " ON CONFLICT (explanation_run_id) DO NOTHING",
            (run_id, prereqs["route"], prereqs["bundle"], explanation_as_of, "DAILY"),
        )
        cur.execute(
            "INSERT INTO explanation_result (explanation_result_id, explanation_run_id, etf_instrument_id,"
            " trade_date, explanation_as_of, primary_thread_id, explanation_type, summary, confidence_level,"
            " stage_results, publication_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT')"
            " ON CONFLICT (explanation_result_id) DO NOTHING",
            (
                result_id,
                run_id,
                etf_instrument_id,
                settings.trade_date.isoformat(),
                explanation_as_of,
                primary_thread_id,
                etype,
                summary,
                confidence,
                stage_results,
            ),
        )
    conn.commit()
    log("explanation_result.stored", explanation_result_id=result_id, run_id=run_id)
    return {"persisted": "rds", "explanation_result_id": result_id, "run_id": run_id}


def _explanation_prerequisites(conn, settings: Settings, etf_instrument_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM etf_profile WHERE instrument_id = %s", (etf_instrument_id,))
        has_profile = cur.fetchone() is not None
        cur.execute(
            "SELECT er.explanation_route_id FROM explanation_route er"
            " JOIN etf_contribution_observation o ON o.contribution_observation_id = er.contribution_observation_id"
            " JOIN price_movement_trigger t ON t.price_movement_trigger_id = o.price_movement_trigger_id"
            " WHERE t.etf_instrument_id = %s AND t.trade_date = %s LIMIT 1",
            (etf_instrument_id, settings.trade_date.isoformat()),
        )
        route_row = cur.fetchone()
        bundle = settings.release_bundle_version
        has_bundle = False
        if bundle:
            cur.execute(
                "SELECT 1 FROM release_bundle WHERE bundle_version = %s AND status = 'PUBLISHED'",
                (bundle,),
            )
            has_bundle = cur.fetchone() is not None
    return {
        "profile": has_profile,
        "route": route_row[0] if route_row else None,
        "bundle": bundle if has_bundle else None,
    }


def _write_explanation_to_s3(s3, settings: Settings, explanation: dict[str, Any], events: list[dict[str, Any]]) -> str:
    prefix = settings.result_s3_prefix or f"s3://{settings.lake_bucket}/operations_archive/etf_explanations/"
    if not prefix.startswith("s3://"):
        raise PipelineError(f"ALPHAMALE_RESULT_S3_PREFIX must be an s3:// URI, got {prefix!r}")
    bucket, _, key_prefix = prefix[len("s3://") :].partition("/")
    key = (
        f"{key_prefix.rstrip('/')}/etf={settings.etf_ticker}/trade_date={settings.trade_date.isoformat()}/"
        f"{settings.request_id}.json"
    )
    payload = json.dumps(
        {
            "etf_ticker": settings.etf_ticker,
            "trade_date": settings.trade_date.isoformat(),
            "request_id": settings.request_id,
            "generated_at": _utcnow_iso(),
            "explanation": explanation,
            "events": [
                {k: e[k] for k in ("source_event_id", "thread_id", "event_type_code", "ticker", "title")}
                for e in events
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/json")
    return f"s3://{bucket}/{key}"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(settings: Settings) -> int:
    log("start", trade_date=settings.trade_date.isoformat(), request_id=settings.request_id)
    registry = load_registry()
    client = DeepSeekClient(settings.deepseek_api_key, settings.deepseek_model)

    s3 = _boto3_client("s3", settings)
    news = read_daily_news(s3, settings.lake_bucket, settings.trade_date)
    log("news.read", rows=len(news))
    if not news:
        log("done", reason="no_news")
        return 0

    conn = connect(settings)
    try:
        entity_index = load_entity_index(conn)
        etf_instrument_id = resolve_etf_instrument(conn, settings.etf_ticker)

        # Only news mentioning a seeded entity can produce FK-safe canonical events.
        in_universe = [n for n in news if any(t in entity_index for t in n["tickers"])]
        already = existing_document_source_ids(conn, [n["article_id"] for n in in_universe])
        todo = [n for n in in_universe if n["article_id"] not in already]
        log(
            "normalize.scope",
            in_universe=len(in_universe),
            already_normalized=len(already),
            to_normalize=len(todo),
        )

        classifications = classify_titles(client, todo, registry, entity_index) if todo else {}
        created = persist_normalization(conn, todo, classifications, entity_index, settings)
        log("normalize.written", canonical_events=len(created))

        # Thread every KODEX-constituent event created this run.
        kodex_events = select_kodex_events(created)
        thread_events(conn, kodex_events)
        log("thread.written", kodex_events=len(kodex_events))

        if not kodex_events:
            log("done", reason="no_kodex_events", canonical_events=len(created))
            return 0

        explanation = analyze(client, settings, kodex_events)
        outcome = persist_explanation(conn, s3, settings, etf_instrument_id, explanation, kodex_events)
        log("done", **outcome)
        return 0
    finally:
        conn.close()


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m edge_analysis",
        description="Normalize the day's news titles and explain the KODEX semiconductor ETF.",
    )
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD (Asia/Seoul); default today")
    parser.add_argument("--request-id", default=None, help="caller correlation id")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(args)
        return run(settings)
    except PipelineError as exc:
        log("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
