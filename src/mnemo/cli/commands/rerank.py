"""``mnemo rerank`` — turn the opt-in recall rerank on, off, or just look at it (#406).

    mnemo rerank             # is it on, where does the key come from, what has it done
    mnemo rerank --setup     # consent, key, one test request, then write
    mnemo rerank --off       # provider back to "none", key off the machine

#405 shipped the stage with one way to give it a key: an environment variable
read by the MCP server. Claude Code spawns that server, so the variable
reaches it only when ``claude`` itself was started from the shell that
exported it — and never from an app launched from the Dock. The feature was
therefore unreachable for the person most likely to want it. :mod:`mnemo.core.secrets`
is the second source; this command is what writes it.

Three things this deliberately does not do. It never takes the key as an
argument — that would put it in shell history and in ``ps`` — so it is
``getpass`` or ``--key-stdin``. It never prints it back, in any mode,
``--json`` included. And it will not store a key it has not seen work: one
request goes to the provider first, and a failure writes nothing at all,
because a stored key that does not work is indistinguishable, from every
later report, from the silent fallback this command exists to end.
"""
from __future__ import annotations

import argparse
import sys
import textwrap

from mnemo.cli.parser import command

#: Quoted from the ``recall`` section of ``docs/configuration.md`` — the same
#: sentences the docs make the promise in, so consent here and the documented
#: behaviour cannot drift apart. ``tests/unit/test_cli_rerank.py`` pins them
#: against the file.
WHAT_IT_SENDS = (
    "What leaves the machine when it is on: for each `list_rules_by_topic` "
    "call that carries a `query`, that query and the first 800 characters of "
    "every rule in the topic (link section removed) are posted to "
    "`api.typesafe.ai`. No slug, path, project name or transcript is sent. "
    "Nothing is sent by the per-prompt reflex, by `mnemo recall`, or by any "
    "hook — the MCP server is the stage's only caller."
)

#: The one request ``--setup`` makes. Neither line comes from the vault: the
#: point is to prove the key and the endpoint work, and a check that leaks a
#: real rule to do it has already done the thing consent was asked for.
PROBE_TASK = "Add a retry to an HTTP client."
PROBE_RULE = "Prefer exponential backoff over a fixed sleep between retries."

#: Longer than the stage's own timeout. Four seconds is tuned for a tool call
#: an agent is waiting on; a person running setup would rather wait than be
#: told their key is bad because the first connection was slow.
PROBE_TIMEOUT_S = 15.0


class _ProbeFailed(Exception):
    """The provider was reached and did not answer usefully."""


def _settings():
    from mnemo.core import config as cfg_mod
    from mnemo.core.mcp import rerank as mcp_rerank

    return mcp_rerank.settings(cfg_mod.load_config())


def _source_line(chosen) -> str:
    """Where the key comes from, in words. Never the key."""
    from mnemo.core import secrets
    from mnemo.core.mcp import rerank as mcp_rerank

    source = mcp_rerank.key_source(chosen)
    if source == "env":
        return "env %s" % chosen["keyEnv"]
    if source == "secrets":
        return "secrets file (%s)" % secrets.path()
    return "none"


def _probe(key: str, chosen) -> None:
    """One request, with the module's own client. Raises on every failure."""
    from mnemo.core.mcp import rerank as mcp_rerank

    client = mcp_rerank.typesafe_client(
        key, model=chosen["model"], timeout=PROBE_TIMEOUT_S)
    out = client({"developer_task": PROBE_TASK},
                 {"r0": mcp_rerank.question(PROBE_RULE)})
    value = ((out.get("answers") or {}).get("r0") or {}).get("noul")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise _ProbeFailed(
            "the provider answered, but with no score for the test question — "
            "check `recall.rerank.model` (%s)" % chosen["model"])


def _why_it_failed(exc: Exception) -> str:
    """A failure a person can act on, with nothing of the key in it.

    Only the status code, the transport's own reason and our own message are
    quoted. An arbitrary exception contributes its type and nothing else: the
    key goes into an ``Authorization`` header this process builds, and the
    cheapest way to be sure no rendering of it ever reaches a terminal or a
    log is to never format a foreign exception's text.
    """
    import urllib.error

    if isinstance(exc, _ProbeFailed):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "HTTP %s — the provider rejected the key" % exc.code
        return "HTTP %s from the provider" % exc.code
    if isinstance(exc, urllib.error.URLError):
        return "could not reach the provider (%s)" % (exc.reason,)
    return "the request failed (%s)" % type(exc).__name__


def _read_key(args: argparse.Namespace) -> str:
    import getpass

    if getattr(args, "key_stdin", False):
        return sys.stdin.read().strip()
    return getpass.getpass("API key (not echoed): ").strip()


def _setup(args: argparse.Namespace) -> int:
    from mnemo.core import config as cfg_mod
    from mnemo.core import secrets

    provider = args.provider
    chosen = dict(_settings(), provider=provider)

    # Wrapped here rather than stored wrapped: the constant is the docs'
    # prose, and a test holds the two to the same sentences.
    print(textwrap.fill(WHAT_IT_SENDS, 78))
    print()
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print("Refusing to turn this on without a tty. Re-run with --yes "
                  "if you meant to consent from a script.", file=sys.stderr)
            return 2
        if input("Turn it on? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing written.")
            return 1

    key = _read_key(args)
    if not key:
        print("No key given; nothing written.", file=sys.stderr)
        return 2

    print("Checking the key with one request to the provider…")
    try:
        _probe(key, chosen)
    except Exception as exc:  # noqa: BLE001 — every failure is reported the same way
        print("The key was not stored: %s" % _why_it_failed(exc), file=sys.stderr)
        return 1
    print("  ok — the provider answered.")

    stored = secrets.write(provider, key)
    cfg_path = cfg_mod.set_config_value("recall.rerank.provider", provider)
    print("Key stored in %s (owner-only where the platform has file modes; "
          "Windows has none)." % stored)
    print("recall.rerank.provider = %r in %s" % (provider, cfg_path))
    print("Restart Claude Code so the MCP server picks it up, then "
          "`mnemo rerank` to see what it does.")
    return 0


def _off(_args: argparse.Namespace) -> int:
    import os

    from mnemo.core import config as cfg_mod
    from mnemo.core import secrets
    from mnemo.core.mcp import rerank as mcp_rerank

    cfg_path = cfg_mod.set_config_value("recall.rerank.provider", "none")
    # Every provider, not only the one that was configured: "off" should leave
    # no key on the machine, and a stale entry under a provider nobody set
    # would be read again the moment someone set it.
    dropped = [p for p in mcp_rerank.PROVIDERS if p != "none" and secrets.remove(p)]
    print("recall.rerank.provider = 'none' in %s" % cfg_path)
    if dropped:
        print("Removed the stored key for: %s (%s)" % (", ".join(dropped), secrets.path()))
    else:
        print("No stored key to remove.")
    env = _settings()["keyEnv"]
    if os.environ.get(env):
        print("Note: %s is still set in this shell. The stage is off either "
              "way, but the variable would win again if the provider is set "
              "back." % env)
    return 0


def _report(args: argparse.Namespace) -> int:
    import json

    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.core import secrets
    from mnemo.core.mcp import rerank as mcp_rerank
    from mnemo.core.mcp import rerank_stats

    vault = cli._resolve_vault()
    chosen = _settings()
    source = mcp_rerank.key_source(chosen)
    days = int(getattr(args, "days", rerank_stats.DEFAULT_DAYS))
    summary = rerank_stats.summarize(vault, days=days)

    if getattr(args, "json", False):
        print(json.dumps({
            "provider": chosen["provider"],
            "model": chosen["model"],
            "keyEnv": chosen["keyEnv"],
            # The label only. The key itself has no representation here.
            "keySource": source,
            "secretsPath": str(secrets.path()),
            **summary,
        }, indent=2))
        return 0

    if chosen["provider"] == "none":
        print("rerank: off — recall.rerank.provider is \"none\", "
              "list_rules_by_topic is ordered locally by BM25F.")
    else:
        print("rerank: on — %s, model %s" % (chosen["provider"], chosen["model"]))
    print("  key: %s" % _source_line(chosen))
    if chosen["provider"] != "none" and source == "none":
        print("  → no key anywhere, so every call falls back to the local "
              "order. `mnemo rerank --setup` fixes that.")

    if not summary["calls"]:
        print("  last %d days: no list_rules_by_topic call reached the stage." % days)
    else:
        print("  last %d days: %d calls — %s" % (
            days, summary["calls"],
            ", ".join(rerank_stats.status_terms(summary["by_status"])) or "no status recorded"))
        none = summary["marked_none"]
        print("    %d rules judged, %d marked relevant, %d call%s marked none"
              % (summary["judged"], summary["relevant"], none, "" if none == 1 else "s"))
    if chosen["provider"] == "none":
        print("  turn it on: `mnemo rerank --setup` (docs/configuration.md, "
              "`recall`, says what it sends)")
    return 0


@command("rerank")
def cmd_rerank(args: argparse.Namespace) -> int:
    if getattr(args, "off", False):
        return _off(args)
    if getattr(args, "setup", False):
        return _setup(args)
    return _report(args)
