"""Зареєструвати датчик і незмінну версію правил без повторного створення."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import Settings
from app.domain.validation import RulePolicy
from app.domain.electrical import ElectricalPolicy
from app.repositories.database import create_database
from app.repositories.models import RulePolicyRow, Sensor, SensorState

async def seed(
    sensor_id = "household-001",
    model_dir=None,
    profile="power",
):
    model_dir = (
        Path(model_dir)
        if model_dir is not None
        else Path(
            "models/electrical-iforest-v1"
            if profile == "electrical"
            else "models/power-iforest-v1"
        )
    )

    metadata = json.loads(
        (model_dir / "metadata.json").read_text(encoding="utf-8")
    )

    policy = (
        ElectricalPolicy.from_dict(metadata["electrical_policy"])
        if profile == "electrical"
        else RulePolicy(**metadata["rule_policy"])
    )

    metric = "electrical" if profile == "electrical" else "active_power"

    expected_model = (
        "electrical-iforest-v1"
        if profile == "electrical"
        else "power-iforest-v1"
    )

    if metadata["model_version"] != expected_model:
        raise ValueError("Model/profile mismatch")

    engine, sessions = create_database(Settings())

    try:
        async with sessions() as session, session.begin():
            existing_policy = await session.get(
                RulePolicyRow,
                policy.version,
            )

            if existing_policy is None:
                session.add(
                    RulePolicyRow(
                        version=policy.version,
                        configuration=policy.to_dict(),
                    )
                )
                await session.flush()

            elif existing_policy.configuration != policy.to_dict():
                raise ValueError("An immutable policy version already has different parameters")

            sensor = await session.get(Sensor, sensor_id)

            if sensor is None:
                session.add(
                    Sensor(
                        id=sensor_id,
                        metric=metric,
                        unit="kW,V,A" if profile == "electrical" else "kW",
                        policy_version=policy.version,
                        model_version=metadata["model_version"],
                    )
                )

                await session.flush()

                session.add(
                    SensorState(
                        sensor_id=sensor_id,
                        version=0,
                    )
                )

            elif (
                sensor.metric != metric
                or sensor.policy_version != policy.version
                or sensor.model_version != metadata["model_version"]
            ):
                raise ValueError("Existing sensor has a different model/policy")

    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--sensor-id",
        default="household-001",
    )

    parser.add_argument(
        "--model-dir",
        type=Path,
    )

    parser.add_argument(
        "--profile",
        choices=("power", "electrical"),
        default="power",
    )

    args = parser.parse_args()

    asyncio.run(
        seed(
            args.sensor_id,
            args.model_dir,
            args.profile,
        )
    )

    print(f"Registered {args.sensor_id}")


if __name__ == "__main__":
    main()
