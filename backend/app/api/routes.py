import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime
from sqlalchemy import select, text
from starlette.responses import JSONResponse

from app.api.schemas import BatchOutput, EventOutput, QualityFlag, TelemetryBatch, TelemetryOutput, TelemetryPage, SensorStatusOutput
from app.repositories.models import Sensor
from app.repositories.telemetry import TelemetryRepository
from app.domain.electrical import ElectricalPolicy
from app.domain.validation import RulePolicy

router = APIRouter()


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([row.timestamp.isoformat(), row.sensor_id]).encode()).decode()


def decode_cursor(cursor):
    if cursor is None:
        return None
    try:
        if len(cursor) > 1024:
            raise ValueError
        values = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(values, list) or len(values) != 2 or not all(isinstance(x, str) for x in values):
            raise ValueError
        timestamp = datetime.fromisoformat(values[0])
        if timestamp.tzinfo is None or not 0 < len(values[1]) <= 128:
            raise ValueError
        return timestamp, values[1]
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid pagination cursor") from None


@router.post("/telemetry", response_model=BatchOutput, tags=["Telemetry"])
async def ingest(batch: TelemetryBatch, request: Request):
    """Persist every well-formed measurement, including quality failures.

    Send at least 30 preceding, rule-passing minute samples before expecting ML.
    A 200 confirms persistence; inspect evaluation_status for analysis outcomes.
    """
    if request.app.state.service is None:
        raise HTTPException(503, "Model is not ready")
    rows = await request.app.state.service.ingest(batch, request.state.request_id)
    return BatchOutput(items=[TelemetryOutput.model_validate(row) for row in rows])


@router.get("/telemetry", response_model=TelemetryPage, tags=["Telemetry"])
async def get_telemetry(
    request: Request,
    sensor_id: str | None = Query(None, max_length=128),
    start: AwareDatetime | None = Query(None, alias="from"),
    end: AwareDatetime | None = Query(None, alias="to"),
    quality_flag: QualityFlag | None = None,
    cursor: str | None = Query(None, max_length=1024),
    limit: int = Query(100, ge=1, le=1000),
):
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "from must be earlier than to")
    position = decode_cursor(cursor)
    async with request.app.state.sessions() as session:
        rows = await TelemetryRepository(session).query(sensor_id, start, end, quality_flag, position, limit)
    page = rows[:limit]
    return TelemetryPage(items=[TelemetryOutput.model_validate(row) for row in page], next_cursor=encode_cursor(page[-1]) if len(rows) > limit else None)


@router.get("/quality-events", response_model=list[EventOutput], tags=["Telemetry"])
async def get_events(request: Request, sensor_id: str | None = Query(None, max_length=128), start: AwareDatetime | None = Query(None, alias="from"), end: AwareDatetime | None = Query(None, alias="to"), limit: int = Query(100, ge=1, le=1000)):
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "from must be earlier than to")
    async with request.app.state.sessions() as session:
        rows = await TelemetryRepository(session).events(sensor_id, start, end, limit)
    return [EventOutput.model_validate(row) for row in rows]


@router.get("/health/live", tags=["Operations"])
async def live():
    return {"status": "alive"}


@router.get("/health", tags=["Operations"])
async def health(request: Request):
    database = False
    compatible = True
    try:
        async with request.app.state.sessions() as session:
            await session.execute(text("SELECT 1"))
            sensors = list(await session.scalars(select(Sensor)))
            database = True
            registry = request.app.state.model_registry
            compatible = all(s.model_version in registry and s.policy_version == registry[s.model_version].get("policy_version") for s in sensors)
    except Exception as error:
        request.app.state.logger.warning("health_database_unavailable", extra={"request_id": request.state.request_id, "error_type": type(error).__name__})
    model = request.app.state.service is not None
    ready = database and model and compatible
    return JSONResponse({"status": "ready" if ready else "not_ready", "database": database, "model": model, "compatible": compatible, "model_version": request.app.state.model_metadata.get("model_version"), "loaded_models": sorted(request.app.state.model_registry)}, status_code=200 if ready else 503)


@router.get(
    "/sensors/{sensor_id}/status",
    response_model=SensorStatusOutput,
    tags=["Sensors"],
    summary="Перевірити своєчасність вимірювань датчика",
)
async def get_sensor_status(sensor_id: str, request: Request):
    async with request.app.state.sessions() as session:
        repository = TelemetryRepository(session)
        row = await repository.get_sensor_status_data(sensor_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Unknown sensor")

    if row.state_sensor_id is None:
        raise HTTPException(status_code=503, detail="Sensor state is missing")

    if row.metric == "electrical":
        policy = ElectricalPolicy.from_dict(row.configuration).power
    else:
        policy = RulePolicy(**row.configuration)

    last = row.last_timestamp
    next_expected = None
    status = "awaiting_first_measurement"

    if last is not None:
        next_expected = last + timedelta(seconds=policy.interval_seconds)
        deadline = next_expected + timedelta(seconds=policy.tolerance_seconds)
        now = datetime.now(timezone.utc)
        status = "overdue" if now > deadline else "on_time"

    return SensorStatusOutput(
        sensor_id=sensor_id,
        status=status,
        last_measurement_at=last,
        next_expected_at=next_expected,
        interval_seconds=policy.interval_seconds,
        tolerance_seconds=policy.tolerance_seconds,
    )