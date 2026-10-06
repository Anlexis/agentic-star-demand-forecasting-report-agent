"""AgentCore Platform v1.0"""

# RET-C2-281 — carrying caller context across the nested-graph boundary.
#
# The outer graph receives ``input_context`` as a first-class argument of
# ``invoke()`` and the framework seeds it into the outer state. The inner graph
# does not: ``GraphNode.execute()`` calls
# ``subgraph.invoke(user_input, session_id=..., ctx=ctx)`` and passes no
# ``input_context``, so an inner node reading ``state["input_context"]`` sees an
# empty mapping on every real invocation while unit tests that construct the
# inner state by hand pass. The gap is invisible from either side alone.
#
# A ContextVar closes it without changing the framework's signature: the outer
# node stores the already-validated context on the way in, and the inner graph's
# ``_extra_initial_state()`` reads it back when it builds the inner state. A
# ContextVar is the right primitive rather than a module global because it is
# per-execution-context, so two concurrent requests in the same worker cannot
# observe each other's value.
#
# Only values that have ALREADY passed the request contract are put here. This
# module is a transport, not a boundary — it performs no validation, and nothing
# should be stashed that has not been validated first.

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Dict, Optional

_REQUEST_CHANNEL: ContextVar[Optional[str]] = ContextVar("ret_c2_281_request_channel", default=None)


def stash_channel(channel: Optional[str]) -> Token[Optional[str]]:
    """Record the validated caller channel for the inner graph to pick up.

    Returns the ContextVar token so a caller that needs strict scoping (a test,
    or a nested invocation) can reset it; ordinary request handling does not,
    because each request runs in its own context.
    """
    return _REQUEST_CHANNEL.set(channel)


def take_channel() -> Optional[str]:
    """Return the stashed channel, or None when the outer node did not set one."""
    return _REQUEST_CHANNEL.get()


def reset_channel(token: Token[Optional[str]]) -> None:
    """Restore the value the ContextVar held before ``stash_channel``."""
    _REQUEST_CHANNEL.reset(token)


def inner_seed() -> Dict[str, str]:
    """State fragment the inner graph merges into its initial state.

    Empty when no channel was stashed, so the inner nodes keep their declared
    defaults rather than observing a ``None`` they would have to special-case.
    """
    channel = take_channel()
    if not channel:
        return {}
    return {"request_channel": channel}
