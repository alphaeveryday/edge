"""KODEX 반도체 daily news normalization + explanation pipeline (single ECS task).

Flow, one Step Functions invocation -> one Fargate task (ALPHA-412 이후 — 소비자):

  1. 파이프라인이 만든 feature 산출물만 읽는다(ADR-0028): price_movement_trigger
     (L0 게이트, load-price-triggers)와 source_event 계보(assemble-events).
     뉴스 읽기·분류·계보 조립·threading 은 feature 페이즈로 이관됐다.
  2. 구성종목 분해(가격 S3 읽기)는 observation·설명 packet 입력으로 유지한다.
  3. The analysis agent (DeepSeek) reads the KODEX threads and produces the
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


KST = timezone(timedelta(hours=9))
PIPELINE_ID = "alphamale-etf-daily-v1"
DEFAULT_ETF_TICKER = "091160"
KODEX_SEMI_INSTRUMENT_FALLBACK = "inst_01KXJB6W2EFJF0AGPMWG967ZSZ"
LAKE_PRICE_PREFIX = "canonical/market_data/price_daily"
LAKE_HOLDINGS_PREFIX = "canonical/holdings/etf_holdings"
CONCENTRATION_THRESHOLD = 0.5
# 게이트 임계값은 이제 여기 없다 — L0 게이트는 파이프라인 load-price-triggers 가 단일
# writer 로 판정한다(ALPHA-411, [price_triggers] 설정). 아래 버전은 observation 의
# data_version(분해 산출 버전) 스탬프로만 남는다.
POLICY_VERSION = "l0-abs-v1"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
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


# --------------------------------------------------------------------------- #
# Price consumption, identity decomposition, L0 gate, routing (cloud S3)
# --------------------------------------------------------------------------- #
def _partition_values(s3, bucket: str, base: str, key: str) -> list[str]:
    """Sorted partition values for ``key=`` immediately under ``base``."""
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=base, Delimiter="/")
    out: list[str] = []
    for common in resp.get("CommonPrefixes", []):
        seg = common.get("Prefix", "").rstrip("/").split("/")[-1]
        if seg.startswith(f"{key}="):
            out.append(seg[len(key) + 1 :])
    return sorted(out)


def _read_parquet_prefix(s3, bucket: str, prefix: str, columns: list[str]) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if not obj["Key"].endswith(".parquet"):
                continue
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            rows.extend(pq.read_table(io.BytesIO(body), columns=columns).to_pylist())
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return rows


def load_constituent_prices(s3, bucket: str, market: str, trade_date: date) -> dict[str, dict[str, Any]]:
    """Close-to-close returns per ticker for the trade day, from the S3 lake.

    Uses the immediately-preceding available trade_date partition as D-1.
    """
    base = f"{LAKE_PRICE_PREFIX}/market={market}/"
    dates = _partition_values(s3, bucket, base, "trade_date")
    d = trade_date.isoformat()
    if d not in dates:
        return {}
    idx = dates.index(d)
    prev = dates[idx - 1] if idx > 0 else None
    cur = {
        str(r["ticker"]): r["close"]
        for r in _read_parquet_prefix(s3, bucket, f"{base}trade_date={d}/", ["ticker", "close"])
        if r.get("close") is not None
    }
    prv = (
        {
            str(r["ticker"]): r["close"]
            for r in _read_parquet_prefix(s3, bucket, f"{base}trade_date={prev}/", ["ticker", "close"])
            if r.get("close") is not None
        }
        if prev
        else {}
    )
    out: dict[str, dict[str, Any]] = {}
    for ticker, close in cur.items():
        prev_close = prv.get(ticker)
        ret = (close / prev_close - 1.0) if prev_close and prev_close > 0 else None
        out[ticker] = {"close": close, "prev_close": prev_close, "ret": ret, "prev_date": prev}
    return out


def load_etf_holdings(s3, bucket: str, market: str, etf_id: str, trade_date: date) -> tuple[list[dict[str, Any]], str | None]:
    """Constituent weights (fraction) for one ETF, latest as_of <= trade_date."""
    base = f"{LAKE_HOLDINGS_PREFIX}/market={market}/"
    dates = _partition_values(s3, bucket, base, "as_of_date")
    if not dates:
        return [], None
    eligible = [x for x in dates if x <= trade_date.isoformat()]
    chosen = eligible[-1] if eligible else dates[0]
    rows = _read_parquet_prefix(
        s3, bucket, f"{base}as_of_date={chosen}/",
        ["etf_id", "constituent_ticker", "constituent_name", "weight_pct"],
    )
    holdings = [
        {
            "ticker": str(r["constituent_ticker"]),
            "name": r.get("constituent_name"),
            "weight": float(r["weight_pct"] or 0.0) / 100.0,
        }
        for r in rows
        if str(r.get("etf_id")) == etf_id and r.get("constituent_ticker")
    ]
    return holdings, chosen


def compute_decomposition(holdings: list[dict[str, Any]], prices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Constituent-contribution decomposition over the priced subset."""
    total_weight = sum(h["weight"] for h in holdings)
    members: list[dict[str, Any]] = []
    num = den = 0.0
    for h in holdings:
        ret = prices.get(h["ticker"], {}).get("ret")
        if ret is None:
            continue
        contribution = h["weight"] * ret
        members.append(
            {"ticker": h["ticker"], "name": h["name"], "weight": h["weight"], "ret": ret, "contribution": contribution}
        )
        num += contribution
        den += h["weight"]
    members.sort(key=lambda m: abs(m["contribution"]), reverse=True)
    for rank, m in enumerate(members, 1):
        m["rank"] = rank
    total_abs = sum(abs(m["contribution"]) for m in members)
    proxy_ret = (num / den) if den > 0 else None
    return {
        "members": members,
        "proxy_ret": proxy_ret,
        "covered_weight": den,
        "total_weight": total_weight,
        "coverage": (den / total_weight) if total_weight > 0 else 0.0,
        "top1": (abs(members[0]["contribution"]) / total_abs) if members and total_abs > 0 else None,
        "top3": (sum(abs(m["contribution"]) for m in members[:3]) / total_abs) if members and total_abs > 0 else None,
        "advancing": sum(1 for m in members if m["ret"] > 0),
        "total_priced": len(members),
        "n_constituents": len(holdings),
    }


def fetch_price_trigger(conn, etf_instrument_id: str, trade_date: date) -> dict[str, Any] | None:
    """Consume the pipeline-produced L0 trigger (single writer: load-price-triggers).

    The engine no longer computes or persists the gate (ALPHA-411) — the pipeline's
    holdings-weighted proxy 3% gate decides. No row for the day == normal variation.
    Transitional safety: pick the latest detected_at if legacy duplicates exist
    (the uq is 3-keyed on detected_at).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT price_movement_trigger_id, observed_return, detection_reason,"
            " absolute_gate_triggered, relative_gate_triggered"
            " FROM price_movement_trigger"
            " WHERE etf_instrument_id = %s AND trade_date = %s"
            " ORDER BY detected_at DESC LIMIT 1",
            (etf_instrument_id, trade_date.isoformat()),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "trigger_id": str(row[0]),
        "observed_return": float(row[1]) if row[1] is not None else None,
        "reason": row[2],
        "abs_gate": bool(row[3]),
        "rel_gate": bool(row[4]),
    }


def decide_route(decomp: dict[str, Any]) -> tuple[str, bool]:
    """Route code + whether event (news) search is required."""
    top1 = decomp.get("top1")
    if top1 is not None and top1 >= CONCENTRATION_THRESHOLD:
        return "CONCENTRATED", True
    return "COMMON_FACTOR", True


def persist_observation_route(
    conn,
    trigger_id: str,
    decomp: dict[str, Any],
    route_code: str,
    event_search: bool,
    entity_index: dict[str, str],
) -> dict[str, str]:
    """Persist L1/route lineage off the **consumed** trigger. FK-safe: seeded instruments only.

    The trigger row itself is the pipeline's (ALPHA-411) — obs/route ids derive from
    whatever id the pipeline minted, so the lineage stays attached to the real row.
    """
    from psycopg2.extras import execute_values

    detected_at = _utcnow_iso()
    obs_id = _stable_id("cob", trigger_id)
    route_id = _stable_id("rte", obs_id)
    contribution_sum = sum(m["contribution"] for m in decomp["members"]) if decomp["members"] else None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO etf_contribution_observation (contribution_observation_id, price_movement_trigger_id,"
            " etf_return, nav_return, constituent_contribution_return, fx_contribution_return,"
            " premium_discount_contribution_return, reconciliation_error, advancing_constituent_count,"
            " total_constituent_count, top3_contribution_ratio, available_at, data_version)"
            " VALUES (%s,%s,%s,NULL,%s,NULL,NULL,NULL,%s,%s,%s,%s,%s)"
            " ON CONFLICT (contribution_observation_id) DO NOTHING",
            (
                obs_id, trigger_id, decomp["proxy_ret"], contribution_sum,
                decomp["advancing"], decomp["total_priced"], decomp["top3"], detected_at, POLICY_VERSION,
            ),
        )
        members = [
            (obs_id, entity_index[m["ticker"]], m["weight"], m["ret"], m["contribution"], m["rank"])
            for m in decomp["members"]
            if m["ticker"] in entity_index
        ]
        if members:
            execute_values(
                cur,
                "INSERT INTO etf_contribution_member (contribution_observation_id, constituent_instrument_id,"
                " weight_ratio, constituent_return, contribution_return, contribution_rank) VALUES %s"
                " ON CONFLICT (contribution_observation_id, constituent_instrument_id) DO NOTHING",
                members,
            )
        cur.execute(
            "INSERT INTO explanation_route (explanation_route_id, contribution_observation_id, route_code,"
            " event_search_required, decision_reason, evaluated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (contribution_observation_id) DO NOTHING",
            (
                route_id, obs_id, route_code, event_search,
                f"top1={decomp['top1']}, coverage={decomp['coverage']:.2f}", detected_at,
            ),
        )
    conn.commit()
    return {"trigger_id": trigger_id, "obs_id": obs_id, "route_id": route_id}


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


# --------------------------------------------------------------------------- #
# Normalization writes
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Threading
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Analysis + explanation
# --------------------------------------------------------------------------- #
def fetch_kodex_events(conn, trade_date: date, tickers: list[str]) -> list[dict[str, Any]]:
    """Load the trade day's KODEX-constituent source events from the DB.

    Used so an idempotent rerun (documents already normalized this day) still
    has the full event set to thread/explain, not just events created this run.
    """
    sql = (
        "SELECT DISTINCT ON (se.source_event_id)"
        " se.source_event_id, se.event_type_code, se.available_at, ea.entity_id, i.ticker,"
        " etl.thread_id, etl.novelty_status, ev.evidence_text"
        " FROM source_event se"
        " JOIN event_argument ea ON ea.source_event_id = se.source_event_id"
        " JOIN instrument i ON i.instrument_id = ea.entity_id"
        " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
        " LEFT JOIN event_evidence ev ON ev.source_event_id = se.source_event_id AND ev.evidence_type = %s"
        " WHERE se.event_date = %s AND se.source_class = 'NEWS' AND se.event_status = 'ACTIVE'"
        " AND i.ticker = ANY(%s)"
        " ORDER BY se.source_event_id, se.available_at"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (TITLE_EVIDENCE_TYPE, trade_date.isoformat(), tickers))
        rows = cur.fetchall()
    return [
        {
            "source_event_id": str(r[0]),
            "event_type_code": r[1],
            "available_at": _iso(r[2]),
            "entity_id": str(r[3]),
            "ticker": str(r[4]),
            "thread_id": r[5],
            "novelty_status": r[6] or "UNKNOWN",
            "title": r[7] or "",
        }
        for r in rows
    ]


def analyze(
    client: DeepSeekClient,
    settings: Settings,
    decomp: dict[str, Any],
    gate: dict[str, Any],
    route_code: str,
    kodex_events: list[dict[str, Any]],
) -> dict[str, Any]:
    proxy = decomp["proxy_ret"]
    price_lines = [
        f"ETF 프록시 등락(구성종목 기여 합, 가격 커버리지 {decomp['coverage']:.0%}): "
        + (f"{proxy * 100:+.2f}%" if proxy is not None else "산출 불가"),
        f"진입 게이트: 절대(|등락|>=3%)={'발화' if gate['abs_gate'] else '미발화'} (상대 게이트: 지수 미확보로 미적용)",
        f"라우팅: {route_code}",
    ]
    if decomp["top3"] is not None:
        price_lines.append(
            f"상승 {decomp['advancing']}/{decomp['total_priced']}종목(가격 보유분), 상위3 기여집중도 {decomp['top3']:.0%}"
        )
    price_lines.append("구성종목 기여(가격 보유분만, 비중×등락):")
    for m in decomp["members"][:8]:
        price_lines.append(
            f"  {m['name'] or m['ticker']}({m['ticker']}) 비중 {m['weight']:.1%}"
            f" | 등락 {m['ret'] * 100:+.1f}% | 기여 {m['contribution'] * 100:+.2f}%p"
        )
    price_lines.append(
        f"주의: 구성종목 {decomp['n_constituents']}개 중 {decomp['total_priced']}개만 가격 확보"
        f"(비중 {decomp['coverage']:.0%}). 나머지·NAV·괴리·환율은 미확보이므로 단정 금지."
    )

    event_lines = []
    for event in sorted(kodex_events, key=lambda e: e["available_at"]):
        name, _weight = KODEX_CONSTITUENTS.get(event["ticker"], (event["ticker"], 0.0))
        event_lines.append(
            f"- {name}({event['ticker']}) | {event['event_type_code']}"
            f" | {event['novelty_status']} | 「{event['title']}」"
        )
    events_block = "\n".join(event_lines) if event_lines else "  (해당 없음)"

    packet = (
        f"[데이터] KODEX 반도체 ({settings.etf_ticker}) {settings.trade_date.isoformat()}\n\n"
        f"[가격 분해]\n" + "\n".join(price_lines) + "\n\n"
        f"[구성종목 뉴스 이벤트 {len(kodex_events)}건 (제목 기반)]\n" + events_block
    )
    system = (
        "너는 KODEX 반도체 ETF의 당일 움직임을 설명하는 분석 에이전트다. "
        "[가격 분해]의 수치와 [뉴스 이벤트]의 제목만 근거로 판단하며, 없는 사실을 만들지 마라. "
        "가격 커버리지가 부분이면 그 한계를 반영하고 단정하지 마라. 숫자는 제공된 값만 인용한다. "
        "반드시 아래 JSON만 출력한다.\n"
        '{"verdict": <"공식 이벤트 선행"|"시장·섹터 주도"|"가격 선행·설명 후행"|"수급·흐름 추정"|"원인 미확인">, '
        '"headline": <한 문장 존댓말>, "explain": <3~6문장 존댓말, 견인 종목 기여와 이벤트를 연결>, '
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
            " stage_results, publication_status, headline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)"
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
                str(explanation.get("headline") or "") or None,
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
    client = DeepSeekClient(settings.deepseek_api_key, settings.deepseek_model)
    s3 = _boto3_client("s3", settings)

    conn = connect(settings)
    try:
        entity_index = load_entity_index(conn)
        etf_instrument_id = resolve_etf_instrument(conn, settings.etf_ticker)

        # --- Consume price (cloud S3) and decompose the ETF move (L1) --------
        holdings, holdings_asof = load_etf_holdings(s3, settings.lake_bucket, "KR", settings.etf_ticker, settings.trade_date)
        prices = load_constituent_prices(s3, settings.lake_bucket, "KR", settings.trade_date)
        decomp = compute_decomposition(holdings, prices)
        log(
            "price.decomposed",
            holdings_asof=holdings_asof,
            constituents=decomp["n_constituents"],
            priced=decomp["total_priced"],
            coverage=round(decomp["coverage"], 4),
            proxy_ret=decomp["proxy_ret"],
        )

        # --- L0 gate is consumed, not computed (ALPHA-411) --------------------
        # 파이프라인 load-price-triggers 가 단일 writer 다 — 행이 없으면 그날은 평온이다.
        gate = fetch_price_trigger(conn, etf_instrument_id, settings.trade_date)
        if gate is None:
            # Normal variation is a first-class answer; no explanation is produced.
            log("done", reason="normal_variation", observed_return=decomp["proxy_ret"])
            return 0

        route_code, event_search = decide_route(decomp)
        ids = persist_observation_route(conn, gate["trigger_id"], decomp, route_code, event_search, entity_index)
        log("trigger.consumed", route=route_code, event_search=event_search, **ids)

        # --- Event search: consume events assembled by the pipeline ----------
        # 뉴스 읽기·분류·계보 조립·threading 은 feature 페이즈의 assemble-events 로
        # 이관됐다(ALPHA-412, ADR-0028) — 여기서는 DB 의 이벤트를 소비만 한다.
        kodex_events: list[dict[str, Any]] = []
        if event_search:
            kodex_events = fetch_kodex_events(conn, settings.trade_date, list(KODEX_CONSTITUENTS))
            log("events.ready", kodex_events=len(kodex_events))

        # --- Synthesis: price decomposition + news events --------------------
        explanation = analyze(client, settings, decomp, gate, route_code, kodex_events)
        outcome = persist_explanation(conn, s3, settings, etf_instrument_id, explanation, kodex_events)
        log("done", route=route_code, kodex_events=len(kodex_events), **outcome)
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
