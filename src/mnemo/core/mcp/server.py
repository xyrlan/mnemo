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
from pathlib import Path
from typing import IO, Any

from mnemo.core.mcp import counter as mcp_counter
from mnemo.core.mcp import tools as mcp_tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mnemo"
SERVER_VERSION = "0.5.0"

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_rules_by_topic",
        "description": (
            "List mnemo rules tagged with a given topic. Returns slugs sorted "
            "by source_count desc (multi-agent synthesized rules first). "
            "Call this BEFORE writing code when the task matches a known topic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "read_mnemo_rule",
        "description": (
            "Read the full body and frontmatter of a mnemo rule by slug. "
            "Use after list_rules_by_topic to fetch the actual rule content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        },
    },
    {
        "name": "get_mnemo_topics",
        "description": (
            "Return all topic tags currently known in the mnemo brain. "
            "Fallback for when the SessionStart topic injection is stale."
        ),
        "inputSchema": {"type": "object", "properties": {}},
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


def _handle_tool_call(
    req_id: Any,
    params: dict[str, Any],
    vault_root: Path | None,
) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
    if vault_root is None:
        return _err(req_id, -32603, "vault_root not configured")

    if name == "list_rules_by_topic":
        result = mcp_tools.list_rules_by_topic(vault_root, str(args.get("topic", "")))
        mcp_counter.increment(vault_root)
        return _ok(req_id, _text_content(result))
    if name == "read_mnemo_rule":
        result = mcp_tools.read_mnemo_rule(vault_root, str(args.get("slug", "")))
        mcp_counter.increment(vault_root)
        return _ok(req_id, _text_content(result))
    if name == "get_mnemo_topics":
        result = mcp_tools.get_mnemo_topics(vault_root)
        mcp_counter.increment(vault_root)
        return _ok(req_id, _text_content(result))

    return _err(req_id, -32602, f"unknown tool: {name}")


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
