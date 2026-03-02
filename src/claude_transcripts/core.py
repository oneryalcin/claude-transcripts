"""Claude Code session transcript reader. Parse, unwrap, join."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Message:
    type: str
    role: str | None
    uuid: str
    timestamp: str
    text: str
    content_blocks: list[dict]
    raw: dict


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    tool_use_id: str
    input: dict
    result: str | None
    is_error: bool
    duration_ms: int | None
    assistant_uuid: str
    user_uuid: str | None


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    api_calls: int


@dataclass
class Session:
    path: Path
    session_id: str
    messages: list[Message]
    tool_calls: list[ToolCall]
    usage: Usage
    version: str | None
    model: str | None
    cwd: str | None
    _subagent_dir: Path | None = field(default=None, repr=False)
    _include_progress: bool = field(default=False, repr=False)

    @cached_property
    def subagents(self) -> list[Session]:
        if not self._subagent_dir or not self._subagent_dir.exists():
            return []
        return [
            load(p, include_progress=self._include_progress)
            for p in sorted(self._subagent_dir.glob("*.jsonl"))
            if p.stat().st_size > 0
        ]

    def walk(self):
        """Depth-first traversal: self, then subagents recursively."""
        yield self
        for sub in self.subagents:
            yield from sub.walk()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _extract_text(content: str | list) -> tuple[str, list[dict]]:
    """Extract concatenated text and raw blocks from message content."""
    if isinstance(content, str):
        return content, []
    blocks = content if isinstance(content, list) else []
    texts = []
    for b in blocks:
        if isinstance(b, dict):
            if b.get("type") == "text":
                texts.append(b.get("text", ""))
            elif b.get("type") == "thinking":
                texts.append(b.get("thinking", ""))
    return "\n".join(texts), blocks


def _parse_message(raw: dict) -> Message:
    msg_type = raw.get("type", "")
    msg_body = raw.get("message", {})
    role = msg_body.get("role") if isinstance(msg_body, dict) else None
    content = msg_body.get("content", "") if isinstance(msg_body, dict) else ""
    text, blocks = _extract_text(content)

    # System messages store content differently
    if msg_type == "system":
        text = raw.get("content", "") or text
    elif msg_type == "summary":
        text = raw.get("summary", "") or text

    return Message(
        type=msg_type,
        role=role,
        uuid=raw.get("uuid", ""),
        timestamp=raw.get("timestamp", ""),
        text=text,
        content_blocks=blocks,
        raw=raw,
    )


def _ts_to_ms(ts: str) -> int | None:
    """Parse ISO-8601 timestamp to epoch ms. Returns None on failure."""
    try:
        # Handle Z suffix and +00:00
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return None


def _build_tool_calls(messages: list[Message]) -> list[ToolCall]:
    """Match tool_use blocks to tool_result blocks across messages."""
    pending: dict[str, tuple[dict, Message]] = {}  # tool_use_id -> (block, msg)
    pairs: list[ToolCall] = []

    for msg in messages:
        if msg.role == "assistant":
            for block in msg.content_blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    pending[block["id"]] = (block, msg)

        elif msg.role == "user":
            for block in msg.content_blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    if tool_use_id in pending:
                        use_block, asst_msg = pending.pop(tool_use_id)
                        # Extract result text
                        result_content = block.get("content")
                        if isinstance(result_content, list):
                            result_content = "\n".join(
                                item.get("text", "") for item in result_content
                                if isinstance(item, dict) and item.get("type") == "text"
                            ) or json.dumps(result_content)
                        elif not isinstance(result_content, str):
                            result_content = str(result_content) if result_content else None

                        # Duration
                        asst_ts = _ts_to_ms(asst_msg.timestamp)
                        user_ts = _ts_to_ms(msg.timestamp)
                        duration = (user_ts - asst_ts) if asst_ts and user_ts else None

                        pairs.append(ToolCall(
                            name=use_block.get("name", ""),
                            tool_use_id=tool_use_id,
                            input=use_block.get("input", {}),
                            result=result_content,
                            is_error=bool(block.get("is_error")),
                            duration_ms=duration,
                            assistant_uuid=asst_msg.uuid,
                            user_uuid=msg.uuid,
                        ))

    # Unmatched tool_uses (interrupted sessions)
    for tool_use_id, (use_block, asst_msg) in pending.items():
        pairs.append(ToolCall(
            name=use_block.get("name", ""),
            tool_use_id=tool_use_id,
            input=use_block.get("input", {}),
            result=None,
            is_error=False,
            duration_ms=None,
            assistant_uuid=asst_msg.uuid,
            user_uuid=None,
        ))

    return pairs


def _build_usage(messages: list[Message]) -> Usage:
    """Aggregate token usage across all assistant messages."""
    inp = out = cache_create = cache_read = calls = 0
    for msg in messages:
        if msg.role != "assistant":
            continue
        usage = msg.raw.get("message", {}).get("usage")
        if not usage:
            continue
        inp += usage.get("input_tokens", 0)
        out += usage.get("output_tokens", 0)
        cache_create += usage.get("cache_creation_input_tokens", 0)
        cache_read += usage.get("cache_read_input_tokens", 0)
        calls += 1
    return Usage(inp, out, cache_create, cache_read, calls)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SKIP_TYPES = frozenset({"progress"})


def load(path: str | Path, *, include_progress: bool = False) -> Session:
    """Load a session from a JSONL file.

    Args:
        path: Path to a .jsonl session file.
        include_progress: If False (default), skip progress messages (~44% of lines).
    """
    path = Path(path).expanduser()
    skip = frozenset() if include_progress else SKIP_TYPES
    messages: list[Message] = []
    version = model = cwd = None

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            msg_type = raw.get("type", "")
            if msg_type in skip:
                continue
            msg = _parse_message(raw)
            messages.append(msg)

            # Extract metadata from first message that has it
            if not version:
                version = raw.get("version")
            if not model and msg.role == "assistant":
                model = raw.get("message", {}).get("model")
            if not cwd:
                cwd = raw.get("cwd")

    tool_calls = _build_tool_calls(messages)
    usage = _build_usage(messages)

    # Determine subagent directory
    session_id = path.stem
    subagent_dir = path.parent / session_id / "subagents"

    return Session(
        path=path,
        session_id=session_id,
        messages=messages,
        tool_calls=tool_calls,
        usage=usage,
        version=version,
        model=model,
        cwd=cwd,
        _subagent_dir=subagent_dir,
        _include_progress=include_progress,
    )


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def discover(
    project_path: str | Path | None = None,
    *,
    latest: bool = False,
) -> list[Path] | Path:
    """Find session JSONL files.

    Args:
        project_path: Absolute project path (e.g., /Users/me/dev/myapp).
            Resolved to ~/.claude/projects/-Users-me-dev-myapp/.
            If None, searches all projects.
        latest: If True, return only the most recently modified file (Path, not list).

    Returns:
        List of Path objects, or a single Path if latest=True.
    """
    if project_path:
        encoded = str(Path(project_path).expanduser()).replace("/", "-")
        search_dir = CLAUDE_PROJECTS_DIR / encoded
    else:
        search_dir = CLAUDE_PROJECTS_DIR

    if not search_dir.exists():
        return Path() if latest else []

    files = sorted(
        (f for f in search_dir.glob("**/*.jsonl") if f.stat().st_size > 0),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if latest:
        return files[0] if files else Path()
    return files
