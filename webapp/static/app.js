'use strict';
const $ = (s) => document.querySelector(s);
const form = $('#searchForm'), addrEl = $('#address'), btn = $('#runBtn');
const statusEl = $('#status'), resultsEl = $('#results');

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
    const r = await fetch('/api/report', {
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
  const lvi = d.lvi || {}, v = d.verdict || {}, dem = d.demographics || {},
        comp = d.competition || {}, sp = d.spatial || {}, ec = d.econ || {};
  const near = comp.nearest_mi == null ? 'none' : num(comp.nearest_mi, 1) + ' mi';
  let html = '';

  // Hero
  html += `<div class="card"><div class="body">
    <p class="addr">${esc(d.address)}</p>
    <div class="hero">
      <div class="score" style="color:${v.color}">${num(lvi.mean, 1)}</div>
      <div>
        <span class="badge" style="background:${v.color}">${esc(v.band)}</span>
        <div class="sub" style="margin-top:6px">Location Viability Index · point ${num(lvi.point_estimate,1)} · 90% CI [${num(lvi.p05,1)}, ${num(lvi.p95,1)}]</div>
      </div>
    </div>
    <div class="metrics" style="margin-top:14px">
      ${metric('Median income', money(dem.income))}
      ${metric('Median age', num(dem.age,1))}
      ${metric('Specialist competitors', comp.count + ' · nearest ' + near)}
      ${metric('Referring physicians', (d.referrals||{}).count ?? 'n/a')}
      ${metric('Demand capture (Huff)', sp.huff == null ? 'n/a' : num(sp.huff,1)+'%')}
      ${metric('Cases/yr vs break-even', (ec.projected==null?'n/a':Math.round(ec.projected))+' / '+(ec.break_even==null?'n/a':Math.round(ec.break_even)))}
    </div>
  </div></div>`;

  // Consultant's Read
  if (d.consultant_html) {
    html += `<div class="card"><h3>Consultant's Read — how this verdict is built</h3>
      <div class="body consult">${d.consultant_html}</div></div>`;
  }

  // Competitors
  if (d.competitors && d.competitors.length) {
    let rows = d.competitors.map(c => `<tr>
      <td><div class="nm">${esc(c.name)}</div><div class="meta">${esc(c.tier)} · ${esc(c.address)}</div></td>
      <td class="d">${c.distance_mi == null ? '' : num(c.distance_mi,1)+' mi'}</td></tr>`).join('');
    html += `<div class="card"><h3>Competitors (credentialed + advertising TMJ/sleep)</h3>
      <div class="body"><table class="comp">${rows}</table></div></div>`;
  }

  if (d.errors && d.errors.length) {
    html += `<div class="card"><h3>Data-gap notes</h3><div class="body errs">${d.errors.map(esc).join('<br>')}</div></div>`;
  }
  resultsEl.innerHTML = html;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
