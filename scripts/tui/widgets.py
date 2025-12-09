"""Custom widgets for the hammer-bench TUI."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from .data import (
    HierarchicalRuns,
    ComparisonResult,
    DiffSample,
    compute_comparison,
)


class RunSelected(Message):
    """Message sent when run selection changes."""

    def __init__(self, selected_run_ids: list[str]) -> None:
        self.selected_run_ids = selected_run_ids
        super().__init__()


class RunTree(Widget):
    """Hierarchical tree of runs with selection support.

    Hierarchy: commit -> target -> fraction -> preset/provider
    """

    DEFAULT_CSS = """
    RunTree {
        width: 100%;
        height: 100%;
    }

    RunTree Tree {
        width: 100%;
        height: 100%;
    }

    RunTree .queued {
        color: $text-muted;
    }
    """

    # Track current selection scope (commit, target, fraction) - selection clears when this changes
    current_scope: reactive[Optional[tuple[str, str, int]]] = reactive(None)

    def __init__(self, runs: HierarchicalRuns, **kwargs) -> None:
        super().__init__(**kwargs)
        self.runs = runs
        self.selected: set[str] = set()  # Set of run_ids
        self._tree: Optional[Tree] = None

    def compose(self) -> ComposeResult:
        tree: Tree[dict] = Tree("Runs", id="run-tree")
        tree.show_root = False
        self._tree = tree
        yield tree

    def on_mount(self) -> None:
        """Build the tree when mounted."""
        self._build_tree()

    def _build_tree(self) -> None:
        """Build the tree structure from runs data."""
        if self._tree is None:
            return

        self._tree.clear()

        # Sort commits by most recent first (based on run timestamps in the data)
        for commit_hash, commit_group in self.runs.commits.items():
            commit_node = self._tree.root.add(
                commit_group.display_label,
                data={"type": "commit", "commit": commit_hash},
                expand=False,
            )

            # Sort targets
            for target_name, target_group in sorted(commit_group.targets.items()):
                target_label = target_name
                if target_group.module_count:
                    target_label += f" ({target_group.module_count} modules)"

                target_node = commit_node.add(
                    target_label,
                    data={"type": "target", "target": target_name, "commit": commit_hash},
                    expand=False,
                )

                # Sort fractions (smallest first)
                for fraction, fraction_group in sorted(target_group.fractions.items()):
                    fraction_label = f"1/{fraction}" if fraction > 1 else "1/1 (all)"

                    fraction_node = target_node.add(
                        fraction_label,
                        data={
                            "type": "fraction",
                            "fraction": fraction,
                            "target": target_name,
                            "commit": commit_hash,
                        },
                        expand=False,
                    )

                    # Sort runs by preset name
                    for key, run_info in sorted(fraction_group.runs.items()):
                        is_queued = run_info.status == "queued"
                        is_selected = run_info.run_id in self.selected

                        # Build label (escape brackets for Rich markup)
                        checkbox = "\\[x]" if is_selected else "\\[ ]"
                        if is_queued:
                            checkbox = "   "  # No checkbox for queued

                        stats = ""
                        if not is_queued:
                            stats = f" ({run_info.replacements}"
                            if run_info.duration:
                                stats += f", {run_info.duration}s"
                            stats += ")"
                        else:
                            stats = " (queued)"

                        label = f"{checkbox} {key}{stats}"

                        run_node = fraction_node.add_leaf(
                            label,
                            data={
                                "type": "run",
                                "run_id": run_info.run_id,
                                "key": key,
                                "queued": is_queued,
                                "commit": commit_hash,
                                "target": target_name,
                                "fraction": fraction,
                            },
                        )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection (toggle checkbox for runs)."""
        node = event.node
        data = node.data

        if data is None:
            return

        if data.get("type") == "run" and not data.get("queued"):
            run_id = data["run_id"]
            commit = data["commit"]
            target = data["target"]
            fraction = data["fraction"]
            new_scope = (commit, target, fraction)

            # Check if selection needs to be cleared (different scope)
            if self.current_scope is not None and self.current_scope != new_scope:
                # Clear selection and update all previously selected labels
                old_selected = self.selected.copy()
                self.selected.clear()
                self._refresh_labels_for_runs(old_selected)

            self.current_scope = new_scope

            # Toggle selection
            if run_id in self.selected:
                self.selected.remove(run_id)
            else:
                self.selected.add(run_id)

            # Update the label
            self._update_run_label(node)

            # Notify about selection change
            self.post_message(RunSelected(list(self.selected)))

    def _update_run_label(self, node: TreeNode) -> None:
        """Update a run node's label to reflect selection state."""
        data = node.data
        if data is None or data.get("type") != "run":
            return

        is_selected = data["run_id"] in self.selected
        is_queued = data.get("queued", False)

        key = data["key"]

        # Note: double brackets escape Rich markup ([ ] would be parsed as tags)
        checkbox = "\\[x]" if is_selected else "\\[ ]"
        if is_queued:
            checkbox = "   "

        # Reconstruct stats (we don't store them, so look them up)
        run_info = None
        commit = data["commit"]
        target = data["target"]
        fraction = data["fraction"]

        if commit in self.runs.commits:
            commit_group = self.runs.commits[commit]
            if target in commit_group.targets:
                target_group = commit_group.targets[target]
                if fraction in target_group.fractions:
                    fraction_group = target_group.fractions[fraction]
                    if key in fraction_group.runs:
                        run_info = fraction_group.runs[key]

        stats = ""
        if run_info and not is_queued:
            stats = f" ({run_info.replacements}"
            if run_info.duration:
                stats += f", {run_info.duration}s"
            stats += ")"
        elif is_queued:
            stats = " (queued)"

        node.set_label(f"{checkbox} {key}{stats}")

    def _refresh_labels_for_runs(self, run_ids: set[str]) -> None:
        """Refresh labels for specific run IDs (used when clearing selection)."""
        if self._tree is None:
            return

        def walk_tree(node: TreeNode) -> None:
            data = node.data
            if data and data.get("type") == "run" and data.get("run_id") in run_ids:
                self._update_run_label(node)
            for child in node.children:
                walk_tree(child)

        walk_tree(self._tree.root)

    def refresh_data(self, runs: HierarchicalRuns) -> None:
        """Refresh the tree with new data."""
        self.runs = runs
        self._build_tree()


class ComparisonPanel(Widget):
    """Panel showing comparison results for selected runs."""

    DEFAULT_CSS = """
    ComparisonPanel {
        width: 100%;
        height: 100%;
        padding: 1;
    }

    ComparisonPanel .title {
        text-style: bold;
        margin-bottom: 1;
    }

    ComparisonPanel .info {
        margin-bottom: 1;
    }

    ComparisonPanel .table {
        margin-bottom: 1;
    }

    ComparisonPanel .no-selection {
        color: $text-muted;
    }
    """

    comparison: reactive[Optional[ComparisonResult]] = reactive(None)
    samples_loaded: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content: Optional[Static] = None
        self._pending_run_ids: Optional[list[str]] = None

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(
            Static("Select a run to view details", classes="no-selection", id="comparison-content"),
        )

    def _build_display(self, comparison: ComparisonResult, include_samples: bool = False) -> str:
        """Build the display string for a comparison."""
        from rich.markup import escape

        lines = []
        num_runs = len(comparison.run_ids)
        if num_runs == 1:
            lines.append(f"[bold]{escape(comparison.preset_names[0])}[/bold]")
        else:
            lines.append(f"[bold]Comparison ({num_runs} runs)[/bold]")
        lines.append("")
        lines.append(f"Base: {escape(comparison.base_ref)}")
        lines.append(f"Machine: {escape(comparison.machine)}")
        lines.append(f"Targets: {escape(comparison.target_collection)}")
        lines.append(f"Fraction: 1/{comparison.fraction}")
        lines.append("")

        # Results table
        lines.append("[bold]Results[/bold]")

        # Header
        preset_headers = "  ".join(f"{escape(p[:10]):>10}" for p in comparison.preset_names)
        lines.append(f"{'Metric':<12} {preset_headers}")
        lines.append("-" * (12 + 2 + len(preset_headers)))

        # Replacements row
        repl_values = "  ".join(f"{r:>10}" for r in comparison.replacements)
        lines.append(f"{'Replacements':<12} {repl_values}")

        # Duration row
        dur_values = "  ".join(
            f"{d:>9}s" if d else f"{'N/A':>10}"
            for d in comparison.durations
        )
        lines.append(f"{'Duration':<12} {dur_values}")

        # Overlap analysis (only for 2+ runs)
        if num_runs >= 2 and comparison.overlap_counts:
            lines.append("")
            lines.append("[bold]Overlap[/bold]")

            # Build labels for each run
            labels = comparison.preset_names

            # Sort by combo size then count
            for combo, count in sorted(
                comparison.overlap_counts.items(),
                key=lambda x: (len(x[0]), -x[1])
            ):
                combo_labels = [escape(labels[i]) for i in sorted(combo)]
                label = " ∩ ".join(combo_labels)
                lines.append(f"  {label}: {count}")

        # Samples section (if available)
        if include_samples and comparison.diff_samples:
            lines.append("")
            lines.append("[bold]Samples[/bold]")

            labels = comparison.preset_names
            for (i, j), samples in comparison.diff_samples.items():
                if samples:
                    lines.append("")
                    lines.append(f"[dim]{escape(labels[i])} succeeded, {escape(labels[j])} failed:[/dim]")
                    for sample in samples:
                        lines.append(f"  • {sample.file}:{sample.row}:{sample.col}")
                        # Truncate long original proofs
                        orig = sample.original
                        if len(orig) > 50:
                            orig = orig[:47] + "..."
                        lines.append(f"    [dim]Original:[/dim] {escape(orig)}")
        elif include_samples and num_runs >= 2:
            lines.append("")
            lines.append("[dim]Loading samples...[/dim]")

        return "\n".join(lines)

    def watch_comparison(self, comparison: Optional[ComparisonResult]) -> None:
        """Update display when comparison changes."""
        content = self.query_one("#comparison-content", Static)

        if comparison is None:
            content.update("Select a run to view details")
            content.add_class("no-selection")
            return

        content.remove_class("no-selection")

        # Check if samples are loaded
        has_samples = bool(comparison.diff_samples)
        display_text = self._build_display(comparison, include_samples=has_samples)
        # Use Text.from_markup to pre-render, avoiding issues with Static.update
        from rich.text import Text
        content.update(Text.from_markup(display_text))

    def watch_samples_loaded(self, loaded: bool) -> None:
        """Update display when samples finish loading."""
        if loaded and self.comparison is not None:
            content = self.query_one("#comparison-content", Static)
            display_text = self._build_display(self.comparison, include_samples=True)
            from rich.text import Text
            content.update(Text.from_markup(display_text))

    def update_selection(self, run_ids: list[str]) -> None:
        """Update comparison based on selected runs."""
        if len(run_ids) < 1:
            self.comparison = None
            self.samples_loaded = False
            self._pending_run_ids = None
        else:
            # First, show comparison without samples (fast)
            self.samples_loaded = False
            self.comparison = compute_comparison(run_ids, include_samples=False)
            self._pending_run_ids = run_ids

            # Then load samples in background (if 2+ runs)
            if len(run_ids) >= 2:
                self.call_later(self._load_samples, run_ids)

    def _load_samples(self, run_ids: list[str]) -> None:
        """Load samples in background and update display."""
        # Check if selection changed while we were waiting
        if self._pending_run_ids != run_ids:
            return

        # Compute comparison with samples
        comparison_with_samples = compute_comparison(run_ids, include_samples=True, sample_count=5)

        # Check again if selection changed
        if self._pending_run_ids != run_ids:
            return

        if comparison_with_samples:
            self.comparison = comparison_with_samples
            self.samples_loaded = True
