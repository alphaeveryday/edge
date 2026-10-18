CREATE OR REPLACE VIEW ontology_view.cash_distribution AS
SELECT json_build_array(d.market_code, d.ticker, d.source_vendor, d.source_event_key)::text AS id,
       i.instrument_id,
       ep.instrument_id AS etf_instrument_id,
       eq.instrument_id AS equity_instrument_id,
       d.ticker, d.market_code, d.source_vendor, d.source_event_key,
       d.record_date, d.ex_date, d.pay_date, d.amount_per_share,
       d.amount_per_share::text AS amount_decimal_text,
       d.currency, d.distribution_kind, d.confirmation, d.available_at,
       d.raw_key, d.raw_sha256, d.raw_run_id
FROM public.cash_distribution_latest d
LEFT JOIN public.instrument i ON i.market_code = d.market_code AND i.ticker = d.ticker
LEFT JOIN public.etf_profile ep ON ep.instrument_id = i.instrument_id AND i.instrument_type = 'ETF'
LEFT JOIN public.equity_profile eq ON eq.instrument_id = i.instrument_id AND i.instrument_type = 'EQUITY';
