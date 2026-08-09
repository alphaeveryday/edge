"""Cloud Event Store 리포지토리(Postgres).

분석 산출물(observation/route/run/result)만 쓴다. 트리거와 이벤트 계보는 읽기 전용이다
(파이프라인이 단일 writer — ALPHA-411/412). ``psycopg2`` 는 import 를 가볍게 유지하고
무거운 드라이버의 레포 관례를 따르려 지연 import 한다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, NamedTuple

from ..config import KST, PipelineError, ReturnsNotReadyError, Settings
from ..domain.models import (
    POLICY_VERSION,
    Decomposition,
    EventContext,
    Explanation,
    Measure,
    Argument,
    PriceTrigger,
)
from ..domain.window import CommittedMinuteWindow
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


class MinuteTriggerRow(NamedTuple):
    """분봉 트리거 소비 결과 — 게이트 + 분해 입력이 필요로 하는 window 좌표."""

    gate: PriceTrigger
    ticker: str
    trade_date: date
    session_id: str
    window_start: datetime
    generation: int


class WindowCandidate(NamedTuple):
    """오늘 분봉 계보가 이미 성립해 요청창 설명을 매달 수 있는 ETF."""

    instrument_id: str
    ticker: str
    name: str
    route_id: str
    route_code: str


class ExplanationPersistencePlan(NamedTuple):
    """Archive-first handoff: the exact identifiers the DB transaction will use."""

    explanation_as_of: str
    run_id: str
    result_id: str


def minute_observation_id(trigger_id: str) -> str:
    """분봉 트리거 계보의 observation id — trigger_id 결정적 파생(멱등 upsert 재료)."""
    return stable_id("cob", trigger_id)


def minute_route_id(trigger_id: str) -> str:
    """분봉 트리거 계보의 route id — **소비자의 멱등 프리플라이트와 같은 유도식**(ALPHA-719).

    consumer 가 이 함수를 import 해 재배달 판정에 쓴다 — 두 벌로 갈리면 프리플라이트가
    항상 False 라 재배달마다 새 run 이 생기고 LLM 이 재과금된다(조용한 붕괴).
    """
    return stable_id("rte", minute_observation_id(trigger_id))


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

    def try_lock_route(self, route_id: str) -> bool:
        """이 route 를 이 커넥션이 소유하는가 — 재배달 동시 처리 차단(ALPHA-779).

        `has_run_for_route` 프리플라이트는 **처리가 끝나야** 참이 된다. 가시성
        (`PROCESSING_VISIBILITY_SECONDS`)을 넘긴 처리는 SQS 가 재배달하고, 재배달본은
        아직 run 이 없어 프리플라이트를 통과한다 → 같은 트리거에 LLM 이중 과금.
        태스크가 1대여도 샌다 — 순차 처리가 방어막이 아니라 **만료가 순차성을 깬다**.

        세션 락이라 **해제 코드가 없다** — 커넥션에 매달려 `close()` 로 풀린다.
        ⚠️ 커넥션 풀을 도입하면 이 설계가 깨진다(락이 반납된 커넥션을 타고 다음 메시지로
        샌다). 그때는 명시적 `pg_advisory_unlock` 이 필요하다.

        UNIQUE 제약이 아닌 이유: route 당 run 다건은 **의도된 계약**이다(무효화 후 재실행,
        ADR-0045) — 제약을 걸면 daily 재실행이 통째로 막힌다.

        천장: 락은 **커넥션이 살아 있는 동안만** 소유권이다. 처리 중 backend 종료·failover·
        네트워크 단절이 나면 서버는 락을 즉시 풀지만 이 프로세스는 다음 DB 접근 전까지
        모르고 LLM 을 계속 태운다 — 그 창에선 이중 과금이 다시 가능하다. 닫으려면 LLM
        호출 경계마다 소유권 재확인이 필요한데, 이 물량(하루 수십 건)에 그 기계는 과잉이라
        받아들인 천장이다.

        반대편 천장: 프로세스가 **죽지 않고 얼면** 락이 안 풀린다(커넥션이 살아 있어서다).
        그 route 의 재배달은 매번 튕기다 receive 예산을 태우고 DLQ 로 간다 — 그 트리거의
        설명은 수동 복구 전까지 없다. 락 없는 지금이라면 재배달본이 대신 만들었을 것이라,
        이 한 갈래는 교환에서 잃는 쪽이다. 조용한 이중 과금보다 드러나는 DLQ 를 택했다.
        태스크가 죽거나 재시작하면 커넥션이 끊겨 락은 풀린다 — 얼되 살아 있는 경우만이다.
        """
        with self._conn.cursor() as cur:
            # hashtext 는 32비트라 충돌 가능하나 **동시에 쥔 락 사이에서만** 문제라
            # 실질 0 이다(같은 파일의 tenant-delivery-fanout 락이 이미 같은 방식).
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)::bigint)", (route_id,))
            return bool(cur.fetchone()[0])

    def has_run_for_route(self, route_id: str) -> bool:
        """이 route 로 이미 설명 run 이 확정됐는가 — 분봉 소비자의 멱등 프리플라이트(ALPHA-719).

        `explanation_run.run_id` 는 벽시계(`explanation_as_of`)가 재료라 재배달마다 새
        run·result 행이 생기고 LLM 이 재과금된다. route id 는 trigger_id 에서 결정적으로
        유도되므로(`stable_id`) 이 존재 검사가 재배달을 걸러낸다. run 직전 crash 로
        관측·route 만 남은 경우는 False 라 재실행된다(L1 은 ON CONFLICT 멱등 — 안전).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM explanation_run WHERE explanation_route_id = %s LIMIT 1",
                (route_id,),
            )
            return cur.fetchone() is not None

    def plan_explanation(
        self, settings: Settings, etf_instrument_id: str, *, route_id: str,
    ) -> ExplanationPersistencePlan:
        """Plan IDs before the required archive; no database state is changed."""
        explanation_as_of = datetime.now(timezone.utc).isoformat()
        run_id = stable_id(
            "run", etf_instrument_id, settings.trade_date.isoformat(),
            explanation_as_of, route_id,
        )
        return ExplanationPersistencePlan(
            explanation_as_of, run_id, stable_id("res", run_id))

    def window_candidates(self, trade_date: date) -> list[WindowCandidate]:
        """당일 분봉 trigger→observation→route가 모두 있는 ETF의 최신 route."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (i.ticker) i.instrument_id,"
                " regexp_replace(i.ticker, '\\.(KS|KQ)$', ''),"
                " e.display_name, r.explanation_route_id, r.route_code"
                " FROM minute_price_trigger m"
                " JOIN etf_contribution_observation o"
                "   ON o.minute_price_trigger_id = m.trigger_id"
                " JOIN explanation_route r"
                "   ON r.contribution_observation_id = o.contribution_observation_id"
                " JOIN instrument i"
                "   ON regexp_replace(i.ticker, '\\.(KS|KQ)$', '') = m.entity_id"
                " JOIN etf_profile p ON p.instrument_id = i.instrument_id"
                " JOIN entity e ON e.entity_id = i.instrument_id"
                " WHERE (m.window_start AT TIME ZONE 'Asia/Seoul')::date = %s"
                " ORDER BY i.ticker, m.window_start DESC",
                (trade_date,),
            )
            return [WindowCandidate(*map(str, row)) for row in cur.fetchall()]

    def find_published_minute_run_ids(
        self, entity_id: str, session_id: str, until_window_start: datetime,
    ) -> list[str]:
        """그 종목·세션의 **분봉 트리거 기원** 설명 중 PUBLISHED 인 run id 목록(ALPHA-746).

        ExposureReverted 회수 대상 결정. 무효화 API 의 지목 축이 run 이라
        (POST /analyses/{explanation_run_id}/invalidate) run id 를 돌려준다.

        - **EOD 제외의 WHY**: 관측의 트리거 축은 정확히 하나다
          (ck_etf_contribution_one_trigger) — EOD 설명은 price_movement_trigger_id 에,
          분봉 설명은 minute_price_trigger_id 에 매달린다. minute_price_trigger 로의
          INNER JOIN 이 EOD 계보를 구조적으로 떨군다(run_reason 은 두 경로 모두
          'DAILY' 라 분기 축이 못 된다).
        - **당일 한정 = session_id**: 트리거와 회수 사건이 같은 세션 좌표를 나른다
          (와이어 계약) — 날짜 재계산(KST 변환)보다 정확하고 자정 crossing 에 안전하다.
        - **상한 = 회수 사건의 window_start**: 회수는 그 시점 이전 발화만 덮는다.
          지연·재배달된 회수가 세션 전체를 잡으면, 복귀 **이후** 재발화(앵커 리셋,
          ALPHA-745)한 새 설명까지 무효화한다 — 정당하게 노출 중인 설명이 지연 하나로
          내려간다. 트리거의 window_start 는 항상 복귀 window 보다 앞이므로 <= 다.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT run.explanation_run_id"
                " FROM explanation_result res"
                " JOIN explanation_run run"
                "   ON run.explanation_run_id = res.explanation_run_id"
                " JOIN explanation_route rte"
                "   ON rte.explanation_route_id = run.explanation_route_id"
                " JOIN etf_contribution_observation obs"
                "   ON obs.contribution_observation_id = rte.contribution_observation_id"
                " JOIN minute_price_trigger trg"
                "   ON trg.trigger_id = obs.minute_price_trigger_id"
                " WHERE trg.entity_id = %s AND trg.session_id = %s"
                " AND trg.window_start <= %s"
                " AND res.publication_status = 'PUBLISHED'"
                " ORDER BY run.explanation_run_id",
                (entity_id, session_id, until_window_start),
            )
            return [str(row[0]) for row in cur.fetchall()]

    # -- 읽기 --------------------------------------------------------------- #
    def load_entity_index(self) -> dict[str, str]:
        """ticker -> instrument entity_id (KR 시드 전 종목).

        KR MIC(XKRX·XKOS·XKON)로 좁힌다 — instrument 유일성이 (market_code, ticker)라
        전 시장을 dict 로 접으면 타 MIC 동일 ticker 에서 어느 행이 남는지 조회 순서에
        달리고, 구성종목 계보(constituent_instrument_id)가 무관한 시장 종목으로 조용히
        영속된다. 구성종목은 코스닥 포함이라 XKRX 단독이 아니고, KR 안에서 6자리
        단축코드는 전국 유일이다(ALPHA-709).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, instrument_id FROM instrument"
                " WHERE market_code IN ('XKRX', 'XKOS', 'XKON')"
            )
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
                # XKRX 한정 — instrument 유일성이 (market_code, ticker)라 시장 조건
                # 없는 fetchone 은 타 MIC 동일 ticker 에서 비결정적으로 다른 시장
                # instrument 에 계보를 붙인다(분석 대상 ETF 는 전부 XKRX, ALPHA-709)
                " WHERE i.ticker = %s AND i.instrument_type = 'ETF'"
                " AND i.market_code = 'XKRX'",
                (ticker,),
            )
            row = cur.fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def fetch_minute_price_trigger(self, trigger_id: str):
        """분봉 트리거 한 행 소비(ALPHA-709) — ``MinuteTriggerRow`` | None.

        entity_id 가 곧 단축코드(ticker)다 — 판정기(ALPHA-708)의 unit 축과 동일.
        trade_date 는 window_start 의 KST 날짜. observed_return 은 부호 있는
        close/open−1 로 재구성한다 — 행의 change_rate 는 절대값이라 방향이 없다.
        session_id·generation 은 분봉 분해 입력(ALPHA-710)이 그 window artifact 를
        정확히 집게 하는 좌표다.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT trigger_id, entity_id, window_start, open_price, close_price,"
                " change_rate, threshold, detection_policy_version, session_id, generation"
                " FROM minute_price_trigger WHERE trigger_id = %s",
                (trigger_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        open_price = float(row[3])
        observed = (float(row[4]) / open_price - 1) if open_price else None
        window_start = row[2]
        return MinuteTriggerRow(
            gate=PriceTrigger(
                trigger_id=str(row[0]),
                observed_return=observed,
                reason=(
                    f"intraday |close/open-1|={row[5]} >= {row[6]}"
                    f" ({row[7]}, window={window_start.isoformat()})"
                ),
                abs_gate=True,
                rel_gate=False,
            ),
            ticker=str(row[1]),
            trade_date=window_start.astimezone(KST).date(),
            session_id=str(row[8]),
            window_start=window_start,
            generation=int(row[9]),
        )

    def fetch_previous_trigger_window(
        self, entity_id: str, session_id: str, window_start: datetime,
    ) -> datetime | None:
        """같은 대상·세션에서 이 시각 **직전 트리거 행**의 window_start | None.

        트리거 경로에서 설명 구간의 시작이다(ALPHA-854) — 창이 직전 트리거에서
        시작해 이번 트리거 분에서 끝나므로 전부 과거고, 아직 수집 안 된 분을
        기다리지 않는다.

        **종류를 가리지 않는다**(V202608061020 의 "구간 시작 = 가장 최근 행, 종류
        무관"과 같은 축). 그래서 REVERT 행도 구간을 끊는다 — 회수는 그 지점에서
        노출이 바뀌었다는 뜻이라 다음 설명이 그 뒤부터인 것이 맞다. 발화의 **설명
        성공 여부도 보지 않는다**: 실패한 발화를 건너뛰면 같은 구간이 두 번 설명되고
        원장의 발화 축(ALPHA-710)과도 어긋난다.

        UNIQUE(entity_id, session_id, window_start) 인덱스를 그대로 탄다.
        반환은 KST aware — 호출부가 다시 변환하지 않는다.
        """
        entity_id = str(entity_id).strip()
        session_id = str(session_id).strip()
        if not entity_id or not session_id:
            raise ValueError("entity_id·session_id가 비었다")
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT window_start FROM minute_price_trigger"
                " WHERE entity_id = %s AND session_id = %s AND window_start < %s"
                " ORDER BY window_start DESC LIMIT 1",
                (entity_id, session_id, window_start),
            )
            row = cur.fetchone()
        return row[0].astimezone(KST) if row else None

    def fetch_committed_minute_windows(
        self, session_id: str, start: datetime, end: datetime,
    ) -> list[CommittedMinuteWindow]:
        """``[start, end)``의 커밋된 1분 artifact 좌표를 순서대로 반환한다.

        계획 행 자체가 없거나 중간이 비면 원장 불변식 위반이고, ``DUE``/``CLAIMED``는
        정정·수집이 끝나면 낫는 재시도 축이다. DB 예외는 감싸지 않는다 — 연결 장애와
        데이터 부재를 같은 ``PipelineError``로 만들면 운영 처방이 갈리지 않는다.
        """
        session_id = str(session_id).strip()
        if not session_id:
            raise ValueError("session_id가 비었다")
        for value, field in ((start, "start"), (end, "end")):
            if value.tzinfo is None or value.utcoffset() != timedelta(hours=9):
                raise ValueError(f"{field}는 KST(+09:00) aware datetime이어야 한다")
            if value.second or value.microsecond:
                raise ValueError(f"{field}는 분 경계여야 한다")
        if end <= start:
            raise ValueError("end는 start보다 뒤여야 한다")

        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT window_start, window_end, data_status, generation, checksum"
                " FROM minute_ingestion_window"
                " WHERE session_id = %s AND window_start >= %s AND window_start < %s"
                " ORDER BY window_start",
                (session_id, start, end),
            )
            rows = cur.fetchall()

        expected = []
        cursor = start
        while cursor < end:
            expected.append(cursor)
            cursor += timedelta(minutes=1)
        by_start: dict[datetime, tuple] = {}
        for row in rows:
            window_start = row[0].astimezone(KST)
            if window_start in by_start:
                raise PipelineError(
                    f"분봉 원장 window 중복: session={session_id} window={window_start.isoformat()}")
            by_start[window_start] = row

        missing = [value for value in expected if value not in by_start]
        if missing:
            sample = ",".join(value.strftime("%H:%M") for value in missing[:5])
            raise PipelineError(
                f"분봉 원장 window 누락: session={session_id} count={len(missing)} sample={sample}")

        out: list[CommittedMinuteWindow] = []
        for expected_start in expected:
            row = by_start[expected_start]
            status, generation, checksum = str(row[2]), int(row[3]), row[4]
            if status in ("DUE", "CLAIMED"):
                raise ReturnsNotReadyError(
                    f"분봉 window 정정·수집 중: session={session_id}"
                    f" window={expected_start.isoformat()} status={status}")
            if status not in ("VALID", "VALID_EMPTY", "INCOMPLETE", "MISSING", "INVALID"):
                raise PipelineError(
                    f"분봉 원장 status 미지: session={session_id}"
                    f" window={expected_start.isoformat()} status={status}")
            if generation < 1 or not checksum:
                raise PipelineError(
                    f"분봉 terminal window 커밋 좌표 없음: session={session_id}"
                    f" window={expected_start.isoformat()} status={status}"
                    f" generation={generation} checksum={bool(checksum)}")
            try:
                out.append(CommittedMinuteWindow(
                    session_id=session_id,
                    start=expected_start,
                    end=row[1].astimezone(KST),
                    generation=generation,
                    checksum=str(checksum),
                ))
            except ValueError as error:
                raise PipelineError(
                    f"분봉 원장 window 계약 위반: session={session_id}"
                    f" window={expected_start.isoformat()} — {error}") from error
        return out

    def fetch_event_contexts(self, trade_date: date, tickers: list[str]) -> list[EventContext]:
        """파이프라인이 조립한 당일 구성종목 source event 를 참여자·측정값까지 붙여 읽는다.

        사건 선별(NEWS·ACTIVE·holdings 접지)과 문맥 수집(참여자 전원·측정값 전부)을
        분리한 3쿼리다 — 단일 조인을 DISTINCT ON 으로 붕괴시키면 사건당 참여자 1명만
        남는다(v4 온톨로지 이전의 손실). DISTINCT ON 은 이제 evidence fanout 방어만 한다
        (TITLE evidence 는 assertion 별로 여럿일 수 있다). 신규 온톨로지 컬럼
        (predicate_code·slot 등)은 백필 전 NULL 이어도 동작한다.

        **스니펫(lead_text)** 은 evidence→assertion→document→news_document 로 잇는다.
        제목만으로는 사건의 내용(금액·상대·조건)이 프롬프트에 닿지 않는다 — 측정값이
        붙어도 서술 맥락이 없으면 LLM 이 제목을 재진술하는 데 그친다. 조인은 전부 PK
        1:1 이라 DISTINCT ON 이 방어하는 fanout 을 늘리지 않는다. 백필 전에는 NULL 이다.
        """
        head_sql = (
            "SELECT DISTINCT ON (se.source_event_id)"
            " se.source_event_id, se.event_type_code, se.available_at,"
            " se.predicate_code, se.lifecycle_stage,"
            " etl.thread_id, etl.novelty_status, ev.evidence_text, ev.evidence_id, nd.lead_text"
            " FROM source_event se"
            " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
            " LEFT JOIN event_evidence ev ON ev.source_event_id = se.source_event_id"
            " AND ev.evidence_type = %s"
            " LEFT JOIN document_assertion da ON da.assertion_id = ev.assertion_id"
            " LEFT JOIN news_document nd ON nd.document_id = da.document_id"
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
                lead_text=h[9],
            ))
        return contexts

    def explanation_prerequisites(
        self, settings: Settings, etf_instrument_id: str
    ) -> dict[str, Any]:
        """explanation_result 의 FK 전제: profile 존재·route id·bundle.

        route 조회는 **입력 축을 따라간다** — 분봉 실행(settings.trigger_id)의 계보는
        minute_price_trigger_id 에 매달리므로, 일 단위 (etf, trade_date) 조인으로
        찾으면 없거나(전제 누락으로 런 실패) 같은 날의 **다른** 일 단위 트리거 route
        가 잡혀 남의 계보에 영속된다(ALPHA-709 리뷰 실측).
        """
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM etf_profile WHERE instrument_id = %s", (etf_instrument_id,))
            has_profile = cur.fetchone() is not None
            if getattr(settings, "trigger_id", None):
                cur.execute(
                    "SELECT er.explanation_route_id FROM explanation_route er"
                    " JOIN etf_contribution_observation o"
                    " ON o.contribution_observation_id = er.contribution_observation_id"
                    " WHERE o.minute_price_trigger_id = %s LIMIT 1",
                    (settings.trigger_id,),
                )
            else:
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
        *,
        minute: bool = False,
    ) -> dict[str, str]:
        """소비한 트리거에서 파생한 L1/route 계보를 적재한다(트리거 insert 없음).

        ``minute`` 이면 계보가 분봉 축(minute_price_trigger_id)에 매달린다 — 두 축은
        정확히 하나만 값을 갖는다(ck_etf_contribution_one_trigger, ALPHA-709).
        """
        from psycopg2.extras import execute_values

        detected_at = utcnow_iso()
        # 계보 id 는 소비한 trigger_id 에서 파생 — DB 에 있는 그 행에 계보가 매달린다.
        obs_id = minute_observation_id(trigger_id)
        route_id = minute_route_id(trigger_id)
        contribution_sum = (
            sum(m.contribution for m in decomp.members) if decomp.members else None
        )
        with self._conn.cursor() as cur:
            trigger_column = (
                "minute_price_trigger_id" if minute else "price_movement_trigger_id"
            )
            cur.execute(
                "INSERT INTO etf_contribution_observation (contribution_observation_id,"
                f" {trigger_column}, etf_return, nav_return,"
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
        run_reason: str = "DAILY",
        publishable: bool = True,
        plan: ExplanationPersistencePlan | None = None,
    ) -> dict[str, str | int | None]:
        """explanation_run + explanation_result + 근거 lineage를 한 트랜잭션으로 적재한다."""
        import json

        from psycopg2.extras import execute_values

        # 마이크로초 정밀(utcnow_iso 의 초 해상도 대신) — as_of 는 게시 grain 부분
        # 유니크(uq_explanation_result_published_grain)의 축이라, 초 해상도면 같은 초에
        # 끝난 서로 다른 발화 둘이 모두 PUBLISHED 를 타며 충돌해 두 번째 INSERT 가
        # 터진다(ON CONFLICT 는 PK 만 커버). route 축 정책(ALPHA-710)에선 둘 다 게시가
        # 맞다 — 정밀도로 충돌을 없앤다(정확 일치는 유니크가 fail-loud 백스톱).
        plan = plan or self.plan_explanation(
            settings, etf_instrument_id, route_id=route_id)
        explanation_as_of = plan.explanation_as_of
        event_count = len(events)
        run_id = plan.run_id
        result_id = plan.result_id
        # **NUL(0x00) 소거 - Postgres TEXT·JSONB 는 NUL 을 원리적으로 못 담는다.**
        # 벤더 원문(뉴스 제목·리드)에서 흘러온 제어문자가 산문·stage_results 에 실리면
        # 이 INSERT 가 통째로 죽는다(08-07 재실행 실측: 직전 거래일 사건 문맥 확장이
        # NUL 든 원문을 처음 밟았다 - ALPHA-885). 원문은 레이크에 그대로 있으므로
        # 영속 경계에서 지우는 것이 정보 손실이 아니다.
        def _scrub(value):
            if isinstance(value, str):
                return value.replace("\x00", "")
            if isinstance(value, dict):
                return {k: _scrub(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_scrub(v) for v in value]
            return value

        raw = _scrub(dict(explanation.raw))
        stage = raw.pop("stage_results", {}) or {}
        stage_results = json.dumps(
            {"events": event_count, **stage, "raw": raw}, ensure_ascii=False
        )
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO explanation_run (explanation_run_id, explanation_route_id,"
                " bundle_version, explanation_as_of, run_reason, run_status, finished_at)"
                " VALUES (%s,%s,%s,%s,%s,'SUCCEEDED',now())"
                " ON CONFLICT (explanation_run_id) DO NOTHING",
                (run_id, route_id, bundle, explanation_as_of, run_reason),
            )
            # 게시 게이트·cursor 채번 직렬화(ALPHA-493) — analyze 동시 실행이 같은 날
            # PUBLISHED 를 이중 게시하거나 같은 테넌트 cursor 를 겹쳐 뽑지 못하게 전역 락
            # 하나로 묶는다. fan-out 은 매번 전 테넌트 대상이라 테넌트별 락은 이득이 없다.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext('tenant-delivery-fanout')::bigint)"
            )
            # 발화(route)당 첫 결과만 PUBLISHED — 게이트 축은 트리거다(ALPHA-710 게시
            # 정책). 분봉 트리거는 하루 여러 번 발화할 수 있고 발화마다 게시된다 —
            # 같은 날 다건 PUBLISHED 는 서빙층이 최근 게시 시각 우선으로 흡수한다
            # (publication-api findPublishedOn/findLatestPublished). 같은 route 재실행은
            # DRAFT 보존이다 — explanation_as_of 가 런마다 새로워 grain 부분 유니크만으론
            # 이중 게시를 못 막는다. EXISTS 가 PUBLISHED 만 보는 이유: 무효화(WITHDRAWN,
            # super-admin-api 무효화 액션 — ALPHA-440)로 게시본이 사라진 발화는 재실행 시
            # 새로 게시된다(ADR-0045 발번 정책).
            cur.execute(
                "INSERT INTO explanation_result (explanation_result_id, explanation_run_id,"
                " etf_instrument_id, trade_date, explanation_as_of, primary_thread_id,"
                " explanation_type, summary, confidence_level, stage_results,"
                " publication_status, headline)"
                " SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                # `%s = FALSE` 는 **표면 부재**다. 게시 규칙("발화당 첫 결과가 게시본")은
                # "첫 결과가 그 발화의 최선"을 전제하는데, 내용 없는 결과가 먼저 오면 그
                # 전제가 깨진다 - 자리를 선점하고 재실행분을 DRAFT 로 밀어낸다. 무효화된
                # 게시본이 자리를 안 지키는 것과 결이 같다(ADR-0045): **내용 없는 결과도
                # 자리를 지킬 이유가 없다.** DRAFT 로 떨어지면 아래 fan-out 게이트가
                # PUBLISHED 만 보므로 테넌트 전파도 함께 멈춘다.
                " CASE WHEN EXISTS (SELECT 1 FROM explanation_result p"
                "   JOIN explanation_run r ON r.explanation_run_id = p.explanation_run_id"
                "   WHERE r.explanation_route_id = %s"
                "     AND p.publication_status = 'PUBLISHED')"
                "   OR %s = FALSE"
                " THEN 'DRAFT' ELSE 'PUBLISHED' END, %s"
                " ON CONFLICT (explanation_result_id) DO NOTHING"
                " RETURNING publication_status",
                (
                    result_id, run_id, etf_instrument_id, settings.trade_date.isoformat(),
                    explanation_as_of, primary_thread_id, explanation.explanation_type,
                    _scrub(explanation.summary), explanation.confidence_level, stage_results,
                    route_id, publishable,
                    _scrub(explanation.headline),
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
            # 커서에 노출된다(sync-protocol). 여기는 NEW 만 발번 — INVALIDATION 은
            # super-admin-api 무효화 액션이 같은 advisory lock 을 잡고 발번한다(ALPHA-440).
            # CORRECTION 은 폐지(ADR-0044).
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
            # DRAFT 사유가 둘이다 — 같은 발화 재실행(게시분이 이미 있다)과 표면 부재
            # (내용 없는 결과라 자리를 안 준다). 한 사유로 뭉치면 "왜 안 나갔나"에
            # 못 답한다(Rule 12 — 부재를 사유와 함께 드러낸다).
            log(
                "explanation_result.publish_skipped",
                reason="route_already_published" if publishable else "surface_absent",
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

    def persist_hypothesis_trials(
        self,
        rows: list[dict[str, Any]],
        *,
        minute_price_trigger_id: str,
        trade_date: date,
        ticker: str,
        explanation_run_id: str | None = None,
    ) -> int:
        """가설 원장(hypothesis_trial) upsert — 단일 writer 는 이 리포지토리다(ADR-0005).

        trial_id 는 (trigger_id, stage, 정체성)의 결정적 해시다. 정체성은 TESTED 면
        튜플 슬롯(방아쇠·채널·노출·층·조건), REJECTED 면 기각 사유 원문 — 같은 트리거
        재실행이 같은 id 로 수렴해 중복 행이 쌓이지 않는다(멱등 축 = PK).
        (trigger, run) UNIQUE 축을 안 쓴 이유: run_id 는 벽시계 재료라 재배달·재실행마다
        새 값이고 그 축은 재실행마다 행을 쌓는다. DO UPDATE 라 재실행이 판정·통계·run
        연결을 최신으로 덮는다(정정 후 재실행이 낡은 판정을 남기지 않는다).

        같은 배치 안의 동일 id 는 마지막 것만 남긴다 — ON CONFLICT DO UPDATE 는 한
        문장에서 같은 행을 두 번 못 건드린다(cannot affect row a second time).
        실패는 롤백 후 그대로 올린다 — 원장 실패로 런을 죽일지는 호출부가 정한다.
        """
        import json

        from psycopg2.extras import Json, execute_values

        if not rows:
            return 0
        by_id: dict[str, tuple] = {}
        for row in rows:
            stage = str(row.get("stage", ""))
            if stage == "TESTED":
                identity = json.dumps(
                    {k: row.get(k) for k in
                     ("trigger_slot", "channel", "exposure", "layer", "conditions")},
                    ensure_ascii=False, sort_keys=True)
            else:
                identity = str(row.get("reason") or "")
            trial_id = stable_id("hyt", minute_price_trigger_id, stage, identity)
            conditions = row.get("conditions")
            by_id[trial_id] = (
                trial_id, minute_price_trigger_id, explanation_run_id,
                trade_date.isoformat(), ticker, stage,
                row.get("trigger_slot"), row.get("channel"), row.get("exposure"),
                row.get("layer"),
                Json(conditions) if conditions is not None else None,
                row.get("verdict"), row.get("applies_today"),
                row.get("n"), row.get("p"),
                row.get("effect_low"), row.get("effect_high"),
                str(row.get("reason") or ""),
            )
        try:
            with self._conn.cursor() as cur:
                execute_values(
                    cur,
                    "INSERT INTO hypothesis_trial (trial_id, minute_price_trigger_id,"
                    " explanation_run_id, trade_date, ticker, stage, trigger_slot,"
                    " channel, exposure, layer, conditions, verdict, applies_today,"
                    " n, p, effect_low, effect_high, reason) VALUES %s"
                    " ON CONFLICT (trial_id) DO UPDATE SET"
                    " explanation_run_id = EXCLUDED.explanation_run_id,"
                    " verdict = EXCLUDED.verdict,"
                    " applies_today = EXCLUDED.applies_today,"
                    " n = EXCLUDED.n, p = EXCLUDED.p,"
                    " effect_low = EXCLUDED.effect_low,"
                    " effect_high = EXCLUDED.effect_high,"
                    " reason = EXCLUDED.reason",
                    list(by_id.values()),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return len(by_id)

    def persist_evidence_rows(
        self,
        build,
        *,
        explanation_result_id: str,
        explanation_run_id: str | None = None,
    ) -> int:
        """근거 행(explanation_evidence_row, v3) upsert — 단일 writer (ALPHA-888).

        ``build`` 는 `statics.evidence_rows.EvidenceBuild` 다 — §5 게이트를 이미
        통과한 행만 담겨 있다(통과 못 한 검정은 행 자체가 없다). 멱등 축은
        결정적 id `stable_id('evr', result_id, ref)` 다: 같은 결과 재적재(재배달)가
        같은 행으로 수렴하고, DO UPDATE 가 내용을 최신으로 덮는다.

        `persist_explanation` 트랜잭션 **직후**에 부른다 — 합류시키지 않는 이유:
        설명 영속은 근거 원장 결손으로 죽으면 안 되고(설명이 정본, 근거는 파생),
        결손은 재실행이 멱등으로 메운다. 실패는 롤백 후 그대로 올린다 — 로그로
        드러낼지(런 지속) 죽일지는 호출부가 정한다(hypothesis_trial 과 같은 계약).
        """
        from psycopg2.extras import Json, execute_values

        if not build.rows:
            return 0
        values = []
        for row in build.rows:
            row_id = stable_id("evr", explanation_result_id, row.ref)
            rec = build.stat_records.get(row.ref)
            if rec is not None:
                # STAT_TEST — content 는 저장하지 않는다(§0 완성 문장 금지). 문형은
                # template+slots 가 렌더 시 조립한다. effect_low/high 는 칼만 배선
                # (ALPHA-803) 전이라 NULL 이다.
                values.append((
                    row_id, explanation_result_id, explanation_run_id, row.ref,
                    "STAT_TEST", None, row.source, None,
                    rec.template, rec.basis, rec.method,
                    Json(rec.slots), rec.n, rec.unit, rec.estimate, rec.p, rec.k,
                    rec.band, Json(list(rec.series)), None, None, rec.null_kind,
                ))
            else:
                values.append((
                    row_id, explanation_result_id, explanation_run_id, row.ref,
                    row.type, row.content, row.source, row.time or None,
                    None, None, None, None, None, None, None, None, None,
                    None, None, None, None, None,
                ))
        try:
            with self._conn.cursor() as cur:
                execute_values(
                    cur,
                    "INSERT INTO explanation_evidence_row (evidence_row_id,"
                    " explanation_result_id, explanation_run_id, ref, evidence_type,"
                    " content, source, observed_at, template, basis, method, slots,"
                    " n, unit, estimate, p, k, band, series, effect_low, effect_high,"
                    " null_kind) VALUES %s"
                    " ON CONFLICT (evidence_row_id) DO UPDATE SET"
                    " explanation_run_id = EXCLUDED.explanation_run_id,"
                    " evidence_type = EXCLUDED.evidence_type,"
                    " content = EXCLUDED.content,"
                    " source = EXCLUDED.source,"
                    " observed_at = EXCLUDED.observed_at,"
                    " template = EXCLUDED.template, basis = EXCLUDED.basis,"
                    " method = EXCLUDED.method, slots = EXCLUDED.slots,"
                    " n = EXCLUDED.n, unit = EXCLUDED.unit,"
                    " estimate = EXCLUDED.estimate, p = EXCLUDED.p,"
                    " k = EXCLUDED.k, band = EXCLUDED.band,"
                    " series = EXCLUDED.series,"
                    " effect_low = EXCLUDED.effect_low,"
                    " effect_high = EXCLUDED.effect_high,"
                    " null_kind = EXCLUDED.null_kind",
                    values,
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return len(values)

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
