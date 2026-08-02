"""DuckDB 조인층 — S3(5분봉 parquet)·RDB(Postgres)·로컬 백필을 한 SQL 표면으로.

데이터가 없어도 설계는 선다: 소스가 빠지면 뷰가 안 생기고 coverage() 가
그 부재를 **보고**한다 — 조용히 빈 조인을 만드는 것이 최악이므로(P1 규율),
없는 소스를 참조하는 질의는 즉시 죽는다.

소스 우선순위: 로컬 백필 디렉터리(.tmp/causal-backfill) → RDB 터널 → S3.
경로·DSN 은 env 로 받는다. 전역 상수는 vocab 에 있다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

RDB_DSN_ENV = "EDGE_RDB_DSN"         # 예: host=127.0.0.1 port=15432 dbname=edge user=edge password=...
BACKFILL_ENV = "CAUSAL_BACKFILL_DIR"  # 기본 .tmp/causal-backfill

# RDB 에서 끌어와 쓰는 표 — 설계 §16 간선 카탈로그의 원천.
RDB_TABLES = ("price_daily", "investor_flow_daily", "etf_holding_snapshot",
              "etf_nav_daily", "source_event", "event_argument", "instrument",
              "instrument_classification", "supply_contract_fact",
              "price_movement_trigger", "etf_contribution_observation",
              "etf_contribution_member", "event_thread", "event_thread_link",
              "entity", "document", "news_document", "event_evidence",
              "document_assertion", "event_measure")

LAKE = "s3://edge-dev-pipeline-lake/"
AWS_PROFILE_ENV = "AWS_PROFILE"          # 기본 work — 자격증명은 SSO 체인에서 온다

# S3 데이터셋 전량. **데이터가 없어도 붙인다** - 빈 Iceberg 테이블도 스키마는 있고,
# 스키마가 보여야 '아직 안 채워진 축'과 '존재하지 않는 축'을 구분할 수 있다.
#   hive  = Hive 파티션 parquet (market=KR/ · language=ko/ · report_date=…/)
#   glob  = 종목당 파일 하나인 평평한 parquet 묶음
#   ice   = Iceberg (metadata/*.avro) - draft/canonical 계열
#   csv   = gzip CSV (DataGuide 적재본). **지연 바인딩** - 뷰 생성에만 10.9초가
#           든다(스키마 스니핑). 매 셀마다 44초를 내는 대신 첫 조회 때 건다.
S3_SETS: tuple[tuple[str, str, str], ...] = (
    ("s3_price_daily",      "hive", "canonical/market_data/price_daily"),
    ("s3_investor_flow",    "hive", "canonical/market_data/investor_flow_daily"),
    ("s3_etf_nav",          "hive", "canonical/market_data/etf_nav"),
    ("s3_news_articles",    "hive", "canonical/news/news_articles"),
    ("s3_etf_holdings",     "hive", "canonical/holdings/etf_holdings"),
    ("s3_etf_profile",      "hive", "canonical/reference/etf_profile"),
    ("s3_segment_fact",     "hive", "canonical/disclosures/business_segment_fact"),
    ("s3_supply_fact",      "hive", "canonical/disclosures/supply_contract_fact"),
    ("s3_assertions",       "hive", "feature/news/assertions"),
    # 20R 오픈소스 수집분 - 표현력 측정이 가리킨 비-뉴스 방아쇠를 메운다.
    ("s3_fx_daily",         "hive", "canonical/market_data/fx_daily"),
    ("s3_index_daily",      "hive", "canonical/market_data/index_daily"),
    ("s3_rates_daily",      "hive", "canonical/market_data/rates_daily"),
    ("s3_analyst_target",   "hive", "canonical/reports/analyst_target"),
    ("s3_rating_dist",      "hive", "canonical/reports/rating_distribution"),
    ("s3_dg_financials",    "csv",  "draft/curated/source=dataguide/dataset=financial_statements"),
    ("s3_dg_flow",          "csv",  "draft/curated/source=dataguide/dataset=investor_flow_daily"),
    ("s3_dg_price",         "csv",  "draft/curated/source=dataguide/dataset=price_daily"),
    ("s3_dg_reference",     "csv",  "draft/curated/source=dataguide/dataset=reference"),
    ("s3_kr_5min",          "glob", "raw/kr_intraday/fmp_5min"),
    ("s3_us_5min",          "glob", "raw/fmp_5min_us"),
    ("s3_statement_line",   "ice",  "draft/canonical/financials/statement_line"),
    ("s3_estimate_line",    "ice",  "draft/canonical/estimates/estimate_line"),
    ("s3_shareholder",      "ice",  "draft/canonical/ownership/shareholder_stake"),
    ("s3_officer_tenure",   "ice",  "draft/canonical/governance/officer_tenure"),
    ("s3_credit_rating",    "ice",  "draft/canonical/credit/credit_rating"),
    ("s3_person_master",    "ice",  "draft/canonical/people/person_master"),
    ("s3_entity_master",    "ice",  "draft/canonical/reference/entity_master"),
    ("s3_report_warning",   "ice",  "draft/canonical/reports/report_warning"),
)


class CausalLake:
    """한 연결 위의 뷰 집합. exists 딕셔너리가 곧 커버리지 보고서다."""

    def __init__(self, backfill_dir: str | Path | None = None,
                 rdb_dsn: str | None = None, bars_dir: str | Path | None = None):
        import duckdb
        self.con = duckdb.connect()
        # 좌표계 고정: RDB 는 UTC timestamptz, 봉은 KST naive 문자열이다.
        # 세션 TZ 를 서울로 못 박아 CAST(timestamptz AS TIMESTAMP) 가 KST naive 가
        # 되게 한다 — 머신 TZ 에 따라 τ 가 조용히 밀리는 사고를 막는다.
        self.con.execute("SET TimeZone='Asia/Seoul'")
        self.exists: dict[str, Any] = {}
        self.rows: dict[str, int] = {}          # 표 → 행수 추정 (원장 전량)
        self.cols: dict[str, list[str]] = {}    # 표 → 열 (자동 뷰 생성의 입력)
        self.bound: dict[str, str | None] = {}  # 표 → 클램프 열 (None = 시점 불변 차원)
        self.unbound: dict[str, str] = {}       # 표 → 못 묶은 사유 (침묵 금지)
        self.day: str = ""                      # 지금 뷰가 잘려 있는 기준일
        self.effective: dict[str, tuple[int, str | None]] = {}  # 표 → (그날 행수, 도달 지평)
        self.s3: dict[str, str] = {}            # S3 뷰 이름 → 버킷 경로
        self.deferred: dict[str, str] = {}      # 첫 조회 때 걸 뷰 (스니핑이 비싼 것)
        self._probed: str = ""
        self._bars(Path(bars_dir) if bars_dir else self._default_bars())
        self._backfill(Path(backfill_dir or os.environ.get(BACKFILL_ENV, ".tmp/causal-backfill")))
        self._s3()
        self._rdb(rdb_dsn or os.environ.get(RDB_DSN_ENV, ""))

    @staticmethod
    def _default_bars() -> Path:
        return Path(os.environ.get(BACKFILL_ENV, ".tmp/causal-backfill")) / "bars"

    def _bars(self, d: Path) -> None:
        """kr 5분봉: 종목당 parquet ({ticker}.KS.parquet, 컬럼 symbol·datetime·OHLCV)."""
        files = sorted(d.glob("*.parquet")) if d.is_dir() else []
        if not files:
            self.exists["bars_5m"] = 0
            return
        self.con.execute(
            f"CREATE VIEW bars_5m AS SELECT symbol, CAST(datetime AS TIMESTAMP) AS ts, "
            f"open, high, low, close, volume FROM read_parquet('{d.as_posix()}/*.parquet')")
        self.exists["bars_5m"] = self.con.execute("SELECT count(*) FROM bars_5m").fetchone()[0]

    def _backfill(self, d: Path) -> None:
        """로컬 백필: us_market(전일 미국장) · fx_usdkrw(환율) · tau_sidecar(초 단위 τ)."""
        for name in ("us_market", "fx_usdkrw", "tau_sidecar"):

            f = d / f"{name}.parquet"
            if f.is_file():
                self.con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{f.as_posix()}')")
                self.exists[name] = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            else:
                self.exists[name] = 0

    def _s3(self) -> None:
        """S3 데이터셋 전량을 뷰로 건다. **데이터 유무와 무관하게 스키마를 붙인다.**

        20R: 백필 대장에 '대기'로 적어 둔 항목 중 셋(#3 재무·#4 수급·#7 ETF 보유)이
        이미 S3 에 있었다. 없어서 못 한 게 아니라 **안 붙여서 못 하고 있었다**.
        빈 테이블도 붙이는 이유: 스키마가 보여야 '아직 안 채워진 축'과 '존재하지
        않는 축'이 구분된다 - 후자만 설계 한계이고 전자는 적재 일감이다.

        뷰는 게으르다(정의만 등록). 1,273종목 5분봉 글롭도 만들 때는 안 읽는다.
        """
        try:
            self.con.execute("INSTALL httpfs; LOAD httpfs; INSTALL iceberg; LOAD iceberg;")
            # 이 Iceberg 테이블들엔 version-hint 파일이 없다 - 최신 스냅샷을 글롭으로
            # 찾게 허용한다. 읽기 전용이고 커밋 중인 쓰기가 없으므로 안전하다.
            self.con.execute("SET unsafe_enable_version_guessing = true")
            self.con.execute(
                "CREATE SECRET (TYPE s3, PROVIDER credential_chain, "
                "CHAIN 'sso;config;env', PROFILE '"
                + os.environ.get(AWS_PROFILE_ENV, "work")
                + "', REGION 'ap-northeast-2')")
        except Exception as e:      # noqa: BLE001 - 자격증명 부재도 커버리지 보고
            self.exists["s3"] = f"실패: {str(e)[:120]}"
            return
        src = {"hive": "read_parquet('{p}/**/*.parquet', hive_partitioning=true)",
               "glob": "read_parquet('{p}/*.parquet')",
               "ice": "iceberg_scan('{p}')",
               "csv": ("read_csv('{p}/**/*.csv.gz', hive_partitioning=true, "
                       "all_varchar=true, ignore_errors=true)")}
        for name, kind, path in S3_SETS:
            ddl = (f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM "
                   + src[kind].format(p=LAKE + path))
            if kind == "csv":
                self.deferred[name] = ddl        # 등록만 - 부를 때 건다
                self.s3[name] = path
                continue
            try:
                self.con.execute(ddl)
                self.s3[name] = path
            except Exception as e:  # noqa: BLE001 - 못 붙인 것은 이름과 사유로 남긴다
                self.unbound[name] = f"{type(e).__name__}: {str(e)[:80]}"
        self.exists["s3"] = len(self.s3)

    def bind_s3(self, name: str) -> str:
        """지연 바인딩 셋을 실제로 건다. 반환: 빈 문자열 = 성공, 아니면 사유."""
        ddl = self.deferred.pop(name, "")
        if not ddl:
            return ""
        try:
            self.con.execute(ddl)
            return ""
        except Exception as e:      # noqa: BLE001
            self.unbound[name] = f"{type(e).__name__}: {str(e)[:80]}"
            return self.unbound[name]

    def _rdb(self, dsn: str) -> None:
        """Postgres 를 붙이고 **살아 있는 표 전량**에 클램프 뷰를 생성한다 (19R).

        표 목록을 손으로 적지 않는다: 적으면 새 표의 기본값이 '안 묶임'이 되고,
        실측이 정확히 그랬다(44표 중 20표만). 원장이 목록의 원천이다.
        행수는 `pg_stat_user_tables` 추정치 - 66표에 count(*) 를 돌리면 터널로
        전량 스캔이 나간다 (실측 0.17s vs 수십 초).
        """
        if not dsn:
            self.exists["rdb"] = False
            return
        try:
            self.con.execute("INSTALL postgres; LOAD postgres;")
            self.con.execute(f"ATTACH '{dsn}' AS rdb (TYPE postgres, READ_ONLY)")
            self.rows = {r[0]: r[1] for r in self.con.execute(
                "SELECT * FROM postgres_query('rdb', 'SELECT relname, n_live_tup "
                "FROM pg_stat_user_tables WHERE schemaname = ''public''')").fetchall()}
            cols: dict[str, list[str]] = {}
            for t, c in self.con.execute(
                    "SELECT table_name, column_name FROM duckdb_columns() "
                    "WHERE database_name = 'rdb' AND schema_name = 'public'").fetchall():
                cols.setdefault(t, []).append(c)
            self.cols = cols
            self.bound = {t: c for t, c, _ in self._plan()}
            self.exists.update({t: self.rows.get(t, 0) for t in RDB_TABLES})
            self.exists["rdb"] = True
        except Exception as e:      # 터널 죽음 = 부재 보고, 침묵 금지
            self.exists["rdb"] = f"실패: {e}"

    def _plan(self) -> list[tuple[str, str | None, str]]:
        """묶을 표와 각자의 클램프 열. **생성과 분리**한다 - 클램프 기준일은 셀마다
        다르지만 무엇이 묶이는지는 원장 구조만으로 정해지므로, 보고서는 날짜 없이
        낼 수 있어야 한다."""
        from ..adapters.sql_surface import HAND_VIEWS, auto_views_sql
        return auto_views_sql(self.cols, as_of="TIMESTAMP '{as_of}'",
                              trade_date="DATE '{day}'", prefix="rdb.public.",
                              skip={n[2:] for n in HAND_VIEWS})

    def bind_day(self, day: str, clock: str = "23:59:59") -> int:
        """그날 시점으로 자른 뷰를 **전 표에** 건다. 반환: 생성된 뷰 수.

        뷰는 메타데이터라 재생성이 싸다(실측 <0.1s/40뷰). 셀이 바뀌면 다시 부른다.
        """
        if self.day == day or not self.cols:
            return len(self.bound)
        for t, _clamp, ddl in self._plan():
            try:
                self.con.execute(ddl.format(as_of=f"{day} {clock}", day=day))
            except Exception as e:      # noqa: BLE001 - 실패도 커버리지 보고 대상
                self.unbound[t] = f"{type(e).__name__}: {str(e)[:80]}"
        self.day = day
        return len(self.bound) - len(self.unbound)

    # ── 표면 ────────────────────────────────────────────────────────────
    def sql(self, q: str) -> list[tuple]:
        return self.con.execute(q).fetchall()

    def probe_day(self) -> dict[str, tuple[int, str | None]]:
        """표 → (뷰 기준일의 행수, 도달 지평). 바인딩과 유효 커버리지는 다른 숫자다.

        **도달 지평** = 클램프 열의 최솟값. 그 이전 셀에서 그 표는 '없는' 게 아니라
        '아직 못 닿는' 것이다. 실측이 이 구분을 강요했다: `available_at` 이 대부분
        정보 도달이 아니라 **적재 시각**이라, document(293,930행)는 2026-07-08
        부터만 보인다. 부재로 보고하면 '뉴스가 없는 날'이 되고, 그건 거짓이다.
        한 번의 왕복으로 센다 - 표마다 왕복하면 35표 × 터널 지연이다.
        """
        if self.effective and self._probed == self.day:
            return self.effective
        if not self.day or not self.bound:
            return {}
        live = [t for t, n in self.rows.items() if n and t in self.bound]
        parts = " UNION ALL ".join(
            f"SELECT ''{t}'' AS t, count(*) AS n, "
            + (f"min({c})::text AS h FROM public.{t} WHERE {c} <= ''{self.day} 23:59:59''"
               if (c := self.bound[t]) else f"NULL::text AS h FROM public.{t}")
            for t in live)
        try:
            rows = self.sql(f"SELECT * FROM postgres_query('rdb', '{parts}')")
        except Exception:       # noqa: BLE001 - 못 재면 빈 보고, 거짓말 금지
            return {}
        got = {r[0]: (r[1], r[2]) for r in rows}
        # 0행인 표는 클램프 없이 다시 재야 지평을 안다 (WHERE 가 다 걷어냈으므로).
        blind = [t for t, (n, _) in got.items() if not n and self.bound.get(t)]
        if blind:
            q = " UNION ALL ".join(
                f"SELECT ''{t}'' AS t, min({self.bound[t]})::text AS h FROM public.{t}"
                for t in blind)
            try:
                for t, h in self.sql(f"SELECT * FROM postgres_query('rdb', '{q}')"):
                    got[t] = (0, h)
            except Exception:   # noqa: BLE001
                pass
        self.effective, self._probed = got, self.day
        return got

    def coverage(self, *, effective: bool = False) -> str:
        """바인딩 원장 — **도메인 표 중 몇 %가 시점 뷰로 도달 가능한가**.

        분모에서 빼는 것 둘, 각각 사유가 다르다: 빈 표(적재 안 됨 - 표면 결함 아님),
        배관 표(ops/tenant/flyway - 도메인이 아님). 뺀 개수를 같이 말한다.
        effective=True 면 그날 실제로 행이 남는지까지 잰다(터널 왕복 1회).
        """
        from ..adapters.sql_surface import HAND_VIEWS, PLUMBING
        live = {t: n for t, n in self.rows.items() if n}
        plumb = {t for t in live if t.startswith(PLUMBING)}
        dom = {t: n for t, n in live.items() if t not in plumb}
        hand = {n[2:] for n in HAND_VIEWS} & set(dom)
        reach = (set(self.bound) & set(dom)) | hand
        miss = sorted(set(dom) - reach - set(self.unbound), key=lambda t: -dom[t])
        pct = 100.0 * len(reach) / len(dom) if dom else 0.0
        lines = [f"바인딩 {len(reach)}/{len(dom)} = {pct:.0f}%  (도메인 표 기준 · "
                 f"빈 표 {len(self.rows) - len(live)} · 배관 {len(plumb)} 제외 · "
                 f"뷰 기준일 {self.day or '미고정'})"]
        if miss:
            lines.append("  미도달: " + ", ".join(f"{t}({dom[t]:,})" for t in miss))
        if self.unbound:
            lines.append("  생성 실패: " + " · ".join(f"{t}: {w}" for t, w in
                                                  list(self.unbound.items())[:4]))
        noclamp = sorted(t for t, c in self.bound.items() if c is None and dom.get(t))
        if noclamp:
            lines.append(f"  시점 불변 차원 {len(noclamp)}개(클램프 열 없음 - PIT 보장 없이 "
                         f"쓰는 것이므로 사실이 늦게 바뀌면 선견이다): " + ", ".join(noclamp))
        if effective:
            got = self.probe_day()
            zero = [(t, (got[t][1] or "")[:10]) for t in sorted(reach)
                    if t in got and not got[t][0]]
            # **미도달 ≠ 부재.** 지평이 셀 뒤에 있으면 그 표는 그날 존재하지 않았다.
            late = [(t, h) for t, h in zero if h and h[:10] > self.day]
            void = [t for t, h in zero if not (h and h[:10] > self.day)]
            lines.append(f"  그날 유효 {len(reach) - len(zero)}/{len(reach)}")
            if late:
                lines.append("  미도달(적재 지평이 셀보다 늦다 - 부재가 아니다): "
                             + ", ".join(f"{t}≥{h}" for t, h in late))
            if void:
                lines.append("  진짜 0행: " + ", ".join(void))
        if self.s3 or self.unbound:
            lines.append(f"S3 데이터셋 {len(self.s3)}개 (즉시 {len(self.s3) - len(self.deferred)}"
                         f" · 지연 {len(self.deferred)}) — canonical·draft·feature·raw 전량."
                         "\n  **빈 것도 붙였다**: 스키마가 보여야 '아직 안 채워진 축'과 "
                         "'존재하지 않는 축'이 갈린다. peek(이름) 으로 열·행수를 본다."
                         "\n  " + ", ".join(sorted(self.s3)))
        lines.append(self.frontier())
        return "\n".join(lines)

    def frontier(self) -> str:
        """봉과 원장이 **동시에** 닿는 전선. 이게 시스템의 실제 가용 범위다.

        19R 실측이 강요한 보고: 봉은 2026-07-16 에서 끊기는데 원장 4표의 적재
        지평은 07-20~07-25 다 → 그 표들은 **어떤 셀에서도 봉과 함께 못 쓴다**.
        표별 미도달만 보면 '이 셀에서 늦었다'로 보이고, 구조적 불가라는 게 안 보인다.
        """
        if not self.exists.get("bars_5m"):
            return "전선: 봉 없음 - 시간 분해 불가"
        lo, hi = self.sql("SELECT min(CAST(ts AS DATE)), max(CAST(ts AS DATE)) "
                          "FROM bars_5m")[0]
        never = sorted(t for t, (_n, h) in self.effective.items()
                       if h and h[:10] > str(hi))
        line = f"전선: 봉 {lo}~{hi}"
        if never:
            line += (f"\n  봉 종료일 이후에야 적재된 표 {len(never)}개 - 어떤 셀에서도 "
                     f"시간 분해와 함께 못 쓴다 (봉 백필 연장이 해소): {', '.join(never)}")
        if self.day:
            has = self.sql("SELECT count(*) FROM bars_5m WHERE CAST(ts AS DATE) = "
                           f"DATE '{self.day}'")[0][0]
            line += f"\n  이 셀({self.day}) 봉 {has}개" + ("" if has else " - 시간 분해 불가")
        return line

    def bars(self, ticker: str, day: str) -> list[tuple]:
        """(ts, close) — tree.decompose 의 입력. 심볼 규약: '005930.KS'."""
        if not self.exists.get("bars_5m"):
            raise RuntimeError("bars_5m 없음 — 백필 먼저 (coverage 참조)")
        return self.sql(
            f"SELECT ts, close FROM bars_5m WHERE symbol='{ticker}' "
            f"AND CAST(ts AS DATE) = DATE '{day}' ORDER BY ts")

    def prev_close(self, ticker: str, day: str) -> float:
        row = self.sql(
            f"SELECT close FROM bars_5m WHERE symbol='{ticker}' "
            f"AND CAST(ts AS DATE) < DATE '{day}' ORDER BY ts DESC LIMIT 1")
        if not row:
            raise RuntimeError(f"{ticker} {day} 이전 봉 없음")
        return float(row[0][0])

    def taus(self, instrument_id: str, day: str) -> list[tuple]:
        """그날(KST) 그 종목의 사건 (τ KST naive, source_event_id).

        τ 사이드카가 있으면 **초 단위 발행시각**을 쓴다 — RDB available_at 은
        블로커 4(날짜 해상도) 재적재 전까지 자정이라, 사슬
        event→evidence→assertion→document.source_document_id(=article_id) 로
        사이드카와 크로스 스토어 조인해 승격한다. 사이드카에 없는 사건은
        available_at 폴백 — 부재를 침묵시키지 않고 그대로 드러낸다(09:00 뭉침).
        """
        if self.exists.get("rdb") is not True:
            raise RuntimeError("RDB 부재 — coverage 참조")
        promote = ""
        if self.exists.get("tau_sidecar"):
            promote = (
                "LEFT JOIN rdb.public.event_evidence ev ON ev.source_event_id=e.source_event_id "
                "LEFT JOIN rdb.public.document_assertion da ON da.assertion_id=ev.assertion_id "
                "LEFT JOIN rdb.public.document doc ON doc.document_id=da.document_id "
                "LEFT JOIN tau_sidecar sc ON sc.article_id=doc.source_document_id ")
        t_expr = ("coalesce(min(sc.published_kst), min(CAST(e.available_at AS TIMESTAMP)))"
                  if promote else "min(CAST(e.available_at AS TIMESTAMP))")
        return self.sql(
            f"SELECT {t_expr} AS t, e.source_event_id "
            "FROM rdb.public.source_event e "
            "JOIN rdb.public.event_argument a ON a.source_event_id=e.source_event_id "
            + promote +
            f"WHERE a.entity_id='{instrument_id}' "
            f"AND CAST(CAST(e.available_at AS TIMESTAMP) AS DATE)=DATE '{day}' "
            "GROUP BY e.source_event_id ORDER BY 1")


__all__ = ["CausalLake", "RDB_TABLES"]
