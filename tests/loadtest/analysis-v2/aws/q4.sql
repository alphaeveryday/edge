SELECT json_build_object(
 'now', now(),
 'roles', (SELECT json_agg(json_build_object('role', rolname, 'limit', rolconnlimit, 'sessions', (SELECT count(*) FROM pg_stat_activity a WHERE a.usename = r.rolname)) ORDER BY rolname) FROM pg_roles r WHERE rolcanlogin AND rolname !~ '^(pg_|rds)'),
 'total_sessions', (SELECT count(*) FROM pg_stat_activity WHERE usename IS NOT NULL),
 'settings', (SELECT json_object_agg(name, setting) FROM pg_settings WHERE name IN ('max_connections', 'superuser_reserved_connections', 'reserved_connections'))
) AS j
