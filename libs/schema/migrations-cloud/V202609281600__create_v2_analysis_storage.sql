-- Analysis outputs only. Source ingestion tables are unchanged.
CREATE TABLE movement_analyses (
    analysis_id text PRIMARY KEY,
    etf_code text NOT NULL,
    analysis_at timestamptz NOT NULL,
    previous_analysis_id text REFERENCES movement_analyses(analysis_id),
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    published_at timestamptz,
    error_message text,
    trading_date date NOT NULL,
    summary text,
    selected_item_ids text[] NOT NULL DEFAULT '{}',
    CHECK (cardinality(selected_item_ids) <= 5),
    CHECK (previous_analysis_id IS DISTINCT FROM analysis_id)
);
CREATE INDEX movement_analyses_etf_time ON movement_analyses(etf_code, analysis_at DESC);

CREATE TABLE movement_items (
    item_id text PRIMARY KEY,
    analysis_id text NOT NULL REFERENCES movement_analyses(analysis_id),
    type text NOT NULL CHECK (type IN ('이슈', '차트', '매크로', '밸류', '수급')),
    title_keyword text NOT NULL,
    sentence text NOT NULL,
    sentiment text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    source_as_of timestamptz,
    tool_run_ids text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX movement_items_analysis ON movement_items(analysis_id);

CREATE TABLE outlook_analyses (
    analysis_id text PRIMARY KEY,
    etf_code text NOT NULL,
    analysis_at timestamptz NOT NULL,
    previous_analysis_id text REFERENCES outlook_analyses(analysis_id),
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    published_at timestamptz,
    error_message text,
    outlook_sticker text CHECK (outlook_sticker IN ('강력상승', '상승', '중립', '하락', '강력하락')),
    summary_title text,
    summary text,
    detail_title text,
    detail_mode text CHECK (detail_mode IN ('create', 'update', 'rewrite')),
    conclusion_title text,
    conclusion_sentence text,
    change_condition text,
    CHECK (previous_analysis_id IS DISTINCT FROM analysis_id)
);
CREATE INDEX outlook_analyses_etf_time ON outlook_analyses(etf_code, analysis_at DESC);

CREATE TABLE outlook_items (
    row_id text PRIMARY KEY,
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    item_id text NOT NULL,
    section text NOT NULL CHECK (section IN ('detail', 'update')),
    change_type text,
    position integer NOT NULL CHECK (position >= 0),
    title_keyword text NOT NULL,
    bullets jsonb,
    sentence text,
    source_as_of timestamptz,
    tool_run_ids text[] NOT NULL DEFAULT '{}',
    UNIQUE (analysis_id, section, item_id),
    UNIQUE (analysis_id, section, position),
    CHECK ((section = 'detail' AND change_type IS NULL AND bullets IS NOT NULL
            AND jsonb_typeof(bullets) = 'array')
        OR (section = 'update' AND change_type IS NOT NULL
            AND change_type IN ('added', 'modified', 'deleted') AND bullets IS NULL))
);

CREATE TABLE outlook_factors (
    row_id text PRIMARY KEY,
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    type text NOT NULL CHECK (type IN ('이슈', '차트', '매크로', '밸류', '수급')),
    sticker text NOT NULL CHECK (sticker IN ('강력상승', '상승', '중립', '하락', '강력하락')),
    sentence text NOT NULL,
    UNIQUE (analysis_id, type)
);

CREATE TABLE outlook_conclusion_keywords (
    row_id text PRIMARY KEY,
    analysis_id text NOT NULL REFERENCES outlook_analyses(analysis_id),
    kind text NOT NULL CHECK (kind IN ('support', 'burden')),
    position integer NOT NULL CHECK (position >= 0),
    label text NOT NULL,
    tool_run_ids text[] NOT NULL DEFAULT '{}',
    UNIQUE (analysis_id, kind, position)
);

CREATE TABLE tool_definitions (
    tool_id text PRIMARY KEY,
    function_name text NOT NULL,
    version text NOT NULL,
    formula_latex text,
    description text NOT NULL,
    source_names text[] NOT NULL DEFAULT '{}',
    UNIQUE (function_name, version)
);

CREATE TABLE tool_runs (
    tool_run_id text PRIMARY KEY,
    tool_id text NOT NULL REFERENCES tool_definitions(tool_id),
    movement_analysis_id text REFERENCES movement_analyses(analysis_id),
    outlook_analysis_id text REFERENCES outlook_analyses(analysis_id),
    arguments jsonb NOT NULL DEFAULT '{}',
    context jsonb NOT NULL DEFAULT '{}',
    output jsonb,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (num_nonnulls(movement_analysis_id, outlook_analysis_id) = 1),
    CHECK (jsonb_typeof(arguments) = 'object' AND jsonb_typeof(context) = 'object'),
    CHECK (status <> 'completed' OR (output IS NOT NULL AND finished_at IS NOT NULL))
);
CREATE INDEX tool_runs_movement ON tool_runs(movement_analysis_id) WHERE movement_analysis_id IS NOT NULL;
CREATE INDEX tool_runs_outlook ON tool_runs(outlook_analysis_id) WHERE outlook_analysis_id IS NOT NULL;
