-- Business context: tier, SLAs, dependent jobs and an optional operator note per dataset.
CREATE TABLE IF NOT EXISTS dataset_catalog (
  pipeline_id LowCardinality(String),
  dataset_id LowCardinality(String),
  tier LowCardinality(String),
  freshness_sla_min UInt32,
  completeness_sla Float64,
  downstream_critical UInt8,
  dependent_jobs Array(String),
  ops_note LowCardinality(String)
) ENGINE = MergeTree
ORDER BY (pipeline_id, dataset_id);
