"""duck.py 런타임 해소 계약 — 노트북에서 되던 것이 컨테이너에서 조용히 죽는 지점들.

배포 감사가 찾은 네 가지가 전부 같은 병이다: **환경이 다르면 원천이 빠지는데 코드가
그 부재를 성공처럼 처리한다.** DSN 이 안 잡히면 손뷰 13개가 미생성, 자격증명이 안
잡히면 S3 뷰 33개가 미등록, 백필이 없으면 빈 스키마 뷰가 걸려 파스도 질의도 성공한 뒤
0행을 돌려준다. 그래서 검사 대상은 '읽었는가'가 아니라 **'어디서 읽었다고 말하는가'** 다.

DuckDB 실물 연결이 필요 없는 것은 순수 함수로, 필요한 것(백필 우선순위)은 in-memory
연결 + 로컬 parquet 로 검사한다 — S3 는 절대 건드리지 않는다.
"""
import tempfile

import duckdb
import pytest

from edge_analysis.statics.duck import (
    BACKFILL_SETS, CausalLake, backfill_sources, rdb_dsn_from_env, s3_secret_sql,
    session_pragmas)

_ENVS = ("EDGE_RDB_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
         "AWS_PROFILE", "AWS_REGION", "DUCKDB_S3_CHAIN", "DUCKDB_MEMORY_LIMIT",
         "DUCKDB_TEMP_DIR", "CAUSAL_BACKFILL_S3")


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
def test_secret_omits_profile_in_container():
    # 컨테이너엔 ~/.aws/config 가 없다 — PROFILE 절이 있으면 시크릿 생성부터 깨진다.
    sql = s3_secret_sql()
    assert "PROFILE" not in sql
    assert "CHAIN 'env;instance;config;sso'" in sql   # instance 가 앞에 있어야 task role 이 잡힌다
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
    # (causeflow·evidence 의 tau_sidecar). 부재는 falsy 로 남되 사유는 남긴다.
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
