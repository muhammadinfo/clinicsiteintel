"""Headless re-run of the 128 LoopNet listings on the CORRECTED engine
(real-address geocoding, referral-access + medical-hub scoring). Reuses the
exact Site Scout backend the desktop/PWA use — no GUI — and writes a fresh
ranked CSV. Run: python rerun_128.py
"""
import csv
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))

import config            # noqa: E402
import sitescout         # noqa: E402

IN = os.path.join(HERE, "loopnet_import.txt")
OUT = os.path.join(HERE, "loopnet_shortlist_128_v2.csv")


def last_zip(text):
    m = re.findall(r"\b\d{5}\b", text or "")
    return m[-1] if m else ""


def main():
    cfg = config.load_config()
    census = cfg.get("census_api_key", "")
    kc = cfg.get("known_competitors", [])
    mapbox = cfg.get("mapbox_key", "")

    recs = sitescout.parse_bulk_lines(open(IN, encoding="utf-8").read())
    listings = sitescout.listings_from_records(recs, log=lambda m: None)
    print(f"geocoded {len(listings)} listings of {len(recs)} records", flush=True)

    ctx_cache, rows = {}, []
    t0 = time.time()
    for i, pl in enumerate(listings, 1):
        try:
            z = last_zip(pl.address)
            ctx = ctx_cache.get(z)
            if ctx is None:
                ctx = sitescout.build_context(pl, z, "CA", kc, census, log=lambda m: None)
                ctx_cache[z] = ctx
            v = sitescout.calculate_location_verdict(pl, ctx, mapbox)
            rows.append([pl.address, pl.price, pl.sqft, v.verdict, v.score,
                         v.capture_score, v.recommendation])
            print(f"{i}/{len(listings)}  {v.verdict:<12} {v.score:>3}  {pl.address}", flush=True)
        except Exception as e:
            print(f"{i}/{len(listings)}  ERROR {pl.address}: {e}", flush=True)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["address", "price", "sqft", "verdict", "score", "capture_%", "recommendation"])
        w.writerows(rows)
    print(f"DONE {len(rows)} listings in {time.time()-t0:.0f}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
