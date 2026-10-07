"""Opt-in judge as the reflex's gate: it reads the (prompt, rule) pair (#412).

**Off by default, and the only thing on the prompt path that can leave the
machine.** With ``reflex.judge.provider`` set, every prompt the lexical stage
finds candidates for posts the first 1,200 characters of that prompt —
*what the user typed*, secrets redacted before the cut (#591) — and the first 800 characters of up to
``candidates`` rules to the provider, from inside the ``UserPromptSubmit``
hook. That is a different cost from ``recall.rerank``'s (#405), which only
ever sees a query an agent chose to type into a tool call, and it is why this
stage has its own switch and its own consent rather than riding on that one:
``mnemo rerank --setup`` asks for it separately once the key works (#461), and
``mnemo rerank --reflex on`` asks for it on its own.

Why it exists. Since ``relativeGap`` went to 1.0 (#332) the reflex fires on
roughly every second prompt, and of what it injects about 11% is about the
prompt and 56% is noise; it also drops half the on-point rules sitting in its
own top 3. No BM25F bar and no minimum prompt length fixes that — the score
says a prompt and a rule share vocabulary, never that the rule bears on the
task. A judge reading the pair does: on 300 sampled prompts, at
``injectAt`` 0.4, 209 injections instead of 298, 15% noise instead of 56%,
27% on-point instead of 11%, and 57 of the 64 on-point rules kept against 32.
``tools/measure_reflex_gate.py`` is that measurement and it computes its judge
rows with :func:`question` and :func:`chosen` from here, so the table grades
what ships.

Why 0.4 and not 0.6 (#461). 0.6 cuts the noise from 15% to 10% and keeps 47
of the 64. What decides it is how often an on-point rule reaches the prompt at all,
which ``tools/measure_reflex_reach.py`` measures over the top 10 (#455): of
the prompts holding one, the judge at 0.4 reaches 57.6% under Sonnet's labels
and 34.6% under Fable's where it labelled (Sonnet's elsewhere), against 36.0% / 28.6% at 0.6 and 48.8% / 21.8% for
the lexical gates. 0.4 is the only bar that beats the lexical gates under
both raters; at 0.6 the sign depends on which rater you believe.

**It replaces the accept step; it does not stack on it.** That is what was
measured: the pool is the top ``candidates`` of the *ranking*, including the
prompts the shipped gate silenced for ``term_overlap_fail`` or
``absolute_floor_fail``, and the judge alone decides. The session cap, the
token pre-gate and a missing index are unchanged and cost no request.

Every failure — no key, a timeout, an HTTP error, a malformed answer — is
:func:`ask` returning ``None`` and the hook falling back to the decision the
shipped gates made, silently to the session. A hook must never raise and
never hang, so the whole stage — key lookup, page reads and the request —
runs on a thread this module abandons at ``timeoutSeconds``: the wall is hard
even if the socket's is not, and a row's ``ms`` cannot run past it (#593).

The client, the key and the TLS context are :mod:`mnemo.core.mcp.rerank`'s,
imported rather than copied — one TypeSafe key per machine serves both stages
(same ``TYPESAFE_API_KEY``, same ``~/.mnemo/secrets.json`` entry), and a
second copy of the CA-bundle workaround is a second thing to get wrong.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from mnemo.core.mcp import rerank
from mnemo.core.redact import redact_secrets

PROVIDERS = rerank.PROVIDERS

#: Pinned, never an alias: a calibration measured on one model says nothing
#: about the next one.
DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_KEY_ENV = rerank.DEFAULT_KEY_ENV

#: Two and a half seconds is the whole budget a prompt can spend waiting: the
#: measured round trip from here is ~1 s, and past this the shipped gate's
#: answer is better than a slower one.
DEFAULT_TIMEOUT_S = 2.5

#: How deep into the ranking the pool goes. Three is what was labelled, and
#: what ``reflex-log``'s receipt already keeps.
DEFAULT_CANDIDATES = 3

#: The bar a rule's probability has to clear to be injected. Fitted on the
#: dev two thirds of the sample (the loosest bar keeping 90% of dev's on-point
#: pairs) and read once on the held-out third (``tools/measure_reflex_gate.py``);
#: chosen over 0.6 for reach (``tools/measure_reflex_reach.py``, #461).
DEFAULT_INJECT_AT = 0.4

#: How much of the prompt is sent. The measurement truncated here, and a
#: paste of a whole file is not a better question for having been sent whole.
PROMPT_CHARS = 1200

#: What :func:`ask` can report. ``ok`` includes "asked nothing, because the
#: pool was empty" and "asked, and nothing cleared the bar" — both are
#: answers, not failures.
STATUSES = ("ok", "no_key", "error", "timeout")

#: The silence the hook logs when the judge answered and no rule cleared the
#: bar. Distinct from every gate reason: this one means retrieval found
#: candidates and the judge turned them down.
SILENCE_REASON = "judge_none_relevant"


class Timeout(Exception):
    """The request outlived its deadline and the thread running it was left."""


def question(text: str) -> Dict[str, Any]:
    """The measured wording, verbatim.

    Not to be improved: every number in ``tools/measure_reflex_gate.py``'s
    report was produced by these three sentences, and a better-sounding
    question is an unmeasured one.
    """
    return {
        "type": "noul",
        "instructions": (
            "This stored engineering rule should be shown to the AI coding assistant "
            "before it answers the developer's message in the state, because it bears "
            "directly on what that message asks for. Rule: " + text
        ),
        "criteria": {
            "true": "The rule is about the same problem, component or pitfall the message "
                    "is about and would change what the assistant does",
            "false": "The rule is about something else, is too general to change anything, "
                     "or the message is too short or context-dependent to tell what it is about",
        },
    }


def message(prompt: str) -> Tuple[str, int]:
    """The prompt as it may leave the machine, and how many secrets it lost.

    Whitespace-collapsed, then redacted, then cut — in that order (#591).
    Collapsing first means a pasted diff spends its 1,200 characters on words
    rather than on indentation; redacting before the cut means a token that
    straddles character 1,200 is replaced whole, where cutting first would
    leave a head too short for any pattern to recognise and send it.
    """
    text, redacted = redact_secrets(" ".join((prompt or "").split()))
    return text[:PROMPT_CHARS], redacted


def state(prompt: str) -> Dict[str, str]:
    """What the judge is told the developer asked, and the only user text sent.

    :func:`message`'s text: redacting what it was already handed is a no-op,
    so a caller that redacted first sends the same bytes.
    """
    return {"developer_message": message(prompt)[0]}


def _number(value: Any, fallback: float) -> float:
    """A configured number, or the default — :func:`rerank.settings`'s rule.

    An explicit ``0`` is a value; a string nobody meant must not move a bar.
    """
    if value is None or isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _count(value: Any, fallback: int) -> int:
    """A positive count, or the default. Zero candidates is not a configuration
    of this stage — it is the stage turned off, which ``provider`` is for."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number >= 1 else fallback


def settings(cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The ``reflex.judge`` block with its defaults.

    An unknown provider is ``none``: a typo must not start posting prompts
    somewhere.
    """
    block = ((cfg or {}).get("reflex") or {}).get("judge") or {}
    provider = str(block.get("provider") or "none").lower()
    return {
        "provider": provider if provider in PROVIDERS else "none",
        "model": str(block.get("model") or DEFAULT_MODEL),
        "keyEnv": str(block.get("keyEnv") or DEFAULT_KEY_ENV),
        "timeoutSeconds": _number(block.get("timeoutSeconds"), DEFAULT_TIMEOUT_S),
        "candidates": _count(block.get("candidates"), DEFAULT_CANDIDATES),
        "injectAt": _number(block.get("injectAt"), DEFAULT_INJECT_AT),
    }


def enabled(reflex_cfg: Optional[Mapping[str, Any]]) -> bool:
    """Is the stage configured on, read off the raw ``reflex`` block?

    The hook asks this before importing anything, so a vault with the stage
    off — the default — never pays for this module or for :mod:`ssl`.
    A provider this version does not know still answers ``True`` here and
    ``none`` in :func:`settings`, which is the cheap check being conservative
    rather than a second opinion.
    """
    provider = ((reflex_cfg or {}).get("judge") or {}).get("provider")
    return bool(provider) and str(provider).lower() != "none"


def resolve_key(chosen: Mapping[str, Any]) -> Tuple[Optional[str], str]:
    """The provider's key and where it came from — ``rerank``'s resolution.

    Deliberately the same environment variable and the same secrets entry:
    one TypeSafe key per machine serves the list stage and this one, and
    ``mnemo rerank --setup`` is the only thing that writes it.
    """
    return rerank.resolve_key(chosen)


def key_source(chosen: Mapping[str, Any]) -> str:
    """Which source would answer, without handing the key over."""
    return rerank.key_source(chosen)


def scores(prompt: str, texts: Mapping[str, str], slugs: Sequence[str],
           client: rerank.Client) -> Dict[str, float]:
    """The judge's probability per rule, in one request.

    A rule with no text is not asked about, and a rule the judge skipped is
    left out — absent means "not judged", which the caller reads as "not
    injected" only because nothing below the bar is injected either.
    """
    asked = [s for s in slugs if texts.get(s)]
    if not asked:
        return {}
    out = client(state(prompt),
                 {"r%d" % i: question(texts[s]) for i, s in enumerate(asked)})
    answers = (out or {}).get("answers") or {}
    found: Dict[str, float] = {}
    for i, slug in enumerate(asked):
        value = (answers.get("r%d" % i) or {}).get("noul")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found[slug] = float(value)
    return found


def chosen(judged: Mapping[str, float], at: float) -> List[str]:
    """The rules to inject: at or over the bar, best first, ties by slug.

    The tie-break is the slug so that a report and a hook given the same
    numbers emit the same order — the alternative, dictionary order, is the
    order the pool happened to arrive in.
    """
    return [slug for slug in sorted(judged, key=lambda s: (-float(judged[s]), s))
            if float(judged[slug]) >= at]


def _elapsed_ms(started: float) -> int:
    return int(round((time.time() - started) * 1000))


def _failure(exc: BaseException) -> str:
    """``timeout`` or ``error`` — the two a report can act on differently.

    A timeout says the budget is too small or the provider is slow; anything
    else says the key, the endpoint or the answer is wrong.
    """
    import socket
    import urllib.error

    if isinstance(exc, (Timeout, socket.timeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, socket.timeout):
        return "timeout"
    return "error"


def within(call: Callable[[], Any], timeout: float) -> Any:
    """Run ``call`` and give up hard at ``timeout``, raising :class:`Timeout`.

    ``urllib``'s timeout bounds each socket operation, not the request: a
    server that dribbles a byte every two seconds holds it open forever. This
    hook runs before every prompt the user types, so the wall has to be real.
    The worker is a daemon thread and is simply abandoned — it holds a socket
    or a file read and nothing the caller can see, and the process it would outlive is a
    hook that exits in milliseconds.
    """
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=_run, name="mnemo-reflex-judge", daemon=True)
    worker.start()
    worker.join(max(0.0, float(timeout)))
    if worker.is_alive():
        raise Timeout("the judge did not answer within %.3gs" % timeout)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def page_text(vault_root: Path, *, project: Optional[str] = None) -> Callable[[str], str]:
    """``slug -> the text the judge reads``, the same one the list stage sends.

    :func:`rerank.rule_text` bounds it and drops the link section, so a rule's
    graph edges are neither sent nor judged.
    """
    from mnemo.core.mcp import tools

    def _read(slug: str) -> str:
        page = tools.read_mnemo_rule(Path(vault_root), slug, project=project) or {}
        return rerank.rule_text(page.get("body") or "")

    return _read


def ask(vault_root: Path, *, prompt: str, slugs: Sequence[str],
        chosen_settings: Mapping[str, Any], project: Optional[str] = None,
        client: Optional[rerank.Client] = None,
        read_text: Optional[Callable[[str], str]] = None,
        ) -> Tuple[Optional[List[str]], Dict[str, Any]]:
    """Judge ``slugs`` against ``prompt``; return what to inject and a log row.

    Three outcomes, and the caller must tell them apart:

    - ``([...], info)`` — the judge answered and these rules clear the bar,
      best first.
    - ``([], info)`` — the judge answered and none did, or there was nothing
      to ask about. The hook silences with :data:`SILENCE_REASON`; this is the
      stage doing its job, not failing.
    - ``(None, info)`` — no key, a timeout, an HTTP error, a malformed
      answer. The caller falls back to the decision the shipped gates made,
      exactly as if this stage did not exist.

    ``info`` is the ``judge`` object of the ``reflex-log`` row and holds no
    prompt text: status, how many rules were asked about and injected, the
    wall time in milliseconds, the probability per slug, and ``redacted`` —
    how many secrets were cut out of the prompt before it was sent, a count
    and never the text (#591).
    """
    started = time.time()
    sent, redacted = message(prompt)
    info: Dict[str, Any] = {"status": "ok", "asked": 0, "injected": 0, "ms": 0,
                            "scores": [], "redacted": redacted}
    if not slugs:
        info["ms"] = _elapsed_ms(started)
        return [], info
    timeout = float(chosen_settings.get("timeoutSeconds") or DEFAULT_TIMEOUT_S)
    # The wall is the stage's, not the request's (#593): the key lookup and
    # the page reads ran outside it, so a cold disk made ``ok`` rows of 3.5 s
    # under a 2.5 s budget. ``seen`` is the worker's own scratch, read once
    # when the wall falls — an abandoned worker must never write into the row
    # this function has already returned.
    seen: Dict[str, Any] = {}

    def _stage() -> Optional[Dict[str, float]]:
        asking = client
        if asking is None:
            key, _source = resolve_key(chosen_settings)
            if not key:
                seen["no_key"] = True
                return None
            asking = rerank.typesafe_client(
                key, model=str(chosen_settings.get("model") or DEFAULT_MODEL), timeout=timeout)
        reader = read_text if read_text is not None else page_text(vault_root, project=project)
        texts = {slug: reader(slug) for slug in slugs}
        asked = [slug for slug in slugs if texts.get(slug)]
        seen["asked"] = len(asked)
        if not asked:
            return {}
        return scores(sent, texts, asked, asking)

    try:
        judged = within(_stage, timeout)
    except BaseException as exc:  # noqa: BLE001 — every gap is a fallback
        info["asked"] = int(seen.get("asked") or 0)
        info["status"] = _failure(exc)
        info["ms"] = _elapsed_ms(started)
        return None, info
    info["asked"] = int(seen.get("asked") or 0)
    if seen.get("no_key"):
        info["status"] = "no_key"
        info["ms"] = _elapsed_ms(started)
        return None, info
    if not info["asked"]:
        info["ms"] = _elapsed_ms(started)
        return [], info
    if not judged:
        # Asked, and got back nothing usable: a malformed answer is a failure,
        # not a verdict of "none of these".
        info["status"] = "error"
        info["ms"] = _elapsed_ms(started)
        return None, info
    picks = chosen(judged, float(chosen_settings.get("injectAt", DEFAULT_INJECT_AT)))
    info["scores"] = [[slug, round(float(judged[slug]), 4)]
                      for slug in sorted(judged, key=lambda s: (-float(judged[s]), s))]
    info["injected"] = len(picks)
    info["ms"] = _elapsed_ms(started)
    return picks, info
