"""mnemo MCP stdio server — JSON-RPC 2.0 over stdin/stdout, stdlib only.

Implements the minimum MCP surface required by Claude Code:

  - ``initialize`` — protocol handshake
  - ``tools/list`` — advertise the three mnemo tools
  - ``tools/call`` — dispatch into :mod:`mnemo.core.mcp.tools`

We hand-roll JSON-RPC instead of pulling the official ``mcp`` SDK because
mnemo's pyproject.toml declares ``dependencies = []`` as a load-bearing
architectural choice (consistent with the hand-rolled frontmatter parser).

Notifications (``id`` absent) are silently consumed — not replied to — per
JSON-RPC 2.0 §4.1. Unknown methods return -32601, unknown tools return -32602.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import IO, Any

from mnemo.core.mcp import access_log as mcp_access_log
from mnemo.core.mcp import counter as mcp_counter
from mnemo.core.mcp import tools as mcp_tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mnemo"
SERVER_VERSION = "0.8.0"

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_rules_by_topic",
        "description": (
            "List mnemo rules tagged with a given topic. Returns slugs sorted "
            "by source_count desc (multi-agent synthesized rules first). "
            "Results are scoped to the current project by default. "
            "Pass scope=\"vault\" to include rules from all projects. "
            "Call this BEFORE writing code when the task matches a known topic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["project", "local-only", "vault"],
                    "description": "Filter scope. Default: project (local rules + universal rules). Use local-only to exclude universal; vault for everything.",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "read_mnemo_rule",
        "description": (
            "Read the full body and frontmatter of a mnemo rule by slug. "
            "Use after list_rules_by_topic to fetch the actual rule content. "
            "Results are scoped to the current project by default. "
            "Pass scope=\"vault\" to read rules from any project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["project", "local-only", "vault"],
                    "description": "Filter scope. Default: project (local rules + universal rules). Use local-only to exclude universal; vault for everything.",
                },
            },
            "required": ["slug"],
        },
    },
    {
        "name": "get_mnemo_topics",
        "description": (
            "Return all topic tags currently known in the mnemo brain. "
            "Results are scoped to the current project by default. "
            "Pass scope=\"vault\" to include topics from all projects. "
            "Fallback for when the SessionStart topic injection is stale."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["project", "local-only", "vault"],
                    "description": "Filter scope. Default: project (local rules + universal rules). Use local-only to exclude universal; vault for everything.",
                },
            },
        },
    },
]


def handle_request(req: dict[str, Any], vault_root: Path | None) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request. Returns ``None`` for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    is_notification = "id" not in req

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": _TOOL_DEFS})
    if method == "tools/call":
        return _handle_tool_call(req_id, req.get("params") or {}, vault_root)

    if is_notification:
        return None
    return _err(req_id, -32601, f"method not found: {method}")


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _handle_tool_call(
    req_id: Any,
    params: dict[str, Any],
    vault_root: Path | None,
) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
    if vault_root is None:
        return _err(req_id, -32603, "vault_root not configured")

    project = mcp_tools._resolve_current_project(vault_root)
    scope = str(args.get("scope", "project"))

    t0 = time.perf_counter()
    result = None
    hit_slugs: list[str] = []
    result_count = 0

    if name == "list_rules_by_topic":
        result = mcp_tools.list_rules_by_topic(
            vault_root, str(args.get("topic", "")),
            scope=scope, project=project,
        )
        result_count = len(result)
        hit_slugs = [r["slug"] for r in result]
    elif name == "read_mnemo_rule":
        result = mcp_tools.read_mnemo_rule(
            vault_root, str(args.get("slug", "")),
            scope=scope, project=project,
        )
        result_count = 1 if result is not None else 0
        hit_slugs = [result["slug"]] if result is not None else []
    elif name == "get_mnemo_topics":
        result = mcp_tools.get_mnemo_topics(
            vault_root,
            scope=scope, project=project,
        )
        result_count = len(result)
    else:
        return _err(req_id, -32602, f"unknown tool: {name}")

    elapsed_ms = (time.perf_counter() - t0) * 1000

    mcp_counter.increment(vault_root)
    try:
        mcp_access_log.record(vault_root, {
            "timestamp": _utc_now_iso(),
            "tool": name,
            "args": {**args, "scope": scope},
            "scope_requested": scope,
            "scope_effective": scope if project is not None else "vault",
            "project": project,
            "result_count": result_count,
            "hit_slugs": hit_slugs,
            "elapsed_ms": round(elapsed_ms, 2),
        })
    except Exception:
        pass

    return _ok(req_id, _text_content(result))


def _text_content(obj: Any) -> dict[str, Any]:
    """Wrap a JSON-serializable payload in the MCP ``content`` envelope."""
    return {"content": [{"type": "text", "text": json.dumps(obj)}]}


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def serve(stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    """Long-running stdio loop. One JSON-RPC message per line, line-delimited."""
    from mnemo.core.config import load_config
    from mnemo.core.paths import vault_root as resolve_vault_root

    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout

    try:
        cfg = load_config()
        vault = resolve_vault_root(cfg)
    except Exception:
        vault = None

    for line in in_stream:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(req, dict):
            continue
        resp = handle_request(req, vault_root=vault)
        if resp is None:
            continue
        out_stream.write(json.dumps(resp) + "\n")
        out_stream.flush()
    return 0
