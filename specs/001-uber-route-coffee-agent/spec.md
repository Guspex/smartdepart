# Feature Specification: Uber Route & Coffee Recommendation Agent

**Feature Branch**: `001-uber-route-coffee-agent`

**Created**: 2026-08-07

**Status**: Draft

**Input**: User description: "SPECS-001: Uber Route & Coffee Recommendation Agent (InterSystems IRIS + PyProd + RAG + IntegratedML) — Precisamos criar uma aplicação inteligente de otimização de viagens da Uber e recomendações locais de apoio. O sistema recebe origem, destino e horário desejado; analisa o melhor momento/tarifa para a viagem e, se houver diferença superior a 30 minutos em relação ao horário sugerido, indica um café próximo para aguardar."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get the best time and fare for a trip (Priority: P1)

A rider who wants to go from an origin to a destination around a desired time asks the
system for the best way to make that trip. The system tells them the recommended departure
time and the expected fare for that trip, so they can decide when to actually request the
ride.

**Why this priority**: This is the core value of the feature — without a time/fare
recommendation there is nothing else to build on. It must work standalone before any waiting
suggestion logic is added.

**Independent Test**: Can be fully tested by submitting an origin, destination, and desired
time, and verifying the response contains a recommended departure time and an estimated
fare — with no dependency on the waiting-place suggestion feature.

**Acceptance Scenarios**:

1. **Given** a valid origin, destination, and desired time, **When** the rider submits the
   request, **Then** the system returns a recommended departure time and an estimated fare
   for the trip.
2. **Given** the recommended departure time is the same as the requested time (within 30
   minutes), **When** the rider views the response, **Then** no waiting-place suggestion is
   included.
3. **Given** an origin or destination that cannot be recognized, **When** the rider submits
   the request, **Then** the system tells the rider the location could not be understood
   instead of returning a recommendation.

---

### User Story 2 - Get a nearby place to wait when the best time is far off (Priority: P2)

When the recommended departure time is more than 30 minutes away from what the rider asked
for (earlier or later), the rider wants somewhere comfortable and nearby to spend the wait —
so the system suggests a nearby café or similar spot instead of leaving the rider stranded.

**Why this priority**: This is the feature's differentiator, but it only has meaning once
User Story 1's recommendation exists — it is a conditional enhancement, not a standalone
flow.

**Independent Test**: Can be fully tested by submitting a request engineered to produce a
recommended time more than 30 minutes from the requested time, and verifying the response
includes at least one nearby waiting-place suggestion with enough detail to act on.

**Acceptance Scenarios**:

1. **Given** the recommended departure time is more than 30 minutes later than requested,
   **When** the rider views the response, **Then** the system includes at least one nearby
   place suggestion where the rider can wait.
2. **Given** the recommended departure time is more than 30 minutes earlier than requested,
   **When** the rider views the response, **Then** the system still includes a nearby
   waiting-place suggestion (the 30-minute rule applies regardless of direction).
3. **Given** a waiting-place suggestion is returned, **When** the rider views it, **Then** it
   includes the place's name, its location, and enough descriptive detail (e.g., what kind of
   place it is, general reputation) to help the rider decide whether to go there.
4. **Given** no suitable nearby place can be found, **When** the rider views the response,
   **Then** the system still returns the time/fare recommendation and clearly indicates that
   no waiting place is available, rather than failing the whole request.

---

### User Story 3 - Understand why a waiting place was suggested (Priority: P3)

A rider who receives a waiting-place suggestion wants a brief reason it was picked (e.g., how
close it is, why it's a good fit) so they trust the recommendation enough to act on it.

**Why this priority**: Improves trust and adoption of User Story 2's suggestions but the
feature is usable without it — an unranked or unexplained suggestion still lets the rider
wait somewhere.

**Independent Test**: Can be fully tested by requesting a trip that triggers a waiting-place
suggestion and verifying the suggestion is accompanied by a short rationale distinguishing it
from other candidate places.

**Acceptance Scenarios**:

1. **Given** more than one candidate waiting place exists near the rider, **When** the system
   selects one to suggest, **Then** the response indicates why that place was chosen over
   others (e.g., closer, better rated, more relevant to the rider's description).

---

### Edge Cases

- What happens when the origin and destination are the same place?
- What happens when the desired time is in the past?
- What happens when no fare/timing data is available for the requested route (e.g., unusual
  or unsupported area)?
- What happens when the rider is in an area with no nearby waiting places at all?
- What happens when the origin or destination text is ambiguous (matches multiple real
  places)?
- What happens when external conditions data (e.g., traffic or weather) needed to compute the
  recommendation is temporarily unavailable — does the system degrade gracefully or fail the
  request?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept a trip request consisting of an origin, a destination, and a
  desired time from the rider.
- **FR-002**: System MUST validate that origin, destination, and desired time are all present
  and well-formed before attempting to produce a recommendation, and MUST reject requests
  missing any of these with a clear explanation.
- **FR-003**: System MUST determine a recommended departure time and an estimated fare for
  the requested trip, based on expected conditions around the requested time.
- **FR-004**: System MUST compute the absolute difference between the rider's requested time
  and the recommended departure time.
- **FR-005**: When that absolute difference exceeds 30 minutes, System MUST include at least
  one nearby waiting-place suggestion in the response.
- **FR-006**: When that absolute difference is 30 minutes or less, System MUST NOT include a
  waiting-place suggestion.
- **FR-007**: Each waiting-place suggestion MUST include, at minimum, a name, a location, and
  a short description sufficient for the rider to judge whether to go there.
- **FR-008**: System MUST select waiting-place suggestions using both how well the place
  matches the rider's likely needs (descriptive relevance) and exact/known-fact matches (e.g.,
  proximity, name, category) rather than either alone.
- **FR-009**: System MUST select waiting-place suggestions that are near the rider's origin
  (where the rider is waiting), not the destination.
- **FR-010**: System MUST return the time/fare recommendation even when no suitable
  waiting-place suggestion can be found, and MUST indicate clearly that none is available in
  that case.
- **FR-011**: System MUST inform the rider when the origin or destination cannot be resolved
  to a real location, rather than silently failing or returning an empty recommendation.
- **FR-012**: System MUST record each request and the key decision points of its response
  (recommended time, computed time difference, whether a waiting place was suggested) to
  support monitoring and troubleshooting.

### Key Entities

- **Trip Request**: The rider's ask — origin, destination, desired time, and when the request
  was made.
- **Route Recommendation**: The system's answer to a trip request — recommended departure
  time, estimated fare, and the computed difference from the requested time.
- **Waiting Place**: A candidate location (e.g., café, coworking space) the rider could wait
  at — name, location, category, and descriptive attributes (e.g., rating, ambiance) used to
  judge fit.
- **Waiting-Place Suggestion**: The link between a Route Recommendation and the Waiting
  Place(s) chosen for it, including why it was chosen.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Riders receive a complete time/fare recommendation for a valid trip request
  within 5 seconds.
- **SC-002**: 100% of responses where the recommended time differs from the requested time by
  more than 30 minutes include a waiting-place suggestion.
- **SC-003**: 100% of responses where the recommended time differs from the requested time by
  30 minutes or less contain no waiting-place suggestion (no false positives).
- **SC-004**: 100% of waiting-place suggestions include a name, location, and descriptive
  detail sufficient for the rider to decide whether to go there without further lookup.
- **SC-005**: At least 90% of waiting-place suggestions are within comfortable walking
  distance (approximately 1 km) of the rider's origin.
- **SC-006**: Requests with an unrecognized origin or destination receive a clear explanatory
  response, not a silent failure or empty result, 100% of the time.

## Assumptions

- **Clarified (post-implementation)**: the rider's "desired time" throughout this document
  is their **arrival deadline** — e.g. "I need to be at my meeting by 14:00" — not a
  departure time. The system works backwards from it: it estimates a typical-traffic
  departure time for that arrival, then recommends whichever nearby departure time is
  cheapest; "30 minutes" (FR-005/FR-006) is measured against that typical-traffic baseline,
  not against the raw arrival time itself. `recommended_time` in every response is always a
  departure time; `estimated_arrival_time` is the system's estimate of when that departure
  gets the rider there.
- "Best time/fare" means minimizing the rider's cost (e.g., avoiding surge/peak pricing)
  while staying within a reasonable window of the requested time — not simply the fastest
  possible pickup. This is a recommendation to help the rider decide when to travel, not an
  automatic rebooking of a fixed itinerary.
- This feature recommends a time, fare estimate, and (when relevant) a waiting place — it
  does not place an actual ride request, process payment, or communicate with a live driver
  dispatch system. Those are out of scope for this feature.
- "Nearby" for waiting-place suggestions defaults to roughly a 1 km / comfortable walking
  radius around the rider's origin, absent a rider-specified preference.
- The system may suggest more than one waiting place but highlights a single best match by
  default; showing a ranked list of alternatives is a reasonable enhancement but not required
  for the feature to deliver value.
- The rider is a single, unauthenticated end user for this feature — account creation, rider
  profiles, and trip history are out of scope.
- "30 minutes" is measured as wall-clock minutes between the rider's requested time and the
  system's recommended departure time, regardless of whether the recommended time is earlier
  or later.
- Underlying conditions data (traffic, weather, pricing history) needed to compute the
  recommendation is assumed to be available from existing or third-party sources; this
  specification does not define where that data comes from.
