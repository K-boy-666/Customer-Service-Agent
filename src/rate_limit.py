"""slowapi rate limiter setup with env-driven configuration."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

import config as runtime_config

_cfg = runtime_config.load_runtime_config()

LIMIT_OTP = _cfg.rate_limit_otp
LIMIT_ORCHESTRATOR = _cfg.rate_limit_orchestrator
LIMIT_WRITE = _cfg.rate_limit_write
LIMIT_READ = _cfg.rate_limit_read

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_cfg.rate_limit_storage_uri,
    enabled=_cfg.rate_limit_enabled,
)
