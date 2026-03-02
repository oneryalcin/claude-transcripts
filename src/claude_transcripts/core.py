"""Claude Code session transcript reader. Parse, unwrap, join.

Uses vendored Pydantic models from agent-schemas for full schema parity.
All RootModel .root access is hidden — users get clean Python types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Any

from . import _schema as S


# ---------------------------------------------------------------------------
# Unwrap helpers — hide Pydantic RootModel .root from users
# ---------------------------------------------------------------------------

def _unwrap(val: Any) -> Any:
    """Recursively unwrap RootModel instances."""
    if hasattr(val, "root"):
        return _unwrap(val.root)
    return val


def _unwrap_tool_name(name: S.ToolName) -> str:
    """ToolName -> str. Handles BuiltInToolName enum and MCPToolName pattern."""
    inner = _unwrap(name)
    if isinstance(inner, S.BuiltInToolName):
        return inner.value
    return str(inner)


def _unwrap_uuid(uuid: S.UUID | None) -> str:
    return str(_unwrap(uuid)) if uuid else ""


def _unwrap_timestamp(ts: S.ISO8601Timestamp | None) -> str:
    if not ts:
        return ""
    val = _unwrap(ts)
    return val.isoformat() if hasattr(val, "isoformat") else str(val)


def _ts_to_epoch_ms(ts: S.ISO8601Timestamp | None) -> int | None:
    if not ts:
        return None
    val = _unwrap(ts)
    if hasattr(val, "timestamp"):
        return int(val.timestamp() * 1000)
    return None


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolCall:
    """A matched tool_use + tool_result pair across messages."""
    name: str
    tool_use_id: str
    input: dict
    result: str | None
    is_error: bool
    duration_ms: int | None
    assistant_uuid: str
    user_uuid: str | None
    # Full typed objects for power users who want schema parity
    tool_use_block: Any = field(repr=False, default=None)
    tool_result_block: Any = field(repr=False, default=None)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    api_calls: int


# Typed message = Pydantic model. Falls back to dict for unknown schemas.
AnyMessage = (
    S.UserMessage
    | S.AssistantMessage
    | S.SystemMessage
    | S.SummaryMessage
    | S.FileHistorySnapshot
    | S.QueueOperation
    | S.ProgressMessage
    | S.PRLinkMessage
    | dict  # fallback for lines that don't match the schema
)


@dataclass
class Session:
    path: Path
    session_id: str
    messages: list[AnyMessage]
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
# Parsing
# ---------------------------------------------------------------------------

def _parse_line(line: str) -> AnyMessage | dict:
    """Parse a JSONL line into a typed Pydantic model.

    Falls back to raw dict if validation fails (e.g., new enum values
    in newer CLI versions than the schema covers).
    """
    try:
        return S.ClaudeCodeSessionSchemaV2163.model_validate_json(line).root
    except Exception:
        return json.loads(line)


def _extract_result_text(block: S.ToolResultBlock) -> str | None:
    """Extract text from a ToolResultBlock's content."""
    content = block.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            inner = _unwrap(item)
            if isinstance(inner, str):
                texts.append(inner)
            elif hasattr(inner, "text"):
                texts.append(inner.text)
        return "\n".join(texts) if texts else json.dumps(
            [_unwrap(i) for i in content], default=str
        )
    return None


def _build_tool_calls(messages: list[AnyMessage]) -> list[ToolCall]:
    """Match tool_use blocks to tool_result blocks across messages."""
    pending: dict[str, tuple[S.ToolUseBlock, AnyMessage]] = {}
    pairs: list[ToolCall] = []

    for msg in messages:
        if isinstance(msg, S.AssistantMessage):
            for block_wrapper in msg.message.content:
                block = _unwrap(block_wrapper)
                if isinstance(block, S.ToolUseBlock):
                    pending[block.id] = (block, msg)

        elif isinstance(msg, S.UserMessage):
            content = _unwrap(msg.message.content)
            if isinstance(content, list):
                for block_wrapper in content:
                    block = _unwrap(block_wrapper)
                    if isinstance(block, S.ToolResultBlock):
                        tool_use_id = block.tool_use_id
                        if tool_use_id in pending:
                            use_block, asst_msg = pending.pop(tool_use_id)
                            asst_ts = _ts_to_epoch_ms(asst_msg.timestamp)
                            user_ts = _ts_to_epoch_ms(msg.timestamp)
                            duration = (user_ts - asst_ts) if asst_ts and user_ts else None

                            pairs.append(ToolCall(
                                name=_unwrap_tool_name(use_block.name),
                                tool_use_id=tool_use_id,
                                input=use_block.input,
                                result=_extract_result_text(block),
                                is_error=bool(block.is_error),
                                duration_ms=duration,
                                assistant_uuid=_unwrap_uuid(asst_msg.uuid),
                                user_uuid=_unwrap_uuid(msg.uuid),
                                tool_use_block=use_block,
                                tool_result_block=block,
                            ))

    # Unmatched tool_uses (interrupted sessions)
    for tool_use_id, (use_block, asst_msg) in pending.items():
        pairs.append(ToolCall(
            name=_unwrap_tool_name(use_block.name),
            tool_use_id=tool_use_id,
            input=use_block.input,
            result=None,
            is_error=False,
            duration_ms=None,
            assistant_uuid=_unwrap_uuid(asst_msg.uuid),
            user_uuid=None,
            tool_use_block=use_block,
            tool_result_block=None,
        ))

    return pairs


def _build_usage(messages: list[AnyMessage]) -> Usage:
    """Aggregate token usage across all assistant messages."""
    inp = out = cache_create = cache_read = calls = 0
    for msg in messages:
        if not isinstance(msg, S.AssistantMessage):
            continue
        usage = msg.message.usage
        inp += usage.input_tokens
        out += usage.output_tokens
        cache_create += usage.cache_creation_input_tokens or 0
        cache_read += usage.cache_read_input_tokens or 0
        calls += 1
    return Usage(inp, out, cache_create, cache_read, calls)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(path: str | Path, *, include_progress: bool = False) -> Session:
    """Load a session from a JSONL file.

    Args:
        path: Path to a .jsonl session file.
        include_progress: If False (default), skip progress messages (~44% of lines).
    """
    path = Path(path).expanduser()
    messages: list[AnyMessage] = []
    version = model = cwd = None

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Quick pre-filter before Pydantic parsing
            if not include_progress and '"type":"progress"' in line:
                continue

            msg = _parse_line(line)
            messages.append(msg)

            # Extract metadata from first message that has it
            if isinstance(msg, dict):
                if not version:
                    version = msg.get("version")
                if not model and msg.get("type") == "assistant":
                    model = msg.get("message", {}).get("model")
                if not cwd:
                    cwd = msg.get("cwd")
            else:
                if not version and hasattr(msg, "version"):
                    version = str(_unwrap(msg.version))
                if not model and isinstance(msg, S.AssistantMessage):
                    model = str(_unwrap(msg.message.model))
                if not cwd and hasattr(msg, "cwd"):
                    cwd = msg.cwd

    tool_calls = _build_tool_calls(messages)
    usage = _build_usage(messages)

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
