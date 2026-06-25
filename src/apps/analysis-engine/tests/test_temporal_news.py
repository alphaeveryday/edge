"""TemporalNewsModel + NewsWindows: window assembly and signal recovery."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from edge_event_model.features.news_arm import NewsWindows
from edge_event_model.models.temporal_nn import TemporalNewsModel

torch = pytest.importorskip("torch")

EMB = 16
SEQ = 8
WEEK = 5


def _toy():
    dates = pd.bdate_range("2024-01-01", periods=45)
    cal = np.array([np.datetime64(d) for d in dates])
    rng = np.random.default_rng(0)
    signal = rng.normal(size=EMB).astype("float32")
    today_emb, day_mean, ys = {}, {}, {}
    for d in dates:
        y = float(rng.normal())
        k = int(rng.integers(1, 4))
        mat = (signal[None, :] * y + rng.normal(scale=0.3, size=(k, EMB))).astype("float32")
        key = ("A", pd.Timestamp(d))
        today_emb[key] = mat
        day_mean[key] = mat.mean(axis=0).astype("float32")
        ys[pd.Timestamp(d)] = y
    w = NewsWindows(today_emb, day_mean, {"A": cal}, week=WEEK, month=SEQ, emb_dim=EMB)
    df = pd.DataFrame({
        "ticker": "A",
        "trade_date": [pd.Timestamp(d) for d in dates],
        "abnormal_return": [ys[pd.Timestamp(d)] for d in dates],
    })
    return w, df, cal


def test_windows_shapes_and_content():
    w, df, cal = _toy()
    t, wk, m = w.windows("A", pd.Timestamp(cal[20]))
    assert m.shape == (SEQ, EMB)                     # fixed-length CNN sequence
    assert t is not None and t.shape[1] == EMB       # same-day matrix
    # 7d set aggregates the last WEEK trading days' articles
    assert wk is not None and wk.shape[0] >= t.shape[0]
    # last row of the month sequence is today's mean (non-zero)
    assert np.abs(m[-1]).sum() > 0
    # a date with no calendar history before it still yields a right-aligned seq
    t0, _, m0 = w.windows("A", pd.Timestamp(cal[0]))
    assert m0.shape == (SEQ, EMB) and np.abs(m0[:-1]).sum() == 0


def test_temporal_model_recovers_signal():
    w, df, _ = _toy()
    model = TemporalNewsModel(target_col="abnormal_return", emb_dim=EMB, seq_len=SEQ,
                              proj=16, hidden=16, epochs=80, patience=30, batch_size=32)
    model.fit(df, w)
    abn, sig = model.predict_abnormal(df, w)
    assert abn.shape == (len(df),)
    assert np.isfinite(abn).all() and np.isfinite(sig).all()
    assert (sig > 0).all()
    # in-sample the model should track the injected signal
    assert np.corrcoef(abn, df["abnormal_return"].to_numpy())[0, 1] > 0.4


def test_no_context_rows_are_zero():
    w, df, _ = _toy()
    model = TemporalNewsModel(target_col="abnormal_return", emb_dim=EMB, seq_len=SEQ,
                              proj=16, hidden=16, epochs=20, patience=10)
    model.fit(df, w)
    # a ticker/date absent from the windows -> zero abnormal + unconditional sigma
    other = pd.DataFrame({"ticker": ["Z"], "trade_date": [pd.Timestamp("2024-01-15")], "abnormal_return": [0.0]})
    abn, sig = model.predict_abnormal(other, w)
    assert abn[0] == 0.0 and sig[0] == pytest.approx(model.target_z.std)
