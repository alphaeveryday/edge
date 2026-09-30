"""Source observations from the shared database as fixture-shaped tool inputs (ALPHA-1130).

Reads only the point-in-time functions that are the storage contract
(docs/design/etf-data-storage-plan.md §10.4): ``macro_observations_as_of`` and
``financial_quarters_as_of``. The rows come back in the shapes the fixture tools already
consume (`tools/fixture_data`), so the same calculation tools run on stored data and on synthetic fixtures.

What is *not* done here: no timestamp is invented for date-only observations (``observed_at``
carries the observation day, which the tools read as "observed once that Korean day ended"),
missing values are reported as gaps rather than filled, and a company whose per-common-share
BPS is blocked (preferred shares) is surfaced as a gap with the database's note.
"""
from datetime import datetime, timezone

MACRO_SERIES = ("usd_krw", "kr_10y_yield", "us_10y_yield", "kr_cpi_yoy", "brent_spot_usd")


def _instant(analysis_at):
    if not isinstance(analysis_at, datetime) or analysis_at.utcoffset() is None:
        raise ValueError("analysis_at requires timezone")


def _iso(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


def macro_inputs(conn, analysis_at, series=MACRO_SERIES, limit=21):
    """Return ``macro`` rows visible at ``analysis_at`` and the series that have none.

    Args:
        conn: PostgreSQL connection with EXECUTE on the point-in-time functions.
        analysis_at: Analysis instant with an explicit offset.
        series: Registered series ids to read.
        limit: Latest observations per series (the tool exposes 21; cards need 2).

    Returns:
        ``(rows, gaps)``: fixture rows ``series,value,unit,observed_at,available_at,subject,
        reference_period,evidence`` (observed_at is the observation day), and gaps
        ``{series, reason}`` for series with nothing visible.
    """
    _instant(analysis_at)
    rows, gaps = [], []
    with conn.cursor() as cur:
        for series_id in series:
            cur.execute(
                "SELECT series_id, observation_date, reference_period, value, unit, available_at,"
                " raw_run_id, raw_key, canonical_run_id, artifact_key"
                " FROM macro_observations_as_of(%s, %s, %s) ORDER BY observation_date",
                (analysis_at, series_id, limit))
            found = cur.fetchall()
            if not found:
                gaps.append({"series": series_id, "reason": "no_observation_visible"})
            for sid, day, period, value, unit, available_at, run_id, raw_key, canonical_run_id, artifact_key in found:
                rows.append({"series": sid, "value": str(value), "unit": unit, "observed_at": day.isoformat(),
                             "available_at": _iso(available_at), "subject": sid, "reference_period": period,
                             "evidence": {"raw_run_id": run_id, "raw_key": raw_key,
                                          "canonical_run_id": canonical_run_id, "artifact_key": artifact_key}})
    return rows, gaps


def financial_inputs(conn, analysis_at, instrument_ids):
    """Return ``financials`` rows (quarterly EPS + quarter-end BPS) visible at ``analysis_at``.

    A row is a **confirmed report version** (the latest one the pipeline confirmed, by receipt
    order); a missing EPS or BPS stays ``None`` in the row **and** is listed in ``gaps`` with the
    reason (``bps_note`` PREFERRED_SHARES_PRESENT = the per-common-share BPS is deliberately
    blocked, a team decision; COMMON_SHARE_BPS_UNAVAILABLE / BPS_ABSENT_IN_LATEST_VERSION /
    EPS_ABSENT_IN_LATEST_VERSION = the confirmed version lacks the value; BPS_UNCONFIRMED = the
    share-count response failed, so absence is not confirmed). ``version`` carries which run the
    values come from and ``latest_unconfirmed_at`` when a later check failed — the caller sees it
    is reading an older confirmed version, and nothing is filled from older versions. A report that
    was never confirmed but was attempted appears as an all-``None`` row with
    ``version.status == "UNCONFIRMED"`` (gap reason REPORT_UNCONFIRMED). Dropping an incomplete latest quarter would let the
    valuation tool slide to the previous four quarters and report a stale ratio as current, so
    the hole is kept in place and ``valuation.calculate`` fails on it instead.
    Derived Q4 EPS (``FY_MINUS_9M``) is passed through with its derivation so the caller can
    decide whether an approximation is acceptable.
    """
    _instant(analysis_at)
    rows, gaps = [], []
    with conn.cursor() as cur:
        for instrument_id in instrument_ids:
            cur.execute(
                "SELECT period, eps, eps_derivation, bps, bps_total_shares, bps_note, fs_basis, available_at,"
                " rcept_nos, raw_run_ids, version_raw_run_id, version_received_at, version_rejected, shares_status,"
                " latest_unconfirmed_at, version_status FROM financial_quarters_as_of(%s, %s) ORDER BY period",
                (analysis_at, instrument_id))
            found = cur.fetchall()
            if not found:
                gaps.append({"instrument_id": instrument_id, "period": None, "missing": ["all"],
                             "reasons": {"all": "no_release_visible"}})
            for (period, eps, eps_derivation, bps, bps_total, note, basis, available_at, rcept_nos, run_ids,
                 version_run, version_received, rejected, shares_status, unconfirmed_at, status) in found:
                version = {"status": status, "raw_run_id": version_run, "received_at": _iso(version_received),
                           "latest_unconfirmed_at": _iso(unconfirmed_at), "shares_status": shares_status}
                if status == "UNCONFIRMED":   # a report never confirmed: only failed attempts exist
                    reasons = {"eps": "REPORT_UNCONFIRMED", "bps": "REPORT_UNCONFIRMED"}
                else:
                    reasons = {name: reason for name, value, reason in (("eps", eps, "EPS_ABSENT_IN_LATEST_VERSION"),
                                                                         ("bps", bps, note)) if value is None}
                if reasons:   # one reason per missing metric — a policy block on BPS must survive an EPS gap
                    gaps.append({"instrument_id": instrument_id, "period": period, "missing": sorted(reasons),
                                 "reasons": reasons, "bps_total_shares": _num(bps_total),
                                 "rejected": list(rejected or []), "version": version})
                rows.append({"instrument_id": instrument_id, "period": period, "eps": _num(eps), "bps": _num(bps),
                             "available_at": _iso(available_at), "fs_basis": basis,
                             "eps_derivation": eps_derivation, "version": version,
                             "evidence": {"rcept_nos": list(rcept_nos or []), "raw_run_ids": list(run_ids or [])}})
    return rows, gaps


def _num(value):
    return None if value is None else str(value)


def freshness(conn):
    """Return per-dataset freshness facts; ``status`` is always UNKNOWN (no provider calendar).

    A successful API call or load is not evidence that the newest observation exists, so the
    row carries the last fulfilled load, the last received row and the latest observation date
    side by side. A dataset with a fulfilled load but no rows (a legitimately empty run) is a row with
    ``latest_observation_date`` None; a dataset that was never loaded is absent.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT dataset, series_id, latest_observation_date, last_received_at, last_load_fulfilled_at,"
                    " last_load_data_status, freshness_status, freshness_reason, basis FROM source_observation_freshness()")
        return [{"dataset": d, "series": s, "latest_observation_date": latest.isoformat() if latest else None,
                 "last_received_at": _iso(received),
                 "last_load_fulfilled_at": _iso(loaded), "last_load_data_status": status, "freshness_status": fresh,
                 "freshness_reason": reason, "basis": basis}
                for d, s, latest, received, loaded, status, fresh, reason, basis in cur.fetchall()]
