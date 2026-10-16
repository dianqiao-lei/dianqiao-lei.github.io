"""Local synthetic fixtures only; no production API/collector calls."""

import copy
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / "update-51la-snapshot.py"
spec = importlib.util.spec_from_file_location("snapshot_51la", MODULE)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

NOW = datetime(2026, 10, 17, 2, 23, tzinfo=timezone.utc)
CUTOFF = "2026-10-15T13:19:58.040Z"
MASK = "fixtureMask123"
BASELINE = {
    "places": [{"lat": 34.1, "lon": -118.2, "cc": "US", "city": "Los Angeles", "n": 13}],
    "mappedPageviews": 13, "unmappedPageviews": 0,
    "startedAt": "2026-10-15T12:26:19.968Z", "updatedAt": CUTOFF,
}

# Deliberately synthetic MMDB records. These never leave test memory and are not
# assertions about the actual location of any public IP address.
CHINA = {"country": {"iso_code": "CN"}, "city": {"names": {"zh-CN": "北京市"}},
         "location": {"latitude": 39.9042, "longitude": 116.4074}}
USA = {"country": {"iso_code": "US"}, "city": {"names": {"en": "Los Angeles"}},
       "location": {"latitude": 34.0522, "longitude": -118.2437}}


def lookup(address):
    return {"223.5.5.5": CHINA, "8.8.8.8": USA}.get(address)


def visit(number, pv=1, day="2026-10-16", ip="223.5.5.5"):
    return {"ip": ip, "pv": pv, "dateTime": f"{day} 12:00:{number % 60:02d}",
            "uuid": f"private-fixture-user-{number}", "region": "ignored-free-text",
            "entryPage": "https://fixture.invalid/private-path", "srcUrl": "https://private-referrer.invalid"}


def page(records, number=1, total=None):
    total = len(records) if total is None else total
    return {"success": True, "code": "0000", "curPage": number, "pageSize": 100,
            "total": total, "pages": (total + 99) // 100, "data": records}


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def query(self, day, number):
        self.calls.append((day, number))
        response = self.responses[(day, number)]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class SnapshotTests(unittest.TestCase):
    def state(self, used=0):
        return sync.initial_state(BASELINE, MASK, CUTOFF, NOW, used)

    def budget(self, state, monthly=90, run=3):
        writes = []
        return sync.Budget(state, NOW, monthly, run, lambda value: writes.append(copy.deepcopy(value))), writes

    def legacy_state(self, used=0, day="2026-10-16", total=3, sessions=1):
        state = self.state(used)
        state["version"] = 1
        state["days"][day] = {
            "places": [{"lat": 34.1, "lon": -118.2, "cc": "US", "city": "Los Angeles", "n": total}],
            "mappedPageviews": total, "unmappedPageviews": 0,
            "startedAt": f"{day}T14:00:00.000Z", "updatedAt": f"{day}T15:00:00.000Z",
            "sessionCount": sessions, "complete": False,
        }
        return state

    def collect(self, responses, state=None, day="2026-10-16", **budget_options):
        state = state or self.state()
        budget, writes = self.budget(state, **budget_options)
        client = Client(responses)
        value = sync.collect_day(day, client, lookup, sync.parse_stamp(CUTOFF, NOW), NOW, budget)
        return value, state, client, writes

    def test_signature_follows_documented_sha256_sorted_fields(self):
        at = datetime.fromtimestamp(1624939232345 / 1000, timezone.utc)
        payload = sync.signed_payload("fixture-access", "fixture-secret", MASK, "2026-10-16", 1, at, "1234")
        self.assertEqual(payload["sign"], "0CA4C43F9363DBF98E45A5DF12395617D2BFB7D129BC4B9DC14204ADF6B252E6")
        self.assertEqual(payload["timestamp"], "1624939232345")
        self.assertEqual(payload["size"], 100)
        self.assertNotIn("secret", json.dumps(payload))
        with patch.object(sync, "read_response", return_value=page([])) as request:
            sync.APIClient("fixture-access", "fixture-secret", MASK).query("2026-10-16", 1)
        self.assertEqual(request.call_args.args[0].full_url,
                         "https://v6-open.51.la/open/visitor/detail/page/list")

    def test_each_row_counts_once_with_unknowns_and_no_raw_fields(self):
        records = [visit(1, 3), visit(2, 2, ip="8.8.8.8"), visit(3, 4, ip="10.0.0.1"), visit(4, 0)]
        daily, state, _, writes = self.collect({("2026-10-16", 1): page(records)})
        self.assertEqual(daily["mappedPageviews"], 3)
        self.assertEqual(daily["unmappedPageviews"], 1)
        self.assertEqual(daily["recordCount"], 4)
        self.assertTrue(daily["complete"])
        self.assertEqual(daily["places"][0], {"lat": 39.9, "lon": 116.4, "cc": "CN", "city": "北京市", "n": 2})
        self.assertEqual(state["requests"]["2026-10"], 1)
        # The charged ledger is persisted before processing any visitor record.
        self.assertEqual(writes[0]["days"], {})
        dumped = json.dumps([daily, writes], ensure_ascii=False)
        for secret in ["223.5.5.5", "8.8.8.8", "10.0.0.1", "private-fixture-user", "private-path", "private-referrer"]:
            self.assertNotIn(secret, dumped)

    def test_cityless_and_bad_coordinates_never_become_fake_city_centers(self):
        for record in [None, {}, {"country": {"iso_code": "CN"}, "location": CHINA["location"]},
                       {**CHINA, "location": {"latitude": 0, "longitude": 0}},
                       {**CHINA, "location": {"latitude": True, "longitude": 12}},
                       {**CHINA, "location": {"latitude": float("nan"), "longitude": 12}}]:
            self.assertIsNone(sync.place_from_mmdb(record))

    def test_complete_two_page_day_counts_each_page_once(self):
        records = [visit(i) for i in range(101)]
        daily, _, client, _ = self.collect({("2026-10-16", 1): page(records[:100], total=101),
                                           ("2026-10-16", 2): page(records[100:], 2, 101)})
        self.assertEqual(daily["mappedPageviews"], 101)
        self.assertEqual(daily["recordCount"], 101)
        self.assertEqual(client.calls, [("2026-10-16", 1), ("2026-10-16", 2)])

    def test_empty_day_is_valid_without_inventing_coordinates(self):
        for empty_pages in (0, 1):
            response = page([])
            response["pages"] = empty_pages
            daily, _, _, _ = self.collect({("2026-10-16", 1): response})
            self.assertEqual(daily["places"], [])
            self.assertEqual(daily["mappedPageviews"], 0)
            self.assertIsNone(daily["startedAt"])

    def test_page_drift_and_truncation_are_rejected(self):
        first = [visit(i) for i in range(100)]
        alternatives = [page([visit(100)], 2, 102), page([], 2, 101)]
        for second in alternatives:
            with self.subTest(second=second["total"]):
                with self.assertRaisesRegex(sync.SyncError, "invalid_response"):
                    self.collect({("2026-10-16", 1): page(first, total=101), ("2026-10-16", 2): second})

    def test_failed_second_page_keeps_entire_previous_day_and_charges_both(self):
        state = self.state()
        original, _, _, _ = self.collect({("2026-10-16", 1): page([visit(i) for i in range(3)])})
        original["updatedAt"] = "2026-10-16T14:00:00.000Z"
        original["complete"] = False
        state["days"]["2026-10-16"] = original
        client = Client({("2026-10-16", 1): page([visit(i) for i in range(100)], total=101),
                         ("2026-10-16", 2): sync.SyncError("request_failed")})
        writes = []
        snapshot, refreshed, issues = sync.sync(state, client, lookup, NOW, lambda v: writes.append(copy.deepcopy(v)))
        self.assertEqual(state["days"]["2026-10-16"], original)
        self.assertEqual(snapshot["mappedPageviews"], 16)
        self.assertEqual(snapshot["updatedAt"], original["updatedAt"])
        self.assertEqual(state["requests"]["2026-10"], 2)
        self.assertEqual(refreshed, [])
        self.assertEqual(issues, ["request_failed"])
        self.assertNotIn("223.5.5.5", json.dumps(writes))

    def test_quota_or_run_budget_cannot_commit_first_page_only(self):
        for monthly, run, expected in [(1, 3, "quota_exhausted"), (90, 1, "run_limit")]:
            state = self.state()
            client = Client({("2026-10-16", 1): page([visit(i) for i in range(100)], total=101)})
            snapshot, refreshed, issues = sync.sync(state, client, lookup, NOW, lambda _: None,
                                                    monthly_limit=monthly, run_limit=run)
            self.assertEqual(state["days"], {})
            self.assertEqual(snapshot["mappedPageviews"], 13)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(issues, [expected])

    def test_fully_used_month_makes_no_request(self):
        state, client = self.state(90), Client({})
        snapshot, _, issues = sync.sync(state, client, lookup, NOW, lambda _: None)
        self.assertEqual(client.calls, [])
        self.assertEqual(snapshot["updatedAt"], CUTOFF)
        self.assertEqual(issues, ["quota_exhausted"])

    def test_daily_replacement_and_current_day_finalization_do_not_double_count(self):
        state = self.state()
        first_now = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
        record = {**visit(1, 2, "2026-10-15"), "dateTime": "2026-10-15 22:00:00"}
        client = Client({("2026-10-15", 1): page([record, copy.deepcopy(record)])})
        snapshot, _, _ = sync.sync(state, client, lookup, first_now, lambda _: None, include_today=True)
        self.assertEqual(snapshot["mappedPageviews"], 15)
        self.assertFalse(state["days"]["2026-10-15"]["complete"])
        second_now = first_now + timedelta(days=1)
        client = Client({("2026-10-15", 1): page([copy.deepcopy(record) for _ in range(4)])})
        snapshot, _, _ = sync.sync(state, client, lookup, second_now, lambda _: None)
        self.assertEqual(snapshot["mappedPageviews"], 17)
        self.assertTrue(state["days"]["2026-10-15"]["complete"])
        self.assertEqual(snapshot["coverage"]["completeThrough"], "2026-10-15")
        # A rerun on the same day does not spend quota re-fetching a settled day.
        no_calls = Client({})
        snapshot, _, _ = sync.sync(state, no_calls, lookup, second_now, lambda _: None)
        self.assertEqual(no_calls.calls, [])
        self.assertEqual(snapshot["mappedPageviews"], 17)

    def test_cutover_and_wrong_day_data_are_checked(self):
        before = {**visit(1, 9, "2026-10-15"), "dateTime": "2026-10-15 21:19:58"}
        after = {**visit(2, 3, "2026-10-15"), "dateTime": "2026-10-15 21:20:00"}
        daily, _, _, _ = self.collect({("2026-10-15", 1): page([before, after, copy.deepcopy(after), copy.deepcopy(after)])}, day="2026-10-15")
        self.assertEqual(daily["mappedPageviews"], 3)
        self.assertEqual(daily["recordCount"], 3)
        with self.assertRaisesRegex(sync.SyncError, "invalid_response"):
            self.collect({("2026-10-16", 1): page([after])})

    def test_corrupt_db_aborts_without_marking_day_synced(self):
        def corrupt(_):
            raise ValueError("Never print this raw address 223.5.5.5")
        state = self.state()
        snapshot, refreshed, issues = sync.sync(state, Client({("2026-10-16", 1): page([visit(1)])}), corrupt,
                                               NOW, lambda _: None)
        self.assertEqual(issues, ["geolocation_failed"])
        self.assertEqual(state["days"], {})
        self.assertEqual(snapshot["mappedPageviews"], 13)

    def test_documented_pv_field_is_not_a_weight_and_uuid_is_optional(self):
        for value in [0, 9, True, -1, 1.5, "3", None]:
            record = visit(1, value)
            del record["uuid"]
            daily, _, _, _ = self.collect({("2026-10-16", 1): page([record])})
            self.assertEqual(daily["mappedPageviews"], 1)
            self.assertEqual(daily["recordCount"], 1)

    def test_same_browser_can_have_multiple_locations_and_identical_time_loads(self):
        same = visit(1, 0)
        rows = [same, copy.deepcopy(same), {**same, "ip": "8.8.8.8"}]
        daily, _, _, _ = self.collect({("2026-10-16", 1): page(rows)})
        self.assertEqual(daily["mappedPageviews"], 3)
        self.assertEqual([(p["cc"], p["n"]) for p in daily["places"]], [("CN", 2), ("US", 1)])
        # Identical records across the pagination boundary can also be real.
        daily, _, _, _ = self.collect({("2026-10-16", 1): page([same] * 100, total=101),
                                       ("2026-10-16", 2): page([same], 2, 101)})
        self.assertEqual(daily["mappedPageviews"], 101)

    def test_v1_migration_preserves_original_aggregate_and_request_ledger(self):
        legacy = self.legacy_state(7)
        upgraded = sync.validate_state(legacy, NOW, MASK)
        self.assertEqual(upgraded["version"], 2)
        self.assertEqual(upgraded["baseline"], legacy["baseline"])
        self.assertEqual(upgraded["requests"], {"2026-10": 7})
        self.assertEqual(upgraded["days"]["2026-10-16"]["method"], "session-ip")
        self.assertEqual(upgraded["days"]["2026-10-16"]["recordCount"], 1)
        self.assertEqual(sync.build_snapshot(upgraded, NOW, 90)["mappedPageviews"], 16)
        self.assertEqual(sync.validate_state(upgraded, NOW, MASK), upgraded)
        self.assertEqual(legacy["version"], 1)  # The input itself was not mutated.

    def test_same_total_migration_redistributes_locations_without_double_count(self):
        state = sync.validate_state(self.legacy_state(4, sessions=8), NOW, MASK)
        corrected = [visit(1, 0), visit(2, 0), visit(3, 0, ip="8.8.8.8")]
        client = Client({("2026-10-16", 1): page(corrected), ("2026-10-15", 1): page([])})
        snapshot, refreshed, issues = sync.sync(state, client, lookup, NOW, lambda _: None)
        self.assertEqual(issues, [])
        self.assertIn("2026-10-16", refreshed)
        self.assertEqual(snapshot["mappedPageviews"], 16)
        self.assertEqual([(p["cc"], p["n"]) for p in snapshot["places"]], [("US", 14), ("CN", 2)])
        self.assertEqual(state["days"]["2026-10-16"]["method"], "pageview-ip")
        self.assertEqual(state["requests"]["2026-10"], 6)
        self.assertEqual(sync.sync(state, Client({}), lookup, NOW, lambda _: None)[2], [])
        self.assertEqual(sync.build_snapshot(state, NOW, 90)["mappedPageviews"], 16)

    def test_corrected_method_wins_both_merge_orders_even_if_legacy_stamp_is_later(self):
        legacy = sync.validate_state(self.legacy_state(9, sessions=8), NOW, MASK)
        daily, _, _, _ = self.collect({("2026-10-16", 1): page([visit(i) for i in range(3)])})
        daily["updatedAt"], daily["complete"] = "2026-10-16T14:30:00.000Z", False
        corrected = self.state(4)
        corrected["days"]["2026-10-16"] = daily
        for first, second in [(legacy, corrected), (corrected, legacy)]:
            merged = sync.merge_states(sync.validate_state(first, NOW, MASK), sync.validate_state(second, NOW, MASK))
            self.assertEqual(merged["days"]["2026-10-16"], daily)
            self.assertEqual(merged["requests"]["2026-10"], 9)
            self.assertEqual(sync.build_snapshot(merged, NOW, 90)["mappedPageviews"], 16)

    def test_failed_migration_keeps_the_legacy_day_and_charged_request(self):
        state = sync.validate_state(self.legacy_state(4), NOW, MASK)
        old = copy.deepcopy(state["days"]["2026-10-16"])
        client = Client({("2026-10-16", 1): page([visit(i) for i in range(100)], total=101),
                         ("2026-10-16", 2): sync.SyncError("request_failed")})
        _, refreshed, issues = sync.sync(state, client, lookup, NOW, lambda _: None)
        self.assertEqual(state["days"]["2026-10-16"], old)
        self.assertEqual(refreshed, [])
        self.assertEqual(issues, ["request_failed"])
        self.assertEqual(state["requests"]["2026-10"], 6)

    def test_midnight_recheck_captures_late_arrivals_once_and_preserves_totals(self):
        state = self.state()
        midnight = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)  # Oct 16 in Beijing.
        first = {**visit(1, 0, "2026-10-15"), "dateTime": "2026-10-15 23:59:59"}
        client = Client({("2026-10-15", 1): page([first])})
        snapshot, _, issues = sync.sync(state, client, lookup, midnight, lambda _: None)
        self.assertEqual(issues, [])
        self.assertEqual(snapshot["mappedPageviews"], 14)
        self.assertEqual(sync.days_to_query(state, midnight, False, 14), [])
        next_midnight = midnight + timedelta(days=1)
        self.assertEqual(sync.days_to_query(state, next_midnight, False, 14), ["2026-10-16", "2026-10-15"])
        client = Client({("2026-10-16", 1): page([]), ("2026-10-15", 1): page([first, copy.deepcopy(first)])})
        snapshot, _, issues = sync.sync(state, client, lookup, next_midnight, lambda _: None)
        self.assertEqual(issues, [])
        self.assertEqual(snapshot["mappedPageviews"], 15)
        self.assertEqual(state["requests"]["2026-10"], 3)
        self.assertEqual(sync.days_to_query(state, next_midnight, False, 14), [])
        self.assertNotIn("2026-10-15", sync.days_to_query(state, next_midnight + timedelta(days=1), False, 14))

    def test_migration_and_delayed_rechecks_respect_quota(self):
        state = sync.validate_state(self.legacy_state(90), NOW, MASK)
        original = copy.deepcopy(state["days"])
        client = Client({})
        _, _, issues = sync.sync(state, client, lookup, NOW, lambda _: None)
        self.assertEqual(issues, ["quota_exhausted"])
        self.assertEqual(client.calls, [])
        self.assertEqual(state["days"], original)

    def test_v2_pageview_count_must_equal_all_rows_including_unmapped(self):
        state = sync.validate_state(self.legacy_state(), NOW, MASK)
        state["days"]["2026-10-16"]["method"] = "pageview-ip"
        with self.assertRaisesRegex(sync.SyncError, "invalid_response"):
            sync.validate_state(state, NOW, MASK)

    def test_equal_total_known_unknown_reclassification_is_safe_in_both_directions(self):
        for old_ip, new_ip in [("223.5.5.5", "10.0.0.1"), ("10.0.0.1", "223.5.5.5")]:
            state = self.state()
            old, _, _, _ = self.collect({("2026-10-16", 1): page([visit(i, ip=old_ip) for i in range(3)])})
            old["updatedAt"], old["complete"] = "2026-10-16T14:00:00.000Z", False
            state["days"]["2026-10-16"] = old
            previous = copy.deepcopy(state)
            client = Client({("2026-10-16", 1): page([visit(i, ip=new_ip) for i in range(3)]),
                             ("2026-10-15", 1): page([])})
            snapshot, _, issues = sync.sync(state, client, lookup, NOW, lambda _: None)
            self.assertEqual(issues, [])
            self.assertEqual(snapshot["mappedPageviews"] + snapshot["unmappedPageviews"], 16)
            for a, b in [(state, previous), (previous, state)]:
                merged = sync.merge_states(sync.validate_state(a, NOW, MASK), sync.validate_state(b, NOW, MASK))
                self.assertEqual(merged["days"]["2026-10-16"], state["days"]["2026-10-16"])

    def test_restore_only_migrates_v1_checkout_and_merges_corrected_cache_and_remote(self):
        legacy = self.legacy_state(8, day="2026-10-15")
        corrected = sync.validate_state(legacy, NOW, MASK)
        day = corrected["days"]["2026-10-15"]
        day.update(method="pageview-ip", recordCount=3,
                   places=[{"lat": 39.9, "lon": 116.4, "cc": "CN", "city": "Beijing", "n": 3}])
        corrected["requests"]["2026-10"] = 9
        with tempfile.TemporaryDirectory() as directory:
            state_path, cache, output = [Path(directory) / n for n in ("state.json", "cache.json", "snapshot.json")]
            sync.atomic_json(state_path, legacy)
            sync.atomic_json(cache, corrected)
            with patch.dict(os.environ, {}, clear=True), patch.object(sync, "fetch_state", return_value=legacy), \
                    patch.object(sync.APIClient, "query", side_effect=AssertionError("No API request")), redirect_stdout(io.StringIO()):
                code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output),
                                  "--cache-state", str(cache), "--state-url", "https://fixture.invalid/state.json"])
            self.assertEqual(code, 0)
            saved = json.loads(state_path.read_text())
            self.assertEqual(saved["version"], 2)
            self.assertEqual(saved["baseline"], legacy["baseline"])
            self.assertEqual(saved["requests"]["2026-10"], 9)
            self.assertEqual(saved["days"]["2026-10-15"]["method"], "pageview-ip")
            snapshot = json.loads(output.read_text())
            self.assertEqual(snapshot["mappedPageviews"], 16)
            self.assertEqual([(p["cc"], p["n"]) for p in snapshot["places"]], [("US", 13), ("CN", 3)])

    def test_regressed_daily_response_does_not_delete_real_prior_counts(self):
        state = self.state()
        old, _, _, _ = self.collect({("2026-10-16", 1): page([visit(i) for i in range(10)])})
        old["updatedAt"] = "2026-10-16T14:00:00.000Z"
        old["complete"] = False
        state["days"]["2026-10-16"] = old
        _, _, issues = sync.sync(state, Client({("2026-10-16", 1): page([visit(i) for i in range(2)])}), lookup, NOW, lambda _: None)
        self.assertEqual(issues, ["regression"])
        self.assertEqual(state["days"]["2026-10-16"], old)

    def test_restore_merge_uses_max_quota_latest_day_and_rejects_foreign_baseline(self):
        local, remote = self.state(4), self.state(2)
        old, _, _, _ = self.collect({("2026-10-16", 1): page([visit(i) for i in range(2)])})
        newer = {**old, "places": [{**old["places"][0], "n": 5}], "mappedPageviews": 5, "recordCount": 5}
        old["updatedAt"] = "2026-10-16T14:00:00.000Z"
        old["complete"] = False
        local["days"]["2026-10-16"], remote["days"]["2026-10-16"] = old, newer
        merged = sync.merge_states(sync.validate_state(local, NOW, MASK), sync.validate_state(remote, NOW, MASK))
        self.assertEqual(merged["requests"]["2026-10"], 4)
        self.assertEqual(merged["days"]["2026-10-16"]["mappedPageviews"], 5)
        self.assertEqual(sync.build_snapshot(merged, NOW, 90)["mappedPageviews"], 18)
        remote["baseline"]["places"][0]["n"] = 99
        with self.assertRaises(sync.SyncError):
            sync.merge_states(local, remote)

    def test_state_rejects_injected_identifiers_and_inconsistent_aggregates(self):
        for mutate in [lambda s: s.update(ip="223.5.5.5"),
                       lambda s: s["baseline"]["places"][0].update(uuid="private-id"),
                       lambda s: s["baseline"].update(mappedPageviews=99),
                       lambda s: s.update(maskId="anotherMask"),
                       lambda s: s["requests"].update({"2026-10": True})]:
            state = self.state()
            mutate(state)
            with self.assertRaises(sync.SyncError):
                sync.validate_state(state, NOW, MASK)

    def test_merge_rejects_regression_in_either_source_and_timestamp_conflicts(self):
        earlier, later = self.state(), self.state()
        daily, _, _, _ = self.collect({("2026-10-16", 1): page([visit(i) for i in range(5)])})
        daily["updatedAt"] = "2026-10-16T14:00:00.000Z"
        daily["complete"] = False
        earlier["days"]["2026-10-16"] = copy.deepcopy(daily)
        daily.update(places=[{**daily["places"][0], "n": 2}], mappedPageviews=2, recordCount=2,
                     updatedAt="2026-10-16T15:00:00.000Z")
        later["days"]["2026-10-16"] = daily
        for local, remote in [(earlier, later), (later, earlier)]:
            with self.assertRaisesRegex(sync.SyncError, "state_unavailable"):
                sync.merge_states(sync.validate_state(local, NOW, MASK), sync.validate_state(remote, NOW, MASK))
        daily["updatedAt"] = "2026-10-16T14:00:00.000Z"
        with self.assertRaisesRegex(sync.SyncError, "state_unavailable"):
            sync.merge_states(earlier, later)

    def test_old_missing_days_are_honestly_reported_but_not_queried_forever(self):
        state = self.state()
        later = NOW + timedelta(days=30)
        days = sync.days_to_query(state, later, False, 14)
        self.assertNotIn("2026-10-15", days)
        self.assertEqual(len(days), 14)
        result = sync.build_snapshot(state, later, 90)
        self.assertIn("2026-10-15", result["coverage"]["missingDays"])
        self.assertIsNone(result["coverage"]["completeThrough"])
        budget, _ = self.budget(state)
        # Provider calendar resets the request ledger month, not historic data.
        next_budget = sync.Budget(state, later, 90, 3, lambda _: None)
        next_budget.charge()
        self.assertEqual(state["requests"]["2026-11"], 1)

    def test_restore_only_uses_no_secret_mmdb_or_api(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path, output = Path(directory) / "state.json", Path(directory) / "snapshot.json"
            sync.atomic_json(state_path, self.state(3))
            stdout = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch.object(sync.APIClient, "query", side_effect=AssertionError("No network")), redirect_stdout(stdout):
                code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text())["mappedPageviews"], 13)
            self.assertIn("no API request made", stdout.getvalue())

    def test_remote_failure_retains_cache_and_safe_error_without_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path, cache, output = [Path(directory) / n for n in ("state.json", "cache.json", "snapshot.json")]
            sync.atomic_json(state_path, self.state(1))
            sync.atomic_json(cache, self.state(5))
            stderr = io.StringIO()
            with patch.object(sync, "fetch_state", side_effect=sync.SyncError("request_failed")), redirect_stderr(stderr):
                code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output),
                                  "--cache-state", str(cache), "--state-url", "https://fixture.invalid/state.json"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(state_path.read_text())["requests"]["2026-10"], 5)
            self.assertNotIn("fixture-secret", stderr.getvalue())
            self.assertNotIn("223.5.5.5", stderr.getvalue())

    def test_downloaded_conflicting_or_invalid_remote_never_overwrites_history(self):
        foreign = self.state(20)
        foreign["baseline"]["updatedAt"] = "2026-10-15T13:19:58.039Z"
        invalid = {"private-fixture-ip": "223.5.5.5"}
        for remote in (foreign, invalid):
            with self.subTest(remote_valid=remote is foreign), tempfile.TemporaryDirectory() as directory:
                state_path, cache, output = [Path(directory) / n for n in ("state.json", "cache.json", "snapshot.json")]
                sync.atomic_json(state_path, self.state())
                sync.atomic_json(cache, self.state())
                output.write_text("existing deployed snapshot must remain untouched")
                stderr = io.StringIO()
                with patch.object(sync, "fetch_state", return_value=remote), redirect_stderr(stderr):
                    code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output),
                                      "--cache-state", str(cache), "--state-url", "https://fixture.invalid/state.json"])
                self.assertEqual(code, 1)
                self.assertEqual(output.read_text(), "existing deployed snapshot must remain untouched")
                self.assertNotIn("223.5.5.5", stderr.getvalue())

    def test_first_real_http404_may_publish_seed_without_api_or_fabricated_days(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path, output = Path(directory) / "state.json", Path(directory) / "snapshot.json"
            sync.atomic_json(state_path, self.state())
            with patch.object(sync, "fetch_state", side_effect=sync.StateNotFound()), redirect_stderr(io.StringIO()):
                code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output),
                                  "--state-url", "https://fixture.invalid/state.json"])
            self.assertEqual(code, 2)
            result = json.loads(output.read_text())
            self.assertEqual(result["mappedPageviews"], 13)
            self.assertEqual(result["coverage"]["monthlyRequestsUsed"], 0)
            self.assertEqual(json.loads(state_path.read_text())["days"], {})

    def test_missing_non_initial_remote_without_cache_stops_deployment(self):
        for failure, used in [(sync.StateNotFound(), 5), (sync.SyncError("request_failed"), 0)]:
            with tempfile.TemporaryDirectory() as directory:
                state_path, output = Path(directory) / "state.json", Path(directory) / "snapshot.json"
                sync.atomic_json(state_path, self.state(used))
                with patch.object(sync, "fetch_state", side_effect=failure), redirect_stderr(io.StringIO()):
                    code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output),
                                      "--state-url", "https://fixture.invalid/state.json"])
                self.assertEqual(code, 1)
                self.assertFalse(output.exists())

    def test_downloaded_bad_json_is_not_treated_as_transient_network_failure(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _):
                return b'{"private":"223.5.5.5"'
        class Opener:
            def open(self, *_, **__):
                return Response()
        with patch.object(sync.urllib.request, "build_opener", return_value=Opener()):
            with self.assertRaisesRegex(sync.SyncError, "invalid_response"):
                sync.read_response(sync.urllib.request.Request("https://fixture.invalid/state.json"), state_request=True)

    def test_invalid_cache_fails_closed_and_does_not_replace_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path, cache, output = [Path(directory) / n for n in ("state.json", "cache.json", "snapshot.json")]
            sync.atomic_json(state_path, self.state())
            cache.write_text('{"ip":"223.5.5.5"}')
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = sync.main(["--restore-only", "--mask-id", MASK, "--state", str(state_path), "--output", str(output), "--cache-state", str(cache)])
            self.assertEqual(code, 1)
            self.assertFalse(output.exists())
            self.assertNotIn("223.5.5.5", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
