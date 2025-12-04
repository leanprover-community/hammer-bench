"""Core data models and storage for hammer benchmarking."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import os
import socket
import subprocess


@dataclass
class LinterConfig:
    """Configuration for which linters to enable."""
    tryAtEachStepGrind: bool = False
    tryAtEachStepSimpAll: bool = False
    tryAtEachStepAesop: bool = False
    tryAtEachStepGrindSuggestions: bool = False
    tryAtEachStepSimpAllSuggestions: bool = False
    fraction: int = 1

    def to_dict(self) -> dict:
        return {
            "tryAtEachStepGrind": self.tryAtEachStepGrind,
            "tryAtEachStepSimpAll": self.tryAtEachStepSimpAll,
            "tryAtEachStepAesop": self.tryAtEachStepAesop,
            "tryAtEachStepGrindSuggestions": self.tryAtEachStepGrindSuggestions,
            "tryAtEachStepSimpAllSuggestions": self.tryAtEachStepSimpAllSuggestions,
            "fraction": self.fraction,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LinterConfig":
        return cls(
            tryAtEachStepGrind=d.get("tryAtEachStepGrind", False),
            tryAtEachStepSimpAll=d.get("tryAtEachStepSimpAll", False),
            tryAtEachStepAesop=d.get("tryAtEachStepAesop", False),
            tryAtEachStepGrindSuggestions=d.get("tryAtEachStepGrindSuggestions", False),
            tryAtEachStepSimpAllSuggestions=d.get("tryAtEachStepSimpAllSuggestions", False),
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
class RunConfig:
    """Configuration for a benchmark run."""
    preset_name: str
    linters: LinterConfig
    suggestion_provider: Optional[SuggestionProvider] = None
    timing_mode: bool = True
    build_timeout_hours: float = 6.0

    def to_dict(self) -> dict:
        return {
            "preset_name": self.preset_name,
            "linters": self.linters.to_dict(),
            "suggestion_provider": self.suggestion_provider.to_dict() if self.suggestion_provider else None,
            "timing_mode": self.timing_mode,
            "build_timeout_hours": self.build_timeout_hours,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunConfig":
        return cls(
            preset_name=d["preset_name"],
            linters=LinterConfig.from_dict(d["linters"]),
            suggestion_provider=SuggestionProvider.from_dict(d["suggestion_provider"]) if d.get("suggestion_provider") else None,
            timing_mode=d.get("timing_mode", True),
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

    def to_dict(self) -> dict:
        d = {
            "file": self.file,
            "row": self.row,
            "col": self.col,
            "original": self.original,
            "replacement": self.replacement,
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
    message_count: int = 0
    timed_out: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
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
            "message_count": self.message_count,
            "timed_out": self.timed_out,
            "error": self.error,
        }

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
            message_count=d.get("message_count", 0),
            timed_out=d.get("timed_out", False),
            error=d.get("error"),
        )


def get_hammer_bench_dir() -> Path:
    """Get the hammer-bench directory."""
    return Path.home() / "hammer-bench"


def get_mathlib_dir() -> Path:
    """Get the mathlib4 worktree directory."""
    return get_hammer_bench_dir() / "mathlib4"


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
    return get_git_commit(repo_path)[:12]


def get_lean_toolchain(repo_path: Path) -> str:
    """Get the Lean toolchain version from lean-toolchain file."""
    toolchain_file = repo_path / "lean-toolchain"
    if toolchain_file.exists():
        return toolchain_file.read_text().strip()
    return "unknown"


def generate_run_id(preset_name: str) -> str:
    """Generate a unique run ID."""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    commit_short = get_git_commit(get_mathlib_dir())[:7] if get_mathlib_dir().exists() else "unknown"
    return f"{timestamp}_{preset_name}_{commit_short}"


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON data atomically (write to temp, then rename)."""
    temp_path = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(temp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.rename(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
