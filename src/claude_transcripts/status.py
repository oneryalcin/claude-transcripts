"""Lightweight session status checks. No Pydantic, no full parsing.

Reads raw lines for speed — suitable for stop hooks and background
agent tracking where latency matters.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_QUEUE_OP_COMPLETION_PATTERN = re.compile(
    r"<agent-notification>.*?<agent-id>([a-f0-9]+)</agent-id>"
    r".*?<status>(completed|error)</status>",
    re.IGNORECASE | re.DOTALL,
)

DEFAULT_STALENESS_MINUTES = 10


def has_result(
    jsonl_path: Path,
    max_retries: int = 1,
    retry_delay: float = 0.1,
) -> bool:
    """Check if JSONL indicates agent completion.

    Completion = last assistant message has only text content (no tool_use).
    Lightweight: scans backward from end, no Pydantic parsing.
    Retry logic handles partial disk flushes.
    """
    for attempt in range(max_retries + 1):
        if not jsonl_path.exists():
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            return False

        try:
            content = jsonl_path.read_text().strip()
            if not content:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return False

            lines = [line.strip() for line in content.split("\n") if line.strip()]
            if not lines:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return False

            retry = False
            for line in reversed(lines):
                try:
                    last_msg = json.loads(line)
                except json.JSONDecodeError:
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        retry = True
                        break
                    return False

                msg_type = last_msg.get("type")

                # Legacy completion marker
                if msg_type == "result":
                    return True

                if msg_type == "assistant":
                    message = last_msg.get("message")
                    if not isinstance(message, dict):
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            retry = True
                            break
                        return False

                    content_blocks = message.get("content", [])
                    if isinstance(content_blocks, list):
                        has_tool_use = any(
                            block.get("type") == "tool_use"
                            for block in content_blocks
                            if isinstance(block, dict)
                        )
                        return not has_tool_use

                    if attempt < max_retries:
                        time.sleep(retry_delay)
                        retry = True
                        break
                    return False

                # Skip non-message entries (system/queue-operation/etc.)
                continue

            if retry:
                continue

            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            return False

        except (OSError, IOError):
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            return False

    return False


def is_stale(
    jsonl_path: Path,
    threshold_minutes: int | None = None,
    has_result_precomputed: bool | None = None,
) -> tuple[bool, float | None]:
    """Check if JSONL file appears stale (agent likely crashed).

    Stale = file exists + no modification for threshold minutes + not completed.
    Returns (is_stale, age_minutes). age_minutes is None if file doesn't exist.
    """
    if threshold_minutes is None:
        try:
            threshold_minutes = int(
                os.environ.get(
                    "AGENT_STALENESS_TIMEOUT_MINUTES", DEFAULT_STALENESS_MINUTES
                )
            )
        except ValueError:
            threshold_minutes = DEFAULT_STALENESS_MINUTES

    if not jsonl_path.exists():
        return False, None

    try:
        mtime = jsonl_path.stat().st_mtime
        age_minutes = (time.time() - mtime) / 60

        if age_minutes < 0:
            return False, age_minutes

        if age_minutes < threshold_minutes:
            return False, age_minutes

        if has_result_precomputed is None:
            has_result_precomputed = has_result(jsonl_path)

        if has_result_precomputed:
            return False, age_minutes

        return True, age_minutes

    except (OSError, IOError):
        return False, None


def queue_operation_completions(
    session_jsonl_path: Path,
    agent_ids: set[str] | None = None,
) -> set[str]:
    """Scan session JSONL for queue-operation agent-notification completions.

    Uses string pre-filter before JSON parse for speed.
    """
    completed: set[str] = set()
    if not session_jsonl_path.exists():
        return completed

    try:
        with open(session_jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "queue-operation" not in line or "agent-notification" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "queue-operation":
                    continue
                content = obj.get("content")
                if not isinstance(content, str):
                    continue
                match = _QUEUE_OP_COMPLETION_PATTERN.search(content)
                if not match:
                    continue
                agent_id = match.group(1).lower()
                if agent_ids is None or agent_id in agent_ids:
                    completed.add(agent_id)
    except (OSError, IOError):
        return completed

    return completed
