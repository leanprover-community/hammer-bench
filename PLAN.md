# tryAtEachStep Location Logging Investigation

## Problem
When comparing runs of different tactics (e.g., `grind` vs `grind +suggestions`), we're seeing unexpectedly low overlap (~887 common locations out of ~4000+ each). We suspect the hash-based fraction filtering may be selecting different goals for different runs.

## What We've Done

### 1. Mathlib Changes
- Created branch `feat/tryAtEachStep-logging` in `kim-em/mathlib4`
- Added logging to `tryAtEachStepCore` in `Mathlib/Tactic/TacticAnalysis/Declarations.lean`:
  ```lean
  if (hash goalPP) % fraction = 0 then
    -- Log that we're about to test this location (for verifying consistent sampling)
    logInfoAt i.tacI.stx m!"`tryAtEachStep` running"
    let tac ← tac i.tacI.stx goal
  ```
- This outputs `info: <file>:<row>:<col>: \`tryAtEachStep\` running` before each tactic test

### 2. hammer-bench Changes (committed to main)
- Added `AttemptedLocation` class to `scripts/core.py`
- Added `parse_attempted_locations()` to `scripts/parser.py`
- Updated `scripts/runner.py` to capture and store `attempted.jsonl`
- Added attempted locations comparison to `bench compare` and `bench validate`
- Added `--samples` flag to `bench compare` for viewing diff samples
- Updated TUI to show samples incrementally

### 3. Queue Configuration
Updated `queue.yaml` to:
- Use source: `kim-em/mathlib4@feat/tryAtEachStep-logging`
- Queue: `grind/10` and `grind_suggestions/10`

## Current Status
Benchmark runs are queued and running via nohup.

## Next Steps After Runs Complete

1. **Check the runs completed**:
   ```bash
   ./bench list
   ```

2. **Compare the two runs**:
   ```bash
   ./bench compare <grind_run_id> <grind_suggestions_run_id>
   ```

   This will show:
   - Overlap in replacement messages (where tactics succeeded)
   - Attempted locations comparison (should be 100% if hashing is stable)

3. **If attempted locations differ**:
   - The hash is based on `goalPP` (pretty-printed goal with `pp.mvars false`)
   - Something about the goal state must differ between runs
   - Need to investigate what's causing the difference

4. **If attempted locations are identical but results differ**:
   - The hashing is working correctly
   - The difference is genuine (one tactic succeeds where another fails)
   - This is the expected/desired behavior

5. **Validate with identical runs** (optional):
   ```bash
   # Queue two identical grind runs
   ./bench queue add grind/10
   ./bench queue add grind/10
   ./bench run
   # Then compare - should have 100% overlap in both attempted and results
   ```

## Files Modified

- `Mathlib/Tactic/TacticAnalysis/Declarations.lean` (in kim-em/mathlib4@feat/tryAtEachStep-logging)
- `scripts/core.py` - AttemptedLocation class
- `scripts/parser.py` - parse_attempted_locations()
- `scripts/runner.py` - capture attempted.jsonl
- `scripts/commands.py` - comparison logic
- `scripts/cli.py` - --samples flag
- `scripts/tui/*.py` - TUI updates for samples display
