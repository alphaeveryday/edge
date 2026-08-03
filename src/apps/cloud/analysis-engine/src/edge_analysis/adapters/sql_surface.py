"""자유 SQL 표면 — **실험판(`experiments/storm/src/storm/tools.py`)의 이식.**

고정 함수 6개(`cohort`·`universe`·`ar`·`mom`·`vol`·`prior`)는 검정 단계에는 맞지만
**가설을 세우는 단계에는 맞지 않는다.** 무엇을 물어야 할지 모르는 상태에서 물음의
모양을 미리 정해 주면, 모델은 답을 찾는 대신 주어진 함수로 표현 가능한 답만 찾는다.
실험판이 자유 SELECT 를 준 이유가 그것이고, 여기서 그 표면을 클라우드 원장으로 옮긴다.

**실험판보다 강한 점 하나.** storm 은 `pit()` 헬퍼를 주고 모델이 붙이기를 기대했다.
여기서는 **뷰 자체를 시점으로 고정한다** - `as_of` 클램프가 CTE 안에 있으므로 모델이
무엇을 쓰든 미래를 볼 수 없다. 잊을 수 있는 규칙을 없앤 자리다.

    storm v_event        -> v_event         (source_event + event_argument)
    storm v_daily        -> v_daily         (price_daily 파생 수익률·초과수익)
    storm v_hold         -> v_hold          (etf_holding_snapshot)
    storm v_entity       -> v_instrument    (instrument + instrument_classification)
    storm bars()         -> 없음            (분봉 원장 미보유 - SCHEMA 에 명시한다)

원장에 **없는 것을 없다고 적는 것**이 이 표면의 절반이다. 없는 축을 침묵으로 두면
모델이 지어내고, 지어낸 것은 `available_at` 클램프를 지나지 않는다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..config import PipelineError

MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 20_000

# 기반 테이블 직접 접근 금지. **클램프된 뷰를 우회하는 유일한 경로다.**
_BASE_TABLES = (
    "source_event", "event_argument", "event_measure", "event_evidence",
    "event_thread_link", "document_assertion", "news_document",
    "instrument", "instrument_classification", "entity",
    "price_daily", "etf_holding_snapshot", "etf_profile", "price_movement_trigger",
    "explanation_result", "explanation_route", "release_bundle", "tenant",
    "investor_flow_daily",
)
_BANNED = re.compile(
    r"(--|/\*|\*/)"
    r"|\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|copy|call|do|"
    r"vacuum|analyze|explain|listen|notify|lock|prepare|execute|declare|fetch|move|"
    r"set_config|current_setting|pg_sleep|pg_read_file|pg_ls_dir|lo_import|lo_export|"
    r"dblink|postgres_fdw)\b",
    re.I,
)
_BASE_HIT = re.compile(r"\b(" + "|".join(_BASE_TABLES) + r")\b", re.I)

SCHEMA = """단일 SQL 표면 (PostgreSQL). **SELECT 하나만.** 조회 키는 instrument_id 다.

시점은 코드가 이미 박아 뒀다 - 아래 뷰는 전부 `available_at <= AS_OF` 로 잘려 있다.
미래를 보려 해도 볼 수 없으니 PIT 절을 직접 쓸 필요가 없다.

  v_event          source_event_id · instrument_id · role_code · trade_date · available_at
                   event_type_code · predicate_code · lifecycle_stage · event_status
                   title · thread_id · novelty_status
                   사건 × 등장 엔티티. 다종목 사건은 여러 행이다
  v_measure        source_event_id · role_code · value · unit · basis · value_source · surface
                   사건의 수치 역할인자. 앵커(사건이 실제로 얼마였나)의 출처
  v_instrument     instrument_id · ticker · display_name · instrument_type
                   sector_name · industry_name · market_cap · listing_market
                   분류는 PIT 상 최신 1건
  v_daily          instrument_id · trade_date · close_price · volume
                   r(단순수익률) · ar(횡단면 평균 대비 초과수익) · mkt(그날 횡단면 평균)
                   초과수익 기준선은 **당일 횡단면 평균**이다 (설명 프레임워크 §0)
  v_hold          etf_instrument_id · constituent_instrument_id · trade_date · weight_ratio
  v_flow          instrument_id · trade_date · 투자자 유형별 **순매수 대금·수량**
                  net_val_individual(개인) · net_val_foreign(외국인)
                  net_val_institution_total(기관계) · net_val_pension(연기금)
                  net_val_investment_trust(투신) · net_val_financial_invest(증권)
                  net_val_private_fund(사모) · net_val_insurance(보험) · net_val_bank(은행)
                  순매수는 음수가 정상이다. grain 은 **개별주식** - ETF 자체가 아니라
                  구성종목의 수급이다. 기관 세부는 결측 가능(개인·외국인·기관계는 아니다)
  v_liquidity     instrument_id · trade_date · turnover_value · illiq
                  illiq = |r| / 거래대금 × 1e12 (Amihud 2002 비유동성). 수준이 아니라
                  **기준창 대비 차이**로 읽어라 - 절대값은 종목마다 자릿수가 다르다
  v_cohort        v_event × v_instrument 를 미리 조인한 표면.
                  instrument_id · trade_date · source_event_id · event_type_code
                  predicate_code · role_code · lifecycle_stage · sector_name
                  industry_name · market_cap · listing_market · ticker

원장에 **없는 것** - 물어봐야 소용없고, 필요하면 그 사실을 산출물에 적어라:
  · 분봉·틱 (장중 타이밍 검정 불가 - 사건 시각 대비 반응 시각을 못 가른다)
  · 고가·저가 (`price_daily` 는 종가만. 따라서 high-low 스프레드 추정량·정적 VI 판정 불가)
  · 호가·스프레드·깊이 (거래량은 있으나 **잔여 유동성은 없다** - 둘은 다른 변수다)
  · 공매도·대차·신용 잔고 (단 공매도 *규제 구간*은 제도 사실이라 알려져 있다)
  · 합성 캘린더 (만기·리밸런스 효력일·락업 해제)
  · 교차자산 (금리·환율·변동성지수)

함정 - 집계 전에 읽어라:
  · v_daily.r 은 직전 **거래일** 종가 대비다. 결측일은 건너뛴다
  · v_daily.ar 은 그날 횡단면에 50종목 이상 있을 때만 존재한다. 그리고 이것은 β=1·α=0 을
    강제한 시장조정 수익률이다 - 고베타 ETF 는 시장이 크게 움직인 날 잔차가 부풀려진다
  · v_event 는 사건당 여러 행(등장 엔티티 수만큼). 건수 집계 시 DISTINCT 를 써라
  · lifecycle_stage 는 RUMORED/CONFIRMED 등으로 갈린다. 섞으면 사건 정의가 흐려진다
  · |r| ≥ 0.299 는 상하한가다(2015-06-15~, ETF 포함). 관측이 절단됐다는 뜻이므로
    그날 예산은 상한이 아니라 **하한**이다
  · 결과는 최대 {max_rows}행. 큰 질의는 집계로 줄여라"""


def _guard(q: str) -> str:
    """실행 전 검사. **통과 못 하면 관측이 아니라 거부다.**"""
    s = (q or "").strip().rstrip(";").strip()
    if not s:
        raise PipelineError("빈 질의다.")
    if ";" in s:
        raise PipelineError("문장은 하나만. 세미콜론으로 이어 붙일 수 없다.")
    if not re.match(r"(?is)^\s*select\b", s):
        raise PipelineError(
            "SELECT 로 시작해야 한다. CTE 가 필요하면 서브쿼리를 써라 "
            "(WITH 는 표면 정의에 이미 쓰였다).")
    hit = _BANNED.search(s)
    if hit:
        raise PipelineError(f"쓸 수 없는 토큰: {hit.group()!r}. 읽기 전용 SELECT 만 받는다.")
    base = _BASE_HIT.search(s)
    if base:
        raise PipelineError(
            f"기반 테이블 {base.group()!r} 직접 접근 금지 - 시점 클램프를 우회한다. "
            "v_event · v_measure · v_instrument · v_daily · v_hold · v_flow · "
            "v_liquidity · v_cohort 를 써라.")
    # psycopg2 가 `%` 를 자기 플레이스홀더로 읽는다. as_of 가 항상 파라미터로 붙으므로
    # 보간이 언제나 일어나고, 따라서 이 이스케이프도 언제나 필요하다 (causal_data 와 동일).
    return s.replace("%", "%%")


def _views(as_of: str = "%(as_of)s", trade_date: str = "%(trade_date)s",
           prefix: str = "") -> str:
    """시점으로 잘린 뷰 정의. **클램프가 여기 있으므로 질의가 우회할 수 없다.**

    방언 파라미터 (18R): 인과 패널(`statics/*`)이 DuckDB 세션에서 **같은 표면**을
    쓰도록 시점 표현식과 테이블 접두사를 인자로 받는다. 표면이 둘로 갈리면 한쪽만
    클램프가 빠지고, 그게 정확히 이 파일이 막으려던 사고다.
      postgres  _views()                                    → %(as_of)s · 접두사 없음
      duckdb    _views("TIMESTAMP '…'", "DATE '…'", "rdb.public.")
    """
    return f"""
    _cls AS (
        SELECT DISTINCT ON (ic.instrument_id)
               ic.instrument_id, ic.sector_name, ic.industry_name,
               ic.market_cap, ic.listing_market
        FROM {prefix}instrument_classification ic
        WHERE ic.as_of_date <= {trade_date}
        ORDER BY ic.instrument_id, ic.as_of_date DESC
    ),
    v_instrument AS (
        SELECT i.instrument_id, i.ticker, i.instrument_type, e.display_name,
               c.sector_name, c.industry_name, c.market_cap, c.listing_market
        FROM {prefix}instrument i
        LEFT JOIN {prefix}entity e ON e.entity_id = i.instrument_id
        LEFT JOIN _cls c ON c.instrument_id = i.instrument_id
    ),
    _title AS (
        -- 제목은 `source_event` 에 없다 — 근거 문서 lineage 에 있다(evidence_type='TITLE').
        -- 사건당 1건으로 접어서 붙인다: 그냥 조인하면 v_event 가 근거 수만큼 부풀고
        -- 사건 건수 집계가 조용히 틀어진다. 선택 규칙은 eventstore.fetch_event_contexts
        -- 와 같다(evidence_id 최소) — 두 경로가 같은 제목을 보여야 한다.
        SELECT DISTINCT ON (ev.source_event_id)
               ev.source_event_id, ev.evidence_text AS title
        FROM {prefix}event_evidence ev
        WHERE ev.evidence_type = 'TITLE'
        ORDER BY ev.source_event_id, ev.evidence_id
    ),
    v_event AS (
        SELECT se.source_event_id, ea.entity_id AS instrument_id, ea.role_code,
               se.event_date AS trade_date, se.available_at,
               se.event_type_code, se.predicate_code, se.lifecycle_stage,
               se.event_status, t.title,
               etl.thread_id, etl.novelty_status
        FROM {prefix}source_event se
        JOIN {prefix}event_argument ea ON ea.source_event_id = se.source_event_id
        LEFT JOIN {prefix}event_thread_link etl ON etl.source_event_id = se.source_event_id
        LEFT JOIN _title t ON t.source_event_id = se.source_event_id
        WHERE se.event_status = 'ACTIVE'
          AND se.event_date IS NOT NULL
          AND se.available_at <= {as_of}
    ),
    v_measure AS (
        -- 컬럼 이름은 `event_measure` 실물이다(V202607242020). `measure_code`·
        -- `measure_value`·`unit_code` 는 존재한 적이 없어 이 뷰를 쓰는 질의가 전부
        -- UndefinedColumn 으로 죽었다 - 뷰 하나가 P1 지문 축과 P7 스크린을 같이 무너뜨린다.
        -- 이름은 도메인 모델(`Measure`)과 eventstore 조회에 맞춘다.
        SELECT em.source_event_id, em.role_code, em.value, em.unit, em.basis,
               em.value_source, em.surface
        FROM {prefix}event_measure em
        JOIN {prefix}source_event se ON se.source_event_id = em.source_event_id
        WHERE se.available_at <= {as_of}
    ),
    _ret AS (
        -- lr: 파이프라인 log_return 이 SSOT, 없으면 수정종가→종가 순으로 유도한다
        -- (최근 5,803행이 NULL 이라 유도 없이는 최근일 패널이 통째로 빈다).
        SELECT instrument_id, trade_date, close_price, volume,
               close_price / NULLIF(LAG(close_price) OVER w, 0) - 1 AS r,
               coalesce(log_return,
                        ln(adjusted_close_price / NULLIF(LAG(adjusted_close_price) OVER w, 0)),
                        ln(close_price / NULLIF(LAG(close_price) OVER w, 0))) AS lr
        FROM {prefix}price_daily
        WHERE close_price > 0 AND trade_date <= {trade_date}
        WINDOW w AS (PARTITION BY instrument_id ORDER BY trade_date)
    ),
    _xs AS (
        SELECT trade_date, avg(r) AS mkt, avg(lr) AS mkt_lr
        FROM _ret WHERE r IS NOT NULL
        GROUP BY trade_date HAVING count(*) >= 50
    ),
    _mkt AS (
        SELECT t.instrument_id, t.trade_date, t.close_price, t.volume, t.r, t.lr,
               x.mkt, t.r - x.mkt AS ar, t.lr - x.mkt_lr AS ar_lr, c.industry_name
        FROM _ret t
        LEFT JOIN _xs x ON x.trade_date = t.trade_date
        LEFT JOIN _cls c ON c.instrument_id = t.instrument_id
    ),
    v_daily AS (
        -- ar_ind: 시장 차감 뒤 **산업층 재차감** (산업 표본 ≥5 일 때만). 등가중 시장
        -- 차감만으로는 산업 공행이 잔차에 남아 사건연구의 횡단면 독립이 깨진다.
        SELECT m.*,
               CASE WHEN m.industry_name IS NOT NULL AND count(*) OVER di >= 5
                    THEN m.ar_lr - avg(m.ar_lr) OVER di ELSE m.ar_lr END AS ar_ind
        FROM _mkt m
        WINDOW di AS (PARTITION BY m.trade_date, m.industry_name)
    ),
    v_hold AS (
        SELECT etf_instrument_id, constituent_instrument_id, trade_date, weight_ratio
        FROM {prefix}etf_holding_snapshot
        WHERE trade_date <= {trade_date}
    ),
    v_flow AS (
        SELECT instrument_id, trade_date,
               net_val_individual, net_val_foreign, net_val_institution_total,
               net_val_pension, net_val_investment_trust, net_val_financial_invest,
               net_val_private_fund, net_val_insurance, net_val_bank,
               net_qty_individual, net_qty_foreign, net_qty_institution_total
        FROM {prefix}investor_flow_daily
        WHERE trade_date <= {trade_date} AND available_at <= {as_of}
    ),
    v_pit AS (
        -- curated DataGuide 특정시점 스냅샷(statics.pit → pit_daily 백필).
        -- **available_at 클램프가 없다** - 원본이 trade_date 별 PIT 스냅샷이라
        -- 파티션 키가 곧 클램프다. 그날 관측 가능했던 값만 그 행에 들어 있다.
        -- 주주(외국인지분율) · 신용(융자잔고율) · 공매도(잔고비율) · 배수(PBR) ·
        -- 주식수(상장주식수) - 계열족당 하나씩만 싣는다(어휘지 열 목록이 아니다).
        SELECT i.instrument_id, p.trade_date,
               p.foreign_ratio, p.credit_ratio, p.short_ratio,
               p.pbr, p.shares, p.mktcap
        FROM pit_daily p
        JOIN v_instrument i ON i.ticker = p.ticker
        WHERE p.trade_date <= {trade_date}
    ),
    v_liquidity AS (
        SELECT instrument_id, trade_date, turnover_value,
               CASE WHEN turnover_value > 0 AND r IS NOT NULL
                    THEN abs(r) / turnover_value * 1e12 END AS illiq
        FROM (
            SELECT p.instrument_id, p.trade_date, p.turnover_value,
                   p.close_price / NULLIF(LAG(p.close_price) OVER (
                       PARTITION BY p.instrument_id ORDER BY p.trade_date), 0) - 1 AS r
            FROM {prefix}price_daily p
            WHERE p.close_price > 0 AND p.trade_date <= {trade_date}
        ) _a
    ),
    v_entity AS (
        -- 엔티티 원장. 상장사만이 아니라 인물·기관·제품까지 - 역할 인자가 가리키는 대상.
        SELECT e.entity_id, e.entity_type, e.display_name, e.status
        FROM {prefix}entity e WHERE e.status <> 'MERGED'
    ),
    v_thread AS (
        -- 사건 스레드: 같은 사태의 연속 보도를 하나로 묶은 것. novelty 가 여기 있다
        -- (신규 보도인가 후속인가) - PIT: 평가 시각이 as_of 이전인 링크만.
        SELECT tl.source_event_id, tl.thread_id, tl.novelty_status, tl.link_type,
               t.thread_key, t.event_type_code, t.current_stage, t.opened_at
        FROM {prefix}event_thread_link tl
        JOIN {prefix}event_thread t ON t.thread_id = tl.thread_id
        WHERE tl.evaluated_at <= {as_of}
    ),
    v_news AS (
        -- 뉴스 원장. available_at 이 곧 정보 도달 시각 - 클램프의 본체다.
        SELECT d.document_id, d.source_code, d.source_document_id, d.title,
               d.published_at, d.available_at, n.lead_text, n.dedup_cluster_id,
               n.theme_concept_id, n.representative_document_id
        FROM {prefix}document d
        LEFT JOIN {prefix}news_document n ON n.document_id = d.document_id
        WHERE d.available_at <= {as_of}
    ),
    v_event_news AS (
        -- 사건 ↔ 근거 문서. '무엇을 보고 그 사건이라 했나'의 연결 - 접지의 뿌리.
        SELECT DISTINCT ev.source_event_id, da.document_id
        FROM {prefix}event_evidence ev
        JOIN {prefix}document_assertion da ON da.assertion_id = ev.assertion_id
    ),
    v_link AS (
        -- **객체 타입 간 1홉** (19R). ObjectSet DSL 은 data-pipeline 앱에 있고 이
        -- 워크트리에 없다 - 여기서는 그 *개념*(타입 있는 관계 + 역할 방향 + PIT 홉)을
        -- 공유 뷰로 구현한다. 같은 사건에 **서로 다른 역할**로 등장한 두 상장사가
        -- 한 간선이다. 실측 쌍: 공급망 57 · 제휴 117 · 지분 10 · 공동발행 185.
        -- 산업 동일성(v_instrument)은 속성이지 관계가 아니다 - 그것만 재던 것이
        -- '경로형'이라는 이름과 실제 사이의 격차였다.
        SELECT a.entity_id AS src, b.entity_id AS dst,
               a.role_code AS role_src, b.role_code AS role_dst,
               CASE
                 WHEN a.role_code IN ('CUSTOMER','SUPPLIER')
                  AND b.role_code IN ('CUSTOMER','SUPPLIER')
                  AND a.role_code <> b.role_code           THEN 'SUPPLY_CHAIN'
                 WHEN a.role_code LIKE 'PARTNER%' AND b.role_code LIKE 'PARTNER%'
                                                            THEN 'PARTNERSHIP'
                 WHEN a.role_code IN ('INVESTOR','TARGET_COMPANY')
                  AND b.role_code IN ('INVESTOR','TARGET_COMPANY')
                  AND a.role_code <> b.role_code           THEN 'OWNERSHIP'
                 WHEN a.role_code = 'ISSUER' AND b.role_code = 'ISSUER'
                                                            THEN 'CO_ISSUER'
               END AS link_type,
               se.source_event_id, se.event_date AS link_date
        FROM {prefix}source_event se
        JOIN {prefix}event_argument a ON a.source_event_id = se.source_event_id
        JOIN {prefix}event_argument b ON b.source_event_id = se.source_event_id
                                     AND a.entity_id <> b.entity_id
        JOIN {prefix}instrument ia ON ia.instrument_id = a.entity_id
        JOIN {prefix}instrument ib ON ib.instrument_id = b.entity_id
        WHERE se.event_status = 'ACTIVE' AND se.available_at <= {as_of}
    ),
    v_cohort AS (
        SELECT e.instrument_id, e.trade_date, e.source_event_id, e.event_type_code,
               e.predicate_code, e.role_code, e.lifecycle_stage,
               i.ticker, i.sector_name, i.industry_name, i.market_cap, i.listing_market
        FROM v_event e JOIN v_instrument i ON i.instrument_id = e.instrument_id
    )"""


class SqlLedger:
    """던진 질의 전량. **보고된 하나가 아니라 시도 전부가 남는다.**"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, query: str, rows: int, error: str = "") -> None:
        self.calls.append({"query": query.strip()[:2000], "rows": rows, "error": error})

    @property
    def queries(self) -> list[str]:
        return [c["query"] for c in self.calls]


class SqlSurface:
    """읽기 전용 자유 질의. **시점·행수·문장 종류를 코드가 정한다.**"""

    def __init__(self, conn, *, as_of: str, trade_date: date,
                 ledger: SqlLedger | None = None) -> None:
        if not as_of:
            raise PipelineError("SqlSurface 에 as_of 가 필요하다 - 시점 없이는 클램프가 없다")
        self._conn = conn
        self._as_of = as_of
        self._trade_date = trade_date
        self.ledger = ledger if ledger is not None else SqlLedger()

    def schema(self) -> str:
        return SCHEMA.format(max_rows=MAX_ROWS)

    def query(self, sql: str, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        """SELECT 하나. 실패는 예외로 올리되 **트랜잭션을 오염시키지 않는다.**

        거부도 원장에 남긴다. 안 남기면 "거부당한 질의"와 "안 던진 질의"가 같은 모양
        (부재)이 되는데, 그 이중성을 없애는 것이 이 파이프라인 전체의 설계다 - 모델이
        무엇을 물으려다 막혔는지가 곧 표면의 결함 목록이다.
        """
        try:
            inner = _guard(sql)
        except PipelineError as exc:
            self.ledger.record(sql, 0, f"거부: {exc}")
            raise
        n = max(1, min(int(limit), MAX_ROWS))
        full = f"WITH {_views()}\nSELECT * FROM (\n{inner}\n) _q LIMIT {n}"
        params = {"as_of": self._as_of, "trade_date": self._trade_date}
        with self._conn.cursor() as cur:
            cur.execute("SAVEPOINT causal_sql")
            try:
                cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute(full, params)
                cols = [d[0] for d in cur.description or ()]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            except Exception as exc:                      # noqa: BLE001 - 되먹임 대상
                cur.execute("ROLLBACK TO SAVEPOINT causal_sql")
                self.ledger.record(sql, 0, f"{type(exc).__name__}: {exc}")
                raise
            cur.execute("RELEASE SAVEPOINT causal_sql")
        self.ledger.record(sql, len(rows))
        return rows

    def ask(self, sql: str, *, show: int = 20) -> str:
        """모델에게 되돌려 줄 문자열. **실패도 관측이다** - 고쳐 쓰게 한다."""
        try:
            rows = self.query(sql)
        except PipelineError as exc:
            return f"거부: {exc}"
        except Exception as exc:                          # noqa: BLE001
            return f"오류: {type(exc).__name__}: {exc}"
        if not rows:
            return "0행. 술어가 너무 좁거나 그 관계가 원장에 없다."
        head = list(rows[0].keys())
        lines = [" | ".join(head)]
        for r in rows[:show]:
            lines.append(" | ".join(_cell(r[c]) for c in head))
        if len(rows) > show:
            lines.append(f"... 총 {len(rows)}행")
        return "\n".join(lines)


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.6g}"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."


# 공유 표면의 공개 이름 - 인과 패널(statics)이 같은 정의를 쓴다 (18R).
views_sql = _views

# 손으로 쓴 의미 뷰의 이름. 자동 생성기가 **이 이름은 안 만든다** - 같은 이름이
# 둘이면 CTE 가 영구 뷰를 가리고, 그게 정확히 "표면이 둘로 갈린다" 사고다.
HAND_VIEWS = ('v_cohort', 'v_daily', 'v_entity', 'v_event', 'v_event_news', 'v_flow', 'v_hold', 'v_instrument', 'v_link', 'v_liquidity', 'v_measure', 'v_news', 'v_thread')

# 시점 클램프 후보 열. 우선순위 = **정보가 관측자에게 도달한 시각**에 가까운 순.
# `*_at` 은 타임스탬프(as_of 로 자름), `*_date` 는 날짜(trade_date 로 자름).
# 도메인이 아닌 배관 표. 분모에서 뺀다 - 못 묶은 게 아니라 **안 묶는다**.
PLUMBING = ("flyway_", "ops_", "tenant", "admin_", "release_")

CLAMP_COLS = ("available_at", "evaluated_at", "published_at", "opened_at",
              "as_of_date", "profile_as_of_date", "trade_date", "created_at")


def auto_views_sql(cols: dict[str, list[str]], *, as_of: str, trade_date: str,
                   prefix: str, skip: set[str] = frozenset()) -> list[tuple[str, str | None, str]]:
    """손으로 안 쓴 표 전량에 시점 클램프 뷰를 **생성**한다 (19R).

    왜 자동인가: 클램프를 표마다 손으로 쓰면 새 표는 기본값이 '안 묶임'이 된다.
    실측이 그랬다 - 살아 있는 44표 중 20표만 묶여 있었고, 안 묶인 쪽에
    `thread_discovery_snapshot`(사건별 신규성 축) 같은 1급 재료가 앉아 있었다.
    생성기가 하나면 커버리지가 **구조적으로** 100% 이고, 클램프 누락이 불가능하다.

    반환: (표 이름, 클램프 열 또는 None, CREATE VIEW 문). 클램프 열이 없는 표는
    시점 불변 차원으로 취급하되 **None 을 그대로 보고**한다 - 조용히 통과시키면
    PIT 위반이 어디서 들어오는지 알 수 없다.
    """
    out: list[tuple[str, str | None, str]] = []
    for t in sorted(cols):
        if t in skip or t.startswith(PLUMBING):
            continue
        have = set(cols[t])
        clamp = next((c for c in CLAMP_COLS if c in have), None)
        where = ""
        if clamp:
            where = f" WHERE {clamp} <= {as_of if clamp.endswith('_at') else trade_date}"
        out.append((t, clamp,
                    f"CREATE OR REPLACE VIEW v_{t} AS SELECT * FROM {prefix}{t}{where}"))
    return out


__all__ = ["MAX_ROWS", "SCHEMA", "SqlLedger", "SqlSurface", "HAND_VIEWS", "PLUMBING", "auto_views_sql", "views_sql"]
