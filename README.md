# Hammer Benchmarking Tool

A tool for systematically benchmarking Lean 4 "hammer" tactics (grind, simp_all, aesop, etc.)
across Mathlib to measure their effectiveness at replacing existing proof tactics.

## Quick Start

### Prerequisites
- Python 3.10+
- Git
- Lake (comes with Lean toolchain)

### Setup
```bash
cd ~/hammer-bench
./bin/hammer-bench init
```

This creates a git worktree of mathlib4 at `~/hammer-bench/mathlib4/`.

### Running Your First Benchmark
```bash
# Add a benchmark to the queue
./bin/hammer-bench queue add grind_only

# Run the benchmark (this takes several hours for full Mathlib)
./bin/hammer-bench run
```

### Viewing Results
```bash
# List completed runs
./bin/hammer-bench list

# Show summary for a specific run
./bin/hammer-bench show 2025-12-04T10-30-00_grind_abc123

# Compare two runs
./bin/hammer-bench compare RUN1 RUN2
```

## Concepts

### Tactics
The tool measures these hammer tactics:
- `grind` - General automation tactic
- `simp_all` - Simplification tactic
- `aesop` - Proof search tactic
- `grind +suggestions` - grind with library suggestions enabled
- `simp_all? +suggestions` - simp_all with suggestions

### Suggestion Providers
When using `+suggestions` variants, you can configure different suggestion providers
that determine which lemmas are suggested to the tactic.

### Presets
Named configurations in `config/presets.yaml`:
- `grind_only` - Just grind, full coverage
- `simp_all_only` - Just simp_all, full coverage
- `grind_f10` - grind at 10% sampling (faster)
- `coverage_all` - All tactics together (for coverage analysis)

## Configuration

### config/presets.yaml
```yaml
grind_only:
  linters:
    tryAtEachStepGrind: true
  fraction: 1
  timing_mode: true
  build_timeout_hours: 6

grind_f10:
  linters:
    tryAtEachStepGrind: true
  fraction: 10
  timing_mode: true
  build_timeout_hours: 3
```

### config/providers.yaml
```yaml
providers:
  default:
    command: null  # No set_library_suggestions

  sineQuaNon:
    command: "Lean.LibrarySuggestions.sineQuaNonSelector"

  disabled:
    command: "fun _ _ => pure #[]"
```

## Queue System

The queue is a simple text file (`queue.txt`):
```
# Pending runs (one per line)
grind_only
simp_all_only:sineQuaNon    # preset:provider

# Completed runs are marked:
#done:2025-12-04T10-30-00: grind_only
```

## Output Format

Each run produces:
- `metadata.json` - Configuration, timing, machine info
- `messages.jsonl` - All "can be replaced with" messages
- `build.log.gz` - Compressed build output
- `checksums.sha256` - File integrity

### Message Format
Each line in `messages.jsonl`:
```json
{"file": "Mathlib/Data/List/Basic.lean", "row": 123, "col": 5, "original": "simp", "replacement": "grind", "time_ms": 45}
```

## Analysis

### Comparing Runs
```bash
./bin/hammer-bench compare run1 run2
```

Output:
```
## Comparison: run1 vs run2

| Tactic    | Run1 Only | Run2 Only | Both | Neither |
|-----------|-----------|-----------|------|---------|
| grind     | 234       | 45        | 1245 | 8976    |
| simp_all  | 156       | 23        | 987  | 9334    |
```

### Validating Consistency
Run the same config twice and compare:
```bash
./bin/hammer-bench queue add grind_only
./bin/hammer-bench queue add grind_only
./bin/hammer-bench run
./bin/hammer-bench validate run1 run2
```

## Reproducing Results

To reproduce a benchmark:
1. Clone this repository
2. Run `./bin/hammer-bench init`
3. Check out the same base: `./bin/hammer-bench rebase nightly-testing-2025-12-01`
4. Add the same config to queue and run

## Extending

### Adding a New Tactic
1. Add the linter to `Mathlib/Tactic/TacticAnalysis/Declarations.lean`
2. Add preset(s) to `config/presets.yaml`

### Adding a New Suggestion Provider
1. Define the selector function in Lean
2. Add entry to `config/providers.yaml`

## Metadata Recorded

Each run records:
- Machine name (hostname)
- Base commit (e.g., `nightly-testing-2025-12-01`)
- Lean toolchain version
- Which linters were enabled
- Sampling fraction
- Suggestion provider (if any)
- Total run time
- Message count
- Timeout settings and whether timeout was hit
- Per-tactic timing (in ms) for each replacement suggestion

## Timeouts

**Build-level timeout**: Prevents runaway builds
```yaml
grind_only:
  build_timeout_hours: 6  # Kill build after 6 hours
```

**Per-tactic timeout**: Control via Lean's maxHeartbeats option:
```bash
lake build Mathlib -KmaxHeartbeats=200000000
```

**Timing data**: Each "can be replaced with" message includes timing:
```
`simp` can be replaced with `grind` (45ms)
```

This enables timing analysis:
```bash
./bin/hammer-bench stats run1 --timing
# Shows: mean, median, p95, p99 timing per tactic
```

## Required Mathlib Changes

See [MATHLIB.md](MATHLIB.md) for the minimal changes required in Mathlib to support
this benchmarking tool. These should be merged to Mathlib master so the tool can
target any commit.

## License

MIT License - see Mathlib for licensing of the underlying code.
