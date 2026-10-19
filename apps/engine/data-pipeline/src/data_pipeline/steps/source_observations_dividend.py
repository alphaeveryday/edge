"""Cash distribution observations using the shared immutable ingestion contract."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .source_observations import (
    Column, DatasetSpec, RawObject, _PROVENANCE, _collapse, ensure_same_request, existing_raw_manifest,
    record_already_collected, sha256, write_raw_run, write_rows,
)
from ..lake import raw_run_manifest_key
from ..sources.http import StopFetch


def exact_date(value: str, *, required=False) -> str | None:
    """Parse a complete calendar date, preserving an optional missing value."""
    if not value and not required:
        return None
    text = str(value).replace("/", "").replace("-", "")
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise ValueError("invalid_date")
    return datetime.strptime(text, "%Y%m%d").date().isoformat()


def _normalize(objects, manifest):
    rows = []
    rejects = [{"raw_key": entry.get("key"), "reasons": ["source_fetch_failed"],
                "detail": entry.get("detail")}
               for entry in manifest.get("objects", []) if entry.get("status") == "error"]
    rejects.extend({"raw_key": None, "reasons": ["missing_empty_response"]}
                   for entry in manifest.get("objects", [])
                   if entry.get("status") == "empty" and not entry.get("key"))
    for obj in objects:
        request = obj["request"]
        if request.get("superseded_by_split"):
            continue
        data = json.loads(obj["body"])
        if data.get("rt_cd") != "0" or not isinstance(data.get("output1"), list):
            rejects.append({"raw_key": obj["key"], "reasons": ["invalid_response"]})
            continue
        for item in data["output1"]:
            try:
                if not isinstance(item, dict):
                    raise ValueError("invalid_record_type")
                if item.get("sht_cd") != request["ticker"] or request["market_code"] != "XKRX":
                    raise ValueError("instrument_mismatch")
                record = exact_date(item.get("record_date"), required=True)
                if not request["from"] <= record <= request["to"]:
                    raise ValueError("outside_requested_range")
                pay = exact_date(item.get("divi_pay_dt"))
                text = item.get("per_sto_divi_amt")
                if text is not None and not isinstance(text, str):
                    raise ValueError("invalid_amount_type")
                amount = Decimal(text) if isinstance(text, str) and text.strip() else None
                if amount is not None and (not amount.is_finite() or amount < 0):
                    raise ValueError("invalid_amount")
                if pay and pay < record:
                    raise ValueError("payment_before_record")
                # Unannounced domestic schedules use zero and no payment date.
                if amount == 0 and not pay:
                    amount = None
                event_key = json.dumps([record, item.get("divi_kind", ""),
                                        item.get("stk_kind", "")], ensure_ascii=False, separators=(",", ":"))
                rows.append({"market_code": request["market_code"], "ticker": request["ticker"],
                    "source_vendor": "kis", "source_event_key": event_key,
                    "record_date": record, "ex_date": None, "pay_date": pay,
                    "amount_per_share": str(amount) if amount is not None else None,
                    "currency": "KRW", "distribution_kind": "cash",
                    "confirmation": "published" if pay and amount is not None else "pending",
                    "received_at": obj["fetched_at"], "available_at": obj["fetched_at"],
                    "availability_basis": "received", "raw_run_id": manifest["run_id"],
                    "raw_key": obj["key"], "raw_sha256": obj["sha256"]})
            except (ValueError, TypeError, InvalidOperation) as error:
                rejects.append({"raw_key": obj["key"], "ticker": request["ticker"],
                                "reasons": [str(error) if isinstance(error, ValueError) else type(error).__name__]})
    return rows, rejects


def _with_coverage(objects, manifest):
    rows, rejects = _normalize(objects, manifest)
    canonical_rows, conflicts, _ = _collapse(DIVIDEND, rows)
    observations_sha = sha256(write_rows(DIVIDEND, canonical_rows))
    bad_keys = {r.get("raw_key") for r in rejects}
    bad_tickers = {(r["key"]["market_code"], r["key"]["ticker"]) for r in conflicts}
    requested = manifest["request_scope"]["requested"]
    coverage = []
    for instrument in requested["instruments"]:
        entries = [e for e in manifest["objects"]
                   if all(e["request"].get(k) == instrument[k] for k in ("market_code", "ticker"))
                   and not e["request"].get("superseded_by_split")]
        # A complete root interval requires every disjoint leaf, including empty leaves.
        windows = sorted((e["request"]["from"], e["request"]["to"]) for e in entries)
        from datetime import timedelta
        cursor = date.fromisoformat(requested["from"])
        contiguous = bool(windows)
        for first, last in windows:
            contiguous &= date.fromisoformat(first) == cursor and first <= last
            cursor = date.fromisoformat(last) + timedelta(days=1)
        contiguous &= cursor == date.fromisoformat(requested["to"]) + timedelta(days=1)
        valid = [e for e in entries if e["status"] in ("ok", "empty") and e.get("key") not in bad_keys]
        complete = contiguous and len(valid) == len(entries) and (
            instrument["market_code"], instrument["ticker"]) not in bad_tickers
        has_rows = any(all(row[k] == instrument[k] for k in ("market_code", "ticker")) for row in rows)
        received = max((e["fetched_at"] for e in entries), default=manifest["started_at"])
        coverage.append({**instrument, "source_vendor": "kis",
            "coverage_from": requested["from"], "coverage_to": requested["to"],
            "status": "complete" if complete else "partial" if valid or has_rows else "failed",
            "observations_sha256": observations_sha,
            "received_at": received, "available_at": received, "availability_basis": "received",
            "raw_run_id": manifest["run_id"],
            "raw_key": raw_run_manifest_key(DIVIDEND.dataset, manifest["run_id"]),
            "raw_sha256": manifest["manifest_sha256"]})
    return rows, rejects, coverage


DIVIDEND_COVERAGE = DatasetSpec(
    dataset="cash_distribution_coverage", table="cash_distribution_coverage", collection_vendor="kis",
    columns=(Column("market_code", "str"), Column("ticker", "str"), Column("source_vendor", "str"),
        Column("coverage_from", "date"), Column("coverage_to", "date"), Column("status", "str"),
        Column("observations_sha256", "str"), *_PROVENANCE),
    key=("market_code", "ticker", "source_vendor"),
    partition=lambda row: "", normalize=lambda objects, manifest: ([], []),
)


DIVIDEND = DatasetSpec(
    dataset="cash_distribution", table="cash_distribution_observation", collection_vendor="kis",
    columns=(Column("market_code", "str"), Column("ticker", "str"), Column("source_vendor", "str"),
        Column("source_event_key", "str"), Column("record_date", "date"),
        Column("ex_date", "date"), Column("pay_date", "date"),
        Column("amount_per_share", "str"), Column("currency", "str"),
        Column("distribution_kind", "str"), Column("confirmation", "str"), *_PROVENANCE),
    key=("market_code", "ticker", "source_vendor", "source_event_key"),
    partition=lambda row: f"canonical/market_data/cash_distribution/record_date={row['record_date']}",
    normalize=_with_coverage, companion=DIVIDEND_COVERAGE,
)


def collect_dividends(storage, source, run_id, *, instruments, start: date, end: date, deadline_sec=None):
    """Write a replay-checked raw run for the requested instruments and dates."""
    if not instruments or start > end or any(
        row.get("market_code") != "XKRX" or not isinstance(row.get("ticker"), str)
        or not re.fullmatch(r"[0-9A-Z]{6}", row["ticker"]) for row in instruments
    ):
        raise ValueError("Dividend scope must contain domestic instruments and a valid date range")
    scope = {"instruments": sorted(instruments, key=lambda row: (row["market_code"], row["ticker"])),
             "from": start.isoformat(), "to": end.isoformat()}
    producer = "ingest_raw_dividend"
    done = existing_raw_manifest(storage, DIVIDEND.dataset, run_id)
    if done is not None:
        ensure_same_request(DIVIDEND.dataset, run_id, done, scope)
        return record_already_collected(storage, DIVIDEND, run_id, producer, done)
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + deadline_sec if deadline_sec is not None else None
    objects, stopped = [], False
    for instrument in scope["instruments"]:
        if deadline is not None and time.monotonic() >= deadline:
            stopped = True
        if not stopped:
            try:
                objects.extend(source.fetch(instrument, start, end, **({"deadline": deadline} if deadline is not None else {})))
                continue
            except StopFetch as error:
                partial = getattr(error, "objects", [])
                objects.extend(partial)
                stopped = True
                if partial:
                    continue
        # Preserve completed instruments and make every unattempted symbol visible.
        objects.append(RawObject("kis", "market", "KR", instrument["ticker"], "json", None,
            {**instrument, "from": start.isoformat(), "to": end.isoformat()},
            "error", "collection_deadline" if deadline is not None and time.monotonic() >= deadline else "source_stopped",
            datetime.now(timezone.utc).isoformat()))
    return write_raw_run(storage, DIVIDEND, run_id, producer=producer, objects=objects,
                         started_at=started, request_scope={"requested": scope})
