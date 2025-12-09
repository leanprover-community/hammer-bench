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


def format_table(headers: list[str], rows: list[list[str]], alignments: list[str] | None = None) -> str:
    """Format a markdown table with proper column padding.

    Args:
        headers: List of header strings
        rows: List of rows, each row is a list of cell strings
        alignments: List of alignments ('l', 'r', 'c') for each column.
                   Defaults to left-aligned.

    Returns:
        Formatted markdown table string
    """
    if not headers:
        return ""

    num_cols = len(headers)
    if alignments is None:
        alignments = ['l'] * num_cols

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                widths[i] = max(widths[i], len(str(cell)))

    # Build separator line based on alignments
    sep_parts = []
    for i, align in enumerate(alignments):
        w = widths[i]
        if align == 'r':
            sep_parts.append('-' * (w + 1) + ':')
        elif align == 'c':
            sep_parts.append(':' + '-' * w + ':')
        else:  # left
            sep_parts.append('-' * (w + 2))

    # Format header
    header_parts = []
    for i, h in enumerate(headers):
        if alignments[i] == 'r':
            header_parts.append(h.rjust(widths[i]))
        else:
            header_parts.append(h.ljust(widths[i]))

    lines = []
    lines.append('| ' + ' | '.join(header_parts) + ' |')
    lines.append('|' + '|'.join(sep_parts) + '|')

    # Format rows
    for row in rows:
        row_parts = []
        for i in range(num_cols):
            cell = str(row[i]) if i < len(row) else ''
            if alignments[i] == 'r':
                row_parts.append(cell.rjust(widths[i]))
            else:
                row_parts.append(cell.ljust(widths[i]))
        lines.append('| ' + ' | '.join(row_parts) + ' |')

    return '\n'.join(lines)


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

    elif args.queue_command == "redo":
        # Re-queue a completed run
        if not queue.completed:
            print("No completed runs to redo")
            return 1

        if args.run_id:
            # Find by run_id (can be partial match)
            matches = [c for c in queue.completed if args.run_id in c.get("run_id", "")]
            if not matches:
                print(f"No completed run matching '{args.run_id}'")
                return 1
            if len(matches) > 1:
                print(f"Multiple matches for '{args.run_id}':")
                for m in matches:
                    print(f"  - {m.get('run_id')}")
                return 1
            completed = matches[0]
        else:
            # Default to most recent
            completed = queue.completed[-1]

        # Create entry from completed run
        entry = QueueEntry(
            preset=completed["preset"],
            targets=completed.get("targets"),
            provider=completed.get("provider"),
            fraction=completed.get("fraction"),
        )
        queue.entries.append(entry)
        queue.save()

        desc = entry.preset
        if entry.targets:
            desc += f"@{entry.targets}"
        if entry.provider:
            desc += f":{entry.provider}"
        if entry.fraction:
            desc += f"/{entry.fraction}"
        print(f"Re-queued: {desc} (from {completed.get('run_id', 'unknown')})")
        return 0

    else:
        # Default: list queue
        if queue.default_source:
            print(f"Default source: {queue.default_source}")
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

    # Checkout default source if specified
    if queue.default_source:
        print(f"Default source: {queue.default_source}")
        try:
            checkout_source(queue.default_source)
        except Exception as e:
            print(f"Error checking out source: {e}", file=sys.stderr)
            return 1
        print()

    # Process entries
    processed = 0
    try:
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
                # Remove invalid entry and save
                queue.entries.pop(0)
                queue.save()
                continue

            # Execute the run
            try:
                metadata = execute_run(config, dry_run=args.dry_run, source=queue.default_source)
            except KeyboardInterrupt:
                # Re-raise to be caught by outer handler
                raise
            except Exception as e:
                print(f"Error executing run: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
                # Keep entry on queue - don't remove on error
                print(f"\nEntry kept on queue. Fix the issue and run './bench run' again.")
                return 1

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

    except KeyboardInterrupt:
        print(f"\n\nInterrupted. Current run aborted, {len(queue.entries)} entries still pending.")
        print("Run './bench run' to restart from the interrupted entry.")
        return 130  # Standard exit code for SIGINT

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
    print(f"  Replacements: {metadata.get('replacement_count', 0)} ({metadata.get('steps_replaced', 0)} steps)")
    print(f"  Timed out: {metadata.get('timed_out', False)}")

    return 0


def cmd_compare(args) -> int:
    """Compare multiple runs."""
    runs_dir = get_runs_dir()
    run_ids = args.runs

    if len(run_ids) < 2:
        print("Error: Need at least 2 runs to compare", file=sys.stderr)
        return 1

    # Validate runs exist and load data
    run_dirs = []
    for run_id in run_ids:
        run_dir = runs_dir / run_id
        if not run_dir.exists():
            print(f"Run not found: {run_id}", file=sys.stderr)
            return 1
        if not (run_dir / "messages.jsonl").exists():
            print(f"Messages file not found for run: {run_id}", file=sys.stderr)
            return 1
        run_dirs.append(run_dir)

    # Load messages from all runs
    def load_messages(run_dir):
        messages = {}
        with open(run_dir / "messages.jsonl", encoding="utf-8") as f:
            for line in f:
                msg = Message.from_dict(json.loads(line))
                # Key by location (file:row:col)
                key = f"{msg.file}:{msg.row}:{msg.col}"
                if key not in messages:
                    messages[key] = []
                messages[key].append(msg)
        return messages

    all_msgs = [load_messages(d) for d in run_dirs]
    all_keys = [set(msgs.keys()) for msgs in all_msgs]

    # Load metadata
    all_meta = []
    for run_dir in run_dirs:
        with open(run_dir / "metadata.json", encoding="utf-8") as f:
            all_meta.append(json.load(f))

    # Output based on format
    if args.format == "json":
        result = {
            "runs": [
                {"id": run_id, "total": len(keys)}
                for run_id, keys in zip(run_ids, all_keys)
            ],
            "all_locations": len(set().union(*all_keys)),
            "common_to_all": len(set.intersection(*all_keys)) if all_keys else 0,
        }
        print(json.dumps(result, indent=2))
    elif args.format == "csv":
        headers = ["location"] + [f"run{i+1}" for i in range(len(run_ids))]
        print(",".join(headers))
        all_locs = sorted(set().union(*all_keys))
        for loc in all_locs:
            row = [loc] + ["1" if loc in keys else "0" for keys in all_keys]
            print(",".join(row))
    else:  # markdown
        # Validate that runs are comparable
        ref_meta = all_meta[0]
        ref_base = ref_meta.get('base_commit', '')
        ref_machine = ref_meta.get('machine', '')
        ref_targets = ref_meta.get('config', {}).get('targets', [])
        ref_target_coll = ref_meta.get('config', {}).get('target_collection', 'all')
        ref_fraction = ref_meta.get('config', {}).get('linters', {}).get('fraction', 1)

        errors = []
        for i, meta in enumerate(all_meta[1:], 2):
            base = meta.get('base_commit', '')
            machine = meta.get('machine', '')
            targets = meta.get('config', {}).get('targets', [])
            fraction = meta.get('config', {}).get('linters', {}).get('fraction', 1)

            if base != ref_base:
                errors.append(f"Run {i}: Base commit mismatch ({base[:12]} vs {ref_base[:12]})")
            if machine != ref_machine:
                errors.append(f"Run {i}: Machine mismatch ({machine} vs {ref_machine})")
            if targets != ref_targets:
                target_coll = meta.get('config', {}).get('target_collection', 'all')
                errors.append(f"Run {i}: Targets mismatch ({target_coll} vs {ref_target_coll})")
            if fraction != ref_fraction:
                errors.append(f"Run {i}: Fraction mismatch ({fraction} vs {ref_fraction})")

        if errors:
            print("ERROR: Runs are not comparable:\n")
            for err in errors:
                print(f"  - {err}")
            print()
            return 1

        # Header with shared info
        print(f"# Comparison\n")
        print(f"Base:     {ref_meta.get('base_ref', ref_base[:12])}")
        print(f"Machine:  {ref_machine}")
        print(f"Targets:  {ref_target_coll}" + (f" ({len(ref_targets)} modules)" if ref_target_coll != 'all' else ""))
        print(f"Fraction: 1/{ref_fraction}" + (" (all)" if ref_fraction == 1 else ""))
        print()

        # Get provider info
        def get_provider(meta):
            provider = meta.get('config', {}).get('suggestion_provider')
            if provider:
                return provider.get('name', 'default')
            return 'default'

        # Build column headers
        col_headers = [''] + [f'Run {i+1}' for i in range(len(run_ids))]

        # Runs table
        print("## Runs\n")
        rows = [
            ['Run ID'] + run_ids,
            ['Preset'] + [m.get('config', {}).get('preset_name', 'N/A') for m in all_meta],
            ['Provider'] + [get_provider(m) for m in all_meta],
            ['Duration'] + [f"{m.get('duration_seconds', 'N/A')}s" for m in all_meta],
            ['Status'] + [m.get('status', 'N/A') for m in all_meta],
        ]
        print(format_table(col_headers, rows, alignments=['l'] * len(col_headers)))
        print()

        # Compute overlap statistics
        all_locations = set().union(*all_keys)
        common_to_all = set.intersection(*all_keys) if all_keys else set()

        # Count how many runs each location appears in
        location_counts = {}
        for loc in all_locations:
            count = sum(1 for keys in all_keys if loc in keys)
            location_counts[loc] = count

        # Results table
        print("## Results\n")
        result_headers = ['Metric'] + [f'Run {i+1}' for i in range(len(run_ids))]
        result_rows = [
            ['Replacements'] + [str(m.get('total_replacements', len(keys))) for m, keys in zip(all_meta, all_keys)],
        ]
        print(format_table(result_headers, result_rows, alignments=['l'] + ['r'] * len(run_ids)))
        print()

        # Overlap summary - show which combination of runs each location appears in
        print("## Overlap\n")

        # Build unique labels for each run
        # Use preset name, but add provider or run number if there are duplicates
        def build_run_labels(all_meta):
            base_names = [m.get('config', {}).get('preset_name', f'run{i+1}') for i, m in enumerate(all_meta)]
            providers = [get_provider(m) for m in all_meta]

            # Check for duplicate preset names
            name_counts = {}
            for name in base_names:
                name_counts[name] = name_counts.get(name, 0) + 1

            labels = []
            name_seen = {}
            for i, (name, provider) in enumerate(zip(base_names, providers)):
                if name_counts[name] > 1:
                    # Duplicate preset - disambiguate with provider or run number
                    if provider != 'default':
                        label = f"{name}:{provider}"
                    else:
                        # Use run number if providers are also the same
                        idx = name_seen.get(name, 0) + 1
                        name_seen[name] = idx
                        label = f"{name} (#{idx})"
                else:
                    label = name
                labels.append(label)
            return labels

        preset_names = build_run_labels(all_meta)

        # Group locations by which runs they appear in (as a frozenset of indices)
        by_combination = {}
        for loc in all_locations:
            combo = frozenset(i for i, keys in enumerate(all_keys) if loc in keys)
            if combo not in by_combination:
                by_combination[combo] = 0
            by_combination[combo] += 1

        # Build rows for the table, sorted by combo size (single, double, triple), then by count descending
        overlap_rows = []
        for combo, count in sorted(by_combination.items(), key=lambda x: (len(x[0]), -x[1])):
            # Build the label showing which presets
            names = [preset_names[i] for i in sorted(combo)]
            label = ' ∩ '.join(names)
            overlap_rows.append([label, str(count)])

        print(format_table(
            ['Runs', 'Locations'],
            overlap_rows,
            alignments=['l', 'r']
        ))

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
            replacement_count = metadata.get("replacement_count", 0)
            print(f"  {run_dir.name} [{status}] {replacement_count} replacements ({run_time.date()})")
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
                        f"{metadata.replacement_count} replacements (no expected file)"))
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
