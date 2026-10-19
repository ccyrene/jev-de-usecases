-- Run after loading pipeline_events: daily per-dataset aggregates.
INSERT INTO dq_window_measurements
SELECT
  pipeline_id, dataset_id, toDate(event_time) AS window_day,
  count(), uniqExact(record_id), countIf(customer_id IS NULL),
  min(amount), max(amount), sum(amount), stddevPop(amount), quantileExactInclusive(0.95)(amount),
  max(event_time), uniqExact(toHour(event_time)), uniqExact(batch_id)
FROM pipeline_events
GROUP BY pipeline_id, dataset_id, window_day;
