'use strict';
const $ = (s) => document.querySelector(s);
const form = $('#searchForm'), addrEl = $('#address'), btn = $('#runBtn');
const statusEl = $('#status'), resultsEl = $('#results');

// Backend base URL. On the web build the UI is served by the backend, so a
// relative path works. In the packaged phone app there is no server origin, so
// the user points it at their hosted backend (Render URL) once; we remember it.
const isNative = !/^https?:$/.test(location.protocol) || (location.hostname === 'localhost' && window.Capacitor);
function apiBase() {
  let b = (localStorage.getItem('apiBase') || '').trim().replace(/\s+/g, '').replace(/\/+$/, '');
  if (b && !/^https?:\/\//i.test(b)) b = 'https://' + b;     // add scheme if missing
  try { if (b) new URL(b); } catch (e) { b = ''; }            // ignore a malformed value
  return b;
}
function apiUrl(p) { return apiBase() ? apiBase() + p : p; }

// Never fail silently to a blank screen — surface any script error on-page.
window.addEventListener('error', (e) => {
  const s = document.getElementById('status');
  if (s) { s.className = 'status error'; s.classList.remove('hidden'); s.textContent = '⚠ ' + (e.message || 'Script error'); }
});
function setBackend() {
  const cur = apiBase();
  const v = window.prompt('Backend server URL (your hosted ClinicSiteIntel, e.g. https://clinicsiteintel.onrender.com):', cur);
  if (v !== null) { localStorage.setItem('apiBase', v.trim()); maybeBanner(); }
}
function maybeBanner() {
  if (isNative && !apiBase()) {
    statusEl.className = 'status error';
    statusEl.innerHTML = 'Tap ⚙ and set your backend server URL to start running reports.';
    statusEl.classList.remove('hidden');
  }
}
const sb = $('#settingsBtn'); if (sb) sb.addEventListener('click', setBackend);
maybeBanner();

const money = (v) => (v == null ? 'n/a' : '$' + Math.round(v).toLocaleString());
const num = (v, d = 0) => (v == null ? 'n/a' : Number(v).toFixed(d));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

let timer = null;
function showProgress() {
  const steps = [
    'Geocoding address (US Census)…',
    'Pulling tract & ZIP demographics (live ACS)…',
    'Finding credentialed competitors (NPI Registry)…',
    'Geocoding competitors & referrers to real coordinates…',
    'Running spatial models + Bayesian Location Viability Index…',
  ];
  let i = 0;
  statusEl.className = 'status';
  statusEl.innerHTML = `<div class="spinner"></div><div><b>Running live analysis…</b><br><span id="stepmsg">${steps[0]}</span><br><span class="sub">This takes ~60–90 seconds.</span></div>`;
  statusEl.classList.remove('hidden');
  timer = setInterval(() => { i = Math.min(i + 1, steps.length - 1); const m = $('#stepmsg'); if (m) m.textContent = steps[i]; }, 9000);
}
function stopProgress() { if (timer) clearInterval(timer); timer = null; }

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const address = addrEl.value.trim();
  if (!address) return;
  btn.disabled = true; resultsEl.innerHTML = ''; showProgress();
  try {
    const r = await fetch(apiUrl('/api/report'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address })
    });
    const data = await r.json();
    stopProgress(); statusEl.classList.add('hidden');
    if (!r.ok) throw new Error(data.error || 'Report failed.');
    render(data);
  } catch (err) {
    stopProgress();
    statusEl.className = 'status error';
    statusEl.textContent = '⚠ ' + err.message;
    statusEl.classList.remove('hidden');
  } finally { btn.disabled = false; }
});

function metric(k, v) { return `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`; }

function render(d) {
  let html = '';

  // Competitors
  if (d.competitors && d.competitors.length) {
    let rows = d.competitors.map(c => `<tr>
      <td><div class="nm">${esc(c.name)}</div><div class="meta">${esc(c.tier)} · ${esc(c.address)}</div></td>
      <td class="d">${c.distance_mi == null ? '' : num(c.distance_mi,1)+' mi'}</td></tr>`).join('');
    html += `<div class="card"><h3>Competitors (credentialed + advertising TMJ/sleep)</h3>
      <div class="body"><table class="comp">${rows}</table></div></div>`;
  }

  // Referral sources
  if (d.referrals && d.referrals.length) {
    let rows = d.referrals.map(x => `<tr>
      <td><div class="nm">${esc(x.name)}</div><div class="meta">${esc(x.specialty)}</div></td>
      <td class="d">${x.distance_mi == null ? '' : num(x.distance_mi,1)+' mi'}</td></tr>`).join('');
    html += `<div class="card"><h3>Referral sources nearby (physicians who feed OSA/TMD cases)</h3>
      <div class="body"><table class="comp">${rows}</table></div></div>`;
  }

  if (d.errors && d.errors.length) {
    html += `<div class="card"><h3>Data-gap notes</h3><div class="body errs">${d.errors.map(esc).join('<br>')}</div></div>`;
  }
  const frame = d.summary_html
    ? '<iframe id="summaryFrame" scrolling="no" title="Site assessment" style="width:100%;border:0;display:block;border-radius:18px;background:#f5f5f7;height:1200px;"></iframe>'
    : '';
  resultsEl.innerHTML = frame + html;
  if (d.summary_html) {
    const f = document.getElementById('summaryFrame');
    f.srcdoc = d.summary_html;
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// The premium summary iframe reports its content height so it never inner-scrolls.
window.addEventListener('message', (e) => {
  if (e.data && e.data.csiHeight) {
    const f = document.getElementById('summaryFrame');
    if (f) f.style.height = (e.data.csiHeight + 24) + 'px';
  }
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
