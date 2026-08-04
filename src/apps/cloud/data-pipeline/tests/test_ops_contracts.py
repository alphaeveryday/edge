"""Dataset Contract registry tests (ADR-0043, ALPHA-654)."""

from __future__ import annotations

from datetime import date

from data_pipeline.ops import catalog, contracts


def test_catalog_contract_references_are_registered_and_required_matches():
    """WHY: dangling key나 required 이중 SSOT는 Planner 의미를 배포마다 바꾼다."""
    linked = [entry for entry in catalog.entries() if entry.contract_key]
    assert [entry.task_key for entry in linked] == ["ETF_HOLDINGS_COLLECTION_KRX"]
    for entry in linked:
        contract = contracts.require(entry.contract_key)
        assert entry.required == contract.required


def test_krx_contract_snapshot_is_typed_and_interpreted():
    contract = contracts.require(contracts.ETF_HOLDINGS_KRX_EOD)
    expected = contracts.resolve_expected_as_of(
        contract, date(2026, 7, 26), frozenset({"2026-07-24"})
    )
    snap = contracts.snapshot(contract, expected)

    assert snap == {
        "contract_key": contracts.ETF_HOLDINGS_KRX_EOD,
        "contract_version": "1",
        "cadence": "MARKET_EVENT",
        "timezone": "Asia/Seoul",
        "expected_as_of_rule": "LATEST_KR_TRADING_DAY",
        "expected_as_of": "2026-07-23",
        "allowed_as_of_lag_trading_days": 0,
        "required": True,
        "retry_owner": "SFN",
        "operational_owner": None,
        "runbook_uri": None,
    }
