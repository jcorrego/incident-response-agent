from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import ApprovalStatus, Evidence, IncidentReport, Proposal
from .state import CheckpointStore, RunState
from .subagents import ChangeAnalyst, EvidenceCollector, RunbookMatcher
from .tools import ScopedTools, ToolUnavailable


class WorkflowStopped(ToolUnavailable):
    """Investigation needs human review; no cause or mitigation was produced."""


class IncidentOrchestrator:
    """Read-only, checkpointed investigation; never deploys or approves a change."""

    def __init__(
        self,
        tools: ScopedTools | None = None,
        *,
        state_path: Path | str = ":memory:",
        max_attempts: int = 2,
        step_timeout: float = 5.0,
    ) -> None:
        if max_attempts < 1 or step_timeout <= 0:
            raise ValueError("max_attempts and step_timeout must be positive")
        self.tools = tools or ScopedTools()
        self.store = CheckpointStore(state_path)
        self.max_attempts = max_attempts
        self.step_timeout = step_timeout
        self.collector = EvidenceCollector()
        self.change_analyst = ChangeAnalyst()
        self.runbook_matcher = RunbookMatcher()

    def get_state(self, incident_id: str) -> RunState | None:
        return self.store.get(incident_id)

    def advance(self, incident_id: str, service: str) -> RunState:
        """Commit at most one stage. Call again to resume after interruption."""
        return self.store.advance(incident_id, service, self._step, self.max_attempts)

    def investigate(self, incident_id: str, service: str) -> IncidentReport:
        while True:
            state = self.advance(incident_id, service)
            if state.stage == "escalated":
                raise WorkflowStopped(state.reason or "investigation stopped")
            if state.stage == "completed":
                report = state.data["report"]
                evidence = [Evidence(**item) for item in report["evidence"]]
                proposal = Proposal(
                    action=report["proposal"]["action"],
                    rationale=report["proposal"]["rationale"],
                    approval=ApprovalStatus(report["proposal"]["approval"]),
                )
                return IncidentReport(
                    incident_id=report["incident_id"],
                    summary=report["summary"],
                    hypothesis=report["hypothesis"],
                    evidence=evidence,
                    proposal=proposal,
                    trace=report["trace"],
                )

    def _step(self, state: RunState) -> tuple[str, dict[str, Any], dict[str, Any]]:
        start = time.monotonic()
        if state.stage == "evidence":
            evidence = self.collector.collect(self.tools, state.incident_id)
            alert, change = evidence
            # Do not infer a relationship from two unrelated or untagged records.
            if (
                alert.kind != "alert"
                or change.kind != "change"
                or alert.incident_id != state.incident_id
                or change.incident_id != state.incident_id
                or alert.source_id == change.source_id
            ):
                raise ToolUnavailable("evidence conflict: source kinds or incident IDs disagree")
            next_stage = "analysis"
            update = {"evidence": [asdict(item) for item in evidence]}
            event = {"step": "collect_evidence", "status": "completed", "sources": 2}
        elif state.stage == "analysis":
            change = Evidence(**state.data["evidence"][1])
            next_stage = "runbook"
            update = {"hypothesis": self.change_analyst.analyze(change)}
            event = {"step": "analyze_change", "status": "completed"}
        elif state.stage == "runbook":
            runbook = self.runbook_matcher.match(self.tools, state.service)
            if runbook.kind != "runbook" or not runbook.source_id:
                raise ToolUnavailable("runbook evidence conflict: invalid source")
            next_stage = "decision"
            update = {"runbook": asdict(runbook)}
            event = {"step": "match_runbook", "status": "completed", "sources": 1}
        elif state.stage == "decision":
            alert, change = [Evidence(**item) for item in state.data["evidence"]]
            runbook = Evidence(**state.data["runbook"])
            proposal = Proposal(
                action="Check alert and change correlation; request human mitigation review.",
                rationale=(
                    f"Alert {alert.citation()} and change {change.citation()} need review. "
                    f"Consult runbook {runbook.citation()}; any mitigation needs human approval."
                ),
                approval=ApprovalStatus.REQUIRED,
            )
            event = {"step": "propose_mitigation", "status": "approval_required"}
            report = IncidentReport(
                incident_id=state.incident_id,
                summary=f"{alert.summary} {alert.citation()}",
                hypothesis=state.data["hypothesis"],
                evidence=[alert, change, runbook],
                proposal=proposal,
                trace=[*state.trace, event],
            )
            next_stage = "completed"
            update = {"report": report.to_dict()}
        else:
            raise ValueError(f"unexpected stage: {state.stage}")
        # Cooperative elapsed-time budget: a blocking read cannot be cancelled here.
        # A late result is discarded rather than checkpointed.
        if time.monotonic() - start > self.step_timeout:
            raise ToolUnavailable(f"{state.stage} exceeded step time budget", retryable=True)
        return next_stage, update, event
