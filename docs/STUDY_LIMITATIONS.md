# Study limitations — de-laya-jev-th-en-2026

Read these before quoting any number from `docs/FINAL_REPORT.md`.

## Execution incidents (resolved, disclosed)

1. **Jev credential incident.** Until 15:25 the harness sent a stale `TYPESAFE_API_KEY` exported in
   `~/.bashrc` instead of the `.env` key, so Jev returned HTTP 403 and was wrongly reported as
   blocked. Config precedence was fixed (`.env` secrets now win; shadowed names are logged) and Jev
   then ran on the same frozen prompts and scenarios. The 403 attempts remain in the ledger (USD 0).
   Details: `results/BLOCKERS.md`.
2. **Probability-sum tolerance follows reported precision.** Jev reports probabilities at 2 decimals
   (sums such as 0.99 occur, sometimes with float noise like `0.8200000000000001`); Laya at 4. The
   normalizer accepts a sum deviation up to K × ½ unit of the last reported decimal (never coarser
   than 2 dp; minimum 1e-3) and renormalizes. Rows rejected by the first, stricter tolerance
   (3 pilot + 57 test-split Jev answers) were recomputed from the stored raw answers
   (`python -m debench.renormalize`, tagged `renormalized`). Probabilities are never imputed.
3. **First Jev latency sweep invalid.** Its in-memory cache served repeats, so concurrency 4/8
   samples were cache hits. The sweep was rerun with caching disabled (360 real requests, 0 cached,
   0 errors); only the rerun is reported (`results/latency/jev_114_remote.json`).
4. **Final analysis overlapped the Laya CPU latency run** (analysis at `nice 19`, one BLAS thread).
   CPU latency numbers may be slightly pessimistic; GPU latency was measured before it.

## Coverage limits

5. **Jev P5 used 2 option permutations** (Laya 5) because of the user's 12,000-request cap; P5
   comparisons use only the shared permutations 1–2. Total Jev spend: USD 0.50 on 11,384 attempts.
6. **Optional tracks not run:** `jev_113` (P10), fine-tuned Laya (P11), local-LLM cascade (P12 / RQ6),
   paraphrase perturbations within P5 (only option-order permutations), cardinality stress.
7. **Docker unavailable in WSL.** The same upstream engines ran natively (OpenSearch 3.9.0, Neo4j
   Community 5.26.32 tarballs) or embedded (PostgreSQL 18.4 via pixeltable-pgserver, ClickHouse
   26.9.2.1 via chdb-core, DuckDB 1.5.6). The pinned `infra/compose.yaml` (PostgreSQL 18.6,
   ClickHouse server 26.9.21.9) was not executed.

## Validity caveats

8. **Gold is policy over verified facts.** Facts are measured by the engine and cross-checked by an
   independent implementation; labels are deterministic policies over those facts (documented per
   module). Rules baselines read the same facts as the gold policy, so they are near-oracles
   (E04 exact by construction). Rules-vs-model tables show whether a model can read the *rendered*
   evidence, not whether rules are generally better.
9. **Thai has not had native-speaker review.** Rubrics and states were machine-drafted and
   model-reviewed; one mistranslation was fixed before freezing. Automated checks enforce identical
   machine tokens, digits and field keys, not fluency.
10. **No prompt tuning.** Rubrics were written once (e0X-v1) and frozen without model-specific tuning.
    Both models' absolute scores might change with rubric work.
11. **Sample sizes.** 200 test scenarios per case per language (E06: 201); thin cells include E02
    impact level 4 (n=7), E05 tier 0 (n=12), E06 quality 3 (n=4), E04 critical-downstream positives
    (n=34). Zero observed misses are reported with Clopper–Pearson upper bounds, not as 0% risk.
12. **Calibration split is small** (40 scenarios per case per language). Temperature scaling made
    Jev E05 ECE worse (0.053 → 0.087); raw and calibrated values are both reported.
13. **Laya configuration.** `Router(preload=False)` + `preload(["english", "multilingual"])` instead of
    `Router(preload=True)` (typed-decisions is never auto-routed; RAM saved). Default
    `max_len`/`head_max_len`. One checkpoint warns its temperatures fall outside [0.5, 5]. Vendor
    `confidence` fields are stored, never compared.
14. **Context fit.** Laya's English checkpoint truncates 41% of Thai `common_fit` requests and 49% of
    English `natural_long` requests; these are reported as truncation, and quality is still scored
    on the attempted denominator.
15. **Latency topology.** Laya was measured in-process on an RTX 3060 Laptop GPU (and on CPU) under
    WSL2; Jev over HTTPS from Bangkok (unauthenticated round trip ~205 ms). This is a deployment
    comparison, not intrinsic model speed. Laya cost is shown only under explicitly assumed host
    prices.
16. **E03 consumer bank includes write probes.** 11 of 44 consumers are INSERT probes inside a
    savepoint that is always rolled back (the only way to observe NOT NULL / default drift).
17. **E06 labels.** Execution errors (e.g. absent column) are `semantically_wrong`; only
    guard-blocked or unparseable candidates are `unsafe_or_unexecutable`.
18. **E05 masking.** Thai-script sample values are fully masked, so script cues reach models only via
    metadata; masks keep 1–2 characters at each end of longer values.
19. **Local DB hardening.** Neo4j's minimum password length was lowered for the localhost-only
    synthetic instance; the initial password is briefly visible in the process list on first install.
