-- Landing table: every delivered row with the amount still as text, used for type-mismatch counts.
CREATE TABLE IF NOT EXISTS pipeline_events_raw (
  event_time DateTime,
  pipeline_id LowCardinality(String),
  dataset_id LowCardinality(String),
  batch_id String,
  record_id UInt64,
  amount_raw String,
  customer_id Nullable(String),
  event_type LowCardinality(String)
) ENGINE = MergeTree
ORDER BY (pipeline_id, dataset_id, event_time, record_id);
