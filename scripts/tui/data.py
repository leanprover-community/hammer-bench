"""Data loading and transformation for the TUI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core import get_runs_dir, get_hammer_bench_dir, Message
from ..runner import (
    load_presets,
    load_providers,
    load_targets,
    parse_queue_file,
    QueueEntry,
)


@dataclass
class RunInfo:
    """Information about a single run."""
    run_id: str
    preset: str
    provider: Optional[str]
    status: str  # "completed", "failed", "queued", etc.
    replacements: int
    duration: Optional[int]  # seconds
    target_collection: str
    fraction: int
    base_commit: str
    base_ref: str

    @property
    def preset_provider_key(self) -> str:
        """Key for grouping: 'preset' or 'preset:provider'."""
        if self.provider and self.provider != "default":
            return f"{self.preset}:{self.provider}"
        return self.preset


@dataclass
class FractionGroup:
    """Runs grouped by fraction within a target."""
    fraction: int
    runs: dict[str, RunInfo] = field(default_factory=dict)  # preset_provider_key -> RunInfo


@dataclass
class TargetGroup:
    """Runs grouped by target collection within a commit."""
    target_collection: str
    module_count: Optional[int]  # Number of modules if available
    fractions: dict[int, FractionGroup] = field(default_factory=dict)


@dataclass
class CommitGroup:
    """Runs grouped by commit."""
    commit: str
    ref: str  # Display name (branch/tag or short hash)
    toolchain: str = ""  # e.g., "v4.26.0-rc2"
    first_run_date: str = ""  # e.g., "Dec 5"
    targets: dict[str, TargetGroup] = field(default_factory=dict)

    @property
    def display_label(self) -> str:
        """Human-readable label for this commit group."""
        parts = []
        if self.first_run_date:
            parts.append(self.first_run_date)
        if self.toolchain:
            # Extract version from "leanprover/lean4:v4.26.0-rc2"
            if ":" in self.toolchain:
                version = self.toolchain.split(":")[-1]
            else:
                version = self.toolchain
            parts.append(version)
        parts.append(self.commit[:8])
        return " · ".join(parts)


@dataclass
class HierarchicalRuns:
    """All runs organized hierarchically."""
    commits: dict[str, CommitGroup] = field(default_factory=dict)  # commit hash -> CommitGroup

    def get_run_ids_for_selection(self, selected: set[str]) -> list[str]:
        """Convert selected preset_provider keys to run IDs."""
        run_ids = []
        for commit_group in self.commits.values():
            for target_group in commit_group.targets.values():
                for fraction_group in target_group.fractions.values():
                    for key, run_info in fraction_group.runs.items():
                        if key in selected and run_info.status != "queued":
                            run_ids.append(run_info.run_id)
        return run_ids


def load_runs_hierarchical(include_queued: bool = True) -> HierarchicalRuns:
    """Load all runs organized hierarchically.

    Hierarchy: commit -> target -> fraction -> preset/provider

    Returns:
        HierarchicalRuns with nested structure
    """
    result = HierarchicalRuns()
    runs_dir = get_runs_dir()

    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
            metadata_file = run_dir / "metadata.json"
            if not metadata_file.exists():
                continue

            with open(metadata_file, encoding="utf-8") as f:
                metadata = json.load(f)

            config = metadata.get("config", {})
            linters = config.get("linters", {})

            # Extract fields
            commit = metadata.get("base_commit", "")
            ref = metadata.get("base_ref", commit[:12] if commit else "unknown")
            target_collection = config.get("target_collection", "all")
            fraction = linters.get("fraction", 1)
            preset = config.get("preset_name", "unknown")

            # Get provider
            provider = None
            provider_config = config.get("suggestion_provider")
            if provider_config:
                provider = provider_config.get("name", "default")

            run_info = RunInfo(
                run_id=metadata.get("run_id", run_dir.name),
                preset=preset,
                provider=provider,
                status=metadata.get("status", "unknown"),
                replacements=metadata.get("total_replacements", metadata.get("replacement_count", 0)),
                duration=metadata.get("duration_seconds"),
                target_collection=target_collection,
                fraction=fraction,
                base_commit=commit,
                base_ref=ref,
            )

            # Extract toolchain and date for display
            toolchain = metadata.get("lean_toolchain", "")
            started_at = metadata.get("started_at", "")
            run_date = ""
            if started_at:
                # Parse ISO date and format as "2025-12-05 13:45"
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    run_date = dt.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    pass

            # Build hierarchy
            if commit not in result.commits:
                result.commits[commit] = CommitGroup(
                    commit=commit,
                    ref=ref,
                    toolchain=toolchain,
                    first_run_date=run_date,
                )

            commit_group = result.commits[commit]
            if target_collection not in commit_group.targets:
                targets = config.get("targets", [])
                commit_group.targets[target_collection] = TargetGroup(
                    target_collection=target_collection,
                    module_count=len(targets) if targets else None,
                )

            target_group = commit_group.targets[target_collection]
            if fraction not in target_group.fractions:
                target_group.fractions[fraction] = FractionGroup(fraction=fraction)

            fraction_group = target_group.fractions[fraction]
            key = run_info.preset_provider_key
            # Only keep the most recent run for each preset/provider combo
            if key not in fraction_group.runs:
                fraction_group.runs[key] = run_info

    # Add queued entries
    if include_queued:
        queue_file = get_hammer_bench_dir() / "queue.yaml"
        if queue_file.exists():
            queue = parse_queue_file(queue_file)
            for entry in queue.entries:
                # Queued entries don't have commit info, so we can't place them
                # in the hierarchy properly. We'll need to handle this differently
                # in the UI - showing them separately or under "pending".
                pass

    return result


def load_pending_queue() -> list[QueueEntry]:
    """Load pending queue entries."""
    queue_file = get_hammer_bench_dir() / "queue.yaml"
    if queue_file.exists():
        queue = parse_queue_file(queue_file)
        return queue.entries
    return []


def add_to_queue(
    preset: str,
    targets: Optional[str] = None,
    provider: Optional[str] = None,
    fraction: Optional[int] = None,
) -> None:
    """Add a new entry to the queue."""
    queue_file = get_hammer_bench_dir() / "queue.yaml"
    queue = parse_queue_file(queue_file)
    queue.entries.append(QueueEntry(
        preset=preset,
        targets=targets,
        provider=provider,
        fraction=fraction,
    ))
    queue.save()


def get_default_source() -> Optional[str]:
    """Get the current default source from queue.yaml."""
    queue_file = get_hammer_bench_dir() / "queue.yaml"
    if queue_file.exists():
        queue = parse_queue_file(queue_file)
        if queue.default_source:
            return str(queue.default_source)
    return None


def set_default_source(source: str) -> None:
    """Set the default source in queue.yaml."""
    from ..core import SourceSpec
    queue_file = get_hammer_bench_dir() / "queue.yaml"
    queue = parse_queue_file(queue_file)
    queue.default_source = SourceSpec.parse(source)
    queue.save()


def get_available_presets() -> list[str]:
    """Get list of available preset names for autocomplete."""
    presets = load_presets()
    return sorted(presets.keys())


def get_available_targets() -> list[str]:
    """Get list of available target collection names for autocomplete."""
    targets = load_targets()
    return sorted(targets.keys())


def get_available_providers() -> list[str]:
    """Get list of available provider names for autocomplete."""
    providers = load_providers()
    return sorted(providers.keys())


@dataclass
class ComparisonResult:
    """Structured comparison data."""
    run_ids: list[str]
    base_commit: str
    base_ref: str
    machine: str
    target_collection: str
    fraction: int

    # Per-run data
    preset_names: list[str]
    providers: list[str]
    replacements: list[int]
    durations: list[Optional[int]]

    # Overlap data: maps frozenset of run indices -> count
    overlap_counts: dict[frozenset[int], int] = field(default_factory=dict)

    @property
    def total_locations(self) -> int:
        """Total unique locations across all runs."""
        return sum(self.overlap_counts.values())


def compute_comparison(run_ids: list[str]) -> Optional[ComparisonResult]:
    """Compute comparison data for one or more runs.

    Returns None if runs are not comparable (different commit/machine/targets/fraction).
    """
    if len(run_ids) < 1:
        return None

    runs_dir = get_runs_dir()

    # Load metadata for all runs
    all_meta = []
    for run_id in run_ids:
        metadata_file = runs_dir / run_id / "metadata.json"
        if not metadata_file.exists():
            return None
        with open(metadata_file, encoding="utf-8") as f:
            all_meta.append(json.load(f))

    # Validate comparability
    ref_meta = all_meta[0]
    ref_base = ref_meta.get("base_commit", "")
    ref_machine = ref_meta.get("machine", "")
    ref_config = ref_meta.get("config", {})
    ref_targets = ref_config.get("targets", [])
    ref_target_coll = ref_config.get("target_collection", "all")
    ref_fraction = ref_config.get("linters", {}).get("fraction", 1)

    for meta in all_meta[1:]:
        config = meta.get("config", {})
        if meta.get("base_commit", "") != ref_base:
            return None
        if meta.get("machine", "") != ref_machine:
            return None
        if config.get("targets", []) != ref_targets:
            return None
        if config.get("linters", {}).get("fraction", 1) != ref_fraction:
            return None

    # Load messages from all runs
    all_keys = []
    for run_id in run_ids:
        messages_file = runs_dir / run_id / "messages.jsonl"
        keys = set()
        if messages_file.exists():
            with open(messages_file, encoding="utf-8") as f:
                for line in f:
                    msg = Message.from_dict(json.loads(line))
                    key = f"{msg.file}:{msg.row}:{msg.col}"
                    keys.add(key)
        all_keys.append(keys)

    # Compute overlap
    all_locations = set().union(*all_keys)
    overlap_counts: dict[frozenset[int], int] = {}
    for loc in all_locations:
        combo = frozenset(i for i, keys in enumerate(all_keys) if loc in keys)
        overlap_counts[combo] = overlap_counts.get(combo, 0) + 1

    # Extract per-run data
    def get_provider(meta):
        provider = meta.get("config", {}).get("suggestion_provider")
        if provider:
            return provider.get("name", "default")
        return "default"

    def get_preset(meta):
        return meta.get("config", {}).get("preset_name", "unknown")

    return ComparisonResult(
        run_ids=run_ids,
        base_commit=ref_base,
        base_ref=ref_meta.get("base_ref", ref_base[:12]),
        machine=ref_machine,
        target_collection=ref_target_coll,
        fraction=ref_fraction,
        preset_names=[get_preset(m) for m in all_meta],
        providers=[get_provider(m) for m in all_meta],
        replacements=[m.get("total_replacements", m.get("replacement_count", 0)) for m in all_meta],
        durations=[m.get("duration_seconds") for m in all_meta],
        overlap_counts=overlap_counts,
    )
