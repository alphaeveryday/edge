SELECT json_build_object(
 'now', now(),
 'roles',(SELECT json_agg(json_build_object('r',rolname,'limit',rolconnlimit,'login',rolcanlogin,'cfg',rolconfig)) FROM pg_roles WHERE rolname !~ '^(pg_|rds)'),
 'settings',(SELECT json_object_agg(name,setting) FROM pg_settings WHERE name IN ('max_connections','shared_buffers','work_mem','superuser_reserved_connections','server_version','idle_session_timeout','max_locks_per_transaction','reserved_connections')),
 'activity',(SELECT json_agg(x) FROM (SELECT usename,application_name,count(*) n FROM pg_stat_activity WHERE backend_type='client backend' GROUP BY 1,2 ORDER BY 3 DESC) x),
 'outlook_counts',(SELECT json_agg(x) FROM (SELECT data_source,status,count(*) n,count(DISTINCT etf_code) etfs,min(analysis_at) lo,max(analysis_at) hi FROM outlook_analyses GROUP BY 1,2) x),
 'movement_counts',(SELECT json_agg(x) FROM (SELECT data_source,status,count(*) n,count(DISTINCT etf_code) etfs,min(analysis_at) lo,max(analysis_at) hi FROM movement_analyses GROUP BY 1,2) x),
 'tool_runs',(SELECT json_build_object('n',count(*),'lo',min(started_at),'hi',max(finished_at)) FROM tool_runs),
 'db',(SELECT json_build_object('numbackends',numbackends,'xact_commit',xact_commit,'deadlocks',deadlocks,'temp_bytes',temp_bytes,'sessions',sessions,'sessions_abandoned',sessions_abandoned,'sessions_fatal',sessions_fatal,'sessions_killed',sessions_killed,'stats_reset',stats_reset) FROM pg_stat_database WHERE datname=current_database())
) AS j
