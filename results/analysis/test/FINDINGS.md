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
