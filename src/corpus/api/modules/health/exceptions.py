"""Errors raised by health checks."""


class CheckFailed(Exception):
    """A check failed for a reason whose message is safe to return to callers."""
    pass
