# Required Mathlib Changes for Hammer Benchmarking

This document describes the minimal changes needed in Mathlib to support
the hammer benchmarking tool. These should be merged to `master` so the
tool can target any commit.

## Status

- [ ] PR opened: (link)
- [ ] PR merged: (commit hash)
- [ ] Minimum supported commit: (commit hash)

## Required Changes

### 1. Timing in tryAtEachStep Messages

**File:** `Mathlib/Tactic/TacticAnalysis/Declarations.lean`

**Change:** Add elapsed time measurement to the "can be replaced with" message.

**Before:**
```lean
/-- Run a tactic at each proof step. -/
def Mathlib.TacticAnalysis.tryAtEachStep (tac : Syntax → MVarId → CommandElabM (TSyntax `tactic)) : TacticAnalysis.Config where
  run seq := do
    let fraction := linter.tacticAnalysis.tryAtEachStep.fraction.get (← getOptions)
    for i in seq do
      if let [goal] := i.tacI.goalsBefore then
        if (hash goal) % fraction = 0 then
          let tac ← tac i.tacI.stx goal
          let goalsAfter ← try
            i.runTacticCode goal tac
          catch _e =>
            pure [goal]
          if goalsAfter.isEmpty then
            logInfoAt i.tacI.stx m!"`{i.tacI.stx}` can be replaced with `{tac}`"
```

**After:**
```lean
/-- Run a tactic at each proof step, with timing.

Reports elapsed time in milliseconds for each successful replacement.
To limit tactic runtime, use `set_option maxHeartbeats N` in the build command.
-/
def Mathlib.TacticAnalysis.tryAtEachStep (tac : Syntax → MVarId → CommandElabM (TSyntax `tactic)) : TacticAnalysis.Config where
  run seq := do
    let fraction := linter.tacticAnalysis.tryAtEachStep.fraction.get (← getOptions)
    for i in seq do
      if let [goal] := i.tacI.goalsBefore then
        if (hash goal) % fraction = 0 then
          let tac ← tac i.tacI.stx goal
          let startTime ← IO.monoMsNow
          let goalsAfter ← try
            i.runTacticCode goal tac
          catch _e =>
            pure [goal]
          let elapsedMs := (← IO.monoMsNow) - startTime
          if goalsAfter.isEmpty then
            logInfoAt i.tacI.stx m!"`{i.tacI.stx}` can be replaced with `{tac}` ({elapsedMs}ms)"
```

**Rationale:** Enables timing analysis without requiring any external instrumentation.
The message format `(NNNms)` is easily parseable by the benchmarking tool.

**Diff:**
```diff
-/-- Run a tactic at each proof step. -/
+/-- Run a tactic at each proof step, with timing.
+
+Reports elapsed time in milliseconds for each successful replacement.
+To limit tactic runtime, use `set_option maxHeartbeats N` in the build command.
+-/
 def Mathlib.TacticAnalysis.tryAtEachStep (tac : Syntax → MVarId → CommandElabM (TSyntax `tactic)) : TacticAnalysis.Config where
   run seq := do
     let fraction := linter.tacticAnalysis.tryAtEachStep.fraction.get (← getOptions)
     for i in seq do
       if let [goal] := i.tacI.goalsBefore then
         if (hash goal) % fraction = 0 then
           let tac ← tac i.tacI.stx goal
+          let startTime ← IO.monoMsNow
           let goalsAfter ← try
             i.runTacticCode goal tac
           catch _e =>
             pure [goal]
+          let elapsedMs := (← IO.monoMsNow) - startTime
           if goalsAfter.isEmpty then
-            logInfoAt i.tacI.stx m!"`{i.tacI.stx}` can be replaced with `{tac}`"
+            logInfoAt i.tacI.stx m!"`{i.tacI.stx}` can be replaced with `{tac}` ({elapsedMs}ms)"
```

## Non-Required Changes

These changes are **NOT** needed in Mathlib master. The benchmarking tool
handles them via runtime patching:

- Enabling/disabling specific linters (patched in lakefile.lean)
- Setting the sampling fraction (patched in lakefile.lean)
- Configuring suggestion providers (patched in Mathlib/Init.lean)

## Testing the Changes

To verify the changes work:

```bash
# Build a small module with the linter enabled
lake build Mathlib.Logic.Basic \
  -Klinter.tacticAnalysis.tryAtEachStepGrind=true \
  2>&1 | grep "can be replaced with" | head -5

# Should see output like:
# info: Mathlib/Logic/Basic.lean:47:55: `rfl` can be replaced with `grind` (2ms)
# info: Mathlib/Logic/Basic.lean:51:12: `cases h₁` can be replaced with `grind` (3ms)
```

## Compatibility

Once these changes are merged, the hammer benchmarking tool can target:
- Any commit after the merge
- Any nightly-testing branch that includes these changes
- Any release tag (v4.X.Y) that includes these changes

The tool will fail gracefully if targeting an older commit without the timing
infrastructure, with a clear error message.

## Current Location

The timing changes are currently on the `hammer_measurements` branch of mathlib4,
based off `nightly-testing-2025-12-01`.

To extract a clean PR:
1. Create a new branch from `master`
2. Cherry-pick only the timing change (the diff above)
3. Open PR with title: "feat(TacticAnalysis): add timing to tryAtEachStep messages"
