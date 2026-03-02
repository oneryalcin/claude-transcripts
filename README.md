# claude-transcripts

Parse and inspect Claude Code session transcripts with full schema parity.

Built on [agent-schemas](https://github.com/oneryalcin/agent-schemas) — typed Pydantic models generated from JSON Schema definitions for every message type, content block, and tool input.

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

# Core data — typed Pydantic models, not raw dicts
s.messages      # list[AnyMessage] — typed per message kind
s.tool_calls    # list[ToolCall] — tool_use + tool_result pre-joined
s.usage         # Usage — aggregated tokens
s.subagents     # list[Session] — subagent sessions (lazy-loaded)
s.version       # "2.1.63"
s.model         # "claude-opus-4-6"
```

### Typed Access (full schema parity)

Messages are Pydantic models with full IDE autocomplete:

```python
from claude_transcripts import load, schema

s = load("session.jsonl")

for m in s.messages:
    if isinstance(m, schema.AssistantMessage):
        print(m.message.model.root)          # "claude-opus-4-6"
        print(m.message.usage.input_tokens)  # 1234
        for block in m.message.content:
            b = block.root
            if isinstance(b, schema.ToolUseBlock):
                print(b.name.root, b.input)
            elif isinstance(b, schema.TextBlock):
                print(b.text[:100])

    elif isinstance(m, schema.SystemMessage):
        print(m.subtype.value, m.content)
```

Lines that don't match the schema (e.g., new subtypes in newer CLI versions) gracefully fall back to plain dicts.

### Filtering (plain Python, no DSL)

```python
# Find failed tool calls
errors = [tc for tc in s.tool_calls if tc.is_error]
for e in errors:
    print(f"{e.name}: {e.result[:100]}")

# Bash commands only
bash = [tc for tc in s.tool_calls if tc.name == "Bash"]

# Assistant messages (type-safe)
assistant_msgs = [m for m in s.messages if isinstance(m, schema.AssistantMessage)]

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
progress = [m for m in s.messages if isinstance(m, schema.ProgressMessage)]
```

## Data Types

| Type | Description |
|------|-------------|
| `Session` | Loaded session with messages, tool_calls, usage, subagents |
| `ToolCall` | Pre-joined tool_use + tool_result with name, input, result, is_error, duration_ms |
| `Usage` | Aggregated input/output/cache tokens and API call count |
| `schema.*` | Full Pydantic models: `UserMessage`, `AssistantMessage`, `SystemMessage`, `ToolUseBlock`, `BashInput`, etc. |

`ToolCall` also exposes `.tool_use_block` and `.tool_result_block` for direct access to the underlying schema types.

## Architecture

- **`_schema.py`** — Vendored Pydantic v2 models generated from [agent-schemas](https://github.com/oneryalcin/agent-schemas) JSON Schema (v2.1.59)
- **`core.py`** — Session loading, tool pairing, usage aggregation, path discovery
- **`__main__.py`** — CLI interface

## License

MIT
