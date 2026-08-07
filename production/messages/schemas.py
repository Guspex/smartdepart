"""Message shapes passed between the production's hosts.

Flat fields only (no nested message objects) — JsonSerialize.chunks_from_python()
serializes with a plain json.dumps() over declared fields, which requires every field
to be a JSON-native scalar.
"""
from intersystems_pyprod import Column, JsonSerialize

iris_package_name = "UberRoute"


class TripRequestMessage(JsonSerialize):
    """The validated payload BS_UberRouteService hands to BP_RouteOrchestrator (FR-001)."""

    session_id: str = Column(default="", datatype="%VarString")
    origin: str = Column(default="", datatype="%VarString")
    destination: str = Column(default="", datatype="%VarString")
    target_time: str = Column(default="", datatype="%VarString")


class RouteRecommendationMessage(JsonSerialize):
    """BP_RouteOrchestrator's response, echoed back out through BS_UberRouteService.

    `waiting_place_*` fields are static defaults (`waiting_place_suggested=False`,
    the rest empty) until the Business Rule + BO_HybridRAGEngine populate them
    (tasks.md T032-T034; contracts/bs_uber_route_service.md).
    """

    trip_request_id: int = Column(default=0, datatype="%Integer")
    recommended_time: str = Column(default="", datatype="%VarString")
    estimated_fare: float = Column(default=0.0, datatype="%Numeric")
    delta_minutes: int = Column(default=0, datatype="%Integer")
    waiting_place_suggested: bool = Column(default=False, datatype="%Boolean")
    waiting_place_name: str = Column(default="", datatype="%VarString")
    waiting_place_address: str = Column(default="", datatype="%VarString")
    waiting_place_category: str = Column(default="", datatype="%VarString")
    waiting_place_rating: float = Column(default=0.0, datatype="%Numeric")
    waiting_place_distance_km: float = Column(default=0.0, datatype="%Numeric")
    waiting_place_rationale: str = Column(default="", datatype="%VarString")
    waiting_place_unavailable_reason: str = Column(default="", datatype="%VarString")
    error_code: str = Column(default="", datatype="%VarString")
    error_message: str = Column(default="", datatype="%VarString")


class WaitingPlaceQueryMessage(JsonSerialize):
    """Request BP_RouteOrchestrator sends to BO_HybridRAGEngine when the Business Rule fires."""

    session_id: str = Column(default="", datatype="%VarString")
    origin_lat: float = Column(default=0.0, datatype="%Numeric")
    origin_lng: float = Column(default=0.0, datatype="%Numeric")
    query_text: str = Column(default="", datatype="%VarString")
    radius_km: float = Column(default=1.0, datatype="%Numeric")


class FarePredictionQueryMessage(JsonSerialize):
    """Request BP_RouteOrchestrator sends to BO_IntegratedMLPredictor per candidate time."""

    session_id: str = Column(default="", datatype="%VarString")
    candidate_time: str = Column(default="", datatype="%VarString")
    day_of_week: int = Column(default=1, datatype="%Integer")
    distance_km: float = Column(default=0.0, datatype="%Numeric")


class FarePredictionResultMessage(JsonSerialize):
    """BO_IntegratedMLPredictor's response for one candidate time."""

    candidate_time: str = Column(default="", datatype="%VarString")
    predicted_fare: float = Column(default=0.0, datatype="%Numeric")
    ok: bool = Column(default=False, datatype="%Boolean")
    error_message: str = Column(default="", datatype="%VarString")


class WaitingPlaceResultMessage(JsonSerialize):
    """BO_HybridRAGEngine's response — the single best-ranked waiting place, if any."""

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
