"""원천 관측 수집 → 정제 → 적재 → 기준시각 조회 E2E — 실 PostgreSQL (ALPHA-1130).

단위 테스트는 레이크까지만 본다. 소비 계약은 DB 함수(`*_as_of`)이므로 "기준시각 T 에 무엇이 보이는가"는
TIMESTAMPTZ·DISTINCT ON·CHECK 가 실제로 도는 PostgreSQL 위에서 확인한다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# 단위 테스트와 같은 응답 생성기(tests/source_observation_fakes.py)를 쓴다 — e2e 단독 실행에서도 찾게 한다.
sys.path.insert(0, str(Path(__file__).parents[1]))

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "source_observations"
RUN = "e2e_so_"     # 이 테스트의 run_id 접두 — 정리 범위


def _db():
    from data_pipeline.config import DbConfig

    return DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ.get("E2E_PGPORT", "5432")),
                    name=os.environ.get("E2E_PGDATABASE", "edge"), user=os.environ.get("E2E_PGUSER", "edge"),
                    password=os.environ.get("E2E_PGPASSWORD", "edge"), sslmode="disable")


@pytest.fixture()
def conn():
    import psycopg

    db = _db()
    connection = psycopg.connect(host=db.host, port=db.port, dbname=db.name, user=db.user,
                                 password=db.password, autocommit=True)
    cleanup = ["DELETE FROM macro_observation WHERE raw_run_id LIKE %s",
               "DELETE FROM financial_metric WHERE raw_run_id LIKE %s",
               "DELETE FROM financial_report_version WHERE raw_run_id LIKE %s",
               "DELETE FROM sector_classification WHERE raw_run_id LIKE %s"]
    for sql in cleanup:
        connection.execute(sql, (RUN + "%",))
    yield connection
    for sql in cleanup:
        connection.execute(sql, (RUN + "%",))
    connection.close()


class _Client:
    def __init__(self, routes):
        self.routes = routes

    def request(self, method, url, *, headers=None, data=None, decode=True):
        return next(v for k, v in self.routes.items() if k in url)


def _macro_source(usd_body: bytes):
    from data_pipeline.config import MacroObservationSource
    from data_pipeline.sources import macro_series

    routes = {"731Y003": usd_body,
              "treasury-rates": (FIXTURES / "fmp_treasury.json").read_bytes()}
    return macro_series.MacroSource(MacroObservationSource(ecos_api_key="E"), fmp_api_key="F", client=_Client(routes))


def _macro_run(storage, tag: str, usd_body: bytes) -> str:
    """수집·정제 한 번. 반환: 정제 run_id(적재 입력)."""
    from data_pipeline.steps import source_observations as so

    now = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)
    assert so.collect_macro(storage, _macro_source(usd_body), f"{RUN}{tag}_raw",
                            series_ids=["usd_krw", "us_10y_yield"], from_date=None, to_date=None, now=now) == 0
    assert so.normalize(storage, so.MACRO, f"{RUN}{tag}_norm", f"{RUN}{tag}_raw", producer="normalize_macro") == 0
    return f"{RUN}{tag}_norm"


def _as_of(conn, at, series="usd_krw", limit=21):
    return conn.execute(
        "SELECT observation_date::text, value::text, raw_run_id, raw_key, canonical_run_id, available_at"
        " FROM macro_observations_as_of(%s, %s, %s) ORDER BY observation_date", (at, series, limit)).fetchall()


def test_macro_chain_lands_versions_and_the_as_of_query_respects_receipt(tmp_path, conn):
    from data_pipeline.lake import LocalStorage, run_manifest_consumed_key
    from data_pipeline.steps import source_observations as so

    storage = LocalStorage(tmp_path)
    usd = (FIXTURES / "ecos_usdkrw.json").read_bytes()
    first = _macro_run(storage, "a", usd)

    # 저장 뒤 적재 전에 멈췄다가 복구: 마커 없는 완료 manifest 를 --all 이 싣는다.
    assert so.load(storage, so.MACRO, _db(), f"{RUN}load1", input_run_id=None, pending=True,
                   producer="load_macro") == 0
    assert storage.list_keys(run_manifest_consumed_key("canonical", "macro_observation", first, so.CONSUMER))
    # 같은 실행 재적재는 행을 늘리지 않는다.
    count = conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id LIKE %s", (RUN + "%",)).fetchone()[0]
    assert so.load(storage, so.MACRO, _db(), f"{RUN}load2", input_run_id=first, pending=False,
                   producer="load_macro") == 0
    assert conn.execute("SELECT count(*) FROM macro_observation WHERE raw_run_id LIKE %s",
                        (RUN + "%",)).fetchone()[0] == count == 6

    received = conn.execute("SELECT min(received_at) FROM macro_observation WHERE raw_run_id = %s",
                            (f"{RUN}a_raw",)).fetchone()[0]
    # 수신 전 기준시각에는 아무것도 보이지 않는다(미래 수신 제외).
    assert _as_of(conn, received - timedelta(seconds=1)) == []
    rows = _as_of(conn, received)
    assert [(d, v) for d, v, *_ in rows] == [("2026-07-24", "1459.66"), ("2026-07-27", "1464.671"),
                                            ("2026-07-28", "1461.35")]
    # DB 행이 manifest·원천 근거로 이어진다.
    manifest = json.loads(storage.get_bytes(
        f"operations_archive/canonical_run_manifests/dataset=macro_observation/run_id={first}/manifest.json"))
    assert {r[4] for r in rows} == {first} and rows[0][3].startswith("raw/source=ecos/dataset=macro_observation/")
    assert storage.get_bytes(rows[0][3])     # raw 원문이 그 키에 있다
    assert manifest["artifact"]["rows"] == 6
    # "최근 공개 2관측일" 요구는 limit 로 고른다 — 툴 기본 21 과 섞지 않는다.
    assert [d for d, *_ in _as_of(conn, received, limit=2)] == ["2026-07-27", "2026-07-28"]

    # 공급자 정정: 새 실행의 값은 그 수신 이후에만 보이고, 이전 기준시각은 옛 값을 그대로 본다.
    revised = json.loads(usd)
    revised["StatisticSearch"]["row"][1]["DATA_VALUE"] = "1465.0"
    second = _macro_run(storage, "b", json.dumps(revised).encode())
    assert so.load(storage, so.MACRO, _db(), f"{RUN}load3", input_run_id=second, pending=False,
                   producer="load_macro") == 0
    received_b = conn.execute("SELECT min(received_at) FROM macro_observation WHERE raw_run_id = %s",
                              (f"{RUN}b_raw",)).fetchone()[0]
    assert received_b > received
    assert dict((d, v) for d, v, *_ in _as_of(conn, received_b - timedelta(microseconds=1)))["2026-07-27"] == "1464.671"
    assert dict((d, v) for d, v, *_ in _as_of(conn, received_b))["2026-07-27"] == "1465.0"


def test_db_rejects_mixed_units_and_early_receipt(conn):
    # WHY: 계열·단위·공급자 쌍과 "관측 기간이 끝난 뒤 수신"을 DB 가 강제한다 — 코드 한 곳이 틀려도 섞이지 않게.
    import psycopg

    base = {"series_id": "kr_10y_yield", "observation_date": "2026-07-28", "value": "2.9", "unit": "percent",
            "source_vendor": "ecos", "source_series": "ECOS 817Y002/D/010210000",
            "received_at": "2026-07-29T01:00:00+00:00", "available_at": "2026-07-29T01:00:00+00:00",
            "availability_basis": "received", "raw_run_id": f"{RUN}chk", "raw_key": "k", "raw_sha256": "0" * 64,
            "canonical_run_id": "c", "artifact_key": "a", "artifact_sha256": "0" * 64}
    sql = (f"INSERT INTO macro_observation ({', '.join(base)}) VALUES ({', '.join(['%s'] * len(base))})")
    conn.execute(sql, list(base.values()))
    for bad in ({"unit": "percentage_points", "raw_run_id": f"{RUN}u"},
                {"observation_date": "2026-07-29", "raw_run_id": f"{RUN}e"},       # 수신 당일(KST) 관측
                {"available_at": "2026-07-28T00:00:00+00:00", "raw_run_id": f"{RUN}v"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(sql, list({**base, **bad}.values()))


def _financial_loaded(tmp_path):
    """삼성전자(연결·별도)·SK하이닉스(별도만) 정기보고서 4건을 수집·정제·적재한다."""
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import source_observations as so
    from source_observation_fakes import (FILINGS, HYNIX, SAMSUNG, DartFake, filing_list, shares, statement,
                                          write_holdings)

    responses = {}
    for corp, fs_divs in ((SAMSUNG, ("CFS", "OFS")), (HYNIX, ("OFS",))):
        for year, code in FILINGS:
            for fs in fs_divs:
                responses[("statement", corp["corp_code"], year, code, fs)] = statement(corp, year, code, fs)
            responses[("shares", corp["corp_code"], year, code)] = shares(corp, year, code, treasury=50)
    dart = DartFake([SAMSUNG, HYNIX], responses,
                    {SAMSUNG["corp_code"]: filing_list(SAMSUNG), HYNIX["corp_code"]: filing_list(HYNIX)})
    storage = LocalStorage(tmp_path)
    write_holdings(storage, "2026-08-14", ["005930", "000660"])
    now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    assert so.collect_financial(storage, dart, f"{RUN}f_raw", etf_ids=["091160"], from_date="2025-10-01",
                                to_date="2026-08-20", now=now) == 0
    assert so.normalize(storage, so.FINANCIAL, f"{RUN}f_norm", f"{RUN}f_raw",
                        producer="normalize_financial_metric") == 0
    assert so.load(storage, so.FINANCIAL, _db(), f"{RUN}f_load", input_run_id=f"{RUN}f_norm", pending=False,
                   producer="load_financial_metric") == 0
    return storage


def _quarters(conn, at, code):
    return conn.execute(
        "SELECT period, fs_basis, eps::text, eps_derivation, bps::text, revenue::text, available_at"
        " FROM financial_quarters_as_of(%s, %s)", (at, code)).fetchall()


KST = timezone(timedelta(hours=9))


def test_financial_quarters_follow_release_dates_and_never_mix_bases(tmp_path, conn):
    _financial_loaded(tmp_path)
    # 사업보고서 접수일(03-10) 당일에는 아직 안 보인다 — 다음날 00:00 KST 부터(시각을 지어내지 않는다).
    before = _quarters(conn, datetime(2026, 3, 10, 23, 59, tzinfo=KST), "005930")
    assert [p for p, *_ in before] == ["2025-Q3"]
    after = _quarters(conn, datetime(2026, 3, 11, 0, 0, tzinfo=KST), "005930")
    q4 = {p: rest for p, *rest in after}["2025-Q4"]
    assert q4[:3] == ["CFS", "1100", "FY_MINUS_9M"]
    # 반기 공개 뒤 연속 4분기가 모두 해당 분기 EPS 로 나온다(누적 아님).
    latest = _quarters(conn, datetime(2026, 8, 15, 9, 0, tzinfo=KST), "005930")
    assert [(p, eps) for p, _, eps, *_ in latest] == [("2025-Q3", "1000"), ("2025-Q4", "1100"),
                                                      ("2026-Q1", "1100"), ("2026-Q2", "1200")]
    assert {basis for _, basis, *_ in latest} == {"CFS"}          # 연결이 있는 회사는 연결만
    hynix = _quarters(conn, datetime(2026, 8, 15, 9, 0, tzinfo=KST), "000660")
    assert hynix and {basis for _, basis, *_ in hynix} == {"OFS"}  # 연결이 없는 회사만 별도


def test_sector_as_of_and_constituent_coverage_at_analysis_time(tmp_path, conn):
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import source_observations as so
    from source_observation_fakes import SECTOR_ROUTES, SectorClient

    _financial_loaded(tmp_path / "fin")
    storage = LocalStorage(tmp_path / "sector")
    assert so.collect_sector(storage, SectorClient(SECTOR_ROUTES), "https://example.invalid", f"{RUN}s_raw",
                             now=datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)) == 0
    assert so.normalize(storage, so.SECTOR, f"{RUN}s_norm", f"{RUN}s_raw", producer="normalize_sector") == 0
    assert so.load(storage, so.SECTOR, _db(), f"{RUN}s_load", input_run_id=f"{RUN}s_norm", pending=False,
                   producer="load_sector") == 0
    received = conn.execute("SELECT min(received_at) FROM sector_classification WHERE raw_run_id=%s",
                            (f"{RUN}s_raw",)).fetchone()[0]
    rows = conn.execute("SELECT instrument_code, found, large_code, large_name, small_code FROM"
                        " sector_classification_as_of(%s, %s)", (received, ["005930", "091160", "123456"])).fetchall()
    assert rows == [("005930", True, "0013", "전기·전자", None),      # 소분류 0000 → 분류 없음
                    ("091160", True, None, None, None),                # 대분류부터 0000
                    ("123456", False, None, None, None)]               # 스냅샷에 없는 종목 — 분류 없음과 다르다
    # 원천이 현재값만 주므로 받기 전 시점에는 아무 분류도 없다(과거 분류를 복원하지 않는다).
    assert conn.execute("SELECT bool_or(found) FROM sector_classification_as_of(%s, %s)",
                        (received - timedelta(seconds=1), ["005930"])).fetchone()[0] is False

    # 기준시각의 구성종목 스냅샷 × 원천 확보 여부. 스냅샷 전 시점은 0행(현재 구성으로 대신하지 않는다).
    # 마이그레이션 시드에 있는 종목(091160·005930·000660)은 그 행을 쓰고, 없는 종목만 이 테스트가 만든다.
    wanted = {"091160": ("ETF", "XKRX"), "005930": ("EQUITY", "XKRX"), "000660": ("EQUITY", "XKRX"),
              "058470": ("EQUITY", "XKOS")}
    ids, created = {}, []
    try:
        for ticker, (kind, mic) in wanted.items():
            found = conn.execute("SELECT instrument_id FROM instrument WHERE market_code=%s AND ticker=%s",
                                 (mic, ticker)).fetchone()
            if found is None:
                iid = f"e2e_so_{ticker}"
                conn.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                             " VALUES (%s,'INSTRUMENT',%s)", (iid, ticker))
                conn.execute("INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)"
                             " VALUES (%s,%s,%s,%s)", (iid, mic, ticker, kind))
                created.append(iid)
                found = (iid,)
            ids[ticker] = found[0]
        etf = ids["091160"]
        conn.execute("INSERT INTO etf_profile (instrument_id) VALUES (%s) ON CONFLICT DO NOTHING", (etf,))
        conn.execute("INSERT INTO etf_holding_snapshot_status (etf_instrument_id, trade_date, input_row_count,"
                     " valid_row_count, data_version) VALUES (%s,'2026-08-14',3,3,'e2e_so')", (etf,))
        for ticker, weight in (("005930", 0.5), ("000660", 0.3), ("058470", 0.2)):
            conn.execute("INSERT INTO etf_holding_snapshot (etf_instrument_id, constituent_instrument_id,"
                         " trade_date, weight_ratio, available_at, data_version)"
                         " VALUES (%s,%s,'2026-08-14',%s,'2026-08-14T18:00:00+09:00','e2e_so')",
                         (etf, ids[ticker], weight))
        # 두 시장 파일을 차례로 받으므로 KOSDAQ 행은 KOSPI 보다 늦게 보인다 — 실행의 마지막 수신 시각에서 본다.
        last = conn.execute("SELECT max(received_at) FROM sector_classification WHERE raw_run_id=%s",
                            (f"{RUN}s_raw",)).fetchone()[0]
        coverage = conn.execute(
            "SELECT constituent_ticker, has_sector_classification, eps_quarters, latest_eps_period"
            " FROM etf_constituent_source_coverage('091160', %s)", (last,)).fetchall()
        assert coverage == [("000660", False, 4, "2026-Q2"), ("005930", True, 4, "2026-Q2"),
                            ("058470", True, 0, None)]
        assert conn.execute("SELECT count(*) FROM etf_constituent_source_coverage('091160',"
                            " '2026-08-14T17:00:00+09:00')").fetchone()[0] == 0
    finally:
        conn.execute("DELETE FROM etf_holding_snapshot WHERE data_version='e2e_so'")
        conn.execute("DELETE FROM etf_holding_snapshot_status WHERE data_version='e2e_so'")
        for iid in created:
            conn.execute("DELETE FROM entity WHERE entity_id=%s", (iid,))


# ── 보고서 판본(financial_report_version) — 실 PostgreSQL 조회 계약 ──────────────────────

def _financial_run(tmp_path, tag, responses, fetched_at, *, from_date, to_date, now, load=True):
    """삼성전자 한 회사의 수집 → 정제 → (적재). 응답 표는 호출자가 고쳐 넣는다. 반환: (storage, raw, norm)."""
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import source_observations as so
    from source_observation_fakes import SAMSUNG, DartFake, filing_list, write_holdings

    dart = DartFake([SAMSUNG], responses, {SAMSUNG["corp_code"]: filing_list(SAMSUNG)})
    dart.fetched_at = fetched_at
    storage = LocalStorage(tmp_path / tag)
    write_holdings(storage, "2026-08-14", ["005930"])
    raw, norm = f"{RUN}{tag}_raw", f"{RUN}{tag}_norm"
    assert so.collect_financial(storage, dart, raw, etf_ids=["091160"], from_date=from_date, to_date=to_date,
                                now=now) in (0, 2)
    assert so.normalize(storage, so.FINANCIAL, norm, raw, producer="normalize_financial_metric") in (0, 2)
    if load:
        assert so.load(storage, so.FINANCIAL, _db(), f"{RUN}{tag}_load", input_run_id=norm, pending=False,
                       producer="load_financial_metric") == 0
    return storage, raw, norm


def _samsung_responses():
    from source_observation_fakes import FILINGS, SAMSUNG, shares, statement

    responses = {}
    for year, code in FILINGS:
        for fs in ("CFS", "OFS"):
            responses[("statement", SAMSUNG["corp_code"], year, code, fs)] = statement(SAMSUNG, year, code, fs)
        responses[("shares", SAMSUNG["corp_code"], year, code)] = shares(SAMSUNG, year, code, treasury=50)
    return responses


def _q(conn, at, code="005930"):
    return {p: rest for p, *rest in conn.execute(
        "SELECT period, eps::text, bps::text, bps_note, version_raw_run_id, latest_unconfirmed_at, shares_status,"
        " version_rejected FROM financial_quarters_as_of(%s, %s)", (at, code)).fetchall()}


def test_zero_metric_correction_replaces_values_and_failures_do_not(tmp_path, conn):
    """지표 0개 정정 판본 · 일부 지표 판본 · 공급자 실패 vs 정상 무자료 · 늦게 끝난 옛 실행 — 조회 계약."""
    from source_observation_fakes import SAMSUNG, statement

    corp = SAMSUNG["corp_code"]
    now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    window = dict(from_date="2025-10-01", to_date="2026-08-20", now=now)
    _financial_run(tmp_path, "a", _samsung_responses(), "2026-08-20T00:00:00+00:00", **window)
    base = _q(conn, datetime(2026, 8, 21, tzinfo=KST))
    eps, bps = base["2026-Q2"][:2]
    q1 = base["2026-Q1"][:2]
    assert eps == "1200" and bps is not None and base["2026-Q2"][3] == f"{RUN}a_raw"

    # B(09-05): 반기 CFS 응답이 HTTP 파손 → UNCONFIRMED. 옛 확정값은 그대로, 실패 시각만 드러난다.
    broken = _samsung_responses()
    broken[("statement", corp, "2026", "11012", "CFS")] = b"<html>502</html>"
    _financial_run(tmp_path, "b", broken, "2026-09-05T00:00:00+00:00", from_date="2026-08-01",
                   to_date="2026-09-05", now=datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc))
    after_b = _q(conn, datetime(2026, 9, 6, tzinfo=KST))
    assert after_b["2026-Q2"][:2] == [eps, bps] and after_b["2026-Q2"][3] == f"{RUN}a_raw"
    assert after_b["2026-Q2"][4] is not None                       # latest_unconfirmed_at
    assert _q(conn, datetime(2026, 9, 4, tzinfo=KST))["2026-Q2"][4] is None   # 실패 전 시점엔 실패도 없다

    # C(09-10): 정정본을 정상으로 확인했으나 쓸 계정 줄이 없다 → CONFIRMED, 지표 0 → 옛 값이 최신처럼 남지 않는다.
    empty = _samsung_responses()
    body = json.loads(statement(SAMSUNG, "2026", "11012", "CFS"))
    for line in body["list"]:
        line["account_id"] = "x_unknown"
    empty[("statement", corp, "2026", "11012", "CFS")] = json.dumps(body).encode()
    _financial_run(tmp_path, "c", empty, "2026-09-10T00:00:00+00:00", from_date="2026-08-01",
                   to_date="2026-09-10", now=datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc))
    after_c = _q(conn, datetime(2026, 9, 11, tzinfo=KST))
    assert after_c["2026-Q2"][:4] == [None, None, "BPS_ABSENT_IN_LATEST_VERSION", f"{RUN}c_raw"]
    assert after_c["2026-Q2"][4] is None                           # C 뒤엔 실패가 없다(B 는 C 보다 앞)
    assert any(r["metric"] == "eps_basic" for r in after_c["2026-Q2"][6])   # 못 만든 지표와 사유가 판본에 있다
    assert after_c["2026-Q1"][:2] == q1                           # 다른 보고서는 영향 없다

    # D(09-12): 재무제표는 정상, 주식총수 응답만 파손 → 일부 지표 판본. 분모 부재는 "확정 못 함"이다.
    part = _samsung_responses()
    part[("shares", corp, "2026", "11012")] = b"<html>502</html>"
    _financial_run(tmp_path, "d", part, "2026-09-12T00:00:00+00:00", from_date="2026-08-01",
                   to_date="2026-09-12", now=datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc))
    after_d = _q(conn, datetime(2026, 9, 13, tzinfo=KST))
    assert after_d["2026-Q2"][:4] == ["1200", None, "BPS_UNCONFIRMED", f"{RUN}d_raw"] and after_d["2026-Q2"][5] == "error"
    # D2(09-14): 주식총수 "조회 데이터 없음"(013) → 정상 무자료 = 확정된 부재
    none = _samsung_responses()
    none[("shares", corp, "2026", "11012")] = json.dumps({"status": "013", "message": "없음"}).encode()
    _financial_run(tmp_path, "d2", none, "2026-09-14T00:00:00+00:00", from_date="2026-08-01",
                   to_date="2026-09-14", now=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc))
    after_d2 = _q(conn, datetime(2026, 9, 15, tzinfo=KST))
    assert after_d2["2026-Q2"][2:4] == ["BPS_ABSENT_IN_LATEST_VERSION", f"{RUN}d2_raw"] and after_d2["2026-Q2"][5] == "empty"

    # 늦게 끝난 옛 실행(08-16 에 받았는데 지금 적재) — 최신 확정(d2)을 덮지 못한다.
    _financial_run(tmp_path, "late", _samsung_responses(), "2026-08-16T00:00:00+00:00", from_date="2026-08-01",
                   to_date="2026-08-16", now=datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc))
    assert _q(conn, datetime(2026, 9, 15, tzinfo=KST))["2026-Q2"][3] == f"{RUN}d2_raw"
    # 같은 접수번호의 재수집은 모두 원 공개일(08-15)부터 보인다(결정 ①) — 그중 가장 늦게 받은 d2 가 과거 시점에서도 이긴다.
    # (새 접수번호를 단 정정본만 그 접수일부터 보인다 — test_financial_quarters_follow_release_dates 가 고정.)
    assert _q(conn, datetime(2026, 8, 17, tzinfo=KST))["2026-Q2"][3] == f"{RUN}d2_raw"


def test_interrupted_load_recovers_both_tables_and_duplicates_do_not_multiply(tmp_path, conn):
    """저장·적재 사이 중단 후 --all 복구, 같은 raw 의 중복 정제·적재."""
    from data_pipeline.steps import source_observations as so

    now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    storage, raw, norm = _financial_run(tmp_path, "i", _samsung_responses(), "2026-08-20T00:00:00+00:00",
                                        from_date="2025-10-01", to_date="2026-08-20", now=now, load=False)
    count = lambda table: conn.execute(f"SELECT count(*) FROM {table} WHERE raw_run_id = %s", (raw,)).fetchone()[0]
    assert count("financial_metric") == 0 and count("financial_report_version") == 0
    assert so.load(storage, so.FINANCIAL, _db(), f"{RUN}i_load", input_run_id=None, pending=True,
                   producer="load_financial_metric") == 0
    metrics, versions = count("financial_metric"), count("financial_report_version")
    assert metrics > 0 and versions == 8                            # 4 보고서 × CFS·OFS
    # 같은 raw 를 다시 정제(다른 run_id)해 적재해도 판본·지표가 늘지 않는다(같은 raw_run_id → PK 충돌은 무시)
    assert so.normalize(storage, so.FINANCIAL, f"{RUN}i_norm2", raw, producer="normalize_financial_metric") == 0
    assert so.load(storage, so.FINANCIAL, _db(), f"{RUN}i_load2", input_run_id=f"{RUN}i_norm2", pending=False,
                   producer="load_financial_metric") == 0
    assert (count("financial_metric"), count("financial_report_version")) == (metrics, versions)


def test_consolidated_basis_survives_empty_latest_cfs_and_never_confirmed_reports_show_as_attempts(tmp_path, conn):
    """연결 이력이 있는 회사의 최신 CFS 판본이 전부 비어도 별도 값으로 갈아타지 않는다. 확정이 한 번도 없는 보고서는
    UNCONFIRMED 시도 행으로 보인다."""
    from source_observation_fakes import SAMSUNG, statement

    corp = SAMSUNG["corp_code"]
    now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    _financial_run(tmp_path, "h", _samsung_responses(), "2026-08-20T00:00:00+00:00",
                   from_date="2025-10-01", to_date="2026-08-20", now=now)
    empty_cfs = _samsung_responses()
    for (kind, c, year, code, *fs) in list(empty_cfs):
        if kind == "statement" and fs == ["CFS"]:
            body = json.loads(statement(SAMSUNG, year, code, "CFS"))
            for line in body["list"]:
                line["account_id"] = "x_unknown"
            empty_cfs[(kind, c, year, code, "CFS")] = json.dumps(body).encode()
    _financial_run(tmp_path, "h2", empty_cfs, "2026-09-20T00:00:00+00:00",
                   from_date="2025-10-01", to_date="2026-09-20", now=datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc))
    rows = conn.execute("SELECT period, fs_basis, eps::text, version_status FROM financial_quarters_as_of(%s, %s)",
                        (datetime(2026, 9, 21, tzinfo=KST), "005930")).fetchall()
    assert rows and {basis for _, basis, *_ in rows} == {"CFS"}                # OFS 값으로 갈아타지 않는다
    assert all(eps is None and status == "CONFIRMED" for _, _, eps, status in rows)

    # 확정이 한 번도 없는 보고서: 다른 회사(하이닉스)의 반기 CFS 만 HTTP 파손, OFS 는 013 → CFS UNCONFIRMED·OFS 빈 확정
    from source_observation_fakes import HYNIX, DartFake, filing_list, write_holdings
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import source_observations as so
    dart = DartFake([HYNIX], {("statement", HYNIX["corp_code"], "2026", "11012", "CFS"): b"<html>502</html>"},
                    {HYNIX["corp_code"]: filing_list(HYNIX)})
    dart.fetched_at = "2026-09-22T00:00:00+00:00"
    storage = LocalStorage(tmp_path / "hx")
    write_holdings(storage, "2026-08-14", ["000660"])
    so.collect_financial(storage, dart, f"{RUN}hx_raw", etf_ids=["091160"], from_date="2026-08-01", to_date="2026-09-22",
                         now=datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc))
    so.normalize(storage, so.FINANCIAL, f"{RUN}hx_norm", f"{RUN}hx_raw", producer="normalize_financial_metric")
    assert so.load(storage, so.FINANCIAL, _db(), f"{RUN}hx_load", input_run_id=f"{RUN}hx_norm", pending=False,
                   producer="load_financial_metric") == 0
    hynix = conn.execute("SELECT period, fs_basis, eps::text, version_status, latest_unconfirmed_at FROM"
                         " financial_quarters_as_of(%s, %s)", (datetime(2026, 9, 23, tzinfo=KST), "000660")).fetchall()
    # 연결 지표 이력이 없으니 기준은 OFS — OFS 는 013(빈 확정), CFS 시도 실패는 기준 밖이라 행이 없다
    assert [(p, b, e, s) for p, b, e, s, _ in hynix] == [("2026-Q2", "OFS", None, "CONFIRMED")]
