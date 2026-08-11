"""duck.py 런타임 해소 계약 — 노트북에서 되던 것이 컨테이너에서 조용히 죽는 지점들.

배포 감사가 찾은 네 가지가 전부 같은 병이다: **환경이 다르면 원천이 빠지는데 코드가
그 부재를 성공처럼 처리한다.** DSN 이 안 잡히면 손뷰 13개가 미생성, 자격증명이 안
잡히면 S3 뷰 33개가 미등록, 백필이 없으면 빈 스키마 뷰가 걸려 파스도 질의도 성공한 뒤
0행을 돌려준다. 그래서 검사 대상은 '읽었는가'가 아니라 **'어디서 읽었다고 말하는가'** 다.

DuckDB 실물 연결이 필요 없는 것은 순수 함수로, 필요한 것(백필 우선순위)은 in-memory
연결 + 로컬 parquet 로 검사한다 — S3 는 절대 건드리지 않는다.
"""
import datetime as dt
import re
import tempfile

import duckdb
import pytest

from edge_analysis.statics.duck import (
    BACKFILL_SETS, MARKET_PROXY_TICKER, MIN_LANDED_TICKERS, SECTOR_ROLLUP_VENDOR, CausalLake,
    backfill_sources, iceberg_covers, rdb_dsn_from_env, s3_secret_sql, session_pragmas)

_ENVS = ("EDGE_RDB_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
         "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_PROFILE", "AWS_REGION",
         "DUCKDB_S3_CHAIN", "DUCKDB_MEMORY_LIMIT", "DUCKDB_TEMP_DIR",
         # `off` 로 켜 둔 채 스위트를 돌리면 `_bars_iceberg` 가 첫 줄에서 반환해
         # iceberg 검사들이 통째로 무의미해진다(질의를 안 내므로 탐침 단언은 에러).
         "CAUSAL_BACKFILL_S3", "EDGE_BARS_ICEBERG")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # 개발 노트북엔 AWS_PROFILE 이 살아 있다 — 그대로 두면 '컨테이너' 경로를 못 잰다.
    for e in _ENVS:
        monkeypatch.delenv(e, raising=False)


# ── DSN 해소 ────────────────────────────────────────────────────────────
def test_dsn_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("EDGE_RDB_DSN", "host=tunnel port=15432 dbname=edge")
    monkeypatch.setenv("PGHOST", "rds.internal")
    monkeypatch.setenv("PGDATABASE", "edge")
    assert rdb_dsn_from_env() == "host=tunnel port=15432 dbname=edge"


def test_dsn_assembles_from_pg_vars(monkeypatch):
    # Fargate task 가 실제로 주는 형태. 여기서 조립이 안 되면 배포판은 rdb 부재다.
    monkeypatch.setenv("PGHOST", "rds.internal")
    monkeypatch.setenv("PGDATABASE", "edge")
    monkeypatch.setenv("PGUSER", "edge_app")
    monkeypatch.setenv("PGPASSWORD", "s3cr3t")
    dsn = rdb_dsn_from_env()
    assert dsn == "host=rds.internal port=5432 dbname=edge user=edge_app password=s3cr3t"


def test_dsn_omits_password_for_iam_auth(monkeypatch):
    # 비밀번호 없음 = IAM 인증. 빈 password 절을 붙이면 붙었다 실패하며 사유가 흐려진다.
    monkeypatch.setenv("PGHOST", "rds.internal")
    monkeypatch.setenv("PGDATABASE", "edge")
    monkeypatch.setenv("PGPORT", "6432")
    assert rdb_dsn_from_env() == "host=rds.internal port=6432 dbname=edge"


def test_dsn_refuses_half_configuration(monkeypatch):
    # 반쪽 설정으로 조립하면 '자격증명 실패'와 '설정 누락'이 같은 문장이 된다.
    monkeypatch.setenv("PGHOST", "rds.internal")
    monkeypatch.setenv("PGUSER", "edge_app")
    assert rdb_dsn_from_env() == ""
    monkeypatch.delenv("PGHOST")
    monkeypatch.setenv("PGDATABASE", "edge")
    assert rdb_dsn_from_env() == ""


# ── S3 자격증명 ─────────────────────────────────────────────────────────
def test_secret_uses_aws_default_chain_in_ecs(monkeypatch):
    # ECS task role은 container credentials provider다. 명시 CHAIN의 `instance`는 EC2
    # metadata만 보므로 상대 URI가 있어도 자격증명을 못 찾는다. 기본 AWS SDK 체인에
    # 맡겨야 container provider가 선택된다.
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/credentials/task")
    sql = s3_secret_sql()
    assert "PROFILE" not in sql
    assert "CHAIN" not in sql
    assert "REGION 'ap-northeast-2'" in sql


def test_secret_keeps_profile_when_set(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "work")
    assert "PROFILE 'work'" in s3_secret_sql()


def test_secret_chain_and_region_follow_env(monkeypatch):
    monkeypatch.setenv("DUCKDB_S3_CHAIN", "instance")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    sql = s3_secret_sql()
    assert "CHAIN 'instance'" in sql and "REGION 'us-east-1'" in sql
    assert "ap-northeast-2" not in sql               # 하드코딩이 남아 있으면 여기서 죽는다


def test_secret_ignores_blank_env(monkeypatch):
    # 컨테이너 정의는 변수를 '있지만 빈 값'으로 주는 일이 잦다 — 기본값을 덮으면 안 된다.
    monkeypatch.setenv("AWS_PROFILE", "")
    monkeypatch.setenv("AWS_REGION", "  ")
    sql = s3_secret_sql()
    assert "PROFILE" not in sql and "REGION 'ap-northeast-2'" in sql


# ── 세션 한도 ───────────────────────────────────────────────────────────
def test_pragmas_bound_memory_and_open_spill(monkeypatch):
    # 한도가 없으면 OOM 으로 태스크가 죽는다. 스필 경로가 있어야 죽는 대신 느려진다.
    # 경로는 플랫폼이 정한다 - '/tmp' 하드코딩은 Windows 에서 git-bash 매핑에 기대는
    # 우연이었고, 그게 없으면 스필이 안 열려 정확히 필요할 때 장치가 없다.
    #
    # `preserve_insertion_order=false` 도 계약이다: 5분봉 시각창 집계(64M 행 스캔)가
    # 순서 버퍼 때문에 1.5GB 한도에서 **OOM 으로 죽었다**(실측 4회 중 2회). 우리 SQL 은
    # 순서가 중요한 곳마다 ORDER BY 를 쓰므로 보존은 비용뿐이다 - 빠지면 다시 죽는다.
    assert session_pragmas() == ("SET TimeZone='Asia/Seoul'",
                                 "SET enable_progress_bar=false",
                                 "SET preserve_insertion_order=false",
                                 "SET memory_limit='1.5GB'",
                                 f"SET temp_directory='{tempfile.gettempdir()}'")
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "6GB")
    monkeypatch.setenv("DUCKDB_TEMP_DIR", "/mnt/spill")
    assert session_pragmas()[3:] == ("SET memory_limit='6GB'",
                                     "SET temp_directory='/mnt/spill'")


def test_pragmas_are_accepted_by_duckdb():
    # 문자열만 맞고 DuckDB 가 거부하면 연결 자체가 죽는다 — 실물로 한 번 건다.
    con = duckdb.connect()
    for p in session_pragmas():
        con.execute(p)
    # 1.5GB(10진) 을 DuckDB 는 "1.3 GiB" 로 보고한다. 정확한 표기가 아니라 **한도가
    # 호스트 메모리가 아니라는 것**이 계약이다 - 기본값은 물리 메모리의 80% 다.
    got = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    assert got.endswith("GiB") and 1.0 < float(got.split()[0]) < 1.5
    assert con.execute("SELECT current_setting('temp_directory')").fetchone()[0] \
        == tempfile.gettempdir()


# ── 백필 우선순위 ───────────────────────────────────────────────────────
def test_backfill_sources_prefers_s3_and_joins_without_double_slash(monkeypatch, tmp_path):
    local = tmp_path / "pit_daily.parquet"
    local.write_bytes(b"")
    monkeypatch.setenv("CAUSAL_BACKFILL_S3", "s3://lake/analysis/backfill/")
    # s3 는 // 를 빈 키 세그먼트로 봐서 조용히 0파일이 된다 - 슬래시는 한 번만.
    assert backfill_sources("pit_daily", local) == (
        ("S3", "s3://lake/analysis/backfill/pit_daily.parquet"),
        ("로컬", local.as_posix()))


def test_backfill_sources_defaults_to_the_deployed_s3_prefix(tmp_path):
    """env 가 없어도 S3 후보가 서야 한다 — 안 서면 기본값이 '로컬 전용'이고,
    로컬이 없는 컨테이너는 빈 스키마 뷰로 **질의 성공 · 0행**을 낸다.

    이 문자열은 terraform 의 `analysis_backfill_s3_prefix`(Fargate env
    CAUSAL_BACKFILL_S3 로 주입)와 **같아야** 한다. 여기가 갈리면 노트북만 살고
    배포판은 아무것도 없는 프리픽스를 읽는다 - 그래서 리터럴로 박아 카나리로 쓴다.
    """
    assert backfill_sources("pit_daily", tmp_path / "nope.parquet") == (
        ("S3", "s3://edge-dev-pipeline-lake/analysis/backfill/pit_daily.parquet"),)


def test_backfill_sources_drops_absent_local(monkeypatch, tmp_path):
    monkeypatch.setenv("CAUSAL_BACKFILL_S3", "s3://lake/analysis/backfill")
    assert backfill_sources("pit_daily", tmp_path / "nope.parquet") == (
        ("S3", "s3://lake/analysis/backfill/pit_daily.parquet"),)


def _lake(tmp_path):
    """실물 연결 위의 _backfill 만 떼어 돌린다 — __init__ 은 S3·RDB 로 나간다."""
    lk = CausalLake.__new__(CausalLake)
    lk.con = duckdb.connect()
    lk.exists, lk.backfill_notes = {}, {}
    return lk


def _parquet(con, path, rows):
    con.execute(f"COPY (SELECT * FROM (VALUES {rows}) t(trade_date, ticker, for_net)) "
                f"TO '{path.as_posix()}' (FORMAT parquet)")


def test_backfill_reads_s3_first_and_says_so(tmp_path, monkeypatch):
    """우선순위와 표기를 동시에 검사한다. 실제 S3 는 안 건드린다 - 원격 후보 자리에
    로컬 디렉터리를 꽂아 '먼저 시도되는 자리'만 검증한다(경로 조립은 위 검사가 본다)."""
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir(), local.mkdir()
    lk = _lake(tmp_path)
    _parquet(lk.con, remote / "flow_daily.parquet", "(DATE '2026-08-03', '005930', 1.0)")
    _parquet(lk.con, local / "flow_daily.parquet",
             "(DATE '2026-08-03', '005930', 2.0), (DATE '2026-08-04', '005930', 3.0)")
    monkeypatch.setenv("CAUSAL_BACKFILL_S3", remote.as_posix())

    CausalLake._backfill(lk, local)

    assert lk.exists["flow_daily"] == "S3 (1행)"      # 로컬이 2행이므로 폴백이면 여기서 죽는다
    assert lk.con.execute("SELECT for_net FROM flow_daily").fetchall() == [(1.0,)]


def test_backfill_falls_back_to_local_when_s3_missing(tmp_path, monkeypatch):
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir(), local.mkdir()
    lk = _lake(tmp_path)
    _parquet(lk.con, local / "flow_daily.parquet",
             "(DATE '2026-08-03', '005930', 2.0), (DATE '2026-08-04', '005930', 3.0)")
    monkeypatch.setenv("CAUSAL_BACKFILL_S3", remote.as_posix())

    CausalLake._backfill(lk, local)

    # 같은 데이터라도 **어디서 읽었는지**가 달라야 한다 - 스모크에서 사람이 보는 문자열이다.
    assert lk.exists["flow_daily"] == "로컬 (2행)"
    assert "S3 실패" in lk.backfill_notes["flow_daily"]


def test_backfill_absence_is_falsy_and_carries_a_reason(tmp_path, monkeypatch):
    # 기본 프리픽스는 실제 버킷이다 - 없는 로컬 경로로 덮어 S3 를 안 건드린다.
    monkeypatch.setenv("CAUSAL_BACKFILL_S3", (tmp_path / "nowhere").as_posix())
    lk = _lake(tmp_path)

    CausalLake._backfill(lk, tmp_path)

    # 0행을 문자열로 승격하면 exists.get(name) 참/거짓 분기가 전부 뒤집힌다
    # (evidence 의 tau_sidecar). 부재는 falsy 로 남되 사유는 남긴다.
    assert all(lk.exists[n] == 0 for n in BACKFILL_SETS)
    assert not any(lk.exists[n] for n in BACKFILL_SETS)
    assert "S3 실패" in lk.backfill_notes["layers_daily"]
    # 빈 스키마 뷰는 그래도 걸려야 한다 - 하류 패널 SQL 이 파스 단계에서 죽지 않게.
    assert lk.con.execute("SELECT count(*) FROM pit_daily").fetchone()[0] == 0


def test_coverage_does_not_call_zero_rows_present(tmp_path, monkeypatch):
    local = tmp_path / "local"
    local.mkdir()
    # 기본 프리픽스는 실제 버킷이다 - 없는 로컬 경로로 덮어 S3 를 안 건드린다.
    monkeypatch.setenv("CAUSAL_BACKFILL_S3", (tmp_path / "nowhere").as_posix())
    lk = _lake(tmp_path)
    _parquet(lk.con, local / "flow_daily.parquet", "(DATE '2026-08-03', '005930', 2.0)")
    lk.rows, lk.bound, lk.unbound, lk.s3, lk.deferred, lk.day = {}, {}, {}, {}, {}, ""
    lk.effective = {}

    CausalLake._backfill(lk, local)
    out = lk.coverage()

    assert "백필 1/10 — flow_daily 로컬 (1행)" in out
    assert "0행/부재" in out and "layers_daily" in out


# ── 5분봉 정본 신선도 ────────────────────────────────────────────────────
def test_empty_or_stale_iceberg_is_not_treated_as_the_source_of_truth():
    """**붙었다 ≠ 오늘을 담고 있다.**

    `_bars_iceberg` 는 ATTACH 와 뷰 생성이 되면 True 라 정본이 비어 있어도 합집합 폴백을
    건너뛴다. 2026-08-06 실측: 정본 표는 그날 0행이었고 canonical 롤업 산출은 정상이었는데,
    Athena·버킷 권한이 붙자 엔진이 낡은 정본을 잡고 5분봉을 통째로 못 봤다.
    """
    day = "2026-08-06"
    assert iceberg_covers(None, day, 0, 0) is False                    # 빈 표
    assert iceberg_covers(dt.date(2026, 8, 5), day, 0, 0) is False     # 하루 낡음
    assert iceberg_covers(dt.date(2026, 8, 6), day, 366, 78) is True   # 그날이 있다
    # **최신일이 아니라 그날의 착지가 기준이다.** 상류가 띄엄띄엄 채우면 '최신은
    # 최신인데 그날은 없는' 상태가 난다.
    # ⚠️ 이 판정은 **폭만 재고 깊이는 안 잰다** - 실측 8/5 처럼 12:45 에 끊긴 날은
    # 366종이 다 있어 그대로 통과한다. 세션 절단은 이 가드 밖의 일감이다.
    assert iceberg_covers(dt.date(2026, 8, 7), day, 0, 0) is False


def test_partial_landing_is_not_the_source_of_truth():
    """**13종만 착지한 날은 정본이 아니다.** 행 수만 보던 자가 정확히 여기로 샜다.

    실측(Athena 2026-08-07): 8/3·8/4 는 13종뿐이었고 **그 안에 069500(시장)이 있었다**
    - 행이 있고 시장 지수도 있으니 이전 판정으로는 전부 통과였다. 통과하면 나머지
    종목은 `_on()` 이 조용히 버리고, 그 13종으로 세운 층이 하루 전체의 설명으로 실린다.

    임계는 관측 분포의 빈 구간이다: 부분 착지 13 · 롤업 정상일 366 · fmp 정상일 1,270.
    **정상일 폭이 시대마다 다르므로 상한 쪽(1,200)에 기준을 두면 안 된다** - 롤업 시대가
    통째로 폴백이 된다.
    """
    day = "2026-08-03"
    # 실측 부분 착지. **그 13종에는 069500 이 있었다** - 시장 프록시만 보는 판정으로는
    # 못 걸렀을 날이고, 그래서 착지 폭을 함께 본다.
    assert iceberg_covers(dt.date(2026, 8, 3), day, 13, 78) is False
    assert iceberg_covers(dt.date(2026, 8, 5), "2026-08-05", 366, 78) is True   # 롤업 정상일
    assert iceberg_covers(dt.date(2026, 7, 31), "2026-07-31", 1270, 78) is True  # fmp 정상일
    # **경계를 고정한다.** 없으면 임계를 14~366 어디로 옮겨도 스위트가 초록이라
    # "부분 착지를 막는다" 는 계약을 단언이 하나도 안 지킨다(Rule 9).
    assert iceberg_covers(dt.date(2026, 8, 3), day, MIN_LANDED_TICKERS - 1, 78) is False
    assert iceberg_covers(dt.date(2026, 8, 3), day, MIN_LANDED_TICKERS, 78) is True


def test_cardinality_alone_does_not_make_a_source_of_truth():
    """**무엇이 착지했는지도 본다.** 임계 100 은 롤업 정상일(366)의 27%라, 종목 수만
    보면 '3분의 1만 착지 + 시장 프록시 없음' 이 통과한다 - 그러면 시장 층이 없는 정본을
    정본으로 확정하고 `stale_5m` 은 빈 문자열이라 Athena 오프로드까지 그대로 탄다.
    """
    assert iceberg_covers(dt.date(2026, 8, 5), "2026-08-05", 120, 0) is False
    assert iceberg_covers(dt.date(2026, 8, 5), "2026-08-05", 120, 78) is True


def test_market_proxy_constant_does_not_drift_from_layers():
    """`duck.MARKET_PROXY_TICKER` 는 `layers.MARKET_CODE` 와 같아야 한다.

    import 로 묶을 수 없다 - layers → athena → duck 이라 역방향은 순환이다. 갈리면
    가드가 엉뚱한 종목을 찾아 **정상일을 전부 폴백**시키고, 그 폴백은 곧 층 미계측이다.
    묶을 수 없는 두 자리는 테스트가 지킨다.
    """
    from edge_analysis.statics.layers import MARKET_CODE

    assert MARKET_PROXY_TICKER == MARKET_CODE


def test_freshness_is_not_judged_without_an_asked_day():
    """기준일이 없으면 판정하지 않는다 — 자가검사·탐색 실행의 기존 동작을 안 바꾼다.

    다만 **빈 표는 기준일과 무관하게 정본이 아니다.** 그건 신선도가 아니라 부재다.
    """
    assert iceberg_covers(dt.date(2020, 1, 1), "", 0, 0) is True
    assert iceberg_covers(None, "", 0, 0) is False


class _RecordingCon:
    """`_bars_iceberg` 가 내는 SQL 을 받아 적고 `max(trade_date)` 만 답한다."""

    def __init__(self, newest, day_tks, mkt_rows):
        self.newest, self.day_tks, self.mkt_rows, self.sql = newest, day_tks, mkt_rows, []

    def execute(self, q):
        self.sql.append(q)
        return self

    def fetchone(self):
        return (self.newest, self.day_tks, self.mkt_rows)


def _iceberg_lake(newest, asked_day, day_tks=0, mkt_rows=0):
    lk = CausalLake.__new__(CausalLake)
    lk.con = _RecordingCon(newest, day_tks, mkt_rows)
    lk.exists, lk.unbound, lk.asked_day, lk.stale_5m = {}, {}, asked_day, ""
    return lk


def test_stale_iceberg_falls_back_and_says_why():
    """판정이 **배선돼 있어야** 한다 — 순수 함수만 맞고 호출이 빠지면 아무것도 안 바뀐다.

    그리고 폴백은 조용하면 안 된다(Rule 12): 왜 정본을 안 썼는지가 커버리지에 남아야
    `statics.coverage` 로그가 그걸 드러낸다.
    """
    lk = _iceberg_lake(dt.date(2026, 8, 5), "2026-08-06", day_tks=0)

    assert lk._bars_iceberg() is False
    assert "bars_5m_iceberg" in lk.unbound
    assert "bars_5m" not in lk.exists          # 정본이라 말하지 않는다
    # **부재 분기의 문구도 고정한다.** 앞 두 삼항을 맞바꾸면 적재가 한 번도 안 돈 날이
    # "돌다 말았다"(부분 착지)로 보고되는데, 그 스왑을 잡는 단언이 없었다(Rule 9).
    assert "요청일이 없다" in lk.stale_5m and "0종" in lk.stale_5m


def test_partial_landing_says_partial_not_absent():
    """**부재와 부분 착지는 다른 일감이다** — 없는 것은 적재가 안 돈 것이고, 13종은
    돌다 만 것이다. 한 문장으로 뭉치면 상류를 볼 사람이 엉뚱한 데를 본다.
    """
    lk = _iceberg_lake(dt.date(2026, 8, 3), "2026-08-03", day_tks=13, mkt_rows=78)

    assert lk._bars_iceberg() is False
    assert "부분 착지" in lk.stale_5m and "13종" in lk.stale_5m
    assert "요청일이 없다" not in lk.stale_5m
    assert "bars_5m_iceberg" in lk.unbound
    # **탐침 질의의 형상도 고정한다.** 대역이 SQL 을 안 보고 고정 튜플을 돌려주므로,
    # 질의를 `count(*)` 로 되돌려도(=이 PR 이 막으려는 그 회귀) 스위트가 초록이다.
    probe = next(q for q in lk.con.sql if "max(trade_date)" in q)
    assert "count(DISTINCT ticker)" in probe and MARKET_PROXY_TICKER in probe
    # 🔴 **가격 집합 제한이 스캔 전체에 걸려야 한다** (ALPHA-941). 같은 데이터셋에 사는
    # 업종지수 파생을 집계 `FILTER` 안에서만 빼면 `max(trade_date)` 가 안 걸리고, 표에
    # 업종지수 행만 있을 때 `newest` 가 non-NULL 이 된다 — `asked_day` 가 빈 호출(생성
    # 지점 23곳 중 21곳)은 `iceberg_covers` 가 거기서 곧장 True 라 **가격 봉이 없는 표를
    # 정본으로 승인**한다. `FILTER (...)` 를 걷어내고도 남아야 스캔 절에 있는 것이다 —
    # 있는지만 보면 집계 안으로 되돌아간 회귀를 그대로 통과시킨다.
    assert SECTOR_ROLLUP_VENDOR in probe, "업종지수를 안 거른다"
    assert SECTOR_ROLLUP_VENDOR in re.sub(r"FILTER\s*\([^)]*\)", "", probe), (
        "가격 집합 제한이 집계 FILTER 안에만 있다 — max(trade_date) 가 안 걸린다")


def test_missing_market_proxy_is_named_as_such():
    """세 번째 사유도 도달 가능하고, 앞 둘과 다른 문장이어야 한다.

    100종을 넘겨도 시장 프록시가 없으면 층이 안 선다 - '부분 착지'라고 적으면
    착지 폭을 볼 사람이 폭은 멀쩡한데 왜 막혔는지 못 찾는다.
    """
    lk = _iceberg_lake(dt.date(2026, 8, 5), "2026-08-05", day_tks=150, mkt_rows=0)

    assert lk._bars_iceberg() is False
    assert "시장 프록시가 없다" in lk.stale_5m and MARKET_PROXY_TICKER in lk.stale_5m
    assert "부분 착지" not in lk.stale_5m


def test_stale_iceberg_does_not_pay_the_45day_materialization():
    """낡은 표에 `_icb_suffix` 45일 스캔을 치르고 버리면 1분 주기에서 그대로 낭비다.

    판정이 그 물질화 **앞**에 있어야 한다 - 순서가 곧 비용이다.
    """
    lk = _iceberg_lake(dt.date(2026, 8, 5), "2026-08-06", day_tks=0)
    lk._bars_iceberg()

    assert not any("_icb_suffix" in q for q in lk.con.sql)
    assert not any("CREATE OR REPLACE VIEW bars_5m" in q for q in lk.con.sql)


def test_fresh_iceberg_still_becomes_the_source_of_truth():
    """폴백이 상시화되면 정본을 둔 이유가 사라진다 — 신선하면 그대로 쓴다."""
    lk = _iceberg_lake(dt.date(2026, 8, 6), "2026-08-06", day_tks=366, mkt_rows=78)

    assert lk._bars_iceberg() is True
    assert "Glue Iceberg" in lk.exists["bars_5m"]
    # 폴백 경로만 검사하면 정본 경로가 깨져도 통과한다 - 뷰까지 실제로 세우는지 본다.
    assert any("_icb_suffix" in q for q in lk.con.sql)
    assert any("CREATE OR REPLACE VIEW bars_5m" in q for q in lk.con.sql)
