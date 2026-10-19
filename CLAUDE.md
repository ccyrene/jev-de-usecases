# Working rules for this repository

`PLAN.md` is the execution contract. `README.md` explains the use cases and results.

## Hard rules

- Never read, print or log secret values from `.env` (`TYPESAFE_API_KEY`, `*_PASSWORD`). Report
  them as set/empty only. Clients get them via `debench.config.Settings.secret()`.
- Paid remote calls require `ALLOW_REMOTE_API=1` and a key in `.env`; never raise
  `JEV_MAX_USD` / `JEV_MAX_REQUESTS` without asking. Pinned `jev-X.Y.Z` only, never
  `jev-latest` / `jev-preview`.
- Never invent model outputs, latency, cost or accuracy. Anything that cannot run is recorded in
  `results/BLOCKERS.md` with the exact error and the command that fixes it.
- Synthetic data only. Never execute unsafe candidate SQL (`src/debench/sql_guard.py` decides).
- Gold comes from injected faults verified by the database, never from a model.
- Both providers get byte-identical `state` + `questions`; build requests only via
  `debench.payload.build_request` (it enforces the leakage allowlist).
- Never edit `configs/prompts/*.yaml` or `configs/prompts/FROZEN.json`; test-split runs refuse to
  start if prompts or datasets differ from the frozen hashes.
- Mock provider rows are for CI only and are rejected by `debench.analyze`.
- Run one database engine at a time (WSL RAM is ~6 GB); stop native engines when done.

## Playbook: "reproduce the results"

Goal: regenerate `docs/FINAL_REPORT.md`, `docs/figures/` and `results/summary.csv` from scratch and
report how they compare with the committed versions.

1. **Environment.** Run `make doctor` and read `results/hardware.json`. Required: Python 3.12, `uv`,
   Java 21 (for the OpenSearch/Neo4j tarballs), ~6 GB RAM, ~10 GB disk. A CUDA GPU is optional
   (`DEVICE=cpu` works but Laya runs take many hours). If `uv` or the venv is missing, run
   `make setup`.
2. **Configuration.** If `.env` does not exist, copy `.env.example` to `.env` and stop to ask the
   user to fill the database passwords (and, optionally, `TYPESAFE_API_KEY` + `ALLOW_REMOTE_API=1`
   for the Jev track). Do not invent values. Check keys with a regex over key names only.
3. **Tests.** `make test` must pass before any run.
4. **Run the study** with `make reproduce` (or step by step: `make reproduce STEP=data`, then
   `pilot`, `laya`, `jev`, `latency`, `report`). Each step is resumable; if one fails, read the error,
   fix the cause, and rerun the same step. Expect roughly: data 15 min, pilot 5 min, Laya 1.5 h on
   an RTX 3060, Jev 25 min and ~USD 0.50, latency 1 h, report 15 min.
   - `data` must print "frozen prompts and datasets match"; if it does not, stop and report which
     case/context differs. Do not re-freeze.
   - If remote calls are disabled, the Jev steps are skipped and the head-to-head stays
     "Not executed"; say so instead of filling gaps.
   - Run `latency` with nothing else heavy running on the host.
5. **Compare.** `git diff --stat docs/FINAL_REPORT.md results/summary.csv` and compare the primary
   macro-F1 per case/language/model with the committed table in `README.md`. Local GPU numerics and
   remote model updates can move values slightly; flag any primary macro-F1 difference > 0.02 and any
   verdict change.
6. **Report back** with: steps completed, skipped steps and why, the comparison, total Jev spend
   from `results/remote_ledger.jsonl`, and links to `docs/FINAL_REPORT.md` and `docs/figures/`.
   Do not commit unless the user asks.
