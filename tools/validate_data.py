"""Validate parcel CSV exports without importing the generator's scenario logic."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "generation.json").read_text(encoding="utf-8"))
T = datetime.fromisoformat(CONFIG["reporting_cutoff_utc"].replace("Z", "+00:00"))
E = datetime.fromisoformat(CONFIG["extract_as_of_utc"].replace("Z", "+00:00"))
NAMES = ("shipments", "tracking_events", "customers", "customer_locations", "depots", "service_levels")
HEADERS = {
    "shipments": "shipment_id customer_id service_code origin_depot_id destination_depot_id booked_at_utc handed_over_at_utc promised_delivery_at_utc cancelled_at_utc attempt_history_status attempt_history_through_at_utc attempt_history_attempt_count attempt_history_attested_at_utc record_version source_updated_at_utc".split(),
    "tracking_events": "event_id shipment_id event_type occurred_at_utc recorded_at_utc depot_id physical_attempt_id attempt_sequence".split(),
    "customers": "customer_id customer_name customer_segment account_opened_at_utc record_version source_updated_at_utc".split(),
    "customer_locations": "location_id customer_id location_name city_name region_code is_active is_primary record_version source_updated_at_utc".split(),
    "depots": "depot_id depot_name city_name region_code record_version source_updated_at_utc".split(),
    "service_levels": "service_code service_name nominal_transit_hours record_version source_updated_at_utc".split(),
}
KEY = {"shipments": "shipment_id", "tracking_events": "event_id",
       "customers": "customer_id", "customer_locations": "location_id",
       "depots": "depot_id", "service_levels": "service_code"}
PATTERNS = {
    "shipment_id": re.compile(r"SHP\d{9}\Z"),
    "event_id": re.compile(r"EVT\d{11}\Z"),
    "physical_attempt_id": re.compile(r"ATT\d{11}\Z"),
    "customer_id": re.compile(r"CUS\d{4}\Z"),
    "location_id": re.compile(r"LOC\d{5}\Z"),
    "origin_depot_id": re.compile(r"DEP\d{2}\Z"),
    "destination_depot_id": re.compile(r"DEP\d{2}\Z"),
    "depot_id": re.compile(r"DEP\d{2}\Z"),
}
EVENT_TYPES = {"IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERY_FAILED",
               "DELIVERY_SUCCEEDED", "RETURNED_TO_SENDER", "LOST"}
TERMINALS = {"DELIVERY_SUCCEEDED", "RETURNED_TO_SENDER", "LOST"}
ATTEMPT_TYPES = {"DELIVERY_FAILED", "DELIVERY_SUCCEEDED"}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
DEPOT_CITIES = ("Lumenford", "Brindle Quay", "Alderwick", "Mistral Hollow",
                "Sunmere", "Reedhaven", "Ferncross", "Mossgate")
CITY_REGION = {city: CONFIG["regions"][index // 2]
               for index, city in enumerate(DEPOT_CITIES)}
MAX_LENGTHS = {
    "customer_name": 100, "customer_segment": 12, "location_name": 100,
    "city_name": 60, "region_code": 12, "depot_name": 80,
    "service_code": 8, "service_name": 60, "event_type": 18,
    "attempt_history_status": 11,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def parse_time(value: str) -> datetime | None:
    if not value or not DATE_PATTERN.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.second > 59:
            return None
        return parsed
    except ValueError:
        return None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv_strict(path: Path, source: str) -> list[dict[str, str]]:
    raw = path.read_bytes()
    require(raw.startswith(b"\xef\xbb\xbf") is False, f"BOM: {path}")
    require(b"\r" not in raw, f"CRLF/control return: {path}")
    require(raw.endswith(b"\n"), f"missing final LF: {path}")
    text = raw.decode("utf-8", errors="strict")
    require("\x00" not in text, f"NUL byte: {path}")
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        header = next(reader)
        require(header == HEADERS[source], f"header: {path}: {header}")
        rows = []
        for line, values in enumerate(reader, 2):
            require(len(values) == len(header), f"field count: {path}:{line}")
            require(not any("\n" in field or "\r" in field for field in values),
                    f"embedded newline: {path}:{line}")
            rows.append(dict(zip(header, values)))
    return rows


def load_sources(folder: Path) -> dict[str, list[dict[str, str]]]:
    actual = {path.name for path in folder.iterdir() if path.is_file()}
    expected = {f"{name}.csv" for name in NAMES}
    require(actual == expected, f"six CSV files required in {folder}: {actual ^ expected}")
    return {name: read_csv_strict(folder / f"{name}.csv", name) for name in NAMES}


def validate_fields(sources: dict[str, list[dict[str, str]]],
                    mode: str, operations: dict | None = None) -> dict:
    operations = operations or {}
    missing_book = set(operations.get("missing_booking", {}).get("shipment_ids", []))
    missing_promise = set(operations.get("missing_promise", {}).get("shipment_ids", []))
    missing_handover = set(operations.get("missing_handover", {}).get("shipment_ids", []))
    missing_terminal = set(operations.get("missing_terminal_occurrence", {}).get("shipment_ids", []))
    unresolved_fk = set(operations.get("unresolved_customer_fk", {}).get("shipment_ids", []))
    orphan_ids = set(operations.get("orphan_events", {}).get("event_ids", []))
    whitespace_ids = set(operations.get("whitespace_names", {}).get("customer_ids", []))
    problems = Counter()
    whitespace_entities = set()
    for source, rows in sources.items():
        for number, row in enumerate(rows, 2):
            label = f"{mode}/{source}:{number}"
            require(bool(row[KEY[source]]), f"missing business key: {label}")
            if source != "tracking_events":
                require(row["record_version"].isdigit() and int(row["record_version"]) > 0,
                        f"invalid source version: {label}")
                committed = parse_time(row["source_updated_at_utc"])
                require(committed is not None and committed <= E,
                        f"invalid source commit: {label}")
            for field, pattern in PATTERNS.items():
                if field not in row or not row[field]:
                    continue
                require(bool(pattern.fullmatch(row[field])), f"invalid {field}: {label}")
            for field, value in row.items():
                if field.endswith("_at_utc") and value:
                    require(parse_time(value) is not None, f"invalid timestamp {field}: {label}")
                if field in MAX_LENGTHS:
                    require(len(value) <= MAX_LENGTHS[field], f"field too long {field}: {label}")
                if field in ("record_version", "attempt_sequence", "attempt_history_attempt_count",
                             "nominal_transit_hours") and value:
                    require(value.isascii() and value.isdigit() and int(value) <= 2_147_483_647,
                            f"integer format/range {field}: {label}")
            if source == "shipments":
                sid = row["shipment_id"]
                if sid in missing_book:
                    require(row["booked_at_utc"] == "", f"missing-booking injection: {label}")
                    problems["missing_booking"] += 1
                else:
                    require(parse_time(row["booked_at_utc"]) is not None, f"booking: {label}")
                if sid in missing_promise or (mode == "fixture" and sid == "SHP900000011"):
                    require(row["promised_delivery_at_utc"] == "", f"missing-promise injection: {label}")
                    problems["missing_promise"] += 1
                else:
                    require(parse_time(row["promised_delivery_at_utc"]) is not None, f"promise: {label}")
                if sid in missing_handover:
                    require(row["handed_over_at_utc"] == "", f"missing-handover injection: {label}")
                    problems["missing_handover"] += 1
                if sid in unresolved_fk:
                    require(row["customer_id"] in {"CUS9997", "CUS9998", "CUS9999"},
                            f"unknown customer injection: {label}")
                    problems["unresolved_customer_fk"] += 1
                require(row["service_code"] in CONFIG["services"], f"service: {label}")
                require(row["attempt_history_status"] in {"COMPLETE", "PARTIAL", "UNAVAILABLE"},
                        f"certificate status: {label}")
                if row["attempt_history_status"] == "COMPLETE":
                    require(parse_time(row["attempt_history_through_at_utc"]) is not None, f"W: {label}")
                    require(parse_time(row["attempt_history_attested_at_utc"]) is not None, f"attestation: {label}")
                    require(row["attempt_history_attempt_count"].isdigit(), f"control count: {label}")
                else:
                    require(row["attempt_history_attempt_count"] == "", f"unexpected control count: {label}")
                for field in ("record_version",):
                    require(row[field].isdigit() and int(row[field]) > 0, f"version: {label}")
                require(parse_time(row["source_updated_at_utc"]) <= E, f"commit after extract: {label}")
            elif source == "tracking_events":
                require(row["event_type"] in EVENT_TYPES, f"event type: {label}")
                if row["shipment_id"] in missing_terminal and row["event_type"] == "DELIVERY_SUCCEEDED" and not row["occurred_at_utc"]:
                    problems["missing_terminal_occurrence"] += 1
                else:
                    require(parse_time(row["occurred_at_utc"]) is not None, f"occurrence: {label}")
                require(parse_time(row["recorded_at_utc"]) is not None, f"recorded time: {label}")
                if row["event_id"] in orphan_ids:
                    require(row["shipment_id"].startswith("SHP999999"), f"orphan injection: {label}")
                    problems["orphan_events"] += 1
                if row["event_type"] in ATTEMPT_TYPES:
                    require(bool(PATTERNS["physical_attempt_id"].fullmatch(row["physical_attempt_id"])), f"attempt ID: {label}")
                    require(row["attempt_sequence"].isdigit() and int(row["attempt_sequence"]) >= 1,
                            f"attempt sequence: {label}")
                else:
                    require(row["physical_attempt_id"] == "" and row["attempt_sequence"] == "",
                            f"non-attempt fields: {label}")
            elif source == "customers":
                require(row["customer_segment"] in {"SMALL", "MIDMARKET", "ENTERPRISE"}, f"segment: {label}")
                require(parse_time(row["account_opened_at_utc"]) <= parse_time(row["source_updated_at_utc"]) <= E,
                        f"customer chronology: {label}")
                if row["customer_id"] in whitespace_ids and row["customer_name"].startswith("  "):
                    whitespace_entities.add(row["customer_id"])
                else:
                    require(row["customer_name"] == row["customer_name"].strip(), f"unexpected whitespace: {label}")
            elif source == "customer_locations":
                require(bool(row["customer_id"]), f"missing site owner: {label}")
                require(row["region_code"] in CONFIG["regions"], f"region: {label}")
                require(CITY_REGION.get(row["city_name"]) == row["region_code"],
                        f"site town/region mismatch: {label}")
                require(row["is_active"] in {"true", "false"} and row["is_primary"] in {"true", "false"},
                        f"location boolean: {label}")
            elif source == "depots":
                require(row["region_code"] in CONFIG["regions"], f"depot region: {label}")
                require(CITY_REGION.get(row["city_name"]) == row["region_code"],
                        f"depot town/region mismatch: {label}")
            elif source == "service_levels":
                require(row["service_code"] in CONFIG["services"], f"service key: {label}")
                require(row["nominal_transit_hours"] == str(CONFIG["services"][row["service_code"]]),
                        f"nominal target: {label}")
            if source in ("customers", "customer_locations", "depots", "service_levels"):
                for field in ("customer_name", "city_name", "depot_name", "service_name"):
                    if field in row:
                        require(bool(row[field].strip()), f"missing required description: {label}/{field}")
    problems["whitespace_names"] = len(whitespace_entities)
    for name, count in problems.items():
        if name in operations:
            require(count == operations[name]["count"], f"{name} count: {count}")
    return dict(problems)


def resolve_versions(sources: dict[str, list[dict[str, str]]], mode: str,
                     operations: dict | None = None) -> tuple[dict[str, dict[str, dict]], dict]:
    operations = operations or {}
    current: dict[str, dict[str, dict]] = {}
    dispositions = {}
    for source, rows in sources.items():
        seen_exact = set()
        unique = []
        exact = 0
        for row in rows:
            image = tuple(row[field] for field in HEADERS[source])
            if image in seen_exact:
                exact += 1
            else:
                seen_exact.add(image)
                unique.append(row)
        require(exact == (CONFIG["demo_anomalies"]["exact_copies"][source] if mode == "demo" else 0),
                f"unexpected exact copies in {mode}/{source}: {exact}")
        groups = defaultdict(list)
        for row in unique:
            groups[row[KEY[source]]].append(row)
        winners = {}
        superseded = 0
        for key, versions in groups.items():
            if source == "tracking_events":
                require(len(versions) == 1, f"event ID conflict outside fixture variant: {key}")
                winners[key] = versions[0]
            else:
                ordered = sorted(versions, key=lambda r: int(r["record_version"]))
                require(len({r["record_version"] for r in ordered}) == len(ordered),
                        f"same-version conflict outside fixture variant: {source}/{key}")
                times = [parse_time(r["source_updated_at_utc"]) for r in ordered]
                require(all(t is not None for t in times), f"invalid version time: {source}/{key}")
                require(times == sorted(times), f"version time reversed: {source}/{key}")
                winners[key] = ordered[-1]
                superseded += len(ordered) - 1
        required_superseded = (CONFIG["demo_anomalies"]["older_versions"].get(source, 0)
                               if mode == "demo" else 0)
        require(superseded == required_superseded,
                f"unexpected superseded count {mode}/{source}: {superseded}")
        current[source] = winners
        dispositions[source] = {"raw": len(rows), "exact_duplicate": exact,
                                "superseded": superseded,
                                "current_representatives": len(winners)}
    return current, dispositions


def assess_sources(sources: dict[str, list[dict[str, str]]], mode: str,
                   operations: dict | None = None) -> dict:
    operations = operations or {}
    current, dispositions = resolve_versions(sources, mode, operations)
    invalid_bookings = set(operations.get("missing_booking", {}).get("shipment_ids", []))
    missing_terminal = set(operations.get("missing_terminal_occurrence", {}).get("shipment_ids", []))
    orphan_event_ids = set(operations.get("orphan_events", {}).get("event_ids", []))
    valid_shipments = {sid: row for sid, row in current["shipments"].items()
                       if sid not in invalid_bookings}
    for sid, row in valid_shipments.items():
        require(parse_time(row["booked_at_utc"]) is not None, f"unintended invalid booking: {mode}/{sid}")
        for field, parent_source in (("customer_id", "customers"),
                                     ("service_code", "service_levels"),
                                     ("origin_depot_id", "depots"),
                                     ("destination_depot_id", "depots")):
            if mode == "demo" and field == "customer_id" and sid in set(operations["unresolved_customer_fk"]["shipment_ids"]):
                require(row[field] not in current[parent_source], f"unresolved FK resolved: {sid}")
            else:
                require(row[field] in current[parent_source], f"unexpected FK {mode}/{sid}/{field}")
        booked = parse_time(row["booked_at_utc"])
        handover = parse_time(row["handed_over_at_utc"])
        promise = parse_time(row["promised_delivery_at_utc"])
        cancelled = parse_time(row["cancelled_at_utc"])
        require(not handover or booked <= handover <= parse_time(row["source_updated_at_utc"]),
                f"handover chronology {mode}/{sid}")
        require(not promise or booked <= promise and (not handover or handover <= promise),
                f"promise chronology {mode}/{sid}")
        require(not cancelled or booked <= cancelled <= parse_time(row["source_updated_at_utc"]),
                f"cancellation chronology {mode}/{sid}")
        require(not (cancelled and handover), f"clean/demo lifecycle contradiction {mode}/{sid}")
    terminal_holds = set()
    accepted_events = defaultdict(list)
    event_quarantine = Counter()
    for event in current["tracking_events"].values():
        sid = event["shipment_id"]
        event_id = event["event_id"]
        if event_id in orphan_event_ids or sid not in valid_shipments:
            event_quarantine["unresolved_parent"] += 1
            continue
        if not event["occurred_at_utc"] and sid in missing_terminal:
            terminal_holds.add(sid)
            event_quarantine["missing_terminal_time"] += 1
            continue
        occurrence = parse_time(event["occurred_at_utc"])
        recorded = parse_time(event["recorded_at_utc"])
        booked = parse_time(valid_shipments[sid]["booked_at_utc"])
        handover = parse_time(valid_shipments[sid]["handed_over_at_utc"])
        require(occurrence is not None and recorded is not None, f"unintended invalid event time: {mode}/{event_id}")
        require(booked <= occurrence <= recorded <= E, f"event time order: {mode}/{event_id}")
        require(not handover or occurrence >= handover, f"pre-handover event: {mode}/{event_id}")
        accepted_events[sid].append(event)
    if mode == "demo":
        require(event_quarantine["unresolved_parent"] ==
                operations["missing_booking"]["orphaned_child_events"] + operations["orphan_events"]["count"],
                f"orphan event count: {event_quarantine}")
        require(event_quarantine["missing_terminal_time"] == operations["missing_terminal_occurrence"]["count"],
                f"terminal Q count: {event_quarantine}")
    else:
        require(not event_quarantine, f"unexpected event quarantine: {event_quarantine}")
    current_locations = current["customer_locations"]
    for location in current_locations.values():
        require(location["customer_id"] in current["customers"],
                f"unresolved site owner: {mode}/{location['location_id']}")
    attempt_owner = {}
    sequence_owner = {}
    for sid, events in accepted_events.items():
        for event in events:
            if event["depot_id"]:
                require(event["depot_id"] in current["depots"],
                        f"unresolved scan depot: {mode}/{event['event_id']}")
            if event["event_type"] in ATTEMPT_TYPES:
                aid = event["physical_attempt_id"]
                sequence = event["attempt_sequence"]
                require(attempt_owner.setdefault(aid, sid) == sid,
                        f"attempt ID reused across shipments: {mode}/{aid}")
                require(sequence_owner.setdefault((sid, sequence), aid) == aid,
                        f"attempt sequence reused: {mode}/{sid}/{sequence}")
    active_primary = Counter(row["customer_id"] for row in current_locations.values()
                             if row["is_active"] == "true" and row["is_primary"] == "true")
    if mode == "demo":
        expected_conflicts = set(operations["multiple_primary_customers"]["customer_ids"])
        require({customer for customer, count in active_primary.items() if count > 1} == expected_conflicts,
                "unexpected current primary-location conflict")
    else:
        require(all(active_primary[customer] == 1 for customer in current["customers"]),
                f"missing or duplicate primary location in {mode}")
    event_flagged = set()
    flagged_shipments = set()
    for sid, row in valid_shipments.items():
        if (not row["promised_delivery_at_utc"] or sid in terminal_holds or
            sid in set(operations.get("missing_handover", {}).get("shipment_ids", [])) or
            sid in set(operations.get("unresolved_customer_fk", {}).get("shipment_ids", [])) or
            sid in set(operations.get("unavailable_certificate", {}).get("shipment_ids", [])) or
            sid in set(operations.get("certificate_count_mismatch", {}).get("shipment_ids", [])) or
            sid in set(operations.get("terminal_conflicts", {}).get("shipment_ids", []))):
            flagged_shipments.add(sid)
    conflict_shipments = set(operations.get("terminal_conflicts", {}).get("shipment_ids", []))
    no_handover_shipments = set(operations.get("missing_handover", {}).get("shipment_ids", []))
    for sid, events in accepted_events.items():
        for event in events:
            if (sid in conflict_shipments or sid in no_handover_shipments or
                (parse_time(event["occurred_at_utc"]) <= T < parse_time(event["recorded_at_utc"]))):
                event_flagged.add(event["event_id"])
    customer_flagged = set(operations.get("whitespace_names", {}).get("customer_ids", []))
    customer_flagged.update(customer for customer, count in active_primary.items() if count > 1)
    location_flagged = {row["location_id"] for row in current_locations.values()
                        if active_primary[row["customer_id"]] > 1}
    dispositions["shipments"]["quarantined"] = len(invalid_bookings)
    dispositions["shipments"]["accepted"] = len(valid_shipments)
    dispositions["tracking_events"]["quarantined"] = sum(event_quarantine.values())
    dispositions["tracking_events"]["accepted"] = sum(map(len, accepted_events.values()))
    for source in NAMES:
        if source not in ("shipments", "tracking_events"):
            dispositions[source]["quarantined"] = 0
            dispositions[source]["accepted"] = dispositions[source]["current_representatives"]
        row = dispositions[source]
        require(row["raw"] == row["exact_duplicate"] + row["superseded"] + row["quarantined"] + row["accepted"],
                f"source reconciliation: {mode}/{source}: {row}")
        flagged = {
            "shipments": flagged_shipments,
            "tracking_events": event_flagged,
            "customers": customer_flagged,
            "customer_locations": location_flagged,
            "depots": set(), "service_levels": set(),
        }[source]
        require(len(flagged) <= row["accepted"], f"flag count exceeds accepted rows: {mode}/{source}")
        row["accepted_flagged"] = len(flagged)
        row["accepted_clean"] = row["accepted"] - len(flagged)
        row["normalized_equivalent_duplicate"] = 0
        require(row["raw"] == row["accepted_clean"] + row["accepted_flagged"] +
                row["exact_duplicate"] + row["normalized_equivalent_duplicate"] +
                row["superseded"] + row["quarantined"],
                f"full AC/AF/DX/DE/DS/Q reconciliation: {mode}/{source}")
    future_bookings = sum(parse_time(row["booked_at_utc"]) > T
                          for row in valid_shipments.values())
    fact_bookings = len(valid_shipments) - future_bookings
    event_scope = Counter()
    for sid, events in accepted_events.items():
        handover = parse_time(valid_shipments[sid]["handed_over_at_utc"])
        for event in events:
            if parse_time(event["occurred_at_utc"]) > T:
                event_scope["after_cutoff"] += 1
            elif handover is None or handover > T:
                event_scope["by_cutoff_no_valid_handover"] += 1
            else:
                event_scope["by_cutoff_usable"] += 1
    require(fact_bookings + future_bookings == dispositions["shipments"]["accepted"],
            f"shipment cutoff reconciliation: {mode}")
    require(sum(event_scope.values()) == dispositions["tracking_events"]["accepted"],
            f"event cutoff reconciliation: {mode}")
    return {"current": current, "valid_shipments": valid_shipments,
            "accepted_events": accepted_events, "terminal_holds": terminal_holds,
            "dispositions": dispositions, "event_quarantine": dict(event_quarantine),
            "reporting_scope": {"fact_bookings": fact_bookings,
                                "future_bookings": future_bookings,
                                "events": dict(event_scope)}}


def classify_shipments(assessment: dict) -> tuple[dict[str, dict], dict]:
    """Independent CSV interpretation for validation against the planned ledger."""
    observed = {}
    messages_count = 0
    physical_attempts = set()
    for sid, row in assessment["valid_shipments"].items():
        booked = parse_time(row["booked_at_utc"])
        handover = parse_time(row["handed_over_at_utc"])
        promise = parse_time(row["promised_delivery_at_utc"])
        cancelled = parse_time(row["cancelled_at_utc"])
        fact = booked <= T
        H = fact and handover is not None and handover <= T and (cancelled is None or cancelled > T)
        D = H and promise is not None and promise <= T
        evidence = assessment["accepted_events"].get(sid, [])
        messages_count += len(evidence)
        for event in evidence:
            if event["event_type"] in ATTEMPT_TYPES:
                physical_attempts.add((sid, event["physical_attempt_id"]))
        current_evidence = [event for event in evidence
                            if parse_time(event["occurred_at_utc"]) <= T and H]
        tracked = bool(current_evidence)
        terminal_rows = [event for event in current_evidence if event["event_type"] in TERMINALS]
        terminal_types = {event["event_type"] for event in terminal_rows}
        terminal_conflict = len(terminal_types) > 1 or sid in assessment["terminal_holds"]
        if terminal_rows and not terminal_conflict:
            first_terminal = min(parse_time(event["occurred_at_utc"]) for event in terminal_rows)
            if any(parse_time(event["occurred_at_utc"]) > first_terminal
                   and event["event_type"] not in terminal_types
                   for event in current_evidence):
                terminal_conflict = True
        outcome = ""
        transit_hours = None
        overdue_hours = None
        if not fact:
            outcome = "FUTURE_BOOKING"
        elif cancelled is not None and cancelled <= T:
            outcome = "CANCELLED"
        elif not H:
            outcome = "NOT_HANDED"
        elif terminal_conflict:
            outcome = "UNKNOWN"
        elif terminal_types == {"DELIVERY_SUCCEEDED"}:
            delivery = min(parse_time(event["occurred_at_utc"]) for event in terminal_rows)
            transit_hours = (delivery - handover).total_seconds() / 3600
            outcome = ("DELIVERED_PROMISE_UNKNOWN" if promise is None else
                       "ON_TIME" if delivery <= promise else "LATE")
        elif terminal_types == {"RETURNED_TO_SENDER"}:
            outcome = "RETURNED"
        elif terminal_types == {"LOST"}:
            outcome = "LOST"
        elif D:
            outcome = "OPEN_OVERDUE" if promise < T else "OPEN_AT_DEADLINE"
            if promise < T:
                overdue_hours = (T - promise).total_seconds() / 3600
        else:
            outcome = "OPEN_FUTURE" if H else "NOT_HANDED"
        first_success = None
        attempt_rows = [event for event in evidence if event["event_type"] in ATTEMPT_TYPES]
        status = row["attempt_history_status"]
        W = parse_time(row["attempt_history_through_at_utc"])
        attested = parse_time(row["attempt_history_attested_at_utc"])
        if H and not terminal_conflict and status == "COMPLETE" and W is not None and W >= T and attested is not None:
            by_attempt = defaultdict(list)
            for event in attempt_rows:
                if parse_time(event["occurred_at_utc"]) <= W:
                    by_attempt[event["physical_attempt_id"]].append(event)
            canonical = []
            consistent = True
            for rows in by_attempt.values():
                signatures = {(item["attempt_sequence"], item["occurred_at_utc"], item["event_type"])
                              for item in rows}
                if len(signatures) != 1 or not any(parse_time(item["recorded_at_utc"]) <= attested for item in rows):
                    consistent = False
                else:
                    seq, when, result = next(iter(signatures))
                    canonical.append((int(seq), parse_time(when), result))
            canonical.sort()
            if len(canonical) != int(row["attempt_history_attempt_count"]):
                consistent = False
            if [item[0] for item in canonical] != list(range(1, len(canonical) + 1)):
                consistent = False
            if any(canonical[i][1] >= canonical[i + 1][1]
                   for i in range(len(canonical) - 1)):
                consistent = False
            if consistent and canonical and canonical[0][1] <= T:
                first_success = canonical[0][2] == "DELIVERY_SUCCEEDED"
        observed[sid] = {
            "fact": fact, "handed_over": H, "due": D, "outcome": outcome,
            "first_success": first_success, "tracked": tracked if H else False,
            "transit_hours": transit_hours, "overdue_hours": overdue_hours,
        }
    return observed, {"accepted_messages_after_source_resolution": messages_count,
                      "distinct_physical_attempt_ids": len(physical_attempts)}


def summarize_observed(observed: dict[str, dict]) -> dict:
    fact = [item for item in observed.values() if item["fact"]]
    handed = [item for item in fact if item["handed_over"]]
    due = [item for item in handed if item["due"]]
    outcome = Counter(item["outcome"] for item in due)
    transit = [item["transit_hours"] for item in handed if item["transit_hours"] is not None]
    overdue = [item["overdue_hours"] for item in due if item["outcome"] == "OPEN_OVERDUE"]
    first = [item for item in handed if item["first_success"] is not None]
    return {
        "fact_rows": len(fact), "handed_over": len(handed), "due": len(due),
        "due_outcomes": dict(sorted(outcome.items())),
        "confirmed_on_time": {"numerator": outcome["ON_TIME"], "denominator": len(due)},
        "transit": {"hours_sum": sum(transit), "eligible": len(transit)},
        "open_overdue": {"shipments": len(overdue), "hours_sum": sum(overdue)},
        "first_attempt_success": {"numerator": sum(bool(item["first_success"]) for item in first),
                                  "denominator": len(first)},
        "tracking_coverage": {"numerator": sum(bool(item["tracked"]) for item in handed),
                              "denominator": len(handed)},
        "return_rate": {"numerator": outcome["RETURNED"], "denominator": len(due)},
        "loss_rate": {"numerator": outcome["LOST"], "denominator": len(due)},
    }


def compare(expected: object, actual: object, label: str) -> None:
    if isinstance(expected, dict):
        require(isinstance(actual, dict), f"{label}: dictionary required")
        require(set(expected) == set(actual), f"{label}: keys {set(expected) ^ set(actual)}")
        for key in expected:
            compare(expected[key], actual[key], f"{label}.{key}")
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        require(isinstance(actual, (int, float)) and math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-7),
                f"{label}: expected {expected}, observed {actual}")
    else:
        require(expected == actual, f"{label}: expected {expected}, observed {actual}")


def fixture_check(folder: Path) -> dict:
    sources = load_sources(folder)
    expected = json.loads((ROOT / "tests" / "expected" / "fixture.json").read_text(encoding="utf-8"))
    compare(expected["source_rows"], {name: len(rows) for name, rows in sources.items()}, "fixture rows")
    validate_fields(sources, "fixture")
    assessment = assess_sources(sources, "fixture")
    observed, info = classify_shipments(assessment)
    summary = summarize_observed(observed)
    for key in ("fact_rows", "handed_over", "due", "due_outcomes", "confirmed_on_time",
                "transit", "open_overdue", "first_attempt_success", "tracking_coverage",
                "return_rate", "loss_rate"):
        compare(expected[key], summary[key], f"fixture.{key}")
    messages = sources["tracking_events"]
    attempt_messages = [row for row in messages if row["event_type"] in ATTEMPT_TYPES]
    compare(expected["event_messages"], len(messages), "fixture event messages")
    compare(expected["attempt_messages"], len(attempt_messages), "fixture attempt messages")
    compare(expected["non_attempt_messages"], len(messages) - len(attempt_messages),
            "fixture non-attempt messages")
    compare(expected["physical_attempts"], len({(row["shipment_id"], row["physical_attempt_id"])
                                                for row in attempt_messages}), "fixture physical attempts")
    require(observed["SHP900000001"]["outcome"] == "ON_TIME", "late-arriving F01")
    require(observed["SHP900000002"]["first_success"] is True, "F02 repeated scan")
    require(observed["SHP900000009"]["outcome"] == "UNKNOWN", "F09 terminal conflict")
    require(observed["SHP900000014"]["first_success"] is None, "F14 uncertified lone success")
    return {"rows": expected["source_rows"], "observed": summary,
            "event_messages": len(messages), "physical_attempts": expected["physical_attempts"],
            "late_f01_recording": next(row["recorded_at_utc"] for row in messages
                                       if row["shipment_id"] == "SHP900000001")}


def dataset_check(folder: Path, mode: str) -> dict:
    sources = load_sources(folder)
    expected = json.loads((ROOT / "tests" / "expected" / f"{mode}.json").read_text(encoding="utf-8"))
    compare(expected["source_rows"], {name: len(rows) for name, rows in sources.items()},
            f"{mode} source rows")
    operations = expected.get("operations", {})
    injected = validate_fields(sources, mode, operations)
    assessment = assess_sources(sources, mode, operations)
    observed, info = classify_shipments(assessment)
    summary = summarize_observed(observed)
    late_arrivals = sum(parse_time(event["occurred_at_utc"]) <= T <
                        parse_time(event["recorded_at_utc"])
                        for events in assessment["accepted_events"].values()
                        for event in events)
    compare(expected["expected"], summary, f"{mode} KPI oracle")
    quarantined_ids = set(operations.get("missing_booking", {}).get("shipment_ids", []))
    require(set(expected["per_shipment"]) - quarantined_ids == set(observed),
            f"{mode} accepted shipment identity mismatch")
    for sid, planned in expected["per_shipment"].items():
        if sid in quarantined_ids:
            require(sid not in observed, f"quarantined shipment entered observed population: {sid}")
            continue
        for field in ("fact", "handed_over", "due", "outcome", "first_success",
                      "tracked", "transit_hours", "overdue_hours"):
            compare(planned[field], observed[sid][field], f"{mode}.{sid}.{field}")
    if mode == "clean":
        compare(CONFIG["lifecycle_counts"], expected["scenario_counts"], "clean scenario quotas")
        lifecycle_outcomes = {
            "due_on_time": "ON_TIME", "due_late": "LATE",
            "due_open": "OPEN_OVERDUE", "due_returned": "RETURNED",
            "due_lost": "LOST", "future_due": "OPEN_FUTURE",
            "not_handed_over": "NOT_HANDED", "cancelled": "CANCELLED",
            "future_booking": "FUTURE_BOOKING",
        }
        compare({lifecycle_outcomes[name]: amount
                 for name, amount in CONFIG["lifecycle_counts"].items()},
                dict(Counter(row["outcome"] for row in observed.values())),
                "clean lifecycle quotas measured from CSV")
        compare(CONFIG["service_assignments"],
                dict(Counter(row["service_code"] for row in assessment["current"]["shipments"].values())),
                "clean service assignments")
        compare({"SMALL": 60, "MIDMARKET": 30, "ENTERPRISE": 10},
                dict(Counter(row["customer_segment"] for row in assessment["current"]["customers"].values())),
                "clean customer segments")
        primary_locations = {row["customer_id"]: row for row in
                             assessment["current"]["customer_locations"].values()
                             if row["is_active"] == "true" and row["is_primary"] == "true"}
        for segment in ("SMALL", "MIDMARKET", "ENTERPRISE"):
            regions = {primary_locations[cid]["region_code"] for cid, row in
                       assessment["current"]["customers"].items()
                       if row["customer_segment"] == segment}
            require(len(regions) >= 2, f"insufficient region spread for {segment}")
        for service in CONFIG["services"]:
            depot_pairs = {(row["origin_depot_id"], row["destination_depot_id"])
                           for row in assessment["current"]["shipments"].values()
                           if row["service_code"] == service}
            require(len(depot_pairs) >= 2, f"insufficient depot-pair spread for {service}")
        require(40000 <= len(sources["tracking_events"]) <= 65000, "clean event range")
        require(len(sources["shipments"]) == 10000 and len(sources["customers"]) == 100,
                "clean business counts")
        require(len(sources["customer_locations"]) == 140 and len(sources["depots"]) == 8
                and len(sources["service_levels"]) == 3, "clean master counts")
        require(summary["fact_rows"] == 9800 and summary["handed_over"] == 9300
                and summary["due"] == 9000, "clean cohort targets")
        require(summary["confirmed_on_time"]["numerator"] == 7000, "clean on-time quota")
        require(expected["late_recorded_messages"] > 0 and late_arrivals >= expected["late_recorded_messages"],
                "late-arrival scenario absent")
    else:
        for key, value in CONFIG["demo_anomalies"].items():
            if key in ("exact_copies", "older_versions"):
                compare(sum(value.values()), operations[key]["count"], f"demo quota {key}")
            else:
                compare(value, operations[key]["count"], f"demo quota {key}")
        require(summary["fact_rows"] == 9795, "demo fact target")
        require(assessment["dispositions"]["shipments"]["quarantined"] == 5, "demo shipment Q")
        require(assessment["dispositions"]["tracking_events"]["quarantined"] ==
                10 + operations["missing_booking"]["orphaned_child_events"], "demo event Q")
    checksums = {name: digest(folder / f"{name}.csv") for name in NAMES}
    return {"rows": {name: len(sources[name]) for name in NAMES},
            "sha256": checksums, "dispositions": assessment["dispositions"],
            "injected_values_seen": injected, "observed": summary,
            "event_checks": info, "event_quarantine": assessment["event_quarantine"],
            "reporting_scope": assessment["reporting_scope"],
            "late_arrivals": late_arrivals}


WIDER_CERTIFICATE_EXPECTATIONS = {
    "wider_certificate_valid": {"certified_count": 2, "first_success": False,
                                "first_attempt_denominator": 7},
    "wider_certificate_invalid": {"certified_count": 3, "first_success": None,
                                  "first_attempt_denominator": 6},
}


def check_wider_certificate(data: dict[str, list[dict[str, str]]], name: str) -> dict:
    """Check fixed Step 2 fixture expectations independently of the generator."""
    expected = WIDER_CERTIFICATE_EXPECTATIONS[name]
    shipment_id = "SHP900000004"
    booking = next(row for row in data["shipments"] if row["shipment_id"] == shipment_id)
    require(booking["attempt_history_status"] == "COMPLETE", f"{name}: certificate status")
    require(booking["attempt_history_attempt_count"] == str(expected["certified_count"]),
            f"{name}: certified count must be {expected['certified_count']}")
    W = parse_time(booking["attempt_history_through_at_utc"])
    attested = parse_time(booking["attempt_history_attested_at_utc"])
    committed = parse_time(booking["source_updated_at_utc"])
    require(W is not None and T < W <= attested <= committed <= E,
            f"{name}: wider certificate chronology")
    validate_fields(data, "fixture")
    assessment = assess_sources(data, "fixture")
    attempts = [event for event in assessment["accepted_events"][shipment_id]
                if event["event_type"] in ATTEMPT_TYPES and
                parse_time(event["occurred_at_utc"]) <= W]
    window_attempts = {(event["shipment_id"], event["physical_attempt_id"])
                       for event in attempts}
    cutoff_attempts = {(event["shipment_id"], event["physical_attempt_id"])
                       for event in attempts if parse_time(event["occurred_at_utc"]) <= T}
    require(len(window_attempts) == 2 and len(cutoff_attempts) == 1,
            f"{name}: expected two window attempts and one cutoff attempt")
    require({event["attempt_sequence"] for event in attempts} == {"1", "2"},
            f"{name}: attempt sequence evidence")
    require({event["event_type"] for event in attempts
             if parse_time(event["occurred_at_utc"]) <= T} == {"DELIVERY_FAILED"},
            f"{name}: first attempt must fail by cutoff")
    require({event["event_type"] for event in attempts
             if parse_time(event["occurred_at_utc"]) > T} == {"DELIVERY_SUCCEEDED"},
            f"{name}: later success evidence")
    observed, _ = classify_shipments(assessment)
    result = observed[shipment_id]
    require(result["handed_over"] and result["due"] and
            result["outcome"] == "OPEN_OVERDUE" and result["transit_hours"] is None,
            f"{name}: later success must not become a cutoff delivery")
    require(result["first_success"] is expected["first_success"],
            f"{name}: first-attempt eligibility/result")
    denominator = summarize_observed(observed)["first_attempt_success"]["denominator"]
    require(denominator == expected["first_attempt_denominator"],
            f"{name}: first-attempt denominator")
    return {"certified_count": expected["certified_count"],
            "attempts_through_window": len(window_attempts),
            "attempts_by_cutoff": len(cutoff_attempts),
            "first_attempt_eligible": result["first_success"] is not None,
            "first_attempt_success": result["first_success"],
            "cutoff_outcome": result["outcome"],
            "fixture_first_attempt_denominator": denominator}


def variant_check(root: Path) -> dict:
    catalog = json.loads((ROOT / "tests" / "expected" / "variants.json").read_text(encoding="utf-8"))
    known = load_sources(root / "known_answer")
    variant_root = root / "variants"
    expected_names = set(catalog["variants"])
    actual_names = {path.name for path in variant_root.iterdir() if path.is_dir()}
    require(expected_names == actual_names, f"fixture variant names: {expected_names ^ actual_names}")
    mixed = {"wider_certificate_valid", "wider_certificate_invalid",
             "success_after_cutoff", "certified_zero_attempts"}
    add_event = {"exact_event_copy", "event_id_conflict", "attempt_result_conflict",
                 "sequence_collision", "pre_handover_progress", "pre_handover_success",
                 "wider_certificate_valid", "wider_certificate_invalid", "repeated_terminal",
                 "progress_after_terminal"}
    remove_event = {"missing_first_attempt", "certified_zero_attempts"}
    add_shipment = {"older_booking_version", "same_version_conflict", "reversed_version_times",
                    "invalid_newest_metadata", "invalid_newest_booking", "booking_after_cutoff",
                    "booking_at_cutoff"}
    add_location = {"multiple_primary_locations", "quarantined_primary_location"}
    results = {}
    wider_data = {}
    for name in sorted(expected_names):
        data = load_sources(variant_root / name)
        if name in WIDER_CERTIFICATE_EXPECTATIONS:
            wider_data[name] = data
        require(all("scenario" not in field and "expected" not in field
                    for source in NAMES for field in data[source][0].keys()) if all(data[s] for s in NAMES) else True,
                f"scenario field leaked in {name}")
        changes = {source for source in NAMES if data[source] != known[source]}
        if name == "empty_selected_cohort":
            require(not changes, f"empty selection should not alter source rows: {name}")
        elif name == "only_cancelled_bookings":
            require(changes == {"shipments", "tracking_events"}, f"isolation: {name}: {changes}")
            require(len(data["shipments"]) == 1 and len(data["tracking_events"]) == 0,
                    "only cancelled variant shape")
        else:
            require(changes, f"variant did not modify any source: {name}")
            require(changes <= ({"shipments", "tracking_events"} if name in mixed else
                                {"shipments"} if name in add_shipment or name in {
                                    "stale_certificate", "partial_certificate", "missing_certificate_detail",
                                    "certificate_count_mismatch", "success_after_cutoff",
                                    "promise_at_cutoff", "promise_before_handover",
                                    "cancel_after_handover", "cancel_tied_handover",
                                    "cancel_before_later_handover", "future_cancellation",
                                    "cancel_then_future_handover", "handover_after_cutoff",
                                    "handover_after_extract", "unparseable_handover",
                                    "missing_depot_reference", "missing_service_reference",
                                    "missing_customer_reference", "impossible_calendar_date",
                                    "invalid_promise",
                                    "booking_at_cutoff", "booking_after_cutoff", "zero_open_backlog"
                                } else
                                {"customer_locations"} if name in add_location or name in {
                                    "no_primary_location", "inactive_primary_location"} else
                                {"customers"} if name in {"invalid_master_key", "invalid_account_opening"} else
                                {"shipments", "tracking_events"} if name == "certified_zero_attempts" else
                                {"tracking_events"}),
                    f"variant contaminated another source: {name}: {changes}")
        row_delta = {source: len(data[source]) - len(known[source]) for source in NAMES}
        if name in add_event:
            require(row_delta["tracking_events"] == 1, f"expected added event in {name}")
        if name in remove_event:
            require(row_delta["tracking_events"] == -1, f"expected removed event in {name}")
        if name in add_shipment:
            require(row_delta["shipments"] == 1, f"expected added booking/version in {name}")
        if name in add_location:
            require(row_delta["customer_locations"] == 1, f"expected added site/version in {name}")
        if name == "success_after_cutoff":
            event = next(row for row in data["tracking_events"]
                         if row["shipment_id"] == "SHP900000001")
            require(parse_time(event["occurred_at_utc"]) > T, "future event occurrence")
        if name.startswith("wider_certificate"):
            f4 = next(row for row in data["shipments"] if row["shipment_id"] == "SHP900000004")
            require(parse_time(f4["attempt_history_through_at_utc"]) > T, "wider W")
        if name == "multiple_primary_locations":
            require(sum(row["is_primary"] == "true" and row["customer_id"] == "CUS0001"
                        for row in data["customer_locations"]) == 2,
                    "multiple primary evidence")
        if name == "invalid_account_opening":
            require(parse_time(data["customers"][0]["account_opened_at_utc"]) is None,
                    "invalid account-opening case")
        if name == "invalid_event_occurrence":
            require(any(parse_time(row["occurred_at_utc"]) is None for row in data["tracking_events"]),
                    "invalid event occurrence case")
        if name == "invalid_promise":
            require(parse_time(next(row for row in data["shipments"]
                                    if row["shipment_id"] == "SHP900000001")["promised_delivery_at_utc"]) is None,
                    "invalid promise case")
        results[name] = {"expectation": catalog["variants"][name], "changed_sources": sorted(changes),
                         "row_deltas": {key: value for key, value in row_delta.items() if value}}
    valid_data = wider_data["wider_certificate_valid"]
    invalid_data = wider_data["wider_certificate_invalid"]
    require(valid_data != invalid_data, "wider certificate variants must not be identical")
    wider_results = {name: check_wider_certificate(wider_data[name], name)
                     for name in WIDER_CERTIFICATE_EXPECTATIONS}
    changed_back = copy.deepcopy(invalid_data)
    changed_booking = next(row for row in changed_back["shipments"]
                           if row["shipment_id"] == "SHP900000004")
    changed_booking["attempt_history_attempt_count"] = "2"
    require(changed_back == valid_data,
            "wider variants should differ only in F04's certified count")
    try:
        check_wider_certificate(changed_back, "wider_certificate_invalid")
    except AssertionError as error:
        require("certified count must be 3" in str(error),
                f"unexpected regression failure: {error}")
    else:
        raise AssertionError("regression failed: changing invalid certified count to 2 passed")
    malformed_root = root / "malformed"
    expected_bad = set(catalog["malformed_file_cases"])
    actual_bad = {path.name for path in malformed_root.iterdir() if path.is_dir()}
    require(expected_bad == actual_bad, f"malformed case names: {expected_bad ^ actual_bad}")
    malformed = {}
    for name in sorted(expected_bad):
        try:
            load_sources(malformed_root / name)
        except (AssertionError, UnicodeError, csv.Error, FileNotFoundError, StopIteration) as error:
            malformed[name] = type(error).__name__
        else:
            raise AssertionError(f"malformed fixture unexpectedly parsed: {name}")
    return {"isolated_variants": len(results), "malformed_cases_rejected": malformed,
            "variant_details": results, "wider_certificate": wider_results,
            "invalid_count_mutation_rejected": True}


def reproducibility_check() -> dict:
    # Import only the generator entry point to create two copies. Comparison uses raw bytes.
    from generate_data import generate_all
    with (tempfile.TemporaryDirectory(prefix="parcel_repeat_a_") as left,
          tempfile.TemporaryDirectory(prefix="parcel_repeat_b_") as right):
        left_root, right_root = Path(left), Path(right)
        generate_all(left_root, CONFIG)
        generate_all(right_root, CONFIG)
        left_files = {str(path.relative_to(left_root)): digest(path)
                      for path in left_root.rglob("*.csv")}
        right_files = {str(path.relative_to(right_root)): digest(path)
                       for path in right_root.rglob("*.csv")}
        require(left_files == right_files, "CSV checksums differ between temporary generations")
        expected_files = {str(path.relative_to(left_root)): digest(path)
                          for path in left_root.rglob("*.json")}
        other_expected = {str(path.relative_to(right_root)): digest(path)
                          for path in right_root.rglob("*.json")}
        require(expected_files == other_expected, "expected manifest checksums differ")
        committed = {str(path.relative_to(ROOT)): digest(path)
                     for path in (ROOT / "datasets").rglob("*.csv")}
        committed.update({str(path.relative_to(ROOT)): digest(path)
                          for path in (ROOT / "tests" / "fixtures").rglob("*.csv")})
        require(left_files == committed, "generated CSVs differ from repository outputs")
        committed_expected = {str(path.relative_to(ROOT)): digest(path)
                              for path in (ROOT / "tests" / "expected").glob("*.json")}
        require(expected_files == committed_expected,
                "generated expectation manifests differ from repository outputs")
        return {"csv_files_compared": len(left_files),
                "expected_json_files_compared": len(expected_files),
                "two_temporary_runs_equal": True, "repository_matches_temporary_runs": True}


def report(result: dict) -> str:
    clean, demo = result["clean"], result["demo"]
    lines = [
        "# Step 3 validation",
        "",
        "**SYNTHETIC — FOR LEARNING ONLY.**",
        "",
        "## Generated artifacts",
        "",
        "- Six contracted CSVs in each of `datasets/clean/` and `datasets/demo/`.",
        "- Six contracted CSVs for the 14-shipment known-answer fixture and each isolated variant.",
        f"- {result['variants']['isolated_variants']} isolated variants; "
        f"{len(result['variants']['malformed_cases_rejected'])} deliberately malformed file cases.",
        "- Test-only oracle files in `tests/expected/`, separate from business CSVs.",
        "",
        "## Runtime and reproduction",
        "",
        f"- Working repository: `{ROOT}`. Generator `{CONFIG['generator_version']}`; "
        f"configuration `{CONFIG['config_version']}`.",
        f"- Working code SHA-256: generator `{digest(ROOT / 'tools' / 'generate_data.py')}`; "
        f"validator `{digest(ROOT / 'tools' / 'validate_data.py')}`; "
        f"configuration `{digest(ROOT / 'config' / 'generation.json')}`.",
        f"- Python: `{sys.version.split()[0]}` at `{sys.executable}`; standard library only, no pip dependencies.",
        f"- Random source: `{CONFIG['random_algorithm']}` with master seed "
        f"`{CONFIG['master_seed']}` and fixed streams `{CONFIG['streams']}`.",
        f"- T = `{CONFIG['reporting_cutoff_utc']}`; E = `{CONFIG['extract_as_of_utc']}`.",
        f"- Two independent temporary generations matched on all "
        f"{result['reproducibility']['csv_files_compared']} CSV checksums and "
        f"{result['reproducibility']['expected_json_files_compared']} JSON oracle checksums. "
        "The repository outputs matched both temporary runs.",
        "",
        "From the `parcel-delivery-warehouse` project directory:",
        "",
        "```powershell",
        f"& '{sys.executable}' tools/generate_data.py --all",
        f"& '{sys.executable}' tools/validate_data.py --all",
        "```",
        "",
        "## Source rows and SHA-256",
        "",
        "| Source | Clean rows | Clean SHA-256 | Demo rows | Demo SHA-256 |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for name in NAMES:
        lines.append(f"| `{name}.csv` | {clean['rows'][name]:,} | `{clean['sha256'][name]}` | "
                     f"{demo['rows'][name]:,} | `{demo['sha256'][name]}` |")
    lines.extend([
        "",
        "## Source-row dispositions and reporting scope",
        "",
        "Each raw row has exactly one disposition: AC accepted clean, AF accepted with a quality flag, "
        "DX exact duplicate, DE normalized equivalent, DS superseded version, or Q quarantined. "
        "The validator checks `raw = AC + AF + DX + DE + DS + Q` for every file.",
        "",
        "| Dataset | Source | AC | AF | DX | DE | DS | Q |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for label, item in (("Clean", clean), ("Demo", demo)):
        for name in NAMES:
            d = item["dispositions"][name]
            lines.append(f"| {label} | `{name}` | {d['accepted_clean']:,} | "
                         f"{d['accepted_flagged']:,} | {d['exact_duplicate']:,} | "
                         f"{d['normalized_equivalent_duplicate']:,} | {d['superseded']:,} | "
                         f"{d['quarantined']:,} |")
    lines.append("")
    for label, item in (("Clean", clean), ("Demo", demo)):
        scope = item["reporting_scope"]
        events = scope["events"]
        lines.append(
            f"- {label} reporting split: accepted shipments = "
            f"{scope['fact_bookings']:,} fact + {scope['future_bookings']:,} future bookings; "
            f"accepted events = {events.get('by_cutoff_usable', 0):,} usable by T + "
            f"{events.get('by_cutoff_no_valid_handover', 0):,} by T without valid handover + "
            f"{events.get('after_cutoff', 0):,} after T. "
            f"Late-recorded pre-T accepted messages: {item['late_arrivals']:,}."
        )
    lines.extend([
        "",
        "## Clean and demonstration checks",
        "",
        "- Clean source counts: 10,000 shipment IDs, 100 customers, 140 sites, "
        "8 depots, 3 services. Clean event volume is inside the specified 40,000–65,000 range. "
        "All nine lifecycle quotas, service assignments, segment counts, and region/depot spread "
        "were checked from the generated CSVs.",
        "- Clean cutoff cohorts: 9,800 fact shipments; H=9,300; D=9,000. "
        "Due outcomes: 7,000 on time, 1,000 late, 650 open overdue, 250 returned, 100 lost.",
        "- Demonstration fact count: 9,795 after five intentionally unusable booking timestamps. "
        "Valid post-cutoff bookings remain accepted and outside the fact.",
        "- The demonstration adds 132 exact copies and 18 older versions, "
        "plus the documented missing values, orphan scans, incompatible outcomes, "
        "certificate defects, primary-location conflicts, unknown customer keys, and whitespace cases.",
        f"- Demonstration event quarantine: "
        f"{demo['dispositions']['tracking_events']['quarantined']} rows "
        f"({demo['event_quarantine']}); demonstration shipment quarantine: "
        f"{demo['dispositions']['shipments']['quarantined']} rows.",
        "- Source-row equations balance separately from fact/throughput/due exclusions. "
        "Current master keys, references, types, UTC chronology, certificate controls, "
        "and intended defect counts were checked. Detailed planned outcomes are in `tests/expected/`.",
        "",
        "## Known-answer fixture",
        "",
        "- 14 shipments; 14 accepted event messages (11 attempt messages and 3 other terminal messages); "
        "10 physical attempts; H=12; D=10.",
        "- Confirmed on time 4/10 (40%); transit 114h/6 (19h); "
        "open overdue backlog 2 with 48h total (24h average).",
        "- First-attempt success 4/7 (57.14%); tracking coverage 10/12 (83.33%); "
        "return 1/10 and loss 1/10. F02's two messages represent one attempt; "
        "F14's lone success is not first-attempt eligible without certification.",
        "- All five malformed file cases failed parsing/loading as intended. "
        "Each isolated variant has six CSVs, a test-only expected assertion, "
        "and validated source isolation/row deltas. The wider-certificate cases distinguish "
        "post-cutoff event outcomes from certificate trust.",
        "",
        "## Wider-certificate regression",
        "",
        "The two F04 variants differ only in the source-certified count. Both have two "
        "physical attempts through W, of which one occurred by T. Their later success "
        "does not count as delivery at T.",
        "",
        "| Variant | Certified count | Attempts through W | Attempts by T | "
        "First-attempt eligible | First attempt | Outcome at T | Fixture first-attempt denominator |",
        "| --- | ---: | ---: | ---: | --- | --- | --- | ---: |",
    ])
    for name, values in result["variants"]["wider_certificate"].items():
        lines.append(
            f"| `{name}` | {values['certified_count']} | "
            f"{values['attempts_through_window']} | {values['attempts_by_cutoff']} | "
            f"{'Yes' if values['first_attempt_eligible'] else 'No'} | "
            f"{'Failed' if values['first_attempt_success'] is False else 'Excluded'} | "
            f"{values['cutoff_outcome']} | {values['fixture_first_attempt_denominator']} |"
        )
    lines.extend([
        "",
        "- Regression mutation: changing the invalid case's certified count from 3 back to 2 "
        "made it identical to the valid variant and the validator rejected it as expected. "
        "The independent reviewed expectations in `tests/expected/` were not changed.",
        "",
        "## Limits and next gate",
        "",
        "- No SQL Server execution, Bronze/Silver/Gold load, or warehouse recovery test has run. "
        "Most variant assertions that require warehouse outcome logic remain documented for that stage; "
        "Step 3 verified their file shape and isolated mutations, and now directly checks "
        "the wider-certificate outcomes above.",
        "- Generated source data and fixture arithmetic have no unresolved Step 3 blocker.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Validate all Step 3 artifacts")
    args = parser.parse_args()
    if not args.all:
        parser.error("use --all to verify fixture, datasets, variants, and reproducibility")
    fixture = fixture_check(ROOT / "tests" / "fixtures" / "known_answer")
    clean = dataset_check(ROOT / "datasets" / "clean", "clean")
    demo = dataset_check(ROOT / "datasets" / "demo", "demo")
    variants = variant_check(ROOT / "tests" / "fixtures")
    reproducibility = reproducibility_check()
    result = {"fixture": fixture, "clean": clean, "demo": demo,
              "variants": variants, "reproducibility": reproducibility}
    target = ROOT / "evidence" / "step3_validation.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report(result), encoding="utf-8", newline="\n")
    print(json.dumps({
        "status": "passed", "fixture": fixture["observed"],
        "clean_rows": clean["rows"], "demo_rows": demo["rows"],
        "variants": variants["isolated_variants"],
        "malformed_rejected": len(variants["malformed_cases_rejected"]),
        "reproducibility": reproducibility,
        "report": str(target),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
