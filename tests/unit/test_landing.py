"""The last metre of a contract: the order, the signatures, the rehearsal.

Every git fact is asserted against **real repositories**, as
:mod:`tests.unit.test_delivery` does and for the same reason: the claim of
:mod:`mnemo.core.landing` is that git is the authority on whether a signature
exists on a branch and whether two pieces merge, and a stubbed git tests the
stub. ``gh`` is never invoked — it is stubbed at the subprocess boundary — and
the suite a rehearsal runs is a tiny interpreter command, so a red suite is a
real non-zero exit rather than a mocked one.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.core import contracts, landing
from mnemo.core.contracts import Contract, Piece
from tests.unit import _git_template


def _run(args, *, cwd) -> None:
    subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _build_repo(parent: Path) -> None:
    root = parent / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    _run(["git", "add", "src/base.py"], cwd=root)
    _run(["git", "commit", "-m", "base"], cwd=root)


@pytest.fixture
def repo(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real git repo with one commit on ``master`` and no remote."""
    # Built once per worker and copied per test (#799): a git call is a process.
    _git_template.copy("landing-repo", _build_repo, tmp_path, tmp_path_factory)
    return tmp_path / "mnemo"


def _branch(repo: Path, branch: str, files: dict[str, str]) -> None:
    """Commit *files* on *branch* (from master) in a throwaway worktree."""
    tree = repo.parent / f"tree-{branch.replace('/', '-')}"
    _run(["git", "worktree", "add", "-b", branch, str(tree), "master"], cwd=repo)
    for name, body in files.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        _run(["git", "add", name], cwd=tree)
    _run(["git", "commit", "-m", f"work on {branch}"], cwd=tree)
    _run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo)


@pytest.fixture(autouse=True)
def no_gh(monkeypatch: pytest.MonkeyPatch):
    """No test here reaches the network: ``gh`` answers "no PR"."""
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _contract(*pieces: Piece, feature: str = "f") -> Contract:
    return Contract(feature=feature, verdict="parallel", pieces=list(pieces))


# --- order: owners before consumers, contract order otherwise ---------------


def test_order_lands_an_owner_before_its_consumer() -> None:
    api = Piece("api", files=["api.py"], consumes=[("`load(k)`", "storage")])
    storage = Piece("storage", files=["s.py"], exposes=["`load(k)`"])

    assert [p.slug for p in landing.order(_contract(api, storage))] == [
        "storage", "api",
    ]


def test_order_keeps_contract_order_between_independent_pieces() -> None:
    a = Piece("a", files=["a.py"])
    b = Piece("b", files=["b.py"])
    c = Piece("c", files=["c.py"])

    assert [p.slug for p in landing.order(_contract(c, a, b))] == ["c", "a", "b"]


def test_order_refuses_a_cycle_by_name() -> None:
    """``_validate`` admits A<->B; landing has no order for it and says so."""
    a = Piece("a", files=["a.py"], consumes=[("`g()`", "b")])
    b = Piece("b", files=["b.py"], consumes=[("`f()`", "a")])

    with pytest.raises(landing.LandingError) as exc:
        landing.order(_contract(a, b))
    assert "a" in str(exc.value) and "b" in str(exc.value)


# --- the name a signature is looked up by ----------------------------------


@pytest.mark.parametrize(
    "signature, name",
    [
        ("`load(key) -> Record | None`", "load"),
        ("`ready(worktree, *, repo_root) -> Readiness`", "ready"),
        ("`Readiness.ready`", "ready"),
        ("`Readiness.ready -> bool`", "ready"),
        ("`class Piece`", "Piece"),
        ("`def parse(x)`", "parse"),
        ("`async def fetch(x)`", "fetch"),
        ("`BASE: str`", "BASE"),
        ("`parse_contract`", "parse_contract"),
        ("`mnemo dispatch --contract <path>`", None),
        ("`--json`", None),
        ("nothing", None),
        # #675: a Rust field, and a TS optional member with its `?`.
        ("`ChildSession.notice: Option<String>`", "notice"),
        ("`ChildSession.notice?: 'held' | 'delivered' | null`", "notice"),
        ("`Store.load?(key)`", "load"),
        ("`ready?`", None),
        ('`"held" | "delivered" | null`', None),
    ],
)
def test_signature_name(signature: str, name) -> None:
    assert landing.signature_name(signature) == name


# --- presence: a definition in the piece's files at a ref -------------------


def test_a_def_in_the_boundary_is_present(repo: Path) -> None:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    return None\n"})
    piece = Piece("storage", files=["src/storage.py"], exposes=["`load(key) -> R`"])

    assert landing.present("`load(key) -> R`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is True


def test_a_name_defined_nowhere_in_the_boundary_is_missing(repo: Path) -> None:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})
    piece = Piece("storage", files=["src/storage.py"], exposes=["`load(key)`"])

    assert landing.present("`load(key)`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is False


def test_a_name_defined_outside_the_boundary_does_not_count(repo: Path) -> None:
    """The contract's promise is *this piece* delivers it, in *these* files."""
    _branch(repo, "feat/f/storage", {
        "src/storage.py": "x = 1\n",
        "src/elsewhere.py": "def load(key):\n    pass\n",
    })
    piece = Piece("storage", files=["src/storage.py"], exposes=["`load(key)`"])

    assert landing.present("`load(key)`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is False


def test_a_glob_boundary_is_expanded_against_the_ref(repo: Path) -> None:
    _branch(repo, "feat/f/storage", {"src/pkg/deep.py": "class Store:\n    pass\n"})
    piece = Piece("storage", files=["src/pkg/*.py"], exposes=["`class Store`"])

    assert landing.present("`class Store`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is True


def test_a_dynamic_route_segment_is_a_literal_path(repo: Path) -> None:
    """`[id]` is a directory name to Next.js and a character class to fnmatch.

    The path #516 was written for names a file that exists as spelled; read as
    a glob it matches only `.../i/actions.ts` or `.../d/actions.ts`, and the
    piece's own definition was never looked at.
    """
    path = "src/app/(dashboard)/simulations/[id]/actions.ts"
    _branch(repo, "feat/f/route", {path: "export function materialize() {}\n"})
    piece = Piece("route", files=[path], exposes=["`materialize()`"])

    assert landing.present("`materialize()`", piece=piece, ref="feat/f/route",
                           repo_root=repo) is True


def test_a_dynamic_route_directory_is_a_literal_path(repo: Path) -> None:
    """The directory form of the same segment: `[id]/` is a prefix, not a class."""
    _branch(repo, "feat/f/route", {
        "src/app/[id]/actions.ts": "export function materialize() {}\n",
    })
    piece = Piece("route", files=["src/app/[id]/"], exposes=["`materialize()`"])

    assert landing.present("`materialize()`", piece=piece, ref="feat/f/route",
                           repo_root=repo) is True


def test_a_method_counts_when_defined_indented(repo: Path) -> None:
    _branch(repo, "feat/f/d", {
        "src/d.py": "class Readiness:\n    @property\n    def ready(self):\n        return True\n",
    })
    piece = Piece("d", files=["src/d.py"], exposes=["`Readiness.ready`"])

    assert landing.present("`Readiness.ready`", piece=piece, ref="feat/f/d",
                           repo_root=repo) is True


def _defined_in(repo: Path, path: str, source: str, signature: str) -> bool | None:
    _branch(repo, "feat/f/lang", {path: source})
    piece = Piece("lang", files=[path], exposes=[signature])
    return landing.present(signature, piece=piece, ref="feat/f/lang", repo_root=repo)


@pytest.mark.parametrize("path, source", [
    # mnemo-desktop round 18, the two definitions `land` could not see (#446).
    ("src/actions.ts", "import type { Pr } from './types'\n\n"
                       "export function mergePr(root: string, pr: Pr) {\n  run(root, pr)\n}\n"),
    ("src/actions.ts", "export async function mergePr(root: string, pr: Pr): Promise<void> {}\n"),
    ("src/actions.ts", "function mergePr(root, pr) {}\n"),
    ("src/actions.ts", "export default function mergePr(root: string) {}\n"),
    ("src/actions.ts", "export const mergePr = (root: string, pr: Pr): void => {}\n"),
    ("src/actions.tsx", "export let mergePr: (root: string) => void\n"),
    ("src/actions.ts", "export class mergePr {}\n"),
    ("src/actions.ts", "export interface mergePr { root: string }\n"),
    ("src/actions.ts", "export type mergePr = (root: string) => void\n"),
    ("src-tauri/src/merge.rs", "pub fn mergePr(root: &str) -> Result<(), String> {\n    Ok(())\n}\n"),
    ("src-tauri/src/merge.rs", "pub(crate) async fn mergePr(root: &str) {}\n"),
    ("src-tauri/src/merge.rs", "impl Lens {\n    pub fn mergePr(&self) {}\n}\n"),
    ("src-tauri/src/merge.rs", "pub struct mergePr {\n    root: String,\n}\n"),
])
def test_a_typescript_or_rust_definition_is_present(repo: Path, path: str, source: str) -> None:
    assert _defined_in(repo, path, source, "`mergePr(root: string, pr: Pr): void`") is True


@pytest.mark.parametrize("path, source", [
    ("src/board.ts", "import { mergePr } from './actions'\n"),
    ("src/board.ts", "export { mergePr } from './actions'\n"),
    ("src/board.ts", "function go() {\n  mergePr(root, pr)\n}\n"),
    ("src/board.ts", "const x = mergePr(root, pr)\n"),
    ("src/board.ts", "// export function mergePr(root: string) {}\n"),
    ("src-tauri/src/board.rs", "fn go() {\n    mergePr(root);\n}\n"),
    ("src-tauri/src/board.rs", "use crate::merge::mergePr;\n"),
])
def test_an_import_or_a_call_is_not_a_definition(repo: Path, path: str, source: str) -> None:
    """The contract's promise is that this piece *defines* it; using it is the consumer's side."""
    assert _defined_in(repo, path, source, "`mergePr(root: string, pr: Pr): void`") is False


def _present_in(repo: Path, signature: str, source: str) -> bool | None:
    branch = "feat/f/attr"
    _branch(repo, branch, {"src/m.py": source})
    piece = Piece("attr", files=["src/m.py"], exposes=[signature])
    return landing.present(signature, piece=piece, ref=branch, repo_root=repo)


@pytest.mark.parametrize(
    "signature, source",
    [
        # #305: the field `mnemo land` refused on PR #294, as it was written.
        ("`ConsumeReport.retired`",
         "from dataclasses import dataclass\n\n@dataclass\nclass ConsumeReport:\n"
         "    consumed: int = 0\n    #: Markers retired.\n    retired: int = 0\n"),
        ("`Limits.MAX`", "class Limits:\n    MAX = 3\n"),
        ("`Color.RED`", "import enum\n\nclass Color(enum.Enum):\n    RED = enum.auto()\n"),
        ("`Point.x -> int`", "from typing import NamedTuple\n\nclass Point(NamedTuple):\n    x: int\n"),
        ("`Store.path`", "class Store:\n    def __init__(self, root):\n        self.path = root\n"),
        ("`Outer.Inner.depth`", "class Outer:\n    class Inner:\n        depth = 1\n"),
    ],
)
def test_a_class_attribute_counts(repo: Path, signature: str, source: str) -> None:
    assert _present_in(repo, signature, source) is True


def test_a_local_of_the_same_name_is_not_the_attribute(repo: Path) -> None:
    """An indented ``name = ...`` in a function body is still not a boundary."""
    source = ("class ConsumeReport:\n    consumed: int = 0\n\n"
              "def consume():\n    retired = 0\n    return retired\n")

    assert _present_in(repo, "`ConsumeReport.retired`", source) is False


def test_a_method_on_another_class_is_not_the_owners(repo: Path) -> None:
    source = ("class Readiness:\n    ok = True\n\n"
              "class Other:\n    def ready(self):\n        return True\n")

    assert _present_in(repo, "`Readiness.ready`", source) is False


def test_a_method_local_is_not_a_class_attribute(repo: Path) -> None:
    source = "class Store:\n    def load(self):\n        path = 1\n        return path\n"

    assert _present_in(repo, "`Store.path`", source) is False


def test_a_module_qualifier_still_finds_the_top_level_def(repo: Path) -> None:
    """`briefing_select.pick(...)` — the channels contract's own dotted shape."""
    source = "def pick(vault_root, agent_name, *, query):\n    return None\n"

    assert _present_in(repo, "`briefing_select.pick(vault_root) -> R`", source) is True


def test_an_unparseable_file_falls_back_to_the_name_lookup(repo: Path) -> None:
    """No class can be read out of it, so the bare-name rule answers as before."""
    source = "class Store:\n    def path(self):\n        return 1\n    def broken(:\n"

    assert _present_in(repo, "`Store.path`", source) is True


# --- a Rust or TypeScript owner's members (#675) ----------------------------

# The shape of the `notice` piece mnemo-desktop's Nexus round could not land:
# a field added to a shared struct, with doc comments, attributes and generics
# around it, and the same name in a struct expression and an assignment —
# none of which the bare-name lookup counts, which is why it read ✗.
MISSION_RS = '''\
use std::collections::HashMap;

/// One child session as the board shows it. Braces in prose: `{ … }`.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ChildSession {
    pub id: String,
    pub tokens: u64,
    /// Why it waits; `None` while it runs.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub waiting_for: Option<String>,
    pub labels: HashMap<String, Vec<&'static str>>,
    pub pr: Option<Pr>,
    /// Whether the parent has this child's finish notice: `held`, `delivered`.
    pub notice: Option<String>,
}

pub struct Pr {
    pub number: u64,
    pub failing: Vec<String>,
}

pub fn parse_sessions(json: &str) -> Result<Vec<ChildSession>, String> {
    let pr = Pr { number: 1, failing: vec!["}".to_string()] };
    Ok(vec![ChildSession { id: json.into(), tokens: 0, waiting_for: None,
                           labels: HashMap::new(), pr: Some(pr), notice: None }])
}

pub fn apply_notices(children: &mut [ChildSession], notices: &HashMap<String, String>) {
    for c in children.iter_mut() {
        c.notice = notices.get(&c.id).cloned();
    }
}
'''


def test_a_rust_struct_field_is_present(repo: Path) -> None:
    """The issue as filed: the field was delivered, and `land` said ✗."""
    signature = "`ChildSession.notice: Option<String>`"

    assert _defined_in(repo, "src-tauri/src/mission.rs", MISSION_RS, signature) is True


def test_inspect_lands_a_piece_that_exposes_a_struct_field(repo: Path, gh_prs) -> None:
    """`CANNOT LAND (1): notice` was the whole report; the piece is landable."""
    gh_prs["feat/f/notice"] = (1, "OPEN")
    exposes = ["`ChildSession.notice: Option<String>`", '`"held" | "delivered" | null`']
    piece = Piece("notice", files=["src-tauri/src/mission.rs"], exposes=exposes)
    _branch(repo, "feat/f/notice", {"src-tauri/src/mission.rs": MISSION_RS})

    (state,) = landing.inspect(_contract(piece), repo_root=repo)

    assert state.exposes == [(exposes[0], True), (exposes[1], None)]
    assert state.landable, state.reason


@pytest.mark.parametrize("signature, source", [
    ("`Pr.failing: Vec<String>`", MISSION_RS),
    ("`ChildSession.labels`", MISSION_RS),
    ("`Lens.map`", "pub struct Lens<'a, T: Clone> where T: Fn(u8) -> u8 {\n"
                   "    pub(crate) root: &'a str,\n    map: HashMap<String, T>,\n}\n"),
    ("`Query.type`", "pub struct Query {\n    pub r#type: String,\n}\n"),
    ("`Notice.Delivered`", "pub enum Notice {\n    Held,\n    Delivered(String),\n"
                           "    Gone { at: u64 },\n}\n"),
    ("`Store.load(key)`", "pub struct Store;\n\nimpl Store {\n"
                          "    pub fn load(&self, key: &str) -> Option<String> {\n"
                          "        if key == \"{\" { return None; }\n        None\n    }\n}\n"),
    ("`Lens.from`", "impl<T: Into<String>> From<T> for crate::lens::Lens<'_> {\n"
                    "    fn from(t: T) -> Self { todo!() }\n}\n"),
    ("`Source.read`", "pub trait Source {\n    const KIND: &'static str;\n"
                      "    fn read(&self) -> String;\n}\n"),
    ("`Source.KIND`", "pub trait Source {\n    const KIND: &'static str;\n}\n"),
])
def test_a_rust_owner_declares_fields_variants_and_impl_items(
    repo: Path, signature: str, source: str,
) -> None:
    assert _defined_in(repo, "src-tauri/src/lib.rs", source, signature) is True


def test_a_method_in_another_files_impl_block_counts(repo: Path) -> None:
    """A struct in one file and its `impl` in another, both in the boundary."""
    _branch(repo, "feat/f/split", {
        "src-tauri/src/model.rs": "pub struct Store {\n    pub root: String,\n}\n",
        "src-tauri/src/store.rs": "use crate::model::Store;\n\nimpl Store {\n"
                                  "    pub fn notice(&self) -> u8 { 0 }\n}\n",
    })
    piece = Piece("split", files=["src-tauri/src/model.rs", "src-tauri/src/store.rs"])

    assert landing.present("`Store.notice()`", piece=piece, ref="feat/f/split",
                           repo_root=repo) is True


@pytest.mark.parametrize("signature, source", [
    # A field of another struct in the same file.
    ("`Pr.notice`", MISSION_RS),
    # A free function: bare-name lookup found it before, and it is no member.
    ("`ChildSession.apply_notices`", MISSION_RS),
    # A local, and a field in a struct expression, are not declarations.
    ("`ChildSession.notice`", "pub struct ChildSession {\n    pub id: String,\n}\n\n"
                              "fn make() -> Other {\n    let notice = 1;\n"
                              "    Other { notice }\n}\n"),
    # Comments and literals say nothing, and a `}` in one closes nothing.
    ("`ChildSession.notice`", "pub struct ChildSession {\n    /// Closed by `}`, not here.\n"
                              "    pub id: String,\n    // pub notice: Option<String>,\n"
                              "    /* pub notice: Option<String>, */\n}\n"
                              "const SHAPE: &str = \"struct ChildSession { notice: String }\";\n"
                              "const RAW: &str = r#\"pub notice: \"x\"\"#;\n"),
    # `impl Holder<ChildSession>` is Holder's impl.
    ("`ChildSession.notice`", "pub struct ChildSession {\n    pub id: String,\n}\n\n"
                              "impl Holder<ChildSession> {\n    pub fn notice(&self) {}\n}\n"),
])
def test_a_rust_member_is_looked_up_on_its_owner_only(
    repo: Path, signature: str, source: str,
) -> None:
    assert _defined_in(repo, "src-tauri/src/mission.rs", source, signature) is False


def test_a_doc_comment_brace_does_not_close_the_struct(repo: Path) -> None:
    source = ("pub struct ChildSession {\n    /// Closed by `}`, not here.\n"
              "    pub id: String,\n    pub notice: u8,\n}\n")

    assert _defined_in(repo, "src-tauri/src/m.rs", source, "`ChildSession.notice`") is True


def test_a_rust_module_qualifier_still_finds_the_function(repo: Path) -> None:
    """`mission` is no type there, so the bare-name lookup answers, as for Python."""
    signature = "`mission.apply_notices(children, notices)`"

    assert _defined_in(repo, "src-tauri/src/mission.rs", MISSION_RS, signature) is True


def test_an_unreadable_rust_file_falls_back_to_the_name_lookup(repo: Path) -> None:
    """A block comment that never closes: no owner is read, `pub fn` still counts."""
    source = "pub struct S;\n\nimpl S {\n    pub fn load(&self) {}\n}\n/* never closed\n"

    assert landing._owner_members("a.rs", source, "S") is None
    assert _defined_in(repo, "src-tauri/src/s.rs", source, "`S.load()`") is True


CHILD_TS = """\
import type { Base } from './base'

/** One child session. Braces in prose: `{`. */
export type ChildSession = {
  id: string
  // `held` while it waits for the parent's next prompt
  notice?: 'held' | 'delivered' | null
  pr: { number: number; url: string } | null
}

export type Pr = { number: number; failing: string[] }
"""


@pytest.mark.parametrize("signature, source", [
    ("`ChildSession.notice?: 'held' | 'delivered' | null`", CHILD_TS),
    ("`ChildSession.notice: string | null`", CHILD_TS),
    ("`ChildSession.pr`", CHILD_TS),
    ("`ChildSession.map(f)`", "export interface ChildSession extends Base<{ x: 1 }> {\n"
                              "  readonly id: string;\n  notice?: string,\n"
                              "  load(key: string): Promise<void>\n"
                              "  map<T>(f: (c: ChildSession) => T): T[]\n}\n"),
    ("`ChildSession.load(key)`", "export interface ChildSession {\n"
                                 "  load(key: string): Promise<void>\n}\n"),
    ("`ChildSession.notice`", "type ChildSession<T extends { id: string }> = Base<T> & {\n"
                              "  notice?: string\n}\n"),
    ("`Event.notice`", "type Event =\n  | { kind: 'held'; notice: string }\n"
                       "  | { kind: 'gone' }\n"),
    ("`Notice.Delivered`", "export enum Notice {\n  Held = 'held',\n"
                           "  Delivered = 'delivered',\n}\n"),
])
def test_a_typescript_type_declares_its_members(
    repo: Path, signature: str, source: str,
) -> None:
    assert _defined_in(repo, "src/mission/types.ts", source, signature) is True


CHILD_CLASS_TS = """\
export class ChildSession {
  private cache = new Map<string, number>()
  constructor(private readonly root: string, count: number) {}
  @observable notice: string | null = null
  get size(): number { return this.cache.size }
  async load(key: string): Promise<void> {
    const local = key.replace(/[{]/g, '')
    this.cache.set(local, 1)
  }
}
"""


@pytest.mark.parametrize("member, declared", [
    ("notice", True), ("cache", True), ("root", True), ("size", True), ("load", True),
    ("count", False),  # a constructor parameter, not a parameter property
    ("local", False),  # a method's local
])
def test_a_typescript_class_declares_its_members(repo: Path, member: str, declared: bool) -> None:
    signature = f"`ChildSession.{member}`"

    assert _defined_in(repo, "src/session.ts", CHILD_CLASS_TS, signature) is declared


@pytest.mark.parametrize("signature, source", [
    # A member of another type in the same file.
    ("`ChildSession.failing`", CHILD_TS),
    # A one-line alias ends at its line: the call after it declares nothing.
    ("`Fs.expect`", "type Fs = { readFileSync(path: string): string }\n\n"
                    "test('reads', () => {\n  expect(1).toBe(1)\n})\n"),
    # An object value and a parameter are not the type's members.
    ("`ChildSession.notice`", "export type ChildSession = { id: string }\n"
                              "const x = { notice: 1 }\n"
                              "export function f(notice: string) {}\n"),
    # Comments and strings say nothing.
    ("`ChildSession.notice`", "export interface ChildSession {\n  id: string\n"
                              "  // notice?: string\n}\n"
                              "const s = 'interface ChildSession { notice: string }'\n"),
])
def test_a_typescript_member_is_looked_up_on_its_owner_only(
    repo: Path, signature: str, source: str,
) -> None:
    assert _defined_in(repo, "src/mission/types.ts", source, signature) is False


@pytest.mark.parametrize("source, owner, member, declared", [
    # A decorator alone on its line is not `@IsArra` and a member `y`.
    ("export class AssignDto {\n  @ApiProperty({\n    example: ['a', 'b'],\n  })\n"
     "  @IsArray()\n  ids: string[];\n}\n", "AssignDto", "ids", True),
    ("export class AssignDto {\n  @IsArray()\n  ids: string[];\n}\n", "AssignDto", "y", False),
    # A generic method whose bound holds a brace, and one whose list is long.
    ("export class S {\n  private async resolve<T extends { id?: string }>(d: T): Promise<T> {\n"
     "    return d\n  }\n}\n", "S", "resolve", True),
    ("export class S {\n  map<\n    T extends object,\n  >(f: () => T): T {\n    return f()\n"
     "  }\n}\n", "S", "map", True),
    # A line that continues a return type declares nothing.
    ("export class S {\n  rows(): Promise<\n    Array<{ id: string }>\n  > {\n    return x\n"
     "  }\n}\n", "S", "Array", False),
    # `new (…)` is a construct signature; `constructor` is no member.
    ("type Ctrl = { new (...args: any[]): any; prototype: any }\n", "Ctrl", "new", False),
    ("type Ctrl = { new (...args: any[]): any; prototype: any }\n", "Ctrl", "prototype", True),
    (CHILD_CLASS_TS, "ChildSession", "constructor", False),
])
def test_typescript_corners_the_parser_comparison_found(
    source: str, owner: str, member: str, declared: bool,
) -> None:
    """Each disagreed with `tsc` on a real codebase (tools/measure_owner_members.py)."""
    members = landing._owner_members("src/s.ts", source, owner)

    assert members is not None and (member in members) is declared


def test_an_anonymous_class_is_no_type_named_extends() -> None:
    source = "const Fake = class extends Base {\n  run() {}\n}\n"

    assert landing._owner_members("a.test.ts", source, "extends") is None


def test_a_tsx_closing_tag_is_not_a_regex(repo: Path) -> None:
    """Read as `/p>{this.b ? <i/`, `</p>` would swallow a `{` and close the class early."""
    source = ("export class View extends Component<Props> {\n  render() {\n"
              "    return <div><p>{this.a}</p>{this.b ? <i/> : null}</div>\n  }\n"
              "  notice = 1\n}\n")

    assert _defined_in(repo, "src/View.tsx", source, "`View.notice`") is True


def test_an_alias_with_no_literal_is_no_readable_owner() -> None:
    """What `InstanceType<…>` declares lives elsewhere; the name lookup answers."""
    source = ("type ChildSession = InstanceType<typeof Base>\n\n"
              "vi.mock('x', () => ({ notice: 1 }))\n")

    assert landing._owner_members("a.ts", source, "ChildSession") is None


def test_crlf_sources_read_the_same() -> None:
    for path, source in (("a.rs", MISSION_RS), ("a.ts", CHILD_TS)):
        lf = landing._owner_members(path, source, "ChildSession")
        crlf = landing._owner_members(path, source.replace("\n", "\r\n"), "ChildSession")
        assert lf and "notice" in lf
        assert crlf == lf


def test_a_signature_without_an_identifier_is_unverifiable(repo: Path) -> None:
    piece = Piece("cli", files=["src/base.py"], exposes=["`mnemo land <path>`"])

    assert landing.present("`mnemo land <path>`", piece=piece, ref="master",
                           repo_root=repo) is None


def test_a_missing_file_in_the_boundary_is_not_an_error(repo: Path) -> None:
    """A piece that never created one of its files is missing, not crashed."""
    piece = Piece("s", files=["src/never.py", "src/base.py"], exposes=["`BASE`"])

    assert landing.present("`BASE`", piece=piece, ref="master", repo_root=repo) is True


# --- inspect: the read-only view of a delivered contract -------------------


@pytest.fixture
def gh_prs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """``branch -> (number, state)`` answered by a stubbed ``gh pr list``.

    The stdout shape is the one ``gh`` really emits (see
    ``tests.unit.test_delivery.REAL_GH_MERGED``); only the values vary.
    """
    prs: dict[str, tuple[int, str]] = {}
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            if args[1:3] == ["pr", "list"]:
                branch = args[args.index("--head") + 1]
                if branch in prs:
                    number, state = prs[branch]
                    body = (f'[{{"number":{number},"state":"{state}",'
                            f'"url":"https://x/pull/{number}"}}]\n')
                    return subprocess.CompletedProcess(args, 0, body, "")
                return subprocess.CompletedProcess(args, 0, "[]\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return prs


STORAGE = Piece("storage", files=["src/storage.py"], exposes=["`load(key) -> R`"])
API = Piece("api", files=["src/api.py"], exposes=["`handler(req)`"],
            consumes=[("`load(key)`", "storage")])


def _two_pieces(repo: Path) -> Contract:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    return _contract(API, STORAGE)


def test_inspect_lists_pieces_in_landing_order_with_pr_ref_and_signatures(
    repo: Path, gh_prs: dict,
) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")

    states = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert [s.piece.slug for s in states] == ["storage", "api"]
    storage, api = states
    assert storage.branch == "feat/f/storage"
    assert storage.ref == "feat/f/storage"
    assert storage.pr == "https://x/pull/1" and storage.pr_state == "OPEN"
    assert storage.exposes == [("`load(key) -> R`", True)]
    assert api.consumes == [("`load(key)`", "storage", True)]
    assert all(s.landable for s in states)


def test_inspect_refuses_a_piece_without_a_pr_naming_deliver(repo: Path, gh_prs) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")

    storage, api = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert storage.landable
    assert not api.landable
    assert "deliver" in api.reason and "api" in api.reason


def test_inspect_refuses_a_piece_whose_branch_is_gone(repo: Path, gh_prs) -> None:
    """An open PR whose branch nothing here can reach cannot be rehearsed."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    contract = _contract(STORAGE)  # never branched

    (state,) = landing.inspect(contract, repo_root=repo)

    assert state.ref is None
    assert not state.landable
    assert "feat/f/storage" in state.reason


def test_inspect_checks_a_merged_piece_on_the_base(repo: Path, gh_prs) -> None:
    """Merged and branch deleted is the normal end state: its work is in master."""
    gh_prs["feat/f/storage"] = (1, "MERGED")
    (repo / "src" / "storage.py").write_text("def load(key):\n    pass\n", encoding="utf-8")
    _run(["git", "add", "src/storage.py"], cwd=repo)
    _run(["git", "commit", "-m", "storage landed"], cwd=repo)

    (state,) = landing.inspect(_contract(STORAGE), repo_root=repo)

    assert state.merged
    assert state.ref == "master"
    assert state.exposes == [("`load(key) -> R`", True)]
    assert state.landable


def test_inspect_refuses_a_missing_exposed_signature(repo: Path, gh_prs) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})

    (state,) = landing.inspect(_contract(STORAGE), repo_root=repo)

    assert state.exposes == [("`load(key) -> R`", False)]
    assert not state.landable
    assert "load" in state.reason


def test_inspect_flags_a_consumed_name_the_owner_never_exposes(repo: Path, gh_prs) -> None:
    """The static half of the signature check: the contract disagrees with itself."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    api = Piece("api", files=["src/api.py"], consumes=[("`fetch(key)`", "storage")])
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "x = 1\n"})

    storage, api_state = landing.inspect(_contract(api, STORAGE), repo_root=repo)

    assert api_state.consumes == [("`fetch(key)`", "storage", False)]
    assert not api_state.landable
    assert "fetch" in api_state.reason and "storage" in api_state.reason


def test_inspect_reports_an_unverifiable_signature_as_neither(repo: Path, gh_prs) -> None:
    gh_prs["feat/f/cli"] = (1, "OPEN")
    cli = Piece("cli", files=["src/cli.py"], exposes=["`mnemo land <path>`"])
    _branch(repo, "feat/f/cli", {"src/cli.py": "x = 1\n"})

    (state,) = landing.inspect(_contract(cli), repo_root=repo)

    assert state.exposes == [("`mnemo land <path>`", None)]
    assert state.landable


def test_inspect_raises_on_a_cycle(repo: Path, gh_prs) -> None:
    a = Piece("a", files=["a.py"], consumes=[("`g()`", "b")])
    b = Piece("b", files=["b.py"], consumes=[("`f()`", "a")])

    with pytest.raises(landing.LandingError):
        landing.inspect(_contract(a, b), repo_root=repo)


# --- inspect: the CI gate on an open PR ------------------------------------


def test_inspect_refuses_a_piece_whose_pr_has_a_failing_check(
    repo: Path, gh_prs: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child stops in seconds and CI takes minutes, so `land` is the only
    thing left between a red PR and the merge."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    monkeypatch.setattr(
        landing, "failing_checks",
        lambda pr: ["windows / py3.11"] if pr.endswith("/2") else [],
    )

    storage, api = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert storage.landable is True
    assert api.landable is False
    assert "windows / py3.11" in api.reason


def test_inspect_asks_for_checks_only_on_an_open_pr_with_no_other_reason(
    repo: Path, gh_prs: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One `gh` call per landable piece: not for the piece with no PR at all,
    and not for a piece already refused for another reason."""
    gh_prs["feat/f/storage"] = (1, "OPEN")  # api gets no PR
    asked: list[str] = []
    monkeypatch.setattr(landing, "failing_checks",
                        lambda pr: asked.append(pr) or [])

    storage, api = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert asked == ["https://x/pull/1"]
    assert storage.landable and not api.landable


def test_inspect_does_not_ask_for_checks_on_a_merged_piece(
    repo: Path, gh_prs: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The merged piece is otherwise landable — reason-free, signatures present
    — so only the OPEN guard keeps it from costing a `gh` call. Its open
    sibling in the same run is asked, which is what makes the empty answer for
    storage mean something.
    """
    gh_prs["feat/f/storage"] = (1, "MERGED")
    gh_prs["feat/f/api"] = (2, "OPEN")
    asked: list[str] = []
    monkeypatch.setattr(landing, "failing_checks",
                        lambda pr: asked.append(pr) or [])

    storage, api = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert storage.merged and storage.reason == "" and storage.landable
    assert asked == ["https://x/pull/2"]  # api's, never storage's
    assert api.landable


# --- rehearse: merge in order, check, run the suite, touch nothing ----------

# A "suite" that is green unless the merged tree contains `src/red`, and
# records every run. Real exit codes, real cwd — the rehearsal's claim is
# that the suite ran *in the merged tree*, and a stub could not show that.
def _suite(log: Path) -> list[str]:
    return [
        sys.executable, "-c",
        "import pathlib, sys; "
        f"pathlib.Path({str(log)!r}).open('a').write(pathlib.Path().resolve().name + chr(10)); "
        "sys.exit(1 if pathlib.Path('src/red').exists() else 0)",
    ]


def _worktrees(repo: Path) -> list[str]:
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.startswith("worktree ")]


def test_rehearse_merges_each_piece_in_order_and_runs_the_suite_each_time(
    repo: Path, gh_prs: dict, tmp_path: Path,
) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    states = landing.inspect(_two_pieces(repo), repo_root=repo)
    log = tmp_path / "runs.log"
    before = _worktrees(repo)

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert result.ok, result.steps
    assert [s.slug for s in result.steps] == ["storage", "api"]
    runs = log.read_text(encoding="utf-8").splitlines()
    assert len(runs) == 2
    assert all(name.startswith("mnemo-land-") for name in runs)
    assert _worktrees(repo) == before  # the rehearsal tree is gone
    # And master is untouched: nothing was merged for real.
    assert not (repo / "src" / "storage.py").exists()


def test_rehearse_starts_from_the_repos_default_branch_not_master(
    tmp_path: Path, gh_prs: dict,
) -> None:
    """#287: on a ``main`` repo there was no ``master`` to rehearse from."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run(["git", "init", "-b", "main"], cwd=upstream)
    _run(["git", "config", "user.email", "t@example.com"], cwd=upstream)
    _run(["git", "config", "user.name", "t"], cwd=upstream)
    (upstream / "src").mkdir()
    (upstream / "src" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    _run(["git", "add", "src/base.py"], cwd=upstream)
    _run(["git", "commit", "-m", "base"], cwd=upstream)
    clone = tmp_path / "desk" / "desktop"
    clone.parent.mkdir()
    _run(["git", "clone", str(upstream), str(clone)], cwd=tmp_path)
    _run(["git", "config", "user.email", "t@example.com"], cwd=clone)
    _run(["git", "config", "user.name", "t"], cwd=clone)
    _run(["git", "branch", "master", "main"], cwd=clone)  # a decoy, not the base
    for branch, name in (("feat/f/storage", "storage"), ("feat/f/api", "api")):
        tree = tmp_path / f"tree-{name}"
        _run(["git", "worktree", "add", "-b", branch, str(tree), "main"], cwd=clone)
        (tree / "src" / f"{name}.py").write_text(
            "def load(key):\n    pass\n" if name == "storage"
            else "def handler(req):\n    pass\n", encoding="utf-8")
        _run(["git", "add", f"src/{name}.py"], cwd=tree)
        _run(["git", "commit", "-m", name], cwd=tree)
        _run(["git", "worktree", "remove", "--force", str(tree)], cwd=clone)
    # main moves on upstream after the decoy was cut: a rehearsal from
    # `master` would not carry this file, one from `origin/main` does.
    (upstream / "src" / "later.py").write_text("LATER = 1\n", encoding="utf-8")
    _run(["git", "add", "src/later.py"], cwd=upstream)
    _run(["git", "commit", "-m", "later"], cwd=upstream)
    _run(["git", "fetch", "origin"], cwd=clone)
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")

    assert landing.base_ref(repo_root=clone) == "origin/main"

    states = landing.inspect(_contract(API, STORAGE), repo_root=clone)
    suite = [sys.executable, "-c",
             "import pathlib, sys; sys.exit(0 if pathlib.Path('src/later.py').exists() else 1)"]
    result = landing.rehearse(states, repo_root=clone, suite=suite)

    assert result.ok, result.steps


def test_rehearse_stops_at_the_first_conflict(repo: Path, gh_prs, tmp_path) -> None:
    gh_prs["feat/f/a"] = (1, "OPEN")
    gh_prs["feat/f/b"] = (2, "OPEN")
    a = Piece("a", files=["src/base.py"])
    b = Piece("b", files=["src/base.py"])
    _branch(repo, "feat/f/a", {"src/base.py": "BASE = 2\n"})
    _branch(repo, "feat/f/b", {"src/base.py": "BASE = 3\n"})
    states = landing.inspect(_contract(a, b), repo_root=repo)
    log = tmp_path / "runs.log"
    before = _worktrees(repo)

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert not result.ok
    assert result.failed.slug == "b"
    assert "conflict" in result.failed.detail.lower()
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1  # a's suite ran; b never merged
    assert _worktrees(repo) == before


def test_rehearse_stops_when_a_consumed_name_is_absent_from_the_merged_tree(
    repo: Path, gh_prs, tmp_path,
) -> None:
    """The check the issue asks for, at the step where it can first be made.

    The contract says storage exposes ``load``; the branch defines ``save``.
    ``inspect`` already flags storage's own row; this is the *consumer's*
    step, and it must not pass on the strength of the contract's text.
    """
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    states = landing.inspect(_contract(API, STORAGE), repo_root=repo)

    # `inspect` already refuses storage's row; `force` is how the rehearsal's
    # own check is reached, and it is the check under test here.
    result = landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"),
                              force=True)

    assert not result.ok
    assert result.failed.slug == "storage"  # its own exposes check fails first
    assert "load" in result.failed.detail


def test_rehearse_checks_the_consumer_against_the_owner_in_the_merged_tree(
    repo: Path, gh_prs, tmp_path,
) -> None:
    """Owner's row passes (it exposes what it says); the consumer names more."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    api = Piece("api", files=["src/api.py"],
                consumes=[("`load(key)`", "storage"), ("`purge()`", "storage")])
    storage = Piece("storage", files=["src/storage.py"],
                    exposes=["`load(key)`", "`purge()`"])
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "x = 1\n"})
    states = landing.inspect(_contract(api, storage), repo_root=repo)
    # Force the rehearsal past inspect's own refusal: this asserts the
    # rehearsal's check, not inspect's.
    result = landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"),
                              force=True)

    assert not result.ok
    assert result.failed.slug == "storage"
    assert "purge" in result.failed.detail


def test_rehearse_stops_at_the_consumer_when_the_owner_lacks_the_consumed_name(
    repo: Path, gh_prs, tmp_path,
) -> None:
    """The owner's own row passes; the consumer names something it never wrote.

    Mutation-found: with the consumer step's check removed, every other test
    here still passed, because the owner's own ``exposes`` check fired first
    in each of them. This one reaches the consumer's step — ``storage``
    exposes and defines ``load`` — and asserts that ``api`` is the piece
    stopped on, with the name it consumed and cannot have.
    """
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    api = Piece("api", files=["src/api.py"],
                consumes=[("`load(key)`", "storage"), ("`purge()`", "storage")])
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "x = 1\n"})
    states = landing.inspect(_contract(api, STORAGE), repo_root=repo)
    # `inspect` refuses api statically (storage exposes no `purge`); `force`
    # reaches the rehearsal's own check, which reads the merged tree.
    result = landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"),
                              force=True)

    assert not result.ok
    assert result.failed.slug == "api"
    assert "purge" in result.failed.detail and "storage" in result.failed.detail


def test_rehearse_stops_at_a_red_suite_with_its_output(repo: Path, gh_prs, tmp_path) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n",
                                     "src/red": ""})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    states = landing.inspect(_contract(API, STORAGE), repo_root=repo)
    log = tmp_path / "runs.log"

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert not result.ok
    assert result.failed.slug == "storage"
    assert "suite" in result.failed.detail
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1  # api never ran


def test_rehearse_skips_a_merged_piece(repo: Path, gh_prs, tmp_path) -> None:
    gh_prs["feat/f/storage"] = (1, "MERGED")
    gh_prs["feat/f/api"] = (2, "OPEN")
    (repo / "src" / "storage.py").write_text("def load(key):\n    pass\n", encoding="utf-8")
    _run(["git", "add", "src/storage.py"], cwd=repo)
    _run(["git", "commit", "-m", "storage landed"], cwd=repo)
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    states = landing.inspect(_contract(API, STORAGE), repo_root=repo)
    log = tmp_path / "runs.log"

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert result.ok
    assert [(s.slug, s.skipped) for s in result.steps] == [("storage", True), ("api", False)]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_rehearse_refuses_states_that_are_not_landable(repo: Path, gh_prs, tmp_path) -> None:
    """A rehearsal of a contract inspect refused would merge a piece with no PR."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    states = landing.inspect(_two_pieces(repo), repo_root=repo)  # api has no PR

    with pytest.raises(landing.LandingError) as exc:
        landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"))
    assert "api" in str(exc.value)


def test_rehearse_prepends_src_to_pythonpath_when_the_tree_has_one(
    repo: Path, gh_prs, tmp_path, monkeypatch,
) -> None:
    """An editable install resolves to the checkout it came from, never the
    rehearsal tree; a ``src`` layout gets its own tree first on the path."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    states = landing.inspect(_contract(STORAGE), repo_root=repo)
    seen = tmp_path / "path.txt"
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    suite = [sys.executable, "-c",
             f"import os, pathlib; pathlib.Path({str(seen)!r}).write_text(os.environ['PYTHONPATH'])"]

    assert landing.rehearse(states, repo_root=repo, suite=suite).ok

    parts = seen.read_text(encoding="utf-8").split(os.pathsep)
    first = Path(parts[0])
    assert first.name == "src" and first.parent.name.startswith("mnemo-land-")
    assert parts[1] == "/elsewhere"


# --- merge: gh, in order, stopping at the first refusal --------------------


def _gh_merge(monkeypatch: pytest.MonkeyPatch, *, fail_on: str = "") -> list:
    calls: list[list[str]] = []
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            calls.append(list(args))
            if fail_on and fail_on in args:
                return subprocess.CompletedProcess(
                    args, 1, "", "GraphQL: Pull request is not mergeable")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _state(slug: str, pr: str | None, pr_state: str | None = "OPEN") -> landing.PieceState:
    return landing.PieceState(
        piece=Piece(slug, files=["x.py"]), branch=f"feat/f/{slug}", ref=f"feat/f/{slug}",
        pr=pr, pr_state=pr_state, exposes=[], consumes=[],
    )


def test_merge_prs_merges_in_order_and_skips_the_already_merged(monkeypatch) -> None:
    calls = _gh_merge(monkeypatch)
    states = [_state("storage", "https://x/pull/1", "MERGED"), _state("api", "https://x/pull/2")]

    steps = landing.merge_prs(states, repo_root="/repo")

    assert [(s.slug, s.ok, s.skipped) for s in steps] == [("storage", True, True), ("api", True, False)]
    assert calls == [["gh", "pr", "merge", "https://x/pull/2", "--squash"]]


def test_merge_prs_stops_at_the_first_refusal_with_gh_stderr(monkeypatch) -> None:
    calls = _gh_merge(monkeypatch, fail_on="https://x/pull/1")
    states = [_state("storage", "https://x/pull/1"), _state("api", "https://x/pull/2")]

    steps = landing.merge_prs(states, repo_root="/repo")

    assert [s.slug for s in steps] == ["storage"]
    assert not steps[0].ok and "not mergeable" in steps[0].detail
    assert len(calls) == 1  # api was never attempted


def test_merge_prs_takes_the_method(monkeypatch) -> None:
    calls = _gh_merge(monkeypatch)

    landing.merge_prs([_state("a", "https://x/pull/1")], repo_root="/repo", method="rebase")

    assert calls[0][-1] == "--rebase"


def test_merge_prs_never_passes_admin_unless_asked(monkeypatch) -> None:
    """A branch protection is the maintainer's rule; only their `--admin` bypasses it."""
    calls = _gh_merge(monkeypatch)

    landing.merge_prs([_state("a", "https://x/pull/1")], repo_root="/repo")
    assert "--admin" not in calls[0]

    landing.merge_prs([_state("a", "https://x/pull/1")], repo_root="/repo", admin=True)
    assert calls[1][-1] == "--admin" and "--squash" in calls[1]
