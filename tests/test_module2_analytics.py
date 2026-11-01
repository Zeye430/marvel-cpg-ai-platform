"""Out-of-stock analytics and store history on a hand-built SYNTHETIC fixture (no client data).

Daily reports, as-of date D = 2026-06-30; window [D-27, D]. Stores A, B (West) and C (East);
manager sees A-C, rep sees A and C.
  A/P1  MSL, out of stock on D, D-1, D-2 and D-27 (in window) and D-28 (outside); the D row is duplicated
  A/P2  MSL in store A, out of stock on D-5
  B/P2  non-MSL in store B, out of stock on D
  B/P1  out of stock on D-1, no report at all on D (not current)
  B/P3  a D row whose stock state is not reported (unknown)
  C/P3  reported every day, never out of stock (zero, not missing)
  C/P1  out of stock on D with MSL status unknown
  C     no rows on D-12..D-10 (missing dates); P4 never reported anywhere
"""

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb

from data_platform.access import DataUnavailable
from modules.module_2 import business_priority as bp
from modules.module_2 import service as service_module
from modules.module_2.contracts import Module2Error
from modules.module_2.service import Module2Service, Principal

try:
    from tests.test_module2_business import columns, reader
except ImportError:                                    # discovered with -s tests
    from test_module2_business import columns, reader

D = date(2026, 6, 30)
RUN = "score-2026-06-30-synthetic"
TABLES = {"stores": "dim_stores", "products": "dim_products", "visit_priority": "mart_visit_priority",
          "oos_predictions": "predictions_oos", "store_sku_daily": "mart_store_sku_daily",
          "negotiation_context": "mart_negotiation_context"}


def day(n):
    return D - timedelta(days=n)


def daily_rows():
    rows = []

    def add(store, sku, n, oos, msl, inventory=None):
        rows.append({"date": day(n), "store_id": store, "sku_id": sku, "oos_observed": oos, "is_msl": msl,
                     "inventory_observed": (0.0 if oos else 10.0) if inventory is None and oos is not None else inventory,
                     "units_sold_observed": None if oos is None else (0.0 if oos else 2.0),
                     "inventory_data_status": "Observed" if oos is not None else "Unknown"})
    for n in range(0, 30):
        add("A", "P1", n, n in (0, 1, 2, 27, 28), True)
        add("A", "P2", n, n == 5, True)
        add("B", "P2", n, n == 0, False)
        if n != 0:
            add("B", "P1", n, n == 1, True)
        add("B", "P3", n, None if n == 0 else False, False)
        if n not in (10, 11, 12):
            add("C", "P3", n, False, False)
            add("C", "P1", n, n == 0, None if n == 0 else True)
    rows.append(dict(next(r for r in rows if r["store_id"] == "A" and r["sku_id"] == "P1" and r["date"] == D)))
    return rows


def drivers(oos, inventory, velocity=2.0, scheduled=0.0, arrival=None):
    values = {"oos_observed": oos, "inventory_observed": inventory, "in_stock_velocity_28d": velocity,
              "scheduled_units_next7d": scheduled, "days_until_next_scheduled_receipt": arrival, "is_msl": True}
    return {"prediction_start": "2026-07-01", "prediction_end": "2026-07-07", "threshold_route": "segment",
            "evidence": [{"feature": k, "label": k, "value": v, "data_status": "Unknown" if v is None else "Observed",
                          "as_of": str(D)} for k, v in values.items()], "limitations": []}


def insert(con, view, rows):
    names = [n for n, _ in columns(view)]
    for row in rows:
        assert not set(row) - set(names), set(row) - set(names)
        con.execute(f"INSERT INTO gold.{TABLES[view]} VALUES ({','.join('?' * len(names))})",
                    [row.get(n) for n in names])


def build(path):
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA gold; CREATE SCHEMA serving")
        for view, table in TABLES.items():
            con.execute(f"CREATE TABLE gold.{table} ({', '.join(f'{n} {t}' for n, t in columns(view))})")
            con.execute(f"CREATE VIEW serving.vw_{view} AS SELECT * FROM gold.{table}")
        con.execute("CREATE TABLE gold.account_scope (principal_id VARCHAR, store_id VARCHAR, role VARCHAR, "
                    "scope_version VARCHAR)")
        con.executemany("INSERT INTO gold.account_scope VALUES (?,?,?,'v1')",
                        [("mgr", s, "manager") for s in "ABC"] + [("rep", s, "sales") for s in "AC"])
        insert(con, "stores", [{"store_id": s, "store_name": f"SYNTHETIC {s}", "channel": "Modern Trade",
                                "region": "East" if s == "C" else "West", "state": "CA", "segment_tier": "Tier 1",
                                "city": "X"} for s in "ABC"])
        insert(con, "products", [{"sku_id": f"P{i}", "product_name": f"SYNTHETIC P{i}", "category": "Laundry",
                                  "brand": "B", "is_msl": i == 1, "unit_cost": 1, "list_price": 2, "consumer_price": 3}
                                 for i in range(1, 5)])
        for rank, s in enumerate("ABC", start=1):
            reason = {"bucket": 2, "bucket_label": "x", "msl_gap_count": 0, "msl_unknown_count": 0, "alert_count": 1,
                      "inventory_unknown_count": 0, "sku_count": 1,
                      "reference_sales_proxy": {"status": "Complete", "known_sum": 1.0},
                      "impact_fallback_to_risk": False, "top_skus": []}
            insert(con, "visit_priority", [{"scoring_run_id": RUN, "scoring_date": D, "store_id": s,
                                            "priority_rank": rank, "risk_score": 0.6, "msl_gap_count": 0,
                                            "ranking_policy_version": "visit_priority.v1",
                                            "reason_json": json.dumps(reason)}])
            insert(con, "oos_predictions", [{
                "scoring_run_id": RUN, "scoring_date": D, "store_id": s, "sku_id": "P1", "probability": 0.6,
                "threshold": 0.5, "is_predicted_oos": True,
                "drivers_json": json.dumps(drivers(False, 3.0, 2.0, 10.0, 3)),
                "model_version": "m", "feature_version": "f", "input_status": "Observed_context"}])
            insert(con, "negotiation_context", [{"as_of_date": D, "store_id": s, "sku_id": "P1", "is_msl": True,
                                                 "reference_list_price": 2.0, "reported_net_trade_price_28d": 1.9,
                                                 "last_invoice_date": day(1)}])
        insert(con, "store_sku_daily", daily_rows())


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.duckdb"
        build(self.path)
        r = reader(self.path)
        r.record = {**r.record, "rows": {"stores": 3}}
        self.service = Module2Service(reader=r)
        self.mgr, self.rep = Principal("mgr", True), Principal("rep", True)

    def oos(self, principal, measure="current", **filters):
        return self.service.get_oos_analytics(principal, filters, measure)

    def skus(self, response):
        return {s["sku_id"]: s for s in response.detail["skus"]}

    def sql(self, statement, params=()):
        with duckdb.connect(str(self.path)) as con:
            con.execute(statement, list(params))


class SemanticsTests(Base):
    def test_current_uses_the_as_of_report_only(self):
        r = self.oos(self.mgr)
        skus = self.skus(r)
        self.assertEqual(r.status, "Ok")
        self.assertEqual(skus["P1"]["oos"], 2)                         # A and C; B has no D report (not current)
        self.assertEqual(skus["P2"]["oos"], 1)                         # B
        self.assertEqual(skus["P3"]["oos"], 0)                         # observed, zero out of stock
        self.assertNotIn("P4", skus)                                   # never observed: not shown as zero
        self.assertEqual(r.detail["totals"]["duplicates"], 1)          # duplicate grain counted once
        self.assertEqual(r.detail["totals"]["unknown"], 1)             # B/P3 state not reported
        self.assertEqual(r.detail["totals"]["msl"], {"MSL": 1, "Non-MSL": 1, "Unknown": 1})
        self.assertEqual(sum(r.detail["totals"]["msl"].values()), r.detail["totals"]["oos"])

    def test_28_day_window_store_days_and_msl_from_each_observation(self):
        r = self.oos(self.mgr, "28d")
        skus = self.skus(r)
        self.assertEqual(r.detail["window"], [day(27).isoformat(), D.isoformat()])
        self.assertEqual(skus["P1"]["oos"], 6)                         # A 4 (D-28 excluded) + B 1 + C 1
        self.assertEqual(skus["P1"]["msl"], {"MSL": 5, "Non-MSL": 0, "Unknown": 1})
        self.assertEqual(skus["P2"]["msl"], {"MSL": 1, "Non-MSL": 1, "Unknown": 0})   # MSL in A, not in B
        self.assertEqual(skus["P2"]["msl_group"], "Mixed")
        self.assertEqual(sum(r.detail["totals"]["msl"].values()), r.detail["totals"]["oos"])
        self.assertEqual(len(r.detail["daily"]), 28)
        self.assertEqual(sum(d["oos"] for d in r.detail["daily"]), r.detail["totals"]["oos"])
        missing_c = {d["date"]: d["observed"] for d in r.detail["daily"]}
        self.assertLess(missing_c[day(11).isoformat()], missing_c[day(9).isoformat()])   # C's missing dates

    def test_drilldown_lists_exactly_the_affected_authorized_stores(self):
        mgr = self.skus(self.oos(self.mgr, "28d"))["P1"]["stores"]
        self.assertEqual([(s["store_id"], s["oos"]) for s in mgr], [("A", 4), ("B", 1), ("C", 1)])
        rep = self.skus(self.oos(self.rep, "28d"))["P1"]["stores"]
        self.assertEqual([(s["store_id"], s["oos"]) for s in rep], [("A", 4), ("C", 1)])

    def test_partial_scope_and_filters_apply_before_aggregation(self):
        rep = self.oos(self.rep)
        self.assertEqual(rep.detail["stores_in_filter"], 2)
        self.assertEqual(self.skus(rep)["P1"]["oos"], 2)
        self.assertNotIn("B", {s["store_id"] for k in rep.detail["skus"] for s in k["stores"]})
        east = self.oos(self.mgr, region="East")
        self.assertEqual(east.detail["stores_in_filter"], 1)
        self.assertEqual(east.detail["totals"]["msl"], {"MSL": 0, "Non-MSL": 0, "Unknown": 1})
        with self.assertRaises(Module2Error):
            self.oos(self.mgr, view="prevention")
        with self.assertRaises(Module2Error):
            self.oos(self.mgr, "7d")


class CacheTests(Base):
    def count_pages(self):
        real, calls = self.service.reader.read_page, {"n": 0}

        def counted(name, *args, **kwargs):
            if name == "store_sku_daily":
                calls["n"] += 1
            return real(name, *args, **kwargs)
        self.service.reader.read_page = counted
        return calls

    def test_completed_aggregate_is_cached_per_principal_scope_and_filters(self):
        calls = self.count_pages()
        first = self.oos(self.mgr, "28d")
        n = calls["n"]
        second = self.oos(self.mgr, "28d")
        self.assertEqual(second.detail, first.detail)
        self.assertEqual(calls["n"], n)                                # warm: no page read
        self.assertNotEqual(self.oos(self.rep, "28d").detail, first.detail)   # no cross-user contamination
        self.oos(self.mgr, "28d", region="East")
        self.assertGreater(calls["n"], n)

    def test_cache_is_bounded(self):
        with patch.object(service_module, "ANALYTICS_CACHE_ENTRIES", 2):
            for region in ("East", "West", None):
                self.oos(self.mgr, **({"region": region} if region else {}))
            self.assertEqual(len(self.service._analytics_cache()), 2)

    def test_revocation_and_scope_change_clear_and_never_answer_from_cache(self):
        self.assertEqual(self.oos(self.rep).status, "Ok")
        self.sql("DELETE FROM gold.account_scope WHERE principal_id='rep'")
        self.assertEqual(self.oos(self.rep).status, "Permission_denied")
        self.assertFalse([k for k in self.service._analytics_cache() if "rep" in k])
        self.sql("INSERT INTO gold.account_scope VALUES ('rep','A','sales','v2')")
        changed = self.oos(self.rep)
        self.assertEqual(changed.status, "Ok")
        self.assertEqual(changed.detail["stores_in_filter"], 1)        # new scope, recomputed

    def test_scope_change_mid_pagination_discards_the_aggregate(self):
        real = self.service.reader.read_page
        state = {"pages": 0}

        def paged(name, principal_id, **kwargs):
            if name == "store_sku_daily":
                kwargs["page_size"] = 20
                state["pages"] += 1
                if state["pages"] == 2:
                    self.sql("INSERT INTO gold.account_scope VALUES ('mgr','A','manager','v2')")
            return real(name, principal_id, **kwargs)
        self.service.reader.read_page = paged
        r = self.oos(self.mgr, "28d")
        self.assertEqual(r.status, "Unavailable")
        self.assertFalse(self.service._analytics_cache())

    def test_release_change_page_failure_and_row_limit_are_unavailable_not_partial(self):
        real = self.service.reader.read_page
        record = self.service.reader.record

        def switch(name, principal_id, **kwargs):
            out = real(name, principal_id, **kwargs)
            if name == "store_sku_daily":
                record["artifact_id"] = "another-release"
            return out
        self.service.reader.read_page = switch
        self.assertEqual(self.oos(self.mgr).status, "Unavailable")
        record["artifact_id"] = "synthetic-m2"

        def failing(name, principal_id, **kwargs):
            if name == "store_sku_daily" and kwargs.get("after"):
                raise DataUnavailable("page failed")
            kwargs["page_size"] = 50 if name == "store_sku_daily" else kwargs.get("page_size", 1000)
            return real(name, principal_id, **kwargs)
        self.service.reader.read_page = failing
        self.assertEqual(self.oos(self.mgr, "28d").status, "Unavailable")
        self.service.reader.read_page = real
        with patch.object(service_module, "ANALYTICS_MAX_ROWS", 10):
            self.assertEqual(self.oos(self.mgr, "28d").status, "Unavailable")
        self.assertFalse(self.service._analytics_cache())

    def test_logout_clears_the_principal_aggregates(self):
        self.oos(self.mgr)
        self.oos(self.rep)
        self.service.clear("mgr")
        keys = list(self.service._analytics_cache())
        self.assertTrue(keys and all("mgr" not in k for k in keys))


class StoreHistoryTests(Base):
    def test_detail_oos_days_window_current_and_projection(self):
        detail = self.service.get_store_detail(self.mgr, "A").detail
        days = detail["business"]["oos_days"]
        self.assertEqual(detail["business"]["oos_window"], [day(27).isoformat(), D.isoformat()])
        self.assertEqual(days["P1"]["reported_oos_days"], 4)            # D-28 outside, duplicate once
        self.assertIs(days["P1"]["current"], True)
        self.assertEqual(len({h["date"] for h in detail["history"]}), 30)
        b = self.service.get_store_detail(self.mgr, "B").detail["business"]["oos_days"]
        self.assertIsNone(b["P1"]["current"])                          # no as-of report: not carried forward
        self.assertEqual(b["P3"]["unknown_days"], 1)
        est = detail["business"]["skus"]["P1"]
        path = detail["business"]["projection"]["P1"]
        self.assertAlmostEqual(sum(d["short"] for d in path), est["units_short"])
        self.assertEqual([d["receipt"] for d in path], [0, 0, 10.0, 0, 0, 0, 0])   # day-3 receipt only

    def test_projection_reconciles_with_the_scenario(self):
        cases = [(0.0, 2.0, 0.0, None), (0.0, 2.0, 100.0, 1), (5.0, 2.0, 30.0, 7), (3.0, 2.0, 10.0, 3),
                 (0.0, 0.0, 0.0, None), (50.0, 2.0, 0.0, None), (0.0, 2.0, 10.0, 9)]
        for inv, vel, sched, arrival in cases:
            path = bp.scenario_path(inv, vel, sched, arrival)
            self.assertAlmostEqual(sum(d["short"] for d in path), bp.shortfall_units(inv, vel, sched, arrival))
            self.assertTrue(all(d["stock_end"] >= 0 for d in path))
        self.assertIsNone(bp.scenario_path(None, 2.0, 0.0, None))
        self.assertIsNone(bp.scenario_path(5.0, 2.0, 10.0, None))      # scheduled, receipt day unknown


if __name__ == "__main__":
    unittest.main()
