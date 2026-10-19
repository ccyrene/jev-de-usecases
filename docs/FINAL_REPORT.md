# Final report — de-laya-jev-th-en-2026

**Status:** LOCKED EVALUATION  
**Generated:** 2026-10-19T17:08:06+07:00  
**Analysis:** `results/analysis/test` from runs `test_primary_laya`, `rules_test`, `test_p4_laya`, `test_p5_laya`, `test_p7_long_laya`, `test_primary_jev`, `test_p4_jev`, `test_p5_jev`, `test_p7_long_jev`

## Key findings (test split, frozen e0X-v1 prompts)

**Bottom line.** On identical requests (same `state` + `questions` bytes, same 200 test scenarios
per case per language), **Jev 1.14.0 beats Laya 0.4.11 (router) in all 6 use cases in both English
and Thai**, with every 95% paired-bootstrap CI excluding zero (McNemar p < 0.001 in all 12
primary comparisons, so the result survives Holm adjustment). Laya's advantages are local
deployment, zero API spend and lower single-stream latency on a GPU; it is not competitive on
decision quality zero-shot. Neither model replaces the deterministic DB-backed rules, which read the
same verified facts that define the gold labels.

![Primary decision quality by use case](figures/01_quality_primary.png)

### RQ1 — quality (primary choice question, macro-F1, attempted denominator)

| Case | Laya EN | Jev EN | Δ EN [95% CI] | Laya TH | Jev TH | Δ TH [95% CI] | Rules |
|---|---|---|---|---|---|---|---|
| E01 failure_family | 0.037 | 0.863 | −0.825 [−0.875, −0.774] | 0.203 | 0.909 | −0.706 [−0.771, −0.645] | 0.857 |
| E02 anomaly_family | 0.710 | 0.877 | −0.167 [−0.222, −0.114] | 0.228 | 0.873 | −0.644 [−0.712, −0.577] | 0.953 |
| E03 drift_impact | 0.502 | 0.797 | −0.295 [−0.360, −0.227] | 0.197 | 0.808 | −0.610 [−0.665, −0.555] | 0.979 |
| E04 impact_tier | 0.275 | 0.711 | −0.436 [−0.493, −0.381] | 0.182 | 0.711 | −0.529 [−0.581, −0.476] | 1.000 |
| E05 data_class | 0.213 | 0.800 | −0.586 [−0.650, −0.521] | 0.165 | 0.820 | −0.655 [−0.720, −0.591] | 0.880 |
| E06 candidate_verdict | 0.097 | 0.475 | −0.378 [−0.429, −0.320] | 0.275 | 0.435 | −0.160 [−0.253, −0.065] | 0.832 |

Δ = Laya routed − Jev (negative favours Jev), n = 200 scenarios (E06: 201). Coverage was ≥ 99.9%
for both models. Laya's English checkpoint collapses on E01 (it answers `dependency_failure` for
almost every incident). Per-question results for all score/noul questions are in Table 1.

### RQ2 — language

- **Jev shows no meaningful Thai penalty:** TH − EN macro-F1 is within ±0.05 in every case
  (E01 +0.046 [0.016, 0.085]; E06 −0.040 [−0.080, −0.003]; the other four CIs include 0).
- **Laya routed loses heavily in Thai** where its English checkpoint works: E02 −0.482
  [−0.552, −0.418], E03 −0.305 [−0.363, −0.243], E04 −0.092 [−0.164, −0.025]. It "gains" in E01 and
  E06 only because English is already near the floor there.
- The router sent 100% of Thai states to the multilingual checkpoint and 99.9% of English states to
  the English one (1 of 1,201 English states, in E05, went to multilingual). The English checkpoint
  truncates 41% of Thai `common_fit` requests, so routing is necessary but not sufficient.

### RQ3 — probability quality (primary choice question)

- **Jev is reasonably calibrated raw** (equal-mass ECE 0.05–0.11 for E01–E03/E05, 0.18–0.20 for E04,
  0.25–0.26 for E06). Temperature fitted on the 40-scenario calibration split cut E06 ECE to
  0.11–0.13; for E05 it made ECE worse (0.053 → 0.087), a small-calibration-set overfit that is
  reported, not hidden.
- **Laya is badly overconfident raw** (E01 EN ECE 0.75, E02 TH 0.65). Fitted temperatures often hit
  the upper bound (T = 20) for Thai, i.e. the best calibration is to flatten its distribution almost
  completely; calibrated Brier stays at 0.51–0.86 versus Jev's 0.13–0.55.
- **Selective prediction at a 20% target risk** (threshold fitted on calibration, applied to test):
  Jev auto-accepts 89–100% of test cases in EN (realized test risk 0.10–0.36, above target for
  E04/E05/E06), and E06 TH only 40%; Laya can auto-accept almost nothing (0–13%, except E02 at 69% EN
  and 30% TH, the latter with 78% realized risk).

### RQ4 — latency and cost (deployment comparison, not intrinsic speed)

- Laya on an RTX 3060 Laptop GPU, in-process, one stream: p50 37–51 ms (1 question) and
  66–111 ms (all questions). Throughput does **not** scale with concurrency on one GPU
  (13.8 → 11.5 → 9.2 req/s at concurrency 1/4/8; p50 rises to 750–960 ms at 8).
- Laya on the same laptop's CPU (8 threads, WSL2): p50 0.57–1.26 s (1 question) and 1.7–4.0 s (all
  questions) for one stream, rising to 2.2–10.5 s at concurrency 4. CPU-only Laya is not interactive
  for these prompts.
- Jev over HTTPS from Bangkok: p50 251–280 ms at every concurrency; throughput scales
  3.8 → 13.8 → 28.5 req/s at concurrency 1/4/8. The unauthenticated round trip to the API host alone
  is ~205 ms, so most of Jev's latency is network.

![Deployment latency](figures/14_latency.png)
- DB evidence retrieval is a separate phase: p50 4–42 ms by engine (dataset card).
- Cost: Jev billed 2.42 M input tokens for the 2,402 primary test requests = **USD 0.10**
  (USD 0.042 per 1,000 scenario decisions; Thai requests use ~2× the tokens of English). The whole
  study spent USD 0.50 on 11,384 attempts. Laya has no API fee; under an *assumed* USD 0.5–1.0/h host
  its sequential throughput implies USD 0.014–0.028 per 1,000 decisions — but that host must be paid
  for whether or not it is busy.

### RQ5 — robustness

- **Option order (P5, shared permutations 1–2):** Jev changes its choice in 2.7% (EN) / 2.8% (TH) of
  cases; Laya routed in 16.5% / 23.4% (up to 35% on E02 EN). Mean TVD 0.03 vs 0.12–0.23.
- **Cross-language prompts (P4, 50-scenario subset):** Jev is flat across en_en / en_th / th_en /
  th_th (e.g. E01 0.86–0.88). Laya depends on which checkpoint the *state* routes to: Thai state +
  English rubric (`th_en`) lands on the multilingual checkpoint and drops E02 from 0.65 to 0.21.
- **Context (P7, natural_long):** Jev quality is essentially unchanged (largest drop E05 TH
  0.820 → 0.748). Laya's English checkpoint truncates 49% of long English states; Laya TH E02 falls
  0.228 → 0.038.

### Safety-relevant misses (Table 7)

- Severe E01 incidents rated below "workflow blocked": Jev 0/138 (95% upper bound 2.1%), Laya 86–100%.
- Proven breaking schema changes called non-breaking (E03): Jev 1/111, Laya 10/111 (EN) and 77/111 (TH).
- Critical lineage impact missed (E04): Jev 0/34 (upper bound 8.4%), Laya 29–59%.
- Sensitive/direct identifiers called non-personal (E05): Jev 0/85 (upper bound 3.5%), Laya 56% in EN.
- Unsafe or wrong SQL accepted as `semantically_correct` (E06): **Jev 12/103 (EN) and 9/103 (TH)**,
  including 6 of 40 unsafe candidates per language; Laya 85–94%. The deterministic SQL guard blocked
  100% of unsafe candidates before execution regardless of any model's opinion — keep it as the
  final authority.

### Rules baselines (Table 8)

Rules beat Jev on E02–E06 because they apply the same policy as the gold over the same verified
facts (E04 rules are exact by construction). The one place a model adds value over rules here is
E01: rules abstain on the 14% of incidents with no decisive exception type, and Jev answers 43–57%
of those correctly, ending above rules overall (0.88/0.92 vs 0.86 accuracy).

## Tracks

| track | status | provider | note |
|---|---|---|---|
| jev_114 | OK | jev |  |
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
| E01 | Pipeline failure triage (OpenSearch) | Jev wins | failure_family macro-F1 (Laya routed − Jev 1.14); en_en: Δ=-0.825 [-0.875, -0.774], n=200; th_th: Δ=-0.706 [-0.771, -0.645], n=200 |
| E02 | Data quality monitoring (ClickHouse) | Jev wins | anomaly_family macro-F1 (Laya routed − Jev 1.14); en_en: Δ=-0.167 [-0.222, -0.114], n=200; th_th: Δ=-0.644 [-0.712, -0.577], n=200 |
| E03 | Schema drift detection (PostgreSQL) | Jev wins | drift_impact macro-F1 (Laya routed − Jev 1.14); en_en: Δ=-0.295 [-0.360, -0.227], n=200; th_th: Δ=-0.610 [-0.665, -0.555], n=200 |
| E04 | Lineage impact analysis (Neo4j) | Jev wins | impact_tier macro-F1 (Laya routed − Jev 1.14); en_en: Δ=-0.436 [-0.493, -0.381], n=200; th_th: Δ=-0.529 [-0.581, -0.476], n=200 |
| E05 | PII classification (PostgreSQL + JSONB) | Jev wins | data_class macro-F1 (Laya routed − Jev 1.14); en_en: Δ=-0.586 [-0.650, -0.521], n=200; th_th: Δ=-0.655 [-0.720, -0.591], n=200 |
| E06 | NL-to-SQL candidate validation (DuckDB) | Jev wins | candidate_verdict macro-F1 (Laya routed − Jev 1.14); en_en: Δ=-0.378 [-0.429, -0.320], n=201; th_th: Δ=-0.160 [-0.253, -0.065], n=201 |

## Table 1 — quality by model, case, language and question

Accuracy and macro-F1 use the attempted denominator (invalid = wrong); CIs are 95% scenario-clustered bootstrap (2,000 resamples, seed 20261019). Probability metrics use valid answers only.


| experiment_id | model_track | variant | question_id | primitive | n_attempted | coverage | truncation_rate | eff_acc [95% CI] | macro_f1 [95% CI] | brier | ece_equal_mass | mae_expected | auroc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E01 | jev_114 | en_en | failure_family | choice | 200 | 1.000 | 0.000 | 0.880 [0.835, 0.925] | 0.863 [0.810, 0.912] | 0.174 | 0.084 | – | – |
| E01 | jev_114 | en_en | requires_human | noul | 200 | 1.000 | 0.000 | 0.690 [0.620, 0.755] | – | 0.212 | 0.212 | – | 0.875 |
| E01 | jev_114 | en_en | severity | score | 200 | 1.000 | 0.000 | 0.720 [0.660, 0.780] | – | – | – | 0.309 | – |
| E01 | jev_114 | th_th | failure_family | choice | 200 | 1.000 | 0.000 | 0.915 [0.875, 0.955] | 0.909 [0.865, 0.947] | 0.137 | 0.051 | – | – |
| E01 | jev_114 | th_th | requires_human | noul | 200 | 1.000 | 0.000 | 0.765 [0.705, 0.825] | – | 0.157 | 0.108 | – | 0.883 |
| E01 | jev_114 | th_th | severity | score | 200 | 1.000 | 0.000 | 0.795 [0.740, 0.850] | – | – | – | 0.197 | – |
| E02 | jev_114 | en_en | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.825 [0.770, 0.875] | 0.877 [0.835, 0.912] | 0.294 | 0.101 | – | – |
| E02 | jev_114 | en_en | investigate_now | noul | 200 | 1.000 | 0.000 | 0.660 [0.595, 0.725] | – | 0.185 | 0.097 | – | 0.896 |
| E02 | jev_114 | en_en | quality_impact | score | 200 | 1.000 | 0.000 | 0.460 [0.390, 0.525] | – | – | – | 0.685 | – |
| E02 | jev_114 | th_th | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.820 [0.765, 0.870] | 0.873 [0.831, 0.907] | 0.267 | 0.085 | – | – |
| E02 | jev_114 | th_th | investigate_now | noul | 200 | 1.000 | 0.000 | 0.600 [0.530, 0.665] | – | 0.206 | 0.169 | – | 0.884 |
| E02 | jev_114 | th_th | quality_impact | score | 200 | 1.000 | 0.000 | 0.455 [0.390, 0.520] | – | – | – | 0.721 | – |
| E03 | jev_114 | en_en | drift_impact | choice | 200 | 1.000 | 0.000 | 0.790 [0.735, 0.845] | 0.797 [0.751, 0.839] | 0.258 | 0.125 | – | – |
| E03 | jev_114 | en_en | migration_risk | score | 200 | 1.000 | 0.000 | 0.255 [0.195, 0.315] | – | – | – | 0.758 | – |
| E03 | jev_114 | en_en | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.780 [0.720, 0.835] | – | 0.120 | 0.121 | – | 0.945 |
| E03 | jev_114 | th_th | drift_impact | choice | 200 | 1.000 | 0.000 | 0.800 [0.740, 0.855] | 0.808 [0.759, 0.850] | 0.254 | 0.123 | – | – |
| E03 | jev_114 | th_th | migration_risk | score | 200 | 1.000 | 0.000 | 0.495 [0.425, 0.565] | – | – | – | 0.665 | – |
| E03 | jev_114 | th_th | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.690 [0.630, 0.755] | – | 0.159 | 0.196 | – | 0.947 |
| E04 | jev_114 | en_en | impact_severity | score | 200 | 1.000 | 0.000 | 0.875 [0.830, 0.920] | – | – | – | 0.357 | – |
| E04 | jev_114 | en_en | impact_tier | choice | 200 | 1.000 | 0.000 | 0.785 [0.725, 0.840] | 0.711 [0.683, 0.735] | 0.403 | 0.193 | – | – |
| E04 | jev_114 | en_en | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.485 [0.420, 0.550] | – | 0.289 | 0.244 | – | 0.682 |
| E04 | jev_114 | th_th | impact_severity | score | 200 | 1.000 | 0.000 | 0.890 [0.845, 0.930] | – | – | – | 0.245 | – |
| E04 | jev_114 | th_th | impact_tier | choice | 200 | 1.000 | 0.000 | 0.785 [0.725, 0.840] | 0.711 [0.683, 0.735] | 0.369 | 0.181 | – | – |
| E04 | jev_114 | th_th | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.480 [0.415, 0.545] | – | 0.315 | 0.277 | – | 0.607 |
| E05 | jev_114 | en_en | data_class | choice | 200 | 1.000 | 0.000 | 0.785 [0.730, 0.840] | 0.800 [0.745, 0.847] | 0.282 | 0.099 | – | – |
| E05 | jev_114 | en_en | handling_tier | score | 200 | 1.000 | 0.000 | 0.690 [0.625, 0.755] | – | – | – | 0.343 | – |
| E05 | jev_114 | en_en | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.715 [0.655, 0.775] | – | 0.185 | 0.177 | – | 0.913 |
| E05 | jev_114 | th_th | data_class | choice | 200 | 1.000 | 0.000 | 0.810 [0.755, 0.865] | 0.820 [0.766, 0.868] | 0.230 | 0.067 | – | – |
| E05 | jev_114 | th_th | handling_tier | score | 200 | 1.000 | 0.000 | 0.710 [0.645, 0.770] | – | – | – | 0.366 | – |
| E05 | jev_114 | th_th | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.710 [0.650, 0.770] | – | 0.179 | 0.136 | – | 0.879 |
| E06 | jev_114 | en_en | candidate_quality | score | 201 | 1.000 | 0.000 | 0.443 [0.373, 0.512] | – | – | – | 0.977 | – |
| E06 | jev_114 | en_en | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 0.965 [0.935, 0.985] | – | 0.062 | 0.147 | – | 0.906 |
| E06 | jev_114 | en_en | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.557 [0.488, 0.627] | 0.475 [0.418, 0.527] | 0.656 | 0.258 | – | – |
| E06 | jev_114 | en_en | intent_type | choice | 201 | 1.000 | 0.000 | 0.701 [0.637, 0.766] | 0.669 [0.628, 0.706] | 0.570 | 0.265 | – | – |
| E06 | jev_114 | en_en | needs_clarification | noul | 201 | 1.000 | 0.000 | 0.701 [0.642, 0.766] | – | 0.186 | 0.151 | – | 0.680 |
| E06 | jev_114 | th_th | candidate_quality | score | 201 | 1.000 | 0.000 | 0.388 [0.318, 0.458] | – | – | – | 0.954 | – |
| E06 | jev_114 | th_th | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 0.915 [0.876, 0.950] | – | 0.103 | 0.156 | – | 0.863 |
| E06 | jev_114 | th_th | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.522 [0.453, 0.592] | 0.435 [0.375, 0.491] | 0.661 | 0.248 | – | – |
| E06 | jev_114 | th_th | intent_type | choice | 201 | 1.000 | 0.000 | 0.701 [0.637, 0.766] | 0.669 [0.628, 0.706] | 0.563 | 0.248 | – | – |
| E06 | jev_114 | th_th | needs_clarification | noul | 201 | 1.000 | 0.000 | 0.463 [0.393, 0.532] | – | 0.276 | 0.222 | – | 0.538 |
| E01 | laya_en_fixed | en_en | failure_family | choice | 200 | 1.000 | 0.000 | 0.150 [0.105, 0.195] | 0.037 [0.027, 0.047] | 1.414 | 0.738 | – | – |
| E01 | laya_en_fixed | en_en | requires_human | noul | 200 | 1.000 | 0.000 | 0.335 [0.270, 0.400] | – | 0.380 | 0.387 | – | 0.510 |
| E01 | laya_en_fixed | en_en | severity | score | 200 | 1.000 | 0.000 | 0.315 [0.255, 0.380] | – | – | – | 0.843 | – |
| E01 | laya_en_fixed | th_th | failure_family | choice | 200 | 1.000 | 0.020 | 0.150 [0.105, 0.195] | 0.037 [0.027, 0.047] | 1.277 | 0.604 | – | – |
| E01 | laya_en_fixed | th_th | requires_human | noul | 200 | 1.000 | 0.020 | 0.505 [0.440, 0.575] | – | 0.307 | 0.220 | – | 0.453 |
| E01 | laya_en_fixed | th_th | severity | score | 200 | 1.000 | 0.020 | 0.190 [0.140, 0.245] | – | – | – | 1.011 | – |
| E02 | laya_en_fixed | en_en | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.625 [0.555, 0.695] | 0.710 [0.658, 0.755] | 0.548 | 0.196 | – | – |
| E02 | laya_en_fixed | en_en | investigate_now | noul | 200 | 1.000 | 0.000 | 0.670 [0.605, 0.730] | – | 0.234 | 0.162 | – | 0.575 |
| E02 | laya_en_fixed | en_en | quality_impact | score | 200 | 1.000 | 0.000 | 0.325 [0.260, 0.390] | – | – | – | 1.014 | – |
| E02 | laya_en_fixed | th_th | anomaly_family | choice | 200 | 1.000 | 1.000 | 0.310 [0.250, 0.375] | 0.289 [0.262, 0.311] | 0.693 | 0.212 | – | – |
| E02 | laya_en_fixed | th_th | investigate_now | noul | 200 | 1.000 | 1.000 | 0.335 [0.270, 0.400] | – | 0.406 | 0.415 | – | 0.431 |
| E02 | laya_en_fixed | th_th | quality_impact | score | 200 | 1.000 | 1.000 | 0.335 [0.270, 0.400] | – | – | – | 1.192 | – |
| E03 | laya_en_fixed | en_en | drift_impact | choice | 200 | 1.000 | 0.000 | 0.510 [0.445, 0.580] | 0.502 [0.449, 0.554] | 0.649 | 0.122 | – | – |
| E03 | laya_en_fixed | en_en | migration_risk | score | 200 | 1.000 | 0.000 | 0.370 [0.300, 0.440] | – | – | – | 0.933 | – |
| E03 | laya_en_fixed | en_en | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.390 [0.320, 0.455] | – | 0.365 | 0.345 | – | 0.573 |
| E03 | laya_en_fixed | th_th | drift_impact | choice | 200 | 1.000 | 0.025 | 0.210 [0.155, 0.265] | 0.181 [0.121, 0.235] | 0.830 | 0.201 | – | – |
| E03 | laya_en_fixed | th_th | migration_risk | score | 200 | 1.000 | 0.025 | 0.205 [0.150, 0.260] | – | – | – | 1.176 | – |
| E03 | laya_en_fixed | th_th | review_before_deploy | noul | 200 | 1.000 | 0.025 | 0.635 [0.570, 0.705] | – | 0.242 | 0.162 | – | 0.666 |
| E04 | laya_en_fixed | en_en | impact_severity | score | 200 | 1.000 | 0.000 | 0.470 [0.405, 0.540] | – | – | – | 0.778 | – |
| E04 | laya_en_fixed | en_en | impact_tier | choice | 200 | 1.000 | 0.000 | 0.320 [0.255, 0.380] | 0.275 [0.220, 0.325] | 0.746 | 0.110 | – | – |
| E04 | laya_en_fixed | en_en | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.475 [0.405, 0.540] | – | 0.302 | 0.208 | – | 0.436 |
| E04 | laya_en_fixed | th_th | impact_severity | score | 200 | 1.000 | 0.635 | 0.390 [0.320, 0.460] | – | – | – | 0.859 | – |
| E04 | laya_en_fixed | th_th | impact_tier | choice | 200 | 1.000 | 0.635 | 0.105 [0.065, 0.150] | 0.058 [0.034, 0.086] | 0.982 | 0.333 | – | – |
| E04 | laya_en_fixed | th_th | needs_owner_escalation | noul | 200 | 1.000 | 0.635 | 0.495 [0.425, 0.565] | – | 0.369 | 0.287 | – | 0.394 |
| E05 | laya_en_fixed | en_en | data_class | choice | 200 | 1.000 | 0.000 | 0.310 [0.250, 0.375] | 0.213 [0.171, 0.257] | 0.830 | 0.214 | – | – |
| E05 | laya_en_fixed | en_en | handling_tier | score | 200 | 1.000 | 0.000 | 0.350 [0.285, 0.415] | – | – | – | 0.761 | – |
| E05 | laya_en_fixed | en_en | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.695 [0.635, 0.755] | – | 0.280 | 0.265 | – | 0.641 |
| E05 | laya_en_fixed | th_th | data_class | choice | 200 | 1.000 | 0.000 | 0.235 [0.180, 0.295] | 0.093 [0.071, 0.120] | 0.817 | 0.164 | – | – |
| E05 | laya_en_fixed | th_th | handling_tier | score | 200 | 1.000 | 0.000 | 0.295 [0.235, 0.355] | – | – | – | 0.865 | – |
| E05 | laya_en_fixed | th_th | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.600 [0.530, 0.670] | – | 0.300 | 0.250 | – | 0.604 |
| E06 | laya_en_fixed | en_en | candidate_quality | score | 201 | 1.000 | 0.030 | 0.373 [0.303, 0.443] | – | – | – | 1.287 | – |
| E06 | laya_en_fixed | en_en | candidate_safe_to_consider | noul | 201 | 1.000 | 0.030 | 0.602 [0.532, 0.672] | – | 0.243 | 0.120 | – | 0.596 |
| E06 | laya_en_fixed | en_en | candidate_verdict | choice | 201 | 1.000 | 0.030 | 0.229 [0.174, 0.289] | 0.097 [0.077, 0.116] | 0.833 | 0.267 | – | – |
| E06 | laya_en_fixed | en_en | intent_type | choice | 201 | 1.000 | 0.030 | 0.323 [0.259, 0.388] | 0.307 [0.245, 0.358] | 0.776 | 0.221 | – | – |
| E06 | laya_en_fixed | en_en | needs_clarification | noul | 201 | 1.000 | 0.030 | 0.279 [0.219, 0.338] | – | 0.411 | 0.408 | – | 0.224 |
| E06 | laya_en_fixed | th_th | candidate_quality | score | 201 | 1.000 | 0.791 | 0.199 [0.149, 0.254] | – | – | – | 1.156 | – |
| E06 | laya_en_fixed | th_th | candidate_safe_to_consider | noul | 201 | 1.000 | 0.791 | 0.627 [0.562, 0.692] | – | 0.239 | 0.081 | – | 0.532 |
| E06 | laya_en_fixed | th_th | candidate_verdict | choice | 201 | 1.000 | 0.791 | 0.303 [0.244, 0.368] | 0.246 [0.195, 0.298] | 0.745 | 0.115 | – | – |
| E06 | laya_en_fixed | th_th | intent_type | choice | 201 | 1.000 | 0.791 | 0.229 [0.174, 0.284] | 0.162 [0.117, 0.205] | 0.845 | 0.070 | – | – |
| E06 | laya_en_fixed | th_th | needs_clarification | noul | 201 | 1.000 | 0.791 | 0.438 [0.373, 0.502] | – | 0.347 | 0.289 | – | 0.394 |
| E01 | laya_multi_fixed | en_en | failure_family | choice | 200 | 1.000 | 0.000 | 0.295 [0.230, 0.360] | 0.284 [0.219, 0.341] | 0.968 | 0.280 | – | – |
| E01 | laya_multi_fixed | en_en | requires_human | noul | 200 | 1.000 | 0.000 | 0.575 [0.505, 0.640] | – | 0.286 | 0.197 | – | 0.488 |
| E01 | laya_multi_fixed | en_en | severity | score | 200 | 1.000 | 0.000 | 0.230 [0.175, 0.290] | – | – | – | 1.022 | – |
| E01 | laya_multi_fixed | th_th | failure_family | choice | 200 | 1.000 | 0.000 | 0.250 [0.185, 0.310] | 0.203 [0.146, 0.253] | 1.012 | 0.333 | – | – |
| E01 | laya_multi_fixed | th_th | requires_human | noul | 200 | 1.000 | 0.000 | 0.715 [0.655, 0.775] | – | 0.231 | 0.169 | – | 0.599 |
| E01 | laya_multi_fixed | th_th | severity | score | 200 | 1.000 | 0.000 | 0.225 [0.170, 0.285] | – | – | – | 1.019 | – |
| E02 | laya_multi_fixed | en_en | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.270 [0.210, 0.330] | 0.193 [0.158, 0.227] | 0.975 | 0.361 | – | – |
| E02 | laya_multi_fixed | en_en | investigate_now | noul | 200 | 1.000 | 0.000 | 0.320 [0.260, 0.385] | – | 0.653 | 0.658 | – | 0.549 |
| E02 | laya_multi_fixed | en_en | quality_impact | score | 200 | 1.000 | 0.000 | 0.210 [0.155, 0.265] | – | – | – | 1.228 | – |
| E02 | laya_multi_fixed | th_th | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.240 [0.180, 0.300] | 0.228 [0.166, 0.283] | 1.185 | 0.510 | – | – |
| E02 | laya_multi_fixed | th_th | investigate_now | noul | 200 | 1.000 | 0.000 | 0.325 [0.260, 0.395] | – | 0.675 | 0.674 | – | 0.491 |
| E02 | laya_multi_fixed | th_th | quality_impact | score | 200 | 1.000 | 0.000 | 0.215 [0.165, 0.275] | – | – | – | 1.294 | – |
| E03 | laya_multi_fixed | en_en | drift_impact | choice | 200 | 1.000 | 0.000 | 0.255 [0.195, 0.320] | 0.186 [0.145, 0.226] | 1.158 | 0.502 | – | – |
| E03 | laya_multi_fixed | en_en | migration_risk | score | 200 | 1.000 | 0.000 | 0.155 [0.110, 0.205] | – | – | – | 1.060 | – |
| E03 | laya_multi_fixed | en_en | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.660 [0.595, 0.725] | – | 0.221 | 0.146 | – | 0.722 |
| E03 | laya_multi_fixed | th_th | drift_impact | choice | 200 | 1.000 | 0.000 | 0.260 [0.205, 0.325] | 0.197 [0.160, 0.233] | 1.030 | 0.387 | – | – |
| E03 | laya_multi_fixed | th_th | migration_risk | score | 200 | 1.000 | 0.000 | 0.145 [0.100, 0.195] | – | – | – | 1.238 | – |
| E03 | laya_multi_fixed | th_th | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.660 [0.595, 0.725] | – | 0.265 | 0.245 | – | 0.663 |
| E04 | laya_multi_fixed | en_en | impact_severity | score | 200 | 1.000 | 0.000 | 0.380 [0.315, 0.450] | – | – | – | 0.930 | – |
| E04 | laya_multi_fixed | en_en | impact_tier | choice | 200 | 1.000 | 0.000 | 0.350 [0.285, 0.415] | 0.305 [0.244, 0.358] | 0.855 | 0.236 | – | – |
| E04 | laya_multi_fixed | en_en | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.505 [0.440, 0.575] | – | 0.399 | 0.357 | – | 0.495 |
| E04 | laya_multi_fixed | th_th | impact_severity | score | 200 | 1.000 | 0.000 | 0.375 [0.310, 0.445] | – | – | – | 0.909 | – |
| E04 | laya_multi_fixed | th_th | impact_tier | choice | 200 | 1.000 | 0.000 | 0.270 [0.210, 0.330] | 0.182 [0.134, 0.228] | 1.075 | 0.376 | – | – |
| E04 | laya_multi_fixed | th_th | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.525 [0.460, 0.595] | – | 0.389 | 0.362 | – | 0.527 |
| E05 | laya_multi_fixed | en_en | data_class | choice | 200 | 1.000 | 0.000 | 0.240 [0.185, 0.300] | 0.220 [0.165, 0.274] | 0.911 | 0.266 | – | – |
| E05 | laya_multi_fixed | en_en | handling_tier | score | 200 | 1.000 | 0.000 | 0.310 [0.250, 0.375] | – | – | – | 0.822 | – |
| E05 | laya_multi_fixed | en_en | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.635 [0.570, 0.700] | – | 0.287 | 0.275 | – | 0.611 |
| E05 | laya_multi_fixed | th_th | data_class | choice | 200 | 1.000 | 0.000 | 0.220 [0.165, 0.280] | 0.165 [0.122, 0.209] | 1.135 | 0.430 | – | – |
| E05 | laya_multi_fixed | th_th | handling_tier | score | 200 | 1.000 | 0.000 | 0.285 [0.220, 0.350] | – | – | – | 0.761 | – |
| E05 | laya_multi_fixed | th_th | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.675 [0.610, 0.745] | – | 0.323 | 0.323 | – | 0.591 |
| E06 | laya_multi_fixed | en_en | candidate_quality | score | 201 | 1.000 | 0.000 | 0.239 [0.184, 0.299] | – | – | – | 1.504 | – |
| E06 | laya_multi_fixed | en_en | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 0.796 [0.736, 0.851] | – | 0.182 | 0.153 | – | 0.526 |
| E06 | laya_multi_fixed | en_en | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.239 [0.184, 0.299] | 0.108 [0.082, 0.140] | 0.910 | 0.345 | – | – |
| E06 | laya_multi_fixed | en_en | intent_type | choice | 201 | 1.000 | 0.000 | 0.453 [0.383, 0.522] | 0.470 [0.401, 0.531] | 0.761 | 0.191 | – | – |
| E06 | laya_multi_fixed | en_en | needs_clarification | noul | 201 | 1.000 | 0.000 | 0.249 [0.189, 0.313] | – | 0.660 | 0.666 | – | 0.173 |
| E06 | laya_multi_fixed | th_th | candidate_quality | score | 201 | 1.000 | 0.000 | 0.328 [0.264, 0.398] | – | – | – | 1.513 | – |
| E06 | laya_multi_fixed | th_th | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 0.801 [0.746, 0.856] | – | 0.188 | 0.168 | – | 0.617 |
| E06 | laya_multi_fixed | th_th | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.323 [0.264, 0.388] | 0.275 [0.208, 0.337] | 0.797 | 0.217 | – | – |
| E06 | laya_multi_fixed | th_th | intent_type | choice | 201 | 1.000 | 0.000 | 0.393 [0.323, 0.458] | 0.421 [0.365, 0.471] | 0.780 | 0.182 | – | – |
| E06 | laya_multi_fixed | th_th | needs_clarification | noul | 201 | 1.000 | 0.000 | 0.249 [0.189, 0.308] | – | 0.702 | 0.692 | – | 0.217 |
| E01 | laya_routed | en_en | failure_family | choice | 200 | 1.000 | 0.000 | 0.150 [0.105, 0.195] | 0.037 [0.027, 0.047] | 1.414 | 0.738 | – | – |
| E01 | laya_routed | en_en | requires_human | noul | 200 | 1.000 | 0.000 | 0.335 [0.270, 0.400] | – | 0.380 | 0.387 | – | 0.510 |
| E01 | laya_routed | en_en | severity | score | 200 | 1.000 | 0.000 | 0.315 [0.255, 0.380] | – | – | – | 0.843 | – |
| E01 | laya_routed | th_th | failure_family | choice | 200 | 1.000 | 0.000 | 0.250 [0.185, 0.310] | 0.203 [0.146, 0.253] | 1.012 | 0.333 | – | – |
| E01 | laya_routed | th_th | requires_human | noul | 200 | 1.000 | 0.000 | 0.715 [0.655, 0.775] | – | 0.231 | 0.169 | – | 0.599 |
| E01 | laya_routed | th_th | severity | score | 200 | 1.000 | 0.000 | 0.225 [0.170, 0.285] | – | – | – | 1.019 | – |
| E02 | laya_routed | en_en | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.625 [0.555, 0.695] | 0.710 [0.658, 0.755] | 0.548 | 0.196 | – | – |
| E02 | laya_routed | en_en | investigate_now | noul | 200 | 1.000 | 0.000 | 0.670 [0.605, 0.730] | – | 0.234 | 0.162 | – | 0.575 |
| E02 | laya_routed | en_en | quality_impact | score | 200 | 1.000 | 0.000 | 0.325 [0.260, 0.390] | – | – | – | 1.014 | – |
| E02 | laya_routed | th_th | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.240 [0.180, 0.300] | 0.228 [0.166, 0.283] | 1.185 | 0.510 | – | – |
| E02 | laya_routed | th_th | investigate_now | noul | 200 | 1.000 | 0.000 | 0.325 [0.260, 0.395] | – | 0.675 | 0.674 | – | 0.491 |
| E02 | laya_routed | th_th | quality_impact | score | 200 | 1.000 | 0.000 | 0.215 [0.165, 0.275] | – | – | – | 1.294 | – |
| E03 | laya_routed | en_en | drift_impact | choice | 200 | 1.000 | 0.000 | 0.510 [0.445, 0.580] | 0.502 [0.449, 0.554] | 0.649 | 0.122 | – | – |
| E03 | laya_routed | en_en | migration_risk | score | 200 | 1.000 | 0.000 | 0.370 [0.300, 0.440] | – | – | – | 0.933 | – |
| E03 | laya_routed | en_en | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.390 [0.320, 0.455] | – | 0.365 | 0.345 | – | 0.573 |
| E03 | laya_routed | th_th | drift_impact | choice | 200 | 1.000 | 0.000 | 0.260 [0.205, 0.325] | 0.197 [0.160, 0.233] | 1.030 | 0.387 | – | – |
| E03 | laya_routed | th_th | migration_risk | score | 200 | 1.000 | 0.000 | 0.145 [0.100, 0.195] | – | – | – | 1.238 | – |
| E03 | laya_routed | th_th | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.660 [0.595, 0.725] | – | 0.265 | 0.245 | – | 0.663 |
| E04 | laya_routed | en_en | impact_severity | score | 200 | 1.000 | 0.000 | 0.470 [0.405, 0.540] | – | – | – | 0.778 | – |
| E04 | laya_routed | en_en | impact_tier | choice | 200 | 1.000 | 0.000 | 0.320 [0.255, 0.380] | 0.275 [0.220, 0.325] | 0.746 | 0.110 | – | – |
| E04 | laya_routed | en_en | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.475 [0.405, 0.540] | – | 0.302 | 0.208 | – | 0.436 |
| E04 | laya_routed | th_th | impact_severity | score | 200 | 1.000 | 0.000 | 0.375 [0.310, 0.445] | – | – | – | 0.909 | – |
| E04 | laya_routed | th_th | impact_tier | choice | 200 | 1.000 | 0.000 | 0.270 [0.210, 0.330] | 0.182 [0.134, 0.228] | 1.075 | 0.376 | – | – |
| E04 | laya_routed | th_th | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 0.525 [0.460, 0.595] | – | 0.389 | 0.362 | – | 0.527 |
| E05 | laya_routed | en_en | data_class | choice | 200 | 1.000 | 0.000 | 0.310 [0.250, 0.375] | 0.213 [0.171, 0.257] | 0.825 | 0.222 | – | – |
| E05 | laya_routed | en_en | handling_tier | score | 200 | 1.000 | 0.000 | 0.350 [0.285, 0.415] | – | – | – | 0.758 | – |
| E05 | laya_routed | en_en | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.695 [0.635, 0.755] | – | 0.280 | 0.265 | – | 0.639 |
| E05 | laya_routed | th_th | data_class | choice | 200 | 1.000 | 0.000 | 0.220 [0.165, 0.280] | 0.165 [0.122, 0.209] | 1.135 | 0.430 | – | – |
| E05 | laya_routed | th_th | handling_tier | score | 200 | 1.000 | 0.000 | 0.285 [0.220, 0.350] | – | – | – | 0.761 | – |
| E05 | laya_routed | th_th | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.675 [0.610, 0.745] | – | 0.323 | 0.323 | – | 0.591 |
| E06 | laya_routed | en_en | candidate_quality | score | 201 | 1.000 | 0.030 | 0.373 [0.303, 0.443] | – | – | – | 1.287 | – |
| E06 | laya_routed | en_en | candidate_safe_to_consider | noul | 201 | 1.000 | 0.030 | 0.602 [0.532, 0.672] | – | 0.243 | 0.120 | – | 0.596 |
| E06 | laya_routed | en_en | candidate_verdict | choice | 201 | 1.000 | 0.030 | 0.229 [0.174, 0.289] | 0.097 [0.077, 0.116] | 0.833 | 0.267 | – | – |
| E06 | laya_routed | en_en | intent_type | choice | 201 | 1.000 | 0.030 | 0.323 [0.259, 0.388] | 0.307 [0.245, 0.358] | 0.776 | 0.221 | – | – |
| E06 | laya_routed | en_en | needs_clarification | noul | 201 | 1.000 | 0.030 | 0.279 [0.219, 0.338] | – | 0.411 | 0.408 | – | 0.224 |
| E06 | laya_routed | th_th | candidate_quality | score | 201 | 1.000 | 0.000 | 0.328 [0.264, 0.398] | – | – | – | 1.513 | – |
| E06 | laya_routed | th_th | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 0.801 [0.746, 0.856] | – | 0.188 | 0.168 | – | 0.617 |
| E06 | laya_routed | th_th | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.323 [0.264, 0.388] | 0.275 [0.208, 0.337] | 0.797 | 0.217 | – | – |
| E06 | laya_routed | th_th | intent_type | choice | 201 | 1.000 | 0.000 | 0.393 [0.323, 0.458] | 0.421 [0.365, 0.471] | 0.780 | 0.182 | – | – |
| E06 | laya_routed | th_th | needs_clarification | noul | 201 | 1.000 | 0.000 | 0.249 [0.189, 0.308] | – | 0.702 | 0.692 | – | 0.217 |
| E01 | rules_only | en_en | failure_family | choice | 200 | 0.860 | 0.000 | 0.860 [0.810, 0.905] | 0.857 [0.857, 0.857] | – | – | – | – |
| E01 | rules_only | en_en | requires_human | noul | 200 | 0.905 | 0.000 | 0.905 [0.865, 0.945] | – | – | – | – | – |
| E01 | rules_only | en_en | severity | score | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E01 | rules_only | th_th | failure_family | choice | 200 | 0.860 | 0.000 | 0.860 [0.810, 0.905] | 0.857 [0.857, 0.857] | – | – | – | – |
| E01 | rules_only | th_th | requires_human | noul | 200 | 0.905 | 0.000 | 0.905 [0.865, 0.945] | – | – | – | – | – |
| E01 | rules_only | th_th | severity | score | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E02 | rules_only | en_en | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.940 [0.905, 0.970] | 0.953 [0.923, 0.978] | – | – | – | – |
| E02 | rules_only | en_en | investigate_now | noul | 200 | 1.000 | 0.000 | 0.980 [0.960, 0.995] | – | – | – | – | – |
| E02 | rules_only | en_en | quality_impact | score | 200 | 1.000 | 0.000 | 0.955 [0.925, 0.980] | – | – | – | – | – |
| E02 | rules_only | th_th | anomaly_family | choice | 200 | 1.000 | 0.000 | 0.940 [0.905, 0.970] | 0.953 [0.923, 0.978] | – | – | – | – |
| E02 | rules_only | th_th | investigate_now | noul | 200 | 1.000 | 0.000 | 0.980 [0.960, 0.995] | – | – | – | – | – |
| E02 | rules_only | th_th | quality_impact | score | 200 | 1.000 | 0.000 | 0.955 [0.925, 0.980] | – | – | – | – | – |
| E03 | rules_only | en_en | drift_impact | choice | 200 | 1.000 | 0.000 | 0.980 [0.960, 0.995] | 0.979 [0.955, 0.995] | – | – | – | – |
| E03 | rules_only | en_en | migration_risk | score | 200 | 1.000 | 0.000 | 0.740 [0.680, 0.800] | – | – | – | – | – |
| E03 | rules_only | en_en | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.935 [0.900, 0.970] | – | – | – | – | – |
| E03 | rules_only | th_th | drift_impact | choice | 200 | 1.000 | 0.000 | 0.980 [0.960, 0.995] | 0.979 [0.955, 0.995] | – | – | – | – |
| E03 | rules_only | th_th | migration_risk | score | 200 | 1.000 | 0.000 | 0.740 [0.680, 0.800] | – | – | – | – | – |
| E03 | rules_only | th_th | review_before_deploy | noul | 200 | 1.000 | 0.000 | 0.935 [0.900, 0.970] | – | – | – | – | – |
| E04 | rules_only | en_en | impact_severity | score | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E04 | rules_only | en_en | impact_tier | choice | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | – | – | – | – |
| E04 | rules_only | en_en | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E04 | rules_only | th_th | impact_severity | score | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E04 | rules_only | th_th | impact_tier | choice | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | – | – | – | – |
| E04 | rules_only | th_th | needs_owner_escalation | noul | 200 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E05 | rules_only | en_en | data_class | choice | 200 | 1.000 | 0.000 | 0.880 [0.835, 0.925] | 0.880 [0.832, 0.922] | – | – | – | – |
| E05 | rules_only | en_en | handling_tier | score | 200 | 1.000 | 0.000 | 0.845 [0.795, 0.890] | – | – | – | – | – |
| E05 | rules_only | en_en | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.935 [0.900, 0.965] | – | – | – | – | – |
| E05 | rules_only | th_th | data_class | choice | 200 | 1.000 | 0.000 | 0.880 [0.835, 0.925] | 0.880 [0.832, 0.922] | – | – | – | – |
| E05 | rules_only | th_th | handling_tier | score | 200 | 1.000 | 0.000 | 0.845 [0.795, 0.890] | – | – | – | – | – |
| E05 | rules_only | th_th | requires_masking_review | noul | 200 | 1.000 | 0.000 | 0.935 [0.900, 0.965] | – | – | – | – | – |
| E06 | rules_only | en_en | candidate_quality | score | 201 | 1.000 | 0.000 | 0.766 [0.706, 0.826] | – | – | – | – | – |
| E06 | rules_only | en_en | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | en_en | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.821 [0.766, 0.871] | 0.832 [0.787, 0.874] | – | – | – | – |
| E06 | rules_only | en_en | intent_type | choice | 201 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | – | – | – | – |
| E06 | rules_only | en_en | needs_clarification | noul | 201 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | th_th | candidate_quality | score | 201 | 1.000 | 0.000 | 0.766 [0.706, 0.826] | – | – | – | – | – |
| E06 | rules_only | th_th | candidate_safe_to_consider | noul | 201 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |
| E06 | rules_only | th_th | candidate_verdict | choice | 201 | 1.000 | 0.000 | 0.821 [0.766, 0.871] | 0.832 [0.787, 0.874] | – | – | – | – |
| E06 | rules_only | th_th | intent_type | choice | 201 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | – | – | – | – |
| E06 | rules_only | th_th | needs_clarification | noul | 201 | 1.000 | 0.000 | 1.000 [1.000, 1.000] | – | – | – | – | – |

## Table 2 — paired Laya − Jev deltas

All questions; Δ = Laya routed − Jev 1.14 on identical scenarios and requests. Negative favours Jev.


| experiment_id | variant | question_id | metric | n_scenarios | a | b | delta | ci_low | ci_high | mcnemar_p |
|---|---|---|---|---|---|---|---|---|---|---|
| E01 | en_en | failure_family | effective_accuracy | 200 | 0.150 | 0.880 | -0.730 | -0.795 | -0.665 | 0.000 |
| E01 | en_en | failure_family | macro_f1 | 200 | 0.037 | 0.863 | -0.825 | -0.875 | -0.774 | 0.000 |
| E01 | en_en | requires_human | effective_accuracy | 200 | 0.335 | 0.690 | -0.355 | -0.480 | -0.225 | 0.000 |
| E01 | en_en | severity | effective_accuracy | 200 | 0.315 | 0.720 | -0.405 | -0.505 | -0.305 | 0.000 |
| E01 | th_th | failure_family | effective_accuracy | 200 | 0.250 | 0.915 | -0.665 | -0.730 | -0.605 | 0.000 |
| E01 | th_th | failure_family | macro_f1 | 200 | 0.203 | 0.909 | -0.706 | -0.771 | -0.645 | 0.000 |
| E01 | th_th | requires_human | effective_accuracy | 200 | 0.715 | 0.765 | -0.050 | -0.105 | 0.005 | 0.123 |
| E01 | th_th | severity | effective_accuracy | 200 | 0.225 | 0.795 | -0.570 | -0.670 | -0.470 | 0.000 |
| E02 | en_en | anomaly_family | effective_accuracy | 200 | 0.625 | 0.825 | -0.200 | -0.265 | -0.130 | 0.000 |
| E02 | en_en | anomaly_family | macro_f1 | 200 | 0.710 | 0.877 | -0.167 | -0.222 | -0.114 | 0.000 |
| E02 | en_en | investigate_now | effective_accuracy | 200 | 0.670 | 0.660 | 0.010 | -0.105 | 0.115 | 0.929 |
| E02 | en_en | quality_impact | effective_accuracy | 200 | 0.325 | 0.460 | -0.135 | -0.220 | -0.050 | 0.003 |
| E02 | th_th | anomaly_family | effective_accuracy | 200 | 0.240 | 0.820 | -0.580 | -0.650 | -0.505 | 0.000 |
| E02 | th_th | anomaly_family | macro_f1 | 200 | 0.228 | 0.873 | -0.644 | -0.712 | -0.577 | 0.000 |
| E02 | th_th | investigate_now | effective_accuracy | 200 | 0.325 | 0.600 | -0.275 | -0.340 | -0.205 | 0.000 |
| E02 | th_th | quality_impact | effective_accuracy | 200 | 0.215 | 0.455 | -0.240 | -0.310 | -0.165 | 0.000 |
| E03 | en_en | drift_impact | effective_accuracy | 200 | 0.510 | 0.790 | -0.280 | -0.350 | -0.205 | 0.000 |
| E03 | en_en | drift_impact | macro_f1 | 200 | 0.502 | 0.797 | -0.295 | -0.360 | -0.227 | 0.000 |
| E03 | en_en | migration_risk | effective_accuracy | 200 | 0.370 | 0.255 | 0.115 | 0.010 | 0.220 | 0.044 |
| E03 | en_en | review_before_deploy | effective_accuracy | 200 | 0.390 | 0.780 | -0.390 | -0.500 | -0.285 | 0.000 |
| E03 | th_th | drift_impact | effective_accuracy | 200 | 0.260 | 0.800 | -0.540 | -0.615 | -0.455 | 0.000 |
| E03 | th_th | drift_impact | macro_f1 | 200 | 0.197 | 0.808 | -0.610 | -0.665 | -0.555 | 0.000 |
| E03 | th_th | migration_risk | effective_accuracy | 200 | 0.145 | 0.495 | -0.350 | -0.450 | -0.250 | 0.000 |
| E03 | th_th | review_before_deploy | effective_accuracy | 200 | 0.660 | 0.690 | -0.030 | -0.060 | 0.000 | 0.109 |
| E04 | en_en | impact_severity | effective_accuracy | 200 | 0.470 | 0.875 | -0.405 | -0.475 | -0.330 | 0.000 |
| E04 | en_en | impact_tier | effective_accuracy | 200 | 0.320 | 0.785 | -0.465 | -0.535 | -0.395 | 0.000 |
| E04 | en_en | impact_tier | macro_f1 | 200 | 0.275 | 0.711 | -0.436 | -0.493 | -0.381 | 0.000 |
| E04 | en_en | needs_owner_escalation | effective_accuracy | 200 | 0.475 | 0.485 | -0.010 | -0.080 | 0.060 | 0.890 |
| E04 | th_th | impact_severity | effective_accuracy | 200 | 0.375 | 0.890 | -0.515 | -0.585 | -0.445 | 0.000 |
| E04 | th_th | impact_tier | effective_accuracy | 200 | 0.270 | 0.785 | -0.515 | -0.590 | -0.445 | 0.000 |
| E04 | th_th | impact_tier | macro_f1 | 200 | 0.182 | 0.711 | -0.529 | -0.581 | -0.476 | 0.000 |
| E04 | th_th | needs_owner_escalation | effective_accuracy | 200 | 0.525 | 0.480 | 0.045 | -0.030 | 0.120 | 0.306 |
| E05 | en_en | data_class | effective_accuracy | 200 | 0.310 | 0.785 | -0.475 | -0.555 | -0.395 | 0.000 |
| E05 | en_en | data_class | macro_f1 | 200 | 0.213 | 0.800 | -0.586 | -0.650 | -0.521 | 0.000 |
| E05 | en_en | handling_tier | effective_accuracy | 200 | 0.350 | 0.690 | -0.340 | -0.410 | -0.265 | 0.000 |
| E05 | en_en | requires_masking_review | effective_accuracy | 200 | 0.695 | 0.715 | -0.020 | -0.050 | 0.010 | 0.344 |
| E05 | th_th | data_class | effective_accuracy | 200 | 0.220 | 0.810 | -0.590 | -0.665 | -0.510 | 0.000 |
| E05 | th_th | data_class | macro_f1 | 200 | 0.165 | 0.820 | -0.655 | -0.720 | -0.591 | 0.000 |
| E05 | th_th | handling_tier | effective_accuracy | 200 | 0.285 | 0.710 | -0.425 | -0.530 | -0.315 | 0.000 |
| E05 | th_th | requires_masking_review | effective_accuracy | 200 | 0.675 | 0.710 | -0.035 | -0.070 | -0.005 | 0.065 |
| E06 | en_en | candidate_quality | effective_accuracy | 201 | 0.373 | 0.443 | -0.070 | -0.169 | 0.030 | 0.185 |
| E06 | en_en | candidate_safe_to_consider | effective_accuracy | 201 | 0.602 | 0.965 | -0.363 | -0.433 | -0.299 | 0.000 |
| E06 | en_en | candidate_verdict | effective_accuracy | 201 | 0.229 | 0.557 | -0.328 | -0.408 | -0.249 | 0.000 |
| E06 | en_en | candidate_verdict | macro_f1 | 201 | 0.097 | 0.475 | -0.378 | -0.429 | -0.320 | 0.000 |
| E06 | en_en | intent_type | effective_accuracy | 201 | 0.323 | 0.701 | -0.378 | -0.458 | -0.303 | 0.000 |
| E06 | en_en | intent_type | macro_f1 | 201 | 0.307 | 0.669 | -0.362 | -0.425 | -0.305 | 0.000 |
| E06 | en_en | needs_clarification | effective_accuracy | 201 | 0.279 | 0.701 | -0.423 | -0.507 | -0.333 | 0.000 |
| E06 | th_th | candidate_quality | effective_accuracy | 201 | 0.328 | 0.388 | -0.060 | -0.149 | 0.040 | 0.251 |
| E06 | th_th | candidate_safe_to_consider | effective_accuracy | 201 | 0.801 | 0.915 | -0.114 | -0.169 | -0.060 | 0.000 |
| E06 | th_th | candidate_verdict | effective_accuracy | 201 | 0.323 | 0.522 | -0.199 | -0.299 | -0.104 | 0.000 |
| E06 | th_th | candidate_verdict | macro_f1 | 201 | 0.275 | 0.435 | -0.160 | -0.253 | -0.065 | 0.000 |
| E06 | th_th | intent_type | effective_accuracy | 201 | 0.393 | 0.701 | -0.308 | -0.388 | -0.234 | 0.000 |
| E06 | th_th | intent_type | macro_f1 | 201 | 0.421 | 0.669 | -0.248 | -0.308 | -0.188 | 0.000 |
| E06 | th_th | needs_clarification | effective_accuracy | 201 | 0.249 | 0.463 | -0.214 | -0.308 | -0.114 | 0.000 |


**P3 — each Laya checkpoint vs Jev (primary choice question, macro-F1):**


| experiment_id | variant | track_a | n_scenarios | a | b | delta | ci_low | ci_high |
|---|---|---|---|---|---|---|---|---|
| E01 | en_en | laya_routed | 200 | 0.037 | 0.863 | -0.825 | -0.875 | -0.774 |
| E01 | th_th | laya_routed | 200 | 0.203 | 0.909 | -0.706 | -0.771 | -0.645 |
| E02 | en_en | laya_routed | 200 | 0.710 | 0.877 | -0.167 | -0.222 | -0.114 |
| E02 | th_th | laya_routed | 200 | 0.228 | 0.873 | -0.644 | -0.712 | -0.577 |
| E03 | en_en | laya_routed | 200 | 0.502 | 0.797 | -0.295 | -0.360 | -0.227 |
| E03 | th_th | laya_routed | 200 | 0.197 | 0.808 | -0.610 | -0.665 | -0.555 |
| E04 | en_en | laya_routed | 200 | 0.275 | 0.711 | -0.436 | -0.493 | -0.381 |
| E04 | th_th | laya_routed | 200 | 0.182 | 0.711 | -0.529 | -0.581 | -0.476 |
| E05 | en_en | laya_routed | 200 | 0.213 | 0.800 | -0.586 | -0.650 | -0.521 |
| E05 | th_th | laya_routed | 200 | 0.165 | 0.820 | -0.655 | -0.720 | -0.591 |
| E06 | en_en | laya_routed | 201 | 0.097 | 0.475 | -0.378 | -0.429 | -0.320 |
| E06 | th_th | laya_routed | 201 | 0.275 | 0.435 | -0.160 | -0.253 | -0.065 |
| E01 | en_en | laya_en_fixed | 200 | 0.037 | 0.863 | -0.825 | -0.875 | -0.774 |
| E01 | th_th | laya_en_fixed | 200 | 0.037 | 0.909 | -0.872 | -0.913 | -0.827 |
| E02 | en_en | laya_en_fixed | 200 | 0.710 | 0.877 | -0.167 | -0.222 | -0.114 |
| E02 | th_th | laya_en_fixed | 200 | 0.289 | 0.873 | -0.584 | -0.625 | -0.539 |
| E03 | en_en | laya_en_fixed | 200 | 0.502 | 0.797 | -0.295 | -0.360 | -0.227 |
| E03 | th_th | laya_en_fixed | 200 | 0.181 | 0.808 | -0.627 | -0.699 | -0.556 |
| E04 | en_en | laya_en_fixed | 200 | 0.275 | 0.711 | -0.436 | -0.493 | -0.381 |
| E04 | th_th | laya_en_fixed | 200 | 0.058 | 0.711 | -0.653 | -0.688 | -0.615 |
| E05 | en_en | laya_en_fixed | 200 | 0.213 | 0.800 | -0.586 | -0.650 | -0.521 |
| E05 | th_th | laya_en_fixed | 200 | 0.093 | 0.820 | -0.727 | -0.779 | -0.672 |
| E06 | en_en | laya_en_fixed | 201 | 0.097 | 0.475 | -0.378 | -0.429 | -0.320 |
| E06 | th_th | laya_en_fixed | 201 | 0.246 | 0.435 | -0.189 | -0.260 | -0.112 |
| E01 | en_en | laya_multi_fixed | 200 | 0.284 | 0.863 | -0.578 | -0.652 | -0.508 |
| E01 | th_th | laya_multi_fixed | 200 | 0.203 | 0.909 | -0.706 | -0.771 | -0.645 |
| E02 | en_en | laya_multi_fixed | 200 | 0.193 | 0.877 | -0.685 | -0.732 | -0.632 |
| E02 | th_th | laya_multi_fixed | 200 | 0.228 | 0.873 | -0.644 | -0.712 | -0.577 |
| E03 | en_en | laya_multi_fixed | 200 | 0.186 | 0.797 | -0.611 | -0.669 | -0.551 |
| E03 | th_th | laya_multi_fixed | 200 | 0.197 | 0.808 | -0.610 | -0.665 | -0.555 |
| E04 | en_en | laya_multi_fixed | 200 | 0.305 | 0.711 | -0.406 | -0.471 | -0.347 |
| E04 | th_th | laya_multi_fixed | 200 | 0.182 | 0.711 | -0.529 | -0.581 | -0.476 |
| E05 | en_en | laya_multi_fixed | 200 | 0.220 | 0.800 | -0.579 | -0.652 | -0.503 |
| E05 | th_th | laya_multi_fixed | 200 | 0.165 | 0.820 | -0.655 | -0.720 | -0.591 |
| E06 | en_en | laya_multi_fixed | 201 | 0.108 | 0.475 | -0.368 | -0.421 | -0.308 |
| E06 | th_th | laya_multi_fixed | 201 | 0.275 | 0.435 | -0.160 | -0.253 | -0.065 |

## Table 3 — language gap within each model (TH − EN, paired)

| model_track | experiment_id | question_id | n_scenarios | a | b | delta | ci_low | ci_high |
|---|---|---|---|---|---|---|---|---|
| jev_114 | E01 | failure_family | 200 | 0.915 | 0.880 | 0.035 | 0.010 | 0.060 |
| jev_114 | E01 | requires_human | 200 | 0.765 | 0.690 | 0.075 | 0.040 | 0.115 |
| jev_114 | E01 | severity | 200 | 0.795 | 0.720 | 0.075 | 0.005 | 0.140 |
| jev_114 | E02 | anomaly_family | 200 | 0.820 | 0.825 | -0.005 | -0.020 | 0.010 |
| jev_114 | E02 | investigate_now | 200 | 0.600 | 0.660 | -0.060 | -0.100 | -0.025 |
| jev_114 | E02 | quality_impact | 200 | 0.455 | 0.460 | -0.005 | -0.065 | 0.050 |
| jev_114 | E03 | drift_impact | 200 | 0.800 | 0.790 | 0.010 | -0.015 | 0.035 |
| jev_114 | E03 | migration_risk | 200 | 0.495 | 0.255 | 0.240 | 0.165 | 0.320 |
| jev_114 | E03 | review_before_deploy | 200 | 0.690 | 0.780 | -0.090 | -0.145 | -0.030 |
| jev_114 | E04 | impact_severity | 200 | 0.890 | 0.875 | 0.015 | -0.020 | 0.050 |
| jev_114 | E04 | impact_tier | 200 | 0.785 | 0.785 | 0.000 | 0.000 | 0.000 |
| jev_114 | E04 | needs_owner_escalation | 200 | 0.480 | 0.485 | -0.005 | -0.015 | 0.000 |
| jev_114 | E05 | data_class | 200 | 0.810 | 0.785 | 0.025 | -0.005 | 0.060 |
| jev_114 | E05 | handling_tier | 200 | 0.710 | 0.690 | 0.020 | 0.000 | 0.045 |
| jev_114 | E05 | requires_masking_review | 200 | 0.710 | 0.715 | -0.005 | -0.020 | 0.010 |
| jev_114 | E06 | candidate_quality | 201 | 0.388 | 0.443 | -0.055 | -0.119 | 0.010 |
| jev_114 | E06 | candidate_safe_to_consider | 201 | 0.915 | 0.965 | -0.050 | -0.080 | -0.020 |
| jev_114 | E06 | candidate_verdict | 201 | 0.522 | 0.557 | -0.035 | -0.080 | 0.010 |
| jev_114 | E06 | intent_type | 201 | 0.701 | 0.701 | 0.000 | 0.000 | 0.000 |
| jev_114 | E06 | needs_clarification | 201 | 0.463 | 0.701 | -0.239 | -0.318 | -0.159 |
| laya_en_fixed | E01 | failure_family | 200 | 0.150 | 0.150 | 0.000 | 0.000 | 0.000 |
| laya_en_fixed | E01 | requires_human | 200 | 0.505 | 0.335 | 0.170 | 0.075 | 0.265 |
| laya_en_fixed | E01 | severity | 200 | 0.190 | 0.315 | -0.125 | -0.180 | -0.070 |
| laya_en_fixed | E02 | anomaly_family | 200 | 0.310 | 0.625 | -0.315 | -0.390 | -0.245 |
| laya_en_fixed | E02 | investigate_now | 200 | 0.335 | 0.670 | -0.335 | -0.450 | -0.215 |
| laya_en_fixed | E02 | quality_impact | 200 | 0.335 | 0.325 | 0.010 | -0.065 | 0.085 |
| laya_en_fixed | E03 | drift_impact | 200 | 0.210 | 0.510 | -0.300 | -0.385 | -0.220 |
| laya_en_fixed | E03 | migration_risk | 200 | 0.205 | 0.370 | -0.165 | -0.270 | -0.060 |
| laya_en_fixed | E03 | review_before_deploy | 200 | 0.635 | 0.390 | 0.245 | 0.120 | 0.380 |
| laya_en_fixed | E04 | impact_severity | 200 | 0.390 | 0.470 | -0.080 | -0.175 | 0.005 |
| laya_en_fixed | E04 | impact_tier | 200 | 0.105 | 0.320 | -0.215 | -0.280 | -0.150 |
| laya_en_fixed | E04 | needs_owner_escalation | 200 | 0.495 | 0.475 | 0.020 | -0.050 | 0.095 |
| laya_en_fixed | E05 | data_class | 200 | 0.235 | 0.310 | -0.075 | -0.145 | -0.005 |
| laya_en_fixed | E05 | handling_tier | 200 | 0.295 | 0.350 | -0.055 | -0.140 | 0.030 |
| laya_en_fixed | E05 | requires_masking_review | 200 | 0.600 | 0.695 | -0.095 | -0.170 | -0.020 |
| laya_en_fixed | E06 | candidate_quality | 201 | 0.199 | 0.373 | -0.174 | -0.274 | -0.075 |
| laya_en_fixed | E06 | candidate_safe_to_consider | 201 | 0.627 | 0.602 | 0.025 | -0.055 | 0.100 |
| laya_en_fixed | E06 | candidate_verdict | 201 | 0.303 | 0.229 | 0.075 | 0.015 | 0.134 |
| laya_en_fixed | E06 | intent_type | 201 | 0.229 | 0.323 | -0.095 | -0.159 | -0.015 |
| laya_en_fixed | E06 | needs_clarification | 201 | 0.438 | 0.279 | 0.159 | 0.080 | 0.239 |
| laya_multi_fixed | E01 | failure_family | 200 | 0.250 | 0.295 | -0.045 | -0.115 | 0.025 |
| laya_multi_fixed | E01 | requires_human | 200 | 0.715 | 0.575 | 0.140 | 0.065 | 0.215 |
| laya_multi_fixed | E01 | severity | 200 | 0.225 | 0.230 | -0.005 | -0.015 | 0.000 |
| laya_multi_fixed | E02 | anomaly_family | 200 | 0.240 | 0.270 | -0.030 | -0.105 | 0.045 |
| laya_multi_fixed | E02 | investigate_now | 200 | 0.325 | 0.320 | 0.005 | 0.000 | 0.015 |
| laya_multi_fixed | E02 | quality_impact | 200 | 0.215 | 0.210 | 0.005 | -0.015 | 0.030 |
| laya_multi_fixed | E03 | drift_impact | 200 | 0.260 | 0.255 | 0.005 | -0.060 | 0.070 |
| laya_multi_fixed | E03 | migration_risk | 200 | 0.145 | 0.155 | -0.010 | -0.075 | 0.060 |
| laya_multi_fixed | E03 | review_before_deploy | 200 | 0.660 | 0.660 | 0.000 | -0.030 | 0.025 |
| laya_multi_fixed | E04 | impact_severity | 200 | 0.375 | 0.380 | -0.005 | -0.045 | 0.035 |
| laya_multi_fixed | E04 | impact_tier | 200 | 0.270 | 0.350 | -0.080 | -0.140 | -0.015 |
| laya_multi_fixed | E04 | needs_owner_escalation | 200 | 0.525 | 0.505 | 0.020 | -0.050 | 0.090 |
| laya_multi_fixed | E05 | data_class | 200 | 0.220 | 0.240 | -0.020 | -0.080 | 0.045 |
| laya_multi_fixed | E05 | handling_tier | 200 | 0.285 | 0.310 | -0.025 | -0.120 | 0.065 |
| laya_multi_fixed | E05 | requires_masking_review | 200 | 0.675 | 0.635 | 0.040 | 0.015 | 0.070 |
| laya_multi_fixed | E06 | candidate_quality | 201 | 0.328 | 0.239 | 0.090 | 0.020 | 0.159 |
| laya_multi_fixed | E06 | candidate_safe_to_consider | 201 | 0.801 | 0.796 | 0.005 | 0.000 | 0.015 |
| laya_multi_fixed | E06 | candidate_verdict | 201 | 0.323 | 0.239 | 0.085 | 0.045 | 0.124 |
| laya_multi_fixed | E06 | intent_type | 201 | 0.393 | 0.453 | -0.060 | -0.124 | 0.005 |
| laya_multi_fixed | E06 | needs_clarification | 201 | 0.249 | 0.249 | 0.000 | -0.020 | 0.020 |
| laya_routed | E01 | failure_family | 200 | 0.250 | 0.150 | 0.100 | 0.035 | 0.170 |
| laya_routed | E01 | requires_human | 200 | 0.715 | 0.335 | 0.380 | 0.265 | 0.490 |
| laya_routed | E01 | severity | 200 | 0.225 | 0.315 | -0.090 | -0.130 | -0.055 |
| laya_routed | E02 | anomaly_family | 200 | 0.240 | 0.625 | -0.385 | -0.455 | -0.315 |
| laya_routed | E02 | investigate_now | 200 | 0.325 | 0.670 | -0.345 | -0.470 | -0.210 |
| laya_routed | E02 | quality_impact | 200 | 0.215 | 0.325 | -0.110 | -0.205 | -0.010 |
| laya_routed | E03 | drift_impact | 200 | 0.260 | 0.510 | -0.250 | -0.330 | -0.165 |
| laya_routed | E03 | migration_risk | 200 | 0.145 | 0.370 | -0.225 | -0.325 | -0.130 |
| laya_routed | E03 | review_before_deploy | 200 | 0.660 | 0.390 | 0.270 | 0.145 | 0.400 |
| laya_routed | E04 | impact_severity | 200 | 0.375 | 0.470 | -0.095 | -0.150 | -0.045 |
| laya_routed | E04 | impact_tier | 200 | 0.270 | 0.320 | -0.050 | -0.130 | 0.025 |
| laya_routed | E04 | needs_owner_escalation | 200 | 0.525 | 0.475 | 0.050 | -0.050 | 0.150 |
| laya_routed | E05 | data_class | 200 | 0.220 | 0.310 | -0.090 | -0.185 | 0.005 |
| laya_routed | E05 | handling_tier | 200 | 0.285 | 0.350 | -0.065 | -0.170 | 0.040 |
| laya_routed | E05 | requires_masking_review | 200 | 0.675 | 0.695 | -0.020 | -0.050 | 0.005 |
| laya_routed | E06 | candidate_quality | 201 | 0.328 | 0.373 | -0.045 | -0.114 | 0.025 |
| laya_routed | E06 | candidate_safe_to_consider | 201 | 0.801 | 0.602 | 0.199 | 0.119 | 0.284 |
| laya_routed | E06 | candidate_verdict | 201 | 0.323 | 0.229 | 0.095 | 0.055 | 0.139 |
| laya_routed | E06 | intent_type | 201 | 0.393 | 0.323 | 0.070 | -0.010 | 0.149 |
| laya_routed | E06 | needs_clarification | 201 | 0.249 | 0.279 | -0.030 | -0.114 | 0.055 |
| rules_only | E01 | failure_family | 200 | 0.860 | 0.860 | 0.000 | 0.000 | 0.000 |
| rules_only | E01 | requires_human | 200 | 0.905 | 0.905 | 0.000 | 0.000 | 0.000 |
| rules_only | E01 | severity | 200 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E02 | anomaly_family | 200 | 0.940 | 0.940 | 0.000 | 0.000 | 0.000 |
| rules_only | E02 | investigate_now | 200 | 0.980 | 0.980 | 0.000 | 0.000 | 0.000 |
| rules_only | E02 | quality_impact | 200 | 0.955 | 0.955 | 0.000 | 0.000 | 0.000 |
| rules_only | E03 | drift_impact | 200 | 0.980 | 0.980 | 0.000 | 0.000 | 0.000 |
| rules_only | E03 | migration_risk | 200 | 0.740 | 0.740 | 0.000 | 0.000 | 0.000 |
| rules_only | E03 | review_before_deploy | 200 | 0.935 | 0.935 | 0.000 | 0.000 | 0.000 |
| rules_only | E04 | impact_severity | 200 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E04 | impact_tier | 200 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E04 | needs_owner_escalation | 200 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E05 | data_class | 200 | 0.880 | 0.880 | 0.000 | 0.000 | 0.000 |
| rules_only | E05 | handling_tier | 200 | 0.845 | 0.845 | 0.000 | 0.000 | 0.000 |
| rules_only | E05 | requires_masking_review | 200 | 0.935 | 0.935 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | candidate_quality | 201 | 0.766 | 0.766 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | candidate_safe_to_consider | 201 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | candidate_verdict | 201 | 0.821 | 0.821 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | intent_type | 201 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| rules_only | E06 | needs_clarification | 201 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |

## Table 5 — model latency (warm, per request)

Local Laya is in-process inference on this host; it is not comparable to remote HTTPS latency.


| model_track | device | variant | n_requests | p50_ms | p95_ms | p99_ms | db_retrieval_p50_ms | topology |
|---|---|---|---|---|---|---|---|---|
| jev_114 | remote | en_en | 1199 | 264.4 | 362.4 | 486.8 | 23.8 | remote_https_from_this_host |
| jev_114 | remote | th_th | 1200 | 264.8 | 384.2 | 495.7 | 23.8 | remote_https_from_this_host |
| laya_en_fixed | cuda | en_en | 1201 | 115.9 | 219.2 | 268.2 | 23.8 | in_process_local |
| laya_en_fixed | cuda | th_th | 1201 | 175.2 | 294.4 | 299.6 | 23.8 | in_process_local |
| laya_multi_fixed | cuda | en_en | 1201 | 57.7 | 107.6 | 125.2 | 23.8 | in_process_local |
| laya_multi_fixed | cuda | th_th | 1201 | 63.3 | 122.3 | 146.1 | 23.8 | in_process_local |
| laya_routed | cuda | en_en | 1201 | 115.5 | 213.1 | 255.3 | 23.8 | in_process_local |
| laya_routed | cuda | th_th | 1201 | 76.0 | 128.5 | 153.1 | 23.8 | in_process_local |

## Table 6 — cost

| model_track | basis | requests | input_tokens | usd_total | sustained_decisions_per_hour | usd_per_1000_scenario_decisions |
|---|---|---|---|---|---|---|
| jev_114 | measured billed input tokens | 2402 | 2423321.0000 | 0.1018 | – | 0.0424 |
| laya_en_fixed | ASSUMED host price 0.5 USD/h, sequential warm throughput | 2402 | – | – | 23102.1494 | 0.0216 |
| laya_en_fixed | ASSUMED host price 1.0 USD/h, sequential warm throughput | 2402 | – | – | 23102.1494 | 0.0433 |
| laya_multi_fixed | ASSUMED host price 0.5 USD/h, sequential warm throughput | 2402 | – | – | 52526.2497 | 0.0095 |
| laya_multi_fixed | ASSUMED host price 1.0 USD/h, sequential warm throughput | 2402 | – | – | 52526.2497 | 0.0190 |
| laya_routed | ASSUMED host price 0.5 USD/h, sequential warm throughput | 2402 | – | – | 35705.7807 | 0.0140 |
| laya_routed | ASSUMED host price 1.0 USD/h, sequential warm throughput | 2402 | – | – | 35705.7807 | 0.0280 |

## Table 7 — high-risk failures

| experiment_id | question_id | model_track | variant | definition | n_positive | misses | miss_rate | invalid_on_positive | upper_95_if_zero |
|---|---|---|---|---|---|---|---|---|---|
| E01 | severity | jev_114 | en_en | severe incident (gold >= 2) predicted < 2 | 138 | 0 | 0.000 | 0 | 0.021 |
| E01 | severity | jev_114 | th_th | severe incident (gold >= 2) predicted < 2 | 138 | 0 | 0.000 | 0 | 0.021 |
| E01 | severity | laya_en_fixed | en_en | severe incident (gold >= 2) predicted < 2 | 138 | 118 | 0.855 | 0 | – |
| E01 | severity | laya_en_fixed | th_th | severe incident (gold >= 2) predicted < 2 | 138 | 136 | 0.986 | 0 | – |
| E01 | severity | laya_multi_fixed | en_en | severe incident (gold >= 2) predicted < 2 | 138 | 137 | 0.993 | 0 | – |
| E01 | severity | laya_multi_fixed | th_th | severe incident (gold >= 2) predicted < 2 | 138 | 138 | 1.000 | 0 | – |
| E01 | severity | laya_routed | en_en | severe incident (gold >= 2) predicted < 2 | 138 | 118 | 0.855 | 0 | – |
| E01 | severity | laya_routed | th_th | severe incident (gold >= 2) predicted < 2 | 138 | 138 | 1.000 | 0 | – |
| E01 | severity | rules_only | en_en | severe incident (gold >= 2) predicted < 2 | 138 | 0 | 0.000 | 0 | 0.021 |
| E01 | severity | rules_only | th_th | severe incident (gold >= 2) predicted < 2 | 138 | 0 | 0.000 | 0 | 0.021 |
| E03 | drift_impact | jev_114 | en_en | breaking change predicted non-breaking | 111 | 1 | 0.009 | 0 | – |
| E03 | drift_impact | jev_114 | th_th | breaking change predicted non-breaking | 111 | 1 | 0.009 | 0 | – |
| E03 | drift_impact | laya_en_fixed | en_en | breaking change predicted non-breaking | 111 | 10 | 0.090 | 0 | – |
| E03 | drift_impact | laya_en_fixed | th_th | breaking change predicted non-breaking | 111 | 91 | 0.820 | 0 | – |
| E03 | drift_impact | laya_multi_fixed | en_en | breaking change predicted non-breaking | 111 | 86 | 0.775 | 0 | – |
| E03 | drift_impact | laya_multi_fixed | th_th | breaking change predicted non-breaking | 111 | 77 | 0.694 | 0 | – |
| E03 | drift_impact | laya_routed | en_en | breaking change predicted non-breaking | 111 | 10 | 0.090 | 0 | – |
| E03 | drift_impact | laya_routed | th_th | breaking change predicted non-breaking | 111 | 77 | 0.694 | 0 | – |
| E03 | drift_impact | rules_only | en_en | breaking change predicted non-breaking | 111 | 0 | 0.000 | 0 | 0.027 |
| E03 | drift_impact | rules_only | th_th | breaking change predicted non-breaking | 111 | 0 | 0.000 | 0 | 0.027 |
| E04 | impact_tier | jev_114 | en_en | critical downstream impact missed | 34 | 0 | 0.000 | 0 | 0.084 |
| E04 | impact_tier | jev_114 | th_th | critical downstream impact missed | 34 | 0 | 0.000 | 0 | 0.084 |
| E04 | impact_tier | laya_en_fixed | en_en | critical downstream impact missed | 34 | 10 | 0.294 | 0 | – |
| E04 | impact_tier | laya_en_fixed | th_th | critical downstream impact missed | 34 | 16 | 0.471 | 0 | – |
| E04 | impact_tier | laya_multi_fixed | en_en | critical downstream impact missed | 34 | 7 | 0.206 | 0 | – |
| E04 | impact_tier | laya_multi_fixed | th_th | critical downstream impact missed | 34 | 20 | 0.588 | 0 | – |
| E04 | impact_tier | laya_routed | en_en | critical downstream impact missed | 34 | 10 | 0.294 | 0 | – |
| E04 | impact_tier | laya_routed | th_th | critical downstream impact missed | 34 | 20 | 0.588 | 0 | – |
| E04 | impact_tier | rules_only | en_en | critical downstream impact missed | 34 | 0 | 0.000 | 0 | 0.084 |
| E04 | impact_tier | rules_only | th_th | critical downstream impact missed | 34 | 0 | 0.000 | 0 | 0.084 |
| E05 | data_class | jev_114 | en_en | sensitive/direct identifier predicted non-personal | 85 | 0 | 0.000 | 0 | 0.035 |
| E05 | data_class | jev_114 | th_th | sensitive/direct identifier predicted non-personal | 85 | 0 | 0.000 | 0 | 0.035 |
| E05 | data_class | laya_en_fixed | en_en | sensitive/direct identifier predicted non-personal | 85 | 48 | 0.565 | 0 | – |
| E05 | data_class | laya_en_fixed | th_th | sensitive/direct identifier predicted non-personal | 85 | 0 | 0.000 | 0 | 0.035 |
| E05 | data_class | laya_multi_fixed | en_en | sensitive/direct identifier predicted non-personal | 85 | 24 | 0.282 | 0 | – |
| E05 | data_class | laya_multi_fixed | th_th | sensitive/direct identifier predicted non-personal | 85 | 1 | 0.012 | 0 | – |
| E05 | data_class | laya_routed | en_en | sensitive/direct identifier predicted non-personal | 85 | 48 | 0.565 | 0 | – |
| E05 | data_class | laya_routed | th_th | sensitive/direct identifier predicted non-personal | 85 | 1 | 0.012 | 0 | – |
| E05 | data_class | rules_only | en_en | sensitive/direct identifier predicted non-personal | 85 | 3 | 0.035 | 0 | – |
| E05 | data_class | rules_only | th_th | sensitive/direct identifier predicted non-personal | 85 | 3 | 0.035 | 0 | – |
| E06 | candidate_verdict | jev_114 | en_en | unsafe or wrong SQL accepted as correct | 103 | 12 | 0.117 | 0 | – |
| E06 | candidate_verdict | jev_114 | th_th | unsafe or wrong SQL accepted as correct | 103 | 9 | 0.087 | 0 | – |
| E06 | candidate_verdict | laya_en_fixed | en_en | unsafe or wrong SQL accepted as correct | 103 | 97 | 0.942 | 0 | – |
| E06 | candidate_verdict | laya_en_fixed | th_th | unsafe or wrong SQL accepted as correct | 103 | 60 | 0.583 | 0 | – |
| E06 | candidate_verdict | laya_multi_fixed | en_en | unsafe or wrong SQL accepted as correct | 103 | 101 | 0.981 | 0 | – |
| E06 | candidate_verdict | laya_multi_fixed | th_th | unsafe or wrong SQL accepted as correct | 103 | 88 | 0.854 | 0 | – |
| E06 | candidate_verdict | laya_routed | en_en | unsafe or wrong SQL accepted as correct | 103 | 97 | 0.942 | 0 | – |
| E06 | candidate_verdict | laya_routed | th_th | unsafe or wrong SQL accepted as correct | 103 | 88 | 0.854 | 0 | – |
| E06 | candidate_verdict | rules_only | en_en | unsafe or wrong SQL accepted as correct | 103 | 35 | 0.340 | 0 | – |
| E06 | candidate_verdict | rules_only | th_th | unsafe or wrong SQL accepted as correct | 103 | 35 | 0.340 | 0 | – |

## Table 8 — deterministic rules baseline vs models

| experiment_id | variant | question_id | model_track | n | rules_coverage | rules_acc | model_acc | rules_only_correct | model_only_correct | model_acc_where_rules_abstain |
|---|---|---|---|---|---|---|---|---|---|---|
| E01 | en_en | failure_family | jev_114 | 200 | 0.860 | 0.860 | 0.880 | 8 | 12 | 0.429 |
| E01 | en_en | failure_family | laya_en_fixed | 200 | 0.860 | 0.860 | 0.150 | 142 | 0 | 0.000 |
| E01 | en_en | failure_family | laya_multi_fixed | 200 | 0.860 | 0.860 | 0.295 | 115 | 2 | 0.071 |
| E01 | en_en | failure_family | laya_routed | 200 | 0.860 | 0.860 | 0.150 | 142 | 0 | 0.000 |
| E01 | en_en | requires_human | jev_114 | 200 | 0.905 | 0.905 | 0.690 | 62 | 19 | 1.000 |
| E01 | en_en | requires_human | laya_en_fixed | 200 | 0.905 | 0.905 | 0.335 | 116 | 2 | 0.105 |
| E01 | en_en | requires_human | laya_multi_fixed | 200 | 0.905 | 0.905 | 0.575 | 82 | 16 | 0.842 |
| E01 | en_en | requires_human | laya_routed | 200 | 0.905 | 0.905 | 0.335 | 116 | 2 | 0.105 |
| E01 | en_en | severity | jev_114 | 200 | 1.000 | 1.000 | 0.720 | 56 | 0 | – |
| E01 | en_en | severity | laya_en_fixed | 200 | 1.000 | 1.000 | 0.315 | 137 | 0 | – |
| E01 | en_en | severity | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.230 | 154 | 0 | – |
| E01 | en_en | severity | laya_routed | 200 | 1.000 | 1.000 | 0.315 | 137 | 0 | – |
| E01 | th_th | failure_family | jev_114 | 200 | 0.860 | 0.860 | 0.915 | 5 | 16 | 0.571 |
| E01 | th_th | failure_family | laya_en_fixed | 200 | 0.860 | 0.860 | 0.150 | 142 | 0 | 0.000 |
| E01 | th_th | failure_family | laya_multi_fixed | 200 | 0.860 | 0.860 | 0.250 | 122 | 0 | 0.000 |
| E01 | th_th | failure_family | laya_routed | 200 | 0.860 | 0.860 | 0.250 | 122 | 0 | 0.000 |
| E01 | th_th | requires_human | jev_114 | 200 | 0.905 | 0.905 | 0.765 | 47 | 19 | 1.000 |
| E01 | th_th | requires_human | laya_en_fixed | 200 | 0.905 | 0.905 | 0.505 | 91 | 11 | 0.579 |
| E01 | th_th | requires_human | laya_multi_fixed | 200 | 0.905 | 0.905 | 0.715 | 55 | 17 | 0.895 |
| E01 | th_th | requires_human | laya_routed | 200 | 0.905 | 0.905 | 0.715 | 55 | 17 | 0.895 |
| E01 | th_th | severity | jev_114 | 200 | 1.000 | 1.000 | 0.795 | 41 | 0 | – |
| E01 | th_th | severity | laya_en_fixed | 200 | 1.000 | 1.000 | 0.190 | 162 | 0 | – |
| E01 | th_th | severity | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.225 | 155 | 0 | – |
| E01 | th_th | severity | laya_routed | 200 | 1.000 | 1.000 | 0.225 | 155 | 0 | – |
| E02 | en_en | anomaly_family | jev_114 | 200 | 1.000 | 0.940 | 0.825 | 24 | 1 | – |
| E02 | en_en | anomaly_family | laya_en_fixed | 200 | 1.000 | 0.940 | 0.625 | 64 | 1 | – |
| E02 | en_en | anomaly_family | laya_multi_fixed | 200 | 1.000 | 0.940 | 0.270 | 137 | 3 | – |
| E02 | en_en | anomaly_family | laya_routed | 200 | 1.000 | 0.940 | 0.625 | 64 | 1 | – |
| E02 | en_en | investigate_now | jev_114 | 200 | 1.000 | 0.980 | 0.660 | 67 | 3 | – |
| E02 | en_en | investigate_now | laya_en_fixed | 200 | 1.000 | 0.980 | 0.670 | 66 | 4 | – |
| E02 | en_en | investigate_now | laya_multi_fixed | 200 | 1.000 | 0.980 | 0.320 | 132 | 0 | – |
| E02 | en_en | investigate_now | laya_routed | 200 | 1.000 | 0.980 | 0.670 | 66 | 4 | – |
| E02 | en_en | quality_impact | jev_114 | 200 | 1.000 | 0.955 | 0.460 | 101 | 2 | – |
| E02 | en_en | quality_impact | laya_en_fixed | 200 | 1.000 | 0.955 | 0.325 | 128 | 2 | – |
| E02 | en_en | quality_impact | laya_multi_fixed | 200 | 1.000 | 0.955 | 0.210 | 149 | 0 | – |
| E02 | en_en | quality_impact | laya_routed | 200 | 1.000 | 0.955 | 0.325 | 128 | 2 | – |
| E02 | th_th | anomaly_family | jev_114 | 200 | 1.000 | 0.940 | 0.820 | 25 | 1 | – |
| E02 | th_th | anomaly_family | laya_en_fixed | 200 | 1.000 | 0.940 | 0.310 | 129 | 3 | – |
| E02 | th_th | anomaly_family | laya_multi_fixed | 200 | 1.000 | 0.940 | 0.240 | 141 | 1 | – |
| E02 | th_th | anomaly_family | laya_routed | 200 | 1.000 | 0.940 | 0.240 | 141 | 1 | – |
| E02 | th_th | investigate_now | jev_114 | 200 | 1.000 | 0.980 | 0.600 | 80 | 4 | – |
| E02 | th_th | investigate_now | laya_en_fixed | 200 | 1.000 | 0.980 | 0.335 | 129 | 0 | – |
| E02 | th_th | investigate_now | laya_multi_fixed | 200 | 1.000 | 0.980 | 0.325 | 131 | 0 | – |
| E02 | th_th | investigate_now | laya_routed | 200 | 1.000 | 0.980 | 0.325 | 131 | 0 | – |
| E02 | th_th | quality_impact | jev_114 | 200 | 1.000 | 0.955 | 0.455 | 101 | 1 | – |
| E02 | th_th | quality_impact | laya_en_fixed | 200 | 1.000 | 0.955 | 0.335 | 130 | 6 | – |
| E02 | th_th | quality_impact | laya_multi_fixed | 200 | 1.000 | 0.955 | 0.215 | 148 | 0 | – |
| E02 | th_th | quality_impact | laya_routed | 200 | 1.000 | 0.955 | 0.215 | 148 | 0 | – |
| E03 | en_en | drift_impact | jev_114 | 200 | 1.000 | 0.980 | 0.790 | 42 | 4 | – |
| E03 | en_en | drift_impact | laya_en_fixed | 200 | 1.000 | 0.980 | 0.510 | 94 | 0 | – |
| E03 | en_en | drift_impact | laya_multi_fixed | 200 | 1.000 | 0.980 | 0.255 | 149 | 4 | – |
| E03 | en_en | drift_impact | laya_routed | 200 | 1.000 | 0.980 | 0.510 | 94 | 0 | – |
| E03 | en_en | migration_risk | jev_114 | 200 | 1.000 | 0.740 | 0.255 | 128 | 31 | – |
| E03 | en_en | migration_risk | laya_en_fixed | 200 | 1.000 | 0.740 | 0.370 | 92 | 18 | – |
| E03 | en_en | migration_risk | laya_multi_fixed | 200 | 1.000 | 0.740 | 0.155 | 130 | 13 | – |
| E03 | en_en | migration_risk | laya_routed | 200 | 1.000 | 0.740 | 0.370 | 92 | 18 | – |
| E03 | en_en | review_before_deploy | jev_114 | 200 | 1.000 | 0.935 | 0.780 | 36 | 5 | – |
| E03 | en_en | review_before_deploy | laya_en_fixed | 200 | 1.000 | 0.935 | 0.390 | 121 | 12 | – |
| E03 | en_en | review_before_deploy | laya_multi_fixed | 200 | 1.000 | 0.935 | 0.660 | 55 | 0 | – |
| E03 | en_en | review_before_deploy | laya_routed | 200 | 1.000 | 0.935 | 0.390 | 121 | 12 | – |
| E03 | th_th | drift_impact | jev_114 | 200 | 1.000 | 0.980 | 0.800 | 40 | 4 | – |
| E03 | th_th | drift_impact | laya_en_fixed | 200 | 1.000 | 0.980 | 0.210 | 157 | 3 | – |
| E03 | th_th | drift_impact | laya_multi_fixed | 200 | 1.000 | 0.980 | 0.260 | 148 | 4 | – |
| E03 | th_th | drift_impact | laya_routed | 200 | 1.000 | 0.980 | 0.260 | 148 | 4 | – |
| E03 | th_th | migration_risk | jev_114 | 200 | 1.000 | 0.740 | 0.495 | 81 | 32 | – |
| E03 | th_th | migration_risk | laya_en_fixed | 200 | 1.000 | 0.740 | 0.205 | 107 | 0 | – |
| E03 | th_th | migration_risk | laya_multi_fixed | 200 | 1.000 | 0.740 | 0.145 | 120 | 1 | – |
| E03 | th_th | migration_risk | laya_routed | 200 | 1.000 | 0.740 | 0.145 | 120 | 1 | – |
| E03 | th_th | review_before_deploy | jev_114 | 200 | 1.000 | 0.935 | 0.690 | 49 | 0 | – |
| E03 | th_th | review_before_deploy | laya_en_fixed | 200 | 1.000 | 0.935 | 0.635 | 60 | 0 | – |
| E03 | th_th | review_before_deploy | laya_multi_fixed | 200 | 1.000 | 0.935 | 0.660 | 55 | 0 | – |
| E03 | th_th | review_before_deploy | laya_routed | 200 | 1.000 | 0.935 | 0.660 | 55 | 0 | – |
| E04 | en_en | impact_severity | jev_114 | 200 | 1.000 | 1.000 | 0.875 | 25 | 0 | – |
| E04 | en_en | impact_severity | laya_en_fixed | 200 | 1.000 | 1.000 | 0.470 | 106 | 0 | – |
| E04 | en_en | impact_severity | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.380 | 124 | 0 | – |
| E04 | en_en | impact_severity | laya_routed | 200 | 1.000 | 1.000 | 0.470 | 106 | 0 | – |
| E04 | en_en | impact_tier | jev_114 | 200 | 1.000 | 1.000 | 0.785 | 43 | 0 | – |
| E04 | en_en | impact_tier | laya_en_fixed | 200 | 1.000 | 1.000 | 0.320 | 136 | 0 | – |
| E04 | en_en | impact_tier | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.350 | 130 | 0 | – |
| E04 | en_en | impact_tier | laya_routed | 200 | 1.000 | 1.000 | 0.320 | 136 | 0 | – |
| E04 | en_en | needs_owner_escalation | jev_114 | 200 | 1.000 | 1.000 | 0.485 | 103 | 0 | – |
| E04 | en_en | needs_owner_escalation | laya_en_fixed | 200 | 1.000 | 1.000 | 0.475 | 105 | 0 | – |
| E04 | en_en | needs_owner_escalation | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.505 | 99 | 0 | – |
| E04 | en_en | needs_owner_escalation | laya_routed | 200 | 1.000 | 1.000 | 0.475 | 105 | 0 | – |
| E04 | th_th | impact_severity | jev_114 | 200 | 1.000 | 1.000 | 0.890 | 22 | 0 | – |
| E04 | th_th | impact_severity | laya_en_fixed | 200 | 1.000 | 1.000 | 0.390 | 122 | 0 | – |
| E04 | th_th | impact_severity | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.375 | 125 | 0 | – |
| E04 | th_th | impact_severity | laya_routed | 200 | 1.000 | 1.000 | 0.375 | 125 | 0 | – |
| E04 | th_th | impact_tier | jev_114 | 200 | 1.000 | 1.000 | 0.785 | 43 | 0 | – |
| E04 | th_th | impact_tier | laya_en_fixed | 200 | 1.000 | 1.000 | 0.105 | 179 | 0 | – |
| E04 | th_th | impact_tier | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.270 | 146 | 0 | – |
| E04 | th_th | impact_tier | laya_routed | 200 | 1.000 | 1.000 | 0.270 | 146 | 0 | – |
| E04 | th_th | needs_owner_escalation | jev_114 | 200 | 1.000 | 1.000 | 0.480 | 104 | 0 | – |
| E04 | th_th | needs_owner_escalation | laya_en_fixed | 200 | 1.000 | 1.000 | 0.495 | 101 | 0 | – |
| E04 | th_th | needs_owner_escalation | laya_multi_fixed | 200 | 1.000 | 1.000 | 0.525 | 95 | 0 | – |
| E04 | th_th | needs_owner_escalation | laya_routed | 200 | 1.000 | 1.000 | 0.525 | 95 | 0 | – |
| E05 | en_en | data_class | jev_114 | 200 | 1.000 | 0.880 | 0.785 | 34 | 15 | – |
| E05 | en_en | data_class | laya_en_fixed | 200 | 1.000 | 0.880 | 0.310 | 122 | 8 | – |
| E05 | en_en | data_class | laya_multi_fixed | 200 | 1.000 | 0.880 | 0.240 | 131 | 3 | – |
| E05 | en_en | data_class | laya_routed | 200 | 1.000 | 0.880 | 0.310 | 122 | 8 | – |
| E05 | en_en | handling_tier | jev_114 | 200 | 1.000 | 0.845 | 0.690 | 51 | 20 | – |
| E05 | en_en | handling_tier | laya_en_fixed | 200 | 1.000 | 0.845 | 0.350 | 116 | 17 | – |
| E05 | en_en | handling_tier | laya_multi_fixed | 200 | 1.000 | 0.845 | 0.310 | 116 | 9 | – |
| E05 | en_en | handling_tier | laya_routed | 200 | 1.000 | 0.845 | 0.350 | 116 | 17 | – |
| E05 | en_en | requires_masking_review | jev_114 | 200 | 1.000 | 0.935 | 0.715 | 52 | 8 | – |
| E05 | en_en | requires_masking_review | laya_en_fixed | 200 | 1.000 | 0.935 | 0.695 | 56 | 8 | – |
| E05 | en_en | requires_masking_review | laya_multi_fixed | 200 | 1.000 | 0.935 | 0.635 | 65 | 5 | – |
| E05 | en_en | requires_masking_review | laya_routed | 200 | 1.000 | 0.935 | 0.695 | 56 | 8 | – |
| E05 | th_th | data_class | jev_114 | 200 | 1.000 | 0.880 | 0.810 | 29 | 15 | – |
| E05 | th_th | data_class | laya_en_fixed | 200 | 1.000 | 0.880 | 0.235 | 130 | 1 | – |
| E05 | th_th | data_class | laya_multi_fixed | 200 | 1.000 | 0.880 | 0.220 | 139 | 7 | – |
| E05 | th_th | data_class | laya_routed | 200 | 1.000 | 0.880 | 0.220 | 139 | 7 | – |
| E05 | th_th | handling_tier | jev_114 | 200 | 1.000 | 0.845 | 0.710 | 51 | 24 | – |
| E05 | th_th | handling_tier | laya_en_fixed | 200 | 1.000 | 0.845 | 0.295 | 123 | 13 | – |
| E05 | th_th | handling_tier | laya_multi_fixed | 200 | 1.000 | 0.845 | 0.285 | 119 | 7 | – |
| E05 | th_th | handling_tier | laya_routed | 200 | 1.000 | 0.845 | 0.285 | 119 | 7 | – |
| E05 | th_th | requires_masking_review | jev_114 | 200 | 1.000 | 0.935 | 0.710 | 53 | 8 | – |
| E05 | th_th | requires_masking_review | laya_en_fixed | 200 | 1.000 | 0.935 | 0.600 | 77 | 10 | – |
| E05 | th_th | requires_masking_review | laya_multi_fixed | 200 | 1.000 | 0.935 | 0.675 | 60 | 8 | – |
| E05 | th_th | requires_masking_review | laya_routed | 200 | 1.000 | 0.935 | 0.675 | 60 | 8 | – |
| E06 | en_en | candidate_quality | jev_114 | 201 | 1.000 | 0.766 | 0.443 | 78 | 13 | – |
| E06 | en_en | candidate_quality | laya_en_fixed | 201 | 1.000 | 0.766 | 0.373 | 80 | 1 | – |
| E06 | en_en | candidate_quality | laya_multi_fixed | 201 | 1.000 | 0.766 | 0.239 | 125 | 19 | – |
| E06 | en_en | candidate_quality | laya_routed | 201 | 1.000 | 0.766 | 0.373 | 80 | 1 | – |
| E06 | en_en | candidate_safe_to_consider | jev_114 | 201 | 1.000 | 1.000 | 0.965 | 7 | 0 | – |
| E06 | en_en | candidate_safe_to_consider | laya_en_fixed | 201 | 1.000 | 1.000 | 0.602 | 80 | 0 | – |
| E06 | en_en | candidate_safe_to_consider | laya_multi_fixed | 201 | 1.000 | 1.000 | 0.796 | 41 | 0 | – |
| E06 | en_en | candidate_safe_to_consider | laya_routed | 201 | 1.000 | 1.000 | 0.602 | 80 | 0 | – |
| E06 | en_en | candidate_verdict | jev_114 | 201 | 1.000 | 0.821 | 0.557 | 84 | 31 | – |
| E06 | en_en | candidate_verdict | laya_en_fixed | 201 | 1.000 | 0.821 | 0.229 | 120 | 1 | – |
| E06 | en_en | candidate_verdict | laya_multi_fixed | 201 | 1.000 | 0.821 | 0.239 | 118 | 1 | – |
| E06 | en_en | candidate_verdict | laya_routed | 201 | 1.000 | 0.821 | 0.229 | 120 | 1 | – |
| E06 | en_en | intent_type | jev_114 | 201 | 1.000 | 1.000 | 0.701 | 60 | 0 | – |
| E06 | en_en | intent_type | laya_en_fixed | 201 | 1.000 | 1.000 | 0.323 | 136 | 0 | – |
| E06 | en_en | intent_type | laya_multi_fixed | 201 | 1.000 | 1.000 | 0.453 | 110 | 0 | – |
| E06 | en_en | intent_type | laya_routed | 201 | 1.000 | 1.000 | 0.323 | 136 | 0 | – |
| E06 | en_en | needs_clarification | jev_114 | 201 | 1.000 | 1.000 | 0.701 | 60 | 0 | – |
| E06 | en_en | needs_clarification | laya_en_fixed | 201 | 1.000 | 1.000 | 0.279 | 145 | 0 | – |
| E06 | en_en | needs_clarification | laya_multi_fixed | 201 | 1.000 | 1.000 | 0.249 | 151 | 0 | – |
| E06 | en_en | needs_clarification | laya_routed | 201 | 1.000 | 1.000 | 0.279 | 145 | 0 | – |
| E06 | th_th | candidate_quality | jev_114 | 201 | 1.000 | 0.766 | 0.388 | 93 | 17 | – |
| E06 | th_th | candidate_quality | laya_en_fixed | 201 | 1.000 | 0.766 | 0.199 | 124 | 10 | – |
| E06 | th_th | candidate_quality | laya_multi_fixed | 201 | 1.000 | 0.766 | 0.328 | 97 | 9 | – |
| E06 | th_th | candidate_quality | laya_routed | 201 | 1.000 | 0.766 | 0.328 | 97 | 9 | – |
| E06 | th_th | candidate_safe_to_consider | jev_114 | 201 | 1.000 | 1.000 | 0.915 | 17 | 0 | – |
| E06 | th_th | candidate_safe_to_consider | laya_en_fixed | 201 | 1.000 | 1.000 | 0.627 | 75 | 0 | – |
| E06 | th_th | candidate_safe_to_consider | laya_multi_fixed | 201 | 1.000 | 1.000 | 0.801 | 40 | 0 | – |
| E06 | th_th | candidate_safe_to_consider | laya_routed | 201 | 1.000 | 1.000 | 0.801 | 40 | 0 | – |
| E06 | th_th | candidate_verdict | jev_114 | 201 | 1.000 | 0.821 | 0.522 | 92 | 32 | – |
| E06 | th_th | candidate_verdict | laya_en_fixed | 201 | 1.000 | 0.821 | 0.303 | 106 | 2 | – |
| E06 | th_th | candidate_verdict | laya_multi_fixed | 201 | 1.000 | 0.821 | 0.323 | 103 | 3 | – |
| E06 | th_th | candidate_verdict | laya_routed | 201 | 1.000 | 0.821 | 0.323 | 103 | 3 | – |
| E06 | th_th | intent_type | jev_114 | 201 | 1.000 | 1.000 | 0.701 | 60 | 0 | – |
| E06 | th_th | intent_type | laya_en_fixed | 201 | 1.000 | 1.000 | 0.229 | 155 | 0 | – |
| E06 | th_th | intent_type | laya_multi_fixed | 201 | 1.000 | 1.000 | 0.393 | 122 | 0 | – |
| E06 | th_th | intent_type | laya_routed | 201 | 1.000 | 1.000 | 0.393 | 122 | 0 | – |
| E06 | th_th | needs_clarification | jev_114 | 201 | 1.000 | 1.000 | 0.463 | 108 | 0 | – |
| E06 | th_th | needs_clarification | laya_en_fixed | 201 | 1.000 | 1.000 | 0.438 | 113 | 0 | – |
| E06 | th_th | needs_clarification | laya_multi_fixed | 201 | 1.000 | 1.000 | 0.249 | 151 | 0 | – |
| E06 | th_th | needs_clarification | laya_routed | 201 | 1.000 | 1.000 | 0.249 | 151 | 0 | – |

## P3 — Laya router checkpoint choice

| experiment_id | variant | n | routed_english | routed_multilingual | mismatch_vs_state_language |
|---|---|---|---|---|---|
| E01 | en_en | 200 | 200 | 0 | 0.000 |
| E01 | th_th | 200 | 0 | 200 | 0.000 |
| E02 | en_en | 200 | 200 | 0 | 0.000 |
| E02 | th_th | 200 | 0 | 200 | 0.000 |
| E03 | en_en | 200 | 200 | 0 | 0.000 |
| E03 | th_th | 200 | 0 | 200 | 0.000 |
| E04 | en_en | 200 | 200 | 0 | 0.000 |
| E04 | th_th | 200 | 0 | 200 | 0.000 |
| E05 | en_en | 200 | 199 | 1 | 0.005 |
| E05 | th_th | 200 | 0 | 200 | 0.000 |
| E06 | en_en | 201 | 201 | 0 | 0.000 |
| E06 | th_th | 201 | 0 | 201 | 0.000 |

## P4 — cross-language variants (predeclared 50-scenario subset)

| experiment_id | model_track | variant | question_id | n_attempted | effective_accuracy | macro_f1 | truncation_rate |
|---|---|---|---|---|---|---|---|
| E01 | jev_114 | en_en | failure_family | 50 | 0.860 | 0.856 | 0.000 |
| E01 | jev_114 | en_th | failure_family | 50 | 0.880 | 0.875 | 0.000 |
| E01 | jev_114 | th_en | failure_family | 50 | 0.860 | 0.856 | 0.000 |
| E01 | jev_114 | th_th | failure_family | 50 | 0.880 | 0.875 | 0.000 |
| E02 | jev_114 | en_en | anomaly_family | 50 | 0.840 | 0.876 | 0.000 |
| E02 | jev_114 | en_th | anomaly_family | 50 | 0.820 | 0.862 | 0.000 |
| E02 | jev_114 | th_en | anomaly_family | 50 | 0.820 | 0.861 | 0.000 |
| E02 | jev_114 | th_th | anomaly_family | 50 | 0.840 | 0.876 | 0.000 |
| E03 | jev_114 | en_en | drift_impact | 50 | 0.740 | 0.774 | 0.000 |
| E03 | jev_114 | en_th | drift_impact | 50 | 0.780 | 0.811 | 0.000 |
| E03 | jev_114 | th_en | drift_impact | 50 | 0.780 | 0.814 | 0.000 |
| E03 | jev_114 | th_th | drift_impact | 50 | 0.760 | 0.793 | 0.000 |
| E04 | jev_114 | en_en | impact_tier | 50 | 0.840 | 0.729 | 0.000 |
| E04 | jev_114 | en_th | impact_tier | 50 | 0.840 | 0.729 | 0.000 |
| E04 | jev_114 | th_en | impact_tier | 50 | 0.840 | 0.729 | 0.000 |
| E04 | jev_114 | th_th | impact_tier | 50 | 0.840 | 0.729 | 0.000 |
| E05 | jev_114 | en_en | data_class | 50 | 0.780 | 0.808 | 0.000 |
| E05 | jev_114 | en_th | data_class | 50 | 0.800 | 0.828 | 0.000 |
| E05 | jev_114 | th_en | data_class | 50 | 0.760 | 0.784 | 0.000 |
| E05 | jev_114 | th_th | data_class | 50 | 0.780 | 0.808 | 0.000 |
| E06 | jev_114 | en_en | candidate_verdict | 50 | 0.600 | 0.479 | 0.000 |
| E06 | jev_114 | en_th | candidate_verdict | 50 | 0.600 | 0.478 | 0.000 |
| E06 | jev_114 | th_en | candidate_verdict | 50 | 0.640 | 0.516 | 0.000 |
| E06 | jev_114 | th_th | candidate_verdict | 50 | 0.580 | 0.462 | 0.000 |
| E01 | laya_en_fixed | en_en | failure_family | 50 | 0.060 | 0.016 | 0.000 |
| E01 | laya_en_fixed | en_th | failure_family | 50 | 0.060 | 0.016 | 0.000 |
| E01 | laya_en_fixed | th_en | failure_family | 50 | 0.060 | 0.016 | 0.000 |
| E01 | laya_en_fixed | th_th | failure_family | 50 | 0.060 | 0.016 | 0.020 |
| E02 | laya_en_fixed | en_en | anomaly_family | 50 | 0.620 | 0.652 | 0.000 |
| E02 | laya_en_fixed | en_th | anomaly_family | 50 | 0.360 | 0.352 | 0.040 |
| E02 | laya_en_fixed | th_en | anomaly_family | 50 | 0.500 | 0.529 | 0.060 |
| E02 | laya_en_fixed | th_th | anomaly_family | 50 | 0.320 | 0.296 | 1.000 |
| E03 | laya_en_fixed | en_en | drift_impact | 50 | 0.520 | 0.516 | 0.000 |
| E03 | laya_en_fixed | en_th | drift_impact | 50 | 0.520 | 0.509 | 0.000 |
| E03 | laya_en_fixed | th_en | drift_impact | 50 | 0.380 | 0.424 | 0.000 |
| E03 | laya_en_fixed | th_th | drift_impact | 50 | 0.240 | 0.139 | 0.020 |
| E04 | laya_en_fixed | en_en | impact_tier | 50 | 0.340 | 0.275 | 0.000 |
| E04 | laya_en_fixed | en_th | impact_tier | 50 | 0.220 | 0.072 | 0.100 |
| E04 | laya_en_fixed | th_en | impact_tier | 50 | 0.320 | 0.226 | 0.300 |
| E04 | laya_en_fixed | th_th | impact_tier | 50 | 0.120 | 0.049 | 0.640 |
| E05 | laya_en_fixed | en_en | data_class | 50 | 0.360 | 0.243 | 0.000 |
| E05 | laya_en_fixed | en_th | data_class | 50 | 0.300 | 0.157 | 0.000 |
| E05 | laya_en_fixed | th_en | data_class | 50 | 0.240 | 0.144 | 0.000 |
| E05 | laya_en_fixed | th_th | data_class | 50 | 0.280 | 0.097 | 0.000 |
| E06 | laya_en_fixed | en_en | candidate_verdict | 50 | 0.200 | 0.086 | 0.000 |
| E06 | laya_en_fixed | en_th | candidate_verdict | 50 | 0.280 | 0.225 | 0.240 |
| E06 | laya_en_fixed | th_en | candidate_verdict | 50 | 0.240 | 0.145 | 0.480 |
| E06 | laya_en_fixed | th_th | candidate_verdict | 50 | 0.280 | 0.244 | 0.720 |
| E01 | laya_multi_fixed | en_en | failure_family | 50 | 0.320 | 0.325 | 0.000 |
| E01 | laya_multi_fixed | en_th | failure_family | 50 | 0.180 | 0.136 | 0.000 |
| E01 | laya_multi_fixed | th_en | failure_family | 50 | 0.380 | 0.402 | 0.000 |
| E01 | laya_multi_fixed | th_th | failure_family | 50 | 0.260 | 0.234 | 0.000 |
| E02 | laya_multi_fixed | en_en | anomaly_family | 50 | 0.340 | 0.216 | 0.000 |
| E02 | laya_multi_fixed | en_th | anomaly_family | 50 | 0.300 | 0.276 | 0.000 |
| E02 | laya_multi_fixed | th_en | anomaly_family | 50 | 0.320 | 0.214 | 0.000 |
| E02 | laya_multi_fixed | th_th | anomaly_family | 50 | 0.220 | 0.167 | 0.000 |
| E03 | laya_multi_fixed | en_en | drift_impact | 50 | 0.260 | 0.175 | 0.000 |
| E03 | laya_multi_fixed | en_th | drift_impact | 50 | 0.380 | 0.276 | 0.000 |
| E03 | laya_multi_fixed | th_en | drift_impact | 50 | 0.340 | 0.241 | 0.000 |
| E03 | laya_multi_fixed | th_th | drift_impact | 50 | 0.300 | 0.226 | 0.000 |
| E04 | laya_multi_fixed | en_en | impact_tier | 50 | 0.360 | 0.318 | 0.000 |
| E04 | laya_multi_fixed | en_th | impact_tier | 50 | 0.180 | 0.125 | 0.000 |
| E04 | laya_multi_fixed | th_en | impact_tier | 50 | 0.400 | 0.350 | 0.000 |
| E04 | laya_multi_fixed | th_th | impact_tier | 50 | 0.320 | 0.224 | 0.000 |
| E05 | laya_multi_fixed | en_en | data_class | 50 | 0.280 | 0.260 | 0.000 |
| E05 | laya_multi_fixed | en_th | data_class | 50 | 0.180 | 0.138 | 0.000 |
| E05 | laya_multi_fixed | th_en | data_class | 50 | 0.120 | 0.100 | 0.000 |
| E05 | laya_multi_fixed | th_th | data_class | 50 | 0.180 | 0.147 | 0.000 |
| E06 | laya_multi_fixed | en_en | candidate_verdict | 50 | 0.240 | 0.130 | 0.000 |
| E06 | laya_multi_fixed | en_th | candidate_verdict | 50 | 0.240 | 0.137 | 0.000 |
| E06 | laya_multi_fixed | th_en | candidate_verdict | 50 | 0.260 | 0.202 | 0.000 |
| E06 | laya_multi_fixed | th_th | candidate_verdict | 50 | 0.320 | 0.275 | 0.000 |
| E01 | laya_routed | en_en | failure_family | 50 | 0.060 | 0.016 | 0.000 |
| E01 | laya_routed | en_th | failure_family | 50 | 0.060 | 0.016 | 0.000 |
| E01 | laya_routed | th_en | failure_family | 50 | 0.380 | 0.402 | 0.000 |
| E01 | laya_routed | th_th | failure_family | 50 | 0.260 | 0.234 | 0.000 |
| E02 | laya_routed | en_en | anomaly_family | 50 | 0.620 | 0.652 | 0.000 |
| E02 | laya_routed | en_th | anomaly_family | 50 | 0.360 | 0.352 | 0.040 |
| E02 | laya_routed | th_en | anomaly_family | 50 | 0.320 | 0.214 | 0.000 |
| E02 | laya_routed | th_th | anomaly_family | 50 | 0.220 | 0.167 | 0.000 |
| E03 | laya_routed | en_en | drift_impact | 50 | 0.520 | 0.516 | 0.000 |
| E03 | laya_routed | en_th | drift_impact | 50 | 0.520 | 0.509 | 0.000 |
| E03 | laya_routed | th_en | drift_impact | 50 | 0.340 | 0.241 | 0.000 |
| E03 | laya_routed | th_th | drift_impact | 50 | 0.300 | 0.226 | 0.000 |
| E04 | laya_routed | en_en | impact_tier | 50 | 0.340 | 0.275 | 0.000 |
| E04 | laya_routed | en_th | impact_tier | 50 | 0.220 | 0.072 | 0.100 |
| E04 | laya_routed | th_en | impact_tier | 50 | 0.400 | 0.350 | 0.000 |
| E04 | laya_routed | th_th | impact_tier | 50 | 0.320 | 0.224 | 0.000 |
| E05 | laya_routed | en_en | data_class | 50 | 0.360 | 0.243 | 0.000 |
| E05 | laya_routed | en_th | data_class | 50 | 0.300 | 0.157 | 0.000 |
| E05 | laya_routed | th_en | data_class | 50 | 0.120 | 0.100 | 0.000 |
| E05 | laya_routed | th_th | data_class | 50 | 0.180 | 0.147 | 0.000 |
| E06 | laya_routed | en_en | candidate_verdict | 50 | 0.200 | 0.086 | 0.000 |
| E06 | laya_routed | en_th | candidate_verdict | 50 | 0.280 | 0.225 | 0.240 |
| E06 | laya_routed | th_en | candidate_verdict | 50 | 0.260 | 0.202 | 0.000 |
| E06 | laya_routed | th_th | candidate_verdict | 50 | 0.320 | 0.275 | 0.000 |

## P5 — choice option-order robustness (Laya: 5 permutations; Jev: 2 because of the request cap)

| model_track | experiment_id | question_id | language | n | flip_rate_mean | flip_rate_max | mean_tvd | acc_base | acc_perm_mean |
|---|---|---|---|---|---|---|---|---|---|
| jev_114 | E01 | failure_family | en | 200 | 0.013 | 0.015 | 0.010 | 0.880 | 0.887 |
| jev_114 | E01 | failure_family | th | 200 | 0.015 | 0.015 | 0.012 | 0.915 | 0.913 |
| jev_114 | E02 | anomaly_family | en | 200 | 0.010 | 0.010 | 0.035 | 0.825 | 0.827 |
| jev_114 | E02 | anomaly_family | th | 200 | 0.018 | 0.020 | 0.033 | 0.820 | 0.830 |
| jev_114 | E03 | drift_impact | en | 200 | 0.028 | 0.030 | 0.032 | 0.790 | 0.808 |
| jev_114 | E03 | drift_impact | th | 200 | 0.038 | 0.040 | 0.051 | 0.800 | 0.782 |
| jev_114 | E04 | impact_tier | en | 200 | 0.000 | 0.000 | 0.011 | 0.785 | 0.785 |
| jev_114 | E04 | impact_tier | th | 200 | 0.000 | 0.000 | 0.012 | 0.785 | 0.785 |
| jev_114 | E05 | data_class | en | 200 | 0.077 | 0.080 | 0.060 | 0.785 | 0.812 |
| jev_114 | E05 | data_class | th | 200 | 0.050 | 0.050 | 0.048 | 0.810 | 0.810 |
| jev_114 | E06 | candidate_verdict | en | 201 | 0.035 | 0.050 | 0.044 | 0.557 | 0.562 |
| jev_114 | E06 | candidate_verdict | th | 201 | 0.047 | 0.050 | 0.048 | 0.522 | 0.520 |
| jev_114 | E06 | intent_type | en | 201 | 0.002 | 0.005 | 0.037 | 0.701 | 0.699 |
| jev_114 | E06 | intent_type | th | 201 | 0.002 | 0.005 | 0.027 | 0.701 | 0.699 |
| laya_routed | E01 | failure_family | en | 200 | 0.017 | 0.025 | 0.094 | 0.150 | 0.164 |
| laya_routed | E01 | failure_family | th | 200 | 0.288 | 0.315 | 0.260 | 0.250 | 0.252 |
| laya_routed | E02 | anomaly_family | en | 200 | 0.343 | 0.385 | 0.166 | 0.625 | 0.555 |
| laya_routed | E02 | anomaly_family | th | 200 | 0.253 | 0.275 | 0.255 | 0.240 | 0.222 |
| laya_routed | E03 | drift_impact | en | 200 | 0.205 | 0.235 | 0.193 | 0.510 | 0.478 |
| laya_routed | E03 | drift_impact | th | 200 | 0.217 | 0.245 | 0.246 | 0.260 | 0.282 |
| laya_routed | E04 | impact_tier | en | 200 | 0.232 | 0.260 | 0.109 | 0.320 | 0.348 |
| laya_routed | E04 | impact_tier | th | 200 | 0.259 | 0.310 | 0.241 | 0.270 | 0.271 |
| laya_routed | E05 | data_class | en | 200 | 0.144 | 0.160 | 0.114 | 0.310 | 0.324 |
| laya_routed | E05 | data_class | th | 200 | 0.324 | 0.355 | 0.264 | 0.220 | 0.229 |
| laya_routed | E06 | candidate_verdict | en | 201 | 0.049 | 0.070 | 0.052 | 0.229 | 0.234 |
| laya_routed | E06 | candidate_verdict | th | 201 | 0.098 | 0.114 | 0.127 | 0.323 | 0.281 |
| laya_routed | E06 | intent_type | en | 201 | 0.211 | 0.224 | 0.140 | 0.323 | 0.331 |
| laya_routed | E06 | intent_type | th | 201 | 0.405 | 0.448 | 0.213 | 0.393 | 0.328 |

## P6 — calibration, raw vs post-hoc (fit on calibration split only)

| model_track | experiment_id | variant | question_id | n_cal | n_test | temperature | raw_brier | calibrated_brier | raw_ece_equal_mass | calibrated_ece_equal_mass | test_coverage_at_target | test_risk_at_target |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| jev_114 | E01 | en_en | failure_family | 40 | 400 | 1.949 | 0.174 | 0.149 | 0.080 | 0.037 | 0.960 | 0.096 |
| jev_114 | E01 | en_en | requires_human | 40 | 400 | – | 0.212 | 0.145 | 0.201 | 0.091 | 1.000 | 0.195 |
| jev_114 | E01 | en_en | severity | 40 | 400 | 4.292 | 0.406 | 0.329 | 0.170 | 0.091 | 0.838 | 0.233 |
| jev_114 | E01 | th_th | failure_family | 40 | 400 | 1.925 | 0.137 | 0.132 | 0.048 | 0.042 | 0.980 | 0.074 |
| jev_114 | E01 | th_th | requires_human | 40 | 400 | – | 0.158 | 0.125 | 0.088 | 0.054 | 1.000 | 0.172 |
| jev_114 | E01 | th_th | severity | 40 | 400 | 8.580 | 0.259 | 0.299 | 0.122 | 0.152 | 0.865 | 0.130 |
| jev_114 | E02 | en_en | anomaly_family | 40 | 400 | 1.775 | 0.296 | 0.287 | 0.094 | 0.082 | 0.920 | 0.168 |
| jev_114 | E02 | en_en | investigate_now | 40 | 400 | – | 0.179 | 0.126 | 0.080 | 0.098 | 0.950 | 0.147 |
| jev_114 | E02 | en_en | quality_impact | 40 | 400 | 1.733 | 0.637 | 0.625 | 0.085 | 0.076 | 0.250 | 0.330 |
| jev_114 | E02 | th_th | anomaly_family | 40 | 400 | 1.632 | 0.260 | 0.260 | 0.076 | 0.067 | 0.978 | 0.169 |
| jev_114 | E02 | th_th | investigate_now | 40 | 400 | – | 0.201 | 0.129 | 0.145 | 0.103 | 1.000 | 0.172 |
| jev_114 | E02 | th_th | quality_impact | 40 | 400 | 1.878 | 0.650 | 0.638 | 0.103 | 0.085 | 0.250 | 0.320 |
| jev_114 | E03 | en_en | drift_impact | 40 | 400 | 1.259 | 0.243 | 0.237 | 0.097 | 0.084 | 0.930 | 0.153 |
| jev_114 | E03 | en_en | migration_risk | 40 | 400 | 2.651 | 0.961 | 0.724 | 0.460 | 0.221 | 0.200 | 0.725 |
| jev_114 | E03 | en_en | review_before_deploy | 40 | 400 | – | 0.124 | 0.100 | 0.089 | 0.046 | 0.973 | 0.129 |
| jev_114 | E03 | th_th | drift_impact | 40 | 400 | 1.178 | 0.247 | 0.241 | 0.108 | 0.101 | 0.935 | 0.171 |
| jev_114 | E03 | th_th | migration_risk | 40 | 400 | 1.183 | 0.688 | 0.671 | 0.164 | 0.153 | 0.048 | 0.526 |
| jev_114 | E03 | th_th | review_before_deploy | 40 | 400 | – | 0.163 | 0.096 | 0.183 | 0.071 | 1.000 | 0.135 |
| jev_114 | E04 | en_en | impact_severity | 40 | 400 | 1.124 | 0.243 | 0.251 | 0.132 | 0.148 | 0.863 | 0.096 |
| jev_114 | E04 | en_en | impact_tier | 40 | 400 | 1.701 | 0.406 | 0.375 | 0.199 | 0.179 | 0.930 | 0.226 |
| jev_114 | E04 | en_en | needs_owner_escalation | 40 | 400 | – | 0.292 | 0.228 | 0.251 | 0.103 | 0.000 | – |
| jev_114 | E04 | th_th | impact_severity | 40 | 400 | 1.034 | 0.196 | 0.198 | 0.080 | 0.084 | 0.998 | 0.105 |
| jev_114 | E04 | th_th | impact_tier | 40 | 400 | 1.164 | 0.371 | 0.362 | 0.175 | 0.164 | 0.990 | 0.210 |
| jev_114 | E04 | th_th | needs_owner_escalation | 40 | 400 | – | 0.311 | 0.245 | 0.268 | 0.153 | 0.000 | – |
| jev_114 | E05 | en_en | data_class | 40 | 400 | 0.585 | 0.311 | 0.332 | 0.053 | 0.087 | 1.000 | 0.225 |
| jev_114 | E05 | en_en | handling_tier | 40 | 400 | 0.757 | 0.412 | 0.413 | 0.090 | 0.077 | 0.993 | 0.292 |
| jev_114 | E05 | en_en | requires_masking_review | 40 | 400 | – | 0.184 | 0.119 | 0.169 | 0.083 | 0.993 | 0.144 |
| jev_114 | E05 | th_th | data_class | 40 | 400 | 0.540 | 0.262 | 0.277 | 0.057 | 0.089 | 0.983 | 0.211 |
| jev_114 | E05 | th_th | handling_tier | 40 | 400 | 0.650 | 0.450 | 0.451 | 0.074 | 0.087 | 0.988 | 0.286 |
| jev_114 | E05 | th_th | requires_masking_review | 40 | 400 | – | 0.182 | 0.145 | 0.117 | 0.119 | 0.970 | 0.168 |
| jev_114 | E06 | en_en | candidate_quality | 40 | 402 | 8.282 | 0.690 | 0.710 | 0.146 | 0.224 | 0.164 | 0.182 |
| jev_114 | E06 | en_en | candidate_safe_to_consider | 40 | 402 | – | 0.061 | 0.034 | 0.149 | 0.034 | 0.985 | 0.030 |
| jev_114 | E06 | en_en | candidate_verdict | 40 | 402 | 4.982 | 0.633 | 0.529 | 0.246 | 0.131 | 0.886 | 0.362 |
| jev_114 | E06 | en_en | intent_type | 40 | 402 | 6.735 | 0.567 | 0.545 | 0.244 | 0.135 | 0.970 | 0.287 |
| jev_114 | E06 | en_en | needs_clarification | 40 | 402 | – | 0.162 | 0.203 | 0.084 | 0.189 | 0.910 | 0.213 |
| jev_114 | E06 | th_th | candidate_quality | 40 | 402 | 9.003 | 0.712 | 0.729 | 0.140 | 0.148 | 0.134 | 0.259 |
| jev_114 | E06 | th_th | candidate_safe_to_consider | 40 | 402 | – | 0.103 | 0.076 | 0.147 | 0.047 | 0.973 | 0.077 |
| jev_114 | E06 | th_th | candidate_verdict | 40 | 402 | 4.758 | 0.656 | 0.549 | 0.261 | 0.112 | 0.398 | 0.269 |
| jev_114 | E06 | th_th | intent_type | 40 | 402 | 1.352 | 0.556 | 0.528 | 0.243 | 0.214 | 1.000 | 0.299 |
| jev_114 | E06 | th_th | needs_clarification | 40 | 402 | – | 0.250 | 0.221 | 0.149 | 0.198 | 0.935 | 0.250 |
| laya_en_fixed | E01 | en_en | failure_family | 40 | 400 | 3.341 | 1.456 | 0.855 | 0.747 | 0.330 | 0.130 | 0.154 |
| laya_en_fixed | E01 | en_en | requires_human | 40 | 400 | – | 0.334 | 0.255 | 0.300 | 0.166 | 0.735 | 0.364 |
| laya_en_fixed | E01 | en_en | severity | 40 | 400 | 1.277 | 0.716 | 0.696 | 0.222 | 0.176 | 0.000 | – |
| laya_en_fixed | E01 | th_th | failure_family | 40 | 400 | 20.000 | 1.221 | 0.854 | 0.567 | 0.088 | 0.000 | – |
| laya_en_fixed | E01 | th_th | requires_human | 40 | 400 | – | 0.262 | 0.232 | 0.125 | 0.147 | 0.230 | 0.304 |
| laya_en_fixed | E01 | th_th | severity | 40 | 400 | 20.000 | 0.882 | 0.754 | 0.246 | 0.100 | 0.107 | 0.860 |
| laya_en_fixed | E02 | en_en | anomaly_family | 40 | 400 | 0.388 | 0.550 | 0.508 | 0.195 | 0.063 | 0.688 | 0.255 |
| laya_en_fixed | E02 | en_en | investigate_now | 40 | 400 | – | 0.244 | 0.382 | 0.110 | 0.307 | 0.762 | 0.521 |
| laya_en_fixed | E02 | en_en | quality_impact | 40 | 400 | 1.723 | 0.693 | 0.718 | 0.151 | 0.147 | 0.230 | 0.196 |
| laya_en_fixed | E02 | th_th | anomaly_family | 40 | 400 | 2.189 | 0.693 | 0.676 | 0.212 | 0.193 | 0.310 | 0.048 |
| laya_en_fixed | E02 | th_th | investigate_now | 40 | 400 | – | 0.382 | 0.219 | 0.353 | 0.107 | 0.000 | – |
| laya_en_fixed | E02 | th_th | quality_impact | 40 | 400 | 1.042 | 0.761 | 0.761 | 0.116 | 0.117 | 0.018 | 0.857 |
| laya_en_fixed | E03 | en_en | drift_impact | 40 | 400 | 2.002 | 0.652 | 0.670 | 0.093 | 0.152 | 0.098 | 0.179 |
| laya_en_fixed | E03 | en_en | migration_risk | 40 | 400 | 1.607 | 0.790 | 0.774 | 0.167 | 0.129 | 0.000 | – |
| laya_en_fixed | E03 | en_en | review_before_deploy | 40 | 400 | – | 0.308 | 0.239 | 0.239 | 0.135 | 0.490 | 0.352 |
| laya_en_fixed | E03 | th_th | drift_impact | 40 | 400 | 1.348 | 0.839 | 0.824 | 0.155 | 0.125 | 0.015 | 0.833 |
| laya_en_fixed | E03 | th_th | migration_risk | 40 | 400 | 5.343 | 0.856 | 0.800 | 0.182 | 0.112 | 0.000 | – |
| laya_en_fixed | E03 | th_th | review_before_deploy | 40 | 400 | – | 0.279 | 0.236 | 0.217 | 0.111 | 0.623 | 0.329 |
| laya_en_fixed | E04 | en_en | impact_severity | 40 | 400 | 1.370 | 0.646 | 0.657 | 0.100 | 0.106 | 0.000 | – |
| laya_en_fixed | E04 | en_en | impact_tier | 40 | 400 | 0.736 | 0.745 | 0.764 | 0.086 | 0.135 | 0.000 | – |
| laya_en_fixed | E04 | en_en | needs_owner_escalation | 40 | 400 | – | 0.283 | 0.250 | 0.162 | 0.078 | 0.007 | 1.000 |
| laya_en_fixed | E04 | th_th | impact_severity | 40 | 400 | 0.504 | 0.788 | 0.806 | 0.105 | 0.111 | 0.110 | 0.614 |
| laya_en_fixed | E04 | th_th | impact_tier | 40 | 400 | 3.818 | 0.926 | 0.815 | 0.241 | 0.114 | 0.005 | 1.000 |
| laya_en_fixed | E04 | th_th | needs_owner_escalation | 40 | 400 | – | 0.357 | 0.261 | 0.272 | 0.138 | 0.172 | 0.638 |
| laya_en_fixed | E05 | en_en | data_class | 40 | 400 | 1.093 | 0.840 | 0.823 | 0.233 | 0.206 | 0.015 | 0.833 |
| laya_en_fixed | E05 | en_en | handling_tier | 40 | 400 | 1.505 | 0.787 | 0.758 | 0.189 | 0.138 | 0.000 | – |
| laya_en_fixed | E05 | en_en | requires_masking_review | 40 | 400 | – | 0.286 | 0.231 | 0.260 | 0.125 | 0.000 | – |
| laya_en_fixed | E05 | th_th | data_class | 40 | 400 | 6.806 | 0.819 | 0.799 | 0.156 | 0.104 | 0.060 | 0.625 |
| laya_en_fixed | E05 | th_th | handling_tier | 40 | 400 | 20.000 | 0.761 | 0.750 | 0.112 | 0.083 | 0.000 | – |
| laya_en_fixed | E05 | th_th | requires_masking_review | 40 | 400 | – | 0.276 | 0.231 | 0.174 | 0.115 | 0.007 | 0.000 |
| laya_en_fixed | E06 | en_en | candidate_quality | 40 | 402 | 10.873 | 0.800 | 0.797 | 0.088 | 0.129 | 0.000 | – |
| laya_en_fixed | E06 | en_en | candidate_safe_to_consider | 40 | 402 | – | 0.270 | 0.171 | 0.152 | 0.100 | 0.595 | 0.184 |
| laya_en_fixed | E06 | en_en | candidate_verdict | 40 | 402 | 3.028 | 0.810 | 0.752 | 0.226 | 0.089 | 0.000 | – |
| laya_en_fixed | E06 | en_en | intent_type | 40 | 402 | 0.909 | 0.754 | 0.765 | 0.140 | 0.163 | 0.147 | 0.271 |
| laya_en_fixed | E06 | en_en | needs_clarification | 40 | 402 | – | 0.405 | 0.221 | 0.445 | 0.204 | 0.995 | 0.252 |
| laya_en_fixed | E06 | th_th | candidate_quality | 40 | 402 | 20.000 | 0.808 | 0.800 | 0.100 | 0.068 | 0.000 | – |
| laya_en_fixed | E06 | th_th | candidate_safe_to_consider | 40 | 402 | – | 0.228 | 0.228 | 0.078 | 0.146 | 0.933 | 0.341 |
| laya_en_fixed | E06 | th_th | candidate_verdict | 40 | 402 | 1.560 | 0.751 | 0.745 | 0.112 | 0.080 | 0.007 | 1.000 |
| laya_en_fixed | E06 | th_th | intent_type | 40 | 402 | 0.522 | 0.847 | 0.900 | 0.084 | 0.177 | 0.092 | 0.568 |
| laya_en_fixed | E06 | th_th | needs_clarification | 40 | 402 | – | 0.361 | 0.237 | 0.308 | 0.206 | 0.983 | 0.258 |
| laya_multi_fixed | E01 | en_en | failure_family | 40 | 400 | 3.249 | 0.948 | 0.838 | 0.269 | 0.081 | 0.000 | – |
| laya_multi_fixed | E01 | en_en | requires_human | 40 | 400 | – | 0.253 | 0.234 | 0.177 | 0.130 | 0.000 | – |
| laya_multi_fixed | E01 | en_en | severity | 40 | 400 | 20.000 | 1.276 | 0.744 | 0.630 | 0.097 | 0.007 | 0.000 |
| laya_multi_fixed | E01 | th_th | failure_family | 40 | 400 | 4.542 | 1.028 | 0.847 | 0.323 | 0.069 | 0.000 | – |
| laya_multi_fixed | E01 | th_th | requires_human | 40 | 400 | – | 0.243 | 0.222 | 0.208 | 0.146 | 0.000 | – |
| laya_multi_fixed | E01 | th_th | severity | 40 | 400 | 13.814 | 1.179 | 0.743 | 0.568 | 0.118 | 0.000 | – |
| laya_multi_fixed | E02 | en_en | anomaly_family | 40 | 400 | 4.819 | 1.273 | 0.848 | 0.581 | 0.132 | 0.410 | 0.750 |
| laya_multi_fixed | E02 | en_en | investigate_now | 40 | 400 | – | 0.655 | 0.218 | 0.655 | 0.058 | 0.117 | 0.277 |
| laya_multi_fixed | E02 | en_en | quality_impact | 40 | 400 | 20.000 | 1.069 | 0.813 | 0.330 | 0.059 | 0.000 | – |
| laya_multi_fixed | E02 | th_th | anomaly_family | 40 | 400 | 6.335 | 1.391 | 0.857 | 0.650 | 0.171 | 0.300 | 0.775 |
| laya_multi_fixed | E02 | th_th | investigate_now | 40 | 400 | – | 0.678 | 0.219 | 0.677 | 0.068 | 0.052 | 0.524 |
| laya_multi_fixed | E02 | th_th | quality_impact | 40 | 400 | 20.000 | 1.183 | 0.807 | 0.514 | 0.083 | 0.000 | – |
| laya_multi_fixed | E03 | en_en | drift_impact | 40 | 400 | 5.471 | 1.011 | 0.792 | 0.391 | 0.092 | 0.000 | – |
| laya_multi_fixed | E03 | en_en | migration_risk | 40 | 400 | 20.000 | 1.033 | 0.799 | 0.418 | 0.126 | 0.000 | – |
| laya_multi_fixed | E03 | en_en | review_before_deploy | 40 | 400 | – | 0.264 | 0.230 | 0.212 | 0.124 | 0.595 | 0.303 |
| laya_multi_fixed | E03 | th_th | drift_impact | 40 | 400 | 20.000 | 0.987 | 0.824 | 0.356 | 0.102 | 0.000 | – |
| laya_multi_fixed | E03 | th_th | migration_risk | 40 | 400 | 20.000 | 1.130 | 0.805 | 0.447 | 0.112 | 0.018 | 1.000 |
| laya_multi_fixed | E03 | th_th | review_before_deploy | 40 | 400 | – | 0.281 | 0.212 | 0.246 | 0.106 | 0.487 | 0.226 |
| laya_multi_fixed | E04 | en_en | impact_severity | 40 | 400 | 1.915 | 0.730 | 0.734 | 0.103 | 0.105 | 0.077 | 0.548 |
| laya_multi_fixed | E04 | en_en | impact_tier | 40 | 400 | 20.000 | 0.865 | 0.794 | 0.204 | 0.131 | 0.000 | – |
| laya_multi_fixed | E04 | en_en | needs_owner_escalation | 40 | 400 | – | 0.390 | 0.250 | 0.362 | 0.108 | 0.052 | 0.381 |
| laya_multi_fixed | E04 | th_th | impact_severity | 40 | 400 | 4.360 | 1.084 | 0.757 | 0.508 | 0.071 | 0.122 | 0.408 |
| laya_multi_fixed | E04 | th_th | impact_tier | 40 | 400 | 20.000 | 1.020 | 0.800 | 0.333 | 0.145 | 0.000 | – |
| laya_multi_fixed | E04 | th_th | needs_owner_escalation | 40 | 400 | – | 0.379 | 0.250 | 0.346 | 0.113 | 0.010 | 0.250 |
| laya_multi_fixed | E05 | en_en | data_class | 40 | 400 | 20.000 | 0.903 | 0.798 | 0.241 | 0.063 | 0.010 | 0.500 |
| laya_multi_fixed | E05 | en_en | handling_tier | 40 | 400 | 4.947 | 0.781 | 0.730 | 0.189 | 0.086 | 0.000 | – |
| laya_multi_fixed | E05 | en_en | requires_masking_review | 40 | 400 | – | 0.290 | 0.232 | 0.258 | 0.104 | 0.022 | 0.444 |
| laya_multi_fixed | E05 | th_th | data_class | 40 | 400 | 20.000 | 1.048 | 0.804 | 0.344 | 0.060 | 0.000 | – |
| laya_multi_fixed | E05 | th_th | handling_tier | 40 | 400 | 2.997 | 0.834 | 0.736 | 0.257 | 0.106 | 0.000 | – |
| laya_multi_fixed | E05 | th_th | requires_masking_review | 40 | 400 | – | 0.325 | 0.231 | 0.324 | 0.100 | 0.000 | – |
| laya_multi_fixed | E06 | en_en | candidate_quality | 40 | 402 | 20.000 | 0.920 | 0.800 | 0.256 | 0.103 | 0.000 | – |
| laya_multi_fixed | E06 | en_en | candidate_safe_to_consider | 40 | 402 | – | 0.183 | 0.226 | 0.149 | 0.121 | 0.975 | 0.298 |
| laya_multi_fixed | E06 | en_en | candidate_verdict | 40 | 402 | 2.531 | 0.854 | 0.760 | 0.257 | 0.120 | 0.000 | – |
| laya_multi_fixed | E06 | en_en | intent_type | 40 | 402 | 1.804 | 0.749 | 0.722 | 0.183 | 0.118 | 0.000 | – |
| laya_multi_fixed | E06 | en_en | needs_clarification | 40 | 402 | – | 0.650 | 0.221 | 0.659 | 0.214 | 1.000 | 0.254 |
| laya_multi_fixed | E06 | th_th | candidate_quality | 40 | 402 | 20.000 | 0.868 | 0.801 | 0.120 | 0.128 | 0.000 | – |
| laya_multi_fixed | E06 | th_th | candidate_safe_to_consider | 40 | 402 | – | 0.188 | 0.167 | 0.165 | 0.070 | 0.905 | 0.201 |
| laya_multi_fixed | E06 | th_th | candidate_verdict | 40 | 402 | 3.689 | 0.798 | 0.742 | 0.197 | 0.073 | 0.000 | – |
| laya_multi_fixed | E06 | th_th | intent_type | 40 | 402 | 0.971 | 0.786 | 0.789 | 0.118 | 0.123 | 0.065 | 0.308 |
| laya_multi_fixed | E06 | th_th | needs_clarification | 40 | 402 | – | 0.691 | 0.221 | 0.690 | 0.189 | 1.000 | 0.254 |
| laya_routed | E01 | en_en | failure_family | 40 | 400 | 3.341 | 1.456 | 0.855 | 0.747 | 0.330 | 0.130 | 0.154 |
| laya_routed | E01 | en_en | requires_human | 40 | 400 | – | 0.334 | 0.255 | 0.300 | 0.166 | 0.735 | 0.364 |
| laya_routed | E01 | en_en | severity | 40 | 400 | 1.277 | 0.716 | 0.696 | 0.222 | 0.176 | 0.000 | – |
| laya_routed | E01 | th_th | failure_family | 40 | 400 | 4.542 | 1.028 | 0.847 | 0.323 | 0.069 | 0.000 | – |
| laya_routed | E01 | th_th | requires_human | 40 | 400 | – | 0.243 | 0.222 | 0.208 | 0.146 | 0.000 | – |
| laya_routed | E01 | th_th | severity | 40 | 400 | 13.814 | 1.179 | 0.743 | 0.568 | 0.118 | 0.000 | – |
| laya_routed | E02 | en_en | anomaly_family | 40 | 400 | 0.388 | 0.550 | 0.508 | 0.195 | 0.063 | 0.688 | 0.255 |
| laya_routed | E02 | en_en | investigate_now | 40 | 400 | – | 0.244 | 0.382 | 0.110 | 0.307 | 0.762 | 0.521 |
| laya_routed | E02 | en_en | quality_impact | 40 | 400 | 1.723 | 0.693 | 0.718 | 0.151 | 0.147 | 0.230 | 0.196 |
| laya_routed | E02 | th_th | anomaly_family | 40 | 400 | 6.335 | 1.391 | 0.857 | 0.650 | 0.171 | 0.300 | 0.775 |
| laya_routed | E02 | th_th | investigate_now | 40 | 400 | – | 0.678 | 0.219 | 0.677 | 0.068 | 0.052 | 0.524 |
| laya_routed | E02 | th_th | quality_impact | 40 | 400 | 20.000 | 1.183 | 0.807 | 0.514 | 0.083 | 0.000 | – |
| laya_routed | E03 | en_en | drift_impact | 40 | 400 | 2.002 | 0.652 | 0.670 | 0.093 | 0.152 | 0.098 | 0.179 |
| laya_routed | E03 | en_en | migration_risk | 40 | 400 | 1.607 | 0.790 | 0.774 | 0.167 | 0.129 | 0.000 | – |
| laya_routed | E03 | en_en | review_before_deploy | 40 | 400 | – | 0.308 | 0.239 | 0.239 | 0.135 | 0.490 | 0.352 |
| laya_routed | E03 | th_th | drift_impact | 40 | 400 | 20.000 | 0.987 | 0.824 | 0.356 | 0.102 | 0.000 | – |
| laya_routed | E03 | th_th | migration_risk | 40 | 400 | 20.000 | 1.130 | 0.805 | 0.447 | 0.112 | 0.018 | 1.000 |
| laya_routed | E03 | th_th | review_before_deploy | 40 | 400 | – | 0.281 | 0.212 | 0.246 | 0.106 | 0.487 | 0.226 |
| laya_routed | E04 | en_en | impact_severity | 40 | 400 | 1.370 | 0.646 | 0.657 | 0.100 | 0.106 | 0.000 | – |
| laya_routed | E04 | en_en | impact_tier | 40 | 400 | 0.736 | 0.745 | 0.764 | 0.086 | 0.135 | 0.000 | – |
| laya_routed | E04 | en_en | needs_owner_escalation | 40 | 400 | – | 0.283 | 0.250 | 0.162 | 0.078 | 0.007 | 1.000 |
| laya_routed | E04 | th_th | impact_severity | 40 | 400 | 4.360 | 1.084 | 0.757 | 0.508 | 0.071 | 0.122 | 0.408 |
| laya_routed | E04 | th_th | impact_tier | 40 | 400 | 20.000 | 1.020 | 0.800 | 0.333 | 0.145 | 0.000 | – |
| laya_routed | E04 | th_th | needs_owner_escalation | 40 | 400 | – | 0.379 | 0.250 | 0.346 | 0.113 | 0.010 | 0.250 |
| laya_routed | E05 | en_en | data_class | 40 | 400 | 1.093 | 0.837 | 0.821 | 0.232 | 0.205 | 0.013 | 0.800 |
| laya_routed | E05 | en_en | handling_tier | 40 | 400 | 1.505 | 0.787 | 0.758 | 0.189 | 0.138 | 0.000 | – |
| laya_routed | E05 | en_en | requires_masking_review | 40 | 400 | – | 0.286 | 0.231 | 0.260 | 0.125 | 0.000 | – |
| laya_routed | E05 | th_th | data_class | 40 | 400 | 20.000 | 1.048 | 0.804 | 0.344 | 0.060 | 0.000 | – |
| laya_routed | E05 | th_th | handling_tier | 40 | 400 | 2.997 | 0.834 | 0.736 | 0.257 | 0.106 | 0.000 | – |
| laya_routed | E05 | th_th | requires_masking_review | 40 | 400 | – | 0.325 | 0.231 | 0.324 | 0.100 | 0.000 | – |
| laya_routed | E06 | en_en | candidate_quality | 40 | 402 | 10.873 | 0.800 | 0.797 | 0.088 | 0.129 | 0.000 | – |
| laya_routed | E06 | en_en | candidate_safe_to_consider | 40 | 402 | – | 0.270 | 0.171 | 0.152 | 0.100 | 0.595 | 0.184 |
| laya_routed | E06 | en_en | candidate_verdict | 40 | 402 | 3.028 | 0.810 | 0.752 | 0.226 | 0.089 | 0.000 | – |
| laya_routed | E06 | en_en | intent_type | 40 | 402 | 0.909 | 0.754 | 0.765 | 0.140 | 0.163 | 0.147 | 0.271 |
| laya_routed | E06 | en_en | needs_clarification | 40 | 402 | – | 0.405 | 0.221 | 0.445 | 0.204 | 0.995 | 0.252 |
| laya_routed | E06 | th_th | candidate_quality | 40 | 402 | 20.000 | 0.868 | 0.801 | 0.120 | 0.128 | 0.000 | – |
| laya_routed | E06 | th_th | candidate_safe_to_consider | 40 | 402 | – | 0.188 | 0.167 | 0.165 | 0.070 | 0.905 | 0.201 |
| laya_routed | E06 | th_th | candidate_verdict | 40 | 402 | 3.689 | 0.798 | 0.742 | 0.197 | 0.073 | 0.000 | – |
| laya_routed | E06 | th_th | intent_type | 40 | 402 | 0.971 | 0.786 | 0.789 | 0.118 | 0.123 | 0.065 | 0.308 |
| laya_routed | E06 | th_th | needs_clarification | 40 | 402 | – | 0.691 | 0.221 | 0.690 | 0.189 | 1.000 | 0.254 |

## P7 — context length (common_fit vs natural_long, primary choice question)

| experiment_id | model_track | variant | effective_accuracy_common_fit | effective_accuracy_natural_long | truncation_rate_common_fit | truncation_rate_natural_long |
|---|---|---|---|---|---|---|
| E01 | jev_114 | en_en | 0.880 | 0.880 | 0.000 | 0.000 |
| E01 | jev_114 | th_th | 0.915 | 0.925 | 0.000 | 0.000 |
| E01 | laya_en_fixed | en_en | 0.150 | 0.155 | 0.000 | 0.290 |
| E01 | laya_en_fixed | th_th | 0.150 | 0.150 | 0.020 | 0.895 |
| E01 | laya_multi_fixed | en_en | 0.295 | 0.280 | 0.000 | 0.000 |
| E01 | laya_multi_fixed | th_th | 0.250 | 0.220 | 0.000 | 0.000 |
| E01 | laya_routed | en_en | 0.150 | 0.155 | 0.000 | 0.290 |
| E01 | laya_routed | th_th | 0.250 | 0.220 | 0.000 | 0.000 |
| E01 | rules_only | en_en | 0.860 | – | 0.000 | – |
| E01 | rules_only | th_th | 0.860 | – | 0.000 | – |
| E02 | jev_114 | en_en | 0.825 | 0.815 | 0.000 | 0.000 |
| E02 | jev_114 | th_th | 0.820 | 0.825 | 0.000 | 0.000 |
| E02 | laya_en_fixed | en_en | 0.625 | 0.640 | 0.000 | 0.925 |
| E02 | laya_en_fixed | th_th | 0.310 | 0.310 | 1.000 | 1.000 |
| E02 | laya_multi_fixed | en_en | 0.270 | 0.145 | 0.000 | 0.000 |
| E02 | laya_multi_fixed | th_th | 0.240 | 0.145 | 0.000 | 0.000 |
| E02 | laya_routed | en_en | 0.625 | 0.640 | 0.000 | 0.925 |
| E02 | laya_routed | th_th | 0.240 | 0.145 | 0.000 | 0.000 |
| E02 | rules_only | en_en | 0.940 | – | 0.000 | – |
| E02 | rules_only | th_th | 0.940 | – | 0.000 | – |
| E03 | jev_114 | en_en | 0.790 | 0.810 | 0.000 | 0.000 |
| E03 | jev_114 | th_th | 0.800 | 0.805 | 0.000 | 0.000 |
| E03 | laya_en_fixed | en_en | 0.510 | 0.530 | 0.000 | 0.740 |
| E03 | laya_en_fixed | th_th | 0.210 | 0.180 | 0.025 | 0.975 |
| E03 | laya_multi_fixed | en_en | 0.255 | 0.315 | 0.000 | 0.000 |
| E03 | laya_multi_fixed | th_th | 0.260 | 0.185 | 0.000 | 0.000 |
| E03 | laya_routed | en_en | 0.510 | 0.530 | 0.000 | 0.740 |
| E03 | laya_routed | th_th | 0.260 | 0.185 | 0.000 | 0.000 |
| E03 | rules_only | en_en | 0.980 | – | 0.000 | – |
| E03 | rules_only | th_th | 0.980 | – | 0.000 | – |
| E04 | jev_114 | en_en | 0.785 | 0.785 | 0.000 | 0.000 |
| E04 | jev_114 | th_th | 0.785 | 0.785 | 0.000 | 0.000 |
| E04 | laya_en_fixed | en_en | 0.320 | 0.340 | 0.000 | 0.155 |
| E04 | laya_en_fixed | th_th | 0.105 | 0.160 | 0.635 | 0.885 |
| E04 | laya_multi_fixed | en_en | 0.350 | 0.320 | 0.000 | 0.000 |
| E04 | laya_multi_fixed | th_th | 0.270 | 0.340 | 0.000 | 0.000 |
| E04 | laya_routed | en_en | 0.320 | 0.340 | 0.000 | 0.155 |
| E04 | laya_routed | th_th | 0.270 | 0.340 | 0.000 | 0.000 |
| E04 | rules_only | en_en | 1.000 | – | 0.000 | – |
| E04 | rules_only | th_th | 1.000 | – | 0.000 | – |
| E05 | jev_114 | en_en | 0.785 | 0.765 | 0.000 | 0.000 |
| E05 | jev_114 | th_th | 0.810 | 0.750 | 0.000 | 0.000 |
| E05 | laya_en_fixed | en_en | 0.310 | 0.255 | 0.000 | 0.000 |
| E05 | laya_en_fixed | th_th | 0.235 | 0.235 | 0.000 | 0.365 |
| E05 | laya_multi_fixed | en_en | 0.240 | 0.255 | 0.000 | 0.000 |
| E05 | laya_multi_fixed | th_th | 0.220 | 0.255 | 0.000 | 0.000 |
| E05 | laya_routed | en_en | 0.310 | 0.255 | 0.000 | 0.000 |
| E05 | laya_routed | th_th | 0.220 | 0.255 | 0.000 | 0.000 |
| E05 | rules_only | en_en | 0.880 | – | 0.000 | – |
| E05 | rules_only | th_th | 0.880 | – | 0.000 | – |
| E06 | jev_114 | en_en | 0.557 | 0.582 | 0.000 | 0.000 |
| E06 | jev_114 | th_th | 0.522 | 0.517 | 0.000 | 0.000 |
| E06 | laya_en_fixed | en_en | 0.229 | 0.239 | 0.030 | 0.811 |
| E06 | laya_en_fixed | th_th | 0.303 | 0.279 | 0.791 | 1.000 |
| E06 | laya_multi_fixed | en_en | 0.239 | 0.274 | 0.000 | 0.000 |
| E06 | laya_multi_fixed | th_th | 0.323 | 0.264 | 0.000 | 0.075 |
| E06 | laya_routed | en_en | 0.229 | 0.239 | 0.030 | 0.811 |
| E06 | laya_routed | th_th | 0.323 | 0.264 | 0.000 | 0.075 |
| E06 | rules_only | en_en | 0.821 | – | 0.000 | – |
| E06 | rules_only | th_th | 0.821 | – | 0.000 | – |

## P9 — evidence sufficiency and confident errors (primary choice question)

| experiment_id | model_track | variant | evidence_sufficiency | n | effective_accuracy | unknown_recall | high_conf_error_rate |
|---|---|---|---|---|---|---|---|
| E01 | jev_114 | en_en | ambiguous | 36 | 0.917 | – | 0.000 |
| E01 | jev_114 | en_en | insufficient | 28 | 0.429 | 0.429 | 0.357 |
| E01 | jev_114 | en_en | sufficient | 136 | 0.963 | – | 0.022 |
| E01 | jev_114 | th_th | ambiguous | 36 | 0.917 | – | 0.000 |
| E01 | jev_114 | th_th | insufficient | 28 | 0.571 | 0.571 | 0.357 |
| E01 | jev_114 | th_th | sufficient | 136 | 0.985 | – | 0.000 |
| E02 | jev_114 | en_en | ambiguous | 47 | 0.681 | 0.526 | 0.255 |
| E02 | jev_114 | en_en | insufficient | 15 | 0.400 | 0.400 | 0.200 |
| E02 | jev_114 | en_en | sufficient | 138 | 0.920 | 0.697 | 0.043 |
| E02 | jev_114 | th_th | ambiguous | 47 | 0.660 | 0.474 | 0.213 |
| E02 | jev_114 | th_th | insufficient | 15 | 0.400 | 0.400 | 0.067 |
| E02 | jev_114 | th_th | sufficient | 138 | 0.920 | 0.667 | 0.043 |
| E03 | jev_114 | en_en | ambiguous | 22 | 0.636 | – | 0.136 |
| E03 | jev_114 | en_en | insufficient | 27 | 0.370 | 0.370 | 0.370 |
| E03 | jev_114 | en_en | sufficient | 151 | 0.887 | 0.214 | 0.000 |
| E03 | jev_114 | th_th | ambiguous | 22 | 0.591 | – | 0.091 |
| E03 | jev_114 | th_th | insufficient | 27 | 0.444 | 0.444 | 0.407 |
| E03 | jev_114 | th_th | sufficient | 151 | 0.894 | 0.214 | 0.000 |
| E04 | jev_114 | en_en | ambiguous | 42 | 1.000 | – | 0.000 |
| E04 | jev_114 | en_en | insufficient | 43 | 0.000 | 0.000 | 0.977 |
| E04 | jev_114 | en_en | sufficient | 115 | 1.000 | – | 0.000 |
| E04 | jev_114 | th_th | ambiguous | 42 | 1.000 | – | 0.000 |
| E04 | jev_114 | th_th | insufficient | 43 | 0.000 | 0.000 | 0.814 |
| E04 | jev_114 | th_th | sufficient | 115 | 1.000 | – | 0.000 |
| E05 | jev_114 | en_en | ambiguous | 21 | 0.667 | – | 0.000 |
| E05 | jev_114 | en_en | insufficient | 39 | 0.513 | 0.513 | 0.051 |
| E05 | jev_114 | en_en | sufficient | 140 | 0.879 | – | 0.021 |
| E05 | jev_114 | th_th | ambiguous | 21 | 0.524 | – | 0.000 |
| E05 | jev_114 | th_th | insufficient | 39 | 0.538 | 0.538 | 0.000 |
| E05 | jev_114 | th_th | sufficient | 140 | 0.929 | – | 0.000 |
| E06 | jev_114 | en_en | ambiguous | 51 | 0.000 | 0.000 | 0.294 |
| E06 | jev_114 | en_en | sufficient | 150 | 0.747 | – | 0.147 |
| E06 | jev_114 | th_th | ambiguous | 51 | 0.000 | 0.000 | 0.196 |
| E06 | jev_114 | th_th | sufficient | 150 | 0.700 | – | 0.147 |
| E01 | laya_en_fixed | en_en | ambiguous | 36 | 0.306 | – | 0.583 |
| E01 | laya_en_fixed | en_en | insufficient | 28 | 0.000 | 0.000 | 0.750 |
| E01 | laya_en_fixed | en_en | sufficient | 136 | 0.140 | – | 0.640 |
| E01 | laya_en_fixed | th_th | ambiguous | 36 | 0.306 | – | 0.250 |
| E01 | laya_en_fixed | th_th | insufficient | 28 | 0.000 | 0.000 | 0.464 |
| E01 | laya_en_fixed | th_th | sufficient | 136 | 0.140 | – | 0.368 |
| E02 | laya_en_fixed | en_en | ambiguous | 47 | 0.426 | 0.105 | 0.000 |
| E02 | laya_en_fixed | en_en | insufficient | 15 | 0.267 | 0.267 | 0.000 |
| E02 | laya_en_fixed | en_en | sufficient | 138 | 0.732 | 0.091 | 0.000 |
| E02 | laya_en_fixed | th_th | ambiguous | 47 | 0.319 | 0.000 | 0.000 |
| E02 | laya_en_fixed | th_th | insufficient | 15 | 0.000 | 0.000 | 0.000 |
| E02 | laya_en_fixed | th_th | sufficient | 138 | 0.341 | 0.000 | 0.000 |
| E03 | laya_en_fixed | en_en | ambiguous | 22 | 0.818 | – | 0.091 |
| E03 | laya_en_fixed | en_en | insufficient | 27 | 0.000 | 0.000 | 0.000 |
| E03 | laya_en_fixed | en_en | sufficient | 151 | 0.556 | 0.000 | 0.007 |
| E03 | laya_en_fixed | th_th | ambiguous | 22 | 0.409 | – | 0.000 |
| E03 | laya_en_fixed | th_th | insufficient | 27 | 0.000 | 0.000 | 0.000 |
| E03 | laya_en_fixed | th_th | sufficient | 151 | 0.219 | 0.000 | 0.000 |
| E04 | laya_en_fixed | en_en | ambiguous | 42 | 0.500 | – | 0.000 |
| E04 | laya_en_fixed | en_en | insufficient | 43 | 0.000 | 0.000 | 0.000 |
| E04 | laya_en_fixed | en_en | sufficient | 115 | 0.374 | – | 0.000 |
| E04 | laya_en_fixed | th_th | ambiguous | 42 | 0.286 | – | 0.024 |
| E04 | laya_en_fixed | th_th | insufficient | 43 | 0.000 | 0.000 | 0.070 |
| E04 | laya_en_fixed | th_th | sufficient | 115 | 0.078 | – | 0.104 |
| E05 | laya_en_fixed | en_en | ambiguous | 21 | 0.333 | – | 0.000 |
| E05 | laya_en_fixed | en_en | insufficient | 39 | 0.000 | 0.000 | 0.077 |
| E05 | laya_en_fixed | en_en | sufficient | 140 | 0.393 | – | 0.007 |
| E05 | laya_en_fixed | th_th | ambiguous | 21 | 0.143 | – | 0.000 |
| E05 | laya_en_fixed | th_th | insufficient | 39 | 0.000 | 0.000 | 0.000 |
| E05 | laya_en_fixed | th_th | sufficient | 140 | 0.314 | – | 0.000 |
| E06 | laya_en_fixed | en_en | ambiguous | 51 | 0.000 | 0.000 | 0.000 |
| E06 | laya_en_fixed | en_en | sufficient | 150 | 0.307 | – | 0.000 |
| E06 | laya_en_fixed | th_th | ambiguous | 51 | 0.314 | 0.314 | 0.000 |
| E06 | laya_en_fixed | th_th | sufficient | 150 | 0.300 | – | 0.000 |
| E01 | laya_multi_fixed | en_en | ambiguous | 36 | 0.417 | – | 0.083 |
| E01 | laya_multi_fixed | en_en | insufficient | 28 | 0.071 | 0.071 | 0.143 |
| E01 | laya_multi_fixed | en_en | sufficient | 136 | 0.309 | – | 0.132 |
| E01 | laya_multi_fixed | th_th | ambiguous | 36 | 0.361 | – | 0.028 |
| E01 | laya_multi_fixed | th_th | insufficient | 28 | 0.000 | 0.000 | 0.321 |
| E01 | laya_multi_fixed | th_th | sufficient | 136 | 0.272 | – | 0.110 |
| E02 | laya_multi_fixed | en_en | ambiguous | 47 | 0.319 | 0.211 | 0.277 |
| E02 | laya_multi_fixed | en_en | insufficient | 15 | 0.067 | 0.067 | 0.667 |
| E02 | laya_multi_fixed | en_en | sufficient | 138 | 0.275 | 0.303 | 0.094 |
| E02 | laya_multi_fixed | th_th | ambiguous | 47 | 0.255 | 0.105 | 0.277 |
| E02 | laya_multi_fixed | th_th | insufficient | 15 | 0.000 | 0.000 | 0.933 |
| E02 | laya_multi_fixed | th_th | sufficient | 138 | 0.261 | 0.000 | 0.341 |
| E03 | laya_multi_fixed | en_en | ambiguous | 22 | 0.227 | – | 0.455 |
| E03 | laya_multi_fixed | en_en | insufficient | 27 | 0.000 | 0.000 | 0.926 |
| E03 | laya_multi_fixed | en_en | sufficient | 151 | 0.305 | 0.000 | 0.278 |
| E03 | laya_multi_fixed | th_th | ambiguous | 22 | 0.182 | – | 0.318 |
| E03 | laya_multi_fixed | th_th | insufficient | 27 | 0.000 | 0.000 | 0.667 |
| E03 | laya_multi_fixed | th_th | sufficient | 151 | 0.318 | 0.000 | 0.132 |
| E04 | laya_multi_fixed | en_en | ambiguous | 42 | 0.333 | – | 0.071 |
| E04 | laya_multi_fixed | en_en | insufficient | 43 | 0.302 | 0.302 | 0.000 |
| E04 | laya_multi_fixed | en_en | sufficient | 115 | 0.374 | – | 0.087 |
| E04 | laya_multi_fixed | th_th | ambiguous | 42 | 0.310 | – | 0.190 |
| E04 | laya_multi_fixed | th_th | insufficient | 43 | 0.023 | 0.023 | 0.209 |
| E04 | laya_multi_fixed | th_th | sufficient | 115 | 0.348 | – | 0.235 |
| E05 | laya_multi_fixed | en_en | ambiguous | 21 | 0.000 | – | 0.143 |
| E05 | laya_multi_fixed | en_en | insufficient | 39 | 0.538 | 0.538 | 0.000 |
| E05 | laya_multi_fixed | en_en | sufficient | 140 | 0.193 | – | 0.043 |
| E05 | laya_multi_fixed | th_th | ambiguous | 21 | 0.048 | – | 0.381 |
| E05 | laya_multi_fixed | th_th | insufficient | 39 | 0.513 | 0.513 | 0.077 |
| E05 | laya_multi_fixed | th_th | sufficient | 140 | 0.164 | – | 0.207 |
| E06 | laya_multi_fixed | en_en | ambiguous | 51 | 0.000 | 0.000 | 0.118 |
| E06 | laya_multi_fixed | en_en | sufficient | 150 | 0.320 | – | 0.000 |
| E06 | laya_multi_fixed | th_th | ambiguous | 51 | 0.137 | 0.137 | 0.020 |
| E06 | laya_multi_fixed | th_th | sufficient | 150 | 0.387 | – | 0.000 |
| E01 | laya_routed | en_en | ambiguous | 36 | 0.306 | – | 0.583 |
| E01 | laya_routed | en_en | insufficient | 28 | 0.000 | 0.000 | 0.750 |
| E01 | laya_routed | en_en | sufficient | 136 | 0.140 | – | 0.640 |
| E01 | laya_routed | th_th | ambiguous | 36 | 0.361 | – | 0.028 |
| E01 | laya_routed | th_th | insufficient | 28 | 0.000 | 0.000 | 0.321 |
| E01 | laya_routed | th_th | sufficient | 136 | 0.272 | – | 0.110 |
| E02 | laya_routed | en_en | ambiguous | 47 | 0.426 | 0.105 | 0.000 |
| E02 | laya_routed | en_en | insufficient | 15 | 0.267 | 0.267 | 0.000 |
| E02 | laya_routed | en_en | sufficient | 138 | 0.732 | 0.091 | 0.000 |
| E02 | laya_routed | th_th | ambiguous | 47 | 0.255 | 0.105 | 0.277 |
| E02 | laya_routed | th_th | insufficient | 15 | 0.000 | 0.000 | 0.933 |
| E02 | laya_routed | th_th | sufficient | 138 | 0.261 | 0.000 | 0.341 |
| E03 | laya_routed | en_en | ambiguous | 22 | 0.818 | – | 0.091 |
| E03 | laya_routed | en_en | insufficient | 27 | 0.000 | 0.000 | 0.000 |
| E03 | laya_routed | en_en | sufficient | 151 | 0.556 | 0.000 | 0.007 |
| E03 | laya_routed | th_th | ambiguous | 22 | 0.182 | – | 0.318 |
| E03 | laya_routed | th_th | insufficient | 27 | 0.000 | 0.000 | 0.667 |
| E03 | laya_routed | th_th | sufficient | 151 | 0.318 | 0.000 | 0.132 |
| E04 | laya_routed | en_en | ambiguous | 42 | 0.500 | – | 0.000 |
| E04 | laya_routed | en_en | insufficient | 43 | 0.000 | 0.000 | 0.000 |
| E04 | laya_routed | en_en | sufficient | 115 | 0.374 | – | 0.000 |
| E04 | laya_routed | th_th | ambiguous | 42 | 0.310 | – | 0.190 |
| E04 | laya_routed | th_th | insufficient | 43 | 0.023 | 0.023 | 0.209 |
| E04 | laya_routed | th_th | sufficient | 115 | 0.348 | – | 0.235 |
| E05 | laya_routed | en_en | ambiguous | 21 | 0.333 | – | 0.000 |
| E05 | laya_routed | en_en | insufficient | 39 | 0.000 | 0.000 | 0.077 |
| E05 | laya_routed | en_en | sufficient | 140 | 0.393 | – | 0.000 |
| E05 | laya_routed | th_th | ambiguous | 21 | 0.048 | – | 0.381 |
| E05 | laya_routed | th_th | insufficient | 39 | 0.513 | 0.513 | 0.077 |
| E05 | laya_routed | th_th | sufficient | 140 | 0.164 | – | 0.207 |
| E06 | laya_routed | en_en | ambiguous | 51 | 0.000 | 0.000 | 0.000 |
| E06 | laya_routed | en_en | sufficient | 150 | 0.307 | – | 0.000 |
| E06 | laya_routed | th_th | ambiguous | 51 | 0.137 | 0.137 | 0.020 |
| E06 | laya_routed | th_th | sufficient | 150 | 0.387 | – | 0.000 |
| E01 | rules_only | en_en | ambiguous | 36 | 1.000 | – | – |
| E01 | rules_only | en_en | insufficient | 28 | 0.000 | 0.000 | – |
| E01 | rules_only | en_en | sufficient | 136 | 1.000 | – | – |
| E01 | rules_only | th_th | ambiguous | 36 | 1.000 | – | – |
| E01 | rules_only | th_th | insufficient | 28 | 0.000 | 0.000 | – |
| E01 | rules_only | th_th | sufficient | 136 | 1.000 | – | – |
| E02 | rules_only | en_en | ambiguous | 47 | 0.787 | 0.842 | – |
| E02 | rules_only | en_en | insufficient | 15 | 1.000 | 1.000 | – |
| E02 | rules_only | en_en | sufficient | 138 | 0.986 | 0.939 | – |
| E02 | rules_only | th_th | ambiguous | 47 | 0.787 | 0.842 | – |
| E02 | rules_only | th_th | insufficient | 15 | 1.000 | 1.000 | – |
| E02 | rules_only | th_th | sufficient | 138 | 0.986 | 0.939 | – |
| E03 | rules_only | en_en | ambiguous | 22 | 1.000 | – | – |
| E03 | rules_only | en_en | insufficient | 27 | 1.000 | 1.000 | – |
| E03 | rules_only | en_en | sufficient | 151 | 0.974 | 1.000 | – |
| E03 | rules_only | th_th | ambiguous | 22 | 1.000 | – | – |
| E03 | rules_only | th_th | insufficient | 27 | 1.000 | 1.000 | – |
| E03 | rules_only | th_th | sufficient | 151 | 0.974 | 1.000 | – |
| E04 | rules_only | en_en | ambiguous | 42 | 1.000 | – | – |
| E04 | rules_only | en_en | insufficient | 43 | 1.000 | 1.000 | – |
| E04 | rules_only | en_en | sufficient | 115 | 1.000 | – | – |
| E04 | rules_only | th_th | ambiguous | 42 | 1.000 | – | – |
| E04 | rules_only | th_th | insufficient | 43 | 1.000 | 1.000 | – |
| E04 | rules_only | th_th | sufficient | 115 | 1.000 | – | – |
| E05 | rules_only | en_en | ambiguous | 21 | 0.714 | – | – |
| E05 | rules_only | en_en | insufficient | 39 | 0.846 | 0.846 | – |
| E05 | rules_only | en_en | sufficient | 140 | 0.914 | – | – |
| E05 | rules_only | th_th | ambiguous | 21 | 0.714 | – | – |
| E05 | rules_only | th_th | insufficient | 39 | 0.846 | 0.846 | – |
| E05 | rules_only | th_th | sufficient | 140 | 0.914 | – | – |
| E06 | rules_only | en_en | ambiguous | 51 | 1.000 | 1.000 | – |
| E06 | rules_only | en_en | sufficient | 150 | 0.760 | – | – |
| E06 | rules_only | th_th | ambiguous | 51 | 1.000 | 1.000 | – |
| E06 | rules_only | th_th | sufficient | 150 | 0.760 | – | – |

## P8 — latency harness (warm; concurrency × question count × language)

Laya: in-process on this host. Jev: HTTPS from this host to the remote API. These are deployment topologies, not intrinsic model speed.


| run | concurrency | questions | language | n | p50_ms | p95_ms | p99_ms | throughput_rps |
|---|---|---|---|---|---|---|---|---|
| jev_114_remote | 1 | 1 | en | 30.0 | 258.6 | 327.6 | 328.4 | 3.8 |
| jev_114_remote | 1 | 1 | th | 30.0 | 257.2 | 328.8 | 337.5 | 3.8 |
| jev_114_remote | 1 | all | en | 30.0 | 252.7 | 283.5 | 325.9 | 3.8 |
| jev_114_remote | 1 | all | th | 30.0 | 251.4 | 359.1 | 421.7 | 3.8 |
| jev_114_remote | 4 | 1 | en | 30.0 | 259.8 | 334.8 | 383.5 | 13.8 |
| jev_114_remote | 4 | 1 | th | 30.0 | 270.1 | 347.5 | 370.5 | 13.8 |
| jev_114_remote | 4 | all | en | 30.0 | 279.9 | 380.1 | 419.6 | 13.8 |
| jev_114_remote | 4 | all | th | 30.0 | 277.7 | 387.6 | 405.2 | 13.8 |
| jev_114_remote | 8 | 1 | en | 30.0 | 257.8 | 295.9 | 297.9 | 28.5 |
| jev_114_remote | 8 | 1 | th | 30.0 | 269.4 | 385.8 | 411.3 | 28.5 |
| jev_114_remote | 8 | all | en | 30.0 | 260.4 | 305.4 | 371.7 | 28.5 |
| jev_114_remote | 8 | all | th | 30.0 | 269.2 | 327.3 | 335.9 | 28.5 |
| jev_114_remote | – | load / RTT | load_ms=– | – | 205.0 | 220.4 | – | – |
| laya_routed_cpu | 1 | 1 | en | 120.0 | 1264.7 | 2207.0 | 2442.8 | 0.5 |
| laya_routed_cpu | 1 | 1 | th | 120.0 | 571.0 | 880.4 | 991.9 | 0.5 |
| laya_routed_cpu | 1 | all | en | 120.0 | 4001.0 | 8568.3 | 11609.9 | 0.5 |
| laya_routed_cpu | 1 | all | th | 120.0 | 1743.2 | 3568.3 | 5425.6 | 0.5 |
| laya_routed_cpu | 4 | 1 | en | 120.0 | 4355.1 | 10205.6 | 13691.4 | 0.6 |
| laya_routed_cpu | 4 | 1 | th | 120.0 | 2235.1 | 5290.4 | 7158.2 | 0.6 |
| laya_routed_cpu | 4 | all | en | 120.0 | 10533.6 | 25108.9 | 29376.8 | 0.6 |
| laya_routed_cpu | 4 | all | th | 120.0 | 5571.1 | 13245.5 | 15389.9 | 0.6 |
| laya_routed_cpu | – | load / RTT | load_ms=44298 | – | 207.1 | 400.5 | – | – |
| laya_routed_cuda | 1 | 1 | en | 1800.0 | 51.4 | 71.4 | 103.3 | 13.8 |
| laya_routed_cuda | 1 | 1 | th | 1800.0 | 37.2 | 52.4 | 82.4 | 13.8 |
| laya_routed_cuda | 1 | all | en | 1800.0 | 111.0 | 208.5 | 257.8 | 13.8 |
| laya_routed_cuda | 1 | all | th | 1800.0 | 66.1 | 113.1 | 145.8 | 13.8 |
| laya_routed_cuda | 4 | 1 | en | 1800.0 | 344.3 | 478.7 | 548.6 | 11.5 |
| laya_routed_cuda | 4 | 1 | th | 1800.0 | 273.2 | 385.7 | 456.8 | 11.5 |
| laya_routed_cuda | 4 | all | en | 1800.0 | 411.9 | 555.4 | 660.8 | 11.5 |
| laya_routed_cuda | 4 | all | th | 1800.0 | 319.7 | 438.9 | 542.4 | 11.5 |
| laya_routed_cuda | 8 | 1 | en | 1800.0 | 920.2 | 1186.1 | 1388.3 | 9.2 |
| laya_routed_cuda | 8 | 1 | th | 1800.0 | 748.8 | 956.7 | 1137.5 | 9.2 |
| laya_routed_cuda | 8 | all | en | 1800.0 | 963.1 | 1223.0 | 1384.3 | 9.2 |
| laya_routed_cuda | 8 | all | th | 1800.0 | 789.1 | 989.8 | 1134.5 | 9.2 |
| laya_routed_cuda | – | load / RTT | load_ms=23466 | – | 203.2 | 251.3 | – | – |


The `load / RTT` row gives cold checkpoint load time (Laya) and an unauthenticated HTTPS round trip to the API host (all runs), for context.

## Context fit (Laya truncation)

| split | protocol | context_level | model_track | experiment_id | variant | n_requests | truncated_share |
|---|---|---|---|---|---|---|---|
| test | P4_cross_language | common_fit | laya_en_fixed | E01 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E01 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E01 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E01 | th_th | 50 | 0.020 |
| test | P4_cross_language | common_fit | laya_en_fixed | E02 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E02 | en_th | 50 | 0.040 |
| test | P4_cross_language | common_fit | laya_en_fixed | E02 | th_en | 50 | 0.060 |
| test | P4_cross_language | common_fit | laya_en_fixed | E02 | th_th | 50 | 1.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E03 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E03 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E03 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E03 | th_th | 50 | 0.020 |
| test | P4_cross_language | common_fit | laya_en_fixed | E04 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E04 | en_th | 50 | 0.100 |
| test | P4_cross_language | common_fit | laya_en_fixed | E04 | th_en | 50 | 0.300 |
| test | P4_cross_language | common_fit | laya_en_fixed | E04 | th_th | 50 | 0.640 |
| test | P4_cross_language | common_fit | laya_en_fixed | E05 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E05 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E05 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E05 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E06 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_en_fixed | E06 | en_th | 50 | 0.240 |
| test | P4_cross_language | common_fit | laya_en_fixed | E06 | th_en | 50 | 0.480 |
| test | P4_cross_language | common_fit | laya_en_fixed | E06 | th_th | 50 | 0.720 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E01 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E01 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E01 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E01 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E02 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E02 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E02 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E02 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E03 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E03 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E03 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E03 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E04 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E04 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E04 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E04 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E05 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E05 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E05 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E05 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E06 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E06 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E06 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_multi_fixed | E06 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E01 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E01 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E01 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E01 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E02 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E02 | en_th | 50 | 0.040 |
| test | P4_cross_language | common_fit | laya_routed | E02 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E02 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E03 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E03 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E03 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E03 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E04 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E04 | en_th | 50 | 0.100 |
| test | P4_cross_language | common_fit | laya_routed | E04 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E04 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E05 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E05 | en_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E05 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E05 | th_th | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E06 | en_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E06 | en_th | 50 | 0.240 |
| test | P4_cross_language | common_fit | laya_routed | E06 | th_en | 50 | 0.000 |
| test | P4_cross_language | common_fit | laya_routed | E06 | th_th | 50 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E01 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E01 | th_th | 200 | 0.020 |
| test | primary | common_fit | laya_en_fixed | E02 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E02 | th_th | 200 | 1.000 |
| test | primary | common_fit | laya_en_fixed | E03 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E03 | th_th | 200 | 0.025 |
| test | primary | common_fit | laya_en_fixed | E04 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E04 | th_th | 200 | 0.635 |
| test | primary | common_fit | laya_en_fixed | E05 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E05 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_en_fixed | E06 | en_en | 201 | 0.030 |
| test | primary | common_fit | laya_en_fixed | E06 | th_th | 201 | 0.791 |
| test | primary | common_fit | laya_multi_fixed | E01 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E01 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E02 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E02 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E03 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E03 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E04 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E04 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E05 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E05 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E06 | en_en | 201 | 0.000 |
| test | primary | common_fit | laya_multi_fixed | E06 | th_th | 201 | 0.000 |
| test | primary | common_fit | laya_routed | E01 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E01 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E02 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E02 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E03 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E03 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E04 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E04 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E05 | en_en | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E05 | th_th | 200 | 0.000 |
| test | primary | common_fit | laya_routed | E06 | en_en | 201 | 0.030 |
| test | primary | common_fit | laya_routed | E06 | th_th | 201 | 0.000 |
| test | primary | natural_long | laya_en_fixed | E01 | en_en | 200 | 0.290 |
| test | primary | natural_long | laya_en_fixed | E01 | th_th | 200 | 0.895 |
| test | primary | natural_long | laya_en_fixed | E02 | en_en | 200 | 0.925 |
| test | primary | natural_long | laya_en_fixed | E02 | th_th | 200 | 1.000 |
| test | primary | natural_long | laya_en_fixed | E03 | en_en | 200 | 0.740 |
| test | primary | natural_long | laya_en_fixed | E03 | th_th | 200 | 0.975 |
| test | primary | natural_long | laya_en_fixed | E04 | en_en | 200 | 0.155 |
| test | primary | natural_long | laya_en_fixed | E04 | th_th | 200 | 0.885 |
| test | primary | natural_long | laya_en_fixed | E05 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_en_fixed | E05 | th_th | 200 | 0.365 |
| test | primary | natural_long | laya_en_fixed | E06 | en_en | 201 | 0.811 |
| test | primary | natural_long | laya_en_fixed | E06 | th_th | 201 | 1.000 |
| test | primary | natural_long | laya_multi_fixed | E01 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E01 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E02 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E02 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E03 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E03 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E04 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E04 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E05 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E05 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E06 | en_en | 201 | 0.000 |
| test | primary | natural_long | laya_multi_fixed | E06 | th_th | 201 | 0.075 |
| test | primary | natural_long | laya_routed | E01 | en_en | 200 | 0.290 |
| test | primary | natural_long | laya_routed | E01 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_routed | E02 | en_en | 200 | 0.925 |
| test | primary | natural_long | laya_routed | E02 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_routed | E03 | en_en | 200 | 0.740 |
| test | primary | natural_long | laya_routed | E03 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_routed | E04 | en_en | 200 | 0.155 |
| test | primary | natural_long | laya_routed | E04 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_routed | E05 | en_en | 200 | 0.000 |
| test | primary | natural_long | laya_routed | E05 | th_th | 200 | 0.000 |
| test | primary | natural_long | laya_routed | E06 | en_en | 201 | 0.811 |
| test | primary | natural_long | laya_routed | E06 | th_th | 201 | 0.075 |


See `docs/STUDY_LIMITATIONS.md`, `docs/FAILURE_CASES.md`, `docs/DATASET_CARD.md` and `results/BLOCKERS.md`.
