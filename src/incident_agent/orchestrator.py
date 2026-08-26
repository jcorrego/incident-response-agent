from __future__ import annotations

from .models import ApprovalStatus, IncidentReport, Proposal
from .subagents import ChangeAnalyst, EvidenceCollector, RunbookMatcher
from .tools import ScopedTools


class IncidentOrchestrator:
    """Coordinates specialists; prepares a proposal but deliberately cannot deploy."""

    def __init__(self, tools: ScopedTools | None = None) -> None:
        self.tools = tools or ScopedTools()
        self.collector = EvidenceCollector()
        self.change_analyst = ChangeAnalyst()
        self.runbook_matcher = RunbookMatcher()

    def investigate(self, incident_id: str, service: str) -> IncidentReport:
        trace: list[dict[str, object]] = [{"step": "collect_evidence", "status": "started"}]
        evidence = self.collector.collect(self.tools, incident_id)
        alert, change = evidence
        trace.append({"step": "collect_evidence", "status": "completed", "sources": 2})

        trace.append({"step": "analyze_change", "status": "started"})
        hypothesis = self.change_analyst.analyze(change)
        trace.append({"step": "analyze_change", "status": "completed"})

        trace.append({"step": "match_runbook", "status": "started"})
        runbook = self.runbook_matcher.match(self.tools, service)
        evidence.append(runbook)
        trace.append({"step": "match_runbook", "status": "completed", "sources": 1})

        proposal = Proposal(
            action=(
                "Validate gateway timeout trend and prepare a rollback decision for the "
                "incident commander."
            ),
            rationale=(
                f"Error-rate alert {alert.citation()} follows the payment-routing deployment "
                f"{change.citation()}. The runbook requires incident-commander approval "
                f"before rollback {runbook.citation()}."
            ),
            approval=ApprovalStatus.REQUIRED,
        )
        trace.append({"step": "propose_mitigation", "status": "approval_required"})
        return IncidentReport(
            incident_id=incident_id,
            summary=f"{alert.summary} {alert.citation()}",
            hypothesis=hypothesis,
            evidence=evidence,
            proposal=proposal,
            trace=trace,
        )
