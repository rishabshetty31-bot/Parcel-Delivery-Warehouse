# Parcel Delivery Performance Warehouse

**Status:** Step 2 — implementation baseline. **Baseline version:** `1.0`.  
**Platform:** SQL Server.  
**Data:** Fictional, clearly labelled synthetic data; generation will happen later.  
**This Step 2 baseline originally defined documentation only.** Read it with [SOURCE_CONTRACTS.md](SOURCE_CONTRACTS.md), [EVENT_AND_CUTOFF_RULES.md](EVENT_AND_CUTOFF_RULES.md), and [DATA_GENERATION_PLAN.md](DATA_GENERATION_PLAN.md). A source contract defines a file's fields, keys, allowed values, and validation rules. SQL, datasets, implementation, and diagrams were built in later steps.

**Reader note:** The status and future tense above record the approved Step 2 baseline. The completed implementation, run order, diagrams, and executed checks are indexed in the [project README](../README.md) and [validation summary](VALIDATION.md). This note does not change the business rules below.

## 1. Business scenario, users, and decisions

A fictional domestic parcel carrier serves business customers through a small depot network. Shipment bookings, tracking scans, customer records, and service definitions arrive as separate CSV exports. The operations team cannot consistently distinguish late deliveries, open overdue shipments, repeated delivery attempts, and gaps in tracking information. Joining every scan directly to a shipment also inflates shipment counts.

The warehouse will produce a consistent shipment-level view of delivery performance at a declared reporting cutoff. It will support weekly operational reviews and monthly customer/service reviews. It is a learning and portfolio project, not a live dispatch system or evidence about any real carrier.

| Intended user | Decisions supported |
| --- | --- |
| Operations manager | Prioritize overdue shipments and investigate services or depot pairs with persistent delays. |
| Depot supervisor | Investigate high repeat-attempt rates, aged open shipments, and missing tracking evidence. |
| Customer/account manager | Identify customer accounts experiencing poor service and prepare fact-based service reviews. |
| Analyst or portfolio reviewer | Reproduce metrics, trace them to sources, and assess whether data quality affects conclusions. |

Depot and customer comparisons describe observed performance; they do not establish who caused a delay. Route complexity, staffing, weather, and parcel characteristics are not modeled comprehensively.

### Reference evidence and originality

The benchmark is the extracted `sql-data-warehouse-project-main/` directory reviewed during the early planning steps. During Step 1, the extracted project was inspected, but the audit and ZIP had not yet been located. During Step 2, the original ZIP was located and its inventory checked; the supplied [reference audit/roadmap](VALIDATION.md#reference-benchmark-and-scope) was then read at the path provided by the user. They were project inputs, not required files in this handover. The following features were verified from source files, not by executing the reference warehouse. The audit separately reports CSV profiling and visual inspection; those measurements were not independently rerun for this baseline. No SQL Server execution or runtime validation is claimed for the historical reference review or Step 2 baseline; later parcel-specific execution is recorded in the linked evidence.

The approved specification takes precedence over the audit's proposed parcel design: retain approximately 10,000 shipments over six months, exclude financial analytics/charges/currency, and use all eligible due shipments for confirmed on-time delivery. The audit's larger scale, delivered-only denominator, and financial measures are not adopted. Its reproducibility, failure reporting/recovery, deterministic resolution, row-reconciliation, and accurate-documentation recommendations inform later acceptance checks.

| Feature | Verified reference evidence | Parcel project proposal |
| --- | --- | --- |
| Layered SQL Server warehouse | `README.md` and `scripts/init_database.sql` describe/create Bronze, Silver, and Gold schemas. | Retain the same three-layer learning structure. |
| Six CSV inputs | Six files exist under `datasets/source_crm/` and `datasets/source_erp/`. | Six original parcel-domain sources, specified below. |
| Full refresh | Bronze and Silver loading procedures truncate and reload tables; Bronze uses bulk CSV ingestion. | Full-refresh loading for version one. |
| Cleaning and integration | Silver code trims/standardizes values, handles nulls, deduplicates customers, and transforms dates; Gold joins CRM/ERP records. | Clean parcel identifiers and events, reconcile shipment outcomes, and aggregate many scans to one shipment. |
| Gold model | `scripts/gold/ddl_gold.sql` defines customer/product dimensions and a sales fact as views. | One shipment fact and four dimensions, including date and depot roles. |
| Quality checks | `tests/quality_checks_silver.sql` and `tests/quality_checks_gold.sql` contain diagnostic queries; the README instructs manual execution. | Automated pass/fail checks, reconciliation, and known-answer KPI cases. These are proposed improvements, not a verified reference automation framework. |
| Documentation and diagrams | `docs/data_catalog.md`, `docs/data_layers.pdf`, and four named diagram image files exist: Architecture, Data Flow, Data Integration, Data Model. | A parcel-specific catalog, operational instructions, and four original diagrams with editable sources. The audit reports visual mismatches in the reference; new diagrams must match the actual model. |

Technical depth comes from event aggregation, explicit time rules, controlled joins, transparent KPI eligibility, and reproducible validation. This is an original parcel model, not renamed sales tables. Reproducible synthetic generation, an explicit reporting cutoff, and automated failure reporting are additional proposed capabilities; they were not established as reference features.

## 2. Eight business questions

1. How many shipments are handed over each week and month, by customer, service, origin depot, and destination depot?
2. Of shipments whose promised deadline has passed, what percentage have confirmed delivery by that deadline, and how does this vary by service and destination depot?
3. How long do delivered shipments take from carrier handover to delivery, and which origin–destination depot pairs have the longest average transit times?
4. How many shipments remain open beyond their promised deadline at the reporting cutoff, and how old is this overdue backlog?
5. What percentage of shipments with a recorded delivery attempt succeed on the first attempt, by service and destination depot?
6. Which customer accounts have the largest number and highest proportion of shipments without confirmed on-time delivery?
7. What proportions of due shipments end in return-to-sender or loss, and where are these outcomes concentrated?
8. How complete is the tracking evidence, and how many shipments cannot be evaluated for each KPI because required information is missing or invalid?

Each question will later have a documented analytical query. Report numerator and denominator counts alongside rates; do not rank tiny groups without showing their size. The baseline minimum comparison size is 30 eligible shipments; smaller groups remain visible and labelled.

## 3. Sources and layer responsibilities

Exactly six CSV business sources are in scope. The business roles below are unchanged; exact fields and dispositions are defined in SOURCE_CONTRACTS.md.

| Source | Intended record meaning | Purpose and integration rule |
| --- | --- | --- |
| `shipments` | One exported version of a booking for one physical parcel; resolve versions to one shipment ID. | Defines the shipment population, paying customer, service, depot pair, booking/handover/promise/cancellation timestamps, and source certification of attempt-log completeness. The stored promise is authoritative. |
| `tracking_events` | One immutable event message; multiple messages may describe one physical attempt. | Supplies delivery attempts, successful delivery, return-to-sender, loss, and progress evidence. Deduplicate message IDs, then collapse physical attempts before deriving one shipment summary. |
| `customers` | One exported version of a business customer account. | Resolve to one current account as of E. Provides fictional name and segment; customer means paying sender, not recipient. |
| `customer_locations` | One exported version of a registered site; a customer may have several sites. | Resolve site versions as of E, then select at most one current primary location per customer. This is account geography, not parcel destination geography. |
| `depots` | One exported version of a depot. | Resolve to one current depot as of E. The same lookup supplies both origin and destination roles. |
| `service_levels` | One exported version of a service offering. | Resolve to one current service as of E. Its description/nominal target never overwrites the shipment's stored promise. |

**Bronze — raw landing:** preserve source values and source-row provenance so cleaning decisions can be explained. File-structure failures stop the load; no file or malformed row may disappear silently.

**Silver — cleaned and integrated:** standardize types and codes, resolve duplicates deterministically, validate relationships and chronology, record rejected rows/reasons, and summarize valid events by shipment. “Deterministic” means the same inputs always produce the same decision. Conflicting duplicates without a defensible winner are quarantined rather than chosen arbitrarily; quarantine means retaining them outside accepted analytical data with a reason.

**Gold — business-ready:** expose the one-row-per-shipment fact and four dimensions. A dimension describes entities used for grouping; a fact holds measurements and links to those descriptions. Gold views over Silver are the version-one baseline, matching the reference approach. The implemented column contract is in [WAREHOUSE_DESIGN.md](WAREHOUSE_DESIGN.md).

The date dimension is generated inside the warehouse, not supplied as a seventh CSV. Run settings, quality results, rejection records, and the independent test-only expected-results manifest are technical metadata, not additional business sources or Gold facts. Transformations must never read the expected-results manifest.

**Row accounting:** for each readable file, `R = AC + AF + DX + DE + DS + Q`: raw rows equal accepted clean, accepted flagged, exact extra copies, normalized-equivalent extra copies, superseded versions, and quarantined representatives. These are mutually exclusive source-row dispositions. Accepted rows are `S = AC + AF`. For shipments, `S = F + B_future`, where F is the Gold fact population and B_future is accepted bookings after T. For events, accepted rows split into occurrence at/before T versus after T; neither temporal exclusion is a rejection. Master records are current as of E and are not filtered by T. Resolving several accepted messages to one physical attempt is aggregation, not source-row deduplication. EVENT_AND_CUTOFF_RULES.md defines the complete equations and precedence.

## 4. Fact grain and dimension relationships

**Fact:** `fact_shipments`. **Grain:** exactly one row per accepted, distinct shipment ID booked on or before the reporting cutoff, including cancelled, open, delivered, returned, and lost shipments. Grain is the precise meaning of one fact row.

One shipment is one parcel with one booked service and one assigned origin/destination depot pair. Multi-parcel consignments, split deliveries, and depot reassignment history are out of scope. Events alone never create shipments. Rows with no usable shipment ID, an unusable booking timestamp, or unresolved conflicting shipment identity are quarantined and reconciled separately; the booking timestamp is required to establish inclusion at cutoff. Valid bookings after cutoff are outside the reporting population and counted separately. “One row per shipment” does not mean one row per raw duplicate.

| Dimension | Fact relationship | Meaning and safeguards |
| --- | --- | --- |
| `dim_customers` | Many shipments to one customer. | Current customer account attributes plus at most one primary location. Never join all customer locations directly to the fact. |
| `dim_services` | Many shipments to one service. | The service booked for that shipment. |
| `dim_depots` | Two many-to-one links: origin depot and destination depot. | One dimension reused in two roles, not two separate dimensions. Operational outcome comparisons default to destination depot; depot-pair transit comparisons use both. |
| `dim_date` | Four many-to-one links: booking, handover, promised delivery, actual delivery dates. | One calendar reused in four roles. Actual timestamps remain available for elapsed-hour calculations. Missing dates use a documented unknown/not-applicable key. |

This is a star schema: a central fact joined to descriptive dimensions. Reusing a dimension for different meanings is called a role-playing dimension. There are exactly four dimension objects despite multiple depot and date links.

The fact will contain the shipment identifier, milestone timestamps, outcome at cutoff, event/attempt counts, elapsed durations, KPI eligibility/quality indicators, and the reporting cutoff. Source fields are now defined in SOURCE_CONTRACTS.md; final warehouse-column design remains later work. Shipment ID remains the business identifier even if internal warehouse keys are introduced. Source IDs must be retained; any future surrogate-key approach must not advertise recalculated row numbers as durable identity.

Tracking events remain detailed in Silver. Gold receives their shipment summary only, preventing scan counts from multiplying shipment counts. A missing dimension reference maps to a labelled Unknown member and is reported; it must not silently remove an otherwise accepted shipment. All dimension joins must preserve the fact's row count.

Dimensions contain current attributes as of export time, not historical attributes at the reporting cutoff. Full refresh may restate older shipments under updated customer classifications; version one does not preserve historical attribute versions or past daily backlog snapshots. Customer primary geography is Unknown if missing, conflicting, or subject to an unresolved selection hold; no arbitrary newest-location choice is allowed.

## 5. Core KPI definitions

### Shared rules and reporting populations

- **Fixed cutoff:** `reporting_cutoff_utc` (T) is `2026-06-28T23:59:59Z`; `extract_as_of_utc` (E) is `2026-07-07T23:59:59Z`. Use all supplied evidence recorded by E to restate outcomes occurring by T, including late-arriving scans. This does not reconstruct what was known at T. Events occurring after T never count as T outcomes, physical attempts, or tracking coverage. When certificate watermark W > T, evidence within the full certificate window can change certificate validity and therefore first-attempt KPI eligibility; it cannot move those later events into T. Reruns with identical files/settings must agree; new late evidence can change a restated result.
- **Time:** use UTC timestamps and elapsed calendar hours, including weekends, for version one. Date grouping uses UTC dates. Service targets are fictional learning rules, not real carrier commitments. No holiday/business-day or local-time SLA engine is included. SLA means a promised service target.
- **Authority:** shipments supply the actual handover and promised delivery timestamps; valid tracking events supply delivery and other terminal outcomes. A missing promise is not reconstructed from service defaults. A missing actual handover is not inferred from the booking date.
- **Valid lifecycle:** cancellation is allowed only before carrier handover; a cancelled booking cannot subsequently be handed over in this version. After handover, a shipment is open, delivered, returned to sender, or lost. Return/loss are terminal outcomes; post-delivery customer returns are outside scope. Incompatible terminal events produce Unknown outcome. Cancellation/handover contradictions affecting T produce lifecycle-start uncertainty and exclude H/D; future contradictions are flagged without applying future milestones to T.
- **Delivery and attempts:** successful delivery is the earliest valid successful-delivery event between handover and T, provided the terminal outcome is unambiguous. Return/loss timestamps satisfy the same bounds. Invalid terminal evidence creates a quality hold, not assumed open/delivered status. Group repeated messages by shipment and physical attempt ID; matching sequence, completion time, and result collapse to one attempt. Success is that attempt, not an extra count. Conflicting results/times/identities remain unresolved. First-attempt eligibility requires a COMPLETE source certificate through at least T, a matching dispatch control count, contiguous trustworthy attempt sequences, and no applicable evidence/lifecycle holds. A lone success scan is insufficient. Precise rules are in EVENT_AND_CUTOFF_RULES.md.
- **Accepted reporting population (A):** all fact rows as defined above; A is the same population called F in the reconciliation rules. It is not all accepted source rows S, which also include valid bookings after T. Invalid non-identity fields remain flagged and affect eligibility rather than automatically deleting the shipment.
- **Handed-over population (H):** A with valid booking and actual handover timestamps, booking no later than handover, and handover no later than T. Cancellation/lifecycle-start uncertainty at T is excluded and reported. Conflicts between terminal tracking outcomes do not remove an otherwise valid handover from H; its outcome stays unknown. Reporting partitions and primary exclusion precedence are specified in EVENT_AND_CUTOFF_RULES.md.
- **Due population (D):** H with a valid promised timestamp no earlier than handover and no later than cutoff. Returned, lost, open, and unknown-outcome shipments remain in D. This avoids measuring only successful deliveries. Shipments with missing/invalid promises are outside D and appear in the eligibility report.
- **Period filters:** shipment throughput and transit/attempt analysis default to handover-date cohorts; on-time/return/loss rates default to promised-date cohorts. A cohort is the set of shipments sharing a selected period. Backlog is a snapshot at cutoff. Label the date basis on every result; date-dimension links do not imply one universal time filter.
- **Nulls and rates:** missing or invalid measurements stay unknown, never zero or automatic success. For every rate, show numerator, denominator, and excluded/unknown counts. A zero denominator returns N/A, not 0%. Recompute totals from counts, not by averaging subgroup percentages.

| KPI | Calculation and denominator | Exclusions and missing-data treatment |
| --- | --- | --- |
| Shipment throughput | Count of H in the selected handover period. No rate denominator. Also show total accepted bookings A and pre-handover cancellations separately. | Excludes unhanded-over bookings, cancellations, and invalid lifecycle/timestamps. Report each exclusion reason. |
| Confirmed on-time delivery rate | 100 × shipments in D with valid successful delivery at or before their promised timestamp / count of D. Equality with the promise is on time. | Future promises and missing/invalid handover or promise are excluded by D. Open overdue, returned, lost, missing-delivery-evidence, and conflicting-outcome shipments in D stay in the denominator and cannot enter the numerator. Show these categories separately. “Not confirmed on time” is not proof of physical late delivery. |
| Average transit hours | Sum of elapsed hours from handover to successful delivery / count of H shipments with a valid delivered outcome and nonnegative duration, for the chosen handover cohort. | Excludes open, returned, lost, unknown outcomes, and missing/invalid timestamps. Always display eligible count; this is delivered-shipment speed, not speed across all shipments. |
| Open overdue backlog and average overdue age | Count of D whose promise is strictly before cutoff and whose outcome is open. Average age = sum of hours from promise to cutoff / that backlog count. | Delivered, returned, and lost outcomes are not open backlog. No terminal scan means operationally open if there is no known conflict; label it “no confirmed terminal outcome,” including shipments with no scans. Conflicting outcomes are reported separately, not silently called open. Missing promises cannot be aged and are separately counted. Empty backlog gives count 0 and average N/A. |
| First-attempt delivery success rate | 100 × eligible shipments whose first physical attempt succeeded / H shipments with at least one valid attempt and a trustworthy complete attempt sequence through cutoff. | Unattempted shipments are excluded. A known first failed attempt remains in the denominator even if delivery later succeeds or the shipment stays open. Missing attempt identity/order, contradictory results, or incomplete history make the rate unknown for that shipment; show the excluded count. |
| Return-to-sender rate | 100 × shipments in D with a valid return-to-sender terminal outcome by cutoff / count of D. | Same due-population exclusions as on-time rate. Unknown/conflicting outcomes remain in the denominator but not the numerator; show their count. This is an observed rate at cutoff, not an eventual lifetime return rate. |
| Loss rate | 100 × shipments in D with a valid lost terminal outcome by cutoff / count of D. | Same exclusions as return rate. Missing scans or an old shipment never imply loss; loss requires an explicit valid event. |
| Tracking coverage | 100 × H shipments with at least one valid tracking event between handover and cutoff / count of H. | Duplicate, orphan, unparseable, pre-handover, and future events do not count. No valid event is uncovered, not an excluded denominator row. Coverage measures evidence availability, not proof of a complete event history. |

**Required data-quality companions:** for each KPI report its eligible and excluded shipment counts, with missing handover, promise, delivery time, attempt history, and conflicting lifecycle reasons where relevant. Show unknown dimension usage and accepted/quarantined/duplicate counts separately. If exclusion reasons overlap, report a distinct excluded-shipment total so reason counts are not mistaken for additive totals.

**Sanity example:** ten eligible due shipments include seven delivered on time, one delivered late, one still open overdue, and one returned. Confirmed on-time delivery is 7/10 = 70%, not 7/8 = 87.5%. The open backlog is one shipment; the returned shipment is not open. Missing tracking on a due shipment does not remove it from the on-time denominator.

## 6. Version-one deliverables and boundaries

### Version-one deliverables and current status

1. Specification, six source contracts, event/cutoff rules, and generation plan: finalized as this four-document Step 2 baseline. Source ownership assumptions, identifiers, eligibility, and quality rules are defined; the remaining deliverables below have not been implemented.
2. Reproducible synthetic CSV generation: fixed seed, pinned generator/configuration version, fixed date range and cutoff, and recorded file checksums. A seed is a starting value that makes randomized generation repeatable; a checksum fingerprints file contents. README and data documentation must prominently state “SYNTHETIC — FOR LEARNING ONLY.” Use fictional identities and locations; include controlled dirty-data cases with known expected results.
3. A SQL Server Bronze → Silver → Gold pipeline using full refresh: reload the complete supplied snapshot rather than detect changes. Include documented setup/run order, configurable input location, row reconciliation, and load outcome reporting. Keep orchestration local and simple.
4. Exactly one Gold shipment fact and four dimensions, with the relationships and KPI eligibility above. No extra event fact or location dimension.
5. Automated quality checks integrated with the documented run: machine-readable pass/fail and counts, clear failure status for critical errors, and separate expected quarantines/warnings. A run that fails checks must not be presented as a successful analytical refresh. High-availability or zero-downtime publication is not required.
6. Documentation: setup/run guide, source contracts, transformation rules, Gold data catalog, KPI dictionary, quality-check guide/results, and limitations. Eight business-question queries with sample results are later deliverables; no dashboard is required.
7. Four original diagrams with editable source files, each with a distinct purpose: **architecture** (platform and layers), **data flow** (load/clean/check sequence), **source integration** (six sources, keys, and event/location aggregation), and **Gold model** (grain, four dimensions, and depot/date roles).

**Planned learning-sized dataset:** 10,000 baseline shipment identities, 100 customers, 140 locations, 8 depots, and 3 services in fictional Lumenland. Booking window: 1 January–30 June 2026 inclusive. Reporting cutoff: `2026-06-28T23:59:59Z`; export availability: `2026-07-07T23:59:59Z`. The clean baseline has 9,000 due shipments, plus open future-due, unhanded-over, cancelled, and valid future-booking cases. Controlled defects can reduce accepted counts and must reconcile. DATA_GENERATION_PLAN.md fixes scenario counts, seed streams, event-volume target, anomaly quotas, and a small known-answer fixture; no data has been generated.

The four fictional regions are NORTH_REACH, EAST_VALE, SOUTH_MERE, and WEST_FEN, with two depots each. Service definitions are ECONOMY = 72 hours, STANDARD = 48 hours, and EXPRESS = 24 hours, all elapsed calendar hours. DATA_GENERATION_PLAN.md fixes the eight fictional depot names and assignments. These nominal service targets do not repair missing shipment promises.

### Explicitly outside version one

- Incremental loading (processing only new/changed records), change-data capture, and event streaming.
- SCD2 (keeping dated versions of changing dimension attributes), historical depot assignments, and historical daily backlog facts.
- Cloud deployment, production scheduling/availability guarantees, and dashboards.
- Financial profitability, invoicing, refunds, pricing optimization, route optimization, vehicle/driver analytics, GPS traces, and predictive models.
- Multiple parcels per shipment, international/customs flows, holiday calendars, revised delivery promises, detailed delay-cause attribution, and returns after completed delivery.
- Real personal/customer data, production integrations, and claims that synthetic performance represents a real business.

Incremental loading, SCD2, cloud deployment, and dashboards remain optional future enhancements. They are not prerequisites for accepting version one.

## 7. Measurable acceptance criteria

These are future completion tests, not claims that implementation already exists. Deliberately injected anomalies are assessed against recorded expected outcomes; passing does not require pretending raw data is perfect.

| Area | Pass condition |
| --- | --- |
| Scope and inputs | Exactly six CSV business sources, three layers, one Gold fact, and four dimension objects; each has documented purpose and grain. |
| Reproducible data | Two generation runs with the same seed, version, and configuration produce identical CSV checksums and row counts. All published data/sample outputs are identified as synthetic. |
| Row accountability | For each readable file, raw = accepted clean + accepted flagged + exact/equivalent/superseded duplicates + quarantined. Accepted shipments = fact rows + valid bookings after T. Reporting exclusions partition accepted populations separately; no outside-cutoff row is double-counted as a reject. Unreadable files fail visibly. Event aggregation is separate from source-row deduplication. |
| Shipment grain | Gold fact row count equals the accepted distinct shipment-ID population at cutoff; zero null shipment IDs and zero duplicate shipment IDs. Missing optional measurements do not reduce that population. |
| Safe relationships | Zero duplicate usable dimension keys, zero unresolved fact-to-dimension links after Unknown mapping, and no change in fact row count after any dimension join. Customer location and event joins cannot multiply shipments. |
| Calendar, occurrence, and certificate time | Date dimension covers valid milestone/cutoff dates plus an unknown/not-applicable member. An event occurring after T contributes no T terminal outcome, physical attempt, or tracking coverage. For W > T, the same event may validate or invalidate the full certificate and thereby change first-attempt KPI eligibility only; do not claim that every KPI must be unchanged when wider-window evidence changes. Test both a valid wider certificate and one with missing/conflicting evidence. Late-arriving pre-T evidence is included; no negative duration enters an average. |
| Event correctness | Known cases verify duplicate scans, out-of-order arrival, tied/conflicting events, first failed then successful attempt, incomplete attempt history, and contradictory terminal outcomes. Each matches its documented expected summary or quarantine/quality status. |
| KPI correctness | Known-answer cases cover on-time, deadline equality, late delivery, open overdue, future due, return/loss, cancellation, missing values, no scans, and zero denominators. The fourteen-shipment fixture has 14 accepted event messages, 10 physical attempts, H=12, D=10, on-time=4/10, transit=114h/6, backlog=2 with age=48h/2, first-attempt success=4/7, tracking=10/12, return=1/10, and loss=1/10. Counts match exactly; display rates/hours to two decimals. The separate 7/10 illustration is not this fixture. |
| Refresh repeatability | Two full refreshes with identical files/configuration/cutoff produce identical business rows and KPI results; technical run IDs/timestamps may differ. No append-based duplication occurs. |
| Automated checks | The clean/control fixture passes all critical checks. Deliberate critical failures return a failed run status; expected anomaly cases produce exactly the documented rejection/warning counts. Test report records check name, result, affected count, and run/cutoff. |
| Failure and recovery | Later implementation proves clean setup, visible failure propagation, documented recovery after interrupted full refresh, and equivalent business results on rerun. A failed/partial refresh is not reported as successful. These are future runtime checks, not results of this documentation step. |
| Business usefulness | All eight business questions are answerable from Gold without direct access to raw events; outputs show date basis, eligible population, and relevant unknown/exclusion counts. |
| Documentation | A reviewer can follow the documented local setup and run sequence from a fresh database, reproduce sample results, and explain each KPI using the catalog and four completed diagrams. Record the tested SQL Server version and runtime; no unsupported speed guarantee. |

## 8. Baseline decisions and readiness

The user supplied Step 2 working defaults: fictional domestic geography, 100 customers/8 depots/3 services, UTC/calendar-hour promises, the existing confirmed-on-time denominator, paying-sender customer meaning, cancellation only before handover, mutually exclusive terminal outcomes, and no finance/cloud/extra business sources. These are recorded defaults and do not need to be re-requested. Customer geography remains account geography; destination depot comparisons do not establish causal blame.

The following decisions are resolved in this implementation baseline:

1. **Restated reporting:** late-recorded events may revise an earlier cutoff's result; there is no what-was-known-at-the-time reconstruction. The fixed T/E dates are in the generation plan.
2. **Attempt certification:** a plausible dispatch reconciliation certificate and independent physical-attempt control count are exported inside shipments. Missing or inconsistent certification excludes first-attempt rate only, unless separate lifecycle/terminal defects affect other KPIs.
3. **Conservative unresolved evidence:** malformed terminal messages and genuinely conflicting outcomes leave affected results Unknown; contradictory identity/version evidence is quarantined without fallback. Conflicting primary locations produce Unknown geography while preserving one customer row.

**Remaining genuine blockers:** none to the Step 2 documentation baseline. This is a design-consistency conclusion, not execution evidence. Runtime/tooling selection and SQL Server validation belong to subsequent implementation work; no datasets, code, or warehouse execution have been produced in this step.

### Step 2 change log — 25 September 2026

| Classification | Change |
| --- | --- |
| Clarification | Fixed exact source fields, keys, categories, CSV conventions, required/optional values, and source-row versus reporting exclusions; preserved six sources/one fact/four dimensions and all eight business questions. |
| Clarification | Distinguished immutable event messages from physical attempts; specified duplicate/version ties, primary-location selection, cancellation timing, and explicit unknown outcomes. |
| Substantive decision | Chose restated occurrence-time reporting using evidence available at export time, with T and E recorded separately. |
| Substantive decision | Added source certification/watermark/control count to make complete attempt history observable; transformation cannot infer completeness from observed counts alone. |
| Planning decision | Fixed six-month dates, fictional labels, service targets, seed streams, learning-sized counts, controlled anomalies, and independent fixture expectations. These specify later generation, not completed data. |
| Reference alignment | Read the supplied audit, recorded deliberate scope departures, and made editable diagrams/failure-recovery evidence explicit. No financial fields, cloud services, or broader scale were added. |
| Baseline finalization | Marked all four documents Step 2 — implementation baseline, version 1.0; removed unresolved-review wording; synchronized versioned source grain, fixed dates, geography/services, current masters, and disposition equations. |
| Fixture recount | Independently confirmed the listed per-shipment message counts sum to 14, not 16: 11 attempt messages collapse to 10 attempts, plus 3 non-attempt messages. All fixture KPI totals were rechecked and retained; no extra events were invented. |
| Cutoff clarification | Post-T events never become T outcomes/attempts/coverage; evidence through W > T can affect certificate trust and first-attempt eligibility. Acceptance criteria and edge-case expectations distinguish these effects. |
| Contract consistency | Allowed the trailing separator needed for a null final CSV field while retaining exact field counts; removed ambiguous highest-valid-version wording that could imply fallback; narrowed the invalid-date fixture expectation to the affected field's actual Q/AF rule. No fields or business features were added. |

Step 2 stops at these reviewable documents. Generation, source profiling, warehouse-column design, SQL, and execution remain subsequent authorized tasks.
