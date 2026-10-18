"""Cash distribution observations using the shared immutable ingestion contract."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .source_observations import (
    Column, DatasetSpec, _PROVENANCE, ensure_same_request, existing_raw_manifest,
    record_already_collected, write_raw_run,
)


def exact_date(value: str, *, required=False) -> str | None:
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


DIVIDEND = DatasetSpec(
    dataset="cash_distribution", table="cash_distribution_observation", collection_vendor="kis",
    columns=(Column("market_code", "str"), Column("ticker", "str"), Column("source_vendor", "str"),
        Column("source_event_key", "str"), Column("record_date", "date"),
        Column("ex_date", "date"), Column("pay_date", "date"),
        Column("amount_per_share", "str"), Column("currency", "str"),
        Column("distribution_kind", "str"), Column("confirmation", "str"), *_PROVENANCE),
    key=("market_code", "ticker", "source_vendor", "source_event_key"),
    partition=lambda row: f"canonical/market_data/cash_distribution/record_date={row['record_date']}",
    normalize=_normalize,
)


def collect_dividends(storage, source, run_id, *, instruments, start: date, end: date):
    scope = {"instruments": sorted(instruments, key=lambda row: (row["market_code"], row["ticker"])),
             "from": start.isoformat(), "to": end.isoformat()}
    producer = "ingest_raw_dividend"
    done = existing_raw_manifest(storage, DIVIDEND.dataset, run_id)
    if done is not None:
        ensure_same_request(DIVIDEND.dataset, run_id, done, scope)
        return record_already_collected(storage, DIVIDEND, run_id, producer, done)
    if not instruments or start > end:
        raise ValueError("Dividend scope must contain instruments and a valid date range")
    started = datetime.now(timezone.utc)
    objects = [obj for instrument in scope["instruments"] for obj in source.fetch(instrument, start, end)]
    return write_raw_run(storage, DIVIDEND, run_id, producer=producer, objects=objects,
                         started_at=started, request_scope={"requested": scope})
