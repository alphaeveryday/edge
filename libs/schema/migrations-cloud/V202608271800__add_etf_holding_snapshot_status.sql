-- ALPHA-1039: canonical snapshot completeness and exact loaded version.
CREATE TABLE etf_holding_snapshot_status (
    etf_instrument_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    input_row_count INTEGER NOT NULL CHECK (input_row_count > 0),
    valid_row_count INTEGER NOT NULL CHECK (
        valid_row_count >= 0 AND valid_row_count <= input_row_count
    ),
    data_version VARCHAR(50) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (etf_instrument_id, trade_date),
    CONSTRAINT fk_etf_holding_snapshot_status_etf
        FOREIGN KEY (etf_instrument_id) REFERENCES etf_profile(instrument_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE etf_holding_snapshot_status IS
'ETF·일자별 canonical 전체/유효 행 건수와 정확히 적재된 data_version. latest-good holdings 선택의 SSOT.';
