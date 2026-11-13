"""Response models for /healthz."""

from typing import Literal

from pydantic import BaseModel

Status = Literal["ok", "error"]


class CheckResult(BaseModel):
    status: Status
    latency_ms: float
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Status
    version: str
    checks: dict[str, CheckResult]
