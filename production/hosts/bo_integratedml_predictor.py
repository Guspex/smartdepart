"""BoIntegratedMlPredictor — predicts the fare for a candidate departure time using the
`FarePredictor` IntegratedML model (constitution Principle IV; research.md §7).

Queries IntegratedML through Embedded Python/SQL against the IRIS connection already in
use — no separate model-serving stack, per the constitution. If `FarePredictor` has not
been trained yet, IRIS raises SQLCODE -191 ("no default trained model"); this host
surfaces that as `ok=False` with an explanatory message rather than inventing a parallel,
non-IntegratedML fallback formula.

Class name is underscore-free PascalCase (`BoIntegratedMlPredictor`) — IRIS 2026.1 Build
234U was found to silently truncate brand-new class names at the first underscore during
compilation (research.md §12); this naming avoids that bug entirely.
"""
from __future__ import annotations

import os
import sys

import iris
from intersystems_pyprod import BusinessOperation

# See research.md §14: pyprod's generated OnInit only adds this file's own
# directory to sys.path, not the project root needed for `from production.X.Y
# import ...`. Add it explicitly so every deferred import below resolves.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Imported at module level, before any message can arrive — see research.md
# §14 (deferred imports left intersystems_pyprod's message registry empty,
# breaking incoming-message conversion).
from production.messages.schemas import FarePredictionResultMessage  # noqa: E402
from production.observability.telemetry import timed_event  # noqa: E402

iris_package_name = "UberRoute"


class BoIntegratedMlPredictor(BusinessOperation):
    def on_message(self, request):
        session_id = getattr(request, "session_id", "")

        with timed_event(
            "BoIntegratedMlPredictor",
            "integratedml_call",
            session_id=session_id,
            candidate_time=request.candidate_time,
        ) as ev:
            try:
                fare = self._predict_fare(
                    request.candidate_time, request.day_of_week, request.distance_km
                )
            except Exception as exc:  # noqa: BLE001 — surfaced to the caller, not swallowed
                ev.outcome = "error"
                response = FarePredictionResultMessage(
                    candidate_time=request.candidate_time,
                    predicted_fare=0.0,
                    ok=False,
                    error_message=str(exc),
                )
                return self.OKStatus(), response

        response = FarePredictionResultMessage(
            candidate_time=request.candidate_time,
            predicted_fare=fare,
            ok=True,
        )
        return self.OKStatus(), response

    @staticmethod
    def _predict_fare(candidate_time: str, day_of_week: int, distance_km: float) -> float:
        """Run the IntegratedML predictive query (research.md §7).

        Demand factor is not known ahead of time for a hypothetical candidate slot, so a
        neutral demand_factor=1.0 is passed — FarePredictor was trained on historical
        demand_factor alongside time/day/distance, so this still lets the model's
        time-of-day and day-of-week coefficients drive the fare estimate.
        """
        sql = (
            "SELECT PREDICT(FarePredictor USING "
            "(PickupTime, DayOfWeek, DistanceKm, DemandFactor)) AS PredictedPrice "
            "FROM (SELECT ? AS PickupTime, ? AS DayOfWeek, ? AS DistanceKm, ? AS DemandFactor)"
        )
        stmt = iris.sql.prepare(sql)
        rs = stmt.execute(candidate_time, day_of_week, distance_km, 1.0)
        for row in rs:
            return float(row[0])
        raise RuntimeError("FarePredictor returned no prediction row")
