"""Run execution for hammer benchmarking."""

import gzip
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("Error: pyyaml is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from .core import (
    LinterConfig,
    Message,
    RunConfig,
    RunMetadata,
    SuggestionProvider,
    atomic_write_json,
    generate_run_id,
    get_git_commit,
    get_git_ref,
    get_hammer_bench_dir,
    get_lean_toolchain,
    get_machine_name,
    get_mathlib_dir,
    get_runs_dir,
)
from .parser import parse_build_output


def load_presets() -> dict:
    """Load presets from config/presets.yaml."""
    presets_file = get_hammer_bench_dir() / "config" / "presets.yaml"
    if presets_file.exists():
        with open(presets_file) as f:
            return yaml.safe_load(f) or {}
    return {}


def load_providers() -> dict:
    """Load providers from config/providers.yaml."""
    providers_file = get_hammer_bench_dir() / "config" / "providers.yaml"
    if providers_file.exists():
        with open(providers_file) as f:
            data = yaml.safe_load(f) or {}
            return data.get("providers", {})
    return {}


def load_targets() -> dict:
    """Load target collections from config/targets.yaml."""
    targets_file = get_hammer_bench_dir() / "config" / "targets.yaml"
    if targets_file.exists():
        with open(targets_file) as f:
            return yaml.safe_load(f) or {}
    return {}


def parse_queue_entry(entry: str) -> tuple:
    """Parse a queue entry with optional target collection, provider, and fraction.

    Supported formats:
        preset_name
        preset_name/fraction
        preset_name@targets
        preset_name@targets/fraction
        preset_name:provider
        preset_name:provider/fraction
        preset_name@targets:provider
        preset_name@targets:provider/fraction

    Returns:
        (preset_name, provider_name or None, fraction or None, targets or None)
    """
    entry = entry.strip()
    fraction = None
    targets = None

    # Check for fraction suffix first (must be last)
    if "/" in entry:
        entry, fraction_str = entry.rsplit("/", 1)
        try:
            fraction = int(fraction_str.strip())
        except ValueError:
            raise ValueError(f"Invalid fraction '{fraction_str}' - must be an integer")

    # Check for provider (after @targets if present)
    if ":" in entry:
        entry, provider = entry.rsplit(":", 1)
        provider = provider.strip()
    else:
        provider = None

    # Check for targets
    if "@" in entry:
        preset, targets = entry.split("@", 1)
        preset = preset.strip()
        targets = targets.strip()
    else:
        preset = entry

    return preset, provider, fraction, targets


def get_run_config(preset_name: str, provider_name: Optional[str] = None,
                   fraction_override: Optional[int] = None,
                   target_collection: Optional[str] = None) -> RunConfig:
    """Get a RunConfig from preset and optional provider/fraction/target overrides."""
    presets = load_presets()
    providers = load_providers()
    all_targets = load_targets()

    if preset_name not in presets:
        raise ValueError(f"Unknown preset: {preset_name}")

    preset = presets[preset_name]
    linters = LinterConfig.from_dict(preset.get("linters", {}))
    if "fraction" in preset:
        linters.fraction = preset["fraction"]
    # Apply fraction override from queue entry
    if fraction_override is not None:
        linters.fraction = fraction_override
    # Support custom tactic in preset
    if "customTactic" in preset:
        linters.customTactic = preset["customTactic"]
        linters.customTacticLabel = preset.get("customTacticLabel", preset["customTactic"])

    provider = None
    if provider_name:
        if provider_name not in providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        provider_config = providers[provider_name]
        provider = SuggestionProvider(
            name=provider_name,
            command=provider_config.get("command"),
        )

    # Resolve target collection
    resolved_target_collection = target_collection or "all"
    if resolved_target_collection not in all_targets:
        raise ValueError(f"Unknown target collection: {resolved_target_collection}")
    targets = all_targets[resolved_target_collection].get("targets", ["Mathlib"])

    return RunConfig(
        preset_name=preset_name,
        linters=linters,
        suggestion_provider=provider,
        timing_mode=preset.get("timing_mode", True),
        build_timeout_hours=preset.get("build_timeout_hours", 6.0),
        target_collection=resolved_target_collection,
        targets=targets,
    )


def build_lake_command(config: RunConfig) -> tuple:
    """Build the lake command using the generic TRY_AT_EACH_STEP_* mechanism.

    All tactics are run via the generic tryAtEachStepFromEnv linter using
    environment variables. This avoids needing specialized linter options
    for each tactic variant.

    Args:
        config: Run configuration (includes targets)

    Returns:
        Tuple of (command arguments list, environment variables dict)
    """
    cmd = ["lake", "build"] + config.targets
    env_vars = {}

    linters = config.linters

    # Determine which tactic to run via the generic mechanism
    # Priority: customTactic > specialized flags
    tactic = None
    label = None

    if linters.customTactic:
        tactic = linters.customTactic
        label = linters.customTacticLabel or tactic
    elif linters.tryAtEachStepGrind:
        tactic = "grind"
        label = "grind"
    elif linters.tryAtEachStepSimpAll:
        tactic = "simp_all"
        label = "simp_all"
    elif linters.tryAtEachStepAesop:
        tactic = "aesop"
        label = "aesop"
    elif linters.tryAtEachStepGrindSuggestions:
        tactic = "grind +suggestions"
        label = "grind +suggestions"
    elif linters.tryAtEachStepSimpAllSuggestions:
        # Note: the `try` is needed to avoid errors in some edge cases
        tactic = "try simp_all? +suggestions"
        label = "simp_all +suggestions"

    # Use the generic env var mechanism for all tactics
    if tactic:
        cmd.append("-Klinter.tacticAnalysis.tryAtEachStepFromEnv=true")
        env_vars["TRY_AT_EACH_STEP_TACTIC"] = tactic
        env_vars["TRY_AT_EACH_STEP_LABEL"] = label

    # Add fraction if not 1
    if linters.fraction != 1:
        cmd.append(f"-Klinter.tacticAnalysis.tryAtEachStep.fraction={linters.fraction}")

    return cmd, env_vars


def patch_suggestion_provider(mathlib_dir: Path, provider: Optional[SuggestionProvider]) -> Optional[Path]:
    """Patch Mathlib/Init.lean to set a custom suggestion provider.

    Args:
        mathlib_dir: Path to mathlib4 directory
        provider: Suggestion provider to use, or None for default

    Returns:
        Path to the patched file if patching was done, None otherwise
    """
    if provider is None or provider.command is None:
        return None

    init_file = mathlib_dir / "Mathlib" / "Init.lean"
    if not init_file.exists():
        raise FileNotFoundError(f"Mathlib/Init.lean not found at {init_file}")

    # Read current content
    content = init_file.read_text()

    # Check if already patched
    if "-- HAMMER_BENCH_PROVIDER_START" in content:
        # Remove old patch
        lines = content.split("\n")
        new_lines = []
        skip = False
        for line in lines:
            if "-- HAMMER_BENCH_PROVIDER_START" in line:
                skip = True
            elif "-- HAMMER_BENCH_PROVIDER_END" in line:
                skip = False
            elif not skip:
                new_lines.append(line)
        content = "\n".join(new_lines)

    # Add new patch at the end of imports (before main content)
    patch = f"""
-- HAMMER_BENCH_PROVIDER_START
-- Auto-generated by hammer-bench. Do not edit manually.
set_library_suggestions {provider.command}
-- HAMMER_BENCH_PROVIDER_END
"""

    # Insert after the module's imports
    # Find a good insertion point (after imports, before the main section)
    lines = content.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("import "):
            insert_idx = i + 1

    lines.insert(insert_idx, patch)
    new_content = "\n".join(lines)

    init_file.write_text(new_content)
    return init_file


def unpatch_suggestion_provider(mathlib_dir: Path) -> None:
    """Remove the suggestion provider patch from Mathlib/Init.lean."""
    init_file = mathlib_dir / "Mathlib" / "Init.lean"
    if not init_file.exists():
        return

    content = init_file.read_text()
    if "-- HAMMER_BENCH_PROVIDER_START" not in content:
        return

    # Remove patch
    lines = content.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if "-- HAMMER_BENCH_PROVIDER_START" in line:
            skip = True
        elif "-- HAMMER_BENCH_PROVIDER_END" in line:
            skip = False
        elif not skip:
            new_lines.append(line)

    init_file.write_text("\n".join(new_lines))


def execute_run(config: RunConfig, dry_run: bool = False) -> Optional[RunMetadata]:
    """Execute a single benchmark run.

    Args:
        config: Run configuration
        dry_run: If True, just print what would be done

    Returns:
        RunMetadata for the completed run, or None on failure
    """
    mathlib_dir = get_mathlib_dir()
    runs_dir = get_runs_dir()

    if not mathlib_dir.exists():
        print("Error: mathlib4 not initialized. Run 'hammer-bench init' first.", file=sys.stderr)
        return None

    run_id = generate_run_id(config.preset_name)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd, env_vars = build_lake_command(config)
    timeout_seconds = int(config.build_timeout_hours * 3600)

    print(f"Run ID: {run_id}")
    print(f"Preset: {config.preset_name}")
    if config.target_collection != "all":
        print(f"Targets: {config.target_collection} ({len(config.targets)} module(s))")
    print(f"Provider: {config.suggestion_provider.name if config.suggestion_provider else 'default'}")
    print(f"Command: {' '.join(cmd)}")
    if env_vars:
        print(f"Environment: {env_vars}")
    print(f"Timeout: {config.build_timeout_hours}h ({timeout_seconds}s)")
    print()

    if dry_run:
        print("(dry run - not executing)")
        return None

    # Create initial metadata
    metadata = RunMetadata(
        run_id=run_id,
        machine=get_machine_name(),
        base_commit=get_git_commit(mathlib_dir),
        base_ref=get_git_ref(mathlib_dir),
        lean_toolchain=get_lean_toolchain(mathlib_dir),
        started_at=datetime.now(),
        completed_at=None,
        duration_seconds=None,
        config=config,
        status="running",
    )

    # Save initial metadata
    atomic_write_json(run_dir / "metadata.json", metadata.to_dict())

    # Patch suggestion provider if needed
    patched_file = None
    try:
        if config.suggestion_provider and config.suggestion_provider.command:
            print(f"Patching suggestion provider: {config.suggestion_provider.name}")
            patched_file = patch_suggestion_provider(mathlib_dir, config.suggestion_provider)

        # Run lake clean
        print("Running lake clean...")
        subprocess.run(
            ["lake", "clean"],
            cwd=mathlib_dir,
            capture_output=True,
        )

        # Run lake build with timeout
        print(f"Running lake build (timeout: {config.build_timeout_hours}h)...")
        start_time = time.time()

        # Merge environment variables with current environment
        run_env = os.environ.copy()
        run_env.update(env_vars)

        try:
            result = subprocess.run(
                cmd,
                cwd=mathlib_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=run_env,
            )
            timed_out = False
        except subprocess.TimeoutExpired as e:
            print(f"Build timed out after {config.build_timeout_hours}h")
            timed_out = True
            result = type('Result', (), {
                'stdout': e.stdout.decode() if e.stdout else '',
                'stderr': e.stderr.decode() if e.stderr else '',
                'returncode': -1,
            })()

        end_time = time.time()
        duration_seconds = int(end_time - start_time)

        # Parse output
        output = result.stdout + result.stderr
        print(f"Parsing output ({len(output)} chars)...")
        messages = parse_build_output(output)
        print(f"Found {len(messages)} replacement messages")

        # Save build log (compressed)
        log_path = run_dir / "build.log.gz"
        with gzip.open(log_path, "wt") as f:
            f.write(output)

        # Save messages as JSONL
        messages_path = run_dir / "messages.jsonl"
        with open(messages_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg.to_dict()) + "\n")

        # Update metadata
        metadata.completed_at = datetime.now()
        metadata.duration_seconds = duration_seconds
        metadata.message_count = len(messages)
        metadata.timed_out = timed_out
        metadata.status = "timed_out" if timed_out else ("completed" if result.returncode == 0 else "failed")

        atomic_write_json(run_dir / "metadata.json", metadata.to_dict())

        print(f"Run completed: {metadata.status}")
        print(f"Duration: {duration_seconds}s ({duration_seconds / 3600:.2f}h)")
        print(f"Messages: {len(messages)}")
        print(f"Results saved to: {run_dir}")

        return metadata

    except Exception as e:
        # Update metadata with error
        metadata.completed_at = datetime.now()
        metadata.status = "failed"
        metadata.error = str(e)
        atomic_write_json(run_dir / "metadata.json", metadata.to_dict())
        raise

    finally:
        # Always unpatch
        if patched_file:
            print("Removing suggestion provider patch...")
            unpatch_suggestion_provider(mathlib_dir)
