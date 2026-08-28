"""Request/run correlation context shared by logs and durable incidents."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional


_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "xbrl_correlation_id", default=None,
)


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def bind_correlation_id(value: str) -> Token[Optional[str]]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[Optional[str]]) -> None:
    _correlation_id.reset(token)

