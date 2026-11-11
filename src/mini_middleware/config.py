"""Configuration, read from environment variables so secrets never live in code or files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    db_path: Path = Path("data/middleware.db")
    erp_url: str = "http://127.0.0.1:8099/erp/messages"
    erp_token: str = field(default="", repr=False)
    erp_timeout: float = 5.0
    max_attempts: int = 4
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    rate_limit_per_sec: float = 0.0  # 0 disables rate limiting
    lease_seconds: float = 60.0
    http_token: str = field(default="", repr=False)
    max_message_bytes: int = 1_000_000

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if environ is None else environ
        d = cls()
        return cls(
            db_path=Path(env.get("MM_DB_PATH", str(d.db_path))),
            erp_url=env.get("MM_ERP_URL", d.erp_url),
            erp_token=env.get("MM_ERP_TOKEN", d.erp_token),
            erp_timeout=float(env.get("MM_ERP_TIMEOUT", d.erp_timeout)),
            max_attempts=int(env.get("MM_MAX_ATTEMPTS", d.max_attempts)),
            backoff_base=float(env.get("MM_BACKOFF_BASE", d.backoff_base)),
            backoff_cap=float(env.get("MM_BACKOFF_CAP", d.backoff_cap)),
            rate_limit_per_sec=float(env.get("MM_RATE_LIMIT", d.rate_limit_per_sec)),
            lease_seconds=float(env.get("MM_LEASE_SECONDS", d.lease_seconds)),
            http_token=env.get("MM_HTTP_TOKEN", d.http_token),
            max_message_bytes=int(env.get("MM_MAX_MESSAGE_BYTES", d.max_message_bytes)),
        )
