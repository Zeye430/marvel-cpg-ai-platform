"""Recompute the data figures quoted in the knowledge-base documents (marked *data*).

    python knowledge_base/tools/compute_data_facts.py --data-dir data/raw

Reads the eight authorized V3 CSV files (the shared data platform's `data/raw/` layout, including the
retained `dim_guardrails`), uses reported rows from 1 Jan 2025 to 31 May 2026 only (June 2026 is the
OOS-model test month) and writes knowledge_base/data_facts.json. Needs pandas and numpy.
"""

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

KB_ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--data-dir", type=Path, default=KB_ROOT.parent / "data" / "raw")
ap.add_argument("--out", type=Path, default=KB_ROOT / "data_facts.json")
args = ap.parse_args()
D = args.data_dir
END = pd.Timestamp("2026-05-31")

so = pd.read_csv(D / "fact_sell_out.csv", parse_dates=["date"], dtype={"promo_id": str, "restock_delivery_id": str})
so = so[so.date <= END]
pr = pd.read_csv(D / "dim_products.csv"); st = pd.read_csv(D / "dim_stores.csv")
pm = pd.read_csv(D / "dim_promotions.csv"); gr = pd.read_csv(D / "dim_guardrails.csv")
dl = pd.read_csv(D / "fact_deliveries.csv", parse_dates=["order_date", "scheduled_delivery", "actual_delivery"])
dl = dl[dl.actual_delivery <= END]
au = pd.read_csv(D / "fact_execution_audits.csv", parse_dates=["date"]); au = au[au.date <= END]
si = pd.read_csv(D / "fact_sell_in.csv", parse_dates=["date"]); si = si[si.date <= END]
import hashlib
R = {"window": "2025-01-01..2026-05-31, reported rows only",
     "source_files_sha256": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:16] for f in sorted(D.glob("*.csv"))}}
so = so.merge(pr[['sku_id', 'product_name', 'category', 'brand', 'is_msl', 'list_price']], on='sku_id').merge(st[['store_id', 'channel', 'segment_tier', 'region']], on='store_id')
so['value'] = so.units_sold_daily * so.consumer_price_actual
so['list_value'] = so.units_sold_daily * so.list_price
so['oos'] = so.is_out_of_stock.astype(bool)
so['promo'] = so.promo_id.notna()
# --- per SKU
g = so.groupby('sku_id')
sku = pd.DataFrame({'units': g.units_sold_daily.sum(), 'value': g.value.sum(), 'list_value': g.list_value.sum(),
                    'rows': g.size(), 'oos_rate': g.oos.mean() * 100, 'stores': g.store_id.nunique(),
                    'velocity': so[~so.oos].groupby('sku_id').units_sold_daily.mean()}).join(pr.set_index('sku_id'))
sku['value_share_cat'] = sku.value / sku.groupby('category').value.transform('sum') * 100
sku['value_share_total'] = sku.value / sku.value.sum() * 100
sku['rank_cat'] = sku.groupby('category').value.rank(ascending=False).astype(int)
sku['rank_total'] = sku.value.rank(ascending=False).astype(int)
sku['margin_at_list'] = (sku.list_price - sku.unit_cost) / sku.list_price * 100
# OOS by tier per SKU
ot = so.groupby(['sku_id', 'segment_tier']).oos.mean().unstack() * 100
sku = sku.join(ot.add_prefix('oos_'))
# velocity by channel (in-stock reported days)
vc = so[~so.oos].groupby(['sku_id', 'channel']).units_sold_daily.mean().unstack()
sku = sku.join(vc.add_prefix('vel_'))
R['sku'] = json.loads(sku.round(2).reset_index().to_json(orient='records'))
# --- category
gc = so.groupby('category')
R['category'] = {c: {'value_share': float(v) for v in [gc.value.sum()[c] / so.value.sum() * 100]} for c in gc.groups}
for c in gc.groups:
    R['category'][c]['oos_rate'] = float(gc.oos.mean()[c] * 100)
    R['category'][c]['promo_share_value'] = float(so[so.promo & (so.category == c)].value.sum() / gc.value.sum()[c] * 100)
# --- totals and tiers
R['total_value'] = float(so.value.sum()); R['total_units'] = int(so.units_sold_daily.sum())
R['oos_overall'] = float(so.oos.mean() * 100)
R['oos_tier'] = (so.groupby('segment_tier').oos.mean() * 100).round(2).to_dict()
R['oos_channel'] = (so.groupby('channel').oos.mean() * 100).round(2).to_dict()
R['oos_tier_channel'] = json.loads((so.groupby(['segment_tier', 'channel']).oos.mean().unstack() * 100).round(1).to_json(orient='index'))
# lost sales, Team 1 V3 EDA method: on out-of-stock days the store's normal in-stock daily units, valued at list price
normal = so[~so.oos].groupby(['store_id', 'sku_id']).units_sold_daily.mean().rename('normal_daily')
x = so.join(normal, on=['store_id', 'sku_id'])
x['lost_value'] = np.where(x.oos, (x.normal_daily - x.units_sold_daily).clip(lower=0), 0) * x.list_price
x['actual_value'] = x.units_sold_daily * x.list_price
lost_t = x.groupby('segment_tier')[['lost_value', 'actual_value']].sum()
R['lost_sales_pct_tier'] = (lost_t.lost_value / (lost_t.lost_value + lost_t.actual_value) * 100).round(2).to_dict()
R['lost_sales_pct_overall'] = round(float(x.lost_value.sum() / (x.lost_value.sum() + x.actual_value.sum()) * 100), 2)
R['lost_sales_list_value'] = round(float(x.lost_value.sum()), 0)
R['oos_promo_vs_not'] = json.loads((so.groupby(['channel', 'promo']).oos.mean().unstack() * 100).round(2).to_json(orient='index'))
so['wknd'] = so.date.dt.dayofweek >= 5
w = so[~so.oos].groupby('wknd').units_sold_daily.mean(); R['weekend_uplift_pct'] = float((w[True] / w[False] - 1) * 100)
R['velocity_tier'] = so[~so.oos].groupby('segment_tier').units_sold_daily.mean().round(2).to_dict()
R['msl_value_share'] = float(so[so.is_msl].value.sum() / so.value.sum() * 100)
R['promo_value_share'] = float(so[so.promo].value.sum() / so.value.sum() * 100)
# deliveries by channel
dl = dl.merge(st[['store_id', 'channel']], on='store_id'); dl['diff'] = (dl.actual_delivery - dl.scheduled_delivery).dt.days
dl['lead'] = (dl.actual_delivery - dl.order_date).dt.days
R['delivery'] = {c: {'on_time_or_early_pct': round(float((gr['diff'] <= 0).mean() * 100), 1), 'late_pct': round(float((gr['diff'] > 0).mean() * 100), 1),
                     'late2_pct': round(float((gr['diff'] >= 2).mean() * 100), 1), 'fill_rate_pct': round(float(gr.units_delivered.sum() / gr.units_ordered.sum() * 100), 1),
                     'short_pct': round(float((gr.units_delivered < gr.units_ordered).mean() * 100), 1), 'median_lead_days': float(gr.lead.median()),
                     'sched_lead_min': int((gr.scheduled_delivery - gr.order_date).dt.days.min()), 'sched_lead_max': int((gr.scheduled_delivery - gr.order_date).dt.days.max())}
                 for c, gr in dl.groupby('channel')}
# audits by tier x channel
au = au.merge(st[['store_id', 'channel', 'segment_tier']], on='store_id')
R['planogram_tier_channel'] = json.loads((au.groupby(['segment_tier', 'channel']).planogram_compliance.mean().unstack() * 100).round(1).to_json(orient='index'))
R['display_tier_channel'] = json.loads((au.groupby(['segment_tier', 'channel']).secondary_display.mean().unstack() * 100).round(1).to_json(orient='index'))
R['planogram_overall'] = float(au.planogram_compliance.mean() * 100); R['display_overall'] = float(au.secondary_display.mean() * 100)
R['stores_per_channel_tier'] = json.loads(st.groupby(['channel', 'segment_tier']).size().unstack().to_json(orient='index'))
# MSL compliance (store-day, reported, all MSL SKUs in stock among reported MSL rows)
m = so[so.is_msl].groupby(['store_id', 'date', 'segment_tier', 'channel']).oos.max().reset_index()
R['msl_compliance_overall'] = float((1 - m.oos.mean()) * 100)
R['msl_compliance_tier'] = ((1 - m.groupby('segment_tier').oos.mean()) * 100).round(1).to_dict()
# substitution check: on store-days where SKU A is OOS, are B's sales above B's usual level at that store?
pairs = [('SKU-1001', 'SKU-1002'), ('SKU-1001', 'SKU-1003'), ('SKU-1004', 'SKU-1005'), ('SKU-1018', 'SKU-1019'), ('SKU-1016', 'SKU-1017'), ('SKU-1012', 'SKU-1013')]
piv_u = so.pivot_table(index=['store_id', 'date'], columns='sku_id', values='units_sold_daily'); piv_o = so.pivot_table(index=['store_id', 'date'], columns='sku_id', values='oos')
subs = {}
for a, b in pairs:
    ok = piv_u[[b]].join(piv_o[[a, b]].rename(columns={a: 'oa', b: 'ob'})).dropna()
    ok = ok[ok.ob == 0]
    mu = ok.groupby(level=0)[b].transform('mean')
    rel = ok[b] / mu
    subs[f'{a}->{b}'] = {'b_sales_when_a_oos_vs_usual_pct': round(float((rel[ok.oa == 1].mean() - 1) * 100), 1), 'n_days': int((ok.oa == 1).sum())}
R['substitution_check'] = subs

# 1. pack size and price per unit measure
def pack(name):
    m = re.search(r'(\d+(?:\.\d+)?)\s*(ml|L|oz|ct)\b', name)
    v, u = float(m.group(1)), m.group(2)
    if u == 'L': return v * 33.814, 'fl oz', f'{m.group(0)}'
    if u == 'ml': return v / 29.5735, 'fl oz', f'{m.group(0)}'
    if u == 'oz': return v, 'fl oz', f'{m.group(0)}'
    return v, 'count', m.group(0)
pr[['size_std', 'size_unit', 'pack_label']] = pr.product_name.apply(lambda n: pd.Series(pack(n)))
pr['list_per_unit'] = pr.list_price / pr.size_std; pr['consumer_per_unit'] = pr.consumer_price / pr.size_std
pr['margin_at_list'] = (pr.list_price - pr.unit_cost) / pr.list_price * 100
pr['retailer_margin_ref'] = (pr.consumer_price - pr.list_price) / pr.consumer_price * 100
pr['consumer_to_list'] = pr.consumer_price / pr.list_price

# 2. discount headroom: margin at cap and discount that hits the floor, per SKU x channel
rows = []
for _, p in pr.iterrows():
    for _, g in gr[gr.category == p.category].iterrows():
        net_cap = p.list_price * (1 - g.max_discount_pct / 100); m_cap = (net_cap - p.unit_cost) / net_cap * 100
        d_floor = (1 - p.unit_cost / (p.list_price * (1 - g.min_target_margin / 100))) * 100
        rows.append({'sku_id': p.sku_id, 'channel': g.channel, 'cap': g.max_discount_pct, 'floor': g.min_target_margin, 'margin_at_cap': round(m_cap, 1), 'discount_at_floor': round(d_floor, 1)})

# 4. promo lift with the EDA method (ratio of sums), by type, channel, category
ins = so[~so.oos].copy(); ins['ym'] = ins.date.dt.to_period('M'); ins['on_promo'] = ins.promo_id.notna()
ins['promo_discount_pct'] = ins.promo_discount_pct
kk = ['store_id', 'sku_id', 'ym']; base = ins[~ins.on_promo].groupby(kk).units_sold_daily.mean().rename('base_units')
pdays = ins[ins.on_promo].merge(base, left_on=kk, right_index=True).merge(pm[['promo_id', 'promo_type']], on='promo_id')
def lt(by):
    t = pdays.groupby(by).agg(pu=('units_sold_daily', 'sum'), bu=('base_units', 'sum'), disc=('promo_discount_pct', 'mean'), n=('date', 'size'))
    t['lift'] = (t.pu / t.bu - 1) * 100; t['elasticity'] = np.log(t.pu / t.bu) / np.log(1 - t.disc / 100); return t[['n', 'disc', 'lift', 'elasticity']].round(2)
R['promo_lift_eda_method'] = {k: json.loads(lt(k if k != 'all' else np.repeat('all', len(pdays))).to_json(orient='index')) for k in ['promo_type', 'channel', 'category', 'all']}
R['pack'] = json.loads(pr.round(3).to_json(orient='records'))
R['discount_headroom'] = rows
t = si.merge(st[['store_id', 'channel']], on='store_id').groupby('channel')
R['weighted_trade_discount_pct'] = ((t.gross_sales.sum() - t.net_sales.sum()) / t.gross_sales.sum() * 100).round(2).to_dict()
R['skus_per_account_by_tier'] = so.groupby(['segment_tier', 'store_id']).sku_id.nunique().groupby(level=0).median().to_dict()
d5 = so[so.sku_id == 'SKU-1005'].groupby(['channel', 'segment_tier']).store_id.nunique().unstack()
R['degreaser_500ml_accounts'] = json.loads(d5.to_json(orient='index'))
R['degreaser_velocity_by_tier'] = json.loads(so[so.sku_id.isin(['SKU-1004', 'SKU-1005']) & ~so.oos].groupby(['sku_id', 'segment_tier']).units_sold_daily.mean().unstack().round(2).to_json(orient='index'))
args.out.write_text(json.dumps(R, indent=1, default=str) + "\n")
print("wrote", args.out)
