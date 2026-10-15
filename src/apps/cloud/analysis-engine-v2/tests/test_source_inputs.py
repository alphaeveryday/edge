from datetime import datetime

import pytest

from edge_analysis_v2.storage.source_inputs import financial_inputs, macro_inputs


class _NoQuery:
    def cursor(self):
        raise AssertionError("a naive instant must be rejected before any query")


@pytest.mark.parametrize("read", [lambda at: macro_inputs(_NoQuery(), at),
                                  lambda at: financial_inputs(_NoQuery(), at, ["005930"])])
@pytest.mark.parametrize("analysis_at", [datetime(2026, 9, 29, 10, 0), "2026-09-29T10:00:00"])
def test_analysis_instant_without_offset_is_rejected_before_reading(read, analysis_at):
    # PostgreSQL would read a naive instant in the session time zone, silently shifting what counts as "visible".
    with pytest.raises(ValueError, match="timezone"):
        read(analysis_at)
