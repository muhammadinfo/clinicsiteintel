"""Live demographic pull from the US Census Bureau ACS 5-Year API.

Works at both the address level (nearest Census tract, via geocode.py)
and the ZIP-code level (ZCTA aggregate), matching the user's requirement
to evaluate "both address based and zip code based."

CONFIRMED LIVE 2026-06-20: the Census API now rejects all data.census.gov
ACS requests without an API key ("A valid key must be included with each
data API request"), including low-volume use — this was tested directly
against the live endpoint. A free key is required; sign up instantly at
https://api.census.gov/data/key_signup.html and enter it in Settings.
"""
from dataclasses import dataclass
import requests

ACS_YEAR = "2022"  # latest stable 5-year ACS vintage at time of writing
ACS_BASE = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"

VARIABLES = {
    "B01003_001E": "population",
    "B19013_001E": "median_household_income",
    "B01002_001E": "median_age",
    "B25077_001E": "median_home_value",
    "B25064_001E": "median_gross_rent",
    "B17001_002E": "below_poverty_count",
    "B23025_005E": "unemployed_count",
    "B01001_001E": "total_for_age_sex",
    "B01001_026E": "female_total",          # for female-share / TMD weighting
    # Margins of error (90% CI half-widths) — propagated into LVI uncertainty.
    "B19013_001M": "income_moe",
    "B01002_001M": "age_moe",
    "B01003_001M": "population_moe",
}


@dataclass
class DemographicProfile:
    level: str  # "tract" or "zcta"
    geo_label: str
    population: float | None
    median_household_income: float | None
    median_age: float | None
    median_home_value: float | None
    median_gross_rent: float | None
    poverty_rate_pct: float | None
    unemployment_rate_pct: float | None
    female_share: float | None = None
    income_moe: float | None = None
    age_moe: float | None = None
    population_moe: float | None = None


def _parse_row(level: str, label: str, header: list[str], row: list[str]) -> DemographicProfile:
    raw = {}
    for i, key in enumerate(header):
        if key in VARIABLES:
            val = row[i]
            try:
                f = float(val)
                # ACS uses large negative sentinels (e.g. -666666666, -999999999)
                # for "not available" — treat those as missing, not real values.
                raw[VARIABLES[key]] = None if f <= -1e6 else f
            except (TypeError, ValueError):
                raw[VARIABLES[key]] = None

    pop = raw.get("population")
    poverty = raw.get("below_poverty_count")
    unemployed = raw.get("unemployed_count")
    female = raw.get("female_total")

    poverty_rate = (poverty / pop * 100) if (pop and poverty is not None and pop > 0) else None
    unemployment_rate = (unemployed / pop * 100) if (pop and unemployed is not None and pop > 0) else None
    female_share = (female / pop) if (pop and female is not None and pop > 0) else None

    return DemographicProfile(
        level=level,
        geo_label=label,
        population=pop,
        median_household_income=raw.get("median_household_income"),
        median_age=raw.get("median_age"),
        median_home_value=raw.get("median_home_value"),
        median_gross_rent=raw.get("median_gross_rent"),
        poverty_rate_pct=poverty_rate,
        unemployment_rate_pct=unemployment_rate,
        female_share=female_share,
        income_moe=raw.get("income_moe"),
        age_moe=raw.get("age_moe"),
        population_moe=raw.get("population_moe"),
    )


def _require_key(api_key: str):
    if not api_key:
        raise ValueError(
            "Census API key is required (the Census Bureau now rejects all "
            "unauthenticated ACS requests). Get a free key instantly at "
            "https://api.census.gov/data/key_signup.html and add it in Settings."
        )


def _get_json_or_raise(r) -> list:
    try:
        return r.json()
    except ValueError:
        raise ValueError(
            f"Census API did not return JSON (HTTP {r.status_code}). Raw response: "
            f"{r.text[:300]}"
        )


def get_zcta_profile(zip_code: str, api_key: str = "") -> DemographicProfile:
    if not zip_code:
        raise ValueError("No ZIP/ZCTA available to query.")
    _require_key(api_key)
    var_str = ",".join(VARIABLES.keys())
    params = {"get": var_str, "for": f"zip code tabulation area:{zip_code}", "key": api_key}
    r = requests.get(ACS_BASE, params=params, timeout=20)
    data = _get_json_or_raise(r)
    header, row = data[0], data[1]
    return _parse_row("zcta", f"ZCTA {zip_code}", header, row)


def get_tract_profile(state_fips: str, county_fips: str, tract: str, api_key: str = "") -> DemographicProfile:
    if not (state_fips and county_fips and tract):
        raise ValueError("Incomplete Census geography — cannot query tract-level ACS data.")
    _require_key(api_key)
    var_str = ",".join(VARIABLES.keys())
    params = {
        "get": var_str,
        "for": f"tract:{tract}",
        "in": f"state:{state_fips} county:{county_fips}",
        "key": api_key,
    }
    r = requests.get(ACS_BASE, params=params, timeout=20)
    data = _get_json_or_raise(r)
    header, row = data[0], data[1]
    return _parse_row("tract", f"Tract {state_fips}{county_fips}{tract}", header, row)


def get_neighboring_zctas_profiles(zip_codes: list[str], api_key: str = "") -> list[DemographicProfile]:
    """Pull profiles for a basket of ZIPs — used by stats.py to build the
    address/ZIP success-correlation model across the metro area."""
    profiles = []
    for z in zip_codes:
        try:
            profiles.append(get_zcta_profile(z, api_key))
        except Exception:
            continue
    return profiles
