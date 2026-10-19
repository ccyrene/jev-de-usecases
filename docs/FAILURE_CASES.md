# Representative paired EN/TH failure cases (test split)

Twelve synthetic test scenarios, two per use case, sampled with seed 20261019 from two pools:
(a) Laya routed is right in English but wrong in Thai; (b) Jev 1.14.0 is wrong in at least one
language. `p` is the model's probability for its chosen label. Each note states which clue in the
provider-visible state decides the gold label and what each model answered; it makes no claim about
model internals. Full states: `data/generated/<case>/common_fit.jsonl`; predictions:
`results/runs/test_primary_{laya,jev}/predictions.jsonl`.

## E01 — pipeline failure triage (`failure_family`)

**E01-0171** · gold `dependency_failure` · tags code_switching, negation, distractor

| | Laya routed | Jev |
|---|---|---|
| EN | dependency_failure (p=1.00, english ckpt) ✔ | dependency_failure (p=1.00) ✔ |
| TH | connectivity (p=0.43, multilingual ckpt) ✘ | dependency_failure (p=1.00) ✔ |

Decisive clue (identical bytes in both languages): `ExternalDagFailedError: external task
product_catalog_sync.publish_complete is in failed state` in task `wait_for_upstream_feed`. Only the
`status`/`impact`/`context` prose differs by language. Laya answers correctly from the English state
and switches to `connectivity` when the same log is wrapped in Thai prose (routed to the multilingual
checkpoint).

**E01-0126** · gold `dependency_failure` · evidence `ambiguous` · tags distractor, negation

| | Laya routed | Jev |
|---|---|---|
| EN | dependency_failure (p=0.98) ✔ | connectivity (p=0.69) ✘ |
| TH | resource_exhaustion (p=0.57) ✘ | connectivity (p=0.71) ✘ |

The log has two lines: an earlier retried `socket.timeout ... connecting to feeds-db.example.org:5432`
and the terminal `AirflowSensorTimeout` of the `wait_for_upstream_feed` sensor. The injected fault is
an upstream sensor timeout (`dep_sensor_timeout`) and the socket line is a planted distractor, so gold
is `dependency_failure`; Jev answers from the socket timeout. Laya's
English answer is right, but it answers `dependency_failure` for almost every E01 incident
(macro-F1 0.04), so this is not evidence of reading the log.

## E02 — data quality (`anomaly_family`)

**E02-0096** · gold `distribution_shift`

| | Laya routed | Jev |
|---|---|---|
| EN | distribution_shift (p=0.22) ✔ | distribution_shift (p=0.99) ✔ |
| TH | freshness_lag (p=1.00) ✘ | distribution_shift (p=1.00) ✔ |

Clue: `rule=amount_mean_shift observed=22.925 threshold=0.5`, `amount_mean=1105.16
baseline_mean=90.63`, while volume, nulls, duplicates and freshness (`lag_min=33 sla_min=180`) are
normal. Laya's Thai answer contradicts the freshness line it was given, with full confidence.

**E02-0109** · gold `normal_or_unknown` · hard negative

| | Laya routed | Jev |
|---|---|---|
| EN | volume_drop (p=0.27) ✘ | volume_drop (p=0.91) ✘ |
| TH | freshness_lag (p=0.45) ✘ | volume_drop (p=0.87) ✘ |

The alert fired on the all-days mean (`vs_mean=0.44`), but the policy compares volume with the
same-weekday baseline (`history: basis=same_weekday`), and that ratio is `ratio=0.77`, above the 0.6
anomaly threshold. Both models follow the fired alert instead of the seasonal comparison.

## E03 — schema drift (`drift_impact`)

**E03-0279** · gold `non_breaking_addition`

| | Laya routed | Jev |
|---|---|---|
| EN | non_breaking_addition (p=0.41) ✔ | breaking_rename_drop (p=0.50) ✘ |
| TH | breaking_rename_drop (p=0.63) ✘ | breaking_rename_drop (p=0.75) ✘ |

`ALTER TABLE customers RENAME COLUMN legacy_segment TO legacy_segment_v2`, but the real
compatibility run shows `44 pass, 0 fail`. Gold is consumer-tested (a rename nobody reads is not
breaking), which is a policy choice documented in the E03 docstring; Jev labels by DDL verb. This is
the kind of label where the policy, not the model, decides.

**E03-0249** · gold `no_change_or_unknown` · evidence `insufficient` · hard negative

| | Laya routed | Jev |
|---|---|---|
| EN | non_breaking_addition (p=0.62) ✘ | non_breaking_addition (p=1.00) ✘ |
| TH | non_breaking_addition (p=0.68) ✘ | non_breaking_addition (p=0.99) ✘ |

`ADD COLUMN IF NOT EXISTS snapshot_id bigint` with `catalog_diff: No catalog changes detected` —
the column already existed, so nothing changed. Both models answer from the DDL text and ignore the
measured catalog diff; Jev does so with near-certainty.

## E04 — lineage (`impact_tier`)

**E04-0177** · gold `critical_downstream` · hard negative

| | Laya routed | Jev |
|---|---|---|
| EN | critical_downstream (p=0.32) ✔ | critical_downstream (p=1.00) ✔ |
| TH | none (p=0.39) ✘ | critical_downstream (p=1.00) ✔ |

The changed asset itself is `criticality=low`, but `graph_summary` reports 6 verified downstream
consumers, 3 of them high or critical. Laya's Thai answer `none` contradicts the summary count.

**E04-0091** · gold `unknown` · evidence `insufficient` · distractor

| | Laya routed | Jev |
|---|---|---|
| EN | single_noncritical (p=0.24) ✘ | multi_noncritical (p=0.99) ✘ |
| TH | none (p=0.99) ✘ | multi_noncritical (p=1.00) ✘ |

Two verified medium consumers, plus `graph_caveats: Pending (unverified) lineage edges, not counted: 1`.
The policy treats unverified edges as missing evidence (`unknown`); Jev counts the two verified
consumers and ignores the caveat.

## E05 — PII (`data_class`)

**E05-0243** · gold `sensitive_personal`

| | Laya routed | Jev |
|---|---|---|
| EN | sensitive_personal (p=0.35) ✔ | sensitive_personal (p=1.00) ✔ |
| TH | unknown (p=0.78) ✘ | sensitive_personal (p=1.00) ✔ |

Field `crm.customer.attrs:profile.faith`, purpose "Religion declared by the person" /
"ศาสนาที่บุคคลแจ้งไว้". Laya reads the English metadata but answers `unknown` for the Thai wording.

**E05-0009** · gold `quasi_identifier` · hard negative

| | Laya routed | Jev |
|---|---|---|
| EN | non_personal (p=0.43) ✘ | direct_identifier (p=0.74) ✘ |
| TH | unknown (p=0.59) ✘ | quasi_identifier (p=0.48) ✔ |

`actor.account_token`: "Token that stands in for the account number", issued by a token vault
another team operates. Per policy a vault token is linkable only with the vault (quasi). Jev's
English answer over-escalates to direct identifier (a safe-side error); its Thai answer is right at
p=0.48.

## E06 — NL→SQL candidate validation (`candidate_verdict`)

**E06-0250** · gold `semantically_correct` · distractor

| | Laya routed | Jev |
|---|---|---|
| EN | semantically_correct (p=0.47) ✔ | semantically_wrong (p=0.51) ✘ |
| TH | semantically_wrong (p=0.52) ✘ | semantically_correct (p=0.62) ✔ |

The candidate converts `return_ts + interval 7 hour` before taking the year, which is exactly what the
constraint requires ("bucketed by Asia/Bangkok return date"; UTC storage). It returns the same result
as the reference on all four DuckDB snapshots. Both models are near 0.5 and disagree across languages:
the decision is close to a coin flip for both.

**E06-0047** · gold `insufficient_specification` · evidence `ambiguous`

| | Laya routed | Jev |
|---|---|---|
| EN | semantically_correct (p=0.46) ✘ | semantically_wrong (p=0.82) ✘ |
| TH | insufficient_specification (p=0.84) ✔ | semantically_wrong (p=0.81) ✘ |

"How much stock do we have of Shoes products?" does not say which snapshot (latest, average, a date),
while the constraint states one row per product per Monday. The candidate averages across snapshots.
Gold flags the request as under-specified; Jev judges the SQL instead. Across the E06 test split Jev
never predicts `insufficient_specification` (0 of 102 such cases over both languages).
