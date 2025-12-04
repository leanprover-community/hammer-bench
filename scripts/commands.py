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
    get_repo_dir,
    get_runs_dir,
    get_worktrees_dir,
    get_git_commit,
    get_git_ref,
    get_lean_toolchain,
    Message,
    SourceSpec,
)
from .runner import (
    checkout_source,
    execute_run,
    get_run_config,
    load_presets,
    parse_queue_file,
    QueueEntry,
    QueueFile,
)


def cmd_init(args) -> int:
    """Initialize the hammer-bench environment."""
    hammer_dir = get_hammer_bench_dir()
    worktrees_dir = get_worktrees_dir()
    mathlib_dir = get_mathlib_dir()

    print(f"Initializing hammer-bench in {hammer_dir}")

    # Create directories if needed
    (hammer_dir / "runs").mkdir(parents=True, exist_ok=True)
    (hammer_dir / "config").mkdir(parents=True, exist_ok=True)
    worktrees_dir.mkdir(parents=True, exist_ok=True)

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
    queue_path = get_hammer_bench_dir() / "queue.yaml"
    queue = parse_queue_file(queue_path)

    if args.queue_command == "add":
        # Add a run to the queue
        entry = QueueEntry.parse(args.preset)
        queue.entries.append(entry)
        queue.save()
        print(f"Added '{args.preset}' to queue")
        return 0

    elif args.queue_command == "clear":
        # Clear pending runs (keep completed ones)
        count = len(queue.entries)
        queue.entries = []
        queue.save()
        print(f"Cleared {count} pending runs")
        return 0

    else:
        # Default: list queue
        if queue.source:
            print(f"Source: {queue.source}")
            print()

        print(f"Pending: {len(queue.entries)}")
        for entry in queue.entries:
            desc = entry.preset
            if entry.targets:
                desc += f"@{entry.targets}"
            if entry.provider:
                desc += f":{entry.provider}"
            if entry.fraction:
                desc += f"/{entry.fraction}"
            print(f"  - {desc}")

        print(f"Completed: {len(queue.completed)}")
        return 0


def cmd_run(args) -> int:
    """Execute benchmark runs from the queue."""
    hammer_dir = get_hammer_bench_dir()
    queue_path = hammer_dir / "queue.yaml"

    if not queue_path.exists():
        print("Queue file not found. Create queue.yaml first.")
        return 0

    # Parse queue file
    queue = parse_queue_file(queue_path)

    if not queue.entries:
        print("No pending runs in queue")
        return 0

    # Checkout source if specified
    if queue.source:
        print(f"Source: {queue.source}")
        try:
            checkout_source(queue.source)
        except Exception as e:
            print(f"Error checking out source: {e}", file=sys.stderr)
            return 1
        print()

    # Process entries
    processed = 0
    while queue.entries:
        entry = queue.entries[0]

        print(f"\n{'='*60}")
        print(f"Processing: {entry.preset}" +
              (f"@{entry.targets}" if entry.targets else "") +
              (f":{entry.provider}" if entry.provider else "") +
              (f"/{entry.fraction}" if entry.fraction else ""))
        print(f"{'='*60}\n")

        # Build config
        try:
            config = get_run_config(entry.preset, entry.provider, entry.fraction, entry.targets)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            queue.entries.pop(0)
            continue

        # Execute the run
        try:
            metadata = execute_run(config, dry_run=args.dry_run, source=queue.source)
        except Exception as e:
            print(f"Error executing run: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            queue.entries.pop(0)
            continue

        # Mark as done
        if not args.dry_run and metadata:
            completed_entry = queue.entries.pop(0)
            queue.completed.append({
                "preset": completed_entry.preset,
                "targets": completed_entry.targets,
                "provider": completed_entry.provider,
                "fraction": completed_entry.fraction,
                "completed_at": datetime.now().isoformat(),
                "run_id": metadata.run_id,
            })
            queue.save()
            print(f"\nMarked as completed in queue")
        else:
            queue.entries.pop(0)

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
            with open(metadata_file, encoding="utf-8") as f:
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

    with open(metadata_file, encoding="utf-8") as f:
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
        with open(run_dir / "messages.jsonl", encoding="utf-8") as f:
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
    with open(run1_dir / "metadata.json", encoding="utf-8") as f:
        meta1 = json.load(f)
    with open(run2_dir / "metadata.json", encoding="utf-8") as f:
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
    with open(run1_dir / "metadata.json", encoding="utf-8") as f:
        meta1 = json.load(f)
    with open(run2_dir / "metadata.json", encoding="utf-8") as f:
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
        with open(run_dir / "messages.jsonl", encoding="utf-8") as f:
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
        max_msgs = max(len(msgs1), len(msgs2))
        if max_msgs == 0:
            print("PASS: Both runs have zero messages (trivially consistent)")
            return 0
        consistency = len(common) / max_msgs * 100
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
    subprocess.run(["git", "fetch", "--all", "--tags"], cwd=mathlib_dir, check=True)

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
    """Clean up old runs older than specified number of days."""
    import shutil
    from datetime import timedelta

    runs_dir = get_runs_dir()
    if not runs_dir.exists():
        print("No runs directory found")
        return 0

    cutoff_date = datetime.now() - timedelta(days=args.days)

    runs_to_delete = []
    runs_to_keep = []
    incomplete_runs = []

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue

        metadata_file = run_dir / "metadata.json"

        # Check for incomplete runs (no metadata yet)
        if not metadata_file.exists():
            incomplete_runs.append(run_dir)
            continue

        # Load metadata
        with open(metadata_file, encoding="utf-8") as f:
            metadata = json.load(f)

        # Use completed_at if available, else started_at
        timestamp_str = metadata.get("completed_at") or metadata.get("started_at")
        if not timestamp_str:
            incomplete_runs.append(run_dir)
            continue

        run_time = datetime.fromisoformat(timestamp_str)

        # Check if run is older than cutoff
        if run_time < cutoff_date:
            runs_to_delete.append((run_dir, run_time, metadata))
        else:
            runs_to_keep.append((run_dir, run_time, metadata))

    # Report what we found
    print(f"Cleanup runs older than {args.days} days (before {cutoff_date.date()})")
    print()
    print(f"Summary:")
    print(f"  Runs to delete: {len(runs_to_delete)}")
    print(f"  Runs to keep: {len(runs_to_keep)}")
    if incomplete_runs:
        print(f"  Incomplete runs (skipping): {len(incomplete_runs)}")
    print()

    if not runs_to_delete:
        print("No runs to delete")
        return 0

    # Show what would be deleted
    if runs_to_delete:
        print("Runs to delete:")
        for run_dir, run_time, metadata in runs_to_delete:
            status = metadata.get("status", "unknown")
            msg_count = metadata.get("message_count", 0)
            print(f"  {run_dir.name} [{status}] {msg_count} messages ({run_time.date()})")
        print()

    # If dry-run, stop here
    if args.dry_run:
        print("(dry-run mode - no files deleted)")
        return 0

    # Confirm before deleting (unless --force)
    if not args.force:
        if sys.stdin.isatty():
            print(f"Delete {len(runs_to_delete)} runs? (y/N): ", end="", flush=True)
            response = input().strip().lower()
            if response != 'y':
                print("Cancelled")
                return 0
        else:
            print("Non-interactive mode: use --force to delete without confirmation")
            return 0

    # Delete runs
    deleted_size = 0
    for run_dir, run_time, metadata in runs_to_delete:
        size = sum(f.stat().st_size for f in run_dir.rglob('*') if f.is_file())
        shutil.rmtree(run_dir)
        deleted_size += size

    print(f"Deleted {len(runs_to_delete)} runs, freed {deleted_size / 1024:.1f}KB")
    return 0


# Self-test configuration
# Uses a branch with tryAtEachStepFromEnv linter (not yet in mainline mathlib4)
# Original PR: https://github.com/leanprover-community/mathlib4/pull/32415 (closed)
# Can be overridden with --source argument
DEFAULT_SELFTEST_SOURCE = "kim-em/mathlib4@feat/tryAtEachStepFromEnv"
SELFTEST_TESTS = [
    # (preset, targets, description)
    ("omega", "arithmetic_test", "omega on Nat.Basic"),
    ("decide", "quick_test", "decide on Logic.Basic"),
]


def cmd_selftest(args) -> int:
    """Run self-tests to verify hammer-bench is working correctly.

    This runs a few small tests on a fixed commit and compares the output
    with expected results. Used for CI validation.
    """
    import tempfile
    import shutil

    hammer_dir = get_hammer_bench_dir()
    expected_dir = hammer_dir / "tests" / "expected"

    print("=" * 60)
    print("hammer-bench self-test")
    print("=" * 60)
    print()

    # Parse the source spec (use CLI argument or default)
    source_str = args.source if args.source else DEFAULT_SELFTEST_SOURCE
    source = SourceSpec.parse(source_str)
    print(f"Test source: {source}")
    print()

    # Checkout the test source
    print("Checking out test source...")
    try:
        checkout_source(source)
    except Exception as e:
        print(f"Error checking out source: {e}", file=sys.stderr)
        return 1
    print()

    # Create a temporary runs directory for the tests
    with tempfile.TemporaryDirectory(prefix="hammer-bench-selftest-") as temp_runs_dir:
        temp_runs_path = Path(temp_runs_dir)
        print(f"Using temporary runs directory: {temp_runs_path}")
        print()

        # Run each test
        all_passed = True
        results = []

        for preset, targets, description in SELFTEST_TESTS:
            print(f"\n{'='*60}")
            print(f"Test: {description}")
            print(f"  Preset: {preset}, Targets: {targets}")
            print(f"{'='*60}\n")

            try:
                config = get_run_config(preset, None, None, targets)
                # Override timeout for quick tests
                config.build_timeout_hours = 0.5  # 30 minutes max

                # Execute the run (use temp directory to avoid polluting global runs)
                metadata = execute_run(config, dry_run=args.dry_run, source=source,
                                       runs_dir=temp_runs_path)

                if args.dry_run:
                    results.append((description, "SKIP", "dry run"))
                    continue

                if metadata is None:
                    results.append((description, "FAIL", "run returned None"))
                    all_passed = False
                    continue

                if metadata.status != "completed":
                    results.append((description, "FAIL", f"status={metadata.status}"))
                    all_passed = False
                    continue

                # Check expected output if it exists
                test_name = f"{preset}_{targets}"
                expected_file = expected_dir / f"{test_name}.jsonl"

                if expected_file.exists():
                    # Load expected messages
                    expected_messages = set()
                    with open(expected_file, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:  # Skip empty lines
                                continue
                            msg = json.loads(line)
                            # Key by location + replacement (ignore timing)
                            key = (msg["file"], msg["row"], msg["col"], msg["replacement"])
                            expected_messages.add(key)

                    # Load actual messages
                    actual_messages = set()
                    run_dir = temp_runs_path / metadata.run_id
                    messages_file = run_dir / "messages.jsonl"
                    if not messages_file.exists():
                        results.append((description, "FAIL", "messages.jsonl not found"))
                        all_passed = False
                        continue
                    with open(messages_file, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:  # Skip empty lines
                                continue
                            msg = json.loads(line)
                            key = (msg["file"], msg["row"], msg["col"], msg["replacement"])
                            actual_messages.add(key)

                    # Compare
                    missing = expected_messages - actual_messages
                    extra = actual_messages - expected_messages

                    if missing or extra:
                        results.append((description, "FAIL",
                            f"output mismatch: {len(missing)} missing, {len(extra)} extra"))
                        all_passed = False
                        if missing:
                            print(f"  Missing messages (expected but not found):")
                            for m in list(missing)[:3]:
                                print(f"    {m}")
                        if extra:
                            print(f"  Extra messages (found but not expected):")
                            for m in list(extra)[:3]:
                                print(f"    {m}")
                    else:
                        results.append((description, "PASS",
                            f"{len(actual_messages)} messages match"))
                else:
                    # No expected file - just report count
                    results.append((description, "PASS",
                        f"{metadata.message_count} messages (no expected file)"))
                    if not args.dry_run:
                        print(f"  Note: No expected output file at {expected_file}")
                        print(f"  To create one, copy the messages.jsonl from this run")

            except Exception as e:
                import traceback
                traceback.print_exc()
                results.append((description, "FAIL", str(e)))
                all_passed = False

        # Print summary
        print()
        print("=" * 60)
        print("Self-test results:")
        print("=" * 60)
        for description, status, detail in results:
            icon = "✓" if status == "PASS" else ("○" if status == "SKIP" else "✗")
            print(f"  {icon} {description}: {status} ({detail})")

        print()
        if all_passed:
            print("All tests passed!")
            return 0
        else:
            print("Some tests failed!")
            return 1
