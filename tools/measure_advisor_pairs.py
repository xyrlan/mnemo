"""An advised child against a child on the stronger model, over twin pairs run as two arms (#818).

Usage:
    PYTHONPATH=src python3 tools/measure_advisor_pairs.py [--vault ~/mnemo] [--json]
    PYTHONPATH=src python3 tools/measure_advisor_pairs.py --check-prices [--projects ~/.claude/projects]

Read-only: no LLM calls, no network, no writes.

``mnemo dispatch <n> --twins --model sonnet --advisor opus`` runs one issue as
two blind twins: one on ``sonnet`` consulting an ``opus`` advisor (the
**advised** arm), one on ``opus`` alone (the **alone** arm). From #832 they
differ in model, advisor, and the advisor's checkpoints in the advised twin's
prompt — the one place the two prompts differ, each arm's sha256 recorded on
the pair. #818's first four pairs had one prompt for both and no checkpoints,
and no advised twin called its advisor; a pair from before #832 is marked so.
``mnemo twins show`` reads them blind and ``mnemo twins prefer`` records which
the maintainer would merge. This prints, per pair and in total, what the
decision rests on:

- **Quality**: which arm was preferred, or a tie.
- **Cost**: tokens and API-price cost per arm, from each twin's transcript
  (and its subagents'). An ``advisor_message`` iteration is priced at the
  advisor's own rate, apart from the child's: one call re-reads the child's
  whole context uncached, and the record's top-level ``usage`` leaves it out
  (``bg-advisor-flag`` in :mod:`mnemo.core.claude_cli`).
- **Speed**: wall-clock from the child's ``createdAt`` to its first terminal
  state (``firstTerminalAt``), as ``mnemo twins show`` snapshotted it. A twin
  whose transcript holds an API error (``isApiErrorMessage``: a rate limit,
  an overload) is flagged and its pair left out of the speed figures — that
  time measures the account, not the arm.

And how many advisor calls the advised arm made, and their share of its cost:
each call re-reads the whole conversation at the advisor's price, so three
checkpoints are a known multiple of one. A pair whose advised twin never
called its advisor measured the cheaper model alone, so it is reported apart
and kept out of every figure the verdict reads.

**The bar** (:data:`MIN_READ`, :data:`QUALITY_BAR`, :data:`MARGIN`) is #818's
proposal, written down before the first pair was dispatched, and deliberately
not a flag — a bar that can be moved after the results are in is not a bar:

- Quality is the gate: at least six pairs read, and the advised arm preferred
  or tied in at least 70% of them. Below that the result is negative whatever
  cost and speed say.
- Cost and speed decide, each on the **median over pairs of the advised/alone
  ratio** — paired, so an expensive issue weighs no more than a cheap one.
  Positive when the advised arm is better on both, or better on one and no
  more than 15% worse on the other.

All three figures are printed next to the verdict either way, so a result that
misses the bar says on which axis.

**The prices** (:data:`PRICES`) are USD per million tokens, measured rather
than copied: fitted to Claude Code's own ``cost-state`` records
(``modelUsage[model].costUSD``, the last one in a transcript) and checked
against them by ``--check-prices``. On 2026-11-09, over the newest 400
transcripts on the maintainer's machine, the table reproduced Claude Code's
figure to within 0.1% for 18 of 18 ``claude-sonnet-5-5`` rows, 256 of 262
``claude-opus-5-5`` (and 39 of 42 ``[1m]``, priced the same), 338 of 341
``claude-haiku-5-5`` and 13 of 13 ``claude-fable-5-1``. The Opus misses are long
interactive sessions whose rows carry no 5m/1h split of their cache writes, so
the mix cannot be read back; one Haiku miss is a Haiku child with a Sonnet
advisor, which Claude Code prices at 5x the table while its advisor's row
matches to the cent — a pairing this measurement does not run. Cache reads
cost 0.05x the input rate on Sonnet and Opus 5.5 and 0.025x on Fable, not the
0.1x older models charged, which is why the table is fitted, not assumed. A
model the table does not name is reported as unpriced, never priced as
another. Prices are list API prices: a subscription pays in its limits, and
the comparison between arms is what the verdict reads.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: At least this many pairs read before quality can pass.
MIN_READ = 6
#: The advised arm preferred or tied in at least this share of pairs read.
QUALITY_BAR = 0.70
#: How much worse the advised arm may be on one of cost and speed when it is
#: better on the other.
MARGIN = 0.15

#: USD per million tokens: (input, output, cache read, cache write 5m,
#: cache write 1h). See the module docstring for how each was measured.
PRICES: Dict[str, Tuple[float, float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0, 8.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5, 4.0),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
}

#: USD per web search request, whatever the model: two searches on a Haiku
#: row are exactly the $0.02 its ``costUSD`` carries above its tokens.
WEB_SEARCH_USD = 0.01

#: The arms of a pair, as this report names them.
ADVISED, ALONE = "advised", "alone"

#: What :func:`check_prices` accepts as a reproduced figure.
PRICE_TOLERANCE = 0.001


# --- pricing ------------------------------------------------------------------


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _split_writes(written: int, hour: Optional[int]) -> Tuple[int, int]:
    """``(5m, 1h)`` cache writes, the way Claude Code's ``costUSD`` prices them.

    With a breakdown, the hour's share is what it names and the rest is
    priced as five-minute writes (a Sonnet row of 22,859 written, 22,503 of
    them 1h, reproduces only so). Without one — older records — every write
    is priced as a one-hour write, Claude Code's own TTL.
    """
    if hour is None:
        return 0, written
    hour = min(hour, written)
    return written - hour, hour


def tokens_of(entry: Dict[str, Any]) -> Dict[str, int]:
    """One usage entry's tokens, by how they are priced."""
    written = _int(entry.get("cache_creation_input_tokens"))
    split = entry.get("cache_creation")
    five, hour = _split_writes(
        written, _int(split.get("ephemeral_1h_input_tokens"))
        if isinstance(split, dict) else None,
    )
    return {
        "input": _int(entry.get("input_tokens")),
        "output": _int(entry.get("output_tokens")),
        "cache_read": _int(entry.get("cache_read_input_tokens")),
        "cache_write_5m": five,
        "cache_write_1h": hour,
    }


def web_searches(usage: Any) -> int:
    """Web search requests one response made (``usage.server_tool_use``)."""
    tools = usage.get("server_tool_use") if isinstance(usage, dict) else None
    return _int(tools.get("web_search_requests")) if isinstance(tools, dict) else 0


def _price_key(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    base = model.split("[", 1)[0].strip()
    return base if base in PRICES else None


def price(model: Optional[str], tokens: Dict[str, int]) -> Optional[float]:
    """USD for *tokens* at *model*'s rate, or ``None`` when the table has no rate."""
    key = _price_key(model)
    if key is None:
        return None if any(tokens.values()) else 0.0
    p_in, p_out, p_read, p_5m, p_1h = PRICES[key]
    return (tokens["input"] * p_in + tokens["output"] * p_out
            + tokens["cache_read"] * p_read + tokens["cache_write_5m"] * p_5m
            + tokens["cache_write_1h"] * p_1h) / 1_000_000


# --- one twin's transcript ----------------------------------------------------


def _blank() -> Dict[str, Any]:
    return {"tokens": {"input": 0, "output": 0, "cache_read": 0,
                       "cache_write_5m": 0, "cache_write_1h": 0},
            "usd": 0.0, "unpriced": [], "web_searches": 0}


def _add(into: Dict[str, Any], model: Optional[str], entry: Dict[str, Any]) -> None:
    tokens = tokens_of(entry)
    for key, value in tokens.items():
        into["tokens"][key] += value
    usd = price(model, tokens)
    if usd is None:
        if model not in into["unpriced"]:
            into["unpriced"].append(model)
    else:
        into["usd"] += usd


def subagent_transcripts(transcript: Path) -> List[Path]:
    """The transcripts of *transcript*'s subagents: ``<session>/subagents/*.jsonl``."""
    folder = transcript.with_suffix("") / "subagents"
    try:
        return sorted(p for p in folder.glob("*.jsonl") if p.is_file())
    except OSError:
        return []


def read_records(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def measure_twin(transcript: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Tokens, cost, advisor calls and API errors of one twin, or ``None``.

    Reads the twin's transcript and its subagents'. A response is written as
    several assistant records sharing ``message.id`` and its ``usage``, so
    each id is counted once. ``own`` is the child's own model turns priced at
    each record's model; ``advisor`` the ``advisor_message`` iterations,
    priced at the model each names. API errors are counted in the main
    transcript only: a subagent's error does not stop the child.
    """
    if transcript is None or not Path(transcript).is_file():
        return None
    from mnemo.core import claude_cli
    from mnemo.core.sessions import stalls

    own, advisor = _blank(), _blank()
    calls = 0
    errors: List[str] = []
    for index, path in enumerate([Path(transcript), *subagent_transcripts(Path(transcript))]):
        seen = set()
        for record in read_records(path):
            if record.get("type") != "assistant":
                continue
            if stalls.is_api_error(record):
                if index == 0:
                    errors.append(str(record.get("error") or "api_error"))
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            mid = message.get("id")
            if mid is not None:
                if mid in seen:
                    continue
                seen.add(mid)
            usage = message.get("usage")
            mine, theirs = claude_cli.split_usage(usage)
            for entry in mine:
                _add(own, message.get("model"), entry)
            searches = web_searches(usage)
            own["web_searches"] += searches
            own["usd"] += searches * WEB_SEARCH_USD
            for entry in theirs:
                calls += 1
                _add(advisor, entry.get("model"), entry)
    total = sum(own["tokens"].values()) + sum(advisor["tokens"].values())
    usd = None if own["unpriced"] or advisor["unpriced"] else own["usd"] + advisor["usd"]
    return {"own": own, "advisor": advisor, "advisor_calls": calls,
            "tokens": total, "usd": usd, "api_errors": errors}


def names_advisor(text: str) -> bool:
    """Whether a closing report says *advisor* — which tells a reader its arm."""
    return "advisor" in (text or "").lower()


def advisor_share(measured: Optional[Dict[str, Any]]) -> Optional[float]:
    """The advisor's calls as a share of one twin's cost, or ``None`` unpriced."""
    if not measured or measured.get("usd") is None or measured["usd"] <= 0:
        return None
    return measured["advisor"]["usd"] / measured["usd"]


def with_checkpoints(pair: Any) -> bool:
    """Whether the advised twin's prompt carried the advisor's checkpoints (#832).

    Read off the pair line: from #832 each arm's prompt sha256 is recorded,
    and the advised arm's differs by the checkpoints. A pair from before has
    no per-arm sha256 — both twins had the pair's one prompt.
    """
    shas = {pair.arm_of(t.tag)[1] is not None: getattr(t, "prompt_sha256", None)
            for t in pair.started}
    return bool(shas.get(True)) and bool(shas.get(False)) and shas[True] != shas[False]


# --- the pairs ----------------------------------------------------------------


def _arm_label(model: Optional[str], advisor: Optional[str]) -> str:
    return f"{model or 'default'}" + (f" + advisor {advisor}" if advisor else "")


def rows(pairs: Sequence[Any], *, transcript: Any = None, report: Any = None,
         live: Any = None) -> List[Dict[str, Any]]:
    """One row per complete pair of two arms.

    *transcript* maps ``(pair, twin)`` to the twin's transcript path, and
    *report* a transcript to its closing report; *live* maps a tag to
    :func:`mnemo.core.twins.metrics_of` of its job, for a pair never shown.
    The defaults read the jobs directory and the transcripts on disk.
    """
    from mnemo.core import twins

    if transcript is None:
        transcript = lambda pair, twin: twins.transcript_of(pair, twin)  # noqa: E731
    if report is None:
        from mnemo.core.sessions.report_card import closing_report as report

    out = []
    for pair in pairs:
        if not getattr(pair, "has_arms", False) or len(pair.started) != 2:
            continue
        arms: Dict[str, Dict[str, Any]] = {}
        for twin in pair.started:
            model, advisor = pair.arm_of(twin.tag)
            name = ADVISED if advisor else ALONE
            path = transcript(pair, twin)
            measured = measure_twin(path)
            snap = (pair.metrics or {}).get(twin.tag)
            if isinstance(snap, dict) and snap.get("wall_seconds") is not None:
                wall = snap.get("wall_seconds")
            elif live is not None:
                wall = live(twin.tag).get("wall_seconds")
            else:
                wall = twins.metrics_of(twins._state(twin.short_id)).get("wall_seconds")
            arms[name] = {
                "tag": twin.tag, "model": model, "advisor": advisor,
                "label": _arm_label(model, advisor),
                "measured": measured, "wall_seconds": wall,
                "stalled": bool(measured and measured["api_errors"]),
                "report_names_advisor": names_advisor(report(path)) if path else False,
            }
        if set(arms) != {ADVISED, ALONE}:
            continue  # two twins on the same arm: not this measurement's pair
        preferred = ""
        if pair.choice == twins.TIE:
            preferred = twins.TIE
        elif pair.preferred:
            preferred = ADVISED if arms[ADVISED]["tag"] == pair.preferred else ALONE
        calls = (arms[ADVISED]["measured"] or {}).get("advisor_calls")
        arms[ADVISED]["advisor_share"] = advisor_share(arms[ADVISED]["measured"])
        out.append({
            "pair": pair.pair, "issue": pair.issue, "answer": preferred,
            "arms": arms, "advisor_calls": calls,
            "checkpoints": with_checkpoints(pair),
            "never_called": calls == 0,
            "alone_called": bool((arms[ALONE]["measured"] or {}).get("advisor_calls")),
        })
    return out


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b <= 0 or a < 0:
        return None
    return a / b


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    kept = [v for v in values if v is not None]
    return statistics.median(kept) if kept else None


def axis(ratio: Optional[float]) -> str:
    """``better``, ``within`` (no more than :data:`MARGIN` worse) or ``worse``."""
    if ratio is None:
        return "unknown"
    if ratio < 1:
        return "better"
    return "within" if ratio <= 1 + MARGIN else "worse"


def verdict(*, read: int, quality: Optional[float], cost: Optional[float],
            speed: Optional[float]) -> Tuple[str, str]:
    """``(POSITIVE|NEGATIVE|PENDING, why)`` under the bar declared above."""
    if read < MIN_READ:
        return "PENDING", f"{read} of {MIN_READ} pairs read"
    if quality is None or quality < QUALITY_BAR:
        return "NEGATIVE", (f"quality gate missed: {quality:.0%} preferred or tied, "
                            f"under {QUALITY_BAR:.0%}" if quality is not None
                            else "quality gate missed: nothing read")
    c, s = axis(cost), axis(speed)
    if "unknown" in (c, s):
        missing = " and ".join(n for n, a in (("cost", c), ("speed", s)) if a == "unknown")
        return "PENDING", f"no {missing} figure to decide on"
    if (c == "better" or s == "better") and "worse" not in (c, s):
        return "POSITIVE", f"cost {c}, speed {s}"
    return "NEGATIVE", f"cost {c}, speed {s}"


def measure(pair_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The totals and the verdict, over the pairs the advisor was used in."""
    used = [r for r in pair_rows if not r["never_called"]]
    apart = [r for r in pair_rows if r["never_called"]]
    read = [r for r in used if r["answer"]]
    counts = {name: sum(1 for r in read if r["answer"] == name)
              for name in (ADVISED, "tie", ALONE)}
    quality = (counts[ADVISED] + counts["tie"]) / len(read) if read else None

    def _usd(r: Dict[str, Any], arm: str) -> Optional[float]:
        return (r["arms"][arm]["measured"] or {}).get("usd")

    cost_ratios = [_ratio(_usd(r, ADVISED), _usd(r, ALONE)) for r in read]
    timed = [r for r in read if not (r["arms"][ADVISED]["stalled"] or r["arms"][ALONE]["stalled"])]
    speed_ratios = [_ratio(r["arms"][ADVISED]["wall_seconds"], r["arms"][ALONE]["wall_seconds"])
                    for r in timed]
    cost = _median(cost_ratios)
    speed = _median(speed_ratios)
    calls = [r["advisor_calls"] for r in used if r["advisor_calls"] is not None]
    word, why = verdict(read=len(read), quality=quality, cost=cost, speed=speed)
    return {
        "pairs": len(pair_rows), "used": len(used), "never_called": len(apart),
        "read": len(read), "answers": counts, "quality": quality,
        "cost_ratio": cost, "cost_pairs": sum(1 for v in cost_ratios if v is not None),
        "cost_median_usd": {arm: _median([_usd(r, arm) for r in read]) for arm in (ADVISED, ALONE)},
        "speed_ratio": speed, "speed_pairs": sum(1 for v in speed_ratios if v is not None),
        "speed_left_out": len(read) - len(timed),
        "wall_median_seconds": {arm: _median([r["arms"][arm]["wall_seconds"] for r in timed])
                                for arm in (ADVISED, ALONE)},
        "advisor_calls": {"total": sum(calls), "median": _median(calls)},
        # Each call re-reads the conversation at the advisor's price (#832).
        "advisor_share_median": _median([r["arms"][ADVISED]["advisor_share"] for r in used]),
        "checkpoints": sum(1 for r in pair_rows if r["checkpoints"]),
        "alone_called": sum(1 for r in pair_rows if r["alone_called"]),
        # Only where the blind view had nothing to cut it with: from #832 on,
        # `twins.blind_report` takes the account out of both reports, and an
        # advised report is asked to give one.
        "report_names_advisor": sum(1 for r in pair_rows
                                    if r["arms"][ADVISED]["report_names_advisor"]
                                    and not r["checkpoints"]),
        "bar": {"min_read": MIN_READ, "quality": QUALITY_BAR, "margin": MARGIN},
        "verdict": word, "why": why,
    }


# --- the report ---------------------------------------------------------------


def _usd(value: Optional[float]) -> str:
    return "unpriced" if value is None else f"${value:,.2f}"


def _wall(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m{rest:02d}s"


def _pct(ratio: Optional[float]) -> str:
    if ratio is None:
        return "—"
    change = ratio - 1
    return f"{ratio:.2f} ({'+' if change >= 0 else '−'}{abs(change):.0%})"


def _arm_line(name: str, arm: Dict[str, Any], *, checkpoints: bool = False) -> str:
    m = arm["measured"]
    if m is None:
        body = "transcript gone"
    else:
        cost = _usd(m["usd"])
        if arm["advisor"]:
            cost += (f" (own {_usd(m['own']['usd'] if not m['own']['unpriced'] else None)}, "
                     f"advisor {_usd(m['advisor']['usd'] if not m['advisor']['unpriced'] else None)})")
        body = f"{cost}  {m['tokens']:,} tokens"
        if arm["advisor"] or m["advisor_calls"]:
            body += f"  {m['advisor_calls']} advisor call(s)"
            share = advisor_share(m)
            if m["advisor_calls"] and share is not None:
                body += f" = {share:.0%} of its cost"
    flags = []
    if arm["stalled"]:
        flags.append("stalled: " + ", ".join(sorted(set(m["api_errors"]))))
    if arm["advisor"] and arm["report_names_advisor"] and not checkpoints:
        flags.append("its report names the advisor")
    tail = f"  [{'; '.join(flags)}]" if flags else ""
    return f"    {name:<8} {arm['label']:<28} {body}  wall {_wall(arm['wall_seconds'])}{tail}"


def _pair_lines(r: Dict[str, Any]) -> List[str]:
    answer = {ADVISED: "preferred: advised", ALONE: "preferred: alone",
              "tie": "tie", "": "not read yet"}[r["answer"]]
    before = "" if r["checkpoints"] else "  (before #832: one prompt, no checkpoints)"
    lines = [f"  {r['pair']}  #{r['issue']}  {answer}{before}"]
    for name in (ADVISED, ALONE):
        lines.append(_arm_line(name, r["arms"][name], checkpoints=r["checkpoints"]))
    if r["alone_called"]:
        lines.append("    the alone twin called an advisor too — its arm was not alone")
    return lines


def format_report(report: Dict[str, Any], pair_rows: Sequence[Dict[str, Any]]) -> str:
    lines = [
        f"pairs: {report['pairs']} with two arms, {report['used']} where the advised "
        f"twin used its advisor, {report['never_called']} where it never did (apart, below)",
        f"bar (declared before the first pair, #818): quality gate {QUALITY_BAR:.0%} "
        f"preferred-or-tied over at least {MIN_READ} read; cost and speed on the median "
        f"advised/alone ratio, positive when better on one and no more than "
        f"{MARGIN:.0%} worse on the other",
        "arms: they differ by model, by advisor, and by the advisor's checkpoints "
        f"in the advised twin's prompt (#832) — {report['checkpoints']} of "
        f"{report['pairs']} pair(s) dispatched with them",
        "",
    ]
    used = [r for r in pair_rows if not r["never_called"]]
    for r in used:
        lines += _pair_lines(r)
    a = report["answers"]
    cm, wm = report["cost_median_usd"], report["wall_median_seconds"]
    lines += [
        "",
        f"quality: {a[ADVISED] + a['tie']} of {report['read']} read preferred or tied "
        f"(advised {a[ADVISED]}, tie {a['tie']}, alone {a[ALONE]}) = "
        + ("—" if report["quality"] is None else f"{report['quality']:.0%}")
        + f" — gate {QUALITY_BAR:.0%}",
        f"cost:    median advised/alone {_pct(report['cost_ratio'])} over "
        f"{report['cost_pairs']} pair(s); median per arm {_usd(cm[ADVISED])} advised, "
        f"{_usd(cm[ALONE])} alone — {axis(report['cost_ratio'])}",
        f"speed:   median advised/alone {_pct(report['speed_ratio'])} over "
        f"{report['speed_pairs']} pair(s)"
        + (f", {report['speed_left_out']} left out (a twin stalled on an API error)"
           if report["speed_left_out"] else "")
        + f"; median per arm {_wall(wm[ADVISED])} advised, {_wall(wm[ALONE])} alone"
        + f" — {axis(report['speed_ratio'])}",
        f"advisor calls (advised arm): {report['advisor_calls']['total']} in all, median "
        + ("—" if report["advisor_calls"]["median"] is None
           else f"{report['advisor_calls']['median']:g}") + " per pair; median share of "
        "the advised twin's cost "
        + ("—" if report["advisor_share_median"] is None
           else f"{report['advisor_share_median']:.0%}"),
    ]
    if report["alone_called"]:
        lines.append(f"  {report['alone_called']} pair(s) whose alone twin called an advisor")
    if report["report_names_advisor"]:
        lines.append(f"  {report['report_names_advisor']} pair(s) whose advised twin's "
                     "closing report names the advisor — that read may not have been blind")
    lines.append(f"verdict: {report['verdict']} — {report['why']}")
    apart = [r for r in pair_rows if r["never_called"]]
    if apart:
        lines += ["", "never called the advisor (measured the advised model alone; in no "
                      "figure above):"]
        for r in apart:
            lines += _pair_lines(r)
    return "\n".join(lines) + "\n"


# --- the price check ----------------------------------------------------------


def _cost_states(projects: Path, *, newest: int) -> Iterable[Dict[str, Any]]:
    """The last ``cost-state`` record of each of the *newest* transcripts."""
    files = [p for p in projects.glob("*/*.jsonl") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:newest]:
        last = None
        for record in read_records(path):
            if record.get("type") == "cost-state":
                last = record
        if last is not None:
            yield last


def check_prices(cost_states: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Per model, how many ``modelUsage`` rows :data:`PRICES` reproduces.

    A row is reproduced when :func:`price` lands within
    :data:`PRICE_TOLERANCE` of Claude Code's own ``costUSD``. Models the
    table does not name are counted as ``unpriced``.
    """
    out: Dict[str, Dict[str, int]] = {}
    for state in cost_states:
        usage = state.get("modelUsage")
        if not isinstance(usage, dict):
            continue
        for model, row in usage.items():
            if not isinstance(row, dict):
                continue
            tally = out.setdefault(model, {"rows": 0, "reproduced": 0, "unpriced": 0})
            tally["rows"] += 1
            written = _int(row.get("cacheCreationInputTokens"))
            five, hour = _split_writes(
                written, _int(row.get("cacheCreationEphemeral1hInputTokens"))
                if "cacheCreationEphemeral1hInputTokens" in row else None,
            )
            tokens = {"input": _int(row.get("inputTokens")),
                      "output": _int(row.get("outputTokens")),
                      "cache_read": _int(row.get("cacheReadInputTokens")),
                      "cache_write_5m": five, "cache_write_1h": hour}
            usd = price(model, tokens)
            if usd is None:
                tally["unpriced"] += 1
                continue
            usd += _int(row.get("webSearchRequests")) * WEB_SEARCH_USD
            actual = row.get("costUSD")
            if isinstance(actual, (int, float)) and abs(usd - actual) <= max(
                    PRICE_TOLERANCE * abs(actual), 1e-6):
                tally["reproduced"] += 1
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--vault", help="vault root (default: the configured one)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check-prices", dest="check_prices", action="store_true",
                        help="check PRICES against Claude Code's own cost-state records")
    parser.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"),
                        help="transcripts to check prices against (default ~/.claude/projects)")
    parser.add_argument("--newest", type=int, default=400,
                        help="how many of the newest transcripts --check-prices reads")
    args = parser.parse_args(argv)

    if args.check_prices:
        found = check_prices(_cost_states(Path(args.projects).expanduser(), newest=args.newest))
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True))
            return 0
        for model, tally in sorted(found.items(), key=lambda kv: -kv[1]["rows"]):
            note = (f"{tally['unpriced']} unpriced (not in the table)" if tally["unpriced"]
                    else f"{tally['reproduced']} reproduced within {PRICE_TOLERANCE:.1%}")
            print(f"{model}: {tally['rows']} row(s), {note}")
        return 0

    from mnemo.core import twins

    vault = Path(args.vault).expanduser() if args.vault else twins.default_vault()
    pair_rows = rows(list(twins.read_pairs(vault).values()))
    report = measure(pair_rows)
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp({"report": report, "pairs": pair_rows}, prov),
                         indent=2, default=str))
    else:
        sys.stdout.write(format_report(report, pair_rows))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
