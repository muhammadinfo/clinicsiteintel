"""Spatial-interaction & location-allocation models for a candidate clinic site.

Upgraded for accuracy:
  - demand is EXPECTED CASES (epi-weighted), not raw population (see epi.py),
  - facilities are placed at REAL geocoded coordinates (see report.py),
  - Huff/MCI use a specialist-appropriate decay with a SENSITIVITY BAND
    (beta in [1.2, 2.0]) so capture is an interval, not false precision,
  - the clinic carries a LAUNCH-RAMP attractiveness (year-1 vs steady-state),
  - competitors are RETIREMENT-ADJUSTED over the planning horizon,
  - 2SFCA is upgraded to competition-allocated 3SFCA with Gaussian (E2SFCA) decay,
  - adds Reilly's Law / Converse BREAKPOINT vs. the dominant competitor,
  - adds a Clark-Evans nearest-neighbour CLUSTERING test (clustered vs dispersed
    saturation).

Models remain structural estimates over public data, not real patient flows.
"""
from dataclasses import dataclass, field
import math

from geocode import haversine_miles

BETA_LO, BETA_MID, BETA_HI = 1.2, 1.5, 2.0   # specialist-care decay band
MIN_MILES = 0.25
CATCHMENT_MI = 12.0
LAUNCH_RAMP = 0.45        # year-1 attractiveness = 45% of steady-state
HORIZON_YEARS = 10


@dataclass
class Facility:
    name: str
    lat: float
    lon: float
    attractiveness: float            # 0-100 draw proxy
    retire_prob: float = 0.0         # P(exit within horizon)
    capacity: float = 1.0            # supply units (provider count proxy)

    def horizon_attract(self) -> float:
        """Attractiveness discounted for the chance the provider exits over the
        planning horizon (a long-tenured dominant rival is a depreciating threat)."""
        return self.attractiveness * (1.0 - 0.5 * self.retire_prob)


@dataclass
class DemandPoint:
    lat: float
    lon: float
    population: float        # EXPECTED CASES (epi-weighted) — used for Huff/MCI/P-median capture
    label: str = ""
    headcount: float = 0.0   # raw population — used for 2SFCA/3SFCA accessibility (standard basis)


@dataclass
class SpatialResults:
    huff_share_pct: float = None
    huff_lo: float = None
    huff_hi: float = None
    huff_captured_pop: float = None
    huff_launch_pct: float = None
    mci_share_pct: float = None
    sfca_index: float = None
    sfca_reading: str = ""
    sfca_pct: float = None
    pmedian_efficiency_pct: float = None
    pmedian_optimal_label: str = ""
    breakpoint_mi: float = None
    breakpoint_competitor: str = ""
    breakpoint_reading: str = ""
    nn_index: float = None
    nn_reading: str = ""
    verdict: str = ""
    rows: list = field(default_factory=list)
    note: str = ""
    ok: bool = False


def _d(a_lat, a_lon, b_lat, b_lon) -> float:
    return max(MIN_MILES, haversine_miles(a_lat, a_lon, b_lat, b_lon))


def huff(clinic_attract, clinic, competitors, demand, beta):
    tot = sum(dp.population for dp in demand) or 1.0
    captured = 0.0
    for dp in demand:
        u_cl = clinic_attract / (_d(dp.lat, dp.lon, clinic.lat, clinic.lon) ** beta)
        u_co = sum(c.horizon_attract() / (_d(dp.lat, dp.lon, c.lat, c.lon) ** beta)
                   for c in competitors if c.attractiveness > 0)
        denom = u_cl + u_co
        captured += dp.population * ((u_cl / denom) if denom > 0 else 0.0)
    return captured / tot * 100, captured


def mci(clinic, competitors, demand, beta=BETA_MID):
    """Multiplicative Competitive Interaction — multi-attribute attractiveness."""
    def A(f):
        return (max(f.attractiveness, 1) ** 1.0) * (max(f.attractiveness, 1) ** 0.15)
    tot = sum(dp.population for dp in demand) or 1.0
    captured = 0.0
    for dp in demand:
        u_cl = A(clinic) / (_d(dp.lat, dp.lon, clinic.lat, clinic.lon) ** beta)
        u_co = sum(A(c) / (_d(dp.lat, dp.lon, c.lat, c.lon) ** beta)
                   for c in competitors if c.attractiveness > 0)
        denom = u_cl + u_co
        captured += dp.population * ((u_cl / denom) if denom > 0 else 0.0)
    return round(captured / tot * 100, 1)


def sfca3(clinic, competitors, demand, radius=CATCHMENT_MI):
    """Competition-allocated 3SFCA with Gaussian (E2SFCA) distance decay.
    Demand is split among providers Huff-style (avoids 2SFCA double-counting);
    returns accessibility of EXISTING supply at the clinic location per 100k
    expected cases. High = saturated, low = underserved."""
    providers = [c for c in competitors if c.attractiveness > 0]
    if not providers:
        return 0.0, "No existing specialist supply in catchment — strongly underserved."
    sigma = radius / 3.0
    def G(d):
        return math.exp(-(d * d) / (2 * sigma * sigma)) if d <= radius else 0.0
    # Step 0: demand-side selection weights (competition among providers).
    sel = {}
    for dp in demand:
        ws = []
        for prov in providers:
            ws.append(prov.horizon_attract() * G(haversine_miles(dp.lat, dp.lon, prov.lat, prov.lon)))
        s = sum(ws) or 1.0
        sel[id(dp)] = [w / s for w in ws]
    # Step 1: provider supply-to-(allocated)demand ratios. 2SFCA/3SFCA is a
    # population-accessibility measure, so use raw headcount (falls back to the
    # case weight if headcount wasn't supplied).
    def w(dp):
        return dp.headcount or dp.population
    ratios = []
    for jx, prov in enumerate(providers):
        dem = sum(w(dp) * sel[id(dp)][jx] *
                  G(haversine_miles(dp.lat, dp.lon, prov.lat, prov.lon)) for dp in demand)
        ratios.append((prov.capacity / dem) if dem > 0 else 0.0)
    # Step 2: accessibility at a location = Σ R_j · G(d).
    def access_at(lat, lon):
        return sum(ratios[jx] * G(haversine_miles(lat, lon, prov.lat, prov.lon))
                   for jx, prov in enumerate(providers))
    clinic_access = access_at(clinic.lat, clinic.lon)
    index = round(clinic_access * 100000, 2)
    # Scale-free interpretation: the clinic's PERCENTILE vs. accessibility across
    # the demand points (absolute FCA values are unit-sensitive; percentile is not).
    others = sorted(access_at(dp.lat, dp.lon) for dp in demand)
    if others:
        below = sum(1 for v in others if v <= clinic_access)
        pct = round(below / len(others) * 100)
    else:
        pct = 50
    if pct >= 75:
        reading = (f"{pct}th percentile of local accessibility to existing specialists — among the "
                   "BEST-served spots, i.e. most saturated from a new-entrant view.")
    elif pct >= 45:
        reading = f"{pct}th percentile of local accessibility — moderately served; room to differentiate."
    else:
        reading = f"{pct}th percentile of local accessibility — relatively underserved, favorable for entry."
    return index, pct, reading


def p_median(clinic, demand):
    def cost(lat, lon):
        return sum(dp.population * haversine_miles(dp.lat, dp.lon, lat, lon) for dp in demand)
    cc = cost(clinic.lat, clinic.lon)
    if cc <= 0:
        return None, ""
    best, lbl = cc, "the candidate site itself"
    for dp in demand:
        c = cost(dp.lat, dp.lon)
        if c < best:
            best, lbl = c, (dp.label or f"{dp.lat:.3f},{dp.lon:.3f}")
    return round(best / cc * 100, 1), lbl


def reilly_breakpoint(clinic, competitors):
    """Reilly's Law / Converse breakpoint: the trade-area boundary distance
    (measured FROM the clinic) toward the single most dominant competitor.
    Larger = the clinic owns more territory before the rival takes over."""
    rivals = [c for c in competitors if c.attractiveness > 0]
    if not rivals:
        return None, "", "No dominant competitor — open trade area."
    dom = max(rivals, key=lambda c: c.horizon_attract() / max(_d(clinic.lat, clinic.lon, c.lat, c.lon), 0.5))
    D = _d(clinic.lat, clinic.lon, dom.lat, dom.lon)
    a_cl, a_co = max(clinic.attractiveness, 1), max(dom.horizon_attract(), 1)
    # breakpoint distance from the competitor (Converse): D / (1 + sqrt(A_clinic/A_comp))
    bp_from_comp = D / (1 + math.sqrt(a_cl / a_co))
    bp_from_clinic = D - bp_from_comp
    if bp_from_clinic <= 0.6 * D:
        reading = (f"The boundary with {dom.name} sits ~{bp_from_clinic:.1f} mi out of {D:.1f} mi — "
                   "the rival pulls the dividing line toward you (it owns most of the corridor between you).")
    else:
        reading = (f"The boundary with {dom.name} sits ~{bp_from_clinic:.1f} mi out of {D:.1f} mi — "
                   "you hold the larger share of the corridor between you.")
    return round(bp_from_clinic, 1), dom.name, reading


def nearest_neighbor_index(competitors, radius=CATCHMENT_MI):
    """Clark-Evans R: are competitors CLUSTERED (R<1, sidesteppable node) or
    DISPERSED (R>1, genuinely no gap)?"""
    pts = [(c.lat, c.lon) for c in competitors if c.attractiveness > 0]
    n = len(pts)
    if n < 3:
        return None, "Too few competitors to test clustering."
    dnn = []
    for i in range(n):
        dmin = min(haversine_miles(pts[i][0], pts[i][1], pts[j][0], pts[j][1])
                   for j in range(n) if j != i)
        dnn.append(dmin)
    obs = sum(dnn) / n
    area = math.pi * radius * radius
    expected = 0.5 / math.sqrt(n / area)
    R = obs / expected if expected > 0 else 1.0
    if R < 0.8:
        reading = f"Clustered (R={R:.2f}) — competitors bunch together; a gap between clusters may be open."
    elif R > 1.2:
        reading = f"Dispersed (R={R:.2f}) — competitors blanket the area; little open gap."
    else:
        reading = f"Random/even (R={R:.2f}) — no strong clustering signal."
    return round(R, 2), reading


def compute_all(clinic_lat, clinic_lon, competitors, demand,
                clinic_attractiveness=70.0) -> SpatialResults:
    res = SpatialResults()
    if not demand:
        res.note = ("Spatial models need epi-weighted demand points (nearby ZIP expected-case "
                    "estimates). None were available for this address.")
        return res
    clinic = Facility("Candidate clinic", clinic_lat, clinic_lon, clinic_attractiveness)
    ncomp = len([c for c in competitors if c.attractiveness > 0])

    # Huff with sensitivity band + launch ramp
    mid, captured = huff(clinic.attractiveness, clinic, competitors, demand, BETA_MID)
    lo, _ = huff(clinic.attractiveness, clinic, competitors, demand, BETA_HI)   # high decay = low reach
    hi, _ = huff(clinic.attractiveness, clinic, competitors, demand, BETA_LO)   # low decay = high reach
    launch, _ = huff(clinic.attractiveness * LAUNCH_RAMP, clinic, competitors, demand, BETA_MID)
    res.huff_share_pct, res.huff_captured_pop = round(mid, 1), round(captured)
    res.huff_lo, res.huff_hi = round(min(lo, hi), 1), round(max(lo, hi), 1)
    res.huff_launch_pct = round(launch, 1)

    res.mci_share_pct = mci(clinic, competitors, demand)
    res.sfca_index, res.sfca_pct, res.sfca_reading = sfca3(clinic, competitors, demand)
    res.pmedian_efficiency_pct, res.pmedian_optimal_label = p_median(clinic, demand)
    res.breakpoint_mi, res.breakpoint_competitor, res.breakpoint_reading = reilly_breakpoint(clinic, competitors)
    res.nn_index, res.nn_reading = nearest_neighbor_index(competitors)

    res.rows = [
        ("Huff Gravity Model (steady-state)",
         f"{res.huff_share_pct}% capture  (band {res.huff_lo}–{res.huff_hi}%)",
         f"≈{res.huff_captured_pop:,} expected cases captured vs. {ncomp} competitors; band spans "
         f"specialist→retail distance-decay (β {BETA_LO}–{BETA_HI})."),
        ("Huff — Year-1 launch ramp", f"{res.huff_launch_pct}% capture",
         f"At {int(LAUNCH_RAMP*100)}% of steady-state attractiveness (no reviews/reputation yet) — "
         "the realistic opening-year share before the practice matures."),
        ("MCI (Multiplicative Competitive Interaction)", f"{res.mci_share_pct}% capture",
         "Multi-attribute cross-check on Huff — close agreement = robust estimate."),
        ("3SFCA Accessibility (E2SFCA, competition-allocated)",
         f"{res.sfca_pct}th percentile", res.sfca_reading),
        ("P-Median Location-Allocation", f"{res.pmedian_efficiency_pct}% siting efficiency",
         f"Demand-weighted travel optimum is {res.pmedian_optimal_label}."),
        ("Reilly / Converse Breakpoint",
         (f"~{res.breakpoint_mi} mi trade-area radius" if res.breakpoint_mi is not None else "n/a"),
         res.breakpoint_reading),
        ("Clark-Evans Clustering (nearest-neighbour)",
         (f"R = {res.nn_index}" if res.nn_index is not None else "n/a"),
         res.nn_reading),
    ]

    # ---- Combined verdict ----
    score, drivers = 0, []
    if res.huff_share_pct >= 30: score += 2; drivers.append("strong demand capture")
    elif res.huff_share_pct >= 15: score += 1; drivers.append("moderate demand capture")
    else: drivers.append("thin demand capture")
    if res.sfca_pct is not None and res.sfca_pct < 45: score += 2; drivers.append("underserved market")
    elif res.sfca_pct is not None and res.sfca_pct < 75: score += 1; drivers.append("workable saturation")
    else: drivers.append("saturated supply")
    if res.pmedian_efficiency_pct and res.pmedian_efficiency_pct >= 85:
        score += 1; drivers.append("central to demand")
    if res.nn_index is not None and res.nn_index < 0.8:
        score += 1; drivers.append("clustered rivals (gap may exist)")

    band = ("FAVORABLE spatial position" if score >= 5 else
            "MARGINAL / conditional spatial position" if score >= 3 else
            "UNFAVORABLE spatial position")
    res.verdict = (f"{band}. Huff {res.huff_share_pct}% (band {res.huff_lo}–{res.huff_hi}%, "
                   f"year-1 {res.huff_launch_pct}%), 3SFCA {res.sfca_pct}th-pct accessibility, P-median "
                   f"{res.pmedian_efficiency_pct}%, trade-area radius ~{res.breakpoint_mi} mi. "
                   f"Drivers: " + ", ".join(drivers) + ".")
    res.ok = True
    return res
