"""무거운 집계를 **Athena 로 내보내는 표면**. DuckDB 는 작은 결과만 받아 조인한다.

왜 있나
-------
노트북 회선으로 S3 를 직접 읽는 질의가 세션마다 수백 MB 를 끌어온다. 실측(2026-08-05
· `bars_5m` = Glue Iceberg `market_data_kr.edge_intraday_5m`):

    층분해 시각창(`layers._CLOCK_SQL`)   376,395,649 B · Iceberg 데이터 파일 729개
                                          → 심볼 1·4·8·81 어느 쪽이든 **한 바이트도** 안 변한다

DuckDB 의 `iceberg_scan` 이 `bucket(16, symbol)` 파티션 통계를 안 쓴다. 술어를 표현식
(`regexp_replace(symbol,…) IN (…)`)에서 날것 컬럼(`symbol IN ('X','X.KS','X.KQ')`)으로
바꿔도 729파일 376.4MB 그대로였다 - 리더가 프루닝을 안 하는 것이라 레이아웃을 더
손봐도 안 낫는다. 같은 술어로 Athena 는 프룬다(실측 2심볼 230.6MB → 47.5MB).

그래서 집계는 Athena 가 하고 노트북은 결과 CSV 만 받는다. 같은 패널이 376.4MB →
0.379MB 다(993배).

결과를 받는 길: **결과 CSV 를 DuckDB `read_csv` 로 바로 읽는다**
----------------------------------------------------------------
`GetQueryResults` 는 페이지당 1,000행이고 값이 전부 문자열이라, 7천행이면 8왕복 +
호출자마다 재캐스팅이다. 결과 CSV 는 S3 객체 하나이고 DuckDB 가 그걸 그대로 스캔한다 -
이 모듈이 DuckDB 표면에 붙는 방식과 같은 모양이 된다.

**열 타입은 Athena 메타데이터로 못 박는다.** CSV 스니핑에 맡기면 `'005930'` 이 BIGINT
5930 이 되어 심볼 축이 조용히 깨진다. Athena 는 결과 스키마를 알고 있으므로 한 번
물어서(`get_query_results` 1행) `read_csv(columns=…)` 로 박는다.

무엇을 안 하나
--------------
큰 결과를 받지 않는다(`MAX_RESULT_BYTES` · `MAX_RESULT_ROWS`). 결과가 크면 오프로드가
아니라 원천을 한 번 더 복사하는 것이다 - 그건 `ResultTooLarge` 로 즉사시킨다.
**`AthenaUnavailable` 과 갈라 둔 이유가 그것이다**: 부재는 폴백해도 되지만 설계 착오는
폴백하면 안 된다(DuckDB 로 돌아가 봐야 더 많이 읽는다).

심볼 회원(`layers_daily`)처럼 작고 로컬에 있는 표는 안 올린다. 786k행 parquet 이고
DuckDB 가 자유롭게 읽는다 - 올리면 같은 사실이 두 카탈로그에 살고, 둘이 어긋나는 날
'섹터가 사라진' 것처럼 보인다.

실패는 조용하면 안 된다
-----------------------
Athena 가 못 돌면 `AthenaUnavailable`. 빈 리스트로 돌려주면 호출자는 그걸 '그 계열이
없는 날'로 읽고 산문이 "섹터 부재"라고 거짓말한다 - 이 저장소가 계속 싸워 온 조용한
부재가 정확히 그것이다. 진짜 0행일 때만 빈 리스트다.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from itertools import groupby

from .duck import AWS_PROFILE_ENV, AWS_REGION_ENV
from .duck import DEFAULTS as _DUCK_DEFAULTS

WORKGROUP_ENV = "EDGE_ATHENA_WORKGROUP"
DB_ENV = "EDGE_ATHENA_DB"
TABLE_ENV = "EDGE_ATHENA_BARS_TABLE"
OUTPUT_ENV = "EDGE_ATHENA_OUTPUT"

# 기본값은 **실제로 도는 이름들**이다 - 워크그룹 `market_data` 와 그 결과 위치는
# `.tmp/collect/iceberg_bars.py` 가 5분봉 Iceberg 표를 만든 자리와 같다. 리전은
# `duck.DEFAULTS` 에서 가져온다: 같은 계정·같은 S3 를 보는데 기본값이 두 군데 살면
# 언젠가 갈리고, 갈린 날 한쪽만 조용히 빈 결과를 낸다.
DEFAULTS = {
    WORKGROUP_ENV: "market_data",
    DB_ENV: "market_data_kr",
    TABLE_ENV: "edge_intraday_5m",
    OUTPUT_ENV: "s3://market-data-393229433969/athena-results/",
    AWS_REGION_ENV: _DUCK_DEFAULTS[AWS_REGION_ENV],
}

POLL_SEC = 1.0        # 상태 폴링 간격. 대상 집계가 실측 2~7초라 1초면 낭비가 없다
TIMEOUT_SEC = 300.0   # 5분이면 Athena 쪽이 병든 것이다 - 기다리지 말고 폴백

# **왕복의 고정 비용** — 임계를 정하는 유일한 근거다. 스캔 0 · 결과 10B 인 질의
# (`SELECT 1`)를 5회: 1.94 · 2.03 · 2.03 · 2.22 · 2.70초 (중앙값 2.03).
# 즉 오프로드는 '2초를 내고 로컬 수신 바이트를 사는' 거래다. 대상 질의가 지금
# 376.4MB 를 끌어오므로 그 2초는 어디서든 남는다 - 심볼 수 같은 축으로 가를 이유가
# 없다(실측: DuckDB 경로 바이트는 심볼 1~81 에서 완전히 평평하다). 남는지 안 남는지를
# 정하는 것은 **결과 크기**뿐이고, 그 상한이 아래 둘이다.
#
# **결과 상한.** 오프로드의 정의가 '작은 결과'다. 넘으면 옮길 대상이 아니었던 것이다.
#   바이트: 내려받기 **전에** S3 HEAD 로 막는다 - 이게 실제로 회선을 지키는 관문이다.
#           32MB 는 우리가 없애려는 크기(376.4MB)의 1/10 이자, 실측 clock_panel 결과
#           0.379MB 의 84배 - 정상 오프로드는 절대 안 닿고 패널 복사는 반드시 걸린다.
#           실측으로 걸려야 하는 것들: `v_pit` 결과 CSV 191.8MB · `_fl` 110.9MB.
#   행 수:  파싱 뒤 한 번 더. 열이 좁으면 바이트는 통과해도 행이 패널 크기일 수 있다.
#           10만은 실측 clock_panel 7,071행의 14배, pit_daily 233만행의 1/23 이다.
MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_RESULT_ROWS = 100_000

# 패널 이력 창(달력일). **상한만 걸면 표 전체를 긁는다** - 실측(2026-08-05 · query
# d100beea): 198심볼 × ~800거래일 = 159,572행으로 상한을 넘겨 런이 통째로 죽었다.
# 행 수는 심볼 × 날짜 축이라 **시각창을 좁혀도 안 줄어든다** - 줄일 수 있는 축은 날짜다.
#
# 60(`layers.BETA_WINDOW`)거래일을 채우는 데 주말·공휴일 감안 ~88 달력일이 든다. 120 은
# 그 위의 여유다 - 짧게 잡으면 `layers.MIN_BETA_N` 미달로 층이 **조용히** 후보에서
# 빠진다. 값을 여기 적지 않는다: 요건은 움직이고(ALPHA-849 에서 40→20) 박아 두면 옮길
# 때 이 주석만 거짓이 된다. 창은 요건이 낮아져도 60 그대로다 - 있으면 다 쓴다.
LOOKBACK_DAYS = 120

# Athena 타입 → DuckDB 타입. 없는 것은 VARCHAR 로 둔다 - 모르는 것을 숫자로 만드는
# 것보다 문자열로 두는 쪽이 안전하다(반대는 값이 조용히 바뀐다).
_TYPES = {"boolean": "BOOLEAN", "tinyint": "BIGINT", "smallint": "BIGINT",
          "integer": "BIGINT", "int": "BIGINT", "bigint": "BIGINT",
          "real": "DOUBLE", "float": "DOUBLE", "double": "DOUBLE", "decimal": "DOUBLE",
          "date": "DATE", "timestamp": "TIMESTAMP"}

# 마지막 실행의 계측. `duck.CausalLake.exists` 와 같은 취지 - **어떻게 쟀는지가 보여야**
# 산문이 수치의 출처를 말할 수 있다. 호출자(`layers._series`)가 여기서 읽어 남긴다.
LAST: dict[str, object] = {}


class AthenaUnavailable(RuntimeError):
    """Athena 경로가 못 돈 경우 - boto3·자격증명·워크그룹 부재·질의 실패·타임아웃.

    **빈 결과와 절대 안 섞인다.** 호출자는 이걸 잡아 DuckDB 경로로 돌아가면 된다.
    """


class ResultTooLarge(RuntimeError):
    """결과가 상한을 넘었다. **폴백하면 안 된다** - DuckDB 로 돌아가면 더 읽는다.

    `AthenaUnavailable` 을 안 물려받은 것이 요점이다: 부재는 견디는 것이고 이건
    고치는 것이다(그 질의는 애초에 오프로드 대상이 아니었다).
    """


def _env(key: str) -> str:
    """env 값 또는 확정 기본값. 빈 문자열도 미설정으로 본다 - `duck._env` 와 같은 규약
    (컨테이너 정의는 변수를 '있지만 빈 값'으로 주는 일이 잦다)."""
    return os.environ.get(key, "").strip() or DEFAULTS[key]


def _clients() -> tuple:
    """(athena, s3). boto3 부재·자격증명 부재를 여기서 한 번에 예외로 바꾼다."""
    try:
        import boto3
    except ImportError as e:
        raise AthenaUnavailable(f"boto3 없음: {e}") from e
    prof = os.environ.get(AWS_PROFILE_ENV, "").strip()
    try:
        # 프로파일은 **설정됐을 때만** 쓴다 - 컨테이너엔 ~/.aws/config 가 없고 task
        # role 로 붙는다 (`duck.s3_secret_sql` 과 같은 이유).
        sess = boto3.Session(profile_name=prof or None, region_name=_env(AWS_REGION_ENV))
        return sess.client("athena"), sess.client("s3")
    except Exception as e:                        # noqa: BLE001
        raise AthenaUnavailable(f"AWS 세션 실패: {type(e).__name__}: {str(e)[:160]}") from e


def _start(athena, sql: str) -> str:
    kw = {"QueryString": sql,
          "QueryExecutionContext": {"Database": _env(DB_ENV)},
          "WorkGroup": _env(WORKGROUP_ENV)}
    # 결과 위치는 **재지정됐을 때만** 보낸다. 워크그룹이 자기 위치를 강제(enforce)하면
    # 같이 보낸 값이 충돌해 질의가 시작도 못 한다 - 평소엔 워크그룹에 맡기고, 실제로
    # 어디 떨어졌는지는 응답에서 확인한다(추측하지 않는다).
    if os.environ.get(OUTPUT_ENV, "").strip():
        kw["ResultConfiguration"] = {"OutputLocation": _env(OUTPUT_ENV)}
    try:
        return athena.start_query_execution(**kw)["QueryExecutionId"]
    except Exception as e:                        # noqa: BLE001 - 워크그룹 부재·권한이 여기
        raise AthenaUnavailable(
            f"질의 제출 실패(워크그룹 '{_env(WORKGROUP_ENV)}'): "
            f"{type(e).__name__}: {str(e)[:200]}") from e


def _wait(athena, qid: str) -> tuple[str, int]:
    """완료까지 폴링 → (결과 CSV 의 s3 URI, 스캔 바이트). 성공 아니면 전부 예외."""
    deadline = time.monotonic() + TIMEOUT_SEC
    while True:
        try:
            ex = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        except Exception as e:                    # noqa: BLE001
            raise AthenaUnavailable(f"상태 조회 실패: {type(e).__name__}: {str(e)[:160]}") from e
        state = ex["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.monotonic() > deadline:
            # 매달린 질의는 끊고 나간다 - 안 끊으면 폴백으로 돌아간 뒤에도 클러스터에서
            # 계속 돌며 스캔 비용만 낸다.
            try:
                athena.stop_query_execution(QueryExecutionId=qid)
            except Exception:                     # noqa: BLE001, S110 - 정리 실패는 본 사유를 안 가린다
                pass
            raise AthenaUnavailable(f"{TIMEOUT_SEC:.0f}초 초과 (query {qid})")
        time.sleep(POLL_SEC)
    if state != "SUCCEEDED":
        raise AthenaUnavailable(f"질의 {state}: {ex['Status'].get('StateChangeReason')}")
    uri = ex.get("ResultConfiguration", {}).get("OutputLocation", "")
    if not uri:
        raise AthenaUnavailable(f"결과 위치가 없다 (query {qid})")
    return uri, int(ex.get("Statistics", {}).get("DataScannedInBytes", 0))


def _schema(athena, qid: str) -> dict[str, str]:
    """결과 열 → DuckDB 타입. 스니핑에 맡기면 `'005930'` 이 5930 이 된다."""
    try:
        meta = athena.get_query_results(QueryExecutionId=qid, MaxResults=1)
        cols = meta["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
    except Exception as e:                        # noqa: BLE001
        raise AthenaUnavailable(f"결과 스키마 조회 실패: {type(e).__name__}: {str(e)[:160]}") from e
    return {c["Name"]: _TYPES.get(c["Type"].lower(), "VARCHAR") for c in cols}


def _size(s3, uri: str) -> int:
    """내려받기 **전에** 결과 크기를 안다. 이게 회선을 지키는 유일한 관문이다."""
    bucket, _, key = uri[len("s3://"):].partition("/")
    try:
        return int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except Exception as e:                        # noqa: BLE001
        raise AthenaUnavailable(f"결과 크기 조회 실패 {uri}: {type(e).__name__}: {str(e)[:160]}") from e


def run(sql: str, *, con, params: dict | None = None) -> list[tuple]:
    """Athena 에서 집계하고 **작은 결과만** `con` 으로 받는다.

    `params` 가 있으면 `sql.format(**params)`. 이 저장소의 SQL 은 전부 `.format` 규약이라
    같은 규약을 따른다 - 자리표시자 문법을 하나 더 만들면 어느 쪽인지 매번 봐야 한다.

    `con` 은 DuckDB 연결이다(`lake.con`). httpfs 시크릿이 이미 걸린 연결이어야 한다 -
    `CausalLake` 가 만드는 연결은 그렇다.

    실패: `AthenaUnavailable`(폴백해도 됨) · `ResultTooLarge`(고쳐야 함).
    """
    if params:
        sql = sql.format(**params)
    athena, s3 = _clients()
    t = time.monotonic()
    qid = _start(athena, sql)
    uri, scanned = _wait(athena, qid)
    size = _size(s3, uri)
    if size > MAX_RESULT_BYTES:
        raise ResultTooLarge(
            f"결과 {size / 1e6:.1f}MB > 상한 {MAX_RESULT_BYTES / 1e6:.0f}MB - "
            f"이 질의는 오프로드 대상이 아니다 (query {qid})")
    cols = _schema(athena, qid)
    spec = ", ".join(f"'{n}': '{t_}'" for n, t_ in cols.items())
    try:
        rows = con.execute(
            f"SELECT * FROM read_csv('{uri}', header = true, columns = {{{spec}}})").fetchall()
    except Exception as e:                        # noqa: BLE001
        raise AthenaUnavailable(
            f"결과 CSV 읽기 실패 {uri}: {type(e).__name__}: {str(e)[:200]}") from e
    if len(rows) > MAX_RESULT_ROWS:
        raise ResultTooLarge(f"결과 {len(rows):,}행 > 상한 {MAX_RESULT_ROWS:,}행 (query {qid})")
    LAST.clear()
    LAST.update(uri=uri, query_id=qid, scanned=scanned, result_bytes=size,
                rows=len(rows), cols=list(cols), sec=round(time.monotonic() - t, 2))
    return rows


def note() -> str:
    """마지막 실행 한 줄 요약 - `lake.exists` 에 그대로 넣는 문자열."""
    if not LAST:
        return ""
    return (f"Athena ({LAST['rows']:,}행 · 스캔 {int(LAST['scanned']) / 1e6:.1f}MB · "
            f"결과 {int(LAST['result_bytes']) / 1e6:.3f}MB)")


# ── 소비처 1: 층 β 시각창 패널 ────────────────────────────────────────────
# `layers._CLOCK_SQL` 과 **같은 결과**여야 한다. 옮긴 것들:
#   first/last(… ORDER BY ts) → min_by/max_by(…, ts)   Trino 엔 first/last 집계가 없다
# 그 밖은 문자 그대로 같다: 창 안 첫 `open` → 마지막 `close` 의 로그수익, 거래량 합.
#
# **심볼 필터는 날것 컬럼에 건다.** `regexp_replace(symbol,…) IN (…)` 은 표현식 술어라
# `bucket(16, symbol)` 프루닝이 죽는다(실측 2심볼 230.6MB). 접미사 변형 3개를 나열하면
# 같은 집합을 정확히 가리키면서 - 스트립 결과가 `t` 인 심볼은 `t`·`t.KS`·`t.KQ` 뿐이다 -
# 프루닝이 산다: 같은 질의 47.5MB.
_PANEL_SQL = r"""
-- edge layers clock panel · kinds={kinds} · {t0}~{t1} · <= {day} · {lookback}일 · {n}심볼
SELECT sym, d, lr, vol FROM (
    SELECT regexp_replace(symbol, '\.(KS|KQ)$', '') AS sym,
           CAST(ts AS DATE) AS d,
           ln(max_by(close, ts) / min_by(open, ts)) AS lr,
           sum(volume) AS vol
    FROM {table}
    WHERE trade_date <= DATE '{day}'
      AND trade_date >= DATE '{day}' - INTERVAL '{lookback}' DAY
      AND open > 0 AND close > 0
      AND CAST(ts AS TIME) >= TIME '{t0}' AND CAST(ts AS TIME) < TIME '{t1}'
      AND symbol IN ({lit})
    GROUP BY 1, 2
) WHERE lr IS NOT NULL
ORDER BY sym, d
"""


def panel_sql(day: str, kinds: tuple[str, ...], t0: str, t1: str,
              syms: tuple[str, ...], lookback_days: int = LOOKBACK_DAYS) -> str:
    """질의 문자열. 테스트가 레이크 없이 조립만 검사할 수 있게 따로 뺀다."""
    lit = ", ".join(f"'{s}{sfx}'" for s in syms for sfx in ("", ".KS", ".KQ"))
    return _PANEL_SQL.format(table=f"{_env(DB_ENV)}.{_env(TABLE_ENV)}", day=day,
                             kinds=",".join(kinds), t0=t0, t1=t1, n=len(syms), lit=lit,
                             lookback=lookback_days)


def clock_panel(day: str, kinds: tuple[str, ...], t0: str, t1: str, *,
                con, only: tuple[str, ...] | None = None,
                lookback_days: int = LOOKBACK_DAYS) -> list[tuple]:
    """`layers._CLOCK_SQL` 과 같은 행: [(sym, [lr…], [date…], [vol…])] - 날짜 오름차순.

    `only` 는 **필수**다. 심볼 회원(`layers_daily.kind`)은 Athena 에 없으므로 호출자가
    목록을 정해서 넘긴다. `kinds` 는 그 목록이 무슨 계열이었는지를 질의 주석에 남길
    뿐이다 - Athena 질의 이력에서 '무엇을 물었나'가 보여야 비용을 사후 귀속할 수 있다.
    """
    syms = tuple(only or ())
    if not syms:
        # 여기서 관대하면 1,642 심볼 전량 집계가 조용히 나간다. 폴백으로 넘길 사안이
        # 아니라 호출자 버그이므로 예외 종류를 갈라 던진다.
        raise ValueError("clock_panel: 심볼 목록(only)이 없으면 표 전량을 집계한다")
    rows = run(panel_sql(day, kinds, t0, t1, syms, lookback_days), con=con)
    # 질의가 (sym, d) 오름차순으로 냈으므로 인접 묶음이 곧 심볼이다 - 다시 정렬하지
    # 않는다. `vol` 은 NULL 이 올 수 있다: 거래정지 판정이 그 값을 본다.
    out = []
    for sym, g in groupby(rows, key=lambda r: r[0]):
        block = list(g)
        out.append((sym, [float(r[2]) for r in block],
                    [r[1] if isinstance(r[1], dt.date) else dt.date.fromisoformat(str(r[1]))
                     for r in block],
                    [r[3] for r in block]))
    return out


__all__ = ["AthenaUnavailable", "DEFAULTS", "LAST", "MAX_RESULT_BYTES", "MAX_RESULT_ROWS",
           "ResultTooLarge", "clock_panel", "note", "panel_sql", "run"]
