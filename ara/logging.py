# Location: ara/logging.py
# Purpose: Logging setup for ARA
# Functions: setup_logging
# Calls: N/A
# Imports: logging, pathlib

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(workspace: Path, session_root_dir: str = "ara_data") -> None:
    log_dir = workspace / session_root_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ara.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    # Keep httpx and other noisy loggers quiet
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
