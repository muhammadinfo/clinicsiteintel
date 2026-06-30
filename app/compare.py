"""Two-address comparison view: a side-by-side metric table, an interactive map
showing both real addresses (with competitors & referrers), and an AI
comparative assessment.

All three render into a single QWebEngineView as one HTML page. The map uses
Leaflet + OpenStreetMap tiles (no key needed). The AI assessment uses the
Anthropic API if a key is configured, else a deterministic rule-based fallback.
"""
import json


def _num(x):
    return x if isinstance(x, (int, float)) else None


def extract_metrics(rep: dict) -> dict:
    geo = rep.get("geo") or {}
    lvi = rep.get("lvi_summary") or {}
    inp = rep.get("lvi_inputs") or {}
    ss = rep.get("site_selection") or {}
    d = ss.get("deliverables") or {}
    tract = rep.get("demographics_tract") or {}
    zipd = rep.get("demographics_zip") or {}
    comps = rep.get("competitors") or []
    specs = [c for c in comps if str(c.get("tier", "")).startswith("Specialist")]
    refs = [r for r in (rep.get("referrals") or []) if str(r.get("category", "")).startswith("Physician")]
    nearest = min([c.get("distance_mi") for c in specs if c.get("distance_mi") is not None], default=None)
    return {
        "address": geo.get("matched_address") or rep.get("address_input") or "—",
        "lat": _num(geo.get("lat")), "lon": _num(geo.get("lon")),
        "lvi": lvi.get("mean"), "p05": lvi.get("p05"), "p95": lvi.get("p95"),
        "rubric": ss.get("total"), "band": ss.get("band"),
        "in_building": d.get("in_building_physicians"),
        "within_half": d.get("within_half_mile"),
        "zip_refs": d.get("referring_physicians_total"),
        "n_spec": len(specs),
        "nearest_spec": nearest,
        "income": tract.get("median_household_income"),
        "age": tract.get("median_age"),
        "pop": zipd.get("population"),
        "ds": inp.get("ds"), "rp": inp.get("rp"), "if_": inp.get("if_"), "cp": inp.get("cp"),
        "competitors": specs, "referrals": refs,
    }


def _verdict(mean):
    if mean is None:
        return ("—", "#8e8e93")
    if mean >= 65:
        return ("PURSUE", "#34c759")
    if mean >= 50:
        return ("PURSUE WITH CONDITIONS", "#ff9f0a")
    return ("CAUTION", "#ff3b30")


# label, key, formatter, higher_is_better (None = no winner)
def _f1(v): return f"{v:.1f}" if isinstance(v, (int, float)) else "—"
def _f0(v): return f"{v:,.0f}" if isinstance(v, (int, float)) else "—"
def _f2(v): return f"{v:.2f}" if isinstance(v, (int, float)) else "—"
def _money(v): return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


_ROWS = [
    ("Location Viability Index", "lvi", _f1, True),
    ("Site-selection score (/100)", "rubric", _f1, True),
    ("Physicians in this building", "in_building", _f0, True),
    ("Referrers within ½ mile", "within_half", _f0, True),
    ("Referring physicians in ZIP", "zip_refs", _f0, True),
    ("Specialist competitors (fewer better)", "n_spec", _f0, False),
    ("Nearest competitor — miles (farther better)", "nearest_spec", _f2, True),
    ("Median household income", "income", _money, True),
    ("Median age", "age", _f1, None),
    ("ZIP population", "pop", _f0, True),
    ("Referral access · Rp", "rp", _f0, True),
    ("Medical hub · If", "if_", _f0, True),
    ("Competition score · Cp", "cp", _f0, True),
    ("Demographic fit · Ds", "ds", _f0, True),
]


def _winner(a, b, higher):
    if higher is None or not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or a == b:
        return 0
    if higher:
        return 1 if a > b else 2
    return 1 if a < b else 2


def build_comparison_html(repA: dict, repB: dict, ai_html: str = "", mapbox_key: str = "") -> str:
    A, B = extract_metrics(repA), extract_metrics(repB)
    vA, cA = _verdict(A["lvi"])
    vB, cB = _verdict(B["lvi"])

    # ---- headline winner (by LVI, with row-win tally as support) ----
    winsA = sum(1 for _, k, _, h in _ROWS if _winner(A[k], B[k], h) == 1)
    winsB = sum(1 for _, k, _, h in _ROWS if _winner(A[k], B[k], h) == 2)
    if isinstance(A["lvi"], (int, float)) and isinstance(B["lvi"], (int, float)):
        lead, gap = ("A", A["lvi"] - B["lvi"]) if A["lvi"] >= B["lvi"] else ("B", B["lvi"] - A["lvi"])
        win_addr = A["address"] if lead == "A" else B["address"]
        win_col = cA if lead == "A" else cB
        banner = (f"<b style='color:{win_col};'>{('Site A' if lead=='A' else 'Site B')}</b> leads by "
                  f"<b>{gap:.1f}</b> LVI points and wins <b>{max(winsA,winsB)}</b> of {len(_ROWS)} metrics — "
                  f"{win_addr}")
    else:
        banner = "Run both sites to compare."

    rows_html = ""
    for label, key, fmt, higher in _ROWS:
        w = _winner(A[key], B[key], higher)
        gpos = "background:#e8f8ee;font-weight:700;color:#1d8a3e;"
        aS = gpos if w == 1 else ""
        bS = gpos if w == 2 else ""
        sub = label.startswith(("Referral access", "Medical hub", "Competition score", "Demographic fit"))
        lblstyle = "color:#86868b;font-size:12.5px;padding-left:14px;" if sub else "font-weight:600;font-size:13.5px;"
        rows_html += (
            f"<tr style='border-bottom:1px solid #f0f0f3;'>"
            f"<td style='padding:9px 12px;{lblstyle}'>{label}</td>"
            f"<td style='padding:9px 14px;text-align:center;{aS}'>{fmt(A[key])}</td>"
            f"<td style='padding:9px 14px;text-align:center;{bS}'>{fmt(B[key])}</td></tr>")

    map_html = _build_map_block(A, B)

    ai_block = (ai_html if ai_html else
                "<div style='color:#86868b;font-size:13px;padding:6px 2px;'>"
                "Click <b>Generate AI assessment</b> above for a written comparative analysis.</div>")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ font-family:-apple-system,'Segoe UI',Roboto,sans-serif; margin:0; padding:18px 20px;
         color:#1c1c1e; background:#f5f5f7; }}
  .card {{ background:#fff; border:1px solid #e5e5ea; border-radius:16px; padding:18px 20px; margin-bottom:16px; }}
  h2 {{ font-size:15px; margin:0 0 12px; letter-spacing:-.01em; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  .head th {{ font-size:11px; letter-spacing:.05em; color:#86868b; text-transform:uppercase;
             padding:6px 14px; text-align:center; }}
  .pill {{ display:inline-block; border-radius:980px; padding:3px 12px; color:#fff; font-weight:800; font-size:11px; }}
  .addr {{ font-size:12px; color:#6e6e73; margin-top:3px; word-break:break-word; }}
</style></head><body>

<div class="card" style="border-left:4px solid #0071e3;">
  <div style="font-size:11px;font-weight:800;letter-spacing:.08em;color:#86868b;">HEAD-TO-HEAD</div>
  <div style="font-size:15px;margin-top:6px;">{banner}</div>
</div>

<div class="card">
  <table>
    <tr class="head">
      <th style="text-align:left;">Metric</th>
      <th>Site A<div class="pill" style="background:{cA};margin-top:4px;">{vA} · {_f1(A['lvi'])}</div>
          <div class="addr">{A['address']}</div></th>
      <th>Site B<div class="pill" style="background:{cB};margin-top:4px;">{vB} · {_f1(B['lvi'])}</div>
          <div class="addr">{B['address']}</div></th>
    </tr>
    {rows_html}
  </table>
  <div style="font-size:11px;color:#a1a1a6;margin-top:8px;">Green = better on that metric.</div>
</div>

<div class="card">
  <h2>📍 Site map</h2>
  {map_html}
  <div style="font-size:11px;color:#86868b;margin-top:8px;">
    <span style="color:#0071e3;">●</span> Site A &nbsp; <span style="color:#ff3b30;">●</span> Site B &nbsp;
    <span style="color:#ff9500;">●</span> specialist competitor &nbsp;
    <span style="color:#34c759;">●</span> referring physician</div>
</div>

<div class="card">
  <h2>🧠 AI comparative assessment</h2>
  {ai_block}
</div>

</body></html>"""


def _build_map_block(A, B):
    if not (A["lat"] and A["lon"] and B["lat"] and B["lon"]):
        return "<div style='color:#86868b;'>Map unavailable — missing geocoded coordinates.</div>"

    def pts(metrics, color):
        out = []
        for x in (metrics.get("competitors", []) if color == "#ff9500"
                  else metrics.get("referrals", [])):
            la, lo = _num(x.get("lat")), _num(x.get("lon"))
            if la and lo:
                nm = json.dumps((x.get("name") or "")[:40])
                out.append(f"mk([{la},{lo}],'{color}',{nm});")
        return "\n".join(out[:60])

    return f"""<div id="map" style="height:360px;border-radius:12px;overflow:hidden;"></div>
<script>
(function(){{
  function init(){{
    if (typeof L === 'undefined') {{ return setTimeout(init, 150); }}
    var map = L.map('map', {{scrollWheelZoom:false}});
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
      {{maxZoom:19, attribution:'© OpenStreetMap'}}).addTo(map);
    function mk(c, color, name){{
      L.circleMarker(c, {{radius:4, color:color, weight:1, fillColor:color, fillOpacity:.8}})
        .addTo(map).bindPopup(name);
    }}
    var a=[{A['lat']},{A['lon']}], b=[{B['lat']},{B['lon']}];
    {pts(A, "#ff9500")}
    {pts(A, "#34c759")}
    {pts(B, "#ff9500")}
    {pts(B, "#34c759")}
    L.marker(a).addTo(map).bindPopup({json.dumps('A · ' + A['address'])}).openPopup();
    L.marker(b).addTo(map).bindPopup({json.dumps('B · ' + B['address'])});
    L.circleMarker(a,{{radius:9,color:'#0071e3',weight:3,fillColor:'#0071e3',fillOpacity:.5}}).addTo(map);
    L.circleMarker(b,{{radius:9,color:'#ff3b30',weight:3,fillColor:'#ff3b30',fillOpacity:.5}}).addTo(map);
    map.fitBounds([a,b], {{padding:[50,50], maxZoom:14}});
  }}
  init();
}})();
</script>"""


# ----------------------------------------------------------------- AI assessment
_COMPARE_PROMPT = """You are an expert healthcare-practice site-selection consultant. Compare TWO candidate \
office locations for an Orofacial Pain / TMJ / Dental Sleep Medicine practice whose growth is REFERRAL-DRIVEN \
(not walk-in). Priorities, in order: (1) physician referral build-up, especially in-building / nearby referrers; \
(2) low direct specialist competition; (3) patient demand / population; rent is minor.

Be strictly factual and evidence-based — NO marketing language. Use only the numbers given. Where a number is \
missing, say so rather than guessing. Structure your answer as short HTML using only <p>, <b>, <ul>, <li> tags \
(no markdown, no <html>/<body>). End with a one-line bolded recommendation naming which site to pursue and the \
single most important caveat.

DATA:
"""


def _site_summary(m):
    def g(k, f=lambda x: x): return f(m[k]) if isinstance(m[k], (int, float)) else "n/a"
    return {
        "address": m["address"], "LVI": m["lvi"], "credible_interval": [m["p05"], m["p95"]],
        "rubric_score": m["rubric"], "physicians_in_building": m["in_building"],
        "referrers_within_half_mile": m["within_half"], "referrers_in_ZIP": m["zip_refs"],
        "specialist_competitors": m["n_spec"], "nearest_competitor_mi": m["nearest_spec"],
        "median_income": m["income"], "median_age": m["age"], "ZIP_population": m["pop"],
        "factors": {"referral_access": m["rp"], "medical_hub": m["if_"],
                    "competition": m["cp"], "demographics": m["ds"]},
    }


def build_ai_insight(anthropic_key: str, repA: dict, repB: dict) -> str:
    A, B = extract_metrics(repA), extract_metrics(repB)
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            payload = json.dumps({"Site_A": _site_summary(A), "Site_B": _site_summary(B)}, indent=2)
            msg = client.messages.create(
                model="claude-opus-4-8", max_tokens=1500,
                messages=[{"role": "user", "content": _COMPARE_PROMPT + payload}])
            txt = msg.content[0].text.strip()
            if "<p" in txt or "<ul" in txt:
                return txt
            # plain text fallback formatting
            return "".join(f"<p>{p.strip()}</p>" for p in txt.split("\n\n") if p.strip())
        except Exception as e:
            return (f"<p style='color:#ff3b30;'>AI assessment unavailable ({e.__class__.__name__}). "
                    f"Showing rule-based comparison instead.</p>" + _rule_based_insight(A, B))
    return _rule_based_insight(A, B)


def _rule_based_insight(A, B) -> str:
    lines = []

    def cmp(label, ka, kb, higher=True, fmt=lambda x: f"{x:.0f}"):
        a, b = A[ka], B[kb] if False else B[ka]
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return
        if a == b:
            return
        better = "A" if (a > b) == higher else "B"
        lines.append(f"<li><b>{label}:</b> Site {better} is stronger "
                     f"(A {fmt(a)} vs B {fmt(b)}).</li>")

    cmp("Referral build-up (in-building)", "in_building", "in_building", True, lambda x: f"{x:.0f}")
    cmp("Referrers within ½ mile", "within_half", "within_half", True)
    cmp("Specialist competition", "n_spec", "n_spec", False)
    cmp("Nearest competitor distance", "nearest_spec", "nearest_spec", True, lambda x: f"{x:.2f} mi")
    cmp("Household income", "income", "income", True, lambda x: f"${x:,.0f}")
    cmp("Population", "pop", "pop", True, lambda x: f"{x:,.0f}")

    la, lb = A["lvi"], B["lvi"]
    if isinstance(la, (int, float)) and isinstance(lb, (int, float)):
        lead = "A" if la >= lb else "B"
        addr = A["address"] if lead == "A" else B["address"]
        rec = (f"<p><b>Recommendation: pursue Site {lead} ({addr})</b> — higher overall viability "
               f"({max(la,lb):.1f} vs {min(la,lb):.1f}). Confirm in-building referral relationships and "
               f"lease terms before committing.</p>")
    else:
        rec = "<p>Run both sites to produce a recommendation.</p>"
    return ("<p>Comparison on the priorities that matter for a referral-driven practice:</p>"
            f"<ul>{''.join(lines) or '<li>Sites are closely matched on the measured factors.</li>'}</ul>" + rec)
