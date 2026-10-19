# Final report — de-laya-jev-th-en-2026

**Status:** PILOT (dev split; not a final result)  
**Generated:** 2026-10-19T14:21:00+07:00  
**Analysis:** `results/analysis/pilot` from runs `pilot_laya`, `pilot_rules`

## Tracks

| track | status | provider | note |
|---|---|---|---|
| laya_en_fixed | OK | laya |  |
| laya_multi_fixed | OK | laya |  |
| laya_routed | OK | laya |  |
| rules_only | OK | rules |  |

## Environment

- Host: AMD Ryzen 7 5800H with Radeon Graphics (8 threads), RAM 5.79 GB, Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39
- GPU: NVIDIA GeForce RTX 3060 Laptop GPU (6.0 GB), torch CUDA 13.0
- Docker: not available (engines run natively/embedded; see manifests)
- Packages: laya 0.4.11, torch 2.14.1, transformers 5.20.1, httpx 0.28.1, duckdb 1.5.6, sqlglot 30.23.0, chdb 4.4.0, chdb-core 26.9.0, pixeltable-pgserver 0.6.0, psycopg 3.3.6, opensearch-py 3.2.0, neo4j 6.4.1, numpy 2.5.4, pandas 3.0.6, scipy 1.18.1, scikit-learn 1.9.1, pyarrow 26.0.0

## Datasets

| case | engine | records | gold_verified | pair_failures | splits | db_p95_ms |
|---|---|---|---|---|---|---|
| E01 | opensearch opensearch 3.9.0 (native_tarball) | 600 | 300/300 | 0 | {"test": 200, "calibration": 40, "dev": 60} | 32.482 |
| E02 | clickhouse 26.9.2.1 (embedded) | 600 | 300/300 | 0 | {"test": 200, "dev": 60, "calibration": 40} | 45.030 |
| E03 | postgres PostgreSQL 18.4 on x86_64-pc-linux-gnu, compiled by gcc (GCC) 14.2.1 20250110 (Red Hat 14.2.1-11), 64-bit (embedded) | 600 | 300/300 | 0 | {"dev": 60, "test": 200, "calibration": 40} | 56.501 |
| E04 | neo4j Neo4j Kernel 5.26.32 community (native_tarball) | 600 | 300/300 | 0 | {"test": 200, "dev": 60, "calibration": 40} | 30.343 |
| E05 | postgres PostgreSQL 18.4 on x86_64-pc-linux-gnu, compiled by gcc (GCC) 14.2.1 20250110 (Red Hat 14.2.1-11), 64-bit (embedded) | 600 | 300/300 | 0 | {"test": 200, "dev": 60, "calibration": 40} | 17.478 |
| E06 | duckdb v1.5.6 (embedded) | 600 | 300/300 | 0 | {"test": 201, "dev": 59, "calibration": 40} | 82.333 |

## Verdicts

| case | title | verdict | basis |
|---|---|---|---|
| E01 | Pipeline failure triage (OpenSearch) | Not executed | Jev track NOT_RUN: jev_114 not run |
| E02 | Data quality monitoring (ClickHouse) | Not executed | Jev track NOT_RUN: jev_114 not run |
| E03 | Schema drift detection (PostgreSQL) | Not executed | Jev track NOT_RUN: jev_114 not run |
| E04 | Lineage impact analysis (Neo4j) | Not executed | Jev track NOT_RUN: jev_114 not run |
| E05 | PII classification (PostgreSQL + JSONB) | Not executed | Jev track NOT_RUN: jev_114 not run |
| E06 | NL-to-SQL candidate validation (DuckDB) | Not executed | Jev track NOT_RUN: jev_114 not run |

## Table 1 — quality by model, case, language and question

Accuracy and macro-F1 use the attempted denominator (invalid = wrong); CIs are 95% scenario-clustered bootstrap (2,000 resamples, seed 20261019). Probability metrics use valid answers only.


| experiment_id | model_track | variant | question_id | primitive | n_attempted | coverage | truncation_rate | eff_acc [95% CI] | macro_f1 [95% CI] | brier | ece_equal_mass | mae_expected | auroc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E01 | laya_en_fixed | en_en | failure_family | choice | 20 | 1.000 | 0.000 | 0.150 [0.000, 0.350] | 0.063 [0.000, 0.131] | 1.357 | 0.710 | – | – |
| E01 | laya_en_fixed | en_en | requires_human | noul | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | 0.320 | 0.449 | – | 0.490 |
| E01 | laya_en_fixed | en_en | severity | score | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | – | – | 0.720 | – |
| E01 | laya_en_fixed | th_th | failure_family | choice | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | 0.026 [0.000, 0.057] | 1.315 | 0.671 | – | – |
| E01 | laya_en_fixed | th_th | requires_human | noul | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.600] | – | 0.345 | 0.446 | – | 0.460 |
| E01 | laya_en_fixed | th_th | severity | score | 20 | 1.000 | 0.000 | 0.250 [0.050, 0.450] | – | – | – | 0.935 | – |
| E02 | laya_en_fixed | en_en | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.500 [0.300, 0.700] | 0.576 [0.273, 0.707] | 0.589 | 0.259 | – | – |
| E02 | laya_en_fixed | en_en | investigate_now | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.236 | 0.367 | – | 0.417 |
| E02 | laya_en_fixed | en_en | quality_impact | score | 20 | 1.000 | 0.000 | 0.150 [0.000, 0.300] | – | – | – | 1.041 | – |
| E02 | laya_en_fixed | th_th | anomaly_family | choice | 20 | 1.000 | 1.000 | 0.250 [0.100, 0.450] | 0.275 [0.087, 0.324] | 0.737 | 0.265 | – | – |
| E02 | laya_en_fixed | th_th | investigate_now | noul | 20 | 1.000 | 1.000 | 0.200 [0.050, 0.400] | – | 0.456 | 0.563 | – | 0.286 |
| E02 | laya_en_fixed | th_th | quality_impact | score | 20 | 1.000 | 1.000 | 0.100 [0.000, 0.250] | – | – | – | 1.085 | – |
| E03 | laya_en_fixed | en_en | drift_impact | choice | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | 0.585 [0.309, 0.733] | 0.522 | 0.392 | – | – |
| E03 | laya_en_fixed | en_en | migration_risk | score | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.700] | – | – | – | 0.998 | – |
| E03 | laya_en_fixed | en_en | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | – | 0.465 | 0.613 | – | 0.719 |
| E03 | laya_en_fixed | th_th | drift_impact | choice | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | 0.148 [0.018, 0.227] | 0.894 | 0.384 | – | – |
| E03 | laya_en_fixed | th_th | migration_risk | score | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | – | – | – | 1.392 | – |
| E03 | laya_en_fixed | th_th | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.800 [0.600, 0.950] | – | 0.129 | 0.237 | – | 0.797 |
| E04 | laya_en_fixed | en_en | impact_severity | score | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | – | – | 0.851 | – |
| E04 | laya_en_fixed | en_en | impact_tier | choice | 20 | 1.000 | 0.000 | 0.400 [0.200, 0.650] | 0.368 [0.169, 0.529] | 0.733 | 0.377 | – | – |
| E04 | laya_en_fixed | en_en | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | 0.303 | 0.483 | – | 0.333 |
| E04 | laya_en_fixed | th_th | impact_severity | score | 20 | 1.000 | 0.700 | 0.350 [0.150, 0.550] | – | – | – | 0.872 | – |
| E04 | laya_en_fixed | th_th | impact_tier | choice | 20 | 1.000 | 0.700 | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 1.049 | 0.418 | – | – |
| E04 | laya_en_fixed | th_th | needs_owner_escalation | noul | 20 | 1.000 | 0.700 | 0.550 [0.350, 0.750] | – | 0.370 | 0.411 | – | 0.227 |
| E05 | laya_en_fixed | en_en | data_class | choice | 20 | 1.000 | 0.000 | 0.550 [0.300, 0.750] | 0.305 [0.129, 0.363] | 0.531 | 0.292 | – | – |
| E05 | laya_en_fixed | en_en | handling_tier | score | 20 | 1.000 | 0.000 | 0.150 [0.000, 0.301] | – | – | – | 0.631 | – |
| E05 | laya_en_fixed | en_en | requires_masking_review | noul | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | – | 0.650 | 0.679 | – | 0.411 |
| E05 | laya_en_fixed | th_th | data_class | choice | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | 0.160 [0.021, 0.280] | 0.797 | 0.340 | – | – |
| E05 | laya_en_fixed | th_th | handling_tier | score | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | – | – | – | 0.653 | – |
| E05 | laya_en_fixed | th_th | requires_masking_review | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.451 | 0.555 | – | 0.548 |
| E06 | laya_en_fixed | en_en | candidate_quality | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.417 | – |
| E06 | laya_en_fixed | en_en | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.208 | 0.361 | – | 0.726 |
| E06 | laya_en_fixed | en_en | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.114 [0.050, 0.173] | 0.778 | 0.398 | – | – |
| E06 | laya_en_fixed | en_en | intent_type | choice | 20 | 1.000 | 0.000 | 0.500 [0.300, 0.701] | 0.420 [0.232, 0.559] | 0.709 | 0.378 | – | – |
| E06 | laya_en_fixed | en_en | needs_clarification | noul | 20 | 1.000 | 0.000 | 0.400 [0.200, 0.600] | – | 0.416 | 0.495 | – | 0.222 |
| E06 | laya_en_fixed | th_th | candidate_quality | score | 20 | 1.000 | 0.800 | 0.250 [0.050, 0.450] | – | – | – | 1.271 | – |
| E06 | laya_en_fixed | th_th | candidate_safe_to_consider | noul | 20 | 1.000 | 0.800 | 0.650 [0.450, 0.850] | – | 0.261 | 0.297 | – | 0.500 |
| E06 | laya_en_fixed | th_th | candidate_verdict | choice | 20 | 1.000 | 0.800 | 0.250 [0.100, 0.450] | 0.163 [0.059, 0.257] | 0.756 | 0.381 | – | – |
| E06 | laya_en_fixed | th_th | intent_type | choice | 20 | 1.000 | 0.800 | 0.200 [0.050, 0.400] | 0.111 [0.021, 0.189] | 0.862 | 0.324 | – | – |
| E06 | laya_en_fixed | th_th | needs_clarification | noul | 20 | 1.000 | 0.800 | 0.400 [0.200, 0.600] | – | 0.351 | 0.500 | – | 0.250 |
| E01 | laya_multi_fixed | en_en | failure_family | choice | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | 0.100 [0.020, 0.163] | 1.010 | 0.466 | – | – |
| E01 | laya_multi_fixed | en_en | requires_human | noul | 20 | 1.000 | 0.000 | 0.550 [0.300, 0.800] | – | 0.359 | 0.420 | – | 0.460 |
| E01 | laya_multi_fixed | en_en | severity | score | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | – | – | – | 0.935 | – |
| E01 | laya_multi_fixed | th_th | failure_family | choice | 20 | 1.000 | 0.000 | 0.450 [0.200, 0.650] | 0.314 [0.130, 0.455] | 0.798 | 0.406 | – | – |
| E01 | laya_multi_fixed | th_th | requires_human | noul | 20 | 1.000 | 0.000 | 0.600 [0.400, 0.800] | – | 0.370 | 0.414 | – | 0.500 |
| E01 | laya_multi_fixed | th_th | severity | score | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | – | – | – | 0.889 | – |
| E02 | laya_multi_fixed | en_en | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | 0.219 [0.065, 0.301] | 0.898 | 0.509 | – | – |
| E02 | laya_multi_fixed | en_en | investigate_now | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.697 | 0.697 | – | 0.310 |
| E02 | laya_multi_fixed | en_en | quality_impact | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.124 | – |
| E02 | laya_multi_fixed | th_th | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.316 [0.037, 0.346] | 1.222 | 0.552 | – | – |
| E02 | laya_multi_fixed | th_th | investigate_now | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.700 | 0.700 | – | 0.679 |
| E02 | laya_multi_fixed | th_th | quality_impact | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.195 | – |
| E03 | laya_multi_fixed | en_en | drift_impact | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.188 [0.042, 0.294] | 1.177 | 0.594 | – | – |
| E03 | laya_multi_fixed | en_en | migration_risk | score | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | – | – | – | 1.301 | – |
| E03 | laya_multi_fixed | en_en | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.800 [0.600, 0.950] | – | 0.170 | 0.269 | – | 0.500 |
| E03 | laya_multi_fixed | th_th | drift_impact | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.135 [0.058, 0.198] | 1.052 | 0.521 | – | – |
| E03 | laya_multi_fixed | th_th | migration_risk | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.146 | – |
| E03 | laya_multi_fixed | th_th | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.750 [0.550, 0.950] | – | 0.168 | 0.249 | – | 0.586 |
| E04 | laya_multi_fixed | en_en | impact_severity | score | 20 | 1.000 | 0.000 | 0.400 [0.200, 0.600] | – | – | – | 0.907 | – |
| E04 | laya_multi_fixed | en_en | impact_tier | choice | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | 0.248 [0.100, 0.367] | 0.762 | 0.380 | – | – |
| E04 | laya_multi_fixed | en_en | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 0.550 [0.350, 0.750] | – | 0.284 | 0.367 | – | 0.758 |
| E04 | laya_multi_fixed | th_th | impact_severity | score | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | – | – | – | 0.963 | – |
| E04 | laya_multi_fixed | th_th | impact_tier | choice | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | 0.266 [0.099, 0.390] | 0.877 | 0.423 | – | – |
| E04 | laya_multi_fixed | th_th | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 0.550 [0.300, 0.750] | – | 0.296 | 0.356 | – | 0.657 |
| E05 | laya_multi_fixed | en_en | data_class | choice | 20 | 1.000 | 0.000 | 0.250 [0.050, 0.450] | 0.278 [0.080, 0.400] | 0.896 | 0.420 | – | – |
| E05 | laya_multi_fixed | en_en | handling_tier | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 0.859 | – |
| E05 | laya_multi_fixed | en_en | requires_masking_review | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.556 | 0.573 | – | 0.500 |
| E05 | laya_multi_fixed | th_th | data_class | choice | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | 0.089 [0.000, 0.160] | 1.192 | 0.591 | – | – |
| E05 | laya_multi_fixed | th_th | handling_tier | score | 20 | 1.000 | 0.000 | 0.550 [0.350, 0.750] | – | – | – | 0.884 | – |
| E05 | laya_multi_fixed | th_th | requires_masking_review | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.663 | 0.679 | – | 0.655 |
| E06 | laya_multi_fixed | en_en | candidate_quality | score | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | – | – | – | 1.696 | – |
| E06 | laya_multi_fixed | en_en | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.280 | 0.332 | – | 0.393 |
| E06 | laya_multi_fixed | en_en | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.146 [0.045, 0.265] | 0.846 | 0.447 | – | – |
| E06 | laya_multi_fixed | en_en | intent_type | choice | 20 | 1.000 | 0.000 | 0.550 [0.350, 0.751] | 0.500 [0.294, 0.633] | 0.604 | 0.246 | – | – |
| E06 | laya_multi_fixed | en_en | needs_clarification | noul | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | – | 0.732 | 0.804 | – | 0.389 |
| E06 | laya_multi_fixed | th_th | candidate_quality | score | 20 | 1.000 | 0.000 | 0.250 [0.050, 0.450] | – | – | – | 1.721 | – |
| E06 | laya_multi_fixed | th_th | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.282 | 0.307 | – | 0.440 |
| E06 | laya_multi_fixed | th_th | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | 0.083 [0.024, 0.143] | 0.924 | 0.383 | – | – |
| E06 | laya_multi_fixed | th_th | intent_type | choice | 20 | 1.000 | 0.000 | 0.550 [0.350, 0.800] | 0.511 [0.269, 0.673] | 0.611 | 0.294 | – | – |
| E06 | laya_multi_fixed | th_th | needs_clarification | noul | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | – | 0.812 | 0.849 | – | 0.528 |
| E01 | laya_routed | en_en | failure_family | choice | 20 | 1.000 | 0.000 | 0.150 [0.000, 0.350] | 0.063 [0.000, 0.131] | 1.357 | 0.710 | – | – |
| E01 | laya_routed | en_en | requires_human | noul | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | 0.320 | 0.449 | – | 0.490 |
| E01 | laya_routed | en_en | severity | score | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | – | – | 0.720 | – |
| E01 | laya_routed | th_th | failure_family | choice | 20 | 1.000 | 0.000 | 0.450 [0.200, 0.650] | 0.314 [0.130, 0.455] | 0.798 | 0.406 | – | – |
| E01 | laya_routed | th_th | requires_human | noul | 20 | 1.000 | 0.000 | 0.600 [0.400, 0.800] | – | 0.370 | 0.414 | – | 0.500 |
| E01 | laya_routed | th_th | severity | score | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | – | – | – | 0.889 | – |
| E02 | laya_routed | en_en | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.500 [0.300, 0.700] | 0.576 [0.273, 0.707] | 0.589 | 0.259 | – | – |
| E02 | laya_routed | en_en | investigate_now | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.236 | 0.367 | – | 0.417 |
| E02 | laya_routed | en_en | quality_impact | score | 20 | 1.000 | 0.000 | 0.150 [0.000, 0.300] | – | – | – | 1.041 | – |
| E02 | laya_routed | th_th | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.316 [0.037, 0.346] | 1.222 | 0.552 | – | – |
| E02 | laya_routed | th_th | investigate_now | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.700 | 0.700 | – | 0.679 |
| E02 | laya_routed | th_th | quality_impact | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.195 | – |
| E03 | laya_routed | en_en | drift_impact | choice | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | 0.585 [0.309, 0.733] | 0.522 | 0.392 | – | – |
| E03 | laya_routed | en_en | migration_risk | score | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.700] | – | – | – | 0.998 | – |
| E03 | laya_routed | en_en | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | – | 0.465 | 0.613 | – | 0.719 |
| E03 | laya_routed | th_th | drift_impact | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.135 [0.058, 0.198] | 1.052 | 0.521 | – | – |
| E03 | laya_routed | th_th | migration_risk | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.146 | – |
| E03 | laya_routed | th_th | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.750 [0.550, 0.950] | – | 0.168 | 0.249 | – | 0.586 |
| E04 | laya_routed | en_en | impact_severity | score | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | – | – | 0.851 | – |
| E04 | laya_routed | en_en | impact_tier | choice | 20 | 1.000 | 0.000 | 0.400 [0.200, 0.650] | 0.368 [0.169, 0.529] | 0.733 | 0.377 | – | – |
| E04 | laya_routed | en_en | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 0.450 [0.250, 0.650] | – | 0.303 | 0.483 | – | 0.333 |
| E04 | laya_routed | th_th | impact_severity | score | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | – | – | – | 0.963 | – |
| E04 | laya_routed | th_th | impact_tier | choice | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | 0.266 [0.099, 0.390] | 0.877 | 0.423 | – | – |
| E04 | laya_routed | th_th | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 0.550 [0.300, 0.750] | – | 0.296 | 0.356 | – | 0.657 |
| E05 | laya_routed | en_en | data_class | choice | 20 | 1.000 | 0.000 | 0.550 [0.300, 0.750] | 0.305 [0.129, 0.363] | 0.499 | 0.281 | – | – |
| E05 | laya_routed | en_en | handling_tier | score | 20 | 1.000 | 0.000 | 0.150 [0.000, 0.301] | – | – | – | 0.673 | – |
| E05 | laya_routed | en_en | requires_masking_review | noul | 20 | 1.000 | 0.000 | 0.350 [0.150, 0.550] | – | 0.591 | 0.585 | – | 0.500 |
| E05 | laya_routed | th_th | data_class | choice | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | 0.089 [0.000, 0.160] | 1.192 | 0.591 | – | – |
| E05 | laya_routed | th_th | handling_tier | score | 20 | 1.000 | 0.000 | 0.550 [0.350, 0.750] | – | – | – | 0.884 | – |
| E05 | laya_routed | th_th | requires_masking_review | noul | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | 0.663 | 0.679 | – | 0.655 |
| E06 | laya_routed | en_en | candidate_quality | score | 20 | 1.000 | 0.000 | 0.300 [0.100, 0.500] | – | – | – | 1.417 | – |
| E06 | laya_routed | en_en | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.208 | 0.361 | – | 0.726 |
| E06 | laya_routed | en_en | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.250 [0.100, 0.450] | 0.114 [0.050, 0.173] | 0.778 | 0.398 | – | – |
| E06 | laya_routed | en_en | intent_type | choice | 20 | 1.000 | 0.000 | 0.500 [0.300, 0.701] | 0.420 [0.232, 0.559] | 0.709 | 0.378 | – | – |
| E06 | laya_routed | en_en | needs_clarification | noul | 20 | 1.000 | 0.000 | 0.400 [0.200, 0.600] | – | 0.416 | 0.495 | – | 0.222 |
| E06 | laya_routed | th_th | candidate_quality | score | 20 | 1.000 | 0.000 | 0.250 [0.050, 0.450] | – | – | – | 1.721 | – |
| E06 | laya_routed | th_th | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 0.700 [0.500, 0.900] | – | 0.282 | 0.307 | – | 0.440 |
| E06 | laya_routed | th_th | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.200 [0.050, 0.400] | 0.083 [0.024, 0.143] | 0.924 | 0.383 | – | – |
| E06 | laya_routed | th_th | intent_type | choice | 20 | 1.000 | 0.000 | 0.550 [0.350, 0.800] | 0.511 [0.269, 0.673] | 0.611 | 0.294 | – | – |
| E06 | laya_routed | th_th | needs_clarification | noul | 20 | 1.000 | 0.000 | 0.100 [0.000, 0.250] | – | 0.812 | 0.849 | – | 0.528 |
| E01 | rules_only | en_en | failure_family | choice | 20 | 0.900 | 0.000 | 0.900 [0.750, 1.000] | 0.857 [0.429, 0.857] | – | – | – | – |
| E01 | rules_only | en_en | requires_human | noul | 20 | 0.900 | 0.000 | 0.900 [0.750, 1.000] | – | – | – | – | – |
| E01 | rules_only | en_en | severity | score | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E01 | rules_only | th_th | failure_family | choice | 20 | 0.900 | 0.000 | 0.900 [0.750, 1.000] | 0.857 [0.429, 0.857] | – | – | – | – |
| E01 | rules_only | th_th | requires_human | noul | 20 | 0.900 | 0.000 | 0.900 [0.750, 1.000] | – | – | – | – | – |
| E01 | rules_only | th_th | severity | score | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E02 | rules_only | en_en | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.950 [0.850, 1.000] | 0.943 [0.563, 1.000] | – | – | – | – |
| E02 | rules_only | en_en | investigate_now | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E02 | rules_only | en_en | quality_impact | score | 20 | 1.000 | 0.000 | 0.950 [0.850, 1.000] | – | – | – | – | – |
| E02 | rules_only | th_th | anomaly_family | choice | 20 | 1.000 | 0.000 | 0.950 [0.850, 1.000] | 0.943 [0.563, 1.000] | – | – | – | – |
| E02 | rules_only | th_th | investigate_now | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E02 | rules_only | th_th | quality_impact | score | 20 | 1.000 | 0.000 | 0.950 [0.850, 1.000] | – | – | – | – | – |
| E03 | rules_only | en_en | drift_impact | choice | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [0.667, 1.000] | – | – | – | – |
| E03 | rules_only | en_en | migration_risk | score | 20 | 1.000 | 0.000 | 0.800 [0.600, 0.950] | – | – | – | – | – |
| E03 | rules_only | en_en | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.900 [0.750, 1.000] | – | – | – | – | – |
| E03 | rules_only | th_th | drift_impact | choice | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [0.667, 1.000] | – | – | – | – |
| E03 | rules_only | th_th | migration_risk | score | 20 | 1.000 | 0.000 | 0.800 [0.600, 0.950] | – | – | – | – | – |
| E03 | rules_only | th_th | review_before_deploy | noul | 20 | 1.000 | 0.000 | 0.900 [0.750, 1.000] | – | – | – | – | – |
| E04 | rules_only | en_en | impact_severity | score | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E04 | rules_only | en_en | impact_tier | choice | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [0.800, 1.000] | – | – | – | – |
| E04 | rules_only | en_en | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E04 | rules_only | th_th | impact_severity | score | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E04 | rules_only | th_th | impact_tier | choice | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [0.800, 1.000] | – | – | – | – |
| E04 | rules_only | th_th | needs_owner_escalation | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E05 | rules_only | en_en | data_class | choice | 20 | 1.000 | 0.000 | 0.900 [0.750, 1.000] | 0.743 [0.519, 0.800] | – | – | – | – |
| E05 | rules_only | en_en | handling_tier | score | 20 | 1.000 | 0.000 | 0.950 [0.850, 1.000] | – | – | – | – | – |
| E05 | rules_only | en_en | requires_masking_review | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E05 | rules_only | th_th | data_class | choice | 20 | 1.000 | 0.000 | 0.900 [0.750, 1.000] | 0.743 [0.519, 0.800] | – | – | – | – |
| E05 | rules_only | th_th | handling_tier | score | 20 | 1.000 | 0.000 | 0.950 [0.850, 1.000] | – | – | – | – | – |
| E05 | rules_only | th_th | requires_masking_review | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | en_en | candidate_quality | score | 20 | 1.000 | 0.000 | 0.650 [0.450, 0.850] | – | – | – | – | – |
| E06 | rules_only | en_en | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | en_en | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.750 [0.550, 0.900] | 0.778 [0.454, 0.910] | – | – | – | – |
| E06 | rules_only | en_en | intent_type | choice | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [0.829, 1.000] | – | – | – | – |
| E06 | rules_only | en_en | needs_clarification | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | th_th | candidate_quality | score | 20 | 1.000 | 0.000 | 0.650 [0.450, 0.850] | – | – | – | – | – |
| E06 | rules_only | th_th | candidate_safe_to_consider | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | th_th | candidate_verdict | choice | 20 | 1.000 | 0.000 | 0.750 [0.550, 0.900] | 0.778 [0.454, 0.910] | – | – | – | – |
| E06 | rules_only | th_th | intent_type | choice | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [0.829, 1.000] | – | – | – | – |
| E06 | rules_only | th_th | needs_clarification | noul | 20 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |

## Table 2 — paired Laya − Jev deltas

_Not executed: no scenarios were answered by both laya_routed and jev_114._

## Table 3 — language gap within each model (TH − EN, paired)

| model_track | experiment_id | question_id | n_scenarios | a | b | delta | ci_low | ci_high |
|---|---|---|---|---|---|---|---|---|
| laya_en_fixed | E01 | failure_family | 20 | 0.100 | 0.150 | -0.050 | -0.150 | 0.000 |
| laya_en_fixed | E01 | requires_human | 20 | 0.350 | 0.450 | -0.100 | -0.350 | 0.200 |
| laya_en_fixed | E01 | severity | 20 | 0.250 | 0.450 | -0.200 | -0.450 | 0.001 |
| laya_en_fixed | E02 | anomaly_family | 20 | 0.250 | 0.500 | -0.250 | -0.500 | 0.000 |
| laya_en_fixed | E02 | investigate_now | 20 | 0.200 | 0.700 | -0.500 | -0.800 | -0.200 |
| laya_en_fixed | E02 | quality_impact | 20 | 0.100 | 0.150 | -0.050 | -0.250 | 0.150 |
| laya_en_fixed | E03 | drift_impact | 20 | 0.200 | 0.700 | -0.500 | -0.800 | -0.200 |
| laya_en_fixed | E03 | migration_risk | 20 | 0.100 | 0.450 | -0.350 | -0.650 | -0.050 |
| laya_en_fixed | E03 | review_before_deploy | 20 | 0.800 | 0.200 | 0.600 | 0.200 | 0.900 |
| laya_en_fixed | E04 | impact_severity | 20 | 0.350 | 0.450 | -0.100 | -0.350 | 0.150 |
| laya_en_fixed | E04 | impact_tier | 20 | 0.000 | 0.400 | -0.400 | -0.650 | -0.200 |
| laya_en_fixed | E04 | needs_owner_escalation | 20 | 0.550 | 0.450 | 0.100 | -0.100 | 0.300 |
| laya_en_fixed | E05 | data_class | 20 | 0.200 | 0.550 | -0.350 | -0.600 | -0.100 |
| laya_en_fixed | E05 | handling_tier | 20 | 0.350 | 0.150 | 0.200 | 0.000 | 0.400 |
| laya_en_fixed | E05 | requires_masking_review | 20 | 0.300 | 0.350 | -0.050 | -0.300 | 0.200 |
| laya_en_fixed | E06 | candidate_quality | 20 | 0.250 | 0.300 | -0.050 | -0.400 | 0.250 |
| laya_en_fixed | E06 | candidate_safe_to_consider | 20 | 0.650 | 0.700 | -0.050 | -0.250 | 0.150 |
| laya_en_fixed | E06 | candidate_verdict | 20 | 0.250 | 0.250 | 0.000 | -0.150 | 0.150 |
| laya_en_fixed | E06 | intent_type | 20 | 0.200 | 0.500 | -0.300 | -0.500 | -0.100 |
| laya_en_fixed | E06 | needs_clarification | 20 | 0.400 | 0.400 | 0.000 | -0.350 | 0.350 |
| laya_multi_fixed | E01 | failure_family | 20 | 0.450 | 0.200 | 0.250 | 0.050 | 0.450 |
| laya_multi_fixed | E01 | requires_human | 20 | 0.600 | 0.550 | 0.050 | -0.150 | 0.250 |
| laya_multi_fixed | E01 | severity | 20 | 0.200 | 0.200 | 0.000 | 0.000 | 0.000 |
| laya_multi_fixed | E02 | anomaly_family | 20 | 0.250 | 0.350 | -0.100 | -0.400 | 0.200 |
| laya_multi_fixed | E02 | investigate_now | 20 | 0.300 | 0.300 | 0.000 | 0.000 | 0.000 |
| laya_multi_fixed | E02 | quality_impact | 20 | 0.300 | 0.300 | 0.000 | 0.000 | 0.000 |
| laya_multi_fixed | E03 | drift_impact | 20 | 0.250 | 0.250 | 0.000 | -0.250 | 0.250 |
| laya_multi_fixed | E03 | migration_risk | 20 | 0.300 | 0.100 | 0.200 | -0.050 | 0.450 |
| laya_multi_fixed | E03 | review_before_deploy | 20 | 0.750 | 0.800 | -0.050 | -0.201 | 0.100 |
| laya_multi_fixed | E04 | impact_severity | 20 | 0.350 | 0.400 | -0.050 | -0.250 | 0.100 |
| laya_multi_fixed | E04 | impact_tier | 20 | 0.350 | 0.350 | 0.000 | -0.200 | 0.200 |
| laya_multi_fixed | E04 | needs_owner_escalation | 20 | 0.550 | 0.550 | 0.000 | -0.200 | 0.200 |
| laya_multi_fixed | E05 | data_class | 20 | 0.100 | 0.250 | -0.150 | -0.400 | 0.100 |
| laya_multi_fixed | E05 | handling_tier | 20 | 0.550 | 0.300 | 0.250 | 0.000 | 0.500 |
| laya_multi_fixed | E05 | requires_masking_review | 20 | 0.300 | 0.300 | 0.000 | 0.000 | 0.000 |
| laya_multi_fixed | E06 | candidate_quality | 20 | 0.250 | 0.200 | 0.050 | -0.150 | 0.250 |
| laya_multi_fixed | E06 | candidate_safe_to_consider | 20 | 0.700 | 0.700 | 0.000 | 0.000 | 0.000 |
| laya_multi_fixed | E06 | candidate_verdict | 20 | 0.200 | 0.250 | -0.050 | -0.250 | 0.100 |
| laya_multi_fixed | E06 | intent_type | 20 | 0.550 | 0.550 | 0.000 | -0.300 | 0.300 |
| laya_multi_fixed | E06 | needs_clarification | 20 | 0.100 | 0.100 | 0.000 | 0.000 | 0.000 |
| laya_routed | E01 | failure_family | 20 | 0.450 | 0.150 | 0.300 | 0.100 | 0.500 |
| laya_routed | E01 | requires_human | 20 | 0.600 | 0.450 | 0.150 | -0.250 | 0.500 |
| laya_routed | E01 | severity | 20 | 0.200 | 0.450 | -0.250 | -0.450 | -0.050 |
| laya_routed | E02 | anomaly_family | 20 | 0.250 | 0.500 | -0.250 | -0.500 | 0.000 |
| laya_routed | E02 | investigate_now | 20 | 0.300 | 0.700 | -0.400 | -0.800 | 0.000 |
| laya_routed | E02 | quality_impact | 20 | 0.300 | 0.150 | 0.150 | -0.150 | 0.450 |
| laya_routed | E03 | drift_impact | 20 | 0.250 | 0.700 | -0.450 | -0.650 | -0.250 |
| laya_routed | E03 | migration_risk | 20 | 0.300 | 0.450 | -0.150 | -0.500 | 0.201 |
| laya_routed | E03 | review_before_deploy | 20 | 0.750 | 0.200 | 0.550 | 0.200 | 0.900 |
| laya_routed | E04 | impact_severity | 20 | 0.350 | 0.450 | -0.100 | -0.250 | 0.000 |
| laya_routed | E04 | impact_tier | 20 | 0.350 | 0.400 | -0.050 | -0.300 | 0.150 |
| laya_routed | E04 | needs_owner_escalation | 20 | 0.550 | 0.450 | 0.100 | -0.200 | 0.350 |
| laya_routed | E05 | data_class | 20 | 0.100 | 0.550 | -0.450 | -0.750 | -0.150 |
| laya_routed | E05 | handling_tier | 20 | 0.550 | 0.150 | 0.400 | 0.100 | 0.700 |
| laya_routed | E05 | requires_masking_review | 20 | 0.300 | 0.350 | -0.050 | -0.150 | 0.000 |
| laya_routed | E06 | candidate_quality | 20 | 0.250 | 0.300 | -0.050 | -0.250 | 0.150 |
| laya_routed | E06 | candidate_safe_to_consider | 20 | 0.700 | 0.700 | 0.000 | -0.300 | 0.250 |
| laya_routed | E06 | candidate_verdict | 20 | 0.200 | 0.250 | -0.050 | -0.150 | 0.000 |
| laya_routed | E06 | intent_type | 20 | 0.550 | 0.500 | 0.050 | -0.200 | 0.300 |
| laya_routed | E06 | needs_clarification | 20 | 0.100 | 0.400 | -0.300 | -0.550 | -0.050 |
| rules_only | E01 | failure_family | 20 | 0.900 | 0.900 | 0.000 | 0.000 | 0.000 |
| rules_only | E01 | requires_human | 20 | 0.900 | 0.900 | 0.000 | 0.000 | 0.000 |
| rules_only | E01 | severity | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E02 | anomaly_family | 20 | 0.950 | 0.950 | 0.000 | 0.000 | 0.000 |
| rules_only | E02 | investigate_now | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E02 | quality_impact | 20 | 0.950 | 0.950 | 0.000 | 0.000 | 0.000 |
| rules_only | E03 | drift_impact | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E03 | migration_risk | 20 | 0.800 | 0.800 | 0.000 | 0.000 | 0.000 |
| rules_only | E03 | review_before_deploy | 20 | 0.900 | 0.900 | 0.000 | 0.000 | 0.000 |
| rules_only | E04 | impact_severity | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E04 | impact_tier | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E04 | needs_owner_escalation | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E05 | data_class | 20 | 0.900 | 0.900 | 0.000 | 0.000 | 0.000 |
| rules_only | E05 | handling_tier | 20 | 0.950 | 0.950 | 0.000 | 0.000 | 0.000 |
| rules_only | E05 | requires_masking_review | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | candidate_quality | 20 | 0.650 | 0.650 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | candidate_safe_to_consider | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | candidate_verdict | 20 | 0.750 | 0.750 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | intent_type | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | needs_clarification | 20 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |

## Table 5 — model latency (warm, per request)

Local Laya is in-process inference on this host; it is not comparable to remote HTTPS latency.


| model_track | device | variant | n_requests | p50_ms | p95_ms | p99_ms | db_retrieval_p50_ms | topology |
|---|---|---|---|---|---|---|---|---|
| laya_en_fixed | cuda | en_en | 120 | 104.0 | 161.4 | 183.2 | 26.2 | in_process_local |
| laya_en_fixed | cuda | th_th | 120 | 141.6 | 210.9 | 246.3 | 26.2 | in_process_local |
| laya_multi_fixed | cuda | en_en | 120 | 51.6 | 78.4 | 89.2 | 26.2 | in_process_local |
| laya_multi_fixed | cuda | th_th | 120 | 58.4 | 98.1 | 130.7 | 26.2 | in_process_local |
| laya_routed | cuda | en_en | 120 | 132.1 | 234.3 | 267.4 | 26.2 | in_process_local |
| laya_routed | cuda | th_th | 120 | 92.6 | 174.6 | 196.1 | 26.2 | in_process_local |

## Table 6 — cost

| model_track | basis | requests | sustained_decisions_per_hour | usd_per_1000_scenario_decisions |
|---|---|---|---|---|
| laya_en_fixed | ASSUMED host price 0.5 USD/h, sequential warm throughput | 240 | 28134.0698 | 0.0178 |
| laya_en_fixed | ASSUMED host price 1.0 USD/h, sequential warm throughput | 240 | 28134.0698 | 0.0355 |
| laya_multi_fixed | ASSUMED host price 0.5 USD/h, sequential warm throughput | 240 | 60153.7943 | 0.0083 |
| laya_multi_fixed | ASSUMED host price 1.0 USD/h, sequential warm throughput | 240 | 60153.7943 | 0.0166 |
| laya_routed | ASSUMED host price 0.5 USD/h, sequential warm throughput | 240 | 30614.4215 | 0.0163 |
| laya_routed | ASSUMED host price 1.0 USD/h, sequential warm throughput | 240 | 30614.4215 | 0.0327 |

## Table 7 — high-risk failures

| experiment_id | question_id | model_track | variant | definition | n_positive | misses | miss_rate | invalid_on_positive | upper_95_if_zero |
|---|---|---|---|---|---|---|---|---|---|
| E01 | severity | laya_en_fixed | en_en | severe incident (gold >= 2) predicted < 2 | 15 | 10 | 0.667 | 0 | – |
| E01 | severity | laya_en_fixed | th_th | severe incident (gold >= 2) predicted < 2 | 15 | 14 | 0.933 | 0 | – |
| E01 | severity | laya_multi_fixed | en_en | severe incident (gold >= 2) predicted < 2 | 15 | 15 | 1.000 | 0 | – |
| E01 | severity | laya_multi_fixed | th_th | severe incident (gold >= 2) predicted < 2 | 15 | 15 | 1.000 | 0 | – |
| E01 | severity | laya_routed | en_en | severe incident (gold >= 2) predicted < 2 | 15 | 10 | 0.667 | 0 | – |
| E01 | severity | laya_routed | th_th | severe incident (gold >= 2) predicted < 2 | 15 | 15 | 1.000 | 0 | – |
| E01 | severity | rules_only | en_en | severe incident (gold >= 2) predicted < 2 | 15 | 0 | 0.000 | 0 | 0.181 |
| E01 | severity | rules_only | th_th | severe incident (gold >= 2) predicted < 2 | 15 | 0 | 0.000 | 0 | 0.181 |
| E03 | drift_impact | laya_en_fixed | en_en | breaking change predicted non-breaking | 13 | 0 | 0.000 | 0 | 0.206 |
| E03 | drift_impact | laya_en_fixed | th_th | breaking change predicted non-breaking | 13 | 10 | 0.769 | 0 | – |
| E03 | drift_impact | laya_multi_fixed | en_en | breaking change predicted non-breaking | 13 | 10 | 0.769 | 0 | – |
| E03 | drift_impact | laya_multi_fixed | th_th | breaking change predicted non-breaking | 13 | 7 | 0.538 | 0 | – |
| E03 | drift_impact | laya_routed | en_en | breaking change predicted non-breaking | 13 | 0 | 0.000 | 0 | 0.206 |
| E03 | drift_impact | laya_routed | th_th | breaking change predicted non-breaking | 13 | 7 | 0.538 | 0 | – |
| E03 | drift_impact | rules_only | en_en | breaking change predicted non-breaking | 13 | 0 | 0.000 | 0 | 0.206 |
| E03 | drift_impact | rules_only | th_th | breaking change predicted non-breaking | 13 | 0 | 0.000 | 0 | 0.206 |
| E04 | impact_tier | laya_en_fixed | en_en | critical downstream impact missed | 2 | 0 | 0.000 | 0 | 0.776 |
| E04 | impact_tier | laya_en_fixed | th_th | critical downstream impact missed | 2 | 2 | 1.000 | 0 | – |
| E04 | impact_tier | laya_multi_fixed | en_en | critical downstream impact missed | 2 | 1 | 0.500 | 0 | – |
| E04 | impact_tier | laya_multi_fixed | th_th | critical downstream impact missed | 2 | 0 | 0.000 | 0 | 0.776 |
| E04 | impact_tier | laya_routed | en_en | critical downstream impact missed | 2 | 0 | 0.000 | 0 | 0.776 |
| E04 | impact_tier | laya_routed | th_th | critical downstream impact missed | 2 | 0 | 0.000 | 0 | 0.776 |
| E04 | impact_tier | rules_only | en_en | critical downstream impact missed | 2 | 0 | 0.000 | 0 | 0.776 |
| E04 | impact_tier | rules_only | th_th | critical downstream impact missed | 2 | 0 | 0.000 | 0 | 0.776 |
| E05 | data_class | laya_en_fixed | en_en | sensitive/direct identifier predicted non-personal | 6 | 3 | 0.500 | 0 | – |
| E05 | data_class | laya_en_fixed | th_th | sensitive/direct identifier predicted non-personal | 6 | 0 | 0.000 | 0 | 0.393 |
| E05 | data_class | laya_multi_fixed | en_en | sensitive/direct identifier predicted non-personal | 6 | 2 | 0.333 | 0 | – |
| E05 | data_class | laya_multi_fixed | th_th | sensitive/direct identifier predicted non-personal | 6 | 0 | 0.000 | 0 | 0.393 |
| E05 | data_class | laya_routed | en_en | sensitive/direct identifier predicted non-personal | 6 | 3 | 0.500 | 0 | – |
| E05 | data_class | laya_routed | th_th | sensitive/direct identifier predicted non-personal | 6 | 0 | 0.000 | 0 | 0.393 |
| E05 | data_class | rules_only | en_en | sensitive/direct identifier predicted non-personal | 6 | 0 | 0.000 | 0 | 0.393 |
| E05 | data_class | rules_only | th_th | sensitive/direct identifier predicted non-personal | 6 | 0 | 0.000 | 0 | 0.393 |
| E06 | candidate_verdict | laya_en_fixed | en_en | unsafe or wrong SQL accepted as correct | 13 | 11 | 0.846 | 0 | – |
| E06 | candidate_verdict | laya_en_fixed | th_th | unsafe or wrong SQL accepted as correct | 13 | 5 | 0.385 | 0 | – |
| E06 | candidate_verdict | laya_multi_fixed | en_en | unsafe or wrong SQL accepted as correct | 13 | 12 | 0.923 | 0 | – |
| E06 | candidate_verdict | laya_multi_fixed | th_th | unsafe or wrong SQL accepted as correct | 13 | 13 | 1.000 | 0 | – |
| E06 | candidate_verdict | laya_routed | en_en | unsafe or wrong SQL accepted as correct | 13 | 11 | 0.846 | 0 | – |
| E06 | candidate_verdict | laya_routed | th_th | unsafe or wrong SQL accepted as correct | 13 | 13 | 1.000 | 0 | – |
| E06 | candidate_verdict | rules_only | en_en | unsafe or wrong SQL accepted as correct | 13 | 5 | 0.385 | 0 | – |
| E06 | candidate_verdict | rules_only | th_th | unsafe or wrong SQL accepted as correct | 13 | 5 | 0.385 | 0 | – |

## Table 8 — deterministic rules baseline vs models

| experiment_id | variant | question_id | model_track | n | rules_coverage | rules_acc | model_acc | rules_only_correct | model_only_correct | model_acc_where_rules_abstain |
|---|---|---|---|---|---|---|---|---|---|---|
| E01 | en_en | failure_family | laya_en_fixed | 20 | 0.900 | 0.900 | 0.150 | 15 | 0 | 0.000 |
| E01 | en_en | failure_family | laya_multi_fixed | 20 | 0.900 | 0.900 | 0.200 | 14 | 0 | 0.000 |
| E01 | en_en | failure_family | laya_routed | 20 | 0.900 | 0.900 | 0.150 | 15 | 0 | 0.000 |
| E01 | en_en | requires_human | laya_en_fixed | 20 | 0.900 | 0.900 | 0.450 | 9 | 0 | 0.000 |
| E01 | en_en | requires_human | laya_multi_fixed | 20 | 0.900 | 0.900 | 0.550 | 9 | 2 | 1.000 |
| E01 | en_en | requires_human | laya_routed | 20 | 0.900 | 0.900 | 0.450 | 9 | 0 | 0.000 |
| E01 | en_en | severity | laya_en_fixed | 20 | 1.000 | 1.000 | 0.450 | 11 | 0 | – |
| E01 | en_en | severity | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.200 | 16 | 0 | – |
| E01 | en_en | severity | laya_routed | 20 | 1.000 | 1.000 | 0.450 | 11 | 0 | – |
| E01 | th_th | failure_family | laya_en_fixed | 20 | 0.900 | 0.900 | 0.100 | 16 | 0 | 0.000 |
| E01 | th_th | failure_family | laya_multi_fixed | 20 | 0.900 | 0.900 | 0.450 | 9 | 0 | 0.000 |
| E01 | th_th | failure_family | laya_routed | 20 | 0.900 | 0.900 | 0.450 | 9 | 0 | 0.000 |
| E01 | th_th | requires_human | laya_en_fixed | 20 | 0.900 | 0.900 | 0.350 | 12 | 1 | 0.500 |
| E01 | th_th | requires_human | laya_multi_fixed | 20 | 0.900 | 0.900 | 0.600 | 8 | 2 | 1.000 |
| E01 | th_th | requires_human | laya_routed | 20 | 0.900 | 0.900 | 0.600 | 8 | 2 | 1.000 |
| E01 | th_th | severity | laya_en_fixed | 20 | 1.000 | 1.000 | 0.250 | 15 | 0 | – |
| E01 | th_th | severity | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.200 | 16 | 0 | – |
| E01 | th_th | severity | laya_routed | 20 | 1.000 | 1.000 | 0.200 | 16 | 0 | – |
| E02 | en_en | anomaly_family | laya_en_fixed | 20 | 1.000 | 0.950 | 0.500 | 9 | 0 | – |
| E02 | en_en | anomaly_family | laya_multi_fixed | 20 | 1.000 | 0.950 | 0.350 | 12 | 0 | – |
| E02 | en_en | anomaly_family | laya_routed | 20 | 1.000 | 0.950 | 0.500 | 9 | 0 | – |
| E02 | en_en | investigate_now | laya_en_fixed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E02 | en_en | investigate_now | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E02 | en_en | investigate_now | laya_routed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E02 | en_en | quality_impact | laya_en_fixed | 20 | 1.000 | 0.950 | 0.150 | 16 | 0 | – |
| E02 | en_en | quality_impact | laya_multi_fixed | 20 | 1.000 | 0.950 | 0.300 | 14 | 1 | – |
| E02 | en_en | quality_impact | laya_routed | 20 | 1.000 | 0.950 | 0.150 | 16 | 0 | – |
| E02 | th_th | anomaly_family | laya_en_fixed | 20 | 1.000 | 0.950 | 0.250 | 14 | 0 | – |
| E02 | th_th | anomaly_family | laya_multi_fixed | 20 | 1.000 | 0.950 | 0.250 | 14 | 0 | – |
| E02 | th_th | anomaly_family | laya_routed | 20 | 1.000 | 0.950 | 0.250 | 14 | 0 | – |
| E02 | th_th | investigate_now | laya_en_fixed | 20 | 1.000 | 1.000 | 0.200 | 16 | 0 | – |
| E02 | th_th | investigate_now | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E02 | th_th | investigate_now | laya_routed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E02 | th_th | quality_impact | laya_en_fixed | 20 | 1.000 | 0.950 | 0.100 | 17 | 0 | – |
| E02 | th_th | quality_impact | laya_multi_fixed | 20 | 1.000 | 0.950 | 0.300 | 14 | 1 | – |
| E02 | th_th | quality_impact | laya_routed | 20 | 1.000 | 0.950 | 0.300 | 14 | 1 | – |
| E03 | en_en | drift_impact | laya_en_fixed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E03 | en_en | drift_impact | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.250 | 15 | 0 | – |
| E03 | en_en | drift_impact | laya_routed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E03 | en_en | migration_risk | laya_en_fixed | 20 | 1.000 | 0.800 | 0.450 | 9 | 2 | – |
| E03 | en_en | migration_risk | laya_multi_fixed | 20 | 1.000 | 0.800 | 0.100 | 16 | 2 | – |
| E03 | en_en | migration_risk | laya_routed | 20 | 1.000 | 0.800 | 0.450 | 9 | 2 | – |
| E03 | en_en | review_before_deploy | laya_en_fixed | 20 | 1.000 | 0.900 | 0.200 | 16 | 2 | – |
| E03 | en_en | review_before_deploy | laya_multi_fixed | 20 | 1.000 | 0.900 | 0.800 | 2 | 0 | – |
| E03 | en_en | review_before_deploy | laya_routed | 20 | 1.000 | 0.900 | 0.200 | 16 | 2 | – |
| E03 | th_th | drift_impact | laya_en_fixed | 20 | 1.000 | 1.000 | 0.200 | 16 | 0 | – |
| E03 | th_th | drift_impact | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.250 | 15 | 0 | – |
| E03 | th_th | drift_impact | laya_routed | 20 | 1.000 | 1.000 | 0.250 | 15 | 0 | – |
| E03 | th_th | migration_risk | laya_en_fixed | 20 | 1.000 | 0.800 | 0.100 | 14 | 0 | – |
| E03 | th_th | migration_risk | laya_multi_fixed | 20 | 1.000 | 0.800 | 0.300 | 10 | 0 | – |
| E03 | th_th | migration_risk | laya_routed | 20 | 1.000 | 0.800 | 0.300 | 10 | 0 | – |
| E03 | th_th | review_before_deploy | laya_en_fixed | 20 | 1.000 | 0.900 | 0.800 | 2 | 0 | – |
| E03 | th_th | review_before_deploy | laya_multi_fixed | 20 | 1.000 | 0.900 | 0.750 | 3 | 0 | – |
| E03 | th_th | review_before_deploy | laya_routed | 20 | 1.000 | 0.900 | 0.750 | 3 | 0 | – |
| E04 | en_en | impact_severity | laya_en_fixed | 20 | 1.000 | 1.000 | 0.450 | 11 | 0 | – |
| E04 | en_en | impact_severity | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.400 | 12 | 0 | – |
| E04 | en_en | impact_severity | laya_routed | 20 | 1.000 | 1.000 | 0.450 | 11 | 0 | – |
| E04 | en_en | impact_tier | laya_en_fixed | 20 | 1.000 | 1.000 | 0.400 | 12 | 0 | – |
| E04 | en_en | impact_tier | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E04 | en_en | impact_tier | laya_routed | 20 | 1.000 | 1.000 | 0.400 | 12 | 0 | – |
| E04 | en_en | needs_owner_escalation | laya_en_fixed | 20 | 1.000 | 1.000 | 0.450 | 11 | 0 | – |
| E04 | en_en | needs_owner_escalation | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E04 | en_en | needs_owner_escalation | laya_routed | 20 | 1.000 | 1.000 | 0.450 | 11 | 0 | – |
| E04 | th_th | impact_severity | laya_en_fixed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E04 | th_th | impact_severity | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E04 | th_th | impact_severity | laya_routed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E04 | th_th | impact_tier | laya_en_fixed | 20 | 1.000 | 1.000 | 0.000 | 20 | 0 | – |
| E04 | th_th | impact_tier | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E04 | th_th | impact_tier | laya_routed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E04 | th_th | needs_owner_escalation | laya_en_fixed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E04 | th_th | needs_owner_escalation | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E04 | th_th | needs_owner_escalation | laya_routed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E05 | en_en | data_class | laya_en_fixed | 20 | 1.000 | 0.900 | 0.550 | 9 | 2 | – |
| E05 | en_en | data_class | laya_multi_fixed | 20 | 1.000 | 0.900 | 0.250 | 13 | 0 | – |
| E05 | en_en | data_class | laya_routed | 20 | 1.000 | 0.900 | 0.550 | 9 | 2 | – |
| E05 | en_en | handling_tier | laya_en_fixed | 20 | 1.000 | 0.950 | 0.150 | 17 | 1 | – |
| E05 | en_en | handling_tier | laya_multi_fixed | 20 | 1.000 | 0.950 | 0.300 | 13 | 0 | – |
| E05 | en_en | handling_tier | laya_routed | 20 | 1.000 | 0.950 | 0.150 | 17 | 1 | – |
| E05 | en_en | requires_masking_review | laya_en_fixed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E05 | en_en | requires_masking_review | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E05 | en_en | requires_masking_review | laya_routed | 20 | 1.000 | 1.000 | 0.350 | 13 | 0 | – |
| E05 | th_th | data_class | laya_en_fixed | 20 | 1.000 | 0.900 | 0.200 | 14 | 0 | – |
| E05 | th_th | data_class | laya_multi_fixed | 20 | 1.000 | 0.900 | 0.100 | 16 | 0 | – |
| E05 | th_th | data_class | laya_routed | 20 | 1.000 | 0.900 | 0.100 | 16 | 0 | – |
| E05 | th_th | handling_tier | laya_en_fixed | 20 | 1.000 | 0.950 | 0.350 | 13 | 1 | – |
| E05 | th_th | handling_tier | laya_multi_fixed | 20 | 1.000 | 0.950 | 0.550 | 8 | 0 | – |
| E05 | th_th | handling_tier | laya_routed | 20 | 1.000 | 0.950 | 0.550 | 8 | 0 | – |
| E05 | th_th | requires_masking_review | laya_en_fixed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E05 | th_th | requires_masking_review | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E05 | th_th | requires_masking_review | laya_routed | 20 | 1.000 | 1.000 | 0.300 | 14 | 0 | – |
| E06 | en_en | candidate_quality | laya_en_fixed | 20 | 1.000 | 0.650 | 0.300 | 8 | 1 | – |
| E06 | en_en | candidate_quality | laya_multi_fixed | 20 | 1.000 | 0.650 | 0.200 | 12 | 3 | – |
| E06 | en_en | candidate_quality | laya_routed | 20 | 1.000 | 0.650 | 0.300 | 8 | 1 | – |
| E06 | en_en | candidate_safe_to_consider | laya_en_fixed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E06 | en_en | candidate_safe_to_consider | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E06 | en_en | candidate_safe_to_consider | laya_routed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E06 | en_en | candidate_verdict | laya_en_fixed | 20 | 1.000 | 0.750 | 0.250 | 10 | 0 | – |
| E06 | en_en | candidate_verdict | laya_multi_fixed | 20 | 1.000 | 0.750 | 0.250 | 11 | 1 | – |
| E06 | en_en | candidate_verdict | laya_routed | 20 | 1.000 | 0.750 | 0.250 | 10 | 0 | – |
| E06 | en_en | intent_type | laya_en_fixed | 20 | 1.000 | 1.000 | 0.500 | 10 | 0 | – |
| E06 | en_en | intent_type | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E06 | en_en | intent_type | laya_routed | 20 | 1.000 | 1.000 | 0.500 | 10 | 0 | – |
| E06 | en_en | needs_clarification | laya_en_fixed | 20 | 1.000 | 1.000 | 0.400 | 12 | 0 | – |
| E06 | en_en | needs_clarification | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.100 | 18 | 0 | – |
| E06 | en_en | needs_clarification | laya_routed | 20 | 1.000 | 1.000 | 0.400 | 12 | 0 | – |
| E06 | th_th | candidate_quality | laya_en_fixed | 20 | 1.000 | 0.650 | 0.250 | 9 | 1 | – |
| E06 | th_th | candidate_quality | laya_multi_fixed | 20 | 1.000 | 0.650 | 0.250 | 9 | 1 | – |
| E06 | th_th | candidate_quality | laya_routed | 20 | 1.000 | 0.650 | 0.250 | 9 | 1 | – |
| E06 | th_th | candidate_safe_to_consider | laya_en_fixed | 20 | 1.000 | 1.000 | 0.650 | 7 | 0 | – |
| E06 | th_th | candidate_safe_to_consider | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E06 | th_th | candidate_safe_to_consider | laya_routed | 20 | 1.000 | 1.000 | 0.700 | 6 | 0 | – |
| E06 | th_th | candidate_verdict | laya_en_fixed | 20 | 1.000 | 0.750 | 0.250 | 11 | 1 | – |
| E06 | th_th | candidate_verdict | laya_multi_fixed | 20 | 1.000 | 0.750 | 0.200 | 11 | 0 | – |
| E06 | th_th | candidate_verdict | laya_routed | 20 | 1.000 | 0.750 | 0.200 | 11 | 0 | – |
| E06 | th_th | intent_type | laya_en_fixed | 20 | 1.000 | 1.000 | 0.200 | 16 | 0 | – |
| E06 | th_th | intent_type | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E06 | th_th | intent_type | laya_routed | 20 | 1.000 | 1.000 | 0.550 | 9 | 0 | – |
| E06 | th_th | needs_clarification | laya_en_fixed | 20 | 1.000 | 1.000 | 0.400 | 12 | 0 | – |
| E06 | th_th | needs_clarification | laya_multi_fixed | 20 | 1.000 | 1.000 | 0.100 | 18 | 0 | – |
| E06 | th_th | needs_clarification | laya_routed | 20 | 1.000 | 1.000 | 0.100 | 18 | 0 | – |

## P3 — Laya router checkpoint choice

| experiment_id | variant | n | routed_english | routed_multilingual | mismatch_vs_state_language |
|---|---|---|---|---|---|
| E01 | en_en | 20 | 20 | 0 | 0.000 |
| E01 | th_th | 20 | 0 | 20 | 0.000 |
| E02 | en_en | 20 | 20 | 0 | 0.000 |
| E02 | th_th | 20 | 0 | 20 | 0.000 |
| E03 | en_en | 20 | 20 | 0 | 0.000 |
| E03 | th_th | 20 | 0 | 20 | 0.000 |
| E04 | en_en | 20 | 20 | 0 | 0.000 |
| E04 | th_th | 20 | 0 | 20 | 0.000 |
| E05 | en_en | 20 | 18 | 2 | 0.100 |
| E05 | th_th | 20 | 0 | 20 | 0.000 |
| E06 | en_en | 20 | 20 | 0 | 0.000 |
| E06 | th_th | 20 | 0 | 20 | 0.000 |

## P9 — evidence sufficiency and confident errors (primary choice question)

| experiment_id | model_track | variant | evidence_sufficiency | n | effective_accuracy | unknown_recall | high_conf_error_rate |
|---|---|---|---|---|---|---|---|
| E01 | laya_en_fixed | en_en | ambiguous | 2 | 1.000 | – | 0.000 |
| E01 | laya_en_fixed | en_en | insufficient | 2 | 0.000 | 0.000 | 1.000 |
| E01 | laya_en_fixed | en_en | sufficient | 16 | 0.062 | – | 0.688 |
| E01 | laya_en_fixed | th_th | ambiguous | 2 | 1.000 | – | 0.000 |
| E01 | laya_en_fixed | th_th | insufficient | 2 | 0.000 | 0.000 | 0.500 |
| E01 | laya_en_fixed | th_th | sufficient | 16 | 0.000 | – | 0.562 |
| E02 | laya_en_fixed | en_en | ambiguous | 2 | 0.500 | – | 0.000 |
| E02 | laya_en_fixed | en_en | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E02 | laya_en_fixed | en_en | sufficient | 17 | 0.529 | 0.000 | 0.000 |
| E02 | laya_en_fixed | th_th | ambiguous | 2 | 0.000 | – | 0.000 |
| E02 | laya_en_fixed | th_th | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E02 | laya_en_fixed | th_th | sufficient | 17 | 0.294 | 0.000 | 0.000 |
| E03 | laya_en_fixed | en_en | ambiguous | 3 | 1.000 | – | 0.000 |
| E03 | laya_en_fixed | en_en | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E03 | laya_en_fixed | en_en | sufficient | 16 | 0.688 | 0.000 | 0.062 |
| E03 | laya_en_fixed | th_th | ambiguous | 3 | 0.000 | – | 0.000 |
| E03 | laya_en_fixed | th_th | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E03 | laya_en_fixed | th_th | sufficient | 16 | 0.250 | 0.000 | 0.000 |
| E04 | laya_en_fixed | en_en | ambiguous | 6 | 0.667 | – | 0.000 |
| E04 | laya_en_fixed | en_en | insufficient | 6 | 0.000 | 0.000 | 0.000 |
| E04 | laya_en_fixed | en_en | sufficient | 8 | 0.500 | – | 0.000 |
| E04 | laya_en_fixed | th_th | ambiguous | 6 | 0.000 | – | 0.000 |
| E04 | laya_en_fixed | th_th | insufficient | 6 | 0.000 | 0.000 | 0.000 |
| E04 | laya_en_fixed | th_th | sufficient | 8 | 0.000 | – | 0.250 |
| E05 | laya_en_fixed | en_en | ambiguous | 2 | 0.500 | – | 0.000 |
| E05 | laya_en_fixed | en_en | sufficient | 18 | 0.556 | – | 0.000 |
| E05 | laya_en_fixed | th_th | ambiguous | 2 | 0.500 | – | 0.000 |
| E05 | laya_en_fixed | th_th | sufficient | 18 | 0.167 | – | 0.000 |
| E06 | laya_en_fixed | en_en | ambiguous | 2 | 0.000 | 0.000 | 0.000 |
| E06 | laya_en_fixed | en_en | sufficient | 18 | 0.278 | – | 0.000 |
| E06 | laya_en_fixed | th_th | ambiguous | 2 | 0.000 | 0.000 | 0.000 |
| E06 | laya_en_fixed | th_th | sufficient | 18 | 0.278 | – | 0.000 |
| E01 | laya_multi_fixed | en_en | ambiguous | 2 | 0.500 | – | 0.000 |
| E01 | laya_multi_fixed | en_en | insufficient | 2 | 0.000 | 0.000 | 0.000 |
| E01 | laya_multi_fixed | en_en | sufficient | 16 | 0.188 | – | 0.125 |
| E01 | laya_multi_fixed | th_th | ambiguous | 2 | 1.000 | – | 0.000 |
| E01 | laya_multi_fixed | th_th | insufficient | 2 | 0.000 | 0.000 | 0.000 |
| E01 | laya_multi_fixed | th_th | sufficient | 16 | 0.438 | – | 0.062 |
| E02 | laya_multi_fixed | en_en | ambiguous | 2 | 0.500 | – | 0.500 |
| E02 | laya_multi_fixed | en_en | insufficient | 1 | 0.000 | 0.000 | 1.000 |
| E02 | laya_multi_fixed | en_en | sufficient | 17 | 0.353 | 0.333 | 0.000 |
| E02 | laya_multi_fixed | th_th | ambiguous | 2 | 0.000 | – | 1.000 |
| E02 | laya_multi_fixed | th_th | insufficient | 1 | 0.000 | 0.000 | 1.000 |
| E02 | laya_multi_fixed | th_th | sufficient | 17 | 0.294 | 0.000 | 0.353 |
| E03 | laya_multi_fixed | en_en | ambiguous | 3 | 0.000 | – | 0.667 |
| E03 | laya_multi_fixed | en_en | insufficient | 1 | 0.000 | 0.000 | 1.000 |
| E03 | laya_multi_fixed | en_en | sufficient | 16 | 0.312 | 0.000 | 0.312 |
| E03 | laya_multi_fixed | th_th | ambiguous | 3 | 0.000 | – | 0.333 |
| E03 | laya_multi_fixed | th_th | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E03 | laya_multi_fixed | th_th | sufficient | 16 | 0.312 | 0.000 | 0.125 |
| E04 | laya_multi_fixed | en_en | ambiguous | 6 | 0.500 | – | 0.167 |
| E04 | laya_multi_fixed | en_en | insufficient | 6 | 0.167 | 0.167 | 0.000 |
| E04 | laya_multi_fixed | en_en | sufficient | 8 | 0.375 | – | 0.000 |
| E04 | laya_multi_fixed | th_th | ambiguous | 6 | 0.667 | – | 0.000 |
| E04 | laya_multi_fixed | th_th | insufficient | 6 | 0.000 | 0.000 | 0.167 |
| E04 | laya_multi_fixed | th_th | sufficient | 8 | 0.375 | – | 0.250 |
| E05 | laya_multi_fixed | en_en | ambiguous | 2 | 0.500 | – | 0.500 |
| E05 | laya_multi_fixed | en_en | sufficient | 18 | 0.222 | – | 0.111 |
| E05 | laya_multi_fixed | th_th | ambiguous | 2 | 0.000 | – | 1.000 |
| E05 | laya_multi_fixed | th_th | sufficient | 18 | 0.111 | – | 0.167 |
| E06 | laya_multi_fixed | en_en | ambiguous | 2 | 0.000 | 0.000 | 0.000 |
| E06 | laya_multi_fixed | en_en | sufficient | 18 | 0.278 | – | 0.000 |
| E06 | laya_multi_fixed | th_th | ambiguous | 2 | 0.000 | 0.000 | 0.000 |
| E06 | laya_multi_fixed | th_th | sufficient | 18 | 0.222 | – | 0.000 |
| E01 | laya_routed | en_en | ambiguous | 2 | 1.000 | – | 0.000 |
| E01 | laya_routed | en_en | insufficient | 2 | 0.000 | 0.000 | 1.000 |
| E01 | laya_routed | en_en | sufficient | 16 | 0.062 | – | 0.688 |
| E01 | laya_routed | th_th | ambiguous | 2 | 1.000 | – | 0.000 |
| E01 | laya_routed | th_th | insufficient | 2 | 0.000 | 0.000 | 0.000 |
| E01 | laya_routed | th_th | sufficient | 16 | 0.438 | – | 0.062 |
| E02 | laya_routed | en_en | ambiguous | 2 | 0.500 | – | 0.000 |
| E02 | laya_routed | en_en | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E02 | laya_routed | en_en | sufficient | 17 | 0.529 | 0.000 | 0.000 |
| E02 | laya_routed | th_th | ambiguous | 2 | 0.000 | – | 1.000 |
| E02 | laya_routed | th_th | insufficient | 1 | 0.000 | 0.000 | 1.000 |
| E02 | laya_routed | th_th | sufficient | 17 | 0.294 | 0.000 | 0.353 |
| E03 | laya_routed | en_en | ambiguous | 3 | 1.000 | – | 0.000 |
| E03 | laya_routed | en_en | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E03 | laya_routed | en_en | sufficient | 16 | 0.688 | 0.000 | 0.062 |
| E03 | laya_routed | th_th | ambiguous | 3 | 0.000 | – | 0.333 |
| E03 | laya_routed | th_th | insufficient | 1 | 0.000 | 0.000 | 0.000 |
| E03 | laya_routed | th_th | sufficient | 16 | 0.312 | 0.000 | 0.125 |
| E04 | laya_routed | en_en | ambiguous | 6 | 0.667 | – | 0.000 |
| E04 | laya_routed | en_en | insufficient | 6 | 0.000 | 0.000 | 0.000 |
| E04 | laya_routed | en_en | sufficient | 8 | 0.500 | – | 0.000 |
| E04 | laya_routed | th_th | ambiguous | 6 | 0.667 | – | 0.000 |
| E04 | laya_routed | th_th | insufficient | 6 | 0.000 | 0.000 | 0.167 |
| E04 | laya_routed | th_th | sufficient | 8 | 0.375 | – | 0.250 |
| E05 | laya_routed | en_en | ambiguous | 2 | 0.500 | – | 0.000 |
| E05 | laya_routed | en_en | sufficient | 18 | 0.556 | – | 0.000 |
| E05 | laya_routed | th_th | ambiguous | 2 | 0.000 | – | 1.000 |
| E05 | laya_routed | th_th | sufficient | 18 | 0.111 | – | 0.167 |
| E06 | laya_routed | en_en | ambiguous | 2 | 0.000 | 0.000 | 0.000 |
| E06 | laya_routed | en_en | sufficient | 18 | 0.278 | – | 0.000 |
| E06 | laya_routed | th_th | ambiguous | 2 | 0.000 | 0.000 | 0.000 |
| E06 | laya_routed | th_th | sufficient | 18 | 0.222 | – | 0.000 |
| E01 | rules_only | en_en | ambiguous | 2 | 1.000 | – | – |
| E01 | rules_only | en_en | insufficient | 2 | 0.000 | 0.000 | – |
| E01 | rules_only | en_en | sufficient | 16 | 1.000 | – | – |
| E01 | rules_only | th_th | ambiguous | 2 | 1.000 | – | – |
| E01 | rules_only | th_th | insufficient | 2 | 0.000 | 0.000 | – |
| E01 | rules_only | th_th | sufficient | 16 | 1.000 | – | – |
| E02 | rules_only | en_en | ambiguous | 2 | 0.500 | – | – |
| E02 | rules_only | en_en | insufficient | 1 | 1.000 | 1.000 | – |
| E02 | rules_only | en_en | sufficient | 17 | 1.000 | 1.000 | – |
| E02 | rules_only | th_th | ambiguous | 2 | 0.500 | – | – |
| E02 | rules_only | th_th | insufficient | 1 | 1.000 | 1.000 | – |
| E02 | rules_only | th_th | sufficient | 17 | 1.000 | 1.000 | – |
| E03 | rules_only | en_en | ambiguous | 3 | 1.000 | – | – |
| E03 | rules_only | en_en | insufficient | 1 | 1.000 | 1.000 | – |
| E03 | rules_only | en_en | sufficient | 16 | 1.000 | 1.000 | – |
| E03 | rules_only | th_th | ambiguous | 3 | 1.000 | – | – |
| E03 | rules_only | th_th | insufficient | 1 | 1.000 | 1.000 | – |
| E03 | rules_only | th_th | sufficient | 16 | 1.000 | 1.000 | – |
| E04 | rules_only | en_en | ambiguous | 6 | 1.000 | – | – |
| E04 | rules_only | en_en | insufficient | 6 | 1.000 | 1.000 | – |
| E04 | rules_only | en_en | sufficient | 8 | 1.000 | – | – |
| E04 | rules_only | th_th | ambiguous | 6 | 1.000 | – | – |
| E04 | rules_only | th_th | insufficient | 6 | 1.000 | 1.000 | – |
| E04 | rules_only | th_th | sufficient | 8 | 1.000 | – | – |
| E05 | rules_only | en_en | ambiguous | 2 | 1.000 | – | – |
| E05 | rules_only | en_en | sufficient | 18 | 0.889 | – | – |
| E05 | rules_only | th_th | ambiguous | 2 | 1.000 | – | – |
| E05 | rules_only | th_th | sufficient | 18 | 0.889 | – | – |
| E06 | rules_only | en_en | ambiguous | 2 | 1.000 | 1.000 | – |
| E06 | rules_only | en_en | sufficient | 18 | 0.722 | – | – |
| E06 | rules_only | th_th | ambiguous | 2 | 1.000 | 1.000 | – |
| E06 | rules_only | th_th | sufficient | 18 | 0.722 | – | – |

## Context fit (Laya truncation)

| split | protocol | context_level | model_track | experiment_id | variant | n_requests | truncated_share |
|---|---|---|---|---|---|---|---|
| dev | primary | common_fit | laya_en_fixed | E01 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E01 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E02 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E02 | th_th | 20 | 1.000 |
| dev | primary | common_fit | laya_en_fixed | E03 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E03 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E04 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E04 | th_th | 20 | 0.700 |
| dev | primary | common_fit | laya_en_fixed | E05 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E05 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E06 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_en_fixed | E06 | th_th | 20 | 0.800 |
| dev | primary | common_fit | laya_multi_fixed | E01 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E01 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E02 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E02 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E03 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E03 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E04 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E04 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E05 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E05 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E06 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_multi_fixed | E06 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E01 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E01 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E02 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E02 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E03 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E03 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E04 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E04 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E05 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E05 | th_th | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E06 | en_en | 20 | 0.000 |
| dev | primary | common_fit | laya_routed | E06 | th_th | 20 | 0.000 |


See `docs/STUDY_LIMITATIONS.md` and `results/BLOCKERS.md`.
