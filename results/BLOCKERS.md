# Blockers and incidents

Status as of 2026-10-19 15:30 (Asia/Bangkok). Nothing below was worked around by inventing results.

## Resolved — Jev requests used the wrong API key (harness bug, not the user's key)

- **Symptom:** from 13:43 to 15:25 every `jev-1.14.0` request returned HTTP 403
  `authentication_error` ("Cannot authenticate with the server"), and the Jev track was marked
  BLOCKED. Billed cost: USD 0.
- **Cause:** `~/.bashrc` exports a different `TYPESAFE_API_KEY`. `debench.config.load_settings()`
  let the process environment override `.env`, so the harness never sent the key the user put in
  `.env`. The earlier "invalid key" diagnosis was wrong.
- **Fix:** a secret set in `.env` now always wins over a same-named shell variable; the overridden
  name (never its value) is recorded as `_IGNORED_SHELL_SECRETS` in run manifests.
- **Verification:** with the `.env` key, the contract smoke (6 requests) returned
  `model: jev-1.14.0` with valid choice/score/noul answers and measured `usage.input_tokens`.
- All 403 attempts remain in `results/remote_ledger.jsonl` (hashes only). Jev runs were then
  executed on the same frozen prompts and scenarios as Laya (see `results/jev_eval.log`).
- Diagnostic detail (for the record): no key → 403 "Must supply an API key"; a fake key → 401;
  the shell key → 403 "Cannot authenticate"; the `.env` key → 200.
- **Action for the user:** remove or update the stale `TYPESAFE_API_KEY` export in `~/.bashrc`.

## Request cap shapes the Jev protocol coverage

`JEV_MAX_REQUESTS=12000` (user setting, not raised). Running every protocol for Jev would need
~21,000 requests, so Jev runs in priority order: pilot, P1/P2 test, calibration, P4 (new
crossover variants only; identical requests are served from the resume cache), P7, a small
latency sweep, then P5 with **2** option permutations (Laya ran 5). P5 comparisons use only the
shared permutation seeds. Raise the cap in `.env` and run
`python -m debench.runner --case all --tracks jev_114 --protocol P5_order --split test --perm-seeds 3,4,5 --concurrency 4 --run-name test_p5_jev`
to complete it.

## Docker is not available inside WSL

- `docker` is not on the WSL PATH; Docker Desktop is installed on Windows but its engine was not
  running and WSL integration is off.
- Workaround (recorded in every build report and run manifest as `runtime`): OpenSearch 3.9.0 and
  Neo4j Community 5.26.32 run from official tarballs (`infra/native/`); ClickHouse runs embedded via
  chdb-core 26.9.2.1; PostgreSQL 18.4 runs embedded via pixeltable-pgserver; DuckDB 1.5.6 is
  in-process. These are the same engines, not substitutes.
- `infra/compose.yaml` (pinned tags + digests) is written but **not executed**.
- **Fix:** Docker Desktop → Settings → Resources → WSL Integration → enable this distro, then
  `make db-up CASE=e01 DOCKER=1` (and set `DEBENCH_PG_URI_E03` / `DEBENCH_PG_URI_E05`).
  Expected version differences: PostgreSQL image 18.6 vs embedded 18.4; ClickHouse server
  26.9.21.9 vs chdb-core 26.9.2.1.

## No native Thai-speaker review

Thai rubrics and states were machine-drafted and model-reviewed only (one mistranslation,
"SQL ผู้สมัคร" for "candidate SQL", was found and fixed before the prompts were frozen).
A native Thai reviewer should sign off `configs/prompts/*.yaml` and a sample of
`data/generated/*/common_fit.jsonl` before results are published.

## Optional tracks not run

`jev_113` (P10), `laya_finetuned` (P11) and the local-LLM cascade (P12) were not run.
