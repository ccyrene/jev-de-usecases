-- Known calendar effects (for example public holidays) with the expected volume factor.
CREATE TABLE IF NOT EXISTS business_calendar (
  pipeline_id LowCardinality(String),
  day Date,
  is_holiday UInt8,
  expected_factor Float64
) ENGINE = MergeTree
ORDER BY (pipeline_id, day);
