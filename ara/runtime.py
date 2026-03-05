# Location: ara/runtime.py
# Purpose: Session lifecycle — create, resume, persist state
# Functions: SessionStore, SessionRuntime
# Calls: engine.py, config.py, replay_log.py
# Imports: json, re, secrets, dataclasses, datetime, pathlib

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import ARAConfig
from .db import ARADB
from .engine import ContentDeltaCallback, ExternalContext, RLMEngine, StepCallback, TurnSummary
from .replay_log import ReplayLogger

EventCallback = Callable[[str], None]


class SessionError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


@dataclass
class SessionStore:
    workspace: Path
    session_root_dir: str = "ara_data"

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        self.root = (self.workspace / self.session_root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _metadata_path(self) -> Path:
        return self.root / "metadata.json"

    def _state_path(self) -> Path:
        return self.root / "state.json"

    def _events_path(self) -> Path:
        return self.root / "events.jsonl"

    def open_session(self, session_id: str | None = None, resume: bool = False) -> tuple[str, dict[str, Any], bool]:
        meta_path = self._metadata_path()
        if resume and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                sid = meta.get("session_id", _new_session_id())
            except (OSError, json.JSONDecodeError):
                sid = _new_session_id()
            state = self.load_state()
            return sid, state, False

        sid = session_id or _new_session_id()
        meta = {
            "session_id": sid,
            "workspace": str(self.workspace),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        state = self.load_state()
        return sid, state, True

    def load_state(self) -> dict[str, Any]:
        state_path = self._state_path()
        if not state_path.exists():
            return {"external_observations": []}
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"external_observations": []}

    def save_state(self, state: dict[str, Any]) -> None:
        self._state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
        self._touch_metadata()

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"ts": _utc_now(), "type": event_type, "payload": payload}
        with self._events_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")

    def _touch_metadata(self) -> None:
        meta_path = self._metadata_path()
        base: dict[str, Any] = {}
        if meta_path.exists():
            try:
                base = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        base["updated_at"] = _utc_now()
        meta_path.write_text(json.dumps(base, indent=2), encoding="utf-8")


@dataclass
class SessionRuntime:
    engine: RLMEngine
    store: SessionStore
    session_id: str
    context: ExternalContext
    db: ARADB | None = None
    max_persisted_observations: int = 400
    turn_history: list[TurnSummary] | None = None
    max_turn_summaries: int = 50

    @classmethod
    def bootstrap(
        cls, engine: RLMEngine, config: ARAConfig,
        session_id: str | None = None, resume: bool = False,
    ) -> SessionRuntime:
        store = SessionStore(workspace=config.workspace, session_root_dir=config.session_root_dir)
        sid, state, created_new = store.open_session(session_id=session_id, resume=resume)
        persisted = state.get("external_observations", [])
        obs = [str(x) for x in persisted] if isinstance(persisted, list) else []
        max_obs = max(1, config.max_persisted_observations)
        context = ExternalContext(observations=obs[-max_obs:])
        engine.session_dir = store.root
        engine.session_id = sid

        # Initialize SQLite database in ara_data/session.db
        db_path = store.root / "session.db"
        db = ARADB(db_path=db_path)

        # Wire DB into engine tools
        engine.tools.db = db

        raw_history = state.get("turn_history", [])
        turn_history: list[TurnSummary] = []
        if isinstance(raw_history, list):
            for item in raw_history:
                if isinstance(item, dict):
                    try:
                        turn_history.append(TurnSummary.from_dict(item))
                    except (KeyError, TypeError):
                        pass
        max_turns = max(1, config.max_turn_summaries)
        runtime = cls(
            engine=engine, store=store, session_id=sid,
            context=context, db=db, max_persisted_observations=max_obs,
            turn_history=turn_history[-max_turns:],
            max_turn_summaries=max_turns,
        )
        try:
            store.append_event("session_started", {"resume": resume, "created_new": created_new})
        except OSError:
            pass
        try:
            runtime._persist_state()
        except OSError:
            pass
        return runtime

    def solve(
        self, objective: str,
        on_event: EventCallback | None = None,
        on_step: StepCallback | None = None,
        on_content_delta: ContentDeltaCallback | None = None,
    ) -> str:
        objective = objective.strip()
        if not objective:
            return "No objective provided."
        try:
            self.store.append_event("objective", {"text": objective})
        except OSError:
            pass
        replay_path = self.store.root / "replay.jsonl"
        replay_logger = ReplayLogger(path=replay_path)
        replay_seq_start = replay_logger._seq

        def _on_event(msg: str) -> None:
            try:
                self.store.append_event("trace", {"message": msg})
            except OSError:
                pass
            if on_event:
                on_event(msg)

        result, updated_context = self.engine.solve_with_context(
            objective=objective, context=self.context,
            on_event=_on_event, on_step=on_step,
            on_content_delta=on_content_delta,
            replay_logger=replay_logger, turn_history=self.turn_history,
        )
        self.context = updated_context
        if self.turn_history is None:
            self.turn_history = []
        turn_number = (self.turn_history[-1].turn_number + 1) if self.turn_history else 1
        result_preview = result[:200] + "..." if len(result) > 200 else result
        steps_used = replay_logger._seq - replay_seq_start
        summary = TurnSummary(
            turn_number=turn_number, objective=objective,
            result_preview=result_preview, timestamp=_utc_now(),
            steps_used=steps_used, replay_seq_start=replay_seq_start,
        )
        self.turn_history.append(summary)
        if len(self.turn_history) > self.max_turn_summaries:
            self.turn_history = self.turn_history[-self.max_turn_summaries:]
        try:
            self.store.append_event("result", {"text": result})
        except OSError:
            pass
        try:
            self._persist_state()
        except OSError:
            pass
        return result

    def _persist_state(self) -> None:
        if len(self.context.observations) > self.max_persisted_observations:
            self.context.observations = self.context.observations[-self.max_persisted_observations:]
        state: dict[str, Any] = {
            "session_id": self.session_id,
            "saved_at": _utc_now(),
            "external_observations": self.context.observations,
        }
        if self.turn_history:
            state["turn_history"] = [t.to_dict() for t in self.turn_history]
        self.store.save_state(state)
