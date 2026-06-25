from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from edge_event_model import config
from edge_event_model.features.dataset import (
    LEAKAGE_COLUMNS,
    TARGET_COLUMNS,
    SplitConfig,
    assert_no_leakage,
    chronological_split,
    spread_feature_columns,
)


def test_spread_features_have_no_leakage():
    assert_no_leakage(spread_feature_columns())
    banned = set(TARGET_COLUMNS) | set(LEAKAGE_COLUMNS)
    assert not (set(spread_feature_columns()) & banned)


@pytest.mark.parametrize("bad", ["close", "high", "low", "volume", config.LABEL_CLOSE, config.LABEL_SPREAD, config.LABEL_ABNORMAL])
def test_assert_no_leakage_rejects_same_day_columns(bad):
    with pytest.raises(ValueError):
        assert_no_leakage(["normal_return", bad])


def test_chronological_split_no_date_overlap_and_ratios():
    dates = pd.bdate_range("2023-01-02", periods=100)
    frame = pd.DataFrame({"trade_date": np.repeat(dates, 2), "ticker": ["A", "B"] * 100})
    out = chronological_split(frame, SplitConfig(train_ratio=0.70, valid_ratio=0.15))

    assert (out.groupby("trade_date")["split"].nunique() == 1).all()
    n_train = out.loc[out["split"] == "train", "trade_date"].nunique()
    n_valid = out.loc[out["split"] == "validation", "trade_date"].nunique()
    n_test = out.loc[out["split"] == "test", "trade_date"].nunique()
    assert (n_train, n_valid, n_test) == (70, 15, 15)
    assert out.loc[out["split"] == "train", "trade_date"].max() < out.loc[out["split"] == "test", "trade_date"].min()
