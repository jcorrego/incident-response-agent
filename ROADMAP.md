# Roadmap

Tracking issue: [#1](https://github.com/jcorrego/incident-response-agent/issues/1).
No release tag is claimed by the implementation status below.

## 0.2: durable state and recovery

Implemented in merged [PR #2](https://github.com/jcorrego/incident-response-agent/pull/2):
local SQLite stage checkpoints, read-only idempotent recovery, bounded attempts,
per-attempt elapsed budgets, missing/conflicting evidence escalation and restart
tests. Still local-only, with no global deadline or distributed workflow engine.

## 0.3: MCP integration and contract hardening

Implemented in merged [PR #3](https://github.com/jcorrego/incident-response-agent/pull/3):
three real read-only stdio MCP services, source references across process
boundaries, an explicit client contract, hostile-server tests and an SDK-floor CI
check.

Current increment fixes SDK output-schema failures being retried as transport
outages. A malformed first reply must escalate even if a second reply would be
valid. Regression tests cover invalid/missing structured content, an invalid
advertised schema, a valid control and terminal state after resume. See ADR-008.
This finding was in the body of a review on merged PR #3, not in an inline thread.

Remaining before claiming the full 0.3 theme:

- verified identity and scope propagation through a deterministic policy gateway;
- run/tool trace correlation;
- a separate approved mutation-request workflow, with no automatic execution.

## 0.4: operational evaluation and review

Not implemented: versioned incident dataset and quality gates, human review
interface, trace replay, latency/cost budgets and a recorded walkthrough. Existing
unit and hostile-server tests are not a production incident evaluation dataset.

## Publication gate

Human merge approval, local checks, completed CI and review resolution precede
release claims. All fixtures stay synthetic; no employer systems are connected.
