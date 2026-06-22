"""Epidemiological demand surface — replaces raw population with an estimate
of *expected addressable cases* for an orofacial-pain / TMJ / dental-sleep
clinic, so the spatial models weight demand by real disease burden and
cash-pay propensity rather than headcount.

Demand_z = Pop_z · age45+share(z) · OSA_risk_base · π_cashpay(income_z)
                                              + TMD term (female-weighted)

OSA_risk_base comes from the CDC PLACES tract-level prevalence API (free):
short-sleep duration, obesity and hypertension are validated OSA correlates.
The candidate tract's PLACES profile sets the area base rate; per-ZIP ACS
age/income then modulate it. Includes a latent-demand multiplier because
OSA is ~80% undiagnosed — a new specialist can expand, not just split, the
diagnosed market, so Huff's zero-sum assumption understates opportunity.
"""
import math
import requests

PLACES_URL = "https://data.cdc.gov/resource/cwsq-ngmh.json"
HEADERS = {"User-Agent": "ClinicSiteIntel/1.0"}

# Share of OSA/TMD demand that is currently undiagnosed and addressable by a
# new entrant through screening/awareness (market-expansion, not pure split).
LATENT_UNDIAGNOSED_FRACTION = 0.55   # conservative vs the ~0.8 literature figure


def get_places_osa_base(tract_geoid: str) -> dict:
    """Return {'sleep','obesity','bphigh','osa_index'} for a tract (percent).
    osa_index is a 0-1 composite OSA-risk multiplier vs a US-typical baseline."""
    out = {"sleep": None, "obesity": None, "bphigh": None, "osa_index": 1.0}
    if not tract_geoid:
        return out
    try:
        r = requests.get(PLACES_URL, params={"locationname": tract_geoid, "$limit": 60},
                         headers=HEADERS, timeout=20)
        r.raise_for_status()
        for row in r.json():
            mid = row.get("measureid")
            try:
                val = float(row.get("data_value"))
            except (TypeError, ValueError):
                continue
            if mid == "SLEEP":
                out["sleep"] = val
            elif mid == "OBESITY":
                out["obesity"] = val
            elif mid == "BPHIGH":
                out["bphigh"] = val
    except Exception:
        return out
    # Composite OSA-risk index, normalized to US-typical reference rates
    # (short sleep ~33%, obesity ~32%, hypertension ~32%).
    parts, refs = [], []
    for key, ref in (("sleep", 33.0), ("obesity", 32.0), ("bphigh", 32.0)):
        if out[key] is not None:
            parts.append(out[key]); refs.append(ref)
    if parts:
        out["osa_index"] = round(sum(parts) / sum(refs), 3)
    return out


def _logistic(z: float) -> float:
    """Numerically safe logistic — clamps the exponent so ACS sentinel values
    (e.g. -666666666 for 'not available') can't overflow math.exp."""
    z = max(-50.0, min(50.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def age45_share(median_age) -> float:
    """Approximate fraction of adults in the 45+ OSA/TMD risk window from a
    ZIP's median age (logistic centered near 45)."""
    if median_age is None or median_age < 0:   # ACS sentinel / missing
        return 0.45
    return round(_logistic(0.18 * (median_age - 44.0)), 3)


def cashpay_propensity(median_income) -> float:
    """0-1 propensity to pay out-of-pocket for elective specialist care,
    rising with income (logistic centered ~$90k)."""
    if median_income is None or median_income < 0:   # ACS sentinel / missing
        return 0.5
    return round(_logistic((median_income - 90000) / 35000.0), 3)


def expected_cases(population, median_age, median_income, osa_index: float = 1.0,
                   female_share: float = 0.51) -> float:
    """Expected addressable OSA+TMD cases for one demand area, including the
    latent (undiagnosed) expansion component."""
    if not population:
        return 0.0
    a = age45_share(median_age)
    pay = cashpay_propensity(median_income)
    # Base prevalences (adult): OSA moderate+ ~0.15 scaled by risk index; TMD ~0.07 female-weighted.
    osa = 0.15 * osa_index * a
    tmd = 0.07 * (0.6 + 0.8 * female_share)   # female-skewed
    diagnosed = population * (osa + tmd) * pay
    latent = diagnosed * LATENT_UNDIAGNOSED_FRACTION
    return round(diagnosed + latent, 1)
