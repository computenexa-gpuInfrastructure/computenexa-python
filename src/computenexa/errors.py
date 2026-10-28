"""Errors raised by the ComputeNexa client.

Every refusal from the API carries a stable `code` (e.g. "insufficient_credits"), a `message` that is safe to show
people, and a `request_id` to quote to support@computenexa.com. Catch `ComputeNexaError` for all of them, or a
subclass for one kind.
"""

from __future__ import annotations


class ComputeNexaError(Exception):
    """Base class: any error from the client or the API."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 request_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.request_id = request_id

    def __str__(self) -> str:
        extra = ", ".join(f"{k}={v}" for k, v in (("status", self.status), ("code", self.code),
                                                    ("request_id", self.request_id)) if v is not None)
        return f"{self.message} ({extra})" if extra else self.message


class AuthenticationError(ComputeNexaError):
    """401: the API key is missing, mistyped or revoked, or its creator left the team."""


class PermissionDeniedError(ComputeNexaError):
    """403: the key can't do this (a look-only key, or a dashboard-only setting), or the account is suspended."""


class InsufficientCreditsError(ComputeNexaError):
    """402: not enough credits for this launch or extension. Add credits in the dashboard."""


class NotFoundError(ComputeNexaError):
    """404: no such machine, project or key, or not one this key can see."""


class ConflictError(ComputeNexaError):
    """409: the request clashes with the current state (see the subclasses)."""


class NoCapacityError(ConflictError):
    """409 no_capacity: that GPU is fully booked right now. Retry later or pick another GPU."""


class GpuLimitReachedError(ConflictError):
    """409 gpu_limit_reached: your team (or you) already run as many GPUs as allowed."""


class ProjectInUseError(ConflictError):
    """409 project_in_use: another GPU is using this project."""


class InvalidRequestError(ComputeNexaError):
    """422: a field is missing or wrong; the message says which."""


class RateLimitError(ComputeNexaError):
    """429: more than 120 requests a minute with this key. The client retries these by itself first."""


class ServerError(ComputeNexaError):
    """5xx: something went wrong on our side. The client retries these by itself first."""


class APIConnectionError(ComputeNexaError):
    """The API couldn't be reached (network, DNS, timeout)."""


class LaunchFailedError(ComputeNexaError):
    """The machine couldn't start (no capacity after all, timeout…). Its cost was refunded in full."""


class WaitTimeoutError(ComputeNexaError):
    """`wait_until_running` gave up before the machine was ready. The machine may still start: check or stop it."""


_BY_CODE = {
    "no_capacity": NoCapacityError,
    "gpu_limit_reached": GpuLimitReachedError,
    "project_in_use": ProjectInUseError,
    "insufficient_credits": InsufficientCreditsError,
}
_BY_STATUS = {401: AuthenticationError, 402: InsufficientCreditsError, 403: PermissionDeniedError,
              404: NotFoundError, 409: ConflictError, 422: InvalidRequestError, 429: RateLimitError}


def from_response(status: int, body: object) -> ComputeNexaError:
    """The error for an API refusal: by its code first, else by its HTTP status."""
    data = body if isinstance(body, dict) else {}
    code = data.get("error") if isinstance(data.get("error"), str) else None
    message = data.get("message") or data.get("detail") or f"HTTP {status}"
    if not isinstance(message, str):
        message = str(message)
    cls = _BY_CODE.get(code or "") or _BY_STATUS.get(status) or (ServerError if status >= 500 else ComputeNexaError)
    return cls(message, status=status, code=code, request_id=data.get("request_id"))
