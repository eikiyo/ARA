# Location: ara/runtime.py
# Purpose: Session lifecycle — creation, resumption, solve interface
# Functions: SessionRuntime
# Calls: engine.py, db.py, config.py
# Imports: json, uuid, pathlib

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from .config import ARAConfig
from .db import ARADB
from .engine import RLMEngine, ExternalContext, StepCallback, StepEvent, TurnSummary
from .output import generate_output


class SessionError(Exception):
    pass


class SessionRuntime:
    def __init__(
        self,
        engine: RLMEngine,
        config: ARAConfig,
        db: ARADB,
        session_id: str,
        db_session_id: int | None = None,
    ):
        self.engine = engine
        self.config = config
        self.db = db
        self.session_id = session_id
        self.db_session_id = db_session_id
        self._context = ExternalContext()

        # Wire DB to tools
        engine.tools.db = db
        engine.tools.session_id = db_session_id

    @classmethod
    def bootstrap(
        cls,
        engine: RLMEngine,
        config: ARAConfig,
        resume: bool = False,
    ) -> SessionRuntime:
        db_path = config.workspace / config.session_root_dir / "session.db"
        db = ARADB(db_path)

        if resume:
            # Find most recent active session
            row = db._conn.execute(
                "SELECT session_id FROM sessions WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                db_session_id = row["session_id"]
                session = db.get_session(db_session_id)
                sid = f"session-{db_session_id}"
                rt = cls(engine=engine, config=config, db=db,
                         session_id=sid, db_session_id=db_session_id)
                rt._context.topic = session.get("topic", "") if session else ""
                rt._context.paper_type = session.get("paper_type", "research_article") if session else "research_article"
                return rt
            raise SessionError("No active session to resume")

        sid = f"session-{uuid.uuid4().hex[:8]}"
        return cls(engine=engine, config=config, db=db, session_id=sid)

    def start_research(self, topic: str, paper_type: str = "research_article") -> None:
        self._context.topic = topic
        self._context.paper_type = paper_type
        db_sid = self.db.create_session(topic=topic, paper_type=paper_type)
        self.db_session_id = db_sid
        self.engine.tools.session_id = db_sid

    def solve(
        self,
        objective: str,
        on_event: StepCallback | None = None,
    ) -> str:
        # If no research session started yet, start one
        if not self.db_session_id and not self._context.topic:
            self.start_research(topic=objective)

        result = self.engine.solve(
            objective=objective,
            context=self._context,
            on_event=on_event,
        )

        # Generate output files after solve completes
        self._generate_output()

        return result

    def _generate_output(self) -> None:
        ws = self.config.workspace
        ara_output = ws / self.config.session_root_dir / "output"
        sections_dir = ara_output / "sections"
        if not sections_dir.exists():
            return
        output_dir = ws / "output"
        bib_path = ara_output / "references.bib"
        apa_path = ara_output / "references_apa.txt"
        prisma_svg = ara_output / "prisma.svg"
        prisma_ascii = ara_output / "prisma_ascii.md"
        quality_audit = output_dir / "quality_audit.json"
        try:
            files = generate_output(
                output_dir=output_dir,
                sections_dir=sections_dir,
                bib_path=bib_path if bib_path.exists() else None,
                topic=self._context.topic,
                paper_type=self._context.paper_type,
                apa_path=apa_path if apa_path.exists() else None,
                prisma_svg_path=prisma_svg if prisma_svg.exists() else None,
                prisma_ascii_path=prisma_ascii if prisma_ascii.exists() else None,
                quality_audit_path=quality_audit if quality_audit.exists() else None,
            )
            if files:
                import logging
                logging.getLogger(__name__).info("Output files: %s", list(files.keys()))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Output generation failed: %s", exc)

    def cancel(self) -> None:
        self.engine.cancel_flag.set()
