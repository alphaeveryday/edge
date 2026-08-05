"""DuckDB 조인층 — S3(5분봉 parquet)·RDB(Postgres)·로컬 백필을 한 SQL 표면으로.

데이터가 없어도 설계는 선다: 소스가 빠지면 뷰가 안 생기고 coverage() 가
그 부재를 **보고**한다 — 조용히 빈 조인을 만드는 것이 최악이므로(P1 규율),
없는 소스를 참조하는 질의는 즉시 죽는다.

소스 우선순위: S3(canonical·백필 parquet) → 로컬 백필 디렉터리 → RDB 터널.
경로·DSN·자격증명·메모리 한도는 전부 env 다 — 노트북과 Fargate 가 같은 코드로
돌아야 하므로 프로파일·리전·체인 어느 것도 하드코딩하지 않는다(하단 *_ENV 상수).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

RDB_DSN_ENV = "EDGE_RDB_DSN"         # 예: host=127.0.0.1 port=15432 dbname=edge user=edge password=...
BACKFILL_ENV = "CAUSAL_BACKFILL_DIR"  # 기본 .tmp/causal-backfill
BACKFILL_S3_ENV = "CAUSAL_BACKFILL_S3"  # 기본값은 DEFAULTS 에 있다 (재지정 전용 변수)

# RDB 에서 끌어와 쓰는 표 — 설계 §16 간선 카탈로그의 원천.
RDB_TABLES = ("price_daily", "investor_flow_daily", "etf_holding_snapshot",
              "etf_nav_daily", "source_event", "event_argument", "instrument",
              "instrument_classification", "supply_contract_fact",
              "price_movement_trigger", "etf_contribution_observation",
              "etf_contribution_member", "event_thread", "event_thread_link",
              "entity", "document", "news_document", "event_evidence",
              "document_assertion", "event_measure")

LAKE = "s3://edge-dev-pipeline-lake/"

# 1분 워커 롤업이 5분봉을 실시간 공급하기 시작한 날. 이 날부터는 롤업이 정본이고
# 그 전은 fmp 백필이 정본이다 - 두 벤더가 같은 (symbol, ts) 를 낼 때의 심판.
ROLLUP_FROM = "2026-08-04"

# 하류 SQL 이 이름으로 참조하는 로컬 백필의 빈 스키마. statics.pit / statics.fin 이
# 아직 안 돌았어도 v_pit·v_fin 을 쓰는 패널 SQL 이 파스는 되어야 한다 - '없는 축'이
# 아니라 '안 채워진 축'임을 스키마로 말한다.
_EMPTY_COLS = {
    "pit_daily": ("trade_date DATE", "ticker VARCHAR",
                  *(f"{c} DOUBLE" for c in
                    "foreign_ratio foreign_used credit_ratio short_ratio lend_bal pbr per "
                    "shares treasury beta52w r2_52w mktcap turnover for_buy for_sell "
                    "ins_buy ins_sell ind_buy ind_sell for_net ins_net ind_net "
                    "treasury_ratio".split())),
    "fin_annual": ("fiscal_year INTEGER", "ticker VARCHAR", "available_from DATE",
                   *(f"{c} DOUBLE" for c in
                     "debt_ratio borrow_dep netdebt_dep int_cover cf_assets roe roa "
                     "op_margin net_margin rev_growth op_growth payout".split())),
    "flow_daily": ("trade_date DATE", "ticker VARCHAR", "for_net DOUBLE"),
    "sector_index": ("trade_date DATE", "code VARCHAR", "close DOUBLE"),
    "sector_member": ("as_of DATE", "ticker VARCHAR", "code VARCHAR", "market VARCHAR"),
}
EMPTY_SCHEMA = {
    name: "SELECT " + ", ".join(
        f"CAST(NULL AS {t}) AS {c}" for c, t in (s.split() for s in cols)) + " WHERE false"
    for name, cols in _EMPTY_COLS.items()
}
# 로컬/S3 백필 세트 전량. 하류 SQL 이 이름으로 직접 참조하므로 목록이 곧 계약이다.
BACKFILL_SETS = ("us_market", "fx_usdkrw", "tau_sidecar", "layers_daily",
                 "etf_holdings_fmp", "pit_daily", "fin_annual", "flow_daily",
                 "sector_index", "sector_member")

AWS_PROFILE_ENV = "AWS_PROFILE"          # **설정됐을 때만** 쓴다 - 컨테이너엔 ~/.aws/config 가 없다
AWS_REGION_ENV = "AWS_REGION"
S3_CHAIN_ENV = "DUCKDB_S3_CHAIN"
MEMORY_LIMIT_ENV = "DUCKDB_MEMORY_LIMIT"
TEMP_DIR_ENV = "DUCKDB_TEMP_DIR"
# 기본 체인에 instance 가 앞쪽에 있어야 Fargate 가 붙는다: task role 은 컨테이너
# 자격증명 엔드포인트로 오는데 컨테이너엔 SSO 캐시도 config 파일도 없다.
# 스필 경로는 **플랫폼이 정하게 둔다** - '/tmp' 를 박아 두면 Windows 에선 git-bash
# 매핑이 있을 때만 우연히 살고, 없으면 스필이 안 열려 OOM 으로 죽는다(죽는 대신
# 느려지라고 둔 장치가 정확히 필요할 때 없다). 컨테이너에선 그대로 '/tmp' 다.
# BACKFILL_S3_ENV 의 기본값은 **terraform 이 정한 자리**다: tasks.tf 의
# `analysis_backfill_s3_prefix` 가 Fargate env `CAUSAL_BACKFILL_S3` 로 같은 문자열을
# 주입한다. 셋(여기 · terraform local · Fargate env)이 한 자리를 가리켜야 노트북과
# 컨테이너가 같은 데이터를 본다. 여기를 '예쁘게' 바꾸면 노트북만 새 경로를 보고
# 컨테이너는 env 로 덮인 옛 경로 - 즉 아무것도 없는 프리픽스 - 를 읽어 백필 10세트가
# 빈 스키마 뷰로 걸리고 **질의는 성공한 채 0행**이 온다(backfill_sources 도크스트링의
# 그 실패 양식). 하나만 바꾸는 것은 언제나 버그다.
# 후행 슬래시 금지: 코드가 파일명을 이어붙이므로 '…/backfill//pit_daily.parquet' 가
# 되고 S3 는 빈 세그먼트를 키의 일부로 봐서 조용히 0파일을 돌려준다.
DEFAULTS = {S3_CHAIN_ENV: "env;instance;config;sso", AWS_REGION_ENV: "ap-northeast-2",
            MEMORY_LIMIT_ENV: "1.5GB", TEMP_DIR_ENV: tempfile.gettempdir(),
            BACKFILL_S3_ENV: LAKE + "analysis/backfill"}

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
    ("s3_investor_value",   "hive", "canonical/market_data/investor_value_daily"),
    # **947 항목 long 표** (trade_date, ticker, item_code, value) - 하루 75만 행.
    # 피벗해서 넓히려다 OOM 이 났다: 947열 × 366일 × 3,800종목. 그리고 947열 표는
    # 어차피 못 쓴다 - long 으로 두고 item_code 로 골라 쓰는 게 맞다.
    # 항목 사전은 `s3_dg_items` 다 (name_kr·domain·category). 데이터셋 전체를
    # `csv` 로 등록했던 것은 지웠다 - 그 패턴은 `*.csv.gz` 인데 reference 는 평문
    # `.csv` 라 영구히 0파일이었다(실측 IOException). 거짓 등록은 부재보다 나쁘다.
    # 실측 분류: 투자자별매매-수량 329 · 대금 329 · 가격수익률 97 · 주식수시총 59 ·
    # 베타 45 · 거래량 23 · 주가배수 20 · 신용거래 20 · 대차거래 13 · 차입공매도 12.
    # **컨센서스/추정 항목은 없다** (전수 검색 0건) - 서프라이즈는 다른 소스가 필요하다.
    # 항목 사전 (item_code · name_kr · unit · domain · category). `reference` 셋은
    # `.csv` **비압축**이라 csv 글롭(`*.csv.gz`)에 안 걸린다 - 파일을 직접 짚는다.
    ("s3_dg_items", "csvfile",
     "draft/curated/source=dataguide/dataset=reference/market=KR/"
     "as_of_date=2026-08-02/items_resolved.csv"),
    ("s3_program_trading",  "hive", "canonical/market_data/program_trading_daily"),
    ("s3_intraday_5m",      "hive", "canonical/market_data/intraday_5m"),
    ("s3_dg_financials",    "csv",  "draft/curated/source=dataguide/dataset=financial_statements"),
    ("s3_dg_flow",          "csv",  "draft/curated/source=dataguide/dataset=investor_flow_daily"),
    ("s3_dg_price",         "csv",  "draft/curated/source=dataguide/dataset=price_daily"),
    # **컨센서스**. 뷰가 없어서 통째로 안 보였다 - `s3_estimate_line`(아이스버그)에는
    # 메타데이터만 있고 데이터 파일이 0 개인데, 실제 적재는 curated dataguide 쪽으로
    # 갔다(222객체 · 141MB · 주간 as_of_date × fiscal_year 파티션). 그래서 '컨센서스
    # 3행' 이라는 오진이 문서·설계 포기 목록까지 올라갔다. 뷰 부재를 데이터 부재로
    # 읽은 것이다 - 커버리지 지식이 코드와 따로 살면 정확히 이런 일이 난다.
    ("s3_dg_consensus",     "csv",  "draft/curated/source=dataguide/dataset=consensus"),
    # **947 항목의 실체.** 롱 포맷 (trade_date, ticker, item_code, value) 이고 일자
    # 파티션이라 항목 하나 조회는 파티션 프루닝 + WHERE 한 줄이다 - 947 열로 피벗해
    # 물질화할 이유가 없다(`dgwide.build_mkt` 이 그걸 하려다 한 번도 안 돌았다).
    # 이 뷰가 없어서 '노출 어휘가 23개' 였다: 데이터가 아니라 **뷰가 병목이었다**.
    ("s3_dg_market",        "csv",  "draft/curated/source=dataguide/dataset=market_daily"),
    # glob 은 **파일 패턴까지** 적는다. `*.parquet` 로 뭉뚱그렸더니 같은 폴더의
    # kospi200_proxy.parquet(symbol·name·market_cap·sector)이 딸려 들어와 스키마
    # 충돌로 뷰 전체가 조회 불가였다 - 아무도 안 써서 안 걸렸다.
    ("s3_kr_5min",          "glob", "raw/kr_intraday/fmp_5min/*.KS.parquet"),
    ("s3_us_5min",          "glob", "raw/fmp_5min_us/*.parquet"),
    ("s3_statement_line",   "ice",  "draft/canonical/financials/statement_line"),
    ("s3_estimate_line",    "ice",  "draft/canonical/estimates/estimate_line"),
    ("s3_shareholder",      "ice",  "draft/canonical/ownership/shareholder_stake"),
    ("s3_officer_tenure",   "ice",  "draft/canonical/governance/officer_tenure"),
    ("s3_credit_rating",    "ice",  "draft/canonical/credit/credit_rating"),
    ("s3_person_master",    "ice",  "draft/canonical/people/person_master"),
    ("s3_entity_master",    "ice",  "draft/canonical/reference/entity_master"),
    ("s3_report_warning",   "ice",  "draft/canonical/reports/report_warning"),
)


def _env(key: str) -> str:
    """env 값 또는 확정 기본값. 빈 문자열도 미설정으로 본다 - 컨테이너 정의는
    변수를 '있지만 빈 값'으로 주는 일이 잦고, 그게 기본값을 덮으면 안 된다."""
    return os.environ.get(key, "").strip() or DEFAULTS[key]


def rdb_dsn_from_env() -> str:
    """RDB DSN 해소: EDGE_RDB_DSN → PG* 조립 → "" (부재).

    Fargate task 는 EDGE_RDB_DSN 을 안 준다 - PGHOST/PGPORT/PGDATABASE/PGUSER 와
    시크릿 PGPASSWORD 로 온다. EDGE_RDB_DSN 만 보면 배포판에서 rdb 가 통째로
    부재가 되고 손뷰 13개가 미생성이라 _base() 가 파스 단계에서 죽는다.

    PGHOST 와 PGDATABASE 가 **둘 다** 있을 때만 조립한다: 반쪽 DSN 은 붙었다 실패해
    '자격증명이 틀렸다'와 '설정이 안 왔다'를 같은 문장으로 만든다.
    비밀번호가 없으면 password 절을 뺀다 - IAM 인증이면 그게 정상이다.
    """
    if dsn := os.environ.get(RDB_DSN_ENV, "").strip():
        return dsn
    host = os.environ.get("PGHOST", "").strip()
    db = os.environ.get("PGDATABASE", "").strip()
    if not (host and db):
        return ""
    parts = [f"host={host}", f"port={os.environ.get('PGPORT', '').strip() or '5432'}",
             f"dbname={db}"]
    for key, env in (("user", "PGUSER"), ("password", "PGPASSWORD")):
        if val := os.environ.get(env, "").strip():
            parts.append(f"{key}={val}")
    return " ".join(parts)


def s3_secret_sql() -> str:
    """S3 자격증명 시크릿 DDL. PROFILE 절은 AWS_PROFILE 이 실제 설정됐을 때만 넣는다.

    'sso;config;env' + PROFILE 하드코딩은 개발 노트북 전용 문장이었다: Fargate 엔
    SSO 캐시도 ~/.aws/config 도 없어 PROFILE 절 자체가 시크릿 생성을 깨고, 그러면
    S3 뷰 33개가 통째로 미등록되며 bars_5m 이 빈 로컬 폴백으로 조용히 내려간다.
    """
    clauses = [f"CHAIN '{_env(S3_CHAIN_ENV)}'"]
    if profile := os.environ.get(AWS_PROFILE_ENV, "").strip():
        clauses.append(f"PROFILE '{profile}'")
    clauses.append(f"REGION '{_env(AWS_REGION_ENV)}'")
    return "CREATE SECRET (TYPE s3, PROVIDER credential_chain, " + ", ".join(clauses) + ")"


def session_pragmas() -> tuple[str, ...]:
    """연결 직후 못 박는 세션 설정.

    TimeZone: RDB 는 UTC timestamptz, 봉은 KST naive 문자열이다. 세션 TZ 를 서울로
    못 박아 CAST(timestamptz AS TIMESTAMP) 가 KST naive 가 되게 한다 - 머신 TZ 에
    따라 τ 가 조용히 밀리는 사고를 막는다.

    memory_limit·temp_directory: in-memory 연결이라 한도를 안 주면 호스트 메모리
    전체를 기본 한도로 잡는다. task_memory 2048MB 안에서 pit_daily(101MB) + v_daily
    윈도우(60일/20일 PARTITION BY instrument_id) + _base() CTE 전개가 도는데,
    한도가 없으면 컨테이너가 OOM 으로 통째로 죽는다(정규화 때 실제로 죽었다).
    스필 경로가 열려 있어야 죽는 대신 느려진다.

    preserve_insertion_order=false: 5분봉은 S3 64M 행이고, 구간 β 는 그 중 시각창을
    전 종목·전 날짜로 집계한다. 삽입 순서 보존을 켜 두면 스캔이 순서 버퍼를 들고
    있어야 해서 1.5GB 한도에서 **OOM 으로 죽었다**(실측: 구간 층분해가 4회 중 2회
    실패, 나머지는 지역화된 오류 메시지 때문에 UnicodeDecodeError 로 위장됐다).
    우리 SQL 은 순서가 중요한 곳마다 `ORDER BY` 를 명시한다 - 보존은 비용뿐이다.
    """
    return ("SET TimeZone='Asia/Seoul'",
            "SET preserve_insertion_order=false",
            f"SET memory_limit='{_env(MEMORY_LIMIT_ENV)}'",
            f"SET temp_directory='{_env(TEMP_DIR_ENV)}'")


def backfill_sources(name: str, local: Path) -> tuple[tuple[str, str], ...]:
    """백필 한 세트의 읽기 후보 — (표기, 경로) 를 우선순위 순으로. 없는 후보는 뺀다.

    S3 가 먼저고 **기본값도 S3 다**(DEFAULTS[BACKFILL_S3_ENV]). env 는 재지정용일
    뿐이다 - 예전엔 CAUSAL_BACKFILL_S3 가 없으면 S3 후보가 아예 안 생겨 사실상
    '로컬 전용'이 기본값이었다. 로컬 .tmp/causal-backfill 은 144MB 라 Docker 빌드
    컨텍스트(src/) 밖이고 컨테이너엔 아예 없다. 그러면 빈 스키마 뷰가 걸려 **파스도
    질의도 성공한 뒤 0행**이 돌아온다 - layers_daily 0행이면 층 분해가 None,
    v_pit·flow_daily 0행이면 전 튜플이 판정불가인데 아무도 실패를 못 본다.
    가장 위험한 실패 양식이라 표기를 반환값에 실어 exists 에 남긴다.

    **후보가 있다 ≠ 데이터가 있다.** 여기서 S3 후보는 경로를 조립했다는 뜻일 뿐이다.
    실제 읽기는 _backfill() 이 하고, 못 읽으면 사유가 backfill_notes 에, 부재는
    exists 에 falsy 로 그대로 남는다 - 기본값을 켜는 것이 부재를 덮으면 안 된다.
    """
    prefix = _env(BACKFILL_S3_ENV).rstrip("/")
    return tuple((label, src) for label, src in (
        ("S3", f"{prefix}/{name}.parquet"),
        ("로컬", local.as_posix() if local.is_file() else "")) if src)


class CausalLake:
    """한 연결 위의 뷰 집합. exists 딕셔너리가 곧 커버리지 보고서다."""

    def __init__(self, backfill_dir: str | Path | None = None,
                 rdb_dsn: str | None = None, bars_dir: str | Path | None = None):
        import duckdb
        self.con = duckdb.connect()
        for pragma in session_pragmas():
            self.con.execute(pragma)
        self.exists: dict[str, Any] = {}
        self.rows: dict[str, int] = {}          # 표 → 행수 추정 (원장 전량)
        self.cols: dict[str, list[str]] = {}    # 표 → 열 (자동 뷰 생성의 입력)
        self.bound: dict[str, str | None] = {}  # 표 → 클램프 열 (None = 시점 불변 차원)
        self.unbound: dict[str, str] = {}       # 표 → 못 묶은 사유 (침묵 금지)
        self.backfill_notes: dict[str, str] = {}  # 백필 세트 → 못 읽은/0행인 사유
        self.day: str = ""                      # 지금 뷰가 잘려 있는 기준일
        self.effective: dict[str, tuple[int, str | None]] = {}  # 표 → (그날 행수, 도달 지평)
        self.s3: dict[str, str] = {}            # S3 뷰 이름 → 버킷 경로
        self.deferred: dict[str, str] = {}      # 첫 조회 때 걸 뷰 (스니핑이 비싼 것)
        self._probed: str = ""
        self._s3()
        self._bars(Path(bars_dir) if bars_dir else self._default_bars())
        self._backfill(Path(backfill_dir or os.environ.get(BACKFILL_ENV, ".tmp/causal-backfill")))
        self._rdb(rdb_dsn or rdb_dsn_from_env())

    @staticmethod
    def _default_bars() -> Path:
        return Path(os.environ.get(BACKFILL_ENV, ".tmp/causal-backfill")) / "bars"

    def _bars(self, d: Path) -> None:
        """5분봉 뷰 — S3 canonical 과 로컬 백필의 **합집합** (21R).

        로컬을 폴백으로만 두던 규율을 버렸다: S3 가 있으면 로컬을 안 읽으니 S3 에
        없는 과거 구간(ETF·시장지수는 canonical 이 43일뿐이다)을 로컬 parquet 으로
        채워도 수치가 안 변했다. 이제 둘을 UNION 하고 겹치는 곳만 아래 규칙으로
        하나 남긴다 - 로컬은 '없을 때 쓰는 것'이 아니라 **비는 구간을 메우는 것**이다.

        심볼 규약은 기존 계약을 지킨다: `symbol` = `005930.KS`. `trade_date` 를
        노출해 하이브 파티션 프루닝이 걸리게 한다 - `CAST(ts AS DATE)` 로 거르면
        1.5억 행을 다 읽는다.

        **심볼 정규화** — 상류 1분 워커가 `source_symbol` 을 접미사 없는 bare 로
        쓴다(2026-08-05 실측: 롤업 6,878행 362종목 전부 bare, fmp 6,389만행은 전부
        `.KS`/`.KQ`). 그대로 두면 같은 종목이 두 심볼로 쪼개져 실시간 봉이 이력에
        안 이어지고, 거래일수를 세면 bare 티커가 '1일짜리 종목'으로 뜬다. 상류
        버그지만 **여기서 흡수한다** - 하류 전부가 symbol 로 조인하므로 축이 갈리면
        층분해가 통째로 틀린다. 접미사는 추측하지 않는다: KRX 는 KQ 가 1/3 이라
        (실측 863 KS · 406 KQ) `.KS` 를 무조건 붙이면 그만큼이 깨진다. fmp 이력의
        `ticker → source_symbol` 을 정본 매핑으로 쓰고, 거기 없는 티커(실측 69개)는
        bare 로 남긴다 - 이을 이력 자체가 없는 종목이라 지어내면 그게 거짓이다.

        **`available_at` 을 컬럼으로 낸다** — 장중 분석의 전제다. 1분 워커 롤업은
        닫힌 버킷만 쓰므로 파티션에 나타난 봉은 이미 확정분이지만, 그건 '언제부터
        알 수 있었나'를 말해 주지 않는다. 13:00 에 그날 파티션을 통째로 읽으면
        15:30 봉이 딸려 온다 - 날짜 절단은 장중 PIT 가 아니다. 컬럼이 없는 원천
        (로컬 백필 parquet)은 롤업과 **같은 계약**으로 유도한다: 구간이 닫히는 시각
        `ts + 5분`. 유도분인지는 `source_vendor` 로 갈린다('fmp_backfill').

        **중복 제거는 생성 시점의 서로소 구성이다 — 질의 시점 창은 없다.** 처음엔
        `QUALIFY row_number()` 로 겹침을 걸렀는데, 창 연산자는 블로킹이라 집계 전에
        파티션을 물리화한다: 층분해의 시각창 질의(13:00~15:30 · 전 종목 ≈ 34M행)가
        memory_limit 1.5GB 에서 OOM 으로 죽었다(스필 경로는 열려 있었다). 겹침 한
        쌍을 막자고 6,400만 행에 창을 씌운 것이 비용의 전부였다.

        그래서 겹칠 수 있는 곳만 **작은 표로 미리 계산해 안티조인**한다:
          · S3 내부 벤더 겹침은 **실측 0** 이다 - fmp 는 2026-07-31 에 끝나고 롤업은
            08-05 부터이며 08-03·08-04 파티션은 0행이다. 없는 겹침에 값을 치르지 않는다.
          · `ROLLUP_FROM` 이전은 fmp 가 정본이고 로컬 백필이 곧 fmp 를 더 깊게 다시
            받은 것이다 → 로컬이 가진 (symbol, trade_date) 를 **로컬 파일만 읽어**
            잡고(싸다) S3 가지에서 뺀다.
          · `ROLLUP_FROM` 이후는 롤업이 정본 → 그 시대 S3 의 (symbol, trade_date) 를
            잡고(파티션 한둘, 싸다) 로컬 가지에서 뺀다.
        어느 쪽도 S3 전 구간 집계를 안 한다 - 그건 `max(trade_date)` 하나가 21초다.
        안티조인은 **상대가 실제로 가진 날짜만** 지운다. 정본 벤더가 결측인 구간은
        남은 쪽이 그대로 살아남는다 - 겹칠 때 하나를 고르는 규칙이지 비정본을 지우는
        규칙이 아니다.
        """
        local_src = f"read_parquet('{d.as_posix()}/*.parquet')"
        has_local = d.is_dir() and bool(sorted(d.glob("*.parquet")))
        has_s3 = "s3_intraday_5m" in self.s3
        if not (has_local or has_s3):
            self.exists["bars_5m"] = 0
            return
        parts, cut_s3, cut_local = [], "", ""
        sm = self._symbol_map(local_src if has_local else "") if has_s3 else ""
        if has_s3 and has_local:
            cut_s3, cut_local = self._overlap_cuts(local_src, sm)
        if has_s3:
            sym = "coalesce(sm.symbol, b.source_symbol)" if sm else "b.source_symbol"
            parts.append(
                f"SELECT {sym} AS symbol, b.ticker, b.ts, b.trade_date, b.open, b.high, "
                "b.low, b.close, b.volume, b.source_vendor, "
                "coalesce(b.available_at, b.ts + INTERVAL 5 MINUTE) AS available_at "
                "FROM s3_intraday_5m b "
                + (f"LEFT JOIN {sm} sm ON sm.ticker = b.ticker " if sm else "")
                + "WHERE b.market = 'KR'" + cut_s3.format(sym=sym))
        if has_local:
            # 로컬 백필엔 available_at·source_vendor·ticker·trade_date 가 없다 - 전부 유도한다.
            parts.append(
                "SELECT l.symbol, regexp_replace(l.symbol, '\\.(KS|KQ)$', '') AS ticker, "
                "CAST(l.datetime AS TIMESTAMP) AS ts, CAST(l.datetime AS DATE) AS trade_date, "
                "l.open, l.high, l.low, l.close, l.volume, 'fmp_backfill' AS source_vendor, "
                "CAST(l.datetime AS TIMESTAMP) + INTERVAL 5 MINUTE AS available_at "
                f"FROM {local_src} l WHERE true" + cut_local)
        try:
            self.con.execute("CREATE OR REPLACE VIEW bars_5m AS "
                             + " UNION ALL ".join(parts))
        except Exception as e:      # noqa: BLE001 - 로컬 파일 부재도 커버리지 보고
            self.exists["bars_5m"] = 0
            self.unbound["bars_5m"] = f"{type(e).__name__}: {str(e)[:80]}"
            return
        src = ["S3 canonical"] if has_s3 else []
        if has_local:
            n = self.con.execute(f"SELECT count(*) FROM {local_src}").fetchone()[0]
            src.append(f"로컬 백필 ({n:,}행)")
        self.exists["bars_5m"] = " + ".join(src)

    def _overlap_cuts(self, local_src: str, sm: str) -> tuple[str, str]:
        """두 원천이 겹치는 (symbol, trade_date) 를 미리 잡아 안티조인 절로 돌려준다.

        반환: (S3 가지에 붙일 절, 로컬 가지에 붙일 절). S3 절은 `{sym}` 자리에
        정규화된 심볼 식이 들어간다 - 겹침 판정은 **뷰가 내는 축** 위에서 해야 한다.
        `_rollup_days` 도 같은 이유로 정규화해서 잡는다: 롤업의 `source_symbol` 은
        bare 인데 로컬은 접미사가 붙어 있어, 날것으로 잡으면 키가 영영 안 맞는다
        (실측: 그래서 2026-08-05 에 (symbol, ts) 중복 156쌍이 샜다).

        비싼 쪽을 절대 안 읽는 것이 요점이다: 로컬 날짜는 로컬 파일에서, 롤업 시대
        날짜는 파티션 한둘에서 나온다. 실패하면 절을 안 붙인다 - 겹침이 남아 행이
        두 번 세어질지언정 뷰가 통째로 없어지는 것보다 낫고, 사유는 남긴다.
        """
        cuts = []
        for name, ddl, tmpl in (
            ("_local_days",
             f"SELECT DISTINCT symbol, CAST(datetime AS DATE) AS trade_date FROM {local_src}",
             " AND NOT (b.trade_date < DATE '{rollup}' AND EXISTS (SELECT 1 FROM _local_days x "
             "WHERE x.symbol = {sym} AND x.trade_date = b.trade_date))"),
            ("_rollup_days",
             "SELECT DISTINCT "
             + ("coalesce(m.symbol, b.source_symbol)" if sm else "b.source_symbol")
             + " AS symbol, b.trade_date FROM s3_intraday_5m b "
             + (f"LEFT JOIN {sm} m ON m.ticker = b.ticker " if sm else "")
             + f"WHERE b.market = 'KR' AND b.trade_date >= DATE '{ROLLUP_FROM}'",
             " AND NOT EXISTS (SELECT 1 FROM _rollup_days x WHERE x.symbol = l.symbol "
             "AND x.trade_date = CAST(l.datetime AS DATE))")):
            try:
                self.con.execute(f"CREATE OR REPLACE TABLE {name} AS {ddl}")
            except Exception as e:      # noqa: BLE001 - 못 잡으면 겹침 유지, 침묵 금지
                self.unbound[name] = f"{type(e).__name__}: {str(e)[:80]}"
                cuts.append("")
                continue
            cuts.append(tmpl.replace("{rollup}", ROLLUP_FROM))
        return cuts[0], cuts[1]

    def _symbol_map(self, local_src: str) -> str:
        """bare 티커 → 접미사 붙은 canonical 심볼. 반환: 표 이름 또는 ""(못 만듦).

        `ROLLUP_FROM` 직전 10거래일 창의 fmp 행에서 뽑는다. 전 구간 스캔은 못 쓴다 -
        `max(trade_date)` 한 번이 21초다(실측). 창을 고정 상수에 매달아 두면 오늘이
        언제든 같은 매핑이 나온다.

        **로컬 백필도 매핑 원천이다.** canonical 이 안 가진 종목을 로컬이 채우는데
        (실측: 025560 841일 · 066970 979일), 그 종목의 실시간 롤업 행은 매핑이 없어
        bare 로 남는다 = 새로 받은 이력과 축이 갈린다. 원천을 넓히면 그대로 붙는다.
        S3 를 먼저 놓아 충돌 시 canonical 이 이긴다. 한 티커가 두 심볼을 가지면
        (시장 이전) 하나만 남긴다 - 조인이 불어나 봉이 복제되는 것부터 막아야 한다.
        """
        src = ["SELECT ticker, source_symbol AS symbol, 0 AS prio FROM s3_intraday_5m "
               "WHERE market = 'KR' AND source_symbol LIKE '%.K_' "
               f"AND trade_date BETWEEN DATE '{ROLLUP_FROM}' - 10 AND DATE '{ROLLUP_FROM}'"]
        if local_src:
            src.append("SELECT regexp_replace(symbol, '\\.(KS|KQ)$', '') AS ticker, "
                       f"symbol, 1 AS prio FROM {local_src} WHERE symbol LIKE '%.K_'")
        try:
            self.con.execute(
                "CREATE OR REPLACE TABLE _symbol_suffix AS SELECT ticker, "
                "first(symbol ORDER BY prio, symbol) AS symbol FROM ("
                + " UNION ALL ".join(src) + ") GROUP BY 1")
        except Exception as e:      # noqa: BLE001 - 매핑 부재는 bare 유지, 침묵 금지
            self.unbound["_symbol_suffix"] = f"{type(e).__name__}: {str(e)[:80]}"
            return ""
        n = self.con.execute("SELECT count(*) FROM _symbol_suffix").fetchone()[0]
        if not n:
            self.unbound["_symbol_suffix"] = f"{ROLLUP_FROM} 직전 10일에 fmp 심볼 없음"
            return ""
        return "_symbol_suffix"

    def _backfill(self, d: Path) -> None:
        """백필 세트: us_market · fx_usdkrw · tau_sidecar · layers_daily(층 분해 재료)
        · etf_holdings_fmp · pit_daily(curated DataGuide 일간 패널 - statics.pit).

        **S3 우선 · 로컬 폴백** — `_bars()` 와 같은 규율이고, S3 프리픽스는 env 가
        없어도 기본값이 있다(DEFAULTS[BACKFILL_S3_ENV]) = 컨테이너에도 원천이 있다.
        어느 경로로 읽었는지 exists 에 남긴다: "S3 (1,004,392행)" / "로컬 (1,004,392행)".
        0행은 문자열로 승격하지 않는다 - 하류가 `exists.get(name)` 의 참/거짓으로
        분기하므로(causeflow·evidence 의 tau_sidecar) 0행이 '있음'으로 보이면 안 된다.
        대신 사유를 backfill_notes 에 남기고 coverage() 가 그것을 읽는다.

        `layers_daily` = 시장(KODEX200) · 섹터 ETF 32 · 미국 전일 지수 6 의 일봉.
        KRX 정보데이터시스템이 죽어(2026-08-02 실측) 업종분류 22종을 못 받는 대신
        **섹터를 ETF 로 잡는다** - 관측 가능한 실제 포트폴리오이고 가중치를 시장이
        정하며(우리가 안 정한다 = 왜곡 없음) 보유 비중을 알아 leave-one-out 이 정확하다.

        `pit_daily` = 주주·신용·공매도·배수·주식수 (248일 × 4,054종목). 긴 형식
        curated 를 셀마다 읽으면 2분 12초라 넓은 형식으로 한 번 접어 둔다.
        """
        for name in BACKFILL_SETS:
            self.exists[name] = 0
            bound = False
            for label, src in backfill_sources(name, d / f"{name}.parquet"):
                try:
                    self.con.execute(f"CREATE OR REPLACE VIEW {name} AS "
                                     f"SELECT * FROM read_parquet('{src}')")
                    n = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                except Exception as e:  # noqa: BLE001 - S3 부재·무권한은 로컬로 내려간다
                    self.backfill_notes[name] = f"{label} 실패: {type(e).__name__}: {str(e)[:80]}"
                    continue
                bound = True
                if n:
                    self.exists[name] = f"{label} ({n:,}행)"
                    break
                self.backfill_notes[name] = f"{label} 0행"
            if bound:
                continue
            # 여기까지 왔으면 S3 도 로컬도 못 읽었다. S3 후보는 기본값 덕에 항상
            # 조립되므로 위 except 가 이미 사유를 남겼다 - 이 setdefault 는 그마저
            # 없을 때의 그물이다(사유 없는 0행은 금지).
            self.backfill_notes.setdefault(name, "읽을 수 있는 원천 없음")
            # 하류 SQL 이 이름을 직접 참조하는 백필은 원천이 없어도 **빈 스키마**로 건다
            # (S3 셋과 같은 규율: 스키마가 보여야 '안 채워진 축'과 '없는 축'이 갈린다).
            # 안 그러면 v_pit 을 쓰는 패널 SQL 전체가 파스 단계에서 죽는다.
            if name in EMPTY_SCHEMA:
                self.con.execute(f"CREATE OR REPLACE VIEW {name} AS {EMPTY_SCHEMA[name]}")

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
            self.con.execute(s3_secret_sql())
        except Exception as e:      # noqa: BLE001 - 자격증명 부재도 커버리지 보고
            # **처방을 사유에 넣는다.** 자격증명 실패는 S3 뷰 33개를 통째로 없애고,
            # 그러면 `s3_etf_holdings` 같은 표가 "그런 표 없음" 으로 보인다 - 원인이
            # 인증인데 증상이 스키마로 나타난다. 로컬은 이름 있는 프로파일을 쓰므로
            # `AWS_PROFILE` 이 없으면 기본 프로파일을 찾다 실패한다(컨테이너는 그 반대).
            hint = ("" if os.environ.get("AWS_PROFILE")
                    else " · 로컬이면 AWS_PROFILE 을 설정해라 (컨테이너는 task role 이라 불필요)")
            self.exists["s3"] = f"실패: {str(e)[:120]}{hint}"
            return
        src = {"hive": "read_parquet('{p}/**/*.parquet', hive_partitioning=true)",
               "glob": "read_parquet('{p}')",
               "ice": "iceberg_scan('{p}')",
               "csv": ("read_csv('{p}/**/*.csv.gz', hive_partitioning=true, "
                       "all_varchar=true, ignore_errors=true)"),
               # 단일 비압축 csv (항목 사전 등). 글롭이 아니라 파일을 짚는다.
               "csvfile": "read_csv('{p}', all_varchar=true)"}
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
        """질의. **지연 등록된 뷰는 여기서 걸린다** - 등록만 해두고 부를 때 건다.

        `bind_s3` 를 아무도 부르지 않아 지연 5셋(`s3_dg_*`)이 영구 미바인딩이었다:
        `SELECT 1 FROM s3_dg_market` 이 'Table does not exist' 였다(실측). 등록과
        실현 사이에 다리가 없으면 등록은 거짓말이다. 스니핑이 비싼 셋만 지연이므로
        첫 조회 때 한 번 물면 된다 - 카탈로그 실패에서만 시도해 정상 경로는 안 느려진다.
        """
        try:
            return self.con.execute(q).fetchall()
        except UnicodeDecodeError as e:
            # **오류가 오류를 가린다.** DuckDB 예외 메시지에 OS 지역화 문구가 섞이면
            # 클라이언트가 그걸 UTF-8 로 못 읽고 UnicodeDecodeError 로 바꿔 던진다.
            # 실측: 구간 층분해의 OOM 이 4회 중 2회 이 모습으로 위장돼, 메모리 문제를
            # 인코딩 문제로 몇 시간 쫓았다. 원문 바이트를 살려 진짜 원인을 남긴다.
            raw = getattr(e, "object", b"")
            msg = bytes(raw).decode("cp949", errors="replace") if raw else str(e)
            raise RuntimeError(f"DuckDB 오류(지역화 메시지 복원): {msg[:400]}") from e
        except Exception:
            hit = [n for n in self.deferred if n in q]
            if not hit:
                raise
            for n in hit:
                self.bind_s3(n)
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
        # 백필은 **어디서 읽었는지까지** 말한다. S3 도 로컬도 못 읽으면 빈 스키마 뷰가
        # 걸려 파스도 질의도 성공하고 0행이 나온다 - 그 침묵이 층 분해 None 과 전 튜플
        # 판정불가의 원인이었다. 0행은 '있음'에 세지 않고 사유와 함께 따로 적는다.
        read = [f"{n} {self.exists[n]}" for n in BACKFILL_SETS if self.exists.get(n)]
        gone = [n for n in BACKFILL_SETS if not self.exists.get(n)]
        lines.append(f"백필 {len(read)}/{len(BACKFILL_SETS)}"
                     + (" — " + " · ".join(read) if read else ""))
        if gone:
            # 사유는 한 줄에 열 개가 붙으므로 짧게 자른다. 전문은 backfill_notes 에 있다.
            lines.append("  0행/부재(질의는 성공한다 - 결과가 없을 뿐이라 더 위험하다):\n    "
                         + "\n    ".join(f"{n}: {self.backfill_notes.get(n, '사유 미기록')[:60]}"
                                         for n in gone))
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

    def bars(self, ticker: str, day: str, as_of: datetime | None = None) -> list[tuple]:
        """(ts, close) — tree.decompose 의 입력. 심볼 규약: '005930.KS'.

        `as_of` 는 **장중 PIT** 다. 롤업은 닫힌 버킷만 쓰므로 파티션에 나타난 봉은
        이미 확정분이지만, 그렇다고 13:00 에 그날 파티션을 통째로 읽어도 되는 건
        아니다 - 15:30 봉도 같은 파티션에 있고 날짜 절단은 그것을 못 막는다.
        미래를 읽고 과거를 설명하면 무엇이든 설명된다. `as_of` 를 주면
        `available_at <= as_of`, 즉 **그 시각에 실제로 알 수 있었던 봉**만 낸다.
        None 이면 그날 전량 - 장 마감 뒤 사후 분석의 정당한 기본값이다.
        """
        if not self.exists.get("bars_5m"):
            raise RuntimeError("bars_5m 없음 — 백필 먼저 (coverage 참조)")
        # trade_date 로 거른다 - 하이브 파티션 프루닝이 걸려야 1.5억 행을 안 읽는다.
        cut = f" AND available_at <= TIMESTAMP '{as_of:%Y-%m-%d %H:%M:%S}'" if as_of else ""
        return self.sql(
            f"SELECT ts, close FROM bars_5m WHERE symbol='{ticker}' "
            f"AND trade_date = DATE '{day}'{cut} ORDER BY ts")

    def prev_close(self, ticker: str, day: str) -> float:
        """직전 거래일 종가. **최근 10일로 창을 좁힌다** (20R).

        전에는 `CAST(ts AS DATE) < day` 라 하이브 파티션 프루닝이 안 걸려 916일치
        전 이력을 스캔했다 - 셀 하나에 88초, 배치가 불가능한 비용이었다. 연휴가
        10일을 넘으면 창을 넓혀 재시도한다(있는 것을 못 찾는 일은 없다).
        """
        for back in (10, 40, 400):
            row = self.sql(
                f"SELECT close FROM bars_5m WHERE symbol='{ticker}' "
                f"AND trade_date < DATE '{day}' AND trade_date >= DATE '{day}' - {back} "
                f"ORDER BY ts DESC LIMIT 1")
            if row:
                return float(row[0][0])
        raise RuntimeError(f"{ticker} {day} 이전 봉 없음")

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


__all__ = ["BACKFILL_SETS", "CausalLake", "RDB_TABLES", "ROLLUP_FROM",
           "backfill_sources", "rdb_dsn_from_env", "s3_secret_sql", "session_pragmas"]
