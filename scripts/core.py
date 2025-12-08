"""Core data models and storage for hammer benchmarking."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import os
import socket
import subprocess

# Constants
GIT_HASH_DISPLAY_LENGTH = 12


@dataclass
class LinterConfig:
    """Configuration for which linters to enable.

    Uses the generic TRY_AT_EACH_STEP_* mechanism via environment variables.
    Set customTactic to the tactic string and optionally customTacticLabel for display.
    """
    customTactic: Optional[str] = None
    customTacticLabel: Optional[str] = None
    fraction: int = 1

    def to_dict(self) -> dict:
        d = {"fraction": self.fraction}
        if self.customTactic:
            d["customTactic"] = self.customTactic
        if self.customTacticLabel:
            d["customTacticLabel"] = self.customTacticLabel
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LinterConfig":
        return cls(
            customTactic=d.get("customTactic"),
            customTacticLabel=d.get("customTacticLabel"),
            fraction=d.get("fraction", 1),
        )


@dataclass
class SuggestionProvider:
    """Configuration for a suggestion provider."""
    name: str
    command: Optional[str]  # None means use default

    def to_dict(self) -> dict:
        return {"name": self.name, "command": self.command}

    @classmethod
    def from_dict(cls, d: dict) -> "SuggestionProvider":
        return cls(name=d["name"], command=d.get("command"))


@dataclass
class RepoConfig:
    """Configuration for a repository."""
    name: str
    url: str
    default_ref: str = "master"
    patch_file: Optional[str] = None  # File to patch for set_library_suggestions

    def to_dict(self) -> dict:
        d = {"name": self.name, "url": self.url, "default_ref": self.default_ref}
        if self.patch_file:
            d["patch_file"] = self.patch_file
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "RepoConfig":
        return cls(
            name=name,
            url=d["url"],
            default_ref=d.get("default_ref", "master"),
            patch_file=d.get("patch_file"),
        )


@dataclass
class SourceSpec:
    """Specification of which repository and ref to use.

    Supports two formats:
        1. Full GitHub path: owner/repo@ref
           Example: leanprover-community/mathlib4@master

        2. Short repo name (from repos.yaml): repo_name@ref
           Example: mathlib4@master
           Example: mathlib4-nightly-testing@hammer_measurements

    The repo_name format is resolved via config/repos.yaml.
    """
    repo: str  # e.g., "leanprover-community/mathlib4" or "mathlib4" (short name)
    ref: str   # e.g., "master", "hammer_measurements"

    @property
    def repo_name(self) -> str:
        """Get the short repo name (last component of path, or the repo itself if short)."""
        if "/" in self.repo:
            return self.repo.rsplit("/", 1)[1]
        return self.repo

    @property
    def is_short_name(self) -> bool:
        """Check if this uses a short repo name (vs full owner/repo path)."""
        return "/" not in self.repo

    def resolve_url(self, repos: dict) -> str:
        """Get the GitHub URL for this repo.

        Args:
            repos: Dict of repo configs (from repos.yaml)

        Raises:
            ValueError: If short name is not found in repos config
        """
        if "/" in self.repo:
            # Full owner/repo format - use directly
            return f"https://github.com/{self.repo}.git"
        elif self.repo in repos:
            # Short name found in config
            return repos[self.repo].url
        else:
            raise ValueError(
                f"Unknown repository '{self.repo}'. "
                f"Either use full 'owner/repo@ref' format, or define '{self.repo}' in config/repos.yaml"
            )

    def to_dict(self) -> dict:
        return {"repo": self.repo, "ref": self.ref}

    @classmethod
    def from_dict(cls, d: dict) -> "SourceSpec":
        return cls(repo=d["repo"], ref=d["ref"])

    @classmethod
    def parse(cls, spec: str) -> "SourceSpec":
        """Parse a source spec string like 'repo@ref' or 'owner/repo@ref'."""
        if "@" not in spec:
            raise ValueError(f"Invalid source spec '{spec}' - must be 'repo@ref' or 'owner/repo@ref'")
        repo, ref = spec.rsplit("@", 1)
        return cls(repo=repo.strip(), ref=ref.strip())

    def __str__(self) -> str:
        return f"{self.repo}@{self.ref}"


@dataclass
class RunConfig:
    """Configuration for a benchmark run."""
    preset_name: str
    linters: LinterConfig
    suggestion_provider: Optional[SuggestionProvider] = None
    timing_mode: bool = True
    build_timeout_hours: float = 6.0
    # Target collection name (e.g., "ordered_field") and resolved targets
    target_collection: str = "all"
    targets: list[str] | None = None  # List of lake build targets, defaults to ["Mathlib"]

    def __post_init__(self):
        if self.targets is None:
            self.targets = ["Mathlib"]

    def to_dict(self) -> dict:
        d = {
            "preset_name": self.preset_name,
            "linters": self.linters.to_dict(),
            "suggestion_provider": self.suggestion_provider.to_dict() if self.suggestion_provider else None,
            "timing_mode": self.timing_mode,
            "build_timeout_hours": self.build_timeout_hours,
        }
        if self.target_collection != "all":
            d["target_collection"] = self.target_collection
            d["targets"] = self.targets
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RunConfig":
        return cls(
            preset_name=d["preset_name"],
            linters=LinterConfig.from_dict(d["linters"]),
            suggestion_provider=SuggestionProvider.from_dict(d["suggestion_provider"]) if d.get("suggestion_provider") else None,
            timing_mode=d.get("timing_mode", True),
            target_collection=d.get("target_collection", "all"),
            targets=d.get("targets"),
            build_timeout_hours=d.get("build_timeout_hours", 6.0),
        )


@dataclass
class Message:
    """A single 'can be replaced with' message."""
    file: str
    row: int
    col: int
    original: str
    replacement: str
    time_ms: Optional[int] = None
    later_steps: int = 0  # Number of later steps that would become obsolete

    def to_dict(self) -> dict:
        d = {
            "file": self.file,
            "row": self.row,
            "col": self.col,
            "original": self.original,
            "replacement": self.replacement,
            "later_steps": self.later_steps,
        }
        if self.time_ms is not None:
            d["time_ms"] = self.time_ms
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            file=d["file"],
            row=d["row"],
            col=d["col"],
            original=d["original"],
            replacement=d["replacement"],
            time_ms=d.get("time_ms"),
            later_steps=d.get("later_steps", 0),
        )


@dataclass
class RunMetadata:
    """Metadata for a completed benchmark run."""
    run_id: str
    machine: str
    base_commit: str
    base_ref: str
    lean_toolchain: str
    started_at: datetime
    completed_at: Optional[datetime]
    duration_seconds: Optional[int]
    config: RunConfig
    status: str  # "running", "completed", "failed", "timed_out"
    source: Optional[SourceSpec] = None  # Explicit repo/ref specification
    replacement_count: int = 0  # Number of "can be replaced" messages
    steps_replaced: int = 0  # Total proof steps replaced (sum of 1+later_steps)
    panic_count: int = 0  # Number of PANIC messages during build
    timed_out: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "run_id": self.run_id,
            "machine": self.machine,
            "base_commit": self.base_commit,
            "base_ref": self.base_ref,
            "lean_toolchain": self.lean_toolchain,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "config": self.config.to_dict(),
            "status": self.status,
            "replacement_count": self.replacement_count,
            "steps_replaced": self.steps_replaced,
            "panic_count": self.panic_count,
            "timed_out": self.timed_out,
            "error": self.error,
        }
        if self.source:
            d["source"] = self.source.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RunMetadata":
        return cls(
            run_id=d["run_id"],
            machine=d["machine"],
            base_commit=d["base_commit"],
            base_ref=d["base_ref"],
            lean_toolchain=d["lean_toolchain"],
            started_at=datetime.fromisoformat(d["started_at"]),
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
            duration_seconds=d.get("duration_seconds"),
            config=RunConfig.from_dict(d["config"]),
            status=d["status"],
            source=SourceSpec.from_dict(d["source"]) if d.get("source") else None,
            replacement_count=d.get("replacement_count", 0),
            steps_replaced=d.get("steps_replaced", 0),
            panic_count=d.get("panic_count", 0),
            timed_out=d.get("timed_out", False),
            error=d.get("error"),
        )


def get_hammer_bench_dir() -> Path:
    """Get the hammer-bench directory (auto-detected from script location).

    Can be overridden via HAMMER_BENCH_DIR environment variable.
    """
    if env_dir := os.environ.get("HAMMER_BENCH_DIR"):
        return Path(env_dir)
    # Auto-detect: this script is at hammer-bench/scripts/core.py
    return Path(__file__).resolve().parent.parent


def get_worktrees_dir() -> Path:
    """Get the worktrees directory."""
    return get_hammer_bench_dir() / "worktrees"


def get_repo_dir(repo_name: str = "mathlib4") -> Path:
    """Get the directory for a specific repository worktree.

    Args:
        repo_name: Short name of the repository (e.g., "mathlib4", "mathlib4-nightly-testing")
    """
    return get_worktrees_dir() / repo_name


def get_mathlib_dir() -> Path:
    """Get the default mathlib4 worktree directory."""
    return get_repo_dir("mathlib4")


def get_runs_dir() -> Path:
    """Get the runs directory."""
    return get_hammer_bench_dir() / "runs"


def get_machine_name() -> str:
    """Get the machine hostname."""
    return socket.gethostname()


def get_git_commit(repo_path: Path) -> str:
    """Get the current git commit hash."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_git_ref(repo_path: Path) -> str:
    """Get the current git ref (branch or tag name)."""
    # Try to get a tag first
    result = subprocess.run(
        ["git", "describe", "--tags", "--exact-match"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()

    # Try to get branch name
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip() != "HEAD":
        return result.stdout.strip()

    # Fall back to commit hash
    return get_git_commit(repo_path)[:GIT_HASH_DISPLAY_LENGTH]


def get_lean_toolchain(repo_path: Path) -> str:
    """Get the Lean toolchain version from lean-toolchain file."""
    toolchain_file = repo_path / "lean-toolchain"
    if toolchain_file.exists():
        return toolchain_file.read_text(encoding="utf-8").strip()
    return "unknown"


def generate_run_id(preset_name: str, repo_dir: Path | None = None) -> str:
    """Generate a unique run ID.

    Args:
        preset_name: Name of the preset being run
        repo_dir: Repository directory to get commit from (defaults to mathlib4)
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    if repo_dir is None:
        repo_dir = get_mathlib_dir()
    commit_short = get_git_commit(repo_dir)[:7] if repo_dir.exists() else "unknown"
    return f"{timestamp}_{preset_name}_{commit_short}"


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON data atomically (write to temp, then rename)."""
    temp_path = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.rename(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
