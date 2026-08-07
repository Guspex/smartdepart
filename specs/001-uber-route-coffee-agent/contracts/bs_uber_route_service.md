# Contract: `BS_UberRouteService` (WSGI/REST)

The only external interface this feature exposes: a single synchronous HTTP endpoint backed
by `BS_UberRouteService`, served as an IRIS-native WSGI Web Application (research.md §2).

## `POST /api/uber-route/recommend`

### Request

```json
{
  "origin": "Av. Paulista, 1000, São Paulo",
  "destination": "Aeroporto de Congonhas, São Paulo",
  "target_time": "18:30"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `origin` | string | yes | Free-text location; resolved via geocoding (research.md §9) |
| `destination` | string | yes | Free-text location; resolved via geocoding |
| `target_time` | string | yes | `HH:MM`, 24-hour, local time |

### Response — 200 OK, delta ≤ 30 minutes (User Story 1)

```json
{
  "trip_request_id": 123,
  "recommended_time": "18:35",
  "estimated_fare": 27.90,
  "delta_minutes": 5,
  "waiting_place_suggested": false,
  "waiting_place": null
}
```

### Response — 200 OK, delta > 30 minutes (User Story 2 + 3)

```json
{
  "trip_request_id": 124,
  "recommended_time": "19:20",
  "estimated_fare": 19.50,
  "delta_minutes": 50,
  "waiting_place_suggested": true,
  "waiting_place": {
    "name": "Café Central",
    "address": "Rua Augusta, 500, São Paulo",
    "category": "cafe",
    "rating": 4.6,
    "distance_km": 0.4,
    "rationale": "Closest highly-rated match within walking distance of your origin"
  }
}
```

### Response — 200 OK, delta > 30 minutes but no waiting place available (spec edge case, FR-010)

```json
{
  "trip_request_id": 125,
  "recommended_time": "19:20",
  "estimated_fare": 19.50,
  "delta_minutes": 50,
  "waiting_place_suggested": true,
  "waiting_place": null,
  "waiting_place_unavailable_reason": "No nearby waiting place found within 1 km of the origin"
}
```

### Response — 400 Bad Request (FR-002: missing/malformed fields)

```json
{
  "error": "invalid_request",
  "message": "target_time must be HH:MM"
}
```

### Response — 422 Unprocessable Entity (FR-011: origin/destination cannot be resolved)

```json
{
  "error": "location_not_found",
  "message": "Could not resolve 'destination' to a known location"
}
```

## Contract invariants (tie back to spec Success Criteria)

- `waiting_place_suggested` is `true` if and only if `delta_minutes > 30` (SC-002, SC-003) —
  computed by the Business Rule in `BP_RouteOrchestrator` (research.md §8), never by the
  client.
- When `waiting_place_suggested` is `true` and a place was found, `waiting_place` always
  includes `name`, `address`, and enough descriptive detail to act on (SC-004; FR-007).
- The full round trip (request in, response out) completes within 5 seconds (SC-001).
