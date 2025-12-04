# Hammer Benchmarking Tool

A tool for systematically benchmarking Lean 4 "hammer" tactics (grind, simp_all, aesop, omega, etc.) across Mathlib and downstream projects to measure their effectiveness at replacing existing proof tactics.

## Quick Start

### Prerequisites
- Python 3.10+
- Git
- Elan/Lake (Lean toolchain)

### Setup
```bash
cd ~/hammer-bench

# Set up Python virtual environment (required)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Initialize mathlib4 worktree
./bin/hammer-bench init
```

The `bin/hammer-bench` script automatically uses `.venv` if it exists.

This clones mathlib4 to `~/hammer-bench/worktrees/mathlib4/`.

### Running a Benchmark

Edit `queue.yaml`:
```yaml
source: mathlib4@master

queue:
  - grind@quick_test
```

Then run:
```bash
./bin/hammer-bench run
```

### Viewing Results
```bash
# List completed runs
./bin/hammer-bench list

# Show details for a run
./bin/hammer-bench show <run_id>

# Compare two runs
./bin/hammer-bench compare <run1> <run2>
```

## Queue Format

The queue is a YAML file (`queue.yaml`):

```yaml
# Source repository and ref
source: mathlib4-nightly-testing@hammer_measurements

queue:
  # String shorthand: preset@targets:provider/fraction
  - grind@quick_test
  - omega@logic/100

  # Or explicit format
  - preset: simp_all
    targets: algebra_group
    provider: sineQuaNon
    fraction: 50

# Completed runs (managed automatically)
completed:
  - preset: grind
    targets: quick_test
    completed_at: 2025-12-04T10:30:00
    run_id: 2025-12-04T10-30-00_grind_abc1234
```

### Source Specification

Two formats are supported:

1. **Short name** (from `config/repos.yaml`):
   ```yaml
   source: mathlib4@master
   source: mathlib4-nightly-testing@hammer_measurements
   ```

2. **Full GitHub path**:
   ```yaml
   source: leanprover-community/mathlib4@v4.14.0
   source: my-org/my-lean-project@main
   ```

Short names must be defined in `config/repos.yaml`.

## Configuration

### config/presets.yaml

Presets define which tactic to run and build parameters:

```yaml
grind:
  customTactic: grind
  fraction: 1
  timing_mode: true
  build_timeout_hours: 6
  description: Run grind at every proof step

omega:
  customTactic: omega
  fraction: 1
  timing_mode: true
  build_timeout_hours: 6

grind_suggestions:
  customTactic: grind +suggestions
  fraction: 1
  timing_mode: true
  build_timeout_hours: 8
```

All presets use the generic `TRY_AT_EACH_STEP_*` mechanism - no Mathlib code changes needed per tactic.

### config/targets.yaml

Target collections for partial builds:

```yaml
all:
  description: Full Mathlib build (default)
  targets:
    - Mathlib

quick_test:
  description: Minimal targets for quick testing
  targets:
    - Mathlib.Logic.Basic

logic:
  description: Core logic modules
  targets:
    - Mathlib.Logic.Basic
    - Mathlib.Logic.Function.Basic
    - Mathlib.Logic.Relation

algebra_group:
  description: Group theory basics
  targets:
    - Mathlib.Algebra.Group.Basic
    - Mathlib.Algebra.Group.Defs
```

### config/repos.yaml

Repository definitions for short names:

```yaml
repos:
  mathlib4:
    url: https://github.com/leanprover-community/mathlib4.git
    default_ref: master
    patch_file: Mathlib/Init.lean

  mathlib4-nightly-testing:
    url: https://github.com/leanprover-community/mathlib4-nightly-testing.git
    default_ref: nightly-testing
    patch_file: Mathlib/Init.lean

  # Add downstream projects:
  # my-project:
  #   url: https://github.com/user/my-lean-project.git
  #   patch_file: MyProject/Init.lean
```

The `patch_file` is used when applying custom suggestion providers.

### config/providers.yaml

Suggestion providers for `+suggestions` variants:

```yaml
providers:
  default:
    command: null
    description: Use default suggestion provider

  sineQuaNon:
    command: Lean.LibrarySuggestions.sineQuaNonSelector
    description: Use the Sine Qua Non suggestion selector

  disabled:
    command: "fun _ _ => pure #[]"
    description: Disable suggestions entirely
```

## Commands

```bash
# Initialize (clone mathlib4)
hammer-bench init

# Queue management
hammer-bench queue              # List queue
hammer-bench queue add grind@quick_test
hammer-bench queue clear

# Run benchmarks
hammer-bench run                # Process entire queue
hammer-bench run --once         # Process one entry
hammer-bench run --dry-run      # Show what would run

# View results
hammer-bench list               # List runs
hammer-bench show <run_id>      # Show run details
hammer-bench compare <r1> <r2>  # Compare runs
hammer-bench validate <r1> <r2> # Check consistency

# Repository management
hammer-bench check-base         # Show current checkout
hammer-bench rebase <tag>       # Checkout different ref

# Testing
hammer-bench selftest           # Run self-tests
hammer-bench selftest --dry-run
```

## Output Format

Each run creates a directory in `~/hammer-bench/runs/<run_id>/`:

- `metadata.json` - Configuration, timing, machine info, source
- `messages.jsonl` - All "can be replaced with" messages
- `build.log.gz` - Compressed build output

### metadata.json
```json
{
  "run_id": "2025-12-04T10-30-00_grind_abc1234",
  "machine": "hostname",
  "base_commit": "abc1234...",
  "base_ref": "master",
  "lean_toolchain": "leanprover/lean4:v4.14.0",
  "source": {"repo": "mathlib4", "ref": "master"},
  "config": {...},
  "status": "completed",
  "duration_seconds": 3600,
  "message_count": 1234
}
```

### messages.jsonl
```json
{"file": "Mathlib/Data/List/Basic.lean", "row": 123, "col": 5, "original": "simp", "replacement": "grind", "time_ms": 45}
```

## Directory Structure

```
~/hammer-bench/
├── bin/hammer-bench        # Entry point script
├── config/
│   ├── presets.yaml        # Tactic configurations
│   ├── providers.yaml      # Suggestion providers
│   ├── repos.yaml          # Repository definitions
│   └── targets.yaml        # Target collections
├── queue.yaml              # Run queue
├── runs/                   # Benchmark results (gitignored)
├── worktrees/              # Repository checkouts (gitignored)
│   ├── mathlib4/
│   └── mathlib4-nightly-testing/
├── scripts/                # Python implementation
└── tests/
    ├── expected/           # Expected outputs for selftest
    └── README.md
```

## Multi-Repository Support

hammer-bench can target any Lean project downstream of Mathlib:

1. Add the repository to `config/repos.yaml`:
   ```yaml
   repos:
     my-project:
       url: https://github.com/user/my-lean-project.git
       default_ref: main
       patch_file: MyProject/Init.lean  # or null
   ```

2. Use it in your queue:
   ```yaml
   source: my-project@main
   queue:
     - grind
   ```

The project must depend on Mathlib to have the tactic analysis linters available.

## Required Mathlib Changes

See [MATHLIB.md](MATHLIB.md) for the changes required in Mathlib to support this tool. These add a generic `tryAtEachStepFromEnv` linter that reads the tactic to run from environment variables.

## Self-Test

Run the test suite to verify the tool works:

```bash
hammer-bench selftest
```

This checks out a known commit and runs small benchmarks, comparing against expected outputs.

## License

MIT License
