# Getting ClinicSiteIntel onto your iPhone

iOS apps can ONLY be compiled on macOS. Since you have no Mac, you use a
**cloud Mac** (Codemagic) to compile, then install on the iPhone one of two ways.

The build config is already written: `mobile/codemagic.yaml`.

---

## Step 1 (both routes): build the .ipa on a cloud Mac — FREE
1. Push this repo to GitHub (see DEPLOY.md for the git push commands).
2. Go to https://codemagic.io → sign in with GitHub → add this repo.
3. Run the **ios-clinicsiteintel** workflow (it reads `mobile/codemagic.yaml`).
   Free tier = 500 macOS build-minutes/month; a build takes ~10 min.
4. Download the resulting **`ClinicSiteIntel-unsigned.ipa`** from the build's Artifacts.

---

## Step 2: install it on the iPhone — pick one

### Route A — Free (no $99), re-sign weekly  ·  AltStore / Sideloadly
- Install **AltServer** on your Windows PC (altstore.io) — yes, there's a Windows version.
- Connect the iPhone by cable, sign in with a **free Apple ID**.
- AltStore installs the `.ipa` to your iPhone. It works for **7 days**, then AltStore
  auto-re-signs it (keep the PC + AltServer running, or re-open weekly).
- Good for "I just want to use it on my phone" at zero cost.

### Route B — Smooth, paid  ·  TestFlight ($99/yr Apple Developer)
- Enroll at developer.apple.com ($99/yr).
- In Codemagic, add your Apple Developer credentials so it produces a SIGNED build
  and uploads to **App Store Connect → TestFlight**.
- Install the **TestFlight** app on the iPhone → install ClinicSiteIntel. Lasts 90
  days per build, no weekly re-sign, no PC needed. This is also the on-ramp to a
  full App Store release later.

---

## After install (either route)
Open the app → tap **⚙** → paste your backend URL:
- temporary: the cloudflared tunnel URL, or
- permanent: your Render URL (see DEPLOY.md) — recommended so it never breaks.

## Reality check
- The cheapest *real native app* path = Codemagic (free) + AltStore (free) = $0,
  but you re-sign weekly and need your PC.
- The hassle-free path = $99/yr Apple Developer + TestFlight.
- The zero-effort path that works TODAY = the PWA "Add to Home Screen" (a real
  full-screen app icon, no Mac, no account) — the same web UI in app form.
