"""Runtime configuration validation for production deployments."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEV_AUTH_SECRET = "customer-service-dev-secret-min-32-bytes"
PRODUCTION_ENVS = {"prod", "production"}


def _read_secret(name: str, environ: Mapping[str, str]) -> str:
    """Read a secret value from an env var or a ``_FILE``-suffixed file path.

    If ``{name}_FILE`` is set, the file contents are read and stripped.
    Otherwise, the env var ``{name}`` is returned as-is.
    This supports Docker secrets without breaking env-only deployments.
    """
    file_path = environ.get(f"{name}_FILE")
    if file_path:
        with open(file_path, encoding="utf-8") as f:
            return f.read().strip()
    return environ.get(name, "")


@dataclass(frozen=True)
class RuntimeConfig:
    app_env: str
    database_url: str
    oidc_issuer: str
    oidc_audience: str
    oidc_jwks_url: str
    otp_provider: str
    report_timezone: str
    log_level: str
    faq_rag_backend: str
    log_json: bool
    rate_limit_enabled: bool
    rate_limit_storage_uri: str
    rate_limit_otp: str
    rate_limit_orchestrator: str
    rate_limit_write: str
    rate_limit_read: str

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in PRODUCTION_ENVS


def load_runtime_config(environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    env = environ or os.environ
    app_env = env.get("APP_ENV", "development")
    is_prod = app_env.lower() in PRODUCTION_ENVS
    log_json_raw = env.get("LOG_JSON", "").lower()
    log_json = is_prod if log_json_raw == "" else log_json_raw in {"1", "true", "yes"}
    return RuntimeConfig(
        app_env=app_env,
        database_url=env.get("DATABASE_URL", "sqlite+pysqlite:///data/orders.db"),
        oidc_issuer=env.get("OIDC_ISSUER", "customer-service-dev"),
        oidc_audience=env.get("OIDC_AUDIENCE", "customer-service-api"),
        oidc_jwks_url=env.get("OIDC_JWKS_URL", ""),
        otp_provider=env.get("OTP_PROVIDER", "dev"),
        report_timezone=env.get("REPORT_TIMEZONE", "Asia/Shanghai"),
        log_level=env.get("LOG_LEVEL", "INFO"),
        faq_rag_backend=env.get("FAQ_RAG_BACKEND", "auto"),
        log_json=log_json,
        rate_limit_enabled=env.get("RATE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes"},
        rate_limit_storage_uri=env.get("RATE_LIMIT_STORAGE_URI", "memory://"),
        rate_limit_otp=env.get("RATE_LIMIT_OTP", "5/minute"),
        rate_limit_orchestrator=env.get("RATE_LIMIT_ORCHESTRATOR", "60/minute"),
        rate_limit_write=env.get("RATE_LIMIT_WRITE", "30/minute"),
        rate_limit_read=env.get("RATE_LIMIT_READ", "120/minute"),
    )


def is_production(environ: Mapping[str, str] | None = None) -> bool:
    return load_runtime_config(environ).is_production


def validate_runtime_config(environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    env = environ or os.environ
    cfg = load_runtime_config(environ)
    errors: list[str] = []

    if cfg.is_production:
        if cfg.otp_provider.lower() in {"", "dev", "debug", "local"}:
            errors.append("OTP_PROVIDER must use a non-dev provider in production")
        if not cfg.oidc_jwks_url:
            errors.append("OIDC_JWKS_URL is required in production")
        auth_secret = _read_secret("AUTH_DEV_SECRET", env)
        if not auth_secret or auth_secret == DEV_AUTH_SECRET:
            errors.append("AUTH_DEV_SECRET must not use the development default in production")
        if cfg.database_url.startswith("sqlite"):
            errors.append("DATABASE_URL should point to MySQL or another production database in production")

    if errors:
        raise RuntimeError("; ".join(errors))
    return cfg
