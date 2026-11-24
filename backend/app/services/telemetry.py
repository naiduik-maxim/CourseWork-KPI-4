import asyncio
import logging
from concurrent.futures import Executor
from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4

from app.api.schemas import TelemetryBatch
from app.contracts.ml import MLPoint, MLRequest, ModelInferenceError
from app.domain.validation import Observation, RulePolicy, ValidationService
from app.domain.electrical import (
    ElectricalObservation,
    ElectricalPolicy,
    ElectricalValidationServive,
)
from app.repositories.models import QualityEvent, RulePolicyRow, Sensor, Telemetry
from app.repositories.telemetry import TelemetryRepository

class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


class QualityFlagService:
    @staticmethod
    def combine(rule_flags, ml_status, anomaly=False):
        flags = list(rule_flags)

        if ml_status == "completed" and anomaly:
            flags.append("ml_anomaly")
        elif ml_status == "skipped_history":
            flags.append("insufficient_history")
        elif ml_status == "failed":
            flags.append("analysis_error")

        if not flags and ml_status == "completed": 
            flags.append("valid")

        return list(dict.fromkeys(flags))


class AnomalyDetectionService:
    def __init__(self, detector, executor: Executor, limiter: asyncio.Semaphore):
        self.detector, self.executor, self.limiter = detector, executor, limiter

    async def predict(self, request: MLRequest):
        async with self.limiter:
            result = await asyncio.get_running_loop().run_in_executor(
                self.executor,
                self.detector.predict,
                request,
            )

        if result.status == "completed":
            if not all(
                isinstance(x, (float, int)) and isfinite(x)
                for x in (result.anomaly_score, result.threshold)
            ):
                raise ModelInferenceError("Non-finite inference result")

            if (
                not isinstance(result.is_anomaly, bool)
                or result.is_anomaly != (result.anomaly_score > result.threshold)
            ):
                raise ModelInferenceError("Inconsistent inference decision")

        elif result.status != "skipped_history":
            raise ModelInferenceError("Unknown inference status")

        return result


class TelemetryService:
    def __init__(self, sessions, anomaly_service, model_metadata: dict, registry=None):
        self.sessions = sessions
        self.anomaly_service = anomaly_service
        self.model_metadata = model_metadata
        self.validation = ValidationService()
        self.registry = (
            registry
            if registry is not None
            else {
                model_metadata.get("model_version"): (
                    anomaly_service, model_metadata
                )
            }
        )

    async def ingest(self, batch: TelemetryBatch, request_id: str):
        unique = {}

        for point in batch.points:
            if (
                point.timestamp in unique
                and unique[point.timestamp].model_dump() != point.model_dump()
            ):
                raise ServiceError(409, "Conflicting duplicate inside batch")
            unique[point.timestamp] = point

        async with self.sessions() as session:
            async with session.begin():
                repository = TelemetryRepository(session)
                state = await repository.lock_state(batch.sensor_id)

                if state is None:
                    raise ServiceError(404, "Unknown sensor")

                sensor = await session.get(Sensor, batch.sensor_id)

                electrical = sensor.metric == "electrical"

                for point in batch.points:
                    provided = {"voltage", "current"} & point.model_fields_set

                    if electrical and provided != {"voltage", "current"}:
                        raise ServiceError(
                            422,
                            "Electrical sensors require voltage and current keys; "
                            "use null for missing measurements",
                        )

                    if not electrical and provided:
                        raise ServiceError(
                            422,
                            "Power-only sensor does not accept voltage/current; "
                            "register an electrical sensor",
                        )

                policy_row = await session.get(RulePolicyRow, sensor.policy_version)
                policy = (
                    ElectricalPolicy.from_dict(policy_row.configuration)
                    if electrical
                    else RulePolicy(**policy_row.configuration)
                )

                entry = self.registry.get(sensor.model_version)

                if entry is None:
                    raise ServiceError(503, "Sensor model is not loaded")

                anomaly_service, metadata = entry
                if (
                    metadata.get("policy_version") != sensor.policy_version
                    or metadata.get("model_version") != sensor.model_version
                ):
                    raise ServiceError(
                        503,
                        "Sensor policy/model does not match loaded artifact",
                    )

                existing = await repository.existing(batch.sensor_id, list(unique))

                for timestamp, row in existing.items():
                    if any(
                        getattr(row, field) != getattr(unique[timestamp], field)
                        for field in ("value", "voltage", "current")
                    ):
                        raise ServiceError(409, "Timestamp already has a different value")


                history = await repository.history(batch.sensor_id)
                results = dict(existing)

                for timestamp, point in sorted(unique.items()):
                    if timestamp in existing:
                        continue

                    late = (
                        state.last_timestamp is not None
                        and timestamp < state.last_timestamp
                    )

                    if electrical:
                        rule = ElectricalValidationServive().validate(
                            ElectricalObservation(
                                timestamp, point.value, point.voltage, point.current),
                            [] if late else [
                                ElectricalObservation(
                                    h.timestamp, h.value, h.voltage, h.current
                                )
                                for h in history
                            ],
                            policy,
                            late=late,
                        )
                    else:
                        rule = self.validation.validate(
                            Observation(timestamp, point.value),
                            [] if late else [
                                Observation(h.timestamp, h.value) for h in history
                            ],
                            policy,
                            late=late,
                        )

                    score = threshold = model_version = feature_version = reason = None

                    anomaly = False
                    evaluation = "complete"

                    if late: 
                        ml_status, evaluation = "skipped_late", "partial"
                    elif not rule.eligible:
                        ml_status = "skipped_rules"
                    else:
                        request = MLRequest(
                            batch.sensor_id,
                            MLPoint(
                                timestamp, point.value, True, point.voltage, point.current
                            ),
                            tuple(
                                MLPoint(
                                    h.timestamp, h.value, h.eligible, h.voltage, h.current
                                )
                                for h in history
                            ),
                            policy.version,
                        )

                        try:
                            prediction = await anomaly_service.predict(request)
                            ml_status = prediction.status
                            score, threshold = prediction.anomaly_score, prediction.threshold

                            model_version, feature_version = prediction.model_version, prediction.feature_version 

                            reason, anomaly = prediction.reason, prediction.is_anomaly

                            if ml_status == "skipped_history":
                                evaluation = "partial"

                        except Exception as error:
                            logging.getLogger("telemetry").error(
                                "inference_failed",
                                extra={
                                    "request_id": request_id,
                                    "sensor_id": batch.sensor_id,
                                    "error_type": type(error).__name__,
                                },
                            )
                            ml_status, evaluation, reason = (
                                "failed", "failed", "inference_failed"
                            )

                    details = dict(rule.details)

                    if rule.gap is not None:
                        event_id = str(uuid4())
                        session.add(
                            QualityEvent(
                                id=event_id,
                                sensor_id=batch.sensor_id,
                                event_type="missing",
                                start=rule.gap.start,
                                end=rule.gap.end,
                                missing_count=rule.gap.count,
                            )
                        )
                        details["preceding_gap"]["event_id"] = event_id

                    row = Telemetry(
                        sensor_id=batch.sensor_id,
                        timestamp=timestamp,
                        received_at=datetime.now(timezone.utc),
                        value=point.value,
                        voltage=point.voltage,
                        current=point.current,
                        quality_flags=QualityFlagService.combine(
                            rule.flags, ml_status, anomaly
                        ),
                        rule_results=details,
                        eligible=rule.eligible,
                        sequence_accepted=not late,
                        ml_status=ml_status,
                        evaluation_status=evaluation,
                        rule_policy_version=policy.version,
                        model_version=model_version,
                        feature_version=feature_version,
                        anomaly_score=score,
                        threshold=threshold,
                        ml_reason=reason,
                    )

                    session.add(row)
                    results[timestamp] = row

                    if not late:
                        history = [*history, row][-30:]
                        state.last_timestamp = timestamp
                        state.version += 1

                await session.flush()

            return [results[point.timestamp] for point in batch.points]