"""Focused regressions for the demo generator's independent attempt ledger.

These tests inspect planned anomaly operations and approved test expectations.
They do not import or execute warehouse transformations.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import generate_data  # noqa: E402


def valid_occurrence(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def surviving_attempt_ids(events: list[dict[str, str]], shipment_id: str) -> set[str]:
    return {
        event["physical_attempt_id"]
        for event in events
        if event["shipment_id"] == shipment_id
        and event["event_type"] in ("DELIVERY_FAILED", "DELIVERY_SUCCEEDED")
        and valid_occurrence(event["occurred_at_utc"])
    }


class DemoAttemptLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "config" / "generation.json").read_text(encoding="utf-8"))
        cls.approved = json.loads((ROOT / "tests" / "expected" / "demo.json").read_text(encoding="utf-8"))
        cls.clean, cls.clean_ledger, _ = generate_data.make_clean(cls.config)
        cls.demo, cls.demo_ledger, cls.demo_meta = generate_data.make_demo(
            cls.clean, cls.clean_ledger, cls.config
        )
        cls.affected = cls.demo_meta["operations"]["missing_terminal_occurrence"]["shipment_ids"]

    def assert_attempt_counts(self, ledger: dict[str, dict]) -> None:
        for shipment_id in self.affected:
            observed = surviving_attempt_ids(self.demo["tracking_events"], shipment_id)
            self.assertEqual(
                ledger[shipment_id]["attempt_count"], len(observed),
                f"stale physical-attempt count for {shipment_id}",
            )
            self.assertEqual(
                ledger[shipment_id]["attempt_count"],
                self.approved["per_shipment"][shipment_id]["attempt_count"],
                f"generated oracle differs from approved expectation for {shipment_id}",
            )

    def test_missing_occurrence_group_matches_approved_oracle(self) -> None:
        self.assertEqual(
            self.affected,
            self.approved["operations"]["missing_terminal_occurrence"]["shipment_ids"],
        )
        self.assertEqual(len(self.affected), self.config["demo_anomalies"]["missing_terminal_occurrence"])
        self.assertEqual(len(self.affected), 6)
        self.assert_attempt_counts(self.demo_ledger)

        zero_count = sum(self.demo_ledger[sid]["attempt_count"] == 0 for sid in self.affected)
        self.assertEqual(zero_count, 5)
        surviving = [sid for sid in self.affected if self.demo_ledger[sid]["attempt_count"] > 0]
        self.assertEqual(len(surviving), 1)
        survivor = surviving[0]
        blanked_ids = {
            event["physical_attempt_id"]
            for event in self.demo["tracking_events"]
            if event["shipment_id"] == survivor
            and event["event_type"] == "DELIVERY_SUCCEEDED"
            and not event["occurred_at_utc"]
        }
        self.assertEqual(len(blanked_ids), 1)
        self.assertEqual(surviving_attempt_ids(self.demo["tracking_events"], survivor), blanked_ids)

    def test_stale_one_count_is_rejected(self) -> None:
        missing_attempt = next(sid for sid in self.affected if self.demo_ledger[sid]["attempt_count"] == 0)
        stale_ledger = dict(self.demo_ledger)
        stale_ledger[missing_attempt] = {**self.demo_ledger[missing_attempt], "attempt_count": 1}
        with self.assertRaisesRegex(AssertionError, "stale physical-attempt count"):
            self.assert_attempt_counts(stale_ledger)

    def test_prior_valid_attempt_survives_terminal_anomaly(self) -> None:
        # Build a separate, in-memory two-attempt plan. The anomaly removes
        # its successful second attempt, but its earlier failed visit survives.
        shipment_id = next(
            sid for sid in self.affected
            if self.clean_ledger[sid]["attempt_count"] == 1
            and len([
                event for event in self.clean["tracking_events"]
                if event["shipment_id"] == sid and event["event_type"] == "DELIVERY_SUCCEEDED"
            ]) == 1
        )
        clean = copy.deepcopy(self.clean)
        ledger = copy.deepcopy(self.clean_ledger)
        terminal = next(
            event for event in clean["tracking_events"]
            if event["shipment_id"] == shipment_id and event["event_type"] == "DELIVERY_SUCCEEDED"
        )
        booking = next(row for row in clean["shipments"] if row["shipment_id"] == shipment_id)
        handover = datetime.strptime(booking["handed_over_at_utc"], "%Y-%m-%dT%H:%M:%SZ")
        failed_at = handover + timedelta(hours=4)
        self.assertLess(failed_at, datetime.strptime(terminal["occurred_at_utc"], "%Y-%m-%dT%H:%M:%SZ"))

        failure = terminal.copy()
        failure["event_id"] = "EVT" + str(
            max(int(row["event_id"][3:]) for row in clean["tracking_events"]) + 1
        ).zfill(11)
        failure["physical_attempt_id"] = "ATT" + str(
            max(int(row["physical_attempt_id"][3:]) for row in clean["tracking_events"]
                if row["physical_attempt_id"]) + 1
        ).zfill(11)
        failure["event_type"] = "DELIVERY_FAILED"
        failure["attempt_sequence"] = "1"
        failure["occurred_at_utc"] = failed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        failure["recorded_at_utc"] = (failed_at + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        terminal["attempt_sequence"] = "2"
        clean["tracking_events"].append(failure)
        booking["attempt_history_attempt_count"] = "2"
        ledger[shipment_id]["attempt_count"] = 2
        ledger[shipment_id]["first_success"] = False

        changed, changed_ledger, changed_meta = generate_data.make_demo(clean, ledger, self.config)
        self.assertIn(shipment_id, changed_meta["operations"]["missing_terminal_occurrence"]["shipment_ids"])
        self.assertEqual(changed_ledger[shipment_id]["attempt_count"], 1)
        self.assertEqual(surviving_attempt_ids(changed["tracking_events"], shipment_id),
                         {failure["physical_attempt_id"]})
        self.assertIsNone(changed_ledger[shipment_id]["first_success"])
        self.assertEqual(changed_ledger[shipment_id]["outcome"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
