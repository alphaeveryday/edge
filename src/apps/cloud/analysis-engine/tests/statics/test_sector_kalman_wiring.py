"""ALPHA-897: holdings gates must control the clock-window sector Kalman lane."""
from __future__ import annotations

import re

import numpy as np
import pytest

from edge_analysis.statics.layers import MARKET_CODE, decompose, select_sector

DAY = "2026-08-07"
CLOCK = ("09:00:00", "10:00:00")
SECTOR = "091170"


class Lake:
    def __init__(self, target_holdings=None):
        self.exists = {}
        self.target_holdings = target_holdings or {"a": 0.50, "b": 0.30, "c": 0.20}
        self.holds = {
            "T": self.target_holdings,
            MARKET_CODE: {"m": 1.0},
            SECTOR: {"a": 0.15, "x": 0.85},
            "TWIN": {"a": 0.50, "b": 0.30, "c": 0.20},
            "ALIEN": {"z": 1.0},
        }
        rng = np.random.default_rng(897)
        rm = rng.normal(0, 0.0015, 78)
        rs = 0.25 * rm + rng.normal(0, 0.0014, 78)
        ry = 1.2 * rm + 0.8 * (rs - 0.25 * rm) + rng.normal(0, 0.0003, 78)
        self.prev = {
            "T": 100 * np.exp(np.cumsum(ry)),
            MARKET_CODE: 100 * np.exp(np.cumsum(rm)),
            SECTOR: 100 * np.exp(np.cumsum(rs)),
        }

    def sql(self, query):
        if "FROM bars_5m" in query and "trade_date <" in query:
            return [[code, i, float(value)] for code, values in self.prev.items()
                    for i, value in enumerate(values)]
        if "FROM bars_5m" in query:
            return []
        if "FROM layers_daily" in query:
            return [["T", "target"], [MARKET_CODE, "market"],
                    [SECTOR, "sector"], ["TWIN", "twin"], ["ALIEN", "alien"]]
        if "FROM s3_etf_holdings" in query:
            code = re.search(r"etf_id = '([^']+)'", query).group(1)
            return [[ticker, ticker, weight * 100]
                    for ticker, weight in self.holds.get(code, {}).items()]
        if "FROM s3_etf_profile" in query:
            return []
        raise AssertionError(query[:100])


def _paths(lake):
    rng = np.random.default_rng(898)
    rm = rng.normal(0, 0.0015, 30)
    rs = 0.25 * rm + rng.normal(0, 0.0014, 30)
    ry = 1.2 * rm + 0.8 * (rs - 0.25 * rm) + rng.normal(0, 0.0003, 30)
    return {"T": tuple(ry), MARKET_CODE: tuple(rm), SECTOR: tuple(rs)}


def test_overlap_contract_selects_only_the_non_tautological_sector():
    selected = select_sector(Lake(), "T", DAY, {SECTOR, "TWIN", "ALIEN"})
    assert selected.code == SECTOR and selected.overlap == pytest.approx(0.15)
    assert selected.twins == ("TWIN",)
    assert selected.alien == ("ALIEN",)


def test_broad_market_overlap_folds_sector_before_selection():
    lake = Lake(target_holdings={"m": 0.80, "a": 0.20})
    selected = select_sector(lake, "T", DAY, {SECTOR})
    assert selected.broad is True and selected.code is None


def test_selected_sector_is_wired_into_two_factor_clock_kalman():
    lake = Lake()
    paths = _paths(lake)
    intraday = {code: (float(sum(path)), False) for code, path in paths.items()}
    roll = decompose(lake, "T", DAY, clock=CLOCK, intraday=intraday, paths=paths)
    assert roll is not None
    sector = next(layer for layer in roll.layers if layer.kind == "섹터")
    assert sector.code == SECTOR and sector.overlap == pytest.approx(0.15)
    assert "sector_layer" not in lake.exists
    assert sum(layer.contribution for layer in roll.layers) + roll.idio == pytest.approx(
        roll.total, abs=1e-12)


def test_missing_sector_path_falls_back_loudly_to_market_only():
    lake = Lake()
    paths = _paths(lake)
    intraday = {code: (float(sum(path)), False) for code, path in paths.items()}
    paths.pop(SECTOR)
    roll = decompose(lake, "T", DAY, clock=CLOCK, intraday=intraday, paths=paths)
    assert roll is not None
    assert all(layer.kind != "섹터" for layer in roll.layers)
    assert "sector_layer" in lake.exists
