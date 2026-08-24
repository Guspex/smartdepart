# Contract: `BsUberRouteService` (WSGI/REST)

The only external interface this feature exposes: a single synchronous HTTP endpoint backed
by `BsUberRouteService`, served as an IRIS-native WSGI Web Application (research.md §2, §15).

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
| `origin` | string | yes | Free-text location; resolved via geocoding (research.md §9, §18) |
| `destination` | string | yes | Free-text location; resolved via geocoding |
| `target_time` | string | yes | `HH:MM`, 24-hour, local time — **the time the rider needs to arrive** at `destination` (e.g. for an appointment), not a departure time |

The response always returns **three fixed departure options**, anchored to a "naive
departure" time (the arrival deadline minus a typical-traffic travel estimate,
`UberRoute.TrafficWeatherReference`-adjusted for that hour — research.md §20):

| `label` | Meaning |
|---|---|
| `ideal` | Leave at the naive departure time — no extra wait |
| `30min_earlier` | Leave 30 minutes before the naive departure time, and wait somewhere before heading out |
| `60min_earlier` | Leave 60 minutes before the naive departure time, and wait somewhere before heading out |

Each option is independently priced by `FarePredictor` at its own departure time, so the
rider can directly compare "leave now for X" against "leave early, wait, pay Y". The two
earlier-departure options always carry a `waiting_place` suggestion (or an explanation of
why none was found) — waiting is the reason those options exist, so this is unconditional,
not gated by any delta threshold.

### Response — 200 OK

```json
{
  "trip_request_id": 123,
  "options": [
    {
      "label": "ideal",
      "wait_minutes": 0,
      "departure_time": "18:35",
      "arrival_time": "19:05",
      "estimated_fare": 27.90,
      "waiting_place": null,
      "waiting_place_unavailable_reason": null
    },
    {
      "label": "30min_earlier",
      "wait_minutes": 30,
      "departure_time": "18:05",
      "arrival_time": "18:35",
      "estimated_fare": 24.10,
      "waiting_place": {
        "name": "Café Central",
        "address": "Rua Augusta, 500, São Paulo",
        "category": "cafe",
        "rating": 4.6,
        "distance_km": 0.4,
        "rationale": "Closest highly-rated match within walking distance of your origin"
      },
      "waiting_place_unavailable_reason": null
    },
    {
      "label": "60min_earlier",
      "wait_minutes": 60,
      "departure_time": "17:35",
      "arrival_time": "18:05",
      "estimated_fare": 21.00,
      "waiting_place": null,
      "waiting_place_unavailable_reason": "No nearby waiting place found within 1 km of the origin"
    }
  ]
}
```

If a given option's fare prediction itself fails, that option is simply omitted from the
`options` array rather than failing the whole request — the response is only a `503` (below)
if *every* option failed.

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

### Response — 503 Service Unavailable (no fare prediction available for any option)

```json
{
  "error": "prediction_unavailable",
  "message": "Could not compute a fare/time recommendation right now: ..."
}
```

## Contract invariants (tie back to spec Success Criteria)

- `options` always has one entry per label that produced a valid fare prediction, in the
  fixed order `ideal`, `30min_earlier`, `60min_earlier` (SC-002, SC-003).
- `30min_earlier` and `60min_earlier` always include either a `waiting_place` (with `name`,
  `address`, and enough descriptive detail to act on — SC-004; FR-007) or a
  `waiting_place_unavailable_reason`, never neither.
- The full round trip (request in, response out) completes within 5 seconds (SC-001).
