# Model card snapshots

Verified 2026-10-19. Re-check every link before a final run; vendors change releases and prices without notice.

Sources:

- https://docs.typesafe.ai/models
- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/sdk/python
- https://docs.typesafe.ai/model-jaggedness/jev-1.13
- https://github.com/NandhaKishorM/laya
- https://huggingface.co/convaiinnovations/laya
- https://pypi.org/project/laya/

## TypeSafe Jev (remote)

| Item | Value |
|---|---|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Auth | `Authorization: Bearer <key>`, `Content-Type: application/json` |
| Body | `{"state", "model", "questions"}`; `state` is a string, object or array |
| Question | `{type, instructions, criteria}`; choice: map option to description (at most 255 options); score: array of 2..10 levels; noul: optional `{true,false}` |
| Encoding limits | UTF-8 JSON, NaN/Infinity rejected, at most 32 MiB |
| Response | `{"model": <resolved id>, "answers": {...}, "usage": {"input_tokens", "output_tokens"}}` |
| Pinned models | `jev-1.14.0` (available since 2026-10-14), `jev-1.13.0` |
| Aliases (forbidden here) | `jev-latest` resolves to 1.13.0, `jev-preview` resolves to 1.14.0 |
| Context | 64k tokens per request, 32k per question; header `TypeSafe-Danger-Zone-Long-Context: true` raises both to 262k; exceeding gives 400 `max_tokens_exceeded` |
| Price | USD 0.042 per million input tokens; output tokens free |
| Errors | 400, 401, 413, 415, 422, 429, 529; retry 429/529 with exponential backoff, honour `retry-after` and `retry-after-ms` |
| Rate limits | per organisation; numbers only in the console |
| SDK | typesafe-sdk 0.7.5 (default model `jev-latest`, 2 retries, 30 s budget); not used, httpx is |
| Known limits of 1.13 | first-option bias, weak arithmetic, literal reading |

Per-type answer shape:

- choice: `{type, choice, probabilities{label: p}, confidence}`
- score: `{type, score (probability-weighted), legend{idx: desc}, probabilities{idx: p}, confidence}`
- noul: `{type, noul}`, no confidence

## Laya (local)

| Item | Value |
|---|---|
| Package | laya 0.4.11 (installed and verified against the installed source) |
| Bundle repo | `convaiinnovations/laya` |
| Pinned revision | `7b928d828b7b0e022f929d9bd2e44165aa270148` (both checkpoints) |
| `english` | repo root; ModernBERT-large; max_len 512, head_max_len 192; `model.safetensors` 842,609,210 bytes |
| `multilingual` | `multilingual/` subfolder; mmBERT-base; max_len 1024, head_max_len 256; 643,835,514 bytes |
| `typed-decisions` | never auto-routed (`auto_task_detection=False`) and not loaded by this study |
| Routing precedence | explicit `model` > `task` > detected workflow (opt-in) > `lang` > `lang_guess` > built-in script/language detection > `default` (multilingual since 0.4.0); Thai script routes to multilingual |
| Result | Jev-shaped `answers`, `usage` {input_tokens, output_tokens 0, state_tokens, state_tokens_dropped, truncated, truncated_questions}, `routing` {model, repo, reason, detection, workflow} |
| Rounding | probabilities rounded to 4 decimal places |
| Threading | no async API; the Router is thread-safe (RLock); this repo calls it through `asyncio.to_thread` |

Facts confirmed from the installed source (`laya/router.py`, `laya/agent.py`, `laya/revisions.py`):

- `Router(models, device, token, revision, revisions, max_loaded=2, default="multilingual", auto_task_detection=False, standalone_repos=False, preload=False, lang_guess=None, ...)`.
- `revisions={"english": sha, "multilingual": sha}` pins per checkpoint; the Agent records the commit its snapshot directory points at in `agent.revision`, and the provider compares it with the pin (mismatch gives `version_mismatch`).
- `Router.preload(names)` raises `max_loaded` to fit the requested names, so incremental preloading never evicts.
- `Agent.cfg["max_len"]` and `["head_max_len"]` are the budgets actually used; the provider reports them per call.
- Importing laya sets `USE_TF=0` via `setdefault`.

Deviation from the plan: it names `Router(preload=True)`. This repo builds with `preload=False` and calls `router.preload(["english", "multilingual"])`, because `preload=True` also loads `typed-decisions`, which is never routed here and costs about 1.2 GB of RAM on a small host.

## Environment of the first verification

Installed: laya 0.4.11, torch 2.14.1+cu130, httpx 0.28.1, Python 3.12. GPU: RTX 3060 Laptop, 6 GB. Measured smoke output: `results/smoke/laya_smoke.json`.

## Differences that matter for fairness

1. Confidence. Jev choice: `(p_max - 1/n) / (1 - 1/n)`; Jev score: `1 - S / MAD_unif`; noul has none. Laya `confidence` has different semantics and Laya also emits `answer_confidence`. Raw vendor confidence is stored as `confidence_vendor` only; comparisons use `max_probability` and `normalized_entropy` computed from the distributions.
2. Precision. Laya rounds probabilities to 4 dp; Jev does not state this. The normalizer tolerates rounding drift in sums and renormalizes.
3. Context. Laya truncates the state (512 or 1024 tokens, head budget 192 or 256) and reports `truncated`, `state_tokens_dropped`; Jev accepts 64k tokens per request (32k per question), 262k with the long-context header. Differences in what each model actually saw are reported, not hidden.
4. Routing. `laya_routed` lets Laya choose the checkpoint (the routing decision is logged per call); the fixed tracks force one checkpoint. Latin-script English routes to the English checkpoint; Thai script routes to the multilingual checkpoint.
5. Pinned identity. Jev aliases are rejected and the response `model` must equal the pinned id; Laya identity is `laya:<checkpoint>@<first 12 chars of the commit>`.
6. Latency topology. Laya `wall_ms` is in-process inference only and excludes model load (reported as `load_ms`). Jev `wall_ms` is end to end from the same client, including retries and backoff (`retry_wait_ms`, `attempts` recorded). The two are not directly comparable (see PLAN section 11.1).
7. Cost. Jev: billed input tokens times USD 0.042 per million. Laya: local hardware only.
8. Retries and caps. Remote attempts are bounded by `JEV_MAX_RETRIES`, `JEV_MAX_REQUESTS` and `JEV_MAX_USD`. Cached responses from a resumed run are flagged `cached=True` and are not latency samples.

## Observed in this study (2026-10-19, not from vendor documentation)

- **Jev response precision.** `probabilities` arrive at 2 decimals (with occasional float noise such
  as `0.8200000000000001`); sums of 0.99 or 1.01 occur. The normalizer's tolerance follows the
  reported precision (see `src/debench/normalize.py`).
- **Jev resolved model.** Every successful response reported `"model": "jev-1.14.0"` for requests
  pinned to `jev-1.14.0`; no alias was used.
- **Jev token usage.** `usage.input_tokens` was present on every success; Thai requests used about
  twice the input tokens of the English request with the same facts (e.g. 515 vs 959–1007).
- **Jev auth errors.** No `Authorization` header → 403 "Must supply an API key"; a malformed key → 401;
  a well-formed but rejected key → 403 "Cannot authenticate with the server". No 429 or 529 was seen
  in 11,374 successful requests at concurrency ≤ 8.
- **Laya routing.** With default settings the router sent every Thai state to the multilingual
  checkpoint and 1,200 of 1,201 English test states to the English checkpoint. Very short English
  sentences can be classed as "undecided" and fall back to the multilingual default (seen in the smoke).
- **Laya load and memory.** Router load + `preload(["english","multilingual"])`: 23–41 s from the
  local HF cache (93 s on first download); peak GPU memory about 3.6 GB; host RSS about 1.5 GB.
