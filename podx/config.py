"""Environment-driven settings. One object, no magic constants elsewhere."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Vercel (and any Lambda) mounts the deployment read-only and offers /tmp as the only
# scratch space, so storage moves there and the log poller is switched off. The platform
# sets VERCEL=1 itself; nothing needs configuring to make that happen.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
SCRATCH = Path("/tmp/podx")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # X Layer
    rpc_url: str = field(default_factory=lambda: os.environ.get("RPC_XLAYER", "https://rpc.xlayer.tech"))
    rpc_fallbacks: tuple[str, ...] = ("https://xlayerrpc.okx.com", "https://xlayer.drpc.org")
    chain_id: int = 196
    pod_mining: str = field(
        default_factory=lambda: os.environ.get(
            "POD_MINING", "0x4F6A2312D80753BdAFd35DDB1Bf4BB14C43F8265"
        )
    )
    processor: str = field(
        default_factory=lambda: os.environ.get(
            "POD_PROCESSOR", "0x87BFCa3494Aa228fF589ab9B642143A5e8c8F9a6"
        )
    )
    pod_api: str = field(default_factory=lambda: os.environ.get("POD_API", "https://api.fabx.tech"))
    pin_slack: int = field(default_factory=lambda: _int("PIN_SLACK", 30))

    # storage
    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("DB_PATH")
            or str(SCRATCH / "podx.sqlite" if SERVERLESS else BASE_DIR / "data" / "podx.sqlite")
        )
    )
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CACHE_DIR")
            or str(SCRATCH / "cache" if SERVERLESS else BASE_DIR / "data" / "cache")
        )
    )

    # web
    base_url: str = field(default_factory=lambda: os.environ.get("BASE_URL", "").rstrip("/"))
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", ""))
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_TOKEN", ""))
    telegram_secret: str = field(default_factory=lambda: os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""))
    container_addr: str = field(default_factory=lambda: os.environ.get("CONTAINER_ADDR", ""))

    # limits
    rate_per_hour: int = field(default_factory=lambda: _int("RATE_PER_HOUR", 60))
    rate_daily: int = field(default_factory=lambda: _int("RATE_DAILY", 2000))

    # background
    enable_watcher: bool = field(default_factory=lambda: _bool("ENABLE_WATCHER", not SERVERLESS))
    watch_interval: int = field(default_factory=lambda: _int("WATCH_INTERVAL", 45))
    http_timeout: float = field(default_factory=lambda: float(os.environ.get("HTTP_TIMEOUT", "25")))
    offline: bool = field(default_factory=lambda: _bool("OFFLINE", False))

    engine_version: str = "podx-1.0.0"

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "vectors").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "tasks").mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
