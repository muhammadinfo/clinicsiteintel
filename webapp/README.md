# ClinicSiteIntel — Mobile PWA

A phone-installable (iOS + Android) front-end for the same analysis engine the
Windows desktop app uses. The heavy logic (Census ACS, NPPES, geocoding, the
Bayesian Location Viability Index, the Consultant's Read) runs in Python on a
small server; the phone shows a fast, native-feeling web UI you can add to your
home screen.

```
webapp/
  server.py            Flask backend — serves the UI + POST /api/report
  requirements.txt
  static/
    index.html app.js style.css     mobile UI
    manifest.webmanifest sw.js       makes it installable / offline shell
    icon-192.png icon-512.png        home-screen icons
```

## Run it locally
```bash
pip install -r webapp/requirements.txt
python webapp/server.py
# open http://localhost:8000
```
(Reuses the engine in ../app and your local config in %APPDATA%\ClinicSiteIntel.)

## Put it on YOUR phone (same Wi-Fi, no deploy)
1. Run the server on your PC (above).
2. Find the PC's LAN IP: `ipconfig` → IPv4 (e.g. 192.168.1.50).
3. On the phone (same Wi-Fi): open `http://192.168.1.50:8000`.
4. iOS Safari: Share → **Add to Home Screen**. Android Chrome: ⋮ → **Install app**.

For HTTPS / install from anywhere, use a tunnel: `ngrok http 8000` and open the
https URL it prints (service workers + install need HTTPS off-LAN).

## Deploy to the internet (real "on my phone anywhere")
Any host that runs a WSGI app. Example (Render / Railway / Fly):
- Build: `pip install -r webapp/requirements.txt`
- Start: `gunicorn --chdir webapp server:app --bind 0.0.0.0:$PORT --timeout 180`
- Env vars: `CENSUS_API_KEY` (required), `GOOGLE_PLACES_API_KEY` (optional).

`--timeout 180` matters: a full report runs ~60–90 s, so the default 30 s would
cut it off. Once deployed over HTTPS, open the URL on iOS/Android and
**Add to Home Screen / Install app** — it launches standalone like a native app.

## Notes
- This is the same code path as the desktop, so any engine fix (e.g. the real-
  address geocoding) applies to both automatically.
- No app-store accounts needed — a PWA installs straight from the browser.
- For App Store / Play Store distribution later, wrap this PWA with Capacitor or
  use a Flutter/React Native shell over the same `/api/report` endpoint.
