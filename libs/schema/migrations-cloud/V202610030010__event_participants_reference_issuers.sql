-- Cutover: quiesce event writers, back up affected rows, deploy Actor-aware
-- readers/writers, then run this transaction before resuming ingestion.
-- Keep argument IDs, roles, mentions, groups, event IDs and historical thread IDs.
SET LOCAL lock_timeout = '10s';
SELECT pg_advisory_xact_lock(hashtext('edge-event-threading'));
LOCK TABLE equity_profile IN SHARE MODE;
LOCK TABLE event_argument, event_thread IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE event_actor_issuers ON COMMIT DROP AS
SELECT instrument_id, issuer_actor_id FROM equity_profile;
CREATE UNIQUE INDEX ON event_actor_issuers(instrument_id);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM event_argument a
        LEFT JOIN event_actor_issuers e ON e.instrument_id = a.entity_id
        WHERE a.entity_id IS NOT NULL
        GROUP BY a.source_event_id, a.role_code, coalesce(e.issuer_actor_id, a.entity_id)
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'participant collision after issuer resolution; no evidence was discarded';
    END IF;
END $$;

CREATE FUNCTION pg_temp.actor_thread_key(original text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE parts text[]; i integer; separator integer; value text; converted text;
        actors text[];
BEGIN
    parts := string_to_array(original, '||');
    IF parts[1] NOT LIKE 'event_type_id=%' THEN
        RAISE EXCEPTION 'unsupported thread key: %', original;
    END IF;
    FOR i IN 2..cardinality(parts) LOOP
        separator := strpos(parts[i], '=');
        IF separator = 0 OR parts[i] NOT LIKE 'required:%' THEN
            RAISE EXCEPTION 'unsupported thread identity: %', parts[i];
        END IF;
        value := substr(parts[i], separator + 1);
        IF left(value, 1) = '[' THEN
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(value::jsonb) item
                       WHERE jsonb_typeof(item) <> 'string') THEN
                RAISE EXCEPTION 'non-string thread identity: %', value;
            END IF;
            SELECT array_agg(actor ORDER BY actor COLLATE "C") INTO actors
            FROM (SELECT DISTINCT coalesce(e.issuer_actor_id, item) actor
                  FROM jsonb_array_elements_text(value::jsonb) item
                  LEFT JOIN event_actor_issuers e ON e.instrument_id = item) resolved;
            IF cardinality(actors) = 1 THEN
                converted := actors[1];
            ELSE
                SELECT '[' || string_agg(to_json(item)::text, ', ' ORDER BY item COLLATE "C") || ']'
                  INTO converted FROM unnest(actors) item;
            END IF;
        ELSE
            SELECT issuer_actor_id INTO converted FROM event_actor_issuers
              WHERE instrument_id = value;
            converted := coalesce(converted, value);
        END IF;
        IF converted IS NULL THEN
            RAISE EXCEPTION 'empty thread identity: %', original;
        END IF;
        parts[i] := left(parts[i], separator) || converted;
    END LOOP;
    RETURN array_to_string(parts, '||');
END $$;

CREATE TEMP TABLE event_actor_thread_keys ON COMMIT DROP AS
SELECT thread_id, thread_key AS old_key, pg_temp.actor_thread_key(thread_key) AS new_key
FROM event_thread;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM event_actor_thread_keys GROUP BY new_key HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'thread collision after issuer resolution; histories require explicit reconciliation';
    END IF;
END $$;

UPDATE event_argument a SET entity_id = e.issuer_actor_id
FROM event_actor_issuers e WHERE a.entity_id = e.instrument_id;
UPDATE event_thread t SET thread_key = k.new_key
FROM event_actor_thread_keys k WHERE t.thread_id = k.thread_id AND k.old_key <> k.new_key;

-- Fail loudly if a stale or unconverted writer attempts to restore Equity links.
CREATE FUNCTION reject_equity_event_participant() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM equity_profile WHERE instrument_id = NEW.entity_id) THEN
        RAISE EXCEPTION 'event participant must reference the equity issuer Actor: %', NEW.entity_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER event_participant_requires_issuer
BEFORE INSERT OR UPDATE OF entity_id ON event_argument
FOR EACH ROW EXECUTE FUNCTION reject_equity_event_participant();

COMMENT ON COLUMN event_argument.entity_id IS
'Resolved participant. Equity references use their issuer Actor. Other entities and unresolved mentions retain their source meaning.';
