from __future__ import annotations

from edge_event_model.db import connect, init_schema, schema, upsert_daily


def test_upsert_is_idempotent_on_key(tmp_path):
    conn = connect(tmp_path / "a.sqlite")
    try:
        init_schema(conn)
        row = {
            "trade_date": "2024-01-02", "market": "US", "asset_code": "AAPL",
            "predicted_close_price": 100.0, "news_count": 1,
            "layer1_ff5_importance_betas": {"mkt_rf": 1.0}, "is_event": True,
        }
        upsert_daily(conn, [row])
        upsert_daily(conn, [{**row, "predicted_close_price": 101.0}])
        r = conn.execute("SELECT count(*) c, max(predicted_close_price) p FROM daily_prediction").fetchone()
        assert r["c"] == 1
        assert abs(r["p"] - 101.0) < 1e-9
    finally:
        conn.close()


def test_json_and_bool_coercion(tmp_path):
    conn = connect(tmp_path / "b.sqlite")
    try:
        init_schema(conn)
        upsert_daily(conn, [{
            "trade_date": "2024-01-02", "market": "US", "asset_code": "V",
            "layer1_ff5_importance_betas": {"mkt_rf": 0.5}, "is_event": True, "calibration_pass": False,
        }])
        row = conn.execute(
            "SELECT layer1_ff5_importance_betas, is_event, calibration_pass FROM daily_prediction"
        ).fetchone()
        assert '"mkt_rf"' in row["layer1_ff5_importance_betas"]
        assert row["is_event"] == 1
        assert row["calibration_pass"] == 0
    finally:
        conn.close()


def test_schema_carries_korean_comments():
    ddl = schema.sqlite_ddl()
    assert "예측 기준일" in ddl
    assert "[1층 FF5] 정상수익률 예측" in ddl
    assert "asset_code" in schema.data_dictionary_md()
