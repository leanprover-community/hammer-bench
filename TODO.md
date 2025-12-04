# TODO

## Self-test
- [x] Generate expected test outputs for selftest (saved to `tests/expected/`)
- [x] Enable full selftest in CI (workflow runs both dry-run and full selftest)
- [ ] Improve selftest to run against a Cslib target (better coverage than shallow Mathlib targets)
  - Blocked by: https://github.com/leanprover-community/mathlib4/pull/32415 being merged into Mathlib
  - Then Cslib can depend on a Mathlib version with the linter
- [ ] Switch selftest from `omega` to `lia` (lia is more broadly useful for benchmarking)

## Upstream
- [x] Upstream Mathlib changes: `tryAtEachStepFromEnv` linter that reads tactic from `TRY_AT_EACH_STEP_TACTIC` environment variable
  - PR: https://github.com/leanprover-community/mathlib4/pull/32415

## Code Quality
- [x] Add unit tests for parser module
- [x] Add cross-platform file locking for queue operations
- [x] Add UTF-8 encoding to all file operations
- [x] Auto-detect hammer-bench directory from script location
