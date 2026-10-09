"""Marvel CPG - Module 1: Commercial Execution Analytics Dashboard.
Light Silvery Luxury Edition (Apple Pro Aesthetic).
Powered by real Marvel CPG data: dim_products.csv, dim_stores.csv, fact_execution_audits.csv,
fact_sell_in.csv, and fact_sell_out.csv.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shiny import App, reactive, render, req, ui
from shinywidgets import output_widget, render_widget

# ============================ DATA INGESTION & SETUP ============================
# Resolve path to marvel_data directory reliably across local, package, and container runtimes
BASE_DIR = Path(__file__).resolve().parent
CANDIDATE_DIRS = [
    BASE_DIR / "marvel_data",
    BASE_DIR.parent / "marvel_data",
    Path("marvel_data").resolve(),
    Path("/marvel_data"),
    Path("/app/marvel_data"),
]
DATA_DIR = None
for d in CANDIDATE_DIRS:
    if (d / "dim_products.csv").exists():
        DATA_DIR = d
        break

if DATA_DIR is None:
    raise FileNotFoundError(f"Could not locate marvel_data in candidate directories: {CANDIDATE_DIRS}.")

# 1. Dimension Tables
prods = pd.read_csv(DATA_DIR / "dim_products.csv")
stores = pd.read_csv(DATA_DIR / "dim_stores.csv")
guardrails = pd.read_csv(DATA_DIR / "dim_guardrails.csv")

# 2. Assign Rep Profiles deterministically to stores
REPS = [f"Rep {c}" for c in "ABCDEF"]
REP_PROFILES = {
    "Rep A": {
        "name": "Alex Mercer",
        "title": "Senior Field Representative",
        "region": "Northeast Territory",
        "email": "alex.mercer@marvelcpg.com",
        "badge": "East Commercial Accounts",
    },
    "Rep B": {
        "name": "Beatrice Vance",
        "title": "Regional Account Executive",
        "region": "Southeast Territory",
        "email": "beatrice.vance@marvelcpg.com",
        "badge": "Southeast Retail Accounts",
    },
    "Rep C": {
        "name": "Carlos Delgado",
        "title": "Commercial Execution Lead",
        "region": "Midwest Territory",
        "email": "carlos.delgado@marvelcpg.com",
        "badge": "Midwest Route Operations",
    },
    "Rep D": {
        "name": "Danielle Wu",
        "title": "Territory Account Executive",
        "region": "West Territory",
        "email": "danielle.wu@marvelcpg.com",
        "badge": "Pacific & Mountain Routes",
    },
    "Rep E": {
        "name": "Marcus Stone",
        "title": "Modern Trade Specialist",
        "region": "National Modern Trade",
        "email": "marcus.stone@marvelcpg.com",
        "badge": "Hypermarket & Supercenter Lead",
    },
    "Rep F": {
        "name": "Sarah Jenkins",
        "title": "Traditional Trade Specialist",
        "region": "Independent Retail Network",
        "email": "sarah.jenkins@marvelcpg.com",
        "badge": "Bodega & Distributor Routes",
    },
}

MANAGER_PROFILE = {
    "name": "Elena Rostova",
    "title": "Director of Commercial Operations & Execution",
    "role": "Regional Commercial Director",
    "email": "elena.rostova@marvelcpg.com",
    "scope": "All Commercial Territories (221 Stores · 4 Regions · 23 SKUs)",
}

# Deterministic rep assignment (seed 42 guarantees stability)
rng = np.random.default_rng(42)
stores["rep_id"] = rng.choice(REPS, len(stores))

# 3. Fact Tables
au_raw = pd.read_csv(DATA_DIR / "fact_execution_audits.csv")
si_raw = pd.read_csv(DATA_DIR / "fact_sell_in.csv")
so_raw = pd.read_csv(DATA_DIR / "fact_sell_out.csv")

# Date conversions
so_raw["date"] = pd.to_datetime(so_raw["date"])
so_raw["week"] = so_raw["date"] - pd.to_timedelta(so_raw["date"].dt.dayofweek, unit="D")

si_raw["date"] = pd.to_datetime(si_raw["date"])
si_raw["week"] = si_raw["date"] - pd.to_timedelta(si_raw["date"].dt.dayofweek, unit="D")

au_raw["date"] = pd.to_datetime(au_raw["date"])

# Merge metadata
store_meta = ["store_id", "store_name", "channel", "region", "state", "segment_tier", "city", "rep_id"]
prod_meta = ["sku_id", "product_name", "category", "brand", "is_msl", "unit_cost", "list_price"]

so = so_raw.merge(stores[store_meta], on="store_id").merge(prods[prod_meta], on="sku_id")
si = si_raw.merge(stores[store_meta], on="store_id").merge(prods[prod_meta], on="sku_id")
au = au_raw.merge(stores[store_meta], on="store_id").merge(prods[prod_meta], on="sku_id")

# Global reference lists
CH = sorted(stores["channel"].unique().tolist())
REG = sorted(stores["region"].unique().tolist())
SKU_CHOICES = {row.sku_id: f"{row.product_name} ({row.category})" for _, row in prods.iterrows()}
REP_SELECT_CHOICES = {
    rep_id: f"{prof['name']} — {prof['region']} ({rep_id})"
    for rep_id, prof in REP_PROFILES.items()
}

# Light Apple Pro Luxury Color Palette
COL = {
    "Modern Trade": "#0071E3",      # Apple Pro Blue
    "Traditional Trade": "#FF9500",  # Apple Gold / Amber
    "Distributor": "#AF52DE",        # Apple Royal Purple
}

MIN_DATE = str(so["date"].min().date())
MAX_DATE = str(so["date"].max().date())
DEFAULT_START = "2026-04-01"
DEFAULT_END = "2026-06-30"


# ============================ APPLE PRO LIGHT & SILVERY CSS ============================
APPLE_SILVER_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Cinzel:wght@600;700&display=swap');

:root {
  --canvas-bg: #f5f5f7;
  --card-bg: #ffffff;
  --sidebar-bg: #fbfbfd;
  --border-light: rgba(0, 0, 0, 0.07);
  --border-highlight: rgba(0, 0, 0, 0.12);
  --text-primary: #1d1d1f;
  --text-secondary: #6e6e73;
  --text-tertiary: #86868b;
  --apple-blue: #0071e3;
  --apple-blue-hover: #0077ed;
  --apple-green: #34c759;
  --apple-green-dark: #1b873f;
  --apple-red: #ff3b30;
  --apple-red-dark: #d70015;
  --apple-amber: #ff9500;
  --silver-shimmer: linear-gradient(180deg, #ffffff 0%, #f5f5f7 100%);
}

body, .bslib-page-fillable, .bslib-page-sidebar {
  background-color: var(--canvas-bg) !important;
  color: var(--text-primary) !important;
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* ================= LOGIN SCREEN STYLING (APPLE PRO LIGHT & SILVER) ================= */
.luxury-login-backdrop {
  min-height: 100vh;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(circle at 50% 12%, #ffffff 0%, #f5f5f7 50%, #e5e7eb 100%);
  padding: 40px 20px;
}

.luxury-login-card {
  width: 100%;
  max-width: 520px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(32px);
  -webkit-backdrop-filter: blur(32px);
  border: 1px solid rgba(255, 255, 255, 0.95);
  border-radius: 28px;
  box-shadow: 0 25px 70px -15px rgba(0, 0, 0, 0.07),
              0 1px 3px rgba(0, 0, 0, 0.04);
  padding: 42px 38px;
  position: relative;
  overflow: hidden;
}

.luxury-login-card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #0071e3 0%, #ff9500 50%, #34c759 100%);
}

.brand-crest {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  background: linear-gradient(135deg, #ffffff 0%, #f0f2f5 100%);
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 20px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);
  margin-bottom: 18px;
}

.brand-title {
  font-family: 'Cinzel', serif;
  font-size: 1.55rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: var(--text-primary);
  text-transform: uppercase;
  margin: 0;
}

.brand-subtitle {
  font-size: 0.76rem;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  font-weight: 600;
  margin-top: 6px;
  margin-bottom: 24px;
}

.role-tab-container {
  background: #ebebef;
  border-radius: 14px;
  padding: 4px;
  margin-bottom: 24px;
}

.role-tab-container .shiny-options-group {
  display: flex !important;
  gap: 4px;
}

.role-tab-container .form-check {
  flex: 1;
  padding: 0;
  margin: 0;
}

.role-tab-container .form-check-input {
  display: none;
}

.role-tab-container .form-check-label {
  display: block;
  text-align: center;
  padding: 9px 14px;
  border-radius: 11px;
  font-size: 0.86rem;
  font-weight: 600;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.25s ease;
}

.role-tab-container .form-check-input:checked + .form-check-label {
  background: #ffffff;
  color: var(--text-primary);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.profile-preview-card {
  background: #f5f5f7;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 16px;
  padding: 16px 18px;
  margin-bottom: 22px;
}

.profile-avatar {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, #0071e3, #005bb5);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 1rem;
  box-shadow: 0 4px 10px rgba(0, 113, 227, 0.25);
}

.profile-avatar.rep {
  background: linear-gradient(135deg, #ff9500, #d97706);
  box-shadow: 0 4px 10px rgba(255, 149, 0, 0.25);
}

.luxury-input-label {
  font-size: 0.74rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-bottom: 6px;
  display: block;
}

.luxury-login-card .form-control, .luxury-login-card .form-select {
  background: #ffffff !important;
  border: 1px solid rgba(0, 0, 0, 0.14) !important;
  color: var(--text-primary) !important;
  border-radius: 12px !important;
  padding: 11px 14px !important;
  font-size: 0.9rem !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03) !important;
  transition: all 0.2s ease;
}

.luxury-login-card .form-control:focus, .luxury-login-card .form-select:focus {
  border-color: var(--apple-blue) !important;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.18) !important;
  outline: none !important;
}

.btn-luxury-primary {
  width: 100%;
  background: var(--apple-blue) !important;
  color: #ffffff !important;
  font-weight: 600 !important;
  letter-spacing: 0.02em !important;
  border: none !important;
  border-radius: 14px !important;
  padding: 13px 20px !important;
  font-size: 0.95rem !important;
  box-shadow: 0 4px 14px rgba(0, 113, 227, 0.3) !important;
  cursor: pointer !important;
  transition: all 0.25s ease !important;
}

.btn-luxury-primary:hover {
  background: var(--apple-blue-hover) !important;
  box-shadow: 0 6px 20px rgba(0, 113, 227, 0.4) !important;
  transform: translateY(-1px);
}

.btn-quick-demo {
  background: #ffffff !important;
  border: 1px solid rgba(0, 0, 0, 0.1) !important;
  color: var(--text-primary) !important;
  font-size: 0.8rem !important;
  font-weight: 600 !important;
  border-radius: 10px !important;
  padding: 8px 12px !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03) !important;
  transition: all 0.2s ease !important;
}

.btn-quick-demo:hover {
  background: #f5f5f7 !important;
  border-color: rgba(0, 0, 0, 0.18) !important;
  color: var(--apple-blue) !important;
}

/* ================= DASHBOARD TOP EXECUTIVE BAR ================= */
.executive-navbar {
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  padding: 12px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0px;
}

.navbar-brand-badge {
  font-family: 'Cinzel', serif;
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-primary);
}

.pulse-dot {
  width: 8px;
  height: 8px;
  background-color: var(--apple-green);
  border-radius: 50%;
  display: inline-block;
  box-shadow: 0 0 10px var(--apple-green);
  animation: pulse-green 2s infinite;
}

@keyframes pulse-green {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(52, 199, 89, 0.7); }
  70% { transform: scale(1.05); box-shadow: 0 0 0 8px rgba(52, 199, 89, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(52, 199, 89, 0); }
}

.user-session-chip {
  background: #f5f5f7;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  padding: 6px 14px;
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.btn-luxury-logout {
  background: rgba(0, 0, 0, 0.04) !important;
  border: 1px solid rgba(0, 0, 0, 0.1) !important;
  color: var(--text-primary) !important;
  font-size: 0.78rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.02em !important;
  border-radius: 980px !important;
  padding: 7px 16px !important;
  transition: all 0.2s ease !important;
}

.btn-luxury-logout:hover {
  background: rgba(255, 59, 48, 0.08) !important;
  color: var(--apple-red-dark) !important;
  border-color: rgba(255, 59, 48, 0.25) !important;
}

/* ================= EXPLICIT SIDEBAR READABILITY (id="bslib_sidebar_1e20f2") ================= */
#bslib_sidebar_1e20f2,
[id^="bslib_sidebar"],
.sidebar,
aside.sidebar,
.bslib-sidebar-layout > .sidebar {
  background-color: var(--sidebar-bg) !important;
  border-right: 1px solid rgba(0, 0, 0, 0.08) !important;
  color: var(--text-primary) !important;
  padding: 20px 18px !important;
}

/* Sidebar Headings */
#bslib_sidebar_1e20f2 h6,
[id^="bslib_sidebar"] h6,
.sidebar h6 {
  font-size: 0.74rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: var(--text-primary) !important;
  margin-top: 14px;
  margin-bottom: 8px;
}

/* Sidebar Labels */
#bslib_sidebar_1e20f2 label,
#bslib_sidebar_1e20f2 .form-label,
#bslib_sidebar_1e20f2 .control-label,
[id^="bslib_sidebar"] label,
[id^="bslib_sidebar"] .form-label,
.sidebar label,
.sidebar .form-label {
  font-size: 0.82rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.02em !important;
  color: var(--text-primary) !important;
  margin-bottom: 5px !important;
  display: block !important;
}

/* Checkboxes */
#bslib_sidebar_1e20f2 .form-check-label,
[id^="bslib_sidebar"] .form-check-label,
.sidebar .form-check-label {
  color: var(--text-primary) !important;
  font-size: 0.84rem !important;
  font-weight: 500 !important;
}

#bslib_sidebar_1e20f2 .form-check-input,
[id^="bslib_sidebar"] .form-check-input,
.sidebar .form-check-input {
  border-color: rgba(0, 0, 0, 0.25) !important;
}

#bslib_sidebar_1e20f2 .form-check-input:checked,
[id^="bslib_sidebar"] .form-check-input:checked,
.sidebar .form-check-input:checked {
  background-color: var(--apple-blue) !important;
  border-color: var(--apple-blue) !important;
}

/* Form Controls, Datepicker, Selectize in Sidebar */
#bslib_sidebar_1e20f2 .form-control,
#bslib_sidebar_1e20f2 .form-select,
#bslib_sidebar_1e20f2 .selectize-input,
[id^="bslib_sidebar"] .form-control,
[id^="bslib_sidebar"] .form-select,
[id^="bslib_sidebar"] .selectize-input,
.sidebar .form-control,
.sidebar .form-select,
.sidebar .selectize-input {
  background-color: #ffffff !important;
  border: 1px solid rgba(0, 0, 0, 0.14) !important;
  color: var(--text-primary) !important;
  border-radius: 10px !important;
  font-size: 0.84rem !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04) !important;
}

#bslib_sidebar_1e20f2 .form-control:focus,
#bslib_sidebar_1e20f2 .selectize-input.focus,
[id^="bslib_sidebar"] .form-control:focus,
.sidebar .form-control:focus {
  border-color: var(--apple-blue) !important;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.18) !important;
}

/* Selectize Dropdown Options */
.selectize-dropdown {
  background: #ffffff !important;
  border: 1px solid rgba(0, 0, 0, 0.12) !important;
  color: var(--text-primary) !important;
  border-radius: 12px !important;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1) !important;
}

.selectize-dropdown .option {
  color: var(--text-primary) !important;
  padding: 8px 12px !important;
  font-size: 0.84rem !important;
}

.selectize-dropdown .option.active {
  background: #f5f5f7 !important;
  color: var(--apple-blue) !important;
}

.selectize-input .item {
  background: #f0f0f4 !important;
  color: var(--text-primary) !important;
  border: 1px solid rgba(0, 0, 0, 0.08) !important;
  border-radius: 6px !important;
  font-weight: 500 !important;
}

#bslib_sidebar_1e20f2 hr,
[id^="bslib_sidebar"] hr,
.sidebar hr {
  border-color: rgba(0, 0, 0, 0.08) !important;
  margin: 16px 0 !important;
}

/* Accordion in Sidebar */
#bslib_sidebar_1e20f2 .accordion-button,
[id^="bslib_sidebar"] .accordion-button,
.sidebar .accordion-button {
  background: #ffffff !important;
  color: var(--text-primary) !important;
  border: 1px solid rgba(0, 0, 0, 0.08) !important;
  border-radius: 10px !important;
  font-size: 0.82rem !important;
  font-weight: 600 !important;
  padding: 8px 12px !important;
}

#bslib_sidebar_1e20f2 .accordion-body,
[id^="bslib_sidebar"] .accordion-body,
.sidebar .accordion-body {
  background: #fafafc !important;
  color: #424245 !important;
  font-size: 0.78rem !important;
  border-radius: 0 0 10px 10px !important;
}

#bslib_sidebar_1e20f2 .text-muted,
[id^="bslib_sidebar"] .text-muted,
.sidebar .text-muted {
  color: var(--text-secondary) !important;
}

/* ================= CARDS & METRIC TILES (SILVER / WHITE LUXURY) ================= */
.card {
  background: var(--card-bg) !important;
  border: 1px solid var(--border-light) !important;
  border-radius: 20px !important;
  box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.03), 0 2px 6px -1px rgba(0, 0, 0, 0.02) !important;
  transition: transform 0.2s ease, box-shadow 0.2s ease !important;
  overflow: visible !important;
}

.card:hover {
  box-shadow: 0 10px 30px -4px rgba(0, 0, 0, 0.06), 0 2px 8px rgba(0, 0, 0, 0.03) !important;
}

.card .card-body {
  padding: 22px 24px !important;
  overflow: visible !important;
}

.chart-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
}

.chart-card-title {
  font-size: 0.94rem;
  font-weight: 700;
  color: #1d1d1f;
  letter-spacing: -0.01em;
}

/* KPI Metric Tiles */
.kpi-tile {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.07);
  border-radius: 16px;
  padding: 14px 14px;
  box-shadow: 0 2px 12px -2px rgba(0, 0, 0, 0.03), 0 1px 3px rgba(0, 0, 0, 0.02);
  position: relative;
  overflow: visible;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  z-index: 10;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.kpi-tile:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px -4px rgba(0, 0, 0, 0.06), 0 2px 6px rgba(0, 0, 0, 0.03);
  z-index: 999;
}

.kpi-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  position: relative;
}

.kpi-title {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-bottom: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-info-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.05);
  color: #86868b;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
  outline: none;
  border: none;
}

.kpi-info-symbol {
  font-size: 0.74rem;
  font-weight: 700;
  line-height: 1;
}

.kpi-info-trigger:hover,
.kpi-info-trigger:focus-within,
.kpi-tile:hover .kpi-info-trigger {
  background: #0071e3;
  color: #ffffff;
}

.kpi-hover-popover {
  display: none;
  position: absolute;
  top: calc(100% + 10px);
  left: 0;
  width: 330px;
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 18px 40px -8px rgba(0, 0, 0, 0.18), 0 4px 14px rgba(0, 0, 0, 0.06);
  z-index: 10000;
  text-align: left;
  animation: kpiPopFade 0.18s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.kpi-hover-popover::before {
  content: "";
  position: absolute;
  top: -6px;
  left: 6px;
  width: 10px;
  height: 10px;
  background: #ffffff;
  border-left: 1px solid rgba(0, 0, 0, 0.12);
  border-top: 1px solid rgba(0, 0, 0, 0.12);
  transform: rotate(45deg);
}

.kpi-tile:nth-last-child(-n+2) .kpi-hover-popover {
  left: auto;
  right: -6px;
}

.kpi-tile:nth-last-child(-n+2) .kpi-hover-popover::before {
  left: auto;
  right: 12px;
}

.kpi-info-trigger:hover .kpi-hover-popover,
.kpi-info-trigger:focus-within .kpi-hover-popover {
  display: block;
}

@keyframes kpiPopFade {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.kpi-pop-title {
  font-size: 0.82rem;
  font-weight: 700;
  color: #1d1d1f;
  margin-bottom: 2px;
}

.kpi-pop-desc {
  font-size: 0.72rem;
  color: #6e6e73;
  margin-bottom: 8px;
  line-height: 1.35;
}

.kpi-pop-sec {
  margin-bottom: 7px;
}

.kpi-pop-sec:last-child {
  margin-bottom: 0;
}

.kpi-pop-lbl {
  font-size: 0.62rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #86868b;
  margin-bottom: 2px;
  display: block;
}

.kpi-pop-badge {
  display: inline-block;
  background: #f0f4f8;
  color: #0071e3;
  padding: 2px 7px;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.68rem;
  font-weight: 600;
  border: 1px solid rgba(0, 113, 227, 0.15);
}

.kpi-pop-formula {
  background: #f8f9fa;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 6px;
  padding: 5px 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.66rem;
  color: #1d1d1f;
  word-break: break-word;
  white-space: pre-wrap;
  line-height: 1.35;
}

.kpi-pop-text {
  font-size: 0.7rem;
  color: #3a3a3c;
  line-height: 1.35;
}

.kpi-value {
  font-size: 1.65rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.1;
  margin-bottom: 6px;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Plus Jakarta Sans', sans-serif;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-value.text-success {
  color: var(--apple-green-dark) !important;
}

.kpi-value.text-danger {
  color: var(--apple-red-dark) !important;
}

.kpi-value.text-muted {
  color: var(--text-tertiary) !important;
}

.kpi-delta-pill {
  font-size: 0.68rem;
  font-weight: 600;
  border-radius: 980px;
  padding: 2px 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  letter-spacing: 0.01em;
}

.delta-positive {
  background: rgba(52, 199, 89, 0.12);
  color: var(--apple-green-dark);
  border: 1px solid rgba(52, 199, 89, 0.25);
}

.delta-negative {
  background: rgba(255, 59, 48, 0.1);
  color: var(--apple-red-dark);
  border: 1px solid rgba(255, 59, 48, 0.25);
}

.delta-neutral {
  background: rgba(0, 0, 0, 0.04);
  color: var(--text-tertiary);
  border: 1px solid rgba(0, 0, 0, 0.08);
}

.kpi-target-foot {
  font-size: 0.68rem;
  color: var(--text-tertiary);
  margin-top: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Horizontal 5-Metric Row Grid */
.kpi-horizontal-grid {
  display: grid !important;
  grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
  gap: 12px !important;
  width: 100% !important;
  margin-bottom: 22px !important;
}

@media (max-width: 1024px) {
  .kpi-horizontal-grid {
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)) !important;
  }
}

/* Vertically Stacked Analytical Sections */
.dashboard-section {
  margin-bottom: 38px;
  width: 100%;
}

.section-header-block {
  margin-bottom: 18px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.chart-logic-desc {
  font-size: 0.76rem;
  color: #6e6e73;
  margin-top: 5px;
  font-weight: 400;
  line-height: 1.45;
}

/* ================= DOMAIN TABS (APPLE LUXURY PILLS) ================= */
.nav-pills {
  background: #EDEDF0 !important;
  padding: 6px !important;
  border-radius: 980px !important;
  display: inline-flex !important;
  gap: 6px !important;
  border: 1px solid rgba(0, 0, 0, 0.06) !important;
  margin-bottom: 16px !important;
}

.nav-pills .nav-link {
  color: #6E6E73 !important;
  font-weight: 600 !important;
  font-size: 0.82rem !important;
  border-radius: 980px !important;
  padding: 8px 18px !important;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
  border: none !important;
  background: transparent !important;
}

.nav-pills .nav-link:hover {
  color: #1D1D1F !important;
  background: rgba(255, 255, 255, 0.5) !important;
}

.nav-pills .nav-link.active {
  background: #FFFFFF !important;
  color: #1D1D1F !important;
  font-weight: 700 !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.04) !important;
}

/* Category Section Badges */
.section-badge {
  font-size: 0.7rem;
  letter-spacing: 0.08em;
  font-weight: 700;
  text-transform: uppercase;
  padding: 3px 9px;
  border-radius: 980px;
  display: inline-block;
  margin-bottom: 4px;
}
.badge-oos { background: rgba(255, 59, 48, 0.1); color: #D70015; border: 1px solid rgba(255, 59, 48, 0.2); }
.badge-shelf { background: rgba(0, 113, 227, 0.1); color: #0071E3; border: 1px solid rgba(0, 113, 227, 0.2); }
.badge-trade { background: rgba(255, 149, 0, 0.1); color: #C97200; border: 1px solid rgba(255, 149, 0, 0.2); }
.badge-geo { background: rgba(52, 199, 89, 0.1); color: #1B873F; border: 1px solid rgba(52, 199, 89, 0.2); }

/* Quick Select / Deselect Action Buttons */
.btn-sku-action {
  background: rgba(0, 0, 0, 0.04) !important;
  color: #1d1d1f !important;
  border: 1px solid rgba(0, 0, 0, 0.08) !important;
  border-radius: 980px !important;
  font-size: 0.68rem !important;
  font-weight: 600 !important;
  padding: 2px 9px !important;
  line-height: 1.3 !important;
  transition: all 0.15s ease !important;
  cursor: pointer !important;
}

.btn-sku-action:hover {
  background: #0071e3 !important;
  color: #ffffff !important;
  border-color: #0071e3 !important;
}

/* Selectize luxury search styling */
.selectize-control.multi .selectize-input {
  border: 1px solid rgba(0, 0, 0, 0.1) !important;
  border-radius: 12px !important;
  background: #ffffff !important;
  padding: 6px 10px !important;
  box-shadow: none !important;
  font-size: 0.8rem !important;
  max-height: 160px;
  overflow-y: auto;
}

.selectize-control.multi .selectize-input.focus {
  border-color: #0071e3 !important;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.15) !important;
}

.selectize-control.multi .selectize-input > div {
  background: #f0f0f4 !important;
  color: #1d1d1f !important;
  border: 1px solid rgba(0, 0, 0, 0.08) !important;
  border-radius: 6px !important;
  padding: 2px 7px !important;
  font-size: 0.74rem !important;
  font-weight: 500 !important;
}

.selectize-dropdown {
  border: 1px solid rgba(0, 0, 0, 0.1) !important;
  border-radius: 12px !important;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1) !important;
  font-size: 0.78rem !important;
  background: #ffffff !important;
  z-index: 1050 !important;
}

.selectize-dropdown .option {
  padding: 8px 12px !important;
  color: #1d1d1f !important;
}

.selectize-dropdown .option.active,
.selectize-dropdown .option:hover {
  background: rgba(0, 113, 227, 0.08) !important;
  color: #0071e3 !important;
}

/* Tables */
.table, .dataframe, .shiny-data-grid {
  color: var(--text-primary) !important;
  background-color: transparent !important;
}

.table thead th {
  background: #f5f5f7 !important;
  color: var(--text-secondary) !important;
  font-size: 0.74rem !important;
  letter-spacing: 0.06em !important;
  text-transform: uppercase !important;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08) !important;
}

.table tbody tr {
  border-bottom: 1px solid rgba(0, 0, 0, 0.04) !important;
}

.table tbody tr:hover {
  background: rgba(0, 0, 0, 0.02) !important;
}
"""

# ============================ SHUFFLING NUMBERS & RE-LAYOUT JS ============================
APPLE_SHUFFLE_JS = """
// Apple Pro Number Shuffling & Deceleration Animation
function initNumberShuffle() {
  const elements = document.querySelectorAll('.shuffle-number');
  if (!elements.length) return;

  elements.forEach((el, index) => {
    const rawTarget = el.getAttribute('data-target');
    if (!rawTarget || isNaN(parseFloat(rawTarget))) return;

    const targetVal = parseFloat(rawTarget);
    const prefix = el.getAttribute('data-prefix') || '';
    const suffix = el.getAttribute('data-suffix') || '';
    const decimals = parseInt(el.getAttribute('data-decimals') || '1', 10);

    // Staggered settle timing: each card locks in 180ms after the previous one
    const baseDuration = 1000;
    const duration = baseDuration + (index * 180);
    const startTime = performance.now();

    // Cache corresponding delta pill to reveal after settling
    const parentTile = el.closest('.kpi-tile');
    const deltaPill = parentTile ? parentTile.querySelector('.kpi-delta-pill') : null;
    if (deltaPill) {
      deltaPill.style.opacity = '0.2';
      deltaPill.style.transition = 'opacity 0.4s ease';
    }

    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1.0);

      if (progress < 1.0) {
        // Shuffling phase
        if (progress > 0.65) {
          // Decelerating phase: approaching target with decaying jitter
          const jitterAmp = (1.0 - progress) * (Math.abs(targetVal) * 0.4 + 2);
          const jitter = (Math.random() - 0.5) * jitterAmp;
          const current = (targetVal * progress) + jitter;
          const sign = (current < 0 && !prefix.includes('-')) ? '-' : (prefix === '+' && current > 0 ? '+' : '');
          el.textContent = `${sign}${Math.abs(current).toFixed(decimals)}${suffix}`;
        } else {
          // Fast random slot-machine shuffle phase
          const minVal = targetVal >= 0 ? 5 : -25;
          const maxVal = targetVal >= 0 ? 98 : 15;
          const randVal = (Math.random() * (maxVal - minVal) + minVal);
          const sign = (randVal < 0) ? '-' : (prefix === '+' ? '+' : '');
          el.textContent = `${sign}${Math.abs(randVal).toFixed(decimals)}${suffix}`;
        }
        requestAnimationFrame(step);
      } else {
        // Locked settled phase
        const sign = (targetVal < 0) ? '-' : (prefix === '+' && targetVal > 0 ? '+' : '');
        el.textContent = `${sign}${Math.abs(targetVal).toFixed(decimals)}${suffix}`;
        el.classList.add('shuffle-settled');

        // Reveal delta pill with crisp pop
        if (deltaPill) {
          deltaPill.style.opacity = '1';
        }
      }
    }
    requestAnimationFrame(step);
  });

  // Secondary unit number shuffler (e.g. -42,046 units)
  const unitElements = document.querySelectorAll('.shuffle-units');
  unitElements.forEach((el, index) => {
    const rawVal = el.getAttribute('data-target');
    if (!rawVal || isNaN(parseFloat(rawVal))) return;
    const targetInt = parseInt(rawVal, 10);
    const suffix = el.getAttribute('data-suffix') || '';
    const duration = 1400;
    const startTime = performance.now();

    function stepUnits(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1.0);
      if (progress < 1.0) {
        const randOffset = Math.floor((Math.random() - 0.5) * 50000 * (1 - progress));
        const currentInt = Math.round(targetInt * progress) + randOffset;
        const sign = currentInt >= 0 ? '+' : '';
        el.textContent = `${sign}${currentInt.toLocaleString()}${suffix}`;
        requestAnimationFrame(stepUnits);
      } else {
        const sign = targetInt >= 0 ? '+' : '';
        el.textContent = `${sign}${targetInt.toLocaleString()}${suffix}`;
      }
    }
    requestAnimationFrame(stepUnits);
  });
}

// Hook into Shiny life-cycle events
$(document).on('shiny:value', function(event) {
  if (event.name === 'kpi_row') {
    setTimeout(initNumberShuffle, 60);
  }
});

$(document).on('shiny:inputchanged', function(event) {
  if (event.name === 'auth_state' && event.value === 'logged_in') {
    setTimeout(function() {
      initNumberShuffle();
      window.dispatchEvent(new Event('resize'));
    }, 180);
  }
});

// Window load fallback
window.addEventListener('load', function() {
  setTimeout(initNumberShuffle, 200);
});
"""


# ============================ UI DEFINITIONS ============================

def login_ui():
    """Renders the luxury brand login screen in Apple Pro silver light theme."""
    return ui.div(
        ui.div(
            ui.div(
                # Apple-style Brushed Titanium Monogram
                ui.div(
                    ui.HTML("""
                    <div class="brand-crest">
                        <svg width="34" height="34" viewBox="0 0 34 34" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M7 26V8L17 18L27 8V26" stroke="#1D1D1F" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M17 19L17 26" stroke="#0071E3" stroke-width="3.2" stroke-linecap="round"/>
                        </svg>
                    </div>
                    """),
                    class_="text-center"
                ),
                ui.h1("MARVEL CPG", class_="brand-title text-center"),
                ui.div("COMMERCIAL EXECUTION INTELLIGENCE", class_="brand-subtitle text-center"),

                # Role Selection Tabs (macOS style segmented switch)
                ui.div(
                    ui.input_radio_buttons(
                        "login_role_tab",
                        None,
                        {"manager": "👔  Regional Manager", "rep": "📱  Field Representative"},
                        selected="manager",
                        inline=True
                    ),
                    class_="role-tab-container"
                ),

                # Panel for Regional Manager Login
                ui.panel_conditional(
                    "input.login_role_tab == 'manager'",
                    ui.div(
                        ui.div(
                            ui.div("ER", class_="profile-avatar"),
                            ui.div(
                                ui.div("Elena Rostova", class_="fw-bold fs-6", style="color:#1d1d1f;"),
                                ui.div("Director of Commercial Operations & Execution", class_="small", style="color:#6e6e73;"),
                                ui.div("Executive Scope: All 221 Stores · 4 Regions · 23 SKUs", class_="small", style="color:#0071e3;font-weight:600;"),
                            ),
                            class_="d-flex align-items-center gap-3 profile-preview-card"
                        ),
                        ui.div(
                            ui.span("Enterprise SSO Identity", class_="luxury-input-label"),
                            ui.input_text("login_email", None, value="elena.rostova@marvelcpg.com"),
                            class_="mb-3"
                        ),
                        ui.div(
                            ui.span("Security Access Key", class_="luxury-input-label"),
                            ui.input_password("login_password", None, value="••••••••••••"),
                            class_="mb-4"
                        ),
                        ui.input_action_button(
                            "btn_login_manager",
                            "Enter Executive Command Suite →",
                            class_="btn-luxury-primary"
                        ),
                    )
                ),

                # Panel for Field Representative Login
                ui.panel_conditional(
                    "input.login_role_tab == 'rep'",
                    ui.div(
                        ui.div(
                            ui.span("Representative Account", class_="luxury-input-label"),
                            ui.input_select(
                                "login_rep_choice",
                                None,
                                REP_SELECT_CHOICES,
                                selected="Rep A"
                            ),
                            class_="mb-3"
                        ),
                        ui.div(
                            ui.div("FR", class_="profile-avatar rep"),
                            ui.div(
                                ui.output_ui("login_rep_preview_details")
                            ),
                            class_="d-flex align-items-center gap-3 profile-preview-card mb-3"
                        ),
                        ui.div(
                            ui.span("Territory Access PIN", class_="luxury-input-label"),
                            ui.input_password("login_rep_pin", None, value="••••"),
                            class_="mb-4"
                        ),
                        ui.input_action_button(
                            "btn_login_rep",
                            "Access Territory Field Suite →",
                            class_="btn-luxury-primary"
                        ),
                    )
                ),

                # Quick 1-Click Access Row for fast evaluation
                ui.div(
                    ui.div(
                        ui.hr(style="border-color:rgba(0,0,0,0.08);margin:24px 0 16px 0;"),
                        ui.div("QUICK DEMO ONE-CLICK SIGN IN", class_="text-center mb-2",
                               style="font-size:0.68rem;letter-spacing:0.12em;font-weight:700;color:#86868b;"),
                        ui.div(
                            ui.input_action_button("quick_mgr", "⚡ Manager View (All Stores)", class_="btn-quick-demo flex-fill"),
                            ui.input_action_button("quick_rep", "⚡ Field Rep (Alex Mercer · Rep A)", class_="btn-quick-demo flex-fill"),
                            class_="d-flex gap-2"
                        )
                    )
                ),

                # Security Footnote
                ui.div(
                    ui.HTML("""
                    <div class="mt-4 pt-2 text-center" style="font-size:0.7rem;color:#86868b;">
                        <span>🔒 SOC-2 Type II Certified · 256-bit TLS · Live Marvel CPG Warehouse</span>
                    </div>
                    """)
                ),

                class_="luxury-login-card"
            ),
            class_="luxury-login-backdrop"
        )
    )


def dashboard_ui():
    """Renders the executive commercial dashboard in light silvery Apple luxury theme."""
    return ui.div(
        # Top Executive Bar
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML("""
                    <div style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg, #1d1d1f 0%, #434346 100%);margin-right:10px;box-shadow:0 2px 6px rgba(0,0,0,0.12);">
                        <svg width="18" height="18" viewBox="0 0 34 34" fill="none">
                            <path d="M7 26V8L17 18L27 8V26" stroke="#FFFFFF" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M17 19L17 26" stroke="#0071E3" stroke-width="3.5" stroke-linecap="round"/>
                        </svg>
                    </div>
                    """),
                    ui.span("MARVEL CPG", class_="navbar-brand-badge"),
                    ui.span(" | COMMERCIAL INTELLIGENCE", style="font-size:0.76rem;letter-spacing:0.1em;color:#86868b;font-weight:600;margin-left:4px;"),
                    class_="d-inline-flex align-items-center"
                ),
                ui.div(
                    ui.span(class_="pulse-dot me-2"),
                    ui.span("LIVE WAREHOUSE: 207,020 AUDIT RECORDS", style="font-size:0.72rem;letter-spacing:0.08em;font-weight:700;color:#1b873f;"),
                    class_="d-none d-md-inline-flex align-items-center ms-4"
                ),
                class_="d-flex align-items-center"
            ),
            ui.div(
                ui.output_ui("nav_user_badge"),
                ui.input_action_button("btn_logout", "Sign Out / Switch Role", class_="btn-luxury-logout ms-3"),
                class_="d-flex align-items-center"
            ),
            class_="executive-navbar"
        ),

        # Main Workspace with Sidebar
        ui.layout_sidebar(
            ui.sidebar(
                # Sidebar Brand Header
                ui.div(
                    ui.div("PORTFOLIO FILTERS", class_="fw-bold text-uppercase",
                           style="font-size:0.74rem;letter-spacing:0.12em;color:#1d1d1f;margin-bottom:8px;"),
                    ui.output_ui("sidebar_scope_badge"),
                    ui.hr()
                ),
                # Clean Filters: High contrast and readable against silvery background
                ui.input_date_range(
                    "dates",
                    "Date range (snaps to full weeks)",
                    start=DEFAULT_START,
                    end=DEFAULT_END,
                    min=MIN_DATE,
                    max=MAX_DATE
                ),
                ui.input_checkbox_group("channel", "Channel", CH, selected=CH),
                ui.input_selectize("region", "Region", REG, selected=REG, multiple=True),
                ui.div(
                    ui.div(
                        ui.span("Product SKU (23 Available)", class_="control-label fw-bold", style="font-size:0.8rem;color:#1d1d1f;"),
                        ui.div(
                            ui.input_action_button("btn_sku_all", "Select All", class_="btn-sku-action"),
                            ui.input_action_button("btn_sku_none", "Deselect All", class_="btn-sku-action"),
                            class_="d-flex gap-1"
                        ),
                        class_="d-flex align-items-center justify-content-between mb-1"
                    ),
                    ui.input_selectize(
                        "sku",
                        None,
                        SKU_CHOICES,
                        selected=list(prods.sku_id),
                        multiple=True,
                        options={
                            "placeholder": "Type to search & select products...",
                            "plugins": ["remove_button"],
                            "closeAfterSelect": False
                        }
                    ),
                    class_="mb-2"
                ),
                ui.hr(),
                ui.accordion(
                    ui.accordion_panel(
                        "KPI Definitions & Formulas",
                        ui.tags.small(
                            ui.tags.ul(
                                ui.tags.li(
                                    ui.tags.b("MSL Compliance %: "),
                                    "COUNT(DISTINCT CASE WHEN non_compliant_stores = 0 THEN store_id END) / COUNT(DISTINCT store_id) * 100",
                                    ui.tags.br(),
                                    ui.tags.span("Source: fact_sell_out (or fact_inventory), dim_products · Target ≥ 90%", class_="text-muted", style="font-size:0.68rem;")
                                ),
                                ui.tags.li(
                                    ui.tags.b("OOS Rate %: "),
                                    "SUM(CASE WHEN is_out_of_stock = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100",
                                    ui.tags.br(),
                                    ui.tags.span("Source: fact_sell_out (or fact_inventory) · Target ≤ 5%", class_="text-muted", style="font-size:0.68rem;")
                                ),
                                ui.tags.li(
                                    ui.tags.b("Sell-in vs Sell-out Gap %: "),
                                    "(SUM(units_sell_in) - SUM(units_sold_daily)) / SUM(units_sold_daily) * 100",
                                    ui.tags.br(),
                                    ui.tags.span("Source: fact_sell_in, fact_sell_out · Target within ±5%", class_="text-muted", style="font-size:0.68rem;")
                                ),
                                ui.tags.li(
                                    ui.tags.b("Shelf Share %: "),
                                    "AVG(shelf_share_pct) grouped by category, channel, and region",
                                    ui.tags.br(),
                                    ui.tags.span("Source: fact_execution_audits · Target ≥ 30%", class_="text-muted", style="font-size:0.68rem;")
                                ),
                                ui.tags.li(
                                    ui.tags.b("Planogram Compliance %: "),
                                    "SUM(CASE WHEN planogram_compliance = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100",
                                    ui.tags.br(),
                                    ui.tags.span("Source: fact_execution_audits · Target ≥ 80%", class_="text-muted", style="font-size:0.68rem;")
                                ),
                                style="padding-left:14px;margin-bottom:0;"
                            )
                        )
                    ),
                    open=False
                ),
                ui.div(
                    ui.tags.small("Source: Marvel Enterprise Data Warehouse (dim_products, dim_stores, fact_execution_audits, fact_sell_in, fact_sell_out)"),
                    class_="mt-3 text-muted",
                    style="font-size:0.7rem;line-height:1.4;"
                ),
                width=320,
                id="bslib_sidebar_1e20f2",
                bg="#FBFBFD",
                fg="#1D1D1F"
            ),

            # Main Analytics Canvas
            ui.div(
                ui.output_ui("ctx"),
                ui.output_ui("kpi_row"),
                ui.div(style="height:16px;"),

                # ==================== 4. COMMERCIAL STRATEGY & GEOSPATIAL ====================
                ui.div(
                    ui.div(
                        ui.span("4. COMMERCIAL STRATEGY & GEOSPATIAL EXECUTION", class_="section-badge badge-geo"),
                        ui.h5("Geospatial Territory Health & Multi-Stage Inventory Velocity", class_="fw-bold mb-1", style="color:#1d1d1f;"),
                        ui.p("Target Users: Regional Manager & Commercial Operations · Allocates field rep routes and synchronizes channel Sell-In with store Sell-Out.", class_="small text-muted mb-0"),
                        class_="section-header-block"
                    ),
                    ui.panel_conditional(
                        "input.user_role_state == 'manager'",
                        ui.div(
                            # Chart 9: Full-width
                            ui.card(
                                ui.div(
                                    ui.div(
                                        ui.span("Chart 9: Regional Execution vs Revenue Performance Map (Choropleth)", class_="chart-card-title"),
                                        ui.div(
                                            ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                            "Combines state-level invoiced sales from fact_sell_in, planogram adherence from fact_execution_audits, and store in-stock rates from fact_sell_out into a composite commercial health score across US territory states.",
                                            class_="chart-logic-desc"
                                        )
                                    ),
                                    ui.input_radio_buttons("c9_metric", None, {"health_score": "Health Score", "net_sales": "Net Sales ($)"}, selected="health_score", inline=True),
                                    class_="chart-header-row"
                                ),
                                output_widget("chart9_geo_map"),
                                full_screen=True,
                                class_="w-100 mb-4"
                            ),
                            # Chart 10: Full-width
                            ui.card(
                                ui.div(
                                    ui.div(
                                        ui.span("Chart 10: Sell-In vs Sell-Out Inventory Velocity (Sankey Flow Diagram)", class_="chart-card-title"),
                                        ui.div(
                                            ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                            "Visualizes multi-echelon supply flow from product category to trade channels using fact_sell_in invoiced volume and tracks throughput into consumer offtake versus channel holding buffer using fact_sell_out daily units.",
                                            class_="chart-logic-desc"
                                        ),
                                        class_="d-flex flex-column"
                                    ),
                                    class_="chart-header-row"
                                ),
                                output_widget("chart10_sankey_velocity"),
                                full_screen=True,
                                class_="w-100 mb-4"
                            )
                        )
                    ),
                    ui.panel_conditional(
                        "input.user_role_state == 'rep'",
                        ui.div(
                            ui.HTML("""
                            <div style="width:52px;height:52px;border-radius:16px;background:rgba(255,59,48,0.08);display:inline-flex;align-items:center;justify-content:center;margin-bottom:14px;border:1px solid rgba(255,59,48,0.2);">
                                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                                    <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                                </svg>
                            </div>
                            """),
                            ui.h5("Regional Manager Access Required", class_="fw-bold mb-2", style="color:#1d1d1f;"),
                            ui.div(
                                "Section 4 (Regional Execution Choropleth Map & Multi-Stage Inventory Velocity Sankey) is restricted to Regional Commercial Directors and Supply Chain Operations. Field Sales Representatives have route access scoped to their assigned territory.",
                                class_="text-muted small mb-3",
                                style="max-width:540px;margin:0 auto;line-height:1.5;"
                            ),
                            ui.span("Active Account Role: Field Sales Representative", class_="badge bg-light text-secondary border px-3 py-2 rounded-pill"),
                            class_="text-center p-5 card rounded-4 border-0 my-3",
                            style="background:#ffffff;border:1px solid rgba(0,0,0,0.06) !important;box-shadow:0 4px 20px -2px rgba(0,0,0,0.03);"
                        )
                    ),
                    class_="dashboard-section"
                ),

                # ==================== 1. OOS & SUPPLY CHAIN INSIGHTS ====================
                ui.div(
                    ui.div(
                        ui.span("1. OUT-OF-STOCK (OOS) & SUPPLY CHAIN INSIGHTS", class_="section-badge badge-oos"),
                        ui.h5("Real-Time Store Inventory Radar & Lost Sales Mitigation", class_="fw-bold mb-1", style="color:#1d1d1f;"),
                        ui.p("Target Users: Field Sales Representative & Regional Operations · Tracks mandatory MSL items, highlights impending stockouts, and quantifies commercial loss.", class_="small text-muted mb-0"),
                        class_="section-header-block"
                    ),
                    # Chart 1: Full-width
                    ui.card(
                        ui.div(
                            ui.div(
                                ui.span("Chart 1: Stock Level & OOS Hazard Heatmap (Mandatory MSL Items · Red = Active OOS · Amber = Impending Stockout Hazard)", class_="chart-card-title"),
                                ui.div(
                                    ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                    "Built from fact_sell_out daily on-hand store inventory levels across mandatory MSL items from dim_products, mapping zero inventory units to high-priority red out-of-stock hazard alerts and low buffers (1–15 units) to amber warnings on the latest date.",
                                    class_="chart-logic-desc"
                                )
                            ),
                            ui.span("Target: Field Sales Rep Pre-Visit Radar", class_="badge bg-light text-secondary border"),
                            class_="chart-header-row"
                        ),
                        output_widget("chart1_oos_hazard"),
                        full_screen=True,
                        class_="w-100 mb-4"
                    ),
                    # Chart 2: Full-width
                    ui.panel_conditional(
                        "input.user_role_state == 'manager'",
                        ui.card(
                            ui.div(
                                ui.div(
                                    ui.span("Chart 2: Estimated Lost Sales Trend & Cumulative Loss (Stacked Area Chart)", class_="chart-card-title"),
                                    ui.div(
                                        ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                        "Created by joining fact_sell_out and dim_products, aggregating weekly lost revenue by multiplying active out-of-stock store-SKU days by their in-stock average daily unit baseline and list price.",
                                        class_="chart-logic-desc"
                                    )
                                ),
                                ui.input_radio_buttons("c2_group", None, {"region": "By Region", "category": "By Category"}, selected="region", inline=True),
                                class_="chart-header-row"
                            ),
                            output_widget("chart2_lost_sales"),
                            full_screen=True,
                            class_="w-100 mb-4"
                        )
                    ),
                    # Chart 3: Full-width
                    ui.panel_conditional(
                        "input.user_role_state == 'manager'",
                        ui.card(
                            ui.div(
                                ui.div(
                                    ui.span("Chart 3: OOS Duration Distribution (Boxplot)", class_="chart-card-title"),
                                    ui.div(
                                        ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                        "Calculated from chronological store-SKU-date observations in fact_sell_out, measuring consecutive days in out-of-stock state to distinguish transient single-day spikes from persistent replenishment delays across channels and tiers.",
                                        class_="chart-logic-desc"
                                    )
                                ),
                                ui.input_radio_buttons("c3_group", None, {"channel": "By Channel", "segment_tier": "By Tier"}, selected="channel", inline=True),
                                class_="chart-header-row"
                            ),
                            output_widget("chart3_oos_duration"),
                            full_screen=True,
                            class_="w-100 mb-4"
                        )
                    ),
                    ui.panel_conditional(
                        "input.user_role_state == 'rep'",
                        ui.div(
                            ui.HTML("""
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <span style="font-size:0.95rem;">🔒</span>
                                <span class="fw-bold" style="font-size:0.82rem;color:#1d1d1f;">Macro Financial & Replenishment Analytics Restricted</span>
                            </div>
                            """),
                            ui.div(
                                "Charts 2 & 3 (Estimated Lost Sales Trend & OOS Duration Distribution) are restricted to Regional Management & Operations oversight. Field Sales Representatives have operational access to the store-level Stock Level & OOS Hazard Radar above.",
                                class_="small text-muted"
                            ),
                            class_="card p-3 rounded-4 border-0 mb-3",
                            style="background:#fbfbfd;border:1px solid rgba(0,0,0,0.06) !important;"
                        )
                    ),
                    class_="dashboard-section"
                ),

                # ==================== 2. SHELF EXECUTION & COMPLIANCE ====================
                ui.div(
                    ui.div(
                        ui.span("2. SHELF EXECUTION & COMPLIANCE OPTIMIZATION", class_="section-badge badge-shelf"),
                        ui.h5("Planogram, MSL Availability & Shelf Real Estate Health", class_="fw-bold mb-1", style="color:#1d1d1f;"),
                        ui.p("Target Users: Field Sales Rep & Regional Manager · Converts store audit compliance into revenue-generating shelf real estate.", class_="small text-muted mb-0"),
                        class_="section-header-block"
                    ),
                    # Chart 4: Full-width
                    ui.card(
                        ui.div(
                            ui.div(
                                ui.span("Chart 4: Planogram & MSL Compliance Gauge Matrix (Target Benchmark ≥ 85% vs Historical Baseline 62%)", class_="chart-card-title"),
                                ui.div(
                                    ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                    "Combines planogram compliance adherence rates from fact_execution_audits with in-stock MSL product availability from fact_sell_out, grouped by product category and brand against the 85% target benchmark.",
                                    class_="chart-logic-desc"
                                )
                            ),
                            ui.span("Actionable Store-Fix Checklist", class_="badge bg-light text-secondary border"),
                            class_="chart-header-row"
                        ),
                        output_widget("chart4_compliance_gauge"),
                        full_screen=True,
                        class_="w-100 mb-4"
                    ),
                    # Chart 5: Full-width
                    ui.panel_conditional(
                        "input.user_role_state == 'manager'",
                        ui.card(
                            ui.div(
                                ui.div(
                                    ui.span("Chart 5: Actual Shelf Share vs. Target Bubble Plot", class_="chart-card-title"),
                                    ui.div(
                                        ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                        "Plots store-level average category shelf share from fact_execution_audits against total invoiced net sales from fact_sell_in, sizing bubbles by commercial segment tier from dim_stores to highlight revenue conversion vs. the 30% shelf target.",
                                        class_="chart-logic-desc"
                                    )
                                ),
                                class_="chart-header-row"
                            ),
                            output_widget("chart5_shelf_bubble"),
                            full_screen=True,
                            class_="w-100 mb-4"
                        )
                    ),
                    # Chart 6: Full-width
                    ui.panel_conditional(
                        "input.user_role_state == 'manager'",
                        ui.card(
                            ui.div(
                                ui.div(
                                    ui.span("Chart 6: Multi-Dimensional Execution Scorecard (Radar Chart)", class_="chart-card-title"),
                                    ui.div(
                                        ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                        "Synthesizes 5 key execution dimensions across fact_execution_audits and fact_sell_out (planogram adherence, MSL availability, secondary displays, shelf share %, and general in-stock rate) grouped by territory region or channel.",
                                        class_="chart-logic-desc"
                                    )
                                ),
                                ui.input_radio_buttons("c6_group", None, {"region": "By Region", "segment_tier": "By Tier", "channel": "By Channel"}, selected="region", inline=True),
                                class_="chart-header-row"
                            ),
                            output_widget("chart6_radar_scorecard"),
                            full_screen=True,
                            class_="w-100 mb-4"
                        )
                    ),
                    ui.panel_conditional(
                        "input.user_role_state == 'rep'",
                        ui.div(
                            ui.HTML("""
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <span style="font-size:0.95rem;">🔒</span>
                                <span class="fw-bold" style="font-size:0.82rem;color:#1d1d1f;">Territory-Wide Shelf Share & Multi-KPI Scorecard Restricted</span>
                            </div>
                            """),
                            ui.div(
                                "Charts 5 & 6 (Actual Shelf Share vs. Target Bubble Plot & Multi-Dimensional Radar Scorecard) are restricted to Regional Management oversight. Field Sales Representatives have store audit access via the Planogram & MSL Compliance Checklist above.",
                                class_="small text-muted"
                            ),
                            class_="card p-3 rounded-4 border-0 mb-3",
                            style="background:#fbfbfd;border:1px solid rgba(0,0,0,0.06) !important;"
                        )
                    ),
                    class_="dashboard-section"
                ),

                # ==================== 3. TRADE TERMS & NEGOTIATIONS ====================
                ui.div(
                    ui.div(
                        ui.span("3. TRADE TERMS & COMMERCIAL GUARDRAILS", class_="section-badge badge-trade"),
                        ui.h5("Real-Time Negotiation Boundaries & Concession Enforcement", class_="fw-bold mb-1", style="color:#1d1d1f;"),
                        ui.p("Target Users: Field Sales Rep & Regional Manager · Eliminates data-free negotiations, flags policy-breaching discounts, and verifies execution concessions.", class_="small text-muted mb-0"),
                        class_="section-header-block"
                    ),
                    # Chart 7: Full-width
                    ui.card(
                        ui.div(
                            ui.div(
                                ui.span("Chart 7: Effective Discount vs Margin Realization Scatter Plot with Guardrails", class_="chart-card-title"),
                                ui.div(
                                    ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                    "Integrates weekly transaction revenue and cost from fact_sell_in with dim_guardrails commercial caps, comparing effective invoice discount percentage against gross margin to flag policy-breaching transactions requiring managerial override.",
                                    class_="chart-logic-desc"
                                )
                            ),
                            class_="chart-header-row"
                        ),
                        output_widget("chart7_discount_margin"),
                        full_screen=True,
                        class_="w-100 mb-4"
                    ),
                    # Chart 8: Full-width
                    ui.panel_conditional(
                        "input.user_role_state == 'manager'",
                        ui.card(
                            ui.div(
                                ui.div(
                                    ui.span("Chart 8: Trade-off Execution Commitment Compliance (Grouped Bar Chart)", class_="chart-card-title"),
                                    ui.div(
                                        ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                        "Evaluates customer concession fulfillment by comparing audit observations in fact_execution_audits (shelf share %, secondary displays, planogram rates) against required retailer trade-off commitments defined in dim_guardrails.",
                                        class_="chart-logic-desc"
                                    )
                                ),
                                class_="chart-header-row"
                            ),
                            output_widget("chart8_tradeoff_compliance"),
                            full_screen=True,
                            class_="w-100 mb-4"
                        )
                    ),
                    ui.panel_conditional(
                        "input.user_role_state == 'rep'",
                        ui.div(
                            ui.HTML("""
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <span style="font-size:0.95rem;">🔒</span>
                                <span class="fw-bold" style="font-size:0.82rem;color:#1d1d1f;">Contractual Concession Oversight Restricted</span>
                            </div>
                            """),
                            ui.div(
                                "Chart 8 evaluates contractual concessions and required trade-offs across regional accounts and is restricted to Regional Management. Field Sales Reps have real-time negotiation boundary guidance via Chart 7.",
                                class_="small text-muted"
                            ),
                            class_="card p-3 rounded-4 border-0 mb-3",
                            style="background:#fbfbfd;border:1px solid rgba(0,0,0,0.06) !important;"
                        )
                    ),
                    class_="dashboard-section"
                ),
                class_="p-2"
            )
        )
    )


app_ui = ui.page_fillable(
    # Hidden reactive auth state: 'logged_out' or 'logged_in', and user role: 'manager' or 'rep'
    ui.div(
        ui.input_text("auth_state", "", value="logged_out"),
        ui.input_text("user_role_state", "", value="manager"),
        style="display:none;"
    ),
    # Global head resources: Google Fonts and Light Silver Luxury Apple CSS & Shuffling JS
    ui.tags.head(
        ui.tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
        ui.tags.link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin="anonymous"),
        ui.tags.link(
            href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap",
            rel="stylesheet"
        ),
        ui.tags.style(APPLE_SILVER_CSS),
        ui.tags.script(APPLE_SHUFFLE_JS),
    ),
    # 1. LOGIN SCREEN
    ui.panel_conditional(
        "input.auth_state == 'logged_out'",
        login_ui()
    ),
    # 2. DASHBOARD SCREEN
    ui.panel_conditional(
        "input.auth_state == 'logged_in'",
        dashboard_ui()
    ),
    title="Marvel CPG | Commercial Execution Intelligence"
)


# ============================ SERVER LOGIC ============================
def server(input, output, session):
    # Reactive Auth Session State
    active_role = reactive.value("Regional Manager")
    active_rep = reactive.value(None)
    active_name = reactive.value(MANAGER_PROFILE["name"])
    active_title = reactive.value(MANAGER_PROFILE["title"])

    # ---------- LOGIN ACTIONS ----------
    @reactive.effect
    @reactive.event(input.btn_login_manager)
    def _do_login_manager():
        active_role.set("Regional Manager")
        active_rep.set(None)
        active_name.set(MANAGER_PROFILE["name"])
        active_title.set(MANAGER_PROFILE["title"])
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="manager")

    @reactive.effect
    @reactive.event(input.btn_login_rep)
    def _do_login_rep():
        rep_id = input.login_rep_choice() if input.login_rep_choice() else "Rep A"
        prof = REP_PROFILES.get(rep_id, REP_PROFILES["Rep A"])
        active_role.set("Field Rep")
        active_rep.set(rep_id)
        active_name.set(prof["name"])
        active_title.set(f"{prof['title']} ({prof['region']})")
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="rep")

    @reactive.effect
    @reactive.event(input.quick_mgr)
    def _quick_mgr():
        active_role.set("Regional Manager")
        active_rep.set(None)
        active_name.set(MANAGER_PROFILE["name"])
        active_title.set(MANAGER_PROFILE["title"])
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="manager")

    @reactive.effect
    @reactive.event(input.quick_rep)
    def _quick_rep():
        prof = REP_PROFILES["Rep A"]
        active_role.set("Field Rep")
        active_rep.set("Rep A")
        active_name.set(prof["name"])
        active_title.set(f"{prof['title']} ({prof['region']})")
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="rep")

    @reactive.effect
    @reactive.event(input.btn_logout)
    def _do_logout():
        ui.update_text("auth_state", value="logged_out")

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
                   class_="small", style="color:#ff9500;font-weight:600;")
        )

    # Navbar Profile Badge
    @render.ui
    def nav_user_badge():
        is_rep = active_role() == "Field Rep"
        role_label = f"Field Representative · {active_rep()}" if is_rep else "Regional Manager"
        badge_style = "background:rgba(255,149,0,0.12);border:1px solid rgba(255,149,0,0.25);color:#c97200;" if is_rep else "background:rgba(0,113,227,0.1);border:1px solid rgba(0,113,227,0.25);color:#0071e3;"
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
                ui.span(f"{rep_id} ({n_stores} Stores)", class_="fw-bold", style="color:#ff9500;font-size:0.8rem;"),
                class_="mb-1"
            )
        return ui.div(
            ui.span("Portfolio Active: ", style="color:#6e6e73;font-size:0.78rem;"),
            ui.span(f"All Stores ({len(stores)} Stores)", class_="fw-bold", style="color:#0071e3;font-size:0.8rem;"),
            class_="mb-1"
        )

    # ---------- REACTIVE FILTERING & SCOPE ----------
    def scope(df, col, a, b):
        channels = input.channel() if input.channel() else CH
        regions = input.region() if input.region() else REG
        skus = input.sku() if input.sku() is not None else list(prods.sku_id)

        m = (df[col].between(a, b) & df.channel.isin(channels)
             & df.region.isin(regions) & df.sku_id.isin(skus))
        if active_role() == "Field Rep" and active_rep():
            m &= (df.rep_id == active_rep())
        return df[m]

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
        return scope(so, "date", *span())

    @reactive.calc
    def SI():
        return scope(si, "week", *span())

    @reactive.calc
    def AU():
        return scope(au, "date", *span())

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
        s, i, x = scope(so, "date", a, b), scope(si, "week", a, b), scope(au, "date", a, b)
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
            role_badge = ui.span(who_text, class_="badge", style="background:#ff9500;color:#ffffff;font-weight:600;font-size:0.75rem;padding:6px 12px;border-radius:980px;")
        else:
            who_text = f"Regional Manager Oversight · {active_name()} (All Stores Aggregated)"
            role_badge = ui.span(who_text, class_="badge", style="background:#0071e3;color:#ffffff;font-weight:600;font-size:0.75rem;padding:6px 12px;border-radius:980px;")

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

    # ==================== 1. OOS & SUPPLY CHAIN INSIGHTS ====================

    @render_widget
    def chart1_oos_hazard():
        """Chart 1: Stock Level & OOS Hazard Heatmap (Field Sales Rep Radar)."""
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
        """Chart 2: Estimated Lost Sales Trend & Cumulative Loss Area Chart."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 2: Estimated Lost Sales Trend")
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
        """Chart 3: OOS Duration Distribution (Boxplot)."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 3: OOS Duration Distribution")
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

    # ==================== 2. SHELF EXECUTION & COMPLIANCE ====================

    @render_widget
    def chart4_compliance_gauge():
        """Chart 4: Planogram & MSL Compliance Gauge / Progress Bar Matrix."""
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
        """Chart 5: Actual Shelf Share vs. Target Bubble Plot."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 5: Actual Shelf Share vs Target")
        x, i = AU(), SI()
        req(len(x))

        shelf_store = x.groupby("store_id")["shelf_share_pct"].mean().reset_index()
        sales_store = i.groupby("store_id")["net_sales"].sum().reset_index()

        store_attrs = stores[["store_id", "store_name", "channel", "region", "segment_tier"]].drop_duplicates()
        df_bubble = store_attrs.merge(shelf_store, on="store_id").merge(sales_store, on="store_id")

        tier_sizes = {"Tier 1": 26, "Tier 2": 16, "Tier 3": 10}
        df_bubble["bubble_size"] = df_bubble["segment_tier"].map(tier_sizes).fillna(12)

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
        """Chart 6: Multi-Dimensional Execution Scorecard (Radar Chart)."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 6: Execution Scorecard")
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

    # ==================== 3. TRADE TERMS & NEGOTIATIONS ====================

    @render_widget
    def chart7_discount_margin():
        """Chart 7: Effective Discount vs Margin Realization Scatter Plot with Guardrails."""
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
        """Chart 8: Trade-off Execution Commitment Compliance (Grouped Bar Chart)."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 8: Trade-off Commitment Compliance")
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

    # ==================== 4. COMMERCIAL STRATEGY & GEOSPATIAL ====================

    @render_widget
    def chart9_geo_map():
        """Chart 9: Regional Execution vs Revenue Performance Map (Choropleth Map)."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 9: Regional Execution Map")
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
        """Chart 10: Sell-In vs Sell-Out Inventory Velocity (Sankey Flow Diagram)."""
        if active_role() == "Field Rep":
            return locked_chart("Chart 10: Inventory Velocity Flow")
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


app = App(app_ui, server)
