"""Regression tests for the Module 2 review findings, on hand-built SYNTHETIC fixtures (no client data).

Each class replays the finding's negative reproduction: scope changes during multi-read requests,
absent vs unreported days, MSL filter populations, unranked stores, analytics lock/session lifecycle
and store-specific MSL in the store detail. Fixtures are shared with test_module2_analytics
(stores A-C, manager mgr sees A-C, representative rep sees A and C) and
test_module2_business_priority (D has no price so no score, E has no model score).
"""

import csv
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from data_platform.access import DataUnavailable
from modules.module_2 import business_priority as bp
from modules.module_2 import service as service_module

try:
    from tests import test_module2_analytics as analytics
    from tests import test_module2_business_priority as priority
except ImportError:                                    # discovered with -s tests
    import test_module2_analytics as analytics
    import test_module2_business_priority as priority

D, day = analytics.D, analytics.day


def store_ids(response):
    return sorted(r["store_id"] for r in response.rows)


class ScopeChangeDuringLoadTests(analytics.Base):
    """R-01: a scope change between the reads of one request never returns or caches revoked stores."""

    def change_during(self, view, action, method="read"):
        real = getattr(self.service.reader, method)
        fired = {"n": 0}

        def intercept(name, *args, **kwargs):
            out = real(name, *args, **kwargs)
            if name == view and not fired["n"]:
                fired["n"] = 1
                action()
            return out
        setattr(self.service.reader, method, intercept)
        return fired

    def cached_for(self, principal_id):
        return [k for k in self.service._cache if principal_id in k]

    def test_priorities_discard_a_load_whose_scope_changed(self):
        fired = self.change_during("negotiation_context",
                                   lambda: self.sql("DELETE FROM gold.account_scope WHERE principal_id='rep' "
                                                    "AND store_id='C'"))
        response = self.service.get_priorities(self.rep, {"view": "all"})
        self.assertEqual(fired["n"], 1)
        self.assertEqual(response.status, "Unavailable")
        self.assertEqual(response.rows, [])
        self.assertFalse(self.cached_for("rep"))                          # the stale load was not cached
        again = self.service.get_priorities(self.rep, {"view": "all"})
        self.assertEqual((again.status, store_ids(again)), ("Ok", ["A"]))
        exported = list(csv.DictReader(io.StringIO(self.service.export_visit_plan(self.rep, {"view": "all"}))))
        self.assertEqual([r["store_id"] for r in exported], ["A"])
        with self.assertRaises(PermissionError):
            self.service.get_store_detail(self.rep, "C")

    def test_full_revocation_during_load_is_denied(self):
        self.change_during("negotiation_context",
                           lambda: self.sql("DELETE FROM gold.account_scope WHERE principal_id='rep'"))
        response = self.service.get_priorities(self.rep, {"view": "all"})
        self.assertEqual((response.status, response.rows), ("Permission_denied", []))
        self.assertFalse(self.cached_for("rep"))

    def test_logout_during_load_is_not_undone(self):
        self.change_during("negotiation_context", lambda: self.service.clear("rep"))
        response = self.service.get_priorities(self.rep, {"view": "all"})
        self.assertEqual(response.status, "Unavailable")
        self.assertIn("session ended", response.message)
        self.assertFalse(self.cached_for("rep"))

    def test_population_and_detail_recheck_after_their_reads(self):
        self.change_during("negotiation_context",
                           lambda: self.sql("DELETE FROM gold.account_scope WHERE principal_id='rep' "
                                            "AND store_id='C'"))
        self.assertEqual(self.service.get_population(self.rep).status, "Unavailable")
        self.assertFalse(self.cached_for("rep"))
        self.sql("INSERT INTO gold.account_scope VALUES ('rep','C','sales','v2')")
        self.change_during("store_sku_daily",
                           lambda: self.sql("DELETE FROM gold.account_scope WHERE principal_id='rep' "
                                            "AND store_id='C'"), method="read_page")
        detail = self.service.get_store_detail(self.rep, "A")
        self.assertEqual(detail.status, "Unavailable")
        self.assertEqual(detail.detail, {})

    def test_release_change_during_detail_is_not_returned(self):
        record = self.service.reader.record
        self.change_during("store_sku_daily", lambda: record.update(artifact_id="another-release"),
                           method="read_page")
        detail = self.service.get_store_detail(self.mgr, "A")
        self.assertEqual((detail.status, detail.detail), ("Unavailable", {}))
        self.assertIn("release changed", detail.message)


class CoverageSemanticsTests(analytics.Base):
    """R-02: absent dates are not 'unknown days'; unreported rows and coverage are reported separately."""

    def days(self, store):
        return self.service.get_store_detail(self.mgr, store).detail["business"]["oos_days"]

    def test_absent_rows_null_rows_and_missing_as_of_are_distinct(self):
        b, c = self.days("B"), self.days("C")
        self.assertEqual((b["P3"]["unreported_days"], b["P3"]["as_of_report"], b["P3"]["current"]),
                         (1, "unreported", None))                       # a row exists, state not reported
        self.assertEqual((b["P1"]["as_of_report"], b["P1"]["current"], b["P1"]["days_with_rows"]),
                         ("missing", None, 27))                         # no as-of row at all
        self.assertEqual(b["P1"]["unreported_days"], 0)                 # the absent date is not counted
        self.assertEqual((c["P3"]["days_with_rows"], c["P3"]["unreported_days"]), (25, 0))   # 3 dates without rows
        for counts in (*b.values(), *c.values()):
            self.assertNotIn("unknown_days", counts)
            self.assertEqual(counts["reported_days"] + counts["unreported_days"], counts["days_with_rows"])
            self.assertEqual(counts["window_days"], 28)
        note = self.service.get_store_detail(self.mgr, "C").detail["business"]["oos_coverage_note"]
        self.assertIn("not counted as missing reports", note)

    def test_partial_activation_counts_only_existing_rows(self):
        self.sql("DELETE FROM gold.mart_store_sku_daily WHERE store_id='A' AND sku_id='P2' AND date < ?", [day(9)])
        a = self.days("A")["P2"]
        self.assertEqual((a["days_with_rows"], a["unreported_days"], a["reported_oos_days"]), (10, 0, 1))


class MslPopulationTests(analytics.Base):
    """R-03: per-observation MSL grouping is kept per store, so filters select the same population."""

    def test_per_store_groups_reconcile_with_sku_groups(self):
        skus = self.skus(self.oos(self.mgr, "28d"))
        p2 = {s["store_id"]: s["msl"] for s in skus["P2"]["stores"]}
        self.assertEqual(p2, {"A": {"MSL": 1, "Non-MSL": 0, "Unknown": 0}, "B": {"MSL": 0, "Non-MSL": 1, "Unknown": 0}})
        for sku in skus.values():
            for group in ("MSL", "Non-MSL", "Unknown"):
                self.assertEqual(sum(s["msl"][group] for s in sku["stores"]), sku["msl"][group])
            self.assertTrue(all(sum(s["msl"].values()) == s["oos"] for s in sku["stores"]))

    def test_time_varying_and_unknown_msl_within_scope(self):
        self.sql("UPDATE gold.mart_store_sku_daily SET is_msl=false WHERE store_id='A' AND sku_id='P1' AND date=?",
                 [day(1)])
        p1 = {s["store_id"]: s["msl"] for s in self.skus(self.oos(self.mgr, "28d"))["P1"]["stores"]}
        self.assertEqual(p1["A"], {"MSL": 3, "Non-MSL": 1, "Unknown": 0})
        self.assertEqual(p1["C"], {"MSL": 0, "Non-MSL": 0, "Unknown": 1})
        rep = self.skus(self.oos(self.rep, "28d"))["P1"]["stores"]
        self.assertEqual(sorted(s["store_id"] for s in rep), ["A", "C"])     # B is outside the rep's scope


class UnrankedStoreTests(priority.ServiceTests):
    """R-05: stores without a priority score get no rank, stay visible and are listed last."""

    def test_score_unavailable_and_unscored_have_no_rank(self):
        rows = {r["store_id"]: r for r in self.rows(self.mgr).rows}
        self.assertIsNone(rows["D"]["priority_score"])                 # no price: score unavailable
        self.assertIsNone(rows["E"]["display_rank"])                   # no usable model score
        self.assertEqual((rows["D"]["overall_rank"], rows["E"]["overall_rank"]), (None, None))
        ranked = sorted(r["overall_rank"] for r in rows.values() if r["overall_rank"] is not None)
        self.assertEqual(ranked, list(range(1, len(ranked) + 1)))

    def test_unranked_stores_are_last_in_every_sort(self):
        for key in bp.SORT_KEYS:
            for direction in ("asc", "desc"):
                order = [r["store_id"] for r in self.rows(self.mgr, sort=(key, direction)).rows]
                with self.subTest(key=key, direction=direction):
                    self.assertEqual(order[-1], "E")                   # no model score: nothing to sort by
                    if key in ("rank", "score", "sales_at_risk"):      # D lacks only these (no price)
                        self.assertEqual(order[-2:], ["D", "E"])       # unranked in stored visit order

    def test_detail_export_and_analytics_keep_null_ranks(self):
        self.assertIsNone(self.service.get_store_detail(self.mgr, "E").detail["store"]["overall_rank"])
        exported = {r["store_id"]: r for r in csv.DictReader(io.StringIO(
            self.service.export_visit_plan(self.mgr, {"view": "all"})))}
        self.assertEqual((exported["D"]["overall_rank"], exported["E"]["overall_rank"]), ("", ""))


class AnalyticsLifecycleTests(analytics.Base):
    """R-06: bounded per-key locks; a logout or failure during a build is never undone by that build."""

    def test_unique_filters_leave_no_locks_behind(self):
        for i in range(40):
            self.oos(self.mgr, search=f"store-{i}")
        self.assertLessEqual(len(self.service._analytics_cache()), service_module.ANALYTICS_CACHE_ENTRIES)
        self.assertEqual(self.service.__dict__.get("_analytics_locks"), {})
        self.service.clear("mgr")
        self.assertEqual(self.service.__dict__.get("_analytics_locks"), {})

    def test_logout_after_the_aggregate_completes_is_not_undone(self):
        real = self.service._aggregate_oos

        def then_logout(*args, **kwargs):
            out = real(*args, **kwargs)
            self.service.clear("mgr")                                 # the reviewer's interleaving
            return out
        self.service._aggregate_oos = then_logout
        response = self.oos(self.mgr)
        self.assertEqual(response.status, "Unavailable")
        self.assertFalse([k for k in self.service._analytics_cache() if "mgr" in k])
        self.assertEqual(self.service.__dict__.get("_analytics_locks"), {})

    def test_failure_releases_the_lock_and_caches_nothing(self):
        def failing(*args, **kwargs):
            raise DataUnavailable("page failed")
        self.service._aggregate_oos = failing
        self.assertEqual(self.oos(self.mgr).status, "Unavailable")
        self.assertEqual(self.service.__dict__.get("_analytics_locks"), {})
        self.assertFalse(self.service._analytics_cache())

    def test_concurrent_same_key_builds_once(self):
        calls, real = {"n": 0}, self.service._aggregate_oos

        def slow(*args, **kwargs):
            calls["n"] += 1
            time.sleep(0.2)
            return real(*args, **kwargs)
        self.service._aggregate_oos = slow
        self.oos(self.mgr)                                            # warm the shared priorities cache only
        self.service.clear_analytics()
        calls["n"] = 0
        results, barrier = [], threading.Barrier(3)

        def work():
            barrier.wait()
            results.append(self.oos(self.mgr, "28d"))
        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls["n"], 1)
        self.assertEqual({r.status for r in results}, {"Ok"})
        self.assertEqual(len({json.dumps(r.detail, sort_keys=True, default=str) for r in results}), 1)
        self.assertEqual(self.service.__dict__.get("_analytics_locks"), {})


class StoreSpecificMslTests(analytics.Base):
    """R-07: the store detail uses the as-of store x SKU MSL value, never the product default."""

    def detail(self, store):
        return self.service.get_store_detail(self.mgr, store).detail

    def test_as_of_values_unknown_and_missing_reports(self):
        c = self.detail("C")
        self.assertIsNone({s["sku_id"]: s for s in c["skus"]}["P1"]["is_msl"])          # as-of null, product True
        self.assertIsNone({r["sku_id"]: r for r in c["reported_now"]}["P1"]["is_msl"])
        b = self.detail("B")
        self.assertIsNone({s["sku_id"]: s for s in b["skus"]}["P1"]["is_msl"])          # no as-of report
        reported = {r["sku_id"]: r for r in b["reported_now"]}
        self.assertEqual((reported["P1"]["is_as_of"], reported["P1"]["date"]), (False, day(1).isoformat()))
        self.assertIs(reported["P2"]["is_msl"], False)                                   # P2 is not MSL in B
        self.assertIs({r["sku_id"]: r for r in self.detail("A")["reported_now"]}["P2"]["is_msl"], True)

    def test_action_text_does_not_promote_unknown_msl(self):
        drivers = analytics.drivers(True, 0.0)                     # C/P1 out of stock now, MSL unknown as of D
        self.sql("UPDATE gold.predictions_oos SET drivers_json=? WHERE store_id='C'", [json.dumps(drivers)])
        actions = self.detail("C")["business"]["actions"]
        self.assertFalse(any(a.startswith("Check shelf and backroom stock for MSL") for a in actions))
        self.assertIn("Check replenishment for out-of-stock SKU(s): P1.", actions)

    def test_unscored_store_detail(self):
        self.sql("DELETE FROM gold.predictions_oos WHERE store_id='C'")
        self.sql("DELETE FROM gold.mart_visit_priority WHERE store_id='C'")
        c = self.detail("C")
        self.assertIsNone(c["store"]["overall_rank"])
        self.assertFalse(c["business"]["available"])
        self.assertEqual(c["skus"], [])
        self.assertIsNone({r["sku_id"]: r for r in c["reported_now"]}["P1"]["is_msl"])
        self.assertEqual(c["business"]["oos_days"]["P3"]["days_with_rows"], 25)


class ScenarioBoundaryTests(unittest.TestCase):
    def test_summary_and_path_agree_for_fractional_receipt_days(self):
        for case in [(0, 2, 10, 2.5), (5, 2, 30, 0.2), (0, 2, 10, 6.5), (0, 2, 10, 7.5), (3.0, 2.0, 10.0, 3)]:
            path = bp.scenario_path(*case)
            self.assertAlmostEqual(sum(d["short"] for d in path), bp.shortfall_units(*case), msg=case)
        self.assertEqual(bp.receipt_day(10, 2.5), 3)
        self.assertIsNone(bp.receipt_day(10, 7.5))
        self.assertIsNone(bp.receipt_day(0, 2))


if __name__ == "__main__":
    unittest.main()
