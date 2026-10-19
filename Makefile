# Reproducible entry points (PLAN section 14). Every target is a thin wrapper over a Python module,
# so `python -m debench.<module>` works without make.
SHELL := /bin/bash
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/de-jev
export UV_LINK_MODE ?= copy
PY ?= $(UV_PROJECT_ENVIRONMENT)/bin/python

SEED ?= 20261019
CASE ?= e01
CASES ?= E01 E02 E03 E04 E05 E06
PROVIDER ?= laya
LANG ?= both
N ?= 20
SPLIT ?= test
PROTOCOL ?= primary
DEVICE ?= cuda
RUNS ?= pilot_laya,pilot_rules
OUT ?= results/analysis/pilot
DOCKER ?= 0

ifeq ($(PROVIDER),laya)
TRACKS ?= laya_routed,laya_en_fixed,laya_multi_fixed
else ifeq ($(PROVIDER),jev)
TRACKS ?= jev_114
else ifeq ($(PROVIDER),rules)
TRACKS ?= rules_only
endif

ifeq ($(LANG),both)
PILOT_PROTOCOL := primary
else ifeq ($(LANG),en)
PILOT_PROTOCOL := P1_primary_en
else
PILOT_PROTOCOL := P2_primary_th
endif

UCASE = $(shell echo $(CASE) | tr a-z A-Z)

.PHONY: setup doctor db-up db-down db-oracle-test generate validate-pairs test test-all rules-baseline \
        pilot evaluate analyze report figures reproduce

setup:
	uv sync --extra db --extra laya --group dev

doctor:
	$(PY) -m debench.doctor

# E01 (OpenSearch) and E04 (Neo4j) run natively without Docker; E02/E03/E05/E06 are embedded engines
# started by the build itself. With DOCKER=1 the pinned compose profile is used instead.
db-up:
ifeq ($(DOCKER),1)
	docker compose -f infra/compose.yaml --env-file .env --profile $(CASE) up -d --wait
else
	@case "$(CASE)" in e01) infra/native/opensearch.sh start ;; e04) infra/native/neo4j.sh start ;; \
	  *) echo "$(CASE): embedded engine, nothing to start" ;; esac
endif

db-down:
ifeq ($(DOCKER),1)
	docker compose -f infra/compose.yaml --env-file .env --profile $(CASE) down
else
	@case "$(CASE)" in e01) infra/native/opensearch.sh stop ;; e04) infra/native/neo4j.sh stop ;; \
	  *) echo "$(CASE): embedded engine, nothing to stop" ;; esac
endif

db-oracle-test:
	$(PY) -m pytest tests/test_$(CASE)_*.py -m db -q

# Builds one case (CASE=e01) or, with CASE=all, every case. Loads the DB and verifies gold.
generate:
ifeq ($(CASE),all)
	@for c in $(CASES); do $(PY) -m debench.dataset.build $$c --mode db --seed $(SEED) || exit 1; done
else
	$(PY) -m debench.dataset.build $(UCASE) --mode db --seed $(SEED)
endif

validate-pairs:
	$(PY) -m pytest tests/test_pair_equivalence.py tests/test_gold_is_not_in_prompt.py tests/test_db_ground_truth.py -q

test:
	$(PY) -m pytest -q -m "not remote and not db and not laya"

test-all:
	$(PY) -m pytest -q -m "not remote"

rules-baseline:
	$(PY) -m debench.runner --case all --tracks rules_only --protocol primary --split $(SPLIT) --run-name rules_$(SPLIT)

# Pilot on the dev split (never reported as a final result).
pilot:
	$(PY) -m debench.runner --case $(if $(filter all,$(CASE)),all,$(UCASE)) --tracks $(TRACKS) \
	  --protocol $(PILOT_PROTOCOL) --split dev --n $(N) --device $(DEVICE) --warmup 30 --run-name pilot_$(PROVIDER)

evaluate:
	$(PY) -m debench.runner --case all --tracks $(TRACKS) --protocol $(PROTOCOL) --split $(SPLIT) \
	  --device $(DEVICE) --warmup 30 --run-name $(SPLIT)_$(PROTOCOL)_$(PROVIDER)

analyze:
	$(PY) -m debench.analyze --runs $(RUNS) --out $(OUT)

report:
	$(PY) -m debench.report --analysis $(OUT)

figures:
	$(PY) -m debench.figures --analysis $(OUT)

# Full study in order (data, pilot, Laya, Jev if enabled, latency, report). STEP=<name> runs one step.
STEP ?= all
reproduce:
	PY=$(PY) DEVICE=$(DEVICE) bash scripts/reproduce.sh $(STEP)
