import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from incident_agent.mcp_services import SERVICES, build_server
from incident_agent.mcp_tools import (
    McpScopedTools,
    ToolSurfaceError,
    default_services,
    stdio_service,
)
from incident_agent.orchestrator import IncidentOrchestrator, WorkflowStopped
from incident_agent.subagents import INJECTION_PREFIX
from incident_agent.tools import ScopedTools

HOSTILE = Path(__file__).with_name("hostile_mcp_server.py")


def hostile(mode: str) -> dict:
    services = default_services()
    services["source-control"] = stdio_service(sys.executable, [str(HOSTILE), mode])
    return services


def test_each_service_advertises_one_read_only_non_destructive_tool():
    expected = {
        "observability": "get_alert",
        "source-control": "get_recent_change",
        "runbooks": "get_runbook",
    }
    for service in SERVICES:
        tools = asyncio.run(build_server(service).list_tools())
        assert [tool.name for tool in tools] == [expected[service]]
        hints = tools[0].annotations
        assert hints.readOnlyHint is True and hints.destructiveHint is False


def test_investigation_over_mcp_matches_in_process_report(tmp_path):
    local = IncidentOrchestrator(ScopedTools(), state_path=tmp_path / "local.sqlite")
    expected = local.investigate("inc-042", "checkout-api").to_dict()
    with McpScopedTools() as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "mcp.sqlite")
        report = agent.investigate("inc-042", "checkout-api").to_dict()
        assert tools.calls == ["get_alert", "get_recent_change", "get_runbook"]
    assert report == expected
    # Source references survive the process and protocol boundary unchanged.
    assert [item["source_id"] for item in report["evidence"]] == [
        "alert-042",
        "change-771",
        "runbook-checkout-12",
    ]
    assert "[source:change-771]" in report["hypothesis"]
    assert INJECTION_PREFIX not in report["hypothesis"]
    assert "rollback immediately" not in json.dumps(report["proposal"])
    assert report["proposal"]["approval"] == "required"


def test_missing_resource_over_mcp_escalates_without_retry(tmp_path):
    with McpScopedTools() as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        with pytest.raises(WorkflowStopped, match="get_alert could not find"):
            agent.investigate("unknown", "checkout-api")
        assert tools.calls == ["get_alert"]
    state = agent.get_state("unknown")
    assert state.stage == "escalated" and state.attempts == 1 and state.data == {}


@pytest.mark.parametrize("mode", ["wrong-kind", "extra-field"])
def test_out_of_contract_evidence_escalates_before_any_hypothesis(tmp_path, mode):
    with McpScopedTools(hostile(mode)) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        with pytest.raises(WorkflowStopped, match="outside the contract"):
            agent.investigate("inc-042", "checkout-api")
        assert tools.calls.count("get_recent_change") == 1
    state = agent.get_state("inc-042")
    assert state.stage == "escalated"
    assert state.data == {}


def test_contract_valid_evidence_for_another_incident_is_a_conflict(tmp_path):
    # The schema check passes; the orchestrator's incident binding must still stop it.
    with McpScopedTools(hostile("other-incident")) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        with pytest.raises(WorkflowStopped, match="incident IDs disagree"):
            agent.investigate("inc-042", "checkout-api")
        assert tools.calls == ["get_alert", "get_recent_change"]
    state = agent.get_state("inc-042")
    assert state.stage == "escalated" and state.attempts == 1 and state.data == {}


def test_server_tool_error_escalates_without_retry_or_report(tmp_path):
    with McpScopedTools(hostile("tool-error")) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        with pytest.raises(WorkflowStopped, match="returned an MCP tool error"):
            agent.investigate("inc-042", "checkout-api")
        assert tools.calls.count("get_recent_change") == 1
    state = agent.get_state("inc-042")
    assert state.stage == "escalated" and state.attempts == 1 and state.data == {}


@pytest.mark.parametrize(
    ("mode", "message"),
    [("mutating-tool", "unexpected=\\['rollback_deployment'\\]"), ("not-read-only", "read-only")],
)
def test_service_with_open_or_unlabelled_surface_is_refused_at_startup(mode, message):
    started = time.monotonic()
    with pytest.raises(ToolSurfaceError, match=message), McpScopedTools(hostile(mode)):
        pytest.fail("adapter must not start against a hostile surface")
    assert time.monotonic() - started < 15


def test_hung_service_is_abandoned_within_budget_and_escalates(tmp_path):
    started = time.monotonic()
    with McpScopedTools(hostile("slow"), call_timeout=0.5) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        with pytest.raises(WorkflowStopped, match="get_recent_change"):
            agent.investigate("inc-042", "checkout-api")
    assert time.monotonic() - started < 10
    state = agent.get_state("inc-042")
    assert state.stage == "escalated" and state.attempts == 2 and state.data == {}
    assert [event["status"] for event in state.trace] == ["retry", "escalated"]


def test_one_slow_reply_is_retried_and_late_result_is_not_checkpointed(tmp_path):
    with McpScopedTools(hostile("slow-once"), call_timeout=0.5) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        report = agent.investigate("inc-042", "checkout-api")
        assert tools.calls.count("get_recent_change") == 2
    assert report.trace[0]["status"] == "retry"
    assert report.evidence[1].source_id == "change-771"
    assert agent.get_state("inc-042").attempts == 0


def test_crashed_service_is_a_bounded_transport_failure(tmp_path):
    with McpScopedTools(hostile("crash"), call_timeout=1.0) as tools:
        agent = IncidentOrchestrator(tools, state_path=tmp_path / "runs.sqlite")
        with pytest.raises(WorkflowStopped, match="get_recent_change"):
            agent.investigate("inc-042", "checkout-api")
    state = agent.get_state("inc-042")
    assert state.stage == "escalated" and state.attempts == 2 and state.data == {}


def test_service_processes_do_not_inherit_caller_secrets(monkeypatch):
    monkeypatch.setenv("INCIDENT_DEMO_SECRET", "synthetic-secret-value")
    with McpScopedTools(hostile("env-probe")) as tools:
        names = tools.get_recent_change("inc-042").raw_content.split(",")
    assert "INCIDENT_DEMO_SECRET" not in names
    assert "PATH" in names


def test_cli_runs_and_resumes_over_mcp_services(tmp_path):
    db = tmp_path / "runs.sqlite"
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "incident_agent.cli",
        "--tools",
        "mcp",
        "--state",
        str(db),
        "--output",
        str(output),
    ]
    first = subprocess.run([*command, "--step"], capture_output=True, text=True, check=True)
    assert json.loads(first.stdout)["stage"] == "analysis"
    subprocess.run(command, capture_output=True, text=True, check=True)
    report = json.loads(output.read_text())
    assert report["proposal"]["approval"] == "required"
    assert [event["step"] for event in report["trace"]] == [
        "collect_evidence",
        "analyze_change",
        "match_runbook",
        "propose_mitigation",
    ]
