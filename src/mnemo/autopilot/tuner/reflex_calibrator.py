"""Reflex per-project threshold calibrator.

Reads reflex-log.jsonl, computes per-project emit-rates, and proposes
calibrated gate thresholds targeting a 5–10% emit-rate.

Never modifies core reflex modules — only writes
.mnemo/reflex-config.{project}.json files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from mnemo.core.reflex.gates import DEFAULT_THRESHOLDS

# Default config mirrors gates.DEFAULT_THRESHOLDS
DEFAULT_REFLEX_CONFIG = None  # set after class definition


@dataclass
class ReflexStats:
    """Parsed statistics from reflex-log.jsonl for one project."""
    project: str
    total_prompts: int
    emitted_count: int
    silence_reasons: dict  # reason -> count
    days_covered: int

    @property
    def emit_rate(self) -> float:
        if self.total_prompts == 0:
            return 0.0
        return self.emitted_count / self.total_prompts


@dataclass
class ReflexConfig:
    """Proposed calibrated thresholds for one project."""
    project: str
    relative_gap: float
    absolute_floor: float
    min_tokens: int

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "relative_gap": self.relative_gap,
            "absolute_floor": self.absolute_floor,
            "min_tokens": self.min_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReflexConfig":
        return cls(
            project=str(d["project"]),
            relative_gap=float(d["relative_gap"]),
            absolute_floor=float(d["absolute_floor"]),
            min_tokens=int(d["min_tokens"]),
        )


# Set after class definitions
DEFAULT_REFLEX_CONFIG = ReflexConfig(
    project="__default__",
    relative_gap=float(DEFAULT_THRESHOLDS.get("relative_gap", 1.5)),
    absolute_floor=float(DEFAULT_THRESHOLDS.get("absolute_floor", 2.0)),
    min_tokens=int(DEFAULT_THRESHOLDS.get("term_overlap_min", 2)),
)

# Target emit-rate range
_TARGET_LOW = 0.03   # 3%
_TARGET_HIGH = 0.12  # 12%
_TARGET_MID = 0.07   # 7% midpoint

# Safe bounds for calibrated thresholds
_REL_GAP_MIN = 1.1
_REL_GAP_MAX = 3.0
_ABS_FLOOR_MIN = 0.5
_ABS_FLOOR_MAX = 5.0


# ---------------------------------------------------------------------------
# Log parsing
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
    if not log_path.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    # Aggregate: project → {total, emitted, reasons}
    agg: dict[str, dict] = {}

    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

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
# Threshold calibration
# ---------------------------------------------------------------------------

def calibrate_thresholds(stats: ReflexStats) -> Optional[ReflexConfig]:
    """Propose calibrated thresholds for a project.

    Returns None if:
    - stats.total_prompts < 100 (insufficient data)

    Otherwise returns a ReflexConfig with thresholds tuned to target
    a 5–10% emit-rate. Uses linear interpolation between the current
    rate and the target midpoint to compute threshold adjustments.
    """
    if stats.total_prompts < 100:
        return None

    rate = stats.emit_rate
    default = DEFAULT_REFLEX_CONFIG

    if rate <= 0.0:
        # Maximally loosen: use lower bounds
        rel_gap = _REL_GAP_MIN
        abs_floor = _ABS_FLOOR_MIN
    elif rate >= 1.0:
        # Maximally tighten: use upper bounds
        rel_gap = _REL_GAP_MAX
        abs_floor = _ABS_FLOOR_MAX
    elif _TARGET_LOW <= rate <= _TARGET_HIGH:
        # Already in target range — return defaults unchanged
        rel_gap = default.relative_gap
        abs_floor = default.absolute_floor
    elif rate < _TARGET_LOW:
        # Rate too low → loosen thresholds
        # Linear interpolation: rate=0 → min, rate=TARGET_LOW → default
        t = rate / _TARGET_LOW  # 0..1
        rel_gap = _REL_GAP_MIN + t * (default.relative_gap - _REL_GAP_MIN)
        abs_floor = _ABS_FLOOR_MIN + t * (default.absolute_floor - _ABS_FLOOR_MIN)
    else:
        # rate > _TARGET_HIGH → tighten thresholds
        # Linear interpolation: rate=TARGET_HIGH → default, rate=1.0 → max
        t = (rate - _TARGET_HIGH) / (1.0 - _TARGET_HIGH)  # 0..1
        rel_gap = default.relative_gap + t * (_REL_GAP_MAX - default.relative_gap)
        abs_floor = default.absolute_floor + t * (_ABS_FLOOR_MAX - default.absolute_floor)

    # Clamp to safe bounds
    rel_gap = max(_REL_GAP_MIN, min(_REL_GAP_MAX, rel_gap))
    abs_floor = max(_ABS_FLOOR_MIN, min(_ABS_FLOOR_MAX, abs_floor))

    return ReflexConfig(
        project=stats.project,
        relative_gap=rel_gap,
        absolute_floor=abs_floor,
        min_tokens=default.min_tokens,
    )


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _reflex_config_path(project: str, vault_root: Path) -> Path:
    return vault_root / ".mnemo" / f"reflex-config.{project}.json"


def write_reflex_config(config: ReflexConfig, vault_root: Path) -> None:
    """Atomically write ReflexConfig to .mnemo/reflex-config.{project}.json."""
    target = _reflex_config_path(config.project, vault_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True))
        os.replace(tmp, target)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def load_reflex_config(project: str, vault_root: Path) -> Optional[ReflexConfig]:
    """Load ReflexConfig from .mnemo/reflex-config.{project}.json. Returns None if missing."""
    path = _reflex_config_path(project, vault_root)
    try:
        data = json.loads(path.read_text())
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
            None entries (insufficient data) are skipped.
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

    for proj, cfg in sorted(valid.items()):
        write_reflex_config(cfg, vault_root)
        print(f"[reflex-calibrator] Wrote reflex-config.{proj}.json")

    record_opened(vault_root=vault_root, category="reflex_calibration", pr_number=0)
    return 0
