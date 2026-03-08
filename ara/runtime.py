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

from .central_db import CentralDB
from .config import ARAConfig
from .db import ARADB
from .engine import RLMEngine, ExternalContext, StepCallback, StepEvent, TurnSummary
from .output import generate_output
from .peer_review import PeerReviewPipeline, build_peer_review_models


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
        engine.tools.central_db = getattr(db, '_central', None)

    @classmethod
    def bootstrap(
        cls,
        engine: RLMEngine,
        config: ARAConfig,
        resume: bool = False,
    ) -> SessionRuntime:
        db_path = config.workspace / config.session_root_dir / "session.db"
        # Initialize persistent central DB
        central = CentralDB()  # ~/.ara/central.db
        db = ARADB(db_path, central_db=central)

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
                rt._context.paper_type = session.get("paper_type", config.paper_type) if session else config.paper_type
                return rt
            raise SessionError("No active session to resume")

        sid = f"session-{uuid.uuid4().hex[:8]}"
        return cls(engine=engine, config=config, db=db, session_id=sid)

    def start_research(self, topic: str, paper_type: str | None = None) -> None:
        self._context.topic = topic
        resolved_type = paper_type or self.config.paper_type or "review"
        self._context.paper_type = resolved_type
        db_sid = self.db.create_session(topic=topic, paper_type=resolved_type)
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

        # Use programmatic pipeline instead of LLM manager
        result = self.engine.run_pipeline(
            topic=self._context.topic,
            paper_type=self._context.paper_type,
            context=self._context,
            on_event=on_event,
        )

        # Generate output files after pipeline completes
        self._generate_output()

        # Run peer review pipeline if enabled
        if self.config.peer_review_enabled:
            self._run_peer_review(on_event)

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

    def _run_peer_review(self, on_event: StepCallback | None = None) -> None:
        """Run the peer review pipeline after main pipeline output generation."""
        import logging
        log = logging.getLogger(__name__)

        models = build_peer_review_models(self.config)
        if models is None:
            log.warning("Peer review skipped — required API keys not configured. "
                        "Set ANTHROPIC_API_KEY and GOOGLE_API_KEY to enable.")
            if on_event:
                on_event(StepEvent("text", data="Peer review skipped — API keys not configured", depth=0))
            return

        central = getattr(self.db, '_central', None)
        pipeline = PeerReviewPipeline(
            models=models,
            config=self.config,
            db=self.db,
            session_id=self.db_session_id,
            central_db=central,
            on_event=on_event,
        )

        if on_event:
            on_event(StepEvent("subtask_start", data="Phase: peer_review", depth=0))

        try:
            result = pipeline.run(
                topic=self._context.topic,
                paper_type=self._context.paper_type,
            )
            if result.get("error"):
                log.error("Peer review failed: %s", result["error"])
            else:
                improved = result.get("improved", False)
                cost = result.get("total_cost_usd", 0)
                log.info("Peer review complete — improved=%s, cost=$%.2f", improved, cost)
                if on_event:
                    status = "IMPROVED" if improved else "NOT IMPROVED"
                    on_event(StepEvent("text",
                        data=f"Peer review: {status} | Cost: ${cost:.2f} | "
                             f"Cycle 1 avg: {result.get('cycle1_avg', 0):.1f}"
                             + (f" | Cycle 2 avg: {result.get('cycle2_avg', 0):.1f}" if 'cycle2_avg' in result else ""),
                        depth=0))

                # Post-peer-review programmatic gate — catch regressions from revision agent
                gate_result = self.engine.post_peer_review_gate(on_event)
                if gate_result.get("fixes", 0) > 0:
                    log.info("Post-peer-review gate: applied %d fixes — regenerating output",
                             gate_result["fixes"])
                    self._generate_output()  # Regenerate paper.md with gate fixes applied
                if gate_result.get("issues"):
                    log.warning("Post-peer-review gate: %d issues — %s",
                                len(gate_result["issues"]), gate_result["issues"])
        except Exception as exc:
            log.exception("Peer review pipeline error: %s", exc)
            if on_event:
                on_event(StepEvent("error", data=f"Peer review error: {exc}", depth=0))

        if on_event:
            on_event(StepEvent("subtask_end", data="Phase: peer_review complete", depth=0))

    def cancel(self) -> None:
        self.engine.cancel_flag.set()
