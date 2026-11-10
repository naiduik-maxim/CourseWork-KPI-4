from datetime import datetime, timezone
from math import isfinite
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

QualityFlag = Literal[
    "valid",
    "out_of_bounds",
    "missing",
    "frozen",
    "rate_of_change",
    "ml_anomaly",
    "late_arrival",
    "irregular_timestamp",
    "insufficient_history",
    "analysis_error",
]

class PointInput(BaseModel):
    """Одне вимірювання енергетичної телеметрії."""

    model_config = ConfigDict(extra="forbid")

    timestamp : AwareDatetime = Field(
        description="Час вимірювання у форматі ISO 8601 з часовим поясом.",
    )

    value : float | None = Field(
        description="Активна потужність у кВт. null — значення відсутнє.",
    )

    voltage : float | None = Field(
        default=None,
        description=(
            "Напруга у В. Ключ обов'язковий для електричного профілю. "
            "null — значення відсутнє."
        ),
    )

    current : float | None = Field(
        default=None,
        description=(
            "Струм в А. Ключ обов'язковий для електричного профілю. "
            "null — значення відсутнє."
        ),
    )


    @field_validator("timestamp", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        """Перевірити формат часу до обробки Pydantic."""
        if not isinstance(value, (str, datetime)):
            raise ValueError("timestamp must be ISO 8601 with a timezone")

        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError(
                    "timestamp must be ISO 8601 with a timezone"
                ) from None

            if parsed.tzinfo is None:
                raise ValueError("timestamp must include a timezone")


        return value


    @field_validator("timestamp", mode="after")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        """Привести перевірений час до UTC."""
        return value.astimezone(timezone.utc)


    @field_validator("value", "voltage", "current", mode="before")
    @classmethod
    def strict_number(cls, value):
        """Дозволити лише скінченне число або null."""
        if value is None:
            return None

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value must be a JSON number or null")

        try:
            finite = isfinite(value)
        except OverflowError:
            finite = False

        if not finite:
            raise ValueError("value must be finite") 

        return value


class TelemetryBatch(BaseModel):
    """Пакет від 1 до 100 вимірювань одного датчика."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "sensor_id": "household-electrical-001",
                    "points": [
                        {
                            "timestamp": "2026-11-10T10:00:00Z",
                            "value": 2.41,
                            "voltage": 230.5,
                            "current": 10.6,
                        }
                    ],
                },
                {
                    "sensor_id": "household-001",
                    "points": [
                        {
                            "timestamp": "2026-11-10T10:00:00Z",
                            "value": 2.41,
                        }
                    ],
                },
            ]
        },
    )

    sensor_id : str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Ідентифікатор датчика.",
    )

    points: list[PointInput] = Field(
        min_length=1,
        max_length=100,
        description="Вимірювання від одного датчика.",
    )


class TelemetryOutput(BaseModel):
    """Вимірювання разом із результатами перевірки якості."""

    model_config = ConfigDict(from_attributes=True)

    sensor_id: str
    timestamp: datetime
    received_at: datetime

    value: float | None
    voltage: float | None = None
    current: float | None = None

    quality_flags: list[QualityFlag]
    rule_results: dict

    ml_status: str
    evaluation_status: str

    rule_policy_version: str
    model_version: str | None
    feature_version: str | None

    anomaly_score: float | None
    threshold: float | None
    ml_reason: str | None


class TelemetryPage(BaseModel):
    """Сторінка вимірювань із курсором для наступної сторінки."""

    items: list[TelemetryOutput]
    next_cursor: str | None


class BatchOutput(BaseModel):
    """Результати обробки прийнятого пакета вимірювань."""

    items: list[TelemetryOutput]


class EventOutput(BaseModel):
    """Подія якості даних, наприклад пропуск вимірювань."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    sensor_id: str
    event_type: str

    start: datetime
    end: datetime
    missing_count: int

    detected_at: datetime