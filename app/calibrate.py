"""LVI calibration harness.

The LVI weights (Ds/Rp/If/Cp/Of/Rc) are expert-set, not fitted. This tool lets
you LABEL sites you know are good or bad, then fits the weights to YOUR judgment
so the index reflects real outcomes instead of hand-picked numbers.

Workflow:
  1. Fill calibration_labels.csv:  address,target_score,notes
     - target_score is YOUR 0-100 rating of the site (gut/clinical judgment, or a
       known-good vs known-bad spread, e.g. your current practice = 85, a site you
       rejected = 35). 8-25 sites is plenty to start.
  2. Run:  python app/calibrate.py
     - extracts each site's factor values (Ds/Rp/If/Cp) by running the real report
       engine (cached to _calib_cache.json so re-runs are instant),
     - fits non-negative weights (positive factors sum to 1; Rc subtracts) that
       minimise error vs your targets,
     - prints the fitted weights, fit quality (R², mean abs error), and a
       per-site current-vs-fitted-vs-target comparison.
  3. If you like the fit, paste the suggested weights into lvi.calc_lvi.

No third-party dependencies — pure Python (random + local search).
"""
import csv
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = os.path.join(_HERE, "..", "_calib_cache.json")
_LABELS = os.path.join(_HERE, "..", "calibration_labels.csv")
_FACTORS = ["ds", "rp", "if_", "cp", "of_"]   # positive factors; rc handled separately


def load_labels(path=_LABELS):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            addr = (r.get("address") or "").strip()
            tgt = (r.get("target_score") or "").strip()
            if addr and tgt:
                try:
                    rows.append((addr, float(tgt)))
                except ValueError:
                    pass
    return rows


def _load_cache():
    try:
        return json.load(open(_CACHE, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def extract_factors(addresses, log=print):
    """Run the report engine for each address (cached) -> factor dict per site."""
    cache = _load_cache()
    sys.path.insert(0, _HERE)
    import config, report
    cfg = config.load_config()
    out = {}
    for a in addresses:
        if a in cache:
            out[a] = cache[a]
            continue
        log(f"  extracting factors: {a} …")
        try:
            rep = report.run_full_report(a, cfg.get("google_places_api_key", ""),
                                         cfg.get("census_api_key", ""),
                                         known_competitors=cfg.get("known_competitors", []))
            inp = rep.get("lvi_inputs", {})
            out[a] = {k: float(inp.get(k, 50) or 50) for k in _FACTORS + ["rc"]}
            cache[a] = out[a]
            json.dump(cache, open(_CACHE, "w"), indent=2)
        except Exception as e:
            log(f"    FAILED ({e.__class__.__name__}) — skipped")
    return out


def _predict(factors, w):
    s = sum(w[k] * factors.get(k, 50) for k in _FACTORS) - w["rc"] * factors.get("rc", 50)
    return max(0.0, min(100.0, s))


def _mse(samples, w):
    return sum((_predict(f, w) - t) ** 2 for f, t in samples) / max(1, len(samples))


def _rand_weights(rnd):
    raw = [rnd.random() for _ in _FACTORS]
    tot = sum(raw) or 1.0
    w = {k: raw[i] / tot for i, k in enumerate(_FACTORS)}   # positive factors sum to 1
    w["rc"] = rnd.uniform(0.0, 0.3)
    return w


def fit_weights(samples, iters=40000, seed=7):
    """Random search + local refinement on the weight simplex (Rc in [0,0.3])."""
    rnd = random.Random(seed)
    best, best_mse = None, float("inf")
    for _ in range(iters):
        w = _rand_weights(rnd)
        m = _mse(samples, w)
        if m < best_mse:
            best_mse, best = m, w
    # local refinement: jitter the best, renormalise positives
    for _ in range(iters):
        w = dict(best)
        for k in _FACTORS:
            w[k] = max(0.0, w[k] + rnd.gauss(0, 0.03))
        tot = sum(w[k] for k in _FACTORS) or 1.0
        for k in _FACTORS:
            w[k] /= tot
        w["rc"] = max(0.0, min(0.3, best["rc"] + rnd.gauss(0, 0.02)))
        m = _mse(samples, w)
        if m < best_mse:
            best_mse, best = m, w
    return best, best_mse


def _r2(samples, w):
    ys = [t for _, t in samples]
    mean = sum(ys) / len(ys)
    ss_tot = sum((t - mean) ** 2 for t in ys) or 1.0
    ss_res = sum((_predict(f, w) - t) ** 2 for f, t in samples)
    return 1 - ss_res / ss_tot


# Current production weights, for comparison.
_CURRENT = {"ds": 0.22, "rp": 0.30, "if_": 0.18, "cp": 0.25, "of_": 0.05, "rc": 0.08}


def main():
    labels = load_labels()
    if len(labels) < 4:
        print(f"Need at least ~4 labeled sites in {os.path.relpath(_LABELS)} "
              f"(found {len(labels)}). Add rows: address,target_score,notes")
        return
    factors = extract_factors([a for a, _ in labels])
    samples = [(factors[a], t) for a, t in labels if a in factors]
    if len(samples) < 4:
        print("Too few sites extracted successfully to fit. Check addresses.")
        return
    w, mse = fit_weights(samples)
    mae = sum(abs(_predict(f, w) - t) for f, t in samples) / len(samples)

    print("\n================ LVI CALIBRATION ================")
    print(f"Sites used: {len(samples)}")
    print(f"\nFITTED weights   : " + "  ".join(f"{k}={w[k]:.2f}" for k in _FACTORS + ["rc"]))
    print(f"CURRENT weights  : " + "  ".join(f"{k}={_CURRENT[k]:.2f}" for k in _FACTORS + ["rc"]))
    print(f"\nFit (fitted)  R²={_r2(samples, w):+.3f}  MAE={mae:.1f} pts")
    print(f"Fit (current) R²={_r2(samples, _CURRENT):+.3f}  "
          f"MAE={sum(abs(_predict(f, _CURRENT) - t) for f, t in samples)/len(samples):.1f} pts")
    print("\n  target  current  fitted   site")
    for (a, t), (f, _t) in zip(labels, samples):
        print(f"  {t:5.0f}  {_predict(f, _CURRENT):7.1f}  {_predict(f, w):6.1f}   {a[:40]}")
    print("\nTo adopt: edit lvi.calc_lvi with the FITTED weights above.")
    print("(Verify R² is meaningfully better than current before adopting.)")


if __name__ == "__main__":
    main()
