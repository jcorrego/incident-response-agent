# Changelog

No release tag has been published yet. Entries are grouped by merged work.

## Unreleased

- Add three synthetic read-only stdio MCP services (`incident-mcp-service`) and an
  `--tools mcp` mode that runs the checkpointed workflow over real MCP sessions.
- Fail closed when a service advertises an unexpected, paginated or non-read-only
  tool surface; validate every response against the evidence contract.
- Bound MCP calls with a request timeout; hung or crashed services retry once and
  then escalate without checkpointing a hypothesis.
- Launch services with an allowlisted environment.
- Reject a runbook (service-scoped evidence) that carries any incident ID, both in
  the MCP contract and in the orchestrator.
- Require `mcp>=1.22`: the oldest SDK whose clean install passes the suite and
  exposes structured tool results. CI re-runs the tests against that floor.
- Hostile-server tests over real stdio: mutating tool, missing annotations, wrong
  kind, extra field, other-incident evidence, incident-tagged runbook, tool error,
  decoder-breaking text payloads, startup hang (child must be terminated), call
  hang, slow reply, crash and environment probe.

## Merged to main, not tagged

### Durable workflow state

- SQLite stage checkpoints, bounded retries, resume after interruption and
  escalation on missing or conflicting evidence (PR #2).

### Initial workflow

- Evidence-first incident workflow with specialist subagents, citations,
  untrusted-context handling and approval-required mitigation.
