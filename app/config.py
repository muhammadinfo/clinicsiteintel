"""Local settings store (API keys, last-used paths) for ClinicSiteIntel.
Stored as JSON under %APPDATA%\\ClinicSiteIntel\\config.json so it survives
app updates and is per-Windows-user.
"""
import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ClinicSiteIntel")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DB_PATH = os.path.join(APP_DIR, "clinicsiteintel.sqlite3")

DEFAULTS = {
    "google_places_api_key": "",
    "census_api_key": "",  # optional, Census ACS works without a key at low volume
    "home_clinic_address": "",
    # Named competitors the NPI taxonomy search can't auto-detect because they
    # register as general dentists while practicing/marketing as TMJ / orofacial-
    # pain / dental-sleep specialists. Each is website-verified on every report.
    # Format per entry: {"name", "url", "zip"} (zip is for distance only).
    # Format per entry: {"name", "url", "address", "zip"}. "address" (real
    # street) is geocoded for an accurate distance; "zip" is only a fallback.
    "known_competitors": [
        {"name": "Dr. David Shirazi — TMJ & Sleep Therapy Centre",
         "url": "https://tmjandsleeptherapycentre.com/",
         "address": "555 Marin St Ste 108, Thousand Oaks, CA 91360", "zip": "91360"},
        {"name": "Dr. Rick Borquez — Westlake TMJ & Sleep",
         "url": "https://www.westlaketmj.com/",
         "address": "911 Hampshire Rd Ste 1, Westlake Village, CA 91361", "zip": "91361"},
    ],
    # Site Scout (listings + verdict engine) optional keys.
    "mapbox_key": "",      # drive-time isochrones (free tier; OSRM fallback)
    "anthropic_key": "",   # Claude Vision listing read (paid)
    "apify_key": "",       # live LoopNet/Crexi listings (paid; else sample/manual)
    # Live academy/board credential lookup. OFF by default: querying the AADSM,
    # AAOP/ABOP, AASM and AAO-HNS provider finders at report time is against
    # those sites' terms of use — enable only if you accept that responsibility.
    # When on, one ZIP+radius query per academy per report is made, cached, and
    # rate-limited; results feed the same name+state cross-match as the local
    # credential registry.
    "live_credential_lookup": False,
    "live_credential_radius_mi": 10,
    "live_credential_sources": ["AADSM", "AAOP", "AASM", "AAO-HNS"],
}


def ensure_app_dir():
    os.makedirs(APP_DIR, exist_ok=True)


def load_config() -> dict:
    ensure_app_dir()
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        # Migration: backfill watchlist street addresses (added for accurate
        # distance geocoding) onto saved configs that predate the "address" field.
        def_by_name = {c["name"]: c for c in DEFAULTS["known_competitors"]}
        for kc in merged.get("known_competitors", []):
            if not kc.get("address") and kc.get("name") in def_by_name:
                addr = def_by_name[kc["name"]].get("address")
                if addr:
                    kc["address"] = addr
        return merged
    except Exception:
        return dict(DEFAULTS)


def save_config(cfg: dict):
    ensure_app_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
