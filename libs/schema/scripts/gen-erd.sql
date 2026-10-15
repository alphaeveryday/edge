-- 물리 ERD(DBML) 에미터.
--
-- Flyway 마이그레이션이 적용된 스키마를 pg_catalog 에서 읽어 dbdiagram.io 문법으로 출력한다.
-- 외부 도구(sql2dbml 등) 없이 psql 만 쓴다. 모든 ORDER BY 를 COLLATE "C"(바이트 순서)로
-- 고정해 OS/로케일과 무관하게 결정적이다 — 로컬과 CI 가 바이트 동일한 산출물을 낸다.
-- generate-erd.sh 가 각 세트(cloud/onprem) DB 에 대해 호출한다. Flyway 가 SSOT 이며 이 산출물은 파생물.
WITH
pk AS (
  SELECT conrelid AS reloid, conkey AS keys
  FROM pg_constraint WHERE contype = 'p'
),
pk_single AS (
  SELECT reloid, keys[1] AS attnum FROM pk WHERE cardinality(keys) = 1
),
col AS (
  SELECT c.oid AS reloid, c.relname, a.attnum, a.attname,
    replace(replace(replace(replace(replace(
      format_type(a.atttypid, a.atttypmod),
      'timestamp without time zone', 'timestamp'),
      'timestamp with time zone', 'timestamptz'),
      'character varying', 'varchar'),
      'double precision', 'float8'),
      'integer', 'int') AS typ,
    a.attnotnull AS notnull,
    (a.attidentity <> '' OR COALESCE(pg_get_expr(ad.adbin, ad.adrelid), '') LIKE 'nextval(%') AS incr
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
  WHERE c.relkind = 'r' AND c.relname <> 'flyway_schema_history'
),
colline AS (
  SELECT col.reloid, col.relname, col.attnum,
    '  "' || col.attname || '" ' || col.typ ||
    CASE
      WHEN ps.attnum IS NOT NULL AND col.incr THEN ' [pk, increment]'
      WHEN ps.attnum IS NOT NULL THEN ' [pk]'
      WHEN col.incr AND col.notnull THEN ' [increment, not null]'
      WHEN col.incr THEN ' [increment]'
      WHEN col.notnull THEN ' [not null]'
      ELSE ''
    END AS line
  FROM col
  LEFT JOIN pk_single ps ON ps.reloid = col.reloid AND ps.attnum = col.attnum
),
comp_pk AS (
  SELECT p.reloid,
    E'\n\n  indexes {\n    (' ||
    string_agg('"' || a.attname || '"', ', ' ORDER BY u.ord) ||
    E') [pk]\n  }' AS block
  FROM pk p
  JOIN LATERAL unnest(p.keys) WITH ORDINALITY AS u(attnum, ord) ON true
  JOIN pg_attribute a ON a.attrelid = p.reloid AND a.attnum = u.attnum
  WHERE cardinality(p.keys) > 1
  GROUP BY p.reloid
),
tbl AS (
  SELECT cl.relname,
    'Table "' || cl.relname || '" {' || E'\n' ||
    string_agg(cl.line, E'\n' ORDER BY cl.attnum) ||
    COALESCE(cp.block, '') ||
    E'\n}' AS block
  FROM colline cl
  LEFT JOIN comp_pk cp ON cp.reloid = cl.reloid
  GROUP BY cl.reloid, cl.relname, cp.block
),
fk AS (
  SELECT con.conrelid, con.confrelid, con.conname, con.conkey, con.confkey,
         cr.relname AS child, pr.relname AS parent,
         cardinality(con.conkey) AS n
  FROM pg_constraint con
  JOIN pg_class cr ON cr.oid = con.conrelid
  JOIN pg_class pr ON pr.oid = con.confrelid
  JOIN pg_namespace n ON n.oid = cr.relnamespace AND n.nspname = 'public'
  WHERE con.contype = 'f'
),
fk_line AS (
  SELECT f.child, f.conname,
    CASE WHEN f.n = 1 THEN
      'Ref: "' || f.child || '"."' ||
        (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = f.conrelid AND a.attnum = f.conkey[1]) ||
      '" > "' || f.parent || '"."' ||
        (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = f.confrelid AND a.attnum = f.confkey[1]) || '"'
    ELSE
      'Ref: "' || f.child || '".(' ||
        (SELECT string_agg('"' || a.attname || '"', ', ' ORDER BY u.ord)
           FROM unnest(f.conkey) WITH ORDINALITY u(an, ord)
           JOIN pg_attribute a ON a.attrelid = f.conrelid AND a.attnum = u.an) ||
      ') > "' || f.parent || '".(' ||
        (SELECT string_agg('"' || a.attname || '"', ', ' ORDER BY u.ord)
           FROM unnest(f.confkey) WITH ORDINALITY u(an, ord)
           JOIN pg_attribute a ON a.attrelid = f.confrelid AND a.attnum = u.an) ||
      ')'
    END AS line
  FROM fk f
)
SELECT
  '// AUTO-GENERATED from libs/schema Flyway migrations - DO NOT EDIT.' || E'\n' ||
  '// Regenerate: bash src/libs/schema/scripts/generate-erd.sh (schema-validate CI enforces no drift).' || E'\n' ||
  '// SSOT: libs/schema migrations. Derived physical ERD (dbdiagram.io syntax).' || E'\n\n' ||
  (SELECT string_agg(block, E'\n\n' ORDER BY relname COLLATE "C") FROM tbl) ||
  COALESCE(E'\n\n' || (SELECT string_agg(line, E'\n' ORDER BY child COLLATE "C", conname COLLATE "C") FROM fk_line), '');
