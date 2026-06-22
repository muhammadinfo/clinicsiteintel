"""NPPES NPI Registry client — the credential-aware provider source.

This is what fixes the core blind spot of a plain map search: OpenStreetMap
only knows "amenity=dentist", so it surfaces generic DDS offices and misses
the actual specialist competition (orofacial-pain / TMJ / dental-sleep
providers) and the medical referral sources (sleep medicine, ENT,
neurology) entirely.

The US government's NPPES NPI Registry is free, keyless, and searchable by
*taxonomy* — the provider's registered specialty credential. Searching the
"Dentist, Orofacial Pain" taxonomy returns exactly the board-recognized
specialist competitors; searching "Sleep Medicine", "Otolaryngology", etc.
returns the real referral substrate. A registered taxonomy is a stronger
signal than scraping a website for keywords.

Distance is computed against ZIP-code centroids (geocoded once per unique
ZIP via Nominatim and cached) rather than every street address, which keeps
a full report fast.
"""
from dataclasses import dataclass
import math
import os
import sys

import requests

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/"
HEADERS = {"User-Agent": "ClinicSiteIntel/1.0 (clinic-site-assessment-tool)"}


def resource_path(*parts) -> str:
    """Resolve a bundled data file both in dev and under PyInstaller. The
    build adds the app/ folder via --add-data 'app;app', so frozen data lives
    at {_MEIPASS}/app/..., while in dev it's alongside this module."""
    if getattr(sys, "frozen", False):
        base = os.path.join(sys._MEIPASS, "app")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


# Bundled US ZIP -> (lat, lon) centroid table (GeoNames, ~41k ZIPs). Loaded
# once into memory so distance lookups are instant and fully offline — no
# per-ZIP network geocoding, which was the slow part of a full report.
_ZIP_CENTROIDS_FILE = resource_path("assets", "zip_centroids.csv")
_zip_centroid_table: dict = None


def _load_zip_table() -> dict:
    global _zip_centroid_table
    if _zip_centroid_table is not None:
        return _zip_centroid_table
    table = {}
    try:
        with open(_ZIP_CENTROIDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) == 3:
                    try:
                        table[parts[0]] = (float(parts[1]), float(parts[2]))
                    except ValueError:
                        continue
    except Exception:
        table = {}
    _zip_centroid_table = table
    return table


@dataclass
class NppesProvider:
    name: str
    taxonomy_desc: str
    is_specialist: bool          # True for the credentialed specialist competitor taxonomies
    address: str
    city: str
    state: str
    zip5: str
    phone: str | None
    distance_mi: float = None
    enumeration_date: str = ""   # NPI registration date -> tenure
    status: str = "A"            # 'A' active, 'D' deactivated
    tenure_years: float = 0.0
    retire_prob: float = 0.0     # P(provider exits within ~10-yr horizon), proxy
    lat: float = 0.0             # REAL geocoded coords (0 until attach_real_distances)
    lon: float = 0.0


def zip_prefix(zip_code: str) -> str:
    """'91360' -> '913*' wildcard covering the 3-digit postal region."""
    z = (zip_code or "").strip()[:3]
    return f"{z}*" if len(z) == 3 else ""


def _zip_centroid(zip5: str) -> tuple:
    """Look up a ZIP's approximate centroid (lat, lon) from the bundled
    GeoNames table — instant, offline. Returns None if not found."""
    if not zip5:
        return None
    return _load_zip_table().get(zip5[:5])


def _tenure_and_retire(enum_date: str):
    """Years since NPI enumeration, and a proxy probability the provider exits
    within a ~10-year planning horizon. Longer tenure → nearer career end →
    higher exit probability (a long-tenured dominant competitor is a
    depreciating threat). No DOB in NPPES, so tenure is the available proxy."""
    import datetime
    try:
        y = int(enum_date[:4])
        tenure = max(0.0, datetime.date.today().year - y)
    except Exception:
        return 0.0, 0.0
    # logistic on tenure: ~5% at 5 yrs, ~25% at 20 yrs, ~55% at 32+ yrs
    p = 1.0 / (1.0 + math.exp(-0.16 * (tenure - 26.0)))
    return round(tenure, 1), round(p, 3)


def _parse_provider(raw: dict, taxonomy_label: str, is_specialist: bool) -> NppesProvider:
    b = raw.get("basic", {})
    name = b.get("organization_name") or f"{b.get('first_name','')} {b.get('last_name','')}".strip()
    # Prefer the provider's actual primary taxonomy description when present.
    prim = next((t["desc"] for t in raw.get("taxonomies", []) if t.get("primary")), taxonomy_label)
    loc = next((a for a in raw.get("addresses", []) if a.get("address_purpose") == "LOCATION"), {})
    enum_date = b.get("enumeration_date", "") or ""
    tenure, retire = _tenure_and_retire(enum_date)
    return NppesProvider(
        name=name or "Unknown",
        taxonomy_desc=prim,
        is_specialist=is_specialist,
        address=loc.get("address_1", ""),
        city=loc.get("city", ""),
        state=loc.get("state", ""),
        zip5=(loc.get("postal_code", "") or "")[:5],
        phone=loc.get("telephone_number"),
        enumeration_date=enum_date,
        status=b.get("status", "A") or "A",
        tenure_years=tenure,
        retire_prob=retire,
    )


def _paged_results(base_params: dict, max_results: int = 600) -> list:
    """Page through the NPPES result set in batches of 200 via the `skip`
    offset (skip = 0, 200, 400 … up to the API's max of 1000), so we retrieve
    the COMPLETE provider universe instead of being capped at one 200-row page.
    NPPES allows skip ≤ 1000 + limit 200 ⇒ ~1,200 results max."""
    out = []
    skip = 0
    while skip <= 1000 and len(out) < max_results:
        p = dict(base_params)
        p["limit"] = 200
        p["skip"] = skip
        try:
            r = requests.get(NPPES_URL, params=p, headers=HEADERS, timeout=20)
            r.raise_for_status()
            res = r.json().get("results", [])
        except Exception:
            break
        if not res:
            break
        out.extend(res)
        if len(res) < 200:        # last (partial) page reached
            break
        skip += 200
    return out[:max_results]


def search_by_taxonomy(taxonomy_description: str, zip5: str, state: str = "",
                       is_specialist: bool = False, limit: int = 50,
                       max_results: int = 600) -> list:
    """Search NPPES for providers of a given taxonomy in the ZIP's 3-digit
    postal region, paginated past the 200-row cap. Returns NppesProvider list."""
    prefix = zip_prefix(zip5)
    if not prefix:
        return []
    base = {"version": "2.1", "taxonomy_description": taxonomy_description, "postal_code": prefix}
    if state:
        base["state"] = state
    return [_parse_provider(raw, taxonomy_description, is_specialist)
            for raw in _paged_results(base, max_results)]


def nearby_zip_prefixes(clinic_lat: float, clinic_lon: float, radius_mi: float = 18.0) -> list:
    """3-digit postal prefixes of all ZIPs whose centroid is within radius of
    the clinic — so NPPES searches are genuinely RADIUS-based around the actual
    address, catching specialists across 3-digit-region boundaries rather than
    only the address's own ZIP region."""
    from geocode import haversine_miles
    table = _load_zip_table()
    prefixes = set()
    for z, (la, lo) in table.items():
        if abs(la - clinic_lat) > 0.5 or abs(lo - clinic_lon) > 0.6:
            continue  # cheap bounding-box prefilter before haversine
        if haversine_miles(clinic_lat, clinic_lon, la, lo) <= radius_mi:
            prefixes.add(z[:3])
    return sorted(prefixes)


def search_by_taxonomy_near(taxonomy_description: str, clinic_lat: float, clinic_lon: float,
                            radius_mi: float = 18.0, state: str = "",
                            is_specialist: bool = False, limit: int = 100,
                            max_results_per_prefix: int = 400) -> list:
    """Radius-based taxonomy search: query every nearby 3-digit prefix (each
    paginated past the 200-cap) and dedupe, so results reflect the specific
    address AND the complete provider universe."""
    seen, out = set(), []
    for prefix in nearby_zip_prefixes(clinic_lat, clinic_lon, radius_mi):
        base = {"version": "2.1", "taxonomy_description": taxonomy_description,
                "postal_code": f"{prefix}*"}
        if state:
            base["state"] = state
        for raw in _paged_results(base, max_results_per_prefix):
            p = _parse_provider(raw, taxonomy_description, is_specialist)
            key = (p.name.lower(), p.zip5)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def attach_distances(providers: list, clinic_lat: float, clinic_lon: float) -> list:
    """Fill distance_mi on each provider using cached ZIP centroids (coarse —
    every provider in a ZIP collapses to one point). Use attach_real_distances
    for accuracy; this stays as the instant fallback / prefilter."""
    from geocode import haversine_miles
    for p in providers:
        centroid = _zip_centroid(p.zip5)
        if centroid:
            p.distance_mi = round(haversine_miles(clinic_lat, clinic_lon, centroid[0], centroid[1]), 1)
    return providers


def attach_real_distances(providers: list, clinic_lat: float, clinic_lon: float,
                          limit: int = None) -> list:
    """Precise distances: geocode each provider's REAL street address (cached)
    and measure from there, falling back to the ZIP centroid only when the
    street address won't geocode. This fixes the ZIP-centroid artifact where a
    provider 2-3 mi away reported the same ~0.6 mi as the ZIP centroid.

    `limit` (closest-by-centroid first) caps how many are precision-geocoded so
    a full report stays fast; the remainder keep their centroid distance. Also
    stamps each precisely-located provider's real lat/lon for the spatial models."""
    from geocode import haversine_miles, geocode_oneline
    # 1) instant centroid pass — gives ordering and the fallback distance
    attach_distances(providers, clinic_lat, clinic_lon)
    ordered = sorted((p for p in providers if p.distance_mi is not None),
                     key=lambda p: p.distance_mi)
    subset = ordered if limit is None else ordered[:limit]
    for p in subset:
        if not p.address:
            continue
        one = ", ".join(x for x in (p.address, p.city, p.state, p.zip5) if x)
        coords = geocode_oneline(one)
        if coords:
            p.lat, p.lon = coords
            p.distance_mi = round(haversine_miles(clinic_lat, clinic_lon, coords[0], coords[1]), 2)
    return providers
