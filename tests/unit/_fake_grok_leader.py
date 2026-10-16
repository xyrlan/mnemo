"""A stand-in for ``grok agent --leader stdio`` in the unit tests.

Run as a script, it reads JSON-RPC lines on stdin and answers them with the
shapes measured on grok 1.0.60 (``mnemo.core.grok.acp``'s docstring has the
list). Like the real leader it outlives each client: sessions live in the JSON
file ``$FAKE_GROK_STATE`` names, which the tests seed and read back. Every
request it receives is appended to ``state["log"]`` with the environment
variables the test asked about (``state["env_keys"]``).

Knobs in the state file: ``logged_in`` (default true), ``busy`` (a prompt to a
session that is ``working`` is queued and never answered), ``rate_limited``,
``ask_permission`` (send ``session/request_permission`` and an unknown request
before answering a prompt), ``reply`` (the model's last message).
"""
from __future__ import annotations

import json
import os
import sys
import uuid

STATE = os.environ["FAKE_GROK_STATE"]


def load():
    with open(STATE, encoding="utf-8") as fh:
        return json.load(fh)


def save(state):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def out(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def result(rid, value):
    out({"jsonrpc": "2.0", "id": rid, "result": value})


def error(rid, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    out({"jsonrpc": "2.0", "id": rid, "error": err})


def update(sid, update_, prompt_id=None):
    params = {"sessionId": sid, "update": update_}
    if prompt_id:
        params["_meta"] = {"promptId": prompt_id, "totalTokens": 10}
    out({"jsonrpc": "2.0", "method": "session/update", "params": params})


def chunk(sid, text, prompt_id):
    update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}, prompt_id)


def models(current):
    return {"currentModelId": current, "availableModels": [{"modelId": "grok-4.7", "name": "Grok 4.7"}]}


def row(sid, s):
    entry = {"sessionId": sid, "title": s.get("title"), "cwd": s["cwd"], "isWorktree": False,
             "modelId": s.get("model", "grok-4.7"), "yolo": bool(s.get("yolo")) and s["resident"],
             "activity": s["activity"], "resident": s["resident"],
             "lastChangeUnixMs": s.get("changed", 1792155613349), "origin": {"kind": "local"}}
    if s.get("effort"):
        entry["reasoningEffort"] = s["effort"]
    return entry


def handle(msg, state):
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    sessions = state.setdefault("sessions", {})
    if method == "initialize":
        if not (params.get("clientInfo") or {}).get("version"):
            return error(rid, -32602, "Invalid params", "missing field `version`")
        return result(rid, {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}})
    if method == "session/new":
        if not state.get("logged_in", True):
            return error(rid, -32000, "Authentication required", "no auth method id provided")
        meta = params.get("_meta") or {}
        sid = "01a144cc-77f5-7ea3-9060-" + uuid.uuid4().hex[:12]
        model = meta.get("modelId") if meta.get("modelId") == "grok-4.7" else "grok-4.7"
        sessions[sid] = {"cwd": params["cwd"], "resident": True, "activity": "idle",
                         "yolo": bool(meta.get("yoloMode")), "model": model,
                         "effort": meta.get("reasoningEffort")}
        return result(rid, {"sessionId": sid, "models": models(model)})
    if method == "session/load":
        sid = params.get("sessionId")
        if sid not in sessions:
            return error(rid, -32603, "Path not found.", {"detail": "No such file or directory (os error 2)",
                                                          "code": "FS_NOT_FOUND"})
        s = sessions[sid]
        s.update(resident=True, activity="idle", yolo=bool((params.get("_meta") or {}).get("yoloMode")))
        # The real leader replays history as session/update lines of old turns.
        chunk(sid, "an old answer", "old-prompt")
        return result(rid, {"models": models(s.get("model", "grok-4.7"))})
    if method == "session/prompt":
        sid = params.get("sessionId")
        s = sessions.get(sid)
        if s is None or not s["resident"]:
            return error(rid, -32602, "Invalid params", "unknown session id")
        pid = (params.get("_meta") or {}).get("promptId") or str(uuid.uuid4())
        text = params["prompt"][0]["text"]
        s.setdefault("prompts", []).append(text)
        entry = {"id": pid, "version": 0, "kind": "prompt", "text": text, "position": 0}
        out({"jsonrpc": "2.0", "method": "_x.ai/queue/changed", "params": {"sessionId": sid, "entries": [entry]}})
        if s["activity"] == "working" and state.get("busy"):
            return None  # queued behind the running turn; answered after this client is gone
        if state.get("ask_permission"):
            out({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission",
                 "params": {"sessionId": sid, "toolCall": {"toolCallId": "t1"}}})
            out({"jsonrpc": "2.0", "id": 901, "method": "fs/read_text_file", "params": {"path": "/x"}})
        out({"jsonrpc": "2.0", "method": "_x.ai/queue/changed",
             "params": {"sessionId": sid, "entries": [], "runningPromptId": pid}})
        if state.get("rate_limited"):
            return error(rid, -32000, "Rate limited", {"message": "API error (status 429 Too Many Requests)"})
        chunk(sid, "I'll run that ", pid)
        chunk(sid, "now.", pid)
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "run_terminal_command"})
        chunk("01a144cc-other", "someone else's words", pid)
        for part in state.get("reply", "DONE").split(" "):
            chunk(sid, part + " ", pid)
        return result(rid, {"stopReason": "end_turn", "_meta": {"promptId": pid}})
    if method == "_x.ai/sessions/list":
        return result(rid, {"result": {"sessions": [row(k, v) for k, v in sessions.items()]}})
    if method == "_x.ai/session/close":
        s = sessions.get(params.get("sessionId"))
        if s is not None and s["resident"]:
            s.update(resident=False, activity="dormant")
            return result(rid, {"result": {"success": True, "outcome": "closed"}})
        return result(rid, {"result": {"success": True, "outcome": "notResident"}})
    if method == "_x.ai/billing":
        if not state.get("logged_in", True):
            return error(rid, -32000, "Authentication required")
        return result(rid, state.get("billing", {"config": {"creditUsagePercent": 54.0},
                                                 "subscription_tier": "Free"}))
    if rid is not None and method is not None:
        return error(rid, -32601, "Method not found")
    return None


def main():
    state = load()
    state.setdefault("bridges", []).append(
        {k: os.environ.get(k) for k in state.get("env_keys", [])})
    save(state)
    for line in sys.stdin:
        msg = json.loads(line)
        state = load()
        state.setdefault("log", []).append(msg)
        if state.get("exit_on_initialize") and msg.get("method") == "initialize":
            sys.stderr.write("leader: path must be shorter than SUN_LEN\n")
            save(state)
            return 1
        handle(msg, state)
        save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
