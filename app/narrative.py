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


# ---------------------------------------------------------------------------
# Premium full-page summary (for the desktop QWebEngineView). Detailed,
# explanatory, graphical — a consultant deliverable a client would pay for.
# ---------------------------------------------------------------------------

_SUMMARY_CSS = """
:root{--bg:#f5f5f7;--card:#ffffff;--ink:#1d1d1f;--ink2:#424245;--ink3:#86868b;
--line:#e8e8ed;--blue:#0071e3;--green:#34c759;--amber:#ff9f0a;--red:#ff3b30;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;line-height:1.5;padding:34px 30px 60px;}
.wrap{max-width:920px;margin:0 auto;}
.addr-kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);font-weight:600;}
.addr{font-size:30px;font-weight:700;letter-spacing:-.02em;margin:4px 0 2px;}
.coords{font-size:13px;color:var(--ink3);}
.hero{background:var(--card);border-radius:24px;padding:30px 34px;margin:22px 0 26px;
box-shadow:0 1px 2px rgba(0,0,0,.04),0 18px 40px -22px rgba(0,0,0,.18);
display:grid;grid-template-columns:300px 1fr;gap:34px;align-items:center;}
.score-num{font-size:84px;font-weight:800;line-height:.95;letter-spacing:-.04em;}
.score-out{font-size:22px;font-weight:600;color:var(--ink3);}
.verdict-pill{display:inline-block;padding:7px 18px;border-radius:980px;color:#fff;
font-weight:700;font-size:15px;letter-spacing:.01em;margin-top:14px;}
.ci{font-size:13px;color:var(--ink3);margin-top:12px;}
.ci b{color:var(--ink2);}
.gauge-label{font-size:13px;color:var(--ink3);margin-bottom:9px;font-weight:600;}
.gauge{position:relative;height:16px;border-radius:980px;
background:linear-gradient(90deg,#ff3b30 0%,#ff9f0a 48%,#34c759 100%);}
.gauge .ci-band{position:absolute;top:0;bottom:0;background:rgba(255,255,255,.45);
border-left:1px solid rgba(0,0,0,.18);border-right:1px solid rgba(0,0,0,.18);}
.gauge .mark{position:absolute;top:-7px;width:6px;height:30px;border-radius:6px;
background:#1d1d1f;box-shadow:0 2px 6px rgba(0,0,0,.35);transform:translateX(-3px);}
.gauge-scale{display:flex;justify-content:space-between;font-size:11px;color:var(--ink3);margin-top:8px;}
.tldr{background:var(--card);border-radius:20px;padding:22px 26px;margin-bottom:26px;
border:1px solid var(--line);font-size:16px;line-height:1.6;color:var(--ink);}
.tldr b{color:var(--ink);}
.sec-title{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);
font-weight:700;margin:30px 4px 14px;}
.factor{background:var(--card);border-radius:18px;padding:18px 22px;margin-bottom:12px;
border:1px solid var(--line);box-shadow:0 8px 22px -20px rgba(0,0,0,.25);}
.factor-top{display:flex;align-items:center;gap:14px;}
.ficon{width:38px;height:38px;border-radius:11px;display:flex;align-items:center;
justify-content:center;font-size:19px;flex:0 0 auto;}
.fname{font-size:16px;font-weight:600;flex:1;}
.fweight{font-size:12px;color:var(--ink3);font-weight:600;}
.fchip{font-size:15px;font-weight:800;min-width:64px;text-align:right;}
.fbar-row{display:flex;align-items:center;gap:12px;margin:12px 0 9px;}
.fbar{flex:1;height:9px;border-radius:980px;background:#ececf1;overflow:hidden;}
.fbar-fill{height:100%;border-radius:980px;}
.fscore{font-size:12px;color:var(--ink3);font-weight:600;min-width:52px;text-align:right;}
.fexpl{font-size:14px;color:var(--ink2);line-height:1.55;}
.checks{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px;}
.check{background:var(--card);border-radius:18px;padding:20px 22px;border:1px solid var(--line);
box-shadow:0 8px 22px -20px rgba(0,0,0,.25);}
.check .k{font-size:13px;color:var(--ink3);font-weight:600;}
.check .v{font-size:30px;font-weight:800;letter-spacing:-.02em;margin:4px 0 2px;}
.check .d{font-size:13px;color:var(--ink2);line-height:1.5;}
.prose{margin-top:6px;}
.prose p{font-size:15px;line-height:1.62;color:var(--ink);margin-bottom:12px;}
.prose b{font-weight:700;}
.foot{font-size:13px;color:var(--ink3);line-height:1.55;margin-top:22px;
padding:16px 20px;background:var(--card);border:1px solid var(--line);border-radius:16px;}
sup{font-size:.68em;}
@media(max-width:640px){
body{padding:18px 14px 48px;}
.addr{font-size:23px;}
.hero{grid-template-columns:1fr;gap:18px;padding:22px 20px;border-radius:20px;}
.score-num{font-size:62px;}
.checks{grid-template-columns:1fr;}
.fexpl{font-size:13.5px;}
.tldr{font-size:15px;padding:18px 20px;}
}
"""

_FACTOR_META = {
    "ds":  ("\U0001F4CA", "#0071e3", "#e6f1fb"),
    "rp":  ("\U0001F91D", "#34c759", "#e7f8ec"),
    "if_": ("\U0001F3E5", "#5e5ce6", "#ecebfd"),
    "cp":  ("⚔️", "#ff3b30", "#fdeceb"),
    "of_": ("⚙️", "#8e8e93", "#f0f0f3"),
    "rc":  ("\U0001F4B5", "#ff9f0a", "#fdf2e0"),
}


def _zone_color(v):
    if v is None:
        return "#86868b"
    if v >= 65:
        return "#34c759"
    if v >= 50:
        return "#ff9f0a"
    return "#ff3b30"


def build_summary_html(rep: dict) -> str:
    inp = rep.get("lvi_inputs") or {}
    summ = rep.get("lvi_summary") or {}
    mean = summ.get("mean")
    point = summ.get("point_estimate") or mean or 0
    p05, p95 = summ.get("p05"), summ.get("p95")
    band, _ = verdict_band(mean)
    zc = _zone_color(mean)

    geo = rep.get("geo") or {}
    addr = (geo.get("matched_address") or rep.get("address_input") or "").upper()
    demo = rep.get("demographics_zip") or rep.get("demographics_tract") or {}
    income, age = demo.get("median_household_income"), demo.get("median_age")
    pop = demo.get("population")
    comps = rep.get("competitors", [])
    specialists = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
    sdists = [c.get("distance_mi") for c in specialists if c.get("distance_mi") is not None]
    nearest = min(sdists) if sdists else None
    refs = rep.get("referrals", [])
    n_md = sum(1 for r in refs if str(r.get("category", "")).startswith("Physician"))
    colocated = sum(1 for r in refs if (r.get("distance_mi") if r.get("distance_mi") is not None else 9) <= 0.2)
    sp = rep.get("spatial") or {}
    huff, launch = sp.get("huff_share_pct"), sp.get("huff_launch_pct")
    econ = rep.get("econ") or {}
    proj, be = econ.get("projected_cases"), econ.get("break_even_cases")
    places = rep.get("places") or {}
    moe = rep.get("acs_moe") or {}
    ds_v, rp_v, if_v, cp_v = (inp.get("ds", 0), inp.get("rp", 0), inp.get("if_", 0), inp.get("cp", 0))
    inc_fit = max(0.0, min(100.0, income / 150000 * 100)) if income else None
    age_fit = max(0.0, min(100.0, 100 - abs((age if age is not None else 48) - 48) * 4)) if age is not None else None

    # Per-factor explanations, grounded in the actual formulas behind each sub-score.
    def expl(key):
        if key == "ds":
            bits = []
            if inc_fit is not None:
                bits.append(f"income {_money(income)} scores {inc_fit:.0f}/100 against a $150k benchmark")
            if age_fit is not None:
                bits.append(f"median age {age} scores {age_fit:.0f}/100 (fit peaks 42&ndash;55)")
            return (f"{'; '.join(bits)}. Blended 50% income / 35% age / 15% population gives Dₛ "
                    f"<b>{ds_v:.0f}/100</b> &mdash; at 35% weight, the single largest contribution to the index.")
        if key == "rp":
            co = f" <b>{colocated}</b> sit within ~0.2&nbsp;mi (in or beside the building)." if colocated else ""
            return (f"<b>{n_md}</b> referring physicians in the catchment &mdash; sleep medicine, ENT, neurology, "
                    f"primary care.{co} Each is scored by specialty fit &times; distance decay e<sup>&minus;(mi/3)</sup>, "
                    f"with a 2.5&times; bonus for a high-value referrer essentially on-site; summed access maps to "
                    f"Rₚ <b>{rp_v:.0f}/100</b> (+{0.25*rp_v:.1f} pts).")
        if key == "if_":
            return (f"<b>{colocated}</b> registered providers sit within ~0.2&nbsp;mi, so the address itself is a "
                    f"proven medical building. I_f = 50 + 7 per co-located provider (capped) &rarr; "
                    f"<b>{if_v:.0f}/100</b> &mdash; build-out, parking, ADA and anchor traffic already in place.")
        if key == "cp":
            nn = f"nearest <b>{nearest:.1f}&nbsp;mi</b>" if nearest is not None else "none nearby"
            return (f"<b>{len(specialists)}</b> credentialed specialists ({nn}). Competition starts at 70; each rival "
                    f"subtracts 22 &times; credential-weight &times; e<sup>&minus;(mi/4)</sup> &mdash; so a rival at "
                    f"1.1&nbsp;mi removes far less than one at 0.3&nbsp;mi &rarr; Cₚ <b>{cp_v:.0f}/100</b>. "
                    f"Distances are from each rival's real street address.")
        if key == "of_":
            return ("Hours, access and lease fit are held at a neutral 50 until you enter the specifics for a "
                    "particular suite &mdash; so this factor neither helps nor hurts the location score yet.")
        return ("Rent burden vs the submarket is held at a neutral 50 until you price a real listing on the Real "
                "Estate tab; enter a rate and square footage and the index re-prices the &minus;20% rent term.")

    rows = ""
    for label, w, key, _d in FACTORS:
        v = inp.get(key)
        if v is None:
            continue
        icon, color, tint = _FACTOR_META[key]
        contrib = w * v
        chip_col = "#34c759" if contrib >= 0 else "#ff3b30"
        sign = "+" if contrib >= 0 else "−"
        rows += (
            f"<div class='factor'><div class='factor-top'>"
            f"<div class='ficon' style='background:{tint};color:{color};'>{icon}</div>"
            f"<div class='fname'>{label}<div class='fweight'>{int(w*100):+d}% of the index</div></div>"
            f"<div class='fchip' style='color:{chip_col};'>{sign}{abs(contrib):.1f} pts</div></div>"
            f"<div class='fbar-row'><div class='fbar'><div class='fbar-fill' "
            f"style='width:{max(2,min(100,v)):.0f}%;background:{color};'></div></div>"
            f"<div class='fscore'>{v:.0f}<span style='color:#c7c7cc;'> / 100</span></div></div>"
            f"<div class='fexpl'>{expl(key)}</div></div>"
        )

    # gauge marker + CI band positions
    mk = max(0.0, min(100.0, point))
    lo = max(0.0, min(100.0, p05 if p05 is not None else mk))
    hi = max(0.0, min(100.0, p95 if p95 is not None else mk))

    cap_txt = f"{huff:.1f}%" if huff is not None else "n/a"
    cap_d = (f"of expected demand at steady state" + (f"; ~{launch:.1f}% in year one" if launch is not None else ""))
    econ_v = f"{proj:,.0f}" if proj is not None else "n/a"
    econ_d = (f"projected cases/yr vs ~{be:,.0f} to break even — "
              + ("clears with cushion" if (proj and be and proj >= 1.5*be) else
                 "around break-even" if (proj and be and proj >= be) else "below break-even")
              if be else "enter economics to model")

    tldr = (f"On the numbers this is <b style='color:{zc};'>{band.lower()}</b> — a Location Viability Index of "
            f"<b>{point}</b> out of 100"
            + (f", and I'm ~90% confident the true value sits between <b>{p05}</b> and <b>{p95}</b>." if p05 is not None else ".")
            + " Below is exactly what builds that number, factor by factor.")

    cons = build_consultant_read(rep)  # reuse the prose paragraphs (skip its table)
    prose = cons.split("</table>", 1)[1] if "</table>" in cons else cons

    # ---- Disease burden & demand surface (CDC PLACES + epidemiology) ----
    demand_html = ""
    osa_idx = places.get("osa_index")
    if osa_idx is not None or places.get("sleep") is not None:
        bits = []
        if places.get("sleep") is not None: bits.append(f"short-sleep {places['sleep']:.0f}%")
        if places.get("obesity") is not None: bits.append(f"obesity {places['obesity']:.0f}%")
        if places.get("bphigh") is not None: bits.append(f"hypertension {places['bphigh']:.0f}%")
        cap_pop = sp.get("huff_captured_pop")
        cases = cap_pop if cap_pop else proj
        demand_html = (
            "<div class='sec-title'>Disease burden &amp; addressable demand</div>"
            "<div class='factor'><div class='fexpl' style='font-size:14.5px;line-height:1.65;'>"
            + (f"CDC PLACES for this tract: {', '.join(bits)} &rarr; an OSA-risk index of "
               f"<b>{osa_idx:.2f}</b> (1.00 = US-typical). " if (bits and osa_idx is not None) else "")
            + (f"Applied to the 40+ population across the ZIP basket at its cash-pay propensity, the demand surface "
               f"yields roughly <b>{cases:,.0f}</b> addressable OSA/TMD cases per year. " if cases else "")
            + "Because OSA is ~80% undiagnosed, a new specialist expands the market through screening, rather than "
              "merely splitting the diagnosed pool.</div></div>")

    # ---- Spatial demand models (4 models + clustering) ----
    spatial_html = ""
    if sp.get("ok"):
        def mc(label, val, sub):
            return (f"<div class='check'><div class='k'>{label}</div>"
                    f"<div class='v' style='font-size:23px;'>{val}</div><div class='d'>{sub}</div></div>")
        cells = []
        if huff is not None:
            bd = f"band {sp.get('huff_lo','?')}&ndash;{sp.get('huff_hi','?')}%"
            cells.append(mc("Huff gravity (steady state)", f"{huff:.1f}%",
                            f"{bd}; year-1 ~{launch:.1f}%" if launch is not None else bd))
        if sp.get("mci_share_pct") is not None:
            cells.append(mc("MCI share", f"{sp['mci_share_pct']:.1f}%", "multiplicative competitive-interaction model"))
        if sp.get("sfca_pct") is not None:
            cells.append(mc("2SFCA access", f"{sp['sfca_pct']:.0f}th", "spatial-access percentile vs the metro"))
        if sp.get("pmedian_efficiency_pct") is not None:
            cells.append(mc("P-median efficiency", f"{sp['pmedian_efficiency_pct']:.0f}%", "vs the optimal location-allocation point"))
        if sp.get("breakpoint_mi") is not None:
            cells.append(mc("Reilly breakpoint", f"{sp['breakpoint_mi']:.1f} mi", "trade-area boundary to the nearest rival"))
        if sp.get("nn_index") is not None:
            cells.append(mc("Clark&ndash;Evans NN", f"{sp['nn_index']:.2f}", "competitor clustering (1 = random, &lt;1 clustered)"))
        verdict = sp.get("verdict", "")
        spatial_html = ("<div class='sec-title'>Spatial demand models</div>"
                        f"<div class='checks'>{''.join(cells)}</div>"
                        + (f"<div class='foot' style='margin-top:12px;'>{verdict}</div>" if verdict else ""))

    # ---- Unit economics — the break-even equation, with numbers ----
    econ_html = ""
    fa, cpc, mar = econ.get("fixed_annual"), econ.get("contribution_per_case"), econ.get("margin_cases")
    if be and proj is not None:
        ratio = (proj / be) if be else 0
        econ_html = (
            "<div class='sec-title'>Unit economics &mdash; the break-even math</div>"
            "<div class='factor'><div class='fexpl' style='font-size:14.5px;line-height:1.7;'>"
            + (f"Break-even = fixed annual cost <b>{_money(fa)}</b> &divide; contribution per case <b>{_money(cpc)}</b> "
               f"= <b>{be:,.0f}</b> cases/yr. " if (fa and cpc) else f"Break-even &asymp; <b>{be:,.0f}</b> cases/yr. ")
            + f"Projected capture is <b>{proj:,.0f}</b> cases/yr, a margin of "
              f"<b>{(mar if mar is not None else proj-be):,.0f}</b> cases &mdash; <b>{ratio:.1f}&times;</b> break-even. "
            + ("A comfortable cushion." if ratio >= 1.5 else "Roughly at break-even." if ratio >= 1
               else "Below break-even at default assumptions.")
            + "</div></div>")

    # ---- Uncertainty & sensitivity (Monte Carlo + first-order Sobol) ----
    unc_html = ""
    sens = rep.get("lvi_sensitivity") or []
    sd = summ.get("sd")
    if sd is not None:
        bars = ""
        for lab, pct in sens[:5]:
            bars += (f"<div style='display:flex;align-items:center;gap:10px;margin:7px 0;'>"
                     f"<div style='width:170px;font-size:13px;color:var(--ink2);'>{lab}</div>"
                     f"<div class='fbar' style='flex:1;'><div class='fbar-fill' style='width:{max(2,min(100,pct)):.0f}%;background:#0071e3;'></div></div>"
                     f"<div class='fscore'>{pct:.0f}%</div></div>")
        mo = ""
        if moe.get("income") and moe.get("income_moe"):
            mo = (f" Income carries a &plusmn;{_money(moe['income_moe'])} ACS margin of error, propagated into the "
                  f"score's spread.")
        unc_html = (
            "<div class='sec-title'>Uncertainty &amp; what moves the score</div>"
            "<div class='factor'><div class='fexpl' style='font-size:14.5px;line-height:1.6;'>"
            f"A Monte-Carlo estimate over <b>50,000</b> draws: mean <b>{mean}</b>, SD <b>{sd}</b>, 90% credible "
            f"interval <b>{p05}&ndash;{p95}</b>.{mo} Each input's share of that uncertainty (first-order Sobol):"
            f"</div><div style='margin-top:12px;'>{bars}</div></div>")

    errs = rep.get("errors") or []
    notes_html = ""
    if errs:
        items = "".join(f"<li style='margin-bottom:5px;'>{e}</li>" for e in errs)
        notes_html = ("<div class='sec-title'>Data-gap notes</div>"
                      "<div class='foot' style='color:#9a6a00;'>"
                      f"<ul style='margin:0;padding-left:18px;'>{items}</ul></div>")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_SUMMARY_CSS}</style></head>
<body><div class="wrap">
  <div class="addr-kicker">Site assessment</div>
  <div class="addr">{addr}</div>
  <div class="coords">{geo.get('lat','')}, {geo.get('lon','')} &nbsp;&middot;&nbsp; ZIP {geo.get('zip_code') or 'n/a'}</div>

  <div class="hero">
    <div>
      <div class="score-num" style="color:{zc};">{point}<span class="score-out">/100</span></div>
      <div class="verdict-pill" style="background:{zc};">{band}</div>
      <div class="ci">90% credible interval &nbsp;<b>{p05 if p05 is not None else '—'} &ndash; {p95 if p95 is not None else '—'}</b></div>
    </div>
    <div>
      <div class="gauge-label">Location Viability Index</div>
      <div class="gauge">
        <div class="ci-band" style="left:{lo:.0f}%;width:{max(1,hi-lo):.0f}%;"></div>
        <div class="mark" style="left:{mk:.0f}%;"></div>
      </div>
      <div class="gauge-scale"><span>0 · caution</span><span>50</span><span>100 · strong</span></div>
    </div>
  </div>

  <div class="tldr">{tldr}</div>

  <div class="sec-title">What builds this score</div>
  {rows}

  <div class="sec-title">Reality checks layered on top</div>
  <div class="checks">
    <div class="check"><div class="k">Demand capture (Huff gravity)</div>
      <div class="v" style="color:{zc};">{cap_txt}</div><div class="d">{cap_d}</div></div>
    <div class="check"><div class="k">Unit economics</div>
      <div class="v">{econ_v}</div><div class="d">{econ_d}</div></div>
  </div>

  {spatial_html}
  {demand_html}
  {econ_html}
  {unc_html}

  <div class="sec-title">The consultant's read</div>
  <div class="prose">{prose}</div>

  {notes_html}

  <div class="foot">Real-estate-specific factors (rent, build-out, HVAC) are deliberately held neutral until you price
  a specific suite on the Real Estate tab. Think of this score as the quality of the <b>location</b>, before the
  economics of any one lease.</div>
</div>
<script>
function _csiH(){{try{{parent.postMessage({{csiHeight:document.body.scrollHeight}},'*');}}catch(e){{}}}}
window.addEventListener('load',_csiH);setTimeout(_csiH,300);setTimeout(_csiH,1200);
</script>
</body></html>"""
