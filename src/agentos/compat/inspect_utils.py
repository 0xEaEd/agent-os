"""Callable-signature introspection shared by the gateway and the engine.

Optional keyword arguments are threaded through a lot of seams here — a
``compact`` method that may or may not take ``compaction_id``, a channel's
``send_typing`` that may or may not take ``thread_id``, a ``TurnRunner.run``
that may or may not take ``attachments``. Every one of those call sites asks
the same question, and four modules answered it with their own copy of the
same six lines.

The copies had drifted where it matters most: on a callable whose signature
cannot be read, ``rpc_sessions`` returned ``True`` (pass the keyword anyway),
``channel_dispatch`` and ``context_overflow`` returned ``False``, and
``engine.runtime`` let the exception escape into the caller. This module is
the single answer.
"""

from __future__ import annotations

import inspect
from typing import Any

__all__ = ["accepts_keyword_arg"]


def accepts_keyword_arg(callable_obj: Any, name: str) -> bool:
    """Return True when *callable_obj* accepts *name* as a keyword argument.

    True when the parameter is declared explicitly, and true when a
    ``**kwargs`` catch-all would absorb it.

    A callable whose signature cannot be read at all — :func:`inspect.signature`
    raises ``TypeError`` or ``ValueError`` for some C builtins and for objects
    with an unusable ``__signature__`` — is reported as *not* accepting the
    keyword. That is the safe direction for the callers: passing a keyword the
    target does not take raises ``TypeError`` and fails the turn, while
    omitting an optional one leaves the target on its own default.
    """
    try:
        params = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
