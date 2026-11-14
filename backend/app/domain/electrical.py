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