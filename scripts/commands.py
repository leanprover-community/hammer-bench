"""Command implementations for hammer-bench CLI."""

import os
import subprocess
import sys
from pathlib import Path

from .core import (
    get_hammer_bench_dir,
    get_mathlib_dir,
    get_runs_dir,
    get_git_commit,
    get_git_ref,
    get_lean_toolchain,
)


def cmd_init(args) -> int:
    """Initialize the hammer-bench environment."""
    hammer_dir = get_hammer_bench_dir()
    mathlib_dir = get_mathlib_dir()

    print(f"Initializing hammer-bench in {hammer_dir}")

    # Create directories if needed
    (hammer_dir / "runs").mkdir(parents=True, exist_ok=True)
    (hammer_dir / "config").mkdir(parents=True, exist_ok=True)

    # Check if mathlib4 already exists
    if mathlib_dir.exists():
        print(f"mathlib4 directory already exists at {mathlib_dir}")
        print(f"  Current commit: {get_git_commit(mathlib_dir)}")
        print(f"  Current ref: {get_git_ref(mathlib_dir)}")
        print(f"  Toolchain: {get_lean_toolchain(mathlib_dir)}")
        return 0

    # Clone mathlib4
    print(f"Cloning mathlib4 to {mathlib_dir}...")
    result = subprocess.run(
        ["git", "clone", args.mathlib_repo, str(mathlib_dir)],
        capture_output=False,
    )
    if result.returncode != 0:
        print("Failed to clone mathlib4", file=sys.stderr)
        return 1

    # Checkout specific base if requested
    if args.base:
        print(f"Checking out {args.base}...")
        result = subprocess.run(
            ["git", "checkout", args.base],
            cwd=mathlib_dir,
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"Failed to checkout {args.base}", file=sys.stderr)
            return 1

    print(f"Initialized successfully!")
    print(f"  Commit: {get_git_commit(mathlib_dir)}")
    print(f"  Ref: {get_git_ref(mathlib_dir)}")
    print(f"  Toolchain: {get_lean_toolchain(mathlib_dir)}")

    return 0


def cmd_queue(args) -> int:
    """Manage the run queue."""
    queue_file = get_hammer_bench_dir() / "queue.txt"

    if args.queue_command == "add":
        # Add a run to the queue
        preset = args.preset
        with open(queue_file, "a") as f:
            f.write(f"{preset}\n")
        print(f"Added '{preset}' to queue")
        return 0

    elif args.queue_command == "clear":
        # Clear pending runs (keep completed ones)
        if not queue_file.exists():
            print("Queue is already empty")
            return 0

        lines = queue_file.read_text().splitlines()
        kept = [line for line in lines if line.startswith("#")]
        queue_file.write_text("\n".join(kept) + "\n" if kept else "")
        print(f"Cleared {len(lines) - len(kept)} pending runs")
        return 0

    else:
        # Default: list queue
        if not queue_file.exists():
            print("Queue is empty")
            return 0

        lines = queue_file.read_text().splitlines()
        pending = []
        completed = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#done:"):
                completed.append(line)
            elif not line.startswith("#"):
                pending.append(line)

        print(f"Pending: {len(pending)}")
        for p in pending:
            print(f"  - {p}")
        print(f"Completed: {len(completed)}")
        return 0


def cmd_run(args) -> int:
    """Execute benchmark runs from the queue."""
    print("Run command not yet implemented")
    print("TODO: Implement run execution with:")
    print("  - Parse queue")
    print("  - Patch lakefile for linter config")
    print("  - Run lake clean && lake build")
    print("  - Capture and parse output")
    print("  - Record results")
    return 1


def cmd_list(args) -> int:
    """List completed runs."""
    runs_dir = get_runs_dir()
    if not runs_dir.exists():
        print("No runs found")
        return 0

    runs = sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True)
    runs = runs[:args.limit]

    if not runs:
        print("No runs found")
        return 0

    print(f"Recent runs (showing {len(runs)}):")
    for run_dir in runs:
        metadata_file = run_dir / "metadata.json"
        if metadata_file.exists():
            import json
            with open(metadata_file) as f:
                metadata = json.load(f)
            status = metadata.get("status", "unknown")
            duration = metadata.get("duration_seconds")
            duration_str = f"{duration}s" if duration else "N/A"
            print(f"  {run_dir.name} [{status}] {duration_str}")
        else:
            print(f"  {run_dir.name} [incomplete]")

    return 0


def cmd_show(args) -> int:
    """Show details for a specific run."""
    runs_dir = get_runs_dir()
    run_dir = runs_dir / args.run_id

    if not run_dir.exists():
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    metadata_file = run_dir / "metadata.json"
    if not metadata_file.exists():
        print(f"Metadata not found for run: {args.run_id}", file=sys.stderr)
        return 1

    import json
    with open(metadata_file) as f:
        metadata = json.load(f)

    print(f"Run: {metadata['run_id']}")
    print(f"  Machine: {metadata['machine']}")
    print(f"  Base: {metadata['base_ref']} ({metadata['base_commit'][:12]})")
    print(f"  Toolchain: {metadata['lean_toolchain']}")
    print(f"  Status: {metadata['status']}")
    print(f"  Duration: {metadata.get('duration_seconds', 'N/A')}s")
    print(f"  Messages: {metadata.get('message_count', 'N/A')}")
    print(f"  Timed out: {metadata.get('timed_out', False)}")

    return 0


def cmd_compare(args) -> int:
    """Compare two runs."""
    print(f"Comparing {args.run1} vs {args.run2}")
    print("Compare command not yet implemented")
    return 1


def cmd_validate(args) -> int:
    """Validate consistency between identical runs."""
    print(f"Validating {args.run1} vs {args.run2}")
    print("Validate command not yet implemented")
    return 1


def cmd_rebase(args) -> int:
    """Rebase the mathlib4 worktree to a new base."""
    mathlib_dir = get_mathlib_dir()
    if not mathlib_dir.exists():
        print("mathlib4 not initialized. Run 'hammer-bench init' first.", file=sys.stderr)
        return 1

    print(f"Rebasing to {args.tag}...")

    # Fetch latest
    subprocess.run(["git", "fetch", "--all", "--tags"], cwd=mathlib_dir)

    # Checkout the new base
    result = subprocess.run(
        ["git", "checkout", args.tag],
        cwd=mathlib_dir,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"Failed to checkout {args.tag}", file=sys.stderr)
        return 1

    print(f"Rebased to:")
    print(f"  Commit: {get_git_commit(mathlib_dir)}")
    print(f"  Ref: {get_git_ref(mathlib_dir)}")
    print(f"  Toolchain: {get_lean_toolchain(mathlib_dir)}")

    return 0


def cmd_check_base(args) -> int:
    """Check the current base of the mathlib4 worktree."""
    mathlib_dir = get_mathlib_dir()
    if not mathlib_dir.exists():
        print("mathlib4 not initialized. Run 'hammer-bench init' first.", file=sys.stderr)
        return 1

    print(f"mathlib4 directory: {mathlib_dir}")
    print(f"  Commit: {get_git_commit(mathlib_dir)}")
    print(f"  Ref: {get_git_ref(mathlib_dir)}")
    print(f"  Toolchain: {get_lean_toolchain(mathlib_dir)}")

    return 0


def cmd_cleanup(args) -> int:
    """Clean up old runs."""
    print(f"Cleanup command not yet implemented (would remove runs older than {args.days} days)")
    return 1
