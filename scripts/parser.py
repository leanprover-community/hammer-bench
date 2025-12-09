"""Parser for extracting hammer suggestions from build output."""

import re
from typing import List, Optional, Tuple
from .core import Message, AttemptedLocation


# Pattern to match info messages with "can be replaced with", optional later steps, and optional timing
# Examples:
#   info: Mathlib/Logic/Basic.lean:47:55: `rfl` can be replaced with `grind` (2ms)
#   info: Mathlib/Logic/Basic.lean:47:55: `skip` (+3 later steps) can be replaced with `grind` (2ms)
MESSAGE_PATTERN = re.compile(
    r'info: ([^:]+):(\d+):(\d+):[^\n]*?`([^`]+)`(?: \(\+(\d+) later steps?\))? can be replaced with `([^`]+)`(?: \((\d+)ms\))?'
)

# Pattern to match PANIC messages
# Example: info: Mathlib/Order/Interval/Set/Pi.lean:81:0: PANIC at Lean.Meta.Grind.mkEqProofImpl ...
PANIC_PATTERN = re.compile(r'PANIC at')

# Pattern to match "tryAtEachStep running" messages
# Matches: info: Mathlib/Logic/Basic.lean:47:55: `tryAtEachStep` running
ATTEMPTED_PATTERN = re.compile(
    r'info: ([^:]+):(\d+):(\d+):.*?`tryAtEachStep` running'
)


def normalize_tactic(tactic: str) -> str:
    """Normalize tactic name, handling +suggestions variants and try prefix."""
    # Remove dagger symbol
    tactic = tactic.replace('✝', '')
    tactic = tactic.strip()

    # Remove 'try ' prefix if present
    if tactic.startswith('try '):
        tactic = tactic[4:].strip()

    # Remove '?' modifier (but keep it before +suggestions)
    # e.g., "simp_all? +suggestions" -> "simp_all +suggestions"
    tactic = tactic.replace('? +', ' +')
    # Remove trailing '?' if present
    if tactic.endswith('?'):
        tactic = tactic[:-1].strip()

    return tactic


def count_panics(output: str) -> int:
    """Count the number of PANIC messages in build output."""
    return len(PANIC_PATTERN.findall(output))


def parse_build_output(output: str) -> List[Message]:
    """Parse build output and extract all replacement messages.

    Args:
        output: Combined stdout+stderr from lake build

    Returns:
        List of Message objects
    """
    messages = []

    for match in MESSAGE_PATTERN.finditer(output):
        filepath, row, col, original, later_steps, replacement, time_ms = match.groups()

        # Normalize the replacement tactic
        normalized_replacement = normalize_tactic(replacement)

        messages.append(Message(
            file=filepath,
            row=int(row),
            col=int(col),
            original=original.strip(),
            replacement=normalized_replacement,
            time_ms=int(time_ms) if time_ms else None,
            later_steps=int(later_steps) if later_steps else 0,
        ))

    return messages


def parse_build_output_streaming(output_lines, callback):
    """Parse build output line by line, calling callback for each message.

    Useful for streaming large outputs.

    Args:
        output_lines: Iterator of lines from build output
        callback: Function to call with each Message
    """
    for line in output_lines:
        # Check if this line contains a replacement message
        if "can be replaced with" in line:
            match = MESSAGE_PATTERN.search(line)
            if match:
                filepath, row, col, original, later_steps, replacement, time_ms = match.groups()
                normalized_replacement = normalize_tactic(replacement)

                callback(Message(
                    file=filepath,
                    row=int(row),
                    col=int(col),
                    original=original.strip(),
                    replacement=normalized_replacement,
                    time_ms=int(time_ms) if time_ms else None,
                    later_steps=int(later_steps) if later_steps else 0,
                ))


def group_by_location(messages: List[Message]) -> dict:
    """Group messages by file:row:col location.

    Returns:
        Dict mapping location string to list of messages at that location
    """
    locations = {}
    for msg in messages:
        loc = f"{msg.file}:{msg.row}:{msg.col}"
        if loc not in locations:
            locations[loc] = []
        locations[loc].append(msg)
    return locations


def group_by_tactic(messages: List[Message]) -> dict:
    """Group messages by replacement tactic.

    Returns:
        Dict mapping tactic name to list of messages using that tactic
    """
    tactics = {}
    for msg in messages:
        if msg.replacement not in tactics:
            tactics[msg.replacement] = []
        tactics[msg.replacement].append(msg)
    return tactics


def get_tactic_stats(messages: List[Message]) -> dict:
    """Get statistics about tactics from messages.

    Returns:
        Dict with tactic name -> {count, times, mean_time, etc.}
    """
    by_tactic = group_by_tactic(messages)
    stats = {}

    for tactic, msgs in by_tactic.items():
        times = [m.time_ms for m in msgs if m.time_ms is not None]
        stats[tactic] = {
            "count": len(msgs),
            "times": times,
            "mean_time_ms": sum(times) / len(times) if times else None,
            "min_time_ms": min(times) if times else None,
            "max_time_ms": max(times) if times else None,
        }

    return stats


def parse_attempted_locations(output: str) -> List[AttemptedLocation]:
    """Parse 'tryAtEachStep running' messages from build output.

    These messages indicate which locations were actually tested, before
    the tactic was run. This is useful for verifying that two runs tested
    the same set of locations.

    Args:
        output: Combined stdout+stderr from lake build

    Returns:
        List of AttemptedLocation objects
    """
    locations = []
    for match in ATTEMPTED_PATTERN.finditer(output):
        file, row, col = match.groups()
        locations.append(AttemptedLocation(
            file=file,
            row=int(row),
            col=int(col),
        ))
    return locations
