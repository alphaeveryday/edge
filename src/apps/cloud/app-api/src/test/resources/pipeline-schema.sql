-- 동기화 테스트용 파이프라인 RDS 축소본
-- 동기화가 읽는 컬럼에 한정한 원본과 같은 이름과 제약
CREATE TABLE entity (entity_id TEXT PRIMARY KEY, display_name TEXT NOT NULL);
CREATE TABLE instrument (
    instrument_id TEXT PRIMARY KEY, market_code VARCHAR(30) NOT NULL, ticker VARCHAR(30) NOT NULL,
    instrument_type VARCHAR(20) NOT NULL CHECK (instrument_type IN ('ETF', 'EQUITY')),
    UNIQUE (market_code, ticker));
CREATE TABLE price_daily (
    instrument_id TEXT NOT NULL, trade_date DATE NOT NULL, close_price NUMERIC(24, 8), volume BIGINT,
    PRIMARY KEY (instrument_id, trade_date));
CREATE TABLE etf_holding_snapshot (
    etf_instrument_id TEXT NOT NULL, constituent_instrument_id TEXT NOT NULL, trade_date DATE NOT NULL,
    weight_ratio DOUBLE PRECISION, PRIMARY KEY (etf_instrument_id, constituent_instrument_id, trade_date));
CREATE TABLE movement_analyses (
    analysis_id text PRIMARY KEY, etf_code text NOT NULL, analysis_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')), published_at timestamptz,
    trading_date date NOT NULL, summary text, selected_item_ids text[] NOT NULL DEFAULT '{}',
    data_source text NOT NULL DEFAULT 'unknown' CHECK (data_source IN ('unknown', 'synthetic', 'database')));
CREATE TABLE movement_items (
    item_id text PRIMARY KEY, analysis_id text NOT NULL REFERENCES movement_analyses(analysis_id),
    type text NOT NULL CHECK (type IN ('이슈', '차트', '매크로', '밸류', '수급')), title_keyword text NOT NULL,
    sentence text NOT NULL, sentiment text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE outlook_analyses (
    analysis_id text PRIMARY KEY, etf_code text NOT NULL, analysis_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')), published_at timestamptz,
    outlook_sticker text CHECK (outlook_sticker IN ('강력상승', '상승', '중립', '하락', '강력하락')),
    summary_title text, summary text, detail_title text, conclusion_title text, conclusion_sentence text,
    change_condition text, issue_headline text,
    data_source text NOT NULL DEFAULT 'unknown' CHECK (data_source IN ('unknown', 'synthetic', 'database')));
CREATE TABLE outlook_items (
    row_id text PRIMARY KEY, analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id), item_id text NOT NULL,
    section text NOT NULL CHECK (section IN ('detail', 'update')), change_type text, position integer NOT NULL,
    title_keyword text NOT NULL, bullets jsonb, sentence text);
CREATE TABLE outlook_factors (
    row_id text PRIMARY KEY, analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    type text NOT NULL CHECK (type IN ('이슈', '차트', '매크로', '밸류', '수급')),
    sticker text NOT NULL CHECK (sticker IN ('강력상승', '상승', '중립', '하락', '강력하락')), sentence text NOT NULL);
CREATE TABLE outlook_conclusion_keywords (
    row_id text PRIMARY KEY, analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    kind text NOT NULL CHECK (kind IN ('support', 'burden')), position integer NOT NULL, label text NOT NULL);
CREATE TABLE outlook_factor_metrics (
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    factor_type text NOT NULL CHECK (factor_type IN ('차트', '매크로', '밸류', '수급')), metric_key text NOT NULL,
    numeric_value numeric, text_value text, observed_date date, observed_at timestamptz, subject text,
    position integer NOT NULL, PRIMARY KEY (analysis_id, factor_type, metric_key));
CREATE TABLE outlook_issue_items (
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id), position integer NOT NULL,
    title_keyword text NOT NULL, sentence text NOT NULL,
    sentiment text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')), PRIMARY KEY (analysis_id, position));
