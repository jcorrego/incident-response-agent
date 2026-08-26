from __future__ import annotations

from .models import Evidence
from .tools import ScopedTools

INJECTION_PREFIX = "UNTRUSTED_RELEASE_NOTE:"


class EvidenceCollector:
    def collect(self, tools: ScopedTools, incident_id: str) -> list[Evidence]:
        return [tools.get_alert(incident_id), tools.get_recent_change(incident_id)]


class ChangeAnalyst:
    def analyze(self, change: Evidence) -> str:
        # Treat release-note bodies as untrusted data, not executable instructions.
        trusted = change.raw_content.split(INJECTION_PREFIX, maxsplit=1)[0].strip()
        return (
            "Recent deployment is a plausible contributing change because it modified "
            f"gateway-timeout handling: {trusted} {change.citation()}"
        )


class RunbookMatcher:
    def match(self, tools: ScopedTools, service: str) -> Evidence:
        return tools.get_runbook(service)
