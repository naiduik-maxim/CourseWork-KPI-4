"""Спільні налаштовувані правила для трьох електричних показників."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from app.domain.validation import (
    Observation,
    RulePolicy,
    RuleResult,
    ValidationService,
)

POWER_CONSISTENCY_TOLERANCE = 0.05

@dataclass(frozen=True)
class ElectricalObservation:
    """Вимірювання потужності, напруги та струму."""

    timestamp: datetime
    value: float | None
    voltage: float | None
    current: float | None


@dataclass(frozen=True)
class ElectricalPolicy:
    """Окремі правила для кожного електричного показника."""

    power: RulePolicy = field(
        default_factory=lambda: RulePolicy(
            version="electrical-v1",
        )
    )

    voltage: RulePolicy = field(
        default_factory=lambda: RulePolicy(
            version="electrical-v1",
            minimum=0,
            maximum=300,
            maximum_rate=30,
            frozen_epsilon=0.01,
        )
    )

    current: RulePolicy = field(
        default_factory=lambda: RulePolicy(
            version="electrical-v1",
            minimum=0,
            maximum=100,
            maximum_rate=40,
            frozen_epsilon=0.01,
        )
    )

    @property
    def version(self):
        return self.power.version

    def to_dict(self):
        return {
            name: getattr(self, name).to_dict()
            for name in ("power", "voltage", "current")
        }

    @classmethod
    def from_dict(cls, data):
        policy = cls(
            **{
                name: RulePolicy(**data[name])
                for name in ("power", "voltage", "current")
            }
        )

        if len(
            {
                policy.power.version,
                policy.voltage.version,
                policy.current.version,  
            }
        ) != 1:
            raise ValueError("Electrical policy versions must agree")

        if len(
            {
                (p.interval_seconds, p.tolerance_seconds)
                for p in (
                    policy.power,
                    policy.voltage,
                    policy.current,
                )
            }
        ) != 1:
            raise ValueError("Electrical measurement time grids must agree")

        return policy


class ElectricalValidationServive:
    """Перевірити всі три показники та об'єднати результати."""

    def validate(
            self,
            current: ElectricalObservation,
            history: Sequence[ElectricalObservation],
            policy: ElectricalPolicy | None = None,
            *,
            late=False,
    ) -> RuleResult:
        policy = policy or ElectricalPolicy()
        validator = ValidationService()

        flags, metrics = [], {}
        gap = None

        for name, attribute, unit in (
            ("power", "value", "kW"),
            ("voltage", "voltage", "V"),
            ("current", "current", "A"),
        ):
            result = validator.validate(
                Observation(
                    current.timestamp,
                    getattr(current, attribute),
                ),
                [
                    Observation(
                        p.timestamp,
                        getattr(p, attribute),
                    ) for p in history
                ],
                getattr(policy, name),
                late=late,
            )

            details = dict(result.details)

            if "rate_of_change" in details:
                rate = dict(details["rate_of_change"])
                rate["rate_per_minute"] = rate.pop("rate_kw_per_minute")
                rate["unit"] = unit + "/min"
                details["rate_of_change"] = rate

            metrics[name] = {
                "unit": unit,
                "flags": list(result.flags),
                "details": details,
            }

            flags.extend(result.flags)
            gap = gap or result.gap

        details = {"metrics": metrics}

        power_kw = current.value
        voltage_v = current.voltage
        current_a = current.current

        if (
            power_kw is not None
            and voltage_v is not None
            and current_a is not None
            and "out_of_bounds" not in flags
        ):
            active_power_w = abs(power_kw) * 1000
            apparent_power_va = voltage_v * current_a
            limit_w = apparent_power_va * (1 + POWER_CONSISTENCY_TOLERANCE)
            exceeds_limit = active_power_w > limit_w

            details["power_consistency"] = {
                "rule_version": "single-phase-v1",
                "active_power_w": active_power_w,
                "apparent_power_va": apparent_power_va,
                "tolerance_fraction": POWER_CONSISTENCY_TOLERANCE,
                "limit_w": limit_w,
                "passed": not exceeds_limit,
            }

            if exceeds_limit:
                flags.append("inconsistent_power")

        if gap is not None:
            details["preceding_gap"] = {
                "count": gap.count,
                "start": gap.start.isoformat(),
                "end": gap.end.isoformat(),
            }

        flags = tuple(dict.fromkeys(flags))

        return RuleResult(flags, not flags, details, gap)
