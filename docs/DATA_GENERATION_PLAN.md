# Parcel warehouse — reproducible data generation plan

**Status:** Step 2 — implementation baseline. **Plan version:** `1.0`. No CSVs, generator, SQL, or expected-results file has been created. All planned data is **SYNTHETIC — FOR LEARNING ONLY**. Contracts and rules, not hidden scenario labels, govern later transformations.

## Fixed dates, size, and fictional setting

| Setting | Planned value |
| --- | --- |
| Booking date range | `2026-01-01T00:00:00Z` through `2026-06-30T23:59:59Z` inclusive: exactly six calendar months. |
| Reporting cutoff T | `2026-06-28T23:59:59Z`, inclusive. Bookings on 29–30 June demonstrate valid outside-cutoff rows. |
| Export availability E | `2026-07-07T23:59:59Z`. Supplies late arrivals and post-cutoff events for restatement tests. |
| Customer/reference setup | Accounts and initial depot/service definitions established in December 2025, before January bookings. Later valid master updates may occur through E. |
| Shipment identities | Exactly 10,000 baseline business IDs before anomaly injection; no extra business identities added by duplicate/version injections. |
| Customers | 100 baseline accounts: 60 SMALL, 30 MIDMARKET, 10 ENTERPRISE. |
| Customer locations | 140 baseline sites: 60 customers have one, 40 have two; exactly one active primary per customer in the clean baseline. Nonprimary sites are active. |
| Depots | 8, with two per fictional region. |
| Services | ECONOMY 72 hours, STANDARD 48 hours, EXPRESS 24 hours; 2,500 / 5,000 / 2,500 baseline shipment assignments. |
| Expected event volume | 40,000–65,000 clean event messages; controlled additions/replacements are counted separately. This is a validation range, not a claimed measured result. |
| Certificate window | Default W = T for bookings ≤ T; COMPLETE certificates attested `2026-07-07T12:00:00Z` after relevant attempt messages arrive. Future bookings use UNAVAILABLE. |

The network is a fictional domestic territory, **Lumenland**. Region codes are NORTH_REACH, EAST_VALE, SOUTH_MERE, WEST_FEN; no country field is needed in a one-country project. Depot names/towns are: DEP01 Lumenford Hub; DEP02 Brindle Quay Depot; DEP03 Alderwick Hub; DEP04 Mistral Hollow Depot; DEP05 Sunmere Hub; DEP06 Reedhaven Depot; DEP07 Ferncross Hub; DEP08 Mossgate Depot. Assign consecutive pairs to the four regions above. Businesses and sites use invented names, with no real addresses, emails, phone numbers, or people.

Use at least two regions for each customer segment and send each service through multiple depot pairs. Both local and inter-depot shipments are allowed. Do not intentionally make a named depot universally worst; comparisons demonstrate descriptive analysis, not asserted causation. Nominal service targets inform promise generation, but recorded shipment promises are the only reporting authority.

## Clean lifecycle allocation before anomalies

The following disjoint allocations prevent an all-delivered or mostly-not-yet-due demonstration. They are generator design targets, not transformation logic.

| Population at T | Baseline shipments | Planned evidence |
| --- | ---: | --- |
| Due, delivered on time | 7,000 | Valid handover and successful physical attempt no later than promise. Include exact-deadline cases. |
| Due, delivered late | 1,000 | Successful delivery after promise but by T. |
| Due, open overdue | 650 | Valid handover/promise; no terminal event by T. Include never-attempted and failed-attempt cases. |
| Due, returned to sender | 250 | Explicit terminal return, with or without prior failed attempts. |
| Due, lost | 100 | Explicit lost event; do not infer loss from age. |
| Handed over, future due | 300 | Valid promise > T; open at T. Later valid delivery events may occur by E. |
| Booked, not handed over | 200 | Legitimate null handover, no cancellation or physical tracking yet at T. Some may hand over after T in the supplied snapshot. |
| Cancelled before handover | 300 | Booking ≤ cancellation ≤ T; handover null; no post-handover tracking. |
| Booked after T | 200 | Valid bookings on 29–30 June, retained in Silver, no fact row at T. Some have events by E. |
| **Total** | **10,000** | **9,800 in-cutoff facts; H = 9,300; D = 9,000 before anomalies.** |

Baseline D therefore has on-time rate 7,000/9,000 = 77.78%, return rate 250/9,000 = 2.78%, and loss rate 100/9,000 = 1.11%. These are design values for the clean baseline, not promised dirty-data results. Generate durations and attempts first, then validate that actual evidence meets the assigned scenario. Every entity/time assignment must satisfy its source contract; do not merely label a scenario while generating contradictory timestamps.

Distribute January–May bookings and June bookings so the mature due population reaches the counts above. Reserve the final days for future-due/unhanded-over/future-booking cases. Use selected June dates for most open overdue cases rather than making hundreds of January shipments silently open for six months. Events may occur after the six-month booking window but no later than E, and recording delays must still fit E.

For eligible delivered shipments, use one to three physical attempts, with a successful final attempt. Repeated agreeing scan messages may describe one attempt. Generate ordinary IN_TRANSIT/OUT_FOR_DELIVERY messages separately; neither creates a physical attempt. Include approximately 3% late-arriving pre-cutoff messages (recorded > T), with exact counts recorded later. Protect certificate attestation order. Keep 100 handed-over/open shipments with no tracking messages to make coverage meaningful; this is missing evidence, not a malformed CSV row.

## Reproducible configuration and generation procedure

- Master seed: **20260925**. Planned generator/config versions: `parcel-generator-1.0` and `parcel-config-1.0`. Pin the exact runtime and random-number algorithm in the eventual generator release and manifest; proposed runtime family is Python 3.12 with its explicitly selected MT19937 generator. Record the actual patch release used before any baseline is accepted.
- Use independent fixed random streams: entities 20260926, bookings 20260927, lifecycles 20260928, recording delays 20260929, anomaly placement 20260930. This keeps a change to one generation stage from silently shifting unrelated entities.
- Configuration explicitly fixes all dates, counts, category mappings, quotas, permitted durations, anomaly placements, and output formatting. No current-clock defaults, unseeded randomness, OS-dependent ordering, or locale-dependent timestamps.
- Allocate stable IDs in sorted business order; sort exported rows by business key/version (event ID for messages), with a fixed order for injected duplicates. UTF-8/LF/whole-second conventions come from the source contract. Record checksums and row counts for each file.
- Build a coherent clean source model first. For certification, simulate the dispatch ledger's continuous synchronization acknowledgement and independent control count. Then serialize the six exports and inject controlled export defects. Do not invent COMPLETE merely by counting whatever event rows survive export.
- Anomaly injection uses reserved, disjoint business IDs per anomaly family unless a case explicitly tests interaction. Apply injections in a fixed documented order. Preserve the clean baseline checksum set separately from the dirty demonstration checksum set.
- Keep run configuration and expected results as technical metadata. The transformation reads only the six CSVs plus allowed run settings T/E/input paths; it must not read expected outcomes, scenario IDs, injected-defect labels, or generator internals.

The generator will later fail its own build if it misses counts, time bounds, or scenario prerequisites. It must not “correct” a known-answer expectation to whatever the transformation happens to produce.

## Controlled anomalies and intended dispositions

AC = accepted clean; AF = accepted with quality flag; Q = quarantined representative; DX/DE/DS = exact/equivalent/superseded duplicate dispositions. Raw duplicates can coexist with a flagged or quarantined representative, but each raw row has one disposition. See the reconciliation equations in the event rules.

| Case | Main demonstration quota / fixture coverage | Expected behavior |
| --- | --- | --- |
| Exact repeated rows | Add 20 shipment, 100 event, 4 customer, 4 location, 2 depot, 2 service rows | 132 DX; accepted entities/physical attempts/KPIs unchanged. |
| Older coherent source versions | Add 10 shipment, 3 customer, 3 location, 1 depot, 1 service rows on separate IDs | 18 DS; latest defensible version remains current. Booking versions differ only in permitted operational/certificate fields. |
| Missing required booking timestamp | Replace 5 in-cutoff shipment rows | 5 shipment Q; their child events Q for unresolved parent. Record the exact dependent-event count in the manifest. Fact count falls by 5; no hidden replacement booking. |
| Missing promise | Replace 10 distinct handed-over due booking values | 10 AF; retain fact/H, move from D to unusable-promise exclusion. Do not repair from service level. |
| Missing actual handover | Replace 10 distinct handed-over booking values | 10 AF with tracking-without-handover context where events exist; no H/D eligibility. Accepted events retained with temporal-context flag, no valid coverage contribution. |
| Missing terminal occurrence | Replace 6 successful-message timestamps on separate shipments | 6 event Q, identifiable-parent terminal/certificate hold; fact stays in D but cannot be on time, delivered/transit eligible, or open backlog. |
| Unknown event parent | Replace 4 event shipment references with unused valid IDs | 4 event Q; global orphan count. Where the original shipment loses evidence, its independent certificate count detects a missing attempt if applicable; no invented parent match. |
| Additional incompatible terminal outcome | Add LOST to 5 previously delivered due shipments, occurrence by T | 5 accepted conflict messages; affected terminal outcomes Unknown, D retained, first-attempt eligibility blocked. |
| Partial/no certificate despite successful scan | Change 10 delivered booking certificates coherently to UNAVAILABLE | 10 AF for measurement limitation; delivery/on-time/transit still usable; first-attempt excluded. |
| Certificate control-count mismatch | Change 5 COMPLETE certificate counts on separate attempted shipments | 5 AF; first-attempt excluded, no change to otherwise coherent delivery outcomes. |
| Multiple active primary sites | Change 2 existing nonprimary sites on two multi-site customers to primary | Source rows retained AF at integration; customer geography Unknown, one customer row preserved. No new site added. |
| Shipment customer FK unresolved | Change 3 shipment customer references to unused valid IDs | 3 AF; Unknown customer group, fact/H/D eligibility otherwise unchanged. |
| Whitespace normalization | Alter 6 descriptive-name fields on separate master rows | 6 AF for normalization; no join/measurement change. |

Main demonstration still has exactly 10,000 intended shipment identities, 100 customer identities, 140 sites, 8 depots, and 3 services; raw row counts include duplicates/versions. Accepted fact count is **9,795** after the five invalid in-cutoff bookings; other listed shipment defects retain the fact. Large-data H/D/numerators are recalculated in the independent manifest after disjoint anomaly placement; baseline percentages above must not be reused as dirty expected results.

Additional defects below belong in small isolated fixture variants so a learning dataset is not overwhelmed. They have fixed expected outcomes, not random frequencies. File-structure failures are separate batch tests, not mixed into the normal successful demonstration.

## Small known-answer fixture: fourteen shipment identities

This is a **fixture design**, not CSV data. F01…F14 are test labels that map later to `SHP900000001`…`SHP900000014`; labels do not become business-source columns. Use T from above; common booking `2026-06-26T08:00:00Z`, handover H0 = `2026-06-26T23:59:59Z`, and promise P0 = `2026-06-27T23:59:59Z` (24 hours after H0). T is 48 hours after H0, so an open P0 shipment is 24 hours overdue.

All ordinary fixture messages are recorded one hour after their occurrence unless stated otherwise, and all version commits/certificate attestations occur by E. COMPLETE certificates have W=T and attestation `2026-07-07T12:00:00Z`. “C=n” is certified attempt count; every attempt has its own ID and source sequence. Progress messages are deliberately absent here to make coverage counts easy to verify.

| Label | Evidence / exception to shared defaults | Expected result at T |
| --- | --- | --- |
| F01 | Success at H0+12h; sequence 1; COMPLETE C=1; message recorded on 2 July (late arrival) | On time, transit 12h, first-attempt success, tracked. |
| F02 | Two agreeing messages with different event IDs for the same successful attempt at H0+24h; COMPLETE C=1 | Exact-deadline on time, one attempt, transit 24h, first success, tracked. |
| F03 | Success at H0+36h; COMPLETE C=1 | Delivered late, transit 36h, first success, tracked. |
| F04 | Failed attempt at H0+6h; COMPLETE C=1; no terminal | Open overdue, age 24h, first-attempt failure, tracked. |
| F05 | Failed attempt at H0+6h; returned at H0+20h; COMPLETE C=1 | Returned, first-attempt failure, tracked, no transit average contribution. |
| F06 | Lost at H0+20h; COMPLETE C=0 | Lost, no attempt denominator, tracked. |
| F07 | Failed sequence 1 at H0+6h, success sequence 2 at H0+18h; COMPLETE C=2 | On time, transit 18h, first-attempt failure, tracked. |
| F08 | No events; COMPLETE C=0 | Open overdue, age 24h, no attempt denominator, untracked. |
| F09 | Success at H0+12h and LOST at H0+20h; COMPLETE C=1 | Unknown conflicting terminal outcome; remains due/tracked, excluded from transit/backlog/first-attempt numerator and denominator. |
| F10 | Promise H0+72h; no events; COMPLETE C=0 | Open future due, H but not D, no overdue backlog, untracked. |
| F11 | Promise missing; success H0+12h; COMPLETE C=1 | AF, H but not D; transit 12h, first success, tracked. |
| F12 | Handover legitimately null; no events; UNAVAILABLE | Fact retained, not H/D, no transit/attempt/coverage denominator. |
| F13 | Cancellation `2026-06-26T10:00:00Z`; handover null; no events; UNAVAILABLE | Cancelled before handover; fact retained, not H/D. |
| F14 | Single success H0+12h; UNAVAILABLE certificate | On time, transit 12h, tracked; first-attempt eligibility false. |

Provide two customers (F07/F11 use CUS0002, others CUS0001), three depots (origin DEP01; F03/F07/F09 destination DEP03, others DEP02), and all three services (F10 ECONOMY, F11 STANDARD, others EXPRESS). Both customers have one valid active primary site. Supply all six source files even when small. These assignments allow service/customer/depot joins and UTC daily/weekly/monthly grouping to be verified without extra sources. Missing F11 promise is intentionally invalid, not silently created from STANDARD's nominal target.

**Hand-checkable fixture totals:**

- Accepted fact rows F = 14; H = 12; D = 10. There are **14 accepted event messages**: **11 attempt messages** collapse to **10 physical attempts**, while **3 non-attempt terminal messages** record return/loss. F02's two messages have distinct event IDs, so neither is a duplicate source row. The counts supplied in the independent review also sum to 14, not 16; no two additional messages are specified or added.
- D partitions into 4 on time (F01/F02/F07/F14), 1 late, 2 open overdue, 1 returned, 1 lost, 1 unknown. Confirmed on time = **4/10 = 40%**; not confirmed on time = **6/10 = 60%**. Return = **1/10 = 10%**; loss = **1/10 = 10%**.
- Transit eligible F01/F02/F03/F07/F11/F14: hours sum **114**, count **6**, average **19.00h**.
- Open overdue backlog F04/F08: count **2**, total age **48h**, average **24.00h**.
- First-attempt eligible F01/F02/F03/F04/F05/F07/F11: count **7**, successes **4**, rate **57.14%**. F09 excluded for lifecycle conflict, F14 for lack of certification, F06/F08/F10 for zero attempts.
- Tracked H shipments: **10/12 = 83.33%** (all H except F08/F10). F09's conflicting but structurally valid scans still establish tracking coverage.
- Customer CUS0002 has one due shipment, on time (F07); F11 is excluded for missing promise. CUS0001 has nine due and three on time. Show small-group labels; these counts are below the baseline comparison threshold of 30.

**Independent message/attempt recount:** non-attempt messages are F05's return, F06's loss, and F09's conflicting loss. They are accepted evidence, not extra physical attempts.

| Shipment | Accepted messages | Attempt messages | Non-attempt messages | Physical attempts |
| --- | ---: | ---: | ---: | ---: |
| F01 | 1 | 1 | 0 | 1 |
| F02 | 2 | 2 | 0 | 1 |
| F03 | 1 | 1 | 0 | 1 |
| F04 | 1 | 1 | 0 | 1 |
| F05 | 2 | 1 | 1 | 1 |
| F06 | 1 | 0 | 1 | 0 |
| F07 | 2 | 2 | 0 | 2 |
| F08 | 0 | 0 | 0 | 0 |
| F09 | 2 | 1 | 1 | 1 |
| F10 | 0 | 0 | 0 | 0 |
| F11 | 1 | 1 | 0 | 1 |
| F12 | 0 | 0 | 0 | 0 |
| F13 | 0 | 0 | 0 | 0 |
| F14 | 1 | 1 | 0 | 1 |
| **Total** | **14** | **11** | **3** | **10** |

The event total is `1+2+1+1+2+1+2+0+2+0+1+0+0+1 = 14`. Ten physical attempts include F09's coherent single attempt even though its shipment has a conflicting terminal outcome; physical-attempt count and eligible first-attempt denominator are different measures.

**Population reconciliation:** F = 12 H + 1 no-handover (F12) + 1 cancelled (F13). H = 10 D + 1 unusable-promise (F11) + 1 future-promise (F10). D = 4 on time + 1 late + 2 open overdue + 1 returned + 1 lost + 1 unknown. H = 7 first-attempt eligible + 1 lifecycle exclusion + 1 uncertified exclusion + 3 certified zero-attempt exclusions. The seven eligible shipments split into four first successes and three first failures. H = 6 transit eligible + 6 transit excluded = 10 tracked + 2 untracked. No core fixture booking/event occurs after T; late-recorded F01 is still pre-T occurrence evidence.

The six fixture sources contain 14 shipment rows, 14 event rows, 2 customer rows, 2 location rows, 3 depot rows, and 3 service rows: **38 accepted source rows in total**, with no source duplicates, superseded versions, or quarantined rows. The specified F09 conflict, F11 missing promise, and F14 uncertified attempts are retained/flagged, not quarantined. Other fields follow the contracts; all message IDs are distinct and all master relationships resolve. Source dispositions count AC + AF = accepted once, irrespective of how many quality reasons a row has.

The earlier spec's 7/10 on-time illustration remains an arithmetic example. This richer fixture intentionally has a different population and expected answer.

## Isolated edge-case variants

Each variant starts from a fresh copy of the named core case; do not combine changes unless explicitly stated. Expected results are reviewed before implementation.

| Variant | Expected disposition and assertion |
| --- | --- |
| Add an exact copy of F02's event row | Added row DX; F02 still one physical attempt and all core KPI totals unchanged. |
| Add an older permitted booking/certificate version | DS for older candidate; current result unchanged. |
| Same version, differing payload; version timestamps reversed; invalid newest version metadata | Q all distinct representatives for that key, never fall back. Its fact disappears if booking key is blocked; children become Q unresolved parent. |
| Newest version has invalid booking time but older one valid | Newest Q, older DS, no fallback. |
| Different contents under same event ID | Q all distinct variants, no outcome winner; identifiable parents receive appropriate holds. |
| Successful and failed scans for same physical attempt | Both accepted conflict evidence; unknown terminal, first-attempt excluded, D retained. |
| Two attempt IDs share sequence 1; reuse attempt ID across shipments; tied times for distinct attempts | Accepted conflict evidence; no arbitrary ordering; affected attempt eligibility false, terminal unknown if success/terminal ordering is affected. |
| Delete first attempt of F07 but retain C=2 and sequence 2 | Missing export evidence: first-attempt excluded; coherent successful delivery may remain confirmed. C mismatch/gap is visible. |
| F01 W < T; PARTIAL certificate; missing required certificate detail; C mismatch | AF/unusable certificate; no first-attempt eligibility. Delivery remains usable. |
| COMPLETE C=0 and zero attempts | Valid certificate, no first-attempt denominator, no false success. |
| W > T with one failed attempt before T and one success after T | Validate both against full C=2; first-attempt failure at T, one T physical attempt, no delivered outcome or extra tracking-coverage contribution from the later success. Removing/conflicting the later certificate evidence makes certification unusable and excludes first-attempt measurement; it never moves the later outcome/attempt into T. |
| Replace F01 success with missing terminal time, recorded < occurred, or missing attempt ID | Q message, terminal/certificate hold; F01 stays in D, no on-time/transit/backlog eligibility. |
| Pre-handover progress scan; pre-handover success scan | Both Q; only success creates terminal hold, any malformed attempt blocks certificate. |
| Event type unrecognized or parent unresolvable | Q; known parent gets conservative holds for unknown type; unknown parent remains global Q with no guessed association. |
| Move F01 success to T+1 second, recording later but ≤ E | Accepted outside cutoff; at T open overdue, no delivery/attempt contribution. Keep certificate coherent for the variant (C=0 through T). |
| Add valid booking at T+1 second / booking exactly at T | First accepted outside cutoff; second fact included. No bad-data classification. |
| F04 promise exactly T | D retained, OPEN_AT_DEADLINE, not overdue; backlog falls to 1 and average stays 24h. |
| F01 promise before handover | AF/unusable promise, excluded D; valid delivered transit still 12h. |
| Cancellation after handover by T; cancellation equals handover; cancellation before later handover by T | AF start conflict; excluded H/D, retained fact. No cancellation-time tie winner. |
| Valid handover by T and cancellation after T / cancelled by T with handover after T | Full-extract conflict flag; first evaluated as handed over at T, second cancelled at T. Do not retroactively apply future cancellation. |
| Handover after T, after E, or unparseable | After T but ≤ E is accepted future milestone; after E/unparseable is invalid AF; all excluded from H with distinct reasons. |
| Multiple terminal kinds tied; repeat same-kind terminal confirmations; progress strictly after terminal | Tied different kinds/progress-after-terminal → unknown; repeat same-kind confirmations → one earliest terminal, no duplicate outcome. |
| No primary location; two primary sites; inactive-primary; quarantined potentially-primary version | One customer retained; Unknown geography with distinct missing/conflict/hold reasons. |
| Missing depot/service/customer master reference | Shipment AF mapped Unknown, unchanged fact count and KPI eligibility when needed timestamps remain valid. |
| Impossible calendar date in required booking/account-opening/event occurrence, or invalid required master key | Q according to source contract; no ordinary timestamp cast assumed to succeed. An invalid promise or optional handover instead retains the shipment as AF and excludes dependent KPIs, as its contract requires. |
| Entire selected cohort empty, only cancelled bookings, or zero open backlog | Denominator 0 gives N/A, not 0%; backlog count 0 and average N/A. |
| Missing file, bad header, wrong field count, broken quoting, invalid UTF-8 | Batch ingestion failure; no accepted refresh claim. Separate file-failure tests. |

## Expected-results manifest and later acceptance

A separate test-only manifest will record contract/config/generator versions, runtime, seed streams, T/E/W, checksums, source counts, scenario-to-business-ID mapping, raw-row dispositions/reasons, per-shipment outcomes and eligibility, canonical attempts, dimension-join expectations, and all KPI numerators/denominators/sums. Store both clean and dirty expected results plus each isolated variant's exact delta. It is technical test metadata, never a seventh business feed or an input to Bronze/Silver/Gold.

For the main dataset, expected dispositions come from the planned clean ledger and explicit anomaly operations, not from executing the transformation under test. For the fourteen-row fixture, the arithmetic above supplies an independently reviewable oracle (a known correct answer). The manifest must be reviewed/frozen before using it to judge warehouse results. Keep original expected values when tests fail; investigate the discrepancy rather than making expected and actual agree by definition.

Later acceptance requires identical checksums on two same-config generations, all source-row and reporting-population equations balancing, exact fixture counts, percentage/hour presentation within two decimals, coverage of every variant, and proof the pipeline cannot access the expected manifest. No such runtime evidence is claimed in Step 2.

Cutoff acceptance separately proves occurrence eligibility and certificate trust: post-T events must never supply T outcomes, physical-attempt counts, or coverage; evidence inside W > T may change certificate validity and thus first-attempt KPI eligibility. The wider-window variant above tests both effects. It is incorrect to demand that every KPI remain unchanged when a certificate becomes untrustworthy.

**Baseline review:** fixture message counts, physical attempts, population partitions, and all KPI arithmetic were independently rechecked. The correct message total remains 14; the unsupported proposed total of 16 was not adopted. This is a documentation/arithmetic review, not generated data or SQL Server execution. See PROJECT_SPEC.md for the consolidated finalization change log; no Step 2 design blocker remains.
