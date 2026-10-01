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
store_meta = ["store_id", "store_name", "channel", "region", "segment_tier", "rep_id"]
prod_meta = ["sku_id", "product_name", "category", "is_msl"]

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
  overflow: hidden;
}

.card:hover {
  box-shadow: 0 10px 30px -4px rgba(0, 0, 0, 0.06), 0 2px 8px rgba(0, 0, 0, 0.03) !important;
}

.card-header {
  background: #ffffff !important;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06) !important;
  font-size: 0.82rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.01em !important;
  color: var(--text-primary) !important;
  padding: 14px 20px !important;
}

/* KPI Metric Tiles */
.kpi-tile {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.07);
  border-radius: 18px;
  padding: 18px 20px;
  box-shadow: 0 2px 12px -2px rgba(0, 0, 0, 0.03), 0 1px 3px rgba(0, 0, 0, 0.02);
  position: relative;
  overflow: hidden;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.kpi-tile:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px -4px rgba(0, 0, 0, 0.06), 0 2px 6px rgba(0, 0, 0, 0.03);
}

.kpi-title {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-bottom: 6px;
}

.kpi-value {
  font-size: 2.1rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.1;
  margin-bottom: 8px;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Plus Jakarta Sans', sans-serif;
  color: var(--text-primary);
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
  font-size: 0.72rem;
  font-weight: 600;
  border-radius: 980px;
  padding: 3px 9px;
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
  font-size: 0.74rem;
  color: var(--text-tertiary);
  margin-top: 6px;
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
                ui.input_selectize("sku", "Product SKU (23 Available)", SKU_CHOICES, selected=list(prods.sku_id), multiple=True),
                ui.hr(),
                ui.accordion(
                    ui.accordion_panel(
                        "KPI Definitions & Targets",
                        ui.tags.small(
                            ui.tags.ul(
                                ui.tags.li(ui.tags.b("MSL: "), "% store-days with mandatory SKUs in stock (Target ≥ 90%)"),
                                ui.tags.li(ui.tags.b("OOS: "), "% store-SKU-days out of stock (Target ≤ 5%)"),
                                ui.tags.li(ui.tags.b("Sell-in Gap: "), "Sell-in minus sell-out units (Target within ±5%)"),
                                ui.tags.li(ui.tags.b("Shelf Share: "), "Average category shelf share held by Marvel (Target ≥ 30%)"),
                                ui.tags.li(ui.tags.b("Planogram: "), "% of audited SKUs aligned with planogram (Target ≥ 80%)"),
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
                ui.div(style="height:12px;"),
                ui.layout_columns(
                    ui.card(
                        ui.card_header("Sell-In vs Sell-Out Velocity (Weekly Units) — Shaded area indicates channel stock buildup"),
                        output_widget("flow"),
                        full_screen=True,
                        height="350px"
                    ),
                    ui.card(
                        ui.card_header("Out-of-Stock (OOS) Rate Trend by Channel (Dotted line = 5% Target)"),
                        output_widget("oos_trend"),
                        full_screen=True,
                        height="350px"
                    ),
                    col_widths=(6, 6)
                ),
                ui.div(style="height:12px;"),
                ui.layout_columns(
                    ui.card(
                        ui.card_header(
                            ui.div(
                                ui.span("Out-of-Stock Heatmap by SKU", class_="me-auto"),
                                ui.input_radio_buttons("hm_col", None, {"channel": "By Channel", "region": "By Region"}, inline=True),
                                class_="d-flex align-items-center justify-content-between"
                            )
                        ),
                        output_widget("heat"),
                        full_screen=True,
                        height="410px"
                    ),
                    ui.card(
                        ui.card_header("Top 10 High-Risk Out-of-Stock SKUs"),
                        output_widget("rank"),
                        full_screen=True,
                        height="410px"
                    ),
                    col_widths=(7, 5)
                ),
                ui.div(style="height:12px;"),
                ui.layout_columns(
                    ui.card(
                        ui.card_header("Shelf Share % by Category & Channel"),
                        output_widget("shelf"),
                        full_screen=True,
                        height="390px"
                    ),
                    ui.card(
                        ui.card_header("Store Execution Priority Matrix (Highest OOS & Compliance Issues First)"),
                        ui.output_data_frame("stores_tbl"),
                        full_screen=True,
                        height="390px"
                    ),
                    col_widths=(5, 7)
                ),
                class_="p-2"
            )
        )
    )


app_ui = ui.page_fillable(
    # Hidden reactive auth state: 'logged_out' or 'logged_in'
    ui.div(
        ui.input_text("auth_state", "", value="logged_out"),
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

    @reactive.effect
    @reactive.event(input.quick_mgr)
    def _quick_mgr():
        active_role.set("Regional Manager")
        active_rep.set(None)
        active_name.set(MANAGER_PROFILE["name"])
        active_title.set(MANAGER_PROFILE["title"])
        ui.update_text("auth_state", value="logged_in")

    @reactive.effect
    @reactive.event(input.quick_rep)
    def _quick_rep():
        prof = REP_PROFILES["Rep A"]
        active_role.set("Field Rep")
        active_rep.set("Rep A")
        active_name.set(prof["name"])
        active_title.set(f"{prof['title']} ({prof['region']})")
        ui.update_text("auth_state", value="logged_in")

    @reactive.effect
    @reactive.event(input.btn_logout)
    def _do_logout():
        ui.update_text("auth_state", value="logged_out")

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
        skus = input.sku() if input.sku() else list(prods.sku_id)

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

    def kpis(a, b):
        s, i, x = scope(so, "date", a, b), scope(si, "week", a, b), scope(au, "date", a, b)
        if s.empty:
            return dict(msl=np.nan, oos=np.nan, gap=0, gap_pct=np.nan, shelf=np.nan, plano=np.nan)
        m = s[s.is_msl]
        msl = 100 * (1 - m.groupby(["date", "store_id"]).is_out_of_stock.max().mean()) if not m.empty else np.nan
        oos = 100 * s.is_out_of_stock.mean()
        out = s.units_sold_daily.sum()
        gap = i.units_sold.sum() - out
        gap_pct = 100 * gap / out if out else np.nan
        shelf = x.shelf_share_pct.mean() if not x.empty else np.nan
        plano = 100 * x.planogram_compliance.mean() if not x.empty else np.nan
        return dict(msl=msl, oos=oos, gap=gap, gap_pct=gap_pct, shelf=shelf, plano=plano)

    def luxury_tile(title, cur, prv, ok, better_up, txt, foot, prefix="", suffix="", decimals=1, foot_units=None, foot_target=None):
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

        return ui.div(
            ui.div(title, class_="kpi-title"),
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
            class_="kpi-tile flex-fill"
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
                suffix="%", decimals=1
            ),
            luxury_tile(
                "OOS Rate", c["oos"], p["oos"],
                c["oos"] <= 5 if not pd.isna(c["oos"]) else False, False,
                f"{c['oos']:.1f}%" if not pd.isna(c["oos"]) else "–", "Target ≤ 5%",
                suffix="%", decimals=1
            ),
            luxury_tile(
                "Sell-In vs Sell-Out Gap", c["gap_pct"], p["gap_pct"],
                abs(c["gap_pct"]) <= 5 if not pd.isna(c["gap_pct"]) else False, False,
                f"{c['gap_pct']:+.1f}%" if not pd.isna(c["gap_pct"]) else "–",
                f"{c['gap']:+,.0f} units · target within ±5%",
                prefix="", suffix="%", decimals=1,
                foot_units=c["gap"] if not pd.isna(c["gap"]) else None,
                foot_target=" units · target within ±5%"
            ),
            luxury_tile(
                "Shelf Share", c["shelf"], p["shelf"],
                c["shelf"] >= 30 if not pd.isna(c["shelf"]) else False, True,
                f"{c['shelf']:.1f}%" if not pd.isna(c["shelf"]) else "–", "Target ≥ 30%",
                suffix="%", decimals=1
            ),
            luxury_tile(
                "Planogram Compliance", c["plano"], p["plano"],
                c["plano"] >= 80 if not pd.isna(c["plano"]) else False, True,
                f"{c['plano']:.1f}%" if not pd.isna(c["plano"]) else "–", "Target ≥ 80%",
                suffix="%", decimals=1
            ),
            class_="d-flex flex-wrap gap-3",
            style="margin-bottom:6px;"
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

    def tidy(fig, h=300):
        fig.update_layout(
            template="plotly_white",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=h,
            margin=dict(l=14, r=14, t=14, b=14),
            legend=dict(
                orientation="h",
                y=-0.22,
                title=None,
                font=dict(size=11, color="#6E6E73", family="'Plus Jakarta Sans', -apple-system, sans-serif")
            ),
            font=dict(family="'Plus Jakarta Sans', -apple-system, sans-serif", size=12, color="#6E6E73"),
            xaxis=dict(
                gridcolor="rgba(0,0,0,0.04)",
                zerolinecolor="rgba(0,0,0,0.08)",
                tickfont=dict(color="#6E6E73", size=11)
            ),
            yaxis=dict(
                gridcolor="rgba(0,0,0,0.04)",
                zerolinecolor="rgba(0,0,0,0.08)",
                tickfont=dict(color="#6E6E73", size=11)
            ),
            hoverlabel=dict(
                bgcolor="#FFFFFF",
                font_size=12,
                font_family="'Plus Jakarta Sans', -apple-system, sans-serif",
                font_color="#1D1D1F",
                bordercolor="rgba(0,0,0,0.12)"
            )
        )
        return fig

    @render_widget
    def flow():
        s, i = SO(), SI()
        req(len(s))
        a = s.groupby("week").units_sold_daily.sum()
        b = i.groupby("week").units_sold.sum().reindex(a.index, fill_value=0)
        f = go.Figure([
            go.Scatter(
                x=b.index, y=b.values, name="Sell-In (Replenishment Orders)",
                line=dict(color="#0071E3", width=3)
            ),
            go.Scatter(
                x=a.index, y=a.values, name="Sell-Out (Consumer Offtake)",
                line=dict(color="#FF3B30", width=3),
                fill="tonexty", fillcolor="rgba(0, 113, 227, 0.08)"
            )
        ])
        return tidy(f, 300)

    @render_widget
    def oos_trend():
        s = SO()
        req(len(s))
        d = s.groupby(["week", "channel"]).is_out_of_stock.mean().mul(100).reset_index()
        f = px.line(
            d, x="week", y="is_out_of_stock", color="channel",
            color_discrete_map=COL, labels={"is_out_of_stock": "OOS %", "week": ""}
        )
        f.add_hline(
            y=5, line_dash="dot", line_color="#86868B",
            annotation_text="5% OOS Target Threshold",
            annotation_position="bottom right",
            annotation_font=dict(size=10, color="#86868B")
        )
        return tidy(f, 300)

    @render_widget
    def heat():
        s = SO()
        req(len(s))
        p = s.groupby(["product_name", input.hm_col()]).is_out_of_stock.mean().mul(100).unstack().fillna(0)
        f = px.imshow(
            p,
            text_auto=".1f",
            aspect="auto",
            color_continuous_scale=[[0, "#F5F5F7"], [0.25, "#E2E8F0"], [0.55, "#FDA4AF"], [0.85, "#F43F5E"], [1.0, "#BE123C"]],
            labels=dict(color="OOS %", x="", y="")
        )
        return tidy(f, 350)

    @render_widget
    def rank():
        s = SO()
        req(len(s))
        d = (s.groupby(["product_name", "category"]).is_out_of_stock.mean().mul(100)
             .reset_index(name="oos").nlargest(10, "oos").sort_values("oos"))
        f = px.bar(
            d, x="oos", y="product_name", color="category", orientation="h", text_auto=".1f",
            color_discrete_map={
                "Surface Cleaners": "#0071E3",
                "Dishwashing": "#FF9500",
                "Laundry": "#AF52DE"
            },
            labels={"oos": "OOS %", "product_name": ""}
        )
        return tidy(f, 350)

    @render_widget
    def shelf():
        x = AU()
        req(len(x))
        d = x.groupby(["category", "channel"]).shelf_share_pct.mean().reset_index()
        f = px.bar(
            d, x="category", y="shelf_share_pct", color="channel", barmode="group", text_auto=".1f",
            color_discrete_map=COL, labels={"shelf_share_pct": "Avg Shelf Share %", "category": ""}
        )
        return tidy(f, 330)

    @render.data_frame
    def stores_tbl():
        s, i, x = SO(), SI(), AU()
        req(len(s))
        g = s.groupby("store_id").agg(
            Store=("store_name", "first"),
            Channel=("channel", "first"),
            Region=("region", "first"),
            Tier=("segment_tier", "first"),
            oos=("is_out_of_stock", "mean"),
            out=("units_sold_daily", "sum")
        )
        si_sum = i.groupby("store_id").units_sold.sum().reindex(g.index, fill_value=0)
        plano_mean = x.groupby("store_id").planogram_compliance.mean().reindex(g.index, fill_value=0)

        g["Sell-in gap (units)"] = (si_sum - g.out).round().astype("Int64")
        g["Planogram %"] = (plano_mean * 100).round(1)
        g["OOS %"] = (g.oos * 100).round(1)
        g = g.sort_values("OOS %", ascending=False).head(15).drop(columns=["oos", "out"]).reset_index()
        return render.DataGrid(g.rename(columns={"store_id": "Store ID"}), height="310px")


app = App(app_ui, server)
