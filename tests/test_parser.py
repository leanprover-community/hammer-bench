"""Unit tests for the build output parser."""

import unittest
from scripts.parser import (
    parse_build_output,
    normalize_tactic,
    group_by_location,
    group_by_tactic,
    get_tactic_stats,
)


class TestNormalizeTactic(unittest.TestCase):
    """Tests for the normalize_tactic function."""

    def test_simple_tactic(self):
        self.assertEqual(normalize_tactic("grind"), "grind")
        self.assertEqual(normalize_tactic("omega"), "omega")
        self.assertEqual(normalize_tactic("simp_all"), "simp_all")

    def test_try_prefix(self):
        self.assertEqual(normalize_tactic("try simp_all"), "simp_all")
        self.assertEqual(normalize_tactic("try grind"), "grind")

    def test_question_mark(self):
        self.assertEqual(normalize_tactic("simp_all?"), "simp_all")
        self.assertEqual(normalize_tactic("grind?"), "grind")

    def test_suggestions_variant(self):
        self.assertEqual(normalize_tactic("grind +suggestions"), "grind +suggestions")
        self.assertEqual(normalize_tactic("simp_all? +suggestions"), "simp_all +suggestions")

    def test_try_with_suggestions(self):
        self.assertEqual(normalize_tactic("try simp_all? +suggestions"), "simp_all +suggestions")

    def test_dagger_removal(self):
        self.assertEqual(normalize_tactic("grind✝"), "grind")

    def test_whitespace_handling(self):
        self.assertEqual(normalize_tactic("  grind  "), "grind")
        self.assertEqual(normalize_tactic("try  simp_all"), "simp_all")


class TestParseBuildOutput(unittest.TestCase):
    """Tests for the parse_build_output function."""

    def test_single_message(self):
        output = 'info: Mathlib/Logic/Basic.lean:47:55: `rfl` can be replaced with `grind` (2ms)'
        messages = parse_build_output(output)
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual(msg.file, "Mathlib/Logic/Basic.lean")
        self.assertEqual(msg.row, 47)
        self.assertEqual(msg.col, 55)
        self.assertEqual(msg.original, "rfl")
        self.assertEqual(msg.replacement, "grind")
        self.assertEqual(msg.time_ms, 2)

    def test_multiple_messages(self):
        output = '''info: Mathlib/Logic/Basic.lean:47:55: `rfl` can be replaced with `grind` (2ms)
info: Mathlib/Data/Nat/Basic.lean:100:10: `simp` can be replaced with `omega` (5ms)'''
        messages = parse_build_output(output)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].file, "Mathlib/Logic/Basic.lean")
        self.assertEqual(messages[1].file, "Mathlib/Data/Nat/Basic.lean")

    def test_message_without_timing(self):
        output = 'info: Mathlib/Logic/Basic.lean:47:55: `rfl` can be replaced with `grind`'
        messages = parse_build_output(output)
        self.assertEqual(len(messages), 1)
        self.assertIsNone(messages[0].time_ms)

    def test_no_messages(self):
        output = 'Building Mathlib...\nCompiling Mathlib.Logic.Basic\nDone.'
        messages = parse_build_output(output)
        self.assertEqual(len(messages), 0)

    def test_mixed_output(self):
        output = '''Building Mathlib...
info: Mathlib/Logic/Basic.lean:47:55: `rfl` can be replaced with `grind` (2ms)
Compiling Mathlib.Data.Nat.Basic
warning: unused variable
info: Mathlib/Data/Nat/Basic.lean:100:10: `simp` can be replaced with `omega` (5ms)
Done.'''
        messages = parse_build_output(output)
        self.assertEqual(len(messages), 2)

    def test_complex_original_tactic(self):
        output = 'info: Mathlib/Data/Nat/Init.lean:100:8: `rw [Nat.add_comm] <;> simp` can be replaced with `omega` (11ms)'
        messages = parse_build_output(output)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].original, "rw [Nat.add_comm] <;> simp")


class TestGroupByLocation(unittest.TestCase):
    """Tests for the group_by_location function."""

    def test_grouping(self):
        output = '''info: file.lean:10:5: `a` can be replaced with `grind` (1ms)
info: file.lean:10:5: `a` can be replaced with `omega` (2ms)
info: file.lean:20:3: `b` can be replaced with `grind` (1ms)'''
        messages = parse_build_output(output)
        grouped = group_by_location(messages)
        self.assertEqual(len(grouped), 2)
        self.assertEqual(len(grouped["file.lean:10:5"]), 2)
        self.assertEqual(len(grouped["file.lean:20:3"]), 1)


class TestGroupByTactic(unittest.TestCase):
    """Tests for the group_by_tactic function."""

    def test_grouping(self):
        output = '''info: file.lean:10:5: `a` can be replaced with `grind` (1ms)
info: file.lean:15:5: `b` can be replaced with `grind` (2ms)
info: file.lean:20:3: `c` can be replaced with `omega` (1ms)'''
        messages = parse_build_output(output)
        grouped = group_by_tactic(messages)
        self.assertEqual(len(grouped), 2)
        self.assertEqual(len(grouped["grind"]), 2)
        self.assertEqual(len(grouped["omega"]), 1)


class TestGetTacticStats(unittest.TestCase):
    """Tests for the get_tactic_stats function."""

    def test_stats(self):
        output = '''info: file.lean:10:5: `a` can be replaced with `grind` (1ms)
info: file.lean:15:5: `b` can be replaced with `grind` (3ms)
info: file.lean:20:3: `c` can be replaced with `omega` (5ms)'''
        messages = parse_build_output(output)
        stats = get_tactic_stats(messages)

        self.assertEqual(stats["grind"]["count"], 2)
        self.assertEqual(stats["grind"]["mean_time_ms"], 2.0)
        self.assertEqual(stats["grind"]["min_time_ms"], 1)
        self.assertEqual(stats["grind"]["max_time_ms"], 3)

        self.assertEqual(stats["omega"]["count"], 1)
        self.assertEqual(stats["omega"]["mean_time_ms"], 5.0)


if __name__ == "__main__":
    unittest.main()
