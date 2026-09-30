"""Marvel CPG - Module 1: Commercial Execution Analytics Dashboard (Shiny for Python).
Run:  shiny run --reload app.py      (dummy data mirrors the brief's data dictionary;
to go live, replace the DUMMY DATA block with pd.read_csv() of the 5 fact/dim files)."""
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shiny import App, reactive, render, req, ui
from shinywidgets import output_widget, render_widget

# ============================ DUMMY DATA ============================
rng = np.random.default_rng(7)
CH = ["Modern Trade", "Traditional Trade", "Distributor"]
REG = ["Northeast", "Southeast", "Midwest", "West"]
REPS = [f"Rep {c}" for c in "ABCDEF"]
COL = {"Modern Trade": "#1f4e9c", "Traditional Trade": "#e8833a", "Distributor": "#2a9d8f"}
N = 80

stores = pd.DataFrame({
    "store_id": [f"STR-{i:04d}" for i in range(1, N + 1)],
    "channel": rng.choice(CH, N, p=[.4, .4, .2]),
    "region": rng.choice(REG, N),
    "segment_tier": rng.choice(["Tier 1", "Tier 2", "Tier 3"], N, p=[.25, .45, .3]),
    "rep_id": rng.choice(REPS, N),  # assumed field: store -> rep assignment (not in dim_stores)
})
stores["store_name"] = [f"{n} {i:02d}" for i, n in enumerate(
    rng.choice(["Supermercado Central", "Quick Mart", "Corner Shop", "Value Foods", "Metro Market"], N), 1)]

cats = {"Surface Cleaners": ["Power Clean Lemon 1L", "Glass Shine 500ml", "Multi-Surface Spray 750ml", "Bathroom Fresh 750ml"],
        "Dishwashing": ["Dish Gel Lemon 500ml", "Dish Gel Ultra 750ml", "Degreaser Pro 500ml", "Dish Tabs 30ct"],
        "Laundry": ["Liquid Detergent 2L", "Fabric Softener 1.5L", "Stain Remover 500ml", "Laundry Pods 40ct"]}
rows, k = [], 1000
for cat, names in cats.items():
    for j, n in enumerate(names):
        price = round(rng.uniform(3, 9), 2)
        rows.append((f"SKU-{k}", f"Marvel {n}", cat, j < 2, price, round(price * rng.uniform(.4, .5), 2)))
        k += 1
prods = pd.DataFrame(rows, columns=["sku_id", "product_name", "category", "is_msl", "list_price", "unit_cost"])

# fact_sell_out (daily) -> 80 stores x 12 SKUs x 18 months
D = pd.date_range("2025-01-01", "2026-06-30")
S, K, T = len(stores), len(prods), len(D)
doy = D.dayofyear.to_numpy()
tier = stores.segment_tier.map({"Tier 1": 1.6, "Tier 2": 1.0, "Tier 3": .6}).to_numpy()[:, None, None]
lam = tier * rng.uniform(1.5, 4.5, K)[None, :, None] * (1 + .15 * np.sin(2 * np.pi * (doy - 60) / 365))[None, None, :]
p_oos = (stores.channel.map({"Modern Trade": .05, "Traditional Trade": .13, "Distributor": .09}).to_numpy()[:, None, None]
         * (1 + .35 * np.cos(2 * np.pi * doy / 365))[None, None, :])
oos = rng.random((S, K, T)) < p_oos
idx = pd.MultiIndex.from_product([stores.store_id, prods.sku_id, D], names=["store_id", "sku_id", "date"])
so = pd.DataFrame({"units_sold_daily": np.where(oos, 0, rng.poisson(lam)).ravel(),
                   "inventory_level": np.where(oos, 0, rng.poisson(lam * 4) + 1).ravel(),
                   "is_out_of_stock": oos.ravel()}, index=idx).reset_index()
so = so.merge(stores, on="store_id").merge(prods[["sku_id", "product_name", "category", "is_msl"]], on="sku_id")
so["week"] = so.date - pd.to_timedelta(so.date.dt.dayofweek, unit="D")

# fact_sell_in (weekly): sell-out plus channel-specific over-shipment
si = so.groupby(["store_id", "sku_id", "week"], as_index=False).units_sold_daily.sum()
bias = si.store_id.map(stores.set_index("store_id").channel).map({"Modern Trade": .03, "Traditional Trade": .06, "Distributor": .14})
si["units_sold"] = (si.units_sold_daily * (1 + bias + rng.normal(0, .05, len(si)))).round().astype(int)
si = si.drop(columns="units_sold_daily").merge(stores, on="store_id")

# fact_execution_audits (monthly)
M = pd.date_range("2025-01-01", "2026-06-01", freq="MS") + pd.Timedelta(days=14)
au = pd.MultiIndex.from_product([stores.store_id, prods.sku_id, M], names=["store_id", "sku_id", "date"]).to_frame(index=False)
au = au.merge(stores, on="store_id").merge(prods[["sku_id", "category"]], on="sku_id")
au["planogram_compliance"] = rng.random(len(au)) < au.channel.map({"Modern Trade": .78, "Traditional Trade": .48, "Distributor": .6})
au["shelf_share_pct"] = np.clip(rng.normal(au.channel.map({"Modern Trade": 34, "Traditional Trade": 22, "Distributor": 27})
                                           + np.where(au.planogram_compliance, 5, -3), 7), 3, 70).round(1)

# ============================== UI ==============================
CSS = ".card-header{font-weight:600;background:#f6f8fb}.bslib-page-sidebar{background:#f0f2f6}"
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h6("VIEW"),
        ui.input_radio_buttons("role", None, ["Regional Manager", "Field Rep"]),
        ui.panel_conditional("input.role == 'Field Rep'", ui.input_select("rep", "My account", REPS)),
        ui.help_text("Manager = all stores, aggregated. Rep = assigned stores only."),
        ui.hr(),
        ui.h6("FILTERS"),
        ui.input_date_range("dates", "Date range (snaps to full weeks)", start="2026-04-01", end="2026-06-30",
                            min="2025-01-01", max="2026-06-30"),
        ui.input_checkbox_group("channel", "Channel", CH, selected=CH),
        ui.input_selectize("region", "Region", REG, selected=REG, multiple=True),
        ui.input_selectize("sku", "SKU", dict(zip(prods.sku_id, prods.product_name)), selected=list(prods.sku_id), multiple=True),
        ui.hr(),
        ui.accordion(ui.accordion_panel("KPI definitions", ui.tags.small(ui.tags.ul(
            ui.tags.li("MSL: % of store-days with every mandatory SKU in stock"),
            ui.tags.li("OOS: % of store-SKU-days out of stock"),
            ui.tags.li("Gap: sell-in minus sell-out units (+ = stock building up)"),
            ui.tags.li("Shelf share: avg % of category shelf held by Marvel"),
            ui.tags.li("Planogram: % of audited SKUs displayed to plan")))), open=False),
        ui.help_text("Synthetic dummy data, Jan 2025 - Jun 2026."),
        width=310),
    ui.tags.style(CSS),
    ui.output_ui("ctx"),
    ui.output_ui("kpi_row"),
    ui.layout_columns(
        ui.card(ui.card_header("Sell-in vs sell-out (weekly units) - shaded area = channel stock build-up"), output_widget("flow"), full_screen=True, height="340px"),
        ui.card(ui.card_header("OOS rate trend by channel (dotted = 5% target)"), output_widget("oos_trend"), full_screen=True, height="340px"),
        col_widths=(6, 6)),
    ui.layout_columns(
        ui.card(ui.card_header("OOS heatmap by SKU  ", ui.input_radio_buttons("hm_col", None, {"channel": "by channel", "region": "by region"}, inline=True)),
                output_widget("heat"), full_screen=True, height="400px"),
        ui.card(ui.card_header("Top 10 SKUs by OOS rate"), output_widget("rank"), full_screen=True, height="400px"),
        col_widths=(7, 5)),
    ui.layout_columns(
        ui.card(ui.card_header("Shelf share % by category and channel"), output_widget("shelf"), full_screen=True, height="380px"),
        ui.card(ui.card_header("Stores to prioritize (highest OOS first)"), ui.output_data_frame("stores_tbl"), full_screen=True, height="380px"),
        col_widths=(5, 7)),
    title="Marvel CPG | Commercial Execution Dashboard", fillable=False)


# ============================ SERVER ============================
def server(input, output, session):
    def scope(df, col, a, b):
        m = (df[col].between(a, b) & df.channel.isin(input.channel())
             & df.region.isin(input.region()) & df.sku_id.isin(input.sku()))
        if input.role() == "Field Rep":
            m &= df.rep_id == input.rep()
        return df[m]

    @reactive.calc
    def span():
        a, b = map(pd.Timestamp, input.dates())
        return a - pd.Timedelta(days=a.dayofweek), min(b + pd.Timedelta(days=6 - b.dayofweek), D[-1])

    @reactive.calc
    def SO(): return scope(so, "date", *span())
    @reactive.calc
    def SI(): return scope(si, "week", *span())
    @reactive.calc
    def AU(): return scope(au, "date", *span())

    def kpis(a, b):
        s, i, x = scope(so, "date", a, b), scope(si, "week", a, b), scope(au, "date", a, b)
        m = s[s.is_msl]
        out, gap = s.units_sold_daily.sum(), i.units_sold.sum() - s.units_sold_daily.sum()
        return dict(msl=100 * (1 - m.groupby(["date", "store_id"]).is_out_of_stock.max().mean()),
                    oos=100 * s.is_out_of_stock.mean(), gap=gap, gap_pct=100 * gap / out if out else np.nan,
                    shelf=x.shelf_share_pct.mean(), plano=100 * x.planogram_compliance.mean())

    def tile(title, cur, prv, ok, better_up, txt, foot):
        na = pd.isna(cur)
        d = None if na or pd.isna(prv) else cur - prv
        arrow = "" if d is None else ui.div(f"{'▲' if d >= 0 else '▼'} {abs(d):.1f} pts vs prior period",
                                            class_="small " + ("text-success" if (d >= 0) == better_up else "text-danger"))
        return ui.card(ui.div(title, class_="text-muted small fw-semibold text-uppercase"),
                       ui.div("–" if na else txt, class_="fs-2 fw-bold " + ("text-muted" if na else "text-success" if ok else "text-danger")),
                       arrow, ui.div(foot, class_="small text-muted"), fill=False)

    @render.ui
    def kpi_row():
        a, b = span()
        n = (b - a).days + 1
        c, p = kpis(a, b), kpis(a - pd.Timedelta(days=n), a - pd.Timedelta(days=1))
        return ui.layout_column_wrap(
            tile("MSL Compliance", c["msl"], p["msl"], c["msl"] >= 90, True, f"{c['msl']:.1f}%", "Target ≥ 90%"),
            tile("OOS Rate", c["oos"], p["oos"], c["oos"] <= 5, False, f"{c['oos']:.1f}%", "Target ≤ 5%"),
            tile("Sell-in vs Sell-out Gap", c["gap_pct"], p["gap_pct"], abs(c["gap_pct"]) <= 5, False,
                 f"{c['gap_pct']:+.1f}%", f"{c['gap']:+,.0f} units · target within ±5%"),
            tile("Shelf Share", c["shelf"], p["shelf"], c["shelf"] >= 30, True, f"{c['shelf']:.1f}%", "Target ≥ 30%"),
            tile("Planogram Compliance", c["plano"], p["plano"], c["plano"] >= 80, True, f"{c['plano']:.1f}%", "Target ≥ 80%"),
            width="190px", fill=False)

    @render.ui
    def ctx():
        a, b = span()
        who = (f"Field Rep · {input.rep()}" if input.role() == "Field Rep" else "Regional Manager · aggregated view")
        return ui.div(ui.span(who, class_="badge bg-primary me-2"),
                      ui.span(f"{SO().store_id.nunique()} stores · {a:%b %d, %Y} – {b:%b %d, %Y}", class_="text-muted"), class_="mb-2")

    def tidy(fig, h=290):
        fig.update_layout(template="plotly_white", height=h, margin=dict(l=8, r=8, t=8, b=8),
                          legend=dict(orientation="h", y=-0.2, title=None), font=dict(size=12))
        return fig

    @render_widget
    def flow():
        s, i = SO(), SI()
        req(len(s))
        a, b = s.groupby("week").units_sold_daily.sum(), i.groupby("week").units_sold.sum()
        f = go.Figure([go.Scatter(x=b.index, y=b.values, name="Sell-in", line=dict(color="#1f4e9c", width=3)),
                       go.Scatter(x=a.index, y=a.values, name="Sell-out", line=dict(color="#e8833a", width=3),
                                  fill="tonexty", fillcolor="rgba(31,78,156,.12)")])
        return tidy(f)

    @render_widget
    def oos_trend():
        s = SO()
        req(len(s))
        d = s.groupby(["week", "channel"]).is_out_of_stock.mean().mul(100).reset_index()
        f = px.line(d, x="week", y="is_out_of_stock", color="channel", color_discrete_map=COL,
                    labels={"is_out_of_stock": "OOS %", "week": ""})
        f.add_hline(y=5, line_dash="dot", line_color="grey")
        return tidy(f)

    @render_widget
    def heat():
        s = SO()
        req(len(s))
        p = s.groupby(["product_name", input.hm_col()]).is_out_of_stock.mean().mul(100).unstack()
        f = px.imshow(p, text_auto=".1f", aspect="auto", color_continuous_scale="Reds", labels=dict(color="OOS %", x="", y=""))
        return tidy(f, 330)

    @render_widget
    def rank():
        s = SO()
        req(len(s))
        d = (s.groupby(["product_name", "category"]).is_out_of_stock.mean().mul(100)
             .reset_index(name="oos").nlargest(10, "oos").sort_values("oos"))
        f = px.bar(d, x="oos", y="product_name", color="category", orientation="h", text_auto=".1f",
                   labels={"oos": "OOS %", "product_name": ""})
        return tidy(f, 330)

    @render_widget
    def shelf():
        x = AU()
        req(len(x))
        d = x.groupby(["category", "channel"]).shelf_share_pct.mean().reset_index()
        f = px.bar(d, x="category", y="shelf_share_pct", color="channel", barmode="group", text_auto=".1f",
                   color_discrete_map=COL, labels={"shelf_share_pct": "Shelf share %", "category": ""})
        return tidy(f, 320)

    @render.data_frame
    def stores_tbl():
        s, i, x = SO(), SI(), AU()
        req(len(s))
        g = s.groupby("store_id").agg(Store=("store_name", "first"), Channel=("channel", "first"), Region=("region", "first"),
                                      Tier=("segment_tier", "first"), oos=("is_out_of_stock", "mean"), out=("units_sold_daily", "sum"))
        g["Sell-in gap (units)"] = (i.groupby("store_id").units_sold.sum() - g.out).round().astype("Int64")
        g["Planogram %"] = (x.groupby("store_id").planogram_compliance.mean() * 100).round(1)
        g["OOS %"] = (g.oos * 100).round(1)
        g = g.sort_values("OOS %", ascending=False).head(15).drop(columns=["oos", "out"]).reset_index()
        return render.DataGrid(g.rename(columns={"store_id": "ID"}), height="290px")


app = App(app_ui, server)
