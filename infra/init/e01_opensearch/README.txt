Index `airflow-task-logs`: strict mapping in mapping.json (single shard, no replicas).
Fields: event_ts, dag_id, task_id, run_id, status, message (+ message.raw keyword), exception_type,
retry_count, duration_ms, upstream_task, deployment_id. No gold or fault ids are ever indexed.
Seeded deterministically by debench.dataset.e01.db_seed (>= 10,000 synthetic documents).
