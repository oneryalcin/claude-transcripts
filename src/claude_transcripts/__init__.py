"""Claude Code session transcript reader."""

from . import paths, status

__all__ = [
    "Session", "ToolCall", "Usage", "AnyMessage", "load", "discover",
    "schema", "paths", "status",
]


def __getattr__(name: str):
    """Lazy import heavy modules (Pydantic) only when accessed."""
    if name in ("Session", "ToolCall", "Usage", "AnyMessage", "load", "discover"):
        from . import core
        return getattr(core, name)
    if name == "schema":
        from . import _schema
        return _schema
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
