"""bars_5m 의 장중 PIT 계약 — 실시간 5분봉이 미래를 안 읽고, 이력에 이어지는지.

1분 워커 롤업은 **닫힌 버킷만** 쓴다. 그래서 파티션에 나타난 봉은 확정분이고,
'그 봉을 써도 되는가'는 해결된 것처럼 보인다. 아니다: 13:00 에 그날 파티션을
통째로 읽으면 15:30 봉도 같이 온다. 날짜 절단은 장중 PIT 가 아니다. 그 구분을
컬럼 하나(`available_at`)와 인자 하나(`bars(as_of=)`)가 진다.

여기에 축 문제가 하나 더 붙는다: 상류 롤업 워커가 심볼을 bare 로 쓴다. 축이
갈리면 PIT 가 맞아도 실시간 봉이 이력에 안 붙는다 - 둘 다 여기서 검사한다.

S3 는 절대 안 건드린다(test_duck_runtime 규율). canonical 스키마를 그대로 가진
로컬 parquet 을 `s3_intraday_5m` 이라는 이름으로 걸어 S3 가지를 흉내 낸다.
"""
from datetime import date, datetime

import duckdb
import pytest

from edge_analysis.statics.duck import ROLLUP_FROM, CausalLake

# 롤업 전/후를 갈라 보려면 경계일이 상수와 묶여 있어야 한다 - 상수를 바꾸면 테스트도
# 같이 움직이지, 하드코딩된 날짜가 조용히 틀리지 않는다.
BEFORE, AFTER = "2026-07-31", ROLLUP_FROM
MAPPED = "2026-07-28"       # ROLLUP_FROM - 10 창 안 = 접미사 매핑이 뽑히는 날


def _lake(bars_dir, *, s3_rows=None):
    """실물 연결 위의 `_bars` 만 떼어 돌린다 — `__init__` 은 S3·RDB 로 나간다.

    `s3_rows` 가 있으면 canonical 스키마 뷰를 로컬 VALUES 로 세우고 `self.s3` 에
    이름을 넣는다 = `_bars` 입장에서 'S3 가 있다'.
    """
    lk = CausalLake.__new__(CausalLake)
    lk.con = duckdb.connect()
    lk.exists, lk.unbound, lk.s3 = {}, {}, {}
    # `__init__` 이 세우는 나머지 상태. 스텁이라 손으로 채운다 - 프로덕션 쪽을
    # `getattr` 로 무르게 만들면 '초기화가 이걸 세운다' 는 불변식이 사라진다.
    # `_heavy_cut = 0` 은 바이트 감시를 끈다: 프로파일링은 여기서 잴 것이 아니다.
    lk.deferred, lk.heavy, lk._heavy_cut = {}, [], 0
    if s3_rows:
        lk.con.execute(
            "CREATE VIEW s3_intraday_5m AS SELECT * FROM (VALUES " + s3_rows + ") "
            "t(ticker, source_symbol, ts, open, high, low, close, volume, "
            "source_vendor, available_at, market, trade_date)")
        lk.s3["s3_intraday_5m"] = "canonical/market_data/intraday_5m"
    CausalLake._bars(lk, bars_dir)
    return lk


def _s3_row(sym, ts, close, vendor, avail=None, day=AFTER):
    """canonical 한 행. `avail=None` 이면 컬럼이 NULL - 롤업 계약으로 유도돼야 한다.

    `sym` 을 그대로 `source_symbol` 에 넣는다 - bare 를 주면 상류 버그 재현이다.
    `ticker` 는 두 벤더 모두 bare 다(실측).
    """
    a = f"TIMESTAMP '{avail}'" if avail else "NULL::TIMESTAMP"
    return (f"('{sym.split('.')[0]}', '{sym}', TIMESTAMP '{ts}', 1.0, 1.0, 1.0, "
            f"CAST({close} AS DOUBLE), 100, '{vendor}', {a}, 'KR', DATE '{day}')")


def _backfill(tmp_path, rows):
    """available_at·source_vendor 가 **없는** 백필 parquet (fmp 시절 스키마)."""
    d = tmp_path / "bars"
    d.mkdir(exist_ok=True)
    duckdb.connect().execute(
        f"COPY (SELECT * FROM (VALUES {rows}) t(symbol, datetime, open, high, low, "
        f"close, volume)) TO '{(d / 'part.parquet').as_posix()}' (FORMAT parquet)")
    return d


def _bf_row(sym, ts, close):
    return f"('{sym}', TIMESTAMP '{ts}', 1.0, 1.0, 1.0, {close}, 100)"


# ── available_at 유도 ───────────────────────────────────────────────────
def test_backfill_rows_derive_available_at_from_bar_close(tmp_path):
    """백필엔 컬럼이 없다. 없다고 뷰에서 빠지면 하류는 as_of 를 못 건다 -
    롤업과 같은 계약(구간이 닫히는 시각)으로 유도하고, 유도분임을 벤더로 말한다."""
    lk = _lake(_backfill(tmp_path, _bf_row("005930.KS", f"{BEFORE} 09:05:00", 70000)))

    assert lk.con.execute(
        "SELECT ts, available_at, source_vendor, ticker, trade_date FROM bars_5m"
    ).fetchall() == [(datetime(2026, 7, 31, 9, 5), datetime(2026, 7, 31, 9, 10),
                      "fmp_backfill", "005930", date(2026, 7, 31))]


def test_null_available_at_in_canonical_falls_back_to_same_contract(tmp_path):
    # canonical 에 컬럼은 있어도 값이 비는 행이 섞일 수 있다 - NULL 이 새면
    # `available_at <= as_of` 가 그 행을 통째로 떨어뜨려 봉이 조용히 사라진다.
    lk = _lake(tmp_path / "none", s3_rows=_s3_row("005930", f"{AFTER} 09:05:00",
                                                  70000, "1m_rollup", avail=None))

    assert lk.con.execute("SELECT available_at FROM bars_5m").fetchone()[0] \
        == datetime(2026, 8, 4, 9, 10)


# ── 장중 절단 ───────────────────────────────────────────────────────────
def test_as_of_refuses_bars_that_had_not_closed_yet(tmp_path):
    """13:00 의 분석은 13:00 까지만 안다. 파티션에 15:30 봉이 있어도 마찬가지다."""
    rows = ",".join(_s3_row("005930.KS", f"{AFTER} {t}", 70000 + i, "1m_rollup",
                            avail=f"{AFTER} {a}")
                    for i, (t, a) in enumerate([("12:50:00", "12:55:00"),
                                                ("12:55:00", "13:00:00"),
                                                ("13:00:00", "13:05:00"),
                                                ("15:25:00", "15:30:00")]))
    lk = _lake(tmp_path / "none", s3_rows=rows)

    cut = lk.bars("005930.KS", AFTER, as_of=datetime(2026, 8, 4, 13, 0))

    # 경계는 포함이다: 13:00 에 닫힌 봉(12:55)은 알 수 있었고, 13:00 봉은 13:05 에야 안다.
    assert [str(t) for t, _ in cut] == [f"{AFTER} 12:50:00", f"{AFTER} 12:55:00"]
    assert len(lk.bars("005930.KS", AFTER)) == 4      # as_of 없으면 그날 전량


def test_as_of_before_the_open_yields_nothing_rather_than_everything(tmp_path):
    # 장 시작 전이면 '아직 없음'이 정답이다. 빈 절단이 전량 폴백으로 새면
    # 그게 정확히 선견이다 - 부재를 부재로 낸다.
    lk = _lake(tmp_path / "none", s3_rows=_s3_row("005930.KS", f"{AFTER} 09:05:00",
                                                  70000, "1m_rollup",
                                                  avail=f"{AFTER} 09:10:00"))

    assert lk.bars("005930.KS", AFTER, as_of=datetime(2026, 8, 4, 8, 30)) == []


# ── 정본 중복 제거 ──────────────────────────────────────────────────────
def test_local_backfill_is_canonical_before_rollup_started(tmp_path):
    """`ROLLUP_FROM` 이전의 정본은 fmp 이고, 로컬 백필이 곧 그 fmp 를 더 깊게 다시
    받은 것이다(끊긴 구간을 앵커를 옮겨 메운 판). 같은 (symbol, trade_date) 를
    둘 다 가지면 로컬이 이긴다 - 안 그러면 다시 받은 이유가 없어진다."""
    lk = _lake(_backfill(tmp_path, _bf_row("005930.KS", f"{BEFORE} 09:05:00", 999)),
               s3_rows=_s3_row("005930.KS", f"{BEFORE} 09:05:00", 111, "fmp",
                               avail=f"{BEFORE} 09:10:00", day=BEFORE))

    assert lk.con.execute("SELECT close, source_vendor FROM bars_5m").fetchall() \
        == [(999.0, "fmp_backfill")]


def test_rollup_is_canonical_once_realtime_started(tmp_path):
    """`ROLLUP_FROM` 이후엔 뒤집힌다. 로컬 백필기가 'today' 를 같이 긁어 오면
    확정 롤업 봉과 겹치는데, 그때 정본은 닫힌 버킷을 쓰는 롤업이다."""
    lk = _lake(_backfill(tmp_path, _bf_row("005930.KS", f"{AFTER} 09:05:00", 999)),
               s3_rows=_s3_row("005930.KS", f"{AFTER} 09:05:00", 222, "1m_rollup",
                               avail=f"{AFTER} 09:10:00"))

    assert lk.con.execute("SELECT close, source_vendor FROM bars_5m").fetchall() \
        == [(222.0, "1m_rollup")]


def test_the_cut_only_removes_days_the_canonical_source_actually_has(tmp_path):
    """안티조인은 **상대가 가진 (symbol, trade_date) 만** 지운다. 시대로 뭉뚱그려
    자르면 롤업이 안 받는 종목·날짜가 통째로 사라져 '거래 없는 날'이 된다."""
    lk = _lake(_backfill(tmp_path, ",".join([
                   # 롤업이 받는 날 - S3 가 이겨서 로컬이 빠진다.
                   _bf_row("005930.KS", f"{AFTER} 09:05:00", 999),
                   # 롤업이 그 종목을 안 받는 날 - 지울 상대가 없으니 살아남는다.
                   _bf_row("069500.KS", f"{AFTER} 09:05:00", 888)])),
               s3_rows=_s3_row("005930.KS", f"{AFTER} 09:05:00", 222, "1m_rollup",
                               avail=f"{AFTER} 09:10:00"))

    assert lk.con.execute(
        "SELECT symbol, close, source_vendor FROM bars_5m ORDER BY 1").fetchall() \
        == [("005930.KS", 222.0, "1m_rollup"), ("069500.KS", 888.0, "fmp_backfill")]


def test_the_cut_matches_bare_rollup_symbols_against_suffixed_local_ones(tmp_path):
    """안티조인 키도 **정규화된 축** 위에 있어야 한다. 롤업의 `source_symbol` 은
    bare 인데 로컬은 접미사가 붙어 있어, 날것으로 잡으면 키가 영영 안 맞고 겹침이
    조용히 통과한다 - 실측으로 2026-08-05 에 (symbol, ts) 중복 156쌍이 샜다."""
    lk = _lake(_backfill(tmp_path, _bf_row("025560.KS", f"{AFTER} 09:05:00", 999)),
               s3_rows=",".join([
                   # 매핑 원천(로컬) + bare 로 오는 실시간 롤업.
                   _s3_row("005930.KS", f"{MAPPED} 09:05:00", 70000, "fmp",
                           avail=f"{MAPPED} 09:10:00", day=MAPPED),
                   _s3_row("025560", f"{AFTER} 09:05:00", 510, "1m_rollup",
                           avail=f"{AFTER} 09:10:00")]))

    assert lk.con.execute(
        "SELECT symbol, ts FROM bars_5m GROUP BY 1, 2 HAVING count(*) > 1").fetchall() == []
    assert lk.con.execute(
        "SELECT close, source_vendor FROM bars_5m WHERE ticker = '025560'").fetchall() \
        == [(510.0, "1m_rollup")]


def test_dedup_keeps_a_bar_the_canonical_vendor_never_delivered(tmp_path):
    # 롤업이 결측인 구간. 겹칠 때 하나를 고르는 규칙이지 비정본을 지우는 규칙이
    # 아니다 - 여기서 행이 사라지면 롤업 장애가 '거래 없는 날'로 보인다.
    lk = _lake(_backfill(tmp_path, _bf_row("005930.KS", f"{AFTER} 09:05:00", 999)))

    assert lk.con.execute("SELECT close, source_vendor FROM bars_5m").fetchall() \
        == [(999.0, "fmp_backfill")]


# ── 로컬 백필이 S3 를 보충한다 (폴백 아님) ──────────────────────────────
def test_local_backfill_fills_days_canonical_does_not_have(tmp_path):
    """ETF·시장지수는 canonical 이 43일뿐이라 층분해가 죽는다. 로컬을 폴백으로만
    보면 S3 가 있는 한 그 parquet 은 영원히 안 읽힌다 - 합집합이어야 메워진다."""
    lk = _lake(_backfill(tmp_path, ",".join([_bf_row("069500.KS", "2026-05-02 09:05:00", 41),
                                             _bf_row("069500.KS", "2026-05-04 09:05:00", 42)])),
               s3_rows=_s3_row("069500.KS", f"{BEFORE} 09:05:00", 43, "fmp",
                               avail=f"{BEFORE} 09:10:00", day=BEFORE))

    assert lk.con.execute(
        "SELECT trade_date, close FROM bars_5m ORDER BY 1").fetchall() == [
            (date(2026, 5, 2), 41.0), (date(2026, 5, 4), 42.0), (date(2026, 7, 31), 43.0)]
    assert "S3 canonical + 로컬 백필 (2행)" == lk.exists["bars_5m"]


def test_the_view_never_puts_a_window_in_the_query_plan(tmp_path):
    """겹침을 `QUALIFY row_number()` 로 걸렀더니 층분해 시각창 질의(≈34M행)가
    memory_limit 1.5GB 에서 OOM 으로 죽었다 - 창 연산자는 블로킹이라 집계 전에
    파티션을 물리화한다. 서로소 구성은 생성 시점에 끝나야 하고 질의 계획엔 창이
    한 개도 없어야 한다. 계획을 직접 보는 이유: 결과만 보면 회귀를 못 잡는다."""
    lk = _lake(_backfill(tmp_path, _bf_row("005930.KS", f"{BEFORE} 09:05:00", 999)),
               s3_rows=_s3_row("005930.KS", f"{BEFORE} 09:05:00", 111, "fmp",
                               avail=f"{BEFORE} 09:10:00", day=BEFORE))

    plan = lk.con.execute(
        "EXPLAIN SELECT symbol, sum(volume) FROM bars_5m "
        "WHERE CAST(ts AS TIME) >= TIME '13:00:00' GROUP BY 1").fetchall()[0][1]

    assert "WINDOW" not in plan.upper()


def test_local_backfill_also_teaches_the_suffix_map(tmp_path):
    """canonical 이 안 가진 종목을 로컬이 채운다(실측 025560 841일 · 066970 979일).
    매핑 원천이 S3 뿐이면 그 종목의 실시간 롤업 행은 bare 로 남아 방금 받은 이력과
    축이 갈린다 - 로컬을 못 채운 것이 아니라 채우고도 안 이어지는 상태가 된다."""
    lk = _lake(_backfill(tmp_path, _bf_row("025560.KS", f"{BEFORE} 09:05:00", 500)),
               s3_rows=",".join([
                   _s3_row("005930.KS", f"{MAPPED} 09:05:00", 70000, "fmp",
                           avail=f"{MAPPED} 09:10:00", day=MAPPED),
                   # 025560 은 fmp 이력에 없다 - 로컬만이 접미사를 안다.
                   _s3_row("025560", f"{AFTER} 09:05:00", 510, "1m_rollup",
                           avail=f"{AFTER} 09:10:00")]))

    assert lk.con.execute(
        "SELECT DISTINCT symbol FROM bars_5m WHERE ticker = '025560'").fetchall() \
        == [("025560.KS",)]
    assert len(lk.bars("025560.KS", BEFORE)) == 1        # 로컬 이력
    assert len(lk.bars("025560.KS", AFTER)) == 1         # 실시간 롤업, 같은 축


# ── 심볼 축 정규화 ──────────────────────────────────────────────────────
def test_bare_rollup_symbols_are_stitched_onto_the_fmp_axis(tmp_path):
    """상류 롤업 워커가 bare 로 쓴다(실측). 축이 갈리면 실시간 봉이 이력에 안 붙어
    같은 종목이 '1일짜리 종목' 둘로 보인다. 접미사는 fmp 이력에서 **유도**한다 -
    KOSDAQ 이 1/3 이라 `.KS` 를 무조건 붙이면 그만큼이 깨진다."""
    rows = ",".join([
        # 매핑 원천: ROLLUP_FROM-10 창 안의 fmp 행. KS 와 KQ 를 하나씩 준다.
        _s3_row("005930.KS", f"{MAPPED} 09:05:00", 70000, "fmp",
                avail=f"{MAPPED} 09:10:00", day=MAPPED),
        _s3_row("247540.KQ", f"{MAPPED} 09:05:00", 300000, "fmp",
                avail=f"{MAPPED} 09:10:00", day=MAPPED),
        # 실시간 롤업: 둘 다 bare 로 온다.
        _s3_row("005930", f"{AFTER} 09:05:00", 71000, "1m_rollup",
                avail=f"{AFTER} 09:10:00"),
        _s3_row("247540", f"{AFTER} 09:05:00", 301000, "1m_rollup",
                avail=f"{AFTER} 09:10:00")])
    lk = _lake(tmp_path / "none", s3_rows=rows)

    assert lk.con.execute(
        "SELECT DISTINCT symbol FROM bars_5m ORDER BY 1").fetchall() \
        == [("005930.KS",), ("247540.KQ",)]      # 4개로 쪼개져 있으면 여기서 죽는다
    # 이력과 실시간이 한 축에 붙어야 bars() 한 번으로 이어진다.
    assert len(lk.bars("247540.KQ", MAPPED)) == 1
    assert len(lk.bars("247540.KQ", AFTER)) == 1


def test_unmapped_bare_tickers_stay_bare_instead_of_guessing_a_suffix(tmp_path):
    # fmp 이력에 없는 티커가 실측 69개다. 이을 이력이 없으므로 접미사를 지어내면
    # 그건 1/3 확률로 틀린 거짓이다 - bare 로 남겨 안 이어졌음을 보이게 둔다.
    rows = ",".join([_s3_row("005930.KS", f"{MAPPED} 09:05:00", 70000, "fmp",
                             avail=f"{MAPPED} 09:10:00", day=MAPPED),
                     _s3_row("0005G0", f"{AFTER} 09:05:00", 1000, "1m_rollup",
                             avail=f"{AFTER} 09:10:00")])
    lk = _lake(tmp_path / "none", s3_rows=rows)

    assert lk.con.execute(
        "SELECT DISTINCT symbol FROM bars_5m ORDER BY 1").fetchall() \
        == [("0005G0",), ("005930.KS",)]


def test_symbol_map_never_duplicates_bars_when_a_ticker_moved_market(tmp_path):
    """한 티커가 KQ→KS 로 옮기면 매핑이 둘이 된다. 조인이 불어나면 봉이 복제되고
    수익률이 두 번 세어진다 - 매핑은 티커당 정확히 하나여야 한다."""
    rows = ",".join([_s3_row("123456.KQ", f"{MAPPED} 09:05:00", 100, "fmp",
                             avail=f"{MAPPED} 09:10:00", day=MAPPED),
                     _s3_row("123456.KS", f"{BEFORE} 09:05:00", 101, "fmp",
                             avail=f"{BEFORE} 09:10:00", day=BEFORE),
                     _s3_row("123456", f"{AFTER} 09:05:00", 102, "1m_rollup",
                             avail=f"{AFTER} 09:10:00")])
    lk = _lake(tmp_path / "none", s3_rows=rows)

    assert lk.con.execute("SELECT count(*) FROM bars_5m").fetchone()[0] == 3


# ── 원천 부재 ───────────────────────────────────────────────────────────
def test_absent_sources_report_absence_instead_of_an_empty_view(tmp_path):
    # 빈 뷰는 '봉 없는 날'과 구분이 안 된다. exists 가 falsy 여야 하류가 멈춘다.
    lk = _lake(tmp_path / "none")

    assert not lk.exists["bars_5m"]
    with pytest.raises(RuntimeError, match="bars_5m 없음"):
        lk.bars("005930.KS", AFTER)
