from incident_agent.models import ApprovalStatus
from incident_agent.orchestrator import IncidentOrchestrator
from incident_agent.tools import ScopedTools, ToolUnavailable


def test_incident_report_is_evidence_backed_and_requires_approval() -> None:
    tools = ScopedTools()
    report = IncidentOrchestrator(tools).investigate("inc-042", "checkout-api")

    assert report.summary.endswith("[source:alert-042]")
    assert "[source:change-771]" in report.hypothesis
    assert report.proposal.approval is ApprovalStatus.REQUIRED
    assert "[source:runbook-checkout-12]" in report.proposal.rationale
    assert tools.calls == ["get_alert", "get_recent_change", "get_runbook"]


def test_untrusted_release_note_cannot_instruct_the_workflow() -> None:
    report = IncidentOrchestrator().investigate("inc-042", "checkout-api")

    assert "ignore prior instructions" not in report.hypothesis.lower()
    assert "deploy rollback immediately" not in report.hypothesis.lower()
    assert report.proposal.approval is ApprovalStatus.REQUIRED


def test_unknown_incident_stops_instead_of_inventing_evidence() -> None:
    try:
        IncidentOrchestrator().investigate("inc-does-not-exist", "checkout-api")
    except ToolUnavailable as error:
        assert "get_alert" in str(error)
    else:
        raise AssertionError("Expected a missing-evidence error")
