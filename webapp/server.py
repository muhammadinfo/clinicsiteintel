"""ClinicSiteIntel PWA backend — exposes the SAME Python analysis engine the
desktop app uses (report.run_full_report + narrative.build_consultant_read) as
a small JSON API, and serves the installable mobile web front-end.

Run locally:   python webapp/server.py      (then open http://localhost:8000)
Deploy:        any host that runs a Flask/WSGI app (Render, Railway, Fly, a VPS).
               gunicorn 'webapp.server:app'  with env PORT + CENSUS_API_KEY.
"""
import os
import sys

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(os.path.dirname(ROOT), "app")
sys.path.insert(0, APP_DIR)          # reuse the desktop engine unchanged

import report as report_mod          # noqa: E402
import narrative as narrative_mod    # noqa: E402
import config as config_mod          # noqa: E402

app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="")
CORS(app)


def _cfg() -> dict:
    cfg = config_mod.load_config()
    # On a server, secrets come from the environment; fall back to the local config.
    cfg["census_api_key"] = os.environ.get("CENSUS_API_KEY", cfg.get("census_api_key", ""))
    cfg["google_places_api_key"] = os.environ.get(
        "GOOGLE_PLACES_API_KEY", cfg.get("google_places_api_key", ""))
    return cfg


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json(force=True, silent=True) or {}
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"error": "Enter a full address (street, city, state, ZIP)."}), 400
    cfg = _cfg()
    try:
        rep = report_mod.run_full_report(
            address,
            cfg.get("google_places_api_key", ""),
            cfg.get("census_api_key", ""),
            known_competitors=cfg.get("known_competitors", []),
        )
    except Exception as e:
        return jsonify({"error": f"Report failed: {e}"}), 500
    return jsonify(_shape(rep))


def _shape(rep: dict) -> dict:
    summ = rep.get("lvi_summary") or {}
    band, color = narrative_mod.verdict_band(summ.get("mean"))
    demo = rep.get("demographics_zip") or rep.get("demographics_tract") or {}
    comps = rep.get("competitors", [])
    specs = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
    dists = [c.get("distance_mi") for c in specs if c.get("distance_mi") is not None]
    refs = rep.get("referrals", [])
    n_md = sum(1 for r in refs if str(r.get("category", "")).startswith("Physician"))
    sp = rep.get("spatial") or {}
    econ = rep.get("econ") or {}
    geo = rep.get("geo") or {}

    def _comp_sort(c):
        return (not str(c.get("tier", "")).startswith("Specialist"),
                c.get("distance_mi") if c.get("distance_mi") is not None else 999)

    return {
        "address": geo.get("matched_address") or rep.get("address_input"),
        "lvi": {k: summ.get(k) for k in ("mean", "point_estimate", "sd", "p05", "p95")},
        "verdict": {"band": band, "color": color},
        "consultant_html": narrative_mod.build_consultant_read(rep),
        "demographics": {"income": demo.get("median_household_income"), "age": demo.get("median_age")},
        "competition": {"count": len(specs), "nearest_mi": (min(dists) if dists else None)},
        "referrals": {"count": n_md},
        "spatial": {"huff": sp.get("huff_share_pct"), "launch": sp.get("huff_launch_pct")},
        "econ": {"projected": econ.get("projected_cases"), "break_even": econ.get("break_even_cases")},
        "competitors": [
            {"name": c.get("name"), "address": c.get("address"),
             "distance_mi": c.get("distance_mi"), "tier": c.get("tier"),
             "score": c.get("competition_score")}
            for c in sorted(comps, key=_comp_sort)[:12]
        ],
        "referrals": [
            {"name": r.get("name"), "specialty": r.get("specialty"),
             "distance_mi": r.get("distance_mi"), "fit": r.get("fit_weight")}
            for r in sorted(
                [r for r in refs if str(r.get("category", "")).startswith("Physician")],
                key=lambda r: (r.get("distance_mi") if r.get("distance_mi") is not None else 999))[:18]
        ],
        "errors": rep.get("errors", []),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
