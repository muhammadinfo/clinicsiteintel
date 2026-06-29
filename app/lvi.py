"""Location Viability Index — same formula as market-intelligence.html,
plus a Monte Carlo Bayesian uncertainty layer (ported from bayes_lvi.py)
so a single address report shows not just a point score but a credible
interval reflecting how much of the input is verified vs. estimated.

LVI = 0.35*Ds + 0.25*Rp + 0.20*If + 0.10*Cp + 0.10*Of - 0.20*Rc
All components 0-100, result clipped to 0-100. Rc data-gap default = 50.
"""
from dataclasses import dataclass
import math
import random


def clip(x: float) -> float:
    return max(0.0, min(100.0, x))


def calc_lvi(ds: float, rp: float, if_: float, cp: float, of_: float, rc: float) -> float:
    score = 0.35 * ds + 0.25 * rp + 0.20 * if_ + 0.10 * cp + 0.10 * of_ - 0.20 * rc
    return clip(score)


@dataclass
class LVIInputs:
    ds: float = 50.0   # demographic strength (income/age/density fit)
    rp: float = 50.0   # referral-partner density nearby
    if_: float = 50.0  # infrastructure fit (medical/dental build-out, HVAC)
    cp: float = 50.0   # competitive positioning (lower competitor threat = higher score)
    of_: float = 50.0  # operational fit (hours/access/lease terms)
    rc: float = 50.0   # rent burden vs submarket median (data-gap default)
    sigma: dict = None  # per-component uncertainty, keyed same as fields


def derive_ds_from_demographics(median_income: float | None, median_age: float | None, population: float | None) -> float:
    """Heuristic translation of raw ACS figures into the 0-100 Ds component,
    benchmarked against the priority-geography baseline already validated
    in market-intelligence.html (Thousand Oaks: $134K income, age 44.5, pop 125K -> Ds ~85)."""
    if median_income is None or median_age is None:
        return 50.0
    income_score = clip((median_income / 150000) * 100)
    # Target cohort skews 40+; score peaks around age 42-55, tapers outside that band
    if median_age <= 0:
        age_score = 50.0
    else:
        age_score = clip(100 - abs(median_age - 48) * 4)
    pop_score = clip((min(population or 0, 150000) / 150000) * 100) if population else 50.0
    return clip(0.5 * income_score + 0.35 * age_score + 0.15 * pop_score)


def derive_cp_from_competitors(competitor_scores: list[int], nearest_distance_mi: float | None) -> float:
    """Lower Cp the more, and the closer, high-scoring (verified specialist)
    competitors are found nearby. Mirrors the dashboard's compression logic
    discovered through the 2026-06-18 Borquez/Shirazi proximity findings.
    Retained for back-compat; derive_cp_from_competitors_v2 supersedes it with
    smooth per-competitor distance decay on REAL geocoded distances."""
    if not competitor_scores:
        return 70.0  # no verified competitors found = open market, but capped (data may be incomplete)
    base = 70.0
    for score in sorted(competitor_scores, reverse=True)[:5]:
        weight = score / 100.0
        proximity_penalty = 18 * weight
        if nearest_distance_mi is not None and nearest_distance_mi < 3:
            proximity_penalty *= 1.4
        base -= proximity_penalty
    return clip(base)


def derive_cp_from_competitors_v2(competitors: list) -> float:
    """Competition score from REAL per-competitor distances. Each verified
    competitor depresses the score by its credential weight × a smooth distance
    decay (full weight if it's on top of you, ~half at ~3 mi, negligible past
    ~8 mi). Replaces the old hard 'nearest < 3 mi' cliff — so a specialist truly
    1.7 mi away no longer counts the same as one 0.3 mi away.
    `competitors`: list of (competition_score, distance_mi)."""
    if not competitors:
        return 70.0
    base = 70.0
    for score, dist in sorted(competitors, key=lambda c: -(c[0] or 0))[:6]:
        weight = (score or 0) / 100.0
        d = 5.0 if dist is None else dist
        base -= 22 * weight * math.exp(-d / 4.0)
    return clip(base)


_HIGH_VALUE_REFERRAL = 8   # fit_weight >= this = sleep med / ENT / neurology / PCP


def derive_rp_from_referrals(referrals: list) -> float:
    """Referral-ACCESS score (replaces the flat `min(100, n×4)` count cap, which
    saturated instantly and couldn't reward exceptional access). Rewards close,
    high-fit referrers via distance decay, with a strong bonus when a high-value
    referrer (sleep medicine, ENT, neurology, pulmonology, primary care) is
    essentially CO-LOCATED — the single most valuable site attribute for a
    referral-driven OSA/TMD practice. A sleep-medicine group on your floor now
    scores far above 'lots of doctors somewhere in the ZIP'."""
    if not referrals:
        return 50.0
    access = 0.0
    for r in referrals:
        w = (r.get("fit_weight") or 4) / 10.0
        d = r.get("distance_mi")
        d = 5.0 if d is None else d
        contrib = w * math.exp(-d / 3.0)                      # 3-mi e-folding decay
        if d <= 0.2 and (r.get("fit_weight") or 0) >= _HIGH_VALUE_REFERRAL:
            contrib *= 2.5                                    # co-located anchor bonus
        access += contrib
    return clip(100 * (1 - math.exp(-access / 6.0)))          # diminishing returns


def derive_if_from_medical_hub(referrals: list, competitors: list = None) -> float:
    """Infrastructure / site-quality proxy (replaces the hardcoded 50). An
    address co-located with many registered medical providers IS, by definition,
    a proven medical office building — medical build-out, parking, ADA access,
    dental plumbing, an anchor tenant feeding foot traffic. Counts providers
    within ~0.2 mi (same building/parcel) using REAL geocoded distances; with
    ZIP-centroid distances this signal was invisible."""
    pool = list(referrals or []) + list(competitors or [])
    near = sum(1 for r in pool
               if (r.get("distance_mi") if r.get("distance_mi") is not None else 9) <= 0.2)
    if near <= 0:
        return 50.0
    return clip(50 + min(45, near * 7))


def monte_carlo_lvi(inputs: LVIInputs, n: int = 50000, seed: int = 42,
                    on_site_count: int = 0, competitor_count: int = 0) -> dict:
    """Monte Carlo credible interval.

    Adapt the standard deviations (sigma) based on data certainty: strong signals
    (many on-site referrers, few competitors, dense medical hub) should have lower
    uncertainty; weak signals should have higher uncertainty."""
    if not inputs.sigma:
        # Base sigmas are conservative defaults for mid-range inputs.
        # Adapt down for high-confidence signals: many on-site referrers and few
        # competitors = much lower uncertainty. Adapt up for weak signals.
        ds_sigma = 8.0
        rp_sigma = max(4.0, 12.0 - on_site_count / 3)  # more on-site → much tighter
        if__sigma = max(3.0, 15.0 - on_site_count / 2)  # more on-site MDs → much tighter
        cp_sigma = max(2.0, 10.0 - 8.0 / max(1, competitor_count))  # fewer competitors → much tighter
        of_sigma = 12.0
        rc_sigma = 20.0
        sigma = {"ds": ds_sigma, "rp": rp_sigma, "if_": if__sigma, "cp": cp_sigma, "of_": of_sigma, "rc": rc_sigma}
    else:
        sigma = inputs.sigma
    rnd = random.Random(seed)
    draws = []
    for _ in range(n):
        ds = clip(rnd.gauss(inputs.ds, sigma.get("ds", 8)))
        rp = clip(rnd.gauss(inputs.rp, sigma.get("rp", 12)))
        if_ = clip(rnd.gauss(inputs.if_, sigma.get("if_", 15)))
        cp = clip(rnd.gauss(inputs.cp, sigma.get("cp", 10)))
        of_ = clip(rnd.gauss(inputs.of_, sigma.get("of_", 12)))
        rc = clip(rnd.gauss(inputs.rc, sigma.get("rc", 20)))
        draws.append(calc_lvi(ds, rp, if_, cp, of_, rc))
    draws.sort()
    mean = sum(draws) / n
    sd = (sum((x - mean) ** 2 for x in draws) / n) ** 0.5
    return {
        "mean": round(mean, 1),
        "sd": round(sd, 1),
        "p05": round(draws[int(0.05 * n)], 1),
        "p50": round(draws[int(0.50 * n)], 1),
        "p95": round(draws[int(0.95 * n)], 1),
        "point_estimate": round(calc_lvi(inputs.ds, inputs.rp, inputs.if_, inputs.cp, inputs.of_, inputs.rc), 1),
    }


def first_order_sensitivity(inputs: "LVIInputs") -> list:
    """First-order Sobol-style variance contribution of each LVI input: the
    fraction of output variance explained by each component's uncertainty,
    using the analytic linear-combination variance (LVI is a weighted sum, so
    Var = Σ wᵢ²·σᵢ²). Tells the user which input most drives the uncertainty —
    i.e. which data is worth improving."""
    sigma = inputs.sigma or {"ds": 8, "rp": 12, "if_": 15, "cp": 10, "of_": 12, "rc": 20}
    weights = {"ds": 0.35, "rp": 0.25, "if_": 0.20, "cp": 0.10, "of_": 0.10, "rc": 0.20}
    labels = {"ds": "Demographic fit (Dₛ)", "rp": "Referral density (Rₚ)",
              "if_": "Infrastructure (I_f)", "cp": "Competition (Cₚ)",
              "of_": "Operations (O_f)", "rc": "Rent burden (R_c)"}
    contrib = {k: (weights[k] ** 2) * (sigma.get(k, 10) ** 2) for k in weights}
    total = sum(contrib.values()) or 1.0
    rows = sorted(((labels[k], round(contrib[k] / total * 100, 1)) for k in weights),
                  key=lambda x: x[1], reverse=True)
    return rows


def dominance_probability(mean_a, sd_a, mean_b, sd_b) -> float:
    """P(site A's true LVI > site B's), closed-form under the Monte-Carlo
    normal approximation: Φ((μ_A−μ_B)/√(σ_A²+σ_B²)). Lets the user judge
    whether a ranking gap is real or within noise."""
    import math
    denom = math.sqrt((sd_a or 0) ** 2 + (sd_b or 0) ** 2) or 1e-9
    z = (mean_a - mean_b) / denom
    # standard normal CDF
    return round(0.5 * (1 + math.erf(z / math.sqrt(2))) * 100, 1)
