SET LOCAL lock_timeout = '5s';
ALTER TABLE movement_analyses ADD COLUMN withdrawn_at timestamptz;
