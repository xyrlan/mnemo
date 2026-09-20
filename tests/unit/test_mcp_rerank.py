"""The opt-in mark-and-rerank stage of ``list_rules_by_topic`` (#401, #404).

No test here opens a socket: the provider is a function the test hands in,
and the one test that lets ``apply`` build its own client proves it never
gets that far. What is pinned is the promise the README makes about the
stage — off unless configured, one request per list call, nothing sent
without a query, and every failure invisible to the agent but counted in the
access log.

Since #404 the stage marks before it orders, and the marking has promises of
its own: nothing is ever dropped, a rule the judge did not score carries no
``relevant`` key at all, an empty relevant set is ``ok`` rather than an error,
and with the stage off no item carries the key.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.config import DEFAULTS
from mnemo.core.mcp import rerank
from mnemo.core.mcp.server import handle_request
from mnemo.core.mcp.tools import RuleRefs

ON = {"recall": {"rerank": {"provider": "typesafe"}}}


@pytest.fixture(autouse=True)
def _no_project_resolution(monkeypatch):
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(rerank.DEFAULT_KEY_ENV, raising=False)


def _write_page(vault: Path, slug: str, *, sources: int, body: str) -> None:
    target = vault / "shared" / "feedback"
    target.mkdir(parents=True, exist_ok=True)
    listed = "\n".join("  - bots/a/m%d.md" % i for i in range(sources))
    (target / (slug + ".md")).write_text(
        "---\nname: %s\ndescription: d\ntype: feedback\nstability: stable\n"
        "sources:\n%s\ntags:\n  - workflow\n---\n\n%s\n" % (slug, listed, body),
        encoding="utf-8")


@pytest.fixture
def vault(tmp_vault):
    _write_page(tmp_vault, "often-seen", sources=3, body="Squash before merge.")
    _write_page(tmp_vault, "the-one-that-matters", sources=2,
                body="Commit a script before running it on prod. " + rerank.GRAPH_SECTION + " [[links]]")
    _write_page(tmp_vault, "unrelated", sources=1, body="Name branches after the issue.")
    return tmp_vault


def _matches():
    return RuleRefs([{"slug": s, "type": "feedback", "source_count": n}
                     for s, n in (("often-seen", 3), ("the-one-that-matters", 2), ("unrelated", 1))])


class Judge:
    """Answers by rule text; records what it was sent."""

    def __init__(self, favourite="Commit a script", skip=()):
        self.calls = []
        self.favourite, self.skip = favourite, skip

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {
            key: {"noul": 0.9 if self.favourite in q["instructions"] else 0.1}
            for key, q in questions.items() if not any(s in q["instructions"] for s in self.skip)}}


def test_the_default_config_sends_nothing(vault):
    assert DEFAULTS["recall"]["rerank"]["provider"] == "none"
    judge = Judge()
    out, info = rerank.apply(vault, _matches(), "run a script on prod", project=None,
                             cfg=DEFAULTS, client=judge)
    assert info is None and not judge.calls
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


def test_a_provider_nobody_wrote_is_off_not_on():
    assert rerank.settings({"recall": {"rerank": {"provider": "typesafe.ai"}}})["provider"] == "none"
    assert rerank.settings(None)["provider"] == "none"
    assert rerank.settings(ON)["model"] == "jev-1.13.0"


def test_the_pair_reading_judge_reorders_the_bucket_in_one_request(vault):
    judge = Judge()
    given = _matches()
    given.retired_withheld = 2
    out, info = rerank.apply(vault, given, "run a script on prod", project=None, cfg=ON, client=judge)
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]
    assert info == {"provider": "typesafe", "status": "ok", "judged": 3, "relevant": 1}
    assert len(judge.calls) == 1
    assert out.retired_withheld == 2


def test_what_is_sent_is_the_query_and_the_bounded_rule_text_and_nothing_else(vault):
    judge = Judge()
    rerank.apply(vault, _matches(), "run a script on prod", project=None, cfg=ON, client=judge)
    state, questions = judge.calls[0]
    assert state == {"developer_task": "run a script on prod"}
    sent = json.dumps(questions)
    assert "Commit a script before running it on prod." in sent
    assert "[[links]]" not in sent and "the-one-that-matters" not in sent and str(vault) not in sent
    assert len(rerank.rule_text("x" * 5000)) == rerank.BODY_CHARS


def test_without_a_query_nothing_is_sent(vault):
    judge = Judge()
    out, info = rerank.apply(vault, _matches(), None, project=None, cfg=ON, client=judge)
    assert info["status"] == "no_query" and not judge.calls


def test_without_a_key_the_order_stands_and_no_request_is_built(vault):
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON)
    assert info["status"] == "no_key"
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


# ── where the key comes from (#406) ───────────────────────────────────────
#
# Claude Code spawns this server, so the environment variable reaches it only
# when `claude` was started from the shell that exported it. The secrets file
# is the second source; `mnemo rerank --setup` writes it.


def test_the_secrets_file_is_used_when_the_environment_has_nothing(vault, monkeypatch, tmp_path):
    from mnemo.core import secrets

    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(tmp_path / "s.json"))
    secrets.write("typesafe", "sk-from-file")
    key, source = rerank.resolve_key(rerank.settings(ON))
    assert (key, source) == ("sk-from-file", "secrets")


def test_the_environment_wins_over_the_file(vault, monkeypatch, tmp_path):
    from mnemo.core import secrets

    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(tmp_path / "s.json"))
    secrets.write("typesafe", "sk-from-file")
    monkeypatch.setenv(rerank.DEFAULT_KEY_ENV, "sk-from-env")
    assert rerank.resolve_key(rerank.settings(ON)) == ("sk-from-env", "env")


def test_a_key_stored_for_another_provider_is_not_used(vault, monkeypatch, tmp_path):
    from mnemo.core import secrets

    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(tmp_path / "s.json"))
    secrets.write("someone-else", "sk-theirs")
    assert rerank.resolve_key(rerank.settings(ON)) == (None, "none")


def test_an_unreadable_secrets_file_is_no_key_not_an_error(vault, monkeypatch, tmp_path):
    broken = tmp_path / "s.json"
    broken.write_text("{ truncated", encoding="utf-8")
    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(broken))
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON)
    assert info["status"] == "no_key"
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


def test_key_source_never_returns_the_key(vault, monkeypatch, tmp_path):
    from mnemo.core import secrets

    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(tmp_path / "s.json"))
    secrets.write("typesafe", "sk-secret")
    assert rerank.key_source(rerank.settings(ON)) == "secrets"
    assert rerank.key_source(rerank.settings(None)) == "none"


def test_with_the_provider_off_the_secrets_file_is_never_opened(vault, monkeypatch, tmp_path):
    """The default must not read a file that may not exist on this machine."""
    from mnemo.core import secrets

    def refuse():
        raise AssertionError("the stage read the secrets file while off")

    monkeypatch.setattr(secrets, "path", refuse)
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=None)
    assert info is None


@pytest.mark.parametrize("answer", [TimeoutError("slow"), {"answers": {}}, {"error": 429}, "not a dict"])
def test_a_provider_that_fails_leaves_the_order_that_came_in(vault, answer):
    def broken(state, questions):
        if isinstance(answer, Exception):
            raise answer
        return answer
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=broken)
    assert info["status"] == "error"
    assert [m["slug"] for m in out] == ["often-seen", "the-one-that-matters", "unrelated"]


def test_a_rule_the_judge_skipped_is_unjudged_not_irrelevant(vault):
    judge = Judge(favourite="Name branches", skip=("Squash",))
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=judge)
    assert [m["slug"] for m in out] == ["unrelated", "the-one-that-matters", "often-seen"]
    assert info["judged"] == 2


def test_a_bucket_over_the_cap_sends_its_head_and_keeps_its_tail(vault):
    judge = Judge()
    cfg = {"recall": {"rerank": {"provider": "typesafe", "maxRules": 2}}}
    out, _ = rerank.apply(vault, _matches(), "q", project=None, cfg=cfg, client=judge)
    assert len(judge.calls[0][1]) == 2
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]


def test_a_bool_is_not_a_probability():
    got = rerank.scores("q", {"a": "text"}, ["a"], lambda s, q: {"answers": {"r0": {"noul": True}}})
    assert got == {}


def _call(vault, cfg, arguments):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "list_rules_by_topic", "arguments": arguments}}
    response = handle_request(request, vault_root=vault, cfg=cfg)
    return [r["slug"] for r in json.loads(response["result"]["content"][0]["text"])]


def _log(vault):
    path = vault / ".mnemo" / "mcp-access-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_server_reorders_only_when_configured_and_logs_what_happened(vault, monkeypatch):
    judge = Judge()
    real = rerank.apply
    monkeypatch.setattr(rerank, "apply", lambda *a, **k: real(*a, client=judge, **k))
    args = {"topic": "workflow", "query": "run a script on prod"}

    assert _call(vault, None, args)[0] == "often-seen"
    assert not judge.calls and "rerank" not in _log(vault)[-1]

    assert _call(vault, ON, args)[0] == "the-one-that-matters"
    assert _log(vault)[-1]["rerank"] == {"provider": "typesafe", "status": "ok",
                                        "judged": 3, "relevant": 1}
    assert _log(vault)[-1]["hit_slugs"][0] == "the-one-that-matters"


def test_a_configured_server_without_a_key_answers_as_before(vault):
    assert _call(vault, ON, {"topic": "workflow", "query": "q"})[0] == "often-seen"
    assert _log(vault)[-1]["rerank"]["status"] == "no_key"


# --- #404: mark first, order second -----------------------------------------


def test_every_judged_rule_is_marked_and_nothing_is_dropped(vault):
    out, info = rerank.apply(vault, _matches(), "run a script on prod", project=None,
                             cfg=ON, client=Judge())
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]
    assert [m["relevant"] for m in out] == [True, False, False]
    assert info["relevant"] == 1


def test_with_the_stage_off_no_item_carries_a_relevant_key(vault):
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=DEFAULTS, client=Judge())
    assert info is None
    assert all("relevant" not in m for m in out)
    # And through the server, which is the stage's only caller.
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "list_rules_by_topic",
                          "arguments": {"topic": "workflow", "query": "q"}}}
    response = handle_request(request, vault_root=vault, cfg=None)
    items = json.loads(response["result"]["content"][0]["text"])
    assert items and all("relevant" not in item for item in items)


@pytest.mark.parametrize("query,client,status", [
    ("q", None, "no_key"),
    (None, Judge(), "no_query"),
    ("q", lambda state, questions: {"answers": {}}, "error"),
])
def test_a_stage_that_did_not_run_marks_nothing(vault, query, client, status):
    out, info = rerank.apply(vault, _matches(), query, project=None, cfg=ON, client=client)
    assert info["status"] == status
    assert all("relevant" not in m for m in out)


def test_a_rule_the_judge_skipped_carries_no_mark_at_all(vault):
    judge = Judge(favourite="Name branches", skip=("Squash",))
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=judge)
    marked = {m["slug"]: m.get("relevant", "absent") for m in out}
    assert marked == {"unrelated": True, "the-one-that-matters": False, "often-seen": "absent"}
    assert info == {"provider": "typesafe", "status": "ok", "judged": 2, "relevant": 1}


def test_a_rule_past_the_cap_is_neither_judged_nor_marked(vault):
    cfg = {"recall": {"rerank": {"provider": "typesafe", "maxRules": 2}}}
    out, _ = rerank.apply(vault, _matches(), "q", project=None, cfg=cfg, client=Judge())
    assert [m.get("relevant", "absent") for m in out] == [True, False, "absent"]


def test_nothing_relevant_is_an_answer_not_an_error(vault):
    """11 of 30 real queries had nothing in their topic about the task."""
    judge = Judge(favourite="nothing matches this")
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=judge)
    assert info["status"] == "ok" and info["judged"] == 3 and info["relevant"] == 0
    assert len(out) == 3 and all(m["relevant"] is False for m in out)


def test_bm25f_breaks_the_judges_ties_and_a_missing_index_costs_only_that(vault, monkeypatch):
    judge = Judge(favourite="this favours nobody")  # every rule 0.1
    monkeypatch.setattr(rerank, "bm25f_scores",
                        lambda root, query, slugs: {"unrelated": 4.0, "often-seen": 1.0})
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=judge)
    # 0.1 + 0.5 * 4/4 = 0.6, 0.1 + 0.5 * 1/4 = 0.225, 0.1 + 0 = 0.1.
    assert [m["slug"] for m in out] == ["unrelated", "often-seen", "the-one-that-matters"]
    assert info["relevant"] == 0

    monkeypatch.setattr(rerank, "bm25f_scores", lambda root, query, slugs: {})
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=Judge())
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]
    assert info["status"] == "ok"


def test_a_broken_index_does_not_throw_away_the_answer_already_paid_for(vault, monkeypatch):
    def broken(root, query, slugs):
        raise RuntimeError("index is a directory")
    monkeypatch.setattr(rerank, "bm25f_scores", broken)
    out, info = rerank.apply(vault, _matches(), "q", project=None, cfg=ON, client=Judge())
    assert info["status"] == "ok" and info["relevant"] == 1
    assert [m["slug"] for m in out] == ["the-one-that-matters", "often-seen", "unrelated"]


def test_the_two_new_knobs_have_defaults_a_typo_cannot_move():
    assert DEFAULTS["recall"]["rerank"]["bm25Weight"] == rerank.DEFAULT_BM25_WEIGHT
    assert DEFAULTS["recall"]["rerank"]["relevantAt"] == rerank.DEFAULT_RELEVANT_AT
    chosen = rerank.settings({"recall": {"rerank": {"bm25Weight": "half", "relevantAt": None}}})
    assert chosen["bm25Weight"] == rerank.DEFAULT_BM25_WEIGHT
    assert chosen["relevantAt"] == rerank.DEFAULT_RELEVANT_AT
    # An explicit zero is a value, not a missing one: BM25F off, everything marked.
    off = rerank.settings({"recall": {"rerank": {"bm25Weight": 0, "relevantAt": 0.0}}})
    assert off == dict(rerank.settings(None), bm25Weight=0.0, relevantAt=0.0)


def test_the_signal_is_the_judge_with_bm25f_as_a_tie_break():
    judged = {"a": 0.8, "b": 0.2, "c": 0.2}
    assert rerank.fuse(judged, {}) == judged
    assert rerank.fuse(judged, {"a": 0.0, "b": 0.0}) == judged
    fused = rerank.fuse(judged, {"b": 2.0, "c": 1.0}, weight=0.5)
    assert fused == {"a": 0.8, "b": 0.7, "c": 0.45}
    assert rerank.ranked(["c", "b", "a", "d"], fused) == ["a", "b", "c", "d"]
    assert rerank.marks(fused, 0.7) == {"a": True, "b": True, "c": False}
    # A rule the judge never scored is not a key at all.
    assert "d" not in rerank.fuse(judged, {"d": 9.0})


# --- TLS: a Python with no CA bundle must still verify, not give up (#406) ---


class _Store:
    """Stands in for ``ssl.SSLContext``: counts roots, records what was loaded."""

    def __init__(self, roots):
        self.roots, self.loaded = roots, []

    def cert_store_stats(self):
        return {"x509_ca": self.roots}

    def load_verify_locations(self, cafile=None):
        self.loaded.append(cafile)


def _tls(monkeypatch, tmp_path, roots, bundles):
    import ssl
    store = _Store(roots)
    monkeypatch.setattr(ssl, "create_default_context", lambda: store)
    monkeypatch.setattr(rerank, "SYSTEM_CA_BUNDLES", tuple(str(tmp_path / b) for b in bundles))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    return store


def test_a_python_that_has_its_roots_is_left_alone(monkeypatch, tmp_path):
    (tmp_path / "os.pem").write_text("x", encoding="utf-8")
    store = _tls(monkeypatch, tmp_path, roots=140, bundles=["os.pem"])
    assert rerank.tls_context() is store and store.loaded == []


def test_a_python_with_no_roots_borrows_the_operating_systems(monkeypatch, tmp_path):
    (tmp_path / "second.pem").write_text("x", encoding="utf-8")
    store = _tls(monkeypatch, tmp_path, roots=0, bundles=["missing.pem", "second.pem"])
    rerank.tls_context()
    assert store.loaded == [str(tmp_path / "second.pem")]


def test_an_explicit_ssl_cert_file_is_the_users_choice(monkeypatch, tmp_path):
    (tmp_path / "os.pem").write_text("x", encoding="utf-8")
    store = _tls(monkeypatch, tmp_path, roots=0, bundles=["os.pem"])
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "theirs.pem"))
    rerank.tls_context()
    assert store.loaded == []


def test_no_bundle_anywhere_still_hands_back_a_verifying_context(monkeypatch, tmp_path):
    store = _tls(monkeypatch, tmp_path, roots=0, bundles=["missing.pem"])
    assert rerank.tls_context() is store and store.loaded == []


def test_verification_is_never_switched_off():
    import ssl
    context = rerank.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True


def test_the_client_sends_its_request_through_that_context(monkeypatch):
    seen = {}

    class _Reply:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"answers": {}}'

    def fake_urlopen(request, timeout=None, context=None):
        seen["context"], seen["auth"] = context, request.get_header("Authorization")
        return _Reply()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    sentinel = object()
    monkeypatch.setattr(rerank, "tls_context", lambda: sentinel)
    rerank.typesafe_client("k", model="m", timeout=1.0)({}, {})
    assert seen["context"] is sentinel and seen["auth"] == "Bearer k"
