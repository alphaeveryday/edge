"""Dataset Contract typed registry (ADR-0043, ALPHA-654)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .trading_calendar import latest_kr_trading_day


class Cadence(str, Enum):
    MARKET_EVENT = "MARKET_EVENT"


class ExpectedAsOfRule(str, Enum):
    LATEST_KR_TRADING_DAY = "LATEST_KR_TRADING_DAY"


class RetryOwner(str, Enum):
    SFN = "SFN"


@dataclass(frozen=True)
class DatasetContract:
    contract_key: str
    version: str
    cadence: Cadence
    timezone: str
    expected_as_of_rule: ExpectedAsOfRule
    allowed_as_of_lag_trading_days: int
    required: bool
    retry_owner: RetryOwner
    operational_owner: str | None
    runbook_uri: str | None


ETF_HOLDINGS_KRX_EOD = "ETF_HOLDINGS_KRX_EOD"
ETF_NAV_KIS_DAILY = "ETF_NAV_KIS_DAILY"

_CONTRACTS = {
    ETF_HOLDINGS_KRX_EOD: DatasetContract(
        contract_key=ETF_HOLDINGS_KRX_EOD,
        version="1",
        cadence=Cadence.MARKET_EVENT,
        timezone="Asia/Seoul",
        expected_as_of_rule=ExpectedAsOfRule.LATEST_KR_TRADING_DAY,
        allowed_as_of_lag_trading_days=0,
        required=True,
        retry_owner=RetryOwner.SFN,
        # ADR-0043은 소유 위치만 정했고 첫 계약의 실제 값은 정하지 않았다. 추측하지 않고 드러낸다.
        operational_owner=None,
        runbook_uri=None,
    ),
    ETF_NAV_KIS_DAILY: DatasetContract(
        contract_key=ETF_NAV_KIS_DAILY,
        version="1",
        cadence=Cadence.MARKET_EVENT,
        timezone="Asia/Seoul",
        expected_as_of_rule=ExpectedAsOfRule.LATEST_KR_TRADING_DAY,
        allowed_as_of_lag_trading_days=0,
        required=True,
        retry_owner=RetryOwner.SFN,
        operational_owner=None,
        runbook_uri=None,
    ),
}


def entries() -> tuple[DatasetContract, ...]:
    return tuple(_CONTRACTS.values())


def get(contract_key: str) -> DatasetContract | None:
    return _CONTRACTS.get(contract_key)


def require(contract_key: str) -> DatasetContract:
    contract = get(contract_key)
    if contract is None:
        raise ValueError(f"등록되지 않은 Dataset Contract: {contract_key}")
    return contract


def resolve_expected_as_of(
    contract: DatasetContract,
    slot_date: date,
    holidays: frozenset[str] | None = None,
) -> date:
    if contract.expected_as_of_rule == ExpectedAsOfRule.LATEST_KR_TRADING_DAY:
        return latest_kr_trading_day(slot_date, holidays)
    raise ValueError(f"지원하지 않는 expected-as-of 규칙: {contract.expected_as_of_rule}")


def snapshot(contract: DatasetContract, expected_as_of: date) -> dict:
    """계획 당시의 계약과 해석값을 JSON 호환 값으로 고정한다."""
    return {
        "contract_key": contract.contract_key,
        "contract_version": contract.version,
        "cadence": contract.cadence.value,
        "timezone": contract.timezone,
        "expected_as_of_rule": contract.expected_as_of_rule.value,
        "expected_as_of": expected_as_of.isoformat(),
        "allowed_as_of_lag_trading_days": contract.allowed_as_of_lag_trading_days,
        "required": contract.required,
        "retry_owner": contract.retry_owner.value,
        "operational_owner": contract.operational_owner,
        "runbook_uri": contract.runbook_uri,
    }
