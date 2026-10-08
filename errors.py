"""Errors that tools turn into JSON. Messages are safe to show."""

from __future__ import annotations

from typing import Any, Optional


class SubsidiesError(Exception):
    """A problem the caller can act on."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "api_error",
        next_step: str = "",
        status: Optional[int] = None,
        extra: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.next_step = next_step
        self.status = status
        self.extra = dict(extra or {})

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": str(self), "kind": self.kind}
        if self.next_step:
            out["next_step"] = self.next_step
        if self.status is not None:
            out["status"] = self.status
        out.update(self.extra)
        return out
