"""Message shapes passed between the production's hosts.

Flat fields only (no nested message objects) — JsonSerialize.chunks_from_python()
serializes with a plain json.dumps() over declared fields, which requires every field
to be a JSON-native scalar.
"""
from intersystems_pyprod import Column, JsonSerialize

iris_package_name = "UberRoute"


class TripRequestMessage(JsonSerialize):
    """The validated payload BsUberRouteService hands to BpRouteOrchestrator (FR-001)."""

    session_id: str = Column(default="", datatype="%VarString")
    origin: str = Column(default="", datatype="%VarString")
    destination: str = Column(default="", datatype="%VarString")
    target_time: str = Column(default="", datatype="%VarString")


class RouteRecommendationMessage(JsonSerialize):
    """BpRouteOrchestrator's response, echoed back out through BsUberRouteService.

    `options_json` holds a JSON-serialized list of up to 3 departure options ("ideal" /
    "30min_earlier" / "60min_earlier"), each with its own fare estimate and — for the two
    earlier options — a waiting-place suggestion (research.md §20). It is itself a single
    %VarString field, not a nested message, because JsonSerialize.chunks_from_python()
    requires every declared field to be a JSON-native scalar (see module docstring) — a
    JSON *string* satisfies that; a nested list/object field would not.
    """

    trip_request_id: int = Column(default=0, datatype="%Integer")
    options_json: str = Column(default="[]", datatype="%VarString")
    error_code: str = Column(default="", datatype="%VarString")
    error_message: str = Column(default="", datatype="%VarString")


class WaitingPlaceQueryMessage(JsonSerialize):
    """Request BpRouteOrchestrator sends to BoHybridRagEngine when the Business Rule fires."""

    session_id: str = Column(default="", datatype="%VarString")
    origin_lat: float = Column(default=0.0, datatype="%Numeric")
    origin_lng: float = Column(default=0.0, datatype="%Numeric")
    query_text: str = Column(default="", datatype="%VarString")
    radius_km: float = Column(default=1.0, datatype="%Numeric")


class FarePredictionQueryMessage(JsonSerialize):
    """Request BpRouteOrchestrator sends to BoIntegratedMlPredictor per candidate time."""

    session_id: str = Column(default="", datatype="%VarString")
    candidate_time: str = Column(default="", datatype="%VarString")
    day_of_week: int = Column(default=1, datatype="%Integer")
    distance_km: float = Column(default=0.0, datatype="%Numeric")


class FarePredictionResultMessage(JsonSerialize):
    """BoIntegratedMlPredictor's response for one candidate time."""

    candidate_time: str = Column(default="", datatype="%VarString")
    predicted_fare: float = Column(default=0.0, datatype="%Numeric")
    ok: bool = Column(default=False, datatype="%Boolean")
    error_message: str = Column(default="", datatype="%VarString")


class WaitingPlaceResultMessage(JsonSerialize):
    """BoHybridRagEngine's response — the single best-ranked waiting place, if any."""

    found: bool = Column(default=False, datatype="%Boolean")
    name: str = Column(default="", datatype="%VarString")
    address: str = Column(default="", datatype="%VarString")
    category: str = Column(default="", datatype="%VarString")
    rating: float = Column(default=0.0, datatype="%Numeric")
    distance_km: float = Column(default=0.0, datatype="%Numeric")
    rationale: str = Column(default="", datatype="%VarString")
    vector_score: float = Column(default=0.0, datatype="%Numeric")
    keyword_score: float = Column(default=0.0, datatype="%Numeric")
    unavailable_reason: str = Column(default="", datatype="%VarString")
