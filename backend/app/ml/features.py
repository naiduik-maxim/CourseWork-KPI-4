from __future__ import annotations

from math import isfinite

import numpy as np
from app.contracts.ml import MLRequest, ModelInferenceError
from app.domain.validation import RulePolicy, is_regular_window

FEATURE_VERSION = "power-v1"
FEATURE_NAMES = (
    "power_kw",
    "delta_kw",
    "mean_5m",
    "std_5m",
    "mean_30m",
    "std_30m",
    "deviation_30m",
)


def extract_features(request: MLRequest, policy: RulePolicy = RulePolicy()) -> tuple[np.ndarray | None, str | None]:
    if request.policy_version != policy.version:
        raise ModelInferenceError("policy_version_mismatch")

    current = request.current
    if not current.eligible or current.value is None or not isfinite(current.value):
        raise ModelInferenceError("current_point_not_eligible")

    points = (*request.history, current)

    if any(p.timestamp.tzinfo is None or p.timestamp.utcoffset() is None for p in points):
        raise ModelInferenceError("timezone_required")

    if any(a.timestamp >= b.timestamp for a, b in zip(points, points[1:])):
        raise ModelInferenceError("history_must_precede_current_in_order")

    if len(request.history) > 30:
        raise ModelInferenceError("history_exceeds_contract_limit")

    if len(request.history) < 30:
        return None, "not_enough_points"

    if not is_regular_window([p.timestamp for p in points], policy):
        return None, "history_gap"

    if any(
        not p.eligible or p.value is None or not isfinite(p.value)
        for p in request.history
    ):
        return None, "ineligible_history"

    values = np.asarray([p.value for p in request.history], dtype=np.float64)
    mean30 = float(values.mean())

    features = np.asarray(
        [current.value,
         current.value - values[-1],
         values[-5:].mean(),
        values[-5:].std(ddof=0),
        mean30,
        values.std(ddof=0),
        current.value - mean30,
        ],
        dtype=np.float64,
    )

    if not np.isfinite(features).all():
        raise ModelInferenceError("non_finite_features")

    return features, None