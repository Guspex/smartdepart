# Feature Specification: Uber Route & Coffee Recommendation Agent

**Feature Branch**: `001-uber-route-coffee-agent`

**Created**: 2026-08-07

**Status**: Draft

**Input**: User description: "SPECS-001: Uber Route & Coffee Recommendation Agent (InterSystems IRIS + PyProd + RAG + IntegratedML) — Precisamos criar uma aplicação inteligente de otimização de viagens da Uber e recomendações locais de apoio. O sistema recebe origem, destino e horário desejado; analisa o melhor momento/tarifa para a viagem e, se houver diferença superior a 30 minutos em relação ao horário sugerido, indica um café próximo para aguardar."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get the best time and fare for a trip (Priority: P1)

A rider who wants to go from an origin to a destination around a desired time asks the
system for the best way to make that trip. The system tells them the fare for leaving right
away, so they can decide when to actually request the ride.

**Why this priority**: This is the core value of the feature — without a time/fare
recommendation there is nothing else to build on. It must work standalone before any waiting
suggestion logic is added.

**Independent Test**: Can be fully tested by submitting an origin, destination, and desired
time, and verifying the response contains a departure time and an estimated fare for the
"ideal" (no-wait) option — with no dependency on the waiting-place options.

**Acceptance Scenarios**:

1. **Given** a valid origin, destination, and desired time, **When** the rider submits the
   request, **Then** the system returns a departure time and an estimated fare for the
   "ideal" (no-wait) option.
2. **Given** an origin or destination that cannot be recognized, **When** the rider submits
   the request, **Then** the system tells the rider the location could not be understood
   instead of returning a recommendation.

---

### User Story 2 - Get a nearby place to wait if leaving earlier saves money (Priority: P2)

The rider wants to see, alongside the "leave now" option, what it would cost to leave 30 or
60 minutes earlier instead — and where they could wait nearby in the meantime — so they can
weigh a lower fare against the extra wait and pick whichever trade-off suits them.

**Why this priority**: This is the feature's differentiator, but it only has meaning once
User Story 1's "ideal" recommendation exists — it augments that recommendation with two
comparable alternatives, not a standalone flow.

**Independent Test**: Can be fully tested by submitting a valid trip request and verifying
the response includes a "30 minutes earlier" and a "60 minutes earlier" option, each with its
own fare and a nearby waiting-place suggestion with enough detail to act on.

**Acceptance Scenarios**:

1. **Given** a valid trip request, **When** the rider views the response, **Then** the system
   includes, alongside the "ideal" (no-wait) option, a "30 minutes earlier" and a "60 minutes
   earlier" option, each with its own departure time and estimated fare.
2. **Given** the "30 minutes earlier" or "60 minutes earlier" option, **When** the rider views
   it, **Then** the system includes at least one nearby place suggestion where the rider can
   wait for that option.
3. **Given** a waiting-place suggestion is returned, **When** the rider views it, **Then** it
   includes the place's name, its location, and enough descriptive detail (e.g., what kind of
   place it is, general reputation) to help the rider decide whether to go there.
4. **Given** no suitable nearby place can be found for the "30 minutes earlier" or "60 minutes
   earlier" option, **When** the rider views the response, **Then** the system still returns
   that option's time/fare and clearly indicates that no waiting place is available, rather
   than omitting the option or failing the whole request.

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
- **FR-003**: System MUST determine, for the requested trip, three departure options: leaving
  at the "ideal" (no extra wait) time, leaving 30 minutes earlier, and leaving 60 minutes
  earlier — each with its own departure time and estimated fare, based on expected conditions
  around that option's departure time.
- **FR-004**: *(superseded — see FR-005)* System previously computed a single "best" departure
  time and a delta from the requested time; this was replaced by the three fixed options in
  FR-003 so the rider can compare trade-offs directly rather than receive one auto-picked
  time (amended post-implementation; see Assumptions).
- **FR-005**: For the "30 minutes earlier" and "60 minutes earlier" options, System MUST
  include at least one nearby waiting-place suggestion in the response.
- **FR-006**: For the "ideal" (no-wait) option, System MUST NOT include a waiting-place
  suggestion.
- **FR-007**: Each waiting-place suggestion MUST include, at minimum, a name, a location, and
  a short description sufficient for the rider to judge whether to go there.
- **FR-008**: System MUST select waiting-place suggestions using both how well the place
  matches the rider's likely needs (descriptive relevance) and exact/known-fact matches (e.g.,
  proximity, name, category) rather than either alone.
- **FR-009**: System MUST select waiting-place suggestions that are near the rider's origin
  (where the rider is waiting), not the destination.
- **FR-010**: System MUST return every option's time/fare even when no suitable waiting-place
  suggestion can be found for it, and MUST indicate clearly that none is available for that
  option, rather than omitting the option.
- **FR-011**: System MUST inform the rider when the origin or destination cannot be resolved
  to a real location, rather than silently failing or returning an empty recommendation.
- **FR-012**: System MUST record each request and the key decision points of its response
  (each option's departure time and fare, and whether a waiting place was found for it) to
  support monitoring and troubleshooting.

### Key Entities

- **Trip Request**: The rider's ask — origin, destination, desired time, and when the request
  was made.
- **Route Recommendation**: The system's answer to a trip request — up to three departure
  options ("ideal", "30 minutes earlier", "60 minutes earlier"), each with its own departure
  time and estimated fare.
- **Waiting Place**: A candidate location (e.g., café, coworking space) the rider could wait
  at — name, location, category, and descriptive attributes (e.g., rating, ambiance) used to
  judge fit.
- **Waiting-Place Suggestion**: The link between a Route Recommendation and the Waiting
  Place(s) chosen for it, including why it was chosen.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Riders receive a complete time/fare recommendation for a valid trip request
  within 5 seconds.
- **SC-002**: 100% of responses' "30 minutes earlier" and "60 minutes earlier" options
  include a waiting-place suggestion (or a clear explanation why none was found).
- **SC-003**: 100% of responses' "ideal" (no-wait) option contains no waiting-place
  suggestion (no false positives).
- **SC-004**: 100% of waiting-place suggestions include a name, location, and descriptive
  detail sufficient for the rider to decide whether to go there without further lookup.
- **SC-005**: At least 90% of waiting-place suggestions are within comfortable walking
  distance (approximately 1 km) of the rider's origin.
- **SC-006**: Requests with an unrecognized origin or destination receive a clear explanatory
  response, not a silent failure or empty result, 100% of the time.

## Assumptions

- **Amended (post-implementation, research.md §20)**: the original design auto-picked a
  single "cheapest nearby" departure time and conditionally attached one waiting-place
  suggestion only when that time was more than 30 minutes from the rider's request. Live user
  feedback showed this hid the trade-off the rider actually wants to see and compare, so it
  was replaced with three fixed, always-returned options — "ideal" (no wait), "30 minutes
  earlier", and "60 minutes earlier" — each independently priced, with the two earlier options
  always carrying a waiting-place suggestion. The "30-minute rule" (FR-005/FR-006 in their
  original form) no longer exists as a conditional trigger; see the FR-003 through FR-006
  entries above for the current requirements.
- **Clarified (post-implementation)**: the rider's "desired time" throughout this document
  is their **arrival deadline** — e.g. "I need to be at my meeting by 14:00" — not a
  departure time. The system works backwards from it: it estimates a typical-traffic
  departure time for that arrival (the "naive departure"), and every option's departure time
  in FR-003 is that naive departure, or 30/60 minutes before it. Each option's `departure_time`
  is always a departure time; `arrival_time` is the system's estimate of when that departure
  gets the rider there.
- "Best fare" for the "30 minutes earlier"/"60 minutes earlier" options means whatever
  `FarePredictor` estimates for departing at that specific earlier time — not a search for the
  single cheapest time in a window. This is a recommendation to help the rider decide when to
  travel, not an automatic rebooking of a fixed itinerary.
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
- Underlying conditions data (traffic, weather, pricing history) needed to compute the
  recommendation is assumed to be available from existing or third-party sources; this
  specification does not define where that data comes from.
