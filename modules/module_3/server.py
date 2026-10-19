"""
modules/module_3/server.py
==========================
Server calculation and scenario modeling for Module 3: Commercial Negotiation Simulator.
"""

import pandas as pd
import plotly.graph_objects as go
from shiny import render, reactive, ui
from shinywidgets import render_widget


def module_3_server(input, output, session, dataset: dict, auth_state: dict):
    """Executes guardrail checks, margin realization formulas, and scenario visualization."""
    guardrails = dataset["guardrails"]
    prods = dataset["prods"]

    @reactive.calc
    def scenario_calc():
        chan = input.m3_channel() if hasattr(input, "m3_channel") else "Modern Trade"
        cat = input.m3_category() if hasattr(input, "m3_category") else "Surface Cleaners"
        sku = input.m3_sku() if hasattr(input, "m3_sku") else list(prods.sku_id)[0]
        vol = input.m3_volume() if hasattr(input, "m3_volume") and input.m3_volume() else 5000
        disc = input.m3_discount() if hasattr(input, "m3_discount") else 12.0

        p_row = prods[prods.sku_id == sku]
        if p_row.empty:
            p_row = prods.iloc[0:1]

        list_price = float(p_row["list_price"].iloc[0])
        unit_cost = float(p_row["unit_cost"].iloc[0])

        g_row = guardrails[(guardrails.channel == chan) & (guardrails.category == cat)]
        if not g_row.empty:
            max_disc = float(g_row["max_discount_pct"].iloc[0])
            min_margin = float(g_row["min_target_margin"].iloc[0])
            req_tradeoff = str(g_row["required_exec_tradeoff"].iloc[0])
        else:
            max_disc = 15.0
            min_margin = 25.0
            req_tradeoff = "+15% Shelf Share or Feature Endcap"

        net_price = list_price * (1.0 - (disc / 100.0))
        margin_pct = ((net_price - unit_cost) / net_price * 100.0) if net_price > 0 else 0.0

        shelf_checked = input.m3_tradeoff_shelf() if hasattr(input, "m3_tradeoff_shelf") else False
        endcap_checked = input.m3_tradeoff_endcap() if hasattr(input, "m3_tradeoff_endcap") else False
        tradeoff_met = shelf_checked or endcap_checked

        # Status determination
        if disc <= max_disc and margin_pct >= min_margin:
            status = "APPROVED"
            status_desc = f"Proposed discount ({disc:.1f}%) is within contractual cap (≤ {max_disc:.1f}%) and margin exceeds floor (≥ {min_margin:.1f}%)."
            badge_class = "badge bg-success"
            bg_style = "background:rgba(52, 199, 89, 0.12);border:1px solid rgba(52, 199, 89, 0.4);"
        elif margin_pct >= min_margin and tradeoff_met:
            status = "MANAGER OVERRIDE REQUIRED"
            status_desc = f"Discount ({disc:.1f}%) exceeds standard policy cap ({max_disc:.1f}%), but retailer concession ({req_tradeoff}) is verified and margin floor is preserved."
            badge_class = "badge bg-warning text-dark"
            bg_style = "background:rgba(255, 149, 0, 0.12);border:1px solid rgba(255, 149, 0, 0.4);"
        else:
            status = "POLICY BREACH / REJECTED"
            status_desc = f"Proposal breaches commercial policy. Realized margin ({margin_pct:.1f}%) violates contractual minimum ({min_margin:.1f}%) or mandatory concession was refused."
            badge_class = "badge bg-danger"
            bg_style = "background:rgba(255, 59, 48, 0.12);border:1px solid rgba(255, 59, 48, 0.4);"

        gross_rev = vol * list_price
        net_rev = vol * net_price
        cogs = vol * unit_cost
        gross_profit = net_rev - cogs

        return {
            "status": status,
            "status_desc": status_desc,
            "badge_class": badge_class,
            "bg_style": bg_style,
            "list_price": list_price,
            "net_price": net_price,
            "unit_cost": unit_cost,
            "margin_pct": margin_pct,
            "min_margin": min_margin,
            "max_disc": max_disc,
            "gross_rev": gross_rev,
            "net_rev": net_rev,
            "gross_profit": gross_profit,
            "cogs": cogs,
            "discount_val": gross_rev - net_rev
        }

    @render.ui
    def m3_approval_banner():
        res = scenario_calc()
        return ui.div(
            ui.div(
                ui.span(res["status"], class_=f"{res['badge_class']} px-3 py-2 rounded-pill fw-bold me-2"),
                ui.span("Automated Deal Assessment", class_="fw-bold", style="font-size:0.95rem;color:#1d1d1f;"),
                class_="d-flex align-items-center mb-2"
            ),
            ui.p(res["status_desc"], class_="small mb-0", style="line-height:1.45;"),
            class_="p-3 rounded-3 mb-3",
            style=res["bg_style"]
        )

    @render.ui
    def m3_financial_metrics():
        res = scenario_calc()
        return ui.div(
            ui.div(
                ui.div("Net Invoiced Price", class_="kpi-title"),
                ui.div(f"${res['net_price']:.2f}", class_="kpi-value text-dark"),
                ui.div(f"List: ${res['list_price']:.2f} · Cost: ${res['unit_cost']:.2f}", class_="small text-muted"),
                class_="kpi-tile"
            ),
            ui.div(
                ui.div("Realized Gross Margin %", class_="kpi-title"),
                ui.div(f"{res['margin_pct']:.1f}%", class_="kpi-value text-primary"),
                ui.div(f"Policy Floor: ≥ {res['min_margin']:.1f}%", class_="small text-muted"),
                class_="kpi-tile"
            ),
            ui.div(
                ui.div("Net Deal Revenue", class_="kpi-title"),
                ui.div(f"${res['net_rev']:,.0f}", class_="kpi-value text-success"),
                ui.div(f"Discount Concession: -${res['discount_val']:,.0f}", class_="small text-muted"),
                class_="kpi-tile"
            ),
            ui.div(
                ui.div("Realized Gross Profit", class_="kpi-title"),
                ui.div(f"${res['gross_profit']:,.0f}", class_="kpi-value text-dark"),
                ui.div(f"COGS: ${res['cogs']:,.0f}", class_="small text-muted"),
                class_="kpi-tile"
            ),
            class_="kpi-grid-container"
        )

    @render_widget
    def chart_m3_waterfall():
        res = scenario_calc()
        fig = go.Figure(go.Waterfall(
            name="Deal Structure",
            orientation="v",
            measure=["relative", "relative", "total", "relative", "total"],
            x=["Gross List Revenue", "Trade Discount", "Net Invoiced Revenue", "COGS (Cost)", "Gross Margin Dollar"],
            textposition="outside",
            text=[f"${res['gross_rev']:,.0f}", f"-${res['discount_val']:,.0f}", f"${res['net_rev']:,.0f}", f"-${res['cogs']:,.0f}", f"${res['gross_profit']:,.0f}"],
            y=[res['gross_rev'], -res['discount_val'], 0, -res['cogs'], 0],
            connector={"line": {"color": "rgba(74, 143, 231, 0.4)"}},
            decreasing={"marker": {"color": "#ff4d4f"}},
            increasing={"marker": {"color": "#4a8fe7"}},
            totals={"marker": {"color": "#73fbd3"}}
        ))
        fig.update_layout(
            template="plotly_white",
            height=340,
            margin=dict(l=10, r=20, t=30, b=20),
            yaxis_title="USD ($)"
        )
        return fig
