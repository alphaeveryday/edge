-- SFN owns a slot until its ECS task has stopped; no time-based lease expiry.
CREATE TABLE analysis_execution_slots (
    execution_arn text PRIMARY KEY,
    started_by varchar(36) NOT NULL UNIQUE,
    task_arns text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, DELETE, UPDATE(task_arns) ON analysis_execution_slots TO edge_analysis_v2_writer;
