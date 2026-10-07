"""``tools/measure_native_memory_cut.py`` over synthetic auto-memory
directories, transcripts and vaults shaped like the real ones (#574)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_native_memory_cut as tool  # noqa: E402

TS = "2026-10-01T10:00:00Z"


def _agent(name: str) -> str:
    """The test's ``mirror._agent_from_project_dir``: the last dash part."""
    return name.rsplit("-", 1)[-1]


def _index(names, extra=()):
    return "# Memory Index\n" + "".join("- [%s](%s.md) — hook\n" % (n, n) for n in names) + "".join(extra)


def _memdir(root: Path, project: str, index: str, files) -> Path:
    memdir = root / project / "memory"
    memdir.mkdir(parents=True)
    (memdir / "MEMORY.md").write_text(index, encoding="utf-8")
    for name in files:
        (memdir / (name + ".md")).write_text("body of %s\n" % name, encoding="utf-8")
    return memdir


def _page(vault: Path, slug: str, sources, page_type="feedback"):
    d = vault / "shared" / page_type
    d.mkdir(parents=True, exist_ok=True)
    src = "" if sources is None else "sources:\n" + "".join("  - %s\n" % s for s in sources)
    if sources == []:
        src = "sources: []\n"
    (d / (slug + ".md")).write_text("---\nname: %s\nslug: %s\ntype: feedback\n%s---\n\nRule %s.\n"
                                    % (slug, slug, src, slug), encoding="utf-8")


def _load(memdir: Path, lines, total=None):
    content = "\n".join(lines)
    if total is not None:
        content += ("\n\n> WARNING: MEMORY.md is %d lines and 30KB. Only part of it was loaded: %d of %d "
                    "lines were cut off, starting at line %d (\"…\")." % (total, total - len(lines), total,
                                                                         len(lines) + 1))
    return {"type": "attachment", "isSidechain": False, "attachment": {"type": "instructions", "files": [
        {"path": "/Users/you/github/app/CLAUDE.md", "type": "Project", "content": "Use yarn."},
        {"path": str(memdir / "MEMORY.md"), "type": "AutoMem", "content": content}]}}


def _user(text, *, bg=False, sidechain=False, ts=TS):
    record = {"type": "user", "isSidechain": sidechain, "timestamp": ts, "sessionId": "s",
              "message": {"role": "user", "content": text}}
    if bg:
        record["sessionKind"] = "bg"
    return record


def _write(path: str, ts: str):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Write", "id": "t", "input": {"file_path": path, "content": "x"}}]}}


def _transcript(root: Path, project: str, sid: str, records):
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _rows(vault: Path, rows, name="reflex-log.jsonl"):
    path = vault / ".mnemo" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(sid, emitted, ts=TS, h="sha256:1"):
    return {"session_id": sid, "project": "app", "prompt_hash": h, "emitted": emitted,
            "scores": [1.0] * len(emitted), "silence_reason": None, "format": "full", "ts": ts}


# --- the cut ---------------------------------------------------------------------------------

def test_line_limit_cuts_at_200_whole_lines():
    assert tool.loaded_count(["- x"] * 250) == 200
    assert tool.loaded_count(["- x"] * 200) == 200
    assert tool.loaded_count(["- x"] * 12) == 12
    assert tool.loaded_count([]) == 0


def test_char_limit_cuts_whole_lines_counting_newlines_between():
    # The maintainer's index on 2026-10-07: 198 lines join to 24,885
    # characters, the 199th would make 25,010, so 198 load.
    lines = ["a" * 124] * 199 + ["e"] * 53
    lines[0] = "a" * (24885 - 197 - 124 * 197)
    assert len("\n".join(lines[:198])) == 24885
    assert len("\n".join(lines[:199])) == 25010
    assert tool.loaded_count(lines) == 198


def test_chars_are_utf16_units_not_bytes():
    # 12,000 em dashes are 36,000 bytes but 12,000 characters: no cut.
    assert tool.loaded_count(["—" * 6000, "—" * 6000]) == 2
    # An emoji is two UTF-16 units: 12,501 of them overflow alone.
    assert tool.loaded_count(["ok", "\U0001F419" * 12501]) == 1


def test_links_reads_every_relative_md_target():
    lines = ["- [A](a.md) — hook", "- Unblock: [b](./b.md), [c](sub/c.md#part)",
             "- [web](https://x.io/d.md), [abs](/etc/e.md), [up](../f.md), [img](g.png)", "- [A again](a.md)"]
    assert tool.links(lines) == ["a.md", "b.md", "sub/c.md"]


def test_project_cut_counts_entries_past_the_cut_and_unindexed_files(tmp_path):
    names = ["m%03d" % n for n in range(205)]
    # Line 1 is the heading, so entries m199..m204 sit past line 200.
    memdir = _memdir(tmp_path, "-Users-you-github-app", _index(names, ["- [gone](gone.md)\n"]),
                     names + ["loose", "also-loose"])
    (memdir / "sub").mkdir()
    (memdir / "sub" / "nested.md").write_text("x", encoding="utf-8")
    row = tool.project_cut(memdir)
    assert row["lines"] == 207
    assert row["loaded_lines"] == 200 and row["cut_at"] == 201 and row["cut_by"] == "lines"
    assert row["entries"] == 206
    assert row["entries_past_cut"] == 7          # m199..m204 and gone.md
    assert row["files_past_cut"] == 6            # gone.md is a dead link
    assert row["dead_links"] == 1
    assert row["files"] == 208
    assert row["unindexed"] == 3                 # loose, also-loose, sub/nested.md


def test_project_cut_whole_index_has_no_cut(tmp_path):
    memdir = _memdir(tmp_path, "-p-app", _index(["a", "b"]), ["a", "b", "c"])
    row = tool.project_cut(memdir)
    assert row["cut_at"] is None and row["cut_by"] is None
    assert row["entries_past_cut"] == 0 and row["unindexed"] == 1


# --- the transcripts -------------------------------------------------------------------------

def test_loaded_index_strips_the_warning_and_reads_the_total(tmp_path):
    record = _load(tmp_path, ["# Memory Index", "- [a](a.md)"], total=246)
    content = record["attachment"]["files"][1]["content"]
    assert tool.loaded_index(content) == (["# Memory Index", "- [a](a.md)"], 246)
    assert tool.loaded_index("# Memory Index\n- [a](a.md)\n") == (["# Memory Index", "- [a](a.md)"], None)


def test_read_session_kind_and_load(tmp_path):
    memdir = tmp_path / "-p-app" / "memory"
    _transcript(tmp_path, "-p-app", "bg1", [_load(memdir, ["- [a](a.md)"], total=300),
                                             _user("go", bg=True)])
    _transcript(tmp_path, "-p-app", "h1", [_user("sub", bg=True, sidechain=True), _user("hi")])
    bg = tool.read_session(tmp_path / "-p-app" / "bg1.jsonl")
    assert bg["kind"] == "bg"
    assert bg["memory"] == {"memdir": str(memdir), "lines": ["- [a](a.md)"], "total": 300}
    human = tool.read_session(tmp_path / "-p-app" / "h1.jsonl")
    assert human["kind"] == "human" and human["memory"] is None


def test_memory_writes_keeps_the_first_time(tmp_path):
    _transcript(tmp_path, "-p-app", "s", [_user("hi"), _write("/x/memory/a.md", "2026-10-01T09:00:00Z"),
                                           _write("/x/memory/a.md", "2026-10-01T11:00:00Z"),
                                           _write("/x/src/b.py", TS)])
    assert tool.memory_writes(tmp_path / "-p-app" / "s.jsonl") == {"/x/memory/a.md": "2026-10-01T09:00:00Z"}


# --- the mapping -----------------------------------------------------------------------------

def test_memory_sources_reads_vault_relative_and_absolute_paths(tmp_path):
    vault = tmp_path / "vault"
    sources = ["bots/app/memory/a.md", str(vault / "bots" / "lib" / "memory" / "sub" / "b.md"),
               "bots/app/briefings/sessions/s.md", "bots/app/memory/MEMORY.md", "bots/app/memory/a.md"]
    assert tool.memory_sources(sources, vault) == [("app", "a.md"), ("lib", "sub/b.md")]


def test_vault_sources_keys_pages_by_slug(tmp_path):
    _page(tmp_path, "app__a", ["bots/app/memory/a.md"])
    _page(tmp_path, "app__bare", [])
    _page(tmp_path, "app__ref", ["bots/app/briefings/sessions/s.md"], page_type="reference")
    assert tool.vault_sources(tmp_path) == {"app__a": ["bots/app/memory/a.md"], "app__bare": [],
                                            "app__ref": ["bots/app/briefings/sessions/s.md"]}


def _session(memdir: Path, lines, total=None, kind="human"):
    return {"kind": kind, "memory": {"memdir": str(memdir), "lines": lines, "total": total}}


def test_classify_every_status(tmp_path):
    vault = tmp_path / "vault"
    root = tmp_path / "projects"
    names = ["m%03d" % n for n in range(210)]
    memdir = _memdir(root, "-Users-you-github-app", _index(names), names + ["loose"])
    lines = tool.split_lines(_index(names))
    cut = _session(memdir, lines[:200], total=len(lines))
    whole = _session(memdir, ["# Memory Index", "- [m000](m000.md)"])

    def c(sources, session=cut):
        return tool.classify(sources, session, vault, _agent)

    assert c(["bots/app/memory/m000.md"]) == tool.SHOWN
    assert c(["bots/app/memory/m205.md"]) == tool.PAST_CUT
    assert c(["bots/app/memory/loose.md"]) == tool.UNINDEXED
    # The whole index loaded: anything not on it was unlinked at the time,
    # whatever today's file says.
    assert c(["bots/app/memory/m100.md"], whole) == tool.UNINDEXED
    assert c(["bots/app/memory/deleted.md"]) == tool.REMOVED
    assert c(["bots/app/memory/deleted.md"], whole) == tool.REMOVED
    assert c(["bots/lib/memory/m000.md"]) == tool.OTHER_PROJECT
    assert c(["bots/lib/memory/x.md", "bots/app/memory/m001.md"]) == tool.SHOWN
    assert c(["bots/lib/memory/x.md", "bots/app/memory/m205.md"]) == tool.PAST_CUT
    assert c(["bots/app/briefings/sessions/s.md"]) == tool.NOT_MEMORY
    assert c([]) == tool.NO_SOURCE
    assert c(None) == tool.NO_PAGE
    assert c(["bots/app/memory/m000.md"], None) == tool.UNKNOWN
    assert c(["bots/app/memory/m000.md"], {"kind": "human", "memory": None}) == tool.UNKNOWN


def test_self_written_needs_the_write_before_the_row(tmp_path):
    session = {"writes": {"/Users/you/.claude/projects/-p-app/memory/a.md": "2026-10-01T09:00:00Z"}}
    assert tool.self_written(["bots/app/memory/a.md"], session, tmp_path, TS)
    assert not tool.self_written(["bots/app/memory/a.md"], session, tmp_path, "2026-10-01T08:00:00Z")
    assert not tool.self_written(["bots/app/memory/b.md"], session, tmp_path, TS)


def test_model_check(tmp_path):
    names = ["m%03d" % n for n in range(205)]
    memdir = _memdir(tmp_path, "-p-app", _index(names), names)
    lines = tool.split_lines(_index(names))
    assert tool.model_check(_session(memdir, lines[:200], total=206)) == (True, "lines")
    assert tool.model_check(_session(memdir, lines[:199], total=206)) == (False, "chars")
    assert tool.model_check(_session(memdir, lines[:200], total=250)) is None     # the file changed
    assert tool.model_check(_session(memdir, ["# other"], total=206)) is None
    assert tool.model_check({"memory": None}) is None
    small = _memdir(tmp_path, "-p-lib", _index(["a"]), ["a"])
    assert tool.model_check(_session(small, ["# Memory Index", "- [a](a.md) — hook"])) == (True, "none")


# --- the reflex ------------------------------------------------------------------------------

def test_emitting_rows_merges_both_generations_and_the_archive(tmp_path):
    a = _row("s1", ["x"], ts="2026-09-30T00:00:00Z")
    b = _row("s1", ["y"], ts="2026-10-02T00:00:00Z", h="sha256:2")
    old = _row("s0", ["x"], ts="2026-09-28T23:00:00Z")
    silent = dict(_row("s1", [], ts=TS), silence_reason="judge_none_relevant")
    _rows(tmp_path, [old, a], name="full-body-fresh/reflex-rows.jsonl")
    _rows(tmp_path, [a, b], name="reflex-log.jsonl.1")
    _rows(tmp_path, [b, silent], name="reflex-log.jsonl")
    assert tool.emitting_rows(tmp_path) == [a, b]
    assert tool.emitting_rows(tmp_path, since="2026-09-01T00:00:00Z") == [old, a, b]


def test_measure_end_to_end(tmp_path):
    root = tmp_path / "projects"
    vault = tmp_path / "vault"
    names = ["m%03d" % n for n in range(205)]
    app = _memdir(root, "-Users-you-github-app", _index(names), names + ["new"])
    _memdir(root, "-Users-you-github-lib", _index(["l1"]), ["l1"])
    (root / "-Users-you-tmp-empty" / "memory").mkdir(parents=True)

    for slug, src in (("app__shown", "bots/app/memory/m000.md"), ("app__cut", "bots/app/memory/m204.md"),
                      ("app__new", "bots/app/memory/new.md"), ("lib__l1", "bots/lib/memory/l1.md"),
                      ("app__brief", "bots/app/briefings/sessions/s.md")):
        _page(vault, slug, [src])
    _page(vault, "app__bare", [])

    lines = tool.split_lines(_index(names))
    _transcript(root, "-Users-you-github-app", "h1", [
        _load(app, lines[:200], total=len(lines)), _user("fix it"),
        _write(str(app / "new.md"), "2026-10-01T09:30:00Z")])
    _transcript(root, "-Users-you-github-app", "b1", [_load(app, lines[:200], total=len(lines)),
                                                      _user("job", bg=True)])
    _rows(vault, [
        _row("h1", ["app__shown", "app__cut", "app__new", "lib__l1", "app__brief", "app__bare", "app__gone"]),
        _row("h1", ["app__cut"], ts="2026-10-01T10:05:00Z", h="sha256:2"),
        _row("b1", ["app__cut", "app__shown"]),
        _row("lost", ["app__shown"]),
        _row("h1", ["app__cut"], ts="2026-09-20T00:00:00Z"),
    ])
    data = tool.measure(root, vault, agent_of=_agent)

    assert [p["alias"] for p in data["projects"]] == ["project 1", "project 2"]
    assert data["projects"][0]["entries_past_cut"] == 6 and data["projects"][0]["unindexed"] == 1
    assert data["model_check"]["agree"] == 2 and data["model_check"]["agree_by"]["lines"] == 2

    human, bg = data["reflex"]["human"], data["reflex"]["bg"]
    assert human["sessions"] == 2 and human["no_transcript"] == 1 and human["rows"] == 3
    assert human["units"] == 9
    assert human["units_by"] == {"shown": 1, "past_cut": 2, "unindexed": 1, "removed": 0, "other_project": 1,
                                 "unknown": 1, "not_memory": 1, "no_source": 1, "no_page": 1}
    assert human["pairs"] == 8 and human["pairs_by"]["past_cut"] == 1
    assert human["units_hidden_self_written"] == 1          # new.md, written before the row
    assert bg["sessions"] == 1 and bg["units"] == 2
    assert bg["units_by"]["shown"] == 1 and bg["units_by"]["past_cut"] == 1


def test_main_prints_aliases_not_project_names(tmp_path, capsys):
    root = tmp_path / "projects"
    vault = tmp_path / "vault"
    _memdir(root, "-Users-you-github-secret", _index(["a"]), ["a"])
    (vault / ".mnemo").mkdir(parents=True)
    assert tool.main(["--projects-root", str(root), "--vault", str(vault)]) == 0
    text = capsys.readouterr().out
    assert "project 1" in text and "secret" not in text
    assert tool.main(["--projects-root", str(root), "--vault", str(vault), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["projects"][0]["name"] == "project 1"
    assert "secret" not in json.dumps(data)
    tool.main(["--projects-root", str(root), "--vault", str(vault), "--names"])
    assert "secret" in capsys.readouterr().out
