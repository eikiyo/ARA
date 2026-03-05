# Location: ara/logging.py
# Purpose: Structured file-based logging for ARA — API failures, source bans, tool errors
# Functions: get_logger, setup_logging
# Calls: N/A
# Imports: logging, pathlib, datetime

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


_INITIALIZED = False


def setup_logging(workspace: Path, session_root_dir: str = "ara_data") -> logging.Logger:
    """Set up file + stderr logging for ARA. Returns the root 'ara' logger."""
    global _INITIALIZED
    logger = logging.getLogger("ara")

    if _INITIALIZED:
        return logger

    logger.setLevel(logging.DEBUG)

    log_dir = (workspace / session_root_dir / "logs").resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"ara_{stamp}.log"

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — DEBUG level (everything)
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Stderr handler — WARNING+ only (don't clutter TUI)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Also create a symlink to latest log for easy access
    latest = log_dir / "latest.log"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(log_file.name)
    except OSError:
        pass

    _INITIALIZED = True
    logger.info("ARA logging initialized — %s", log_file)
    return logger


def get_logger(name: str = "ara") -> logging.Logger:
    """Get a named child logger under the 'ara' namespace."""
    if name == "ara":
        return logging.getLogger("ara")
    return logging.getLogger(f"ara.{name}")
