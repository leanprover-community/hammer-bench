"""Command implementations for hammer-bench CLI."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .core import (
    get_hammer_bench_dir,
    get_mathlib_dir,
    get_runs_dir,
    get_git_commit,
    get_git_ref,
    get_lean_toolchain,
    Message,
)
from .runner import (
    execute_run,
    get_run_config,
    load_presets,
    parse_queue_entry,
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
    hammer_dir = get_hammer_bench_dir()
    queue_file = hammer_dir / "queue.txt"
    mathlib_dir = get_mathlib_dir()

    if not mathlib_dir.exists():
        print("Error: mathlib4 not initialized. Run 'hammer-bench init' first.", file=sys.stderr)
        return 1

    if not queue_file.exists():
        print("Queue is empty")
        return 0

    # Read queue and find next pending entry
    lines = queue_file.read_text().splitlines()
    pending_entries = []
    pending_indices = []

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        pending_entries.append(line_stripped)
        pending_indices.append(i)

    if not pending_entries:
        print("No pending runs in queue")
        return 0

    # Process entries
    processed = 0
    while pending_entries:
        entry = pending_entries.pop(0)
        idx = pending_indices.pop(0)

        print(f"\n{'='*60}")
        print(f"Processing queue entry: {entry}")
        print(f"{'='*60}\n")

        # Parse the entry
        try:
            preset_name, provider_name, fraction = parse_queue_entry(entry)
            config = get_run_config(preset_name, provider_name, fraction)
        except ValueError as e:
            print(f"Error parsing queue entry '{entry}': {e}", file=sys.stderr)
            continue

        # Execute the run
        try:
            metadata = execute_run(config, dry_run=args.dry_run)
        except Exception as e:
            print(f"Error executing run: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            continue

        # Mark as done in queue
        if not args.dry_run and metadata:
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            lines[idx] = f"#done:{timestamp}: {entry}"
            queue_file.write_text("\n".join(lines) + "\n")
            print(f"\nMarked '{entry}' as done in queue")

        processed += 1

        if args.once:
            break

    print(f"\nProcessed {processed} run(s)")
    return 0


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
    runs_dir = get_runs_dir()
    run1_dir = runs_dir / args.run1
    run2_dir = runs_dir / args.run2

    # Validate runs exist
    for run_id, run_dir in [(args.run1, run1_dir), (args.run2, run2_dir)]:
        if not run_dir.exists():
            print(f"Run not found: {run_id}", file=sys.stderr)
            return 1
        if not (run_dir / "messages.jsonl").exists():
            print(f"Messages file not found for run: {run_id}", file=sys.stderr)
            return 1

    # Load messages from both runs
    def load_messages(run_dir):
        messages = {}
        with open(run_dir / "messages.jsonl") as f:
            for line in f:
                msg = Message.from_dict(json.loads(line))
                # Key by location + original tactic
                key = f"{msg.file}:{msg.row}:{msg.col}:{msg.original}"
                if key not in messages:
                    messages[key] = []
                messages[key].append(msg)
        return messages

    msgs1 = load_messages(run1_dir)
    msgs2 = load_messages(run2_dir)

    # Load metadata
    with open(run1_dir / "metadata.json") as f:
        meta1 = json.load(f)
    with open(run2_dir / "metadata.json") as f:
        meta2 = json.load(f)

    # Compute statistics
    keys1 = set(msgs1.keys())
    keys2 = set(msgs2.keys())
    common = keys1 & keys2
    only_in_1 = keys1 - keys2
    only_in_2 = keys2 - keys1

    # Count by replacement tactic
    def count_by_replacement(messages):
        counts = {}
        for key, msg_list in messages.items():
            for msg in msg_list:
                counts[msg.replacement] = counts.get(msg.replacement, 0) + 1
        return counts

    counts1 = count_by_replacement(msgs1)
    counts2 = count_by_replacement(msgs2)
    all_tactics = sorted(set(counts1.keys()) | set(counts2.keys()))

    # Output based on format
    if args.format == "json":
        result = {
            "run1": {"id": args.run1, "total": len(keys1), "by_tactic": counts1},
            "run2": {"id": args.run2, "total": len(keys2), "by_tactic": counts2},
            "common": len(common),
            "only_in_run1": len(only_in_1),
            "only_in_run2": len(only_in_2),
        }
        print(json.dumps(result, indent=2))
    elif args.format == "csv":
        print("tactic,run1,run2,diff")
        for tactic in all_tactics:
            c1 = counts1.get(tactic, 0)
            c2 = counts2.get(tactic, 0)
            print(f"{tactic},{c1},{c2},{c2-c1}")
        print(f"TOTAL,{len(keys1)},{len(keys2)},{len(keys2)-len(keys1)}")
    else:  # markdown
        print(f"# Comparison: {args.run1} vs {args.run2}\n")
        print(f"## Metadata\n")
        print(f"| | Run 1 | Run 2 |")
        print(f"|---|---|---|")
        print(f"| Run ID | {args.run1} | {args.run2} |")
        print(f"| Base | {meta1.get('base_ref', 'N/A')} | {meta2.get('base_ref', 'N/A')} |")
        print(f"| Commit | {meta1.get('base_commit', 'N/A')[:12]} | {meta2.get('base_commit', 'N/A')[:12]} |")
        print(f"| Duration | {meta1.get('duration_seconds', 'N/A')}s | {meta2.get('duration_seconds', 'N/A')}s |")
        print(f"| Status | {meta1.get('status', 'N/A')} | {meta2.get('status', 'N/A')} |")
        print()
        print(f"## Summary\n")
        print(f"| Metric | Run 1 | Run 2 | Diff |")
        print(f"|---|---:|---:|---:|")
        print(f"| Total messages | {len(keys1)} | {len(keys2)} | {len(keys2)-len(keys1):+d} |")
        print(f"| Common locations | {len(common)} | {len(common)} | - |")
        print(f"| Only in Run 1 | {len(only_in_1)} | - | - |")
        print(f"| Only in Run 2 | - | {len(only_in_2)} | - |")
        print()
        print(f"## By Tactic\n")
        print(f"| Tactic | Run 1 | Run 2 | Diff |")
        print(f"|---|---:|---:|---:|")
        for tactic in all_tactics:
            c1 = counts1.get(tactic, 0)
            c2 = counts2.get(tactic, 0)
            diff = c2 - c1
            print(f"| {tactic} | {c1} | {c2} | {diff:+d} |")

    return 0


def cmd_validate(args) -> int:
    """Validate consistency between identical runs.

    This checks if two runs with the same configuration produce
    identical results, which is important for reproducibility.
    """
    runs_dir = get_runs_dir()
    run1_dir = runs_dir / args.run1
    run2_dir = runs_dir / args.run2

    # Validate runs exist
    for run_id, run_dir in [(args.run1, run1_dir), (args.run2, run2_dir)]:
        if not run_dir.exists():
            print(f"Run not found: {run_id}", file=sys.stderr)
            return 1
        if not (run_dir / "messages.jsonl").exists():
            print(f"Messages file not found for run: {run_id}", file=sys.stderr)
            return 1

    # Load metadata
    with open(run1_dir / "metadata.json") as f:
        meta1 = json.load(f)
    with open(run2_dir / "metadata.json") as f:
        meta2 = json.load(f)

    # Check if configurations match
    config1 = meta1.get("config", {})
    config2 = meta2.get("config", {})

    print(f"Validating: {args.run1} vs {args.run2}\n")

    # Check base commit
    if meta1.get("base_commit") != meta2.get("base_commit"):
        print(f"WARNING: Different base commits:")
        print(f"  Run 1: {meta1.get('base_commit', 'N/A')[:12]}")
        print(f"  Run 2: {meta2.get('base_commit', 'N/A')[:12]}")
        print()

    # Check config
    if config1 != config2:
        print(f"WARNING: Different configurations:")
        print(f"  Run 1: {config1.get('preset_name', 'N/A')}")
        print(f"  Run 2: {config2.get('preset_name', 'N/A')}")
        print()

    # Load messages
    def load_messages_set(run_dir):
        """Load messages as a set of (file, row, col, original, replacement) tuples."""
        messages = set()
        with open(run_dir / "messages.jsonl") as f:
            for line in f:
                msg = json.loads(line)
                # Ignore timing for consistency check
                key = (msg["file"], msg["row"], msg["col"], msg["original"], msg["replacement"])
                messages.add(key)
        return messages

    msgs1 = load_messages_set(run1_dir)
    msgs2 = load_messages_set(run2_dir)

    # Compare
    only_in_1 = msgs1 - msgs2
    only_in_2 = msgs2 - msgs1
    common = msgs1 & msgs2

    print(f"Results:")
    print(f"  Messages in Run 1: {len(msgs1)}")
    print(f"  Messages in Run 2: {len(msgs2)}")
    print(f"  Common messages: {len(common)}")
    print(f"  Only in Run 1: {len(only_in_1)}")
    print(f"  Only in Run 2: {len(only_in_2)}")
    print()

    if len(only_in_1) == 0 and len(only_in_2) == 0:
        print("PASS: Runs are perfectly consistent")
        return 0
    else:
        consistency = len(common) / max(len(msgs1), len(msgs2)) * 100
        print(f"FAIL: Runs are not consistent ({consistency:.2f}% agreement)")
        print()

        # Show some differences
        if only_in_1:
            print(f"Sample messages only in Run 1 (showing up to 5):")
            for msg in list(only_in_1)[:5]:
                print(f"  {msg[0]}:{msg[1]}:{msg[2]}: {msg[3]} -> {msg[4]}")
        print()
        if only_in_2:
            print(f"Sample messages only in Run 2 (showing up to 5):")
            for msg in list(only_in_2)[:5]:
                print(f"  {msg[0]}:{msg[1]}:{msg[2]}: {msg[3]} -> {msg[4]}")

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
