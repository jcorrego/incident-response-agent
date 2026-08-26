# Architecture decisions

## ADR-001: preparation is separate from execution

The orchestrator can prepare an incident report and mitigation proposal but has no state-changing tool. This reduces the blast radius of incorrect reasoning, incomplete context and prompt injection. A real rollback would occur through a separate, authenticated human approval flow.

## ADR-002: specialist subagents have narrow responsibilities

Evidence collection, change analysis and runbook matching are distinct components. This keeps each step inspectable and makes it easier to evaluate failures separately rather than hiding everything inside one large prompt.

## ADR-003: retrieved operational text is untrusted

Release notes, tickets and logs can contain accidental or malicious instructions. The change analyst handles their content as evidence only; it does not execute directives contained in the text. The included fixture demonstrates this with an injection-style release note.

## ADR-004: source citations are required in operational claims

The report uses stable source IDs rather than presenting hypotheses as facts. The small synthetic setup makes citations simple, but the same pattern maps to alert IDs, trace IDs, commits, dashboards and runbook revisions in production.

## ADR-005: tool surfaces stay read-only for the reference workflow

The project intentionally demonstrates a safe integration boundary, not a realistic privileged deployment. Mutating tools should be added only alongside verified identity, policy enforcement, idempotency, audit retention, approval workflows and environment-specific safeguards.
