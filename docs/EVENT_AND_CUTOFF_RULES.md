# Parcel warehouse — event, cutoff, and reconciliation rules

**Status:** Step 2 — implementation baseline. **Rules version:** `1.0`. This document specifies behavior; it contains no implementation. Field definitions are in [SOURCE_CONTRACTS.md](SOURCE_CONTRACTS.md). Fixed dates and test cases are in [DATA_GENERATION_PLAN.md](DATA_GENERATION_PLAN.md).

## Policy and input precedence

The approved project specification and Step 2 defaults govern this parcel design. The earlier reference audit informed validation and documentation practices but is not a dependency of this public package. Its proposals do not replace the approved parcel fields, scale, or all-due confirmed-on-time denominator. Tolerant parsing, explicit rejects, deterministic resolution, failure propagation, and reconciliation remain part of the parcel rules below.

## Reporting cutoff: restated occurrence-time outcomes

**Chosen version-one policy: use all supplied evidence available by the export time to restate outcomes that occurred on or before the reporting cutoff.** We do not reconstruct what staff knew at that earlier moment.

Two fixed run settings are required:

- `reporting_cutoff_utc` (T): `2026-06-28T23:59:59Z`, the business instant being reported, inclusive to the second.
- `extract_as_of_utc` (E): `2026-07-07T23:59:59Z`, latest source availability/commit time included in the complete six-file export, with E ≥ T.

`occurred_at_utc` is physical event time; `recorded_at_utc` is arrival/first persistence at the central tracking system. Warehouse load time is technical provenance only. For a valid event, occurrence ≤ recording ≤ E. An event occurring ≤ T is eligible temporal evidence even if recorded > T. Events occurring > T are valid future evidence, excluded from T outcomes/attempt counts. They may support certificate validation over a longer declared window, as explained below. No event recorded after E belongs to this extract; such a row is a contract defect, not merely a future-to-report event.

Example using the planned cutoff: delivery occurred 28 June at 18:00, arrived centrally 2 July, and is included in the 7 July extract. It can count as delivered at the 28 June end-of-day cutoff (and on time if no later than its promise). A delivery occurring 29 June cannot count at that cutoff even if present in the same export. Later extracts can change the restated June answer. Identical files, contracts, T, and E must reproduce the same answer.

Use selected current booking/master versions as of E. Current dimension attributes may therefore be newer than T; this is intentional, with no SCD2 or historical attribute reconstruction. A valid booking after T remains accepted in Silver but has no Gold row at T. For an included booking, handover/cancellation after T are future milestones, not current facts. Promise may be future and remains stored as the agreed deadline. Invalid actual timestamps beyond E are flagged as unusable; promises beyond E are allowed.

The fact is a **restated snapshot** at T. It is not a stored series of past daily snapshots. Business outputs must label T, E, and this restatement policy. Late-arrival counts are quality context; recorded time is never substituted for delivery time.

## A. Physical delivery attempts

An **event ID** identifies a message. A **physical attempt ID** identifies one real visit/attempt to deliver one parcel. Dispatch assigns `attempt_sequence` starting at 1. Two messages can share an attempt ID without representing two visits.

1. Resolve duplicate event IDs and malformed rows using the source-row rules below. Keep every distinct valid event as evidence.
2. For attempt evidence, group by `(shipment_id, physical_attempt_id)`. All messages for that attempt must agree on `attempt_sequence`, physical completion time (`occurred_at_utc`), and result (`DELIVERY_FAILED` or `DELIVERY_SUCCEEDED`). Recording times, message IDs, and optional event depot can differ. Conflicting scan-depot metadata is a quality flag, not a second attempt or an outcome chooser.
3. If the required physical facts agree, collapse them to **one** canonical attempt. A canonical attempt is the single summary used for measurement; retain links to all contributing messages. A successful attempt also supplies delivery evidence; do not add a second synthetic attempt because the event is terminal.
4. If the result, time, or sequence differs, that attempt is unresolved. Keep all otherwise well-formed messages as accepted conflict evidence; do not favor SUCCESS, latest arrival, largest ID, or file order. Exclude that shipment from first-attempt rate. If any conflicting result asserts success, the shipment's terminal outcome is Unknown at any cutoff the conflict could affect.
5. A physical attempt ID reused on different shipments is a conflict for every affected shipment. Two attempt IDs with the same sequence on one shipment are also a conflict. Distinct attempts must have strictly increasing completion times as sequence increases. Two different attempts at the same second cannot be ordered defensibly and fail attempt-history eligibility.
6. A known gap, such as sequences 1 and 3, is incomplete history. Sequence 2 alone is not the first attempt. Out-of-order arrival is harmless if physical sequence/times are consistent.

**Scope of holds:** independent, parseable events occurring after T do not retroactively change a coherent T terminal outcome. Conflicting messages about the same event or physical attempt can leave its occurrence uncertain: if one claim is at/before T, or its time is unusable, that entity may affect T and creates a hold. This is uncertainty about a potentially pre-T entity, not counting a distinct future outcome in the past. A hold means “do not assert this derived value”; it does not delete an accepted shipment. Certificate validity is separately evaluated over its watermark window, so a wider-window defect can invalidate that certificate without altering a T terminal outcome.

Repeated confirmations of delivery on the same successful attempt collapse to one attempt and one delivery. Successful delivery on two different physical attempts is incompatible with one parcel's mutually exclusive terminal lifecycle; the terminal outcome becomes Unknown. There is no “latest status wins” rule.

## B. Evidence of complete attempt history

**Plausible source mechanism:** the carrier's dispatch platform maintains a per-shipment attempt ledger and acknowledges synchronization from driver devices. A reconciliation job checks the device/dispatch ledger against centrally persisted attempt messages, including offline work and pending messages. It writes a certification into the booking export only when it can attest that every physical attempt from booking through a specific time is synchronized. This is an operational assertion, comparable to an export-completeness watermark; a watermark is a time through which a process claims it has finished.

The evidence is in the existing `shipments.csv`, not a seventh feed:

- `attempt_history_status`: COMPLETE, PARTIAL, or UNAVAILABLE.
- `attempt_history_through_at_utc` (W): inclusive end of the continuous certified window, starting at `booked_at_utc`.
- `attempt_history_attempt_count` (C): physical attempts the independent dispatch ledger reconciled in that window; required only for COMPLETE, including explicit 0.
- `attempt_history_attested_at_utc`: when reconciliation was performed, W ≤ attestation ≤ booking-version commit ≤ E.

This certifies the **attempt log**, not every possible non-attempt event. The count is an operational control total, not a warehouse KPI. The generator later simulates both the dispatch ledger and its exported certificate; transformation logic reads only the six exports. It never reads the hidden scenario labels or expected-results manifest. Synthetic assertions demonstrate a contract, not proof that a real carrier would meet it.

First-attempt measurement eligibility requires **all** of the following:

1. Shipment is in H (valid actual handover by T and no cancellation/lifecycle-start uncertainty).
2. COMPLETE certificate is well-formed, booking ≤ W ≤ attestation ≤ commit ≤ E, and W ≥ T. PARTIAL/UNAVAILABLE, missing fields, a count without a certificate, or W < T is insufficient.
3. Use all supplied valid attempt evidence with occurrence in `[booking, W]`, not just events recorded by T. After collapsing scans, exactly C physical attempts exist, with sequences exactly 1…C, unique IDs, strictly increasing physical times, and consistent outcomes. All are at/after handover. At least one message supporting each canonical attempt was recorded by attestation; a certificate cannot attest to a result that only arrived later. Later redundant agreeing messages do not invalidate it.
4. No unresolved malformed/quarantined attempt evidence, unknown-type event, duplicate-ID conflict, or attempt conflict that could belong in that certificate window. An exact duplicate is harmless. A quarantined terminal record also blocks first-attempt eligibility conservatively if it could change the lifecycle. Unidentifiable-parent records remain global quality issues; never attach them to an invented shipment.
5. No terminal/lifecycle conflict affecting the shipment at T. At least one canonical physical attempt occurred ≤ T. A fully certified zero-attempt history is valid evidence but supplies no first-attempt denominator row.

Then the first attempt is sequence 1, and success means its result is DELIVERY_SUCCEEDED. A failed first attempt followed by success remains one denominator shipment and zero numerator shipments. A known failed attempt on a still-open shipment is also in the denominator if certification is valid.

Default generation uses W = T and attestation after T. The contract permits W > T: validate the entire certificate through W, then count only attempts occurring ≤ T for the KPI. Later attempts never enter the cutoff's attempt count, terminal outcome, or tracking coverage. A wider certificate that cannot be validated is unusable; record that exclusion rather than silently shortening its promise. Its evidence can therefore change first-attempt KPI eligibility (and the resulting numerator/denominator) without adding any post-T attempt or outcome to T. This distinction between certificate trust and event occurrence is deliberate.

Example: one successful message, sequence 1, but no COMPLETE certificate → delivery may be confirmed and transit/on-time calculated; first-attempt eligibility is **false**. COMPLETE with C=2 but only one attempt in the export → incomplete export evidence, not first-attempt success. COMPLETE with C=1, the corresponding trustworthy attempt, and W ≥ T → eligible.

## C–D. Lifecycle, malformed evidence, and ties

Parsing errors and business contradictions have different meanings. **Malformed event:** its required values cannot describe a valid message; quarantine that row. **Business conflict:** individually well-formed messages make incompatible claims; retain them with conflict flags so the contradiction remains visible.

Apply the following rules at T after version resolution. State names below are warehouse derivations, never source status fields.

| Evidence | Treatment / cutoff consequence |
| --- | --- |
| Cancellation at/after booking, by T, no actual handover anywhere in the selected booking | CANCELLED_PRE_HANDOVER. Retain fact; exclude H and D. |
| Cancellation and handover both by T | Unknown lifecycle start, regardless of their order or equality. Even a handover after cancellation violates an unreversed cancellation. Exclude H/D and flag the contradiction. |
| Cancellation after handover but after T | Report full-extract defect; at T ignore that future cancellation and evaluate the earlier valid handover. Future-to-T contradictions do not silently change past handover eligibility. |
| Cancellation by T, handover after T | At T classify cancelled and outside H; flag the whole-extract contradiction. |
| Nonnull unparseable cancellation | Timing cannot be placed relative to T; lifecycle-start hold, exclude H/D, retain fact. |
| Cancellation before booking or after its source commit/E | Invalid source chronology; retain flagged shipment with lifecycle-start hold. Do not treat an impossible future timestamp as a valid future cancellation. |
| Invalid/missing handover | Retain fact; invalid and legitimate missing are separate reasons. Exclude H/D. Do not infer handover from scans. |
| Valid handover by T, no valid terminal evidence and no terminal hold | OPEN (no confirmed terminal outcome). Includes no scans. If promise < T, it is open overdue backlog. |
| Exactly one terminal kind, coherent evidence | DELIVERED, RETURNED_TO_SENDER, or LOST. Earliest valid occurrence of that terminal kind is its completion time. Repeated agreeing confirmations do not create new terminal outcomes. |
| More than one terminal kind at/before T | Unknown outcome. Retain H and D if start/deadline are valid; no terminal KPI numerator or transit/backlog eligibility. Arrival order never resolves this conflict. |
| Success mixed with failure on the same attempt; different successful attempts; a later physical attempt after terminal completion; progress scan strictly after terminal completion by T | Unknown terminal/lifecycle outcome, with accepted evidence and conflict flag. Repeated same-kind terminal confirmations are allowed; post-delivery returns/attempts are not modeled. |
| Invalid/missing terminal occurrence time, or pre-handover terminal scan | Q event, retain identifiable parent's terminal hold. Do not convert it into an apparently open shipment. Its delivery time and terminal outcome remain Unknown. |
| Pre-handover nonterminal scan | Q event. A malformed attempt blocks certificate eligibility; an ordinary progress scan alone does not make terminal outcome unknown. No tracking-coverage contribution. |
| Scan before booking, including when handover is missing | Q event for impossible parent chronology. Apply a terminal/certificate hold if the event type requires it. |
| Unknown event type or malformed duplicate-ID variants with identifiable parent | Q event. Potentially conceals a terminal/attempt claim: terminal and certificate holds if it could affect the relevant window. |
| Tied events | Same attempt + same required facts → collapse. Different terminal kinds at the same second → conflict. Different attempts at the same second → invalid physical ordering/conflict. Progress and coherent terminal scan at the same second may coexist; no artificial within-second ordering is asserted. |
| Late recording with valid occurrence ≤ T | Accept for restated outcome; mark late arrival where recorded > T. Does not by itself create conflict. |
| Valid occurrence > T | Accept in Silver, temporally outside T; not bad data and not a cutoff outcome. |

For a quarantined record, use only its parseable shipment ID/type/time to assign a **quality hold**, never to assert a successful outcome or metric. If occurrence is parseable and strictly outside the relevant window, it causes no hold in that window. An invalid occurrence cannot be proven outside, so hold conservatively. Missing/invalid shipment ID cannot be attributed; preserve it in global Q totals. If one conflicting event ID names multiple identifiable shipments, hold each plausible affected parent, without counting the event as accepted evidence.

An accepted event can be a valid scan for **tracking coverage** even when it participates in a genuine terminal conflict. Coverage asks whether usable tracking exists, not whether the outcome is trustworthy. Quarantined events and events with no valid handover never count. A valid scan at exactly handover or cutoff counts. Repeated agreeing messages are multiple valid source events but only one shipment toward the coverage numerator.

Maintain independent flags for terminal uncertainty, attempt-history uncertainty, and lifecycle-start uncertainty; they are not interchangeable. A gap or missing certificate alone does not undo coherent delivery evidence. An attempt-identity/time/sequence contradiction involving a success message does make that delivery uncertain; a conflict limited to failed attempts blocks first-attempt measurement without automatically overturning a separate coherent terminal outcome. Required-field parsing defects are evaluated before these integration rules.

## Customer primary location

1. Resolve each location ID to its current version as of E; customer ID ownership cannot change.
2. Retain exactly one resolved row per customer ID independently of location data. Ignore no customer merely because its location is absent.
3. Candidates are accepted locations for that customer with `is_active=true` and `is_primary=true`. Inactive/nonprimary sites cannot win by recency.
4. Exactly one candidate and no selection hold → select it. Copy available city/region; missing descriptive values remain Unknown with a flag.
5. Zero candidates → Unknown geography, `NO_PRIMARY_LOCATION`. Multiple candidates → Unknown geography, `MULTIPLE_PRIMARY_LOCATIONS`; retain all site evidence for review, choose none. Do not use latest update, alphabetic location name, or smallest ID as a substitute for an explicit designation.
6. Quarantined/ambiguous location versions for an identifiable customer cause a selection hold if any variant is primary or its primary/active markers cannot be trusted. If every available variant clearly asserts nonprimary, it does not block an otherwise unique candidate. An inactive-primary contradiction itself causes a selection hold, even though the row is not a candidate. Unknown parent means global quality reporting only.

Produce one enrichment result per customer before joining `dim_customers`. Location counts do not multiply customer or shipment rows. Current location at E is intentional; there is no as-of-booking location history.

## Deterministic resolution and row accounting

### Resolution order

All six files use the following precedence. Its purpose is both repeatability and a truthful count of every source row.

1. Validate whole-file structure. On failure, fail the batch before analytical publication; record file/failure details. No completed row reconciliation is claimed for unparseable boundaries.
2. Assign provenance to every parseable row. Identify exact duplicates by equality of all parsed field strings (quoting differences alone do not change a parsed field). Preserve the lowest logical row number as the representative **only for identical content**; extra identical copies have disposition DUPLICATE_EXACT. This is provenance selection, not a business winner. It applies even if the representative will later be Q.
3. Normalize allowed whitespace and validate usable business keys/order metadata. Missing business key → Q representative. A usable key with any invalid version/commit metadata blocks reliable ordering: Q all its remaining distinct representatives. Do not fall back to an apparently valid old version.
4. For the five versioned sources: compare versions by the integer `record_version`. Same-version rows with identical normalized payload are equivalent copies; retain one provenance representative and classify others DUPLICATE_EQUIVALENT. If payloads differ at the same version, Q all remaining distinct versions for that key. As version rises, commit times must not move backwards; two revisions committed within the same second can share a timestamp because the source-issued version supplies their order. Inconsistent ordering or forbidden immutable changes Q the key group. Compare immutable business values only when both values are individually valid; an unparseable business timestamp is evaluated as a candidate-content defect in step 6, not an invented historical timestamp. No requirement to receive every intervening version. For a coherent group select its maximum version. Earlier versions are DUPLICATE_SUPERSEDED, not current analytical records. Versions are source ordering evidence, not SCD2 dimension history.
5. Tracking events have no revision order. Same event ID with differing normalized payload (including different recorded time) → Q all its remaining distinct variants. Agreeing normalized repeats are DUPLICATE_EQUIVALENT. Different message IDs for the same attempt remain accepted messages, subsequently aggregated, not duplicate source rows.
6. Validate the selected candidate's remaining content and references. Apply the source-specific Q/AF rules. If the selected candidate fails a hard rule, it is Q; earlier versions remain superseded and are not revived. Resolve customer/service/depot entities before their dependants; resolve shipments before events. A missing event parent includes a parent quarantined during shipment resolution. A master missing from Gold maps a usable fact FK to Unknown; no fact is lost through an inner join.
7. Assign final source-row dispositions and reasons. Accepted with flags is a subset of accepted, not a second record. Business-level conflicts between accepted rows are retained as quality evidence. Then apply reporting/certificate/KPI scope filters. Never overwrite a source disposition with a cutoff exclusion.

If multiple Q reasons apply, store all reasons and one primary reason using a fixed priority: identity → ordering/duplicate conflict → required value/type → intrinsic chronology → parent/reference. For reporting exclusions use the priorities below. Reason totals can overlap; distinct-row totals cannot.

### Source-row equations

For each successfully parsed file:

**R = AC + AF + DX + DE + DS + Q**

- R = raw data rows excluding the header.
- AC = accepted current representatives without a quality flag.
- AF = accepted current representatives with one or more flags.
- DX = exact extra copies; DE = normalized-equivalent extra copies; DS = coherent superseded versions (zero for immutable tracking events).
- Q = quarantined representatives, including distinct conflicting variants. Each raw row has exactly one final disposition.

Let **S = AC + AF** and **DUP = DX + DE + DS**. Thus **R = S + DUP + Q**. Attempt collapse is not part of DUP: source messages that describe the same physical attempt remain in S.

Then split accepted shipments: **S_shipments = F + B_future**, where F is fact rows (valid booking ≤ T) and B_future is valid booking > T. Hence **R_shipments = F + B_future + DUP_shipments + Q_shipments**. Do not add B_future to S again.

Accepted events have valid occurrence/parent but can be flagged for unavailable handover. Split: **S_events = EV_at_or_before_T + EV_after_T**. Split the first bucket again: **EV_at_or_before_T = EV_usable + EV_no_valid_handover**. Intrinsically invalid/pre-handover rows are already Q; do not count them in either accepted bucket. Genuine outcome conflicts can be in EV_usable for coverage while blocked for outcome inference. Events after T stay accepted even if recorded after T. A child of an accepted future booking remains a valid parent relationship; its chronology normally also places it after T.

Master/location sources are current reference snapshots at E, not a T-filtered transaction population. For them **S = current accepted entities**, with no artificial outside-cutoff bucket based on modification time. All raw-row equations still apply.

### Reporting-population equations

These equations count distinct shipments, not source rows or events. Each partition is mutually exclusive using the stated precedence.

**F = H + X_start_conflict + X_cancelled + X_invalid_handover + X_no_handover + X_future_handover**.

Apply start/cancellation uncertainty first; next valid cancellation by T; next invalid nonnull handover; next legitimate null; next valid handover > T; otherwise H. Booking is already valid. Full-extract contradictions entirely after T are separate flags. A handover before booking is invalid, not H. Cancellation after T alone does not exclude an otherwise eligible handover at T.

Handover after its own source commit (or E) is also invalid, even if it parses. An invalid nonnull handover alongside cancellation is start uncertainty, not a proven cancellation-only record. For a valid cancellation by T and a valid handover after T, the specific cancelled-at-T rule takes precedence over the full-extract coexistence flag.

**H = D + X_promise_unusable + X_promise_future**. Unusable means missing/unparseable or earlier than booking/handover. Future means valid promise > T. D includes equality with T. Unknown outcome does not move a shipment out of D.

**D = ON_TIME + DELIVERED_LATE + OPEN_DUE + RETURNED + LOST + OUTCOME_UNKNOWN**.

ON_TIME has coherent successful delivery ≤ promise; DELIVERED_LATE has coherent delivery > promise but ≤ T. All other buckets are mutually exclusive derived outcomes. Confirmed on-time numerator is ON_TIME, denominator D; customer not-confirmed-on-time count is D − ON_TIME. **OPEN_DUE = OPEN_OVERDUE + OPEN_AT_DEADLINE**, with strict promise < T for backlog; a due shipment exactly at its deadline is not yet overdue.

**H = TRANSIT_ELIGIBLE + TRANSIT_EXCLUDED**. Delivered, coherent, nonnegative handover-to-delivery time establishes transit eligibility, regardless of whether promise is missing/future. Average divides total eligible hours by eligible count, not by D.

**H = FA_ELIGIBLE + FA_X_LIFECYCLE + FA_X_EVIDENCE + FA_X_UNCERTIFIED + FA_X_NO_ATTEMPT**. Priority: conflicting lifecycle → malformed/conflicting attempt/terminal evidence (including gaps) → missing/partial/stale/count-mismatched certificate → zero attempts by T → eligible. Distinguish subreasons, but assign one primary exclusion bucket. Valid count-matched certification is required even for a known first success. **FA_ELIGIBLE = FA_SUCCESS + FA_FAILURE**.

**H = TRACKED + UNTRACKED**. TRACKED has at least one EV_usable; UNTRACKED has none. Coverage = TRACKED / H. Unknown outcome is allowed in TRACKED. Percentage KPIs multiply ratios by 100; zero denominator means N/A. Transit/backlog hours use exact seconds divided by 3,600, rounded to two decimals only for presentation.

Every result also applies a documented selected cohort (handover or promised date from the specification) to numerator and denominator together. Test fixture totals use all included dates. Unknown-dimension groups remain visible, not filtered from denominators. A date-dimension role never changes which milestone defines eligibility.

## Acceptance and scope guard

Later tests must prove each equation, verify both business-key uniqueness and join row preservation, and compare known-answer outcomes. A duplicate-generated surrogate key test alone is insufficient. Automated runs must propagate failure, record stage/counts, and demonstrate recovery/rerun behavior before claiming reliability, following the audit's lessons.

**Cutoff acceptance:** test a wider certificate W > T with a failed first attempt by T and successful second attempt after T. When its full evidence is valid, first-attempt result at T is failure, the shipment is not delivered at T, and its T attempt count is one. Removing or contradicting the later certificate evidence must invalidate certification and exclude that shipment from first-attempt measurement, while still not turning the later success into a T outcome. This is not a requirement that all KPI values remain unchanged when certificate trust changes.

This step adds no event fact, location dimension, business source, dashboard, cloud service, financial measure, or implementation. In Gold there remains one shipment fact, customers/services/depots/date dimensions, two depot roles, and four date roles. The more detailed rules explain eligibility and evidence; they do not enlarge the business domain.
