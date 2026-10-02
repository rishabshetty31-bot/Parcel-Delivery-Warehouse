"""Deterministic operational CSV exports for the fictional parcel project.

Only the Python standard library is used. Scenario/expected data is written outside
the six business CSVs and must never be consumed by warehouse transformations.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "generation.json"
FILES = (
    "shipments", "tracking_events", "customers", "customer_locations", "depots", "service_levels"
)
HEADERS = {
    "shipments": (
        "shipment_id", "customer_id", "service_code", "origin_depot_id",
        "destination_depot_id", "booked_at_utc", "handed_over_at_utc",
        "promised_delivery_at_utc", "cancelled_at_utc", "attempt_history_status",
        "attempt_history_through_at_utc", "attempt_history_attempt_count",
        "attempt_history_attested_at_utc", "record_version", "source_updated_at_utc",
    ),
    "tracking_events": (
        "event_id", "shipment_id", "event_type", "occurred_at_utc",
        "recorded_at_utc", "depot_id", "physical_attempt_id", "attempt_sequence",
    ),
    "customers": (
        "customer_id", "customer_name", "customer_segment", "account_opened_at_utc",
        "record_version", "source_updated_at_utc",
    ),
    "customer_locations": (
        "location_id", "customer_id", "location_name", "city_name", "region_code",
        "is_active", "is_primary", "record_version", "source_updated_at_utc",
    ),
    "depots": (
        "depot_id", "depot_name", "city_name", "region_code", "record_version",
        "source_updated_at_utc",
    ),
    "service_levels": (
        "service_code", "service_name", "nominal_transit_hours", "record_version",
        "source_updated_at_utc",
    ),
}
DEPOT_NAMES = (
    ("Lumenford Hub", "Lumenford"), ("Brindle Quay Depot", "Brindle Quay"),
    ("Alderwick Hub", "Alderwick"), ("Mistral Hollow Depot", "Mistral Hollow"),
    ("Sunmere Hub", "Sunmere"), ("Reedhaven Depot", "Reedhaven"),
    ("Ferncross Hub", "Ferncross"), ("Mossgate Depot", "Mossgate"),
)
NAME_A = ("Cloudfern", "Brightmoss", "Alderline", "Northglass", "Wrenfield",
          "Riverlark", "Amberroot", "Fernway", "Silvermere", "Bluewick")
NAME_B = ("Crafts", "Supply", "Textiles", "Works", "Studio", "Trading",
          "Goods", "Books", "Equipment", "Market")
UTC = timezone.utc


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso(value: datetime | None) -> str:
    return "" if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plus(value: datetime, hours: int = 0, seconds: int = 0) -> datetime:
    return value + timedelta(hours=hours, seconds=seconds)


def rand_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    seconds = int((end - start).total_seconds())
    assert seconds >= 0
    return plus(start, seconds=rng.randint(0, seconds))


def empty_sources() -> dict[str, list[dict[str, str]]]:
    return {name: [] for name in FILES}


def make_row(source: str, **values: object) -> dict[str, str]:
    unknown = set(values) - set(HEADERS[source])
    if unknown:
        raise ValueError(f"unknown {source} fields: {sorted(unknown)}")
    return {field: "" if values.get(field) is None else str(values.get(field, ""))
            for field in HEADERS[source]}


def source_sort(name: str, row: dict[str, str]) -> tuple[object, ...]:
    key = {
        "shipments": "shipment_id", "tracking_events": "event_id",
        "customers": "customer_id", "customer_locations": "location_id",
        "depots": "depot_id", "service_levels": "service_code",
    }[name]
    return (row[key], int(row.get("record_version", "1") or "1"),
            tuple(row[field] for field in HEADERS[name]))


def write_sources(folder: Path, sources: dict[str, list[dict[str, str]]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(HEADERS[name])
            for row in sorted(sources[name], key=lambda item: source_sort(name, item)):
                writer.writerow([row[field] for field in HEADERS[name]])


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def master_rows(config: dict, fixture: bool = False) -> dict[str, list[dict[str, str]]]:
    sources = empty_sources()
    customer_count = 2 if fixture else 100
    for number in range(1, customer_count + 1):
        customer_id = f"CUS{number:04d}"
        segment = ("SMALL" if number <= 60 else "MIDMARKET" if number <= 90 else "ENTERPRISE")
        sources["customers"].append(make_row(
            "customers", customer_id=customer_id,
            customer_name=f"{NAME_A[(number - 1) % 10]} {NAME_B[((number - 1) // 10) % 10]}",
            customer_segment=segment, account_opened_at_utc="2025-12-15T09:00:00Z",
            record_version=1, source_updated_at_utc="2026-06-30T10:00:00Z"))
        primary_depot_index = (number - 1) % 8
        region = config["regions"][primary_depot_index // 2]
        primary_number = len(sources["customer_locations"]) + 1
        sources["customer_locations"].append(make_row(
            "customer_locations", location_id=f"LOC{primary_number:05d}",
            customer_id=customer_id, location_name=f"{customer_id} Main Site",
            city_name=DEPOT_NAMES[primary_depot_index][1], region_code=region,
            is_active="true", is_primary="true", record_version=1,
            source_updated_at_utc="2026-06-20T11:00:00Z"))
        if not fixture and number > 60:
            second_number = len(sources["customer_locations"]) + 1
            sources["customer_locations"].append(make_row(
                "customer_locations", location_id=f"LOC{second_number:05d}",
                customer_id=customer_id, location_name=f"{customer_id} Secondary Site",
                city_name=DEPOT_NAMES[number % 8][1],
                region_code=config["regions"][(number % 8) // 2],
                is_active="true", is_primary="false", record_version=1,
                source_updated_at_utc="2026-06-20T11:00:00Z"))
    for number, (depot_name, city_name) in enumerate(DEPOT_NAMES[:3 if fixture else 8], 1):
        sources["depots"].append(make_row(
            "depots", depot_id=f"DEP{number:02d}", depot_name=depot_name,
            city_name=city_name, region_code=config["regions"][(number - 1) // 2],
            record_version=1, source_updated_at_utc="2025-12-01T09:00:00Z"))
    for code, hours in config["services"].items():
        sources["service_levels"].append(make_row(
            "service_levels", service_code=code,
            service_name=f"{code.title()} Parcel", nominal_transit_hours=hours,
            record_version=1, source_updated_at_utc="2025-12-01T09:00:00Z"))
    return sources


def shipment_row(shipment_id: str, customer_id: str, service: str,
                 origin: str, destination: str, booked: datetime,
                 handover: datetime | None, promise: datetime | None,
                 cancelled: datetime | None, count: int | None,
                 config: dict) -> dict[str, str]:
    complete = count is not None
    return make_row(
        "shipments", shipment_id=shipment_id, customer_id=customer_id,
        service_code=service, origin_depot_id=origin, destination_depot_id=destination,
        booked_at_utc=iso(booked), handed_over_at_utc=iso(handover),
        promised_delivery_at_utc=iso(promise), cancelled_at_utc=iso(cancelled),
        attempt_history_status="COMPLETE" if complete else "UNAVAILABLE",
        attempt_history_through_at_utc=config["reporting_cutoff_utc"] if complete else "",
        attempt_history_attempt_count=str(count) if complete else "",
        attempt_history_attested_at_utc=config["certificate_attested_at_utc"] if complete else "",
        record_version=1, source_updated_at_utc=config["certificate_commit_at_utc"])


class EventBuilder:
    def __init__(self, sources: dict[str, list[dict[str, str]]], config: dict,
                 delay_rng: random.Random | None = None):
        self.sources = sources
        self.config = config
        self.delay_rng = delay_rng
        self.next_event = 1
        self.next_attempt = 1
        self.late_count = 0

    def add(self, shipment_id: str, kind: str, occurred: datetime,
            depot_id: str, attempt_id: str = "", sequence: int | str = "",
            recorded: datetime | None = None) -> dict[str, str]:
        cutoff = dt(self.config["reporting_cutoff_utc"])
        if recorded is None:
            recorded = plus(occurred, hours=1)
            if self.delay_rng is not None and occurred <= cutoff and self.delay_rng.random() < 0.03:
                recorded = plus(cutoff, hours=self.delay_rng.randint(2, 144))
                self.late_count += 1
        row = make_row(
            "tracking_events", event_id=f"EVT{self.next_event:011d}",
            shipment_id=shipment_id, event_type=kind,
            occurred_at_utc=iso(occurred), recorded_at_utc=iso(recorded),
            depot_id=depot_id, physical_attempt_id=attempt_id,
            attempt_sequence=sequence)
        self.next_event += 1
        self.sources["tracking_events"].append(row)
        return row

    def attempt(self, shipment_id: str, kind: str, occurred: datetime,
                depot_id: str, sequence: int, repeat: bool = False,
                recorded: datetime | None = None) -> None:
        attempt_id = f"ATT{self.next_attempt:011d}"
        self.next_attempt += 1
        first = self.add(shipment_id, kind, occurred, depot_id,
                         attempt_id, sequence, recorded=recorded)
        if repeat:
            self.add(shipment_id, kind, occurred, depot_id,
                     attempt_id, sequence,
                     recorded=plus(dt(first["recorded_at_utc"]), seconds=1))


FIXTURE_EXPECTED = {
    "label": "SYNTHETIC — FOR LEARNING ONLY",
    "source": "Hand-reviewed Step 2 fixture; numbers are written explicitly, not calculated from CSV output",
    "source_rows": {"shipments": 14, "tracking_events": 14, "customers": 2,
                    "customer_locations": 2, "depots": 3, "service_levels": 3},
    "fact_rows": 14, "handed_over": 12, "due": 10,
    "event_messages": 14, "attempt_messages": 11, "non_attempt_messages": 3,
    "physical_attempts": 10,
    "due_outcomes": {"ON_TIME": 4, "LATE": 1, "OPEN_OVERDUE": 2,
                     "RETURNED": 1, "LOST": 1, "UNKNOWN": 1},
    "confirmed_on_time": {"numerator": 4, "denominator": 10},
    "transit": {"hours_sum": 114, "eligible": 6},
    "open_overdue": {"shipments": 2, "hours_sum": 48},
    "first_attempt_success": {"numerator": 4, "denominator": 7},
    "tracking_coverage": {"numerator": 10, "denominator": 12},
    "return_rate": {"numerator": 1, "denominator": 10},
    "loss_rate": {"numerator": 1, "denominator": 10},
}


def fixture_sources(config: dict) -> dict[str, list[dict[str, str]]]:
    sources = master_rows(config, fixture=True)
    builder = EventBuilder(sources, config)
    booked = dt("2026-06-26T08:00:00Z")
    handover = dt("2026-06-26T23:59:59Z")
    promise = plus(handover, hours=24)
    for number in range(1, 15):
        shipment_id = f"SHP9000000{number:02d}"
        customer = "CUS0002" if number in (7, 11) else "CUS0001"
        service = "ECONOMY" if number == 10 else "STANDARD" if number == 11 else "EXPRESS"
        destination = "DEP03" if number in (3, 7, 9) else "DEP02"
        sh_handover = None if number in (12, 13) else handover
        sh_promise = None if number == 11 else plus(handover, hours=72) if number == 10 else promise
        cancelled = dt("2026-06-26T10:00:00Z") if number == 13 else None
        count = None if number in (12, 13, 14) else 2 if number == 7 else 0 if number in (6, 8, 10) else 1
        sources["shipments"].append(shipment_row(
            shipment_id, customer, service, "DEP01", destination, booked,
            sh_handover, sh_promise, cancelled, count, config))
        def scan(kind: str, hours: int, sequence: int | None = None,
                 repeat: bool = False, late: bool = False) -> None:
            when = plus(handover, hours=hours)
            arrival = dt("2026-07-02T08:00:00Z") if late else plus(when, hours=1)
            if sequence is None:
                builder.add(shipment_id, kind, when, destination, recorded=arrival)
            else:
                builder.attempt(shipment_id, kind, when, destination, sequence,
                                repeat=repeat, recorded=arrival)
        if number == 1:
            scan("DELIVERY_SUCCEEDED", 12, 1, late=True)
        elif number == 2:
            scan("DELIVERY_SUCCEEDED", 24, 1, repeat=True)
        elif number == 3:
            scan("DELIVERY_SUCCEEDED", 36, 1)
        elif number == 4:
            scan("DELIVERY_FAILED", 6, 1)
        elif number == 5:
            scan("DELIVERY_FAILED", 6, 1)
            scan("RETURNED_TO_SENDER", 20)
        elif number == 6:
            scan("LOST", 20)
        elif number == 7:
            scan("DELIVERY_FAILED", 6, 1)
            scan("DELIVERY_SUCCEEDED", 18, 2)
        elif number == 9:
            scan("DELIVERY_SUCCEEDED", 12, 1)
            scan("LOST", 20)
        elif number in (11, 14):
            scan("DELIVERY_SUCCEEDED", 12, 1)
    return sources


def summary_from_ledger(ledger: dict[str, dict], config: dict) -> dict:
    """Summarize planned scenario ledger, never parse or classify generated CSVs."""
    facts = [item for item in ledger.values() if item["fact"]]
    handed = [item for item in facts if item["handed_over"]]
    due = [item for item in handed if item["due"]]
    outcomes = Counter(item["outcome"] for item in due)
    transit = [item["transit_hours"] for item in handed if item["transit_hours"] is not None]
    overdue = [item["overdue_hours"] for item in due if item["outcome"] == "OPEN_OVERDUE"]
    first = [item for item in handed if item["first_success"] is not None]
    return {
        "fact_rows": len(facts), "handed_over": len(handed), "due": len(due),
        "due_outcomes": dict(sorted(outcomes.items())),
        "confirmed_on_time": {"numerator": outcomes["ON_TIME"], "denominator": len(due)},
        "transit": {"hours_sum": sum(transit), "eligible": len(transit)},
        "open_overdue": {"shipments": len(overdue), "hours_sum": sum(overdue)},
        "first_attempt_success": {
            "numerator": sum(1 for item in first if item["first_success"]),
            "denominator": len(first)},
        "tracking_coverage": {
            "numerator": sum(1 for item in handed if item["tracked"]),
            "denominator": len(handed)},
        "return_rate": {"numerator": outcomes["RETURNED"], "denominator": len(due)},
        "loss_rate": {"numerator": outcomes["LOST"], "denominator": len(due)},
    }


def make_clean(config: dict) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict], dict]:
    sources = master_rows(config)
    streams = {name: random.Random(seed) for name, seed in config["streams"].items()}
    scenario_pool = [scenario for scenario, count in config["lifecycle_counts"].items()
                     for _ in range(count)]
    assert len(scenario_pool) == 10000
    streams["lifecycles"].shuffle(scenario_pool)
    service_pool = [service for service, count in config["service_assignments"].items()
                    for _ in range(count)]
    streams["bookings"].shuffle(service_pool)
    events = EventBuilder(sources, config, streams["recording_delays"])
    ledger: dict[str, dict] = {}
    counts = Counter()
    cutoff = dt(config["reporting_cutoff_utc"])
    booking_start = dt(config["booking_start_utc"])
    for number, (scenario, service) in enumerate(zip(scenario_pool, service_pool), 1):
        counts[scenario] += 1
        sid = f"SHP{number:09d}"
        hours = config["services"][service]
        booking_rng = streams["bookings"]
        lifecycle_rng = streams["lifecycles"]
        if scenario == "due_open":
            booked = rand_between(booking_rng, dt("2026-06-14T00:00:00Z"),
                                  dt("2026-06-23T00:00:00Z"))
        elif scenario == "future_due":
            booked = dt("2026-06-28T08:00:00Z")
        elif scenario == "future_booking":
            booked = dt("2026-06-29T08:00:00Z") if counts[scenario] <= 100 else dt("2026-06-30T08:00:00Z")
        elif scenario in ("not_handed_over", "cancelled"):
            booked = rand_between(booking_rng, booking_start,
                                  dt("2026-06-25T00:00:00Z"))
        else:
            booked = rand_between(booking_rng, booking_start,
                                  dt("2026-06-20T00:00:00Z"))
        handover = None if scenario == "cancelled" else plus(booked, hours=2)
        if scenario == "not_handed_over":
            handover = dt("2026-06-29T10:00:00Z") if counts[scenario] <= 40 else None
        if scenario == "future_booking":
            handover = plus(booked, hours=2) if counts[scenario] <= 80 else None
        promise = plus(handover or booked, hours=hours)
        cancelled = plus(booked, hours=1) if scenario == "cancelled" else None
        customer_id = f"CUS{streams['entities'].randint(1, 100):04d}"
        origin_number = streams["entities"].randint(1, 8)
        destination_number = streams["entities"].randint(1, 8)
        origin = f"DEP{origin_number:02d}"
        destination = f"DEP{destination_number:02d}"
        attempt_count = 0
        first_success = None
        outcome = ""
        transit_hours = None
        overdue_hours = None
        tracked = False
        after_cutoff = scenario in ("future_booking", "not_handed_over")
        if scenario not in ("cancelled", "future_booking", "not_handed_over"):
            no_tracking = scenario == "due_open" and counts[scenario] <= config["clean_no_tracking_open"]
            if not no_tracking:
                for step, kind in ((1, "IN_TRANSIT"), (2, "IN_TRANSIT"), (3, "OUT_FOR_DELIVERY")):
                    events.add(sid, kind, plus(handover, hours=step), destination)
                tracked = True
        elif after_cutoff and handover is not None:
            events.add(sid, "IN_TRANSIT", plus(handover, hours=1), destination)
        if scenario in ("due_on_time", "due_late"):
            attempts = 1 if lifecycle_rng.random() < 0.80 else 2 if lifecycle_rng.random() < 0.85 else 3
            attempt_count = attempts
            first_success = attempts == 1
            if scenario == "due_on_time":
                delivered_hours = lifecycle_rng.randint(8, hours)
                if counts[scenario] <= 100:
                    delivered_hours = hours
                outcome = "ON_TIME"
            else:
                delivered_hours = hours + lifecycle_rng.randint(2, 24)
                outcome = "LATE"
            for sequence in range(1, attempts):
                events.attempt(sid, "DELIVERY_FAILED", plus(handover, hours=sequence + 3),
                               destination, sequence, repeat=events.next_attempt % 4 == 0)
            events.attempt(sid, "DELIVERY_SUCCEEDED", plus(handover, hours=delivered_hours),
                           destination, attempts, repeat=events.next_attempt % 4 == 0)
            transit_hours = delivered_hours
        elif scenario == "due_open":
            outcome = "OPEN_OVERDUE"
            overdue_hours = (cutoff - promise).total_seconds() / 3600
            if config["clean_no_tracking_open"] < counts[scenario] <= 450:
                attempt_count = 1
                first_success = False
                events.attempt(sid, "DELIVERY_FAILED", plus(handover, hours=6),
                               destination, 1, repeat=events.next_attempt % 4 == 0)
        elif scenario == "due_returned":
            outcome = "RETURNED"
            if counts[scenario] <= 175:
                attempt_count = 1
                first_success = False
                events.attempt(sid, "DELIVERY_FAILED", plus(handover, hours=6),
                               destination, 1, repeat=events.next_attempt % 4 == 0)
            events.add(sid, "RETURNED_TO_SENDER", plus(handover, hours=12), destination)
        elif scenario == "due_lost":
            outcome = "LOST"
            events.add(sid, "LOST", plus(handover, hours=12), destination)
        elif scenario == "future_due":
            outcome = "OPEN_FUTURE"
        elif scenario == "not_handed_over":
            outcome = "NOT_HANDED"
        elif scenario == "cancelled":
            outcome = "CANCELLED"
        else:
            outcome = "FUTURE_BOOKING"
        certificate_count = attempt_count if booked <= cutoff and scenario != "cancelled" else None
        sources["shipments"].append(shipment_row(
            sid, customer_id, service, origin, destination, booked, handover,
            promise, cancelled, certificate_count, config))
        fact = booked <= cutoff
        handed = fact and handover is not None and handover <= cutoff and cancelled is None
        due = handed and promise <= cutoff
        ledger[sid] = {
            "scenario": scenario, "fact": fact, "handed_over": handed,
            "due": due, "outcome": outcome, "first_success": first_success if handed else None,
            "tracked": tracked if handed else False, "attempt_count": attempt_count,
            "transit_hours": transit_hours if handed else None,
            "overdue_hours": overdue_hours if due else None,
            "customer_id": customer_id, "service_code": service,
            "origin_depot_id": origin, "destination_depot_id": destination,
        }
    assert counts == config["lifecycle_counts"], (counts, config["lifecycle_counts"])
    assert len(sources["customers"]) == 100
    assert len(sources["customer_locations"]) == 140
    assert len(sources["depots"]) == 8
    assert len(sources["service_levels"]) == 3
    assert config["clean_event_count_min"] <= len(sources["tracking_events"]) <= config["clean_event_count_max"], len(sources["tracking_events"])
    meta = {"scenario_counts": dict(counts), "late_recorded_messages": events.late_count,
            "source_rows": {source: len(rows) for source, rows in sources.items()}}
    return sources, ledger, meta


def make_demo(clean: dict[str, list[dict[str, str]]],
              clean_ledger: dict[str, dict], config: dict
              ) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict], dict]:
    sources = copy.deepcopy(clean)
    ledger = copy.deepcopy(clean_ledger)
    quotas = config["demo_anomalies"]
    rng = random.Random(config["streams"]["anomaly_placement"])
    by_id = {row["shipment_id"]: row for row in sources["shipments"]}
    candidates = [sid for sid, value in ledger.items() if value["scenario"] == "due_on_time"]
    rng.shuffle(candidates)
    cursor = 0
    groups: dict[str, list[str]] = {}
    for name in ("missing_booking", "missing_promise", "missing_handover",
                 "missing_terminal_occurrence", "orphan_events", "terminal_conflicts",
                 "unavailable_certificate", "certificate_count_mismatch",
                 "unresolved_customer_fk"):
        amount = quotas[name]
        groups[name] = candidates[cursor:cursor + amount]
        cursor += amount
    assert len(set(sid for values in groups.values() for sid in values)) == cursor
    event_groups = defaultdict(list)
    for row in sources["tracking_events"]:
        event_groups[row["shipment_id"]].append(row)
    terminal_by_shipment = {
        sid: next(row for row in event_groups[sid] if row["event_type"] == "DELIVERY_SUCCEEDED")
        for name in ("missing_terminal_occurrence", "terminal_conflicts")
        for sid in groups[name]
    }
    operations: dict[str, dict] = {}
    def record(name: str, ids: list[str], **details: object) -> None:
        operations[name] = {"shipment_ids": ids, "count": len(ids), **details}
    for sid in groups["missing_booking"]:
        by_id[sid]["booked_at_utc"] = ""
        ledger[sid]["fact"] = False
        ledger[sid]["handed_over"] = False
        ledger[sid]["due"] = False
    child_q = sum(len(event_groups[sid]) for sid in groups["missing_booking"])
    record("missing_booking", groups["missing_booking"], disposition="Q", orphaned_child_events=child_q)
    for sid in groups["missing_promise"]:
        by_id[sid]["promised_delivery_at_utc"] = ""
        ledger[sid]["due"] = False
        ledger[sid]["outcome"] = "DELIVERED_PROMISE_UNKNOWN"
    record("missing_promise", groups["missing_promise"], disposition="AF")
    for sid in groups["missing_handover"]:
        by_id[sid]["handed_over_at_utc"] = ""
        ledger[sid]["handed_over"] = False
        ledger[sid]["due"] = False
        ledger[sid]["outcome"] = "NOT_HANDED"
        ledger[sid]["transit_hours"] = None
        ledger[sid]["first_success"] = None
        ledger[sid]["tracked"] = False
    record("missing_handover", groups["missing_handover"], disposition="AF")
    for sid in groups["missing_terminal_occurrence"]:
        terminal = terminal_by_shipment[sid]
        terminal["occurred_at_utc"] = ""
        attempt_id = terminal["physical_attempt_id"]
        assert attempt_id and ledger[sid]["attempt_count"] > 0
        # This anomaly removes one message, not necessarily its physical attempt:
        # a second valid scan of that visit may remain in the clean plan.
        if not any(event is not terminal and event["physical_attempt_id"] == attempt_id
                   and event["occurred_at_utc"] for event in event_groups[sid]):
            ledger[sid]["attempt_count"] -= 1
        ledger[sid]["outcome"] = "UNKNOWN"
        ledger[sid]["transit_hours"] = None
        ledger[sid]["first_success"] = None
    record("missing_terminal_occurrence", groups["missing_terminal_occurrence"], disposition="Q")
    orphan_event_ids = []
    for offset, sid in enumerate(groups["orphan_events"], 1):
        progress = next(row for row in event_groups[sid] if row["event_type"] == "IN_TRANSIT")
        progress["shipment_id"] = f"SHP999999{offset:03d}"
        orphan_event_ids.append(progress["event_id"])
    record("orphan_events", groups["orphan_events"], disposition="Q", event_ids=orphan_event_ids)
    next_event_id = max(int(row["event_id"][3:]) for row in sources["tracking_events"]) + 1
    for sid in groups["terminal_conflicts"]:
        original = terminal_by_shipment[sid]
        loss = make_row("tracking_events", event_id=f"EVT{next_event_id:011d}",
                        shipment_id=sid, event_type="LOST",
                        occurred_at_utc=iso(plus(dt(original["occurred_at_utc"]), hours=1)),
                        recorded_at_utc=iso(plus(dt(original["occurred_at_utc"]), hours=2)),
                        depot_id=by_id[sid]["destination_depot_id"])
        sources["tracking_events"].append(loss)
        next_event_id += 1
        ledger[sid]["outcome"] = "UNKNOWN"
        ledger[sid]["transit_hours"] = None
        ledger[sid]["first_success"] = None
    record("terminal_conflicts", groups["terminal_conflicts"], disposition="AF", added_messages=5)
    for sid in groups["unavailable_certificate"]:
        row = by_id[sid]
        row["attempt_history_status"] = "UNAVAILABLE"
        row["attempt_history_through_at_utc"] = ""
        row["attempt_history_attempt_count"] = ""
        row["attempt_history_attested_at_utc"] = ""
        ledger[sid]["first_success"] = None
    record("unavailable_certificate", groups["unavailable_certificate"], disposition="AF")
    for sid in groups["certificate_count_mismatch"]:
        row = by_id[sid]
        row["attempt_history_attempt_count"] = str(int(row["attempt_history_attempt_count"]) + 1)
        ledger[sid]["first_success"] = None
    record("certificate_count_mismatch", groups["certificate_count_mismatch"], disposition="AF")
    for offset, sid in enumerate(groups["unresolved_customer_fk"], 1):
        by_id[sid]["customer_id"] = f"CUS{9996 + offset:04d}"
        ledger[sid]["customer_id"] = by_id[sid]["customer_id"]
    record("unresolved_customer_fk", groups["unresolved_customer_fk"], disposition="AF")
    primary_conflicts = ["CUS0061", "CUS0062"]
    for customer_id in primary_conflicts:
        secondary = next(row for row in sources["customer_locations"]
                         if row["customer_id"] == customer_id and row["is_primary"] == "false")
        secondary["is_primary"] = "true"
    operations["multiple_primary_customers"] = {"customer_ids": primary_conflicts,
                                                   "count": len(primary_conflicts), "disposition": "AF"}
    whitespace_ids = [f"CUS{number:04d}" for number in range(95, 101)]
    for customer_id in whitespace_ids:
        customer = next(row for row in sources["customers"] if row["customer_id"] == customer_id)
        customer["customer_name"] = f"  {customer['customer_name']}  "
    operations["whitespace_names"] = {"customer_ids": whitespace_ids,
                                       "count": len(whitespace_ids), "disposition": "AF"}
    older_selection = {
        "shipments": [sid for sid in candidates[cursor:cursor + 10]],
        "customers": ["CUS0001", "CUS0002", "CUS0003"],
        "customer_locations": ["LOC00001", "LOC00002", "LOC00003"],
        "depots": ["DEP01"], "service_levels": ["STANDARD"],
    }
    key_field = {"shipments": "shipment_id", "customers": "customer_id",
                 "customer_locations": "location_id", "depots": "depot_id",
                 "service_levels": "service_code"}
    for name, identifiers in older_selection.items():
        for identifier in identifiers:
            current = next(row for row in sources[name] if row[key_field[name]] == identifier)
            older = copy.deepcopy(current)
            older["record_version"] = "1"
            current["record_version"] = "2"
            if name == "shipments":
                older["source_updated_at_utc"] = "2026-07-01T00:00:00Z"
                older["attempt_history_status"] = "UNAVAILABLE"
                older["attempt_history_through_at_utc"] = ""
                older["attempt_history_attempt_count"] = ""
                older["attempt_history_attested_at_utc"] = ""
            elif name == "customers":
                older["source_updated_at_utc"] = "2026-06-01T00:00:00Z"
                older["customer_name"] = f"Former {older['customer_name']}"
            elif name == "customer_locations":
                older["source_updated_at_utc"] = "2026-06-01T00:00:00Z"
                older["location_name"] = f"Earlier {older['location_name']}"
            else:
                older["source_updated_at_utc"] = "2025-11-30T09:00:00Z"
                display_field = "depot_name" if name == "depots" else "service_name"
                older[display_field] = f"Former {older[display_field]}"
            sources[name].append(older)
    operations["older_versions"] = {"business_ids": older_selection, "disposition": "DS",
                                     "count": sum(map(len, older_selection.values()))}
    exact_selection = {}
    for name, amount in quotas["exact_copies"].items():
        originals = list(sources[name])
        chosen = originals[-amount:]
        sources[name].extend(copy.deepcopy(chosen))
        exact_selection[name] = [row[key_field.get(name, "event_id")] for row in chosen]
    operations["exact_copies"] = {"business_ids": exact_selection, "disposition": "DX",
                                  "count": sum(map(len, exact_selection.values()))}
    assert operations["older_versions"]["count"] == 18
    assert operations["exact_copies"]["count"] == 132
    assert len([sid for sid, value in ledger.items() if value["fact"]]) == 9795
    meta = {"operations": operations,
            "source_rows": {name: len(sources[name]) for name in FILES}}
    return sources, ledger, meta


VARIANT_ASSERTIONS = {
    "exact_event_copy": "One DX event; fixture outcomes and one physical attempt on F02 unchanged.",
    "older_booking_version": "One DS booking version; current result unchanged.",
    "same_version_conflict": "Both distinct F01 booking variants Q; no arbitrary winner.",
    "reversed_version_times": "Both F01 booking versions Q for decreasing commit time.",
    "invalid_newest_metadata": "All distinct F01 versions Q; do not fall back.",
    "invalid_newest_booking": "Newest F01 Q, older F01 DS; do not fall back.",
    "event_id_conflict": "Both distinct messages with one event ID Q; F01 gets a terminal hold.",
    "attempt_result_conflict": "F01 conflicting success/failure evidence: outcome Unknown, first attempt ineligible.",
    "sequence_collision": "Two attempt IDs share F07 sequence 2: first attempt ineligible and success uncertain.",
    "attempt_id_reuse": "Same physical attempt ID on two shipments: both affected histories ineligible.",
    "tied_attempt_times": "F07's distinct attempts tie: first attempt ineligible, success uncertain.",
    "missing_first_attempt": "F07 sequence gap/C mismatch: first attempt ineligible, valid delivery evidence retained.",
    "stale_certificate": "F01 certificate W before T: first attempt ineligible, delivery retained.",
    "partial_certificate": "F01 PARTIAL: first attempt ineligible, delivery retained.",
    "missing_certificate_detail": "F01 COMPLETE without W: first attempt ineligible.",
    "certificate_count_mismatch": "F01 control count 2 but one physical attempt: first attempt ineligible.",
    "certified_zero_attempts": "F04 certificate C=0 and no attempts: no first-attempt denominator row.",
    "wider_certificate_valid": "F04 failed attempt by T remains first failure; later success validates C=2 but is not a T outcome.",
    "wider_certificate_invalid": "F04 C=3 with two attempts: first-attempt measurement excluded; later success is not a T outcome.",
    "missing_terminal_time": "F01 success Q with terminal hold; no on-time/transit/backlog assertion.",
    "recorded_before_occurrence": "F01 success Q with terminal hold.",
    "missing_attempt_id": "F01 success Q with terminal and certificate holds.",
    "pre_handover_progress": "New F01 progress event Q; no T coverage contribution from it.",
    "pre_handover_success": "New F01 success event Q; identifiable terminal hold.",
    "unknown_event_type": "F01 event Q, terminal/certificate holds.",
    "orphan_event": "F01 event Q for unknown parent; do not invent a parent.",
    "success_after_cutoff": "Valid success after T; F01 open overdue at T, C=0 through T.",
    "booking_after_cutoff": "New valid booking at T+1 second accepted outside fact population.",
    "booking_at_cutoff": "New valid booking exactly at T enters fact population.",
    "promise_at_cutoff": "F04 remains due but is not yet overdue at equality with T.",
    "promise_before_handover": "F01 promise unusable; delivery remains transit eligible.",
    "cancel_after_handover": "F01 start conflict excludes H/D.",
    "cancel_tied_handover": "F01 start conflict excludes H/D; no tie winner.",
    "cancel_before_later_handover": "F13 start conflict excludes H/D.",
    "future_cancellation": "F01 valid H at T; later contradictory cancellation is flagged only.",
    "cancel_then_future_handover": "F13 cancelled at T; later handover flagged as full-extract conflict.",
    "handover_after_cutoff": "F12 future valid handover, excluded H at T.",
    "handover_after_extract": "F12 invalid nonnull handover, excluded H.",
    "unparseable_handover": "F01 invalid handover, excluded H.",
    "terminal_tie": "F09 success and loss tie; outcome Unknown.",
    "repeated_terminal": "F06 agreeing LOST confirmation; one loss outcome, no physical attempt.",
    "progress_after_terminal": "F01 later progress creates Unknown outcome.",
    "no_primary_location": "CUS0001 retained with Unknown geography.",
    "multiple_primary_locations": "CUS0001 retained once with Unknown geography and two candidate sites.",
    "inactive_primary_location": "CUS0001 retained with Unknown geography and selection hold.",
    "quarantined_primary_location": "Conflicting LOC00001 versions Q; CUS0001 geography on hold.",
    "missing_depot_reference": "F01 retained with Unknown destination depot.",
    "missing_service_reference": "F01 retained with Unknown service.",
    "missing_customer_reference": "F01 retained with Unknown paying customer.",
    "impossible_calendar_date": "F01 booking Q; child events Q for unresolved parent.",
    "invalid_master_key": "Bad customer key Q; referenced shipments retain Unknown customer.",
    "invalid_account_opening": "CUS0001 account-opening date is impossible: customer Q; its shipments retain Unknown customer.",
    "invalid_event_occurrence": "F01 success occurrence is an impossible date: event Q with terminal/certificate holds.",
    "invalid_promise": "F01 promise is an impossible date: shipment AF, excluded D but delivery remains transit eligible.",
    "empty_selected_cohort": "An empty date selection has denominator zero and displays N/A.",
    "only_cancelled_bookings": "Single F13 cancelled booking: no H/D, all rate denominators zero/N/A.",
    "zero_open_backlog": "Move F04 and F08 promises after T: backlog count zero and average N/A.",
}


def make_variant(name: str, baseline: dict[str, list[dict[str, str]]],
                 config: dict) -> dict[str, list[dict[str, str]]]:
    sources = copy.deepcopy(baseline)
    shipments = {row["shipment_id"]: row for row in sources["shipments"]}
    events = defaultdict(list)
    for row in sources["tracking_events"]:
        events[row["shipment_id"]].append(row)
    f1 = shipments["SHP900000001"]
    f4 = shipments["SHP900000004"]
    f7 = shipments["SHP900000007"]
    f9 = shipments["SHP900000009"]
    f12 = shipments["SHP900000012"]
    f13 = shipments["SHP900000013"]
    cutoff = dt(config["reporting_cutoff_utc"])
    handover = dt("2026-06-26T23:59:59Z")
    booked = dt("2026-06-26T08:00:00Z")
    f1_success = next(row for row in events[f1["shipment_id"]]
                      if row["event_type"] == "DELIVERY_SUCCEEDED")
    def extra(original: dict[str, str], **changes: str) -> dict[str, str]:
        row = copy.deepcopy(original)
        row["event_id"] = f"EVT{90000000000 + len(sources['tracking_events']) + 1:011d}"
        row.update(changes)
        sources["tracking_events"].append(row)
        return row
    if name == "exact_event_copy":
        sources["tracking_events"].append(copy.deepcopy(events["SHP900000002"][0]))
    elif name in ("older_booking_version", "same_version_conflict", "reversed_version_times",
                  "invalid_newest_metadata", "invalid_newest_booking"):
        older = copy.deepcopy(f1)
        older["record_version"] = "1"
        older["source_updated_at_utc"] = "2026-07-01T00:00:00Z"
        older["attempt_history_status"] = "UNAVAILABLE"
        older["attempt_history_through_at_utc"] = ""
        older["attempt_history_attempt_count"] = ""
        older["attempt_history_attested_at_utc"] = ""
        f1["record_version"] = "2"
        if name == "same_version_conflict":
            older = copy.deepcopy(f1)
            older["customer_id"] = "CUS0002"
        elif name == "reversed_version_times":
            older["source_updated_at_utc"] = "2026-07-07T12:00:02Z"
        elif name == "invalid_newest_metadata":
            f1["source_updated_at_utc"] = "broken"
        elif name == "invalid_newest_booking":
            f1["booked_at_utc"] = "2026-02-30T08:00:00Z"
        sources["shipments"].append(older)
    elif name == "event_id_conflict":
        row = extra(f1_success, event_type="LOST", physical_attempt_id="", attempt_sequence="")
        row["event_id"] = f1_success["event_id"]
    elif name == "attempt_result_conflict":
        extra(f1_success, event_type="DELIVERY_FAILED")
    elif name == "sequence_collision":
        success = next(row for row in events[f7["shipment_id"]]
                       if row["event_type"] == "DELIVERY_SUCCEEDED")
        extra(success, physical_attempt_id="ATT90000000001")
    elif name == "attempt_id_reuse":
        next(row for row in events["SHP900000003"] if row["event_type"] == "DELIVERY_SUCCEEDED")["physical_attempt_id"] = f1_success["physical_attempt_id"]
    elif name == "tied_attempt_times":
        failed = next(row for row in events[f7["shipment_id"]]
                      if row["event_type"] == "DELIVERY_FAILED")
        next(row for row in events[f7["shipment_id"]]
             if row["event_type"] == "DELIVERY_SUCCEEDED")["occurred_at_utc"] = failed["occurred_at_utc"]
    elif name == "missing_first_attempt":
        sources["tracking_events"].remove(next(row for row in events[f7["shipment_id"]]
                                              if row["event_type"] == "DELIVERY_FAILED"))
    elif name in ("stale_certificate", "partial_certificate", "missing_certificate_detail",
                  "certificate_count_mismatch"):
        if name == "stale_certificate":
            f1["attempt_history_through_at_utc"] = iso(plus(cutoff, seconds=-3600))
        elif name == "partial_certificate":
            f1["attempt_history_status"] = "PARTIAL"
            f1["attempt_history_attempt_count"] = ""
        elif name == "missing_certificate_detail":
            f1["attempt_history_through_at_utc"] = ""
        else:
            f1["attempt_history_attempt_count"] = "2"
    elif name == "certified_zero_attempts":
        sources["tracking_events"].remove(events[f4["shipment_id"]][0])
        f4["attempt_history_attempt_count"] = "0"
    elif name in ("wider_certificate_valid", "wider_certificate_invalid"):
        extra(events[f4["shipment_id"]][0], event_type="DELIVERY_SUCCEEDED",
              occurred_at_utc=iso(plus(cutoff, hours=1)),
              recorded_at_utc=iso(plus(cutoff, hours=2)),
              physical_attempt_id="ATT90000000002", attempt_sequence="2")
        f4["attempt_history_through_at_utc"] = iso(plus(cutoff, hours=2))
        f4["attempt_history_attempt_count"] = "2" if name == "wider_certificate_valid" else "3"
    elif name == "missing_terminal_time":
        f1_success["occurred_at_utc"] = ""
    elif name == "recorded_before_occurrence":
        f1_success["recorded_at_utc"] = iso(plus(handover, hours=11))
    elif name == "missing_attempt_id":
        f1_success["physical_attempt_id"] = ""
    elif name in ("pre_handover_progress", "pre_handover_success"):
        kind = "IN_TRANSIT" if name.endswith("progress") else "DELIVERY_SUCCEEDED"
        extra(f1_success, event_type=kind, occurred_at_utc=iso(plus(booked, hours=1)),
              recorded_at_utc=iso(plus(booked, hours=2)),
              physical_attempt_id="" if kind == "IN_TRANSIT" else "ATT90000000003",
              attempt_sequence="" if kind == "IN_TRANSIT" else "2")
    elif name == "unknown_event_type":
        f1_success["event_type"] = "MYSTERY_SCAN"
        f1_success["physical_attempt_id"] = ""
        f1_success["attempt_sequence"] = ""
    elif name == "orphan_event":
        f1_success["shipment_id"] = "SHP999999001"
    elif name == "success_after_cutoff":
        f1_success["occurred_at_utc"] = iso(plus(cutoff, seconds=1))
        f1["attempt_history_attempt_count"] = "0"
    elif name in ("booking_after_cutoff", "booking_at_cutoff"):
        new_booked = plus(cutoff, seconds=1) if name == "booking_after_cutoff" else cutoff
        row = shipment_row("SHP900000015", "CUS0001", "EXPRESS", "DEP01", "DEP02",
                           new_booked, None, plus(new_booked, hours=24), None, None, config)
        sources["shipments"].append(row)
    elif name == "promise_at_cutoff":
        f4["promised_delivery_at_utc"] = iso(cutoff)
    elif name == "promise_before_handover":
        f1["promised_delivery_at_utc"] = iso(plus(handover, seconds=-1))
    elif name in ("cancel_after_handover", "cancel_tied_handover", "future_cancellation"):
        f1["cancelled_at_utc"] = iso(
            plus(cutoff, hours=1) if name == "future_cancellation" else
            handover if name == "cancel_tied_handover" else plus(handover, hours=13))
    elif name in ("cancel_before_later_handover", "cancel_then_future_handover"):
        when = handover if name == "cancel_before_later_handover" else plus(cutoff, hours=1)
        f13["handed_over_at_utc"] = iso(when)
        if name == "cancel_then_future_handover":
            f13["promised_delivery_at_utc"] = iso(plus(when, hours=24))
    elif name in ("handover_after_cutoff", "handover_after_extract"):
        when = plus(cutoff, hours=1) if name == "handover_after_cutoff" else plus(dt(config["extract_as_of_utc"]), hours=1)
        f12["handed_over_at_utc"] = iso(when)
        f12["promised_delivery_at_utc"] = iso(plus(when, hours=24))
    elif name == "unparseable_handover":
        f1["handed_over_at_utc"] = "broken"
    elif name == "terminal_tie":
        lost = next(row for row in events[f9["shipment_id"]] if row["event_type"] == "LOST")
        success = next(row for row in events[f9["shipment_id"]] if row["event_type"] == "DELIVERY_SUCCEEDED")
        lost["occurred_at_utc"] = success["occurred_at_utc"]
    elif name == "repeated_terminal":
        lost = events["SHP900000006"][0]
        extra(lost, occurred_at_utc=iso(plus(handover, hours=21)),
              recorded_at_utc=iso(plus(handover, hours=22)))
    elif name == "progress_after_terminal":
        extra(f1_success, event_type="IN_TRANSIT", occurred_at_utc=iso(plus(handover, hours=13)),
              recorded_at_utc=iso(plus(handover, hours=14)),
              physical_attempt_id="", attempt_sequence="")
    elif name in ("no_primary_location", "inactive_primary_location",
                  "quarantined_primary_location", "multiple_primary_locations"):
        loc = next(row for row in sources["customer_locations"] if row["customer_id"] == "CUS0001")
        if name == "no_primary_location":
            loc["is_primary"] = "false"
        elif name == "inactive_primary_location":
            loc["is_active"] = "false"
        else:
            second = copy.deepcopy(loc)
            if name == "multiple_primary_locations":
                second["location_id"] = "LOC90000"
                second["location_name"] = "Cloudfern Second Studio"
            else:
                second["is_primary"] = "false"
            sources["customer_locations"].append(second)
    elif name in ("missing_depot_reference", "missing_service_reference",
                  "missing_customer_reference"):
        field, value = {
            "missing_depot_reference": ("destination_depot_id", "DEP99"),
            "missing_service_reference": ("service_code", "PRIORITY"),
            "missing_customer_reference": ("customer_id", "CUS9999"),
        }[name]
        f1[field] = value
    elif name == "impossible_calendar_date":
        f1["booked_at_utc"] = "2026-02-30T08:00:00Z"
    elif name == "invalid_master_key":
        sources["customers"][0]["customer_id"] = "BAD"
    elif name == "invalid_account_opening":
        sources["customers"][0]["account_opened_at_utc"] = "2026-02-30T08:00:00Z"
    elif name == "invalid_event_occurrence":
        f1_success["occurred_at_utc"] = "2026-02-30T08:00:00Z"
    elif name == "invalid_promise":
        f1["promised_delivery_at_utc"] = "2026-02-30T08:00:00Z"
    elif name == "empty_selected_cohort":
        pass  # The date filter belongs to the test expectation, not a source column.
    elif name == "only_cancelled_bookings":
        sources["shipments"] = [copy.deepcopy(f13)]
        sources["tracking_events"] = []
    elif name == "zero_open_backlog":
        f4["promised_delivery_at_utc"] = iso(plus(cutoff, hours=24))
        shipments["SHP900000008"]["promised_delivery_at_utc"] = iso(plus(cutoff, hours=24))
    else:
        raise ValueError(f"unknown fixture variant {name}")
    return sources


MALFORMED_CASES = {
    "missing_file": "shipments.csv absent: batch failure",
    "bad_header": "shipments.csv header mismatch: batch failure",
    "wrong_field_count": "tracking_events.csv extra field: batch failure",
    "broken_quoting": "customers.csv unbalanced quote: batch failure",
    "invalid_utf8": "depots.csv invalid UTF-8: batch failure",
}


def generate_malformed(base: dict[str, list[dict[str, str]]], config: dict,
                       output_root: Path) -> None:
    for name in MALFORMED_CASES:
        folder = output_root / name
        write_sources(folder, base)
        if name == "missing_file":
            (folder / "shipments.csv").unlink()
        elif name == "bad_header":
            path = folder / "shipments.csv"
            data = path.read_bytes()
            path.write_bytes(data.replace(b"shipment_id,", b"parcel_id,", 1))
        elif name == "wrong_field_count":
            path = folder / "tracking_events.csv"
            lines = path.read_bytes().splitlines(keepends=True)
            lines[1] = lines[1].rstrip(b"\r\n") + b",EXTRA\n"
            path.write_bytes(b"".join(lines))
        elif name == "broken_quoting":
            path = folder / "customers.csv"
            lines = path.read_bytes().splitlines(keepends=True)
            lines[1] = b'CUS0001,"unclosed name,SMALL,2025-12-15T09:00:00Z,1,2026-06-30T10:00:00Z\n'
            path.write_bytes(b"".join(lines))
        elif name == "invalid_utf8":
            path = folder / "depots.csv"
            path.write_bytes(path.read_bytes() + b"\xff")


def generate_all(output_root: Path, config: dict) -> dict:
    expected_root = output_root / "tests" / "expected"
    fixture = fixture_sources(config)
    write_sources(output_root / "tests" / "fixtures" / "known_answer", fixture)
    write_json(expected_root / "fixture.json", FIXTURE_EXPECTED)
    clean, clean_ledger, clean_meta = make_clean(config)
    write_sources(output_root / "datasets" / "clean", clean)
    clean_summary = summary_from_ledger(clean_ledger, config)
    write_json(expected_root / "clean.json", {
        "label": config["label"], "oracle_basis": "planned lifecycle ledger, before CSV serialization",
        "scenario_counts": clean_meta["scenario_counts"],
        "late_recorded_messages": clean_meta["late_recorded_messages"],
        "source_rows": clean_meta["source_rows"], "expected": clean_summary,
        "per_shipment": clean_ledger,
    })
    demo, demo_ledger, demo_meta = make_demo(clean, clean_ledger, config)
    write_sources(output_root / "datasets" / "demo", demo)
    demo_summary = summary_from_ledger(demo_ledger, config)
    write_json(expected_root / "demo.json", {
        "label": config["label"],
        "oracle_basis": "planned lifecycle ledger plus documented anomaly operations; no CSV parsing",
        "source_rows": demo_meta["source_rows"], "expected": demo_summary,
        "operations": demo_meta["operations"], "per_shipment": demo_ledger,
    })
    variants_root = output_root / "tests" / "fixtures" / "variants"
    for name in VARIANT_ASSERTIONS:
        write_sources(variants_root / name, make_variant(name, fixture, config))
    generate_malformed(fixture, config, output_root / "tests" / "fixtures" / "malformed")
    write_json(expected_root / "variants.json", {
        "oracle_basis": "reviewed Step 2 edge cases; statements fixed before future warehouse implementation",
        "variants": VARIANT_ASSERTIONS, "malformed_file_cases": MALFORMED_CASES,
    })
    return {"fixture": FIXTURE_EXPECTED, "clean": clean_summary, "demo": demo_summary,
            "variants": len(VARIANT_ASSERTIONS), "malformed": len(MALFORMED_CASES),
            "clean_rows": clean_meta["source_rows"], "demo_rows": demo_meta["source_rows"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Generate fixture, clean/demo, and variants")
    parser.add_argument("--output", type=Path, default=ROOT,
                        help="Project-shaped output directory (default: this repository)")
    args = parser.parse_args()
    if not args.all:
        parser.error("Step 3 uses --all so fixture and expectations stay in sync")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    result = generate_all(args.output, config)
    print(json.dumps({"python": sys.version.split()[0], **result}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
