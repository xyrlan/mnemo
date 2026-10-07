"""``tools/measure_native_memory_cut.py``: the auto-memory Claude Code's
native loading cuts off, and the reflex injections that reached it (#574),
over synthetic memory directories, transcripts and a synthetic vault."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_native_memory_cut as tool  # noqa: E402

APP = "-Users-you-github-app"
LIB = "-Users-you-github-lib"
AGENTS = {APP: "app", LIB: "lib"}
LOADED_AT = "2026-10-01T10:00:00.000Z"


def agent_of(name: str) -> str:
    return AGENTS.get(name, name)


def _index(names: List[str], *, pad: int = 0) -> str:
    """An index linking ``names`` in order, after ``pad`` unlinked lines."""
    lines = ["# Memory Index"] + [f"- note {i}" for i in range(pad)]
    lines += [f"- [{n}]({n}.md) — about {n}" for n in names]
    return "\n".join(lines) + "\n"


def _memory_dir(projects: Path, project: str, index: Optional[str], files: List[str]) -> Path:
    mem = projects / project / "memory"
    mem.mkdir(parents=True)
    if index is not None:
        (mem / "MEMORY.md").write_text(index, encoding="utf-8")
    for name in files:
        (mem / f"{name}.md").write_text(f"---\nname: {name}\n---\n\nbody\n", encoding="utf-8")
    return mem


def _transcript(projects: Path, project: str, sid: str, records: List[Dict[str, Any]]) -> Path:
    path = projects / project / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _user(*, bg: bool = False, sidechain: bool = False, at: str = "2026-10-01T09:59:59.000Z") -> Dict[str, Any]:
    record: Dict[str, Any] = {"type": "user", "timestamp": at, "cwd": "/Users/you/github/app",
                              "message": {"content": "hi"}}
    if bg:
        record["sessionKind"] = "bg"
    if sidechain:
        record["isSidechain"] = True
    return record


def _load(project: str, content: str, at: str = LOADED_AT) -> Dict[str, Any]:
    """The ``instructions`` attachment Claude Code writes with the loaded index."""
    return {"type": "attachment", "timestamp": at, "attachment": {"type": "instructions", "files": [
        {"path": "/Users/you/CLAUDE.md", "type": "Project", "content": "rules"},
        {"path": f"/Users/you/.claude/projects/{project}/memory/MEMORY.md", "type": "AutoMem",
         "content": content},
    ]}}


def _write(project: str, name: str, at: str) -> Dict[str, Any]:
    return {"type": "assistant", "timestamp": at, "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Write",
         "input": {"file_path": f"/Users/you/.claude/projects/{project}/memory/{name}.md",
                   "content": "x"}}]}}


def _page(vault: Path, rel: str, sources: List[str]) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    src = "".join(f"  - {s}\n" for s in sources)
    path.write_text(f"---\nname: r\ntype: feedback\nsources:\n{src}---\n\nbody\n", encoding="utf-8")


def _vault_memory(vault: Path, agent: str, name: str, *, backfill: bool = False) -> None:
    path = vault / "bots" / agent / "memory" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = "metadata:\n  type: feedback\n  origin: backfill\n" if backfill else "metadata:\n  type: feedback\n"
    path.write_text(f"---\nname: {name}\n{meta}---\n\nbody\n", encoding="utf-8")


# --- the cut -------------------------------------------------------------------------------

def test_the_line_limit_cuts_a_long_index_at_200_lines():
    text = "\n".join(f"- line {i}" for i in range(250)) + "\n"
    assert tool.loaded_lines(text) == 200


def test_an_index_under_both_limits_loads_whole():
    assert tool.loaded_lines("a\nb\nc\n") == 3
    assert tool.loaded_lines("") == 0


def test_the_character_limit_cuts_before_the_line_limit():
    line = "x" * 199  # 200 characters with its newline
    text = "\n".join([line] * 150)
    # 125 lines: 125 * 200 - 1 = 24,999 characters; 126 would be 25,199
    assert tool.loaded_lines(text) == 125


def test_the_character_limit_is_inclusive_and_counts_characters_not_bytes():
    exact = "\n".join(["x" * 9999, "y" * 9999, "z" * 5000])  # 25,000 characters
    assert len(exact) == 25_000
    assert tool.loaded_lines(exact + "\nmore") == 3
    assert tool.loaded_lines(exact + "z\nmore") == 2
    # Three-byte characters: 25,000 characters, 75,000 bytes, still loaded.
    wide = "\n".join(["…" * 9999, "…" * 9999, "…" * 5000])
    assert tool.loaded_lines(wide) == 3


def test_native_index_reports_entries_past_the_cut_unindexed_and_broken(tmp_path):
    names = [f"m{i}" for i in range(5)]
    index = _index(names[:3], pad=197) + "- [gone](gone.md)\n"
    # line 1 header, 197 pads, m0 at 199, m1 at 200, m2 at 201, gone at 202
    mem = _memory_dir(tmp_path, APP, index, names)
    ix = tool.native_index(mem)
    assert ix["lines"] == 202 and ix["loaded_lines"] == 200 and ix["cut"]
    assert ix["before"] == ["m0.md", "m1.md"]
    assert ix["past"] == ["m2.md"]
    assert ix["unindexed"] == ["m3.md", "m4.md"]
    assert ix["broken"] == ["gone.md"]


def test_a_file_linked_both_before_and_past_the_cut_is_before(tmp_path):
    index = _index(["a"], pad=0) + "\n".join(f"- {i}" for i in range(250)) + "\n- [a again](a.md)\n"
    ix = tool.native_index(_memory_dir(tmp_path, APP, index, ["a"]))
    assert ix["before"] == ["a.md"] and ix["past"] == []


def test_a_directory_with_no_index_has_every_file_unindexed(tmp_path):
    ix = tool.native_index(_memory_dir(tmp_path, APP, None, ["a", "b"]))
    assert not ix["has_index"] and ix["loaded_lines"] == 0
    assert ix["unindexed"] == ["a.md", "b.md"]


def test_claude_codes_cut_warning_is_not_an_index_entry():
    loaded = ('- [a](a.md) — x\n\n> WARNING: MEMORY.md is 220 lines and 27.4KB. Only part of it was '
              'loaded: 20 of 220 lines were cut off, starting at line 201 ("- [B](b.md) — y"). Keep it short.')
    assert tool.linked_names(loaded) == {"a.md"}


def test_links_outside_the_directory_and_urls_are_not_topic_files():
    text = "- [a](./a.md) [b](../other/b.md) [c](https://x.y/c.md) [d](d.md#part)\n"
    assert tool.linked_names(text) == {"a.md", "d.md"}


def test_today_state_takes_the_best_placement_across_an_agents_indexes():
    one = {"before": [], "past": ["a.md"], "unindexed": ["b.md"]}
    two = {"before": ["a.md"], "past": [], "unindexed": []}
    assert tool.today_state("a.md", [one, two]) == tool.BEFORE
    assert tool.today_state("a.md", [one]) == tool.PAST
    assert tool.today_state("b.md", [one]) == tool.UNINDEXED
    assert tool.today_state("c.md", [one, two]) == tool.GONE


# --- the vault's mapping -------------------------------------------------------------------

def test_memory_sources_reads_the_pages_sources_frontmatter():
    text = ("---\nname: r\nsources:\n  - bots/app/memory/a.md\n"
            "  - bots/app/briefings/sessions/x.md\n  - bots/app/memory/MEMORY.md\n---\n\nbody\n")
    assert tool.memory_sources(text) == [("app", "a.md")]
    assert tool.memory_sources("---\nname: r\n---\n\nbody\n") == []


def test_rule_sources_maps_an_auto_memory_source_and_says_why_others_do_not(tmp_path):
    vault = tmp_path
    _vault_memory(vault, "app", "a")
    _vault_memory(vault, "app", "made", backfill=True)
    _page(vault, "shared/feedback/r-auto.md", ["bots/app/memory/a.md", "bots/app/briefings/sessions/s.md"])
    _page(vault, "shared/feedback/r-brief.md", ["bots/app/briefings/sessions/s.md"])
    _page(vault, "shared/feedback/r-none.md", [])
    _page(vault, "shared/feedback/r-backfill.md", ["bots/app/memory/made.md"])
    _page(vault, "shared/feedback/r-mixed.md", ["bots/app/memory/made.md", "bots/app/memory/a.md"])
    _page(vault, "shared/feedback/r-lost.md", ["bots/app/memory/lost.md"])
    docs = {s: {"path": f"shared/feedback/{s}.md"}
            for s in ("r-auto", "r-brief", "r-none", "r-backfill", "r-mixed", "r-lost", "r-nopage")}
    cache: Dict[str, Any] = {}
    got = {s: tool.rule_sources(s, docs, vault, cache) for s in list(docs) + ["r-unknown"]}
    assert got["r-auto"] == ("", [("app", "a.md")])
    assert got["r-mixed"] == ("", [("app", "a.md")])
    assert got["r-brief"] == (tool.NO_MEMORY, [])
    assert got["r-none"] == (tool.NO_MEMORY, [])
    assert got["r-backfill"] == (tool.BACKFILL_ONLY, [])
    assert got["r-lost"] == (tool.SOURCE_MISSING, [])
    assert got["r-nopage"] == (tool.NO_PAGE, [])
    assert got["r-unknown"] == (tool.NO_INDEX_ENTRY, [])


# --- the reflex log ------------------------------------------------------------------------

def test_injecting_rows_keeps_emitting_rows_since_live_once_each():
    row = {"session_id": "s1", "prompt_hash": "h", "ts": "2026-10-01T10:00:00Z", "emitted": ["r"]}
    rows = [
        {"session_id": "s0", "ts": "2026-09-28T23:08:59Z", "emitted": ["r"]},  # before LIVE
        {"session_id": "s1", "ts": "2026-10-01T09:00:00Z", "emitted": []},     # silent
        row, dict(row),                                                         # log + archive
        {"session_id": "s2", "ts": "2026-09-30T00:00:00Z", "emitted": ["q"]},
    ]
    got = tool.injecting_rows(rows)
    assert [r["session_id"] for r in got] == ["s2", "s1"]


# --- the transcripts -----------------------------------------------------------------------

def test_read_session_kind_is_the_first_main_thread_user_record(tmp_path):
    bg = _transcript(tmp_path, APP, "s-bg", [_user(sidechain=True), _user(bg=True), _user()])
    human = _transcript(tmp_path, APP, "s-h", [_user(bg=True, sidechain=True), _user()])
    empty = _transcript(tmp_path, APP, "s-e", [_load(APP, "x")])
    assert tool.read_session(bg)["kind"] == tool.BG
    assert tool.read_session(human)["kind"] == tool.HUMAN
    assert tool.read_session(empty)["kind"] == tool.UNKNOWN


def test_read_session_records_loads_and_memory_writes(tmp_path):
    path = _transcript(tmp_path, APP, "s1", [
        _user(), _load(APP, _index(["a"])),
        _write(APP, "b", "2026-10-01T10:05:00.000Z"),
        {"type": "assistant", "timestamp": "2026-10-01T10:06:00.000Z", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/Users/you/code/memory/c.md"}}]}},
    ])
    s = tool.read_session(path)
    assert [(ld["dir"], ld["content"]) for ld in s["loads"]] == [(APP, _index(["a"]))]
    assert [(w["dir"], w["name"]) for w in s["writes"]] == [(APP, "b.md")]


def test_loads_at_picks_the_last_load_written_by_the_injection():
    loads = [{"at": 100.0, "dir": APP, "content": "one"}, {"at": 200.0, "dir": APP, "content": "two"}]
    assert [ld["content"] for ld in tool.loads_at(loads, 150.0)] == ["one"]
    assert [ld["content"] for ld in tool.loads_at(loads, 250.0)] == ["two"]
    assert [ld["content"] for ld in tool.loads_at(loads, 50.0)] == ["one"]
    assert tool.loads_at([], 50.0) == []


def _session(loads: List[Dict[str, Any]], writes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"kind": tool.HUMAN, "loads": loads, "writes": writes or []}


def test_session_reading_each_case():
    loaded = [{"at": 100.0, "dir": APP, "content": _index(["a"])}]
    assert tool.session_reading([("app", "a.md")], _session(loaded), 150.0, agent_of) == tool.SHOWN
    assert tool.session_reading([("app", "b.md")], _session(loaded), 150.0, agent_of) == tool.NOT_SHOWN
    assert tool.session_reading([("lib", "a.md")], _session(loaded), 150.0, agent_of) == tool.OTHER
    # one of a rule's sources shown is enough
    assert tool.session_reading([("app", "b.md"), ("app", "a.md")], _session(loaded), 150.0,
                                agent_of) == tool.SHOWN
    assert tool.session_reading([("app", "b.md")], _session([]), 150.0, agent_of) == tool.NOTHING
    assert tool.session_reading([("app", "b.md")], None, 150.0, agent_of) == tool.NO_TRANSCRIPT


def test_a_memory_the_session_wrote_before_the_injection_was_in_front_of_it():
    loaded = [{"at": 100.0, "dir": APP, "content": _index(["a"])}]
    wrote = [{"at": 120.0, "dir": APP, "name": "b.md"}]
    assert tool.session_reading([("app", "b.md")], _session(loaded, wrote), 150.0, agent_of) == tool.WRITTEN
    assert tool.session_reading([("app", "b.md")], _session(loaded, wrote), 110.0, agent_of) == tool.NOT_SHOWN
    assert tool.session_reading([("app", "b.md")], _session([], wrote), 150.0, agent_of) == tool.WRITTEN


def test_observed_cuts_reads_claude_codes_warning():
    def warned(body: str, line: int) -> Dict[str, Any]:
        return {"at": 1.0, "dir": APP, "content": body + f"\n\n> WARNING: MEMORY.md is 300 lines and 30KB. "
                f"Only part of it was loaded: 9 of 300 lines were cut off, starting at line {line} (\"x\")."}
    sessions = [{"loads": [warned("…" * 10, 120), warned("a\nb", 201), {"at": 1.0, "dir": APP, "content": "c"}]}]
    obs = tool.observed_cuts(sessions)
    assert obs["loads"] == 3 and obs["cut"] == 2
    assert obs["cut_at_line"] == {"120": 1, "201": 1}
    assert obs["char_cuts"] == 1
    assert obs["char_cut_chars"] == [10, 10] and obs["char_cut_max_bytes"] == 30


# --- end to end ----------------------------------------------------------------------------

def _world(tmp_path: Path) -> Dict[str, Path]:
    projects = tmp_path / "projects"
    vault = tmp_path / "vault"
    # app: "seen" before the cut, "late" past it, "loose" unindexed
    _memory_dir(projects, APP, _index(["seen"], pad=198) + "- [late](late.md)\n", ["seen", "late", "loose"])
    _memory_dir(projects, LIB, _index(["libnote"]), ["libnote"])
    for agent, name in (("app", "seen"), ("app", "late"), ("app", "loose"), ("lib", "libnote")):
        _vault_memory(vault, agent, name)
    _vault_memory(vault, "app", "harvested", backfill=True)
    pages = {"r-seen": "app/memory/seen", "r-late": "app/memory/late", "r-loose": "app/memory/loose",
             "r-lib": "lib/memory/libnote", "r-made": "app/memory/harvested",
             "r-brief": "app/briefings/sessions/x"}
    docs = {}
    for slug, src in pages.items():
        _page(vault, f"shared/feedback/{slug}.md", [f"bots/{src}.md"])
        docs[slug] = {"path": f"shared/feedback/{slug}.md"}
    mnemo = vault / ".mnemo"
    mnemo.mkdir(parents=True)
    (mnemo / "reflex-index.json").write_text(json.dumps({"docs": docs}), encoding="utf-8")
    loaded = _index(["seen"], pad=198)
    _transcript(projects, APP, "human-1", [_user(), _load(APP, loaded)])
    _transcript(projects, APP, "bg-1", [_user(bg=True), _load(APP, loaded)])
    rows = [
        {"session_id": "human-1", "project": "app", "prompt_hash": "h1", "ts": "2026-10-01T10:01:00Z",
         "emitted": ["r-seen", "r-late", "r-loose", "r-lib", "r-made", "r-brief"]},
        {"session_id": "bg-1", "project": "app", "prompt_hash": "h2", "ts": "2026-10-01T10:02:00Z",
         "emitted": ["r-late"]},
        {"session_id": "gone-1", "project": "app", "prompt_hash": "h3", "ts": "2026-10-01T10:03:00Z",
         "emitted": ["r-late"]},
        {"session_id": "human-1", "project": "app", "prompt_hash": "h0", "ts": "2026-09-20T10:00:00Z",
         "emitted": ["r-late"]},  # before LIVE
    ]
    (mnemo / "reflex-log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows[1:]), encoding="utf-8")
    archive = mnemo / tool.ARCHIVE
    archive.parent.mkdir(parents=True)
    archive.write_text("".join(json.dumps(r) + "\n" for r in rows[:2]), encoding="utf-8")
    return {"projects": projects, "vault": vault}


def test_gather_maps_each_injection_and_splits_human_from_bg(tmp_path):
    w = _world(tmp_path)
    report = tool.gather(w["vault"], w["projects"], agent_of=agent_of)
    assert report["rows"] == 3
    assert report["sessions"] == {tool.HUMAN: 1, tool.BG: 1, tool.UNKNOWN: 1}
    by = {(i["session_id"], i["slug"]): i for i in report["injections"]}
    human = {slug: by[("human-1", slug)] for _, slug in by if _ == "human-1"}
    assert human["r-seen"]["reading"] == tool.SHOWN and "today" not in human["r-seen"]
    assert (human["r-late"]["reading"], human["r-late"]["today"]) == (tool.NOT_SHOWN, tool.PAST)
    assert (human["r-loose"]["reading"], human["r-loose"]["today"]) == (tool.NOT_SHOWN, tool.UNINDEXED)
    assert (human["r-lib"]["reading"], human["r-lib"]["today"]) == (tool.OTHER, tool.BEFORE)
    assert human["r-made"]["unmapped"] == tool.BACKFILL_ONLY
    assert human["r-brief"]["unmapped"] == tool.NO_MEMORY
    assert by[("bg-1", "r-late")]["kind"] == tool.BG
    assert by[("gone-1", "r-late")]["reading"] == tool.NO_TRANSCRIPT

    s = tool.summarize(report["injections"])
    assert s["all"]["injections"] == 8 and s["all"]["mapped"] == 6
    assert s[tool.HUMAN]["mapped"] == 4
    assert s[tool.HUMAN]["unmapped"] == {tool.BACKFILL_ONLY: 1, tool.NO_MEMORY: 1}
    assert s[tool.HUMAN]["unseen"] == 3
    assert s[tool.HUMAN]["unseen_today"] == {tool.BEFORE: 1, tool.PAST: 1, tool.UNINDEXED: 1, tool.GONE: 0}
    assert s[tool.BG]["unseen"] == 1 and s[tool.BG]["unseen_rules"] == 1
    assert s[tool.UNKNOWN]["unseen"] == 0  # no transcript: not counted as unseen

    native = {p["dir"]: p for p in report["native"]}
    assert native[APP]["past"] == ["late.md"] and native[APP]["unindexed"] == ["loose.md"]


def test_the_report_renders_and_anonymizes(tmp_path, capsys):
    w = _world(tmp_path)
    text = tool.render(tool.gather(w["vault"], w["projects"], agent_of=agent_of), anonymize=True)
    assert "project 1" in text and APP not in text
    assert "native loading could not have shown" in text

    assert tool.main(["--vault", str(w["vault"]), "--projects", str(w["projects"]),
                      "--json", "--anonymize"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "injections" not in data and data["summary"]["all"]["injections"] == 8
    for name in ("app", "lib", "seen", "late", "r-late"):
        assert f'"{name}' not in out and f"{name}.md" not in out
