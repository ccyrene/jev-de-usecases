-- Raw synthetic events. Typed table; rows that fail type coercion are kept only in pipeline_events_raw.
CREATE TABLE IF NOT EXISTS pipeline_events (
  event_time DateTime,
  pipeline_id LowCardinality(String),
  dataset_id LowCardinality(String),
  batch_id String,
  record_id UInt64,
  amount Float64,
  customer_id Nullable(String),
  event_type LowCardinality(String)
) ENGINE = MergeTree
ORDER BY (pipeline_id, dataset_id, event_time, record_id);
