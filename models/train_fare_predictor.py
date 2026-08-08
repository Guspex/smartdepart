"""Trains FarePredictor outside IRIS and exports PMML for IntegratedML's %ML.PMML.Provider
to import (research.md §16: TRAIN MODEL cannot succeed on this Community Edition image
because no AutoML provider is installed; PMML import needs no in-database training step).

Feature columns are all-numeric (PickupMinutes, DayOfWeek, DistanceKm, DemandFactor) rather
than matching UberRoute.TripHistory's storage types (PickupTime is VARCHAR(5) there) --
PMML has no portable way to express "parse an HH:MM string" as a transform nyoka can export,
so the model is declared with an explicit feature-column clause (CREATE MODEL ... WITH (...))
instead of inferring columns FROM TripHistory, and PickupTime is converted to
minutes-since-midnight before both training (here) and prediction
(production/hosts/bo_integratedml_predictor.py's _minutes_since_midnight).
"""
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from nyoka import skl_to_pmml

df = pd.read_csv("data/trip_history_seed.csv")
hours, minutes = df["PickupTime"].str.split(":", expand=True).astype(int).values.T
df["PickupMinutes"] = hours * 60 + minutes

feature_cols = ["PickupMinutes", "DayOfWeek", "DistanceKm", "DemandFactor"]
label_col = "FinalPrice"

pipeline = Pipeline([("regressor", LinearRegression())])
pipeline.fit(df[feature_cols], df[label_col])

skl_to_pmml(pipeline, feature_cols, label_col, "models/fare_predictor.pmml")
print("Wrote models/fare_predictor.pmml")
print("Train R^2:", pipeline.score(df[feature_cols], df[label_col]))
