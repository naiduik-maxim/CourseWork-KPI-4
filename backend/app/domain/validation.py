"""Правила якості без залежності від БД, HTTP та поточного часу."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from typing import Sequence


@dataclass(frozen=True)
class RulePolicy:
    """Налаштування перевірок одного показника."""

    version: str = "power-v1"
    minimum: float = 0.0
    maximum: float = 20.0
    interval_seconds: int = 60
    tolerance_seconds: int = 5
    frozen_seconds: int = 600
    frozen_epsilon: float = 0.001
    maximum_rate: float = 8.0

    def __post_init__(self) -> None:
        if not self.version or self.minimum > self.maximum:
            raise ValueError("Invalid policy identity or range")

        if(self.interval_seconds <= 0 or not 0 <= self.tolerance_seconds < self.interval_seconds / 2):
            raise ValueError("Invalid time-grid policy")

        if (
            self.frozen_seconds <= 0
            or self.frozen_epsilon < 0
            or self.maximum_rate <= 0
        ):
            raise ValueError("Invalid temporal rule thresholds")

        if not all(
            isfinite(x)
            for x in (
                self.minimum,
                self.maximum,
                self.frozen_epsilon,
                self.maximum_rate,
            )
        ):
            raise ValueError("Policy thresholds must be finite")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    """Одне вимірювання одного показника."""
    timestamp: datetime
    value: float | None


@dataclass(frozen=True)
class Gap:
    """Інтервал пропущених вимірювань."""

    start: datetime
    end: datetime
    count: int


@dataclass(frozen=True)
class RuleResult:
    """Результат перевірок за правилами."""

    flags: tuple[str, ...]
    eligible: bool
    details: dict = field(default_factory=dict)
    gap: Gap | None = None


def is_regular_window(timestamps: Sequence[datetime], policy: RulePolicy = RulePolicy(),) -> bool:
    """
    Перевірити регулярність вікна відносно його першої точки.

    Кожен час має відповідати позиції:
    перша точка + номер позиції * інтервал,
    із дозволеним відхиленням.

    Часові позначки повинні строго зростати.
    """

    if not timestamps:
        return False

    anchor = timestamps[0]

    if anchor.tzinfo is None or anchor.utcoffset() is None:
        return None

    for i, timestamp in enumerate(timestamps):
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return False

        if i and timestamp <= timestamps[i - 1]:
            return False

        offset = (timestamp - anchor).total_seconds()

        if abs(offset - i * policy.interval_seconds) > policy.tolerance_seconds:
            return False


    return True


class ValidationService:
    """Перевірки якості одного показника."""

    def validate(
        self,
        current: Observation,
        history: Sequence[Observation],
        policy: RulePolicy = RulePolicy(),
        *,
        late: bool = False,
    ) -> RuleResult:
        """
        Перевірити поточне вимірювання з урахуванням історії.

        Історія має бути хронологічною, передувати поточній точці
        та містити не більше 30 початкових вимірювань.

        Для запізнілої точки перевіряється лише її власне значення
        та додається позначка late_arrival.

        Обробку дублікатів виконує код, який викликає цей метод.
        """

        if (
            current.timestamp.tzinfo is None
            or current.timestamp.utcoffset() is None
        ):
            raise ValueError("Timezone-aware timestamp required")

        flags: list[str] = []
        details: dict = {}
        gap = None
        value = current.value

        if value is None:
            flags.append("missing")
            details["missing"] = {"reason": "missing_value"}

        elif not isfinite(value):
            raise ValueError(
                "Non-finite measurements must be rejected by input validation"
            )

        elif not policy.minimum <= value <= policy.maximum:
            flags.append("out_of_bounds")
            details["out_of_bounds"] = {
                "minimum": policy.minimum,
                "maximum": policy.maximum,
            }

        if late:
            flags.append("late_arrival")
            details["late_arrival"] = {
                "reason": "historical_checks_not_performed"
            }
            return RuleResult(tuple(flags), False, details)

        if any(point.timestamp >= current.timestamp for point in history):
            raise ValueError("History must precede current")

        if any(
            a.timestamp >= b.timestamp
            for a, b in zip(history, history[1:])
        ):
            raise ValueError("History must be strictly chronological")

        if history:
            previous = history[-1]
            delta_seconds = (
                current.timestamp - previous.timestamp
            ).total_seconds()

            slots = round(delta_seconds / policy.interval_seconds)

            on_grid = (
                slots >= 1
                and abs(
                    delta_seconds - slots * policy.interval_seconds
                ) <= policy.tolerance_seconds
            )

            if on_grid and slots > 1:
                gap = Gap(
                    previous.timestamp +
                    timedelta(seconds=policy.interval_seconds),
                    previous.timestamp +
                    timedelta(seconds=slots * policy.interval_seconds),
                    slots - 1,
                )

                if "missing" not in flags:
                    flags.append("missing")

                details["preceding_gap"] = {
                    "count": gap.count,
                    "start": gap.start.isoformat(),
                    "end": gap.end.isoformat(),
                }

            elif not on_grid:
                flags.append("irregular_timestamp")
                details["irregular_timestamp"] = {
                    "delta_seconds": delta_seconds
                }

            elif (
                value is not None
                and previous.value is not None
                and isfinite(previous.value)
            ):
                rate = abs(value - previous.value) / (delta_seconds / 60)

                if rate > policy.maximum_rate:
                    flags.append("rate_of_change")
                    details["rate_of_change"] = {
                        "rate_kw_per_minute": rate,
                        "limit": policy.maximum_rate,
                    }

            # Використовуємо початкові значення, включно з точками,
            # які раніше отримували позначку frozen.
            window = [*history, current]

            for start in range(len(window) - 1, -1, -1):
                duration = (
                    current.timestamp - window[start].timestamp
                ).total_seconds()

                if duration >= policy.frozen_seconds:
                    candidates = window[start:]
                    values = [point.value for point in candidates]

                    if (
                        is_regular_window(
                            [p.timestamp for p in candidates],
                            policy,
                        )
                        and all(
                            v is not None and isfinite(v)
                            for v in values
                        )
                    ):
                        if (
                            max(values) - min(values)
                            <= policy.frozen_epsilon + 1e-12
                        ):
                            flags.append("frozen")
                            details["frozen"] = {
                                "duration_seconds": duration,
                                "epsilon": policy.frozen_epsilon,
                            }

                    break

        return RuleResult(tuple(flags), not flags, details, gap)