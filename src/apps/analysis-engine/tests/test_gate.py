from __future__ import annotations

import numpy as np
import pandas as pd

from edge_event_model import config
from edge_event_model.calibration.gate import apply_calibration_gate, calibration_pass


def test_calibration_pass_boundary_and_direction():
    pred = np.array([0.010, 0.030, -0.010, 0.005, 0.019])
    real = np.array([0.005, 0.005, 0.005, -0.001, 0.000])
    out = calibration_pass(pred, real, max_error=0.02)
    # 0: |0.005|<0.02 and +/+        -> True
    # 1: |0.025|>=0.02               -> False
    # 2: sign mismatch (-/+)         -> False
    # 3: |0.006|<0.02 but +/-        -> False
    # 4: real==0 -> sign 0 vs +1     -> False
    assert list(out) == [True, False, False, False, False]


def test_apply_gate_without_real_returns_na():
    df = pd.DataFrame({"close_return_pred": [0.01, -0.02]})
    out = apply_calibration_gate(df)
    assert out["calibration_pass"].isna().all()
    assert out["error_return"].isna().all()


def test_apply_gate_with_real():
    df = pd.DataFrame({"close_return_pred": [0.01], config.LABEL_CLOSE: [0.009]})
    out = apply_calibration_gate(df)
    assert bool(out["calibration_pass"].iloc[0]) is True
    assert np.isclose(out["error_return"].iloc[0], 0.001)
