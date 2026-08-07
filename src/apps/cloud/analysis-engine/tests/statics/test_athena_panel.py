"""Athena 오프로드 표면의 계약 — **무거운 집계는 클라우드가, 노트북은 작은 결과만**.

이 표면이 지켜야 하는 것은 속도가 아니라 세 가지 정직성이다:

1. **프루닝이 살아 있어야 한다.** 심볼 술어를 표현식(`regexp_replace(symbol,…) IN`)으로
   쓰면 `bucket(16, symbol)` 프루닝이 죽는다 - 실측 2심볼 230.6MB vs 47.5MB. 질의를
   '읽기 좋게' 정리하다 이 한 줄이 되돌아가면 비용이 5배가 되고 아무도 못 본다.
2. **부재는 부재로 말해야 한다.** Athena 가 없을 때 빈 결과를 돌려주면 상위 산문이
   "섹터 부재"라고 거짓말한다. 예외를 던지고 DuckDB 로 폴백하되 사유를 남긴다.
3. **큰 결과는 오프로드가 아니다.** 상한을 넘으면 즉사한다 - 그리고 그 예외는
   `AthenaUnavailable` 이 아니어야 한다(폴백해 봐야 더 읽는다).

레이크 없이 도는 것이 기본이다. 라이브는 자격증명·워크그룹이 있을 때만 돈다.
"""
import datetime as dt

import duckdb
import pytest

from edge_analysis.statics import athena, layers

DAY = "2026-08-04"
T0, T1 = "13:00:00", "15:30:00"
KINDS = ("market", "sector")

_ENVS = ("EDGE_ATHENA_WORKGROUP", "EDGE_ATHENA_DB", "EDGE_ATHENA_BARS_TABLE",
         "EDGE_ATHENA_OUTPUT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # 노트북엔 재지정이 살아 있을 수 있다 - 기본값 계약을 재려면 비워야 한다.
    for e in _ENVS:
        monkeypatch.delenv(e, raising=False)


# ── 가짜 AWS ────────────────────────────────────────────────────────────
class _FakeAthena:
    """Athena 클라이언트의 최소 모양. 결과는 로컬 CSV 파일 하나로 대신한다 -
    DuckDB `read_csv` 는 경로가 s3 든 로컬이든 같은 코드로 읽으므로 타입 계약을
    그대로 검사할 수 있다."""

    def __init__(self, uri: str, cols: tuple[tuple[str, str], ...], *,
                 state: str = "SUCCEEDED", start_error: Exception | None = None,
                 scanned: int = 1234):
        self.uri, self.cols, self.state = uri, cols, state
        self.start_error, self.scanned = start_error, scanned
        self.stopped: list[str] = []

    def start_query_execution(self, **_kw):
        if self.start_error:
            raise self.start_error
        return {"QueryExecutionId": "qid-1"}

    def get_query_execution(self, QueryExecutionId):        # noqa: N803 - boto3 규약
        return {"QueryExecution": {
            "Status": {"State": self.state, "StateChangeReason": "테스트 사유"},
            "Statistics": {"DataScannedInBytes": self.scanned},
            "ResultConfiguration": {"OutputLocation": self.uri}}}

    def get_query_results(self, QueryExecutionId, MaxResults=1):  # noqa: N803
        return {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": [
            {"Name": n, "Type": t} for n, t in self.cols]}}}

    def stop_query_execution(self, QueryExecutionId):       # noqa: N803
        self.stopped.append(QueryExecutionId)


class _FakeS3:
    def __init__(self, size: int):
        self.size = size

    def head_object(self, **_kw):
        return {"ContentLength": self.size}


def _wire(monkeypatch, tmp_path, body: str, cols, *, size=None, **kw):
    """가짜 클라이언트를 물리고 (연결, athena 클라이언트) 를 돌려준다."""
    p = tmp_path / "result.csv"
    p.write_text(body, encoding="utf-8")
    cl = _FakeAthena(p.as_posix(), tuple(cols), **kw)
    monkeypatch.setattr(athena, "_clients",
                        lambda: (cl, _FakeS3(size if size is not None else len(body))))
    return duckdb.connect(), cl


class _FakeLake:
    """`_series` 의 clock 분기가 만지는 것만: sql · con · exists.

    `con` 은 **있기만 하면 된다** - Athena 호출은 이 테스트들에서 대역으로 갈아끼우므로
    연결이 쓰이지 않는다. 다만 `None` 이면 '연결 없음' 부재 경로로 빠지므로 센티널을 둔다.
    """

    def __init__(self, names, clock_rows):
        self.names, self.clock_rows = names, clock_rows
        self.exists: dict = {}
        self.unbound: dict = {}
        # 정본이 요청일을 안 담는다는 판정. 비어 있음 = 담고 있다(= Athena 를 쓴다).
        self.stale_5m: str = ""
        self.con = object()
        self.asked: list[str] = []

    def sql(self, q):
        self.asked.append(q)
        if "any_value(name)" in q:
            return list(self.names.items())
        return self.clock_rows


def _lake():
    """DuckDB 폴백이 낼 행과 같은 모양(sym, [lr], [date], [vol])."""
    d = [dt.date(2026, 8, 3), dt.date(2026, 8, 4)]
    return _FakeLake({"069500": "KODEX200"},
                     [("069500", [0.01, -0.02], d, [100, 0])])


# ── 1. 프루닝 계약 ──────────────────────────────────────────────────────
def test_symbol_filter_uses_the_raw_column_with_every_suffix_variant():
    """`bucket(16, symbol)` 프루닝은 **날것 컬럼 등치**에서만 산다. 표현식으로 쓰면
    실측 2심볼 스캔이 47.5MB → 230.6MB 로 5배가 된다 - 결과는 같아서 안 보인다.
    접미사 3변형이 전부 있어야 같은 집합이다(스트립 결과가 t 인 심볼은 그 셋뿐)."""
    sql = athena.panel_sql(DAY, KINDS, T0, T1, ("005930", "069500"))
    assert "symbol IN ('005930', '005930.KS', '005930.KQ', " \
           "'069500', '069500.KS', '069500.KQ')" in sql
    # 술어 쪽에 표현식이 돌아오면 프루닝이 죽는다. 투영에는 남아 있어야 한다.
    where = sql.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "regexp_replace" not in where
    assert "regexp_replace(symbol, '\\.(KS|KQ)$', '') AS sym" in sql


def test_panel_uses_min_by_max_by_because_trino_has_no_first_last():
    """`first/last(… ORDER BY ts)` 는 DuckDB 문법이다. Trino 에 그대로 보내면 질의가
    실패하고, 폴백이 있으니 **조용히 DuckDB 로 돌아가** 오프로드가 사라진다."""
    sql = athena.panel_sql(DAY, KINDS, T0, T1, ("005930",))
    assert "min_by(open, ts)" in sql and "max_by(close, ts)" in sql
    assert "first(" not in sql and "last(" not in sql


def test_kinds_are_recorded_in_the_query_so_cost_can_be_attributed_later():
    # Athena 이력에 '무엇을 물었나'가 없으면 나중에 비용을 어느 계열에 귀속할지 모른다.
    sql = athena.panel_sql(DAY, KINDS, T0, T1, ("005930",))
    assert "kinds=market,sector" in sql and "1심볼" in sql


def test_panel_bounds_the_history_so_the_row_cap_is_not_blown():
    """상한만 걸면 표 전체(2022-11~)를 긁는다.

    실측(2026-08-05 · query d100beea): 198심볼 × ~800거래일 = **159,572행**으로
    `MAX_RESULT_ROWS`(100,000)를 넘겨 `ResultTooLarge` 가 났고, 그 예외는 일부러 안
    잡히므로 런이 `판정불가 — 통계 표면 부재` 로 통째로 떨어졌다.

    필요한 이력은 `BETA_WINDOW`(60거래일)뿐이다. 행 수는 심볼 × 날짜 축이라 **시각창을
    좁혀도 안 줄어든다** - 줄일 수 있는 축은 날짜다.
    """
    sql = athena.panel_sql(DAY, KINDS, T0, T1, ("005930",))
    where = sql.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert f"trade_date <= DATE '{DAY}'" in where
    assert "trade_date >= DATE" in where
    # 60거래일을 채울 만큼은 남긴다 - 짧으면 MIN_BETA_N(40) 미달로 층이 조용히 빠진다.
    # 주말·공휴일을 감안하면 60거래일에 최소 ~88 달력일이 든다.
    assert athena.LOOKBACK_DAYS >= 88


def test_lookback_is_overridable_per_call():
    """창을 넓혀야 하는 질의(긴 β)가 생겨도 상수를 고치지 않는다."""
    sql = athena.panel_sql(DAY, KINDS, T0, T1, ("005930",), lookback_days=7)
    assert "INTERVAL '7' DAY" in sql


def test_table_and_database_are_env_overridable(monkeypatch):
    monkeypatch.setenv("EDGE_ATHENA_DB", "other_db")
    monkeypatch.setenv("EDGE_ATHENA_BARS_TABLE", "other_table")
    assert "FROM other_db.other_table" in athena.panel_sql(DAY, KINDS, T0, T1, ("005930",))


def test_empty_symbol_list_is_a_caller_bug_not_a_fallback():
    """심볼 목록 없이 부르면 표 전량(1,642심볼) 집계가 조용히 나간다. 이건 Athena
    부재가 아니라 호출자 버그이므로 폴백으로 넘어가면 안 된다 - 예외 종류를 가른다."""
    with pytest.raises(ValueError):
        athena.clock_panel(DAY, KINDS, T0, T1, con=None, only=())
    with pytest.raises(ValueError):
        athena.clock_panel(DAY, KINDS, T0, T1, con=None, only=None)


# ── 2. 타입 계약 ────────────────────────────────────────────────────────
def test_symbol_column_keeps_its_leading_zero(monkeypatch, tmp_path):
    """CSV 스니핑에 맡기면 `'005930'` 이 BIGINT 5930 이 되어 심볼 축이 통째로
    안 맞는다(조인이 0행 → '그 종목 자료 없음'). Athena 메타데이터로 못 박는다."""
    con, _ = _wire(monkeypatch, tmp_path,
                   '"sym","d","lr","vol"\n"005930","2026-08-04","0.01","100"\n',
                   (("sym", "varchar"), ("d", "date"), ("lr", "double"), ("vol", "bigint")))
    rows = athena.run("SELECT 1", con=con)
    assert rows == [("005930", dt.date(2026, 8, 4), 0.01, 100)]


def test_unknown_athena_types_become_varchar_rather_than_a_guess(monkeypatch, tmp_path):
    # 모르는 타입을 숫자로 만들면 값이 조용히 바뀐다. 문자열은 최소한 안 바꾼다.
    con, _ = _wire(monkeypatch, tmp_path, '"v"\n"0.5"\n', (("v", "array(varchar)"),))
    assert athena.run("SELECT 1", con=con) == [("0.5",)]


def test_params_are_formatted_into_the_sql(monkeypatch, tmp_path):
    con, cl = _wire(monkeypatch, tmp_path, '"v"\n"1"\n', (("v", "bigint"),))
    seen = []
    cl.start_query_execution = lambda **kw: (seen.append(kw["QueryString"]),
                                             {"QueryExecutionId": "qid-1"})[1]
    athena.run("SELECT {n} AS v", con=con, params={"n": 7})
    assert seen == ["SELECT 7 AS v"]


# ── 3. 상한: 큰 결과는 오프로드가 아니다 ────────────────────────────────
def test_result_bigger_than_the_byte_cap_dies_before_it_is_downloaded(monkeypatch, tmp_path):
    """바이트 관문은 **내려받기 전에** 걸려야 한다 - 걸린 뒤에 재면 이미 회선을 썼다."""
    con, _ = _wire(monkeypatch, tmp_path, '"v"\n"1"\n', (("v", "bigint"),),
                   size=athena.MAX_RESULT_BYTES + 1)
    with pytest.raises(athena.ResultTooLarge):
        athena.run("SELECT 1", con=con)


def test_result_over_the_row_cap_dies_even_when_the_bytes_fit(monkeypatch, tmp_path):
    # 열이 좁으면 바이트는 통과해도 행이 패널 크기일 수 있다.
    monkeypatch.setattr(athena, "MAX_RESULT_ROWS", 2)
    con, _ = _wire(monkeypatch, tmp_path, '"v"\n"1"\n"2"\n"3"\n', (("v", "bigint"),))
    with pytest.raises(athena.ResultTooLarge):
        athena.run("SELECT 1", con=con)


def test_too_large_is_not_an_availability_problem():
    """**폴백으로 삼키면 안 된다.** DuckDB 로 돌아가 봐야 더 읽는다 - 이건 견딜
    부재가 아니라 고칠 설계 착오다. 상속 관계 하나가 그 구분을 지킨다."""
    assert not issubclass(athena.ResultTooLarge, athena.AthenaUnavailable)


# ── 4. 부재는 부재로 ────────────────────────────────────────────────────
def test_missing_workgroup_raises_instead_of_returning_no_rows(monkeypatch, tmp_path):
    con, _ = _wire(monkeypatch, tmp_path, '"v"\n', (("v", "bigint"),),
                   start_error=RuntimeError("WorkGroup is not found"))
    with pytest.raises(athena.AthenaUnavailable, match="워크그룹"):
        athena.run("SELECT 1", con=con)


def test_failed_query_raises_with_the_reason_attached(monkeypatch, tmp_path):
    con, _ = _wire(monkeypatch, tmp_path, '"v"\n', (("v", "bigint"),), state="FAILED")
    with pytest.raises(athena.AthenaUnavailable, match="테스트 사유"):
        athena.run("SELECT 1", con=con)


def test_timeout_stops_the_query_instead_of_leaving_it_running(monkeypatch, tmp_path):
    """폴백으로 돌아간 뒤에도 클러스터에서 계속 돌면 스캔 비용만 낸다."""
    monkeypatch.setattr(athena, "TIMEOUT_SEC", 0.0)
    monkeypatch.setattr(athena, "POLL_SEC", 0.0)
    con, cl = _wire(monkeypatch, tmp_path, '"v"\n', (("v", "bigint"),), state="RUNNING")
    with pytest.raises(athena.AthenaUnavailable, match="초 초과"):
        athena.run("SELECT 1", con=con)
    assert cl.stopped == ["qid-1"]


def test_missing_boto3_is_reported_as_absence(monkeypatch):
    real = __import__

    def no_boto3(name, *a, **kw):
        if name == "boto3":
            raise ImportError("No module named 'boto3'")
        return real(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", no_boto3)
    with pytest.raises(athena.AthenaUnavailable, match="boto3"):
        athena._clients()


# ── 5. `_series` 배선 ───────────────────────────────────────────────────
def test_clock_series_keeps_its_contract_when_athena_answers(monkeypatch):
    """반환 계약은 토큰 하나도 안 바뀐다: {symbol: (name, {date: lr}, {정지날})}.
    거래량 0 인 날은 정지다 - 그날 수익률 0 은 거짓이라 β 에서 빠져야 한다."""
    d = [dt.date(2026, 8, 3), dt.date(2026, 8, 4)]
    monkeypatch.setattr(athena, "clock_panel",
                        lambda *_a, **_k: [("069500", [0.01, -0.02], d, [100, 0])])
    lk = _lake()
    got = layers._series(lk, DAY, KINDS, clock=(T0, T1))
    assert got == {"069500": ("KODEX200", {d[0]: 0.01, d[1]: -0.02}, {d[1]})}


def test_clock_series_falls_back_to_duckdb_and_says_why(monkeypatch):
    """Athena 부재가 '섹터 부재'로 보이면 안 된다. 값은 그대로 나오고, **경로와
    사유가 한 자리에**(`exists`) 남는다 - 둘로 갈라 두면 한쪽만 읽는 소비처가 생기고
    그러면 폴백이 다시 조용해진다."""
    def boom(*_a, **_k):
        raise athena.AthenaUnavailable("워크그룹 없음")

    monkeypatch.setattr(athena, "clock_panel", boom)
    lk = _lake()
    got = layers._series(lk, DAY, KINDS, clock=(T0, T1))
    assert set(got) == {"069500"} and len(got["069500"][1]) == 2
    seen = lk.exists["clock_panel"]
    assert seen.startswith("DuckDB _CLOCK_SQL") and "워크그룹 없음" in seen
    assert any("bars_5m" in q for q in lk.asked)          # 폴백이 실제로 돌았다


def test_a_stale_canon_never_reaches_athena_at_all(monkeypatch):
    """**낡은 정본은 예외가 아니라 '과거만 있는 패널'로 온다** - 성공으로 받으면 폴백이
    안 걸리고 요청일 층이 통째로 빠진다(실측 2026-08-07: Iceberg 최신 08-05, 같은 시각
    canonical 엔 당일이 있었는데 산문은 시장·섹터·구성종목을 전부 '미계측'이라 냈다).

    판정을 여기서 다시 하지 않는 것이 요점이다 - 레이크가 바인드할 때 `iceberg_covers`
    가 낸 답(`unbound["bars_5m_iceberg"]`)을 읽는다. 같은 판정이 두 곳에 살면 갈리고,
    갈린 날 한쪽만 낡은 표를 쓴다. 그리고 **질의를 아예 안 보내야** 한다: 정본이 낡은
    동안은 그게 정상 상태라 왕복 비용이 런마다 곱해진다(`decompose` 가 clock `_series`
    를 최대 3회 × 한 런이 `decompose` 2회).
    """
    called = []
    monkeypatch.setattr(athena, "clock_panel",
                        lambda *a, **k: called.append(a) or [])
    lk = _lake()
    # 손으로 쓴 픽스처는 프로덕션이 실제로 만드는 문구여야 한다 — 갈리면 소비자가
    # truthiness 만 보므로 아무도 안 깨지고(그래서 못 잡고) 픽스처만 조용히 낡는다.
    lk.stale_5m = f"정본에 요청일이 없다 ({DAY} 0종 · 최신 2026-08-02)"
    # ATTACH·자격증명 실패는 **여기 안 담긴다** — 그건 다른 일감이고 Athena 는 boto3 로
    # 따로 붙는다. 같이 담으면 권한 사고 때 사유를 '정본이 낡았다'로 잘못 말하고,
    # 덤으로 멀쩡한 오프로드까지 끈다.
    lk.unbound = {"bars_5m_iceberg": f"{lk.stale_5m} - canonical 합집합으로 내려간다"}
    got = layers._series(lk, DAY, KINDS, clock=(T0, T1))

    assert called == []                                   # 왕복을 아예 안 쳤다
    assert dt.date.fromisoformat(DAY) in got["069500"][1]  # DuckDB 가 당일을 냈다
    seen = lk.exists["clock_panel"]
    assert seen.startswith("DuckDB _CLOCK_SQL")
    # 바인드 때의 사유가 그대로 실려야 '왜 폴백했나'가 한 자리에서 읽힌다.
    assert "정본이 요청일을 안 담는다" in seen and "최신 2026-08-02" in seen
    assert any("bars_5m" in q for q in lk.asked)


def test_a_fresh_canon_still_goes_to_athena(monkeypatch):
    """위 가드의 반대편 - 정본이 멀쩡하면 오프로드가 그대로 산다. 이게 없으면 가드가
    '항상 폴백' 으로 퇴화해도 아무도 못 잡는다(376.4MB 수신이 조용히 상시화된다)."""
    d = [dt.date(2026, 8, 3), dt.date(2026, 8, 4)]
    called = []
    monkeypatch.setattr(athena, "clock_panel", lambda *a, **k: called.append(a) or [
        ("069500", [0.01, -0.02], d, [100, 100])])
    # `note()` 는 모듈 전역 `athena.LAST` 를 읽는다 - 안 물리면 앞선 테스트가 채워 둔
    # 값에 얹혀 통과하고 단독 실행·`-k`·재정렬에서 빨개진다. 가드가 '항상 폴백' 으로
    # 퇴화하는 것을 잡을 그물이 남의 전역에 매달려 있으면 안 된다.
    monkeypatch.setattr(athena, "note", lambda: "Athena (7,071행 · 결과 0.379MB)")
    lk = _lake()
    # 정본은 멀쩡하고 **다른** 표만 못 묶인 상태 — 그때도 오프로드는 살아야 한다.
    # 이 줄이 N1(사유를 뭉쳐 읽어 멀쩡한 Athena 를 끄던 결함)의 회귀를 막는다.
    lk.unbound = {"bars_5m_iceberg": "NoCredentialsError: Unable to locate credentials"}
    got = layers._series(lk, DAY, KINDS, clock=(T0, T1))
    assert len(called) == 1
    assert got["069500"][1] == {d[0]: 0.01, d[1]: -0.02}
    assert lk.exists["clock_panel"] == "Athena (7,071행 · 결과 0.379MB)"


def test_a_lake_without_a_connection_says_so_instead_of_skipping_quietly(monkeypatch):
    """결과 CSV 를 받을 데가 없는 것도 부재다. 조용히 로컬로 돌면 '왜 376MB 를
    끌어왔나'가 아무 데도 안 남는다."""
    lk = _lake()
    del lk.con
    layers._series(lk, DAY, KINDS, clock=(T0, T1))
    assert "DuckDB 연결 없음" in lk.exists["clock_panel"]


def test_clock_series_does_not_swallow_a_too_large_result(monkeypatch):
    """상한 초과에서 폴백하면 **더 많이 읽고** 성공한 척한다 - 그 경로를 막는다."""
    def boom(*_a, **_k):
        raise athena.ResultTooLarge("결과 200MB")

    monkeypatch.setattr(athena, "clock_panel", boom)
    with pytest.raises(athena.ResultTooLarge):
        layers._series(_lake(), DAY, KINDS, clock=(T0, T1))


def test_athena_gets_the_membership_intersection_not_the_raw_only(monkeypatch):
    """회원 표(`layers_daily`)는 Athena 에 없다. `_CLOCK_SQL` 이 `k` 와 조인해 걸러
    주던 것을 여기서 미리 교집합으로 만들어 넘기지 않으면, 계열에 없는 심볼이
    패널에 끼어 상위 후보 풀이 달라진다."""
    seen = {}

    def spy(*_a, only=None, **_k):
        seen["only"] = only
        return []

    monkeypatch.setattr(athena, "clock_panel", spy)
    lk = _FakeLake({"069500": "KODEX200", "091160": "반도체"}, [])
    layers._series(lk, DAY, KINDS, clock=(T0, T1), only=("069500", "999999"))
    assert seen["only"] == ("069500",)


def test_clock_route_is_recorded_with_the_bytes_it_cost(monkeypatch):
    """'어떻게 쟀는지'가 안 보이면 산문이 수치의 출처를 못 말한다."""
    monkeypatch.setattr(athena, "clock_panel", lambda *_a, **_k: [])
    monkeypatch.setattr(athena, "note", lambda: "Athena (7,071행 · 결과 0.379MB)")
    lk = _lake()
    layers._series(lk, DAY, KINDS, clock=(T0, T1))
    assert lk.exists["clock_panel"] == "Athena (7,071행 · 결과 0.379MB)"


def test_note_reports_both_the_scan_and_what_actually_came_down():
    """스캔 바이트만 적으면 오프로드의 요점(내려온 바이트)이 안 보인다."""
    athena.LAST.clear()
    athena.LAST.update(rows=7071, scanned=148_591_360, result_bytes=379_362)
    n = athena.note()
    assert "148.6MB" in n and "0.379MB" in n and "7,071행" in n


def test_note_is_empty_before_anything_ran():
    athena.LAST.clear()
    assert athena.note() == ""


# ── 6. 라이브 (자격증명·워크그룹이 있을 때만) ───────────────────────────
def _live_con():
    """실물 Athena 왕복이 되는지 확인하고 연결을 돌려준다. 안 되면 skip."""
    con = duckdb.connect()
    try:
        from edge_analysis.statics.duck import s3_secret_sql
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(s3_secret_sql())
        athena.run("SELECT 1 AS one", con=con)
    except athena.AthenaUnavailable as e:
        pytest.skip(f"Athena 부재: {str(e)[:80]}")
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"S3 자격증명 부재: {type(e).__name__}: {str(e)[:80]}")
    return con


@pytest.mark.parametrize("syms", [("069500",)])
def test_live_clock_panel_shape_and_measured_bytes(syms):
    """라이브 계약: 행 모양이 `_CLOCK_SQL` 과 같고, **내려온 바이트가 스캔보다
    훨씬 작다** - 그 부등식이 이 모듈의 존재 이유다."""
    rows = athena.clock_panel(DAY, KINDS, T0, T1, con=_live_con(), only=syms)
    assert rows and len(rows[0]) == 4
    sym, lrs, dates, vols = rows[0]
    assert sym == "069500"
    # 세 배열은 같은 행의 세 축이라 길이가 어긋나면 계약이 깨진 것이다.
    assert len(lrs) == len(dates) == len(vols)
    # 하한은 **거래일** 기준이다. `LOOKBACK_DAYS` 는 달력일이고 KRX 는 주말·휴장일에
    # 거래하지 않으므로 한 심볼이 얻는 거래일은 그보다 한참 적다(실측 2026-08-04:
    # 120 달력일 → 82 거래일). `> 100` 은 production 질의가 의도대로 동작하면
    # **원리적으로 만족될 수 없는** 단언이었고, CI 가 자격증명 부재로 이 테스트를
    # skip 해 그 모순이 드러나지 않았다.
    #
    # 지켜야 할 계약은 "충분히 길다" 가 아니라 **소비자가 층을 세울 수 있다** 이다.
    # 그 판정은 `layers.decompose` 가 하고, 원시 행 수가 아니라 아래 셋을 본다.
    assert dates, "행은 왔는데 날짜가 비었다"
    assert all(isinstance(d, dt.date) for d in dates)
    assert dates == sorted(dates)

    # ① 중복 날짜는 표본이 아니다 — `_series` 가 `{date: 수익률}` 로 접으므로 같은
    #    날이 두 번 오면 행 수만 늘고 표본은 그대로다.
    assert len(set(dates)) == len(dates), "같은 거래일이 두 번 왔다"

    # ② 당일이 있어야 한다 — `decompose` 는 `d0 not in ser[etf][1]` 이면 **즉시 None**
    #    이다(`layers.py`). 과거만 잔뜩 와도 층 분해는 전건 실패한다.
    target = dt.date.fromisoformat(DAY)
    assert dates[-1] == target, f"당일({target})이 계열에 없다: 마지막 {dates[-1]}"

    # ③ β 표본은 **당일을 뺀 과거 고유 거래일**이다 — `hist = (d < d0)[-BETA_WINDOW:]`
    #    이고 `len(hist) < MIN_BETA_N` 이면 그 층이 후보에서 빠진다. 하한을 `MIN_BETA_N`
    #    으로 두면 창을 절반만 긁는 회귀를 통과시키므로, 이 질의가 겨냥한
    #    `BETA_WINDOW` 를 채우는지로 본다(`LOOKBACK_DAYS` 가 그러라고 120 이다).
    past = [d for d in dates if d < target]
    assert len(past) >= layers.BETA_WINDOW, (
        f"β 창을 못 채운다: 과거 {len(past)}거래일 < {layers.BETA_WINDOW}"
        f" (하드 하한은 {layers.MIN_BETA_N})")

    # ④ 창의 반대편 — 하한이 사라지면(전량 스캔 회귀, ALPHA-782) 위 단언들은 통과하지만
    #    날짜가 하한 밖으로 넘친다.
    floor = target - dt.timedelta(days=athena.LOOKBACK_DAYS)
    assert dates[0] >= floor, f"이력 하한 밖의 날짜가 왔다: {dates[0]} < {floor}"
    assert int(athena.LAST["result_bytes"]) * 10 < int(athena.LAST["scanned"])
