"""GET /healthz.

Answers 200 when every check passes and 503 when any fails, so it can serve as a
readiness probe.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from corpus.api.modules.health.dto import HealthResponse
from corpus.api.modules.health.repository import HealthRepository
from corpus.api.modules.health.service import HealthService
from corpus.config import get_settings

router = APIRouter(tags=["health"])


def get_health_service(request: Request) -> HealthService:
    return HealthService(
        HealthRepository(request.app.state.engine),
        timeout=get_settings().healthz_timeout_seconds,
    )


@router.get(
    "/healthz",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthResponse,
            "description": "At least one dependency is unhealthy.",
        }
    },
)
async def healthz(
    response: Response, service: Annotated[HealthService, Depends(get_health_service)]
) -> HealthResponse:
    result = await service.check()
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
