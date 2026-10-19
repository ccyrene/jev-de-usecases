"""Runtime settings from .env. Secret values are held privately and never printed or logged."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs"


def is_secret_key(key: str) -> bool:
    return key == "TYPESAFE_API_KEY" or key.endswith("_PASSWORD")


@dataclass(frozen=True)
class Settings:
    values: dict[str, str] = field(repr=False)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key) or default

    def secret(self, key: str) -> str | None:
        """Only provider/DB clients call this; callers must not log the result."""
        if not is_secret_key(key):
            raise KeyError(f"{key} is not a secret key")
        return self.values.get(key) or None

    def is_set(self, key: str) -> bool:
        return bool(self.values.get(key))

    @property
    def allow_remote_api(self) -> bool:
        return self.get("ALLOW_REMOTE_API", "0") == "1"

    @property
    def remote_enabled(self) -> bool:
        return self.allow_remote_api and self.is_set("TYPESAFE_API_KEY")

    def get_int(self, key: str, default: int) -> int:
        return int(self.get(key, str(default)))

    def get_float(self, key: str, default: float) -> float:
        return float(self.get(key, str(default)))

    def redacted(self) -> dict[str, Any]:
        """Safe for manifests: secrets become booleans."""
        return {
            k: (f"<{'set' if v else 'empty'}>" if is_secret_key(k) else v) for k, v in sorted(self.values.items())
        }

    def __repr__(self) -> str:
        return f"Settings(keys={len(self.values)}, remote_enabled={self.remote_enabled})"


def load_settings(env_file: Path | None = None) -> Settings:
    """.env values; the process environment may override non-secret switches only.

    A secret set in .env always wins over a same-named shell variable (a stale exported key in a
    shell profile must never silently replace the project's key). Shell secrets are used only when
    .env leaves them empty. Overridden names are recorded, never their values.
    """
    import os

    path = env_file or ROOT / ".env"
    values = {k: (v or "") for k, v in dotenv_values(path).items()} if path.exists() else {}
    ignored: list[str] = []
    for key, val in os.environ.items():
        if not val or not (key in values or key.startswith(("ALLOW_", "TYPESAFE_", "JEV_", "LAYA_", "SEED"))):
            continue
        if is_secret_key(key) and values.get(key):
            if val != values[key]:
                ignored.append(key)
            continue
        values[key] = val
    if ignored:
        values["_IGNORED_SHELL_SECRETS"] = ",".join(sorted(ignored))
    return Settings(values=values)
