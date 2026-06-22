"""Site Scout Pro — medical/dental commercial-real-estate intelligence (Streamlit).

Run:  streamlit run site_scout_pro.py

Reuses the validated analytical engine in ClinicSiteIntel/app/ (Census ACS
demographics, CDC PLACES disease burden, NPPES competitor/referral discovery,
Huff gravity, epi-weighted demand, unit economics) and adds:
  - a 4-tier Verdict engine (Strong Buy / Viable / Caution / Not Recommended)
  - drive-time isochrones (Mapbox key → OSRM fallback → Haversine radius)
  - listing ingestion (manual paste + sample feed + optional Apify; LoopNet/
    Crexi are NOT directly scrapable — they 403/CAPTCHA — so live pulls require
    a paid Apify actor token, surfaced as a UI field)
  - optional Claude Vision listing read (Anthropic key) with the embedded
    Site Scout Pro master prompt.

Honest constraints (per the brief's own note): there are no official public APIs
for LoopNet/Crexi, and direct Playwright scraping is reliably blocked. So the
default path is manual paste / sample data, with key-gated live upgrades.
"""
from __future__ import annotations
import os
import sys
import json
import math
import time
from dataclasses import dataclass, field, asdict

# --- make the existing analytical engine importable ---
APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import requests

# Reuse the validated modules (no reimplementation of the science).
import geocode          # geocode_address, geocode_oneline, haversine_miles
import demographics     # ACS pulls
import epi              # CDC PLACES + expected-cases demand
import nppes            # NPI competitor/referral + ZIP centroids
import competitors as comp_mod
import referrals as ref_mod
import spatial          # Huff / MCI / 3SFCA / Reilly / Clark-Evans
import econ             # break-even overlay

HAVERSINE = geocode.haversine_miles


# ============================================================================
# Data model
# ============================================================================
@dataclass
class PropertyListing:
    id: str
    address: str
    lat: float
    lng: float
    price: float | None
    sqft: float | None
    source: str
    days_on_market: int | None = None
    url: str | None = None


@dataclass
class LocationVerdict:
    verdict: str            # Strong Buy | Viable | Caution | Not Recommended
    score: int              # 0-100
    capture_score: float    # Huff demand-capture %
    drive_time_minutes: float | None
    isochrone_summary: str
    reasoning_simple: str
    medical_dental_fit: str
    payer_affluence_summary: str
    recommendation: str
    risks: list
    opportunities: list
    referrals_near: list = field(default_factory=list)     # (name, specialty, miles)
    competitors_near: list = field(default_factory=list)   # (name, tier, miles)


# ============================================================================
# STEP 1 — Listing ingestion
# ============================================================================
SAMPLE_LISTINGS = [
    # Address-only seeds (geocoded at load) — a realistic Conejo-Valley sample
    # so the app is fully demonstrable with zero paid keys.
    ("2220 Lynn Rd, Thousand Oaks, CA 91360", 42.0, 1100, "Sample"),
    ("179 Auburn Ct, Westlake Village, CA 91362", 18.0, 1800, "Sample"),
    ("29525 Canwood St, Agoura Hills, CA 91301", None, 1500, "Sample"),
    ("24011 Ventura Blvd, Calabasas, CA 91302", None, 2000, "Sample"),
]


def _geocode_listing(addr: str, rate_per_sf, sqft, source) -> PropertyListing | None:
    coords = geocode.geocode_oneline(addr)
    if not coords:
        return None
    price = (rate_per_sf * sqft) if (rate_per_sf and sqft) else None
    return PropertyListing(id=str(abs(hash(addr)) % 10_000_000), address=addr,
                           lat=coords[0], lng=coords[1], price=price, sqft=sqft,
                           source=source, days_on_market=14)


def fetch_recent_listings(zip_code: str, radius_miles: float = 10.0,
                          apify_token: str = "", log=lambda m: None) -> list[PropertyListing]:
    """Return listings <= 60 days on market within radius of the ZIP.

    Live LoopNet/Crexi requires a paid Apify actor (no public API; direct
    scraping is 403/CAPTCHA-blocked). With a token we call Apify; otherwise we
    fall back to the geocoded sample feed so the pipeline is fully usable."""
    out: list[PropertyListing] = []
    if apify_token:
        log("Apify token present — calling LoopNet/Crexi actors…")
        try:
            out = _fetch_via_apify(zip_code, radius_miles, apify_token, log)
        except Exception as e:
            log(f"Apify call failed ({e}); falling back to sample feed.")
    if not out:
        log("Using geocoded sample listing feed (no live source configured).")
        center = nppes._zip_centroid(zip_code)
        for addr, rate, sqft, src in SAMPLE_LISTINGS:
            pl = _geocode_listing(addr, rate, sqft, src)
            if pl and (center is None or
                       HAVERSINE(center[0], center[1], pl.lat, pl.lng) <= max(radius_miles, 25)):
                out.append(pl)
    # STEP-1 filter: only properties <= 60 days on market.
    return [p for p in out if (p.days_on_market is None or p.days_on_market <= 60)]


def _fetch_via_apify(zip_code: str, radius_miles: float, token: str, log) -> list[PropertyListing]:
    """Apify integration. Actor IDs are configurable; defaults are common
    community LoopNet/Crexi actors. Maps the payload into PropertyListing and
    keeps only days_on_market <= 60."""
    try:
        from apify_client import ApifyClient
    except Exception:
        log("apify-client not installed (pip install apify-client) — skipping live pull.")
        return []
    client = ApifyClient(token)
    results: list[PropertyListing] = []
    for actor_id, source in (("epctex/loopnet-scraper", "LoopNet"),
                             ("epctex/crexi-scraper", "Crexi")):
        try:
            run_input = {"search": zip_code, "maxItems": 40}
            run = client.actor(actor_id).call(run_input=run_input)
            for it in client.dataset(run["defaultDatasetId"]).iterate_items():
                dom = it.get("daysOnMarket") or it.get("days_on_market")
                if dom is not None and dom > 60:
                    continue
                lat = it.get("latitude") or (it.get("location") or {}).get("lat")
                lng = it.get("longitude") or (it.get("location") or {}).get("lng")
                if lat is None or lng is None:
                    continue
                results.append(PropertyListing(
                    id=str(it.get("id") or abs(hash(it.get("url", ""))) % 10_000_000),
                    address=it.get("address") or it.get("title") or "",
                    lat=float(lat), lng=float(lng),
                    price=it.get("price"), sqft=it.get("sqft") or it.get("squareFeet"),
                    source=source, days_on_market=dom, url=it.get("url")))
            log(f"{source}: {len(results)} listings via Apify.")
        except Exception as e:
            log(f"{source} actor failed: {e}")
    return results


# ============================================================================
# Drive-time isochrones (Mapbox key → OSRM fallback → Haversine radius proxy)
# ============================================================================
def drive_time_minutes(from_lat, from_lng, to_lat, to_lng, mapbox_token: str = "") -> float | None:
    """Driving minutes between two points. Mapbox Directions if a token is
    given, else OSRM public demo, else None (caller uses Haversine proxy)."""
    if mapbox_token:
        try:
            url = (f"https://api.mapbox.com/directions/v5/mapbox/driving/"
                   f"{from_lng},{from_lat};{to_lng},{to_lat}")
            r = requests.get(url, params={"access_token": mapbox_token, "overview": "false"}, timeout=12)
            r.raise_for_status()
            return round(r.json()["routes"][0]["duration"] / 60.0, 1)
        except Exception:
            pass
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{from_lng},{from_lat};{to_lng},{to_lat}"
        r = requests.get(url, params={"overview": "false"}, timeout=12)
        r.raise_for_status()
        return round(r.json()["routes"][0]["duration"] / 60.0, 1)
    except Exception:
        return None


def isochrone_population(lat, lng, demand_points, mapbox_token: str = "") -> dict:
    """Expected addressable patients reachable within 15/30/45 driving minutes.
    Uses real drive-time to each demand ZIP when routing is available, else a
    speed-based Haversine proxy (~28 mph effective urban)."""
    buckets = {15: 0.0, 30: 0.0, 45: 0.0}
    for dp in demand_points:
        mins = drive_time_minutes(lat, lng, dp.lat, dp.lon, mapbox_token)
        if mins is None:
            mins = HAVERSINE(lat, lng, dp.lat, dp.lon) / 28.0 * 60.0  # ~28 mph proxy
        for thr in (15, 30, 45):
            if mins <= thr:
                buckets[thr] += dp.population   # population = expected cases (epi)
    return {k: round(v) for k, v in buckets.items()}


# ============================================================================
# STEP 2 — Analytical pipeline / Verdict engine
# ============================================================================
def _build_context(listing, zip5, state, known_competitors, census_key, log):
    """Run the shared engine for one location: demographics, epi demand,
    competitors, referrals."""
    lat, lng = listing.lat, listing.lng
    ctx = {}
    try:
        ctx["zcta"] = demographics.get_zcta_profile(zip5, census_key) if zip5 else None
    except Exception as e:
        ctx["zcta"] = None
        log(f"ACS pull failed: {e}")
    # epi demand over nearby ZIP basket; OSA risk from CDC PLACES (needs tract).
    demand = []
    osa = 1.0
    try:
        g = geocode.geocode_address(listing.address)
        osa = epi.get_places_osa_base(g.geoid_tract).get("osa_index", 1.0) or 1.0
    except Exception:
        osa = 1.0
    fshare = (ctx["zcta"].female_share if ctx.get("zcta") and ctx["zcta"].female_share else 0.51)
    basket = [zip5] + nppes_neighbor_zips(zip5)
    for z in basket:
        cen = nppes._zip_centroid(z)
        prof = None
        try:
            prof = demographics.get_zcta_profile(z, census_key)
        except Exception:
            pass
        if cen and prof and prof.population:
            cases = epi.expected_cases(prof.population, prof.median_age,
                                       prof.median_household_income, osa, fshare)
            if cases > 0:
                demand.append(spatial.DemandPoint(cen[0], cen[1], cases, f"ZIP {z}",
                                                  headcount=float(prof.population)))
    ctx["demand"] = demand
    # competitors + referrals via the shared engine
    try:
        scan = comp_mod.run_full_competitor_scan("", lat, lng, zip5=zip5, state=state,
                                                 known_competitors=known_competitors)
        ctx["competitors"] = [c.__dict__ for c in scan["competitors"]]  # dict access downstream
    except Exception as e:
        ctx["competitors"] = []; log(f"competitor scan failed: {e}")
    try:
        ctx["referrals"] = ref_mod.find_referral_candidates("", lat, lng, zip5=zip5, state=state)
    except Exception as e:
        ctx["referrals"] = []; log(f"referral scan failed: {e}")
    return ctx


def nppes_neighbor_zips(zip5):
    try:
        base = int(zip5)
    except (TypeError, ValueError):
        return []
    return [str(base + d) for d in (-3, -2, -1, 1, 2, 3)]


def calculate_location_verdict(listing: PropertyListing, ctx: dict,
                               mapbox_token: str = "") -> LocationVerdict:
    """Huff gravity capture + drive-time isochrones + competitive/affluence
    synthesis → a 4-tier verdict with plain-English reasoning."""
    demand = ctx.get("demand", [])
    comps = ctx.get("competitors", [])
    refs = ctx.get("referrals", [])
    zcta = ctx.get("zcta")

    # --- Huff capture via the shared spatial engine ---
    facilities = []
    for c in comps:
        if not (c.get("competition_score", 0) > 0):
            continue
        flat, flng = c.get("lat", 0), c.get("lon", 0)
        if not flat or not flng:
            import re as _re
            m = _re.search(r"(\d{5})", c.get("address", "") or "")
            cen = nppes._zip_centroid(m.group(1)) if m else None
            if cen:
                flat, flng = cen
        if flat and flng:
            facilities.append(spatial.Facility(c.get("name", ""), flat, flng,
                                               float(c.get("competition_score", 0) or 0),
                                               retire_prob=float(c.get("retire_prob", 0) or 0),
                                               capacity=max(0.1, float(c.get("competition_score", 0) or 0) / 90.0)))
    sp = spatial.compute_all(listing.lat, listing.lng, facilities, demand, clinic_attractiveness=70.0)
    capture = sp.huff_share_pct if sp.ok else 0.0

    # --- drive-time / isochrone ---
    iso = isochrone_population(listing.lat, listing.lng, demand, mapbox_token)
    nearest_comp_dt = None
    spec = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
    if spec:
        nearest = min(spec, key=lambda c: c.get("distance_mi") or 999)
        nlat, nlng = nearest.get("lat", 0), nearest.get("lon", 0)
        if not nlat or not nlng:
            import re as _re
            m = _re.search(r"(\d{5})", nearest.get("address", "") or "")
            cen = nppes._zip_centroid(m.group(1)) if m else None
            if cen:
                nlat, nlng = cen
        if nlat and nlng:
            nearest_comp_dt = drive_time_minutes(listing.lat, listing.lng, nlat, nlng, mapbox_token)

    # --- payer / affluence (ACS) ---
    income = zcta.median_household_income if zcta else None
    affluence = (epi.cashpay_propensity(income) if income else 0.5)
    payer_summary = (
        f"Median household income ${income:,.0f} → cash-pay/PPO propensity {affluence*100:.0f}%. "
        + ("Affluent, fee-for-service-friendly market." if affluence >= 0.6 else
           "Mixed affluence — verify commercial-insurance density." if affluence >= 0.4 else
           "Lower-affluence/Medicaid-leaning — weaker for elective cash-pay.")
        if income else "Income data unavailable for payer/affluence read.")

    # --- referral proximity ---
    n_md = sum(1 for r in refs if str(getattr(r, "category", "")).startswith("Physician"))
    refs_near = sorted(
        [(r.name, r.specialty, r.distance_mi) for r in refs if r.distance_mi is not None],
        key=lambda x: x[2])[:10]
    comps_near = sorted(
        [(c.get("name"), c.get("tier"), c.get("distance_mi")) for c in comps if c.get("distance_mi") is not None],
        key=lambda x: x[2] if x[2] is not None else 999)[:10]
    n_spec = len(spec)
    nearest_spec_mi = min([c.get("distance_mi") for c in spec if c.get("distance_mi") is not None], default=None)

    # --- score → verdict ---
    score = 0
    score += min(35, capture * 2.0)                 # demand capture (Huff)
    score += 25 * affluence                          # affluence/payer
    score += min(20, n_md * 1.0)                     # referral depth
    if n_spec == 0: score += 20
    elif (nearest_spec_mi or 99) >= 8: score += 12
    elif (nearest_spec_mi or 99) >= 4: score += 6
    if econ_clears(sp.huff_captured_pop): score += 8
    score = int(max(0, min(100, score)))

    verdict = ("Strong Buy" if score >= 72 else "Viable" if score >= 55
               else "Caution" if score >= 38 else "Not Recommended")

    near_txt = (f"the nearest competitor ~{nearest_spec_mi:.0f} mi away"
                if nearest_spec_mi is not None else "no credentialed specialist nearby")
    reasoning = (
        f"This site captures ~{capture:.0f}% of local TMJ/OSA demand, with {n_md} physician "
        f"referral sources in the catchment and {n_spec} credentialed specialist competitor(s) — "
        f"{near_txt}. {payer_summary.split('.')[0]}.")

    er = econ.proforma(sp.huff_captured_pop or 0)
    fit = (f"Drive-time reach: 15 min ≈ {iso[15]:,} expected cases, 30 min ≈ {iso[30]:,}, "
           f"45 min ≈ {iso[45]:,}. Break-even ≈ {er.break_even_cases:,}/yr vs projected "
           f"≈ {er.projected_cases:,}/yr. {n_md} referrers; specialist density "
           f"{'low (favorable)' if n_spec <= 2 else 'moderate' if n_spec <= 5 else 'high (saturated)'}.")

    risks, opps = [], []
    if (nearest_spec_mi or 99) < 3: risks.append(f"Specialist competitor only {nearest_spec_mi:.1f} mi away")
    if affluence < 0.45: risks.append("Lower commercial-payer affluence")
    if capture < 12: risks.append("Thin predicted demand capture")
    if er.projected_cases < er.break_even_cases: risks.append("Projected volume below break-even at default costs")
    if n_md >= 30: opps.append(f"Deep referral pool ({n_md} physicians)")
    if n_spec <= 2: opps.append("Under-served specialist market")
    if affluence >= 0.6: opps.append("Affluent fee-for-service / PPO base")
    if iso[30] >= 8000: opps.append(f"{iso[30]:,} expected cases within a 30-min drive")
    if not risks: risks.append("No major modeled risks — verify lease & build-out in person")
    if not opps: opps.append("Confirm on-site fundamentals (parking, visibility, zoning)")

    rec = ("Advance to LOI / broker diligence." if verdict == "Strong Buy" else
           "Pursue with a differentiation plan; confirm suite + lease terms." if verdict == "Viable" else
           "Only if better candidates are exhausted; mitigate the listed risks first." if verdict == "Caution" else
           "Pass — prefer a less saturated / more affluent site.")

    iso_summary = f"15 min: {iso[15]:,} | 30 min: {iso[30]:,} | 45 min: {iso[45]:,} expected cases"
    return LocationVerdict(
        verdict=verdict, score=score, capture_score=round(capture, 1),
        drive_time_minutes=nearest_comp_dt, isochrone_summary=iso_summary,
        reasoning_simple=reasoning, medical_dental_fit=fit,
        payer_affluence_summary=payer_summary, recommendation=rec,
        risks=risks, opportunities=opps,
        referrals_near=refs_near, competitors_near=comps_near)


def econ_clears(captured_cases) -> bool:
    er = econ.proforma(captured_cases or 0)
    return er.projected_cases >= er.break_even_cases


# ============================================================================
# Claude Vision (optional) — embedded Site Scout Pro master prompt
# ============================================================================
SITE_SCOUT_MASTER_PROMPT = """You are Site Scout Pro, a world-class medical and dental healthcare real estate intelligence agent.

**Target ZIP:** {ZIP_CODE}
**Property Type Focus:** Medical offices, dental clinics, healthcare suites, professional office buildings suitable for clinical use.

**Analysis Framework (apply all layers):**
- True drive-time isochrones (traffic-aware) instead of radius
- Clinical patient flow potential and referral network strength
- Payer mix (commercial PPO insurance density vs Medicare/Medicaid)
- Affluence indicators (median income, disposable income)
- Property fundamentals (visibility, parking, zoning, build-out readiness, condition from photos)

**Output ONLY valid JSON:**
{{
  "verdict": "Strong Buy | Viable | Caution | Not Recommended",
  "score": 0-100,
  "drive_time_minutes": number,
  "isochrone_summary": "15 min: X potential patients | 30 min: Y ...",
  "reasoning_simple": "Short, clear explanation in plain English for a busy doctor (2-4 sentences)",
  "medical_dental_fit": "Detailed clinical suitability assessment",
  "payer_affluence_summary": "e.g. 68% commercial insurance, median income $135k — excellent for fee-for-service and PPO",
  "recommendation": "Actionable next step",
  "risks": ["list of key risks"],
  "opportunities": ["list of advantages"]
}}"""


def claude_vision_analyze(anthropic_key: str, zip_code: str, context_text: str,
                          screenshot_path: str | None = None) -> dict | None:
    """Optional: send the data context (+ a listing screenshot if provided) to
    Claude for a vision-aware verdict using the embedded master prompt."""
    if not anthropic_key:
        return None
    try:
        import anthropic
    except Exception:
        return None
    client = anthropic.Anthropic(api_key=anthropic_key)
    content = [{"type": "text",
                "text": SITE_SCOUT_MASTER_PROMPT.format(ZIP_CODE=zip_code)
                + "\n\nStructured data context:\n" + context_text}]
    if screenshot_path and os.path.exists(screenshot_path):
        import base64
        with open(screenshot_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({"type": "image", "source": {"type": "base64",
                        "media_type": "image/png", "data": b64}})
    try:
        msg = client.messages.create(model="claude-opus-4-8", max_tokens=1200,
                                     messages=[{"role": "user", "content": content}])
        txt = msg.content[0].text
        start, end = txt.find("{"), txt.rfind("}")
        return json.loads(txt[start:end + 1]) if start >= 0 else None
    except Exception:
        return None


# ============================================================================
# STEP 3 — Streamlit GUI
# ============================================================================
VERDICT_COLORS = {
    "Strong Buy": "#34c759", "Viable": "#007aff",
    "Caution": "#ff9500", "Not Recommended": "#ff3b30",
}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_scout_config.yaml")


def _load_cfg():
    import yaml
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}
    return {}


def _save_cfg(cfg):
    import yaml
    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(cfg, f)
    except Exception:
        pass


def run_app():
    import streamlit as st
    import pandas as pd

    st.set_page_config(page_title="Site Scout Pro", page_icon="🏥", layout="wide")
    st.markdown("""<style>
      .stApp { background:#0f1722; }
      .block-container { padding-top: 1.5rem; }
      .verdict-pill { color:#fff; font-weight:800; padding:4px 12px; border-radius:14px; font-size:13px; }
      .card { background:#16202e; border:1px solid #233247; border-radius:12px; padding:14px 18px; margin-bottom:10px; }
    </style>""", unsafe_allow_html=True)

    cfg = _load_cfg()
    if "logs" not in st.session_state:
        st.session_state.logs = []
    if "listings" not in st.session_state:
        st.session_state.listings = []
    if "verdicts" not in st.session_state:
        st.session_state.verdicts = {}

    def log(m):
        st.session_state.logs.append(f"{time.strftime('%H:%M:%S')}  {m}")

    # ---------------- Sidebar (shared settings) ----------------
    with st.sidebar:
        st.title("🏥 Site Scout Pro")
        st.caption("Medical & dental CRE intelligence")
        zip_code = st.text_input("Target ZIP", cfg.get("zip", "91360"))
        radius = st.slider("Search radius (mi)", 3, 30, int(cfg.get("radius", 10)))
        st.divider()
        st.subheader("API keys")
        census_key = st.text_input("US Census API key (required for demographics)",
                                   cfg.get("census_key", ""), type="password")
        mapbox_key = st.text_input("Mapbox token (drive-time isochrones)",
                                   cfg.get("mapbox_key", ""), type="password")
        anthropic_key = st.text_input("Anthropic key (Claude Vision — optional)",
                                      cfg.get("anthropic_key", ""), type="password")
        apify_key = st.text_input("Apify token (live LoopNet/Crexi — optional)",
                                  cfg.get("apify_key", ""), type="password")
        if st.button("💾 Save settings"):
            _save_cfg({"zip": zip_code, "radius": radius, "census_key": census_key,
                       "mapbox_key": mapbox_key, "anthropic_key": anthropic_key, "apify_key": apify_key})
            st.success("Saved to site_scout_config.yaml")
        st.divider()
        st.caption("LoopNet/Crexi have no public API and block scraping. Live pulls "
                   "need an Apify token; otherwise a geocoded sample feed + manual paste is used.")

    tab_camp, tab_med = st.tabs(["⛺ Camping", "🏥 Medical & Dental"])

    with tab_camp:
        st.subheader("Camping")
        st.info("Placeholder tab (per the shared tabbed layout). The medical/dental "
                "intelligence lives in the next tab.")

    with tab_med:
        c1, c2, c3 = st.columns([1, 1, 2])
        search = c1.button("🔎 Search listings", type="primary", use_container_width=True)
        analyze = c2.button("⚙️ Analyze all", use_container_width=True)
        status = c3.empty()

        # --- manual paste ---
        with st.expander("➕ Add a listing manually (paste address)"):
            man_addr = st.text_input("Property address", key="man_addr")
            mcol1, mcol2 = st.columns(2)
            man_rate = mcol1.number_input("Rate $/SF/yr (optional)", 0.0, 200.0, 0.0)
            man_sqft = mcol2.number_input("Square feet (optional)", 0.0, 50000.0, 0.0)
            if st.button("Add listing") and man_addr.strip():
                pl = _geocode_listing(man_addr.strip(), man_rate or None, man_sqft or None, "Manual")
                if pl:
                    st.session_state.listings.append(pl); log(f"Added manual listing: {pl.address}")
                else:
                    st.error("Could not geocode that address.")

        if search:
            with st.spinner("Fetching recent listings (≤60 days on market)…"):
                st.session_state.listings = fetch_recent_listings(zip_code, radius, apify_key, log)
                st.session_state.verdicts = {}
            status.success(f"{len(st.session_state.listings)} listings loaded.")

        listings = st.session_state.listings

        if analyze and listings:
            state = "CA"
            prog = st.progress(0.0)
            for i, pl in enumerate(listings):
                lz = _zip_from_latlng(pl) or zip_code
                ctx = _build_context(pl, lz, state, _watchlist(), census_key, log)
                v = calculate_location_verdict(pl, ctx, mapbox_key)
                st.session_state.verdicts[pl.id] = v
                if anthropic_key:
                    cv = claude_vision_analyze(anthropic_key, lz,
                                               f"{pl.address}\n{v.reasoning_simple}\n{v.medical_dental_fit}")
                    if cv:
                        v.reasoning_simple = cv.get("reasoning_simple", v.reasoning_simple)
                        v.verdict = cv.get("verdict", v.verdict)
                log(f"Analyzed {pl.address} → {v.verdict} ({v.score})")
                prog.progress((i + 1) / len(listings))
            status.success("Analysis complete.")

        # --- listings grid with verdict cards ---
        if not listings:
            st.info("Click **Search listings** (sample feed) or add one manually above.")
        for pl in sorted(listings, key=lambda p: -(st.session_state.verdicts.get(p.id).score
                                                   if st.session_state.verdicts.get(p.id) else 0)):
            v = st.session_state.verdicts.get(pl.id)
            badge = (f"<span class='verdict-pill' style='background:{VERDICT_COLORS.get(v.verdict,'#888')}'>"
                     f"{v.verdict} · {v.score}</span>") if v else \
                    "<span class='verdict-pill' style='background:#8e8e93'>Not analyzed</span>"
            price = f"${pl.price:,.0f}/yr" if pl.price else "Price n/a"
            sqft = f"{pl.sqft:,.0f} SF" if pl.sqft else "SF n/a"
            header = f"{pl.address}   —   {price} · {sqft}"
            with st.expander(header):
                st.markdown(badge, unsafe_allow_html=True)
                if not v:
                    st.caption("Run **Analyze all** to score this listing.")
                    continue
                st.markdown(f"**Capture {v.capture_score}%**  ·  "
                            f"{'nearest competitor ' + str(v.drive_time_minutes) + ' min drive' if v.drive_time_minutes else ''}")
                st.markdown("##### 🧭 Verdict reasoning")
                st.write(v.reasoning_simple)
                st.markdown("##### 🚗 Drive-time isochrones")
                st.write(v.isochrone_summary)
                st.markdown("##### 🦷 Medical / dental fit")
                st.write(v.medical_dental_fit)
                st.markdown("##### 💳 Payer & affluence")
                st.write(v.payer_affluence_summary)
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown("##### 🤝 Referral proximity")
                    for name, spec, mi in v.referrals_near:
                        st.caption(f"{name} · {spec} · {mi} mi")
                with cc2:
                    st.markdown("##### ⚔️ Competitor density")
                    for name, tier, mi in v.competitors_near:
                        st.caption(f"{name} · {tier} · {mi} mi")
                cr1, cr2 = st.columns(2)
                cr1.markdown("**Opportunities**"); [cr1.caption("✅ " + o) for o in v.opportunities]
                cr2.markdown("**Risks**"); [cr2.caption("⚠️ " + r) for r in v.risks]
                st.success(f"**Recommendation:** {v.recommendation}")

        # --- map ---
        if listings:
            st.markdown("### 🗺️ Map")
            try:
                import folium
                from streamlit_folium import st_folium
                m = folium.Map(location=[listings[0].lat, listings[0].lng], zoom_start=11,
                               tiles="cartodbpositron")
                for pl in listings:
                    v = st.session_state.verdicts.get(pl.id)
                    color = {"Strong Buy": "green", "Viable": "blue", "Caution": "orange",
                             "Not Recommended": "red"}.get(v.verdict if v else "", "gray")
                    folium.Marker([pl.lat, pl.lng], tooltip=pl.address,
                                  popup=f"{pl.address}<br>{(v.verdict + ' ' + str(v.score)) if v else 'unscored'}",
                                  icon=folium.Icon(color=color, icon="plus-sign")).add_to(m)
                st_folium(m, height=420, use_container_width=True)
            except Exception as e:
                st.caption(f"Map unavailable: {e}")

        # --- export ---
        if st.session_state.verdicts:
            rows = []
            for pl in listings:
                v = st.session_state.verdicts.get(pl.id)
                if v:
                    rows.append({"address": pl.address, "price": pl.price, "sqft": pl.sqft,
                                 "verdict": v.verdict, "score": v.score, "capture_%": v.capture_score,
                                 "recommendation": v.recommendation})
            if rows:
                import pandas as pd
                st.download_button("⬇️ Export shortlist (CSV)",
                                   pd.DataFrame(rows).to_csv(index=False).encode(),
                                   "site_scout_shortlist.csv", "text/csv")

    # ---------------- live logs / status ----------------
    with st.sidebar:
        st.divider()
        st.subheader("Live logs")
        st.code("\n".join(st.session_state.logs[-12:]) or "—", language=None)


def _zip_from_latlng(pl):
    import re as _re
    m = _re.search(r"(\d{5})", pl.address or "")
    return m.group(1) if m else None


def _watchlist():
    try:
        import config
        return config.load_config().get("known_competitors", [])
    except Exception:
        return []


if __name__ == "__main__":
    # When run with `streamlit run site_scout_pro.py`, Streamlit imports this
    # module; guard so a plain `python site_scout_pro.py` gives a helpful hint.
    try:
        import streamlit.runtime.scriptrunner as _sr
        if _sr.get_script_run_ctx() is not None:
            run_app()
        else:
            print("Run with:  streamlit run site_scout_pro.py")
    except Exception:
        run_app()
