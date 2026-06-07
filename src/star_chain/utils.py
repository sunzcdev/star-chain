"""工具函数 — 日志初始化。"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(
    log_dir: str = "~/.star-chain/logs",
    log_level: str | None = None,
) -> None:
    log_dir_path = Path(log_dir).expanduser()
    log_dir_path.mkdir(parents=True, exist_ok=True)

    level_name = log_level or os.environ.get("STAR_CHAIN_LOG_LEVEL", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)

    file_handler = TimedRotatingFileHandler(
        log_dir_path / "star-chain.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    error_handler = TimedRotatingFileHandler(
        log_dir_path / "errors.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)
