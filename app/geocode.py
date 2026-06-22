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

    @property
    def geoid_tract(self) -> str:
        return f"{self.state_fips}{self.county_fips}{self.tract}"


def geocode_address(address: str) -> GeoResult:
    params = {
        "address": address,
        "benchmark": "4",       # Public_AR_Current
        "vintage": "4",         # Current_Current
        "format": "json",
    }
    r = requests.get(CENSUS_GEOCODE_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        raise ValueError(
            f"Could not geocode address: {address!r}. "
            "Check spelling, or add city/state/ZIP for a better match."
        )
    m = matches[0]
    coords = m["coordinates"]
    geographies = m.get("geographies", {})

    tract_key = next((k for k in geographies if "Census Tracts" in k), None)
    tract_info = geographies.get(tract_key, [{}])[0] if tract_key else {}

    matched_address = m.get("matchedAddress", address)
    # The ZCTA geography layer isn't reliably present on every benchmark/vintage
    # combination, but the matched address string always includes the ZIP —
    # pull it directly from there instead of depending on that layer.
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", matched_address.strip())
    zip_code = zip_match.group(1) if zip_match else ""

    return GeoResult(
        matched_address=matched_address,
        lat=float(coords["y"]),
        lon=float(coords["x"]),
        zip_code=zip_code,
        state_fips=tract_info.get("STATE", ""),
        county_fips=tract_info.get("COUNTY", ""),
        tract=tract_info.get("TRACT", ""),
        block=tract_info.get("BLOCK", ""),
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
