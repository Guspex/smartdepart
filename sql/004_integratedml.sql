-- IntegratedML fare predictor, per constitution Principle IV and research.md §7.
-- Requires UberRoute.TripHistory to be populated first (see data/trip_history_seed.csv
-- and the Foundational setup step that loads it).

CREATE MODEL UberRoute.FarePredictor PREDICT (FinalPrice)
    FROM UberRoute.TripHistory;

TRAIN MODEL UberRoute.FarePredictor;

-- Example predictive query used by BO_IntegratedMLPredictor (research.md §7):
-- SELECT PREDICT(UberRoute.FarePredictor USING (PickupTime, DayOfWeek, DistanceKm, DemandFactor))
-- AS PredictedPrice
-- FROM (SELECT ? AS PickupTime, ? AS DayOfWeek, ? AS DistanceKm, ? AS DemandFactor)
