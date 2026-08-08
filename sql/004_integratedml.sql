-- IntegratedML fare predictor, per constitution Principle IV and research.md §7, §16.
--
-- TRAIN MODEL (AutoML) cannot run on this Community Edition image -- no AutoML provider
-- is installed (research.md §14: SQLCODE -186 "AutoML provider not available", confirmed
-- as a genuine platform limitation). Instead, FarePredictor is trained outside IRIS
-- (models/train_fare_predictor.py, a plain scikit-learn LinearRegression) and imported via
-- %ML.PMML.Provider, which needs no training step -- see research.md §16.
--
-- CREATE MODEL's model-name must be unqualified (no "UberRoute." schema prefix) -- a
-- schema-qualified name here is a parser error ("PREDICTING expected, . found"), unlike
-- table names elsewhere in this project.
--
-- Because PMML has no portable way to parse an "HH:MM" string field, the model uses an
-- explicit feature-column clause (numeric PickupMinutes) instead of inferring columns FROM
-- UberRoute.TripHistory (whose PickupTime column is VARCHAR(5)). Predictions must be called
-- with PickupMinutes = minutes-since-midnight, not the "HH:MM" string --
-- production/hosts/bo_integratedml_predictor.py converts this before calling PREDICT().

CREATE MODEL FarePredictor PREDICTING (FinalPrice)
    WITH (PickupMinutes INTEGER, DayOfWeek INTEGER, DistanceKm DOUBLE, DemandFactor DOUBLE);

-- A helper view supplies PickupMinutes for the (formality-only, PMML ignores it for actual
-- fitting) training-data FROM clause -- TRAIN MODEL's FROM subquery parser rejects inline
-- CAST/SUBSTRING expressions directly (research.md §16), so precompute via a view instead.
CREATE VIEW UberRoute.TripHistoryForTraining AS
    SELECT (CAST(SUBSTRING(PickupTime,1,2) AS INTEGER)*60
            + CAST(SUBSTRING(PickupTime,4,2) AS INTEGER)) AS PickupMinutes,
           DayOfWeek, DistanceKm, DemandFactor, FinalPrice
    FROM UberRoute.TripHistory;

-- Selects the PMML provider for this session/connection -- without this, TRAIN MODEL falls
-- back to the (unavailable) %AutoML default and fails with "%ML Provider 'AutoML' is not
-- available on this instance", even with a USING {"file_name": ...} clause present.
SET ML CONFIGURATION %PMML;

-- file_name is a path inside the IRIS container/server filesystem -- copy
-- models/fare_predictor.pmml there first (see deploy/UberRouteSetup.cls or research.md §16
-- for the exact steps used in this session).
TRAIN MODEL FarePredictor
    FROM UberRoute.TripHistoryForTraining
    USING {"file_name": "/tmp/uberroute_app/models/fare_predictor.pmml"};

-- Example predictive query used by BoIntegratedMlPredictor (research.md §7, §16). No USING
-- clause: PREDICT(model USING (...)) is not valid IntegratedML syntax -- PREDICT(model)
-- matches feature columns by name against the FROM row context instead.
-- SELECT PREDICT(FarePredictor) AS PredictedPrice
-- FROM (SELECT ? AS PickupMinutes, ? AS DayOfWeek, ? AS DistanceKm, ? AS DemandFactor)
