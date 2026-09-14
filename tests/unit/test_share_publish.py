"""``core.share.publish`` — selection, ownership, prune, manifest, staleness.

Every assertion here is about counts, paths, ownership and bytes-on-disk
equality, never about the portable page's text: that layout belongs to the
``format`` piece (see ``_share_stub`` for why).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.unit import _share_stub
from tests.unit._export_fixtures import write_rule

_share_stub.ensure_format_module()

from mnemo.core.share import format as share_format  # noqa: E402
from mnemo.core.share import publish as pub  # noqa: E402

SHARE_DIR = share_format.SHARE_DIR
TODAY = "2026-09-13"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    return root


def _publish(vault: Path, repo: Path, **kw) -> pub.PublishReport:
    kw.setdefault("today", TODAY)
    return pub.run_publish(vault, project="app", repo_root=repo, **kw)


def _tree(repo: Path) -> set[str]:
    root = repo / SHARE_DIR
    if not root.is_dir():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*.md")}


def _manifest(vault: Path) -> dict:
    return json.loads((vault / ".mnemo" / "share" / "app.json").read_text(encoding="utf-8"))


def _write_foreign(repo: Path, *, slug: str, page_type: str = "feedback", vault: str = "feedfacefeedface") -> Path:
    """A portable page another vault published, built through the consumed
    signature so its shape is whatever ``format`` says it is."""
    text = share_format.to_portable(
        "---\n"
        f"name: 'Foreign {slug}'\n"
        f"slug: {slug}\n"
        f"description: 'theirs'\n"
        f"type: {page_type}\n"
        "stability: stable\n"
        "sources:\n"
        "  - bots/app/briefings/sessions/theirs.md\n"
        "tags:\n"
        "  - auto-promoted\n"
        "---\n\nTheir rule.\n",
        vault=vault, project="app", today="2026-01-01",
    )
    assert text is not None
    path = repo / SHARE_DIR / page_type / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- selection ---------------------------------------------------------------

def test_first_publish_writes_project_scope_and_manifest(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="use-yarn", quote="always yarn")
    write_rule(tmp_vault, slug="uni", projects=("x", "y"))            # universal → in
    write_rule(tmp_vault, slug="elsewhere", projects=("other",))      # other project → out
    write_rule(tmp_vault, slug="draft", inbox=True)                   # _inbox → out
    write_rule(tmp_vault, slug="fluid", stability="evolving")         # evolving → out
    write_rule(tmp_vault, slug="me", page_type="user")                # user → out by default
    write_rule(tmp_vault, slug="notes", page_type="project")          # project → out by default
    _share_stub.CALLS.clear()

    rep = _publish(tmp_vault, repo)

    assert rep.wrote is True
    assert sorted(rep.written) == ["feedback/uni.md", "feedback/use-yarn.md"]
    assert rep.updated == [] and rep.unchanged == [] and rep.pruned == [] and rep.refused == []
    assert rep.universal == 1
    assert _tree(repo) == {"feedback/uni.md", "feedback/use-yarn.md"}

    data = _manifest(tmp_vault)
    assert data["cwd"] == str(repo.resolve()) and data["path"] == SHARE_DIR
    assert data["vault"] == share_format.vault_id(tmp_vault)
    assert set(data["rules"]) == {"uni", "use-yarn"}
    assert "published_at" in data

    # The wiring, not just the outcome: every page went through to_portable
    # stamped with *this* vault's id, the caller's project and the given day.
    if _share_stub.MODULE_NAME in __import__("sys").modules and _share_stub.CALLS:
        assert {(c["vault"], c["project"], c["today"]) for c in _share_stub.CALLS} == {
            (share_format.vault_id(tmp_vault), "app", TODAY)
        }


def test_types_opt_in_user_and_project_pages(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="me", page_type="user")
    write_rule(tmp_vault, slug="notes", page_type="project")
    rep = _publish(tmp_vault, repo, types=("feedback", "user", "project"))
    assert _tree(repo) == {"user/me.md", "project/notes.md"}
    assert rep.user_pages == ["me"]


def test_empty_selection_writes_and_prunes_nothing(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    _publish(tmp_vault, repo)
    (tmp_vault / "shared" / "feedback" / "a.md").unlink()

    rep = _publish(tmp_vault, repo)

    assert rep.rules == [] and rep.wrote is False and rep.pruned == []
    assert _tree(repo) == {"feedback/a.md"}, "an empty selection is not a prune — that is --remove"


# --- idempotence, update, prune ---------------------------------------------

def test_rerun_on_unchanged_vault_rewrites_nothing(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    write_rule(tmp_vault, slug="b", projects=("x", "y"))
    _publish(tmp_vault, repo)
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (repo / SHARE_DIR).rglob("*.md")}
    manifest_before = _manifest(tmp_vault)["rules"]

    rep = _publish(tmp_vault, repo, today="2026-12-31")  # a later day must not count as a change

    assert sorted(rep.unchanged) == ["feedback/a.md", "feedback/b.md"]
    assert rep.written == [] and rep.updated == []
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (repo / SHARE_DIR).rglob("*.md")}
    assert after == before
    assert _manifest(tmp_vault)["rules"] == manifest_before


def test_changed_new_and_gone_rules(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    write_rule(tmp_vault, slug="gone")
    _publish(tmp_vault, repo)
    old_bytes = (repo / SHARE_DIR / "feedback" / "a.md").read_bytes()

    write_rule(tmp_vault, slug="a", body="changed\n")
    write_rule(tmp_vault, slug="new")
    (tmp_vault / "shared" / "feedback" / "gone.md").unlink()
    rep = _publish(tmp_vault, repo)

    assert rep.updated == ["feedback/a.md"]
    assert rep.written == ["feedback/new.md"]
    assert rep.pruned == ["feedback/gone.md"]
    assert _tree(repo) == {"feedback/a.md", "feedback/new.md"}
    assert (repo / SHARE_DIR / "feedback" / "a.md").read_bytes() != old_bytes
    assert set(_manifest(tmp_vault)["rules"]) == {"a", "new"}


def test_type_change_moves_the_file(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a", page_type="reference")
    _publish(tmp_vault, repo, types=("feedback", "reference"))
    (tmp_vault / "shared" / "reference" / "a.md").unlink()
    write_rule(tmp_vault, slug="a", page_type="feedback")

    rep = _publish(tmp_vault, repo, types=("feedback", "reference"))

    assert rep.written == ["feedback/a.md"] and rep.pruned == ["reference/a.md"]
    assert _tree(repo) == {"feedback/a.md"}
    assert not (repo / SHARE_DIR / "reference").exists(), "an emptied type directory goes too"


# --- ownership ----------------------------------------------------------------

def test_another_vaults_file_is_never_overwritten_or_pruned(tmp_vault: Path, repo: Path):
    theirs_selected = _write_foreign(repo, slug="a")       # collides with our rule
    theirs_other = _write_foreign(repo, slug="only-theirs")  # no rule of ours
    write_rule(tmp_vault, slug="a")
    write_rule(tmp_vault, slug="b")

    rep = _publish(tmp_vault, repo)

    assert rep.written == ["feedback/b.md"]
    assert [r for r, _ in rep.refused] == ["feedback/a.md"]
    assert "another vault" in rep.refused[0][1]
    assert rep.pruned == []
    assert theirs_selected.read_text(encoding="utf-8").count("Their rule.") == 1
    assert theirs_other.exists()
    assert set(_manifest(tmp_vault)["rules"]) == {"b"}, "a refused slug is not something we wrote"

    # Their rule vanishing from *our* vault changes nothing in the tree.
    (tmp_vault / "shared" / "feedback" / "a.md").unlink()
    rep = _publish(tmp_vault, repo)
    assert rep.pruned == [] and theirs_selected.exists()


def test_stray_files_are_reported_and_left_alone(tmp_vault: Path, repo: Path):
    stray = repo / SHARE_DIR / "feedback" / "README.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("# not a rule\n", encoding="utf-8")
    colliding = repo / SHARE_DIR / "feedback" / "a.md"
    colliding.write_text("hand-written, no provenance\n", encoding="utf-8")
    write_rule(tmp_vault, slug="a")
    write_rule(tmp_vault, slug="b")

    rep = _publish(tmp_vault, repo)

    assert rep.not_pages == ["feedback/README.md"]
    assert [r for r, _ in rep.refused] == ["feedback/a.md"]
    assert "not a mnemo page" in rep.refused[0][1]
    assert colliding.read_text(encoding="utf-8") == "hand-written, no provenance\n"
    assert stray.exists() and rep.written == ["feedback/b.md"]


def test_hop_keeps_first_vault_but_stays_ours_to_update_and_prune(tmp_vault: Path, repo: Path):
    """A rule this vault imported from vault X and promoted re-publishes naming X
    (the contract's hop rule). Ownership then comes from our manifest, or the
    next publish would refuse our own file as "another vault's"."""
    page = write_rule(tmp_vault, slug="hopped")
    text = page.read_text(encoding="utf-8")
    text = text.replace(
        "tags:\n",
        "origin: imported\nimported:\n  vault: cafebabecafebabe\n  project: theirs\n"
        "  date: 2026-01-01\n  source_count: 3\n  at: 2026-02-02\ntags:\n",
    )
    page.write_text(text, encoding="utf-8")

    rep = _publish(tmp_vault, repo)
    assert rep.written == ["feedback/hopped.md"]
    entries = dict(share_format.iter_portable(repo / SHARE_DIR))
    (rule,) = [r for r in entries.values() if r is not None]
    if rule.vault != share_format.vault_id(tmp_vault):
        assert rule.vault == "cafebabecafebabe"

    rep = _publish(tmp_vault, repo)
    assert rep.unchanged == ["feedback/hopped.md"] and rep.refused == []

    page.unlink()
    write_rule(tmp_vault, slug="other")
    rep = _publish(tmp_vault, repo)
    assert rep.pruned == ["feedback/hopped.md"]


# --- dry run, remove -----------------------------------------------------------

def test_dry_run_plans_and_touches_nothing(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    _publish(tmp_vault, repo)
    write_rule(tmp_vault, slug="a", body="changed\n")
    write_rule(tmp_vault, slug="new")
    _write_foreign(repo, slug="new")
    write_rule(tmp_vault, slug="also-new")
    snapshot = {p: p.read_bytes() for p in (repo / SHARE_DIR).rglob("*.md")}
    manifest_before = (tmp_vault / ".mnemo" / "share" / "app.json").read_bytes()

    rep = _publish(tmp_vault, repo, dry_run=True)

    assert rep.wrote is False
    assert rep.updated == ["feedback/a.md"] and rep.written == ["feedback/also-new.md"]
    assert [r for r, _ in rep.refused] == ["feedback/new.md"]
    assert {p: p.read_bytes() for p in (repo / SHARE_DIR).rglob("*.md")} == snapshot
    assert (tmp_vault / ".mnemo" / "share" / "app.json").read_bytes() == manifest_before


def test_remove_deletes_only_our_files_and_the_manifest(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    _publish(tmp_vault, repo)
    theirs = _write_foreign(repo, slug="theirs")

    rep = _publish(tmp_vault, repo, remove=True)

    assert rep.removed is True and rep.pruned == ["feedback/a.md"]
    assert _tree(repo) == {"feedback/theirs.md"} and theirs.exists()
    assert not (tmp_vault / ".mnemo" / "share" / "app.json").exists()
    assert pub.staleness(tmp_vault, project="app", repo_root=repo) is None

    rep = _publish(tmp_vault, repo, remove=True)
    assert rep.removed is False


def test_remove_clears_an_emptied_tree(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    _publish(tmp_vault, repo)
    _publish(tmp_vault, repo, remove=True)
    assert not (repo / SHARE_DIR).exists()


# --- staleness ----------------------------------------------------------------

def test_staleness_none_then_current_then_behind(tmp_vault: Path, repo: Path):
    assert pub.staleness(tmp_vault, project="app", repo_root=repo) is None
    write_rule(tmp_vault, slug="a")
    write_rule(tmp_vault, slug="b")
    _publish(tmp_vault, repo)

    assert pub.staleness(tmp_vault, project="app", repo_root=repo) == (2, 0)

    write_rule(tmp_vault, slug="a", body="changed\n")   # differs
    write_rule(tmp_vault, slug="c")                      # new
    assert pub.staleness(tmp_vault, project="app", repo_root=repo) == (2, 2)

    (tmp_vault / "shared" / "feedback" / "b.md").unlink()  # gone → differs too
    assert pub.staleness(tmp_vault, project="app", repo_root=repo) == (2, 3)


def test_staleness_ignores_the_calendar(tmp_vault: Path, repo: Path, monkeypatch):
    """Status runs every day; an unchanged vault must read as up to date
    tomorrow, so the publish date is pinned to the tree, not re-stamped."""
    write_rule(tmp_vault, slug="a")
    _publish(tmp_vault, repo, today="2026-09-13")
    monkeypatch.setattr(pub, "_today", lambda: "2027-01-01")
    assert pub.staleness(tmp_vault, project="app", repo_root=repo) == (1, 0)


def test_staleness_is_read_only(tmp_vault: Path, repo: Path):
    write_rule(tmp_vault, slug="a")
    _publish(tmp_vault, repo)
    write_rule(tmp_vault, slug="b")
    before = {p: p.read_bytes() for p in (repo / SHARE_DIR).rglob("*.md")}
    pub.staleness(tmp_vault, project="app", repo_root=repo)
    assert {p: p.read_bytes() for p in (repo / SHARE_DIR).rglob("*.md")} == before
    assert set(_manifest(tmp_vault)["rules"]) == {"a"}


# --- gitignore probe -------------------------------------------------------------

def test_gitignore_probe_reads_check_ignore_exit_code(repo: Path, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    assert pub.share_dir_is_gitignored(repo) is True
    assert seen and seen[0][:3] == ["git", "-C", str(repo)] and "check-ignore" in seen[0]
    assert seen[0][-1] == SHARE_DIR

    monkeypatch.setattr(pub.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    assert pub.share_dir_is_gitignored(repo) is False


def test_gitignore_probe_survives_no_git(repo: Path, monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(pub.subprocess, "run", boom)
    assert pub.share_dir_is_gitignored(repo) is False
