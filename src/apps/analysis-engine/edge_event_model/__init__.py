"""EDGE event-driven return model.

Predicts same-day close and high (as log returns reconstructed to prices) from
end-of-day-confirmed data only -- never the same-day close/high themselves --
following the screenshot architecture:

    Stage A  linear regression (FF5)  -> normal_return, abnormal_return (= residual)
    Stage B  NN (news)                -> news_score (+ mu, sigma for confidence)
    Stage C  final linear regression  -> abnormal_return ~ news_score

See ``src/spec/edge_event_return_model_spec.md`` for the full contract.
"""
from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
