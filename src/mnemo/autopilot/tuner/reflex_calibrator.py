"""Reflex per-project threshold calibrator — carried injections, measured.

Writes ``.mnemo/reflex-config.{project}.json``, which
:mod:`mnemo.core.reflex.project_config` merges over global config before the
gates run. Those files therefore have the last word on ``relative_gap``,
``absolute_floor`` and ``term_overlap_min``, which is why what this module
targets matters more than how it interpolates.

The target
----------
**Carried injections, counted by** :mod:`mnemo.core.reflex.replay` — a rule
that fired on a prompt and had been extracted from an *earlier* session. It
is the only bucket the vault can take credit for; *hindsight* (extracted from
the very session it fires in) and *not yet learned* cannot be claimed. The
knob is only moved when the measured carried curve has a **maximum strictly
inside** the safe range and the peak clears the 95% Wilson interval of the
carried rate at the value currently in force.

What it replaced, and why (#333)
--------------------------------
Until 2026-09-16 this module interpolated all three thresholds against a
**3–12% emit rate** band. Nothing in the repo recorded where that band came
from, and emit rate cannot tell carried from hindsight — it counts prompts
where *something* was injected. Worse, the band was fitted while
``relative_gap`` was mis-posed (#332), so it encoded the old gate's behaviour
as the goal: measured live off ``reflex-log.jsonl``, the emit rate is 5.5% at
``relative_gap`` 1.3276 and 6.7% at 1.5 (both inside the band) but **20.0%**
at 1.15, so the band's own rule was to tighten 1.15 back toward 1.5.

Why the calibrator now usually declines
---------------------------------------
Measured 2026-09-16 over the whole vault (2708 prompts, 1937 rules, replayed
through the hook's own :func:`mnemo.core.reflex.decide.decide`), **carried is
monotone in both knobs on every project with enough data** — it only ever
falls as a threshold tightens. ``relative_gap`` vs carried:

======  ========  =====  ==========  =====
  gap   clubinho  mnemo  clearframe  meunu
======  ========  =====  ==========  =====
1.10         237     78          57     83
1.15         224     64          39     72
1.25         145     40          30     50
1.3276       122     29          22     37
1.50          77     18          10     22
3.00           7      2           0      1
======  ========  =====  ==========  =====

``absolute_floor`` behaves the same way: flat below 2.0 (it binds on almost
nothing there) and strictly destructive above it — on ``mnemo``, floor 5.0
keeps 46 of the 64 carried that floor 2.0 keeps.

A monotone curve has no interior maximum, so hill-climbing it lands on
whichever bound the objective's sign points at: the old emit-rate band chose
the *tight* bound and cost 2–3x carried; a naive "maximise carried" would
choose the *loose* bound, which is a design decision about whether the gate
should exist at all (#332) rather than a per-project tuning outcome. Either
way the number the calibrator would write is set by the edge of an arbitrary
safe range, not by the data. So it writes nothing and says so. If a future
vault does show a real interior peak, it will take it.

A file carrying ``"pinned": true`` is never rewritten, whatever the curve says.

Never modifies core reflex modules — only writes reflex-config files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from mnemo.core.log_utils import iter_rotated_rows
from mnemo.core.reflex.gates import DEFAULT_THRESHOLDS

# Default config mirrors gates.DEFAULT_THRESHOLDS
DEFAULT_REFLEX_CONFIG = None  # set after class definition

# Pre-scoring skips: reflex never ran the gates on these, so they are NOT
# threshold rejections. Counting them in the emit-rate denominator deflates
# the rate and lets a chatty project hide behind a fake-low number. Exclude
# them; report over the prompts that were actually scored. `all_exported`
# belongs in this set for a different reason: the gates DID run and DID pick a
# winner, but the prompt was still answered — by the rules file Claude Code
# already loaded, not by reflex.
_DEAD_END_REASONS = frozenset({"index_missing", "below_min_tokens", "all_exported"})

# Minimum replayed prompts required before a project is tuned at all. Below
# this, a carried count is noise dressed as a number
# (`honest-sample-size-blocks-tuning`).
MIN_ELIGIBLE_PROMPTS = 100


@dataclass
class ReflexStats:
    """Parsed statistics from reflex-log.jsonl for one project.

    Descriptive only since #333 — the emit rate is printed as "what actually
    happened", never used as a calibration target.
    """
    project: str
    total_prompts: int
    emitted_count: int
    silence_reasons: dict  # reason -> count
    days_covered: int

    @property
    def eligible_prompts(self) -> int:
        """Prompts that were actually scored (total minus pre-scoring skips)."""
        dead = sum(self.silence_reasons.get(r, 0) for r in _DEAD_END_REASONS)
        return self.total_prompts - dead

    @property
    def emit_rate(self) -> float:
        eligible = self.eligible_prompts
        if eligible <= 0:
            return 0.0
        return self.emitted_count / eligible


@dataclass
class ReflexConfig:
    """Proposed calibrated thresholds for one project."""
    project: str
    relative_gap: float
    absolute_floor: float
    min_tokens: int
    pinned: bool = False

    def to_dict(self) -> dict:
        d = {
            "project": self.project,
            "relative_gap": self.relative_gap,
            "absolute_floor": self.absolute_floor,
            "min_tokens": self.min_tokens,
        }
        if self.pinned:
            d["pinned"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ReflexConfig":
        return cls(
            project=str(d["project"]),
            relative_gap=float(d["relative_gap"]),
            absolute_floor=float(d["absolute_floor"]),
            min_tokens=int(d["min_tokens"]),
            pinned=bool(d.get("pinned", False)),
        )


# Set after class definitions
DEFAULT_REFLEX_CONFIG = ReflexConfig(
    project="__default__",
    relative_gap=float(DEFAULT_THRESHOLDS.get("relative_gap", 1.0)),
    absolute_floor=float(DEFAULT_THRESHOLDS.get("absolute_floor", 2.0)),
    min_tokens=int(DEFAULT_THRESHOLDS.get("term_overlap_min", 2)),
)

# Safe bounds for calibrated thresholds. 1.0 is gap-off, the shipped default
# since #332, so it is part of the sweep: an interior peak must beat the gate
# being off, or the calibrator would raise a project's gap above the default
# on a curve that never measured the default.
_REL_GAP_MIN = 1.0
_REL_GAP_MAX = 3.0
_ABS_FLOOR_MIN = 0.5
_ABS_FLOOR_MAX = 5.0

# The values swept per knob. They span the safe range end to end so that a
# peak found strictly inside it is a peak of the whole range, not of the grid.
GAP_CANDIDATES: tuple[float, ...] = (1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4, 1.5, 1.75, 2.0, 2.5, 3.0)
FLOOR_CANDIDATES: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)

#: knob name → (candidate grid, safe bounds)
KNOBS: dict = {
    "relative_gap": (GAP_CANDIDATES, (_REL_GAP_MIN, _REL_GAP_MAX)),
    "absolute_floor": (FLOOR_CANDIDATES, (_ABS_FLOOR_MIN, _ABS_FLOOR_MAX)),
}

# Reasons a project is left alone. Printed, and written to the report.
NO_CHANGE_PINNED = "pinned"
NO_CHANGE_INSUFFICIENT = "insufficient_data"
NO_CHANGE_MONOTONE = "monotone"
NO_CHANGE_NOISE = "within_noise"
CHANGE_PEAK = "interior_peak"


# ---------------------------------------------------------------------------
# Log parsing — descriptive only
# ---------------------------------------------------------------------------

def analyze_reflex_log(
    *,
    vault_root: Path,
    project: Optional[str] = None,
    window_days: int = 30,
) -> dict:
    """Parse reflex-log.jsonl and return per-project ReflexStats.

    Args:
        vault_root: Vault root containing .mnemo/reflex-log.jsonl.
        project: If set, only include this project. Otherwise all projects.
        window_days: Only include entries from the last N days.

    Returns:
        Dict mapping project name → ReflexStats.
        Empty dict if log is missing or empty.
    """
    log_path = vault_root / ".mnemo" / "reflex-log.jsonl"

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    # Aggregate: project → {total, emitted, reasons}
    agg: dict[str, dict] = {}

    # iter_rotated_rows also reads log_path.1: rotation at 1MB means a
    # window_days-wide cutoff can straddle both files. It also now tolerates
    # a torn or undecodable line by skipping it, like every other reader —
    # previously a bad byte here raised and aborted calibration outright.
    for entry in iter_rotated_rows(log_path):
        proj = entry.get("project", "")
        if project is not None and proj != project:
            continue

        # Parse timestamp and apply window filter
        ts_str = entry.get("ts", "")
        if ts_str:
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                if ts < cutoff:
                    continue
            except ValueError:
                pass  # keep entry if unparseable

        if proj not in agg:
            agg[proj] = {"total": 0, "emitted": 0, "reasons": {}}
        agg[proj]["total"] += 1

        emitted = entry.get("emitted", [])
        if emitted:  # non-empty list = emitted
            agg[proj]["emitted"] += 1
        else:
            reason = entry.get("silence_reason")
            if reason:
                agg[proj]["reasons"][reason] = agg[proj]["reasons"].get(reason, 0) + 1

    result: dict[str, ReflexStats] = {}
    for proj_name, data in agg.items():
        result[proj_name] = ReflexStats(
            project=proj_name,
            total_prompts=data["total"],
            emitted_count=data["emitted"],
            silence_reasons=data["reasons"],
            days_covered=window_days,
        )
    return result


# ---------------------------------------------------------------------------
# The measurement — carried, per knob value, from the hook's own decision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CurvePoint:
    """One replayed threshold value for one project."""
    value: float
    prompts: int       # replayed prompts for this project (constant per curve)
    fired: int         # prompts where at least one rule survived
    injections: int
    carried: int
    hindsight: int


def _base_overrides(vault_root: Path, project: str) -> dict:
    from mnemo.core.reflex.project_config import load_project_thresholds
    return load_project_thresholds(vault_root, project)


def carried_curves(
    *,
    vault_root: Path,
    prompts=None,
    index=None,
    facts=None,
    reflex_cfg: Optional[dict] = None,
    knobs: Optional[dict] = None,
) -> dict:
    """Replay the vault once per candidate value and count carried per project.

    One :func:`mnemo.core.reflex.replay.run` per candidate covers every
    project at once — a project's decision depends only on its own overrides,
    so slicing the injections by project gives the same curve as sweeping each
    project separately (verified on this vault, #333) at a fraction of the
    cost. Everything not being swept stays at the value the project actually
    runs with, so the curve answers "what would moving *this* knob do".

    Returns ``{project: {knob: [CurvePoint, ...]}}``.
    """
    from mnemo.core import config as cfg_mod
    from mnemo.core.reflex import replay as rp
    from mnemo.core.reflex.index import build_index

    if reflex_cfg is None:
        reflex_cfg = cfg_mod.load_config().get("reflex") or {}
    if prompts is None:
        prompts = rp.collect_prompts(vault_root)
    if not prompts:
        return {}
    if index is None:
        index = build_index(vault_root)
    if not index.get("doc_count"):
        return {}
    if facts is None:
        facts = rp.rule_facts(vault_root)

    knobs = knobs or KNOBS
    seen_projects = {p.project for p in prompts}
    prompt_counts = {proj: sum(1 for p in prompts if p.project == proj) for proj in seen_projects}
    base = {proj: _base_overrides(vault_root, proj) for proj in seen_projects}

    out: dict = {proj: {knob: [] for knob in knobs} for proj in seen_projects}
    for knob, (candidates, _bounds) in knobs.items():
        for value in candidates:
            def overrides_for(project: str, knob=knob, value=value) -> dict:
                ov = dict(base.get(project) or {})
                ov[knob] = value
                return ov

            result = rp.run(
                prompts, index, facts,
                reflex_cfg=reflex_cfg, overrides_for=overrides_for,
            )
            per_project: dict[str, dict] = {}
            for inj in result.injections:
                slot = per_project.setdefault(
                    inj.project, {"inj": 0, "carried": 0, "hindsight": 0, "fired": set()}
                )
                slot["inj"] += 1
                slot["fired"].add((inj.session_id, inj.ts))
                if inj.bucket == rp.CARRIED:
                    slot["carried"] += 1
                elif inj.bucket == rp.HINDSIGHT:
                    slot["hindsight"] += 1
            for proj in seen_projects:
                slot = per_project.get(proj) or {"inj": 0, "carried": 0, "hindsight": 0, "fired": set()}
                out[proj][knob].append(CurvePoint(
                    value=float(value),
                    prompts=prompt_counts[proj],
                    fired=len(slot["fired"]),
                    injections=slot["inj"],
                    carried=slot["carried"],
                    hindsight=slot["hindsight"],
                ))
    return out


def pick_from_curve(
    points: list,
    *,
    current: Optional[float] = None,
) -> tuple:
    """Choose a knob value from its measured carried curve.

    Returns ``(value, reason)``; ``value`` is ``None`` when the knob is to be
    left alone. The rule, in full:

    - the maximum must sit **strictly inside** the swept range — an endpoint
      that matches it, even as a tie, means the curve never turned over, and
      the value written would be whichever bound the safe range happens to
      have rather than anything the data chose (see the module docstring).
    - the peak must clear the **95% Wilson upper bound** of the carried rate
      at the value currently in force. Without that, a one-injection wobble on
      a 300-prompt project reads as an improvement.
    """
    from mnemo.core.reflex.replay import wilson_interval

    pts = sorted(points, key=lambda p: p.value)
    if len(pts) < 3:
        return (None, NO_CHANGE_MONOTONE)

    top = max(p.carried for p in pts)
    if pts[0].carried >= top or pts[-1].carried >= top:
        return (None, NO_CHANGE_MONOTONE)
    # Among the interior points that reach the maximum, prefer the one that
    # moves the knob least.
    anchor = current if current is not None else pts[len(pts) // 2].value
    best = min((p for p in pts[1:-1] if p.carried == top), key=lambda p: abs(p.value - anchor))

    baseline = None
    if current is not None:
        baseline = min(pts, key=lambda p: abs(p.value - current))
    if baseline is None:
        baseline = pts[-1]
    if baseline.prompts <= 0:
        return (None, NO_CHANGE_INSUFFICIENT)

    _lo, hi = wilson_interval(baseline.carried, baseline.prompts)
    if best.carried <= hi * baseline.prompts:
        return (None, NO_CHANGE_NOISE)
    return (best.value, CHANGE_PEAK)


def calibrate_thresholds(
    project: str,
    *,
    curves: dict,
    current: Optional[ReflexConfig] = None,
) -> tuple:
    """Propose thresholds for ``project`` from its measured carried curves.

    Returns ``(config, reasons)`` where ``config`` is ``None`` when nothing is
    to be written, and ``reasons`` maps each knob to why it moved or did not.
    """
    knob_curves = curves.get(project) or {}
    if not knob_curves:
        return (None, {k: NO_CHANGE_INSUFFICIENT for k in KNOBS})

    any_curve = next(iter(knob_curves.values()), [])
    if not any_curve or any_curve[0].prompts < MIN_ELIGIBLE_PROMPTS:
        return (None, {k: NO_CHANGE_INSUFFICIENT for k in KNOBS})

    if current is not None and current.pinned:
        return (None, {k: NO_CHANGE_PINNED for k in KNOBS})

    base = current or DEFAULT_REFLEX_CONFIG
    chosen: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for knob in KNOBS:
        points = knob_curves.get(knob) or []
        current_value = getattr(base, knob, None)
        value, reason = pick_from_curve(points, current=current_value)
        reasons[knob] = reason
        if value is not None:
            chosen[knob] = value

    if not chosen:
        return (None, reasons)

    return (
        ReflexConfig(
            project=project,
            relative_gap=float(chosen.get("relative_gap", base.relative_gap)),
            absolute_floor=float(chosen.get("absolute_floor", base.absolute_floor)),
            min_tokens=base.min_tokens,
        ),
        reasons,
    )


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _reflex_config_path(project: str, vault_root: Path) -> Path:
    return vault_root / ".mnemo" / f"reflex-config.{project}.json"


def is_pinned(project: str, vault_root: Path) -> bool:
    """True when the project's file says a human set these values by hand."""
    current = load_reflex_config(project, vault_root)
    return bool(current and current.pinned)


def write_reflex_config(config: ReflexConfig, vault_root: Path) -> bool:
    """Atomically write ReflexConfig to .mnemo/reflex-config.{project}.json.

    Refuses — returning False, writing nothing — when the file on disk carries
    ``"pinned": true``. A hand-set threshold outranks any measurement.
    """
    target = _reflex_config_path(config.project, vault_root)
    if not config.pinned and is_pinned(config.project, vault_root):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    return True


def load_reflex_config(project: str, vault_root: Path) -> Optional[ReflexConfig]:
    """Load ReflexConfig from .mnemo/reflex-config.{project}.json. Returns None if missing."""
    path = _reflex_config_path(project, vault_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ReflexConfig.from_dict(data)
    except (FileNotFoundError, KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# PR opening
# ---------------------------------------------------------------------------

def open_reflex_calibration_pr(
    per_project: dict,
    *,
    vault_root: Path,
    dry_run: bool = False,
) -> int:
    """Propose or apply per-project reflex configs.

    Args:
        per_project: Dict mapping project name → ReflexConfig | None.
            None entries (no measured peak, pinned, or too little data)
            are skipped.
        vault_root: Vault root for budget tracking.
        dry_run: If True, print proposals without writing files.

    Returns:
        -2  — skipped (kill switch off or budget exhausted)
        -1  — dry run (printed proposals, no writes)
         0  — configs written successfully
    """
    # Filter valid configs
    valid = {proj: cfg for proj, cfg in per_project.items() if cfg is not None}

    if dry_run:
        if not valid:
            print("[reflex-calibrator] [dry-run] No valid per-project configs to propose.")
        else:
            for proj, cfg in sorted(valid.items()):
                print(
                    f"[reflex-calibrator] [dry-run] Proposed config for {proj}:\n"
                    f"  relative_gap={cfg.relative_gap:.3f}\n"
                    f"  absolute_floor={cfg.absolute_floor:.3f}\n"
                    f"  min_tokens={cfg.min_tokens}\n"
                )
        return -1

    if not valid:
        return -1

    # Gate on kill switch + budget
    from mnemo.autopilot.core.pr_budget import can_open, record_opened
    ok, reason = can_open(vault_root=vault_root, category="reflex_calibration")
    if not ok:
        print(f"[reflex-calibrator] Skipping: {reason}")
        return -2

    wrote = False
    for proj, cfg in sorted(valid.items()):
        if write_reflex_config(cfg, vault_root):
            wrote = True
            print(f"[reflex-calibrator] Wrote reflex-config.{proj}.json")
        else:
            print(f"[reflex-calibrator] {proj}: pinned by hand — left alone")

    if not wrote:
        return -1
    record_opened(vault_root=vault_root, category="reflex_calibration", pr_number=0)
    return 0
