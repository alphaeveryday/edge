"""Cloud Event Store 리포지토리(Postgres).

분석 산출물(observation/route/run/result)만 쓴다. 트리거와 이벤트 계보는 읽기 전용이다
(파이프라인이 단일 writer — ALPHA-411/412). ``psycopg2`` 는 import 를 가볍게 유지하고
무거운 드라이버의 레포 관례를 따르려 지연 import 한다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ..config import Settings
from ..domain.models import (
    POLICY_VERSION,
    Decomposition,
    EventContext,
    Explanation,
    Measure,
    Argument,
    PriceTrigger,
)
from ..observability import log, stable_id, utcnow_iso

_TITLE_EVIDENCE_TYPE = "TITLE"

# explanation_run_event_evidence.stage_code — "이 근거를 어느 단계에서 썼나".
# 우리 엔진은 당일 사건을 홀딩스로 걸러 LLM 을 한 번 부르는 단일 경로라 **후보 수집 이후의
# 재심사 단계가 없다**. 설계 문서의 단계명(A·B·E·F·G·L4)을 빌려 쓰면 통과한 적 없는 관문을
# 통과한 것처럼 기록되므로, 실제로 한 일만 말하는 값을 쓴다 — 프롬프트에 실었다.
# 논리 계약(hq_run_evidence)에는 stage 축 자체가 없다(PK 가 run+evidence 2축).
_PROMPT_STAGE_CODE = "PROMPT"


def _iso(value: Any) -> str:
    """datetime/None 을 ISO 문자열로(None 이면 지금, naive 는 UTC 로 간주)."""
    if value is None:
        return utcnow_iso()
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


class EventStore:
    """psycopg2 커넥션 위의 얇은 리포지토리."""

    def __init__(self, conn) -> None:
        """주어진 psycopg2 커넥션을 감싼다."""
        self._conn = conn

    @classmethod
    def connect(cls, settings: Settings) -> EventStore:
        """설정으로 접속하고 search_path 를 세팅한 EventStore 를 반환한다."""
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
        """커넥션을 닫는다."""
        self._conn.close()

    # -- 읽기 --------------------------------------------------------------- #
    def causal_data(self):
        """인과 설계용 조회 표면. **같은 커넥션**을 공유한다 - PIT 기준이 갈리면 안 된다.

        `_conn` 을 밖으로 내보내지 않는다. 인과 엔진이 필요한 것은 코호트·정렬열·비중이고
        그건 `CausalData` 의 계약이다.
        """
        from .causal_data import CausalData
        return CausalData(self._conn)

    def load_entity_index(self) -> dict[str, str]:
        """ticker -> instrument entity_id (시드된 전 종목)."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT ticker, instrument_id FROM instrument")
            return {str(ticker): str(iid) for ticker, iid in cur.fetchall()}

    def resolve_etf_instrument(self, ticker: str) -> tuple[str, str] | None:
        """ETF 의 (instrument_id, 표시명) — 마스터에 없으면 ``None``.

        표시명은 ``entity.display_name``(instrument_id = entity_id)에서 온다 — instrument
        자체엔 이름 컬럼이 없다. 구현은 조회 실패 시 091160 instrument_id 로 폴백했는데,
        다른 ETF 를 돌리면 holdings 는 env 티커로, 트리거·설명은 폴백 id 로 붙어 **계보가
        조용히 오염**된다(ALPHA-467). 폴백을 없애고 None 을 돌려 호출부가 fail-loud 한다.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, e.display_name FROM instrument i"
                " JOIN entity e ON e.entity_id = i.instrument_id"
                " WHERE i.ticker = %s AND i.instrument_type = 'ETF'",
                (ticker,),
            )
            row = cur.fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def fetch_price_trigger(self, etf_instrument_id: str, trade_date: date):
        """파이프라인 L0 트리거 소비. ``None`` == 평온(정상 변동).

        이행기 중복이 있으면 최신 detected_at 을 고른다(uq 3번째 키가 detected_at).
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

    def fetch_event_contexts(self, trade_date: date, tickers: list[str]) -> list[EventContext]:
        """파이프라인이 조립한 당일 구성종목 source event 를 참여자·측정값까지 붙여 읽는다.

        사건 선별(NEWS·ACTIVE·holdings 접지)과 문맥 수집(참여자 전원·측정값 전부)을
        분리한 3쿼리다 — 단일 조인을 DISTINCT ON 으로 붕괴시키면 사건당 참여자 1명만
        남는다(v4 온톨로지 이전의 손실). DISTINCT ON 은 이제 evidence fanout 방어만 한다
        (TITLE evidence 는 assertion 별로 여럿일 수 있다). 신규 온톨로지 컬럼
        (predicate_code·slot 등)은 백필 전 NULL 이어도 동작한다.
        """
        head_sql = (
            "SELECT DISTINCT ON (se.source_event_id)"
            " se.source_event_id, se.event_type_code, se.available_at,"
            " se.predicate_code, se.lifecycle_stage,"
            " etl.thread_id, etl.novelty_status, ev.evidence_text, ev.evidence_id"
            " FROM source_event se"
            " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
            " LEFT JOIN event_evidence ev ON ev.source_event_id = se.source_event_id"
            " AND ev.evidence_type = %s"
            " WHERE se.event_date = %s AND se.source_class = 'NEWS' AND se.event_status = 'ACTIVE'"
            " AND EXISTS (SELECT 1 FROM event_argument ea"
            " JOIN instrument i ON i.instrument_id = ea.entity_id"
            " WHERE ea.source_event_id = se.source_event_id AND i.ticker = ANY(%s))"
            " ORDER BY se.source_event_id, ev.evidence_id"
        )
        with self._conn.cursor() as cur:
            cur.execute(head_sql, (_TITLE_EVIDENCE_TYPE, trade_date.isoformat(), tickers))
            heads = cur.fetchall()
            if not heads:
                return []
            event_ids = [str(h[0]) for h in heads]
            # 참여자 전원 — 비종목 entity 도 유지하도록 instrument 는 LEFT JOIN 이다.
            cur.execute(
                "SELECT ea.source_event_id, ea.role_code, ea.slot, ea.entity_id,"
                " i.ticker, ea.confidence"
                " FROM event_argument ea"
                " LEFT JOIN instrument i ON i.instrument_id = ea.entity_id"
                " WHERE ea.source_event_id = ANY(%s)"
                " ORDER BY ea.source_event_id, ea.role_code, ea.entity_id",
                (event_ids,),
            )
            argument_rows = cur.fetchall()
            cur.execute(
                "SELECT em.source_event_id, em.role_code, em.value, em.unit, em.basis,"
                " em.value_source, em.surface"
                " FROM event_measure em"
                " WHERE em.source_event_id = ANY(%s)"
                " ORDER BY em.source_event_id, em.measure_ord",
                (event_ids,),
            )
            measure_rows = cur.fetchall()

        arguments: dict[str, list[Argument]] = {}
        for r in argument_rows:
            arguments.setdefault(str(r[0]), []).append(Argument(
                role_code=str(r[1]),
                slot=r[2],
                entity_id=str(r[3]),
                ticker=str(r[4]) if r[4] is not None else None,
                confidence=float(r[5]) if r[5] is not None else None,
            ))
        measures: dict[str, list[Measure]] = {}
        for r in measure_rows:
            measures.setdefault(str(r[0]), []).append(Measure(
                role_code=str(r[1]),
                value=r[2],
                unit=r[3],
                basis=str(r[4] or "UNKNOWN"),
                value_source=str(r[5] or "UNRESOLVED"),
                surface=r[6],
            ))

        wanted = set(tickers)
        contexts: list[EventContext] = []
        for h in heads:
            event_id = str(h[0])
            event_arguments = tuple(arguments.get(event_id, ()))
            # 대표 참여자 = holdings 접지 참여자(이 사건이 선별된 근거) 중 첫 번째.
            anchor = next((p for p in event_arguments if p.ticker in wanted), None)
            if anchor is None:
                continue  # 선별·수집 사이 인자 소실(비정상) — 접지 없는 사건은 버린다.
            contexts.append(EventContext(
                source_event_id=event_id,
                event_type_code=h[1],
                available_at=_iso(h[2]),
                entity_id=anchor.entity_id,
                ticker=anchor.ticker or "",
                thread_id=h[5],
                novelty_status=h[6] or "UNKNOWN",
                title=h[7] or "",
                arguments=event_arguments,
                measures=tuple(measures.get(event_id, ())),
                predicate_code=h[3],
                lifecycle_stage=h[4],
                # LEFT JOIN 이라 TITLE evidence 가 없는 사건은 None 이다 — 그 사건은
                # 설명에는 실리되 lineage 에는 남길 근거가 없다(persist 가 세어 로그로 낸다).
                evidence_id=str(h[8]) if h[8] is not None else None,
            ))
        return contexts

    def explanation_prerequisites(
        self, settings: Settings, etf_instrument_id: str
    ) -> dict[str, Any]:
        """explanation_result 의 FK 전제: profile 존재·route id·bundle."""
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

    # -- 쓰기 --------------------------------------------------------------- #
    def persist_observation_route(
        self,
        trigger_id: str,
        decomp: Decomposition,
        route_code: str,
        event_search: bool,
        entity_index: dict[str, str],
    ) -> dict[str, str]:
        """소비한 트리거에서 파생한 L1/route 계보를 적재한다(트리거 insert 없음)."""
        from psycopg2.extras import execute_values

        detected_at = utcnow_iso()
        # 계보 id 는 소비한 trigger_id 에서 파생 — DB 에 있는 그 행에 계보가 매달린다.
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
        events: list[EventContext],
    ) -> dict[str, str | int | None]:
        """explanation_run + explanation_result(게시 게이트) + 근거 lineage + fan-out 을
        한 트랜잭션으로 적재한다(FK 전제는 충족 가정, ALPHA-493)."""
        import json

        from psycopg2.extras import execute_values

        explanation_as_of = utcnow_iso()
        event_count = len(events)
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
            # 게시 게이트·cursor 채번 직렬화(ALPHA-493) — analyze 동시 실행이 같은 날
            # PUBLISHED 를 이중 게시하거나 같은 테넌트 cursor 를 겹쳐 뽑지 못하게 전역 락
            # 하나로 묶는다. fan-out 은 매번 전 테넌트 대상이라 테넌트별 락은 이득이 없다.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext('tenant-delivery-fanout')::bigint)"
            )
            # 그날 첫 결과만 PUBLISHED — explanation_as_of 가 런마다 새로워 grain 부분
            # 유니크(as_of 포함)는 같은 날 이중 게시를 못 막는다. EXISTS 가 PUBLISHED 만
            # 보는 이유: WITHDRAWN 뒤 재산출은 재게시(CORRECTION) 시나리오라 후속 티켓
            # 몫이고, 현재 cloud 에 WITHDRAWN writer 가 없어 도달 불가 경로다.
            cur.execute(
                "INSERT INTO explanation_result (explanation_result_id, explanation_run_id,"
                " etf_instrument_id, trade_date, explanation_as_of, primary_thread_id,"
                " explanation_type, summary, confidence_level, stage_results,"
                " publication_status, headline)"
                " SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                " CASE WHEN EXISTS (SELECT 1 FROM explanation_result p"
                "   WHERE p.etf_instrument_id = %s AND p.trade_date = %s"
                "     AND p.publication_status = 'PUBLISHED')"
                " THEN 'DRAFT' ELSE 'PUBLISHED' END, %s"
                " ON CONFLICT (explanation_result_id) DO NOTHING"
                " RETURNING publication_status",
                (
                    result_id, run_id, etf_instrument_id, settings.trade_date.isoformat(),
                    explanation_as_of, primary_thread_id, explanation.explanation_type,
                    explanation.summary, explanation.confidence_level, stage_results,
                    etf_instrument_id, settings.trade_date.isoformat(),
                    explanation.headline,
                ),
            )
            published_row = cur.fetchone()
            publication_status = published_row[0] if published_row else None
            # 근거 lineage — "이 설명이 무엇을 보고 쓰였나"(ALPHA-603). 프롬프트에 실린
            # 사건의 근거만 넣는다(events 는 packet 이 자르지 않고 통째로 싣는 그 목록이다).
            evidence_rows = [
                (run_id, event.evidence_id, _PROMPT_STAGE_CODE)
                for event in events
                if event.evidence_id
            ]
            # 중복 result_id(무삽입)면 lineage 도 건너뛴다 — 이번 런의 근거를 기존 run 에
            # 섞으면 저장된 설명이 실제로 안 본 근거가 연결된다.
            if evidence_rows and published_row is not None:
                execute_values(
                    cur,
                    "INSERT INTO explanation_run_event_evidence (explanation_run_id,"
                    " evidence_id, stage_code) VALUES %s"
                    " ON CONFLICT (explanation_run_id, evidence_id, stage_code) DO NOTHING",
                    evidence_rows,
                )
            # write-time fan-out(ALPHA-493) — 게시와 같은 트랜잭션이라 커밋된 행만
            # 커서에 노출된다(sync-protocol). NEW 만 발번, CORRECTION·INVALIDATION 은 후속.
            fanout_tenants = 0
            if publication_status == "PUBLISHED":
                fanout_tenants = self._fanout_new(cur, result_id)
        self._conn.commit()
        if published_row is None:
            # 같은 result_id 재실행(같은 초·같은 route) — 기존 행이 보존되고 이번 런의
            # 산출물은 버려졌다. 조용히 지나가면 유실이 안 보인다(Rule 12). stored 로그를
            # 찍지 않는다 — 아무것도 저장되지 않은 런이 성공 건으로 집계되면 안 된다.
            log(
                "explanation_result.duplicate_skipped",
                reason="result_id_conflict",
                explanation_result_id=result_id,
                trade_date=settings.trade_date.isoformat(),
            )
            return {
                "persisted": "rds",
                "explanation_result_id": result_id,
                "run_id": run_id,
                "publication_status": None,
                "fanout_tenants": 0,
            }
        log(
            "explanation_result.stored",
            explanation_result_id=result_id,
            run_id=run_id,
            evidence=len(evidence_rows),
            # 근거 없는 사건 — 설명에는 실렸는데 되짚을 문서가 없다는 뜻이라 드러낸다.
            events_without_evidence=event_count - len(evidence_rows),
            publication_status=publication_status,
            fanout_tenants=fanout_tenants,
        )
        if publication_status == "DRAFT":
            # 같은 날 재실행 — 게시분이 이미 있어 DRAFT 보존만 하고 발번하지 않았다.
            log(
                "explanation_result.publish_skipped",
                reason="grain_already_published",
                etf_instrument_id=etf_instrument_id,
                trade_date=settings.trade_date.isoformat(),
                explanation_result_id=result_id,
            )
        elif publication_status == "PUBLISHED" and fanout_tenants == 0:
            # 게시는 됐는데 받을 테넌트가 없다 — 온보딩 전 상태라 런은 성공으로 둔다.
            log("tenant_delivery.fanout_empty", explanation_result_id=result_id)
        return {
            "persisted": "rds",
            "explanation_result_id": result_id,
            "run_id": run_id,
            "publication_status": publication_status,
            "fanout_tenants": fanout_tenants,
        }

    @staticmethod
    def _fanout_new(cur, explanation_result_id: str) -> int:
        """게시된 설명을 전 테넌트 outbox 로 NEW 발번한다 — 게시와 같은 트랜잭션.

        cursor 는 테넌트별 단조증가(sync-protocol.md) — 호출 전 잡은 advisory lock 이
        동시 채번을 직렬화한다. 대상 = tenant 전 행(필터는 필요해질 때 후속).
        """
        cur.execute(
            "INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type,"
            " explanation_result_id)"
            " SELECT t.tenant_id, COALESCE(MAX(d.cursor), 0) + 1, 'NEW', %s"
            " FROM tenant t LEFT JOIN tenant_delivery d ON d.tenant_id = t.tenant_id"
            " GROUP BY t.tenant_id",
            (explanation_result_id,),
        )
        return cur.rowcount
