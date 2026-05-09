"""Standardized error codes for agent-friendly error handling.

Every error raised by the SDK should carry a stable `code` so downstream
agents can branch on machine-readable values rather than parsing messages.
"""
from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Standardized codes — DO NOT rename or remove. Add only.
# ---------------------------------------------------------------------------

class ErrorCode:
    AUTH_REQUIRED       = "AUTH_REQUIRED"
    TOKEN_EXPIRED       = "TOKEN_EXPIRED"
    TOKEN_INVALID       = "TOKEN_INVALID"
    UNAUTHORIZED        = "UNAUTHORIZED"
    RATE_LIMITED        = "RATE_LIMITED"
    NOT_FOUND           = "NOT_FOUND"
    LEAGUE_NOT_FOUND    = "LEAGUE_NOT_FOUND"
    USER_NOT_FOUND      = "USER_NOT_FOUND"
    PLAYER_NOT_FOUND    = "PLAYER_NOT_FOUND"
    TRADE_NOT_FOUND     = "TRADE_NOT_FOUND"
    TRADE_EXPIRED       = "TRADE_EXPIRED"
    TRADE_INVALID       = "TRADE_INVALID"
    INVALID_LINEUP      = "INVALID_LINEUP"
    WAIVER_PERIOD_CLOSED = "WAIVER_PERIOD_CLOSED"
    KTC_STALE           = "KTC_STALE"
    PREVIEW_EXPIRED     = "PREVIEW_EXPIRED"
    PREVIEW_NOT_FOUND   = "PREVIEW_NOT_FOUND"
    IDEMPOTENCY_REPLAY  = "IDEMPOTENCY_REPLAY"
    UPSTREAM_ERROR      = "UPSTREAM_ERROR"
    NETWORK_ERROR       = "NETWORK_ERROR"
    VALIDATION_ERROR    = "VALIDATION_ERROR"
    UNSUPPORTED         = "UNSUPPORTED"
    INTERNAL            = "INTERNAL"


_RETRYABLE = {
    ErrorCode.RATE_LIMITED,
    ErrorCode.NETWORK_ERROR,
    ErrorCode.UPSTREAM_ERROR,
}


class SleeperError(Exception):
    """Base error for the SDK. Every concrete error carries a stable `code`."""

    code: str = ErrorCode.INTERNAL

    def __init__(self, message: str = "", *, code: Optional[str] = None,
                 status_code: Optional[int] = None, details: Optional[dict] = None):
        if code is not None:
            self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self._fmt())

    def _fmt(self) -> str:
        sc = f" (HTTP {self.status_code})" if self.status_code else ""
        return f"[{self.code}]{sc} {self.message}"

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "status_code": self.status_code,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Concrete subclasses — convenient constructors. All optional; raising
# `SleeperError(code=...)` directly is equivalent.
# ---------------------------------------------------------------------------

class SleeperApiError(SleeperError):
    """Generic upstream Sleeper API error. Back-compat with older code."""
    code = ErrorCode.UPSTREAM_ERROR

    def __init__(self, status_code: int, message: str = ""):
        # Map HTTP status to a more specific code when obvious.
        code = ErrorCode.UPSTREAM_ERROR
        if status_code == 401:
            code = ErrorCode.UNAUTHORIZED
        elif status_code == 404:
            code = ErrorCode.NOT_FOUND
        elif status_code == 429:
            code = ErrorCode.RATE_LIMITED
        super().__init__(message, code=code, status_code=status_code)


class SleeperNotFoundError(SleeperApiError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(404, message)


class SleeperRateLimitError(SleeperApiError):
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(429, message)


class SleeperAuthError(SleeperError):
    code = ErrorCode.AUTH_REQUIRED


class SleeperValidationError(SleeperError):
    code = ErrorCode.VALIDATION_ERROR


class SleeperPreviewError(SleeperError):
    code = ErrorCode.PREVIEW_NOT_FOUND
