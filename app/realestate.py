"""Commercial/medical real-estate candidate gathering.

Honest constraint, proven repeatedly in prior research sessions: LoopNet,
Crexi, CityFeet, and Carr.us all return HTTP 403 to automated/bot traffic
and render listings via JavaScript, so there is no reliable way to *live
scrape* them client-side without violating their Terms of Service and
without it actually working technically. Two real, working alternatives
are implemented instead:

1. generate_search_links() — builds direct, pre-filled search URLs for each
   major CRE platform around the target address/ZIP, opened in the user's
   browser with one click.
2. extract_from_pasted_text() — the user copies listing text straight from
   any of those sites and pastes it in; this module regex-parses square
   footage, rate, build-out type, and HVAC mentions into structured fields,
   the same approach used in the market-intelligence.html dashboard.
"""
from dataclasses import dataclass
from urllib.parse import quote_plus
import re

SEARCH_PLATFORMS = {
    "LoopNet": "https://www.loopnet.com/search/medical-office-properties/{q}/for-lease/",
    "Crexi": "https://www.crexi.com/lease/properties/{q_us}/medical-offices",
    "CityFeet": "https://www.cityfeet.com/cont/{q_dash}/medical-offices-for-lease",
    "CARR (medical/dental specialist)": "https://carr.us/find-space/?q={q}",
    "CBRE": "https://www.cbre.com/properties/properties-for-lease?searchtext={q}",
    "Google (broad net)": "https://www.google.com/search?q={q}+medical+OR+dental+office+space+for+lease",
}


def generate_search_links(city_state: str) -> dict[str, str]:
    q = quote_plus(city_state)
    q_us = city_state.strip().replace(" ", "-").replace(",", "")
    q_dash = city_state.lower().strip().replace(", ", "-").replace(" ", "-")
    links = {}
    for name, tmpl in SEARCH_PLATFORMS.items():
        links[name] = tmpl.format(q=q, q_us=quote_plus(q_us), q_dash=quote_plus(q_dash))
    return links


@dataclass
class ExtractedListing:
    address: str = ""
    square_feet: str = ""
    rate_per_sf_yr: float | None = None
    buildout: str = "unknown"
    hvac: str = "unknown"
    raw_text: str = ""
    confidence_note: str = ""


SF_PATTERN = re.compile(r"([\d,]{3,7})\s*(?:rsf|sf|sq\.?\s*ft\.?|square feet)", re.IGNORECASE)
RATE_PATTERN = re.compile(r"\$\s*([\d]+(?:\.\d+)?)\s*(/\s*sf\s*/\s*(yr|year|mo|month))?", re.IGNORECASE)
ADDR_PATTERN = re.compile(r"\d{1,6}\s+[A-Za-z0-9.\- ]+(?:Rd|Road|St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Way|Ct|Court|Ln|Lane)\b", re.IGNORECASE)


def extract_from_pasted_text(raw_text: str) -> ExtractedListing:
    result = ExtractedListing(raw_text=raw_text)

    addr_match = ADDR_PATTERN.search(raw_text)
    if addr_match:
        result.address = addr_match.group(0).strip()

    sf_match = SF_PATTERN.search(raw_text)
    if sf_match:
        result.square_feet = sf_match.group(1)

    rate_match = RATE_PATTERN.search(raw_text)
    if rate_match:
        val = float(rate_match.group(1))
        period = (rate_match.group(3) or "").lower()
        if period in ("mo", "month") or val < 8:
            val = round(val * 12, 2)  # annualize monthly-looking rates
        result.rate_per_sf_yr = val

    lower = raw_text.lower()
    if "dental" in lower and ("wet line" in lower or "plumbing" in lower or "operatory" in lower):
        result.buildout = "dental"
    elif "medical" in lower or "exam room" in lower:
        result.buildout = "medical"
    elif "vanilla shell" in lower or "shell space" in lower:
        result.buildout = "shell"

    if "hvac" in lower:
        if "new hvac" in lower or "upgraded hvac" in lower:
            result.hvac = "upgraded"
        elif "weekend" in lower and "hvac" in lower:
            result.hvac = "weekend-noted"
        else:
            result.hvac = "mentioned-unspecified"

    notes = []
    if not result.address:
        notes.append("No street address pattern detected — fill manually.")
    if not result.square_feet:
        notes.append("No SF figure detected.")
    if result.rate_per_sf_yr is None:
        notes.append("No rate detected.")
    result.confidence_note = "; ".join(notes) if notes else "All core fields auto-detected — verify before relying on them."
    return result
