"""Live academy/board credential lookup (OPT-IN, OFF by default).

Queries the AADSM, AAOP/ABOP, AASM and AAO-HNS provider finders for the single
ZIP + radius being analyzed, normalizes the results, and hands them to the same
name+state cross-match used for the local registry (credentials.py).

IMPORTANT — terms of use: these academies' directories are published for direct
patient/referral use and their terms forbid bulk/database use. Automated querying
is enabled only by the `live_credential_lookup` config flag and is the operator's
responsibility. This module is deliberately POLITE, not stealthy:
  * one query per academy per report (the analyzed ZIP + radius), never a crawl
  * on-disk cache (default 30 days) so the same ZIP isn't re-queried
  * a minimum delay between live requests + an identifying User-Agent
  * no CAPTCHA / anti-bot circumvention

Each per-site adapter's HTTP request/parse shape is marked UNVERIFIED: it follows
the platform's typical pattern but must be confirmed against the live site (this
build environment cannot reach those domains). Until verified+enabled, an adapter
returns [] and records a note rather than guessing.
"""
import json
import os
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "assets", "_live_cred_cache.json")
CACHE_TTL_S = 30 * 24 * 3600
MIN_REQUEST_INTERVAL_S = 2.0
USER_AGENT = "ClinicSiteIntel/1.0 (clinic-site credential verification; contact: operator)"

_last_request_at = [0.0]
_cache = None


def _load_cache():
    global _cache
    if _cache is None:
        try:
            _cache = json.load(open(CACHE_PATH, encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _save_cache():
    try:
        json.dump(_cache, open(CACHE_PATH, "w", encoding="utf-8"))
    except OSError:
        pass


def _http_get(url, params=None, timeout=12):
    """Polite GET: rate-limited, identifying UA. Returns text or raises."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    wait = MIN_REQUEST_INTERVAL_S - (time.time() - _last_request_at[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _last_request_at[0] = time.time()
            return r.read().decode("utf-8", "replace")
    finally:
        _last_request_at[0] = time.time()


# --------------------------------------------------------------------------
# Per-academy adapters. Each: search(zip5, radius_mi) -> list of dicts
#   {"name": str, "city": str, "state": str, "credentials": [label]}
# Set VERIFIED=True only after confirming the endpoint/params/parse against the
# live site. While VERIFIED is False the adapter is a safe no-op.
# --------------------------------------------------------------------------

class _Adapter:
    KEY = ""
    LABEL = ""
    VERIFIED = False

    def search(self, zip5, radius_mi):
        raise NotImplementedError


class AADSM(_Adapter):
    KEY, LABEL, VERIFIED = "AADSM", "AADSM (Dental Sleep Medicine)", False
    # UNVERIFIED scaffold — MemberLeap directory at mms.aadsm.org. The live form
    # POSTs zip + miles; confirm the action URL, field names, and result markup.
    ENDPOINT = "https://mms.aadsm.org/members/directory/search_bootstrap.php"

    def search(self, zip5, radius_mi):
        if not self.VERIFIED:
            return []
        html = _http_get(self.ENDPOINT, {"org_id": "ADSM", "zip": zip5, "miles": radius_mi})
        return _parse_directory_rows(html, self.LABEL)


class AAOP(_Adapter):
    KEY, LABEL, VERIFIED = "AAOP", "AAOP Member (Orofacial Pain)", False
    # UNVERIFIED — ClubExpress finder at member.aaop.org/findamember (advanced
    # search by city/state/zip). Confirm endpoint + result markup.
    ENDPOINT = "https://member.aaop.org/findamember"

    def search(self, zip5, radius_mi):
        if not self.VERIFIED:
            return []
        html = _http_get(self.ENDPOINT, {"zip": zip5, "radius": radius_mi})
        return _parse_directory_rows(html, self.LABEL)


class AASM(_Adapter):
    KEY, LABEL, VERIFIED = "AASM", "AASM (Sleep Medicine)", False
    # UNVERIFIED — sleepeducation.org facility finder. Confirm endpoint + markup.
    ENDPOINT = "https://sleepeducation.org/sleep-center/"

    def search(self, zip5, radius_mi):
        if not self.VERIFIED:
            return []
        html = _http_get(self.ENDPOINT, {"zip": zip5, "distance": radius_mi})
        return _parse_directory_rows(html, self.LABEL)


class AAOHNS(_Adapter):
    KEY, LABEL, VERIFIED = "AAO-HNS", "AAO-HNS (Otolaryngology / ENT)", False
    # UNVERIFIED — enthealth.org "Find an ENT". Confirm endpoint + markup.
    ENDPOINT = "https://www.enthealth.org/find-an-ent/"

    def search(self, zip5, radius_mi):
        if not self.VERIFIED:
            return []
        html = _http_get(self.ENDPOINT, {"zip": zip5, "distance": radius_mi})
        return _parse_directory_rows(html, self.LABEL)


_ADAPTERS = {a.KEY: a() for a in (AADSM, AAOP, AASM, AAOHNS)}


def _parse_directory_rows(html, label):
    """UNVERIFIED placeholder parser. Real sites need a per-platform parser
    (the result markup differs per academy). Returns [] until implemented."""
    return []


def fetch_all(zip5, state, radius_mi=10, sources=None, _mock=None):
    """Query the enabled academy finders for one ZIP/radius and return
    normalized entries: {name, city, state, credentials:[label], source}.
    `_mock` (callable(key)->rows) is used by tests to bypass the network."""
    out, notes = [], []
    sources = sources or list(_ADAPTERS.keys())
    cache = _load_cache()
    for key in sources:
        ad = _ADAPTERS.get(key)
        if not ad:
            continue
        ck = f"{key}:{zip5}:{radius_mi}"
        if _mock is None and ck in cache and (time.time() - cache[ck].get("at", 0)) < CACHE_TTL_S:
            rows = cache[ck]["rows"]
        else:
            try:
                rows = _mock(key) if _mock else ad.search(zip5, radius_mi)
            except Exception as e:  # network/parse failure must not break the report
                notes.append(f"{key} live lookup failed: {e.__class__.__name__}")
                rows = []
            if _mock is None:
                cache[ck] = {"at": time.time(), "rows": rows}
        if _mock is None and not ad.VERIFIED:
            notes.append(f"{key} adapter not yet verified (enable after confirming endpoint).")
        for r in rows:
            r.setdefault("credentials", [ad.LABEL])
            r["source"] = key
            out.append(r)
    if _mock is None:
        _save_cache()
    return {"entries": out, "notes": notes}
