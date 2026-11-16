"""Exercise SDK-side schema validation over real stdio, not a mocked exception."""

import json
import sys
from pathlib import Path

import pytest

from incident_agent.mcp_tools import McpScopedTools, default_services, stdio_service
from incident_agent.orchestrator import IncidentOrchestrator, WorkflowStopped
from incident_agent.tools import ToolUnavailable

HOSTILE = Path(__file__).with_name("hostile_mcp_server.py")


def schema_services(mode, counter):
    services = default_services()
    services["source-control"] = stdio_service(sys.executable, [str(HOSTILE), mode, str(counter)])
    return services


@pytest.mark.parametrize("mode", ["schema-invalid-once", "schema-missing-once", "schema-invalid"])
def test_sdk_schema_violation_stops_on_first_response_and_survives_restart(tmp_path, mode):
    counter = tmp_path / "calls.json"
    database = tmp_path / "runs.sqlite"
    with McpScopedTools(schema_services(mode, counter)) as tools:
        agent = IncidentOrchestrator(tools, state_path=database)
        with pytest.raises(WorkflowStopped, match="output validation failed"):
            agent.investigate("inc-042", "checkout-api")
        assert tools.calls == ["get_alert", "get_recent_change"]

    # The server would send valid evidence on a second call in the *-once cases.
    # A failed contract must never get that second chance.
    assert json.loads(counter.read_text()) == 1
    state = agent.get_state("inc-042")
    assert state is not None and state.reason is not None
    assert state.stage == "escalated"
    assert state.attempts == 1
    assert state.data == {}
    assert [event["status"] for event in state.trace] == ["escalated"]
    assert "SYNTHETIC_RAW_MARKER" not in json.dumps(state.trace)
    assert "SYNTHETIC_RAW_MARKER" not in state.reason

    # Reopening the same run cannot turn the terminal violation into a report.
    resumed = IncidentOrchestrator(state_path=database)
    with pytest.raises(WorkflowStopped, match="output validation failed"):
        resumed.investigate("inc-042", "checkout-api")
    assert resumed.tools.calls == []
    assert resumed.get_state("inc-042") == state


@pytest.mark.parametrize("mode", ["schema-invalid-once", "schema-missing-once"])
def test_hostile_once_server_really_would_return_valid_evidence_on_second_call(tmp_path, mode):
    counter = tmp_path / "calls.json"
    with McpScopedTools(schema_services(mode, counter)) as tools:
        with pytest.raises(ToolUnavailable, match="output validation failed") as caught:
            tools.get_recent_change("inc-042")
        assert caught.value.retryable is False
        # Direct adapter calls prove the hostile fixture recovers. The workflow's
        # persisted escalation, tested above, must prevent this manual second call.
        assert tools.get_recent_change("inc-042").source_id == "change-771"
    assert json.loads(counter.read_text()) == 2


def test_same_schema_server_with_valid_reply_completes(tmp_path):
    counter = tmp_path / "calls.json"
    with McpScopedTools(schema_services("schema-valid", counter)) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        report = agent.investigate("inc-042", "checkout-api")
    assert json.loads(counter.read_text()) == 1
    assert report.proposal.approval.value == "required"
    assert report.evidence[1].source_id == "change-771"
    state = agent.get_state("inc-042")
    assert state is not None and state.stage == "completed"
