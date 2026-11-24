import logging
import pytest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, tzinfo

from app.api.schemas import TelemetryBatch
from app.domain.validation import RulePolicy
from app.domain.validation import Observation, ValidationService
from app.contracts.ml import MLPoint, MLRequest
from app.repositories.models import RulePolicyRow, Sensor, SensorState
from app.repositories.telemetry import TelemetryRepository
from app.services import telemetry
from app.main import app
from app.ml.loader import load_detector
from scripts.backend import seed as sensor_seed



def test_rejects_date_outside_utc(monkeypatch):
    monkeypatch.setattr(
        app.state,
        "logger",
        logging.getLogger("qa"),
        raising=False,
    )

    payload = {
        "sensor_id": "test-power",
        "points": [
            {
                "timestamp": "0001-01-01T00:00:00+01:00",
                "value": 1.0,
            }
        ],
    }

    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.post("/telemetry", json=payload)
    finally:
        client.close()

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_batch_load_history_once(monkeypatch):
    policy = RulePolicy()
    sensor = Sensor(
        id="test-power",
        metric="active_power",
        policy_version=policy.version,
        model_version="power-iforest-v1",
    )
    policy_row = RulePolicyRow(
        version=policy.version,
        configuration=policy.to_dict(),
    )

    session = MagicMock()
    session.get = AsyncMock(
        side_effect=lambda model, key: {
            Sensor: sensor,
            RulePolicyRow: policy_row,
        }[model]
    )

    session.flush = AsyncMock()

    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value = session

    repository = SimpleNamespace(
        lock_state = AsyncMock(
            return_value=SensorState(sensor_id = sensor.id, version=0)
        ),
        existing=AsyncMock(return_value={}),
        history=AsyncMock(return_value=[]),
    )

    monkeypatch.setattr(
        telemetry,
        "TelemetryRepository",
        lambda session: repository,
    )

    detector = SimpleNamespace(
        predict=AsyncMock(
            return_value=SimpleNamespace(
                status="skipped_history",
                anomaly_score=None,
                threshold=None,
                model_version=sensor.model_version,
                feature_version="power-v1",
                reason="not_enough_points",
                is_anomaly=None,
            )
        )
    )
    service = telemetry.TelemetryService(
        sessions,
        detector,
        {
            "model_version": sensor.model_version,
            "policy_version": policy.version,
        },
    )

    packet = TelemetryBatch.model_validate(
        {
            "sensor_id": sensor.id,
            "points": [
                {"timestamp": "2026-01-01T10:00:00Z", "value": 1.0},
                {"timestamp": "2026-01-01T10:01:00Z", "value": 2.0},
            ],
        }
    )

    rows = await service.ingest(packet, "bug")

    assert len(rows) == 2
    assert (repository.history.await_count, session.add.call_count) == (1, 2)


def test_timestamp_without_utc():
    class UndefinedOffset(tzinfo):
        def utcoffset(self, dt):
            return None

    timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=UndefinedOffset())
    current = Observation(timestamp, 1.0)

    with pytest.raises(ValueError, match="Timezone-aware timestamp required"):
        ValidationService().validate(current, [])


@pytest.mark.asyncio
async def test_seed_recovers__missing_sensor_state(monkeypatch):
    policy = RulePolicy()
    sensor = Sensor(
        id="test-power",
        metric="active_power",
        policy_version=policy.version,
        model_version="power-iforest-v1",
    )
    policy_row = RulePolicyRow(
        version=policy.version,
        configuration=policy.to_dict(),
    )

    session = MagicMock()
    session.get = AsyncMock(
        side_effect=lambda model, key: {
            Sensor: sensor,
            SensorState: None,
            RulePolicyRow: policy_row,
        }[model]
    )
    session.flush = AsyncMock()

    engine = SimpleNamespace(dispose=AsyncMock())
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value = session

    monkeypatch.setattr(
        sensor_seed,
        "create_database",
        lambda settings: (engine, sessions),
    )

    await sensor_seed.seed(sensor_id=sensor.id, profile="power")

    insert_states = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], SensorState)
    ]

    assert any(
        row.sensor_id == sensor.id and row.version == 0
        for row in insert_states
    ), "Повторна реєстрація має відновити початковий стан датчика"


@pytest.mark.parametrize(
    "model_name",
    ["power-iforest-v1", "electrical-iforest-v1"],
)
def test_ml_runs_reference_prediction(model_name):
    directory = Path("models") / model_name
    fixture = json.loads(
        (directory / "reference_example.json").read_text(encoding="utf-8")
    )
    expected = fixture["expected_result"]
    detector = load_detector(directory)

    def to_point(row):
        return MLPoint(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            value=row["value"],
            eligible=row["eligible"],
            voltage=row.get("voltage"),
            current=row.get("current"),
        )

    request = MLRequest(
        sensor_id=fixture["sensor_id"],
        current=to_point(fixture["current"]),
        history=tuple(to_point(row) for row in fixture["history"]),
        policy_version=fixture["policy_version"],
    )

    result = detector.predict(request)

    assert result.status == expected["status"] == "completed"
    assert result.is_anomaly == expected["is_anomaly"]
    assert result.anomaly_score == pytest.approx(
        expected["anomaly_score"],
        abs=fixture["absolute_tolerance"],
        rel=0,
    )


def test_bug_01_repository_exposes_lock_state():
    repository = TelemetryRepository(session=None)

    method = getattr(repository, "lock_state", None)

    assert callable(method), "TelemetryRepository має надавати метод lock_state"