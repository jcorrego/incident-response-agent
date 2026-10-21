"""Synchronous evidence adapter backed by separate stdio MCP services.

The orchestrator stays synchronous and checkpointed. This adapter owns an asyncio loop
on a background thread, one MCP client session per service, and a strict client-side
contract for every response:

* the client calls only allowlisted tool names, one per service;
* at startup, each server must advertise exactly that tool, annotated read-only and
  non-destructive, or the adapter refuses to start (fail closed);
* each call has a request timeout enforced by the MCP client, so a hung server is
  abandoned and reported as a retryable failure instead of blocking the run;
* each response must match the evidence schema and the expected evidence kind, so a
  server cannot relabel a release note as a runbook or attach extra fields;
* server processes receive a minimal environment, not the caller's secrets.

Tool annotations are advisory metadata from the server. The enforceable boundary is
that this client never calls any other tool and validates everything it receives.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import sys
import threading
import warnings
from contextlib import AsyncExitStack
from dataclasses import dataclass, fields
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent

from .models import Evidence
from .tools import ToolUnavailable

SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
EVIDENCE_FIELDS = frozenset(field.name for field in fields(Evidence))
MAX_SUMMARY = 300
MAX_RAW_CONTENT = 4_000
ENV_ALLOWLIST = ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")


@dataclass(frozen=True)
class ToolContract:
    service: str
    tool: str
    argument: str
    kind: str
    # Service-scoped lookups (runbooks) must not claim to belong to an incident.
    incident_scoped: bool = True


CONTRACTS = {
    "get_alert": ToolContract("observability", "get_alert", "incident_id", "alert"),
    "get_recent_change": ToolContract(
        "source-control", "get_recent_change", "incident_id", "change"
    ),
    "get_runbook": ToolContract(
        "runbooks", "get_runbook", "service", "runbook", incident_scoped=False
    ),
}


class ToolSurfaceError(RuntimeError):
    """A service advertised a tool surface outside the read-only contract."""


def stdio_service(command: str, args: list[str]) -> StdioServerParameters:
    """Launch a service with an allowlisted environment instead of inheriting secrets."""
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    return StdioServerParameters(command=command, args=args, env=env)


def default_services() -> dict[str, StdioServerParameters]:
    return {
        service: stdio_service(sys.executable, ["-m", "incident_agent.mcp_services", service])
        for service in ("observability", "source-control", "runbooks")
    }


class McpScopedTools:
    """Same read-only interface as ``ScopedTools``, served over real MCP sessions."""

    def __init__(
        self,
        services: dict[str, StdioServerParameters] | None = None,
        *,
        call_timeout: float = 2.0,
        startup_timeout: float = 20.0,
        cleanup_timeout: float = 15.0,
    ) -> None:
        if call_timeout <= 0 or startup_timeout <= 0 or cleanup_timeout <= 0:
            raise ValueError("timeouts must be positive")
        self.services = services or default_services()
        missing = {contract.service for contract in CONTRACTS.values()} - set(self.services)
        if missing:
            raise ValueError(f"missing MCP service configuration: {sorted(missing)}")
        self.call_timeout = call_timeout
        self.startup_timeout = startup_timeout
        self.cleanup_timeout = cleanup_timeout
        self.cleanup_complete = True
        self.calls: list[str] = []
        self._sessions: dict[str, ClientSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._shutdown: asyncio.Event | None = None
        self._serving: concurrent.futures.Future[None] | None = None
        self._finished = threading.Event()

    def __enter__(self) -> McpScopedTools:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        if self._loop is not None:
            raise RuntimeError("MCP tools already started")
        self._finished.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._serving = asyncio.run_coroutine_threadsafe(self._serve(ready), self._loop)
        try:
            ready.result(self.startup_timeout)
        except concurrent.futures.TimeoutError as error:
            self.close()
            raise ToolSurfaceError("MCP services did not start within budget") from error
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        if self._shutdown is not None:
            loop.call_soon_threadsafe(self._shutdown.set)
        elif self._serving is not None:
            # Startup never finished (for example, a hung initialize). Cancel the
            # owning task; cancelling the concurrent future only requests that.
            self._serving.cancel()
        # Keep the loop running until _serve has unwound every stdio_client. Its
        # exit path closes stdin, waits briefly, then escalates SIGTERM -> SIGKILL,
        # so this wait is bounded by the SDK's termination timeout per service.
        self.cleanup_complete = self._finished.wait(self.cleanup_timeout)
        if not self.cleanup_complete:
            warnings.warn(
                "MCP service cleanup exceeded budget; child processes may remain",
                RuntimeWarning,
                stacklevel=2,
            )
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(10)
        loop.close()
        self._loop = None
        self._thread = None
        self._shutdown = None
        self._serving = None
        self._sessions = {}

    async def _serve(self, ready: concurrent.futures.Future[None]) -> None:
        # stdio_client uses anyio task groups, which must be entered and exited in the
        # same task. One long-lived task owns every session; calls run as sibling tasks.
        try:
            async with AsyncExitStack() as stack:
                for service, params in self.services.items():
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    await _verify_surface(service, session)
                    self._sessions[service] = session
                self._shutdown = asyncio.Event()
                ready.set_result(None)
                await self._shutdown.wait()
        except BaseException as error:
            if not ready.done():
                ready.set_exception(_root_cause(error))
                return
            raise
        finally:
            self._finished.set()

    def get_alert(self, incident_id: str) -> Evidence:
        return self._call("get_alert", incident_id)

    def get_recent_change(self, incident_id: str) -> Evidence:
        return self._call("get_recent_change", incident_id)

    def get_runbook(self, service: str) -> Evidence:
        return self._call("get_runbook", service)

    def _call(self, name: str, key: str) -> Evidence:
        contract = CONTRACTS[name]
        if self._loop is None or contract.service not in self._sessions:
            raise RuntimeError("MCP tools are not started; use them as a context manager")
        self.calls.append(name)
        session = self._sessions[contract.service]
        request = session.call_tool(
            contract.tool,
            {contract.argument: key},
            read_timeout_seconds=timedelta(seconds=self.call_timeout),
        )
        future = asyncio.run_coroutine_threadsafe(request, self._loop)
        try:
            result = future.result(self.call_timeout + 1.0)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise ToolUnavailable(f"{name} exceeded MCP call budget", retryable=True) from error
        except McpError as error:
            message = f"{name} MCP transport failure: {error.error.message}"
            raise ToolUnavailable(message, retryable=True) from error
        except Exception as error:  # closed streams, dead process, broken pipe
            message = f"{name} MCP transport failure: {type(error).__name__}"
            raise ToolUnavailable(message, retryable=True) from error
        return _parse_evidence(contract, result)


def _root_cause(error: BaseException) -> BaseException:
    """anyio wraps startup failures in task-group exception groups; surface the cause."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


async def _verify_surface(service: str, session: ClientSession) -> None:
    listing = await session.list_tools()
    if listing.nextCursor:
        raise ToolSurfaceError(f"{service} paginates its tool list; refusing open surface")
    expected = {c.tool for c in CONTRACTS.values() if c.service == service}
    advertised = {tool.name for tool in listing.tools}
    if advertised != expected:
        unexpected = sorted(advertised - expected)
        missing = sorted(expected - advertised)
        raise ToolSurfaceError(
            f"{service} tool surface mismatch: unexpected={unexpected} missing={missing}"
        )
    for tool in listing.tools:
        hints = tool.annotations
        if hints is None or hints.readOnlyHint is not True or hints.destructiveHint is not False:
            raise ToolSurfaceError(f"{service}.{tool.name} is not declared read-only")


def _payload(result: CallToolResult) -> Any:
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [item for item in result.content if isinstance(item, TextContent)]
    if len(texts) != 1:
        return None
    try:
        return json.loads(texts[0].text)
    except (ValueError, RecursionError):
        # ValueError covers JSONDecodeError and oversized integer literals;
        # RecursionError covers hostile deeply nested payloads.
        return None


def _parse_evidence(contract: ToolContract, result: CallToolResult) -> Evidence:
    name = contract.tool
    if result.isError:
        raise ToolUnavailable(f"{name} returned an MCP tool error")
    payload = _payload(result)
    if payload == {"ok": False, "error": "not_found"}:
        raise ToolUnavailable(f"{name} could not find the requested synthetic resource")
    invalid = ToolUnavailable(f"{name} returned evidence outside the contract")
    if not isinstance(payload, dict) or set(payload) != {"ok", "evidence"}:
        raise invalid
    record = payload["evidence"]
    if payload["ok"] is not True or not isinstance(record, dict):
        raise invalid
    if set(record) != EVIDENCE_FIELDS:
        raise invalid
    text_fields = ("source_id", "kind", "summary", "raw_content")
    if not all(isinstance(record[key], str) for key in text_fields):
        raise invalid
    if record["incident_id"] is not None and not isinstance(record["incident_id"], str):
        raise invalid
    if not contract.incident_scoped and record["incident_id"] is not None:
        raise invalid
    if record["kind"] != contract.kind or not SOURCE_ID.fullmatch(record["source_id"]):
        raise invalid
    if len(record["summary"]) > MAX_SUMMARY or len(record["raw_content"]) > MAX_RAW_CONTENT:
        raise invalid
    return Evidence(**record)
