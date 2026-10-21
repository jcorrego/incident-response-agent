from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from .fixtures import synthetic_alerts, synthetic_changes, synthetic_runbooks
from .models import Evidence

T = TypeVar("T")


class ToolUnavailable(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class EvidenceTools(Protocol):
    """Read-only evidence surface the orchestrator depends on."""

    calls: list[str]

    def get_alert(self, incident_id: str) -> Evidence: ...

    def get_recent_change(self, incident_id: str) -> Evidence: ...

    def get_runbook(self, service: str) -> Evidence: ...


class ScopedTools:
    """Synthetic, read-only tool adapter with bounded retry behavior.

    In a real deployment these methods would be MCP tools protected by verified identity,
    tenant policy and upstream service scopes. No tool here can deploy or mutate state.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._alerts = synthetic_alerts()
        self._changes = synthetic_changes()
        self._runbooks = synthetic_runbooks()

    def get_alert(self, incident_id: str) -> Evidence:
        return self._get("get_alert", lambda: self._alerts[incident_id])

    def get_recent_change(self, incident_id: str) -> Evidence:
        return self._get("get_recent_change", lambda: self._changes[incident_id])

    def get_runbook(self, service: str) -> Evidence:
        return self._get("get_runbook", lambda: self._runbooks[service])

    def _get(self, name: str, operation: Callable[[], T]) -> T:
        self.calls.append(name)
        try:
            return operation()
        except KeyError as error:
            message = f"{name} could not find the requested synthetic resource"
            raise ToolUnavailable(message) from error
