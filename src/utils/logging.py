"""Logging configuration with file persistence."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler


def setup_logging(
    log_dir: str = "logs",
    level: int = logging.INFO,
    name: str = "ai-digest",
) -> logging.Logger:
    """
    Configure logging with both console and file output.

    Args:
        log_dir: Directory for log files
        level: Logging level
        name: Logger name

    Returns:
        Configured logger
    """
    # Create logs directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Log file path with date
    log_file = log_path / f"{name}-{datetime.now().strftime('%Y-%m-%d')}.log"

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Clear existing handlers
    logger.handlers.clear()

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    )

    # Rich console handler for pretty output
    console_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
    )
    console_handler.setLevel(level)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str = "ai-digest") -> logging.Logger:
    """
    Get an existing logger or create one with default settings.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)

    # If logger has no handlers, set up default configuration
    if not logger.handlers:
        return setup_logging(name=name)

    return logger


# Default logger instance
_default_logger: Optional[logging.Logger] = None


def log_info(message: str) -> None:
    """Log an info message."""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logging()
    _default_logger.info(message)


def log_warning(message: str) -> None:
    """Log a warning message."""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logging()
    _default_logger.warning(message)


def log_error(message: str, exc_info: bool = False) -> None:
    """Log an error message."""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logging()
    _default_logger.error(message, exc_info=exc_info)
