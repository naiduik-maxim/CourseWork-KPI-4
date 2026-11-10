from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)

from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Спільний базовий клас усіх ORM-моделей."""
    pass


class RulePolicyRow(Base):
    """Версія правил перевірки та їхня конфігурація."""

    __tablename__ = "rule_policies"
    version: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    configuration: Mapped[dict] = mapped_column(JSONB)


class Sensor(Base):
    """Зареєстрований датчик і налаштування його обробки."""

    __tablename__ = "sensors"

    id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )

    metric: Mapped[str] = mapped_column(
        String(64),
        default="active_power",
    )

    unit: Mapped[str] = mapped_column(
        String(16),
        default="kW",
    )

    policy_version: Mapped[str] = mapped_column(
        ForeignKey("rule_policies.version"),
    )

    model_version: Mapped[str] = mapped_column(String(128))


class SensorState(Base):
    """Поточний стан послідовної обробки даних датчика."""

    __tablename__ = "sensor_state"

    sensor_id: Mapped[str] = mapped_column(
        ForeignKey("sensors.id"),
        primary_key=True,
    )

    last_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    version: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )


class Telemetry(Base):
    """Вимірювання та збережені результати перевірки якості."""

    __tablename__ = "telemetry"

    sensor_id: Mapped[str] = mapped_column(
        ForeignKey("sensors.id"),
        primary_key=True,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    value: Mapped[float | None] = mapped_column(Float)
    voltage: Mapped[float | None] = mapped_column(Float)
    current: Mapped[float | None] = mapped_column(Float)

    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    rule_results: Mapped[dict] = mapped_column(JSONB)

    eligible: Mapped[bool] = mapped_column(Boolean)
    sequence_accepted: Mapped[bool] = mapped_column(Boolean)

    ml_status: Mapped[str] = mapped_column(String(32))
    evaluation_status: Mapped[str] = mapped_column(String(16))

    rule_policy_version: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(128))
    feature_version: Mapped[str | None] = mapped_column(String(64))

    anomaly_score: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    ml_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "ix_telemetry_flags",
            "quality_flags",
            postgresql_using="gin",
        ),
        Index(
            "ix_telemetry_time_sensor",
            "timestamp",
            "sensor_id",
        ),
    )


class QualityEvent(Base):
    """Подія якості даних, зокрема інтервал пропущених вимірювань."""

    __tablename__ = "quality_events"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    sensor_id: Mapped[str] = mapped_column(
        ForeignKey("sensors.id"),
    )

    event_type: Mapped[str] = mapped_column(String(32))

    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    missing_count: Mapped[int] = mapped_column(Integer)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_quality_events_sensor_start",
            "sensor_id",
            "start",
        ),
    )

