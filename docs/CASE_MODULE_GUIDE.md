# Case module guide (E01–E06)

Each use case is one module `src/debench/dataset/eXX.py` plus one evidence backend
`src/debench/evidence/<backend>.py`, one question file `configs/prompts/eXX.yaml` and its tests.
The shared orchestrator `src/debench/dataset/build.py` turns them into paired EN/TH records.
`PLAN.md` (sections 1–3, 10, 12, 15 and the case's own section) is the specification.

## Module interface

```python
CASE = "E0X"
BACKEND = "opensearch" | "clickhouse" | "postgres" | "neo4j" | "duckdb"

def generate_specs(seed: int) -> list[ScenarioSpec]          # exactly 300, deterministic
def db_seed(specs, seed) -> Any                              # payload for backend.load (incl. distractors)
def make_backend(settings) -> EvidenceBackend
def expected_evidence(spec) -> dict                          # pure-Python reference of DB facts
def oracle_check(spec, facts) -> list[str]                   # DB facts vs reference + gold; [] = verified
def render_state(spec, facts, lang, context_level) -> dict   # provider-visible state
def protected_tokens(spec, facts) -> list[str]               # must appear byte-identical in EN and TH
def rules_baseline(spec, facts) -> dict[str, Any]            # qid -> label | level | bool | None
```

Build: `~/.venvs/de-jev/bin/python -m debench.dataset.build E0X --mode db` (or `--mode expected`
offline; those records are unverified and never final). Output goes to `data/generated/e0x/`.

## Data rules

- Deterministic: use `random.Random(seed + <case offset>)`, never the global RNG or wall-clock time.
- `scenario_id` from `paired_generator.scenario_id(CASE, i)`, i = 1..300.
- `group_id` = underlying template/incident/schema/graph family. Mutated copies share a group;
  groups are assigned to splits whole. Use many groups (>= 100) and <= 3 specs per group.
- Gold comes only from the injected fault / relational constraint / graph oracle / SQL oracle /
  column provenance. Never from a model.
- At least 20% `evidence_sufficiency` in {`ambiguous`, `insufficient`} where the case allows it;
  at least 20% tagged `hard_negative` (near misses). Balance the primary choice classes.
- Internal fault parameters live under `facts["_internal"]` and must never be rendered.
- Synthetic only: fictional names, invalid-checksum IDs, `example.com`/`example.org` emails,
  reserved phone ranges. No real people, companies, logs or PII.

## Rendering rules (EN/TH pairs)

- `state` is a dict with the same ASCII keys in both languages, in the same order. Natural-language
  values are localized; machine values (ids, SQL, code, error strings, numbers, timestamps,
  column names) are byte-identical. Arabic digits only (no Thai digits).
- Thai must be natural technical Thai, including realistic code-switching where engineers would
  use English terms. Thai negation and hedging must preserve meaning.
- Never put option ids (e.g. `schema_mismatch`) or internal words (`injected`, `gold_label`,
  `fault_id`, `_internal`, `label_source`) in a state. `payload.assert_no_leakage` enforces this.
- `common_fit` states must be compact: aim for <= 110 English words and the equivalent Thai, so the
  English Laya checkpoint (512 tokens, ~300 left for state) usually fits. Truncation is measured at
  run time, not assumed. `natural_long` adds realistic surrounding evidence without removing any
  gold-critical fact.
- `difficulty_tags` vocabulary: `negation`, `code_switching`, `sql_identifier`, `hard_negative`,
  `distractor`, `insufficient_evidence`, `ambiguous`, `long_evidence`, `thai_abbreviation`,
  `spelling_variant`, `out_of_domain`.

## Question files

Follow the case's gold-question table in `PLAN.md` exactly: same question ids, option ids, level
count and level meaning. Criteria text is short (<= 12 English words; natural Thai equivalent)
because Laya shares a 192-token head budget between instructions and options. Options are stable
ASCII ids; score levels are 0-based and never reordered. `noul` criteria are omitted.

## Evidence backends

- Fixed, provider-independent query template; wrap each retrieval in `evidence.base.timed()`.
- Set `runtime` from `evidence.base` (`embedded`, `native_tarball`, `docker`) and report
  `engine_version()` from the engine itself (e.g. `select version()`).
- Bind to 127.0.0.1. Data directories live under `~/.cache/de-jev/<engine>/`. Raise
  `BackendUnavailable` when the engine cannot start.
- Secrets come from `Settings.secret(...)` and are never printed or logged.

## Tests

`tests/test_e0X_*.py`: generator determinism; exactly 300 specs; grouping never crosses splits;
>= 3 hand-verifiable edge-case fixtures with deterministic oracle assertions; every spec renders a
valid EN/TH pair (`paired_generator.validate_pair` returns `[]`); `rules_baseline` behaves on the
fixtures. Tests that need a live engine use `@pytest.mark.db`.
