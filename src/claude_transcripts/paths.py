"""Path utilities for Claude Code session files.

Pure path math — no file I/O (except agent_path which checks exists()),
no Pydantic, no heavy imports.
"""

from __future__ import annotations

from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def mangle_cwd(cwd: str) -> str:
    """Mangle working directory path for Claude project storage.

    Claude stores project files at ~/.claude/projects/{mangled_cwd}/
    where mangled_cwd is the cwd with slashes replaced by dashes.
    """
    return cwd.replace("\\", "/").replace("/", "-")


def projects_base(cwd: str) -> Path:
    """~/.claude/projects/{mangled_cwd}/"""
    return CLAUDE_PROJECTS_DIR / mangle_cwd(cwd)


def session_path(session_id: str, cwd: str) -> Path:
    """{base}/{session_id}.jsonl"""
    return projects_base(cwd) / f"{session_id}.jsonl"


def subagents_dir(session_id: str, cwd: str) -> Path:
    """{base}/{session_id}/subagents/"""
    return projects_base(cwd) / session_id / "subagents"


def agent_path(
    agent_id: str, cwd: str, session_id: str | None = None
) -> Path:
    """Find agent JSONL path. Checks root first, falls back to subagents dir.

    Returns the path (may not exist on disk).
    """
    base = projects_base(cwd)
    root = base / f"agent-{agent_id}.jsonl"
    if root.exists():
        return root
    if session_id:
        return base / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    return root


def all_agent_paths(cwd: str, session_id: str | None = None) -> list[Path]:
    """Glob for all agent-*.jsonl files (root + subagents dir)."""
    base = projects_base(cwd)
    found: list[Path] = []
    if base.exists():
        found.extend(base.glob("agent-*.jsonl"))
    if session_id:
        sub = base / session_id / "subagents"
        if sub.exists():
            found.extend(sub.glob("agent-*.jsonl"))
    return found
