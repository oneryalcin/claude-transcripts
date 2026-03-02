"""CLI for claude-transcripts. Usage: python -m claude_transcripts [options]"""

import argparse
import json
import sys
from pathlib import Path

from .core import load, discover


def cmd_info(args):
    s = load(args.file, include_progress=args.progress)
    print(f"Session:  {s.session_id}")
    print(f"Version:  {s.version}")
    print(f"Model:    {s.model}")
    print(f"CWD:      {s.cwd}")
    print(f"Messages: {len(s.messages)}")
    print(f"Tools:    {len(s.tool_calls)}")
    print(f"Tokens:   in={s.usage.input_tokens:,} out={s.usage.output_tokens:,} "
          f"cache_read={s.usage.cache_read_tokens:,}")
    if s.subagents:
        print(f"Subagents: {len(s.subagents)}")


def cmd_tools(args):
    s = load(args.file, include_progress=False)
    calls = s.tool_calls
    if args.name:
        calls = [tc for tc in calls if tc.name == args.name]
    if args.errors:
        calls = [tc for tc in calls if tc.is_error]
    for tc in calls:
        status = "ERR" if tc.is_error else "ok "
        dur = f"{tc.duration_ms}ms" if tc.duration_ms is not None else "?ms"
        print(f"[{status}] {tc.name:<25} {dur:>8}  {list(tc.input.keys())}")


def cmd_messages(args):
    s = load(args.file, include_progress=args.progress)
    msgs = s.messages
    if args.type:
        msgs = [m for m in msgs if m.type == args.type]
    if args.role:
        msgs = [m for m in msgs if m.role == args.role]
    for m in msgs:
        text = m.text[:120].replace("\n", "\\n") if m.text else "(no text)"
        print(f"[{m.type:<10}] {m.uuid[:8]}  {text}")


def cmd_discover(args):
    if args.latest:
        p = discover(args.project, latest=True)
        if p and p.exists():
            print(p)
    else:
        for p in discover(args.project):
            print(p)


def cmd_usage(args):
    paths = discover(args.project) if args.project else [Path(args.file)]
    total_in = total_out = total_cache = total_calls = 0
    for p in paths:
        s = load(p, include_progress=False)
        total_in += s.usage.input_tokens
        total_out += s.usage.output_tokens
        total_cache += s.usage.cache_read_tokens
        total_calls += s.usage.api_calls
        if not args.project:
            break
    print(f"API calls:    {total_calls:,}")
    print(f"Input tokens: {total_in:,}")
    print(f"Output tokens:{total_out:,}")
    print(f"Cache read:   {total_cache:,}")


def main():
    parser = argparse.ArgumentParser(
        prog="claude-transcripts",
        description="Inspect Claude Code session transcripts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # info
    p = sub.add_parser("info", help="Session summary")
    p.add_argument("file", help="Path to .jsonl session file")
    p.add_argument("--progress", action="store_true", help="Include progress messages")

    # tools
    p = sub.add_parser("tools", help="List tool calls")
    p.add_argument("file", help="Path to .jsonl session file")
    p.add_argument("--name", help="Filter by tool name")
    p.add_argument("--errors", action="store_true", help="Only show errors")

    # messages
    p = sub.add_parser("messages", help="List messages")
    p.add_argument("file", help="Path to .jsonl session file")
    p.add_argument("--type", help="Filter by message type")
    p.add_argument("--role", help="Filter by role (user/assistant)")
    p.add_argument("--progress", action="store_true", help="Include progress messages")

    # discover
    p = sub.add_parser("discover", help="Find session files")
    p.add_argument("project", nargs="?", help="Project path")
    p.add_argument("--latest", action="store_true", help="Only latest session")

    # usage
    p = sub.add_parser("usage", help="Token usage summary")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="Single session file")
    g.add_argument("--project", help="All sessions for a project path")

    args = parser.parse_args()
    commands = {
        "info": cmd_info,
        "tools": cmd_tools,
        "messages": cmd_messages,
        "discover": cmd_discover,
        "usage": cmd_usage,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
