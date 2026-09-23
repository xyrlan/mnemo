"""``mnemo rerank`` — turn the opt-in recall rerank on, off, or just look at it (#406).

    mnemo rerank                 # is it on, where does the key come from, what has it done
    mnemo rerank --setup         # consent, key, one test request, then write;
                                 # then offers the judge on its own consent
    mnemo rerank --reflex on     # the second stage: the per-prompt judge (#412)
    mnemo rerank --off           # both stages back to "none", key off the machine

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
    "Nothing else is sent on this stage's account: `mnemo recall` and every "
    "hook are silent, and the MCP server is its only caller. The per-prompt "
    "reflex has a switch of its own (`reflex.judge`, `mnemo rerank --reflex "
    "on`), off until you turn it on."
)

#: The second stage's own paragraph, because it sends something the first one
#: never does: the text of the prompt itself. Quoted from the `reflex.judge`
#: subsection of ``docs/configuration.md`` and pinned against it by
#: ``tests/unit/test_cli_rerank_reflex.py``, so consent and the documented
#: behaviour cannot drift apart.
REFLEX_SENDS = (
    "What leaves the machine when the reflex judge is on: the text of each "
    "prompt you type (first 1,200 characters) whenever lexical retrieval finds "
    "candidates, plus the first 800 characters of up to three candidate rules, "
    "to `api.typesafe.ai`, inside the UserPromptSubmit hook. That is your own "
    "prompt, on up to every second prompt, which is why this is a separate "
    "switch from `--setup`. No slug, path, project name or transcript is sent. "
    "Any failure falls back to the lexical gates' own decision."
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
    print()
    _offer_judge(args, provider)
    return 0


def _offer_judge(args: argparse.Namespace, provider: str) -> None:
    """The per-prompt judge, offered in the same run on its own consent (#461).

    Only after the key is stored and proved, so a yes here never needs the
    "run --setup first" refusal ``--reflex on`` has. Its paragraph is
    ``--reflex on``'s, word for word, because what it sends is not what the
    list stage sends: the prompt the user typed. A yes to the list stage is
    never read as a yes to this one — off a tty, or with the key on stdin
    (which has already consumed it), only ``--judge`` turns it on.
    """
    if _judge_settings()["provider"] == provider:
        print("The per-prompt reflex judge is already on "
              "(reflex.judge.provider = %r)." % provider)
        return

    print(textwrap.fill(REFLEX_SENDS, 78))
    print()
    if getattr(args, "judge", False):
        _judge_on(provider)
        return
    if getattr(args, "key_stdin", False) or not sys.stdin.isatty():
        print("The per-prompt judge stays off: without a tty it is only turned "
              "on by --judge. `mnemo rerank --reflex on` turns it on later.")
        return
    try:
        answer = input("Turn on the per-prompt judge too? [Y/n] ").strip().lower()
    except EOFError:
        answer = "n"
    if answer not in ("", "y", "yes"):
        print("The per-prompt judge stays off. `mnemo rerank --reflex on` "
              "turns it on later.")
        return
    _judge_on(provider)


def _judge_on(provider: str) -> None:
    """What ``--reflex on`` writes, and nothing else: the one provider key."""
    from mnemo.core import config as cfg_mod

    cfg_path = cfg_mod.set_config_value("reflex.judge.provider", provider)
    print("reflex.judge.provider = %r in %s" % (provider, cfg_path))
    print("It takes effect on the next prompt — the hook reads the config "
          "each time. `mnemo rerank` shows what it has done.")


def _judge_settings():
    from mnemo.core import config as cfg_mod
    from mnemo.core.reflex import judge as reflex_judge

    return reflex_judge.settings(cfg_mod.load_config())


def _reflex(args: argparse.Namespace) -> int:
    """``--reflex on|off`` — the per-prompt stage, on its own consent (#412).

    It makes no request of its own. The key it needs is the one ``--setup``
    already proved and stored, so a machine with no key is sent there rather
    than asked for a second one: there is only ever one TypeSafe key here.
    """
    from mnemo.core import config as cfg_mod
    from mnemo.core.reflex import judge as reflex_judge

    if args.reflex == "off":
        cfg_path = cfg_mod.set_config_value("reflex.judge.provider", "none")
        print("reflex.judge.provider = 'none' in %s" % cfg_path)
        print("The per-prompt reflex is back to its local gates. "
              "`recall.rerank` is unchanged; `mnemo rerank --off` turns both off.")
        return 0

    print(textwrap.fill(REFLEX_SENDS, 78))
    print()
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print("Refusing to turn this on without a tty. Re-run with --yes "
                  "if you meant to consent from a script.", file=sys.stderr)
            return 2
        if input("Turn it on? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing written.")
            return 1

    chosen = dict(_judge_settings(), provider=args.provider)
    if reflex_judge.key_source(chosen) == "none":
        print("No key resolves for %s, and this command does not ask for one: "
              "run `mnemo rerank --setup` first — it reads the key, tests it "
              "with one request and stores it where both stages find it."
              % args.provider, file=sys.stderr)
        return 2

    _judge_on(args.provider)
    return 0


def _off(_args: argparse.Namespace) -> int:
    import os

    from mnemo.core import config as cfg_mod
    from mnemo.core import secrets
    from mnemo.core.mcp import rerank as mcp_rerank

    cfg_path = cfg_mod.set_config_value("recall.rerank.provider", "none")
    # Both stages: "off" that left the per-prompt one sending prompt text
    # would be the opposite of what this command is for.
    cfg_mod.set_config_value("reflex.judge.provider", "none")
    # Every provider, not only the one that was configured: "off" should leave
    # no key on the machine, and a stale entry under a provider nobody set
    # would be read again the moment someone set it.
    dropped = [p for p in mcp_rerank.PROVIDERS if p != "none" and secrets.remove(p)]
    print("recall.rerank.provider = 'none' in %s" % cfg_path)
    print("reflex.judge.provider = 'none' in %s" % cfg_path)
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


def _ms(value) -> str:
    """A millisecond figure, or a dash when no row carried one."""
    return "—" if value is None else "%d" % round(float(value))


def _report(args: argparse.Namespace) -> int:
    import json

    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.core import secrets
    from mnemo.core.mcp import rerank as mcp_rerank
    from mnemo.core.mcp import rerank_stats
    from mnemo.core.reflex import judge_stats

    vault = cli._resolve_vault()
    chosen = _settings()
    source = mcp_rerank.key_source(chosen)
    days = int(getattr(args, "days", rerank_stats.DEFAULT_DAYS))
    summary = rerank_stats.summarize(vault, days=days)
    reflex_chosen = _judge_settings()
    reflex_summary = judge_stats.summarize(vault, days=days)

    if getattr(args, "json", False):
        print(json.dumps({
            "provider": chosen["provider"],
            "model": chosen["model"],
            "keyEnv": chosen["keyEnv"],
            # The label only. The key itself has no representation here.
            "keySource": source,
            "secretsPath": str(secrets.path()),
            **summary,
            "reflex": {
                "provider": reflex_chosen["provider"],
                "model": reflex_chosen["model"],
                "injectAt": reflex_chosen["injectAt"],
                "candidates": reflex_chosen["candidates"],
                "keySource": mcp_rerank.key_source(reflex_chosen),
                **reflex_summary,
            },
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

    # The second stage, from the reflex log rather than the access log: the
    # two run in different processes on different paths and neither one's
    # health says anything about the other's.
    print()
    if reflex_chosen["provider"] == "none":
        print("reflex judge: off — reflex.judge.provider is \"none\", the "
              "per-prompt gates decide locally.")
    else:
        print("reflex judge: on — %s, model %s, inject at %g over the top %d"
              % (reflex_chosen["provider"], reflex_chosen["model"],
                 reflex_chosen["injectAt"], reflex_chosen["candidates"]))
        print("  key: %s" % _source_line(reflex_chosen))
        if mcp_rerank.key_source(reflex_chosen) == "none":
            print("  → no key anywhere, so every prompt falls back to the "
                  "lexical gates. `mnemo rerank --setup` fixes that.")
    if not reflex_summary["prompts"]:
        print("  last %d days: no prompt reached the judge." % days)
    else:
        print("  last %d days: %d prompts judged — %s" % (
            days, reflex_summary["prompts"],
            ", ".join(judge_stats.status_terms(reflex_summary["by_status"]))
            or "no status recorded"))
        print("    %d rules asked, %d injected; %s ms median, %s ms p90"
              % (reflex_summary["asked"], reflex_summary["injected"],
                 _ms(reflex_summary["median_ms"]), _ms(reflex_summary["p90_ms"])))
    if reflex_chosen["provider"] == "none":
        print("  turn it on: `mnemo rerank --reflex on` (docs/configuration.md, "
              "`reflex.judge`, says what it sends — your own prompt text)")
    return 0


@command("rerank")
def cmd_rerank(args: argparse.Namespace) -> int:
    if getattr(args, "off", False):
        return _off(args)
    if getattr(args, "reflex", None):
        return _reflex(args)
    if getattr(args, "setup", False):
        return _setup(args)
    return _report(args)
