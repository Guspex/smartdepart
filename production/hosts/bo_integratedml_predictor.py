"""BO_IntegratedMLPredictor — predicts the fare for a candidate departure time using the
`FarePredictor` IntegratedML model (constitution Principle IV; research.md §7).

Queries IntegratedML through Embedded Python/SQL against the IRIS connection already in
use — no separate model-serving stack, per the constitution. If `FarePredictor` has not
been trained yet, IRIS raises SQLCODE -191 ("no default trained model"); this host
surfaces that as `ok=False` with an explanatory message rather than inventing a parallel,
non-IntegratedML fallback formula.
"""
from __future__ import annotations

import iris
from intersystems_pyprod import BusinessOperation

iris_package_name = "UberRoute"


class BO_IntegratedMLPredictor(BusinessOperation):
    def on_message(self, request):
        from production.messages.schemas import FarePredictionResultMessage
        from production.observability.telemetry import timed_event

        session_id = getattr(request, "session_id", "")

        with timed_event(
            "BO_IntegratedMLPredictor",
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
