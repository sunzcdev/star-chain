"""Utility functions — logging setup, common helpers."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(
    log_dir: str = "~/.agent-channel/logs",
    log_level: str | None = None,
) -> None:
    """Configure logging with daily rotation.

    Args:
        log_dir: Directory for log files.
        log_level: Override log level (default: from AGENT_CHANNEL_LOG_LEVEL
                   env var, or INFO).
    """
    log_dir_path = Path(log_dir).expanduser()
    log_dir_path.mkdir(parents=True, exist_ok=True)

    level_name = (
        log_level
        or os.environ.get("AGENT_CHANNEL_LOG_LEVEL", "INFO")
    )
    level = getattr(logging, level_name.upper(), logging.INFO)

    # Main log file handler (daily rotation, 7 day retention)
    file_handler = TimedRotatingFileHandler(
        log_dir_path / "agent-channel.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    )

    # Error log file handler (WARNING and above, longer retention)
    error_handler = TimedRotatingFileHandler(
        log_dir_path / "errors.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    )

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)
