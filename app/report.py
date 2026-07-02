"""Orchestrates a full, real-time clinic-site assessment for a single
address: geocode -> demographics (tract + ZCTA) -> competitor scan ->
referral candidates -> real-estate search links -> LVI + Bayesian
uncertainty -> ZIP-basket statistics. Returns one structured dict that
both the GUI and an exported report consume.
"""
import traceback

import geocode
import demographics
import competitors
import referrals
import realestate
import lvi
import stats


def run_full_report(address: str, google_api_key: str, census_api_key: str = "",
                     progress_cb=None, known_competitors: list = None) -> dict:
    def step(msg):
        if progress_cb:
            progress_cb(msg)

    report = {"address_input": address, "errors": []}

    step("Geocoding address...")
    geo = geocode.geocode_address(address)
    report["geo"] = geo.__dict__
    if getattr(geo, "match_warning", ""):
        report["errors"].append("⚠ " + geo.match_warning)

    # Extract the 2-letter USPS state from the matched address (e.g.
    # "2220 LYNN RD, THOUSAND OAKS, CA, 91360") for NPPES queries.
    import re as _re
    _m = _re.search(r",\s*([A-Z]{2}),?\s*\d{5}", geo.matched_address or "")
    state_abbr = _m.group(1) if _m else ""

    step("Pulling tract-level demographics (live ACS)...")
    try:
        tract_profile = demographics.get_tract_profile(
            geo.state_fips, geo.county_fips, geo.tract, census_api_key
        )
        report["demographics_tract"] = tract_profile.__dict__
    except Exception as e:
        report["errors"].append(f"Tract demographics failed: {e}")
        tract_profile = None

    step("Pulling ZIP-level demographics (live ACS)...")
    try:
        zcta_profile = demographics.get_zcta_profile(geo.zip_code, census_api_key)
        report["demographics_zip"] = zcta_profile.__dict__
    except Exception as e:
        report["errors"].append(f"ZIP demographics failed: {e}")
        zcta_profile = None

    referral_dentists = []
    step("Classifying competitors (DDS doing orofacial pain / TMJ / dental sleep) vs referral dentists...")
    try:
        scan = competitors.run_full_competitor_scan(
            google_api_key, geo.lat, geo.lon, zip5=geo.zip_code, state=state_abbr,
            known_competitors=known_competitors,
        )
        comp_results = scan["competitors"]
        referral_dentists = scan["referral_dentists"]
        report["competitors"] = [c.__dict__ for c in comp_results]
        report["places_status"] = {"used_google": scan.get("used_google", False),
                                    "google_error": scan.get("google_error", "")}
        # Google Maps building directory — every medical tenant AT this address.
        from geocode import haversine_miles as _hav
        bdir = []
        for d in scan.get("building_directory", []):
            dd = d.__dict__ if hasattr(d, "__dict__") else dict(d)
            if dd.get("lat") and dd.get("lon"):
                dd["distance_mi"] = round(_hav(geo.lat, geo.lon, dd["lat"], dd["lon"]), 2)
            bdir.append(dd)
        bdir.sort(key=lambda x: (x.get("distance_mi") if x.get("distance_mi") is not None else 9))
        report["building_directory"] = bdir
        if scan.get("google_error"):
            report["errors"].append("Google Places (New) error — " + scan["google_error"]
                                    + "  (Enable 'Places API (New)' + billing on the project, or the key is invalid.)")
        n_spec = sum(1 for c in comp_results if c.tier == "Specialist")
        if n_spec == 0:
            report["errors"].append(
                "No NPI-registered orofacial-pain/TMJ specialist found in this ZIP region. Competitors "
                "shown are general dentists who advertise TMJ/sleep services on their website — confirm "
                "whether a credentialed specialist practices nearby under a different registered ZIP."
            )
    except Exception as e:
        report["errors"].append(f"Competitor scan failed: {e}")

    step("Finding referral sources (MDs of all specialties via NPI Registry) + non-competitor dentists...")
    try:
        md_referrals = referrals.find_referral_candidates(
            google_api_key, geo.lat, geo.lon, zip5=geo.zip_code, state=state_abbr,
            target_addr=geo.matched_address,
        )
        # Convert referral-dentists (DDS who don't advertise TMJ/sleep) into referral rows.
        dds_rows = []
        for d in referral_dentists:
            dds_rows.append({
                "name": d.name, "specialty": "General Dentistry (no TMJ/sleep advertised)",
                "address": d.address, "lat": d.lat, "lon": d.lon,
                "place_id": d.place_id, "rating": d.rating,
                "user_ratings_total": d.user_ratings_total, "distance_mi": d.distance_mi,
                "fit_weight": 5, "referral_score": 0.0, "category": "Dentist — non-competitor",
                "phone": d.phone,
            })
        all_referrals = [r.__dict__ for r in md_referrals] + dds_rows
        report["referrals"] = all_referrals
    except Exception as e:
        report["errors"].append(f"Referral search failed: {e}")

    step("Cross-matching academy / board credentials (AAOP·ABOP, AADSM, AASM, AAO-HNS)...")
    try:
        import credentials
        credentials.enrich_report(report)
    except Exception as e:
        report["errors"].append(f"Credential cross-match failed: {e}")

    step("Building real-estate search links...")
    city_state_guess = address.split(",")[-2].strip() + ", CA" if "," in address else address
    report["realestate_links"] = realestate.generate_search_links(city_state_guess)

    step("Computing Location Viability Index with Bayesian uncertainty...")
    ds = lvi.derive_ds_from_demographics(
        tract_profile.median_household_income if tract_profile else None,
        tract_profile.median_age if tract_profile else None,
        zcta_profile.population if zcta_profile else None,
    )
    comp_dicts = report.get("competitors", [])
    # Competition from REAL per-competitor distances (smooth decay, no centroid cliff).
    comp_pairs = [(c.get("competition_score", 0), c.get("distance_mi"))
                  for c in comp_dicts if c.get("competition_score", 0) > 0]
    cp = lvi.derive_cp_from_competitors_v2(comp_pairs)
    # Referral ACCESS (distance/specialty-weighted + co-located anchor bonus) and
    # the medical-hub infrastructure proxy, both off the geocoded referral set.
    referrals_list = report.get("referrals", [])
    rp = lvi.derive_rp_from_referrals(referrals_list)
    if_factor = lvi.derive_if_from_medical_hub(referrals_list, comp_dicts)

    # Propagate the ACS margin of error on income into the Dₛ uncertainty,
    # so the credible interval reflects real Census sampling error.
    inc = tract_profile.median_household_income if tract_profile else None
    inc_moe = tract_profile.income_moe if tract_profile else None
    ds_sigma = 8.0
    if inc and inc_moe:
        rel = (inc_moe / 1.645) / inc   # MOE is 90% half-width -> 1 SD
        ds_sigma = max(5.0, min(22.0, 6.0 + rel * 120.0))
    # Let monte_carlo_lvi compute adaptive sigmas based on data certainty, but
    # seed it with the Census income MOE-derived ds_sigma (that one is data-driven).
    inputs = lvi.LVIInputs(ds=ds, rp=rp, if_=if_factor, cp=cp, of_=50.0, rc=50.0,
                           sigma={"ds": ds_sigma})  # only ds is data-driven; rest adaptive
    # Count on-site referrers & competitors for adaptive uncertainty reduction.
    on_site = sum(1 for r in referrals_list
                  if (r.get("distance_mi") if r.get("distance_mi") is not None else 9) <= 0.2)
    n_comp = len([c for c in comp_dicts if c.get("competition_score", 0) > 0])
    mc = lvi.monte_carlo_lvi(inputs, on_site_count=on_site, competitor_count=n_comp)
    report["lvi_inputs"] = inputs.__dict__
    report["lvi_summary"] = mc
    report["lvi_sensitivity"] = lvi.first_order_sensitivity(inputs)
    report["acs_moe"] = {"income": inc, "income_moe": inc_moe,
                         "age_moe": tract_profile.age_moe if tract_profile else None}

    step("Running ZIP-basket demographic statistics...")
    try:
        neighbor_zips = stats.guess_neighboring_zips(geo.zip_code)
        basket = [geo.zip_code] + neighbor_zips
        profiles = demographics.get_neighboring_zctas_profiles(basket, census_api_key)
        stat_result = stats.build_zip_basket_analysis(profiles)
        report["zip_basket_stats"] = {
            "n": stat_result["n"],
            "income_corr": stat_result["income_corr"],
            "age_corr": stat_result["age_corr"],
            "pop_corr": stat_result["pop_corr"],
            "caveat": stat_result["caveat"],
            "rows": [r.__dict__ for r in stat_result["rows"]],
        }
        # Real (non-circular) external test: supply-per-capita vs income.
        try:
            report["external_regression"] = stats.external_supply_regression(
                report["zip_basket_stats"]["rows"], state_abbr)
        except Exception:
            pass
    except Exception as e:
        report["errors"].append(f"ZIP-basket statistics failed: {e}")

    step("Pulling CDC PLACES disease-burden (OSA risk) for the demand surface...")
    osa_index = 1.0
    try:
        import epi
        places = epi.get_places_osa_base(geo.geoid_tract)
        osa_index = places.get("osa_index", 1.0) or 1.0
        report["places"] = places
    except Exception as e:
        report["errors"].append(f"CDC PLACES fetch failed: {e}")

    step("Geocoding competitors to real coordinates + running spatial models...")
    try:
        import spatial, nppes, geocode as _geo, re as _re2
        fshare = (zcta_profile.female_share if zcta_profile and zcta_profile.female_share else 0.51)

        # Demand points = nearby ZIPs weighted by EXPECTED CASES (epi-weighted),
        # not raw population.
        demand = []
        for row in report.get("zip_basket_stats", {}).get("rows", []):
            z = str(row.get("zip_code", ""))
            cen = nppes._zip_centroid(z)
            if not cen:
                continue
            cases = epi.expected_cases(row.get("population"), row.get("median_age"),
                                       row.get("median_income"), osa_index, fshare)
            if cases > 0:
                demand.append(spatial.DemandPoint(cen[0], cen[1], cases, label=f"ZIP {z}",
                                                  headcount=float(row.get("population") or 0)))

        # Facilities at REAL geocoded coordinates (street address), ZIP-centroid
        # only as last resort; retirement-adjusted; capacity from tier.
        facilities = []
        for c in report.get("competitors", []):
            if not (c.get("competition_score", 0) > 0):
                continue
            flat, flon = c.get("lat", 0.0), c.get("lon", 0.0)
            addr = c.get("address", "") or ""
            if (not flat or not flon) and addr and not addr.startswith("ZIP"):
                coords = _geo.geocode_oneline(addr)
                if coords:
                    flat, flon = coords
            if not flat or not flon:
                zs = _re2.findall(r"\b\d{5}\b", addr)   # ZIP is the LAST 5-digit group
                cen = nppes._zip_centroid(zs[-1]) if zs else None
                if cen:
                    flat, flon = cen
            if flat and flon:
                score = float(c.get("competition_score", 0) or 0)
                facilities.append(spatial.Facility(
                    name=c.get("name", ""), lat=flat, lon=flon,
                    attractiveness=score,
                    retire_prob=float(c.get("retire_prob", 0) or 0),
                    # supply units scale with how specialist the practice is — a
                    # score-17 general dentist is not a full OFP-specialist unit.
                    capacity=max(0.1, score / 90.0)))

        sp = spatial.compute_all(geo.lat, geo.lon, facilities, demand, clinic_attractiveness=70.0)
        report["spatial"] = {
            "ok": sp.ok, "note": sp.note, "verdict": sp.verdict, "rows": sp.rows,
            "huff_share_pct": sp.huff_share_pct, "huff_lo": sp.huff_lo, "huff_hi": sp.huff_hi,
            "huff_launch_pct": sp.huff_launch_pct, "huff_captured_pop": sp.huff_captured_pop,
            "mci_share_pct": sp.mci_share_pct, "sfca_index": sp.sfca_index, "sfca_pct": sp.sfca_pct,
            "pmedian_efficiency_pct": sp.pmedian_efficiency_pct,
            "breakpoint_mi": sp.breakpoint_mi, "nn_index": sp.nn_index,
        }

        # Unit-economics overlay: does predicted capture clear break-even?
        try:
            import econ
            er = econ.proforma(sp.huff_captured_pop or 0)
            report["econ"] = {
                "fixed_annual": er.fixed_annual, "contribution_per_case": er.contribution_per_case,
                "break_even_cases": er.break_even_cases, "projected_cases": er.projected_cases,
                "margin_cases": er.margin_cases, "verdict": er.verdict,
            }
        except Exception as e:
            report["errors"].append(f"Unit-economics overlay failed: {e}")
    except Exception as e:
        report["errors"].append(f"Spatial models failed: {e}")

    step("Scoring site against the Orofacial-Pain / TMJ / DSM selection rubric...")
    try:
        import site_selection
        report["site_selection"] = site_selection.score_site(report)
    except Exception as e:
        report["errors"].append(f"Site-selection scoring failed: {e}")

    step("Report complete.")
    return report
