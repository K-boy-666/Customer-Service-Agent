"""Runtime configuration validation for production deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


DEV_AUTH_SECRET = "customer-service-dev-secret-min-32-bytes"
PRODUCTION_ENVS = {"prod", "production"}


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

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in PRODUCTION_ENVS


def load_runtime_config(environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    env = environ or os.environ
    return RuntimeConfig(
        app_env=env.get("APP_ENV", "development"),
        database_url=env.get("DATABASE_URL", "sqlite+pysqlite:///data/orders.db"),
        oidc_issuer=env.get("OIDC_ISSUER", "customer-service-dev"),
        oidc_audience=env.get("OIDC_AUDIENCE", "customer-service-api"),
        oidc_jwks_url=env.get("OIDC_JWKS_URL", ""),
        otp_provider=env.get("OTP_PROVIDER", "dev"),
        report_timezone=env.get("REPORT_TIMEZONE", "Asia/Shanghai"),
        log_level=env.get("LOG_LEVEL", "INFO"),
        faq_rag_backend=env.get("FAQ_RAG_BACKEND", "auto"),
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
        if env.get("AUTH_DEV_SECRET", DEV_AUTH_SECRET) == DEV_AUTH_SECRET:
            errors.append("AUTH_DEV_SECRET must not use the development default in production")
        if cfg.database_url.startswith("sqlite"):
            errors.append("DATABASE_URL should point to MySQL or another production database in production")

    if errors:
        raise RuntimeError("; ".join(errors))
    return cfg
