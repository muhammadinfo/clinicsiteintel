"""Free, no-key nearby-POI search via OpenStreetMap's Overpass API.

Default discovery source for competitors.py and referrals.py — no
account, no API key, no billing required. Coverage and metadata (no
review counts/ratings, website/phone only present when a mapper added
them) are weaker than Google Places, but for this app's actual job —
finding a practice, then fetching ITS OWN WEBSITE to verify what it
really does — that's enough: the Overpass result is just a seed list of
names/addresses/coordinates, and the real signal comes from the website
scan that runs afterward regardless of which directory found the lead.

If the user later adds a Google Places API key, competitors.py/referrals.py
use that instead (richer results, ratings, but billing-gated).
"""
from dataclasses import dataclass
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "ClinicSiteIntel/1.0 (clinic-site-assessment-tool)"}


@dataclass
class OsmPlace:
    name: str
    lat: float
    lon: float
    address: str
    website: str | None
    phone: str | None
    raw_tags: dict


def _format_address(tags: dict) -> str:
    parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]
    street = " ".join(p for p in parts if p)
    city_parts = [tags.get("addr:city", ""), tags.get("addr:state", ""), tags.get("addr:postcode", "")]
    city = " ".join(p for p in city_parts if p)
    full = ", ".join(p for p in (street, city) if p)
    return full


def query_overpass(tag_filters: list[str], lat: float, lon: float, radius_m: int = 12000) -> list[OsmPlace]:
    """tag_filters: list of Overpass tag-filter strings, e.g. ['["amenity"="dentist"]', '["healthcare"="dentist"]']"""
    clauses = "".join(f'node(around:{radius_m},{lat},{lon}){tf};' for tf in tag_filters)
    query = f"[out:json][timeout:30];({clauses});out center;"
    r = requests.post(OVERPASS_URL, data=query.encode("utf-8"), headers=HEADERS, timeout=45)
    r.raise_for_status()
    data = r.json()
    places = []
    seen_names_coords = set()
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue  # unnamed nodes aren't useful as competitor/referral leads
        lat_, lon_ = el.get("lat"), el.get("lon")
        key = (name, round(lat_ or 0, 4), round(lon_ or 0, 4))
        if key in seen_names_coords:
            continue
        seen_names_coords.add(key)
        places.append(OsmPlace(
            name=name,
            lat=lat_ or 0.0,
            lon=lon_ or 0.0,
            address=_format_address(tags),
            website=tags.get("website") or tags.get("contact:website"),
            phone=tags.get("phone") or tags.get("contact:phone"),
            raw_tags=tags,
        ))
    return places
