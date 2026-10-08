"""Plugin settings read through Hermes ctx.get_config when available."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .errors import SubsidiesError

_reader: Optional[Callable[[str], Any]] = None

DEFAULT_WATCH_DEADLINE_DAYS = 14
MAX_WATCH_DEADLINE_DAYS = 30
DEFAULT_SEARCH_LIMIT = 30
MAX_SEARCH_LIMIT = 100


def bind_config(getter: Optional[Callable[[str], Any]]) -> None:
    global _reader
    _reader = getter


def _raw(key: str) -> Any:
    """Configured value, or None when the setting is unset or unreadable."""
    if _reader is None:
        return None
    try:
        value = _reader(key)
    except Exception:
        return None
    return None if value in (None, "") else value


def _get(key: str, default: Any) -> Any:
    value = _raw(key)
    return default if value is None else value


def deadline_days_from_state(raw: Any) -> int:
    """Use the days saved at setup. Cron has no plugin ctx, so do not re-read config."""
    if isinstance(raw, bool):
        return DEFAULT_WATCH_DEADLINE_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WATCH_DEADLINE_DAYS
    if days < 1:
        return DEFAULT_WATCH_DEADLINE_DAYS
    return min(days, MAX_WATCH_DEADLINE_DAYS)


def watch_deadline_days(override: Optional[int] = None) -> int:
    """Days saved on the watch.

    Omitted ``deadline_days`` uses the configured value (default 14). An explicit
    argument cannot raise the cap: 30 when the setting is unset, or the
    configured value when the user set one. 99 is stored as that cap, never as 99.
    """
    configured = _raw("watch_deadline_days")
    if configured is None or isinstance(configured, bool):
        base = DEFAULT_WATCH_DEADLINE_DAYS
        cap = MAX_WATCH_DEADLINE_DAYS
    else:
        try:
            base = int(configured)
        except (TypeError, ValueError):
            base = DEFAULT_WATCH_DEADLINE_DAYS
        base = max(1, min(base, MAX_WATCH_DEADLINE_DAYS))
        cap = base
    if override is None:
        return base
    return _bounded_override(override, name="deadline_days", cap=cap)


def _bounded_override(override: Any, *, name: str, cap: int) -> int:
    """Reject 0 and non-integers. Values above ``cap`` stay at ``cap`` (cannot raise it)."""
    if isinstance(override, bool):
        asked = None
    elif isinstance(override, int):
        asked = override
    elif isinstance(override, str) and override.strip().isdigit():
        asked = int(override.strip())
    else:
        asked = None
    if asked is None or asked < 1:
        raise SubsidiesError(
            f"{name} must be an integer from 1 to {cap} (got {override!r}). "
            "0 is rejected and is not changed to 1.",
            kind="bad_request",
            next_step=f"Pass {name} as an integer from 1 to {cap}.",
        )
    return min(asked, cap)


def search_limit(override: Optional[int] = None) -> int:
    configured = _get("search_limit", DEFAULT_SEARCH_LIMIT)
    try:
        base = int(configured)
    except (TypeError, ValueError):
        base = DEFAULT_SEARCH_LIMIT
    base = max(1, min(base, MAX_SEARCH_LIMIT))
    if override is None:
        return base
    return _bounded_override(override, name="limit", cap=base)
