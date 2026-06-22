# ClinicSiteIntel

A Windows desktop application: type in an address, get a live, real-time
clinic-site assessment combining demographics, competitor discovery,
referral-partner mapping, real-estate search tools, and statistical
analysis — built around the same Location Viability Index (LVI)
methodology validated in `market-intelligence.html`.

## Install (end user)

Run `installer\output\ClinicSiteIntel-Setup.exe`. It installs to
`Program Files\ClinicSiteIntel`, adds a Start Menu entry, and optionally a
desktop shortcut. No admin rights required beyond the standard installer
prompt. The app itself needs no separate Python install — everything is
bundled.

## Required API keys (one-time setup, both free)

Open the app → **Settings** tab and enter:

1. **Census API key** — REQUIRED for the Demographics and Statistics tabs.
   Confirmed live on 2026-06-20: the Census Bureau now rejects every ACS
   data request without a key, even single low-volume lookups. Sign up
   instantly, free, no approval wait: https://api.census.gov/data/key_signup.html

2. **Google Places API key** — REQUIRED for the Competitors and Referral
   Map tabs. Create a project at https://console.cloud.google.com, enable
   the **Places API**, enable billing on the project (Google requires a
   billing account even though new accounts get a recurring free credit
   that covers normal usage of this app), then create an API key under
   Credentials. Without this key, those two tabs are skipped — everything
   else still works.

Without either key, the relevant tabs report what's missing rather than
silently failing — check the **Summary** tab's "Data-gap / error notes"
after running a report.

## What each tab actually does

- **Summary** — the address's Location Viability Index, computed via a
  50,000-draw Bayesian Monte Carlo simulation (mean, SD, 90% credible
  interval) rather than a single fragile point score.
- **Demographics** — live US Census ACS 5-Year data, pulled fresh on every
  run, at BOTH the Census-tract (address) level and ZCTA (ZIP) level.
- **Competitors** — Google Places nearby search for TMJ/orofacial-pain/
  dental-sleep-medicine practices, then the app fetches each result's own
  website and scores "competition potential" by scanning for board-
  certification language (ABOP/AAOP/Diplomate/AADSM) vs. just an ancillary
  service mention — distinguishing real specialist competition from a
  general dentist who lists TMJ as one of many services.
- **Referral Map** — same Places search, different target list (PCPs,
  ENT, sleep medicine, neurology, PT, oral surgery), ranked by a
  referral-fit score combining specialty weight, proximity, and review
  volume.
- **Real Estate** — IMPORTANT HONEST LIMITATION: LoopNet, Crexi, CityFeet,
  and Carr.us all block automated/bot traffic (HTTP 403), confirmed
  directly multiple times during this project's research. There is no
  working live-scraping solution for these sites from a client app. This
  tab instead (1) generates direct, pre-filled search links you open with
  one click, and (2) lets you paste a listing's text and auto-extracts
  square footage, rate ($/SF/yr, with monthly-rate auto-annualization),
  build-out type, and HVAC mentions — the same approach validated in the
  HTML dashboard's "smart paste" feature.
- **Statistics** — Pearson correlation between demographic variables
  (income, age, population) and a demographic-fit proxy score across a
  basket of nearby ZIP codes, run at both address and ZIP-code resolution.
  This is NOT a regression against real clinic outcomes data (no such
  dataset exists for this exercise) — the tab states that caveat
  explicitly every time.
- **Saved Reports** — every run is stored locally in a SQLite database
  (`%APPDATA%\ClinicSiteIntel\clinicsiteintel.sqlite3`) so you can revisit
  and compare addresses over time, the desktop equivalent of the HTML
  dashboard's localStorage persistence.

## Rebuilding from source

```powershell
cd ClinicSiteIntel
pip install -r requirements.txt
python app\main.py                      # run directly during development
powershell -File build_exe.ps1          # produce dist\ClinicSiteIntel\
# then compile installer\ClinicSiteIntel.iss with Inno Setup (ISCC.exe)
```

Inno Setup (free): https://jrsoftware.org/isdl.php — already installed on
this machine via `winget install JRSoftware.InnoSetup` as part of building
this project.

## What this app deliberately does NOT claim to do

- It does not autonomously crawl LoopNet/Crexi/CityFeet/Carr in the
  background — that has been technically proven infeasible (bot-blocked)
  every time it was attempted in this project's research.
- The Statistics tab's correlations describe demographic variables moving
  together, not a validated predictor of clinic financial success.
- Competitor "competition potential" scoring is keyword-based website
  verification, not a guarantee of someone's actual board-certification
  status — always confirm credentials independently (e.g., via ABOP's own
  diplomate directory) before treating a score as final.
