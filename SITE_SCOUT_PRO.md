# Site Scout Pro

Streamlit medical/dental commercial-real-estate intelligence app. Reuses the
ClinicSiteIntel analytical engine (Census ACS, CDC PLACES, NPPES, Huff gravity,
epi-weighted demand, unit economics) and adds a 4-tier verdict engine,
drive-time isochrones, listing ingestion, and an optional Claude Vision read.

## Run

```bash
pip install streamlit folium streamlit-folium pyyaml requests
# optional live sources:
pip install apify-client anthropic

streamlit run site_scout_pro.py
```

Opens in your browser (default http://localhost:8501).

## How it maps to the brief

| Brief item | Status |
|---|---|
| `fetch_recent_listings(zip, radius)`, `PropertyListing` (id/address/lat/lng/price/sqft/source) | ✅ implemented; filters `daysOnMarket ≤ 60` |
| `calculate_location_verdict(...)` — Huff gravity + Haversine | ✅ reuses `spatial.compute_all` (Huff/MCI/3SFCA) |
| Verdict / captureScore / plain-English reasoning | ✅ `LocationVerdict` |
| Accordion listing list, collapsed badge + expanded 3 sections | ✅ `st.expander` cards: Reasoning / Referral Proximity / Competitor Density |
| 4-tier verdict (Strong Buy/Viable/Caution/Not Recommended) + color | ✅ |
| Drive-time isochrones (15/30/45) — Mapbox → OSRM fallback | ✅ `drive_time_minutes`, `isochrone_population` |
| Payer mix & affluence (ACS) | ✅ via `demographics` + `epi.cashpay_propensity` |
| Claude Vision master prompt embedded | ✅ `SITE_SCOUT_MASTER_PROMPT`, called only if Anthropic key set |
| Streamlit GUI: sidebar settings + keys, Camping/Medical tabs, verdict grid, expandable details, Folium map, CSV export, live logs | ✅ |
| YAML config + UI overrides | ✅ `site_scout_config.yaml` |
| Apify LoopNet/Crexi integration | ✅ code path present; requires paid token |

## Honest constraints (verified this session)

- **LoopNet/Crexi have no public API and block scraping** (HTTP 403 / CAPTCHA;
  DuckDuckGo also CAPTCHA-blocked after ~30 lookups). So **direct Playwright
  scraping is not implemented** — it would fail for users. Live listings require
  a **paid Apify actor token** (UI field). Without it, the app uses a geocoded
  **sample feed + manual paste**, which exercises the full analysis pipeline.
- **Claude Vision** needs an Anthropic API key (paid). Without it, the verdict
  runs on the structured models only.
- **Mapbox** isochrones need a token (free tier); falls back to OSRM, then to a
  Haversine speed proxy.
- **Census key required** for demographics/payer/affluence (free).

The TypeScript/React steps in the original brief don't apply — this repo is
Python; the equivalent logic lives here and in the PySide6 `app/` modules.
"""
