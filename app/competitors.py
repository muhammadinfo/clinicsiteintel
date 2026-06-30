"""Live competitor discovery: free by default via OpenStreetMap Overpass
(no key, no billing), or via Google Places if the user has added their
own API key in Settings (richer metadata, ratings — but billing-gated).

Step 1: find nearby dental/healthcare practices.
Step 2: fetch EACH ONE's own website (not the directory listing) and
score it for "competition potential" by scanning for credential/specialty
keywords. This is the real verification step, and it's identical
regardless of which directory supplied the lead — a listing tagged
"Dentist" tells you nothing about whether they actually do orofacial
pain / TMJ / dental sleep work; the website scan is what answers that.
"""
from dataclasses import dataclass, field
import re
import requests

import overpass

PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

SEARCH_TERMS = [
    "TMJ specialist",
    "orofacial pain",
    "dental sleep medicine",
    "sleep apnea dentist",
    "TMD treatment dentist",
    "oral appliance therapy",
]

# Keyword weight table for scoring a competitor's own website.
# Higher weight = stronger signal of genuine specialist-level competition,
# not just a general dentist offering an ancillary service.
KEYWORD_WEIGHTS = {
    r"\bABOP\b": 25,
    r"\bAAOP\b": 20,
    r"diplomate": 22,
    r"board[- ]certified.{0,40}orofacial": 25,
    r"orofacial pain": 15,
    r"\bRPSGT\b": 15,
    r"american academy of dental sleep medicine|\bAADSM\b": 18,
    r"\bTMJ\b": 6,
    r"\bTMD\b": 6,
    r"dental sleep medicine": 10,
    r"oral appliance therapy": 8,
    r"sleep apnea": 5,
    r"craniofacial pain": 10,
    r"myofascial": 6,
    r"usc.{0,20}faculty|ucla.{0,20}faculty": 12,
}


@dataclass
class CompetitorResult:
    name: str
    address: str
    lat: float
    lon: float
    place_id: str
    rating: float | None
    user_ratings_total: int | None
    website: str | None = None
    phone: str | None = None
    distance_mi: float | None = None
    competition_score: int = 0
    matched_signals: list[str] = field(default_factory=list)
    verification_note: str = ""
    tier: str = "General"   # "Specialist" (orofacial-pain credential) or "General" (DDS office)
    retire_prob: float = 0.0   # P(provider exits within planning horizon) — depreciating threat
    tenure_years: float = 0.0


# OpenStreetMap tag filters covering dental + orofacial-pain-adjacent practices.
OSM_TAG_FILTERS = [
    '["amenity"="dentist"]',
    '["healthcare"="dentist"]',
    '["healthcare:speciality"="oral_and_maxillofacial_surgery"]',
]


def search_nearby_competitors_free(lat: float, lon: float, radius_m: int = 12000) -> list[CompetitorResult]:
    """No-key, no-billing path via OpenStreetMap Overpass."""
    places = overpass.query_overpass(OSM_TAG_FILTERS, lat, lon, radius_m)
    results = []
    for p in places:
        results.append(CompetitorResult(
            name=p.name,
            address=p.address,
            lat=p.lat,
            lon=p.lon,
            place_id=f"osm:{p.name}:{p.lat}:{p.lon}",
            rating=None,
            user_ratings_total=None,
            website=p.website,
            phone=p.phone,
        ))
    return results


def search_nearby_competitors(api_key: str, lat: float, lon: float, radius_m: int = 12000) -> list[CompetitorResult]:
    """Keyword competitor discovery via Places API (New). The new API returns
    website + phone inline (field mask), so no separate Details call is needed."""
    if not api_key:
        raise ValueError(
            "No Google Places API key configured. Add one in Settings → "
            "Google Places API key (Places API (New) must be enabled)."
        )
    import google_places_v1 as gp
    seen: dict[str, CompetitorResult] = {}
    for term in SEARCH_TERMS:
        for pl in gp.text_search(api_key, term, lat, lon, radius_m):
            pid = pl.get("place_id")
            if not pid or pid in seen:
                continue
            seen[pid] = CompetitorResult(
                name=pl.get("name") or "Unknown",
                address=pl.get("address", ""),
                lat=pl.get("lat", 0.0),
                lon=pl.get("lon", 0.0),
                place_id=pid,
                rating=pl.get("rating"),
                user_ratings_total=pl.get("user_ratings_total"),
                website=pl.get("website"),
                phone=pl.get("phone"),
            )
    return list(seen.values())


# Place types that indicate a medical/dental tenant for the building-directory pull.
_DIRECTORY_TYPES = ["doctor", "dentist", "physiotherapist", "hospital",
                    "medical_lab", "wellness_center"]


def building_directory(api_key: str, lat: float, lon: float, radius_m: int = 80) -> list[CompetitorResult]:
    """Google Maps 'Directory'-style pull: every medical/dental tenant AT (or
    within ~radius of) this exact building, via a tight-radius Places (New)
    nearby search. This captures in-building providers that NPI misses."""
    if not api_key:
        return []
    import google_places_v1 as gp
    seen: dict[str, CompetitorResult] = {}
    for pl in gp.nearby(api_key, lat, lon, radius_m, _DIRECTORY_TYPES, max_results=20):
        pid = pl.get("place_id")
        if not pid or pid in seen:
            continue
        seen[pid] = CompetitorResult(
            name=pl.get("name") or "Unknown",
            address=pl.get("address", ""),
            lat=pl.get("lat", 0.0),
            lon=pl.get("lon", 0.0),
            place_id=pid,
            rating=pl.get("rating"),
            user_ratings_total=pl.get("user_ratings_total"),
            website=pl.get("website"),
            phone=pl.get("phone"),
        )
    return list(seen.values())


def fetch_place_details(api_key: str, place_id: str) -> dict:
    params = {
        "key": api_key,
        "place_id": place_id,
        "fields": "website,formatted_phone_number,formatted_address",
    }
    r = requests.get(PLACES_DETAILS_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("result", {})


def verify_competitor_website(comp: CompetitorResult) -> CompetitorResult:
    """Fetch the competitor's own website and score competition potential
    by keyword density. This is the 'verify them on their website' step —
    distinguishes a board-certified specialist from a general dentist who
    merely lists TMJ as one of many services."""
    if not comp.website:
        comp.verification_note = "No website on file — could not verify specialty depth."
        return comp
    try:
        r = requests.get(comp.website, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        text = r.text.lower()
    except Exception as e:
        comp.verification_note = f"Website unreachable ({e.__class__.__name__}) — unverified."
        return comp

    score = 0
    matched = []
    for pattern, weight in KEYWORD_WEIGHTS.items():
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            matched.append(pattern.strip(r"\b").replace("\\", ""))

    comp.competition_score = min(100, score)
    comp.matched_signals = matched
    if score >= 50:
        comp.verification_note = "HIGH — verified specialist-level signals (board cert / diplomate / AADSM)."
    elif score >= 20:
        comp.verification_note = "MODERATE — offers TMJ/sleep services but no confirmed specialist credential."
    elif score > 0:
        comp.verification_note = "LOW — passing mention only; likely ancillary service, not a core focus."
    else:
        comp.verification_note = "NONE DETECTED — site does not mention TMJ/orofacial pain/sleep medicine terms."
    return comp


# The precise specialist competitor per the clinic's definition: a DDS whose
# REGISTERED credential is orofacial pain (which encompasses TMJ and dental
# sleep practice). TMJ-treating and dental-sleep general dentists who lack this
# formal taxonomy are still captured — via the website-advertises-service scan
# below — so this single taxonomy plus that scan covers the full definition
# without dragging in distant oral-surgeons who aren't the conservative-care rival.
SPECIALIST_TAXONOMIES = [
    "Orofacial Pain",
]

# Specialists farther than this aren't "in the area" — drop them from the
# competitor list so the set reflects the specific address, not the whole metro.
SPECIALIST_MAX_MILES = 18.0


def search_specialist_competitors(zip5: str, state: str, lat: float, lon: float) -> list[CompetitorResult]:
    """The important half: credentialed orofacial-pain / TMJ specialists from
    the NPPES NPI Registry (free, by registered taxonomy). These are the real
    direct competition, scored high because the credential itself is the
    verification — far stronger than a website keyword scan."""
    import nppes
    out: list[CompetitorResult] = []
    seen = set()
    for tax in SPECIALIST_TAXONOMIES:
        # Radius-based around the actual address (catches specialists across
        # 3-digit ZIP-region boundaries), not just the address's own region.
        providers = nppes.search_by_taxonomy_near(tax, lat, lon, radius_mi=18.0,
                                                  state=state, is_specialist=True)
        # Specialists are few and decisive — geocode every one to its real
        # street address so the proximity penalty reflects true distance, not
        # the ZIP centroid.
        nppes.attach_real_distances(providers, lat, lon)
        for p in providers:
            key = (p.name.lower(), p.zip5)
            if key in seen:
                continue
            seen.add(key)
            if p.distance_mi is not None and p.distance_mi > SPECIALIST_MAX_MILES:
                continue
            out.append(CompetitorResult(
                name=p.name,
                address=", ".join(x for x in (p.address, p.city, p.state, p.zip5) if x),
                lat=p.lat, lon=p.lon,
                place_id=f"npi:{p.name}:{p.zip5}",
                rating=None, user_ratings_total=None,
                website=None, phone=p.phone,
                distance_mi=p.distance_mi,
                tier="Specialist",
                competition_score=90,
                retire_prob=getattr(p, "retire_prob", 0.0),
                tenure_years=getattr(p, "tenure_years", 0.0),
                verification_note=(
                    "VERIFIED SPECIALIST — registered '" + p.taxonomy_desc +
                    "' provider in the NPI Registry (" +
                    (f"{p.tenure_years:.0f} yr tenure" if p.tenure_years else "tenure n/a") +
                    "). Direct credentialed orofacial-pain competition."
                ),
            ))
    return _dedupe_practices(out)


def _dedupe_practices(items: list[CompetitorResult]) -> list[CompetitorResult]:
    """Collapse the NPI Registry's org-NPI + individual-NPI duplicates (and a
    provider's multiple office NPIs) into one row per practice, keyed on phone
    number, keeping the NEAREST location. Without this the list double/triple-
    counts the same practice (e.g. Omrani, Newman, Khalifeh each appear 2-3×)."""
    import re as _re
    by_phone: dict[str, CompetitorResult] = {}
    no_phone: list[CompetitorResult] = []
    for c in items:
        digits = _re.sub(r"\D", "", c.phone or "")
        if not digits:
            no_phone.append(c)
            continue
        cur = by_phone.get(digits)
        if cur is None or (c.distance_mi or 999) < (cur.distance_mi or 999):
            by_phone[digits] = c
    return list(by_phone.values()) + no_phone


def scan_watchlist(known_competitors: list, clinic_lat: float, clinic_lon: float) -> list[CompetitorResult]:
    """Website-verify a user-curated list of named competitors. This is how we
    reliably surface specialists the NPI taxonomy search misses because they
    register as general dentists (e.g. Shirazi, Borquez) — their TMJ/orofacial-
    pain/sleep focus lives only on their own websites, which we fetch and score."""
    import nppes
    from geocode import haversine_miles, geocode_oneline
    out: list[CompetitorResult] = []
    for entry in known_competitors or []:
        name = entry.get("name", "").strip()
        url = entry.get("url", "").strip()
        if not name or not url:
            continue
        zip5 = entry.get("zip", "").strip()
        street = entry.get("address", "").strip()
        dist, clat, clon, addr_label = None, 0.0, 0.0, (f"ZIP {zip5}" if zip5 else "")
        # Prefer the watchlisted competitor's REAL street address (geocoded) so
        # its distance isn't the ZIP-centroid artifact (e.g. Shirazi at 555 Marin
        # St is ~1.7 mi from 2220 Lynn Rd, not the centroid's 0.6 mi).
        coords = geocode_oneline(street) if street else None
        if coords:
            clat, clon = coords
            dist = round(haversine_miles(clinic_lat, clinic_lon, clat, clon), 2)
            addr_label = street
        elif zip5:
            centroid = nppes._zip_centroid(zip5)
            if centroid:
                dist = round(haversine_miles(clinic_lat, clinic_lon, centroid[0], centroid[1]), 1)
        comp = CompetitorResult(
            name=name, address=addr_label,
            lat=clat, lon=clon, place_id=f"watch:{name}",
            rating=None, user_ratings_total=None,
            website=url, phone=None, distance_mi=dist,
            tier="Specialist (watchlist)",
        )
        verify_competitor_website(comp)  # scores the site
        # A watchlisted competitor is a known specialist; ensure it ranks as one
        # even if the homepage keywords are sparse (the real content is on inner pages).
        if comp.competition_score < 60:
            comp.competition_score = max(comp.competition_score, 80)
            comp.verification_note = (
                "KNOWN SPECIALIST (watchlist) — " + (comp.verification_note or "") +
                " Listed as a TMJ/orofacial-pain/sleep competitor; verify current scope on site."
            )
        else:
            comp.verification_note = "KNOWN SPECIALIST (watchlist) — " + comp.verification_note
        out.append(comp)
    return out


# Directory/aggregator domains that aren't a practice's own site.
_DDG_SKIP = ("yelp", "healthgrades", "zocdoc", "facebook", "yellowpages", "npino", "npidb",
             "ratemds", "vitals", "mapquest", "google", "linkedin", "instagram", "sharecare",
             "webmd", "1800dentist", "opencare", "wellness.com", "doctor.com", "findatopdoc",
             "dentists.com", "smilegeneration", "bing", "tripadvisor", "apple.com", "youtube",
             "groupon", "angi.com", "thumbtack", "birdeye", "demandforce")


def resolve_website_ddg(name: str, city: str, state: str) -> str | None:
    """Resolve a practice's own website from its name + city via DuckDuckGo's
    keyless HTML endpoint (NPPES has the complete dentist list but no websites)."""
    import urllib.parse
    q = urllib.parse.quote_plus(f"{name} dentist {city} {state}")
    try:
        r = requests.get("https://html.duckduckgo.com/html/?q=" + q,
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        for l in re.findall(r'uddg=([^&"]+)', r.text)[:6]:
            u = urllib.parse.unquote(l)
            dom = u.split("//")[-1].split("/")[0].lower()
            if any(b in dom for b in _DDG_SKIP):
                continue
            return u
    except Exception:
        return None
    return None


def search_general_dentists_nppes(lat: float, lon: float, state: str,
                                  radius_mi: float = 10.0, max_n: int = 30) -> list[CompetitorResult]:
    """The COMPLETE nearby general-dentist universe from the NPI Registry —
    OSM only maps a fraction (≈12 of 200+ in one ZIP). Nearest max_n, deduped
    by phone. Websites are resolved + scanned later."""
    import nppes
    providers = nppes.search_by_taxonomy_near("Dentist", lat, lon, radius_mi, state,
                                              is_specialist=False, limit=200)
    nppes.attach_distances(providers, lat, lon)
    providers = [p for p in providers if p.distance_mi is not None]
    providers.sort(key=lambda p: p.distance_mi)
    out, seen = [], set()
    for p in providers:
        key = re.sub(r"\D", "", p.phone or "") or f"{p.name.lower()}|{p.zip5}"
        if key in seen:
            continue
        seen.add(key)
        c = CompetitorResult(
            name=p.name, address=", ".join(x for x in (p.address, p.city, p.state, p.zip5) if x),
            lat=0.0, lon=0.0, place_id=f"npi:{p.name}:{p.zip5}",
            rating=None, user_ratings_total=None, website=None, phone=p.phone,
            distance_mi=p.distance_mi)
        c._city = p.city
        out.append(c)
        if len(out) >= max_n:
            break
    return out


def run_full_competitor_scan(api_key: str, lat: float, lon: float, radius_m: int = 12000,
                             zip5: str = "", state: str = "", known_competitors: list = None) -> dict:
    """Classify nearby dentists per the user's definition:

      COMPETITORS  = DDS who DO orofacial pain / TMJ / dental sleep medicine:
                       (a) NPPES-registered orofacial-pain specialists, plus
                       (b) any general dentist whose own website advertises
                           TMJ / TMD / dental sleep / oral-appliance services.
      REFERRAL DDS = general dentists whose website does NOT advertise those
                       services (prospective referral sources, not rivals).

    Returns {"competitors": [...], "referral_dentists": [...]}. The MD referral
    substrate is added separately by referrals.find_referral_candidates().
    """
    from geocode import haversine_miles

    # 1a) Credentialed specialist competitors (NPPES orofacial-pain taxonomy).
    try:
        specialists = search_specialist_competitors(zip5, state, lat, lon) if zip5 else []
    except Exception:
        specialists = []
    # 1b) Known specialists the NPI taxonomy misses (registered as general
    #     dentists) — website-verified from the user-editable watchlist.
    try:
        watchlisted = scan_watchlist(known_competitors, lat, lon)
    except Exception:
        watchlisted = []
    specialists = watchlisted + specialists
    specialist_keys = {c.name.lower() for c in specialists}
    # Also block watchlist domains from re-appearing as a "general" dentist row.
    watch_domains = {(c.website or "").split("//")[-1].split("/")[0].replace("www.", "")
                     for c in watchlisted if c.website}

    # 2) Every nearby general dental office (Google if key present, else free OSM).
    #    Isolated in try/except: this free/3rd-party map lookup is the flakiest
    #    part (OSM Overpass throws 504s under load), and a failure here must NOT
    #    wipe out the credentialed NPPES specialists or the website-verified
    #    watchlist gathered above.
    dentists = []
    google_error = ""
    used_google = False
    try:
        if api_key:
            # Places API (New): complete dentist coverage WITH website/phone inline,
            # PLUS a tight-radius building-directory pull for in-building tenants.
            merged: dict[str, CompetitorResult] = {}
            for d in search_nearby_competitors(api_key, lat, lon, radius_m):
                merged[d.place_id] = d
            try:
                for d in building_directory(api_key, lat, lon):
                    merged.setdefault(d.place_id, d)
            except Exception:
                pass
            dentists = list(merged.values())
            used_google = True
        else:
            # Free path: OSM-mapped dentists that carry websites — partial coverage.
            dentists = search_nearby_competitors_free(lat, lon, radius_m)
    except Exception as e:
        # Surface the REAL reason (e.g. Places API (New) not enabled / billing /
        # invalid key) instead of silently dropping to free mode.
        google_error = str(e)
        dentists = []  # specialists + watchlist still returned below

    competitor_dentists, referral_dentists = [], []
    for d in dentists:
        if d.lat and d.lon:
            d.distance_mi = round(haversine_miles(lat, lon, d.lat, d.lon), 1)
        dom = (d.website or "").split("//")[-1].split("/")[0].replace("www.", "")
        if d.name.lower() in specialist_keys or (dom and dom in watch_domains):
            continue  # already captured as a credentialed/known specialist
        verify_competitor_website(d)  # sets competition_score + verification_note
        if d.competition_score > 0:
            d.tier = "General (advertises service)"
            competitor_dentists.append(d)
        else:
            d.tier = "Referral DDS (no TMJ/sleep advertised)"
            referral_dentists.append(d)

    competitors = specialists + competitor_dentists
    competitors.sort(key=lambda c: (not c.tier.startswith("Specialist"), -c.competition_score,
                                    c.distance_mi if c.distance_mi is not None else 999))
    referral_dentists.sort(key=lambda c: c.distance_mi if c.distance_mi is not None else 999)
    return {"competitors": competitors, "referral_dentists": referral_dentists,
            "google_error": google_error, "used_google": used_google}
