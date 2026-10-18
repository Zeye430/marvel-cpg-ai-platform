# V3 stage 3 candidate validation

Date: 2026-10-18. Estimator: `historical_state.v1-4fc61eba8d72`.
Input fingerprint: `f5403651d8aa3e9492071a14706dd7a7f31aec88247f7af2f083ab9dca782675`.

## Shared daily state

Candidate build `20261018T184530-80184599d737` contains 2,257,500 store-SKU-days
through 2026-05-31. All 192 required structural/state checks pass. There are
2,073,376 Observed, 183,736 Estimated and 388 Unknown inventory states. Sales
states have the same counts in this input. Unknown consists of 369 leading-history
rows and 19 rows beyond the four-day freshness limit. Observed values are retained;
estimated values are separate. No application or model publication is made.

Eighteen synthetic/integration tests pass. They cover ingestion, publication
retention, full-block masking selection, future perturbation invariance, audit
as-of behavior, exact historical median calculation, promotion fit availability,
unknown cold starts, receipt counting and the documented cumulative floor.

## March/April masking

Run: `mask-20261018T184529-8584fa0f`, seed 650. The experiment samples 400 blocks
(100 each of lengths 1-4), or 1,000 complete account-days and 19,844 SKU-days
across 184 stores. All masked observations are removed before recomputation.
Coverage on the common comparison population is 100% for this experiment.

| Inventory method | MAE, units | Current-state OOS precision | Current-state OOS recall |
| --- | ---: | ---: | ---: |
| Zero fill, diagnostic | 138.45 | 0.0788 | 1.0000 |
| LOCF | 20.06 | 0.7527 | 0.7614 |
| Sales depletion, no receipts | 14.39 | 0.7118 | 0.9322 |
| Promotion-adjusted depletion, no receipts | 14.25 | 0.7155 | 0.9335 |
| Receipts, no promotion adjustment | 3.08 | 0.9662 | 0.9322 |
| Receipts and promotion adjustment | 2.90 | 0.9681 | 0.9335 |

For the last candidate, inventory WAPE is 2.09%, sales MAE is 2.29 units and
sales WAPE is 38.10%. The inventory MAE 95% store-bootstrap interval is
[2.67, 3.11] units (300 resamples). Receipt-block inventory MAE is 3.42 units;
without a receipt inside the block it is 2.78. Length-4 MAE is 3.37 versus 1.82
for length 1. Segment/SKU diagnostics and clustered intervals are retained in
the ignored restricted experiment artifact.

These are reconstruction metrics for a hidden *current* inventory/OOS state.
They do not establish next-seven-day Module 2 precision, recall, ROC-AUC or
probability calibration. May model selection and June model evaluation are not
performed by this experiment.

## Natural-gap behavior requires review before adoption

Retrospective inventory closure through 2026-04-30 gives 158,937 closed
store-SKU gaps. Under the simplified balance equation:

```text
implied missing-day sales
  = prior reported inventory
    + receipts after prior report through closing report
    - closing inventory
    - sales on the closing report date
```

All 148,499 gaps with no receipt inside the missing interval imply zero
missing-day sales. In the other 10,438 gaps, the implied missing-day sales
exactly equal the units delivered inside the missing interval (median 91 units).
Equivalently, across these gaps the observed closing inventory follows the
prior report minus closing-day sales plus closing-day receipts, with no net
inventory change attributable to the hidden interval.

This is a mechanical bridge pattern, not proof of zero true sales or of all
received units being sold. A simulation that freezes inventory or skips receipt
posting on unreported days is one possible explanation; missing adjustments or
other generator rules cannot be identified uniquely from the tables. Per-day
truth remains unknown. Consecutive observed-day reconciliation and explicit
restock links remain valid, but they do not resolve missing-day receipt semantics.

The artificial masking result therefore supports a promising short-gap
candidate but cannot certify its natural-gap accuracy. Do not convert natural
gaps to observed zeros, silently discard missing-day receipts, or use closure
information as a historical feature. Keep estimates visibly provisional and
review this limitation before operational adoption and final model experiments.

To reproduce the receipt equality diagnostic, query only the restricted
`evaluation.natural_gap_closure` relation:

```sql
SELECT count(*) AS gap_rows,
       avg((implied_gap_sales_under_balance = receipts_on_missing_days)::INTEGER)
           AS equality_fraction
FROM evaluation.natural_gap_closure
WHERE receipts_on_missing_days > 0;
```

The accepted Silver pointer is unchanged. The Gold artifact remains internal and
`application_ready = false`; no model, UI or operational estimator is released.
