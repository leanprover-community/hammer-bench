"""Run execution for hammer benchmarking."""

import gzip
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("Error: pyyaml is required. Set up virtual environment:", file=sys.stderr)
    print("  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

from .core import (
    LinterConfig,
    Message,
    RepoConfig,
    RunConfig,
    RunMetadata,
    SourceSpec,
    SuggestionProvider,
    atomic_write_json,
    generate_run_id,
    get_git_commit,
    get_git_ref,
    get_hammer_bench_dir,
    get_lean_toolchain,
    get_machine_name,
    get_mathlib_dir,
    get_repo_dir,
    get_runs_dir,
    get_worktrees_dir,
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


def load_repos() -> dict:
    """Load repository configurations from config/repos.yaml.

    Returns:
        Dict mapping repo names to RepoConfig objects
    """
    repos_file = get_hammer_bench_dir() / "config" / "repos.yaml"
    if repos_file.exists():
        with open(repos_file) as f:
            data = yaml.safe_load(f) or {}
            repos_data = data.get("repos", {})
            return {
                name: RepoConfig.from_dict(name, config)
                for name, config in repos_data.items()
            }
    return {}


def get_repo_config(source: SourceSpec) -> Optional[RepoConfig]:
    """Get the RepoConfig for a source spec, if available."""
    repos = load_repos()
    repo_name = source.repo_name
    return repos.get(repo_name)


def checkout_source(source: SourceSpec, repo_dir: Optional[Path] = None) -> Path:
    """Checkout a specific source (repo + ref) in the worktrees directory.

    If the worktree doesn't exist, it will be cloned. Otherwise, the specified
    ref will be fetched and checked out.

    Args:
        source: The source specification (repo + ref)
        repo_dir: Path to repository directory (defaults to worktrees/<repo_name>)

    Returns:
        Path to the repository directory
    """
    repos = load_repos()
    repo_config = repos.get(source.repo_name)

    if repo_dir is None:
        repo_dir = get_repo_dir(source.repo_name)

    # Ensure worktrees directory exists
    get_worktrees_dir().mkdir(parents=True, exist_ok=True)

    # Get the URL (will error if short name not in repos.yaml)
    url = source.resolve_url(repos)

    if not repo_dir.exists():
        # Clone the repository
        print(f"Cloning {source.repo_name} from {url}...")
        subprocess.run(
            ["git", "clone", url, str(repo_dir)],
            check=True,
        )
        # Fetch the specific ref (clone only gets default branch)
        print(f"Fetching {source.ref}...")
        subprocess.run(
            ["git", "fetch", "origin", source.ref],
            cwd=repo_dir,
            check=True,
        )
    else:
        # Add remote if needed (use repo name as remote name)
        remote_name = source.repo.replace("/", "_")
        result = subprocess.run(
            ["git", "remote", "get-url", remote_name],
            cwd=repo_dir,
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"Adding remote {remote_name} -> {url}")
            subprocess.run(
                ["git", "remote", "add", remote_name, url],
                cwd=repo_dir,
                check=True,
            )

        # Fetch the ref
        print(f"Fetching {source.ref} from {remote_name}...")
        subprocess.run(
            ["git", "fetch", remote_name, source.ref],
            cwd=repo_dir,
            check=True,
        )

    # Checkout the ref
    print(f"Checking out {source.ref}...")
    subprocess.run(
        ["git", "checkout", "--detach", "FETCH_HEAD"],
        cwd=repo_dir,
        check=True,
    )

    print(f"Checked out {source} at {get_git_commit(repo_dir)[:12]}")
    return repo_dir


@dataclass
class QueueEntry:
    """A parsed queue entry."""
    preset: str
    targets: Optional[str] = None
    provider: Optional[str] = None
    fraction: Optional[int] = None

    @classmethod
    def parse(cls, entry) -> "QueueEntry":
        """Parse a queue entry from string or dict format."""
        if isinstance(entry, str):
            # Parse string shorthand: "preset@targets:provider/fraction"
            preset, provider, fraction, targets = parse_queue_entry(entry)
            return cls(preset=preset, targets=targets, provider=provider, fraction=fraction)
        elif isinstance(entry, dict):
            # Parse explicit dict format
            return cls(
                preset=entry["preset"],
                targets=entry.get("targets"),
                provider=entry.get("provider"),
                fraction=entry.get("fraction"),
            )
        else:
            raise ValueError(f"Invalid queue entry format: {entry}")


@dataclass
class QueueFile:
    """Parsed queue file."""
    source: Optional[SourceSpec]
    entries: list  # List of QueueEntry
    completed: list  # List of completed run records
    path: Path

    def save(self) -> None:
        """Save the queue file back to disk."""
        data = {
            "source": str(self.source) if self.source else None,
            "queue": [
                e.preset if not (e.targets or e.provider or e.fraction) else {
                    "preset": e.preset,
                    **({"targets": e.targets} if e.targets else {}),
                    **({"provider": e.provider} if e.provider else {}),
                    **({"fraction": e.fraction} if e.fraction else {}),
                }
                for e in self.entries
            ],
            "completed": self.completed,
        }
        with open(self.path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def parse_queue_file(queue_file: Path) -> QueueFile:
    """Parse a YAML queue file.

    Returns:
        QueueFile with source, entries, and completed runs
    """
    if not queue_file.exists():
        return QueueFile(source=None, entries=[], completed=[], path=queue_file)

    with open(queue_file) as f:
        data = yaml.safe_load(f) or {}

    # Parse source
    source = None
    if data.get("source"):
        source = SourceSpec.parse(data["source"])

    # Parse queue entries
    entries = []
    for entry in data.get("queue", []):
        entries.append(QueueEntry.parse(entry))

    # Keep completed as-is
    completed = data.get("completed", [])

    return QueueFile(source=source, entries=entries, completed=completed, path=queue_file)


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

    # Set environment variables for the tactic to run
    # Note: The linter option is enabled via lakefile patching in execute_run()
    # The -K flags are for Lake config, not Lean options, so we don't use them here
    if tactic:
        env_vars["TRY_AT_EACH_STEP_TACTIC"] = tactic
        env_vars["TRY_AT_EACH_STEP_LABEL"] = label

    return cmd, env_vars


def patch_suggestion_provider(repo_dir: Path, provider: Optional[SuggestionProvider],
                              patch_file: Optional[str] = None) -> Optional[Path]:
    """Patch a file to set a custom suggestion provider.

    Args:
        repo_dir: Path to repository directory
        provider: Suggestion provider to use, or None for default
        patch_file: Relative path to file to patch (e.g., "Mathlib/Init.lean")
                   If None, defaults to "Mathlib/Init.lean"

    Returns:
        Path to the patched file if patching was done, None otherwise
    """
    if provider is None or provider.command is None:
        return None

    if patch_file is None:
        patch_file = "Mathlib/Init.lean"

    init_file = repo_dir / patch_file
    if not init_file.exists():
        raise FileNotFoundError(f"{patch_file} not found at {init_file}")

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


def unpatch_suggestion_provider(repo_dir: Path, patch_file: Optional[str] = None) -> None:
    """Remove the suggestion provider patch from the patched file.

    Args:
        repo_dir: Path to repository directory
        patch_file: Relative path to file that was patched (e.g., "Mathlib/Init.lean")
                   If None, defaults to "Mathlib/Init.lean"
    """
    if patch_file is None:
        patch_file = "Mathlib/Init.lean"

    init_file = repo_dir / patch_file
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


def patch_lakefile_linter(repo_dir: Path, linter_option: str, fraction: int = 1) -> Path:
    """Patch lakefile.lean to enable a linter option.

    Adds the linter option to mathlibOnlyLinters array.

    Args:
        repo_dir: Path to repository directory
        linter_option: The linter option name (e.g., "linter.tacticAnalysis.tryAtEachStepFromEnv")
        fraction: Sampling fraction (1 = all, 10 = 10%, etc.)

    Returns:
        Path to the patched lakefile
    """
    lakefile = repo_dir / "lakefile.lean"
    if not lakefile.exists():
        raise FileNotFoundError(f"lakefile.lean not found at {lakefile}")

    content = lakefile.read_text()

    # Remove any existing hammer-bench patch
    if "-- HAMMER_BENCH_LINTER_START" in content:
        lines = content.split("\n")
        new_lines = []
        skip = False
        for line in lines:
            if "-- HAMMER_BENCH_LINTER_START" in line:
                skip = True
            elif "-- HAMMER_BENCH_LINTER_END" in line:
                skip = False
            elif not skip:
                new_lines.append(line)
        content = "\n".join(new_lines)

    # Find mathlibOnlyLinters and insert our options
    # We insert right after the opening #[
    patch = f"""  -- HAMMER_BENCH_LINTER_START
  ⟨`{linter_option}, true⟩,
  ⟨`linter.tacticAnalysis.tryAtEachStep.fraction, .ofNat {fraction}⟩,
  -- HAMMER_BENCH_LINTER_END"""

    # Find the mathlibOnlyLinters definition and insert after #[
    marker = "abbrev mathlibOnlyLinters : Array LeanOption := #["
    if marker not in content:
        raise ValueError(f"Could not find '{marker}' in lakefile.lean")

    content = content.replace(marker, marker + "\n" + patch)

    lakefile.write_text(content)
    return lakefile


def unpatch_lakefile_linter(repo_dir: Path) -> None:
    """Remove the linter patch from lakefile.lean."""
    lakefile = repo_dir / "lakefile.lean"
    if not lakefile.exists():
        return

    content = lakefile.read_text()
    if "-- HAMMER_BENCH_LINTER_START" not in content:
        return

    lines = content.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if "-- HAMMER_BENCH_LINTER_START" in line:
            skip = True
        elif "-- HAMMER_BENCH_LINTER_END" in line:
            skip = False
        elif not skip:
            new_lines.append(line)

    lakefile.write_text("\n".join(new_lines))


def execute_run(config: RunConfig, dry_run: bool = False,
                source: Optional[SourceSpec] = None) -> Optional[RunMetadata]:
    """Execute a single benchmark run.

    Args:
        config: Run configuration
        dry_run: If True, just print what would be done
        source: Optional source specification (recorded in metadata)

    Returns:
        RunMetadata for the completed run, or None on failure
    """
    runs_dir = get_runs_dir()

    # Determine the repo directory and config
    if source:
        repo_dir = get_repo_dir(source.repo_name)
        repo_config = get_repo_config(source)
    else:
        repo_dir = get_mathlib_dir()
        repo_config = None

    patch_file = repo_config.patch_file if repo_config else None

    if not repo_dir.exists():
        print(f"Error: Repository not initialized at {repo_dir}.", file=sys.stderr)
        print("Run 'hammer-bench init' or specify a source with #source: directive.", file=sys.stderr)
        return None

    run_id = generate_run_id(config.preset_name)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd, env_vars = build_lake_command(config)
    timeout_seconds = int(config.build_timeout_hours * 3600)

    print(f"Run ID: {run_id}")
    print(f"Preset: {config.preset_name}")
    if source:
        print(f"Source: {source}")
    print(f"Repository: {repo_dir}")
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
        base_commit=get_git_commit(repo_dir),
        base_ref=get_git_ref(repo_dir),
        lean_toolchain=get_lean_toolchain(repo_dir),
        started_at=datetime.now(),
        completed_at=None,
        duration_seconds=None,
        config=config,
        status="running",
        source=source,
    )

    # Save initial metadata
    atomic_write_json(run_dir / "metadata.json", metadata.to_dict())

    # Patch suggestion provider if needed
    patched_file = None
    patched_lakefile = False
    try:
        if config.suggestion_provider and config.suggestion_provider.command:
            if patch_file:
                print(f"Patching suggestion provider in {patch_file}: {config.suggestion_provider.name}")
                patched_file = patch_suggestion_provider(repo_dir, config.suggestion_provider, patch_file)
            else:
                print(f"Warning: No patch_file configured for this repo, skipping suggestion provider patch")

        # Patch lakefile to enable linter
        if config.linters.customTactic:
            linter_option = "linter.tacticAnalysis.tryAtEachStepFromEnv"
            print(f"Patching lakefile.lean to enable {linter_option}")
            patch_lakefile_linter(repo_dir, linter_option, config.linters.fraction)
            patched_lakefile = True

        # Run lake clean
        print("Running lake clean...")
        subprocess.run(
            ["lake", "clean"],
            cwd=repo_dir,
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
                cwd=repo_dir,
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
            unpatch_suggestion_provider(repo_dir, patch_file)
        if patched_lakefile:
            print("Removing lakefile linter patch...")
            unpatch_lakefile_linter(repo_dir)
