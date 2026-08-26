from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True)
class Evidence:
    source_id: str
    kind: str
    summary: str
    raw_content: str

    def citation(self) -> str:
        return f"[source:{self.source_id}]"


@dataclass(frozen=True)
class Proposal:
    action: str
    rationale: str
    approval: ApprovalStatus


@dataclass(frozen=True)
class IncidentReport:
    incident_id: str
    summary: str
    hypothesis: str
    evidence: list[Evidence]
    proposal: Proposal
    trace: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["proposal"]["approval"] = self.proposal.approval.value
        return payload
