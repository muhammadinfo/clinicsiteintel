"""Google Places API (New) client — places.googleapis.com/v1.

Replaces the RETIRED legacy endpoints (maps.googleapis.com/maps/api/place/...),
which Google no longer serves on newly-provisioned projects — those return
`REQUEST_DENIED: "The provided API key is invalid."` no matter how the key is
configured. The new API uses POST + an X-Goog-Api-Key header + a required
X-Goog-FieldMask, and returns website/phone inline (no separate Details call).

Requires the "Places API (New)" to be enabled on the project + active billing.
"""
import requests

TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Field mask is REQUIRED by the new API; ask for everything the app uses.
_FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.location", "places.rating", "places.userRatingCount",
    "places.websiteUri", "places.nationalPhoneNumber",
    "places.primaryTypeDisplayName", "places.types",
])


class PlacesError(RuntimeError):
    """Carries the real Google error message so the UI can show it instead of
    silently falling back to free mode."""


def _headers(api_key):
    return {"Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": _FIELD_MASK}


def _normalize(p):
    loc = p.get("location") or {}
    return {
        "place_id": p.get("id"),
        "name": (p.get("displayName") or {}).get("text", ""),
        "address": p.get("formattedAddress", ""),
        "lat": loc.get("latitude", 0.0),
        "lon": loc.get("longitude", 0.0),
        "rating": p.get("rating"),
        "user_ratings_total": p.get("userRatingCount"),
        "website": p.get("websiteUri"),
        "phone": p.get("nationalPhoneNumber"),
        "types": p.get("types", []),
        "primary_type": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
    }


def _post(url, api_key, body):
    if not api_key:
        raise PlacesError("No Google Places API key configured.")
    try:
        r = requests.post(url, headers=_headers(api_key), json=body, timeout=25)
    except requests.RequestException as e:
        raise PlacesError(f"network error: {e.__class__.__name__}")
    if r.status_code != 200:
        msg = ""
        try:
            msg = (r.json().get("error") or {}).get("message", "") or r.text[:200]
        except Exception:
            msg = (r.text or "")[:200]
        raise PlacesError(f"HTTP {r.status_code}: {msg}")
    return [_normalize(p) for p in (r.json().get("places") or [])]


def text_search(api_key, query, lat, lon, radius_m=12000, max_results=20):
    """Keyword/text search (replaces legacy nearbysearch?keyword=)."""
    body = {
        "textQuery": query,
        "maxResultCount": int(max_results),
        "locationBias": {"circle": {
            "center": {"latitude": lat, "longitude": lon},
            "radius": float(min(radius_m, 50000))}},
    }
    return _post(TEXT_URL, api_key, body)


def nearby(api_key, lat, lon, radius_m, included_types, max_results=20):
    """Type-filtered nearby search (used for the building-directory pull)."""
    body = {
        "includedTypes": list(included_types),
        "maxResultCount": int(max_results),
        "locationRestriction": {"circle": {
            "center": {"latitude": lat, "longitude": lon},
            "radius": float(min(radius_m, 50000))}},
    }
    return _post(NEARBY_URL, api_key, body)
