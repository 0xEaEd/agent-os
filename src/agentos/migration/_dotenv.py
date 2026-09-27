"""Read one ``.env`` value the way the source runtimes' dotenv loaders do."""

from __future__ import annotations

import re

_INLINE_COMMENT = re.compile(r"\s+#")


def parse_env_value(raw: str) -> str:
    """Return the value of a ``KEY=<raw>`` line, without quotes or a trailing comment.

    OpenClaw and Hermes both load ``.env`` through a dotenv library: a quoted
    value runs to its closing quote, and an unquoted one ends at the first
    whitespace followed by ``#``. ``KEY=sk-1 # work`` and ``KEY='sk-1'  # work``
    are both ``sk-1`` there. Trimming quote characters off the two ends, as the
    migrators did, kept the comment (and the closing quote) in the migrated key.

    Nothing is unescaped, matching how AgentOS itself reads a ``.env`` value.
    """
    value = raw.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        if end != -1:
            return value[1:end]
    match = _INLINE_COMMENT.search(value)
    if match:
        value = value[: match.start()]
    return value.strip().strip('"').strip("'")
