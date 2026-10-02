# Parcel warehouse — source contracts

**Status:** Step 2 — implementation baseline. **Contract version:** `1.0`. This document records the source contract approved before implementation; the completed code, data, and tests are indexed in the [project README](../README.md). Read with [PROJECT_SPEC.md](PROJECT_SPEC.md), [EVENT_AND_CUTOFF_RULES.md](EVENT_AND_CUTOFF_RULES.md), and [DATA_GENERATION_PLAN.md](DATA_GENERATION_PLAN.md).

## Scope and working defaults

SQL Server; Bronze → Silver → Gold; local full refresh; approximately 10,000 parcels; 100 paying-sender customers; 8 depots; 3 services. The network, regions, towns, businesses, and depots are fictional. Use UTC and elapsed calendar hours. The confirmed on-time denominator includes **all eligible due shipments**, not just delivered shipments. Cancellation is permitted only before handover; delivery, return-to-sender, and loss are mutually exclusive terminal outcomes. No pricing, finance, cloud services, or additional business sources.

Baseline settings: fictional Lumenland; 140 sender sites; January–June 2026 bookings; T = `2026-06-28T23:59:59Z`; E = `2026-07-07T23:59:59Z`. Current master attributes are resolved as of E. Restate occurrence-time outcomes at T using evidence supplied by E. Post-T events never count as T outcomes, attempts, or coverage. Evidence within a certificate window W > T may change certificate validity and first-attempt eligibility, without making later events count before cutoff. No source fields are added for that distinction.

There are exactly six business files: `shipments.csv`, `tracking_events.csv`, `customers.csv`, `customer_locations.csv`, `depots.csv`, `service_levels.csv`. Gold remains `fact_shipments` and `dim_customers`, `dim_services`, `dim_depots`, `dim_date`. Technical run metadata, rejection logs, and test expectations are not business sources.

The specification, extracted reference, original ZIP inventory, and supplied [reference audit/roadmap](VALIDATION.md#reference-benchmark-and-scope) were reviewed. The approved specification supersedes the audit's larger scale, financial measures, and delivered-only on-time denominator. The audit's reproducibility, explicit-reject, deterministic-resolution, and reconciliation recommendations are retained. The attempt certificate and event rules below are original parcel-domain decisions, not claimed reference capabilities. Their later SQL Server tests are summarized in [VALIDATION.md](VALIDATION.md).

## Shared CSV and type conventions

| Item | Contract |
| --- | --- |
| Encoding and lines | UTF-8 without byte-order mark; LF line endings; final newline. Reader may accept CRLF or a UTF-8 BOM with a format warning. |
| Header | Exactly the field names, spelling, case, and order in each table below, once on the first line. No comments, extra columns, or duplicate headers. Every column is present even when its value is optional. |
| Delimiter/quoting | Comma delimiter; double-quote fields containing a comma or double quote; escape an embedded double quote by doubling it. Embedded line breaks in values are prohibited. Every row must have exactly the header's field count. A final empty field requires a trailing comma (or a quoted empty final field); an extra field beyond the header count is prohibited. |
| Null | An empty CSV field, quoted or unquoted, means null. Literal `NULL`, `N/A`, and whitespace-only tokens are not alternative null encodings. Whitespace-only descriptive text may be normalized to null with a quality flag; a required key then fails its key rule. |
| Timestamp | `YYYY-MM-DDTHH:MM:SSZ`, for example `2026-06-29T10:00:00Z`. UTC, whole seconds, Gregorian calendar; no offsets, fractional seconds, leap seconds, or locale-specific dates. Comparisons use timestamps, never date-only truncation. |
| Integer | Base-10 whole number from 0 to 2,147,483,647, with no separators/sign/decimal/exponent notation. Positive means at least 1; nonnegative permits 0. |
| Boolean | Exactly `true` or `false`. |
| Text | Maximum character length shown; no control characters. Trim surrounding spaces with a recorded normalization flag; preserve case in descriptive names. Keys/codes use their stated uppercase patterns; do not guess or repair malformed IDs. |
| Required/optional | `R` = value required in a conforming export; `O` = null allowed; `C` = conditionally required. This describes source validity, not automatic row rejection: the per-source defect rules decide whether to quarantine or retain a faulty row. |
| Examples | Illustrative fictional values, not a generated dataset. `empty` in examples means an empty field, not that literal word. |

**File-level ingestion failure:** missing file, wrong header, invalid UTF-8, unbalanced quoting, wrong field count, embedded newlines, or a file whose record boundaries cannot be trusted. Fail the batch; do not partially publish Gold. A header-only file is structurally valid; required reference/entity counts are checked separately. For a structurally readable file, bad business values are handled per record instead of aborting CSV parsing.

**Raw provenance:** the landing process records file name/checksum, logical row number, load ID, and export cutoff. These are warehouse metadata, not extra CSV fields. Keep original text before cleaning. Export availability time is the batch setting `extract_as_of_utc`, shared across all six files. Occurrence/reporting cutoff and export time are deliberately different.

**Attribute defects:** invalid/overlength descriptive text becomes Unknown (or null for optional descriptions) with a flag; do not silently truncate it. Invalid keys and control fields follow each source's stricter rules. A legitimate optional null is allowed; a flag may still describe an analytical limitation. Source-row AC/AF is finalized after relationship/conflict checks, so an initially well-formed row can become AF without becoming Q. FK means foreign key: a field that refers to another source's business key.

**Reusable types:** shipment ID = text(12), pattern `SHP` plus 9 digits; event ID = text(14), `EVT` plus 11 digits; attempt ID = text(14), `ATT` plus 11 digits; customer ID = text(7), `CUS` plus 4 digits; location ID = text(8), `LOC` plus 5 digits; depot ID = text(5), `DEP` plus 2 digits. IDs are opaque identifiers; their numeric suffix does not indicate time or priority.

**Versioned master/booking exports:** all sources except tracking events have `record_version` and `source_updated_at_utc`. A source-issued version increases when that entity changes; the timestamp is the source's commit time. Resolve a consistent version/time history, select its highest source-issued version, then validate the selected candidate's content. If it fails, quarantine it without falling back to an older version. Equal-version conflicts, invalid ordering metadata, and immutable-field changes do not get an arbitrary winner. Exact duplicate and superseded rows have explicit dispositions. [Resolution rules](EVENT_AND_CUTOFF_RULES.md#deterministic-resolution-and-row-accounting) are authoritative; a CSV line's position never breaks a business tie.

## 1. shipments.csv

**Purpose/grain:** one exported version of one booking for one physical parcel. The selected current version yields one shipment. The file is an operational booking register with a tracking-log certification supplied by the carrier's dispatch synchronization process. It is not a KPI extract.

| Field (header order) | Logical type | Need | Meaning / representative value |
| --- | --- | --- | --- |
| shipment_id | Shipment ID | R | Booking/parcel business key; `SHP000000001`. |
| customer_id | Customer ID | R | Paying sender; `CUS0001`. |
| service_code | text(8), enum | R | Booked offering; `STANDARD`. |
| origin_depot_id | Depot ID | R | Assigned starting depot; `DEP01`. |
| destination_depot_id | Depot ID | R | Assigned delivery depot; `DEP06`. |
| booked_at_utc | timestamp | R | Booking accepted by carrier; `2026-06-28T09:00:00Z`. |
| handed_over_at_utc | timestamp | O | Actual transfer into carrier custody; `2026-06-29T10:00:00Z`, or empty if not yet handed over. |
| promised_delivery_at_utc | timestamp | R | Promise agreed at booking; `2026-07-01T10:00:00Z`. Retain a missing/invalid value as a defect, never fill it from the service target. |
| cancelled_at_utc | timestamp | O | Booking cancellation time; empty unless cancelled. |
| attempt_history_status | text(11), enum | R | `COMPLETE`, `PARTIAL`, or `UNAVAILABLE`; this is a source-system assertion about its synchronized attempt log. |
| attempt_history_through_at_utc | timestamp | C | Required for COMPLETE or PARTIAL; the end of the period covered by the source reconciliation, e.g. `2026-06-30T23:59:59Z`. |
| attempt_history_attempt_count | nonnegative integer | C | Required for COMPLETE; number of physical attempts the dispatch ledger says occurred from booking through the stated watermark, inclusive; e.g. `2`. Not the observed CSV event count. |
| attempt_history_attested_at_utc | timestamp | C | Required for COMPLETE or PARTIAL; when the dispatch system performed/recorded reconciliation, e.g. `2026-07-02T08:00:00Z`. |
| record_version | positive integer | R | Source booking revision; `3`. |
| source_updated_at_utc | timestamp | R | Commit time of this complete record version, including certificate; `2026-07-02T08:00:00Z`. |

**Keys/relationships:** `shipment_id` is the business key; `(shipment_id, record_version)` identifies a version. Customer, service, and both depot IDs reference their respective master business keys. A syntactically valid but unresolved reference is retained and maps to Unknown in Gold. Same origin/destination depot is allowed; local delivery is plausible. No location ID is needed on a shipment.

**Uniqueness/chronology:** booking ≤ handover when present; booking ≤ promise, and handover ≤ promise when handover exists; booking ≤ cancellation. Cancellation and handover must never coexist for the same valid booking, even if cancellation is earlier. Booking, actual handover, cancellation, and certificate attestation must not exceed the version commit time; commit ≤ extract time. A future promise is allowed. Certificate watermark ≤ attestation ≤ commit. COMPLETE may assert zero attempts. PARTIAL has a watermark/attestation but a null certified count; UNAVAILABLE has all three certificate detail fields null.

**Immutable across versions:** shipment ID, customer, service, depot pair, booking, and promise. Handover/cancellation may move from null to a value, but a nonnull occurrence time cannot change. Certificates may progress through time; history watermarks cannot move backwards between versions. Version one supports corrections to certificate status/count and retraction from COMPLETE to PARTIAL, but not silent shipment rebooking, promise revision, or cancellation reversal. A forbidden immutable change quarantines the key's distinct versions for investigation.

**Defects:** missing/malformed shipment ID, booking timestamp, version, or commit metadata → quarantine (Q); no untrustworthy booking can establish cutoff inclusion. Missing/malformed dimension reference → accepted with flag (AF), map Unknown. Missing/invalid promise or invalid handover → AF, null the unusable analytical value and exclude only dependent KPIs; distinguish invalid handover from a legitimate null. A null handover with tracking evidence is retained with a context flag. Bad cancellation timestamp or a cancellation/handover contradiction → AF; throughput/due exclusion depends on whether it affects T, as specified in the event rules. Bad certificate fields or an unusable certificate on an attempted shipment → AF; it affects first-attempt measurement, not delivery confirmation. Valid future bookings → accepted, outside reporting cutoff, not Q. File structure failures follow the shared rule.

**Supports:** throughput, due population, transit start, cancellation exclusions, on-time deadline, age at cutoff, customer/service/depot grouping, and explicit attempt-history eligibility. No on-time flag, duration, attempt-success rate, derived shipment status, or warehouse key belongs in this CSV.

## 2. tracking_events.csv

**Purpose/grain:** one immutable tracking message identified by event ID; immutable means unchanged once issued. Multiple different messages may describe the same physical attempt. Repeated exports of the same message are possible. There are no mutable event versions in version one; correcting an erroneous immutable event requires an investigated future contract change, not a fabricated higher priority scan.

| Field (header order) | Logical type | Need | Meaning / representative value |
| --- | --- | --- | --- |
| event_id | Event ID | R | Message business key; `EVT00000000001`. |
| shipment_id | Shipment ID | R | Referenced booking; `SHP000000001`. |
| event_type | text(18), enum | R | One value from the event vocabulary below; `DELIVERY_SUCCEEDED`. |
| occurred_at_utc | timestamp | R | When the physical event occurred; `2026-06-30T15:00:00Z`. For attempt messages, the physical attempt's completion time. |
| recorded_at_utc | timestamp | R | When the central tracking platform first persisted this message; `2026-07-01T08:00:00Z`. This is arrival at the source platform, not warehouse load time. |
| depot_id | Depot ID | O | Depot associated with this scan; `DEP06`. Not a replacement for the shipment's assigned depot pair. |
| physical_attempt_id | Attempt ID | C | Required only on attempt messages; `ATT00000000001`. Empty on other events. |
| attempt_sequence | positive integer | C | Required only on attempt messages; `1` for the first physical attempt on this shipment, `2` for the second, etc. Assigned by dispatch, not derived by sorting scans. |

**Closed event vocabulary:** `IN_TRANSIT`, `OUT_FOR_DELIVERY`, `DELIVERY_FAILED`, `DELIVERY_SUCCEEDED`, `RETURNED_TO_SENDER`, `LOST`. The two `DELIVERY_...` values are attempt messages and carry attempt ID/sequence. `OUT_FOR_DELIVERY` is a route progress message, not evidence of an actual attempt. Terminal types are `DELIVERY_SUCCEEDED`, `RETURNED_TO_SENDER`, and `LOST`. No cancellation/handover event competes with the booking's authoritative timestamps.

**Keys/relationships:** event ID is unique after deduplication; shipment ID references shipments. The physical attempt key is `(shipment_id, physical_attempt_id)` with exactly one sequence number and completion time; attempt ID must also belong to only one shipment globally. `(shipment_id, attempt_sequence)` identifies at most one physical attempt. Nullable depot ID references depots; unresolved depot maps to Unknown/flag and does not discard a valid tracking event.

**Uniqueness/chronology:** recorded ≥ occurred; recorded ≤ extract; occurred ≥ the parent's valid booking time. A legitimate later-arriving event can have recorded > reporting cutoff. A scan must be no earlier than valid carrier handover; equality is allowed. A future-to-cutoff scan may still be a valid event. Repeat messages for one attempt must agree on attempt time, sequence, and result; central recording time/depot metadata may differ. Distinct event IDs are never declared duplicate source rows just because they describe the same attempt.

**Defects:** missing/invalid event ID, shipment ID, event type, occurrence, recording time, required attempt metadata; forbidden attempt metadata on non-attempt events; recorded < occurred; recorded > extract; unresolved shipment after shipment resolution; or scan before booking or a valid handover → Q. Conflicting payloads (field values) for the same immutable event ID → Q for all distinct variants, with no winner. Missing or invalid optional depot → AF. With a missing/unusable shipment handover, retain otherwise well-formed events with a relationship-time flag; they cannot supply valid post-handover KPI evidence until the handover is known. Well-formed incompatible attempts or terminal outcomes → accepted conflict evidence, not malformed rows; derive an unknown affected result. Quarantined terminal/attempt evidence creates a quality hold on an identifiable parent as specified in the event rules. File structure failures follow the shared rule.

**Supports:** terminal outcomes, delivery time, first-attempt outcome, valid scan coverage, event count, overdue-open determination, and missing/conflicting evidence counts. The successful scan is itself an attempt; no separately generated artificial attempt record is needed.

## 3. customers.csv

**Purpose/grain:** one exported version of one paying business sender account. No recipient or personal contact data.

| Field (header order) | Logical type | Need | Meaning / representative value |
| --- | --- | --- | --- |
| customer_id | Customer ID | R | Business key; `CUS0001`. |
| customer_name | text(100) | R | Fictional account label; `Cloudfern Crafts`. |
| customer_segment | text(12), enum | R | `SMALL`, `MIDMARKET`, or `ENTERPRISE`. |
| account_opened_at_utc | timestamp | R | Account creation; `2025-12-15T09:00:00Z`. |
| record_version | positive integer | R | Source revision; `1`. |
| source_updated_at_utc | timestamp | R | Commit time; `2026-06-30T10:00:00Z`. |

**Keys/rules:** business key customer ID; version key `(customer_id, record_version)`. No outward FK. Account opened ≤ commit ≤ extract. Account ID and opening time immutable; name/segment may change with a later consistent version. Shipments booked before a known account-open date receive a relationship chronology flag; retain the relationship and shipment rather than guessing a different account.

**Defects:** missing/bad key, version/commit, or opening timestamp/order → Q. Missing/overlength name or invalid segment → AF with Unknown for that attribute. Same-version conflict → Q key group; higher consistent version → accepted winner, earlier versions classified superseded. The source does not include a primary-location pointer; location selection follows the location contract. File structure failures follow the shared rule.

**Supports:** all customer/segment splits, account service review, missing customer/reference reporting. Primary-location attributes are enriched only after resolving location candidates to one result per customer.

## 4. customer_locations.csv

**Purpose/grain:** one exported version of one registered sender site. A customer can own several sites, but at most one active site is designated primary. These are account locations, not shipment recipient addresses.

| Field (header order) | Logical type | Need | Meaning / representative value |
| --- | --- | --- | --- |
| location_id | Location ID | R | Site business key; `LOC00001`. |
| customer_id | Customer ID | R | Owning sender; `CUS0001`. |
| location_name | text(100) | O | Fictional label; `Cloudfern Main Studio`. |
| city_name | text(60) | R | Fictional town; `Lumenford`. |
| region_code | text(12), enum | R | `NORTH_REACH`, `EAST_VALE`, `SOUTH_MERE`, or `WEST_FEN`. |
| is_active | boolean | R | Whether the registered site is currently active; `true`. |
| is_primary | boolean | R | Source's current designated-primary marker; `true`. |
| record_version | positive integer | R | Site revision; `2`. |
| source_updated_at_utc | timestamp | R | Commit time; `2026-06-20T11:00:00Z`. |

**Keys/rules:** business key location ID; version key `(location_id, record_version)`; FK customer ID. Ownership and location ID immutable. Commit ≤ extract. Site attributes and primary/active markers may change in a consistent higher version. `is_primary=true` requires `is_active=true`. Two different active primary locations for one customer are a business conflict; different modification times do not decide which is primary.

**Defects:** invalid key, owner, version/commit, or unresolved owner → Q. Invalid required booleans → Q because selection is unknowable. Missing/invalid city/region → AF with Unknown geography. Inactive-but-primary → AF and not a primary candidate. No valid primary candidate → customer retained with Unknown geography. Multiple valid primary candidates → all source rows retained, one customer with Unknown geography and a conflict flag. A quarantined location that could change primary selection causes a selection hold for its identifiable customer; no apparently unique survivor is silently chosen. File structure failures follow the shared rule.

**Supports:** customer geography and quality reporting. No extra Gold location dimension. [Primary selection rules](EVENT_AND_CUTOFF_RULES.md#customer-primary-location) ensure the customer join remains one-to-one.

## 5. depots.csv

**Purpose/grain:** one exported version of one operating depot in the fictional domestic network.

| Field (header order) | Logical type | Need | Meaning / representative value |
| --- | --- | --- | --- |
| depot_id | Depot ID | R | Business key; `DEP01`. |
| depot_name | text(80) | R | Fictional label; `Lumenford Hub`. |
| city_name | text(60) | R | Fictional town; `Lumenford`. |
| region_code | text(12), enum | R | Same four region values as customer locations; `NORTH_REACH`. |
| record_version | positive integer | R | Revision; `1`. |
| source_updated_at_utc | timestamp | R | Commit; `2025-12-01T09:00:00Z`. |

**Keys/rules:** business key depot ID; version key `(depot_id, record_version)`; no outward FK. Commit ≤ extract. Depot ID immutable; descriptive attributes may change in a consistent later version. Names need not be unique; names are never join keys.

**Defects:** key/version/commit defect or unresolved same-version conflict → Q; missing/invalid descriptive field → AF and Unknown attribute. Referencing shipments/events stay accepted with Unknown depot when the master cannot resolve. File structure failures follow the shared rule.

**Supports:** origin/destination grouping, depot-pair transit, destination performance, regional analysis. The same dimension supplies both roles; event depot is available in Silver but does not add an event-depot link to the shipment fact.

## 6. service_levels.csv

**Purpose/grain:** one exported version of one booked delivery service definition.

| Field (header order) | Logical type | Need | Meaning / representative value |
| --- | --- | --- | --- |
| service_code | text(8), enum | R | Business key: `ECONOMY`, `STANDARD`, or `EXPRESS`. |
| service_name | text(60) | R | Fictional display label; `Standard Parcel`. |
| nominal_transit_hours | positive integer | R | Operational target: ECONOMY 72, STANDARD 48, EXPRESS 24. These are elapsed calendar hours, not prices or a calculated KPI. |
| record_version | positive integer | R | Revision; `1`. |
| source_updated_at_utc | timestamp | R | Commit; `2025-12-01T09:00:00Z`. |

**Keys/rules:** service code is business key; version key `(service_code, record_version)`; no outward FK. Commit ≤ extract. Code and nominal target are fixed in version one; consistent later versions may change the display label. No source cost, tariff, revenue, currency, or price columns.

**Defects:** invalid key/version/commit, forbidden target change across versions, or same-version conflict → Q. Missing/bad label or target differing from the stated mapping → AF with Unknown/unusable attribute and a quality flag. Never repair a shipment promise from the service master. File structure failures follow the shared rule.

**Supports:** service grouping and documentation of fictional promise policy. Shipment-provided promises remain authoritative even when the service label/nominal target is unavailable.

## Contract-to-question and KPI cross-check

| Spec question / KPI | Source fields and required derivation |
| --- | --- |
| Q1 throughput | Shipments booking/handover/cancellation; customer, service, and two depot keys. Date dimension generated from UTC dates. |
| Q2 confirmed on time | Valid handover/promise define due population; event type/occurrence establishes unambiguous delivery ≤ promise. Missing/conflicting outcomes stay in denominator. |
| Q3 transit | Shipment handover to confirmed successful event time, grouped by assigned depot pair. |
| Q4 overdue backlog/age | Due promise < cutoff, no confirmed terminal outcome and no terminal uncertainty; cutoff minus promise. No events is open/unconfirmed, not automatic loss. |
| Q5 first attempt | Shipment source certificate and watermark/count; event physical attempt ID, source sequence, result, and time. Certification and collapse rules are mandatory. |
| Q6 customer service review | Customer key/name/segment and due population; count due minus confirmed-on-time, then divide by due. Report excluded and unknown counts. |
| Q7 return/loss | Explicit unambiguous terminal type by cutoff / due population. |
| Q8 evidence/eligibility | Valid event existence after handover, source/relationship flags, certificate validation, and per-KPI exclusion reasons. Quality attributes are produced in Silver/Gold, not source KPI columns. |

Source record grain, event-to-attempt collapse, shipment summary grain, and Gold grain are different. Only accepted distinct bookings enter the Gold fact; every enrichment must preserve that row count.
