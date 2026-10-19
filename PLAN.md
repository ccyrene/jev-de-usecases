# Research & Execution Playbook

## Laya vs TypeSafe Jev for Bilingual Data Engineering Decisions

**Study ID:** `de-laya-jev-th-en-2026`  
**Spec date:** 2026-10-19 (Asia/Bangkok)  
**Status:** executable research specification; NOT a completed benchmark  
**Language:** English instructions, Thai/English evaluation corpus  
**Research focus:** head-to-head Laya vs TypeSafe Jev on the EXACT SAME decision tasks, grounded in six realistic open-source database workloads.

> **Instructions for the executing agent:** Treat this document as an execution contract, not an invitation to produce another plan. Set up a separate research repository or working directory; implement the data generators, databases, adapters, experiment runner, tests, analysis, and reports; execute everything possible in the available environment. Start with an offline smoke test. Never invent model outputs, latency, or accuracy. If credentials/GPU/Docker are missing, mark the affected tasks `BLOCKED`, complete all independent tasks, and provide exact next commands. Do not ask unnecessary questions. Never access a real workplace database or send sensitive records to a remote model.

---

## 0. Research objective and non-negotiable scope

### Research questions

1. **RQ1 / quality:** On the same labeled data-engineering decision, which model has higher accuracy, macro-F1, AUROC/AUPRC where meaningful, and lower ordinal error?
2. **RQ2 / language:** Does relative performance change between native English (`en`) and native Thai (`th`) on paired scenarios with equivalent ground truth?
3. **RQ3 / probability quality:** Which model reports better-calibrated choice/score/noul probability distributions, before and after **separately declared** calibration?
4. **RQ4 / latency and cost:** Which deployment provides better throughput, end-to-end p50/p95/p99, and cost per 1,000 decisions at defined hardware and network conditions?
5. **RQ5 / robustness:** What happens under Thai-English code-switching, instruction-language crossover, label order changes, added distractors, insufficient evidence, increasing context length, and new schema versions?
6. **RQ6 / operational benefit:** In optional end-to-end workflows, can either decision engine reduce local-LLM calls and investigation time **without increasing incorrect automated actions**?

### Scope boundary (extremely important)

- **Primary comparison = Laya vs Jev only.** No LLM-generated prose and no judge-LLM in the scoring path. Both receive the same `state`, `questions`, `instructions`, answer-label keys, and criteria, aside from the required model identifier and provider authentication.
- Only **shared primitives** `choice`, `score`, `noul` are evaluated. Do not mix in Jev-specific `box`, image inputs, or other non-common features.
- Both are decision models, **not SQL generators**, graph traversal engines, schema diff engines, or anomaly-detector replacements. Deterministic queries compute factual evidence; the models make a bounded interpretation or routing decision about that evidence.
- A **secondary** experiment may compare `rules -> local LLM` vs `rules -> Laya -> local LLM` vs `rules -> Jev -> local LLM`, but it MUST be in a separate results table and use a frozen local LLM, same retrieved facts, generation config, and review policy.
- A DB's query speed is **not** a model's decision speed. Report DB evidence retrieval latency independently from decision-model latency; report whole pipeline latency separately.
- Never use vendor-published, cross-dataset numbers as the measured head-to-head result.

### Model tracks to implement

| Track | Required? | Exact configuration | Purpose |
|---|---|---|---|
| `laya_routed` | YES | Open-source Laya `Router(preload=True)`; log routing-selected checkpoint, revision and device | Main deployment candidate |
| `laya_en_fixed` | YES | `convaiinnovations/laya` English checkpoint, pinned revision | Controlled English ablation |
| `laya_multi_fixed` | YES | Laya multilingual checkpoint, pinned revision | Controlled bilingual ablation |
| `jev_114` | YES | Official TypeSafe API, explicitly `jev-1.14.0` | Main Jev head-to-head candidate |
| `jev_113` | OPTIONAL | Official TypeSafe API, explicitly `jev-1.13.0` | Version regression check, not silently pooled |
| `laya_finetuned` | OPTIONAL phase B | Fine-tuned ONLY on designated training split, pinned checkpoint | Domain-specialization study, distinct from zero-shot |
| `rules_only` | YES when applicable | Deterministic SQL/regex/AST/graph logic | Non-AI reference baseline |
| `local_llm_only` | OPTIONAL secondary | Frozen, specifically identified local instruction LLM | End-to-end reference only |

**Version warning, verified 2026-10-19:** TypeSafe documents Jev 1.14.0 as available since 2026-10-14, while its `jev-latest` alias still targets 1.13.0; `jev-preview` targets 1.14.0. **Always use pinned IDs for the benchmark**, and verify the actual version in each response. If the provider changes availability, fail clearly rather than switching silently. Jev is remote/closed; Laya is locally deployable. The former has a substantially larger documented context window than Laya, so test a common-fit context track and an independent capability/coverage track.

**Laya version warning:** The English default context budget is about 512 tokens/question, multilingual about 1,024, with option text consuming part of that budget. Record **actual** SDK/model settings rather than blindly trusting these approximate published defaults. The fixed multilingual checkpoint is essential for Thai. Use repository- and model-revision pinning. Read its current `README`, `AGENTS.md`, and model card before coding.

### Inputs supplied / blocked resources

- Use Python 3.11+, `uv` or `pip`, pytest, pandas/polars, scikit-learn, scipy, httpx, docker compose, and pinned dependencies.
- CPU-only operation is valid for correctness. If no GPU, measure `laya_cpu`, and state prominently that CPU-vs-remote-Jev latency is a deployment comparison, not intrinsic model speed.
- Jev key: `TYPESAFE_API_KEY`; **never put real keys in code, shell history, git, CI logs, results, or prompt text**.
- Outbound paid calls only when `ALLOW_REMOTE_API=1` and `TYPESAFE_API_KEY` is set. Offline default = dry-run / fixtures / provider-contract tests only.
- Set `JEV_MAX_USD=5` and `JEV_MAX_REQUESTS=12000` as initial safety caps (configurable). Stop remote calls before exceeding either. Prices must be checked and logged for the run; do not mistake a self-hosted model's API fee of zero for a total cost of zero.
- Source and use only **synthetic** incidents, tables, customer/person records, and PII lookalikes. No copied corporate logs. Document seed, generator implementation and licensing for any public corpora used as optional validation.

---

## 1. Six use cases and the open-source database assignments

| ID | Use case | Primary DB (license) | Why it is technically appropriate | What the model actually decides |
|---|---|---|---|---|
| `E01` | Pipeline Failure Triage | **OpenSearch** (Apache-2.0) | Full-text search over task logs, errors, time-ordered incidents | Failure family, urgency, human escalation |
| `E02` | Data Quality Monitoring | **ClickHouse** (Apache-2.0) | Fast aggregates and time-window statistics across many pipeline events | Anomaly family, quality severity, investigation need |
| `E03` | Schema Drift Detection | **PostgreSQL** (PostgreSQL License) | Real relational schemas, constraints, `information_schema`, transactional migrations | Drift impact class, risk, review decision |
| `E04` | Data Lineage Impact Analysis | **Neo4j Community Edition** (GPLv3; verify exact distribution) | Directed dependency graph, multi-hop upstream/downstream queries | Business impact tier, owning domain, escalation |
| `E05` | PII / Sensitive Data Classification | **PostgreSQL + JSONB** (PostgreSQL License) | Mixed relational/JSON fields, typed columns, metadata, masking views | Column risk class, sensitivity, review decision |
| `E06` | Natural Language to SQL | **DuckDB** (MIT) | Embedded analytics SQL engine, reproducible datasets and executable read-only candidates | User intent, candidate-SQL semantic validity, need clarification |

All DBs must be open-source editions. Pin a verified stable image/package version and digest in the run manifest; do **not** use `latest` in committed compose files. For Neo4j, use `NEO4J_EDITION=community` and no enterprise-only features. If Neo4j licensing is unsuitable for downstream distribution, the documented alternative for `E04` is **Apache AGE** (Apache-2.0) on PostgreSQL; treat that as a separate engine track, not an unrecorded substitution.

### Recommended unit of evaluation

A **scenario** is an underlying technical situation, not merely a translated paragraph. It has a unique `scenario_id`, DB-backed evidence, and verified gold judgments. Each scenario has:

- `en`: native English description + evidence presentation;
- `th`: native Thai description + the **same underlying technical facts**;
- identical machine facts (code, SQL identifiers, IDs, numeric metrics, timestamps);
- separately localized rubrics/instructions, with **stable ASCII choice IDs**;
- gold labels that are invariant between the languages, or a documented failure of semantic equivalence.

Never score English and Thai as if they were independent ground truths. Pair by `scenario_id` and use paired statistics.

---

## 2. Repository to build

```text
laya-jev-de-benchmark/
  README.md                         # real setup / repro commands
  CLAUDE.md                         # concise execution instructions copied from this spec
  pyproject.toml                    # pinned dependencies and executable scripts
  uv.lock                           # or equivalent lockfile
  .env.example                      # NO real credentials
  .gitignore
  configs/
    study.yaml                      # study ID, seed, datasets, splits, budgets
    models.yaml                     # exact model IDs and revisions
    prompts/                        # YAML with EN/TH criteria; frozen after dev
      e01.yaml ... e06.yaml
    experiments.yaml                # protocol IDs and ablations
  infra/
    compose.yaml                    # one DB profile at a time
    init/e01_opensearch/
    init/e02_clickhouse/
    init/e03_postgres/
    init/e04_neo4j/
    init/e05_postgres/
    init/e06_duckdb/
  src/debench/
    providers/base.py
    providers/laya_provider.py
    providers/jev_provider.py
    providers/mock_provider.py      # CI only, NEVER a real measured model
    dataset/paired_generator.py
    dataset/gold_validation.py
    dataset/e01.py ... e06.py
    evidence/opensearch.py
    evidence/clickhouse.py
    evidence/postgres_schema.py
    evidence/neo4j_lineage.py
    evidence/postgres_pii.py
    evidence/duckdb_sql.py
    eval/metrics.py
    eval/calibration.py
    eval/paired_statistics.py
    eval/robustness.py
    eval/latency.py
    eval/cost.py
    runner.py
    report.py
  data/
    generated/.gitkeep
    snapshots/.gitkeep
    labels/.gitkeep
  tests/
    test_provider_contract.py
    test_pair_equivalence.py
    test_gold_is_not_in_prompt.py
    test_db_ground_truth.py
    test_metric_math.py
    test_sql_read_only.py
    test_reproducibility.py
    test_no_real_pii.py
    test_budgets_and_retries.py
  results/
    .gitkeep
  docs/
    DATASET_CARD.md
    MODEL_CARD_SNAPSHOTS.md
    STUDY_LIMITATIONS.md
    FINAL_REPORT.md
  Makefile
```

`data/generated`, `results/raw`, and any authentic-looking synthetic records must be appropriately ignored or explicitly marked synthetic; small, safe sample fixtures can be versioned. Include SQL/Cypher setup and teardown scripts, health checks, and readiness timeouts.

### Required stable data schema (`scenario`)

```json
{
  "scenario_id": "E01-0001",
  "experiment": "E01",
  "split": "test",
  "seed": 20261019,
  "language": "th",
  "pair_id": "E01-0001",
  "state_text": "<localized text rendered from the same facts>",
  "evidence_ref": {"backend": "opensearch", "document_ids": ["log-0001"]},
  "context_level": "common_fit",
  "questions_version": "e01-v1",
  "gold": {
    "failure_family": "schema_mismatch",
    "severity_level": 2,
    "requires_human": true
  },
  "difficulty_tags": ["negation", "sql_identifier", "code_switching"],
  "label_source": "injected_fault_verified_by_db",
  "gold_verified": true,
  "semantic_pair_validated": true
}
```

This is an **internal** combined object. The actual provider payload MUST be built from an explicit allowlist (`state_text`, rendered evidence, and question definitions), with `gold`, `label_source`, `seed`, internal fault IDs, split and difficulty tags REMOVED. Unit-test for leakage.

### Required common decision interface

```python
from dataclasses import dataclass
from typing import Any, Literal, Protocol

@dataclass(frozen=True)
class DecisionRequest:
    scenario_id: str
    language: Literal["en", "th"]
    state: str | dict[str, Any] | list[str]
    questions: dict[str, dict[str, Any]]

@dataclass(frozen=True)
class DecisionResponse:
    provider: str
    resolved_model: str
    answers: dict[str, dict[str, Any]]
    wall_ms: float
    usage_input_tokens: int | None
    usage_output_tokens: int | None
    error: str | None
    metadata: dict[str, Any]

class DecisionProvider(Protocol):
    async def evaluate(self, request: DecisionRequest) -> DecisionResponse: ...
```

- Local Laya: implement `Router(preload=True, device="cpu" or "cuda")` and `predict(state, questions)` in an async-compatible worker; validate response shape, routing info and checkpoint revision. **Warmup and model-load timing are separated.** A fixed-checkpoint adapter uses `laya.load(...)` only after checking actual installed signatures.
- Official Jev: call `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer $TYPESAFE_API_KEY`; use `model: "jev-1.14.0"` in the provider-specific envelope; keep shared `state` and `questions` byte-for-byte identical to Laya's canonical shared request. Use `httpx.AsyncClient` with explicit timeouts, retries, rate limit handling, token/request budget, and response version checks.
- The TypeSafe SDK (`typesafe-sdk`) is an acceptable alternative to HTTPX, but never let SDK defaults auto-select `jev-latest` or auto-retry without bounding the total spend.
- Normalize outputs into `choice` probability map, `score` indexed distribution, and `noul` P(true), with validation (`finite`, `[0,1]`, distribution sum tolerance). **Do not use vendor `confidence` as a cross-model metric:** formulas differ. Compute comparable `max_probability`, normalized entropy, and task-specific calibrated probability from each probability distribution instead.
- If an answer is malformed or missing, record error/abstain; do not impute a guess and count it as a success.

### Canonical wire example (illustrative; never print a real key)

```json
{
  "state": {
    "dag": "daily_customer_etl",
    "task": "transform_customer_data",
    "log_excerpt": "psycopg2.errors.UndefinedColumn: column customer_id does not exist",
    "observed": "This task started failing after a deployment."
  },
  "questions": {
    "failure_family": {
      "type": "choice",
      "instructions": "Which failure family best matches the evidence?",
      "criteria": {
        "schema_mismatch": "A referenced table or column is missing or structurally incompatible",
        "connectivity": "Network, DNS, or database reachability failure",
        "authentication": "Invalid authentication or insufficient permissions",
        "data_error": "Invalid values or input-data integrity problem",
        "resource_exhaustion": "Memory, CPU, quota, or disk exhaustion",
        "other_unknown": "Another cause or not enough evidence"
      }
    },
    "severity": {
      "type": "score",
      "instructions": "Rate expected operational severity from the visible evidence.",
      "criteria": [
        "0 = minor, non-blocking",
        "1 = degraded but recoverable",
        "2 = important pipeline blocked",
        "3 = critical multiple downstream consumers blocked"
      ]
    },
    "requires_human": {
      "type": "noul",
      "instructions": "Does this incident require a human review before changing a production system?"
    }
  }
}
```

For Thai, translate **all natural-language fields** in the `state`, `instructions`, and `criteria` without translating machine identifiers, SQL, code, option keys, or gold labels. Save native Thai translations in UTF-8 and include a Thai speaker's review or documented manual QA. A language model may draft translations, but model-generated labels **are not** ground truth.

---

## 3. Common experimental protocol: apples-to-apples

### 3.1 Dataset sizes and frozen splits

Target per use case: **300 independent technical scenarios**:

- `dev`: 60 scenarios for rubric, schema, and integration development;
- `calibration`: 40 scenarios, only for post-hoc temperature/threshold selection;
- `test`: 200 scenarios, **never** consulted during prompt/rubric tuning.

Across 6 use cases: 1,800 distinct technical scenarios, each in EN and TH = 3,600 language-specific records. The two primary model tracks would issue 7,200 scenario-model calls for the full in-language primary comparison (one call can ask multiple questions). Count actual attempts, not expected ones. If infrastructure or budget prevents full runs, use a clearly named `pilot` (minimum 20 paired test cases/use case), DO NOT label a pilot as the final result, and preserve a path to resumption.

Seed = `20261019` for generator/split order. Use **grouping by the underlying incident/schema/graph/SQL template** to prevent near-duplicate leakage: no templates or mutated copies crossing splits. Include at least 20% ambiguous/insufficient-evidence scenarios where appropriate, and 20% hard-negative near misses. Balance primary categories or report imbalance and macro metrics. Gold must be generated from an independent injected fault, relational constraint, reference graph traversal, SQL oracle, or validated synthetic column provenance, **not from Laya, Jev, or an LLM-as-judge alone**.

### 3.2 Paired bilingual track

**Primary protocol** (same facts and option keys):

1. `en_state + en_instructions` -> Laya and Jev;
2. `th_state + th_instructions` -> Laya and Jev;
3. Compare each model by language, and per-scenario `th - en` changes;
4. Compare Laya-Jev **paired differences** within each language.

**Cross-lingual robustness** on a predeclared random 50-scenario subset/use case (200 test scenarios remain untouched for main scoring):

| State text language | Question/rubric language | Name |
|---|---|---|
| English | English | `en_en` (primary) |
| Thai | Thai | `th_th` (primary) |
| Thai | English | `th_en` (crossover) |
| English | Thai | `en_th` (crossover) |

Additional declared slices: Thai with English SQL/log tokens; genuine Thai technical abbreviations; mixed Thai+English prose; negation; spelling variants; long evidence; out-of-domain; no-answer. Never translate executable SQL, error codes, column identifiers, counts, or timestamps. Check paired label preservation. Store language-detector/routing choice for Laya.

### 3.3 Context fairness versus capability

**Track A (`common_fit`)**: Render the same essential facts with deterministic, model-blind compression if needed. Confirm BOTH Laya configurations accept the complete payload (instructions + criteria + state) without truncation, using their own tokenizers, and that Jev accepts it. If any model cannot fit, skip from `common_fit` and record `unsupported`, not `wrong`. Keep a coverage denominator. Avoid clipping away the gold-critical clue.

**Track B (`natural_long`)**: Feed naturally long evidence buckets (`short`, `medium`, `long`, `very_long`) without sacrificing key clues. Record actual request size and whether each model supports it; **do not pretend Jev and Laya have equal context capability**. Report raw end-to-end task coverage and quality on the subset each supports, distinct from the shared-budget head-to-head.

**Track C (`equal_information`)**: Optional deterministic evidence extraction (same short DB facts) for both. This tests whether narrow structured state removes Laya's context disadvantage. Log all extraction rules and no model-dependent rewriting.

### 3.4 Pre-registered answer decoding

- `choice`: predicted label = API-selected choice, cross-check with `argmax(probabilities)`; primary metrics macro-F1 and balanced accuracy; also negative log likelihood (NLL), multiclass Brier, ECE.
- `score`: levels are **0-based ordered**; probability-weighted expected ordinal index is used for MAE; argmax level for accuracy/quadratic weighted kappa. Use the **same numeric level semantics** in TH and EN; never reorder ordinal criteria.
- `noul`: use raw P(true) to compute AUROC, AUPRC, Brier and ECE; apply a fixed 0.5 baseline decision plus separately tuned operating thresholds on calibration, never on test.
- Choice abstention uses calibrated `max(p)` / entropy; choose thresholds on calibration per `(model, language, question type)` and report risk-coverage curves. If labels are imbalanced, show per-class recall and minority-class PR.
- For a true unknown case, `other_unknown` must be a real choice option; distinguish this valid prediction from provider failure/timeout abstention.

### 3.5 Statistics and reporting

- Every final accuracy/F1 difference: paired bootstrap **95% CI** clustered by scenario ID, minimum 2,000 resamples; report sample counts and deterministic seed.
- For paired categorical correctness use McNemar as a secondary test (two-sided exact if small discordant count). Do not report significance without multiplicity caution; pre-register six primary comparisons and adjust or report unadjusted exploratory analyses explicitly.
- Report breakdown by `experiment`, `language`, `primitive`, `model`, `difficulty`, `context_bucket`, `question_count`, and `evidence_sufficiency`.
- Compute `language_gap = performance(th_th) - performance(en_en)` *within a model*, and `head_to_head_gap = performance(Laya) - performance(Jev)` *within a language*. Report both **with CI**.
- Report invalid-output rate, failure/timeout rate, unsupported-context rate, and coverage alongside quality; successful cases only is a biased denominator.

---

## 4. E01 - Pipeline Failure Triage (OpenSearch)

### DB workload

Use a **single-node OpenSearch** container with a synthetic `airflow-task-logs` index; document sample mappings and deterministic seeding. Minimum ~10,000 log/event records from 300 distinct root-cause scenarios plus distractor surrounding logs. Index fields: `event_ts`, `dag_id`, `task_id`, `run_id`, `status`, `message`, `exception_type`, `retry_count`, `duration_ms`, `upstream_task`, `deployment_id`. Do not place `gold_cause` in indexed documents presented to the model. Fetch the most relevant, time-bounded task logs using a query template identical across providers.

### Gold questions

| ID | Primitive | Closed answer set / rubric | Ground truth oracle |
|---|---|---|---|
| `failure_family` | choice | `schema_mismatch`, `connectivity`, `authentication`, `data_error`, `resource_exhaustion`, `dependency_failure`, `other_unknown` | Deterministically injected failure and checked log/error signature |
| `severity` | score | 0 informational, 1 task retryable, 2 important workflow blocked, 3 critical multiple consumers affected | Defined DAG graph, SLA and blocked-consumer count |
| `requires_human` | noul | P(manual intervention required before mutating pipeline/database) | Pre-defined runbook decision rules and permissions |

**Fault families to generate:** `UndefinedColumn`, relation missing, DDL rename, DNS refused, socket timeout, password auth denied, access denied, type conversion error, malformed JSON, missing S3/object, OOM, disk full, dependency upstream failure, false alarm / already recovered. Include multiple root-cause possibilities only when evidence genuinely does not disambiguate; gold `other_unknown` and review rather than fabricate certainty.

**Example paired English state:** "The customer transform task failed after a schema rollout; the log reports `UndefinedColumn: customer_id` and its downstream daily report is blocked." Thai is the semantic equivalent (see bilingual examples appendix).

**Required E01 outputs:** confusion matrix per language; minority-failure recall; severe-incident false-negative rate; auto-triage coverage at <=1% observed severe-incident miss rate (if the confidence interval supports it); OpenSearch retrieval p95; separate decision and full pipeline p95.

**Non-AI baseline:** regex/error-code/runbook mapping; hard-coded rules MUST win where the exception type unambiguously identifies the error. Log where models actually add value (mixed/ambiguous evidence).

---

## 5. E02 - Data Quality Monitoring (ClickHouse)

### DB workload

Use ClickHouse to store 100,000+ synthetic events and computed data quality measurements at pipeline-window granularity. Suggested tables:

```sql
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
```

Create separate aggregate queries for expected vs observed row count, null rate, duplicate rate, min/max, schema type mismatches, freshness and distribution shifts. Keep raw synthetic events as oracle; hand the decision engine a **short evidence summary** with computed statistics, alert rule, and the relevant business context. No label hints from the injection generator.

### Gold questions

| ID | Primitive | Closed answer set / rubric | Ground truth oracle |
|---|---|---|---|
| `anomaly_family` | choice | `volume_drop`, `null_spike`, `duplicate_spike`, `distribution_shift`, `freshness_lag`, `type_violation`, `normal_or_unknown` | Independently injected perturbation + exact ClickHouse aggregates |
| `quality_impact` | score | 0 no confirmed impact, 1 low, 2 medium, 3 high, 4 critical | Threshold/SLA policy, affected partitions and validated dependent jobs |
| `investigate_now` | noul | P(incident should be investigated immediately) | Explicit policy applied to validated statistics and downstream criticality |

Generate: weekly seasonality, sudden partition loss, expected holiday dip vs abnormal row collapse, legitimate user growth, upstream null spikes, a few contaminated batches, delayed ingestion, duplicated replay, change in numeric value distribution. Include **no-anomaly** controls and **insufficient-history** cases.

**Critical confound:** The model must not be asked to rediscover arithmetic from 100,000 rows. SQL computes facts; the model classifies qualitative meaning from the same facts. Implement a `rules_only` detector; separately report scenarios where numerical thresholds fully decide the answer (an LLM may be unnecessary).

**Required E02 outputs:** macro-F1 by anomaly; precision/recall on high-risk cases; severity MAE; false-alert rate; per-language delta; benefit over deterministic thresholds on genuinely ambiguous contexts; DB query p95 versus model p95.

---

## 6. E03 - Schema Drift Detection (PostgreSQL)

### DB workload

Start with at least 20 synthetic relational tables for orders, payments, customers, inventory, and reporting. Use transactional migration scripts to create *before* and *after* versions; snapshot `information_schema.columns`, constraints, indexes, and dependency descriptions. Run actual backward-compatibility checks against a bank of read-only consumer queries. Avoid assuming that every DDL change is breaking: database version, defaults, constraints, views, and consumers matter.

### Gold questions

| ID | Primitive | Closed answer set / rubric | Ground truth oracle |
|---|---|---|---|
| `drift_impact` | choice | `non_breaking_addition`, `breaking_rename_drop`, `type_or_cast_break`, `constraint_or_nullability_break`, `semantic_change`, `no_change_or_unknown` | Before/after catalog diff + real consumer-query tests + documented semantics |
| `migration_risk` | score | 0 safe, 1 low, 2 moderate, 3 high, 4 critical | Successful/failing consumer tests, migration reversibility, SLA policy |
| `review_before_deploy` | noul | P(manual schema review needed before deployment) | Change management policy + observed compatibility |

Generate: nullable-column addition, mandatory column without default, rename/drop, widening vs narrowing types, numeric precision reduction, changed meaning with same SQL type, altered unique/check constraints, view compatibility, SQL casts, index-only changes, deprecated columns with no active consumers. Each example must have a before/after snapshot, actual migration SQL and consumer compatibility result.

**Required E03 outputs:** recall on proven breaking changes (primary safety metric); non-breaking false-positive rate; risk MAE; cross-language accuracy; failed-compatibility-query evidence and runnable reproduction.

**Baseline:** PostgreSQL catalog diff + consumer-query validation. Decision engines may add value in severity and human review interpretation, not replace a schema compatibility test.

---
## 7. E04 - Data Lineage Impact Analysis (Neo4j Community Edition)

### DB workload

Seed a directed property graph with >=500 assets and >=1,500 `DEPENDS_ON` / `PRODUCES` / `CONSUMES` edges. Node labels: `SourceTable`, `Transformation`, `DAG`, `Dataset`, `Dashboard`, `CriticalBusinessProcess`, `Owner`. Node properties: `asset_id`, `domain`, `criticality`, `freshness_sla`, `owner_team`, `description_en`, `description_th`. Direction must be documented and tested; define "downstream" as consumers reached by a **consistent, verified** traversal. Include branching, diamond-shaped dependencies, isolated nodes, stale edges, missing owners, and cycles; guard traversal length.

**Graph oracle:** implement a Cypher query returning affected downstream critical datasets/dashboards, shortest hop distance, number of unique affected consumers, and owner domains. Verify its output with a tiny hand-checked DAG and a separate graph algorithm implementation. Only pass the graph fact summary and bounded neighborhood excerpt to the decision model. **Do not ask Laya or Jev to discover graph edges from memory.**

### Gold questions

| ID | Primitive | Closed answer set / rubric | Ground truth oracle |
|---|---|---|---|
| `impact_tier` | choice | `none`, `single_noncritical`, `multi_noncritical`, `critical_downstream`, `unknown` | Verified reachability + criticality policy |
| `impact_severity` | score | 0 none, 1 low, 2 medium, 3 high, 4 critical | Affected assets, downstream SLA, explicit deterministic business weights |
| `needs_owner_escalation` | noul | P(change requires owner approval before deployment) | Policy applied to graph oracle and asset ownership |

Optional separate `owner_domain` choice from **at most 8** domains, with `unknown` available. For multiple owner domains, do not force a false single-label truth: either pick the primary owner under a documented tie-break rule or use independent `noul` per domain.

**Failure cases:** missing graph edges, a newly added dashboard, no active consumer, dependency cycles, stale metadata, a low-value table connected to a high-criticality executive dashboard, and ambiguous ownership. Add explicit `evidence_sufficiency` labels.

**Required E04 outputs:** false-negative rate on critical downstream impacts, severity MAE, abstain/escalate coverage, Thai-vs-English effect of localized descriptions and ownership notes, graph retrieval and inference latencies separately.

**Baseline:** Neo4j traversal + deterministic impact severity policy. A graph's exact downstream reachability is not a decision-model competition.

---

## 8. E05 - PII / Sensitive Data Classification (PostgreSQL + JSONB)

### DB workload

Create synthetic customer, employee, supplier, web-event, and audit tables containing column metadata and **obviously fictitious** example values. Mix scalar columns and `JSONB` nested attributes. Generate ~10,000 rows, but supply models only 2-5 synthetic, minimized, partially masked examples per candidate field plus documented purpose/lineage metadata. No real names, phone numbers, national IDs, patient records, staff exports, or workplace content. Use schema-qualified columns and versioned metadata.

### Gold questions

| ID | Primitive | Closed answer set / rubric | Ground truth oracle |
|---|---|---|---|
| `data_class` | choice | `direct_identifier`, `quasi_identifier`, `sensitive_personal`, `non_personal`, `unknown` | Synthetic schema generator's field semantics + a human-readable policy definition |
| `handling_tier` | score | 0 public/non-personal, 1 internal, 2 restricted, 3 highly restricted | Pre-registered data handling policy; direct identifiers and sensitive fields require elevated handling |
| `requires_masking_review` | noul | P(field should be reviewed for masking/access control) | Declared data-handling policy |

Use a **single primary class** with an explicit precedence rule: `sensitive_personal > direct_identifier > quasi_identifier > non_personal`, except unknown when context is insufficient. Preserve an independent multi-label flag for ancillary evaluation; do not conflate single-label and multi-label metrics. Labels must come from scenario construction and documented semantics, not a regex alone.

Cases: email, Thai-looking national ID (synthetic/invalid-check-digit where safe), international phone number, government-like identifier, random transaction ID, UUID, shipping address, birth month/year, exact birthday, inferred health attribute (fictitious), tokenized account ID, hashed identifier, Thai and English names, nested JSON key, misleading column name (`customer_id` with randomly generated data), generic free text, and unknown fields. Evaluate **Thai semantic metadata** versus English metadata without leaking actual PII.

**Security requirements:** redact sample values, never send genuine PII to Jev, and test that mock national IDs are unmistakably fictional. A high model probability does not establish PDPA/legal compliance. A real deployment needs legal/policy review and deterministic controls. Treat this as **risk triage**, not an autonomous legal classification system.

**Required E05 outputs:** recall on `sensitive_personal` and `direct_identifier`; false-negative rate at chosen automatic allow threshold; calibration; review workload; Thai metadata performance; mock-data leakage tests.

**Baseline:** PostgreSQL metadata rules + structural/regex candidate detection. Report when context semantics improve on simple column-name heuristics.

---

## 9. E06 - Natural Language to SQL Routing and Candidate Validation (DuckDB)

### Why the scope must be exact

Laya and Jev **cannot produce arbitrary SQL text** through the common `choice` / `score` / `noul` contract. Comparing their SQL-generation accuracy directly would be scientifically invalid. Instead measure their ability to **understand a user's analytic intent, select a bounded query type, judge a candidate query, and decide whether the request is ambiguous or requires review**. Test SQL generation separately with a **fixed local LLM** and add Laya/Jev as optional decision validators.

### DB workload

Create a deterministic DuckDB analytics database (`data/warehouse.duckdb`), 100,000+ synthetic rows spread across at least 10-15 tables: customers, orders, order_items, products, payments, provinces, returns, daily_fact_sales, product_categories, campaigns, calendar, inventory snapshots. Define primary/foreign-key relationships in `schema.json` and a human-checked natural-language data dictionary in EN and TH. Keep business semantics identical across localized versions, preserving SQL identifiers in ASCII.

Produce **reference SQL + verified expected result**, plus candidate SQL variants:

- valid query with equivalent semantics but different formatting/aliasing;
- wrong join, omitted WHERE filter, wrong aggregation, wrong time range, wrong timezone/date bucket;
- absent-column query, ambiguous metric definition, omitted business constraint;
- unsafe operation (`DELETE`, `UPDATE`, `DROP`, `ATTACH`, `COPY`, external reads) that MUST be blocked by deterministic policy before execution;
- queries that accidentally return the same results on the original DB but diverge on independently seeded adversarial database snapshots.

Use a **read-only connection**, an AST allowlist (e.g. `sqlglot` with DuckDB dialect; verify parser support) and query timeout/sandbox. NEVER execute untrusted mutations. The SQL AST/permissions layer is the final authority for safe execution; model opinion cannot override it.

### Gold questions

| ID | Primitive | Closed answer set / rubric | Ground truth oracle |
|---|---|---|---|
| `intent_type` | choice | `aggregation`, `filtered_lookup`, `join_analysis`, `time_series`, `ranking`, `unsupported_or_ambiguous` | Authored request template + SQL reference intent |
| `candidate_verdict` | choice | `semantically_correct`, `semantically_wrong`, `insufficient_specification`, `unsafe_or_unexecutable` | Multiple DuckDB snapshots + SQL read-only validation + expert review for ambiguity |
| `candidate_quality` | score | 0 unusable, 1 weak, 2 partly meets intent, 3 meets intent with minor issue, 4 fully correct | Pre-registered rubric and oracle |
| `needs_clarification` | noul | P(query should not proceed without clarifying user intent) | Explicit ambiguity flags + documented missing constraints |
| `candidate_safe_to_consider` | noul | P(candidate merits review for safe read-only execution) | Deterministic SQL policy (note this is a weak use case for AI) |

The candidate verdict input includes **natural-language request, schema descriptions, candidate SQL, and relevant constraints**, rendered in EN or TH. Keep actual SQL bytes the same between language pairs. Include Thai queries containing Thai province names and English SQL identifiers, and explicitly separate translated labels from translated data values.

**Optional full pipeline:** freeze one local model (e.g. a verified instruction-tuned model with Thai/English capability that fits available hardware; log exact repo/revision/quantization, seed, decoding, inference server) to generate candidate SQL. Run `LLM only` vs `LLM + Laya validator` vs `LLM + Jev validator` with the same candidates. Measure `execution_accuracy` versus reference across **multiple DB seeds**, `valid_sql_rate`, `unsafe_sql_block_rate`, `review_rate`, extra latency, and incremental cost. Do not claim Laya/Jev generated the SQL.

**Required E06 outputs:** candidate verdict macro-F1, critical unsafe false-negative count, semantic-wrong acceptance rate, Thai/English differences, LLM pipeline metrics in a separately labeled table if executed.

---

## 10. Controlled experiments to run in EACH use case

Each E01-E06 must run the same controlled experiment names; use a result table with `experiment_id` and `protocol` rather than six incompatible dashboards.

| Protocol | Required | Test | Fixed controls | Measurements |
|---|---|---|---|---|
| `P0_contract` | YES | Provider schema/auth/normalizer checks | Single dry sample, no synthetic accuracy claims | Pass/fail, error parsing, version |
| `P1_primary_en` | YES | Laya routed vs Jev 1.14 on EN state+EN instructions | Same scenarios, labels, evidence, prompts | Accuracy/F1, NLL, Brier, ECE, coverage |
| `P2_primary_th` | YES | Laya routed vs Jev 1.14 on TH state+TH instructions | Same scenarios, labels, evidence, prompts | Same + language gap |
| `P3_checkpoint` | YES | Laya fixed EN vs fixed multilingual vs router; Jev 1.14 | Same paired EN/TH test set | Router mistakes, checkpoint-specific performance |
| `P4_cross_language` | YES | `en_en`, `th_th`, `th_en`, `en_th` | Frozen preselected subset | Macro-F1 gaps, flip rates |
| `P5_order_paraphrase` | YES | Reorder Choice options; 3 semantic paraphrases per question | Only one perturbation at a time; fixed gold | Flip rate, distribution TVD, KL where valid |
| `P6_calibration` | YES | Raw vs separately calibrated probabilities | Fit only on calibration split | ECE, Brier, NLL, risk/coverage |
| `P7_context` | YES | Common-fit vs natural-long evidence | Same gold-critical facts | Accuracy by size, unsupported/truncation rate |
| `P8_latency_cost` | YES | Single/batched questions, warm/cold, concurrency | Fixed host and network, workload trace | p50/p95/p99, QPS, throughput, costs |
| `P9_ambiguity` | YES | Missing clues, adversarial confounders, irrelevant context | Same scenario paired languages | Unknown detection, high-confidence error |
| `P10_jev_regression` | OPTIONAL | Jev 1.13 vs Jev 1.14 | Same requests and splits | Paired quality/calibration/latency |
| `P11_laya_finetune` | OPTIONAL | Domain-specialized Laya vs untuned Laya vs untuned Jev | New training-only scenarios; test frozen | Lift and training+serving cost |
| `P12_llm_cascade` | OPTIONAL | Rules+LLM / Rules+Laya+LLM / Rules+Jev+LLM | Same frozen local LLM, outputs and gates | End-to-end safety/latency/cost |

### Permutation controls

For `choice`, preserve the mapping of label key to description while changing insertion order with at least five seeds. Compare label predictions and probability vectors reindexed by key. **For `score`, never permute the ordered criteria** because that changes what 0, 1, 2 mean; test semantic paraphrase and rubric localization instead. For `noul`, test logically equivalent negation carefully: `P(not X)` should be checked against `1 - P(X)`, but do not score it as the same literal yes-label.

### Cardinality stress (supplementary)

Use choice sets of 2, 4, 8, 16, 32, 64 and optionally 128, with objectively valid distractors. Keep the number and meaning of alternatives equal. If Laya cannot fit options in its shared token budget, mark as **capacity limitation**, do not drop distractors only on Laya. Do **not** mix cardinality results into the primary <=8-label questions. Log token allocation and prompt truncation evidence.

### Operational guardrail testing

- Implement explicit human-review fallback on uncertain/high-impact cases for both providers.
- At high severity, measure false-negative rate **before** and **after** gating. Pre-register the cost matrix for false auto-allow versus unnecessary review; optimize on calibration only.
- Publish coverage vs selective risk curves instead of one arbitrary threshold.
- Report available lower bound where zero unsafe events are observed (zero in a small sample != proved 0% risk).

---

## 11. Benchmark runtime, latency, and cost instrumentation

### 11.1 Latency apples-to-apples notes

Different endpoint topologies make raw model latency inherently unequal:

1. **Laya in-process, warm GPU/CPU** = local model/runtime only; useful as an intrinsic baseline, **NOT directly comparable** to Jev remote HTTP latency.
2. **Laya local HTTP server** vs **Jev official remote HTTP API** = deployment-level end-to-end comparison, inclusive of client serialization, HTTP, network, and queueing; label this `e2e_from_same_client`.
3. **Evidence DB latency**, **decision latency**, **local LLM latency** and **action validation latency** = separately measured phases.

For every latency sample log: `run_id`, monotonic timing, model checkpoint/version, hostname/hardware CPU/GPU, RAM/VRAM, device type, batch/question count, payload bytes/token estimate, region/network conditions, retry attempts, cache status, warm/cold status, concurrency, DB backend. Use no request-response body logging by default, only hashed or synthetic data with explicit flag. Capture clock/timezone and network RTT baseline to official Jev endpoint when possible without leaking credentials.

### 11.2 Measurement protocol

- At least 30 warmup calls per locally loaded Laya configuration where possible; warmup separately from measured requests.
- For latency, choose a stable sample of 100 paired cases/use case (or entire test set if resources permit), execute 3 repeated runs with **seeded shuffled order** and provider interleaving (subject to remote API rate limits). Retries are recorded; a retry is an observed overhead, not invisible.
- At concurrency 1, 4, 8 (then optional 16), measure p50/p95/p99 and success rate, while respecting published/actual rate limits. For local Laya benchmark one vs multiple questions per call (1, 3, 6); for Jev send matching question bundles.
- Device memory and load time are reported separately. Cold-start time includes checkpoint load; do not mix cold samples into warm percentiles.
- **No persistent application response cache** in measured runs. Label any repeated inference as repeated, not deduplicated.
- Throttle rather than hammering the remote API; back off `429`, respect `Retry-After`, bound retries, and stop at spend/request limits.

### 11.3 Cost per 1,000 decisions

```text
remote_jev_cost_usd = billed_input_tokens / 1e6 * verified_usd_per_million_input_tokens
local_laya_hourly_cost = actual_instance_usd_per_hour + (optional energy cost)
local_laya_cost_1000 = local_laya_hourly_cost / (sustained_successful_decisions_per_hour / 1000)
end_to_end_cost = retrieval + model/host + local_llm + retries + review_cost_assumption
```

- TypeSafe documentation currently lists **USD 0.042 per million input tokens** for 1.13 and 1.14 (verify at run time), not 0.042 USD per thousand. The correct denominator matters.
- Distinguish `questions` from `API calls` and `scenarios`: a bundled request has multiple decision questions. Report USD/1,000 successful **questions** and USD/1,000 **scenario decisions**.
- Use *actual* usage fields returned by APIs, plus provider billing if available; if billing differs, report discrepancy. If model token accounting is missing or not comparable, do not invent it: use measured billable input tokens or mark estimated.
- Report self-hosted fixed capacity scenarios (e.g. always-on vs batch jobs) and include GPU rental/storage if used; unused headroom changes cost efficiency.
- The given price is a dated reference point, not a guaranteed future tariff.

---

## 12. Evaluation code requirements and formulas

### Probability integrity

Before any scoring, validate each answer:

```python
import math

def check_probabilities(p):
    assert p, "empty probability distribution"
    vals = list(p.values())
    assert all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals)
    assert all(0.0 <= x <= 1.0 for x in vals)
    assert abs(sum(vals) - 1.0) <= 1e-3, "probabilities do not sum to one"

# Noul has p(true) and p(false) = 1 - p(true).
# Score levels are integer keys 0...(K-1), ordinal expected index = sum(i * p_i).
```

Scores to calculate:

- `choice`: accuracy, balanced accuracy, macro-F1, per-class precision/recall/F1, multiclass Brier `mean(sum_k (p_k - y_k)^2)`, NLL with an **explicit shared epsilon** solely for numerical stability, top-label ECE (e.g. 15 equal-mass bins plus sensitivity with 15 equal-width bins).
- `score`: ordinal expected-value MAE and RMSE, exact argmax accuracy, quadratic weighted kappa, ordinal Brier / ranked probability score as supplementary. Keep ordinal-level definitions fixed by use case.
- `noul`: AUROC, AUPRC, Brier, log loss, ECE, F1 and recall at threshold 0.5 plus calibrated threshold. Report class prevalence, as AUPRC depends on prevalence.
- `robustness`: choice prediction flip %, mean total variation distance between probability vectors `TVD=0.5*sum(abs(p_i-q_i))`, score expected-value shift, and noul absolute probability shift.
- `coverage`: valid answered questions / attempted questions; `effective_accuracy = correct / attempted` may be reported alongside `answered_only_accuracy`, but never replace coverage reporting.
- `language`: evaluate exact same test scenario IDs; perform paired bootstrap at the **scenario** level.

**Calibration discipline:** Save `raw` and `posthoc` tables. Temperature scaling is allowed for logits *only if the raw logits are available*; for APIs returning probabilities only, use a documented post-hoc transformation on the calibration set (e.g. temperature applied to log probabilities with epsilon and renormalization). For Noul, use logit scaling with bounded epsilon, or a calibrated method selected solely on calibration. Never claim a provider's confidence score is the model's calibrated correctness probability. Do not compare one model calibrated and the other uncalibrated without showing both raw and calibrated conditions.

### Minimum results schema (`results/predictions.parquet`)

```text
run_id,scenario_id,pair_id,experiment_id,split,language,state_language,
question_language,model_track,provider,resolved_model,checkpoint_revision,
question_id,primitive,gold_label,gold_level,gold_binary,predicted_label,
predicted_level,score_value,noul_probability,distribution_json,
confidence_vendor,max_probability,normalized_entropy,calibration_version,
input_token_count,output_token_count,request_bytes,db_retrieval_ms,
model_wall_ms,full_pipeline_ms,attempts,error_type,valid_answer,
context_bucket,was_truncated,unsupported_context,device,seed
```

Use nullable values for fields inapplicable to a primitive. Log **raw JSON responses** in `results/raw/` only for synthetic tests, with secrets removed, restrictive permissions, and a file hash. Use a separate request-hash register to pair exact inputs, and include model-specific envelopes in request metadata. All analysis should be regenerable from these files.

### Minimum final result tables

1. Model x UseCase x Language x Primitive -> score, 95% CI, N, support, failure rate.
2. Per-case paired Laya-Jev delta (`EN` and `TH` independently), 95% CI.
3. Per-model language gap (TH minus EN), with paired CI.
4. Calibration raw vs post-hoc (Brier/NLL/ECE/risk coverage).
5. Latency by topology and hardware (warm/cold separated).
6. Cost/1k (remote tokens vs local hardware) + projected monthly volumes.
7. High-risk failure review (PII false negative, SQL unsafe acceptance, schema break miss, critical lineage miss, severe incident miss).
8. Cases where `rules_only` wins, ties, or loses; don't force an AI value proposition.

---

## 13. Execution phases - implement and actually run

### Phase 0 - Discovery and environment audit

- [ ] Create working repo. Record `git rev-parse HEAD`, date, OS, RAM, CPU, GPU, Python version, available disk, Docker/Compose availability.
- [ ] Verify model repositories and documentation, Laya Python API, official TypeSafe API/available Jev model IDs, current pricing and licenses; record in `docs/MODEL_CARD_SNAPSHOTS.md` with URLs, dates and exact revisions.
- [ ] Record whether `TYPESAFE_API_KEY` is set (boolean only; NEVER display its value) and whether remote permission is enabled.
- [ ] Pick and pin working DB image tags/digests and Python dependency versions; produce `uv.lock` or equivalent.
- [ ] Write `.env.example` with `ALLOW_REMOTE_API=0`, `TYPESAFE_API_KEY=`, `JEV_MODEL=jev-1.14.0`, `JEV_MAX_USD=5`, `JEV_MAX_REQUESTS=12000`, `LAYA_DEVICE=cpu`, `SEED=20261019`, DB ports bound to localhost.

### Phase 1 - Providers and common contracts

- [ ] Implement common request DTO and two independent adapters; central normalize + error table.
- [ ] Build a `MockDecisionProvider` strictly for CI and unit tests with a conspicuous `provider=mock` marker. **Reject mock rows from final benchmark aggregation**.
- [ ] For Laya run actual local inference on 2-3 synthetic English and Thai examples, verify routed checkpoint and output schema.
- [ ] For Jev, execute an actual smoke request **only** with permission+key; verify exact `resolved_model` and probability fields. Otherwise mark this smoke test BLOCKED.
- [ ] Enforce remote cap and request idempotency policies (deduplicate resumed calls by content hash, preserving retries).

### Phase 2 - DBs, schemas, evidence and gold oracles

- [ ] Implement `infra/compose.yaml` with **profiles** (`e01`, `e02`, `e03`, `e04`, `e05`); run one DB at a time, not all five concurrently. DuckDB is in-process (`e06` profile not necessary).
- [ ] Implement seed/reset scripts. Verify DB health and query outputs. Each E01-E06 gets at least 3 hand-verifiable edge-case fixtures with deterministic oracle assertions.
- [ ] Generate 300 scenarios per use case. Ensure grouping and frozen split. Keep gold fields fully separate from provider-visible state.
- [ ] Render paired EN/TH descriptions; run equivalence tests on numeric facts, SQL, IDs, timestamps, fault type, and reference results. Validate native Thai phrasing manually where possible; record unreviewed translations honestly.
- [ ] Produce `docs/DATASET_CARD.md`: creation, intended use, limitations, class distributions, language balance, split hashes, why ground truth is trustworthy.

### Phase 3 - Offline baseline and pilot

- [ ] Run deterministic `rules_only` baselines, oracle tests, SQL AST security tests, mock provider smoke tests, metrics tests.
- [ ] Run a **real local Laya** pilot on 20 paired cases per use case; save exact raw predictions and failure cases.
- [ ] If Jev access is present and authorized, run the same 20 paired cases through pinned Jev and compare against the same gold. If not, create `results/BLOCKERS.md` with copyable instructions; do not manufacture comparative numbers.
- [ ] Fix integration/data bugs using `dev` only; freeze the prompt/template hashes before the final test.

### Phase 4 - Locked evaluation

- [ ] Evaluate all test cases `P1/P2`, with dev/calibration/test segregation verified and **unmodified prompts**.
- [ ] Run P3-P9 and P8 performance harness. Deduplicate and resume safely; record failed cases and all model revisions.
- [ ] Calibrate on calibration split, then evaluate raw/calibrated on test; save thresholds and calibration parameters.
- [ ] Execute paired bootstrap + uncertainty analyses and produce tables and figures without discarding failed cases.

### Phase 5 - Optional secondary systems study

- [ ] If local LLM is available, freeze its version and generate candidate explanations/SQL using the same retrieval contexts.
- [ ] Integrate Laya/Jev gate with exactly the same threshold optimization objective, fixed review cost, and allowed actions.
- [ ] Measure whether adding a decision layer reduces LLM use at a bounded high-severity miss rate. Provide a full Pareto curve (risk, cost, latency), not just one chosen threshold.
- [ ] Optionally train a **separate** domain Laya variant with additional training scenarios, keeping original test untouched. No Jev fine-tuning assumption.

### Phase 6 - Audit and deliverables

- [ ] Produce `docs/FINAL_REPORT.md`, `docs/STUDY_LIMITATIONS.md`, `results/summary.csv`, `results/summary.parquet`, `results/predictions.parquet`, `results/paired_deltas.csv`, `results/calibration.csv`, `results/hardware.json`, `results/run_manifest.json` and charts.
- [ ] Tests must pass or blockers must be documented precisely; print actual coverage and run counts.
- [ ] Include 10-20 representative paired EN/TH failure cases **with synthetic context only**, with explanation of which clue each model missed, not unsupported guesses about internal behavior.
- [ ] Include a final factual verdict per use case: `Laya wins`, `Jev wins`, `Statistical tie`, `Inconclusive` or `Not executed`, each qualified by model version, language, sample size, and confidence interval.

---

## 14. Suggested CLI and reproducibility contract

Implement these commands via a Makefile or equivalent. **Do not publish them as working until the code and tests actually support them.**

```bash
# Environment / database setup
make doctor
make db-up CASE=e01
make db-seed CASE=e01
make db-oracle-test CASE=e01
make db-down CASE=e01

# Data preparation and baseline
make generate SEED=20261019
make validate-pairs
make test
make rules-baseline

# Local offline pilot (real local Laya; no Jev credits)
make pilot PROVIDER=laya CASE=e01 LANG=both N=20

# Remote paid pilot requires explicit opt-in and key
ALLOW_REMOTE_API=1 make pilot PROVIDER=jev CASE=e01 LANG=both N=20

# Final strict test runs
make evaluate PROVIDER=laya SPLIT=test PROTOCOL=primary
ALLOW_REMOTE_API=1 make evaluate PROVIDER=jev SPLIT=test PROTOCOL=primary

# Compute paired analysis and report
make analyze
make report
```

The runner must also expose reproducible Python module CLI commands for systems without `make`. For WSL2, document `docker compose`/port binding prerequisites and fallback to DuckDB + local provider tests if Docker is unavailable. Do not make all database services mandatory just to test the scoring library.

### Suggested `study.yaml` core config

```yaml
study_id: de-laya-jev-th-en-2026
seed: 20261019
cases: [E01, E02, E03, E04, E05, E06]
languages: [en, th]
question_types: [choice, score, noul]
split_scenarios_per_case:
  dev: 60
  calibration: 40
  test: 200
primary_models: [laya_routed, jev_114]
extra_models: [laya_en_fixed, laya_multi_fixed]
primary_protocols: [P1_primary_en, P2_primary_th]
remote:
  allow: false
  base_url: https://api.typesafe.ai
  model: jev-1.14.0
  max_requests: 12000
  max_usd: 5
  max_retries: 3
bilingual:
  pair_by: scenario_id
  preserve_machine_tokens: true
  option_ids_ascii_and_invariant: true
  validate_semantic_pairs: true
latency:
  warmup_local_calls: 30
  repeats: 3
  concurrency: [1, 4, 8]
  question_counts: [1, 3, 6]
statistics:
  bootstrap_iterations: 2000
  confidence_interval: 0.95
```

The configuration above is a **target**, not proof of a completed run. Scale down only with an explicit pilot tag and sample-size notation, never silently.

---

## 15. Bilingual examples and localization acceptance checks

The six following pairs are deliberately simple **author-specified examples**, NOT labeled production data. Build many varied and independently validated scenarios; do not benchmark on these six strings alone.

| Case | English state/question example | Thai equivalent |
|---|---|---|
| E01 | Customer pipeline failed after a database column rename. | `งานประมวลผลลูกค้าล้มเหลวหลังจากเปลี่ยนชื่อคอลัมน์ในฐานข้อมูล` |
| E02 | The row count dropped abnormally after the latest batch load. | `จำนวนแถวลดลงผิดปกติหลังจากการโหลดข้อมูลรอบล่าสุด` |
| E03 | Add a new NOT NULL column with no default value. | `เพิ่มคอลัมน์ใหม่ที่ไม่อนุญาตให้เป็นค่าว่าง โดยไม่มีค่าเริ่มต้น` |
| E04 | Will changing this source table impact the daily sales dashboard? | `หากเปลี่ยนตารางต้นทางนี้ รายงานยอดขายรายวันจะได้รับผลกระทบหรือไม่` |
| E05 | Does this field contain a Thai national identification number? | `คอลัมน์นี้เก็บเลขประจำตัวประชาชนไทยหรือไม่` |
| E06 | Show monthly total sales in 2025, broken down by province. | `แสดงยอดขายรวมรายเดือนของปี 2025 โดยแยกตามจังหวัด` |

The canonical Thai instructions for illustrative `choice` and `noul` questions are `ปัญหานี้ควรจัดอยู่ในประเภทใด` and `เหตุการณ์นี้จำเป็นต้องให้มนุษย์ตรวจสอบก่อนดำเนินการหรือไม่`. These examples are UTF-8 Thai strings; the agent must author and validate full Thai rubrics for every task.

### Translation quality checklist

- [ ] Same injected fault, same DB queries and same objective gold in both languages.
- [ ] Preserve SQL/JSON field names, error codes and case-sensitive identifiers.
- [ ] Translate natural-language criteria and score rubrics naturally, not word-for-word blindly.
- [ ] Thai date/time phrasing does not change month/day interpretation or timezone.
- [ ] Thai negation, hedging and absence of evidence are represented correctly.
- [ ] No accidental gold answer token such as `injected_fault=...` in the input.
- [ ] Test both Thai-only prose and actual code-switched Thai-plus-English technical logs.
- [ ] `th_en` and `en_th` are analysis variants, NOT accidental missing translations.

---

## 16. Stop conditions, red flags and scientific limitations

**STOP automatic actions / never claim a successful benchmark** if any applies:

1. Missing vendor access/remote authorization: Jev rows must be `NOT_RUN / BLOCKED`, not fabricated.
2. Provider model version mismatch (`jev-latest` used accidentally, or pinned model not served).
3. Same provider-visible payload cannot be reproduced for both vendors (aside from model field/auth), with no documented normalization difference.
4. Thai scenario loses or changes underlying facts relative to English without being flagged.
5. Train/dev/calibration/test leakage, including paraphrase templates crossing splits.
6. Exposed real PII, real company records, credentials or unintended remote data transfer.
7. SQL mutation or external-network access not blocked in the candidate-validation runner.
8. Confidence comparisons using raw `confidence` fields as though they were defined identically.
9. API timeouts silently dropped from denominator or counted as incorrect gold instead of provider failures.
10. Benchmark result invented from a README, third-party blog, simulation, random mock, or another dataset.

**Non-fatal caveats to disclose:** no GPU; Jev network location differs from local model; cost is estimated from measured tokens vs recorded hardware rental; some labels have insufficient evidence; Thai pairs not human-verified; limited class support; changes in vendor release; different context support; optional local LLM not installed; deployment cost varies by region and utilization.

### Success checklist (definition of done)

- [ ] 6 runnable, isolated DB-backed experiments with seeded synthetic fixtures and gold-verification tests.
- [ ] EN/TH paired data from the **same** scenario IDs, translation quality and leakage tests.
- [ ] Same `choice/score/noul` schemas and versioned prompts sent to both models.
- [ ] Both **real** model adapters execute when resources permit; blocked access explicitly documented.
- [ ] Accuracy + per-language paired deltas + calibration + latency topology + cost + risk coverage.
- [ ] End-to-end report with non-AI rules baselines and use-case-specific verdicts.
- [ ] A fresh reader can re-run the study with only documented dependencies, keys (if authorized) and commands.

---

## 17. Verified references and pre-run verification links

**Decision model interfaces and current version rules**

- TypeSafe official docs, Models (check pinned IDs, language support, limits and pricing): https://docs.typesafe.ai/models
- TypeSafe official HTTP API (auth, fields, question types, outputs): https://docs.typesafe.ai/api
- TypeSafe Python SDK: https://docs.typesafe.ai/sdk/python
- TypeSafe quick start: https://docs.typesafe.ai/introduction/quickstart
- TypeSafe known Jev 1.13 limitations: https://docs.typesafe.ai/model-jaggedness/jev-1.13
- Laya original source: https://github.com/NandhaKishorM/laya
- Laya source model card and checkpoints: https://huggingface.co/convaiinnovations/laya
- Third-party Laya-vs-Jev comparison (use as background only, **not** as benchmark result): https://laya.studio/compare/laya-vs-jev

**Open-source database documentation / licensing**

- OpenSearch docs, single-node test setup: https://docs.opensearch.org/latest/getting-started/quickstart/
- OpenSearch license statement: https://opensearch.org/platform/
- ClickHouse source & license: https://github.com/ClickHouse/ClickHouse
- PostgreSQL license: https://www.postgresql.org/about/licence/
- PostgreSQL information schema: https://www.postgresql.org/docs/current/information-schema.html
- Neo4j Community source & license (verify exact community image/version): https://github.com/neo4j/neo4j
- Apache AGE (optional, Apache-2.0 graph alternative): https://age.apache.org/
- DuckDB source & MIT license: https://github.com/duckdb/duckdb

**Verification rule:** Before execution, the executor must confirm all API details and versions against the official upstream. If a sample path or CLI changed, fix the implementation and document it rather than pretending the old code ran successfully.

---

## 18. Immediate prompt for the coding agent

Copy this prompt after placing the Markdown file in a new repository:

> Read `PLAN.md` completely and treat it as the execution contract. Implement and RUN the bilingual Laya-vs-Jev Data Engineering benchmark for E01-E06. Start with Phase 0, then create provider adapters, DB fixture scripts, paired EN/TH ground-truth datasets, metrics, and reporting. Use actual Laya inference locally; use official pinned Jev 1.14.0 only if `ALLOW_REMOTE_API=1` and `TYPESAFE_API_KEY` exists. Never fabricate unavailable Jev results. Run isolated DB profiles, tests and a real pilot before full evaluation. Preserve identical provider-visible inputs, report confidence intervals, raw calibration, end-to-end latency topology and cost. Use synthetic data only, never real PII or company logs, and never execute unsafe candidate SQL. Continue through all unblocked phases autonomously. At the end, show completed tasks, real numerical findings if measured, blocked tasks with exact fixes, reproducible commands, and a link/path to `docs/FINAL_REPORT.md`.

**End of execution contract.**

