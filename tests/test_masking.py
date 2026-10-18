"""Outcome-independent selection, common-population metrics and clustered sampling."""

import unittest
from datetime import date, timedelta

import pandas as pd

from data_platform.masking import metrics, select_blocks, store_bootstrap


class MaskingTests(unittest.TestCase):
    def test_blocks_are_whole_days_reproducible_and_anchors_remain_visible(self):
        start = date(2026, 3, 1)
        reported = [(store, start + timedelta(days=i), 20) for store in ("S1", "S2", "S3") for i in range(-1, 61)]
        left = select_blocks(reported, {s: 20 for s in ("S1", "S2", "S3")}, start, date(2026, 4, 30), per_length=4)
        right = select_blocks(reported, {s: 20 for s in ("S1", "S2", "S3")}, start, date(2026, 4, 30), per_length=4)
        pd.testing.assert_frame_equal(left, right)
        hidden, anchors = set(), []
        for row in left.itertuples():
            anchors.append((row.store_id, row.start_date - timedelta(days=1)))
            for i in range(row.block_length):
                key = (row.store_id, row.start_date + timedelta(days=i))
                self.assertNotIn(key, hidden)
                hidden.add(key)
        self.assertTrue(all(anchor not in hidden for anchor in anchors))
        self.assertEqual(left.groupby("block_length").size().tolist(), [4, 4, 4, 4])

    def test_undefined_precision_is_not_zero(self):
        frame = pd.DataFrame(dict(store_id=["S1", "S2"], inventory_estimate=[4., 5.],
                                  inventory_truth=[2., 5.], oos_truth=[False, False],
                                  sales_estimate=[1., 2.], sales_truth=[0., 0.]))
        result = metrics(frame)
        self.assertEqual(result["inventory_mae"], 1.)
        self.assertIsNone(result["state_oos_precision"])
        self.assertIsNone(result["sales_wape"])
        self.assertEqual(store_bootstrap(frame)["unit"], "store")


if __name__ == "__main__":
    unittest.main()
