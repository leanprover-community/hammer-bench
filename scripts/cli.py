"""Command-line interface for hammer benchmarking."""

import argparse
import sys
from pathlib import Path

from . import __version__


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="bench",
        description="Hammer benchmarking tool for Mathlib",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize the hammer-bench environment",
    )
    init_parser.add_argument(
        "--mathlib-repo",
        type=str,
        default="https://github.com/leanprover-community/mathlib4.git",
        help="Mathlib repository URL",
    )
    init_parser.add_argument(
        "--base",
        type=str,
        default=None,
        help="Base commit/tag/branch to check out (default: master)",
    )

    # queue command
    queue_parser = subparsers.add_parser(
        "queue",
        help="Manage the run queue",
    )
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")

    # queue add
    queue_add = queue_subparsers.add_parser("add", help="Add a run to the queue")
    queue_add.add_argument("preset", help="Preset name (optionally with :provider suffix)")

    # queue list (default if no subcommand)
    queue_subparsers.add_parser("list", help="List queued runs")

    # queue clear
    queue_subparsers.add_parser("clear", help="Clear pending runs from queue")

    # queue redo
    queue_redo = queue_subparsers.add_parser("redo", help="Re-queue a completed run")
    queue_redo.add_argument(
        "run_id",
        nargs="?",
        help="Run ID to redo (default: most recent completed run)",
    )

    # run command
    run_parser = subparsers.add_parser(
        "run",
        help="Execute benchmark runs from the queue",
    )
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Process only one run and exit",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be run without actually running",
    )

    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List completed runs",
    )
    list_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="Maximum number of runs to show",
    )

    # show command
    show_parser = subparsers.add_parser(
        "show",
        help="Show details for a specific run",
    )
    show_parser.add_argument("run_id", help="Run ID to show")

    # compare command
    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare multiple runs",
    )
    compare_parser.add_argument("runs", nargs='+', help="Run IDs to compare (2 or more)")
    compare_parser.add_argument(
        "--format",
        choices=["markdown", "csv", "json"],
        default="markdown",
        help="Output format",
    )
    compare_parser.add_argument(
        "--samples",
        type=int,
        nargs="?",
        const=5,
        default=None,
        metavar="N",
        help="Show N random samples where one tactic succeeded but another failed (default: 5)",
    )

    # validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate consistency between identical runs",
    )
    validate_parser.add_argument("run1", help="First run ID")
    validate_parser.add_argument("run2", help="Second run ID")

    # rebase command
    rebase_parser = subparsers.add_parser(
        "rebase",
        help="Rebase the mathlib4 worktree to a new base",
    )
    rebase_parser.add_argument("tag", help="Tag or commit to rebase to")

    # check-base command
    subparsers.add_parser(
        "check-base",
        help="Check the current base of the mathlib4 worktree",
    )

    # cleanup command
    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Clean up old runs",
    )
    cleanup_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Remove runs older than this many days (default: 30)",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    cleanup_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # selftest command
    selftest_parser = subparsers.add_parser(
        "selftest",
        help="Run self-tests to verify hammer-bench is working",
    )
    selftest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be run without actually running",
    )
    selftest_parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Override the test source (default: kim-em/mathlib4@feat/tryAtEachStepFromEnv)",
    )

    # tui command
    subparsers.add_parser(
        "tui",
        help="Launch the interactive terminal UI",
    )

    return parser


def main(args=None):
    """Main entry point."""
    parser = create_parser()
    parsed = parser.parse_args(args)

    if parsed.command is None:
        # Show help when no command provided
        parser.print_help()
        return 0

    # Import handlers here to avoid circular imports
    if parsed.command == "init":
        from .commands import cmd_init
        return cmd_init(parsed)
    elif parsed.command == "queue":
        from .commands import cmd_queue
        return cmd_queue(parsed)
    elif parsed.command == "run":
        from .commands import cmd_run
        return cmd_run(parsed)
    elif parsed.command == "list":
        from .commands import cmd_list
        return cmd_list(parsed)
    elif parsed.command == "show":
        from .commands import cmd_show
        return cmd_show(parsed)
    elif parsed.command == "compare":
        from .commands import cmd_compare
        return cmd_compare(parsed)
    elif parsed.command == "validate":
        from .commands import cmd_validate
        return cmd_validate(parsed)
    elif parsed.command == "rebase":
        from .commands import cmd_rebase
        return cmd_rebase(parsed)
    elif parsed.command == "check-base":
        from .commands import cmd_check_base
        return cmd_check_base(parsed)
    elif parsed.command == "cleanup":
        from .commands import cmd_cleanup
        return cmd_cleanup(parsed)
    elif parsed.command == "selftest":
        from .commands import cmd_selftest
        return cmd_selftest(parsed)
    elif parsed.command == "tui":
        from .tui.app import run_tui
        return run_tui()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
