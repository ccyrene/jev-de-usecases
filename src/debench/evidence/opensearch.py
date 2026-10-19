"""OpenSearch evidence backend for E01 (pipeline failure triage).

Runtime: native tarball (infra/native/opensearch.sh), single node, security plugin disabled,
bound to 127.0.0.1. Every provider sees the output of the same fixed query template:

    filter  dag_id == D, run_id == R, event_ts in [window.start, window.end]
    should  constant-score clauses (summed): failed-like status +10, exception_type present +5,
            scheduler impact event +12, alert event +8, success-after-retry +9, error-like message +2
    sort    score desc, event_ts asc, task_id asc, retry_count asc;  size = TOP_N

The ranking is a pure function of document fields (no BM25 statistics), so it can be mirrored in
Python (``rank_events``) and verified against the engine. No gold or fault id is ever indexed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any

from debench.config import ROOT, Settings
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import RUNTIME_NATIVE, BackendUnavailable, Evidence, timed

INDEX = "airflow-task-logs"
TOP_N = 12
FAIL_STATUSES = ("failed", "up_for_retry", "upstream_failed")
IMPACT_STATUSES = ("blocked", "at_risk")
EVENT_FIELDS = (
    "event_ts",
    "task_id",
    "status",
    "exception_type",
    "message",
    "retry_count",
    "duration_ms",
    "upstream_task",
    "deployment_id",
)
ERROR_PATTERN = ".*(error|exception|denied|refused|timed out|no space|killed|does not exist|failed).*"
_ERROR_RE = re.compile(ERROR_PATTERN, re.IGNORECASE)

MAPPING_PATH = ROOT / "infra" / "init" / "e01_opensearch" / "mapping.json"
SCRIPT_PATH = ROOT / "infra" / "native" / "opensearch.sh"


def score_event(e: dict[str, Any]) -> int:
    """Python mirror of the should-clause weights."""
    s = 0
    if e["status"] in FAIL_STATUSES:
        s += 10
    if e["exception_type"]:
        s += 5
    if e["status"] in IMPACT_STATUSES:
        s += 12
    if e["status"] == "alert":
        s += 8
    if e["status"] == "success" and e["retry_count"] >= 1:
        s += 9
    if _ERROR_RE.fullmatch(e["message"]):
        s += 2
    return s


def rank_events(events: list[dict[str, Any]], window: dict[str, str], dag_id: str, run_id: str) -> list[dict[str, Any]]:
    """Reference implementation of the query template over an in-memory event list."""
    kept = []
    for e in events:
        if e["dag_id"] != dag_id or e["run_id"] != run_id:
            continue
        if not (window["start"] <= e["event_ts"] <= window["end"]):
            continue
        sc = score_event(e)
        if sc > 0:
            kept.append((sc, e))
    kept.sort(key=lambda p: (-p[0], p[1]["event_ts"], p[1]["task_id"], p[1]["retry_count"]))
    top = [e for _, e in kept[:TOP_N]]
    top.sort(key=lambda e: (e["event_ts"], e["task_id"], e["retry_count"]))
    return [{k: e.get(k) for k in EVENT_FIELDS} for e in top]


def build_query(dag_id: str, run_id: str, start: str, end: str) -> dict[str, Any]:
    def clause(flt: dict[str, Any], boost: int) -> dict[str, Any]:
        return {"constant_score": {"filter": flt, "boost": boost}}

    return {
        "size": TOP_N,
        "track_total_hits": False,
        "_source": list(EVENT_FIELDS),
        "query": {
            "bool": {
                "filter": [
                    {"term": {"dag_id": dag_id}},
                    {"term": {"run_id": run_id}},
                    {"range": {"event_ts": {"gte": start, "lte": end}}},
                ],
                "should": [
                    clause({"terms": {"status": list(FAIL_STATUSES)}}, 10),
                    clause({"exists": {"field": "exception_type"}}, 5),
                    clause({"terms": {"status": list(IMPACT_STATUSES)}}, 12),
                    clause({"term": {"status": "alert"}}, 8),
                    clause(
                        {
                            "bool": {
                                "filter": [
                                    {"term": {"status": "success"}},
                                    {"range": {"retry_count": {"gte": 1}}},
                                ]
                            }
                        },
                        9,
                    ),
                    clause(
                        {"regexp": {"message.raw": {"value": ERROR_PATTERN, "case_insensitive": True}}},
                        2,
                    ),
                ],
                "minimum_should_match": 1,
            }
        },
        "sort": [
            {"_score": "desc"},
            {"event_ts": "asc"},
            {"task_id": "asc"},
            {"retry_count": "asc"},
        ],
    }


class OpenSearchBackend:
    name = "opensearch"
    runtime = RUNTIME_NATIVE

    def __init__(self, settings: Settings):
        self.port = int(settings.get("OPENSEARCH_PORT", "9200"))
        self.host = "127.0.0.1"
        self._client: Any = None
        self._started_here = False

    def _connect(self) -> Any:
        try:
            from opensearchpy import OpenSearch
        except ImportError as exc:  # pragma: no cover - optional extra
            raise BackendUnavailable("opensearch-py is not installed") from exc
        return OpenSearch(
            hosts=[{"host": self.host, "port": self.port}], use_ssl=False, timeout=60, max_retries=2
        )

    def _ping(self) -> bool:
        try:
            self._client.info()
            return True
        except Exception:
            return False

    def setup(self) -> None:
        self._client = self._connect()
        if self._ping():
            return
        if not SCRIPT_PATH.exists():
            raise BackendUnavailable(f"OpenSearch not reachable on :{self.port} and {SCRIPT_PATH} is missing")
        env = {**os.environ, "OPENSEARCH_PORT": str(self.port)}
        try:
            subprocess.run(["bash", str(SCRIPT_PATH), "start"], check=True, env=env, timeout=900)
        except (subprocess.SubprocessError, OSError) as exc:
            raise BackendUnavailable(f"could not start OpenSearch via {SCRIPT_PATH.name}: {exc}") from exc
        self._started_here = True
        deadline = time.time() + 60
        while time.time() < deadline and not self._ping():
            time.sleep(1)
        if not self._ping():
            raise BackendUnavailable("OpenSearch started but does not answer")

    def engine_version(self) -> str:
        info = self._client.info()["version"]
        return f"{info.get('distribution', 'opensearch')} {info['number']}"

    def load(self, seed_payload: Any) -> None:
        from opensearchpy import helpers

        docs: list[dict[str, Any]] = seed_payload["docs"]
        if self._client.indices.exists(index=INDEX):
            self._client.indices.delete(index=INDEX)
        body = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        self._client.indices.create(index=INDEX, body=body)
        actions = (
            {"_op_type": "index", "_index": INDEX, "_id": d["_id"], "_source": d["_source"]} for d in docs
        )
        ok, errors = helpers.bulk(self._client, actions, chunk_size=2000, raise_on_error=False)
        if errors:
            raise RuntimeError(f"bulk load failed for {len(errors)} documents, first: {errors[0]}")
        self._client.indices.put_settings(index=INDEX, body={"index": {"refresh_interval": "1s"}})
        self._client.indices.refresh(index=INDEX)
        self._client.cluster.health(index=INDEX, wait_for_status="yellow", request_timeout=60)
        count = self._client.count(index=INDEX)["count"]
        if count != len(docs):
            raise RuntimeError(f"indexed {count} documents, expected {len(docs)}")

    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        f = spec.facts
        query = build_query(f["dag_id"], f["run_id"], f["window"]["start"], f["window"]["end"])
        with timed() as t:
            resp = self._client.search(index=INDEX, body=query)
        hits = resp["hits"]["hits"]
        events = [{k: h["_source"].get(k) for k in EVENT_FIELDS} for h in hits]
        order = sorted(range(len(events)), key=lambda i: (events[i]["event_ts"], events[i]["task_id"], events[i]["retry_count"]))
        events = [events[i] for i in order]
        ids = [hits[i]["_id"] for i in order]
        return Evidence(
            facts={"dag_id": f["dag_id"], "run_id": f["run_id"], "events": events},
            ref={"backend": self.name, "index": INDEX, "document_ids": ids},
            db_ms=t["ms"],
            extra={"took_ms": resp.get("took")},
        )

    def teardown(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._started_here and not os.environ.get("DEBENCH_KEEP_ENGINES"):
            subprocess.run(["bash", str(SCRIPT_PATH), "stop"], check=False, timeout=180)
            self._started_here = False
