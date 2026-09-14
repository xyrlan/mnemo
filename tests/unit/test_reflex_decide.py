"""The pure reflex decision the hook and ``mnemo replay`` share.

Parity with the hook is what matters: every branch here mirrors a
``_log_silence`` reason in ``hooks/user_prompt_submit.py``, in the same order.
"""
from __future__ import annotations

from mnemo.core.reflex import decide as D
from mnemo.core.reflex.index import build_index

_CFG = {"enabled": True}


def _vault(tmp_path):
    """One strong rule scoped to project ``alpha`` plus IDF noise, universal off."""
    feedback = tmp_path / "shared" / "feedback"
    feedback.mkdir(parents=True)
    (feedback / "use-prisma-mock.md").write_text(
        "---\nname: use-prisma-mock\n"
        "description: Always use jest-mock-extended to mock Prisma in tests\n"
        "tags:\n  - prisma\n  - testing\n"
        "sources:\n  - bots/alpha/memory/mock.md\n"
        "stability: stable\n---\n"
        "Mock the Prisma client in tests using jest-mock-extended.\n",
        encoding="utf-8",
    )
    for i, (name, desc, tag) in enumerate([
        ("use-yarn", "Prefer yarn over npm for installs", "yarn"),
        ("commit-strategy", "Small atomic commits with clear messages", "git"),
        ("review-etiquette", "Be kind and specific in code reviews", "review"),
        ("python-style", "Follow PEP8 and black formatting", "python"),
        ("docs-style", "Write clear, concise documentation", "docs"),
    ]):
        (feedback / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\ntags:\n  - {tag}\n"
            f"sources:\n  - bots/noise{i}/memory/x.md\nstability: stable\n---\n"
            f"Body for {name}.\n",
            encoding="utf-8",
        )
    return tmp_path


PROMPT = "How do I mock prisma in a jest test with typescript"


def test_short_prompt_is_rejected_before_the_index_is_touched(tmp_path):
    calls = []

    def loader():
        calls.append(1)
        return None

    d = D.decide(loader, project="alpha", prompt="ok", reflex_cfg=_CFG)

    assert d.silence_reason == "below_min_tokens"
    assert calls == [], "the hook never paid for the index on a two-word prompt"
    assert d.scores == [] and d.thresholds == {}, "no receipt when ranking never ran"


def test_missing_index_is_index_missing(tmp_path):
    d = D.decide(None, project="alpha", prompt=PROMPT, reflex_cfg=_CFG)
    assert d.silence_reason == "index_missing"
    assert d.scores == [] and d.thresholds == {}


def test_a_project_with_no_rules_in_scope_is_index_missing(tmp_path):
    index = build_index(_vault(tmp_path))
    d = D.decide(index, project="nobody", prompt=PROMPT, reflex_cfg=_CFG)
    assert d.silence_reason == "index_missing"
    assert d.index is index


def test_confident_match_is_accepted_with_a_receipt(tmp_path):
    index = build_index(_vault(tmp_path))
    d = D.decide(index, project="alpha", prompt=PROMPT, reflex_cfg=_CFG)

    assert d.accepted == ["use-prisma-mock"]
    assert d.silence_reason is None
    assert d.scores[0][0] == "use-prisma-mock"
    assert d.thresholds["doc_count"] == 6
    # Young vault: the floor is scaled and both values ride on the receipt.
    assert d.thresholds["absolute_floor"] == 2.0
    assert d.thresholds["absolute_floor_effective"] < 2.0


def test_a_loader_is_called_once_the_pregate_passes(tmp_path):
    index = build_index(_vault(tmp_path))
    calls = []

    def loader():
        calls.append(1)
        return index

    d = D.decide(loader, project="alpha", prompt=PROMPT, reflex_cfg=_CFG)
    assert calls == [1]
    assert d.index is index


def test_project_overrides_win_over_config_per_key(tmp_path):
    index = build_index(_vault(tmp_path))
    cfg = {"thresholds": {"absoluteFloor": 1.0, "relativeGap": 1.5}}
    d = D.decide(index, project="alpha", prompt=PROMPT, reflex_cfg=cfg,
                 overrides={"absolute_floor": 99.0})

    assert d.silence_reason == "absolute_floor_fail"
    assert d.thresholds["absolute_floor"] == 99.0
    assert d.thresholds["relative_gap"] == 1.5


def test_precomputed_doc_tokens_give_the_same_decision(tmp_path):
    index = build_index(_vault(tmp_path))
    all_tokens = D.doc_token_sets(index)

    per_call = D.decide(index, project="alpha", prompt=PROMPT, reflex_cfg=_CFG)
    precomputed = D.decide(index, project="alpha", prompt=PROMPT, reflex_cfg=_CFG,
                           doc_tokens=all_tokens)

    assert precomputed.accepted == per_call.accepted
    assert precomputed.scores == per_call.scores
    assert set(all_tokens) == set(index["docs"])


def test_doc_token_sets_restricted_to_slugs(tmp_path):
    index = build_index(_vault(tmp_path))
    only = D.doc_token_sets(index, ["use-yarn"])
    assert set(only) == {"use-yarn"}
    assert "yarn" in only["use-yarn"]
