"""Stored source observations reach the v2 tools through the point-in-time functions (ALPHA-1130).

Runs against a local PostgreSQL with the cloud Flyway set applied (V2_SOURCE_TEST_DSN, e.g.
postgresql://edge:edge@127.0.0.1:55445/edge — the guard accepts only this port and database). Rows are tagged with a unique raw_run_id and
deleted afterwards; the reads run as ``edge_analysis_v2_writer`` so the test also proves the
role's read path is the functions, not the tables.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.tools.fixture_data import macro, valuation
from edge_analysis_v2.storage.source_inputs import financial_inputs, macro_inputs

KST = timezone(timedelta(hours=9))
SHA = "0" * 64
ANALYSIS_AT = datetime(2026, 9, 29, 10, 0, tzinfo=KST)


def _dsn():
    dsn = os.environ["V2_SOURCE_TEST_DSN"]
    config = conninfo_to_dict(dsn)
    if (config.get("host") not in ("localhost", "127.0.0.1") or config.get("port") != "55445"
            or config.get("dbname") != "edge" or config.get("hostaddr") not in (None, "127.0.0.1")):
        raise ValueError("Integration tests require the local edge test database on port 55445")
    return dsn


@pytest.fixture
def db():
    """Insert one tagged set of version rows and remove exactly those rows afterwards."""
    run = "v2-source-test-" + uuid4().hex
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        try:
            _seed(conn, run)  # inside the cleanup scope: a seed that fails half-way must not leave tagged rows behind
            yield conn
        finally:
            conn.execute("RESET ROLE")
            conn.execute("DELETE FROM macro_observation WHERE raw_run_id = %s", (run,))
            conn.execute("DELETE FROM financial_metric WHERE raw_run_id LIKE %s", (run + "%",))
            conn.execute("DELETE FROM financial_report_version WHERE raw_run_id LIKE %s", (run + "%",))


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
            " 'ECOS 731Y003/D/0000003', %(received)s, %(received)s, 'received', %(raw_run_id)s, %(raw_key)s,"
            " %(raw_sha256)s, %(canonical_run_id)s, %(artifact_key)s, %(artifact_sha256)s)",
            prov | dict(day=day, value=value, received=received))

    REPRT = {"Q1": "11013", "Q2": "11012", "Q3": "11014", "Q4": "11011"}

    def version(corp, code, year, period, rcept_no, rcept_date, metrics):
        """The confirmed report version the query treats as authoritative (loaded with the metric rows)."""
        received = datetime.fromisoformat(rcept_date).replace(hour=9, tzinfo=KST) + timedelta(days=3)
        available = min(received, datetime.fromisoformat(rcept_date).replace(tzinfo=KST) + timedelta(days=1))
        conn.execute(
            "INSERT INTO financial_report_version (corp_code, instrument_code, fiscal_year, reprt_code, report_period,"
            " fs_basis, status, rcept_no, rcept_date, metrics, rejected, detail, received_at, available_at,"
            " availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256)"
            " VALUES (%(corp)s, %(code)s, %(year)s, %(reprt)s, %(period)s, 'CFS', 'CONFIRMED', %(rcept_no)s,"
            " %(rcept_date)s, %(metrics)s::jsonb, '[]'::jsonb, '{\"statement\": \"ok\", \"shares\": \"ok\"}'::jsonb,"
            " %(received)s, %(available)s, 'provider_release_date', %(raw_run_id)s, %(raw_key)s, %(raw_sha256)s,"
            " %(canonical_run_id)s, %(artifact_key)s, %(artifact_sha256)s) ON CONFLICT DO NOTHING",
            prov | dict(corp=corp, code=code, year=year, reprt=REPRT[period], period=period, rcept_no=rcept_no,
                        rcept_date=rcept_date, metrics=json.dumps(metrics), received=received, available=available))

    def metric(corp, code, year, period, end, name, kind, derivation, value, unit, rcept_no, rcept_date, formula=None,
               inputs="[]"):
        version(corp, code, year, period, rcept_no, rcept_date, [f"{name}/{kind}/{period}"])
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
           inputs='[{"se": "합계", "preferred_istc_totqy": "802371203", "common_bps": "bps_blocked_preferred_shares"}]')   # the pipeline's evidence line shape


def _later_version(db, period, suffix, days, metrics, shares="ok", rcept_no="20260930000009", rcept_date="2026-09-30"):
    """A newer confirmed version of TST001's report, received `days` after the seed version, run id = seed || suffix."""
    db.execute("""INSERT INTO financial_report_version
        SELECT corp_code, instrument_code, fiscal_year, reprt_code, report_period, fs_basis, 'CONFIRMED', %s, %s::date,
               %s::jsonb, '[]'::jsonb, jsonb_build_object('statement', 'ok', 'shares', %s::text),
               received_at + make_interval(days => %s), received_at + make_interval(days => %s), 'received',
               raw_run_id || %s, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256, now()
        FROM financial_report_version
        WHERE instrument_code = 'TST001' AND report_period = %s AND raw_run_id NOT LIKE '%%-v%%'""",
               (rcept_no, rcept_date, json.dumps(metrics), shares, days, days, suffix, period))


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
    assert [(g["instrument_id"], g["period"], g["missing"], g["reasons"], g.get("bps_total_shares")) for g in gaps] == [
        ("TST002", "2026-Q2", ["bps"], {"bps": "PREFERRED_SHARES_PRESENT"}, "62000"),
        ("TST999", None, ["all"], {"all": "no_release_visible"}, None)]
    assert gaps[0]["version"]["shares_status"] == "ok" and gaps[0]["version"]["latest_unconfirmed_at"] is None
    fixture = {"context": {"etf_code": "T", "analysis_at": ANALYSIS_AT.isoformat()},
               "holdings": [{"instrument_id": "TST001", "weight": "1", "as_of_date": "2026-09-26", "available_at": "2026-09-26T18:00:00+09:00"}],
               "prices": [{"instrument_id": "TST001", "date": "2026-09-26", "close": "68000", "available_at": "2026-09-26T16:00:00+09:00"}],
               "financials": rows}
    result = valuation.calculate(fixture, "TST001")
    assert result["ttm_eps"] == 3400 and result["per"] == 20 and result["pbr"] == 1.36
    assert result["periods"] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
    fixture["holdings"][0]["instrument_id"] = "TST002"
    fixture["prices"][0]["instrument_id"] = "TST002"
    with pytest.raises(ValueError):   # one released quarter and no usable book value: neither ratio can be computed
        valuation.calculate(fixture, "TST002")


def test_incomplete_latest_quarter_blocks_instead_of_sliding_to_older_quarters(db):
    # Five visible quarters where the newest has EPS but no BPS at all (share table unreadable).
    db.execute("RESET ROLE")
    run = db.execute("SELECT raw_run_id FROM financial_metric WHERE instrument_code = 'TST001' LIMIT 1").fetchone()[0]
    db.execute("""INSERT INTO financial_report_version
        SELECT corp_code, instrument_code, 2026, '11014', 'Q3', fs_basis, 'CONFIRMED', '20261114000001', '2026-11-14',
               '["eps_basic/QUARTER/Q3"]'::jsonb, '[{"metric": "bps", "reasons": ["bps_share_rows_unreadable"]}]'::jsonb,
               jsonb_build_object('statement', 'ok', 'shares', 'ok'), '2026-11-15 00:00+09', '2026-11-15 00:00+09',
               'received', raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256, now()
        FROM financial_report_version WHERE raw_run_id = %s AND report_period = 'Q2'""", (run,))
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
    gap = gaps[0]
    assert (gap["period"], gap["missing"], gap["reasons"]) == ("2026-Q3", ["bps"], {"bps": "BPS_ABSENT_IN_LATEST_VERSION"})
    assert gap["rejected"] == [{"metric": "bps", "reasons": ["bps_share_rows_unreadable"]}]   # the pipeline's own reason
    assert gap["version"]["raw_run_id"] == run and gap["version"]["latest_unconfirmed_at"] is None
    fixture = {"context": {"etf_code": "T", "analysis_at": "2026-11-20T10:00:00+09:00"},
               "holdings": [{"instrument_id": "TST001", "weight": "1", "as_of_date": "2026-11-19", "available_at": "2026-11-19T18:00:00+09:00"}],
               "prices": [{"instrument_id": "TST001", "date": "2026-11-19", "close": "68000", "available_at": "2026-11-19T16:00:00+09:00"}],
               "financials": rows}
    # The latest quarter has EPS but no BPS: PBR is withheld rather than taken from an older quarter,
    # and PER still covers the latest four quarters.
    result = valuation.calculate(fixture, "TST001")
    assert result["pbr"] is None and "MISSING_BPS" in result["unavailable"]["pbr"]
    assert result["per"] is not None and result["periods"][-1] == "2026-Q3"


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
    from edge_analysis_v2.storage.source_inputs import freshness
    rows = {(r["dataset"], r["series"]): r for r in freshness(db)}
    usd = rows[("macro_observation", "usd_krw")]
    assert usd["latest_observation_date"] >= "2026-09-26"  # the row received after the analysis instant still counts here
    assert usd["freshness_status"] == "UNKNOWN" and usd["freshness_reason"] == "NO_PROVIDER_CALENDAR"
    assert rows[("financial_metric", None)]["latest_observation_date"] >= "2026-06-30"
    assert ("macro_observation", "brent_spot_usd") not in rows  # never loaded = absent, not "stale"


def test_a_later_version_that_blocks_bps_is_not_overridden_by_an_older_bps(db):
    # A correction confirms preferred shares: the new run stores only bps_total_shares. Version choice is
    # per metric, so the old run's bps would otherwise survive and hide the block.
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_metric (corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric,
        period_kind, fs_basis, derivation, value, unit, formula, inputs, rcept_no, rcept_date, received_at, available_at,
        availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256)
        SELECT corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric, period_kind, fs_basis,
        derivation, CASE metric WHEN 'bps_total_shares' THEN 48000 ELSE value END, unit, formula,
        CASE metric WHEN 'bps_total_shares' THEN '[{"se": "합계", "preferred_istc_totqy": "100", "common_bps": "bps_blocked_preferred_shares"}]'::jsonb ELSE inputs END,
        '20260930000001', '2026-09-30', received_at + interval '30 days', received_at + interval '30 days',
        'received', raw_run_id || '-v2', raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256
        FROM financial_metric WHERE instrument_code = 'TST001' AND metric IN ('eps_basic', 'bps_total_shares')
          AND fiscal_period = 'Q2'""")   # the correction run re-extracts the report: EPS again, total-shares BPS, no common bps
    _later_version(db, "Q2", "-v2", 30, ["eps_basic/QUARTER/Q2", "bps_total_shares/POINT/Q2"])
    run_v2 = db.execute("SELECT raw_run_id FROM financial_metric WHERE raw_run_id LIKE %s", ("v2-source-test-%-v2",)).fetchone()[0]
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 11, 20, tzinfo=KST), ["TST001"])
        latest = rows[-1]
        assert latest["period"] == "2026-Q2" and latest["bps"] is None
        assert (gaps[0]["period"], gaps[0]["reasons"], gaps[0]["bps_total_shares"]) == (
            "2026-Q2", {"bps": "PREFERRED_SHARES_PRESENT"}, "48000")
        assert rows[-1]["version"]["raw_run_id"] == run_v2
        # Before the correction was received, the old version (with bps) is still what was known.
        rows, gaps = financial_inputs(db, datetime(2026, 9, 10, 10, 0, tzinfo=KST), ["TST001"])  # v2 received 09-16
        assert rows[-1]["bps"] == "50000" and gaps == []
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_metric WHERE raw_run_id = %s", (run_v2,))
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id = %s", (run_v2,))


def test_a_later_version_without_any_bps_does_not_inherit_the_older_pair(db):
    # Correction run B re-extracts the quarter: EPS fine, both BPS rejected (share rows inconsistent).
    # The quarter must show B's EPS with BPS absent — not A's BPS next to B's EPS.
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_metric (corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric,
        period_kind, fs_basis, derivation, value, unit, formula, inputs, rcept_no, rcept_date, received_at, available_at,
        availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256)
        SELECT corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric, period_kind, fs_basis,
        derivation, 1010, unit, formula, inputs, '20260930000001', '2026-09-30', received_at + interval '30 days',
        received_at + interval '30 days', 'received', raw_run_id || '-v3', raw_key, raw_sha256, canonical_run_id,
        artifact_key, artifact_sha256
        FROM financial_metric WHERE instrument_code = 'TST001' AND metric = 'eps_basic' AND fiscal_period = 'Q2'""")
    _later_version(db, "Q2", "-v3", 30, ["eps_basic/QUARTER/Q2"])
    run_v3 = db.execute("SELECT raw_run_id FROM financial_metric WHERE raw_run_id LIKE %s", ("v2-source-test-%-v3",)).fetchone()[0]
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 11, 20, tzinfo=KST), ["TST001"])
        assert rows[-1]["period"] == "2026-Q2" and rows[-1]["eps"] == "1010" and rows[-1]["bps"] is None
        assert rows[-1]["evidence"]["raw_run_ids"] == [run_v3]
        assert (gaps[0]["period"], gaps[0]["reasons"]) == ("2026-Q2", {"bps": "BPS_ABSENT_IN_LATEST_VERSION"})
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_metric WHERE raw_run_id = %s", (run_v3,))
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id = %s", (run_v3,))


def test_a_newer_annual_report_run_without_q4_rows_invalidates_the_old_q4(db):
    # Q4 rows are derived from the annual (FY) report run. If a later FY run could not derive Q4
    # (9M input missing, shares broken) it stores FY cumulative rows only — the old Q4 must not survive.
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_metric (corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric,
        period_kind, fs_basis, derivation, value, unit, formula, inputs, rcept_no, rcept_date, received_at, available_at,
        availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256)
        SELECT corp_code, instrument_code, 2025, 'FY', '2025-12-31', 'eps_basic', 'CUMULATIVE', fs_basis, 'REPORTED',
        3300, unit, NULL, inputs, '20260930000002', '2026-09-30', received_at + interval '200 days',
        received_at + interval '200 days', 'received', raw_run_id || '-v4', raw_key, raw_sha256, canonical_run_id,
        artifact_key, artifact_sha256
        FROM financial_metric WHERE instrument_code = 'TST001' AND metric = 'eps_basic' AND fiscal_period = 'Q4'""")
    _later_version(db, "Q4", "-v4", 200, ["eps_basic/CUMULATIVE/FY"])
    run_v4 = db.execute("SELECT raw_run_id FROM financial_metric WHERE raw_run_id LIKE %s", ("v2-source-test-%-v4",)).fetchone()[0]
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 12, 20, tzinfo=KST), ["TST001"])
        q4 = next(r for r in rows if r["period"] == "2025-Q4")   # the quarter stays as a hole, evidenced by the FY run
        assert q4["eps"] is None and q4["bps"] is None and q4["evidence"]["raw_run_ids"] == [run_v4]
        gap = next(g for g in gaps if g["period"] == "2025-Q4")
        assert gap["missing"] == ["bps", "eps"] and gap["version"]["raw_run_id"] == run_v4
        assert gap["reasons"] == {"eps": "EPS_ABSENT_IN_LATEST_VERSION", "bps": "BPS_ABSENT_IN_LATEST_VERSION"}
        rows, _ = financial_inputs(db, datetime(2026, 9, 10, tzinfo=KST), ["TST001"])
        assert [r["period"] for r in rows] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_metric WHERE raw_run_id = %s", (run_v4,))
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id = %s", (run_v4,))


def test_a_newer_quarter_run_with_only_cumulative_rows_keeps_the_quarter_as_a_hole(db):
    # Q3 re-extraction stores only the 9M cumulative EPS (3-month field missing, shares broken).
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_metric (corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric,
        period_kind, fs_basis, derivation, value, unit, formula, inputs, rcept_no, rcept_date, received_at, available_at,
        availability_basis, raw_run_id, raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256)
        SELECT corp_code, instrument_code, 2026, 'Q2', '2026-06-30', 'eps_basic', 'CUMULATIVE', fs_basis, 'REPORTED',
        1900, unit, NULL, inputs, '20260930000003', '2026-09-30', received_at + interval '200 days',
        received_at + interval '200 days', 'received', raw_run_id || '-v5', raw_key, raw_sha256, canonical_run_id,
        artifact_key, artifact_sha256
        FROM financial_metric WHERE instrument_code = 'TST001' AND metric = 'eps_basic' AND fiscal_period = 'Q2'""")
    _later_version(db, "Q2", "-v5", 200, ["eps_basic/CUMULATIVE/Q2"])
    run_v5 = db.execute("SELECT raw_run_id FROM financial_metric WHERE raw_run_id LIKE %s", ("v2-source-test-%-v5",)).fetchone()[0]
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2027, 4, 1, tzinfo=KST), ["TST001"])
        q2 = next(r for r in rows if r["period"] == "2026-Q2")
        assert q2["eps"] is None and q2["bps"] is None and q2["evidence"]["raw_run_ids"] == [run_v5]
        assert any(g["period"] == "2026-Q2" and g["missing"] == ["bps", "eps"] for g in gaps)
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_metric WHERE raw_run_id = %s", (run_v5,))
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id = %s", (run_v5,))


def test_a_version_with_zero_metrics_replaces_older_values_but_an_unconfirmed_check_does_not(db):
    # The defect this closes: a correction run whose report parsed but yielded no usable metric used to leave
    # no row, so the older values looked current. Now the confirmed-empty version is the row (all NULL) and a
    # later *unconfirmed* check (HTTP error) leaves the confirmed version in place but is exposed.
    db.execute("RESET ROLE")
    _later_version(db, "Q2", "-v6", 40, [])                       # confirmed, nothing usable
    db.execute("""INSERT INTO financial_report_version
        SELECT corp_code, instrument_code, fiscal_year, reprt_code, report_period, fs_basis, 'UNCONFIRMED', NULL, NULL,
               '[]'::jsonb, '[]'::jsonb, jsonb_build_object('statement', 'error', 'statement_detail', 'http_500'),
               received_at + make_interval(days => 50), received_at + make_interval(days => 50), 'received',
               raw_run_id || '-v7', raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256, now()
        FROM financial_report_version WHERE instrument_code = 'TST001' AND report_period = 'Q2' AND raw_run_id NOT LIKE '%-v%'""")
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 12, 1, tzinfo=KST), ["TST001"])
        q2 = next(r for r in rows if r["period"] == "2026-Q2")
        assert q2["eps"] is None and q2["bps"] is None and q2["version"]["raw_run_id"].endswith("-v6")
        assert q2["version"]["latest_unconfirmed_at"] is not None            # the -v7 failure is visible, not applied
        assert next(g for g in gaps if g["period"] == "2026-Q2")["reasons"] == {
            "eps": "EPS_ABSENT_IN_LATEST_VERSION", "bps": "BPS_ABSENT_IN_LATEST_VERSION"}
        # before -v6 was received, the original version is still what was known — and no failure yet
        rows, _ = financial_inputs(db, datetime(2026, 9, 10, tzinfo=KST), ["TST001"])
        assert rows[-1]["bps"] == "50000" and rows[-1]["version"]["latest_unconfirmed_at"] is None
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id LIKE '%-v6' OR raw_run_id LIKE '%-v7'")


def test_share_count_failure_is_unconfirmed_not_a_confirmed_absence(db):
    db.execute("RESET ROLE")
    _later_version(db, "Q2", "-v8", 45, ["eps_basic/QUARTER/Q2"], shares="error")
    db.execute("""INSERT INTO financial_metric
        SELECT corp_code, instrument_code, fiscal_year, fiscal_period, period_end, metric, period_kind, fs_basis,
               derivation, value, unit, formula, inputs, rcept_no, rcept_date, received_at + interval '45 days',
               received_at + interval '45 days', 'received', raw_run_id || '-v8', raw_key, raw_sha256, canonical_run_id,
               artifact_key, artifact_sha256, now()
        FROM financial_metric WHERE instrument_code = 'TST001' AND metric = 'eps_basic' AND fiscal_period = 'Q2'""")
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 12, 1, tzinfo=KST), ["TST001"])
        q2 = next(g for g in gaps if g["period"] == "2026-Q2")
        assert q2["reasons"] == {"bps": "BPS_UNCONFIRMED"} and q2["version"]["shares_status"] == "error"
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_metric WHERE raw_run_id LIKE '%-v8'")
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id LIKE '%-v8'")


def test_a_report_that_was_only_ever_attempted_shows_as_unconfirmed(db):
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_report_version
        SELECT corp_code, instrument_code, 2026, '11014', 'Q3', fs_basis, 'UNCONFIRMED', NULL, NULL, '[]'::jsonb, '[]'::jsonb,
               jsonb_build_object('statement', 'error', 'statement_detail', 'http_502'), '2026-11-20 00:00+09',
               '2026-11-20 00:00+09', 'received', raw_run_id || '-v9', raw_key, raw_sha256, canonical_run_id, artifact_key,
               artifact_sha256, now()
        FROM financial_report_version WHERE instrument_code = 'TST001' AND report_period = 'Q2' AND raw_run_id NOT LIKE '%-v%'""")
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 11, 21, tzinfo=KST), ["TST001"])
        q3 = next(r for r in rows if r["period"] == "2026-Q3")
        assert q3["eps"] is None and q3["version"]["status"] == "UNCONFIRMED" and q3["version"]["raw_run_id"].endswith("-v9")
        assert next(g for g in gaps if g["period"] == "2026-Q3")["reasons"] == {"eps": "REPORT_UNCONFIRMED", "bps": "REPORT_UNCONFIRMED"}
        fixture = {"context": {"etf_code": "T", "analysis_at": "2026-11-21T10:00:00+09:00"},
                   "holdings": [{"instrument_id": "TST001", "weight": "1", "as_of_date": "2026-11-20", "available_at": "2026-11-20T18:00:00+09:00"}],
                   "prices": [{"instrument_id": "TST001", "date": "2026-11-20", "close": "68000", "available_at": "2026-11-20T16:00:00+09:00"}],
                   "financials": rows}
        with pytest.raises(ValueError):          # the failed attempt blocks the ratio; no slide to the older four quarters
            valuation.calculate(fixture, "TST001")
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id LIKE '%-v9'")


def test_an_attempt_in_another_basis_still_shows_when_the_company_basis_has_none(db):
    # First CFS request stopped by a provider limit (UNCONFIRMED CFS, no OFS request at all): the company's basis
    # defaults to OFS, but the only attempt must still be visible rather than silently absent.
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_report_version
        SELECT '00000003', 'TST003', 2026, '11012', 'Q2', 'CFS', 'UNCONFIRMED', NULL, NULL, '[]'::jsonb, '[]'::jsonb,
               jsonb_build_object('statement', 'error', 'statement_detail', 'dart_020'), '2026-08-20 00:00+09',
               '2026-08-20 00:00+09', 'received', raw_run_id || '-v10', raw_key, raw_sha256, canonical_run_id, artifact_key,
               artifact_sha256, now()
        FROM financial_report_version WHERE instrument_code = 'TST001' AND report_period = 'Q2' AND raw_run_id NOT LIKE '%-v%'""")
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        rows, gaps = financial_inputs(db, datetime(2026, 8, 21, tzinfo=KST), ["TST003"])
        assert [(r["period"], r["fs_basis"], r["version"]["status"]) for r in rows] == [("2026-Q2", "CFS", "UNCONFIRMED")]
        assert gaps[0]["reasons"] == {"eps": "REPORT_UNCONFIRMED", "bps": "REPORT_UNCONFIRMED"}
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id LIKE '%-v10'")


def test_freshness_counts_a_metric_free_financial_load(db):
    # A run whose only outcome is confirmed versions with no metrics is still a load — it must not vanish from freshness.
    db.execute("RESET ROLE")
    db.execute("""INSERT INTO financial_report_version
        SELECT '00000004', 'TST004', 2026, '11012', 'Q2', 'CFS', 'CONFIRMED', '20260814000004', '2026-08-14', '[]'::jsonb,
               '[{"metric": "eps_basic", "reasons": ["account_not_found"]}]'::jsonb,
               jsonb_build_object('statement', 'ok', 'shares', 'ok'), '2027-01-05 09:00+09', '2027-01-05 09:00+09',
               'received', raw_run_id || '-v11', raw_key, raw_sha256, canonical_run_id, artifact_key, artifact_sha256, now()
        FROM financial_report_version WHERE instrument_code = 'TST001' AND report_period = 'Q2' AND raw_run_id NOT LIKE '%-v%'""")
    try:
        db.execute("SET ROLE edge_analysis_v2_writer")
        from edge_analysis_v2.storage.source_inputs import freshness
        fin = next(r for r in freshness(db) if r["dataset"] == "financial_metric")
        assert fin["last_received_at"].startswith("2027-01-05")      # the metric-free run is the latest receipt
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM financial_report_version WHERE raw_run_id LIKE '%-v11'")


def test_freshness_keeps_a_row_for_a_fulfilled_but_empty_load(db):
    # A LOAD_* that fulfilled with zero rows (non-trading day, empty vendor answer) must stay distinguishable from
    # "never loaded": the row exists with no observation columns and the ledger's data_status.
    db.execute("RESET ROLE")
    run = "v2-source-test-ops-" + uuid4().hex
    try:   # cleanup scope starts before the first insert — a failing second insert must not leave the run behind
        db.execute("INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type, execution_name, orchestrator)"
                   " VALUES (%s, %s, 'source-daily', %s, 'AIRFLOW')", (run, run, run))
        db.execute("INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage, dataset, task_outcome,"
                   " data_status, fulfilled_at, idempotency_key) VALUES (%s, %s, 'LOAD_SECTOR', 'feature',"
                   " 'sector_classification_load', 'FULFILLED', 'VALID_EMPTY', '2027-02-01 09:30+09', %s)", (run + "-t", run, run + "-t"))
        assert db.execute("SELECT count(*) FROM sector_classification").fetchone()[0] == 0   # this DB has no sector rows
        db.execute("SET ROLE edge_analysis_v2_writer")
        from edge_analysis_v2.storage.source_inputs import freshness
        sector = next(r for r in freshness(db) if r["dataset"] == "sector_classification")
        assert sector["latest_observation_date"] is None and sector["last_load_data_status"] == "VALID_EMPTY"
        assert sector["last_load_fulfilled_at"].startswith("2027-02-01")
    finally:
        db.execute("RESET ROLE")
        db.execute("DELETE FROM ops_expected_task WHERE pipeline_run_id = %s", (run,))
        db.execute("DELETE FROM ops_pipeline_run WHERE pipeline_run_id = %s", (run,))


def test_dsn_guard_refuses_the_rds_tunnel_port(monkeypatch):
    # The documented EdgeV2-RdsWriterTunnel is also loopback (15433); host alone would let this test mutate RDS.
    monkeypatch.setenv("V2_SOURCE_TEST_DSN", "postgresql://edge:x@127.0.0.1:15433/edge")
    with pytest.raises(ValueError, match="55445"):
        _dsn()
