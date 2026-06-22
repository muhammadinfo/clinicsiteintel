# Deploy the ClinicSiteIntel PWA (iPhone + Android)

The phone app is a PWA: a Python backend (the same engine as the desktop) plus a
web UI you "Add to Home Screen." To use it on your phone *anywhere*, host the
backend once. Everything here is already configured — you just create the
accounts and click deploy.

Files that make this work (already in this folder):
- `render.yaml` — one-click Render config
- `Dockerfile` — for Railway / Fly / any container host
- `webapp/` — the server + mobile UI

You will need **your Census API key** (the same one in the desktop app's
Settings). It goes into the host as an environment variable named
`CENSUS_API_KEY` — never commit it to the repo.

---

## Option A — Render (recommended, free)

**1. Put the code on GitHub** (one time)
```bash
cd "ClinicSiteIntel"
git init
git add .
git commit -m "ClinicSiteIntel PWA"
```
Create an empty repo at github.com (e.g. `clinicsiteintel`), then:
```bash
git remote add origin https://github.com/<you>/clinicsiteintel.git
git branch -M main
git push -u origin main
```

**2. Deploy on Render**
1. Sign in at https://render.com with GitHub.
2. **New → Blueprint** → pick the `clinicsiteintel` repo. Render reads `render.yaml`.
3. When prompted, set the env var **`CENSUS_API_KEY`** = your Census key.
   (Optional: `GOOGLE_PLACES_API_KEY` if you ever add one.)
4. **Apply / Deploy.** First build takes a few minutes; you get a URL like
   `https://clinicsiteintel.onrender.com`.

**3. Install on your phone**
- iPhone (Safari): open the URL → Share → **Add to Home Screen**.
- Android (Chrome): open the URL → ⋮ → **Install app**.

It now launches full-screen like a native app, on both platforms.

---

## Option B — Railway (Docker)
1. https://railway.app → **New Project → Deploy from GitHub repo**.
2. Railway detects the `Dockerfile` and builds it.
3. Add a **Variable** `CENSUS_API_KEY` = your key.
4. **Generate Domain** → open that HTTPS URL on your phone → Add to Home Screen / Install.

---

## Option C — Use it on your phone TODAY (no deploy, same network optional)
Run the server on your PC and expose it over HTTPS with a tunnel:
```bash
python webapp/server.py            # serves on :8000
# in another terminal, with ngrok installed:
ngrok http 8000
```
Open the `https://….ngrok-free.app` URL ngrok prints — on the phone, Add to
Home Screen / Install. (Works only while your PC + ngrok are running.)

---

## Good to know
- **Free-tier cold start:** after ~15 min idle the server sleeps; the first
  request then takes ~30–60 s to wake. Subsequent reports are normal speed.
- **A report takes ~60–90 s** (live Census + NPPES + geocoding). The host start
  command already sets `--timeout 180` so it won't be cut off.
- **One engine, two faces:** any fix to `app/` (e.g. the real-address geocoding,
  the Consultant's Read) ships to the phone on the next deploy automatically.
- **App Store / Play Store later:** wrap this PWA with Capacitor, or build a
  thin Flutter/React Native shell that calls the same `/api/report` endpoint.
