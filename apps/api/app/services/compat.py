"""Small helper for calling evolving Protocol methods against older duck-typed
implementations (test stubs, older adapters) without breaking them.

Ports in this codebase are structurally typed (`Protocol`), so adding a new optional
keyword argument to a port method can't be enforced onto every existing implementation.
This inspects the target callable's signature and only forwards kwargs it actually
accepts, so callers can widen a Protocol's method without touching every caller.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def call_if_supported(fn: Callable[..., T], *args: Any, **optional_kwargs: Any) -> T:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args)
    accepts_var_kwargs = any(p.kind is p.VAR_KEYWORD for p in params.values())
    supported = {
        key: value
        for key, value in optional_kwargs.items()
        if accepts_var_kwargs or key in params
    }
    return fn(*args, **supported)
