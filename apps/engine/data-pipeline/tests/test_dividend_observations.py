"""Distributions must not become wrong holdings, fake zeroes or duplicate cash."""
import json
from pathlib import Path
from decimal import Decimal
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from data_pipeline.lake import LocalStorage
from data_pipeline.sources.kis_dividend import KisDividendSource
from data_pipeline.steps import source_observations as so
from data_pipeline.steps import source_observations_dividend as dividend


INSTRUMENT = {"ticker": "069500", "market_code": "XKRX"}
ITEM = {"sht_cd": "069500", "record_date": "20260731", "divi_pay_dt": "2026/08/04",
        "per_sto_divi_amt": "183", "divi_kind": "", "stk_kind": "보통"}


def normalize(*items):
    body = json.dumps({"rt_cd": "0", "output1": items}).encode()
    obj = {"body": body, "request": {**INSTRUMENT, "from": "2026-01-01", "to": "2026-12-31"},
           "key": "raw/example", "sha256": so.sha256(body), "fetched_at": "2026-10-18T00:00:00+00:00"}
    return dividend._normalize([obj], {"run_id": "raw1"})


def test_disclosed_cash_preserves_decimal_and_separate_dates():
    rows, rejects = normalize({**ITEM, "per_sto_divi_amt": "183.1234567"})
    assert not rejects
    assert (rows[0]["ticker"], rows[0]["market_code"]) == ("069500", "XKRX")
    assert rows[0]["amount_per_share"] == "183.1234567"
    assert rows[0]["pay_date"] == "2026-08-04"
    assert rows[0]["ex_date"] is None  # A record date does not prove an ex-date.


@pytest.mark.parametrize("changes", [
    {"sht_cd": "069501"}, {"record_date": "20260300"},
    {"record_date": "20260230"}, {"record_date": "20250101"},
    {"divi_pay_dt": "20260701"}, {"divi_pay_dt": "20260800"},
    {"per_sto_divi_amt": "NaN"}, {"per_sto_divi_amt": "Infinity"},
    {"per_sto_divi_amt": "-1"}, {"per_sto_divi_amt": "unknown"},
    {"per_sto_divi_amt": 183}, {"per_sto_divi_amt": True},
])
def test_invalid_identity_date_or_cash_is_visible_as_reject(changes):
    rows, rejects = normalize({**ITEM, **changes})
    assert not rows and len(rejects) == 1


@pytest.mark.parametrize("amount", ["0", ""])
def test_unannounced_schedule_does_not_claim_zero_dividend(amount):
    rows, rejects = normalize({**ITEM, "per_sto_divi_amt": amount, "divi_pay_dt": ""})
    assert not rejects
    assert rows[0]["amount_per_share"] is None
    assert rows[0]["confirmation"] == "pending"


def test_one_malformed_item_does_not_erase_other_instruments_cash():
    rows, rejects = normalize(ITEM, None)
    assert len(rows) == len(rejects) == 1
    assert rejects[0]["reasons"] == ["invalid_record_type"]


def test_alphanumeric_ticker_keeps_its_existing_identity():
    obj = {"body": json.dumps({"rt_cd": "0", "output1": [{**ITEM, "sht_cd": "0093A0"}]}).encode(),
           "request": {**INSTRUMENT, "ticker": "0093A0", "from": "2026-01-01", "to": "2026-12-31"},
           "key": "raw/example", "sha256": "a" * 64, "fetched_at": "2026-10-18T00:00:00+00:00"}
    rows, rejects = dividend._normalize([obj], {"run_id": "r"})
    assert len(rows) == 1 and not rejects


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def request(self, method, url, *, headers, decode, response_headers):
        self.calls += 1
        body, continuation = self.response(parse_qs(urlparse(url).query))
        response_headers["tr_cont"] = continuation
        return json.dumps(body).encode()


def source(client):
    result = KisDividendSource("test", "test", client)
    result.auth.token = lambda: "test"
    return result


def test_truncated_response_splits_range_instead_of_claiming_complete():
    client = Client(lambda p: ({"rt_cd": "0", "output1": [ITEM]},
                              "M" if p["F_DT"] != p["T_DT"] else "E"))
    objects = source(client).fetch(INSTRUMENT, date(2026, 7, 30), date(2026, 7, 31))
    assert client.calls == 3
    assert objects[0].request["superseded_by_split"] is True
    assert len([o for o in objects if not o.request.get("superseded_by_split")]) == 2


def test_truncated_single_day_remains_failed():
    objects = source(Client(lambda p: ({"rt_cd": "0", "output1": [ITEM]}, "M"))).fetch(
        INSTRUMENT, date(2026, 7, 31), date(2026, 7, 31))
    assert objects[0].status == "error"
    assert objects[0].detail == "incomplete_single_day"


def test_failed_collection_cannot_be_normalized_as_successful_zero_cash(tmp_path):
    client = Client(lambda p: ({"rt_cd": "1", "output1": []}, "E"))
    storage = LocalStorage(tmp_path)
    assert dividend.collect_dividends(storage, source(client), "failed", instruments=[INSTRUMENT],
        start=date(2026, 7, 1), end=date(2026, 7, 31)) != 0
    assert so.normalize(storage, dividend.DIVIDEND, "failed_norm", "failed", producer="normalize_dividend") != 0


def test_authentication_failure_is_recorded_as_source_failure():
    adapter = source(Client(lambda p: ({"rt_cd": "0", "output1": []}, "E")))
    def fail():
        raise RuntimeError("sensitive details must not appear")
    adapter.auth.token = fail
    objects = adapter.fetch(INSTRUMENT, date(2026, 7, 1), date(2026, 7, 31))
    assert objects[0].status == "error"
    assert objects[0].detail == "RuntimeError"


def test_source_wide_stop_preserves_prior_raw_without_calling_remaining_symbols(tmp_path):
    from data_pipeline.sources.http import StopFetch
    class StoppingSource:
        def __init__(self): self.calls = []
        def fetch(self, instrument, start, end):
            self.calls.append(instrument['ticker'])
            if len(self.calls) == 2:
                raise StopFetch('quota')
            return source(Client(lambda p: ({'rt_cd': '0', 'output1': []}, 'E'))).fetch(instrument, start, end)
    adapter = StoppingSource()
    storage = LocalStorage(tmp_path)
    assert dividend.collect_dividends(storage, adapter, 'stopped',
        instruments=[{'ticker': s, 'market_code': 'XKRX'} for s in ['069500', '360750', '379800']],
        start=date(2026, 1, 1), end=date(2026, 12, 31)) == 2
    assert adapter.calls == ['069500', '360750']
    manifest = so.existing_raw_manifest(storage, dividend.DIVIDEND.dataset, 'stopped')
    assert manifest['counts'] == {'ok': 0, 'empty': 1, 'error': 2}


def test_stop_inside_split_preserves_completed_window_and_parent_bytes(tmp_path):
    from data_pipeline.sources.http import StopFetch
    def response(params):
        if params['F_DT'] == params['T_DT'] == ['20260731']:
            raise StopFetch('quota')
        return {'rt_cd': '0', 'output1': []}, 'M' if params['F_DT'] != params['T_DT'] else 'E'
    storage = LocalStorage(tmp_path)
    client = Client(response)
    assert dividend.collect_dividends(storage, source(client), 'split_stop',
        instruments=[INSTRUMENT, {**INSTRUMENT, 'ticker': '360750'}],
        start=date(2026, 7, 30), end=date(2026, 7, 31)) == 2
    assert client.calls == 3
    manifest = so.existing_raw_manifest(storage, dividend.DIVIDEND.dataset, 'split_stop')
    assert manifest['counts'] == {'ok': 1, 'empty': 1, 'error': 2}
    assert len([e for e in manifest['objects'] if e.get('key')]) == 2


def test_invalid_later_ticker_is_rejected_before_any_network_call(tmp_path):
    client = Client(lambda p: ({'rt_cd': '0', 'output1': []}, 'E'))
    with pytest.raises(ValueError):
        dividend.collect_dividends(LocalStorage(tmp_path), source(client), 'bad_scope',
            instruments=[INSTRUMENT, {**INSTRUMENT, 'ticker': 'INVALID'}],
            start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert client.calls == 0


@pytest.mark.parametrize("body,cont,status", [
    ({"rt_cd": "0", "output1": []}, "E", "empty"),
    ({"rt_cd": "1", "output1": []}, "E", "error"),
    ({"rt_cd": "0", "output1": {}}, "E", "error"),
    ({"rt_cd": "0", "output1": []}, "", "error"),
])
def test_empty_failure_and_unknown_pagination_are_not_interchangeable(body, cont, status):
    result = source(Client(lambda p: (body, cont))).fetch(INSTRUMENT, date(2026, 7, 1), date(2026, 7, 31))
    assert result[0].status == status


def test_raw_replay_does_not_refetch_and_canonical_retains_only_one_event(tmp_path):
    client = Client(lambda p: ({"rt_cd": "0", "output1": [ITEM, ITEM]}, "E"))
    storage = LocalStorage(tmp_path)
    kwargs = dict(instruments=[INSTRUMENT], start=date(2026, 1, 1), end=date(2026, 12, 31))
    for _ in range(2):
        assert dividend.collect_dividends(storage, source(client), "raw1", **kwargs) == 0
    assert client.calls == 1
    assert so.normalize(storage, dividend.DIVIDEND, "norm1", "raw1", producer="normalize_dividend") == 0
    keys = storage.list_keys("canonical/market_data/cash_distribution/")
    rows = [r for key in keys for r in so.read_rows(dividend.DIVIDEND, storage.get_bytes(key))]
    assert len(rows) == 1
    with pytest.raises(SystemExit):
        dividend.collect_dividends(storage, source(client), "raw1", **{**kwargs, "end": date(2026, 11, 1)})


def test_conflicting_same_event_is_not_arbitrarily_summed(tmp_path):
    client = Client(lambda p: ({"rt_cd": "0", "output1": [ITEM, {**ITEM, "per_sto_divi_amt": "200"}]}, "E"))
    storage = LocalStorage(tmp_path)
    dividend.collect_dividends(storage, source(client), "raw1", instruments=[INSTRUMENT],
                               start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert so.normalize(storage, dividend.DIVIDEND, "norm1", "raw1", producer="normalize_dividend") != 0


def test_live_kodex_amount_and_payment_dates_match_issuer_comparison():
    # WHY: a numeric API field is only usable after checking the issuer's cash unit.
    fixtures = Path(__file__).parent / "fixtures/dividends"
    body = json.loads((fixtures / "kis_069500.json").read_text(encoding="utf-8"))["body"]
    issuer = json.loads((fixtures / "kodex_069500_comparison.json").read_text(encoding="utf-8"))
    by_record = {r["record_date"]: r for r in issuer}
    assert len(body["output1"]) == len(by_record) == 7
    for item in body["output1"]:
        expected = by_record[dividend.exact_date(item["record_date"], required=True)]
        assert Decimal(item["per_sto_divi_amt"]) == Decimal(expected["amount"])
        assert dividend.exact_date(item["divi_pay_dt"]) == expected["pay_date"]
