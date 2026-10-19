-- Per pipeline/dataset/day measurements aggregated from pipeline_events (filled by the loader).
CREATE TABLE IF NOT EXISTS dq_window_measurements (
  pipeline_id LowCardinality(String),
  dataset_id LowCardinality(String),
  window_day Date,
  row_count UInt64,
  uniq_records UInt64,
  null_count UInt64,
  amount_min Float64,
  amount_max Float64,
  amount_sum Float64,
  amount_sd Float64,
  amount_p95 Float64,
  last_event DateTime,
  active_hours UInt8,
  batches UInt8
) ENGINE = MergeTree
ORDER BY (pipeline_id, dataset_id, window_day);
