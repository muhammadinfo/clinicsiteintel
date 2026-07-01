"""Address geocoding + reverse lookup of Census geography (tract / ZCTA).

Uses the US Census Bureau's free public geocoder — no API key required,
high reliability, and it returns the Census tract/block/ZCTA needed to
pull matching ACS demographic data in demographics.py.
"""
from dataclasses import dataclass
import re
import requests

CENSUS_GEOCODE_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


@dataclass
class GeoResult:
    matched_address: str
    lat: float
    lon: float
    zip_code: str
    state_fips: str
    county_fips: str
    tract: str
    block: str
    match_warning: str = ""   # set when the resolved address differs from input

    @property
    def geoid_tract(self) -> str:
        return f"{self.state_fips}{self.county_fips}{self.tract}"


def _extract_zip(formatted_address: str) -> str:
    """Extract the ZIP from a formatted address like 'STREET, CITY, ST ZIP,
    COUNTRY'. The street NUMBER (e.g. '22030 Sherman Way') is also a 5-digit
    string and comes FIRST — an unanchored/first-match search grabs the house
    number instead of the ZIP. Anchor on 'ST #####' (state code immediately
    before the ZIP); fall back to the LAST 5-digit group in the string."""
    m = re.search(r"\b[A-Z]{2}\s+(\d{5})(?:-\d{4})?\b", formatted_address or "")
    if m:
        return m.group(1)
    all_5 = re.findall(r"\b(\d{5})(?:-\d{4})?\b", formatted_address or "")
    return all_5[-1] if all_5 else ""


def _house_num(a: str) -> str:
    m = re.match(r"\s*(\d+)", (a or "").strip())
    return m.group(1) if m else ""


def _census_geography_at(lat: float, lon: float) -> dict:
    """Reverse-lookup the Census tract/block for accurate (e.g. Google) coords —
    needed to pull matching ACS demographics regardless of which geocoder we used."""
    try:
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
            params={"x": lon, "y": lat, "benchmark": "4", "vintage": "4", "format": "json"},
            timeout=20)
        g = r.json().get("result", {}).get("geographies", {})
        tk = next((k for k in g if "Census Tracts" in k), None)
        return g.get(tk, [{}])[0] if tk else {}
    except Exception:
        return {}


def _google_geocode(address: str):
    """Accurate geocode via Places API (New), if a key is configured. Returns a
    normalized place dict or None (no key / not found / error)."""
    try:
        import config
        key = (config.load_config() or {}).get("google_places_api_key", "")
    except Exception:
        key = ""
    if not key:
        return None
    try:
        import google_places_v1 as gp
        res = gp.geocode_text(key, address, max_results=1)
    except Exception:
        return None
    if res and res[0].get("lat") and res[0].get("lon"):
        return res[0]
    return None


def geocode_address(address: str) -> GeoResult:
    in_num = _house_num(address)

    # 1) Google (Places API New) — most accurate. Only trust it when the resolved
    #    house number matches what was entered (else fall through to Census).
    g = _google_geocode(address)
    if g and (not in_num or _house_num(g.get("address", "")) == in_num):
        lat, lon = float(g["lat"]), float(g["lon"])
        ti = _census_geography_at(lat, lon)
        matched = g.get("address", address)
        zip_code = _extract_zip(matched)
        return GeoResult(
            matched_address=matched, lat=lat, lon=lon,
            zip_code=zip_code,
            state_fips=ti.get("STATE", ""), county_fips=ti.get("COUNTY", ""),
            tract=ti.get("TRACT", ""), block=ti.get("BLOCK", ""))

    # 2) US Census geocoder (free fallback / no key), now with match VALIDATION.
    r = requests.get(CENSUS_GEOCODE_URL,
                     params={"address": address, "benchmark": "4", "vintage": "4", "format": "json"},
                     timeout=20)
    r.raise_for_status()
    matches = r.json().get("result", {}).get("addressMatches", [])
    if not matches:
        raise ValueError(
            f"Could not geocode address: {address!r}. "
            "Check spelling, or add city/state/ZIP for a better match."
        )
    # Prefer a candidate whose house number matches the input over a blind matches[0].
    m = next((mm for mm in matches if in_num and _house_num(mm.get("matchedAddress", "")) == in_num),
             matches[0])
    coords = m["coordinates"]
    geographies = m.get("geographies", {})
    tract_key = next((k for k in geographies if "Census Tracts" in k), None)
    tract_info = geographies.get(tract_key, [{}])[0] if tract_key else {}
    matched_address = m.get("matchedAddress", address)
    zip_code = _extract_zip(matched_address)

    warning = ""
    out_num = _house_num(matched_address)
    if in_num and out_num and in_num != out_num:
        warning = (f"Address mismatch: you entered number {in_num}, but the closest match found was "
                   f"“{matched_address}”. The report may describe a NEARBY location — verify the "
                   "address (add the ZIP, or a Google Places key gives exact matching).")

    return GeoResult(
        matched_address=matched_address,
        lat=float(coords["y"]),
        lon=float(coords["x"]),
        zip_code=zip_code,
        state_fips=tract_info.get("STATE", ""),
        county_fips=tract_info.get("COUNTY", ""),
        tract=tract_info.get("TRACT", ""),
        block=tract_info.get("BLOCK", ""),
        match_warning=warning,
    )


_oneline_cache: dict = {}


def geocode_oneline(address: str):
    """Lightweight geocode of an arbitrary one-line address to (lat, lon).
    Used to place competitors at their REAL coordinates instead of a coarse
    ZIP centroid. Cached; returns None on miss."""
    if not address:
        return None
    if address in _oneline_cache:
        return _oneline_cache[address]
    result = None
    # 1) US Census geocoder (precise, but strict about formatting).
    try:
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": address, "benchmark": "4", "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            result = (float(c["y"]), float(c["x"]))
    except Exception:
        result = None
    # 2) Fallback: OpenStreetMap Nominatim — tolerant of free-form, browser-
    #    copied addresses (missing commas, suite text, etc.).
    if result is None:
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
                headers={"User-Agent": "ClinicSiteIntel/1.0 (clinic-site-assessment)"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data:
                result = (float(data[0]["lat"]), float(data[0]["lon"]))
        except Exception:
            result = None
    _oneline_cache[address] = result
    return result


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
