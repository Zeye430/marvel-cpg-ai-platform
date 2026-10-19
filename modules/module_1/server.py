"""
modules/module_1/server.py
==========================
Reactive business logic, KPI calculations, and Plotly visual renderers
for Module 1: Commercial Execution Analytics Dashboard.
"""

import warnings
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shiny import reactive, render, req, ui
from shinywidgets import render_widget
from shared.data import CH, REG, COL, REP_PROFILES, MANAGER_PROFILE, scope_dataset


def module_1_server(input, output, session, dataset: dict, auth_state: dict):
    """Executes all reactive filters, KPI aggregations, and Plotly charts for Module 1."""
    prods = dataset["prods"]
    stores = dataset["stores"]
    guardrails = dataset["guardrails"]
    so_raw = dataset["so_raw"]
    si_raw = dataset["si_raw"]
    au_raw = dataset["au_raw"]
    MIN_DATE = dataset["min_date"]
    MAX_DATE = dataset["max_date"]

    active_role = auth_state["role"]
    active_rep = auth_state["rep"]
    active_name = auth_state["name"]
    active_title = auth_state["title"]

# ---------- PRODUCT SKU SELECT / DESELECT ACTIONS ----------
@reactive.effect
@reactive.event(input.btn_sku_all)
def _select_all_skus():
    ui.update_selectize("sku", selected=list(prods.sku_id))

@reactive.effect
@reactive.event(input.btn_sku_none)
def _deselect_all_skus():
    ui.update_selectize("sku", selected=[])

# Dynamic preview in login screen for chosen rep
@render.ui
def login_rep_preview_details():
    rep_id = input.login_rep_choice() if input.login_rep_choice() else "Rep A"
    prof = REP_PROFILES.get(rep_id, REP_PROFILES["Rep A"])
    rep_store_count = (stores["rep_id"] == rep_id).sum()
    return ui.div(
        ui.div(prof["name"], class_="fw-bold fs-6", style="color:#1d1d1f;"),
        ui.div(f"{prof['title']} · {prof['region']}", class_="small", style="color:#6e6e73;"),
        ui.div(f"Territory Scope: {rep_store_count} Assigned Stores · {prof['badge']}",
               class_="small", style="color:#0b594b;font-weight:600;")
    )

# Navbar Profile Badge
@render.ui
def nav_user_badge():
    is_rep = active_role() == "Field Rep"
    role_label = f"Field Representative · {active_rep()}" if is_rep else "Regional Manager"
    badge_style = "background:rgba(115,251,211,0.22);border:1px solid rgba(115,251,211,0.6);color:#064e43;" if is_rep else "background:rgba(74,143,231,0.15);border:1px solid rgba(74,143,231,0.4);color:#2466b8;"
    return ui.div(
        ui.div(
            ui.span("👤", style="font-size:0.95rem;"),
            ui.div(
                ui.div(active_name(), class_="fw-bold", style="font-size:0.84rem;line-height:1.2;color:#1d1d1f;"),
                ui.div(active_title(), class_="text-muted", style="font-size:0.72rem;line-height:1.1;color:#6e6e73;"),
            ),
            class_="d-flex align-items-center gap-2"
        ),
        ui.span(role_label, class_="badge rounded-pill ms-2", style=f"{badge_style}font-size:0.72rem;padding:4px 10px;"),
        class_="user-session-chip"
    )

# Sidebar Scope Indicator
@render.ui
def sidebar_scope_badge():
    if active_role() == "Field Rep":
        rep_id = active_rep()
        n_stores = (stores["rep_id"] == rep_id).sum()
        return ui.div(
            ui.span("Territory Active: ", style="color:#6e6e73;font-size:0.78rem;"),
            ui.span(f"{rep_id} ({n_stores} Stores)", class_="fw-bold", style="color:#0b594b;font-size:0.8rem;"),
            class_="mb-1"
        )
    return ui.div(
        ui.span("Portfolio Active: ", style="color:#6e6e73;font-size:0.78rem;"),
        ui.span(f"All Stores ({len(stores)} Stores)", class_="fw-bold", style="color:#4a8fe7;font-size:0.8rem;"),
        class_="mb-1"
    )

# ---------- REACTIVE FILTERING & SCOPE ----------
def scope(df_raw, col, a, b):
    channels = input.channel() if input.channel() else CH
    regions = input.region() if input.region() else REG
    skus = input.sku() if input.sku() is not None else list(prods.sku_id)

    m_stores = stores["channel"].isin(channels) & stores["region"].isin(regions)
    if active_role() == "Field Rep" and active_rep():
        m_stores &= (stores["rep_id"] == active_rep())
    valid_store_ids = set(stores.loc[m_stores, "store_id"])

    m = (
        df_raw[col].between(a, b)
        & df_raw["store_id"].isin(valid_store_ids)
        & df_raw["sku_id"].isin(skus)
    )
    sub = df_raw[m]

    store_meta = ["store_id", "store_name", "channel", "region", "state", "segment_tier", "city", "rep_id"]
    prod_meta = ["sku_id", "product_name", "category", "brand", "is_msl", "unit_cost", "list_price"]
    return sub.merge(stores[store_meta], on="store_id").merge(prods[prod_meta], on="sku_id")

@reactive.calc
def span():
    dates_val = input.dates()
    a = pd.Timestamp(dates_val[0])
    b = pd.Timestamp(dates_val[1])
    start_snapped = a - pd.Timedelta(days=a.dayofweek)
    end_snapped = min(b + pd.Timedelta(days=6 - b.dayofweek), pd.Timestamp(MAX_DATE))
    return start_snapped, end_snapped

@reactive.calc
def SO():
    return scope(so_raw, "date", *span())

@reactive.calc
def SI():
    return scope(si_raw, "week", *span())

@reactive.calc
def AU():
    return scope(au_raw, "date", *span())

KPI_SPECS = {
    "msl": {
        "title": "MSL Compliance %",
        "desc": "Percentage of stores with mandatory SKUs available on shelf.",
        "source": "fact_inventory (or fact_sell_out), dim_products (or dim_store_assortment)",
        "columns": "store_id, sku_id, is_msl, inventory_level",
        "formula": "COUNT(DISTINCT CASE WHEN non_compliant_stores = 0 THEN store_id END) / COUNT(DISTINCT store_id) * 100",
        "logic": "For each store, flag non_compliant_stores = SUM(CASE WHEN is_msl = TRUE AND (inventory_level <= 0 OR inventory_level IS NULL) THEN 1 ELSE 0 END)."
    },
    "oos": {
        "title": "OOS Rate %",
        "desc": "Out-of-stock rate by SKU, channel, and region.",
        "source": "fact_inventory (or fact_sell_out)",
        "columns": "sku_id, store_id, date (or calendar_day), is_out_of_stock",
        "formula": "SUM(CASE WHEN is_out_of_stock = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100",
        "logic": "Proportion of observed store-SKU-day observations flagged as out-of-stock."
    },
    "gap": {
        "title": "Sell-in vs. Sell-out Gap %",
        "desc": "Difference between what Marvel invoices to the distributor and what the end consumer buys.",
        "source": "fact_sell_in, fact_sell_out",
        "columns": "fact_sell_in.units_sell_in (units_sold), fact_sell_out.units_sold_daily",
        "formula": "(SUM(units_sell_in) - SUM(units_sold_daily)) / SUM(units_sold_daily) * 100",
        "logic": "Volume disparity between upstream sell-in invoice units and downstream sell-out sales units."
    },
    "shelf": {
        "title": "Shelf Share %",
        "desc": "Average shelf share by category and channel.",
        "source": "fact_execution_audits",
        "columns": "shelf_share_pct, category, channel, region",
        "formula": "AVG(shelf_share_pct) grouped by category, channel, and region",
        "logic": "Arithmetic mean of observed percentage shelf frontage dedicated to Marvel products."
    },
    "plano": {
        "title": "Planogram Compliance %",
        "desc": "Compliance with the display planogram.",
        "source": "fact_execution_audits",
        "columns": "planogram_compliance",
        "formula": "SUM(CASE WHEN planogram_compliance = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100",
        "logic": "Proportion of audited store-SKU placements compliant with the designated planogram."
    }
}

def kpis(a, b):
    s, i, x = scope(so_raw, "date", a, b), scope(si_raw, "week", a, b), scope(au_raw, "date", a, b)
    if s.empty:
        return dict(msl=np.nan, oos=np.nan, gap=0, gap_pct=np.nan, shelf=np.nan, plano=np.nan)

    # 1. MSL Compliance %
    # Formula: COUNT(DISTINCT CASE WHEN non_compliant_stores = 0 THEN store_id END) / COUNT(DISTINCT store_id) * 100
    # Logic: For each store, flag non_compliant_stores = SUM(CASE WHEN is_msl = TRUE AND (inventory_level <= 0 OR inventory_level IS NULL) THEN 1 ELSE 0 END)
    is_non_comp = (s["is_msl"] == True) & ((s["inventory_level"] <= 0) | s["inventory_level"].isna())
    if s["date"].nunique() <= 1:
        store_non_comp = is_non_comp.groupby(s["store_id"]).sum()
        total_stores = s["store_id"].nunique()
        msl = ((store_non_comp == 0).sum() / total_stores * 100) if total_stores > 0 else np.nan
    else:
        store_day_non_comp = is_non_comp.groupby([s["date"], s["store_id"]]).sum()
        msl = ((store_day_non_comp == 0).sum() / len(store_day_non_comp) * 100) if len(store_day_non_comp) > 0 else np.nan

    # 2. OOS Rate %
    # Formula: SUM(CASE WHEN is_out_of_stock = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100
    oos_count = (s["is_out_of_stock"] == True).sum()
    oos = (oos_count / len(s) * 100) if len(s) > 0 else np.nan

    # 3. Sell-in vs. Sell-out Gap %
    # Formula: (SUM(units_sell_in) - SUM(units_sold_daily)) / SUM(units_sold_daily) * 100
    units_sell_in = i["units_sold"].sum() if not i.empty else 0
    units_sold_daily = s["units_sold_daily"].sum() if not s.empty else 0
    gap = units_sell_in - units_sold_daily
    gap_pct = (gap / units_sold_daily * 100) if units_sold_daily > 0 else np.nan

    # 4. Shelf Share %
    # Formula: AVG(shelf_share_pct) grouped by category, channel, and region
    shelf = x["shelf_share_pct"].mean() if not x.empty else np.nan

    # 5. Planogram Compliance %
    # Formula: SUM(CASE WHEN planogram_compliance = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100
    if not x.empty and len(x) > 0:
        plano_count = (x["planogram_compliance"] == True).sum()
        plano = (plano_count / len(x)) * 100
    else:
        plano = np.nan

    return dict(msl=msl, oos=oos, gap=gap, gap_pct=gap_pct, shelf=shelf, plano=plano)

def luxury_tile(title, cur, prv, ok, better_up, txt, foot, prefix="", suffix="", decimals=1, foot_units=None, foot_target=None, spec=None):
    na = pd.isna(cur)
    d = None if na or pd.isna(prv) else cur - prv
    if d is not None:
        is_good = (d >= 0) == better_up
        arrow = "▲" if d >= 0 else "▼"
        badge_class = "delta-positive" if is_good else "delta-negative"
        delta_tag = ui.span(f"{arrow} {abs(d):.1f} pts vs prior", class_=f"kpi-delta-pill {badge_class}")
    else:
        delta_tag = ui.span("– baseline", class_="kpi-delta-pill delta-neutral")

    val_color = "text-muted" if na else "text-success" if ok else "text-danger"

    # Check if foot has secondary unit count to shuffle
    if foot_units is not None and not pd.isna(foot_units):
        foot_elem = ui.div(
            ui.span(
                f"{foot_units:+,.0f}{foot_target}",
                class_="shuffle-units",
                data_target=str(int(foot_units)),
                data_suffix=foot_target
            ),
            class_="kpi-target-foot"
        )
    else:
        foot_elem = ui.div(foot, class_="kpi-target-foot")

    tooltip_attr = ""
    info_icon_elem = None
    if spec:
        tooltip_attr = (
            f"{spec['title']}\n"
            f"• Source: {spec['source']}\n"
            f"• Columns: {spec['columns']}\n"
            f"• Formula: {spec['formula']}\n"
            f"• Logic: {spec.get('logic', '')}"
        )
        popover_children = [
            ui.div(spec["title"], class_="kpi-pop-title"),
            ui.div(spec.get("desc", ""), class_="kpi-pop-desc"),
            ui.div(
                ui.span("Source CSV / Table", class_="kpi-pop-lbl"),
                ui.span(spec["source"], class_="kpi-pop-badge"),
                class_="kpi-pop-sec"
            ),
            ui.div(
                ui.span("Required Columns", class_="kpi-pop-lbl"),
                ui.div(spec["columns"], class_="kpi-pop-text"),
                class_="kpi-pop-sec"
            ),
            ui.div(
                ui.span("Aggregation / SQL Formula", class_="kpi-pop-lbl"),
                ui.div(spec["formula"], class_="kpi-pop-formula"),
                class_="kpi-pop-sec"
            ),
        ]
        if "logic" in spec and spec["logic"]:
            popover_children.append(
                ui.div(
                    ui.span("Calculation Logic", class_="kpi-pop-lbl"),
                    ui.div(spec["logic"], class_="kpi-pop-text"),
                    class_="kpi-pop-sec"
                )
            )

        info_icon_elem = ui.div(
            ui.span("ⓘ", class_="kpi-info-symbol"),
            ui.div(*popover_children, class_="kpi-hover-popover"),
            class_="kpi-info-trigger",
            tabindex="0",
            role="tooltip",
            title=f"Calculation details for {spec['title']}"
        )

    header_elem = ui.div(
        ui.div(title, class_="kpi-title"),
        info_icon_elem,
        class_="kpi-header-row"
    )

    return ui.div(
        header_elem,
        ui.div(
            "–" if na else txt,
            class_=f"kpi-value {val_color} shuffle-number",
            data_target=f"{cur:.1f}" if not na else "",
            data_prefix=prefix,
            data_suffix=suffix,
            data_decimals=str(decimals)
        ),
        delta_tag,
        foot_elem,
        class_="kpi-tile",
        title=tooltip_attr if tooltip_attr else None
    )

@render.ui
def kpi_row():
    a, b = span()
    n = (b - a).days + 1
    c = kpis(a, b)
    p = kpis(a - pd.Timedelta(days=n), a - pd.Timedelta(days=1))
    return ui.div(
        luxury_tile(
            "MSL Compliance", c["msl"], p["msl"],
            c["msl"] >= 90 if not pd.isna(c["msl"]) else False, True,
            f"{c['msl']:.1f}%" if not pd.isna(c["msl"]) else "–", "Target ≥ 90%",
            suffix="%", decimals=1, spec=KPI_SPECS["msl"]
        ),
        luxury_tile(
            "OOS Rate", c["oos"], p["oos"],
            c["oos"] <= 5 if not pd.isna(c["oos"]) else False, False,
            f"{c['oos']:.1f}%" if not pd.isna(c["oos"]) else "–", "Target ≤ 5%",
            suffix="%", decimals=1, spec=KPI_SPECS["oos"]
        ),
        luxury_tile(
            "Sell-In vs Sell-Out Gap", c["gap_pct"], p["gap_pct"],
            abs(c["gap_pct"]) <= 5 if not pd.isna(c["gap_pct"]) else False, False,
            f"{c['gap_pct']:+.1f}%" if not pd.isna(c["gap_pct"]) else "–",
            f"{c['gap']:+,.0f} units · target within ±5%",
            prefix="", suffix="%", decimals=1,
            foot_units=c["gap"] if not pd.isna(c["gap"]) else None,
            foot_target=" units · target within ±5%",
            spec=KPI_SPECS["gap"]
        ),
        luxury_tile(
            "Shelf Share", c["shelf"], p["shelf"],
            c["shelf"] >= 30 if not pd.isna(c["shelf"]) else False, True,
            f"{c['shelf']:.1f}%" if not pd.isna(c["shelf"]) else "–", "Target ≥ 30%",
            suffix="%", decimals=1, spec=KPI_SPECS["shelf"]
        ),
        luxury_tile(
            "Planogram Compliance", c["plano"], p["plano"],
            c["plano"] >= 80 if not pd.isna(c["plano"]) else False, True,
            f"{c['plano']:.1f}%" if not pd.isna(c["plano"]) else "–", "Target ≥ 80%",
            suffix="%", decimals=1, spec=KPI_SPECS["plano"]
        ),
        class_="kpi-horizontal-grid"
    )

@render.ui
def ctx():
    a, b = span()
    s = SO()
    store_count = s.store_id.nunique() if not s.empty else 0
    if active_role() == "Field Rep":
        who_text = f"Field Representative Route · {active_name()} ({active_rep()})"
        role_badge = ui.span(who_text, class_="badge", style="background:#73fbd3;color:#04362d;font-weight:700;font-size:0.75rem;padding:6px 12px;border-radius:980px;border:1px solid rgba(115,251,211,0.9);box-shadow:0 2px 8px rgba(115,251,211,0.25);")
    else:
        who_text = f"Regional Manager Oversight · {active_name()} (All Stores Aggregated)"
        role_badge = ui.span(who_text, class_="badge", style="background:#4a8fe7;color:#ffffff;font-weight:600;font-size:0.75rem;padding:6px 12px;border-radius:980px;box-shadow:0 2px 8px rgba(74,143,231,0.3);")

    return ui.div(
        role_badge,
        ui.span(f"Territory Scope: {store_count} stores active", class_="badge bg-white text-dark border border-secondary border-opacity-25 ms-2", style="font-weight:600;font-size:0.75rem;padding:6px 12px;border-radius:980px;box-shadow:0 1px 3px rgba(0,0,0,0.04);"),
        ui.span(f"{a:%b %d, %Y} – {b:%b %d, %Y} ({((b - a).days + 1) // 7} Weeks)", class_="badge bg-white text-muted border border-secondary border-opacity-25 ms-2", style="font-weight:500;font-size:0.75rem;padding:6px 12px;border-radius:980px;box-shadow:0 1px 3px rgba(0,0,0,0.04);"),
        class_="d-flex align-items-center mb-3"
    )

def tidy(fig, h=350, is_polar=False, is_geo=False):
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=h,
        margin=dict(l=16, r=16, t=32, b=16),
        legend=dict(
            orientation="h",
            y=-0.22,
            title=None,
            font=dict(size=11, color="#6E6E73", family="'Plus Jakarta Sans', -apple-system, sans-serif")
        ),
        font=dict(family="'Plus Jakarta Sans', -apple-system, sans-serif", size=12, color="#6E6E73"),
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            font_size=12,
            font_family="'Plus Jakarta Sans', -apple-system, sans-serif",
            font_color="#1D1D1F",
            bordercolor="rgba(0,0,0,0.12)"
        )
    )
    if not is_polar and not is_geo:
        fig.update_layout(
            xaxis=dict(
                gridcolor="rgba(0,0,0,0.04)",
                zerolinecolor="rgba(0,0,0,0.08)",
                tickfont=dict(color="#6E6E73", size=11)
            ),
            yaxis=dict(
                gridcolor="rgba(0,0,0,0.04)",
                zerolinecolor="rgba(0,0,0,0.08)",
                tickfont=dict(color="#6E6E73", size=11)
            )
        )
    return fig

def locked_chart(title="Manager Access Required"):
    fig = go.Figure()
    fig.add_annotation(
        text=f"🔒 <b>{title}</b><br><span style='font-size:12px;color:#86868B;'>Access restricted to Regional Management oversight</span>",
        showarrow=False,
        font=dict(size=13, color="#1D1D1F")
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return tidy(fig, h=420)

# ==================== OOS & SUPPLY CHAIN INSIGHTS ====================

@render_widget
def chart1_oos_hazard():
    """Stock Level & OOS Hazard Heatmap (Field Sales Rep Radar)."""
    s = SO()
    req(len(s))

    # Filter for mandatory MSL items
    msl_df = s[s.is_msl == True]
    if msl_df.empty:
        msl_df = s

    latest_date = msl_df["date"].max()
    latest_so = msl_df[msl_df["date"] == latest_date]

    # Field rep view: scoped to their route stores; Manager view: prioritize top 25 hazard stores
    if active_role() == "Field Rep":
        sub = latest_so
    else:
        if latest_so["store_name"].nunique() > 25:
            store_hazard = latest_so.groupby("store_name").agg(
                oos_count=("is_out_of_stock", "sum"),
                avg_inv=("inventory_level", "mean")
            ).sort_values(by=["oos_count", "avg_inv"], ascending=[False, True])
            top_stores = store_hazard.index[:25]
            sub = latest_so[latest_so["store_name"].isin(top_stores)]
        else:
            sub = latest_so

    pivot = sub.pivot_table(
        index="store_name",
        columns="product_name",
        values="inventory_level",
        aggfunc="mean"
    ).fillna(0)

    # Shorten product names for crisp visual radar
    pivot.columns = [col.replace("Marvel ", "") for col in pivot.columns]

    fig = px.imshow(
        pivot,
        text_auto=".0f",
        aspect="auto",
        color_continuous_scale=[
            [0.0, "#FF3B30"],    # 0 units: Active OOS (Red Hazard)
            [0.12, "#FF9500"],   # 1-15 units: Impending stockout buffer warning (Amber)
            [0.35, "#E5E5EA"],   # Mid buffer (Silver)
            [1.0, "#0071E3"]     # Healthy buffer (Apple Blue)
        ],
        labels=dict(color="On-Hand Units", x="", y="")
    )
    fig.update_xaxes(side="top", tickangle=-30)
    h = max(460, min(680, len(pivot) * 24 + 130))
    return tidy(fig, h=h)

@render_widget
def chart2_lost_sales():
    """Estimated Lost Sales Trend & Cumulative Loss Area Chart."""
    if active_role() == "Field Rep":
        return locked_chart("Estimated Lost Sales Trend")
    s = SO()
    req(len(s))

    group_by = input.c2_group() if hasattr(input, "c2_group") and input.c2_group() else "region"

    # Baseline average daily sales per (store_id, sku_id) when in stock
    in_stock = s[s.is_out_of_stock == 0]
    if in_stock.empty:
        in_stock = s
    base = in_stock.groupby(["store_id", "sku_id"])["units_sold_daily"].mean().rename("baseline_daily")
    s_calc = s.merge(base, on=["store_id", "sku_id"], how="left")
    s_calc["baseline_daily"] = s_calc["baseline_daily"].fillna(s_calc["units_sold_daily"]).clip(lower=1)

    # Estimated Lost Revenue = is_out_of_stock * baseline_daily * list_price
    s_calc["lost_revenue"] = s_calc["is_out_of_stock"] * s_calc["baseline_daily"] * s_calc["list_price"]

    df_loss = s_calc.groupby(["week", group_by])["lost_revenue"].sum().reset_index()

    color_map = {
        "Northeast": "#0071E3", "Southeast": "#34C759", "Midwest": "#FF9500", "West": "#AF52DE",
        "Surface Cleaners": "#0071E3", "Dishwashing": "#FF9500", "Laundry": "#AF52DE"
    }

    fig = px.area(
        df_loss,
        x="week",
        y="lost_revenue",
        color=group_by,
        color_discrete_map=color_map,
        labels={"lost_revenue": "Est. Lost Revenue ($)", "week": "", group_by: group_by.capitalize()}
    )
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return tidy(fig, h=460)

@render_widget
def chart3_oos_duration():
    """OOS Duration Distribution (Boxplot)."""
    if active_role() == "Field Rep":
        return locked_chart("OOS Duration Distribution")
    s = SO()
    req(len(s))

    group_col = input.c3_group() if hasattr(input, "c3_group") and input.c3_group() else "channel"

    # Sort and detect consecutive OOS spells
    s_sorted = s.sort_values(["store_id", "sku_id", "date"]).reset_index(drop=True)
    s_sorted["spell_block"] = (
        (s_sorted["is_out_of_stock"] != s_sorted.groupby(["store_id", "sku_id"])["is_out_of_stock"].shift()) |
        (s_sorted["store_id"] != s_sorted["store_id"].shift()) |
        (s_sorted["sku_id"] != s_sorted["sku_id"].shift())
    ).cumsum()

    oos_only = s_sorted[s_sorted["is_out_of_stock"] == 1]
    if oos_only.empty:
        fig = go.Figure()
        fig.add_annotation(text="Zero OOS incidents recorded in selected period", showarrow=False)
        return tidy(fig, h=450)

    incidents = oos_only.groupby(["spell_block", "store_id", "sku_id", group_col]).agg(
        consecutive_days=("date", "count")
    ).reset_index()

    fig = px.box(
        incidents,
        x=group_col,
        y="consecutive_days",
        color=group_col,
        color_discrete_map=COL if group_col == "channel" else {
            "Tier 1": "#0071E3", "Tier 2": "#FF9500", "Tier 3": "#8E8E93"
        },
        points="outliers",
        labels={"consecutive_days": "Consecutive Days OOS per Incident", group_col: ""}
    )
    return tidy(fig, h=450)

# ==================== SHELF EXECUTION & COMPLIANCE ====================

@render_widget
def chart4_compliance_gauge():
    """Planogram & MSL Compliance Gauge / Progress Bar Matrix."""
    x, s = AU(), SO()
    req(len(x))

    plano = x.groupby(["category", "brand"])["planogram_compliance"].mean().mul(100).reset_index()

    s_msl = s[s.is_msl == True]
    if s_msl.empty:
        s_msl = s
    msl = s_msl.groupby(["category", "brand"])["is_out_of_stock"].apply(
        lambda v: (1 - v.mean()) * 100
    ).reset_index(name="msl_availability")

    merged = plano.merge(msl, on=["category", "brand"], how="left").fillna(0)
    merged["brand_key"] = merged["category"] + " · " + merged["brand"]

    melted = merged.melt(
        id_vars=["brand_key", "category", "brand"],
        value_vars=["planogram_compliance", "msl_availability"],
        var_name="Metric",
        value_name="Percentage"
    )
    melted["Metric"] = melted["Metric"].map({
        "planogram_compliance": "Planogram Compliance %",
        "msl_availability": "MSL Availability %"
    })

    fig = px.bar(
        melted,
        y="brand_key",
        x="Percentage",
        color="Metric",
        barmode="group",
        orientation="h",
        text_auto=".1f",
        color_discrete_map={
            "Planogram Compliance %": "#0071E3",
            "MSL Availability %": "#34C759"
        },
        labels={"Percentage": "Compliance Rate (%)", "brand_key": ""}
    )

    fig.add_vline(
        x=85, line_dash="dash", line_color="#34C759", line_width=2,
        annotation_text="85% Target Line",
        annotation_position="top right",
        annotation_font=dict(size=10, color="#1B873F")
    )
    fig.add_vline(
        x=62, line_dash="dot", line_color="#86868B", line_width=1.5,
        annotation_text="62% Baseline",
        annotation_position="bottom right",
        annotation_font=dict(size=10, color="#86868B")
    )
    fig.update_xaxes(range=[0, 105], ticksuffix="%")
    return tidy(fig, h=480)

@render_widget
def chart5_shelf_bubble():
    """Actual Shelf Share vs. Target Bubble Plot."""
    if active_role() == "Field Rep":
        return locked_chart("Actual Shelf Share vs Target")
    x, i = AU(), SI()
    req(len(x))

    shelf_store = x.groupby("store_id")["shelf_share_pct"].mean().reset_index()
    sales_store = i.groupby("store_id")["net_sales"].sum().reset_index()

    store_attrs = stores[["store_id", "store_name", "channel", "region", "segment_tier"]].drop_duplicates()
    df_bubble = store_attrs.merge(shelf_store, on="store_id").merge(sales_store, on="store_id")

    tier_sizes = {"Tier 1": 26, "Tier 2": 16, "Tier 3": 10}
    df_bubble["bubble_size"] = pd.to_numeric(
        df_bubble["segment_tier"].astype(str).map(tier_sizes).fillna(12),
        errors="coerce"
    ).astype(float)

    fig = px.scatter(
        df_bubble,
        x="shelf_share_pct",
        y="net_sales",
        size="bubble_size",
        color="channel",
        hover_name="store_name",
        hover_data={"segment_tier": True, "region": True, "shelf_share_pct": ":.1f%", "net_sales": ":$,.0f", "bubble_size": False},
        color_discrete_map=COL,
        labels={"shelf_share_pct": "Average Shelf Share %", "net_sales": "Total Net Sales ($)", "channel": "Channel"}
    )
    fig.add_vline(
        x=30, line_dash="dash", line_color="#FF9500", line_width=1.8,
        annotation_text="Target Shelf Share (≥30%)",
        annotation_position="top left",
        annotation_font=dict(size=10, color="#C97200")
    )
    fig.update_xaxes(ticksuffix="%")
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return tidy(fig, h=480)

@render_widget
def chart6_radar_scorecard():
    """Multi-Dimensional Execution Scorecard (Radar Chart)."""
    if active_role() == "Field Rep":
        return locked_chart("Execution Scorecard")
    x, s = AU(), SO()
    req(len(x))

    rad_axes = ["Planogram Compliance %", "MSL Availability %", "Secondary Display %", "Shelf Share %", "In-Stock Rate %"]
    group_col = input.c6_group() if hasattr(input, "c6_group") and input.c6_group() else "region"

    groups = sorted(x[group_col].unique().tolist())
    palette = ["#0071E3", "#34C759", "#FF9500", "#AF52DE", "#FF2D55"]

    fig = go.Figure()
    for idx, grp in enumerate(groups):
        sub_x = x[x[group_col] == grp]
        sub_s = s[s[group_col] == grp]
        sub_msl = sub_s[sub_s.is_msl == True]
        if sub_msl.empty:
            sub_msl = sub_s

        p_val = sub_x["planogram_compliance"].mean() * 100 if not sub_x.empty else 0
        m_val = (1 - sub_msl["is_out_of_stock"].mean()) * 100 if not sub_msl.empty else 0
        d_val = sub_x["secondary_display"].mean() * 100 if not sub_x.empty else 0
        sh_val = sub_x["shelf_share_pct"].mean() if not sub_x.empty else 0
        i_val = (1 - sub_s["is_out_of_stock"].mean()) * 100 if not sub_s.empty else 0

        vals = [p_val, m_val, d_val, sh_val, i_val]
        vals.append(vals[0])

        col_accent = palette[idx % len(palette)]
        fig.add_trace(go.Scatterpolar(
            r=vals,
            theta=rad_axes + [rad_axes[0]],
            fill="toself",
            name=grp,
            line=dict(color=col_accent, width=2.5)
        ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100],
                gridcolor="rgba(0,0,0,0.06)",
                tickfont=dict(size=10, color="#86868B")
            ),
            angularaxis=dict(
                gridcolor="rgba(0,0,0,0.06)",
                tickfont=dict(size=11, color="#1D1D1F")
            )
        )
    )
    return tidy(fig, h=480, is_polar=True)

# ==================== TRADE TERMS & NEGOTIATIONS ====================

@render_widget
def chart7_discount_margin():
    """Effective Discount vs Margin Realization Scatter Plot with Guardrails."""
    i = SI()
    req(len(i))

    si_g = i.merge(guardrails, on=["channel", "category"], how="left")

    agg = si_g.groupby(["store_id", "store_name", "channel", "region", "category"]).agg(
        units_sold=("units_sold", "sum"),
        net_sales=("net_sales", "sum"),
        gross_sales=("units_sold", lambda u: (u * si_g.loc[u.index, "list_price"]).sum()),
        cogs=("units_sold", lambda u: (u * si_g.loc[u.index, "unit_cost"]).sum()),
        max_discount_pct=("max_discount_pct", "first"),
        min_target_margin=("min_target_margin", "first")
    ).reset_index()

    agg["effective_discount_pct"] = (1 - (agg["net_sales"] / agg["gross_sales"])).mul(100).clip(lower=0)
    agg["realized_margin_pct"] = ((agg["net_sales"] - agg["cogs"]) / agg["net_sales"]).mul(100)

    agg["is_breach"] = (agg["effective_discount_pct"] > agg["max_discount_pct"]) | (agg["realized_margin_pct"] < agg["min_target_margin"])
    agg["zone_status"] = np.where(agg["is_breach"], "Red Zone (Requires Approval)", "Safe Zone (Compliant)")

    fig = px.scatter(
        agg,
        x="effective_discount_pct",
        y="realized_margin_pct",
        color="zone_status",
        color_discrete_map={
            "Red Zone (Requires Approval)": "#FF3B30",
            "Safe Zone (Compliant)": "#0071E3"
        },
        hover_name="store_name",
        hover_data={
            "channel": True,
            "category": True,
            "effective_discount_pct": ":.1f%",
            "max_discount_pct": ":.1f%",
            "realized_margin_pct": ":.1f%",
            "min_target_margin": ":.1f%",
            "zone_status": True
        },
        labels={
            "effective_discount_pct": "Effective Discount Rate %",
            "realized_margin_pct": "Realized Margin %",
            "zone_status": "Policy Status"
        }
    )

    avg_max_disc = agg["max_discount_pct"].mean()
    avg_min_mrg = agg["min_target_margin"].mean()
    fig.add_vline(
        x=avg_max_disc, line_dash="dash", line_color="#FF3B30", line_width=1.5,
        annotation_text=f"Max Discount Cap (~{avg_max_disc:.1f}%)",
        annotation_position="top right",
        annotation_font=dict(size=10, color="#D70015")
    )
    fig.add_hline(
        y=avg_min_mrg, line_dash="dash", line_color="#FF9500", line_width=1.5,
        annotation_text=f"Min Target Margin (~{avg_min_mrg:.1f}%)",
        annotation_position="bottom right",
        annotation_font=dict(size=10, color="#C97200")
    )
    fig.update_xaxes(ticksuffix="%")
    fig.update_yaxes(ticksuffix="%")
    return tidy(fig, h=480)

@render_widget
def chart8_tradeoff_compliance():
    """Trade-off Execution Commitment Compliance (Grouped Bar Chart)."""
    if active_role() == "Field Rep":
        return locked_chart("Trade-off Commitment Compliance")
    x = AU()
    req(len(x))

    store_audit = x.groupby("store_id").agg(
        avg_shelf_share=("shelf_share_pct", "mean"),
        avg_sec_display=("secondary_display", "mean"),
        avg_plano=("planogram_compliance", "mean")
    ).reset_index().merge(stores[["store_id", "channel"]], on="store_id")

    store_trades = store_audit.merge(guardrails[["channel", "required_exec_tradeoff"]].drop_duplicates(), on="channel")

    def eval_tradeoff(row):
        t = row["required_exec_tradeoff"]
        if "+10%" in t:
            return "Met (Delivered)" if (row["avg_shelf_share"] >= 30.0 or row["avg_sec_display"] >= 0.4) else "Unfulfilled (Breach)"
        elif "+15%" in t:
            return "Met (Delivered)" if (row["avg_shelf_share"] >= 35.0 or row["avg_sec_display"] >= 0.5) else "Unfulfilled (Breach)"
        elif "endcap" in t:
            return "Met (Delivered)" if (row["avg_sec_display"] >= 0.45) else "Unfulfilled (Breach)"
        elif "100% MSL" in t or "planogram" in t:
            return "Met (Delivered)" if (row["avg_plano"] >= 0.82) else "Unfulfilled (Breach)"
        return "Met (Delivered)"

    store_trades["status"] = store_trades.apply(eval_tradeoff, axis=1)

    short_map = {
        "Secure +10% shelf share or place 1 secondary display": "+10% Shelf Share / Sec. Display",
        "Commit to featured endcap placement for 4 weeks": "Featured Endcap (4 Weeks)",
        "Secure +15% shelf share or place 1 secondary display": "+15% Shelf Share / Sec. Display",
        "Achieve 100% MSL compliance + planogram adherence": "100% MSL & Planogram Adherence"
    }
    store_trades["tradeoff_label"] = store_trades["required_exec_tradeoff"].map(short_map).fillna(store_trades["required_exec_tradeoff"])

    counts = store_trades.groupby(["tradeoff_label", "status"]).size().reset_index(name="store_count")

    fig = px.bar(
        counts,
        x="tradeoff_label",
        y="store_count",
        color="status",
        barmode="group",
        text_auto=True,
        color_discrete_map={
            "Met (Delivered)": "#34C759",
            "Unfulfilled (Breach)": "#FF3B30"
        },
        labels={"store_count": "Number of Stores / Accounts", "tradeoff_label": "", "status": "Execution Status"}
    )
    return tidy(fig, h=460)

# ==================== COMMERCIAL STRATEGY & GEOSPATIAL ====================

@render_widget
def chart9_geo_map():
    """Regional Execution vs Revenue Performance Map (Choropleth Map)."""
    if active_role() == "Field Rep":
        return locked_chart("Regional Execution Map")
    i, x, s = SI(), AU(), SO()
    req(len(i))

    metric_choice = input.c9_metric() if hasattr(input, "c9_metric") and input.c9_metric() else "health_score"

    st_sales = i.groupby("state")["net_sales"].sum().reset_index()
    st_plano = x.groupby("state")["planogram_compliance"].mean().mul(100).reset_index()
    st_stock = s.groupby("state")["is_out_of_stock"].apply(lambda v: (1 - v.mean()) * 100).reset_index(name="in_stock_rate")
    st_stores = stores.groupby("state")["store_id"].count().reset_index(name="store_count")

    state_df = st_sales.merge(st_plano, on="state").merge(st_stock, on="state").merge(st_stores, on="state")
    state_df["health_score"] = (state_df["planogram_compliance"] * 0.5 + state_df["in_stock_rate"] * 0.5).round(1)

    fig = px.choropleth(
        state_df,
        locations="state",
        locationmode="USA-states",
        color=metric_choice,
        scope="usa",
        hover_name="state",
        hover_data={
            "health_score": ":.1f",
            "net_sales": ":$,.0f",
            "planogram_compliance": ":.1f%",
            "in_stock_rate": ":.1f%",
            "store_count": True,
            "state": False
        },
        color_continuous_scale="Blues",
        labels={
            "health_score": "Execution Health Score",
            "net_sales": "Net Sales ($)"
        }
    )
    fig.update_layout(
        geo=dict(
            bgcolor="rgba(0,0,0,0)",
            lakecolor="rgba(245,245,247,0.5)",
            showlakes=True,
            landcolor="#F5F5F7",
            subunitcolor="rgba(0,0,0,0.12)"
        )
    )
    return tidy(fig, h=520, is_geo=True)

@render_widget
def chart10_sankey_velocity():
    """Sell-In vs Sell-Out Inventory Velocity (Sankey Flow Diagram)."""
    if active_role() == "Field Rep":
        return locked_chart("Inventory Velocity Flow")
    i, s = SI(), SO()
    req(len(i))

    # Stage 1: Category -> Channel (Sell-In Volume)
    cat_chan = i.groupby(["category", "channel"])["units_sold"].sum().reset_index()

    # Stage 2: Channel -> Consumer Offtake vs Holding Buffer
    chan_so = s.groupby("channel")["units_sold_daily"].sum().reset_index(name="sell_out_units")
    chan_si = i.groupby("channel")["units_sold"].sum().reset_index(name="sell_in_units")
    chan_flow = chan_si.merge(chan_so, on="channel")
    chan_flow["buffer_units"] = (chan_flow["sell_in_units"] - chan_flow["sell_out_units"]).clip(lower=0)

    categories = sorted(i["category"].unique().tolist())
    channels = sorted(i["channel"].unique().tolist())
    destinations = ["Consumer Offtake (Sell-Out)", "Channel Stock Buffer (Holding)"]

    all_nodes = categories + channels + destinations
    node_idx = {name: idx for idx, name in enumerate(all_nodes)}

    node_colors = [
        "#0071E3", "#34C759", "#AF52DE",  # Categories
        "#FF9500", "#5856D6", "#FF2D55",  # Channels
        "#1B873F", "#8E8E93"              # Destinations
    ]

    sources, targets, values, link_colors = [], [], [], []

    for _, row in cat_chan.iterrows():
        if row["units_sold"] > 0:
            sources.append(node_idx[row["category"]])
            targets.append(node_idx[row["channel"]])
            values.append(row["units_sold"])
            link_colors.append("rgba(0, 113, 227, 0.22)")

    for _, row in chan_flow.iterrows():
        if row["sell_out_units"] > 0:
            sources.append(node_idx[row["channel"]])
            targets.append(node_idx["Consumer Offtake (Sell-Out)"])
            values.append(row["sell_out_units"])
            link_colors.append("rgba(52, 199, 89, 0.28)")

        if row["buffer_units"] > 0:
            sources.append(node_idx[row["channel"]])
            targets.append(node_idx["Channel Stock Buffer (Holding)"])
            values.append(row["buffer_units"])
            link_colors.append("rgba(255, 149, 0, 0.28)")

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=18,
            thickness=20,
            line=dict(color="rgba(0,0,0,0.15)", width=0.8),
            label=all_nodes,
            color=node_colors[:len(all_nodes)]
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_colors
        )
    )])
    return tidy(fig, h=480)
