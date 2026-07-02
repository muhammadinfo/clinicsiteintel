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
    # Weights reflect the practice's stated objective, in priority order:
    #   1. Referral build-up   -> Rp (referral access) 0.30 + If (co-located
    #      medical hub) 0.18 = 0.48 of the index.
    #   2. Low competition     -> Cp 0.25 (was 0.10).
    #   3. Patient demand /     -> Ds 0.22 (income / population / age fit).
    #      population
    # Rent (Rc) and operational fit (Of) are deliberately MINOR (0.08 / 0.05) —
    # the user judges them low-importance vs. referrals, competition and crowds.
    score = 0.22 * ds + 0.30 * rp + 0.18 * if_ + 0.25 * cp + 0.05 * of_ - 0.08 * rc
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
    # Blend TWO signals, both kept heavy (per the user's directive):
    #   proximity  — in-building / geocoded-near referrers, steep decay so a
    #                referrer on your floor far outweighs one across the ZIP;
    #   density    — ZIP-wide physician count (referrers whose exact distance is
    #                unknown — the 0.6-mi ZIP-centroid fallback — count here, NOT
    #                as if they were 0.6 mi away, which previously inflated Rp).
    near_access = 0.0   # ONLY in-building / within 1/4 mi counts as true proximity
    zip_weight = 0.0    # everything else: far-but-real (decayed) + ZIP-centroid fallback
    for r in referrals:
        w = (r.get("fit_weight") or 4) / 10.0
        d = r.get("distance_mi")
        is_fallback = d is not None and abs(d - 0.6) < 0.001   # ZIP-centroid placeholder
        if d is None or is_fallback:
            zip_weight += w                                   # unknown distance -> density
        elif d <= 0.25:
            contrib = w * math.exp(-d / 0.5)                  # steep — only the same block
            if d <= 0.05 and (r.get("fit_weight") or 0) >= _HIGH_VALUE_REFERRAL:
                contrib *= 2.5                                # co-located anchor bonus
            near_access += contrib
        else:
            zip_weight += w * math.exp(-d / 3.0)              # far but real -> decayed density
    prox_score = 100 * (1 - math.exp(-near_access / 3.0))     # in-building/near component
    density_score = 100 * (1 - math.exp(-zip_weight / 8.0))   # ZIP-density component (still counts)
    # Weighted toward in-building / near referrers (0.72) so a standalone building
    # in a dense ZIP can no longer ride ZIP density to a top referral score, while
    # ZIP density (0.28) still meaningfully contributes.
    return clip(0.72 * prox_score + 0.28 * density_score)


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


# Factor correlation matrix, order [ds, rp, if_, cp, of_, rc]:
#   rp↔if_ +0.50 : referral access and medical-hub strength share the same
#                  underlying provider density — they move together.
#   ds↔cp  -0.30 : affluent, high-fit markets ATTRACT specialists, so a strong
#                  demographic draw co-occurs with more competition (lower Cp).
#   rp↔cp  -0.20 : deep referral pools likewise attract competitors.
# Everything else ~0 (rent and operations are site-idiosyncratic).
_FACTOR_CORR = [
    [1.00, 0.00, 0.00, -0.30, 0.00, 0.00],
    [0.00, 1.00, 0.50, -0.20, 0.00, 0.00],
    [0.00, 0.50, 1.00,  0.00, 0.00, 0.00],
    [-0.30, -0.20, 0.00, 1.00, 0.00, 0.00],
    [0.00, 0.00, 0.00,  0.00, 1.00, 0.00],
    [0.00, 0.00, 0.00,  0.00, 0.00, 1.00],
]


def _cholesky(a):
    """Lower-triangular Cholesky factor of a symmetric positive-definite matrix."""
    m = len(a)
    L = [[0.0] * m for _ in range(m)]
    for i in range(m):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(a[i][i] - s, 1e-12))
            else:
                L[i][j] = (a[i][j] - s) / L[j][j]
    return L


_FACTOR_CORR_CHOL = _cholesky(_FACTOR_CORR)


def _norm_ppf(p):
    """Acklam's rational approximation to the standard-normal inverse CDF."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


_Z_NODES = [_norm_ppf((i + 0.5) / 101) for i in range(101)]


def _mean_preserving_logit_mu(p, s):
    """Solve for mu such that E[sigmoid(mu + s·Z)] = p (Z ~ N(0,1)).
    A plain logit-normal centered at logit(p) preserves the MEDIAN but Jensen-
    skews the MEAN away from p near the boundaries; this shift removes that
    bias so the Monte Carlo stays centered on the point estimate."""
    def mean_at(mu):
        return sum(1.0 / (1.0 + math.exp(-(mu + s * z))) for z in _Z_NODES) / len(_Z_NODES)
    lo, hi = math.log(p / (1 - p)) - 6.0, math.log(p / (1 - p)) + 6.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if mean_at(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def monte_carlo_lvi(inputs: LVIInputs, n: int = 50000, seed: int = 42,
                    on_site_count: int = 0, competitor_count: int = 0) -> dict:
    """Monte Carlo credible interval.

    Adapt the standard deviations (sigma) based on data certainty: strong signals
    (many on-site referrers, few competitors, dense medical hub) should have lower
    uncertainty; weak signals should have higher uncertainty.

    If inputs.sigma is provided, it may be partial (e.g. only ds_sigma). Missing
    fields are computed adaptively."""
    # Start with adaptive defaults, then overlay any pre-set sigmas (e.g. ds from Census MOE).
    rp_sigma = max(4.0, 12.0 - on_site_count / 3)  # more on-site → much tighter
    if__sigma = max(3.0, 15.0 - on_site_count / 2)  # more on-site MDs → much tighter
    cp_sigma = max(2.0, 10.0 - 8.0 / max(1, competitor_count))  # fewer competitors → much tighter
    of_sigma = 12.0
    rc_sigma = 20.0
    ds_sigma = 8.0
    sigma = {"ds": ds_sigma, "rp": rp_sigma, "if_": if__sigma, "cp": cp_sigma, "of_": of_sigma, "rc": rc_sigma}
    # Overlay pre-set sigmas (e.g. ds from Census MOE data).
    if inputs.sigma:
        sigma.update(inputs.sigma)

    # --- Boundary-respecting, CORRELATED factor sampling -----------------
    # (1) Each factor is perturbed in LOGIT space (median-preserving, always in
    #     (0,100)) instead of clip(gauss(...)): clipping a Gaussian at the 0/100
    #     boundary biases the factor mean toward the middle — e.g. a competition
    #     score of 3 (saturated market) inflated to ~5.3, systematically
    #     UNDERSTATING competition risk at exactly the sites where it matters.
    # (2) Factors are drawn from a Gaussian copula so their real-world
    #     correlations propagate into the interval: a medical hub and referral
    #     access rise together; affluent markets attract more competitors.
    order = ["ds", "rp", "if_", "cp", "of_", "rc"]
    means = {"ds": inputs.ds, "rp": inputs.rp, "if_": inputs.if_,
             "cp": inputs.cp, "of_": inputs.of_, "rc": inputs.rc}
    corr = _FACTOR_CORR_CHOL
    # Pre-compute per-factor logit parameters: sd via the delta method (capped —
    # far from the boundary it reproduces the requested sigma; near it, spread is
    # naturally compressed), and mu solved so the factor's MEAN equals its point
    # value exactly (mean-preserving; no clipping or Jensen bias).
    logit_mu, logit_sd = {}, {}
    for k in order:
        p = min(max(means[k] / 100.0, 0.005), 0.995)
        slope = 100.0 * p * (1 - p)                 # d(scale)/d(logit) at the median
        logit_sd[k] = min(1.5, sigma.get(k, 10.0) / max(slope, 1e-6))
        logit_mu[k] = _mean_preserving_logit_mu(p, logit_sd[k])

    rnd = random.Random(seed)
    draws = []
    for _ in range(n):
        e = [rnd.gauss(0.0, 1.0) for _ in order]
        f = {}
        for i, k in enumerate(order):
            z = sum(corr[i][j] * e[j] for j in range(i + 1))   # correlated normal
            x = logit_mu[k] + logit_sd[k] * z
            f[k] = 100.0 / (1.0 + math.exp(-x))                # always in (0,100)
        draws.append(calc_lvi(f["ds"], f["rp"], f["if_"], f["cp"], f["of_"], f["rc"]))
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
