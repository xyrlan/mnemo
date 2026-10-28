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
import os
import sys
import time
from pathlib import Path
from typing import IO, Any, Mapping

from mnemo._version import resolve_version
from mnemo.core.mcp import access_log as mcp_access_log
from mnemo.core.mcp import rerank as mcp_rerank
from mnemo.core.mcp import session_state as mcp_session_state
from mnemo.core.mcp import tools as mcp_tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mnemo"
SERVER_VERSION = resolve_version()

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_rules_by_topic",
        "description": (
            "List mnemo rules tagged with a given topic. Returns slugs sorted "
            "by source_count desc (multi-agent synthesized rules first). "
            "Pass query=<the user's task or prompt text> to rerank by textual "
            "relevance — strongly recommended, it surfaces the right rule in "
            "large topics. Results are scoped to the current project by "
            "default. Pass scope=\"vault\" to include rules from all "
            "projects. Call this BEFORE writing code when the task matches a "
            "known topic. When items carry a `relevant` flag, read the ones "
            "marked true first; none marked true means nothing in this topic "
            "was judged to be about the task. Returns the first 10 (and every "
            "rule marked relevant); a longer topic answers {\"rules\": [...], "
            "\"more\": N} — pass limit=0 for all of them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "The user's task/prompt text; rules matching it rank first. Omitting it falls back to source_count order.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["project", "local-only", "vault"],
                    "description": "Filter scope. Default: project (local rules + universal rules). Use local-only to exclude universal; vault for everything.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many rules to return, best first. Default 10; rules marked relevant are always all returned. 0 returns the whole topic.",
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


#: #765: how many rules ``list_rules_by_topic`` hands back unless asked for
#: more. Over the 63 calls of a Windows vault's two weeks (the topic held p50
#: 62 / max 244 rules), 10 plus every judge-marked rule still shows 111 of the
#: 141 reads that followed, in p50 453 / max 1,645 bytes of slugs instead of
#: 2,937 / 10,157; ``tools/measure_list_cap.py`` is that measurement.
DEFAULT_LIST_LIMIT = 10


def _limit(value: Any) -> int:
    """The caller's ``limit``, or the default. 0 (or less) means everything."""
    if value is None or isinstance(value, bool):
        return DEFAULT_LIST_LIMIT
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_LIST_LIMIT


def cap(rules: list, limit: int) -> list:
    """The head of ``rules`` a call shows: the first ``limit``, stretched to
    every rule marked ``relevant`` — the rerank puts them first, and a mark is
    the one thing the stage says to read, so a cap never hides one.
    ``limit`` 0 shows everything."""
    if limit <= 0:
        return list(rules)
    marked = sum(1 for r in rules if r.get("relevant") is True)
    return list(rules[:max(limit, marked)])


def _list_payload(shown: list, total: int) -> Any:
    """The bare list when nothing was cut — the shape the tool always had —
    else the head and how many more there are."""
    more = total - len(shown)
    if more <= 0:
        return shown
    return {"rules": shown, "more": more,
            "note": f"{more} more in this topic; limit=0 returns all {total}"}


def handle_request(
    req: dict[str, Any],
    vault_root: Path | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request. Returns ``None`` for notifications.

    ``cfg`` carries the one setting a tool call reads, ``recall.rerank``;
    without it the opt-in rerank stage is off.
    """
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
        return _handle_tool_call(req_id, req.get("params") or {}, vault_root, cfg)

    if is_notification:
        return None
    return _err(req_id, -32601, f"method not found: {method}")


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


#: What Claude Code exports into the server's environment when it spawns it
#: (``mcp-server-session`` in :mod:`mnemo.core.claude_cli`).
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"


def session_id(env: Mapping[str, str] | None = None) -> str | None:
    """The Claude Code session a tool call belongs to, or ``None`` outside one.

    The environment says which session the server was *spawned* for, and one
    server outlives that session: ``/clear`` starts a new one in the same
    process and the environment keeps the old id. So the live id is read per
    call from ``<config dir>/sessions/<pid>.json``, the file Claude Code keeps
    for its own process — ``<config dir>`` being ``$CLAUDE_CONFIG_DIR`` when
    that is set and ``~/.claude`` otherwise (#671) — found through the messaging socket it exported,
    ``/tmp/cc-socks/<pid>.sock``, and trusted only when that file names the
    same socket. Without a socket or a matching file the spawn id is the
    answer, which is right until the first ``/clear``.

    Never raises: an id that cannot be read costs the row a ``null``, never
    the call.
    """
    env = os.environ if env is None else env
    spawned = str(env.get(SESSION_ENV) or "").strip() or None
    socket = str(env.get(SOCKET_ENV) or "").strip()
    pid = Path(socket).stem if socket else ""
    if not pid.isdigit():
        return spawned
    from mnemo.core.sessions import accounts

    config_dir = accounts.current(env) or Path.home() / ".claude"
    try:
        data = json.loads((config_dir / "sessions" / f"{pid}.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return spawned
    if not isinstance(data, dict) or data.get("messagingSocketPath") != socket:
        return spawned
    live = data.get("sessionId")
    return live.strip() if isinstance(live, str) and live.strip() else spawned


def _handle_tool_call(
    req_id: Any,
    params: dict[str, Any],
    vault_root: Path | None,
    cfg: dict[str, Any] | None = None,
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
    total = None
    rerank_info = None

    if name == "list_rules_by_topic":
        query = args.get("query")
        result = mcp_tools.list_rules_by_topic(
            vault_root, str(args.get("topic", "")),
            scope=scope, project=project,
            query=str(query) if query else None,
        )
        # #401: opt-in, off by default, and the only caller — the prompt path
        # and `mnemo recall` never reach a provider.
        result, rerank_info = mcp_rerank.apply(
            vault_root, result, str(query) if query else None,
            project=project, cfg=cfg,
        )
        total = len(result)
        shown = cap(result, _limit(args.get("limit")))
        result = _list_payload(shown, total)
        result_count = len(shown)
        hit_slugs = [r["slug"] for r in shown]
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

    mcp_session_state.increment(vault_root)
    try:
        mcp_access_log.record(vault_root, {
            "timestamp": _utc_now_iso(),
            "tool": name,
            "args": {**args, "scope": scope},
            "scope_requested": scope,
            "scope_effective": scope if project is not None else "vault",
            "project": project,
            # #416: so a list and the reads that followed it join on the
            # session rather than on project + a time window.
            "session_id": session_id(),
            # `result_count`/`hit_slugs` are what the caller was shown;
            # `total` is the topic before the cap (#765).
            "result_count": result_count,
            **({"total": total} if total is not None else {}),
            "hit_slugs": hit_slugs,
            "elapsed_ms": round(elapsed_ms, 2),
            **({"rerank": rerank_info} if rerank_info else {}),
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
        cfg = None
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
        resp = handle_request(req, vault_root=vault, cfg=cfg)
        if resp is None:
            continue
        out_stream.write(json.dumps(resp) + "\n")
        out_stream.flush()
    return 0
