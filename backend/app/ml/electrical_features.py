from dataclasses import replace
from math import isfinite

import numpy as np

from app.contracts.ml import MLRequest, ModelInferenceError
from app.domain.electrical import ElectricalPolicy
from app.ml.features import extract_features


ELECTRICAL_FEATURE_VERSION = "electrical-v1"
ELECTRICAL_UNITS = {"value": "kW", "voltage": "V", "current": "A"}
ELECTRICAL_FEATURE_NAMES = tuple(
    f"{metric}_{stat}"
    for metric in ("power", "voltage", "current")
    for stat in (
        "value",
        "delta",
        "mean_5m",
        "std_5m",
        "mean_30m",
        "std_30m",
        "deviation_30m",
    )
)

def extract_electrical_features(
    request: MLRequest, policy: ElectricalPolicy | None = None  
):
    policy = policy or ElectricalPolicy()

    if any(
        getattr(request.current, name) is None
        or not isfinite(getattr(request.current, name))
        for name in ("value", "voltage", "current")
    ):
        raise ModelInferenceError("current_electrical_measurements_required")

    vectors = []

    for metric, attribute in (
        ("power", "value"),
        ("voltage", "voltage"),
        ("current", "current"),
    ):
        projected = replace(
            request,
            current=replace(
                request.current, value=getattr(request.current, attribute)
            ),
            history=tuple(
                replace(point, value=getattr(point, attribute))
                for point in request.history
            ),
        )

        vector, reason = extract_features(projected, getattr(policy, metric))

        if reason is not None:
            return None, reason

        vectors.append(vector)

    return np.concatenate(vectors), None