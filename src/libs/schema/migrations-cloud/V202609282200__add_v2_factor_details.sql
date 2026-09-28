ALTER TABLE outlook_analyses ADD COLUMN issue_headline text;

CREATE TABLE outlook_factor_metrics (
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    factor_type text NOT NULL CHECK (factor_type IN ('차트', '매크로', '밸류', '수급')),
    metric_key text NOT NULL,
    numeric_value numeric,
    text_value text,
    observed_date date,
    observed_at timestamptz,
    subject text,
    position integer NOT NULL CHECK (position >= 0),
    tool_run_ids text[] NOT NULL CHECK (cardinality(tool_run_ids) > 0),
    PRIMARY KEY (analysis_id, factor_type, metric_key),
    UNIQUE (analysis_id, factor_type, position),
    CHECK (num_nonnulls(numeric_value, text_value) = 1),
    CHECK (num_nonnulls(observed_date, observed_at) = 1),
    CHECK (numeric_value IS NULL OR numeric_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))
);

CREATE TABLE outlook_issue_items (
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    position integer NOT NULL CHECK (position >= 0),
    title_keyword text NOT NULL,
    sentence text NOT NULL,
    sentiment text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    tool_run_ids text[] NOT NULL CHECK (cardinality(tool_run_ids) > 0),
    PRIMARY KEY (analysis_id, position)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON outlook_factor_metrics, outlook_issue_items
    TO edge_analysis_v2_writer;
