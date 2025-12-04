# TODO

## Self-test
- [x] Generate expected test outputs for selftest (saved to `tests/expected/`)
- [ ] Enable full selftest in CI (currently runs `--dry-run` only)

## Upstream
- [x] Upstream Mathlib changes: `tryAtEachStepFromEnv` linter that reads tactic from `TRY_AT_EACH_STEP_TACTIC` environment variable
  - PR: https://github.com/leanprover-community/mathlib4/pull/32415
