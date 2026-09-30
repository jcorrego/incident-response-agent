"""SQLite checkpoints for the read-only synthetic investigation.

One transaction owns a stage, including its tool reads. A crash before commit repeats
only the current stage; completed stages are never recomputed. This is safe because
all tool calls in this reference workflow are read-only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .tools import ToolUnavailable


class Stage(StrEnum):
    EVIDENCE = "evidence"
    ANALYSIS = "analysis"
    RUNBOOK = "runbook"
    DECISION = "decision"
    COMPLETED = "completed"
    ESCALATED = "escalated"


NEXT_STAGE = {
    Stage.EVIDENCE: Stage.ANALYSIS,
    Stage.ANALYSIS: Stage.RUNBOOK,
    Stage.RUNBOOK: Stage.DECISION,
    Stage.DECISION: Stage.COMPLETED,
}


@dataclass(frozen=True)
class RunState:
    incident_id: str
    service: str
    stage: Stage
    data: dict[str, Any]
    trace: list[dict[str, Any]]
    attempts: int
    reason: str | None


class CheckpointStore:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                incident_id TEXT PRIMARY KEY, service TEXT NOT NULL,
                stage TEXT NOT NULL, data TEXT NOT NULL, trace TEXT NOT NULL,
                attempts INTEGER NOT NULL, reason TEXT
            )"""
        )
        self.connection.commit()

    def get(self, incident_id: str) -> RunState | None:
        row = self.connection.execute(
            "SELECT incident_id, service, stage, data, trace, attempts, reason "
            "FROM runs WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            return None
        return RunState(
            row[0], row[1], Stage(row[2]), json.loads(row[3]), json.loads(row[4]), row[5], row[6]
        )

    def advance(
        self,
        incident_id: str,
        service: str,
        step: Callable[[RunState], tuple[str, dict[str, Any], dict[str, Any]]],
        max_attempts: int,
    ) -> RunState:
        # BEGIN IMMEDIATE serializes competing local workers before the read. The stage
        # result and retry count are committed atomically; no partial evidence escapes.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            state = self.get(incident_id)
            if state is None:
                self.connection.execute(
                    "INSERT INTO runs VALUES (?, ?, 'evidence', '{}', '[]', 0, NULL)",
                    (incident_id, service),
                )
                state = self.get(incident_id)
            assert state is not None
            if state.service != service:
                raise ValueError("incident already has a different service")
            if state.stage in ("completed", "escalated"):
                return state
            try:
                next_stage, update, event = step(state)
            except ToolUnavailable as error:
                attempts = state.attempts + 1
                stage = state.stage if error.retryable and attempts < max_attempts else "escalated"
                trace = [
                    *state.trace,
                    {
                        "step": state.stage,
                        "status": "retry" if stage != "escalated" else "escalated",
                        "reason": str(error),
                    },
                ]
                self._save(incident_id, stage, state.data, trace, attempts, str(error))
            else:
                if Stage(next_stage) != NEXT_STAGE[state.stage]:
                    raise ValueError(f"invalid transition: {state.stage} -> {next_stage}")
                data = {**state.data, **update}
                trace = [*state.trace, event]
                self._save(incident_id, next_stage, data, trace, 0, None)
            result = self.get(incident_id)
            assert result is not None
            return result

    def _save(
        self,
        incident_id: str,
        stage: str,
        data: dict[str, Any],
        trace: list[dict[str, Any]],
        attempts: int,
        reason: str | None,
    ) -> None:
        self.connection.execute(
            "UPDATE runs SET stage = ?, data = ?, trace = ?, attempts = ?, reason = ? "
            "WHERE incident_id = ?",
            (stage, json.dumps(data), json.dumps(trace), attempts, reason, incident_id),
        )
