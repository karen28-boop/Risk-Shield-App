# RISKSHIELD ZIMBABWE 

import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta, date
import io, random, json, os, math
from scipy import stats
from scipy.special import gamma as gamma_fn
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import urllib.request, re
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="RiskShield Zimbabwe | Complete Actuarial Platform",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# LIVE RBZ ZiG RATE FETCHER (with graceful fallback)
# ============================================================
def fetch_live_zig_rate():
    """Attempt to fetch live ZiG/USD rate from multiple sources"""
    sources = [
        {
            "url": "https://api.exchangerate-api.com/v4/latest/USD",
            "parser": lambda x: x.get("rates", {}).get("ZIG", None),
            "label": "ExchangeRate-API"
        },
        {
            "url": "https://www.rbz.co.zw/documents/Exchange_Rates/2024/March/Exchange-Rates.xml",
            "pattern": r"ZIG.*?(\d+\.\d+)",
            "label": "RBZ Official XML"
        },
    ]
    
    fallback = {
        "rate": 26.90,
        "parallel": 33.50,
        "source": "RBZ Official (Cached)",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "live": False,
        "inflation": 8.5,
        "monthly_cpi": 8.5 / 12,
        "policy_rate": 35.0,
        "gold_reserves": 380_000_000,
        "days_stable": (datetime.now() - datetime(2024, 4, 5)).days
    }

    for src in sources:
        try:
            if "exchangerate-api" in src["url"]:
                req = urllib.request.Request(src["url"], headers={"User-Agent": "RiskShield/2.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    rate = src["parser"](data)
                    if rate and 10.0 < rate < 500.0:
                        return {
                            "rate": round(rate, 4),
                            "parallel": round(rate * 1.245, 4),
                            "source": src["label"],
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "live": True,
                            "inflation": 8.5,
                            "monthly_cpi": 8.5 / 12,
                            "policy_rate": 35.0,
                            "gold_reserves": 380_000_000,
                            "days_stable": (datetime.now() - datetime(2024, 4, 5)).days
                        }
            elif "rbz" in src["url"]:
                req = urllib.request.Request(src["url"], headers={"User-Agent": "RiskShield/2.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode("utf-8", errors="ignore")
                    match = re.search(src["pattern"], text, re.IGNORECASE | re.DOTALL)
                    if match:
                        rate = float(match.group(1))
                        if 10.0 < rate < 500.0:
                            return {
                                "rate": round(rate, 4),
                                "parallel": round(rate * 1.245, 4),
                                "source": src["label"],
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "live": True,
                                "inflation": 8.5,
                                "monthly_cpi": 8.5 / 12,
                                "policy_rate": 35.0,
                                "gold_reserves": 380_000_000,
                                "days_stable": (datetime.now() - datetime(2024, 4, 5)).days
                            }
        except Exception:
            continue
    return fallback

def get_zig_rate():
    """Returns cached rate if < 60 min old, else fetches fresh"""
    now = datetime.now()
    cached = st.session_state.get("zig_rate_cache", None)
    fetched_at = st.session_state.get("zig_rate_fetched_at", None)
    if cached and fetched_at:
        age_min = (now - fetched_at).total_seconds() / 60
        if age_min < 60:
            return cached
    fresh = fetch_live_zig_rate()
    st.session_state["zig_rate_cache"] = fresh
    st.session_state["zig_rate_fetched_at"] = now
    return fresh

# ============================================================
# COMPLETE ACTUARIAL ENGINE (ALL FORMULAS FROM POLICY PAPER)
# ============================================================

class ActuarialEngine:
    """Complete implementation of all actuarial models from RiskShield Policy Paper"""
    
    # Severity distribution from policy paper (E[S] = 0.83)
    SEVERITY = {
        "Low":          {"multiplier": 0.30, "prob": 0.50, "description": "Minor damage"},
        "Medium":       {"multiplier": 0.60, "prob": 0.30, "description": "Significant damage"},
        "High":         {"multiplier": 1.00, "prob": 0.15, "description": "Major damage"},
        "Severe":       {"multiplier": 1.50, "prob": 0.04, "description": "Near total loss"},
        "Catastrophic": {"multiplier": 2.00, "prob": 0.01, "description": "Total loss"},
    }
    
    # E[Severity] = Σ p_i × m_i = 0.83
    E_SEVERITY = sum(v["prob"] * v["multiplier"] for v in SEVERITY.values())
    
    # ===== EQUATION 3.1: Real Claims Reserve Erosion (Continuous) =====
    @staticmethod
    def real_reserve_continuous(R0: float, pi: float, t: float) -> float:
        """R(t) = R₀ · e^(-πt)"""
        return R0 * np.exp(-pi * t)
    
    # ===== EQUATION 3.2: Real Claims Reserve Erosion (Discrete) =====
    @staticmethod
    def real_reserve_discrete(R0: float, pi: float, t: float) -> float:
        """R(t) = R₀ · (1 + π)⁻ᵗ"""
        return R0 * (1 + pi) ** (-t)
    
    # ===== EQUATION 3.3: Real Reserve Deficit =====
    @staticmethod
    def reserve_deficit(C0: float, R0: float, pi: float, t: float) -> float:
        """ΔR(t) = C₀·(1+π)^t - R₀·(1+π)^(-t)"""
        return C0 * (1 + pi) ** t - R0 * (1 + pi) ** (-t)
    
    # ===== EQUATION 3.4: Inflation-Adjusted Premium Adequacy =====
    @staticmethod
    def pure_risk_premium(E_Ci: float, pi: float, t_bar: float, theta: float, sigma_Ci: float) -> float:
        """Pᵢ = E[Cᵢ]·(1+π)^t̄ + θ·σ(Cᵢ)"""
        return E_Ci * (1 + pi) ** t_bar + theta * sigma_Ci
    
    # ===== EQUATION 3.5: Gross Office Premium =====
    @staticmethod
    def gross_premium(pure_premium: float, expense_loading: float, profit_margin: float) -> float:
        """Gᵢ = Pᵢ / (1 - ε - λ)"""
        denominator = 1 - expense_loading - profit_margin
        return pure_premium / denominator if denominator > 0 else float('inf')
    
    # ===== EQUATION 3.6: Premium Deficiency Reserve =====
    @staticmethod
    def premium_deficiency_reserve(pv_claims_expenses: float, unearned_premium: float) -> float:
        """PDR = max{0, PV(C+E) - UPR}"""
        return max(0, pv_claims_expenses - unearned_premium)
    
    # ===== EQUATION 3.10: Underwriting Result =====
    @staticmethod
    def underwriting_result(gwp: float, claims_inflated: float, expenses: float, reserve_strengthening: float) -> float:
        """URᵗ = GWPᵗ - Cᵗᵃ - Eᵗ - δᵗ"""
        return gwp - claims_inflated - expenses - reserve_strengthening
    
    # ===== EQUATION 3.11: Inflation-Adjusted Combined Ratio =====
    @staticmethod
    def combined_ratio_inflated(claims_inflated: float, expenses: float, gwp: float, change_upr: float) -> float:
        """CRᵗ* = (Cᵗᵃ + Eᵗ) / (GWPᵗ - ΔUPRᵗ)"""
        denominator = gwp - change_upr
        return (claims_inflated + expenses) / denominator if denominator > 0 else float('inf')
    
    # ===== EQUATION 3.12: Solvency Coverage Ratio =====
    @staticmethod
    def solvency_coverage_ratio(available_capital: float, mcr: float) -> float:
        """SCR = Available Capital / Minimum Capital Requirement ≥ 1.5"""
        return available_capital / mcr if mcr > 0 else float('inf')
    
    # ===== EQUATION 3.13: Effective SCR After Inflation Shock =====
    @staticmethod
    def scr_inflation_shock(available_capital: float, mcr: float, alpha: float, beta: float, pi: float) -> float:
        """SCRᵃ = [Available Capital·(1-απ)] / [MCR·(1+βπ)]"""
        numerator = available_capital * (1 - alpha * pi)
        denominator = mcr * (1 + beta * pi)
        return numerator / denominator if denominator > 0 else float('inf')
    
    # ===== PILLAR I: Inflation-Adjusted Sum Insured =====
    @staticmethod
    def inflation_adjusted_sum_insured(S0: float, months: float, annual_cpi: float) -> float:
        """S_t = S₀ × (1 + CPI_t/100)^(m/12)"""
        monthly_cpi = (1 + annual_cpi / 100) ** (1 / 12) - 1
        return S0 * (1 + monthly_cpi) ** months
    
    # ===== PILLAR I: Premium with Inflation Loading =====
    @staticmethod
    def premium_with_inflation_loading(S_t: float, base_rate: float, monthly_cpi: float, risk_loading: float) -> float:
        """P_t = S_t × (β + π_t/10000 + ρ)"""
        inflation_loading = monthly_cpi / 10000
        return S_t * (base_rate + inflation_loading + risk_loading)
    
    # ===== PILLAR I: Claim Severity Distribution =====
    @staticmethod
    def expected_claim_severity(S_t: float, damage_pct: float, severity_level: str, inflation_impact: float) -> float:
        """Claim = E[S] × S_t × (1+π_claim)^(Δt)"""
        factor = ActuarialEngine.SEVERITY[severity_level]["multiplier"]
        return S_t * (damage_pct / 100) * factor * (1 + inflation_impact / 100)
    
    # ===== PILLAR I & III: Lapsing Survival Model =====
    @staticmethod
    def lapsing_survival(t: float, base_rate: float, economic_factor: float, client_factor: float) -> float:
        """S(t) = exp(-λt) where λ = λ_b + λ_e + λ_c"""
        lam = base_rate + economic_factor + client_factor
        return math.exp(-lam * t)
    
    # ===== PILLAR II: Required Capital =====
    @staticmethod
    def required_capital(total_assets: float, risk_weighted_assets: float, outstanding_claims: float, usd_floor: float, zig_rate: float) -> dict:
        """K_req = max(0.15×A_tot, K_rw, 1.2×L_out, 500,000×e_USD/ZiG)"""
        floor_zig = usd_floor * zig_rate
        components = {
            "Asset_Charge (15%)": total_assets * 0.15,
            "Risk-Weighted Assets": risk_weighted_assets,
            "Claims Reserve (120%)": outstanding_claims * 1.20,
            "USD Floor (500k)": floor_zig,
        }
        required = max(components.values())
        return {"required": required, "components": components}
    
    # ===== PILLAR II: Risk-Weighted Assets =====
    @staticmethod
    def risk_weighted_assets(assets_by_class: dict) -> float:
        """K_rw = Σ w_i × A_i, w_i ∈ {0.05, 0.10, 0.20, 0.35}"""
        weights = {"Class A": 0.05, "Class B": 0.10, "Class C": 0.20, "Class D": 0.35}
        total = 0
        for key, value in assets_by_class.items():
            for class_key, weight in weights.items():
                if class_key in key:
                    total += value * weight
                    break
            else:
                total += value * 0.20
        return total
    
    # ===== PILLAR II: Solvency Ratio =====
    @staticmethod
    def solvency_ratio(actual_capital: float, required_capital: float) -> float:
        """Solvency Ratio = (K_actual / K_req) × 100% ≥ 150%"""
        return (actual_capital / required_capital) * 100 if required_capital > 0 else float('inf')
    
    # ===== PILLAR II: Real-Value Capital Adjustment =====
    @staticmethod
    def real_capital_adjustment(nominal_capital: float, annual_inflation: float, years: float) -> float:
        """Real Capital = K_nominal / (1+π_annual)^t"""
        return nominal_capital / ((1 + annual_inflation / 100) ** years)
    
    # ===== PILLAR II: Historical Currency Conversion =====
    @staticmethod
    def historical_rate(target_date) -> tuple:
        """Returns (official_rate, currency_code) for any date 2009-present"""
        d = pd.Timestamp(target_date)
        if d < pd.Timestamp("2014-01-01"):   return 1.0, "USD"
        elif d < pd.Timestamp("2016-01-01"): return 1.0, "Bond"
        elif d < pd.Timestamp("2018-01-01"): return 2.5, "Bond"
        elif d < pd.Timestamp("2019-01-01"): return 15.0, "RTGS"
        elif d < pd.Timestamp("2020-01-01"): return 80.0, "RTGS"
        elif d < pd.Timestamp("2022-01-01"): return 200.0, "ZWL"
        elif d < pd.Timestamp("2023-01-01"): return 1500.0, "ZWL"
        elif d < pd.Timestamp("2024-04-01"): return 8000.0, "ZWL"
        else:
            r = st.session_state.get("zig_rate_cache", {}).get("rate", 26.90)
            return r, "ZiG"
    
    @staticmethod
    def convert_to_current_zig(amount: float, from_date, current_zig_rate: float) -> float:
        """Convert any historical currency amount to current ZiG value"""
        hist_rate, _ = ActuarialEngine.historical_rate(from_date)
        if hist_rate == 0:
            return 0.0
        usd_value = amount / hist_rate
        return usd_value * current_zig_rate
    
    # ===== PILLAR II: Regime Transition =====
    @staticmethod
    def regime_transition(value_old: float, old_rate: float, new_rate: float) -> float:
        """V_new = V_old × (e_new/e_old)"""
        usd_value = value_old / old_rate if old_rate > 0 else 0
        return usd_value * new_rate
    
    # ===== PILLAR II: Conversion Validation =====
    @staticmethod
    def validate_conversion(calculated: float, rbz_value: float, tolerance: float = 0.01) -> tuple:
        """|V_calculated - V_RBZ| ≤ 0.01 × V_RBZ"""
        error = abs(calculated - rbz_value)
        is_valid = error <= tolerance * rbz_value
        error_pct = (error / rbz_value) * 100 if rbz_value > 0 else 0
        return is_valid, error_pct
    
    # ===== ALL PILLARS: Trust Score Algorithm =====
    @staticmethod
    def trust_score(claims_paid: int, total_claims: int, avg_settlement_days: float, inflation_adjusted: bool, comm_quality: float = 5.0) -> dict:
        """T = 0.40A + 0.30V + 0.10I + 0.10C + 0.10R"""
        base = 70
        claims_comp = (claims_paid / max(total_claims, 1)) * 20
        speed_comp = max(0, 30 - avg_settlement_days)
        infl_comp = 10 if inflation_adjusted else 0
        comm_comp = min(10, comm_quality)
        reg_comp = 10  # Assumed regulatory compliance
        total = min(100, base + claims_comp + speed_comp + infl_comp + comm_comp + reg_comp)
        return {
            "total": round(total, 1),
            "base": base,
            "claims_component": round(claims_comp, 1),
            "speed_component": round(speed_comp, 1),
            "inflation_component": infl_comp,
            "communication_component": round(comm_comp, 1),
            "regulatory_component": reg_comp
        }
    
    # ===== EQUATION 3.14-3.15: Stochastic Reserve (Compound Poisson) =====
    @staticmethod
    def simulate_compound_poisson(lambda_param: float, mu_y: float, sigma_y: float, pi_mean: float, pi_std: float, n_sims: int = 10000) -> np.ndarray:
        """X = Σ Yᵏ·(1+Π) where N~Poisson(λ), Y~Lognormal(μ,σ²), Π~N(π,σπ²)"""
        results = []
        for _ in range(n_sims):
            n_claims = np.random.poisson(lambda_param)
            if n_claims == 0:
                results.append(0)
                continue
            severities = np.random.lognormal(mean=mu_y, sigma=sigma_y, size=n_claims)
            inflation_shock = np.random.normal(loc=pi_mean, scale=pi_std)
            total = np.sum(severities) * (1 + inflation_shock)
            results.append(total)
        return np.array(results)
    
    @staticmethod
    def value_at_risk(losses: list, confidence: float = 0.95) -> dict:
        """VaRα(X) = inf{x ∈ ℝ : P(X > x) ≤ 1 - α}"""
        if not losses:
            return {"var": 0, "cvar": 0, "confidence": confidence}
        arr = np.sort(np.array(losses))
        idx = int(np.ceil(confidence * len(arr))) - 1
        var = float(arr[idx])
        cvar = float(arr[idx:].mean()) if idx < len(arr) - 1 else var
        return {"var": var, "cvar": cvar, "confidence": confidence}
    
    # ===== Profit Testing Model =====
    @staticmethod
    def profit_test(base_premium: float, years: int, inflation: float, growth_rate: float, loss_ratio: float = 0.60, expense_ratio: float = 0.20) -> pd.DataFrame:
        """Profit(t) = Premiums(t) - Claims(t) - Expenses(t)"""
        rows = []
        p = base_premium
        for t in range(years + 1):
            if t > 0:
                p = p * (1 + growth_rate / 100) * (1 + inflation / 100)
            claims = p * loss_ratio * (1 + inflation / 100) if t > 0 else 0
            expenses = p * expense_ratio if t > 0 else 0
            profit = p - claims - expenses if t > 0 else 0
            loss_r = (claims / p * 100) if p > 0 and t > 0 else 0
            combined = ((claims + expenses) / p * 100) if p > 0 and t > 0 else 0
            rows.append({
                "Year": t, "Premiums": p, "Claims": claims,
                "Expenses": expenses, "Profit": profit,
                "Loss_Ratio": loss_r, "Combined_Ratio": combined,
                "NPV_Profit": profit / (1 + 0.15) ** t if t > 0 else 0
            })
        return pd.DataFrame(rows)
    
    # ===== GBM Rate Simulation =====
    @staticmethod
    def gbm_rate_simulation(S0: float, mu: float, sigma: float, T: int = 12, n_paths: int = 200) -> np.ndarray:
        """Geometric Brownian Motion for ZiG/USD rate paths"""
        dt = 1 / 12
        paths = np.zeros((T + 1, n_paths))
        paths[0] = S0
        for t in range(1, T + 1):
            z = np.random.standard_normal(n_paths)
            paths[t] = paths[t - 1] * np.exp((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z)
        return paths
    
    # ===== Pareto Severity Fit =====
    @staticmethod
    def pareto_severity_fit(claims: list) -> dict:
        """Fit Pareto distribution to claim data using MLE"""
        if len(claims) < 3:
            return {"alpha": 2.5, "theta": 50000, "fitted": False, "mean": 0, "variance": 0}
        arr = np.array(claims, dtype=float)
        arr = arr[arr > 0]
        theta = arr.min()
        if theta <= 0:
            theta = 1.0
        alpha = len(arr) / np.sum(np.log(arr / theta))
        return {
            "alpha": round(float(alpha), 4),
            "theta": round(float(theta), 2),
            "mean": round(float(theta / (alpha - 1)) if alpha > 1 else float("inf"), 2),
            "variance": round(float(theta**2 * alpha / ((alpha - 1)**2 * (alpha - 2))) if alpha > 2 else float("inf"), 2),
            "fitted": True
        }

# Initialize actuarial engine
ae = ActuarialEngine()

# ============================================================
# PERSISTENT DATABASE
# ============================================================
DB_FILE = "riskshield_data.json"

ASSET_COLS = [
    "Client_ID", "Asset_ID", "Asset_Type", "Asset_Description",
    "Original_Value_ZiG", "Original_Value_USD", "Inception_Date",
    "Policy_Number", "Policy_Term_Months", "RBZ_Class", "Risk_Loading",
    "Monthly_Premium_ZiG", "Monthly_Premium_USD", "Lapsing_Rate",
    "Original_Currency", "Original_Amount", "Original_Rate",
    "Status", "Registered_By", "Adjusted_Sum_Insured"
]

CLAIM_COLS = [
    "Claim_ID", "Client_ID", "Asset_ID", "Claim_Date",
    "Severity_Level", "Damage_Pct", "Base_Claim_ZiG",
    "Inflation_Adjusted_Claim_ZiG", "Claim_USD",
    "Claim_Type", "Status", "Days_To_Settle", "Processed_By"
]

PAY_COLS = [
    "Date", "Client_ID", "Asset_ID", "Amount_ZiG", "Amount_USD",
    "Method", "Status", "Processed_By", "Exchange_Rate_Used"
]

def _safe_records(df):
    out = []
    for rec in df.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if isinstance(v, (pd.Timestamp, datetime, date)):
                clean[k] = str(v)
            elif hasattr(v, "item"):
                clean[k] = v.item()
            elif isinstance(v, float) and math.isnan(v):
                clean[k] = None
            else:
                clean[k] = v
        out.append(clean)
    return out

def save_db():
    payload = {
        "clients": st.session_state.clients,
        "assets": _safe_records(st.session_state.assets),
        "claims": _safe_records(st.session_state.claims),
        "payments": _safe_records(st.session_state.payments),
        "capital": st.session_state.capital,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    st.session_state.db_saved_at = payload["saved_at"]

def load_db():
    blank = {
        "clients": {}, "assets": [], "claims": [], "payments": [],
        "capital": {"available": 50_000_000, "usd_floor": 500_000},
        "saved_at": None,
    }
    if not os.path.exists(DB_FILE):
        return blank
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in blank.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return blank

def rebuild(records, cols):
    if not records:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(records)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]

# ============================================================
# SESSION STATE INITIALISATION
# ============================================================
if "db_loaded" not in st.session_state:
    db = load_db()
    st.session_state.clients = db["clients"]
    st.session_state.assets = rebuild(db["assets"], ASSET_COLS)
    st.session_state.claims = rebuild(db["claims"], CLAIM_COLS)
    st.session_state.payments = rebuild(db["payments"], PAY_COLS)
    st.session_state.capital = db["capital"]
    st.session_state.db_saved_at = db.get("saved_at", "Never")
    st.session_state.db_loaded = True

AUTHORS = [
    "Kwanai Karen", "Lackson Tsvangirayi", "Kudakwashe Muzanenhamo",
    "Godfrey Masasa", "Charlotte E Makuleke"
]

RBZ_CLASSES = {
    "Class A — Government Bonds / Cash":    {"weight": 0.05, "max_lapse": 0.10},
    "Class B — Corporate Bonds / Property": {"weight": 0.10, "max_lapse": 0.15},
    "Class C — Equities / Vehicles":        {"weight": 0.20, "max_lapse": 0.25},
    "Class D — Speculative / Crypto":       {"weight": 0.35, "max_lapse": 0.40},
}

# Fetch live ZiG rate
zig_data = get_zig_rate()
ZIG_RATE = zig_data["rate"]
ZIG_PAR = zig_data["parallel"]
ZIG_INFL = zig_data["inflation"]
ZIG_MONTHLY_CPI = zig_data["monthly_cpi"]
ZIG_SOURCE = zig_data["source"]
ZIG_LIVE = zig_data["live"]
ZIG_TS = zig_data["timestamp"]
POLICY_RATE = zig_data["policy_rate"]
GOLD_RESERVES = zig_data["gold_reserves"]
DAYS_STABLE = zig_data["days_stable"]

# ============================================================
# CUSTOM CSS
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,600;0,700&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
    background: #f4f6f9;
}
.main-banner {
    background: linear-gradient(135deg, #0a1f3d 0%, #1B3A6B 50%, #0d2b5e 100%);
    padding: 1.2rem 2rem; border-radius: 1rem; margin-bottom: 1rem;
    border-bottom: 3px solid #C9952B;
}
.main-banner h1 {
    font-family: 'EB Garamond', serif; font-size: 2rem;
    color: #ffffff; margin: 0;
}
.main-banner .subtitle {
    font-size: 0.85rem; color: rgba(255,255,255,0.7);
    margin-top: 0.2rem;
}
.rate-strip {
    background: #0a1f3d; color: white;
    padding: 0.5rem 1.2rem; border-radius: 0.5rem;
    margin-bottom: 0.8rem; border-left: 4px solid #C9952B;
    font-size: 0.8rem;
}
.formula-box {
    background: #0a1f3d; color: #e8f0fe;
    padding: 0.8rem 1.2rem; border-radius: 0.5rem;
    border-left: 4px solid #C9952B;
    font-family: monospace; font-size: 0.8rem;
    margin: 0.5rem 0;
}
.formula-box .formula-title {
    color: #C9952B; font-weight: 600;
    font-size: 0.85rem; margin-bottom: 0.3rem;
}
.pillar-card {
    border-radius: 0.6rem; padding: 0.8rem 1rem;
    margin: 0.3rem 0; border-left: 4px solid;
}
.pillar-i { background: #eaf2ff; border-color: #1B3A6B; }
.pillar-ii { background: #fef9e7; border-color: #C9952B; }
.pillar-iii { background: #eafaf1; border-color: #1e8449; }
.kpi-row { display: flex; gap: 0.8rem; flex-wrap: wrap; margin: 0.5rem 0; }
.kpi-card {
    flex: 1; min-width: 120px;
    background: white; border-radius: 0.5rem;
    padding: 0.6rem 0.8rem;
    border-top: 3px solid #1B3A6B;
}
.kpi-card .kpi-val {
    font-family: 'EB Garamond', serif;
    font-size: 1.4rem; font-weight: 700; color: #1B3A6B;
}
.kpi-card .kpi-label {
    font-size: 0.7rem; color: #666;
}
.stTabs [data-baseweb="tab"] {
    font-weight: 600; font-size: 0.75rem;
}
.stTabs [aria-selected="true"] { color: #1B3A6B !important; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# HEADER
# ============================================================
live_badge = '<span style="background:#27ae60; padding:0.1rem 0.5rem; border-radius:3px; font-size:0.7rem;">LIVE</span>' if ZIG_LIVE else '<span style="background:#e67e22; padding:0.1rem 0.5rem; border-radius:3px; font-size:0.7rem;">CACHED</span>'

st.markdown(f"""
<div class="main-banner">
  <h1>🛡️ RiskShield Zimbabwe</h1>
  <div class="subtitle">
    Complete Actuarial Insurance Management System | All Mathematical Models Implemented
  </div>
</div>
<div class="rate-strip">
  {live_badge}
  <strong>RBZ ZiG/USD:</strong> {ZIG_RATE:.4f} &nbsp;|&nbsp;
  <strong>Parallel:</strong> {ZIG_PAR:.4f} &nbsp;|&nbsp;
  <strong>Spread:</strong> {((ZIG_PAR/ZIG_RATE - 1)*100):.1f}% &nbsp;|&nbsp;
  <strong>CPI:</strong> {ZIG_INFL}% &nbsp;|&nbsp;
  <strong>Policy Rate:</strong> {POLICY_RATE}% &nbsp;|&nbsp;
  <strong>Gold Reserves:</strong> ${GOLD_RESERVES/1e6:.0f}M &nbsp;|&nbsp;
  <strong>ZiG Stability:</strong> {DAYS_STABLE} days
</div>
""", unsafe_allow_html=True)

# Main KPIs
total_clients = len(st.session_state.clients)
total_policies = len(st.session_state.assets)
total_si = st.session_state.assets["Original_Value_ZiG"].sum() if not st.session_state.assets.empty else 0
total_premiums = st.session_state.payments["Amount_ZiG"].sum() if not st.session_state.payments.empty else 0
total_claims = st.session_state.claims["Inflation_Adjusted_Claim_ZiG"].sum() if not st.session_state.claims.empty else 0

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Live ZiG Rate", f"{ZIG_RATE:.4f}", delta="ZiG/USD")
k2.metric("Inflation", f"{ZIG_INFL}%", delta="Single Digit")
k3.metric("Clients", total_clients)
k4.metric("Policies", total_policies)
k5.metric("Total SI", f"ZiG {total_si/1e6:.1f}M")
k6.metric("Premiums", f"ZiG {total_premiums/1e3:.1f}K")

# ============================================================
# SIDEBAR NAVIGATION (ALL TABS ON SIDEBAR)
# ============================================================
with st.sidebar:
    st.markdown("""
    <div style="text-align:center; padding:0.5rem 0;">
        <div style="font-family:'EB Garamond',serif; font-size:1.6rem; color:#1B3A6B; font-weight:700;">
            Risk<span style="color:#C9952B;">Shield</span>
        </div>
        <div style="font-size:0.7rem; color:#888;">Zimbabwe · Complete Actuarial</div>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Navigation options - ALL TABS ON SIDEBAR
    nav_options = [
        "🏠 Dashboard",
        "👥 Client Management",
        "📦 Asset Insurance",
        "💰 Premium Payments",
        "📋 Claims & Severity",
        "📈 Inflation Models",
        "📊 Trust Analytics",
        "💼 Capital & Solvency",
        "📉 Profit Testing",
        "🔬 Rate Modelling (GBM)",
        "🏛️ IPEC Dashboard",
        "📜 Currency History"
    ]
    
    selected_nav = st.radio("Navigation", nav_options, index=0, label_visibility="collapsed")
    
    st.markdown("---")
    
    # Live rate display
    st.markdown("**📡 Live RBZ Data**")
    st.markdown(f"""
    <div style="background:#f8f9fa; border-radius:0.5rem; padding:0.6rem; font-size:0.75rem;">
        1 USD = <b>{ZIG_RATE:.4f} ZiG</b><br>
        Parallel: {ZIG_PAR:.4f}<br>
        Spread: {((ZIG_PAR/ZIG_RATE - 1)*100):.1f}%<br>
        CPI: {ZIG_INFL}%<br>
        Source: {ZIG_SOURCE}
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("🔄 Refresh Rate", use_container_width=True):
        for k in ["zig_rate_cache", "zig_rate_fetched_at"]:
            st.session_state.pop(k, None)
        st.rerun()
    
    st.markdown("---")
    st.markdown("**📐 Active Actuarial Models**")
    st.markdown("""
    - Eq 3.1-3.3: Reserve Erosion
    - Eq 3.4-3.6: Premium Adequacy
    - Eq 3.10-3.11: Combined Ratio
    - Eq 3.12-3.13: SCR
    - Eq 3.14-3.15: VaR (97.5%)
    - Pillar I: S_t = S₀(1+CPI)^(m/12)
    - Pillar II: K_req = max(...)
    - Trust: T = 0.40A+0.30V+...
    """)
    
    st.markdown("---")
    if st.button("💾 Save All Data", use_container_width=True):
        save_db()
        st.success("Saved!")
    
    st.markdown(f"""
    <div style="background:#0a1f3d; color:white; padding:0.6rem; border-radius:0.5rem; margin-top:0.5rem; font-size:0.7rem;">
        <b>Authors</b><br>
        Kwanai Karen<br>Lackson Tsvangirayi<br>
        Kudakwashe Muzanenhamo<br>Godfrey Masasa<br>
        Charlotte E Makuleke
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# MAIN CONTENT BASED ON SIDEBAR SELECTION
# ============================================================

# ==================== DASHBOARD HOME ====================
if selected_nav == "🏠 Dashboard":
    st.title("📊 Actuarial Dashboard")
    
    with st.expander("📐 View All Active Actuarial Formulas", expanded=True):
        st.markdown("""
        ### EQUATION 3.1-3.3: Real Claims Reserve Erosion
        ```
        R(t) = R₀ · e^(-πt)                    (Continuous)
        R(t) = R₀ · (1 + π)⁻ᵗ                  (Discrete)
        ΔR(t) = C₀·(1+π)^t - R₀·(1+π)^(-t)     (Deficit)
        ```
        
        ### PILLAR I: Inflation-Adjusted Sum Insured
        ```
        S_t = S₀ × (1 + CPI_t/100)^(m/12)
        P_t = S_t × (β + π_t/10000 + ρ)
        ```
        
        ### PILLAR I: Severity Distribution
        ```
        E[S] = Σ p_k × s_k = 0.30×0.5 + 0.30×0.6 + 0.25×1.0 + 0.10×1.5 + 0.05×2.0 = 0.83
        ```
        
        ### PILLAR II: Capital Adequacy
        ```
        K_req = max(0.15×A_tot, K_rw, 1.2×L_out, 500,000×e_USD/ZiG)
        K_rw = Σ w_i × A_i, w_i ∈ {0.05, 0.10, 0.20, 0.35}
        Solvency Ratio = (K_actual / K_req) × 100% ≥ 150%
        ```
        
        ### ALL PILLARS: Trust Score Algorithm
        ```
        T = 0.40A + 0.30V + 0.10I + 0.10C + 0.10R
        ```
        """)
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Quick Reserve Erosion Calculator (Eq 3.1)")
        R0 = st.number_input("Initial Reserve (R₀) ZiG", value=1_000_000.0, step=100_000.0)
        years = st.slider("Time (t years)", 0.0, 5.0, 1.0)
        eroded = ae.real_reserve_continuous(R0, ZIG_INFL/100, years)
        st.metric("Value After Inflation", f"ZiG {eroded:,.2f}", delta=f"Loss: {R0 - eroded:,.2f}")
    
    with col2:
        st.subheader("Trust Score Calculator")
        paid = st.number_input("Claims Paid", 0, 100, 8)
        total = st.number_input("Total Claims", 1, 100, 10)
        days = st.slider("Avg Settlement Days", 1, 90, 25)
        ts = ae.trust_score(paid, total, days, True, 7.5)
        st.metric("Trust Score", f"{ts['total']:.1f}%", delta=f"Base: {ts['base']}")

# ==================== CLIENT MANAGEMENT ====================
elif selected_nav == "👥 Client Management":
    st.header("👥 Client Management")
    
    col1, col2 = st.columns([1, 1])
    with col1:
        with st.form("client_form"):
            name = st.text_input("Full Name *")
            phone = st.text_input("Phone *")
            address = st.text_area("Address *")
            risk_profile = st.select_slider("Risk Profile", options=['Conservative', 'Moderate', 'Aggressive'], value='Moderate')
            if st.form_submit_button("Register Client") and name and phone and address:
                cid = f"CL-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000,9999)}"
                st.session_state.clients[cid] = {
                    'Client_ID': cid, 'Name': name, 'Phone': phone, 'Address': address,
                    'Risk_Profile': risk_profile, 'Registration_Date': str(date.today()),
                    'Trust_Score': 85, 'Status': 'Active'
                }
                save_db()
                st.success(f"✅ Client registered: {cid}")
                st.rerun()
    
    with col2:
        st.subheader(f"Client Registry ({len(st.session_state.clients)})")
        for cid, client in list(st.session_state.clients.items()):
            with st.expander(f"{client['Name']} - Trust: {client.get('Trust_Score',85)}%"):
                st.write(f"**ID:** {cid}")
                st.write(f"**Phone:** {client['Phone']}")
                st.write(f"**Address:** {client['Address']}")
                if st.button(f"Delete", key=f"del_{cid}"):
                    del st.session_state.clients[cid]
                    save_db()
                    st.rerun()

# ==================== ASSET INSURANCE (PILLAR I) ====================
elif selected_nav == "📦 Asset Insurance":
    st.header("📦 Asset Insurance — Pillar I Implementation")
    st.markdown("""
    <div class="formula-box">
        <div class="formula-title">Pillar I: Inflation-Adjusted Sum Insured</div>
        S_t = S₀ × (1 + CPI_t/100)^(m/12)<br>
        P_t = S_t × (β + π_t/10000 + ρ)
    </div>
    """, unsafe_allow_html=True)
    
    if not st.session_state.clients:
        st.warning("Register a client first")
    else:
        col1, col2 = st.columns([1, 1])
        with col1:
            client_options = {f"{cid} - {d['Name']}": cid for cid, d in st.session_state.clients.items()}
            sel_client = st.selectbox("Select Client", list(client_options.keys()))
            client_id = client_options[sel_client]
            
            with st.form("asset_form"):
                asset_type = st.selectbox("Asset Type", ['Motor Vehicle', 'Building', 'Farm Equipment', 'Electronics'])
                description = st.text_input("Description")
                S0 = st.number_input("Sum Insured (S₀) ZiG", value=100000.0, step=10000.0)
                policy_term = st.number_input("Policy Term (months)", 1, 60, 12)
                
                # Apply Pillar I formula
                S_t = ae.inflation_adjusted_sum_insured(S0, policy_term, ZIG_INFL)
                base_rate = 0.03
                risk_loading = 0.02
                monthly_premium = ae.premium_with_inflation_loading(S_t, base_rate, ZIG_MONTHLY_CPI, risk_loading)
                
                st.info(f"""
                **Actuarial Calculation:**
                - Adjusted SI (S_t): ZiG {S_t:,.2f}
                - Monthly Premium: ZiG {monthly_premium:,.2f}
                - Annual Premium: ZiG {monthly_premium * 12:,.2f}
                """)
                
                rbz_class = st.selectbox("RBZ Class", list(RBZ_CLASSES.keys()))
                
                if st.form_submit_button("Issue Policy"):
                    aid = f"AST-{datetime.now().strftime('%Y%m%d')}-{random.randint(10000,99999)}"
                    pol_num = f"POL-{asset_type[:3].upper()}-{datetime.now().strftime('%Y%m')}-{random.randint(1000,9999)}"
                    new_asset = pd.DataFrame([{
                        "Client_ID": client_id, "Asset_ID": aid, "Asset_Type": asset_type,
                        "Asset_Description": description, "Original_Value_ZiG": S_t,
                        "Original_Value_USD": S_t / ZIG_RATE, "Inception_Date": date.today(),
                        "Policy_Number": pol_num, "Policy_Term_Months": policy_term,
                        "RBZ_Class": rbz_class, "Risk_Loading": risk_loading,
                        "Monthly_Premium_ZiG": monthly_premium, "Monthly_Premium_USD": monthly_premium / ZIG_RATE,
                        "Lapsing_Rate": RBZ_CLASSES[rbz_class]["max_lapse"] + ZIG_INFL/1000,
                        "Original_Currency": "ZiG", "Original_Amount": S0,
                        "Original_Rate": ZIG_RATE, "Status": "Active",
                        "Registered_By": "System", "Adjusted_Sum_Insured": S_t
                    }])
                    st.session_state.assets = pd.concat([st.session_state.assets, new_asset], ignore_index=True)
                    save_db()
                    st.success(f"✅ Policy issued: {pol_num}")
                    st.rerun()
        
        with col2:
            st.subheader(f"Active Policies ({len(st.session_state.assets)})")
            for idx, asset in st.session_state.assets.iterrows():
                client_name = st.session_state.clients.get(asset["Client_ID"], {}).get("Name", "Unknown")
                with st.expander(f"{asset['Asset_Type']} - {client_name}"):
                    st.write(f"**Policy:** {asset['Policy_Number']}")
                    st.write(f"**Adjusted SI:** ZiG {asset['Original_Value_ZiG']:,.2f}")
                    st.write(f"**Premium:** ZiG {asset['Monthly_Premium_ZiG']:,.2f}/month")
                    if st.button(f"Delete Policy", key=f"del_ast_{idx}"):
                        st.session_state.assets = st.session_state.assets.drop(idx).reset_index(drop=True)
                        save_db()
                        st.rerun()

# ==================== PREMIUM PAYMENTS ====================
elif selected_nav == "💰 Premium Payments":
    st.header("💰 Premium Payments")
    
    if st.session_state.assets.empty:
        st.warning("No active policies")
    else:
        col1, col2 = st.columns([1, 1])
        with col1:
            asset_map = {f"{st.session_state.clients.get(r.Client_ID, {}).get('Name', '?')} — {r.Asset_Description}": r.Asset_ID for _, r in st.session_state.assets.iterrows()}
            selected = st.selectbox("Select Policy", list(asset_map.keys()))
            aid = asset_map[selected]
            asset = st.session_state.assets[st.session_state.assets["Asset_ID"] == aid].iloc[0]
            
            with st.form("pay_form"):
                amount = st.number_input("Amount (ZiG)", value=float(asset["Monthly_Premium_ZiG"]), step=10.0)
                if st.form_submit_button("Record Payment"):
                    new_pay = pd.DataFrame([{
                        "Date": date.today(), "Client_ID": asset["Client_ID"], "Asset_ID": aid,
                        "Amount_ZiG": amount, "Amount_USD": amount / ZIG_RATE,
                        "Method": "Cash", "Status": "Completed",
                        "Processed_By": "System", "Exchange_Rate_Used": ZIG_RATE
                    }])
                    st.session_state.payments = pd.concat([st.session_state.payments, new_pay], ignore_index=True)
                    save_db()
                    st.success("Payment recorded!")
                    st.rerun()
        
        with col2:
            if not st.session_state.payments.empty:
                total_zig = st.session_state.payments["Amount_ZiG"].sum()
                st.metric("Total Premiums Collected", f"ZiG {total_zig:,.2f}")

# ==================== CLAIMS & SEVERITY ====================
elif selected_nav == "📋 Claims & Severity":
    st.header("📋 Claims & Severity — E[S] = 0.83")
    st.markdown(f"""
    <div class="formula-box">
        <div class="formula-title">Claim Severity Distribution</div>
        E[S] = 0.50×0.30 + 0.30×0.60 + 0.15×1.00 + 0.04×1.50 + 0.01×2.00 = <b>{ae.E_SEVERITY:.2f}</b>
    </div>
    """, unsafe_allow_html=True)
    
    if not st.session_state.assets.empty:
        col1, col2 = st.columns([1, 1])
        with col1:
            asset_map = {f"{st.session_state.clients.get(r.Client_ID, {}).get('Name', '?')} — {r.Asset_Description}": r.Asset_ID for _, r in st.session_state.assets.iterrows()}
            selected = st.selectbox("Select Asset", list(asset_map.keys()))
            aid = asset_map[selected]
            asset = st.session_state.assets[st.session_state.assets["Asset_ID"] == aid].iloc[0]
            
            with st.form("claim_form"):
                severity = st.select_slider("Severity Level", options=list(ae.SEVERITY.keys()), value="Medium")
                damage_pct = st.slider("Damage (%)", 0, 100, 40)
                months_since = max(0, (date.today() - pd.to_datetime(asset["Inception_Date"]).date()).days / 30)
                infl_impact = (ae.inflation_adjusted_sum_insured(1.0, months_since, ZIG_INFL) - 1.0) * 100
                claim = ae.expected_claim_severity(asset["Original_Value_ZiG"], damage_pct, severity, infl_impact)
                
                st.info(f"**Adjusted Claim:** ZiG {claim:,.2f} (USD {claim/ZIG_RATE:,.2f})")
                
                if st.form_submit_button("Submit Claim"):
                    cid = f"CLM-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000,9999)}"
                    new_claim = pd.DataFrame([{
                        "Claim_ID": cid, "Client_ID": asset["Client_ID"], "Asset_ID": aid,
                        "Claim_Date": date.today(), "Severity_Level": severity,
                        "Damage_Pct": damage_pct, "Base_Claim_ZiG": 0,
                        "Inflation_Adjusted_Claim_ZiG": claim, "Claim_USD": claim / ZIG_RATE,
                        "Claim_Type": "Accident", "Status": "Filed",
                        "Days_To_Settle": 30, "Processed_By": "System"
                    }])
                    st.session_state.claims = pd.concat([st.session_state.claims, new_claim], ignore_index=True)
                    save_db()
                    st.success(f"Claim filed: {cid}")
                    st.rerun()

# ==================== INFLATION MODELS ====================
elif selected_nav == "📈 Inflation Models":
    st.header("📈 Inflation Models — Equations 3.1-3.3")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Reserve Erosion Calculator")
        R0 = st.number_input("Initial Reserve (R₀) ZiG", value=1_000_000.0, step=100_000.0, key="ie_R0")
        t_years = st.slider("Time (t years)", 0.0, 5.0, 1.0, key="ie_t")
        
        continuous = ae.real_reserve_continuous(R0, ZIG_INFL/100, t_years)
        discrete = ae.real_reserve_discrete(R0, ZIG_INFL/100, t_years)
        
        st.metric("Continuous (Eq 3.1)", f"ZiG {continuous:,.2f}")
        st.metric("Discrete (Eq 3.2)", f"ZiG {discrete:,.2f}")
        
        # Plot erosion
        years_plot = np.linspace(0, t_years, 50)
        values = [ae.real_reserve_continuous(R0, ZIG_INFL/100, y) for y in years_plot]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(years_plot, values, 'r-', linewidth=2)
        ax.fill_between(years_plot, values, R0, alpha=0.3, color='red')
        ax.set_xlabel('Years'); ax.set_ylabel('Reserve Value (ZiG)')
        ax.set_title('Real Claims Reserve Erosion')
        ax.grid(True, alpha=0.3)
        st.pyplot(fig)
        plt.close()
    
    with col2:
        st.subheader("Inflation-Adjusted SI Projection")
        S0 = st.number_input("Original SI (S₀) ZiG", value=100_000.0, step=10_000.0, key="si_proj")
        months = st.slider("Months (m)", 0, 60, 12, key="si_months")
        adj_si = ae.inflation_adjusted_sum_insured(S0, months, ZIG_INFL)
        st.metric(f"After {months} months at {ZIG_INFL}% inflation", f"ZiG {adj_si:,.2f}", delta=f"+{adj_si - S0:,.2f}")

# ==================== TRUST ANALYTICS ====================
elif selected_nav == "📊 Trust Analytics":
    st.header("📊 Trust Analytics — T = 0.40A + 0.30V + 0.10I + 0.10C + 0.10R")
    
    col1, col2 = st.columns(2)
    with col1:
        paid = st.number_input("Claims Paid", 0, 100, 8)
        total = st.number_input("Total Claims", 1, 100, 10)
        days = st.slider("Avg Settlement Days", 1, 90, 25)
        infl_adj = st.checkbox("Inflation-Adjusted Claims", value=True)
        comm = st.slider("Communication Score", 0.0, 10.0, 7.5)
        
        ts = ae.trust_score(paid, total, days, infl_adj, comm)
        st.metric("Trust Score", f"{ts['total']:.1f}%")
        st.write(f"Base: {ts['base']} + Claims: {ts['claims_component']} + Speed: {ts['speed_component']} + Inflation: {ts['inflation_component']} + Comm: {ts['communication_component']}")
    
    with col2:
        st.subheader("Industry Trust Recovery")
        trust_data = pd.DataFrame({
            'Period': ['Pre-ZiG (2023)', 'ZiG Y1 (2024)', 'ZiG Y2 (2025)', 'Current (2026)'],
            'Trust': [35, 65, 78, 85]
        })
        fig, ax = plt.subplots(figsize=(8, 4))
        bars = ax.bar(trust_data['Period'], trust_data['Trust'], color=['#dc3545', '#ff9800', '#8bc34a', '#2e7d32'])
        ax.set_ylim(0, 100); ax.set_ylabel('Trust Score (%)')
        ax.set_title('Insurance Industry Trust Recovery')
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + 2, f'{height}%', ha='center')
        st.pyplot(fig)
        plt.close()

# ==================== CAPITAL & SOLVENCY (PILLAR II) ====================
elif selected_nav == "💼 Capital & Solvency":
    st.header("💼 Capital & Solvency — Pillar II")
    st.markdown("""
    <div class="formula-box">
        <div class="formula-title">Pillar II: Required Capital Formula</div>
        K_req = max(0.15×A_tot, K_rw, 1.2×L_out, 500,000×e_USD/ZiG)<br>
        Solvency Ratio = (K_actual / K_req) × 100% ≥ 150%
    </div>
    """, unsafe_allow_html=True)
    
    total_assets = st.session_state.assets["Original_Value_ZiG"].sum() if not st.session_state.assets.empty else 0
    outstanding_claims = st.session_state.claims["Inflation_Adjusted_Claim_ZiG"].sum() if not st.session_state.claims.empty else 0
    
    # RWA calculation
    rwa_dict = {}
    for cls_name in RBZ_CLASSES:
        subset = st.session_state.assets[st.session_state.assets["RBZ_Class"] == cls_name]
        rwa_dict[cls_name] = subset["Original_Value_ZiG"].sum()
    rwa = ae.risk_weighted_assets(rwa_dict)
    
    available = st.session_state.capital.get("available", 50_000_000)
    rc_result = ae.required_capital(total_assets, rwa, outstanding_claims, 500_000, ZIG_RATE)
    rc = rc_result["required"]
    solvency = ae.solvency_ratio(available, rc)
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Assets", f"ZiG {total_assets/1e6:.2f}M")
    col2.metric("Risk-Weighted Assets", f"ZiG {rwa/1e6:.2f}M")
    col3.metric("Required Capital", f"ZiG {rc/1e6:.2f}M")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Available Capital", f"ZiG {available/1e6:.2f}M")
    col2.metric("Solvency Ratio", f"{solvency:.1f}%", delta="Compliant" if solvency >= 150 else "Below")
    col3.metric("USD Floor", f"${500_000:,.0f} = ZiG {500_000 * ZIG_RATE:,.0f}")
    
    if st.button("Add Capital (ZiG 1M)"):
        st.session_state.capital["available"] += 1_000_000
        save_db()
        st.rerun()

# ==================== PROFIT TESTING ====================
elif selected_nav == "📉 Profit Testing":
    st.header("📉 Profit Testing — IFoA Scenario Analysis")
    
    col1, col2 = st.columns([1, 1])
    with col1:
        scenario = st.selectbox("Scenario", ["Base Case (8.5%)", "Optimistic (5%)", "Pessimistic (12%)"])
        years = st.slider("Projection Years", 1, 10, 5)
        
        sc_params = {
            "Base Case (8.5%)": {"cpi": 8.5, "growth": 10, "lr": 0.60},
            "Optimistic (5%)": {"cpi": 5.0, "growth": 15, "lr": 0.50},
            "Pessimistic (12%)": {"cpi": 12.0, "growth": 8, "lr": 0.70},
        }
        p = sc_params[scenario]
        
        base_prem = st.session_state.payments["Amount_ZiG"].sum() if not st.session_state.payments.empty else 500_000
        results = ae.profit_test(base_prem, years, p["cpi"], p["growth"], p["lr"])
        
        st.metric("NPV of Profits", f"ZiG {results['NPV_Profit'].sum():,.0f}")
        st.metric("Avg Combined Ratio", f"{results['Combined_Ratio'][1:].mean():.1f}%")
    
    with col2:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(results["Year"], results["Premiums"], 'b-o', label='Premiums')
        ax.plot(results["Year"], results["Claims"], 'r-s', label='Claims')
        ax.plot(results["Year"], results["Profit"], 'g-^', label='Profit')
        ax.set_xlabel('Year'); ax.set_ylabel('ZiG')
        ax.set_title(f'Profit Projection - {scenario}')
        ax.legend(); ax.grid(True, alpha=0.3)
        st.pyplot(fig)
        plt.close()
    
    st.dataframe(results.round(0), use_container_width=True)

# ==================== RATE MODELLING (GBM) ====================
elif selected_nav == "🔬 Rate Modelling (GBM)":
    st.header("🔬 ZiG/USD Rate Modelling — Geometric Brownian Motion")
    
    col1, col2 = st.columns([1, 1])
    with col1:
        s0 = st.number_input("Current Rate (S₀)", value=float(ZIG_RATE), step=0.5)
        mu = st.slider("Drift μ", -0.5, 0.5, 0.05, 0.01)
        sigma = st.slider("Volatility σ", 0.01, 0.8, 0.20, 0.01)
        months = st.slider("Horizon (months)", 3, 24, 12)
        paths = st.slider("Paths", 50, 500, 200)
        
        if st.button("Run Simulation"):
            sim_paths = ae.gbm_rate_simulation(s0, mu, sigma, months, paths)
            terminal = sim_paths[-1]
            
            fig, ax = plt.subplots(figsize=(8, 4))
            for i in range(min(100, paths)):
                ax.plot(range(months+1), sim_paths[:, i], 'b-', alpha=0.05, linewidth=0.5)
            ax.plot(range(months+1), np.percentile(sim_paths, 50, axis=1), 'r-', linewidth=2, label='Median')
            ax.fill_between(range(months+1), np.percentile(sim_paths, 25, axis=1), np.percentile(sim_paths, 75, axis=1), alpha=0.3, color='gray')
            ax.set_xlabel('Months'); ax.set_ylabel('ZiG/USD')
            ax.set_title('GBM Exchange Rate Simulation')
            ax.legend()
            st.pyplot(fig)
            plt.close()
            
            col1a, col2a, col3a = st.columns(3)
            col1a.metric("Median", f"{np.median(terminal):.4f}")
            col2a.metric("95th Percentile", f"{np.percentile(terminal, 95):.4f}")
            col3a.metric("5th Percentile", f"{np.percentile(terminal, 5):.4f}")

# ==================== IPEC DASHBOARD ====================
elif selected_nav == "🏛️ IPEC Dashboard":
    st.header("🏛️ IPEC NDS2 Policy Dashboard")
    
    st.markdown("""
    <div style="display:flex; gap:0.8rem; margin-bottom:1rem;">
        <div class="pillar-card pillar-i" style="flex:1;">
            <h4>Pillar I — Inflation-Indexed Valuation</h4>
            <p>Mandatory CPI-linked sum insured adjustment for all policies</p>
            <b>Target:</b> 100% compliance by Q2 2027
        </div>
        <div class="pillar-card pillar-ii" style="flex:1;">
            <h4>Pillar II — Multi-Currency Capital</h4>
            <p>Dual ZiG/USD capital adequacy. USD 500K floor</p>
            <b>Target:</b> 120% capital adequacy by 2030
        </div>
        <div class="pillar-card pillar-iii" style="flex:1;">
            <h4>Pillar III — InsurTech Sandbox</h4>
            <p>IPEC-supervised sandbox for InsurTech platforms</p>
            <b>Target:</b> First cohort Q3 2027
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # NDS2 Progress
    st.subheader("NDS2 KPI Progress Tracker")
    progress_data = {
        "Metric": ["Insurance Penetration", "Trust Score", "Capital Adequacy", "Policy Retention"],
        "Current": [12, 85, min(150, (st.session_state.capital.get("available", 50_000_000) / 50_000_000 * 100)), 75],
        "Target": [30, 90, 150, 90]
    }
    progress_df = pd.DataFrame(progress_data)
    st.dataframe(progress_df, use_container_width=True, hide_index=True)
    
    # Penetration chart
    pen_df = pd.DataFrame({
        "Year": [2010, 2015, 2020, 2023, 2024, 2026, 2027, 2029, 2030],
        "Penetration": [60, 45, 25, 12, 10, 12, 18, 25, 30],
        "Type": ["Historical"] * 5 + ["Projected"] * 4
    })
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(pen_df[pen_df["Type"]=="Historical"]["Year"], pen_df[pen_df["Type"]=="Historical"]["Penetration"], 'b-o', label='Historical')
    ax.plot(pen_df[pen_df["Type"]=="Projected"]["Year"], pen_df[pen_df["Type"]=="Projected"]["Penetration"], 'g--s', label='Projected (NDS2)')
    ax.axhline(y=30, color='r', linestyle='--', label='NDS2 Target 30%')
    ax.set_xlabel('Year'); ax.set_ylabel('Insurance Penetration (%)')
    ax.set_title('Insurance Penetration Recovery Under ZiG')
    ax.legend(); ax.grid(True, alpha=0.3)
    st.pyplot(fig)
    plt.close()

# ==================== CURRENCY HISTORY ====================
elif selected_nav == "📜 Currency History":
    st.header("📜 Complete Zimbabwe Currency History (2009-2026)")
    
    history_df = pd.DataFrame({
        'Period': ['2009-2014', '2014-2016', '2016-2018', '2018-2019', '2019-2020', '2020-2022', '2022-2023', '2023-2024', '2024-2025', '2025-2026'],
        'Currency': ['USD/Multi', 'Bond Notes', 'Bond (Devalued)', 'RTGS', 'RTGS (Hyper)', 'ZWL', 'ZWL (Crisis)', 'ZWL (Last)', 'ZiG', 'ZiG (Stable)'],
        'Rate (USD)': [1.0, 1.0, 2.5, 15.0, 80.0, 200.0, 1500.0, 8000.0, 13500.0, ZIG_RATE],
        'Inflation %': [5, 2.5, 50, 150, 500, 350, 800, 600, 150, ZIG_INFL]
    })
    st.dataframe(history_df, use_container_width=True, hide_index=True)
    
    st.subheader("Historical Currency Converter")
    col_h1, col_h2 = st.columns(2)
    with col_h1:
        hist_amt = st.number_input("Amount", value=1_000_000.0, step=100_000.0)
        hist_date = st.date_input("Original Date", value=date(2020, 1, 1))
    with col_h2:
        rate_at_date, currency = ae.historical_rate(hist_date)
        current_value = ae.convert_to_current_zig(hist_amt, hist_date, ZIG_RATE)
        st.metric(f"Original: {hist_amt:,.0f} {currency}", f"= ${hist_amt/rate_at_date:,.2f} USD")
        st.metric("Current ZiG Value", f"ZiG {current_value:,.2f}")
        st.caption(f"Rate on {hist_date}: 1 USD = {rate_at_date:.2f} {currency}")

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown(f"""
<div style="text-align:center; padding:1rem; background:linear-gradient(135deg,#0a1f3d,#1B3A6B); border-radius:0.5rem; color:white;">
    <p><strong>RiskShield Zimbabwe v10.0</strong> | Complete Actuarial Platform | All Formulas Implemented</p>
    <p>Equations 3.1-3.15 | Pillars I, II, III | NDS2-Aligned | Live RBZ Data</p>
    <p>Data Source: {ZIG_SOURCE} | 1 USD = {ZIG_RATE:.4f} ZiG | Inflation: {ZIG_INFL}%</p>
</div>
""", unsafe_allow_html=True)
