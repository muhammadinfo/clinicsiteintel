"""Expert site-selection scoring for an Orofacial Pain / TMJ / Dental Sleep
Medicine practice — a 100-point rubric (plus a +10 bonus) layered on top of the
engine's already-validated sub-scores.

Rubric (matches the consultant brief):
  1. Referral Potential        30   <- LVI referral-access factor (rp)
  2. Medical Building Strength  20   <- LVI medical-hub factor (if_)
  3. Nearby Hospital Ecosystem  10   <- proxy from hub + on-site density (LOW conf.)
  4. Competition Penalty        25   <- LVI competition factor (cp)
  5. Demographics                5   <- LVI demographic-fit factor (ds)
  6. Accessibility               5   <- not yet measured -> neutral (LOW conf.)
  7. Growth Potential            5   <- not yet measured -> neutral (LOW conf.)
  Bonus                        +10   <- large multi-specialty / on-site cluster

Where a category has no live data source yet, it is scored neutrally and flagged
LOW confidence rather than guessed — per the brief's instruction.
"""
import re


def _norm_street(a: str) -> str:
    """Normalize 'street number + street name' so a referrer at the SAME building
    address can be matched (suite numbers stripped)."""
    a = (a or "").upper()
    m = re.match(r"\s*(\d+)\s+([A-Z0-9 ]+?)(,| STE| SUITE| #| APT|$)", a)
    return (m.group(1) + " " + m.group(2).strip()) if m else ""


# Referral specialties that actually send Orofacial Pain / DSM patients.
_REFERRAL_BUCKETS = {
    "Sleep Medicine", "ENT", "Neurology", "Pulmonology", "Pain Medicine",
    "PM&R", "Rheumatology", "Cardiology", "Primary Care", "Oral Surgery",
    "Orthopedics", "Neurosurgery", "Allergy", "Psychiatry", "Pediatrics",
    "Sports Medicine", "Endocrinology",
}


def _bucket(specialty: str) -> str:
    s = (specialty or "").lower()
    if "sleep medicine" in s: return "Sleep Medicine"
    if "otolaryngolog" in s or "ent" in s: return "ENT"
    if "neurosurg" in s: return "Neurosurgery"
    if "neurolog" in s: return "Neurology"
    if "pulmonolog" in s: return "Pulmonology"
    if "pain medicine" in s or "pain management" in s: return "Pain Medicine"
    if "physical medicine" in s or "rehab" in s: return "PM&R"
    if "rheumatolog" in s: return "Rheumatology"
    if "cardiolog" in s: return "Cardiology"
    if "psychiat" in s: return "Psychiatry"
    if "pediatric" in s: return "Pediatrics"
    if ("family medicine" in s or "internal medicine" in s or "primary care" in s
            or "general practice" in s): return "Primary Care"
    if "sports" in s: return "Sports Medicine"
    if "orthop" in s: return "Orthopedics"
    if "allerg" in s: return "Allergy"
    if "endocrin" in s: return "Endocrinology"
    if "oral" in s and "surg" in s: return "Oral Surgery"
    return "Other"


def _band(total: float):
    if total >= 75:
        return "STRONG SITE", "#34c759"
    if total >= 60:
        return "VIABLE", "#34c759"
    if total >= 45:
        return "CONDITIONAL", "#ff9f0a"
    return "WEAK", "#ff3b30"


def score_site(rep: dict) -> dict:
    inp = rep.get("lvi_inputs") or {}
    rp = float(inp.get("rp", 0) or 0)       # referral access 0-100
    if_ = float(inp.get("if_", 0) or 0)     # medical-hub infra 0-100
    cp = float(inp.get("cp", 0) or 0)       # competition 0-100 (higher = less compressed)
    ds = float(inp.get("ds", 0) or 0)       # demographic fit 0-100

    refs = [r for r in rep.get("referrals", []) if str(r.get("category", "")).startswith("Physician")]
    comps = rep.get("competitors", [])
    specs = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]

    tgt_street = _norm_street((rep.get("geo") or {}).get("matched_address") or "")
    counts, onsite = {}, {}
    in_building = near = within2 = 0   # proximity bands (real geocoded distance)
    for r in refs:
        b = _bucket(r.get("specialty"))
        counts[b] = counts.get(b, 0) + 1
        d = r.get("distance_mi")
        same = bool(tgt_street) and _norm_street(r.get("address")) == tgt_street
        zip_fallback = d is not None and abs(d - 0.6) < 0.001   # ZIP-centroid placeholder
        if same:
            in_building += 1
            onsite[b] = onsite.get(b, 0) + 1
        elif d is not None and not zip_fallback and d <= 0.5:
            near += 1
        if d is not None and not zip_fallback and d <= 2.0 and not same:
            within2 += 1
    n_ref = sum(v for k, v in counts.items() if k in _REFERRAL_BUCKETS)
    n_onsite = in_building

    spec_dists = [c.get("distance_mi") for c in specs if c.get("distance_mi") is not None]
    nearest = min(spec_dists) if spec_dists else None

    # ---- Referral Potential (30): BLEND of true proximity (same-building /
    # nearby) and ZIP-level physician density. A standalone building with no
    # in-building MDs can no longer max this out on ZIP density alone. ----
    proximity = (min(1.0, in_building / 10.0) * 0.6
                 + min(1.0, near / 12.0) * 0.3
                 + min(1.0, within2 / 30.0) * 0.1)          # 0-1, building-anchored
    density = rp / 100.0                                     # ZIP density (kept heavy)
    referral_pts = round((0.55 * proximity + 0.45 * density) * 30, 1)

    # ---- Medical Building Strength (20): dominated by physicians AT the address. ----
    building_pts = round((min(1.0, in_building / 12.0) * 0.7 + (if_ / 100) * 0.3) * 20, 1)

    # ---- Hospital ecosystem (10): proxy from real on-site/near medical density. ----
    hosp_proxy = min(1.0, (if_ / 100) * 0.4 + min(1.0, (in_building + near) / 15.0) * 0.6)
    hospital_pts = round(hosp_proxy * 10, 1)
    competition_pts = round(cp / 100 * 25, 1)
    demo_pts = round(ds / 100 * 10, 1)   # 10 (was 5) — population/demand weighted up
    access_pts = 1.5     # max 3, neutral until parking/freeway data is collected
    growth_pts = 1.0     # max 2, neutral until growth data is collected

    bonus = 0.0
    if n_onsite >= 30:
        bonus += 4
    elif n_onsite >= 15:
        bonus += 2
    key = ["Primary Care", "ENT", "Neurology", "Pulmonology", "Sleep Medicine"]
    multi = sum(1 for k in key if counts.get(k, 0) >= 2)
    bonus += min(6, multi * 1.5)
    bonus = round(min(10, bonus), 1)

    base = round(referral_pts + building_pts + hospital_pts + competition_pts
                 + demo_pts + access_pts + growth_pts, 1)
    total = round(min(100.0, base) + bonus, 1)
    band, color = _band(total)

    n_cred = sum(1 for c in specs if c.get("credentials"))

    return {
        "total": total,
        "base": base,
        "bonus": bonus,
        "band": band,
        "color": color,
        "categories": [
            {"name": "Referral Potential", "score": referral_pts, "max": 30,
             "basis": f"{in_building} in-building + {near} within ½ mi; {n_ref} in ZIP",
             "confidence": "High"},
            {"name": "Medical Building Strength", "score": building_pts, "max": 20,
             "basis": f"{in_building} physician(s) at this exact address",
             "confidence": "High"},
            {"name": "Nearby Hospital Ecosystem", "score": hospital_pts, "max": 10,
             "basis": "Proxy from hub strength + on-site cluster", "confidence": "Low — hospital/imaging/sleep-lab proximity not yet measured"},
            {"name": "Competition Penalty", "score": competition_pts, "max": 25,
             "basis": f"{len(specs)} credentialed specialists"
                      + (f", nearest {nearest:.2f} mi" if nearest is not None else ""), "confidence": "High"},
            {"name": "Demographics", "score": demo_pts, "max": 10,
             "basis": "Income / age / population fit to cash-pay cohort", "confidence": "Medium — education/insurance mix not yet pulled"},
            {"name": "Accessibility", "score": access_pts, "max": 3,
             "basis": "Neutral default", "confidence": "Low — parking/freeway/ADA not yet measured"},
            {"name": "Growth Potential", "score": growth_pts, "max": 2,
             "basis": "Neutral default", "confidence": "Low — development pipeline not yet measured"},
        ],
        "deliverables": {
            "referring_physicians_total": n_ref,
            "in_building_physicians": in_building,
            "within_half_mile": near,
            "on_site_physicians": n_onsite,
            "by_specialty": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "specialist_competitors": len(specs),
            "credentialed_competitors": n_cred,
            "nearest_competitor_mi": nearest,
            "not_yet_collected": ["Imaging centers", "Sleep labs", "Physical therapists",
                                  "Chiropractors", "Hospital drive distance", "Parking / ADA"],
        },
        "confidence_overall": "Moderate — referral, building, competition and demographics are data-backed; "
                              "hospital-ecosystem, accessibility and growth are proxied and flagged.",
        "recommendation": _recommend(band, n_ref, len(specs), nearest),
    }


def _recommend(band, n_ref, n_spec, nearest):
    if band in ("STRONG SITE", "VIABLE"):
        return (f"Pursue. {n_ref} referring physicians anchor a referral-driven build; "
                "lock the on-site/in-building relationships first.")
    if band == "CONDITIONAL":
        nn = f" with a credentialed rival {nearest:.2f} mi away" if nearest is not None else ""
        return (f"Conditional. Strong referral substrate ({n_ref} physicians) offset by {n_spec} "
                f"specialist competitors{nn} — proceed only with a clear differentiation + referral plan.")
    return ("Caution. Competitive/structural headwinds outweigh the referral base here; "
            "prefer a less saturated, more referral-dense building.")
