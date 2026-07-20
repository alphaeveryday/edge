"""Cloud Event Store repository (Postgres).

Writes only analysis outputs (observation/route/run/result); the trigger and
event lineage are read-only (the pipeline is their single writer — ALPHA-411/412).
``psycopg2`` is imported lazily to keep import cheap and match the repo
convention for heavy drivers.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ..config import Settings
from ..domain.models import (
    POLICY_VERSION,
    Decomposition,
    Explanation,
    KodexEvent,
    PriceTrigger,
)
from ..observability import log, stable_id, utcnow_iso

_TITLE_EVIDENCE_TYPE = "TITLE"
KODEX_SEMI_INSTRUMENT_FALLBACK = "inst_01KXJB6W2EFJF0AGPMWG967ZSZ"


def _iso(value: Any) -> str:
    if value is None:
        return utcnow_iso()
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


class EventStore:
    """Thin repository over a psycopg2 connection."""

    def __init__(self, conn) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, settings: Settings) -> EventStore:
        import psycopg2

        conn = psycopg2.connect(
            host=settings.pg.host,
            port=settings.pg.port,
            dbname=settings.pg.dbname,
            user=settings.pg.user,
            password=settings.pg.password,
        )
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {settings.pg.schema}")
        conn.commit()
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    # -- reads ------------------------------------------------------------- #
    def load_entity_index(self) -> dict[str, str]:
        """ticker -> instrument entity_id for every seeded instrument."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT ticker, instrument_id FROM instrument")
            return {str(ticker): str(iid) for ticker, iid in cur.fetchall()}

    def resolve_etf_instrument(self, ticker: str) -> str:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT instrument_id FROM instrument WHERE ticker = %s AND instrument_type = 'ETF'",
                (ticker,),
            )
            row = cur.fetchone()
        return str(row[0]) if row else KODEX_SEMI_INSTRUMENT_FALLBACK

    def fetch_price_trigger(self, etf_instrument_id: str, trade_date: date):
        """Consume the pipeline's L0 trigger; ``None`` == normal variation.

        Pick the latest detected_at when transitional duplicates exist (the
        unique key is 3-keyed on detected_at).
        """

        with self._conn.cursor() as cur:
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
        return PriceTrigger(
            trigger_id=str(row[0]),
            observed_return=float(row[1]) if row[1] is not None else None,
            reason=row[2],
            abs_gate=bool(row[3]),
            rel_gate=bool(row[4]),
        )

    def fetch_kodex_events(self, trade_date: date, tickers: list[str]) -> list[KodexEvent]:
        """Load the day's KODEX-constituent source events assembled by the pipeline."""
        sql = (
            "SELECT DISTINCT ON (se.source_event_id)"
            " se.source_event_id, se.event_type_code, se.available_at, ea.entity_id, i.ticker,"
            " etl.thread_id, etl.novelty_status, ev.evidence_text"
            " FROM source_event se"
            " JOIN event_argument ea ON ea.source_event_id = se.source_event_id"
            " JOIN instrument i ON i.instrument_id = ea.entity_id"
            " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
            " LEFT JOIN event_evidence ev ON ev.source_event_id = se.source_event_id"
            " AND ev.evidence_type = %s"
            " WHERE se.event_date = %s AND se.source_class = 'NEWS' AND se.event_status = 'ACTIVE'"
            " AND i.ticker = ANY(%s)"
            " ORDER BY se.source_event_id, se.available_at"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, (_TITLE_EVIDENCE_TYPE, trade_date.isoformat(), tickers))
            rows = cur.fetchall()
        return [
            KodexEvent(
                source_event_id=str(r[0]),
                event_type_code=r[1],
                available_at=_iso(r[2]),
                entity_id=str(r[3]),
                ticker=str(r[4]),
                thread_id=r[5],
                novelty_status=r[6] or "UNKNOWN",
                title=r[7] or "",
            )
            for r in rows
        ]

    def explanation_prerequisites(
        self, settings: Settings, etf_instrument_id: str
    ) -> dict[str, Any]:
        """FK prerequisites for explanation_result: profile, route id, bundle."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM etf_profile WHERE instrument_id = %s", (etf_instrument_id,))
            has_profile = cur.fetchone() is not None
            cur.execute(
                "SELECT er.explanation_route_id FROM explanation_route er"
                " JOIN etf_contribution_observation o"
                " ON o.contribution_observation_id = er.contribution_observation_id"
                " JOIN price_movement_trigger t"
                " ON t.price_movement_trigger_id = o.price_movement_trigger_id"
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

    # -- writes ------------------------------------------------------------ #
    def persist_observation_route(
        self,
        trigger_id: str,
        decomp: Decomposition,
        route_code: str,
        event_search: bool,
        entity_index: dict[str, str],
    ) -> dict[str, str]:
        """Persist L1/route lineage off the consumed trigger (no trigger insert)."""
        from psycopg2.extras import execute_values

        detected_at = utcnow_iso()
        obs_id = stable_id("cob", trigger_id)
        route_id = stable_id("rte", obs_id)
        contribution_sum = (
            sum(m.contribution for m in decomp.members) if decomp.members else None
        )
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO etf_contribution_observation (contribution_observation_id,"
                " price_movement_trigger_id, etf_return, nav_return,"
                " constituent_contribution_return, fx_contribution_return,"
                " premium_discount_contribution_return, reconciliation_error,"
                " advancing_constituent_count, total_constituent_count,"
                " top3_contribution_ratio, available_at, data_version)"
                " VALUES (%s,%s,%s,NULL,%s,NULL,NULL,NULL,%s,%s,%s,%s,%s)"
                " ON CONFLICT (contribution_observation_id) DO NOTHING",
                (
                    obs_id, trigger_id, decomp.proxy_ret, contribution_sum,
                    decomp.advancing, decomp.total_priced, decomp.top3, detected_at,
                    POLICY_VERSION,
                ),
            )
            members = [
                (obs_id, entity_index[m.ticker], m.weight, m.ret, m.contribution, m.rank)
                for m in decomp.members
                if m.ticker in entity_index
            ]
            if members:
                execute_values(
                    cur,
                    "INSERT INTO etf_contribution_member (contribution_observation_id,"
                    " constituent_instrument_id, weight_ratio, constituent_return,"
                    " contribution_return, contribution_rank) VALUES %s"
                    " ON CONFLICT (contribution_observation_id, constituent_instrument_id)"
                    " DO NOTHING",
                    members,
                )
            cur.execute(
                "INSERT INTO explanation_route (explanation_route_id,"
                " contribution_observation_id, route_code, event_search_required,"
                " decision_reason, evaluated_at) VALUES (%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (contribution_observation_id) DO NOTHING",
                (
                    route_id, obs_id, route_code, event_search,
                    f"top1={decomp.top1}, coverage={decomp.coverage:.2f}", detected_at,
                ),
            )
        self._conn.commit()
        return {"trigger_id": trigger_id, "obs_id": obs_id, "route_id": route_id}

    def persist_explanation(
        self,
        settings: Settings,
        etf_instrument_id: str,
        explanation: Explanation,
        *,
        route_id: str,
        bundle: str | None,
        primary_thread_id: str | None,
        event_count: int,
    ) -> dict[str, str]:
        """Insert explanation_run + explanation_result (FK prerequisites assumed)."""
        import json

        explanation_as_of = utcnow_iso()
        run_id = stable_id(
            "run", etf_instrument_id, settings.trade_date.isoformat(),
            explanation_as_of, route_id,
        )
        result_id = stable_id("res", run_id)
        stage_results = json.dumps(
            {"events": event_count, "raw": explanation.raw}, ensure_ascii=False
        )
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO explanation_run (explanation_run_id, explanation_route_id,"
                " bundle_version, explanation_as_of, run_reason, run_status, finished_at)"
                " VALUES (%s,%s,%s,%s,%s,'SUCCEEDED',now())"
                " ON CONFLICT (explanation_run_id) DO NOTHING",
                (run_id, route_id, bundle, explanation_as_of, "DAILY"),
            )
            cur.execute(
                "INSERT INTO explanation_result (explanation_result_id, explanation_run_id,"
                " etf_instrument_id, trade_date, explanation_as_of, primary_thread_id,"
                " explanation_type, summary, confidence_level, stage_results,"
                " publication_status, headline)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)"
                " ON CONFLICT (explanation_result_id) DO NOTHING",
                (
                    result_id, run_id, etf_instrument_id, settings.trade_date.isoformat(),
                    explanation_as_of, primary_thread_id, explanation.explanation_type,
                    explanation.summary, explanation.confidence_level, stage_results,
                    explanation.headline,
                ),
            )
        self._conn.commit()
        log("explanation_result.stored", explanation_result_id=result_id, run_id=run_id)
        return {"persisted": "rds", "explanation_result_id": result_id, "run_id": run_id}
