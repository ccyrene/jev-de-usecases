"""PostgreSQL runtime shared by E03 and E05.

Without Docker, an embedded PostgreSQL 18 server (pixeltable-pgserver, real upstream binaries) runs
from a per-case data directory on the Linux filesystem. With Docker, set DEBENCH_PG_URI_<CASE> to the
compose service URI instead; the runtime is recorded either way.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from debench.evidence.base import RUNTIME_DOCKER, RUNTIME_EMBEDDED, BackendUnavailable

CACHE_DIR = Path(os.environ.get("DEBENCH_CACHE", "~/.cache/de-jev")).expanduser()


class PostgresRuntime:
    def __init__(self, case: str):
        self.case = case.lower()
        self._server: Any = None
        self.uri: str | None = os.environ.get(f"DEBENCH_PG_URI_{case.upper()}") or None
        self.runtime = RUNTIME_DOCKER if self.uri else RUNTIME_EMBEDDED

    def start(self) -> str:
        if self.uri:
            return self.uri
        try:
            import pixeltable_pgserver
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise BackendUnavailable("pixeltable-pgserver not installed (uv sync --extra db)") from exc
        pgdata = CACHE_DIR / "pg" / self.case
        pgdata.parent.mkdir(parents=True, exist_ok=True)
        self._server = pixeltable_pgserver.get_server(pgdata, cleanup_mode="stop")
        self.uri = self._server.get_uri()
        return self.uri

    def stop(self) -> None:
        if self._server is not None:
            self._server.cleanup()
            self._server = None
            self.uri = None
