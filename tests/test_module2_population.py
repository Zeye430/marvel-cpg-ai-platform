"""Population statistics and the data follow-up definition beyond one table page."""

import json
import unittest
from datetime import date

from modules.module_2.service import Module2Service, Principal, needs_data_followup


class StubReader:
    """Serves scoped rows in pages of at most ``page_size`` like ServingReader.read_page."""

    def __init__(self, stores=230, unscored=("S9998", "S9999")):
        self.record = {"artifact_id": "stub", "as_of": "2026-06-30", "rows": {"stores": stores + len(unscored)},
                       "model_outputs": {"model_version": "m", "publication_kind": "Synthetic_demo", "synthetic": True}}
        self.priorities, self.stores = [], []
        for i in range(stores):
            store = f"S{i:04d}"
            late_unknown = i >= 205                     # unknowns only after the first 200 positions
            alert_with_unknown = i in (3, 210)          # bucket 2 stores that also have unknown inputs
            reason = {"bucket": 2 if alert_with_unknown or i < 50 else (3 if late_unknown else 4),
                      "bucket_label": "x", "msl_unknown_count": 1 if late_unknown else 0,
                      "alert_count": 1 if (alert_with_unknown or i < 50) else 0,
                      "inventory_unknown_count": 2 if (late_unknown or alert_with_unknown) else 0, "sku_count": 3,
                      "reference_sales_proxy": {"status": "Complete", "known_sum": 1.0},
                      "impact_fallback_to_risk": False, "top_skus": []}
            self.priorities.append({"scoring_run_id": "r", "scoring_date": date(2026, 6, 30), "store_id": store,
                                    "priority_rank": i + 1, "risk_score": (i % 100) / 100, "msl_gap_count": 0,
                                    "reason_json": json.dumps(reason)})
            self.stores.append({"store_id": store, "store_name": f"Store {i}", "channel": "MT", "region": "E",
                                "segment_tier": "T1"})
        self.stores += [{"store_id": s, "store_name": s, "channel": "MT", "region": "E", "segment_tier": "T1"}
                        for s in unscored]

    def read_page(self, name, principal_id, *, after=None, page_size=1000, **filters):
        rows = {"visit_priority": self.priorities, "stores": self.stores}[name]
        start = int(after or 0)
        page = rows[start:start + min(page_size, 1000)]
        nxt = str(start + len(page)) if start + len(page) < len(rows) else None
        return {"rows": page, "next_cursor": nxt, "artifact_id": "stub"}


class PopulationTests(unittest.TestCase):
    def setUp(self):
        self.service = Module2Service.__new__(Module2Service)
        self.service.reader = StubReader()
        self.service.record = self.service.reader.record
        self.service.outputs = self.service.record["model_outputs"]
        self.service.latest_source_as_of = None
        self.service._cache = {}
        self.manager = Principal("demo:manager", True)

    def test_histogram_and_followups_cover_every_scoped_store(self):
        population = self.service.get_population(self.manager)
        self.assertEqual(sum(b["stores"] for b in population.detail["risk_histogram"]), 230)
        followups = {r["store_id"] for r in population.detail["followups"]}
        late = {f"S{i:04d}" for i in range(205, 230)}
        self.assertTrue(late <= followups)                                 # beyond the first 200 rows
        self.assertIn("S0003", followups)                                  # alert store with unknown inputs
        self.assertTrue({"S9998", "S9999"} <= followups)                   # unscored stores

    def test_tile_list_and_filter_share_one_definition(self):
        population = self.service.get_population(self.manager)
        page = self.service.get_priorities(self.manager, page_size=25)
        self.assertEqual(page.totals["data_followup_stores"], population.totals["data_followup_stores"])
        self.assertEqual(population.totals["data_followup_stores"], len(population.detail["followups"]))
        unknown = self.service.get_priorities(self.manager, {"unknown_only": True}, page_size=200)
        rows, cursor = list(unknown.rows), unknown.next_cursor
        while cursor:
            more = self.service.get_priorities(self.manager, {"unknown_only": True}, cursor=cursor, page_size=200)
            rows += more.rows
            cursor = more.next_cursor
        scored = {r["store_id"] for r in population.detail["followups"] if r["scored"]}
        self.assertEqual({r["store_id"] for r in rows}, scored)
        self.assertTrue(all(needs_data_followup(r) for r in rows))

    def test_population_respects_principal_and_signed_out(self):
        with self.assertRaises(PermissionError):
            self.service.get_population(Principal("demo:manager", False))


if __name__ == "__main__":
    unittest.main()
