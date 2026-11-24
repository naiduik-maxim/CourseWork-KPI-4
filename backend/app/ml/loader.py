from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path
import platform

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from app.contracts.ml import (
    ML_CONTRACT_VERSION,
    Detector,
    MLRequest,
    MLResult,
    ModelArtifactError,
    ModelInferenceError,
)

from app.domain.validation import RulePolicy
from app.ml.features import FEATURE_NAMES, FEATURE_VERSION, extract_features
from app.domain.electrical import ElectricalPolicy
from app.ml.electrical_features import (
    ELECTRICAL_FEATURE_NAMES,
    ELECTRICAL_FEATURE_VERSION,
    ELECTRICAL_UNITS,
    extract_electrical_features,
)


class IsolationForestDetector:
    def __init__(self, model: IsolationForest, metadata: dict, policy: RulePolicy):
        self.model = model
        self.metadata = metadata
        self.policy = policy
        self.model_version = metadata["model_version"]
        self.feature_version = metadata["feature_version"]
        self.policy_version = metadata["policy_version"]
        self.threshold = float(metadata["threshold"])

    def predict(self, request: MLRequest) -> MLResult:
        try:
            extractor = (
                extract_electrical_features
                if self.feature_version == ELECTRICAL_FEATURE_VERSION
                else extract_features
            )

            features, reason = extractor(request, self.policy)

            if features is None:
                return MLResult(
                    "skipped_history",
                    None,
                    None,
                    None,
                    self.model_version,
                    self.feature_version,
                    reason,
                )

            score = float(-self.model.score_samples(features.reshape(1, -1))[0])

            if not isfinite(score):
                raise ModelInferenceError("non_finite_score")

            return MLResult(
                "completed",
                score,
                self.threshold,
                score > self.threshold,
                self.model_version,
                self.feature_version,
                None,
            )

        except ModelInferenceError:
            raise
        except Exception as exc:
            raise ModelInferenceError("inference_failed") from exc


def load_detector(artifact_dir: Path) -> Detector:
    """Joblib використовує pickle. Довіра до джерела потрібна навіть за збігу хешу."""
    try:
        directory = Path(artifact_dir)
        metadata = json.loads(
           (directory / "metadata.json").read_text(encoding="utf-8")
        )

        if metadata["contract_version"] != ML_CONTRACT_VERSION:
           raise ValueError("contract_version_mismatch")

        electrical = metadata["feature_version"] == ELECTRICAL_FEATURE_VERSION
        names = ELECTRICAL_FEATURE_NAMES if electrical else FEATURE_NAMES

        if (metadata["feature_version"] not in (FEATURE_VERSION, ELECTRICAL_FEATURE_VERSION)
           or metadata["feature_names"] != list(names)):
           raise ValueError("feature_schema_mismatch")

        if (
            metadata["threshold_direction"] != "score_gt_threshold"
            or not isfinite(float(metadata["threshold"])) 
        ):
           raise ValueError("invalid_threshold")

        versions = metadata["versions"]

        if versions["sklearn"] != sklearn.__version__ or versions["numpy"] != np.__version__:
            raise ValueError("library_version_mismatch")

        if versions["python"].split(".")[:2] != platform.python_version().split(".")[:2]:
            raise ValueError("python_version_mismatch")

        policy = (
            ElectricalPolicy.from_dict(metadata["electrical_policy"])
            if electrical
            else RulePolicy(**metadata["rule_policy"])
        )

        if policy.version != metadata["policy_version"]:
            raise ValueError("policy_version_mismatch")

        if electrical and (
            metadata.get("units") != ELECTRICAL_UNITS
            or metadata["rule_policy"] != policy.power.to_dict()
        ):
            raise ValueError("electrical_units_or_policy_mismatch")

        model_path = directory / "model.joblib"

        with model_path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()

        if checksum != metadata["model_sha256"]:
            raise ValueError("model_checksum_mismatch")

        model = joblib.load(model_path)

        if not isinstance(model, IsolationForest) or model.n_features_in_ != len(names):
            raise ValueError("invalid_model_type_or_features")

        if not metadata["model_version"]:
            raise ValueError("model_version_required")

        return IsolationForestDetector(model, metadata, policy)

    except Exception as exc:
        raise ModelArtifactError(
            f"Cannot load trusted artifact {artifact_dir}: {exc}"
        ) from exc
