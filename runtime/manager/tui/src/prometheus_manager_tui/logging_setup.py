"""TUI-specific logging setup — redirect stdout/stderr logging to a file.

Implements: memory/specs/008-llama-server-manager.md (fix — TUI stdout logging corruption)
Implements: memory/specs/020-shared-telemetry-package.md — AC-17
"""

from __future__ import annotations

import io
import logging
import logging.handlers
import os
import sys

import structlog
from prometheus_telemetry.core import _SHARED_PROCESSORS

__all__ = ["redirect_logging_for_tui"]


def redirect_logging_for_tui(log_file_path: str | None = None) -> None:
    """Remove all stdout/stderr handlers from the root logger before starting the TUI.

    Textual takes full control of the terminal; any text written to stdout corrupts
    the layout.  This function strips every StreamHandler that points at stdout or
    stderr and installs a RotatingFileHandler at *log_file_path* (when provided) so
    that logs are still persisted and can be followed with ``tail -f``.

    Should be called immediately before ``ManagerApp.run()``.

    Args:
        log_file_path: Path for the rotating log file.  When empty or None all
                       log output is discarded for the duration of the TUI session.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream in (
            sys.stdout,
            sys.stderr,
        ):
            handler.close()
            root.removeHandler(handler)

    # httpx and httpcore emit INFO-level request logs for every health probe.
    # These are noise in the TUI log file — escalate to WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _log_level = os.environ.get("LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, _log_level.upper(), logging.INFO)

    if log_file_path:
        try:
            import pathlib

            path = pathlib.Path(log_file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            formatter = structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    *_SHARED_PROCESSORS,
                    structlog.processors.JSONRenderer(),
                ],
                foreign_pre_chain=_SHARED_PROCESSORS,
            )
            file_handler = logging.handlers.RotatingFileHandler(
                str(path),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

            # Route structlog-native calls through stdlib so they reach the file handler.
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
                processors=[
                    *_SHARED_PROCESSORS,
                    structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
                ],
                logger_factory=structlog.stdlib.LoggerFactory(),
                cache_logger_on_first_use=False,
            )
        except (OSError, PermissionError):
            _silence_structlog()
            if not root.handlers:
                root.addHandler(logging.NullHandler())
    else:
        _silence_structlog()
        if not root.handlers:
            root.addHandler(logging.NullHandler())


def _silence_structlog() -> None:
    """Route structlog to an in-memory sink so no output reaches the terminal."""
    _sink = io.StringIO()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(_sink),
        cache_logger_on_first_use=False,
    )
