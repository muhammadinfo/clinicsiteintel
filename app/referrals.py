"""Referral-partner discovery: finds nearby practices most likely to feed
or receive patients for a TMJ/orofacial-pain/dental-sleep clinic, ranked
by specialty fit and proximity. Free by default via OpenStreetMap Overpass
(no key, no billing); uses Google Places instead if the user has added
their own API key in Settings (richer metadata, ratings).
"""
from dataclasses import dataclass, field
import requests

import overpass

PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

# (search keyword, specialty label, referral-fit weight 1-10) — Google Places path
REFERRAL_TARGETS = [
    ("primary care physician", "Primary Care / Internal Medicine", 8),
    ("family medicine clinic", "Family Medicine", 8),
    ("sleep medicine clinic", "Sleep Medicine", 10),
    ("otolaryngologist ENT", "Otolaryngology (ENT)", 9),
    ("neurologist", "Neurology (headache crossover)", 6),
    ("physical therapy", "Physical Therapy (TMD co-management)", 7),
    ("orthodontist", "Orthodontics (occlusion/airway crossover)", 6),
    ("general dentist", "General Dentistry (referral-out source)", 5),
    ("oral surgeon", "Oral & Maxillofacial Surgery", 7),
    ("chiropractor", "Chiropractic (myofascial crossover)", 4),
]

# (OSM tag filter, specialty label, referral-fit weight 1-10) — free Overpass path
OSM_REFERRAL_TARGETS = [
    ('["amenity"="doctors"]["healthcare:speciality"="general_internal_medicine"]', "Primary Care / Internal Medicine", 8),
    ('["amenity"="doctors"]["healthcare:speciality"="family_practice"]', "Family Medicine", 8),
    ('["healthcare:speciality"="sleep_medicine"]', "Sleep Medicine", 10),
    ('["healthcare:speciality"="otolaryngology"]', "Otolaryngology (ENT)", 9),
    ('["healthcare:speciality"="neurology"]', "Neurology (headache crossover)", 6),
    ('["healthcare"="physiotherapist"]', "Physical Therapy (TMD co-management)", 7),
    ('["healthcare:speciality"="orthodontics"]', "Orthodontics (occlusion/airway crossover)", 6),
    ('["amenity"="dentist"]', "General Dentistry (referral-out source)", 5),
    ('["healthcare:speciality"="oral_and_maxillofacial_surgery"]', "Oral & Maxillofacial Surgery", 7),
    ('["healthcare"="chiropractor"]', "Chiropractic (myofascial crossover)", 4),
    ('["amenity"="doctors"]', "General Physician (unspecified specialty)", 4),
]


@dataclass
class ReferralCandidate:
    name: str
    specialty: str
    address: str
    lat: float
    lon: float
    place_id: str
    rating: float | None
    user_ratings_total: int | None
    distance_mi: float | None = None
    fit_weight: int = 0
    referral_score: float = 0.0
    category: str = "Physician (MD/DO)"   # or "Dentist — non-competitor"
    phone: str | None = None


# NPPES physician taxonomies — "MDs of all specialties in the area." The most
# referral-relevant specialties (sleep medicine, ENT, neurology, primary care)
# carry the highest fit weight; the rest round out full-specialty coverage.
# (taxonomy_description, label, fit weight 1-10)
NPPES_REFERRAL_TAXONOMIES = [
    ("Sleep Medicine", "Sleep Medicine (OSA dx → oral appliance)", 10),
    ("Otolaryngology", "Otolaryngology / ENT (airway, snoring)", 9),
    ("Neurology", "Neurology (headache / facial-pain crossover)", 8),
    ("Internal Medicine", "Internal Medicine / Primary Care", 8),
    ("Family Medicine", "Family Medicine", 8),
    ("Physical Medicine & Rehabilitation", "Physical Medicine & Rehab (chronic pain)", 7),
    ("Pain Medicine", "Pain Medicine", 7),
    ("Psychiatry & Neurology", "Psychiatry & Neurology", 6),
    ("Rheumatology", "Rheumatology (TMJ arthritis crossover)", 6),
    ("Pediatrics", "Pediatrics (pediatric OSA screening)", 5),
    ("Obstetrics & Gynecology", "Obstetrics & Gynecology", 4),
    ("Cardiovascular Disease", "Cardiology (OSA comorbidity)", 5),
    ("Pulmonary Disease", "Pulmonology (OSA comorbidity)", 6),
    ("Orthopaedic Surgery", "Orthopaedic Surgery", 4),
    ("Dermatology", "Dermatology", 3),
    ("Gastroenterology", "Gastroenterology", 3),
    ("Endocrinology, Diabetes & Metabolism", "Endocrinology", 4),
    ("Psychiatry", "Psychiatry", 4),
]


def _find_referral_candidates_nppes(lat: float, lon: float, zip5: str, state: str,
                                    per_taxonomy: int = 4) -> list[ReferralCandidate]:
    """Credential-accurate referral discovery via the NPPES NPI Registry —
    finds sleep-medicine, ENT, neurology, primary-care and PT providers by
    their registered taxonomy, which a generic map search cannot do."""
    import nppes
    out: dict[tuple, ReferralCandidate] = {}
    for tax_desc, label, weight in NPPES_REFERRAL_TAXONOMIES:
        # Single-region search (fast): MD referrers are far less boundary-
        # sensitive than competitors, and 18 taxonomies × multiple prefixes
        # would make the report too slow.
        providers = nppes.search_by_taxonomy(tax_desc, zip5, state, is_specialist=False, limit=50)
        # Geocode the closest handful to their REAL street address (the rest keep
        # the centroid prefilter) so a same-building referrer reads ~0 mi instead
        # of the ZIP-centroid ~0.6 mi — which is what makes a medical-hub site score.
        nppes.attach_real_distances(providers, lat, lon, limit=8)
        # Nearest first, keep the closest few per specialty.
        providers = [p for p in providers if p.distance_mi is not None]
        providers.sort(key=lambda p: p.distance_mi)
        for p in providers[:per_taxonomy]:
            key = (p.name.lower(), p.zip5)
            if key in out:
                continue
            proximity_factor = max(0.0, 1 - (p.distance_mi / 15.0))  # 15-mi reference radius
            score = (weight * 10) * (0.7 * proximity_factor + 0.3)
            out[key] = ReferralCandidate(
                name=p.name,
                specialty=label,
                address=", ".join(x for x in (p.address, p.city, p.state, p.zip5) if x),
                lat=p.lat, lon=p.lon,
                place_id=f"npi:{p.name}:{p.zip5}",
                rating=None, user_ratings_total=None,
                distance_mi=p.distance_mi,
                fit_weight=weight,
                referral_score=round(score, 1),
                category="Physician (MD/DO)",
                phone=p.phone,
            )
    return sorted(out.values(), key=lambda c: c.referral_score, reverse=True)


def _find_referral_candidates_free(lat: float, lon: float, radius_m: int = 8000) -> list[ReferralCandidate]:
    """No-key, no-billing path via OpenStreetMap Overpass."""
    from geocode import haversine_miles
    seen: dict[tuple, ReferralCandidate] = {}

    for tag_filter, specialty, weight in OSM_REFERRAL_TARGETS:
        try:
            places = overpass.query_overpass([tag_filter], lat, lon, radius_m)
        except Exception:
            continue
        for p in places[:10]:
            key = (p.name, round(p.lat, 4), round(p.lon, 4))
            if key in seen:
                continue
            d = haversine_miles(lat, lon, p.lat, p.lon)
            proximity_factor = max(0.0, 1 - (d / (radius_m / 1609.34)))
            # No rating/review data from OSM — score on fit + proximity only.
            score = (weight * 10) * (0.75 * proximity_factor + 0.25)
            seen[key] = ReferralCandidate(
                name=p.name,
                specialty=specialty,
                address=p.address,
                lat=p.lat,
                lon=p.lon,
                place_id=f"osm:{p.name}:{p.lat}:{p.lon}",
                rating=None,
                user_ratings_total=None,
                distance_mi=round(d, 2),
                fit_weight=weight,
                referral_score=round(score, 1),
            )
    return sorted(seen.values(), key=lambda c: c.referral_score, reverse=True)


def find_referral_candidates(api_key: str, lat: float, lon: float, radius_m: int = 8000,
                             zip5: str = "", state: str = "") -> list[ReferralCandidate]:
    # Primary, credential-accurate path: NPPES NPI Registry by taxonomy. This
    # is the correct source for medical referrers (sleep medicine, ENT,
    # neurology, PCP) — strictly better than a generic map search, and free.
    if zip5:
        nppes_results = _find_referral_candidates_nppes(lat, lon, zip5, state)
        if nppes_results:
            return nppes_results
    # Fallbacks if no NPPES results (e.g. no ZIP resolved): Google or free OSM.
    if not api_key:
        return _find_referral_candidates_free(lat, lon, radius_m)
    from geocode import haversine_miles
    seen: dict[str, ReferralCandidate] = {}

    for keyword, specialty, weight in REFERRAL_TARGETS:
        params = {"key": api_key, "location": f"{lat},{lon}", "radius": radius_m, "keyword": keyword}
        r = requests.get(PLACES_NEARBY_URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            continue
        for place in data.get("results", [])[:8]:
            pid = place.get("place_id")
            if not pid or pid in seen:
                continue
            loc = place.get("geometry", {}).get("location", {})
            d = haversine_miles(lat, lon, loc.get("lat", 0.0), loc.get("lng", 0.0))
            rating = place.get("rating") or 0
            ratings_n = place.get("user_ratings_total") or 0
            # referral_score rewards closer, higher-fit, more-established (rating volume) practices
            proximity_factor = max(0.0, 1 - (d / (radius_m / 1609.34)))
            volume_factor = min(1.0, ratings_n / 200)
            score = (weight * 10) * (0.55 * proximity_factor + 0.25 * (rating / 5 if rating else 0) + 0.20 * volume_factor)
            seen[pid] = ReferralCandidate(
                name=place.get("name", "Unknown"),
                specialty=specialty,
                address=place.get("vicinity", ""),
                lat=loc.get("lat", 0.0),
                lon=loc.get("lng", 0.0),
                place_id=pid,
                rating=place.get("rating"),
                user_ratings_total=place.get("user_ratings_total"),
                distance_mi=round(d, 2),
                fit_weight=weight,
                referral_score=round(score, 1),
            )
    ranked = sorted(seen.values(), key=lambda c: c.referral_score, reverse=True)
    return ranked
