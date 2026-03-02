# claude-transcripts

Parse and inspect Claude Code session transcripts. Zero dependencies.

## Install

```bash
# From GitHub
pip install git+https://github.com/oneryalcin/claude-transcripts.git
uv pip install git+https://github.com/oneryalcin/claude-transcripts.git

# Or clone locally
git clone https://github.com/oneryalcin/claude-transcripts.git
cd claude-transcripts
uv pip install -e .
```

## CLI

```bash
# Run without installing (uvx-style)
uvx --from git+https://github.com/oneryalcin/claude-transcripts.git claude-transcripts info <session.jsonl>

# Or after install
claude-transcripts discover /Users/me/dev/myapp --latest
claude-transcripts info <session.jsonl>
claude-transcripts tools <session.jsonl> --errors
claude-transcripts tools <session.jsonl> --name Bash
claude-transcripts messages <session.jsonl> --role assistant
claude-transcripts usage --project /Users/me/dev/myapp
```

## Python API

```python
from claude_transcripts import load, discover

# Load a session
s = load("~/.claude/projects/-my-project/session.jsonl")

# Or discover sessions for a project
paths = discover("/Users/me/dev/myapp")
s = load(discover("/Users/me/dev/myapp", latest=True))

# Core data
s.messages      # list[Message] — clean dataclasses, no wrapper noise
s.tool_calls    # list[ToolCall] — tool_use + tool_result pre-joined
s.usage         # Usage — aggregated tokens
s.subagents     # list[Session] — subagent sessions (lazy-loaded)
s.version       # "2.1.63"
s.model         # "claude-opus-4-6"
```

### Filtering (plain Python, no DSL)

```python
# Find failed tool calls
errors = [tc for tc in s.tool_calls if tc.is_error]
for e in errors:
    print(f"{e.name}: {e.result[:100]}")

# Bash commands only
bash = [tc for tc in s.tool_calls if tc.name == "Bash"]

# Assistant messages
assistant_msgs = [m for m in s.messages if m.role == "assistant"]

# Token usage across sessions
sessions = [load(p) for p in discover("/Users/me/dev/myapp")]
total = sum(s.usage.output_tokens for s in sessions)
```

### Subagent Traversal

```python
# Walk the full session tree (parent + subagents)
for session in s.walk():
    print(f"{session.session_id}: {len(session.tool_calls)} tools")

# Aggregate across tree
all_tools = [tc for ss in s.walk() for tc in ss.tool_calls]
```

### Progress Messages

Progress messages (~44% of lines) are skipped by default for performance:

```python
s = load("session.jsonl", include_progress=True)
progress = [m for m in s.messages if m.type == "progress"]
```

## Data Types

| Type | Fields |
|------|--------|
| `Message` | `type`, `role`, `uuid`, `timestamp`, `text`, `content_blocks`, `raw` |
| `ToolCall` | `name`, `tool_use_id`, `input`, `result`, `is_error`, `duration_ms`, `assistant_uuid`, `user_uuid` |
| `Usage` | `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `api_calls` |
| `Session` | `path`, `session_id`, `messages`, `tool_calls`, `usage`, `subagents`, `version`, `model`, `cwd` |

All dataclasses are `frozen=True, slots=True` (except `Session` which uses `@cached_property` for lazy subagent loading).

## License

MIT
