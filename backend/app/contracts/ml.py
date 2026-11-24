"""Версійований контракт між backend і модулем прогнозування."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

ML_CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class MLPoint:
    timestamp: datetime
    value: float | None
    eligible: bool
    voltage: float | None = None
    current: float | None = None


@dataclass(frozen=True)
class MLRequest:
    sensor_id: str
    current: MLPoint
    history: tuple[MLPoint, ...]
    policy_version: str


@dataclass(frozen=True)
class MLResult:
    status: Literal["completed", "skipped_history"]
    anomaly_score: float | None
    threshold: float | None
    is_anomaly: bool | None
    model_version: str
    feature_version: str
    reason: str | None


class Detector(Protocol):
    def predict(self, request: MLRequest) -> MLResult: ...


class ModelArtifactError(Exception):
    """Не вдалося завантажити або перевірити довірений артефакт моделі."""


class ModelInferenceError(Exception):
    """Помилка прогнозування або порушення контракту його вхідних даних."""