"""structlog configuration with stdlib logging bridge via ProcessorFormatter.

Production outputs JSON; development outputs colored console.
Existing ``logging.getLogger(__name__)`` calls are automatically bridged
through ``foreign_pre_chain`` and receive contextvars (request_id, etc.).
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    log_level: str = "INFO",
    json_logs: bool = False,
    log_to_stderr: bool = False,
) -> None:
    """Configure structlog + stdlib logging with unified ProcessorFormatter.

    Args:
        log_level: Logging level string (DEBUG/INFO/WARNING/ERROR).
        json_logs: True for JSONRenderer (production), False for ConsoleRenderer (dev).
        log_to_stderr: True for MCP stdio servers (stdout reserved for protocol).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            }
        ),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[renderer],
    )

    stream = sys.stderr if log_to_stderr else sys.stdout
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
