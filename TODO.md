# TODO

## Self-test
- [x] Generate expected test outputs for selftest (saved to `tests/expected/`)
- [ ] Enable full selftest in CI (currently runs `--dry-run` only)
- [ ] Improve selftest to run against a Cslib target (better coverage than shallow Mathlib targets)
  - Blocked by: https://github.com/leanprover-community/mathlib4/pull/32415 being merged into Mathlib
  - Then Cslib can depend on a Mathlib version with the linter
- [ ] Switch selftest from `omega` to `lia` (omega finds 0 results on shallow targets; lia is more useful)

## Upstream
- [x] Upstream Mathlib changes: `tryAtEachStepFromEnv` linter that reads tactic from `TRY_AT_EACH_STEP_TACTIC` environment variable
  - PR: https://github.com/leanprover-community/mathlib4/pull/32415
