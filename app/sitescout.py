"""Site Scout backend — listing ingestion + per-listing verdict engine,
integrated into the ClinicSiteIntel desktop app (no separate front-end).

Reuses the validated engine (geocode, demographics, epi, nppes, competitors,
referrals, spatial, econ). Adds:
  - PropertyListing ingestion (manual paste + geocoded sample feed + optional
    Apify LoopNet/Crexi; the sites have no public API and block scraping, so
    live pulls require a paid Apify token),
  - drive-time isochrones (Mapbox key -> OSRM fallback -> Haversine proxy),
  - a 4-tier verdict (Strong Buy / Viable / Caution / Not Recommended) with
    plain-English reasoning, payer/affluence, referral proximity, competitor
    density, risks/opportunities,
  - an optional Claude Vision read using the embedded Site Scout master prompt.
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field

import requests

import geocode
import demographics
import epi
import nppes
import competitors as comp_mod
import referrals as ref_mod
import spatial
import econ

HAVERSINE = geocode.haversine_miles


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
    verdict: str
    score: int
    capture_score: float
    drive_time_minutes: float | None
    isochrone_summary: str
    reasoning_simple: str
    medical_dental_fit: str
    payer_affluence_summary: str
    recommendation: str
    risks: list
    opportunities: list
    referrals_near: list = field(default_factory=list)
    competitors_near: list = field(default_factory=list)


# ---------------------------------------------------------------- listings
SAMPLE_LISTINGS = [
    ("2220 Lynn Rd, Thousand Oaks, CA 91360", 42.0, 1100, "Sample"),
    ("179 Auburn Ct, Westlake Village, CA 91362", 18.0, 1800, "Sample"),
    ("29525 Canwood St, Agoura Hills, CA 91301", None, 1500, "Sample"),
    ("24011 Ventura Blvd, Calabasas, CA 91302", None, 2000, "Sample"),
]


def geocode_listing(addr: str, rate_per_sf=None, sqft=None, source="Manual") -> PropertyListing | None:
    coords = geocode.geocode_oneline(addr)
    if not coords:
        return None
    price = (rate_per_sf * sqft) if (rate_per_sf and sqft) else None
    return PropertyListing(id=str(abs(hash(addr)) % 10_000_000), address=addr,
                           lat=coords[0], lng=coords[1], price=price, sqft=sqft,
                           source=source, days_on_market=14)


def listings_from_records(records, log=lambda m: None) -> list[PropertyListing]:
    """Build PropertyListings from scouted records — used by the bulk-import
    bridge (browser-read or manual entry). Each record is a dict with at least
    'address'; optional price/sqft/source/url/days_on_market. Geocoded here."""
    out = []
    n = len(records)
    for i, r in enumerate(records):
        addr = (r.get("address") or "").strip()
        if not addr:
            continue
        log(f"Geocoding {i+1}/{n}: {addr}")
        pl = geocode_listing(addr, source=r.get("source", "Scouted"))
        if not pl:
            log(f"Could not geocode: {addr}")
            continue
        if r.get("price") is not None:
            pl.price = r["price"]
        if r.get("sqft") is not None:
            pl.sqft = r["sqft"]
        if r.get("url"):
            pl.url = r["url"]
        if r.get("days_on_market") is not None:
            pl.days_on_market = r["days_on_market"]
        out.append(pl)
    return [p for p in out if (p.days_on_market is None or p.days_on_market <= 60)]


def parse_bulk_lines(text: str) -> list[dict]:
    """Parse a textarea where each line is:  address | price | sqft | source
    (only address required). Powers the bulk-import bridge."""
    recs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        rec = {"address": parts[0], "source": "Scouted"}
        if len(parts) > 1 and parts[1]:
            try:
                rec["price"] = float(parts[1].replace("$", "").replace(",", "").replace("/yr", ""))
            except ValueError:
                pass
        if len(parts) > 2 and parts[2]:
            try:
                rec["sqft"] = float(parts[2].replace(",", "").replace("SF", "").strip())
            except ValueError:
                pass
        if len(parts) > 3 and parts[3]:
            rec["source"] = parts[3]
        recs.append(rec)
    return recs


def fetch_recent_listings(zip_code: str, radius_miles: float = 10.0,
                          apify_token: str = "", log=lambda m: None) -> list[PropertyListing]:
    """Listings <= 60 days on market within radius. Apify (paid) if a token is
    given; otherwise a geocoded sample feed (LoopNet/Crexi block scraping)."""
    out: list[PropertyListing] = []
    if apify_token:
        log("Apify token present — calling LoopNet/Crexi actors…")
        try:
            out = _fetch_via_apify(zip_code, radius_miles, apify_token, log)
        except Exception as e:
            log(f"Apify call failed ({e}); using sample feed.")
    if not out:
        log("Using geocoded sample listing feed (no live source configured).")
        center = nppes._zip_centroid(zip_code)
        for addr, rate, sqft, src in SAMPLE_LISTINGS:
            pl = geocode_listing(addr, rate, sqft, src)
            if pl and (center is None or
                       HAVERSINE(center[0], center[1], pl.lat, pl.lng) <= max(radius_miles, 25)):
                out.append(pl)
    return [p for p in out if (p.days_on_market is None or p.days_on_market <= 60)]


def _fetch_via_apify(zip_code, radius_miles, token, log) -> list[PropertyListing]:
    try:
        from apify_client import ApifyClient
    except Exception:
        log("apify-client not installed (pip install apify-client).")
        return []
    client = ApifyClient(token)
    results = []
    for actor_id, source in (("epctex/loopnet-scraper", "LoopNet"),
                             ("epctex/crexi-scraper", "Crexi")):
        try:
            run = client.actor(actor_id).call(run_input={"search": zip_code, "maxItems": 40})
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
                    lat=float(lat), lng=float(lng), price=it.get("price"),
                    sqft=it.get("sqft") or it.get("squareFeet"), source=source,
                    days_on_market=dom, url=it.get("url")))
            log(f"{source}: {len(results)} listings via Apify.")
        except Exception as e:
            log(f"{source} actor failed: {e}")
    return results


# ---------------------------------------------------------------- isochrones
def drive_time_minutes(from_lat, from_lng, to_lat, to_lng, mapbox_token="") -> float | None:
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


def isochrone_population(lat, lng, demand_points, mapbox_token="") -> dict:
    buckets = {15: 0.0, 30: 0.0, 45: 0.0}
    for dp in demand_points:
        mins = drive_time_minutes(lat, lng, dp.lat, dp.lon, mapbox_token)
        if mins is None:
            mins = HAVERSINE(lat, lng, dp.lat, dp.lon) / 28.0 * 60.0
        for thr in (15, 30, 45):
            if mins <= thr:
                buckets[thr] += dp.population
    return {k: round(v) for k, v in buckets.items()}


# ---------------------------------------------------------------- context
def nppes_neighbor_zips(zip5):
    try:
        base = int(zip5)
    except (TypeError, ValueError):
        return []
    return [str(base + d) for d in (-3, -2, -1, 1, 2, 3)]


def build_context(listing: PropertyListing, zip5, state, known_competitors, census_key, log=lambda m: None):
    lat, lng = listing.lat, listing.lng
    ctx = {}
    try:
        ctx["zcta"] = demographics.get_zcta_profile(zip5, census_key) if zip5 else None
    except Exception as e:
        ctx["zcta"] = None
        log(f"ACS pull failed: {e}")
    demand = []
    osa = 1.0
    try:
        g = geocode.geocode_address(listing.address)
        osa = epi.get_places_osa_base(g.geoid_tract).get("osa_index", 1.0) or 1.0
    except Exception:
        osa = 1.0
    fshare = (ctx["zcta"].female_share if ctx.get("zcta") and ctx["zcta"].female_share else 0.51)
    for z in [zip5] + nppes_neighbor_zips(zip5):
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
    try:
        scan = comp_mod.run_full_competitor_scan("", lat, lng, zip5=zip5, state=state,
                                                 known_competitors=known_competitors)
        ctx["competitors"] = [c.__dict__ for c in scan["competitors"]]
    except Exception as e:
        ctx["competitors"] = []; log(f"competitor scan failed: {e}")
    try:
        ctx["referrals"] = ref_mod.find_referral_candidates("", lat, lng, zip5=zip5, state=state,
                                                            target_addr=listing.address)
    except Exception as e:
        ctx["referrals"] = []; log(f"referral scan failed: {e}")
    return ctx


def _econ_clears(captured_cases) -> bool:
    er = econ.proforma(captured_cases or 0)
    return er.projected_cases >= er.break_even_cases


def calculate_location_verdict(listing: PropertyListing, ctx: dict, mapbox_token="") -> LocationVerdict:
    demand = ctx.get("demand", [])
    comps = ctx.get("competitors", [])
    refs = ctx.get("referrals", [])
    zcta = ctx.get("zcta")

    def _last_zip(s):
        z = re.findall(r"\b\d{5}\b", s or "")
        return z[-1] if z else None

    facilities = []
    for c in comps:
        if not (c.get("competition_score", 0) > 0):
            continue
        flat, flng = c.get("lat", 0), c.get("lon", 0)
        if not flat or not flng:
            z = _last_zip(c.get("address", ""))
            cen = nppes._zip_centroid(z) if z else None
            if cen:
                flat, flng = cen
        if flat and flng:
            sc = float(c.get("competition_score", 0) or 0)
            facilities.append(spatial.Facility(c.get("name", ""), flat, flng, sc,
                                               retire_prob=float(c.get("retire_prob", 0) or 0),
                                               capacity=max(0.1, sc / 90.0)))
    sp = spatial.compute_all(listing.lat, listing.lng, facilities, demand, clinic_attractiveness=70.0)
    capture = sp.huff_share_pct if sp.ok else 0.0

    iso = isochrone_population(listing.lat, listing.lng, demand, mapbox_token)
    spec = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
    nearest_spec_mi = min([c.get("distance_mi") for c in spec if c.get("distance_mi") is not None], default=None)
    nearest_comp_dt = None
    if spec:
        nearest = min(spec, key=lambda c: c.get("distance_mi") or 999)
        nlat, nlng = nearest.get("lat", 0), nearest.get("lon", 0)
        if not nlat or not nlng:
            z = _last_zip(nearest.get("address", ""))
            cen = nppes._zip_centroid(z) if z else None
            if cen:
                nlat, nlng = cen
        if nlat and nlng:
            nearest_comp_dt = drive_time_minutes(listing.lat, listing.lng, nlat, nlng, mapbox_token)

    income = zcta.median_household_income if zcta else None
    affluence = epi.cashpay_propensity(income) if income else 0.5
    payer_summary = (
        f"Median household income ${income:,.0f} → cash-pay/PPO propensity {affluence*100:.0f}%. "
        + ("Affluent, fee-for-service-friendly market." if affluence >= 0.6 else
           "Mixed affluence — verify commercial-insurance density." if affluence >= 0.4 else
           "Lower-affluence/Medicaid-leaning — weaker for elective cash-pay.")
        if income else "Income data unavailable for payer/affluence read.")

    n_md = sum(1 for r in refs if str(getattr(r, "category", "")).startswith("Physician"))
    refs_near = sorted([(r.name, r.specialty, r.distance_mi) for r in refs if r.distance_mi is not None],
                       key=lambda x: x[2])[:10]
    comps_near = sorted([(c.get("name"), c.get("tier"), c.get("distance_mi")) for c in comps
                         if c.get("distance_mi") is not None],
                        key=lambda x: x[2] if x[2] is not None else 999)[:10]
    n_spec = len(spec)

    # Score on the SAME Location Viability Index the full Summary report uses, so
    # Site Scout and the Summary never disagree for the same address. (Previously
    # Site Scout had its own capture-dominated formula, which produced e.g.
    # "Caution 46" while the Summary said "PURSUE 72" for the same site.)
    import lvi
    ref_dicts = [r.__dict__ if hasattr(r, "__dict__") else r for r in refs]
    income_v = zcta.median_household_income if zcta else None
    age_v = getattr(zcta, "median_age", None) if zcta else None
    pop_v = getattr(zcta, "population", None) if zcta else None
    ds = lvi.derive_ds_from_demographics(income_v, age_v, pop_v)
    rp = lvi.derive_rp_from_referrals(ref_dicts)
    if_ = lvi.derive_if_from_medical_hub(ref_dicts, comps)
    comp_pairs = [(c.get("competition_score", 0), c.get("distance_mi"))
                  for c in comps if c.get("competition_score", 0) > 0]
    cp = lvi.derive_cp_from_competitors_v2(comp_pairs)
    score = int(round(lvi.calc_lvi(ds, rp, if_, cp, 50.0, 50.0)))
    # Verdict bands aligned to the Summary's LVI bands (PURSUE >=65, conditions >=50).
    verdict = ("Strong Buy" if score >= 65 else "Viable" if score >= 50
               else "Caution" if score >= 38 else "Not Recommended")

    near_txt = (f"the nearest competitor ~{nearest_spec_mi:.0f} mi away"
                if nearest_spec_mi is not None else "no credentialed specialist nearby")
    reasoning = (f"This site captures ~{capture:.0f}% of local TMJ/OSA demand, with {n_md} physician "
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
    return LocationVerdict(verdict=verdict, score=score, capture_score=round(capture, 1),
                           drive_time_minutes=nearest_comp_dt, isochrone_summary=iso_summary,
                           reasoning_simple=reasoning, medical_dental_fit=fit,
                           payer_affluence_summary=payer_summary, recommendation=rec,
                           risks=risks, opportunities=opps,
                           referrals_near=refs_near, competitors_near=comps_near)


# ---------------------------------------------------------------- Claude Vision
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


def claude_vision_analyze(anthropic_key, zip_code, context_text, screenshot_path=None) -> dict | None:
    if not anthropic_key:
        return None
    try:
        import anthropic
    except Exception:
        return None
    client = anthropic.Anthropic(api_key=anthropic_key)
    content = [{"type": "text", "text": SITE_SCOUT_MASTER_PROMPT.format(ZIP_CODE=zip_code)
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
        s, e = txt.find("{"), txt.rfind("}")
        return json.loads(txt[s:e + 1]) if s >= 0 else None
    except Exception:
        return None
