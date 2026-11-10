from fastapi import APIRouter

from app.api.schemas import TelemetryBatch

router = APIRouter()

@router.get(
    "/health/live",
    tags=["Operations"],
    summary="Перевірити, чи працює API",
    )
async def live() -> dict[str, str]:
    """Перевіряє, що API може відповідати на запити."""
    return {"status": "alive"}

@router.post(
    "/telemetry",
    response_model=TelemetryBatch,
    tags=["Telemetry"],
    summary="Перевірити структуру пакета вимірювань",
)
async def ingest(batch: TelemetryBatch) -> TelemetryBatch:
    """
    Приймає пакет вимірювань і повертає перевірені дані.

    На цьому етапі дані не записуються в базу.
    Перевірки якості та ML-аналіз ще не виконуються.
    """
    return batch