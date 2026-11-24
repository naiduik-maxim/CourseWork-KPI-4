from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.models import (
    QualityEvent,
    RulePolicyRow,
    Sensor,
    SensorState,
    Telemetry,
)

class TelemetryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_state(self, sensor_id: str) -> SensorState | None:
        return await self.session.scalar(
            select(SensorState)
            .where(SensorState.sensor_id == sensor_id)
            .with_for_update()
        )

    async def existing(self, sensor_id: str, timestamps: list[datetime], ) -> dict[datetime, Telemetry]:
        rows = await self.session.scalars(
            select(Telemetry).where(
                Telemetry.sensor_id == sensor_id,
                Telemetry.timestamp.in_(timestamps),
            )
        )

        return {row.timestamp: row for row in rows}

    async def history(self, sensor_id: str) -> list[Telemetry]:
        rows = list(
            await self.session.scalars(
                select(Telemetry)
                .where(
                    Telemetry.sensor_id == sensor_id,
                    Telemetry.sequence_accepted.is_(True),
                )
                .order_by(Telemetry.timestamp.desc())
                .limit(30)
            )
        )
        return list(reversed(rows))

    async def query(self, sensor_id, start, end, flag, cursor, limit):
        statement = select(Telemetry)

        if sensor_id is not None:
            statement = statement.where(Telemetry.sensor_id == sensor_id)

        if start is not None:
            statement  = statement.where(Telemetry.timestamp >= start)

        if end is not None:
            statement = statement.where(Telemetry.timestamp < end)

        if flag is not None:
            statement = statement.where(
                Telemetry.quality_flags.contains([flag])
            )

        if cursor is not None:
            statement = statement.where(
                tuple_(Telemetry.timestamp, Telemetry.sensor_id)
                > tuple_(cursor[0], cursor[1])
            )

        return list(
            await self.session.scalars(
                statement
                .order_by(Telemetry.timestamp, Telemetry.sensor_id)
                .limit(limit + 1)
            )
        )


    async def events(self, sensor_id, start, end, limit):
        statement = select(QualityEvent)

        if sensor_id is not None:
            statement = statement.where(QualityEvent.sensor_id == sensor_id)

        if start is not None:
            statement = statement.where(QualityEvent.end > start)

        if end is not None:
            statement = statement.where(QualityEvent.start < end)

        return list(
            await self.session.scalars(
                statement
                .order_by(QualityEvent.start, QualityEvent.id)
                .limit(limit)
            )
        )

    async def get_sensor_status_data(self, sensor_id: str):
        statement = (
            select(
                Sensor.metric,
                RulePolicyRow.configuration,
                SensorState.sensor_id.label("state_sensor_id"),
                SensorState.last_timestamp,
            )
            .select_from(Sensor)
            .join(RulePolicyRow, RulePolicyRow.version == Sensor.policy_version)
            .outerjoin(SensorState, SensorState.sensor_id == Sensor.id)
            .where(Sensor.id == sensor_id)
        )

        result = await self.session.execute(statement)
        return result.one_or_none()