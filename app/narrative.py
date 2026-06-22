"""Consultant's Read — turns a finished report dict into a plain-English,
human-consultant-style explanation of WHAT the verdict is made of and WHY.

Two parts:
  1. A contribution table — each LVI factor's sub-score and the exact points it
     adds to (or subtracts from) the 0-100 index, so the score is fully sourced.
  2. A narrative — short paragraphs walking the drivers the way a site-selection
     consultant would talk you through them.

Returns an HTML fragment with inline styles, so the desktop app and the PWA
backend can both render it without sharing a UI framework.
"""

DEFAULT_PAL = {
    "text": "#1c1c1e", "text2": "#3a3a3c", "text3": "#8e8e93",
    "blue": "#0a84ff", "green": "#34c759", "amber": "#ff9f0a", "red": "#ff3b30",
    "surface2": "#f2f2f7", "border": "#e5e5ea",
}

# LVI = 0.35 Ds + 0.25 Rp + 0.20 If + 0.10 Cp + 0.10 Of - 0.20 Rc
FACTORS = [
    ("Demographics",      0.35, "ds",  "the affluent 40+ cash-pay base that OSA/TMD care depends on"),
    ("Referral access",   0.25, "rp",  "physicians who send OSA/TMD cases, weighted by how close they are"),
    ("Site / medical-hub",0.20, "if_", "whether the address itself is a proven medical building"),
    ("Competition",       0.10, "cp",  "how much nearby specialists compress your share"),
    ("Operations",        0.10, "of_", "hours / access / lease fit — neutral until you enter specifics"),
    ("Rent burden",      -0.20, "rc",  "rent vs the submarket — neutral until you price a real suite"),
]


def _money(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "n/a"


def _band(mean):
    if mean is None:
        return ("indeterminate", "#8e8e93")
    if mean >= 65:
        return ("a site I'd pursue", "#34c759")
    if mean >= 50:
        return ("a site worth pursuing — with conditions", "#ff9f0a")
    return ("a site I'd approach with real caution", "#ff3b30")


def verdict_band(mean):
    """Short verdict label + color for a header badge (desktop & web)."""
    if mean is None:
        return ("INDETERMINATE", DEFAULT_PAL["text3"])
    if mean >= 65:
        return ("PURSUE", DEFAULT_PAL["green"])
    if mean >= 50:
        return ("PURSUE WITH CONDITIONS", DEFAULT_PAL["amber"])
    return ("CAUTION", DEFAULT_PAL["red"])


def build_consultant_read(rep: dict, pal: dict = None) -> str:
    P = pal or DEFAULT_PAL
    inp = rep.get("lvi_inputs") or {}
    summ = rep.get("lvi_summary") or {}
    mean = summ.get("mean")
    point = summ.get("point_estimate") or mean
    p05, p95 = summ.get("p05"), summ.get("p95")

    demo = rep.get("demographics_zip") or rep.get("demographics_tract") or {}
    income, age = demo.get("median_household_income"), demo.get("median_age")
    comps = rep.get("competitors", [])
    specialists = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
    spec_dists = [c.get("distance_mi") for c in specialists if c.get("distance_mi") is not None]
    nearest = min(spec_dists) if spec_dists else None
    refs = rep.get("referrals", [])
    n_md = sum(1 for r in refs if str(r.get("category", "")).startswith("Physician"))
    colocated = [r for r in refs if (r.get("distance_mi") if r.get("distance_mi") is not None else 9) <= 0.2]
    sp = rep.get("spatial") or {}
    huff, launch = sp.get("huff_share_pct"), sp.get("huff_launch_pct")
    econ = rep.get("econ") or {}
    proj, be = econ.get("projected_cases"), econ.get("break_even_cases")

    band_txt, band_col = _band(mean)

    # ---- 1) Contribution table: each factor's points into the index ----------
    rows = []
    for label, w, key, _desc in FACTORS:
        val = inp.get(key)
        if val is None:
            continue
        contrib = w * val
        rows.append((label, val, w, contrib))
    rows.sort(key=lambda r: abs(r[3]), reverse=True)

    tbl = (f"<table style='width:100%; border-collapse:collapse; margin:4px 0 14px;'>"
           f"<tr style='color:{P['text3']}; font-size:11px; text-transform:uppercase;'>"
           f"<td style='padding:4px 6px;'>Factor</td>"
           f"<td style='padding:4px 6px;'>Sub-score</td>"
           f"<td style='padding:4px 6px;'>Weight</td>"
           f"<td style='padding:4px 6px;'>Points into score</td></tr>")
    for label, val, w, contrib in rows:
        col = P["green"] if contrib >= 0 else P["red"]
        sign = "+" if contrib >= 0 else "−"
        tbl += (f"<tr>"
                f"<td style='padding:6px 6px; border-top:1px solid {P['border']}; color:{P['text']}; font-weight:600;'>{label}</td>"
                f"<td style='padding:6px 6px; border-top:1px solid {P['border']}; color:{P['text2']};'>{val:.0f}/100</td>"
                f"<td style='padding:6px 6px; border-top:1px solid {P['border']}; color:{P['text3']};'>{int(w*100):+d}%</td>"
                f"<td style='padding:6px 6px; border-top:1px solid {P['border']}; color:{col}; font-weight:700;'>{sign}{abs(contrib):.1f}</td>"
                f"</tr>")
    tbl += (f"<tr><td colspan='3' style='padding:7px 6px; border-top:2px solid {P['border']}; "
            f"color:{P['text']}; font-weight:700;'>Location Viability Index</td>"
            f"<td style='padding:7px 6px; border-top:2px solid {P['border']}; color:{band_col}; "
            f"font-weight:800;'>{point if point is not None else '—'}</td></tr></table>")

    # ---- 2) Narrative paragraphs --------------------------------------------
    paras = []
    rng = (f" (I'm ~90% confident the true value sits between <b>{p05}</b> and <b>{p95}</b>)"
           if p05 is not None and p95 is not None else "")
    paras.append(
        f"<b>Bottom line.</b> On the numbers this is <b style='color:{band_col};'>{band_txt}</b> — "
        f"a Location Viability Index of <b>{point}</b> out of 100{rng}. Here's what's underneath that.")

    if income is not None and age is not None:
        strength = "carries the score" if (income >= 110000 and 40 <= age <= 55) else \
                   "is a solid base" if income >= 90000 else "is the soft spot"
        paras.append(
            f"<b>Demographics do the heavy lifting (35% of the index).</b> Median household income here is "
            f"<b>{_money(income)}</b> and median age <b>{age}</b>. Your OSA/TMD patients are the affluent, 40-plus, "
            f"often-cash-pay cohort, and this tract {strength}. Because it's the most heavily weighted factor, a strong "
            f"reading here is what pulls the whole verdict up.")

    if n_md:
        co = (f" — and <b>{len(colocated)}</b> are clustered within ~0.2 mi of the site itself"
              if colocated else "")
        paras.append(
            f"<b>Referral access is the second engine (25%).</b> I count <b>{n_md}</b> referring physicians in the "
            f"catchment — sleep medicine, ENT, neurology, primary care{co}. For a referral-fed specialty that's the "
            f"single most valuable thing a site can offer, which is why a doctor on your block counts for far more here "
            f"than one across the ZIP.")

    if specialists:
        near = f"the nearest about <b>{nearest:.1f} mi</b> away" if nearest is not None else "nearby"
        verdict_word = "near you, but not on top of you" if (nearest or 9) >= 1 else "uncomfortably close"
        paras.append(
            f"<b>Competition trims, it doesn't sink (10%, a deduction).</b> There are <b>{len(specialists)}</b> "
            f"credentialed orofacial-pain/TMJ/sleep specialists in the trade area, {near} — {verdict_word}. This is a "
            f"contested market, so the play is differentiation and referral relationships rather than being first; the "
            f"distance math is measured from each competitor's real street address, not a ZIP approximation.")
    else:
        paras.append(
            "<b>Competition looks open (10%).</b> No credentialed orofacial-pain specialist surfaced in the trade "
            "area — a genuine opening, but verify it before leaning on it.")

    checks = []
    if huff is not None:
        lr = f", or ~<b>{launch}%</b> in the opening year before you mature" if launch is not None else ""
        checks.append(f"a gravity model predicts you'd capture ~<b>{huff}%</b> of expected demand{lr}")
    if be and proj is not None:
        verdict = "clears break-even with cushion" if proj >= 1.5 * be else \
                  "lands roughly at break-even" if proj >= be else "falls short of break-even on default assumptions"
        checks.append(f"unit economics project ~<b>{proj:,.0f}</b> cases/yr against the <b>~{be:,.0f}</b> you need to "
                      f"break even, so it {verdict}")
    if checks:
        paras.append("<b>Two reality checks on top of the index:</b> " + "; and ".join(checks) + ".")

    paras.append(
        f"<b>What's deliberately not in this number yet.</b> Operations and rent are held neutral until you price a "
        f"specific suite on the Real Estate tab — so think of <b>{point}</b> as the quality of the <i>location</i>, "
        f"before the economics of any one lease. Drop in a real rent and square footage and the index re-prices for "
        f"that suite.")

    body = (tbl + "".join(
        f"<p style='margin:0 0 9px; line-height:1.55; color:{P['text']}; font-size:13px;'>{p}</p>" for p in paras))
    return body
