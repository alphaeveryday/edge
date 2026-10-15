ALTER TABLE outlook_items
    ADD COLUMN sentiment text,
    ADD COLUMN source_links jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT outlook_items_sentiment_check
        CHECK (sentiment IS NULL OR sentiment IN ('positive', 'neutral', 'negative')),
    ADD CONSTRAINT outlook_items_source_links_array_check
        CHECK (jsonb_typeof(source_links) = 'array');
