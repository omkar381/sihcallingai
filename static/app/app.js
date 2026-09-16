/* =============================================================================
   AI Krishi: product frontend

   One page per person who uses the platform: farmer, buyer, FPO manager, plus
   price discovery and the live call view. Every screen reads the same APIs the
   Kannada voice agent uses, so what is shown here and what a farmer hears on
   the phone cannot disagree.

   Rule carried over from the console: no price renders without its data
   quality, and demo fixtures are always labelled as such.
   ============================================================================= */

'use strict';

/* ------------------------------------------------------------------ utils */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (v) => String(v ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const nf = new Intl.NumberFormat('en-IN');
const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : nf.format(Math.round(Number(v)));
const inr = (v) => (v === null || v === undefined) ? '—' : '₹' + nf.format(Math.round(Number(v)));
const qtl = (kg) => (kg === null || kg === undefined) ? '—'
  : (Number(kg) >= 100 ? `${+(Number(kg) / 100).toFixed(1)} qtl` : `${num(kg)} kg`);
const pct = (v) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;

function ago(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function untilLabel(ts) {
  if (!ts) return 'no expiry';
  const s = ts - Date.now() / 1000;
  if (s <= 0) return 'expired';
  if (s < 3600) return `expires in ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `expires in ${Math.round(s / 3600)} h`;
  return `expires in ${Math.round(s / 86400)} days`;
}

function dateLabel(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function phoneLabel(p, mask = false) {
  if (!p) return '—';
  const d = String(p).replace(/[^\d]/g, '');
  const last10 = d.slice(-10);
  if (last10.length !== 10) return p;
  return mask ? `+91 ••••• ${last10.slice(5)}` : `+91 ${last10.slice(0, 5)} ${last10.slice(5)}`;
}

function normalisePhone(p) {
  const d = String(p || '').replace(/[^\d]/g, '');
  if (d.length === 10) return `+91${d}`;
  if (d.length === 12 && d.startsWith('91')) return `+${d}`;
  return p ? String(p).trim() : '';
}

const shortId = (id) => String(id || '').slice(0, 8);

const store = {
  get(k, fallback = '') {
    try { return localStorage.getItem(`krishi.${k}`) ?? fallback; } catch { return fallback; }
  },
  set(k, v) {
    try { v ? localStorage.setItem(`krishi.${k}`, v) : localStorage.removeItem(`krishi.${k}`); } catch { /* storage blocked */ }
  },
};

/* -------------------------------------------------------------------- api */

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(path, { method = 'GET', body, query } = {}) {
  let url = path;
  if (query) {
    const qs = new URLSearchParams();
    Object.entries(query).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') qs.set(k, v); });
    const s = qs.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  const headers = { 'Content-Type': 'application/json' };
  if (method !== 'GET') {
    const key = store.get('apiKey');
    if (!key) {
      openKeyModal('This action changes records, so it needs the operator key.');
      throw new ApiError('Operator key required', 401);
    }
    headers['X-API-Key'] = key;
  }
  const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = typeof detail === 'string' ? detail
      : (detail && (detail.message || detail.error)) || `Request failed (${res.status})`;
    if (res.status === 401 || res.status === 403) {
      openKeyModal('The operator key was not accepted. Check API_KEY in the server .env.');
    }
    throw new ApiError(message, res.status);
  }
  return data;
}

function toast(message, kind = 'good') {
  const el = document.createElement('div');
  el.className = `toast ${kind === 'bad' ? 'bad' : ''}`;
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 6000 : 3500);
}

async function run(button, fn, success) {
  const label = button ? button.innerHTML : '';
  if (button) { button.disabled = true; button.innerHTML = '<span class="spinner"></span>'; }
  try {
    const result = await fn();
    if (success) toast(typeof success === 'function' ? success(result) : success);
    return result;
  } catch (err) {
    if (err.status !== 401) toast(err.message, 'bad');
    return undefined;
  } finally {
    if (button && button.isConnected) { button.disabled = false; button.innerHTML = label; }
  }
}

/* ------------------------------------------------------------- components */

const QUALITY = {
  LIVE: ['good', 'Live'], FRESH: ['good', 'Fresh'], STALE: ['warn', 'Stale'],
  DEGRADED: ['warn', 'Degraded'], MOCK: ['warn', 'Demo data'], UNAVAILABLE: ['bad', 'Unavailable'],
};

function qualityTag(q) {
  if (!q) return '';
  const [cls, label] = QUALITY[q] || ['', q];
  return `<span class="tag ${cls}" title="Data quality: ${esc(q)}"><span class="dot"></span>${esc(label)}</span>`;
}

const GOOD = ['PUBLISHED', 'ACCEPTED', 'PAID', 'COMPLETED', 'RELEASED', 'ESCROW', 'VERIFIED', 'DELIVERED', 'RESOLVED', 'DELIVERY_CONFIRMED'];
const BAD = ['CANCELLED', 'DISPUTED', 'FAILED', 'SUSPENDED', 'REJECTED', 'REFUNDED', 'EXPIRED'];
const WARN = ['PENDING', 'PAYMENT_PENDING', 'OPEN', 'UNDER_REVIEW', 'DRAFT', 'INITIATED', 'UNVERIFIED', 'OFFER_RECEIVED', 'ESCALATED', 'REQUESTED_INFORMATION', 'RELEASE_PENDING'];

function statusTag(s) {
  if (!s) return '';
  const cls = GOOD.includes(s) ? 'good' : BAD.includes(s) ? 'bad' : WARN.includes(s) ? 'warn' : '';
  const label = s.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
  return `<span class="tag ${cls}">${esc(label)}</span>`;
}

function trustTag(badge) {
  if (!badge) return '';
  const band = badge.trust_band;
  const cls = band === 'STRONG' ? 'good' : ['POOR', 'DO_NOT_TRADE'].includes(band) ? 'bad' : band === 'NEW' ? 'info' : 'warn';
  return `<span class="tag ${cls}" title="Trust score ${esc(badge.trust_score)}">${esc(badge.label)}</span>`;
}

function demoTag(type, id) {
  const ids = (app.demo?.entity_ids || {})[type] || [];
  return ids.includes(id) ? '<span class="tag warn" title="Created by the demo seeder">Demo</span>' : '';
}

const INTENT_LABEL = {
  PRICE: 'Price', COMPARE_MARKETS: 'Best market', PRICE_TREND: 'Trend', SELL_OR_WAIT: 'Sell or wait',
  FIND_BUYERS: 'Buyers', LIST_FOR_SALE: 'List produce', MY_OFFERS: 'Offers', ACCEPT_OFFER: 'Offer accepted',
  PAYMENT_STATUS: 'Payment', RAISE_COMPLAINT: 'Complaint', FPO_POOLING: 'FPO pooling', HELP: 'Help',
  GREETING: 'Greeting', GOODBYE: 'Goodbye', REPEAT: 'Repeat', NO: 'Declined', OTHER: 'General',
};

const empty = (title, body = '') => `<div class="empty"><strong>${esc(title)}</strong>${esc(body)}</div>`;
const loading = () => '<div class="empty"><span class="spinner"></span></div>';

function card(title, body, { hint = '', flush = false, actions = '', id = '' } = {}) {
  return `<section class="card"${id ? ` id="${id}"` : ''}>
    <div class="card-head"><h2>${title}</h2><div class="btn-row">${hint ? `<span class="hint">${hint}</span>` : ''}${actions}</div></div>
    <div class="card-body${flush ? ' flush' : ''}">${body}</div>
  </section>`;
}

function metric(label, value, note = '') {
  return `<div class="metric"><div class="metric-label">${esc(label)}</div><div class="metric-value">${value}</div>${note ? `<div class="metric-note">${note}</div>` : ''}</div>`;
}

function demoNotice() {
  if (!app.overview?.demo_mode) return '';
  return `<div class="notice warn"><div class="grow"><strong>Demo mode.</strong> Mandi prices, buyers, FPOs and offers here are demonstration fixtures and are tagged as such. They are not real market data. Set <span class="mono">DATA_GOV_API_KEY</span> to ingest live Agmarknet prices.</div></div>`;
}

const icons = {
  phone: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2Z"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m4 12 16-8-6 16-2.5-6.5z"/></svg>',
};

/* --------------------------------------------------------------- app state */

const app = {
  overview: null,
  demo: null,
  commodities: [],
  markets: [],
  cleanup: [],
};

async function loadShared() {
  const [overview, demo, commodities, markets] = await Promise.allSettled([
    api('/api/system/overview'), api('/api/demo/status'),
    api('/api/market/commodities'), api('/api/market/markets'),
  ]);
  if (overview.status === 'fulfilled') app.overview = overview.value;
  if (demo.status === 'fulfilled') app.demo = demo.value;
  if (commodities.status === 'fulfilled') app.commodities = (commodities.value.commodities || []).map((c) => c.name).sort();
  if (markets.status === 'fulfilled') app.markets = (markets.value.markets || []).map((m) => m.name).sort();

  const tag = $('#mode-tag');
  if (app.overview) {
    tag.innerHTML = app.overview.market.is_demo_data || app.overview.demo_mode
      ? '<span class="tag warn"><span class="dot"></span>Demo data</span>'
      : '<span class="tag good"><span class="dot"></span>Live data</span>';
  }
  renderKeyButton();
}

function commodityOptions(selected = 'Onion') {
  const list = app.commodities.length ? app.commodities : ['Onion', 'Tur', 'Cotton', 'Maize', 'Tomato'];
  return list.map((c) => `<option ${c === selected ? 'selected' : ''}>${esc(c)}</option>`).join('');
}

function marketDatalist(id) {
  return `<datalist id="${id}">${app.markets.map((m) => `<option value="${esc(m)}">`).join('')}</datalist>`;
}

function defaultPhone() {
  return store.get('farmerPhone') || (app.demo?.anchor_phones || [])[0] || '';
}

/* ------------------------------------------------------------ operator key */

function renderKeyButton() {
  const btn = $('#key-button');
  btn.textContent = store.get('apiKey') ? 'Operator key set' : 'Operator key';
}

function openKeyModal(reason = '') {
  const root = $('#modal-root');
  if (root.childElementCount) return;
  root.innerHTML = `<div class="modal-backdrop" data-close>
    <div class="modal card" role="dialog" aria-modal="true" aria-labelledby="key-title">
      <div class="card-head"><h2 id="key-title">Operator key</h2></div>
      <div class="card-body">
        <p>${esc(reason || 'Actions that change records or place calls need the operator key.')} It is the <span class="mono">API_KEY</span> value in the server's <span class="mono">.env</span>, and it is kept only in this browser.</p>
        <div class="field"><label for="key-input">Key</label><input id="key-input" type="password" autocomplete="off" value="${esc(store.get('apiKey'))}"></div>
        <div class="btn-row" style="margin-top:14px;justify-content:flex-end">
          <button class="btn ghost" data-action="clear">Remove key</button>
          <button class="btn secondary" data-close>Cancel</button>
          <button class="btn" data-action="save">Save</button>
        </div>
      </div>
    </div></div>`;
  const close = () => { root.innerHTML = ''; };
  root.querySelector('.modal-backdrop').addEventListener('click', (e) => {
    if (e.target.hasAttribute('data-close')) close();
  });
  root.querySelector('[data-action="save"]').addEventListener('click', () => {
    store.set('apiKey', $('#key-input').value.trim()); renderKeyButton(); close(); toast('Operator key saved');
  });
  root.querySelector('[data-action="clear"]').addEventListener('click', () => {
    store.set('apiKey', ''); renderKeyButton(); close();
  });
  setTimeout(() => $('#key-input')?.focus(), 30);
}

/* ================================================================== HOME */

async function viewHome(root) {
  const o = app.overview;
  const number = o?.voice_number;
  root.innerHTML = `
    ${demoNotice()}
    <div class="hero">
      <div>
        <span class="eyebrow"><span class="live-dot"></span>Kannada voice line · SIH PS 26132</span>
        <h1>Know the <em>price</em>. Pick the <em>market</em>. Sell to a buyer you can trust.</h1>
        <p class="lede">A farmer calls and asks in Kannada. The agent answers from mandi prices, transport costs and verified buyer demand, then helps list the produce, weigh offers and track the payment.</p>
        <div class="hero-actions">
          <a class="btn large" href="/calls" data-link>Talk to the agent</a>
          <a class="btn secondary large" href="/prices" data-link>Check a price</a>
        </div>
        ${number ? `<div class="dial">
          <span class="dial-icon">${icons.phone}</span>
          <div><div class="dial-label">Call the Kannada line</div><div class="dial-number">${esc(number)}</div></div>
          <span class="muted" style="margin-left:auto;font-size:12px;text-align:right">Replies in<br>about 4 seconds</span>
        </div>` : ''}
      </div>
      <div id="home-exchange">${card('A recent exchange', loading())}</div>
    </div>

    <div class="metrics" id="home-metrics">${homeMetrics(o)}</div>

    <div class="section-title"><h2>What it does</h2><p>Mapped to the problem statement's three gaps</p></div>
    <div class="pillars">
      ${pillar('Price discovery', 'Farmers see what their crop fetches and what they would actually keep.', [
        ['Prices across nearby mandis', '/prices'], ['7, 30 and 90-day trends with arrivals', '/prices'],
        ['Net price after freight, fees and commission', '/prices'], ['Sell now, wait, or sell part', '/prices'],
      ])}
      ${pillar('Market linkage', 'Produce meets demand from buyers whose documents were checked.', [
        ['Verified buyers and open demand', '/buyer'], ['Lots with declared and inspected grade', '/farmer'],
        ['Digital offers, accepted by phone', '/farmer'], ['FPO pooling of small lots', '/fpo'],
      ])}
      ${pillar('Trust and settlement', 'Money moves only when both sides are protected.', [
        ['Escrow before dispatch', '/farmer'], ['Complaints that freeze payment', '/farmer'],
        ['Every call transcribed and logged', '/calls'], ['Append-only audit trail', '/console'],
      ])}
    </div>

    <div class="section-title"><h2>What a farmer actually keeps</h2><p>Ranked on net price, not the headline rate</p></div>
    <div id="home-compare"></div>
  `;

  renderExchange();
  renderCompareWidget($('#home-compare'));
}

function homeMetrics(o) {
  if (!o) return metric('Status', '—', 'Could not load the overview');
  const m = o.market; const t = o.trade; const c = o.calls;
  return [
    metric('Markets tracked', num(m.markets), `${m.commodities_with_data} of ${m.commodities_supported} crops priced`),
    metric('Verified buyers', num(t.buyers_verified), `${num(t.buyers_total)} registered`),
    metric('Open buyer demand', t.open_demand_kg ? qtl(t.open_demand_kg) : '—', `${num(t.open_demand)} requests`),
    metric('Produce listed', num(t.lots_listed), `${num(t.open_offers)} open offers`),
    metric('Held in escrow', inr(t.escrow_rupees), `${inr(t.released_rupees)} released`),
    metric('Calls, last 24 h', num(c.calls_last_24h), c.median_turn_ms ? `median reply ${(c.median_turn_ms / 1000).toFixed(1)} s` : `${num(c.turns_total)} turns logged`),
  ].join('');
}

function pillar(title, text, items) {
  return `<section class="card pillar"><h3>${esc(title)}</h3><p>${esc(text)}</p><ul>
    ${items.map(([label, href]) => `<li><a href="${href}" ${href.startsWith('/console') ? '' : 'data-link'}><span>${esc(label)}</span><span class="muted">→</span></a></li>`).join('')}
  </ul></section>`;
}

async function renderExchange() {
  const host = $('#home-exchange');
  let turn = null;
  try {
    const { calls } = await api('/api/calls', { query: { limit: 15 } });
    for (const c of calls.filter((x) => x.turn_count > 0)) {
      const detail = await api(`/api/calls/${encodeURIComponent(c.call_sid)}`);
      turn = detail.turns.find((t) => ['COMPARE_MARKETS', 'MY_OFFERS', 'SELL_OR_WAIT', 'FIND_BUYERS', 'PRICE'].includes(t.intent));
      if (turn) { turn.channel = c.channel; turn.started_at = c.started_at; break; }
    }
  } catch { /* fall back to the example below */ }

  const example = !turn;
  turn = turn || {
    farmer_kn: 'ನನ್ನ ಬಳಿ ಮೂವತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಇದೆ, ಯಾವ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಒಳ್ಳೆಯ ಬೆಲೆ ಸಿಗುತ್ತದೆ?',
    farmer_en: 'I have thirty quintal of onion. Which market gives a good price?',
    reply_kn: 'ಕಲಬುರಗಿಯಲ್ಲಿ ಖರ್ಚು ಕಳೆದು ಕ್ವಿಂಟಲ್‌ಗೆ ಸುಮಾರು 2550 ರೂಪಾಯಿ ಕೈಗೆ ಬರುತ್ತದೆ. ಸೊಲ್ಲಾಪುರ 136 ಕಿಲೋಮೀಟರ್ ದೂರ, ಆದರೆ ಅಲ್ಲಿ 2912 ರೂಪಾಯಿ ಸಿಗುತ್ತದೆ.',
    reply_en: 'At Kalaburagi you keep about ₹2,550 per quintal after costs. Solapur is 136 km away but leaves you ₹2,912.',
    intent: 'COMPARE_MARKETS', data_quality: 'MOCK',
  };
  host.innerHTML = card(example ? 'How a call sounds' : 'A recent exchange', `
    <div class="transcript" style="padding:0">${turnHtml(turn, { compact: true })}</div>`,
    { hint: example ? 'Example' : `${turn.channel === 'web' ? 'Web' : 'Phone'} · ${ago(turn.created_at)}` });
}

function renderCompareWidget(host, initial = {}) {
  host.innerHTML = card('Compare markets', `
    <form class="form-row" id="cmp-form">
      <div class="field"><label>Crop</label><select name="commodity">${commodityOptions(initial.commodity || 'Onion')}</select></div>
      <div class="field"><label>Your market</label><input name="location" list="cmp-markets" value="${esc(initial.location || 'Kalaburagi')}">${marketDatalist('cmp-markets')}</div>
      <div class="field"><label>Quantity (quintal)</label><input name="quantity" type="number" min="1" step="0.5" value="${esc(initial.quantity || 30)}"></div>
      <div class="field"><button class="btn" type="submit">Compare</button></div>
    </form>
    <div id="cmp-result" style="margin-top:16px"></div>`,
    { hint: 'Freight, loading, APMC fee, commission and expected rejection' });

  const form = $('#cmp-form', host);
  const submit = async (e) => {
    e?.preventDefault();
    const f = new FormData(form);
    const out = $('#cmp-result', host);
    out.innerHTML = loading();
    try {
      const data = await api('/api/market/compare', {
        query: { commodity: f.get('commodity'), location: f.get('location'), quantity: `${f.get('quantity')} quintal` },
      });
      out.innerHTML = compareTable(data);
    } catch (err) {
      out.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`;
    }
  };
  form.addEventListener('submit', submit);
  submit();
}

function compareTable(data) {
  const options = data.options || [];
  if (!options.length) return empty('No comparable markets', 'No usable prices were found nearby.');
  const best = data.best_market;
  const rows = options.map((o) => {
    const costs = o.gross_price_per_quintal - o.net_price_per_quintal;
    return `<tr class="${o.market === best ? 'best' : ''}">
      <td><strong>${esc(o.market)}</strong>${o.is_origin ? ' <span class="tag">Your market</span>' : ''}${o.market === best ? ' <span class="tag good">Best after costs</span>' : ''}<span class="sub">${esc(o.district)}, ${esc(o.state)}</span></td>
      <td class="right">${o.distance_km ? `${num(o.distance_km)} km` : '—'}</td>
      <td class="right">${inr(o.gross_price_per_quintal)}</td>
      <td class="right muted">−${inr(costs)}</td>
      <td class="right"><strong>${inr(o.net_price_per_quintal)}</strong></td>
      <td class="right">${inr(o.total_net)}</td>
      <td>${qualityTag(o.data_quality)}</td>
    </tr>`;
  }).join('');
  const gain = data.advantage_total && data.advantage_total > 0
    ? `<div class="notice good" style="margin:0 0 12px"><div class="grow">Selling at <strong>${esc(best)}</strong> leaves <strong>${inr(data.advantage_per_quintal)} more per quintal</strong>, ${inr(data.advantage_total)} on ${qtl(data.quantity_kg)}, after every cost.</div></div>`
    : `<div class="notice info" style="margin:0 0 12px"><div class="grow">After costs, the nearby market is already the best choice for ${qtl(data.quantity_kg)}.</div></div>`;
  return `${gain}<div class="card"><div class="table-wrap"><table class="data">
    <thead><tr><th>Market</th><th class="right">Distance</th><th class="right">Price / qtl</th><th class="right">Costs / qtl</th><th class="right">Net / qtl</th><th class="right">You keep</th><th>Data</th></tr></thead>
    <tbody>${rows}</tbody></table></div></div>`;
}

/* ================================================================ PRICES */

async function viewPrices(root) {
  root.innerHTML = `
    <div class="page-head"><div><h1>Price discovery</h1><p>Today's rate, how it has moved, where it pays most after costs, and whether to sell now.</p></div></div>
    ${demoNotice()}
    ${card('Your crop', `
      <form class="form-row" id="px-form">
        <div class="field"><label>Crop</label><select name="commodity">${commodityOptions('Onion')}</select></div>
        <div class="field"><label>Your market</label><input name="location" list="px-markets" value="Kalaburagi">${marketDatalist('px-markets')}</div>
        <div class="field"><label>Quantity (quintal)</label><input name="quantity" type="number" min="1" step="0.5" value="30"></div>
        <div class="field"><label>Can store for (days)</label><input name="storage_days" type="number" min="0" max="365" value="15"></div>
        <label class="check"><input type="checkbox" name="urgent"> Needs cash now</label>
        <div class="field"><button class="btn" type="submit">Analyse</button></div>
      </form>`)}
    <div id="px-out" class="stack" style="margin-top:18px"></div>`;

  const form = $('#px-form');
  form.addEventListener('submit', (e) => { e.preventDefault(); analysePrices(new FormData(form)); });
  analysePrices(new FormData(form));
}

async function analysePrices(f) {
  const out = $('#px-out');
  const commodity = f.get('commodity'); const location = f.get('location');
  const quantity = `${f.get('quantity') || 10} quintal`;
  out.innerHTML = `<div class="grid-2"><div id="px-price">${card('Today', loading())}</div><div id="px-trend">${card('Trend', loading())}</div></div>
    <div id="px-window">${card('Sell or wait', loading())}</div>
    <div id="px-compare">${card('Best market after costs', loading())}</div>`;

  const [price, trend, windowRes, compare] = await Promise.allSettled([
    api('/api/market/price', { query: { commodity, location } }),
    api('/api/market/trend', { query: { commodity, location } }),
    api('/api/market/sale-window', { query: { commodity, location, quantity, storage_days: f.get('storage_days') || 0, needs_cash_urgently: f.get('urgent') ? 'true' : 'false' } }),
    api('/api/market/compare', { query: { commodity, location, quantity } }),
  ]);

  const fail = (r) => `<div class="notice bad" style="margin:0">${esc(r.reason?.message || 'Unavailable')}</div>`;

  $('#px-price').innerHTML = card(`Today at ${esc(price.value?.market || location)}`, price.status === 'fulfilled' ? `
    <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
      <span class="figure" style="font-size:30px;font-weight:620;letter-spacing:-0.02em">${inr(price.value.modal_price)}</span>
      <span class="muted">per quintal · about ${inr(price.value.modal_price / 100)}/kg</span>
    </div>
    <dl class="kv" style="margin-top:14px">
      <dt>Range traded</dt><dd>${inr(price.value.min_price)} – ${inr(price.value.max_price)}</dd>
      <dt>Arrivals</dt><dd>${price.value.arrival_qty ? `${num(price.value.arrival_qty)} t` : '—'}</dd>
      <dt>Price date</dt><dd>${esc(price.value.arrival_date || '—')}</dd>
      <dt>Source</dt><dd>${esc(price.value.provenance?.source || '—')}</dd>
    </dl>
    ${price.value.caveat ? `<div class="notice warn" style="margin:14px 0 0">${esc(price.value.caveat)}</div>` : ''}` : fail(price),
    { actions: price.status === 'fulfilled' ? qualityTag(price.value.data_quality) : '' });

  $('#px-trend').innerHTML = card('Trend', trend.status === 'fulfilled' ? trendTable(trend.value) : fail(trend),
    { actions: trend.status === 'fulfilled' ? qualityTag(trend.value.data_quality) : '', flush: trend.status === 'fulfilled' });

  $('#px-window').innerHTML = card('Sell or wait', windowRes.status === 'fulfilled' ? saleWindow(windowRes.value) : fail(windowRes),
    { actions: windowRes.status === 'fulfilled' ? qualityTag(windowRes.value.data_quality) : '' });

  $('#px-compare').innerHTML = card('Best market after costs', compare.status === 'fulfilled' ? compareTable(compare.value) : fail(compare),
    { hint: compare.status === 'fulfilled' ? `${compare.value.coverage?.markets_with_usable_prices ?? '—'} of ${compare.value.coverage?.markets_considered ?? '—'} nearby markets had usable prices` : '' });
}

function trendTable(t) {
  const rows = ['7', '30', '90'].map((k) => {
    const w = (t.windows || {})[k]; const a = (t.arrivals || {})[k];
    if (!w) return '';
    const dir = w.change_pct > 0 ? 'good' : w.change_pct < 0 ? 'bad' : '';
    return `<tr>
      <td>${k} days</td>
      <td class="right">${w.sufficient_data ? inr(w.average) : '<span class="muted">Too few days</span>'}</td>
      <td class="right"><span class="tag ${dir}">${pct(w.change_pct)}</span></td>
      <td>${esc((w.volatility || '').toLowerCase())}</td>
      <td class="right muted">${a && a.change_pct !== null && a.change_pct !== undefined ? pct(a.change_pct) : '—'}</td>
    </tr>`;
  }).join('');
  return `<div class="table-wrap"><table class="data" style="min-width:420px">
    <thead><tr><th>Window</th><th class="right">Average</th><th class="right">Change</th><th>Volatility</th><th class="right">Arrivals</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

const DECISION = {
  SELL_NOW: ['Sell now', ''], WAIT: ['Wait', 'wait'], SELL_PARTIAL: ['Sell part now', ''],
  COMPARE_MARKETS: ['Sell, at a better market', ''], INSUFFICIENT_DATA: ['Not enough data', 'none'],
};

function saleWindow(w) {
  const [label, cls] = DECISION[w.decision] || [w.decision, 'none'];
  const alt = w.alternative_market;
  const range = w.expected_price_range;
  return `<div class="decision">
      <span class="decision-badge ${cls}">${esc(label)}</span>
      <div class="grow" style="flex:1">
        <div class="mid">${w.recommended_days ? `Hold about <strong>${w.recommended_days} days</strong>. ` : ''}Confidence: <strong>${esc((w.confidence || '').toLowerCase().replace('_', ' '))}</strong>${w.current_price ? ` · today ${inr(w.current_price)}/qtl` : ''}</div>
        ${alt ? `<div style="margin-top:6px"><strong>${esc(alt.market)}</strong>, ${num(alt.distance_km)} km away, leaves about <strong>${inr(alt.advantage_per_quintal)}/qtl more</strong> after transport.</div>` : ''}
        ${range ? `<div style="margin-top:6px">Expected range if you wait: <strong>${inr(range.low)} – ${inr(range.high)}</strong> per quintal.</div>` : ''}
        <div class="grid-2" style="margin-top:12px;gap:24px">
          <div><div class="muted" style="font-size:12px">Why</div><ul class="bullets">${(w.reasoning || []).map((r) => `<li>${esc(r)}</li>`).join('') || '<li>—</li>'}</ul></div>
          <div><div class="muted" style="font-size:12px">Risks</div><ul class="bullets risk">${(w.risks || []).map((r) => `<li>${esc(r)}</li>`).join('') || '<li>None flagged</li>'}</ul></div>
        </div>
      </div>
    </div>`;
}

/* ================================================================ FARMER */

async function viewFarmer(root) {
  const phone = defaultPhone();
  root.innerHTML = `
    <div class="page-head">
      <div><h1>Farmer workspace</h1><p>Everything in motion for one farmer: produce listed, offers to decide, payments and complaints. The same records the farmer reaches by phone.</p></div>
      <form class="btn-row" id="farmer-open">
        <input name="phone" placeholder="10-digit mobile" value="${esc(phoneLabel(phone))}" style="width:190px" aria-label="Farmer mobile number">
        <button class="btn secondary" type="submit">Open</button>
        <button class="btn" type="button" id="farmer-call">${icons.phone}<span>Call me</span></button>
      </form>
    </div>
    ${demoNotice()}
    <div id="farmer-body">${phone ? loading() : farmerEmpty()}</div>`;

  $('#farmer-open').addEventListener('submit', (e) => {
    e.preventDefault();
    const p = normalisePhone(new FormData(e.target).get('phone'));
    if (!p) return;
    store.set('farmerPhone', p);
    loadFarmer(p);
  });
  $('#farmer-call').addEventListener('click', (e) => {
    const p = normalisePhone($('#farmer-open input').value);
    if (!p) { toast('Enter a mobile number first', 'bad'); return; }
    run(e.currentTarget, () => api('/api/call', { method: 'POST', body: { phone_number: p } }),
      (r) => `Calling ${phoneLabel(r.phone_number)}. Answer in Kannada.`);
  });
  if (phone) loadFarmer(phone);
}

function farmerEmpty() {
  const anchors = app.demo?.anchor_phones || [];
  return card('Open a farmer', `
    <p class="mid" style="margin-top:0">Enter the farmer's mobile number above. Every call from that number, and everything listed against it, shows up here.</p>
    ${anchors.length ? `<div class="btn-row">${anchors.map((p) => `<button class="chip" data-phone="${esc(p)}">${esc(phoneLabel(p))}</button>`).join('')}</div>` : ''}
    ${app.overview?.demo_mode ? `<div class="notice info" style="margin:16px 0 0"><div class="grow">No marketplace yet? Seed demo buyers, an FPO, offers and an escrowed payment around a number you can call from.</div>
      <form class="btn-row" id="seed-form"><input name="phone" placeholder="Your mobile" style="width:160px"><button class="btn small" type="submit">Seed demo</button></form></div>` : ''}`);
}

async function loadFarmer(phone) {
  const body = $('#farmer-body');
  body.innerHTML = loading();
  let ws;
  try {
    ws = await api(`/api/farmers/${encodeURIComponent(phone)}/workspace`);
  } catch (err) {
    body.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`;
    return;
  }
  const s = ws.summary;
  const openOffers = ws.lots.flatMap((l) => l.offers.filter((o) => o.status === 'OPEN').map((o) => ({ ...o, lot: l })))
    .sort((a, b) => b.price_per_quintal - a.price_per_quintal);
  const committedLots = ws.lots.filter((l) => l.accepted_offer_id);
  const nothing = !ws.lots.length && !ws.payments.length && !ws.fpos.length;

  body.innerHTML = `
    <div class="metrics">
      ${metric('Produce listed', num(s.listed))}
      ${metric('Offers to decide', num(s.open_offers))}
      ${metric('Held in escrow', inr(s.in_escrow_rupees))}
      ${metric('Paid out', inr(s.released_rupees))}
      ${metric('Open complaints', num(s.open_disputes))}
    </div>
    ${nothing ? `<div style="margin-top:18px">${farmerEmpty()}</div>` : ''}
    <div class="split" style="margin-top:18px">
      <div class="stack">
        ${card('Offers waiting for you', openOffers.length ? `<ul class="rows">${openOffers.map(offerRow).join('')}</ul>` : empty('No open offers', ws.lots.length ? 'Verified buyers have not bid on your listed produce yet.' : ''), { flush: true, hint: 'Highest first' })}
        ${card('My produce', lotsTable(ws.lots), { flush: true, actions: '<button class="btn small secondary" id="toggle-list">List produce</button>' })}
        <div id="list-form" hidden>${listForm(phone)}</div>
        ${card('Payments', ws.payments.length ? `<ul class="rows">${ws.payments.map(paymentRow).join('')}</ul>` : empty('No payments yet', 'A payment appears once an offer is accepted.'), { flush: true })}
      </div>
      <div class="stack">
        ${card('Complaints', `${ws.disputes.length ? `<ul class="rows">${ws.disputes.map(disputeRow).join('')}</ul>` : empty('No complaints')}
          ${committedLots.length ? complaintForm(phone, committedLots, ws.payments) : ''}`, { flush: true })}
        ${card('FPO', ws.fpos.length ? ws.fpos.map((f) => fpoSummary(f, phone)).join('') : empty('Not an FPO member', 'Joining an FPO lets small lots ship together.'), { flush: true })}
        ${card('Calls from this number', ws.calls.length ? `<ul class="rows">${ws.calls.map((c) => `
          <li class="row clickable" data-href="/calls?sid=${encodeURIComponent(c.call_sid)}">
            <div class="grow"><div class="row-title">${esc(INTENT_LABEL[c.last_intent] || 'Call')}</div><div class="row-sub">${esc(c.last_question || '')}</div></div>
            <div class="row-figure"><div>${c.turn_count} turns</div><div class="row-sub">${ago(c.started_at)}</div></div>
          </li>`).join('')}</ul>` : empty('No calls yet'), { flush: true })}
      </div>
    </div>`;

  $$('[data-offer-accept]', body).forEach((b) => b.addEventListener('click', () =>
    run(b, () => api(`/api/trade/offers/${b.dataset.offerAccept}/accept`, { method: 'POST', query: { actor: phone } }),
      'Offer accepted. The buyer now pays into escrow.').then((r) => r && loadFarmer(phone))));
  $$('[data-offer-reject]', body).forEach((b) => b.addEventListener('click', () =>
    run(b, () => api(`/api/trade/offers/${b.dataset.offerReject}/reject`, { method: 'POST', query: { actor: phone, reason: 'Declined by farmer' } }),
      'Offer declined').then((r) => r && loadFarmer(phone))));
  $('#toggle-list', body)?.addEventListener('click', () => { const f = $('#list-form'); f.hidden = !f.hidden; });
  $('#lot-form', body)?.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    run(e.submitter, () => api('/api/trade/lots', { method: 'POST', body: {
      commodity: f.get('commodity'), quantity_kg: Number(f.get('quantity')) * 100, grade: f.get('grade'),
      farmer_phone: phone, pickup_district: f.get('district'), quality_notes: f.get('notes') || 'Grade declared by the farmer',
      publish: true,
    } }), 'Produce listed. Matching buyers can now make offers.').then((r) => r && loadFarmer(phone));
  });
  $('#dispute-form', body)?.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const lotId = f.get('lot_id');
    const payment = ws.payments.find((p) => p.lot_id === lotId);
    run(e.submitter, () => api('/api/trade/disputes', { method: 'POST', body: {
      lot_id: lotId, raised_by: phone, raised_by_role: 'farmer', category: f.get('category'),
      description: f.get('description'), payment_id: payment?.id,
    } }), 'Complaint filed. Any held payment is frozen until it is resolved.').then((r) => r && loadFarmer(phone));
  });
  bindSeedForm(body);
  $$('[data-href]', body).forEach((el) => el.addEventListener('click', () => navigate(el.dataset.href)));
}

function bindSeedForm(scope) {
  $$('[data-phone]', scope).forEach((b) => b.addEventListener('click', () => {
    store.set('farmerPhone', b.dataset.phone); $('#farmer-open input').value = phoneLabel(b.dataset.phone); loadFarmer(b.dataset.phone);
  }));
  $('#seed-form', scope)?.addEventListener('submit', (e) => {
    e.preventDefault();
    const p = normalisePhone(new FormData(e.target).get('phone'));
    run(e.submitter, () => api('/api/demo/trade-seed', { method: 'POST', body: { phone: p } }),
      'Demo marketplace ready').then(async (r) => {
      if (!r) return;
      store.set('farmerPhone', p);
      await loadShared();
      navigate('/farmer');
    });
  });
}

function offerRow(o) {
  const b = o.buyer || {};
  return `<li class="row">
    <div class="grow">
      <div class="row-title">${esc(b.name || 'Buyer')} ${trustTag(b.badge)} ${demoTag('OFFER', o.id)}</div>
      <div class="row-sub">${esc(o.lot.commodity)} · ${qtl(o.quantity_kg)} · pays in ${o.payment_terms_days} days · ${untilLabel(o.expires_at)}</div>
    </div>
    <div class="row-figure"><div class="big">${inr(o.price_per_quintal)}<span class="muted" style="font-size:12px;font-weight:400">/qtl</span></div><div class="row-sub">${inr(o.gross_value)} total</div></div>
    <div class="btn-row"><button class="btn small secondary" data-offer-reject="${esc(o.id)}">Decline</button><button class="btn small" data-offer-accept="${esc(o.id)}">Accept</button></div>
  </li>`;
}

function lotsTable(lots) {
  if (!lots.length) return empty('Nothing listed', 'List produce here, or say “ಇಪ್ಪತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಮಾರಬೇಕು” on a call.');
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Produce</th><th class="right">Quantity</th><th>Grade</th><th>Status</th><th class="right">Buyers matched</th><th class="right">Best offer</th></tr></thead>
    <tbody>${lots.map((l) => {
      const best = l.offers.filter((o) => ['OPEN', 'ACCEPTED'].includes(o.status)).sort((a, b) => b.price_per_quintal - a.price_per_quintal)[0];
      return `<tr>
        <td><strong>${esc(l.commodity)}</strong> ${demoTag('LOT', l.id)}<span class="sub">${esc(l.pickup_district || '')} · listed ${dateLabel(l.created_at)}</span></td>
        <td class="right">${qtl(l.quantity_kg)}</td>
        <td>${esc(l.grade || '—')}<span class="sub">${esc((l.grade_basis || '').replace(/_/g, ' ').toLowerCase())}</span></td>
        <td>${statusTag(l.status)}</td>
        <td class="right">${l.match_count ?? '—'}</td>
        <td class="right">${best ? `${inr(best.price_per_quintal)}${best.status === 'ACCEPTED' ? '<span class="sub">accepted</span>' : ''}` : '—'}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
}

function listForm(phone) {
  return card('List produce for sale', `
    <form id="lot-form" class="stack">
      <div class="form-row">
        <div class="field"><label>Crop</label><select name="commodity">${commodityOptions('Onion')}</select></div>
        <div class="field"><label>Quantity (quintal)</label><input name="quantity" type="number" min="0.5" step="0.5" required value="10"></div>
        <div class="field"><label>Quality</label><select name="grade"><option value="A">Grade A, good</option><option value="B" selected>Grade B, medium</option><option value="C">Grade C, ordinary</option></select></div>
        <div class="field"><label>Pickup district</label><input name="district" list="lot-markets" value="Kalaburagi">${marketDatalist('lot-markets')}</div>
      </div>
      <div class="field"><label>Notes for buyers</label><input name="notes" placeholder="Variety, moisture, bags, when it can be collected"></div>
      <div class="btn-row" style="justify-content:space-between"><span class="muted" style="font-size:12px">Grade is recorded as self-declared until an inspection upgrades it.</span><button class="btn" type="submit">List for ${esc(phoneLabel(phone))}</button></div>
    </form>`);
}

const PAYMENT_STEPS = [['PENDING', 'Agreed'], ['INITIATED', 'Paying'], ['ESCROW', 'In escrow'], ['DELIVERY_CONFIRMED', 'Delivered'], ['RELEASED', 'Paid out']];

function paymentSteps(status) {
  const order = PAYMENT_STEPS.map(([s]) => s);
  const at = status === 'RELEASE_PENDING' ? 3 : order.indexOf(status);
  const halted = ['DISPUTED', 'FAILED', 'REFUNDED'].includes(status);
  return `<div class="steps">${PAYMENT_STEPS.map(([s, label], i) => {
    const cls = halted && i === Math.max(at, 0) ? 'halt' : i < at || status === 'RELEASED' ? 'done' : i === at ? 'current' : '';
    return `${i ? `<span class="step-line ${i <= at ? 'done' : ''}"></span>` : ''}<span class="step ${cls}"><span class="pip"></span>${label}</span>`;
  }).join('')}</div>`;
}

function paymentRow(p) {
  return `<li class="row" style="display:block">
    <div style="display:flex;gap:14px;align-items:center">
      <div class="grow" style="flex:1"><div class="row-title">${esc(p.commodity || 'Sale')} · ${esc(p.buyer_name || 'Buyer')} ${demoTag('PAYMENT', p.id)}</div>
        <div class="row-sub">${p.is_overdue ? `<span class="tag bad">${p.days_overdue} days overdue</span> ` : ''}${p.due_date ? `Due ${dateLabel(p.due_date)}` : ''}${p.utr_ref ? ` · UTR ${esc(p.utr_ref)}` : ''}</div></div>
      <div class="row-figure"><div class="big">${inr(p.amount_rupees)}</div><div>${statusTag(p.status)}</div></div>
    </div>
    ${paymentSteps(p.status)}
  </li>`;
}

function disputeRow(d) {
  return `<li class="row"><div class="grow"><div class="row-title">${esc((d.category || '').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()))} complaint</div>
    <div class="row-sub">${esc(d.description || '')}</div></div>
    <div class="row-figure">${statusTag(d.status)}<div class="row-sub">${d.age_days} days</div></div></li>`;
}

function complaintForm(phone, lots, payments) {
  return `<form id="dispute-form" class="stack" style="padding:14px 16px;border-top:1px solid var(--border)">
    <div class="form-row">
      <div class="field"><label>Deal</label><select name="lot_id">${lots.map((l) => {
        const p = payments.find((x) => x.lot_id === l.id);
        return `<option value="${esc(l.id)}">${esc(l.commodity)} · ${qtl(l.quantity_kg)}${p ? ` · ${inr(p.amount_rupees)}` : ''}</option>`;
      }).join('')}</select></div>
      <div class="field"><label>Problem</label><select name="category">
        <option value="PAYMENT">Payment not received</option><option value="WEIGHT">Weighing fraud</option>
        <option value="QUALITY">Rejected on quality</option><option value="QUANTITY">Quantity mismatch</option>
        <option value="DAMAGE">Produce damaged</option><option value="LOGISTICS">Transport problem</option><option value="OTHER">Other</option>
      </select></div>
    </div>
    <div class="field"><label>What happened</label><textarea name="description" required placeholder="Describe the problem in a sentence or two"></textarea></div>
    <div class="btn-row" style="justify-content:flex-end"><button class="btn danger" type="submit">File complaint</button></div>
  </form>`;
}

function fpoSummary(f, phone) {
  const mine = f.groups.filter((g) => g.includes_farmer);
  return `<div style="padding:14px 16px;border-bottom:1px solid var(--border)">
    <div class="row-title">${esc(f.name)} ${statusTag(f.verification_status)} ${demoTag('FPO', f.id)}</div>
    <div class="row-sub">${esc(f.district || '')} · ${num(f.member_count)} members</div>
    ${mine.length ? mine.map((g) => `<div class="notice good" style="margin:10px 0 0"><div class="grow">Your ${esc(g.commodity)} can pool with <strong>${g.farmer_count - 1} other farmers</strong>: ${qtl(g.total_kg)} of grade ${esc(g.grade)} ${g.viable ? 'is enough for one consignment.' : 'is still below a full load.'}</div></div>`).join('')
      : '<div class="row-sub" style="margin-top:8px">None of your listed produce is in a pool right now.</div>'}
    <a href="/fpo?id=${encodeURIComponent(f.id)}" data-link style="display:inline-block;margin-top:10px;font-size:12.5px">Open FPO →</a>
  </div>`;
}

/* ================================================================= BUYER */

async function viewBuyer(root) {
  root.innerHTML = `
    <div class="page-head">
      <div><h1>Buyer workspace</h1><p>Verification, demand and offers for a trader, processor or exporter. Only verified buyers can post demand or bid, and farmers only ever see verified buyers.</p></div>
      <div class="btn-row"><select id="buyer-select" style="width:260px" aria-label="Buyer"></select><button class="btn secondary" id="buyer-new">Register buyer</button></div>
    </div>
    ${demoNotice()}
    <div id="buyer-register" hidden></div>
    <div id="buyer-body">${loading()}</div>`;

  $('#buyer-new').addEventListener('click', () => {
    const host = $('#buyer-register');
    host.hidden = !host.hidden;
    if (!host.hidden) host.innerHTML = buyerRegisterForm();
    $('#buyer-reg-form')?.addEventListener('submit', (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      run(e.submitter, () => api('/api/trade/buyers', { method: 'POST', body: Object.fromEntries(f.entries()) }),
        'Buyer registered as unverified. Add documents to verify.').then((b) => {
        if (!b) return;
        store.set('buyerId', b.id); host.hidden = true; viewBuyer(root);
      });
    });
  });

  let buyers = [];
  try { buyers = (await api('/api/trade/buyers', { query: { limit: 200 } })).buyers; } catch (err) { toast(err.message, 'bad'); }
  const select = $('#buyer-select');
  if (!buyers.length) {
    select.innerHTML = '<option>No buyers yet</option>';
    $('#buyer-body').innerHTML = empty('No buyers registered', 'Register one to post demand and make offers.');
    return;
  }
  const selected = buyers.find((b) => b.id === store.get('buyerId')) || buyers.find((b) => b.verification_status === 'VERIFIED') || buyers[0];
  select.innerHTML = buyers.map((b) => `<option value="${esc(b.id)}" ${b.id === selected.id ? 'selected' : ''}>${esc(b.name)} · ${esc((b.verification_status || '').toLowerCase())}</option>`).join('');
  select.addEventListener('change', () => { store.set('buyerId', select.value); loadBuyer(select.value); });
  loadBuyer(selected.id);
}

function buyerRegisterForm() {
  return card('Register a buyer', `
    <form id="buyer-reg-form" class="stack">
      <div class="form-row">
        <div class="field"><label>Business name</label><input name="name" required></div>
        <div class="field"><label>Type</label><select name="buyer_type">
          <option value="APMC_TRADER">APMC trader</option><option value="PROCESSOR">Processor</option><option value="EXPORTER">Exporter</option>
          <option value="WHOLESALER">Wholesaler</option><option value="RETAILER">Retailer</option><option value="INSTITUTIONAL">Institutional</option><option value="OTHER">Other</option>
        </select></div>
        <div class="field"><label>Phone</label><input name="phone" placeholder="+91"></div>
        <div class="field"><label>District</label><input name="district" list="reg-markets">${marketDatalist('reg-markets')}</div>
        <div class="field"><label>State</label><select name="state"><option>Karnataka</option><option>Maharashtra</option></select></div>
      </div>
      <div class="btn-row" style="justify-content:flex-end"><button class="btn" type="submit">Register</button></div>
    </form>`);
}

async function loadBuyer(id) {
  const body = $('#buyer-body');
  body.innerHTML = loading();
  let buyer; let offers; let payments; let lots;
  try {
    [buyer, offers, payments, lots] = await Promise.all([
      api(`/api/trade/buyers/${id}`), api('/api/trade/offers', { query: { buyer_id: id } }),
      api('/api/trade/payments', { query: { buyer_id: id } }), api('/api/trade/lots', { query: { limit: 200 } }),
    ]);
  } catch (err) { body.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; return; }

  const verified = buyer.verification_status === 'VERIFIED';
  const readiness = buyer.verification_readiness || {};
  const badge = buyer.badge || {};
  const open = lots.lots.filter((l) => ['PUBLISHED', 'MATCHED', 'OFFER_RECEIVED', 'NEGOTIATION'].includes(l.status));
  const trust = buyer.trust || {};

  body.innerHTML = `
    <div class="split">
      <div class="stack">
        ${card('Produce open for offers', open.length ? `<ul class="rows">${open.map((l) => `
          <li class="row" style="display:block">
            <div style="display:flex;gap:14px;align-items:center">
              <div class="grow" style="flex:1"><div class="row-title">${esc(l.commodity)} · grade ${esc(l.grade || '—')} ${demoTag('LOT', l.id)}</div>
                <div class="row-sub">${qtl(l.quantity_kg)} · ${esc(l.pickup_district || '—')} · farmer ${esc(phoneLabel(l.farmer_phone, true))} · grade ${esc((l.grade_basis || '').replace(/_/g, ' ').toLowerCase())}</div></div>
              ${statusTag(l.status)}
              <button class="btn small ${verified ? '' : 'secondary'}" data-bid="${esc(l.id)}" ${verified ? '' : 'disabled title="Only verified buyers can bid"'}>Make offer</button>
            </div>
            <form class="form-row bid-form" data-lot="${esc(l.id)}" hidden style="margin-top:12px">
              <div class="field"><label>Price per quintal</label><input name="price" type="number" min="1" required></div>
              <div class="field"><label>Quantity (quintal)</label><input name="quantity" type="number" min="0.5" step="0.5" max="${l.quantity_kg / 100}" value="${l.quantity_kg / 100}"></div>
              <div class="field"><label>Pay within (days)</label><input name="terms" type="number" min="0" max="60" value="7"></div>
              <div class="field"><button class="btn" type="submit">Send offer</button></div>
            </form>
          </li>`).join('')}</ul>` : empty('No produce open for offers'), { flush: true, hint: `${open.length} lots` })}
        ${card('My offers', offers.offers.length ? `<div class="table-wrap"><table class="data">
          <thead><tr><th>Lot</th><th class="right">Price / qtl</th><th class="right">Quantity</th><th class="right">Value</th><th>Status</th></tr></thead>
          <tbody>${offers.offers.map((o) => `<tr><td class="mono">${shortId(o.lot_id)}</td><td class="right">${inr(o.price_per_quintal)}</td><td class="right">${qtl(o.quantity_kg)}</td><td class="right">${inr(o.gross_value)}</td><td>${statusTag(o.status)}</td></tr>`).join('')}</tbody></table></div>` : empty('No offers sent'), { flush: true })}
        ${card('Payments to make', payments.payments.length ? `<ul class="rows">${payments.payments.map((p) => buyerPaymentRow(p)).join('')}</ul>` : empty('Nothing to pay'), { flush: true, hint: 'Simulated payment rail' })}
      </div>
      <div class="stack">
        ${card('Verification', `
          <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
            <div><div class="row-title" style="font-size:15px">${esc(buyer.name)} ${demoTag('BUYER', buyer.id)}</div><div class="row-sub">${esc((buyer.buyer_type || '').replace(/_/g, ' ').toLowerCase())} · ${esc(buyer.district || '—')}, ${esc(buyer.state || '')}</div></div>
            ${statusTag(buyer.verification_status)}
          </div>
          <div style="margin-top:14px">${trustTag(badge)}</div>
          <div style="display:flex;align-items:center;gap:10px;margin-top:12px"><div class="bar" style="flex:1"><span style="width:${Math.max(0, Math.min(100, trust.score || badge.trust_score || 0))}%"></span></div><span class="num">${num(trust.score ?? badge.trust_score)}/100</span></div>
          ${badge.caution ? `<div class="notice warn" style="margin:12px 0 0">${esc(badge.caution)}</div>` : ''}
          <dl class="kv" style="margin-top:14px">
            <dt>Completed deals</dt><dd>${num(trust.completed_transactions)} of ${num(trust.total_transactions)}</dd>
            <dt>Payment defaults</dt><dd>${num(trust.payment_defaults)}</dd>
            <dt>Documents</dt><dd>${(buyer.documents || []).map((d) => esc(d.doc_type)).join(', ') || '—'}</dd>
          </dl>
          ${!verified ? `<div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
            ${(readiness.missing_documents || []).length ? `<div class="muted" style="font-size:12px;margin-bottom:8px">Missing: ${readiness.missing_documents.map(esc).join(', ')}</div>` : ''}
            <form class="form-row" id="doc-form">
              <div class="field"><label>Document</label><select name="doc_type">${(readiness.missing_documents || ['PAN', 'PHONE']).map((d) => `<option>${esc(d)}</option>`).join('')}</select></div>
              <div class="field"><label>Number</label><input name="doc_number" required></div>
              <div class="field"><button class="btn secondary" type="submit">Add</button></div>
            </form>
            <button class="btn" id="verify-btn" style="margin-top:12px" ${readiness.can_verify ? '' : 'disabled'}>Verify buyer</button>
          </div>` : ''}`)}
        ${verified ? card('Post demand', `
          <form id="demand-form" class="stack">
            <div class="form-row">
              <div class="field"><label>Crop</label><select name="commodity">${commodityOptions('Onion')}</select></div>
              <div class="field"><label>Need (quintal)</label><input name="qty" type="number" min="1" required value="50"></div>
            </div>
            <div class="form-row">
              <div class="field"><label>Minimum grade</label><select name="min_grade"><option value="">Any</option><option>A</option><option>B</option><option>C</option></select></div>
              <div class="field"><label>Price per quintal</label><input name="price" type="number" min="1"></div>
            </div>
            <div class="form-row">
              <div class="field"><label>Delivery district</label><input name="district" list="dem-markets" value="${esc(buyer.district || '')}">${marketDatalist('dem-markets')}</div>
              <div class="field"><label>Pay within (days)</label><input name="terms" type="number" min="0" max="60" value="7"></div>
            </div>
            <div class="btn-row" style="justify-content:flex-end"><button class="btn" type="submit">Post demand</button></div>
          </form>`) : ''}
      </div>
    </div>`;

  $$('[data-bid]', body).forEach((b) => b.addEventListener('click', () => {
    const form = $(`.bid-form[data-lot="${b.dataset.bid}"]`, body); form.hidden = !form.hidden;
  }));
  $$('.bid-form', body).forEach((form) => form.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = new FormData(form);
    run(e.submitter, () => api('/api/trade/offers', { method: 'POST', body: {
      lot_id: form.dataset.lot, buyer_id: id, price_per_quintal: Number(f.get('price')),
      quantity_kg: Number(f.get('quantity')) * 100, payment_terms_days: Number(f.get('terms')),
    } }), 'Offer sent to the farmer').then((r) => r && loadBuyer(id));
  }));
  $('#doc-form', body)?.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    run(e.submitter, () => api(`/api/trade/buyers/${id}/documents`, { method: 'POST', body: Object.fromEntries(f.entries()) }), 'Document added').then((r) => r && loadBuyer(id));
  });
  $('#verify-btn', body)?.addEventListener('click', (e) =>
    run(e.currentTarget, () => api(`/api/trade/buyers/${id}/verify`, { method: 'POST', query: { verified_by: 'operator' } }), 'Buyer verified').then((r) => r && loadBuyer(id)));
  $('#demand-form', body)?.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    run(e.submitter, () => api('/api/trade/demand', { method: 'POST', body: {
      buyer_id: id, commodity: f.get('commodity'), quantity_needed_kg: Number(f.get('qty')) * 100,
      min_grade: f.get('min_grade') || null, price_offered: f.get('price') ? Number(f.get('price')) : null,
      delivery_district: f.get('district'), payment_terms_days: Number(f.get('terms')),
    } }), 'Demand posted. Matching farmers will hear about it on their calls.').then((r) => r && loadBuyer(id));
  });
  $$('[data-pay]', body).forEach((b) => b.addEventListener('click', () =>
    run(b, () => api(`/api/trade/payments/${b.dataset.pay}/${b.dataset.action}`, { method: 'POST', query: { actor: id, utr_ref: b.dataset.action === 'escrow' ? `UTR${Date.now()}` : undefined } }),
      'Payment updated').then((r) => r && loadBuyer(id))));
}

const BUYER_PAYMENT_ACTION = {
  PENDING: ['initiate', 'Start payment'], INITIATED: ['escrow', 'Mark funds in escrow'],
  ESCROW: ['confirm-delivery', 'Confirm delivery'], DELIVERY_CONFIRMED: ['release', 'Release to farmer'],
  RELEASE_PENDING: ['release', 'Release to farmer'],
};

function buyerPaymentRow(p) {
  const action = BUYER_PAYMENT_ACTION[p.status];
  return `<li class="row" style="display:block">
    <div style="display:flex;gap:14px;align-items:center">
      <div class="grow" style="flex:1"><div class="row-title">${inr(p.amount_rupees)} to farmer ${esc(phoneLabel(p.payee_phone, true))}</div>
        <div class="row-sub">${p.is_overdue ? `<span class="tag bad">${p.days_overdue} days overdue</span> ` : ''}Due ${dateLabel(p.due_date)}</div></div>
      ${statusTag(p.status)}
      ${action ? `<button class="btn small" data-pay="${esc(p.id)}" data-action="${action[0]}">${action[1]}</button>` : ''}
    </div>
    ${paymentSteps(p.status)}
  </li>`;
}

/* =================================================================== FPO */

async function viewFpo(root) {
  root.innerHTML = `
    <div class="page-head">
      <div><h1>FPO aggregation</h1><p>Pool small, compatible member lots into one buyer-ready consignment. Pools never mix crop, grade or variety, and the benefit is computed with the same transport model farmers hear on calls.</p></div>
      <select id="fpo-select" style="width:280px" aria-label="FPO"></select>
    </div>
    ${demoNotice()}
    <div id="fpo-body">${loading()}</div>`;

  let fpos = [];
  try { fpos = (await api('/api/trade/fpos')).fpos; } catch (err) { toast(err.message, 'bad'); }
  const select = $('#fpo-select');
  if (!fpos.length) {
    select.innerHTML = '<option>No FPOs</option>';
    $('#fpo-body').innerHTML = empty('No FPOs registered', 'Seed the demo marketplace from the Farmer page, or create one through the API.');
    return;
  }
  const wanted = new URLSearchParams(location.search).get('id') || store.get('fpoId');
  const selected = fpos.find((f) => f.id === wanted) || fpos[0];
  select.innerHTML = fpos.map((f) => `<option value="${esc(f.id)}" ${f.id === selected.id ? 'selected' : ''}>${esc(f.name)}</option>`).join('');
  select.addEventListener('change', () => { store.set('fpoId', select.value); loadFpo(select.value); });
  loadFpo(selected.id);
}

async function loadFpo(id) {
  const body = $('#fpo-body');
  body.innerHTML = loading();
  let org; let groupsRes; let lotsRes;
  try {
    const summary = await api(`/api/trade/fpos/${id}`);
    store.set('fpoLocation', summary.district || 'Kalaburagi');
    [org, groupsRes, lotsRes] = await Promise.all([
      api(`/api/trade/fpos/${id}`),
      api(`/api/trade/fpos/${id}/aggregation-groups`, { query: { location: store.get('fpoLocation') || 'Kalaburagi' } }),
      api('/api/trade/lots', { query: { fpo_id: id, limit: 200 } }),
    ]);
  } catch (err) { body.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; return; }

  const groups = groupsRes.groups;
  const pooled = lotsRes.lots.filter((l) => l.is_aggregate);

  body.innerHTML = `
    <div class="metrics">
      ${metric('Members', num((org.members || []).length))}
      ${metric('Status', statusTag(org.verification_status))}
      ${metric('Pools available', num(groups.filter((g) => g.lot_count > 1).length))}
      ${metric('Pooled consignments', num(pooled.length))}
    </div>
    <div class="split" style="margin-top:18px">
      <div class="stack">
        ${card('Pooling opportunities', groups.length ? `<ul class="rows">${groups.map((g) => poolRow(g)).join('')}</ul>` : empty('No compatible lots', 'Pools form when members list the same crop and grade.'), { flush: true, hint: 'Priced at the best market after transport, as farmers hear on calls' })}
        ${card('Pooled consignments', pooled.length ? `<ul class="rows">${pooled.map((l) => `
          <li class="row" style="display:block"><div style="display:flex;gap:14px;align-items:center">
            <div class="grow" style="flex:1"><div class="row-title">${esc(l.commodity)} · grade ${esc(l.grade || '—')}</div><div class="row-sub">${qtl(l.quantity_kg)} · created ${dateLabel(l.created_at)}</div></div>
            ${statusTag(l.status)}<button class="btn small secondary" data-settle="${esc(l.id)}">Settlement</button></div>
            <div data-settle-out="${esc(l.id)}"></div></li>`).join('')}</ul>` : empty('Nothing pooled yet'), { flush: true })}
      </div>
      ${card('Members', (org.members || []).length ? `<ul class="rows">${org.members.map((m) => `
        <li class="row"><div class="grow"><div class="row-title">${esc(phoneLabel(m.farmer_phone, true))}</div><div class="row-sub">${esc((m.role || 'member').toLowerCase())}${m.joined_at ? ` · joined ${dateLabel(m.joined_at)}` : ''}</div></div></li>`).join('')}</ul>` : empty('No members'), { flush: true })}
    </div>`;

  $$('[data-pool]', body).forEach((b) => b.addEventListener('click', () => {
    const lotIds = b.dataset.pool.split(',');
    run(b, () => api(`/api/trade/fpos/${id}/aggregate`, { method: 'POST', body: { lot_ids: lotIds, pickup_district: org.district } }),
      `Pooled ${lotIds.length} lots into one consignment`).then((r) => r && loadFpo(id));
  }));
  $$('[data-settle]', body).forEach((b) => b.addEventListener('click', async () => {
    const out = $(`[data-settle-out="${b.dataset.settle}"]`, body);
    out.innerHTML = loading();
    try {
      const s = await api(`/api/trade/lots/${b.dataset.settle}/settlement`);
      const shares = s.splits || [];
      out.innerHTML = shares.length ? `<div class="table-wrap" style="margin-top:10px"><table class="data">
        <thead><tr><th>Farmer</th><th class="right">Quantity</th><th class="right">Share</th></tr></thead>
        <tbody>${shares.map((x) => `<tr><td>${esc(phoneLabel(x.farmer_phone, true))}</td><td class="right">${qtl(x.quantity_kg)}</td><td class="right">${inr((x.amount_paise ?? 0) / 100)}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="muted" style="margin-top:8px;font-size:12.5px">${esc(s.payments && s.payments.length ? 'Paid as a single consignment; no member split recorded.' : 'No settlement yet: the consignment has not been sold.')}</div>`;
    } catch (err) { out.innerHTML = `<div class="notice bad" style="margin-top:8px">${esc(err.message)}</div>`; }
  }));
}

const gradeLabel = (g) => (!g || g === 'UNGRADED' ? 'ungraded' : `grade ${g}`);

function poolRow(g) {
  const b = g.benefit;
  const canPool = g.lot_count > 1;
  return `<li class="row" style="display:block">
    <div style="display:flex;gap:14px;align-items:flex-start">
      <div class="grow" style="flex:1">
        <div class="row-title">${esc(g.commodity)} · ${esc(gradeLabel(g.grade))}${g.variety ? ` · ${esc(g.variety)}` : ''} ${g.viable ? '<span class="tag good">Full load</span>' : '<span class="tag">Below minimum</span>'}</div>
        <div class="row-sub">${g.farmer_count} ${g.farmer_count === 1 ? 'farmer' : 'farmers'} · ${g.lot_count} ${g.lot_count === 1 ? 'lot' : 'lots'} · ${qtl(g.total_kg)}${b ? ` · sells at ${esc(b.priced_at_market)}, ${inr(b.price_per_quintal)}/qtl ${qualityTag(b.data_quality)}` : ''}</div>
      </div>
      ${canPool ? `<button class="btn small" data-pool="${g.lots.map((l) => esc(l.id)).join(',')}">Pool ${g.lot_count} lots</button>` : '<span class="muted" style="font-size:12px">Nothing to pool with</span>'}
    </div>
    ${b && canPool ? `<div class="grid-3" style="margin-top:12px;gap:10px">
      <div><div class="muted" style="font-size:11.5px">Shipped separately</div><div class="num">${inr(b.net_if_sold_separately)}</div></div>
      <div><div class="muted" style="font-size:11.5px">Pooled, one ${esc((b.vehicle_when_aggregated || 'vehicle').toLowerCase())}</div><div class="num">${inr(b.net_if_aggregated)}</div></div>
      <div><div class="muted" style="font-size:11.5px">Gain, ${num(b.distance_km)} km haul</div><div class="num" style="color:var(--accent);font-weight:600">${b.total_gain > 0 ? '+' : ''}${inr(b.total_gain)} <span class="muted" style="font-weight:400">· ${inr(b.gain_per_farmer)}/farmer</span></div></div>
    </div>` : ''}
  </li>`;
}

/* ================================================================= CALLS */

const EXAMPLES = [
  'ಕಲಬುರಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು?',
  'ನನ್ನ ಬಳಿ ಮೂವತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಇದೆ, ಯಾವ ಮಾರುಕಟ್ಟೆ ಉತ್ತಮ?',
  'ಈರುಳ್ಳಿ ಬೆಲೆ ಏರುತ್ತಿದೆಯಾ?',
  'ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ?',
  'ಈರುಳ್ಳಿ ಯಾರು ಖರೀದಿ ಮಾಡ್ತಾರೆ?',
  'ನನಗೆ ಆಫರ್ ಬಂದಿದೆಯಾ?',
  'ನನ್ನ ತೊಗರಿ ಹಣ ಬಂತಾ?',
  'ಹೌದು',
  'ಬೇಡ',
];

function sessionId() {
  let id = null;
  try { id = sessionStorage.getItem('krishi.web-session'); } catch { /* blocked */ }
  if (!id) {
    id = Math.random().toString(36).slice(2, 12);
    try { sessionStorage.setItem('krishi.web-session', id); } catch { /* blocked */ }
  }
  return id;
}

function turnHtml(t, { compact = false } = {}) {
  const timing = t.timings?.total_ms ? `${(t.timings.total_ms / 1000).toFixed(1)} s` : '';
  const audio = t.audio_file || t.audio_path;
  return `<div class="turn">
    <div class="bubble farmer">
      <div class="speaker">Farmer${t.slots && Object.keys(t.slots).length && !compact ? ` · <span class="muted">${esc(Object.entries(t.slots).filter(([k]) => k !== 'commodity_raw').map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(', '))}</span>` : ''}</div>
      <div class="kn">${esc(t.farmer_kn)}</div>
      ${t.farmer_en && t.farmer_en !== t.farmer_kn ? `<div class="gloss">${esc(t.farmer_en)}</div>` : ''}
    </div>
    <div class="bubble agent">
      <div class="speaker">Agent <span class="tag">${esc(INTENT_LABEL[t.intent] || t.intent)}</span>${qualityTag(t.data_quality)}${audio ? `<button class="chip" data-audio="${esc(t.audio_path || `/audio/${t.audio_file}`)}" style="padding:1px 8px">${icons.play} Play</button>` : ''}</div>
      <div class="kn">${esc(t.reply_kn)}</div>
      <div class="gloss">${esc(t.reply_en)}</div>
    </div>
    ${!compact && (timing || t.nlu_source) ? `<div class="turn-meta">${timing ? `<span>${timing}</span>` : ''}${t.timings?.nlu_ms ? `<span>understanding ${(t.timings.nlu_ms / 1000).toFixed(1)} s</span>` : ''}${t.nlu_source && t.nlu_source !== 'gemini' ? `<span class="tag warn">${esc(t.nlu_source)} fallback</span>` : ''}</div>` : ''}
  </div>`;
}

let player = null;
function playAudio(src) {
  try {
    if (player) player.pause();
    player = new Audio(src);
    player.play().catch(() => {});
  } catch { /* audio unsupported */ }
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-audio]');
  if (btn) { e.preventDefault(); playAudio(btn.dataset.audio); }
});

async function viewCalls(root) {
  const params = new URLSearchParams(location.search);
  const mySid = `WEB-${sessionId()}`;
  const state = { selected: params.get('sid') || mySid, calls: [], seen: new Set(), started: false };

  root.innerHTML = `
    <div class="page-head">
      <div><h1>Calls</h1><p>Every conversation, as the farmer said it and as the agent answered, with what was understood and where the numbers came from. Talk to the agent here with the exact pipeline the phone uses.</p></div>
      ${app.overview?.voice_number ? `<div class="dial" style="margin:0;padding:10px 14px"><span class="dial-icon" style="width:30px;height:30px">${icons.phone}</span><div><div class="dial-label">Kannada line</div><div class="dial-number" style="font-size:15px">${esc(app.overview.voice_number)}</div></div></div>` : ''}
    </div>
    <div class="calls-layout">
      <section class="card">
        <div class="card-head"><h2>Recent</h2><span class="hint" style="display:flex;align-items:center;gap:6px"><span class="live-dot pulse"></span>Live</span></div>
        <ul class="rows" id="call-list">${loading()}</ul>
      </section>
      <section class="card" id="call-pane"></section>
    </div>`;

  const listEl = $('#call-list');
  const pane = $('#call-pane');

  const renderList = () => {
    const rows = [...state.calls];
    if (!rows.find((c) => c.call_sid === mySid)) {
      rows.unshift({ call_sid: mySid, channel: 'web', turn_count: 0, started_at: Date.now() / 1000, you: true });
    }
    listEl.innerHTML = rows.map((c) => `
      <li class="row clickable ${c.call_sid === state.selected ? 'selected' : ''}" data-sid="${esc(c.call_sid)}">
        <div class="grow">
          <div class="row-title">${c.call_sid === mySid ? 'Your web session' : c.channel === 'web' ? 'Web session' : phoneLabel(c.phone, true)}</div>
          <div class="row-sub">${c.last_intent ? esc(INTENT_LABEL[c.last_intent] || c.last_intent) + ' · ' : ''}${c.turn_count} turns · ${ago(c.started_at)}</div>
        </div>
        <span class="tag ${c.channel === 'phone' ? 'info' : ''}">${c.channel === 'phone' ? 'Phone' : c.channel === 'api' ? 'API' : 'Web'}</span>
      </li>`).join('');
    $$('[data-sid]', listEl).forEach((el) => el.addEventListener('click', () => {
      state.selected = el.dataset.sid;
      history.replaceState(null, '', `/calls?sid=${encodeURIComponent(state.selected)}`);
      renderList(); openCall();
    }));
  };

  const refreshList = async () => {
    try { state.calls = (await api('/api/calls', { query: { limit: 40 } })).calls; } catch { /* keep last list */ }
    renderList();
  };

  const appendTurn = (t) => {
    const key = `${t.call_sid || state.selected}:${t.turn_no}`;
    if (state.seen.has(key)) return;
    state.seen.add(key);
    const box = $('#transcript', pane);
    if (!box) return;
    $('.empty', box)?.remove();
    box.insertAdjacentHTML('beforeend', turnHtml(t));
    box.scrollTop = box.scrollHeight;
  };

  const openCall = async () => {
    const isMine = state.selected === mySid;
    const phone = defaultPhone();
    pane.innerHTML = `
      <div class="card-head">
        <h2>${isMine ? 'Talk to the agent' : 'Transcript'}</h2>
        ${isMine ? `<div class="btn-row"><label class="muted" style="font-size:12px" for="sim-phone">Speaking as</label><input id="sim-phone" value="${esc(phoneLabel(phone))}" placeholder="Farmer mobile" style="width:160px"><button class="btn ghost small" id="sim-reset">New conversation</button></div>` : '<span class="hint mono"></span>'}
      </div>
      ${isMine ? `<div class="examples">${EXAMPLES.map((x) => `<button class="chip" data-example="${esc(x)}">${esc(x)}</button>`).join('')}</div>` : ''}
      <div class="transcript" id="transcript" style="max-height:62vh;overflow-y:auto;min-height:260px">${loading()}</div>
      ${isMine ? `<form class="composer" id="composer">
        <button type="button" class="icon-btn" id="mic" title="Speak in Kannada">${icons.mic}</button>
        <input id="say" autocomplete="off" placeholder="Type or speak in Kannada, or in English">
        <button class="btn" type="submit">${icons.send}<span>Send</span></button>
      </form>` : ''}`;

    state.seen = new Set();
    const box = $('#transcript', pane);
    try {
      const call = await api(`/api/calls/${encodeURIComponent(state.selected)}`);
      box.innerHTML = '';
      call.turns.forEach((t) => appendTurn({ ...t, call_sid: call.call_sid }));
      $('.card-head .hint', pane) && ($('.card-head .hint', pane).textContent = `${call.channel} · ${phoneLabel(call.phone, true)} · ${call.status}`);
      if (isMine && call.turns.length) state.started = true;
    } catch {
      box.innerHTML = empty(isMine ? 'Start the conversation' : 'Call not found', isMine ? 'Pick an example or ask your own question. Replies are spoken in Kannada.' : '');
    }
    if (isMine) bindComposer();
  };

  const send = async (text) => {
    const box = $('#transcript', pane);
    const phone = normalisePhone($('#sim-phone', pane)?.value);
    if (phone) store.set('farmerPhone', phone);
    $('.empty', box)?.remove();
    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.innerHTML = '<span class="spinner"></span> Agent is answering';
    box.appendChild(typing); box.scrollTop = box.scrollHeight;
    try {
      const r = await api('/api/voice/simulate', { method: 'POST', body: { text, session_id: sessionId(), phone, new_session: !state.started } });
      state.started = true;
      typing.remove();
      appendTurn({ ...r, call_sid: mySid });
      if (r.audio_path) playAudio(r.audio_path);
      refreshList();
    } catch (err) {
      typing.remove();
      if (err.status !== 401) box.insertAdjacentHTML('beforeend', `<div class="notice bad">${esc(err.message)}</div>`);
    }
  };

  const bindComposer = () => {
    const input = $('#say', pane);
    $('#composer', pane).addEventListener('submit', (e) => {
      e.preventDefault();
      const text = input.value.trim(); if (!text) return;
      input.value = ''; send(text);
    });
    $$('[data-example]', pane).forEach((b) => b.addEventListener('click', () => send(b.dataset.example)));
    $('#sim-reset', pane).addEventListener('click', async (e) => {
      await run(e.currentTarget, () => api('/api/voice/simulate/reset', { method: 'POST', body: { session_id: sessionId() } }));
      try { sessionStorage.removeItem('krishi.web-session'); } catch { /* blocked */ }
      navigate('/calls', { replace: true });
    });
    const mic = $('#mic', pane);
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) { mic.disabled = true; mic.title = 'Speech input is not supported in this browser'; return; }
    let rec = null;
    mic.addEventListener('click', () => {
      if (rec) { rec.stop(); return; }
      rec = new Recognition();
      rec.lang = 'kn-IN'; rec.interimResults = true; rec.maxAlternatives = 1;
      mic.classList.add('recording');
      rec.onresult = (ev) => {
        const res = ev.results[ev.results.length - 1];
        input.value = res[0].transcript;
        if (res.isFinal) { const text = input.value.trim(); input.value = ''; if (text) send(text); }
      };
      rec.onerror = () => toast('Could not hear that. Check microphone permission.', 'bad');
      rec.onend = () => { mic.classList.remove('recording'); rec = null; };
      rec.start();
    });
  };

  await refreshList();
  openCall();

  // Live updates: new turns on the open call appear as they happen.
  const stream = new EventSource('/api/calls/stream');
  let listTimer = null;
  stream.onmessage = (ev) => {
    let event; try { event = JSON.parse(ev.data); } catch { return; }
    if (event.type === 'turn' && event.call_sid === state.selected && event.call_sid !== mySid) appendTurn(event);
    clearTimeout(listTimer); listTimer = setTimeout(refreshList, 400);
  };
  app.cleanup.push(() => { stream.close(); clearTimeout(listTimer); });
}

/* =============================================================== ADVISOR */

const SEVERITY = { NONE: ['good', 'Healthy'], LOW: ['info', 'Low severity'], MEDIUM: ['warn', 'Medium severity'], HIGH: ['bad', 'High severity'] };
const CATEGORY_LABEL = { DISEASE: 'Disease', PEST: 'Pest', NUTRIENT: 'Nutrient deficiency', ABIOTIC: 'Weather or water stress', HEALTHY: 'Healthy', UNCLEAR: 'Unclear photo' };
const RISK_BAND = { LOW: ['good', 'Low risk'], MODERATE: ['info', 'Moderate risk'], HIGH: ['warn', 'High risk'], SEVERE: ['bad', 'Severe risk'] };
const ACTION_KIND = { TREAT: 'Treat', SPRAY_TIMING: 'Spraying', MARKET: 'Market', STORAGE: 'Storage', SOIL_TEST: 'Soil', INSURANCE: 'Insurance', MONITOR: 'Monitor', PHOTO: 'Photo' };
const MAX_PHOTO_BYTES = 8 * 1024 * 1024;

async function viewAdvisor(root) {
  root.innerHTML = `
    <div class="page-head"><div><h1>Crop advisor</h1><p>Check a leaf or crop photo for disease and pests, then get one recommendation that weighs crop health, weather, soil and the market.</p></div></div>
    ${demoNotice()}
    ${card('Your crop', `
      <form id="adv-form" class="stack">
        <div class="form-row">
          <div class="field"><label>Crop</label><select name="commodity">${commodityOptions('Onion')}</select></div>
          <div class="field"><label>Your market</label><input name="location" list="adv-markets" value="Kalaburagi">${marketDatalist('adv-markets')}</div>
          <div class="field"><label>Quantity (quintal)</label><input name="quantity" type="number" min="1" step="0.5" value="30"></div>
          <div class="field"><label>Can store for (days)</label><input name="storage_days" type="number" min="0" max="365" value="15"></div>
          <label class="check"><input type="checkbox" name="urgent"> Needs cash now</label>
          <label class="check"><input type="checkbox" name="cold"> Has cold storage</label>
        </div>
        <label class="adv-drop" for="adv-photo">
          <img id="adv-preview" alt="" hidden>
          <span><strong id="adv-photo-name">Add a leaf or crop photo</strong><span class="muted">Optional. A close-up of one affected leaf in daylight works best. JPG, PNG or WebP, up to 8 MB.</span></span>
        </label>
        <input id="adv-photo" type="file" accept="image/jpeg,image/png,image/webp" hidden>
        <div class="btn-row"><button class="btn" type="submit">Get advice</button><button class="btn ghost" type="button" id="adv-clear" hidden>Remove photo</button></div>
      </form>`)}
    <div id="adv-out" class="stack" style="margin-top:18px"></div>`;

  const form = $('#adv-form');
  const input = $('#adv-photo');
  const preview = $('#adv-preview');
  const clear = $('#adv-clear');
  const urls = [];
  const objectUrl = (file) => { const u = URL.createObjectURL(file); urls.push(u); return u; };
  app.cleanup.push(() => urls.forEach((u) => URL.revokeObjectURL(u)));

  const showPhoto = () => {
    const file = input.files[0];
    if (file && file.size > MAX_PHOTO_BYTES) { toast('That photo is larger than 8 MB.', 'bad'); input.value = ''; }
    const chosen = input.files[0];
    preview.hidden = !chosen; clear.hidden = !chosen;
    if (chosen) preview.src = objectUrl(chosen);
    $('#adv-photo-name').textContent = chosen ? chosen.name : 'Add a leaf or crop photo';
  };
  input.addEventListener('change', showPhoto);
  clear.addEventListener('click', () => { input.value = ''; showPhoto(); });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const file = input.files[0];
    const button = $('button[type="submit"]', form);
    const out = $('#adv-out');
    const query = {
      commodity: f.get('commodity'), location: f.get('location'), quantity: f.get('quantity') || 10,
      storage_days: f.get('storage_days') || 0, needs_cash: f.get('urgent') ? 'true' : 'false', cold_storage: f.get('cold') ? 'true' : 'false',
    };
    button.disabled = true;
    let diagnosis = null; let photoError = ''; let photoUrl = '';
    try {
      if (file) {
        out.innerHTML = card('Checking the photo', loading(), { hint: 'Usually 5 to 15 seconds' });
        photoUrl = objectUrl(file);
        try {
          diagnosis = await uploadDiagnosis(file, query.commodity, query.location);
          query.diagnosis_id = diagnosis.id;
        } catch (err) { photoError = err.message; }
      }
      out.innerHTML = card('Weighing crop health, weather and market', loading());
      const result = await api('/api/advisor/decision', { query });
      out.innerHTML = (photoError ? `<div class="notice bad"><div class="grow"><strong>Photo check failed.</strong> ${esc(photoError)} The advice below does not include crop health.</div></div>` : '')
        + (diagnosis ? diagnosisCard(diagnosis, photoUrl) : '')
        + decisionCards(result);
      out.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      out.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`;
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  });
}

async function uploadDiagnosis(file, crop, location) {
  const body = new FormData();
  body.append('file', file); body.append('crop', crop || ''); body.append('location', location || '');
  const res = await fetch('/api/advisor/diagnose', { method: 'POST', body });
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) throw new ApiError((data && typeof data.detail === 'string' && data.detail) || `Photo check failed (${res.status})`, res.status);
  return data;
}

const bulletList = (items) => (items && items.length)
  ? `<ul class="bullets">${items.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : '<p class="muted" style="margin:8px 0 0;font-size:13px">Nothing listed.</p>';
const stepList = (items) => `<ol class="adv-steps">${items.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`;

function knowledgeRefs(refs) {
  if (!refs || !refs.length) return '';
  return `<details class="adv-refs"><summary>From the knowledge base (${refs.length})</summary>
    ${refs.map((r) => `<div class="adv-ref"><strong>${esc(r.title)}</strong><p>${esc(r.text)}</p></div>`).join('')}</details>`;
}

function diagnosisCard(d, photoUrl) {
  if (!d.is_plant) {
    return card('Photo check', `<div class="notice warn" style="margin:0"><div class="grow"><strong>This doesn't look like a crop photo.</strong> ${esc(d.summary)}</div></div>`);
  }
  const [sevCls, sevLabel] = SEVERITY[d.severity] || ['', d.severity];
  const confidence = Math.round((d.confidence || 0) * 100);
  const hint = (d.crop_hint || '').toLowerCase(); const seen = (d.crop || '').toLowerCase();
  const mismatch = hint && seen && !seen.includes(hint) && !hint.includes(seen);
  return card('Photo check', `
    <div class="dx">
      ${photoUrl ? `<img src="${esc(photoUrl)}" alt="Uploaded crop photo">` : ''}
      <div class="grow">
        <div class="muted" style="font-size:12px">${esc(d.crop || 'Crop')} · ${esc(CATEGORY_LABEL[d.category] || d.category)}</div>
        <div class="dx-name">${esc(d.condition)}</div>
        <div style="display:flex;align-items:center;gap:10px;margin-top:6px">
          <div class="bar" style="width:120px"><span style="width:${confidence}%"></span></div>
          <span class="muted" style="font-size:12px">${confidence}% confidence${d.affected_area_pct != null ? ` · about ${Math.round(d.affected_area_pct)}% of visible leaf affected` : ''}</span>
        </div>
        ${d.summary ? `<p style="margin:10px 0 0;font-size:13.5px">${esc(d.summary)}</p>` : ''}
        ${mismatch ? `<div class="notice warn" style="margin:10px 0 0"><div class="grow">You chose ${esc(d.crop_hint)}, but the photo looks like ${esc(d.crop)}.</div></div>` : ''}
      </div>
    </div>
    ${d.category === 'UNCLEAR' ? '' : `<div class="grid-2" style="margin-top:18px">
      <div><h3 class="adv-h">What the photo shows</h3>${bulletList(d.symptoms)}</div>
      <div><h3 class="adv-h">What to do</h3>${d.treatment && d.treatment.length ? stepList(d.treatment) : '<p class="muted" style="margin:8px 0 0;font-size:13px">No treatment needed.</p>'}</div>
    </div>
    ${d.prevention && d.prevention.length ? `<h3 class="adv-h" style="margin-top:16px">Prevent it next time</h3>${bulletList(d.prevention)}` : ''}`}
    ${knowledgeRefs(d.references)}`,
  { actions: `<span class="tag ${sevCls}">${esc(sevLabel)}</span>`, hint: 'AI reading of one photo. Confirm with your KVK before costly treatment.' });
}

function decisionCards(r) {
  const [label, cls] = DECISION[r.decision] || [r.decision, 'none'];
  const [bandCls, bandLabel] = RISK_BAND[r.risk.band] || ['', r.risk.band];
  const factorRows = r.risk.factors.map((f) => {
    const share = f.max_points ? f.points / f.max_points : 0;
    return `<tr>
      <td><strong>${esc(f.name)}</strong><span class="sub">${esc(f.detail)}</span></td>
      <td style="width:130px"><div class="bar ${share >= 0.6 ? 'bad' : share >= 0.3 ? 'warn' : ''}"><span style="width:${Math.round(share * 100)}%"></span></div></td>
      <td class="right" style="white-space:nowrap">${f.points} / ${f.max_points}</td>
    </tr>`;
  }).join('');

  const summary = card('Recommendation', `
    <div class="decision">
      <span class="decision-badge ${cls}">${esc(label)}</span>
      ${r.treat_first ? '<span class="decision-badge treat">Treat first</span>' : ''}
      <div class="grow"><strong style="font-size:14.5px">${esc(r.headline)}</strong>
        ${r.overrides.length ? `<ul class="bullets risk">${r.overrides.map((o) => `<li>${esc(o)}</li>`).join('')}</ul>` : ''}
      </div>
    </div>
    <div class="adv-risk">
      <div><div class="metric-label">Risk score</div><div class="risk-score">${r.risk.score}<span class="muted" style="font-size:15px;font-weight:500"> / 100</span></div><span class="tag ${bandCls}">${esc(bandLabel)}</span></div>
      <div class="table-wrap grow"><table class="data"><tbody>${factorRows}</tbody></table></div>
    </div>`,
  { actions: r.inputs.market.data_quality ? qualityTag(r.inputs.market.data_quality) : '' });

  const actions = card('What to do, in order', `<ul class="rows">${r.actions.map((a) => `
    <li class="row" style="align-items:flex-start">
      <span class="tag ${a.priority === 1 ? 'good' : ''}" style="min-width:76px;justify-content:center">${esc(ACTION_KIND[a.kind] || a.kind)}</span>
      <div class="grow">
        <div class="row-title">${esc(a.title)}</div>
        <div class="row-sub" style="font-size:12.5px;margin-top:2px">${esc(a.detail)}</div>
        ${a.steps && a.steps.length ? stepList(a.steps) : ''}
      </div>
      ${a.priority === 1 ? '<span class="tag info">Do first</span>' : ''}
    </li>`).join('')}</ul>`, { flush: true });

  const i = r.inputs; const w = i.weather;
  const marketLine = i.market.available
    ? `${inr(i.market.current_price)} / qtl at ${esc(i.market.market || r.location)} · price signal: ${esc((DECISION[i.market.decision] || [i.market.decision])[0].toLowerCase())}`
    : esc(i.market.reason || 'No reliable price data');
  const basis = card('What this is based on', `
    <dl class="kv">
      <dt>Crop health</dt><dd>${i.diagnosis ? `${esc(i.diagnosis.condition)} · ${esc(((SEVERITY[i.diagnosis.severity] || ['', i.diagnosis.severity])[1] || '').toLowerCase())}` : 'No photo checked'}</dd>
      <dt>Market</dt><dd>${marketLine}</dd>
      <dt>Weather</dt><dd>${w.live ? `${Math.round(w.temperature)}°C, ${esc(w.humidity)}% humidity, ${esc(w.description)}${w.rainfall ? `, ${esc(w.rainfall)} mm rain` : ''}` : 'Live weather unavailable'}</dd>
      <dt>Soil</dt><dd>${esc(i.soil.soil_type || '—')}${i.soil.ph_range ? ` · pH ${esc(i.soil.ph_range)}` : ''}</dd>
      <dt>Rules applied</dt><dd>${r.rules_fired.length ? r.rules_fired.map((x) => `<span class="tag">${esc(x.replace(/_/g, ' ').toLowerCase())}</span>`).join(' ') : 'Market signal only'}</dd>
    </dl>
    ${knowledgeRefs(r.knowledge)}`,
  { hint: `${esc(r.engine_version)} · ${new Date(r.generated_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}` });

  return summary + actions + basis;
}

/* ================================================================ ROUTER */

const ROUTES = { '/': ['home', viewHome], '/prices': ['prices', viewPrices], '/advisor': ['advisor', viewAdvisor], '/farmer': ['farmer', viewFarmer], '/buyer': ['buyer', viewBuyer], '/fpo': ['fpo', viewFpo], '/calls': ['calls', viewCalls] };

async function render() {
  app.cleanup.splice(0).forEach((fn) => { try { fn(); } catch { /* already closed */ } });
  const [name, view] = ROUTES[location.pathname] || ROUTES['/'];
  $$('.nav-link').forEach((a) => a.setAttribute('aria-current', a.dataset.route === name ? 'page' : 'false'));
  const root = $('#view');
  root.innerHTML = loading();
  window.scrollTo(0, 0);
  try {
    await view(root);
  } catch (err) {
    console.error(err);
    root.innerHTML = `<div class="notice bad">Something went wrong rendering this page: ${esc(err.message)}</div>`;
  }
}

function navigate(href, { replace = false } = {}) {
  const url = new URL(href, location.origin);
  if (url.pathname + url.search === location.pathname + location.search && !replace) { render(); return; }
  history[replace ? 'replaceState' : 'pushState'](null, '', url.pathname + url.search);
  render();
}

document.addEventListener('click', (e) => {
  const link = e.target.closest('a[data-link]');
  if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  navigate(link.getAttribute('href'));
});
window.addEventListener('popstate', render);
$('#key-button').addEventListener('click', () => openKeyModal());

(async () => {
  $('#view').innerHTML = loading();
  await loadShared();
  render();
})();
