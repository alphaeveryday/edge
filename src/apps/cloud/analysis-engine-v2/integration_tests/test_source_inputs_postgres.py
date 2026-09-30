"""Stored source observations reach the v2 tools through the point-in-time functions (ALPHA-1130).

Runs against a local PostgreSQL with the cloud Flyway set applied (V2_SOURCE_TEST_DSN, e.g.
postgresql://edge:edge@127.0.0.1:55491/edge). Rows are tagged with a unique raw_run_id and
deleted afterwards; the reads run as ``edge_analysis_v2_writer`` so the test also proves the
role's read path is the functions, not the tables.
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.fixture_tools import macro, valuation
from edge_analysis_v2.source_inputs import financial_inputs, macro_inputs

KST = timezone(timedelta(hours=9))
SHA = "0" * 64
ANALYSIS_AT = datetime(2026, 9, 29, 10, 0, tzinfo=KST)


def _dsn():
    dsn = os.environ["V2_SOURCE_TEST_DSN"]
    if conninfo_to_dict(dsn).get("host") not in ("localhost", "127.0.0.1"):
        raise ValueError("Integration tests require a local database")
    return dsn


@pytest.fixture
def db():
    """Insert one tagged set of version rows and remove exactly those rows afterwards."""
    run = "v2-source-test-" + uuid4().hex
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        _seed(conn, run)
        try:
            yield conn
        finally:
            conn.execute("RESET ROLE")
            conn.execute("DELETE FROM macro_observation WHERE raw_run_id = %s", (run,))
            conn.execute("DELETE FROM financial_metric WHERE raw_run_id = %s", (run,))


def _seed(conn, run):
    prov = dict(raw_run_id=run, raw_key="raw/" + run, raw_sha256=SHA, canonical_run_id=run,
                artifact_key="canonical/" + run, artifact_sha256=SHA)
    macro_rows = [  # usd_krw daily closes; 09-26 was received (and so visible) only after the analysis instant
        ("2026-09-24", "1385.2", datetime(2026, 9, 25, 9, 12, tzinfo=KST)),
        ("2026-09-25", "1390.7", datetime(2026, 9, 26, 9, 12, tzinfo=KST)),
        ("2026-09-26", "1401.0", datetime(2026, 9, 29, 11, 0, tzinfo=KST)),
    ]
    for day, value, received in macro_rows:
        conn.execute(
            "INSERT INTO macro_observation (series_id, observation_date, value, unit, source_vendor, source_series,"
            " received_at, available_at, availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id,"
            " artifact_key, artifact_sha256) VALUES ('usd_krw', %(day)s, %(value)s, 'KRW_per_USD', 'ecos',"
            " '731Y003/D/0000003', %(received)s, %(received)s, 'received', %(raw_run_id)s, %(raw_key)s,"
            " %(raw_sha256)s, %(canonical_run_id)s, %(artifact_key)s, %(artifact_sha256)s)",
            prov | dict(day=day, value=value, received=received))

    def metric(corp, code, year, period, end, name, kind, derivation, value, unit, rcept_no, rcept_date, formula=None,
               inputs="[]"):
        received = datetime.fromisoformat(rcept_date).replace(hour=9, tzinfo=KST) + timedelta(days=3)
        available = min(received, datetime.fromisoformat(rcept_date).replace(tzinfo=KST) + timedelta(days=1))
        conn.execute(
            "INSERT INTO financial_metric (corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric,"
            " period_kind, fs_basis, derivation, value, unit, formula, inputs, rcept_no, rcept_date, received_at,"
            " available_at, availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key,"
            " artifact_sha256) VALUES (%(corp)s, %(code)s, %(year)s, %(period)s, %(end)s, %(name)s, %(kind)s, 'CFS',"
            " %(derivation)s, %(value)s, %(unit)s, %(formula)s, %(inputs)s::jsonb, %(rcept_no)s, %(rcept_date)s, %(received)s,"
            " %(available)s, 'provider_release_date', %(raw_run_id)s, %(raw_key)s, %(raw_sha256)s,"
            " %(canonical_run_id)s, %(artifact_key)s, %(artifact_sha256)s)",
            prov | dict(corp=corp, code=code, year=year, period=period, end=end, name=name, kind=kind,
                        derivation=derivation, value=value, unit=unit, formula=formula, rcept_no=rcept_no,
                        rcept_date=rcept_date, received=received, available=available, inputs=inputs))

    # 000660-like company without preferred shares: four consecutive quarters incl. derived Q4.
    quarters = [(2025, "Q3", "2025-09-30", "700", "20251114000001", "2025-11-14", "REPORTED"),
                (2025, "Q4", "2025-12-31", "800", "20260310000001", "2026-03-10", "FY_MINUS_9M"),
                (2026, "Q1", "2026-03-31", "900", "20260515000001", "2026-05-15", "REPORTED"),
                (2026, "Q2", "2026-06-30", "1000", "20260814000001", "2026-08-14", "REPORTED")]
    for year, period, end, eps, rcept, day, derivation in quarters:
        metric("00000001", "TST001", year, period, end, "eps_basic", "QUARTER", derivation, eps, "KRW_per_share",
               rcept, day, formula="FY-9M" if derivation == "FY_MINUS_9M" else None)
        for name in ("bps", "bps_total_shares"):
            metric("00000001", "TST001", year, period, end, name, "POINT", "EQUITY_OVER_SHARES", "50000", "KRW_per_share",
                   rcept, day, formula="equity/shares")
    # Company with preferred shares: common-share bps is blocked, only the total-shares figure exists.
    metric("00000002", "TST002", 2026, "Q2", "2026-06-30", "eps_basic", "QUARTER", "REPORTED", "1500", "KRW_per_share",
           "20260814000002", "2026-08-14")
    metric("00000002", "TST002", 2026, "Q2", "2026-06-30", "bps_total_shares", "POINT", "EQUITY_OVER_SHARES", "62000",
           "KRW_per_share", "20260814000002", "2026-08-14", formula="equity/(common+preferred)",
           inputs='[{"se": "합계", "preferred_istc_totqy": "802371203"}]')   # the pipeline's evidence line shape


def test_macro_rows_are_date_only_and_visibility_bound(db):
    db.execute("SET ROLE edge_analysis_v2_writer")
    rows, gaps = macro_inputs(db, ANALYSIS_AT, series=("usd_krw", "brent_spot_usd"))
    assert [r["observed_at"] for r in rows if r["series"] == "usd_krw"] == ["2026-09-24", "2026-09-25"]
    assert gaps == [{"series": "brent_spot_usd", "reason": "no_observation_visible"}]
    fixture = {"context": {"etf_code": "T", "analysis_at": ANALYSIS_AT.isoformat()}, "macro": rows}
    result = macro.compare(fixture, "usd_krw", "2026-09-24", "2026-09-25", "difference")
    assert result["change"] == 5.5 and result["change_unit"] == "KRW_per_USD"
    assert macro.metrics(fixture)[0] == {"key": "usd_krw", "value": 1390.7, "observed_at": "2026-09-25", "subject": "usd_krw"}


def test_financial_rows_feed_valuation_and_blocked_bps_is_a_gap(db):
    db.execute("SET ROLE edge_analysis_v2_writer")
    rows, gaps = financial_inputs(db, ANALYSIS_AT, ["TST001", "TST002", "TST999"])
    assert [(r["instrument_id"], r["period"], r["eps_derivation"], r["bps"]) for r in rows] == [
        ("TST001", "2025-Q3", "REPORTED", "50000"), ("TST001", "2025-Q4", "FY_MINUS_9M", "50000"),
        ("TST001", "2026-Q1", "REPORTED", "50000"), ("TST001", "2026-Q2", "REPORTED", "50000"),
        ("TST002", "2026-Q2", "REPORTED", None)]   # the blocked quarter stays in place as a hole
    assert gaps == [
        {"instrument_id": "TST002", "period": "2026-Q2", "missing": ["bps"], "reason": "PREFERRED_SHARES_PRESENT",
         "bps_total_shares": "62000"},
        {"instrument_id": "TST999", "period": None, "missing": ["all"], "reason": "no_release_visible"}]
    fixture = {"context": {"etf_code": "T", "analysis_at": ANALYSIS_AT.isoformat()},
               "holdings": [{"instrument_id": "TST001", "weight": "1", "as_of_date": "2026-09-26", "available_at": "2026-09-26T18:00:00+09:00"}],
               "prices": [{"instrument_id": "TST001", "date": "2026-09-26", "close": "68000", "available_at": "2026-09-26T16:00:00+09:00"}],
               "financials": rows}
    result = valuation.calculate(fixture, "TST001")
    assert result["ttm_eps"] == 3400 and result["per"] == 20 and result["pbr"] == 1.36
    assert result["periods"] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
    fixture["holdings"][0]["instrument_id"] = "TST002"
    fixture["prices"][0]["instrument_id"] = "TST002"
    with pytest.raises(ValueError):   # a hole in the latest quarter blocks the ratio; it never slides to older quarters
        valuation.calculate(fixture, "TST002")


def test_incomplete_latest_quarter_blocks_instead_of_sliding_to_older_quarters(db):
    # Five visible quarters where the newest has EPS but no BPS at all (share table unreadable).
    db.execute("RESET ROLE")
    run = db.execute("SELECT raw_run_id FROM financial_metric WHERE instrument_code = 'TST001' LIMIT 1").fetchone()[0]
    db.execute("""INSERT INTO financial_metric (corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric,
        period_kind, fs_basis, derivation, value, unit, inputs, rcept_no, rcept_date, received_at, available_at,
        availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256)
        SELECT corp_code, instrument_code, 2026, 'Q3', '2026-09-30', 'eps_basic', 'QUARTER', fs_basis, 'REPORTED', 1200,
        unit, inputs, '20261114000001', '2026-11-14', '2026-11-15 00:00+09', '2026-11-15 00:00+09',
        availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256
        FROM financial_metric WHERE instrument_code = 'TST001' AND metric = 'eps_basic' AND fiscal_period = 'Q2'""")
    db.execute("SET ROLE edge_analysis_v2_writer")
    rows, gaps = financial_inputs(db, datetime(2026, 11, 20, tzinfo=KST), ["TST001"])
    assert rows[-1]["period"] == "2026-Q3" and rows[-1]["bps"] is None
    assert gaps == [{"instrument_id": "TST001", "period": "2026-Q3", "missing": ["bps"], "reason": "not_released",
                     "bps_total_shares": None}]
    fixture = {"context": {"etf_code": "T", "analysis_at": "2026-11-20T10:00:00+09:00"},
               "holdings": [{"instrument_id": "TST001", "weight": "1", "as_of_date": "2026-11-19", "available_at": "2026-11-19T18:00:00+09:00"}],
               "prices": [{"instrument_id": "TST001", "date": "2026-11-19", "close": "68000", "available_at": "2026-11-19T16:00:00+09:00"}],
               "financials": rows}
    with pytest.raises(ValueError):
        valuation.calculate(fixture, "TST001")


def test_annual_report_is_invisible_before_the_day_after_receipt(db):
    db.execute("SET ROLE edge_analysis_v2_writer")
    rows, _ = financial_inputs(db, datetime(2026, 3, 10, 23, 59, tzinfo=KST), ["TST001"])
    assert [r["period"] for r in rows] == ["2025-Q3"]
    rows, _ = financial_inputs(db, datetime(2026, 3, 11, 0, 0, tzinfo=KST), ["TST001"])
    assert [r["period"] for r in rows] == ["2025-Q3", "2025-Q4"]


def test_writer_cannot_read_source_tables_directly(db):
    db.execute("SET ROLE edge_analysis_v2_writer")
    for table in ("macro_observation", "financial_metric", "sector_classification"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(f"SELECT 1 FROM {table} LIMIT 1")


def test_freshness_exposes_facts_without_judging(db):
    db.execute("SET ROLE edge_analysis_v2_writer")
    from edge_analysis_v2.source_inputs import freshness
    rows = {(r["dataset"], r["series"]): r for r in freshness(db)}
    usd = rows[("macro_observation", "usd_krw")]
    assert usd["latest_observation_date"] >= "2026-09-26"  # the row received after the analysis instant still counts here
    assert usd["freshness_status"] == "UNKNOWN" and usd["freshness_reason"] == "NO_PROVIDER_CALENDAR"
    assert rows[("financial_metric", None)]["latest_observation_date"] >= "2026-06-30"
    assert ("macro_observation", "brent_spot_usd") not in rows  # never loaded = absent, not "stale"
