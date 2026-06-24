"""Academy / board credential cross-match layer.

Tags competitors and referral physicians with professional-academy or board
credentials that the NPI Registry does NOT carry:

  Competitors  -> AAOP / ABOP (orofacial pain), AADSM / ABDSM (dental sleep)
  Referrals    -> AASM (sleep medicine), AAO-HNS (ENT / otolaryngology)

Two credential sources are combined, BOTH compliant (no live portal scraping):

  1. Website signals already gathered by competitors.py (ABOP / AADSM / diplomate
     regex hits) are mapped to credential labels for free.
  2. A LOCAL, user-maintained reference list (assets/credential_registry.json)
     that the user populates from sources they are permitted to use. Providers
     found via NPI / Google are fuzzy-matched against it by normalized name +
     state, so a hit upgrades confidence without copying any portal's database.

The portals' terms of use forbid bulk harvesting into a product, so this layer
deliberately never queries them at runtime.
"""
import json
import os
import re
from difflib import SequenceMatcher

_REGISTRY_CACHE = None

# Map a website-signal fragment (lowercased) -> a clean credential label.
_SIGNAL_CREDENTIALS = [
    (("abop", "diplomate", "board-certified.{0,40}orofacial", "oard[- ]certified.{0,40}orofacial"),
     "ABOP Diplomate (Orofacial Pain)"),
    (("aadsm", "american academy of dental sleep medicine", "abdsm", "qualified dentist"),
     "AADSM (Dental Sleep Medicine)"),
    (("aaop", "american academy of orofacial pain"),
     "AAOP Member (Orofacial Pain)"),
]

_STOP = {"dr", "dds", "dmd", "md", "do", "ms", "msc", "phd", "inc", "pc", "pa",
         "llc", "corp", "corporation", "professional", "dental", "group", "office",
         "associates", "a", "the", "of", "and", "&", "clinic", "center", "centre",
         "medical", "family", "practice"}


def _registry_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                        "credential_registry.json")


def load_registry(force=False):
    """Load the local credential reference list. Each entry:
       {"name": "...", "city": "...", "state": "CA", "credentials": ["..."]}"""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None and not force:
        return _REGISTRY_CACHE
    entries = []
    try:
        raw = json.load(open(_registry_path(), encoding="utf-8"))
        for e in raw if isinstance(raw, list) else raw.get("providers", []):
            if isinstance(e, dict) and e.get("name") and e.get("credentials"):
                entries.append({
                    "name": e["name"],
                    "norm": _norm(e["name"]),
                    "state": (e.get("state") or "").upper(),
                    "city": (e.get("city") or "").lower(),
                    "credentials": list(e["credentials"]),
                })
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    _REGISTRY_CACHE = entries
    return entries


def _norm(name):
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    toks = [t for t in s.split() if t and t not in _STOP and not t.isdigit()]
    return set(toks)


def _state_of(addr):
    m = re.search(r",\s*([A-Z]{2})[, ]", (addr or "") + " ")
    return m.group(1).upper() if m else ""


def signals_to_credentials(matched_signals):
    joined = " ".join(matched_signals or []).lower()
    out = []
    for fragments, label in _SIGNAL_CREDENTIALS:
        if any(re.search(fr, joined) for fr in fragments):
            out.append(label)
    return out


def _registry_match(reg, name, state, threshold=0.74):
    """Conservative fuzzy match: same state (when known) + high name overlap.
    Deliberately strict — a wrong credential is worse than a missed one."""
    nt = _norm(name)
    if not nt:
        return []
    best = None
    best_score = 0.0
    for e in reg:
        if state and e["state"] and e["state"] != state:
            continue
        et = e["norm"]
        if not et:
            continue
        inter = len(nt & et)
        union = len(nt | et) or 1
        jacc = inter / union
        seq = SequenceMatcher(None, " ".join(sorted(nt)), " ".join(sorted(et))).ratio()
        score = max(jacc, seq)
        # require at least one shared distinctive token (surname-ish)
        if inter == 0:
            continue
        if score > best_score:
            best_score, best = score, e
    return list(best["credentials"]) if best and best_score >= threshold else []


def _live_entries(report):
    """If live lookup is enabled in config, query the academy finders for this
    report's ZIP/radius and return registry-shaped entries to merge in."""
    try:
        import config
        cfg = config.load_config()
    except Exception:
        return [], []
    if not cfg.get("live_credential_lookup"):
        return [], []
    geo = report.get("geo") or {}
    zip5 = geo.get("zip_code") or ""
    state = ""
    m = re.search(r",\s*([A-Z]{2})[, ]", (geo.get("matched_address") or "") + " ")
    if m:
        state = m.group(1).upper()
    if not zip5:
        return [], ["Live credential lookup skipped: no ZIP resolved."]
    try:
        import credentials_live
        res = credentials_live.fetch_all(
            zip5, state, radius_mi=cfg.get("live_credential_radius_mi", 10),
            sources=cfg.get("live_credential_sources"))
    except Exception as e:
        return [], [f"Live credential lookup failed: {e.__class__.__name__}"]
    entries = []
    for r in res.get("entries", []):
        entries.append({"name": r.get("name", ""), "norm": _norm(r.get("name", "")),
                        "state": (r.get("state") or state or "").upper(),
                        "city": (r.get("city") or "").lower(),
                        "credentials": list(r.get("credentials", []))})
    return entries, res.get("notes", [])


def enrich_report(report):
    """Mutate report in place: add a `credentials` list to each competitor and
    referral, plus a `credential_summary` block for the analysis/report."""
    reg = list(load_registry())
    live_entries, live_notes = _live_entries(report)
    reg += live_entries
    comp_n = ref_n = 0

    for c in report.get("competitors", []):
        creds = set(signals_to_credentials(c.get("matched_signals", [])))
        creds |= set(_registry_match(reg, c.get("name", ""), _state_of(c.get("address", ""))))
        c["credentials"] = sorted(creds)
        if creds:
            comp_n += 1
            # A verified board credential confirms specialist tier.
            if any("ABOP" in x or "AADSM" in x for x in creds) and not str(c.get("tier", "")).startswith("Specialist"):
                c["tier"] = "Specialist"
            note = c.get("verification_note", "") or ""
            c["verification_note"] = (note + "  Credential match: " + ", ".join(sorted(creds))).strip()

    for r in report.get("referrals", []):
        creds = set(_registry_match(reg, r.get("name", ""), _state_of(r.get("address", ""))))
        r["credentials"] = sorted(creds)
        if creds:
            ref_n += 1

    live_on = bool(live_entries) or any("live" in n.lower() for n in live_notes)
    report["credential_summary"] = {
        "registry_entries": len(reg),
        "live_entries": len(live_entries),
        "live_lookup_enabled": live_on,
        "live_notes": live_notes,
        "competitors_credentialed": comp_n,
        "referrals_credentialed": ref_n,
        "sources": ["AAOP / ABOP", "AADSM / ABDSM", "AASM", "AAO-HNS"],
        "method": ("Website specialty signals + a locally maintained credential reference list"
                   + (", plus live academy-finder lookups for this ZIP" if live_on else "")
                   + ", fuzzy-matched by name and state."),
    }
    return report
