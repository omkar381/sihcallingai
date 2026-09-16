/* =============================================================================
   AI Krishi — Market Console

   Reads the same APIs the voice agent calls, so what an evaluator sees here and
   what a farmer hears on the phone come from one source.

   The rule this UI enforces visually: no number appears without its data
   quality. A MOCK or STALE figure is never rendered as though it were today's
   live market price, because the whole system is built to make that impossible
   and the interface is the last place it could leak.
   ============================================================================= */

const API = '';

const state = {
  view: 'overview',
  coverage: null,
  commodities: [],
  loading: new Set(),
};

/* ------------------------------------------------------------------ utils */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (v) => String(v ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const nf = new Intl.NumberFormat('en-IN');
const nf2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const num = (v, dp = 0) => (v === null || v === undefined || Number.isNaN(v))
  ? '—' : (dp ? nf2.format(v) : nf.format(Math.round(v)));

const rupees = (v) => (v === null || v === undefined) ? '—' : '₹' + nf2.format(v);
const rupeesShort = (v) => (v === null || v === undefined) ? '—' : '₹' + nf.format(Math.round(v));

const pct = (v, dp = 1) => (v === null || v === undefined)
  ? '—' : (v > 0 ? '+' : '') + v.toFixed(dp) + '%';

function when(ts) {
  if (!ts) return '—';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
       + ' ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
  if (!res.ok) {
    const detail = body && body.detail;
    const msg = typeof detail === 'string' ? detail
      : (detail && detail.message) || `Request failed (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

/* ---------------------------------------------------------- shared pieces */

/**
 * Render a data-quality tag.
 *
 * This is the single most important component on the page. LIVE and FRESH are
 * the only states that may look reassuring; everything else must visibly
 * qualify the number it sits beside.
 */
function qualityTag(q) {
  const map = {
    LIVE:        ['good', 'Live'],
    FRESH:       ['good', 'Fresh'],
    STALE:       ['warn', 'Stale'],
    DEGRADED:    ['warn', 'Degraded'],
    MOCK:        ['bad',  'Demo data'],
    UNAVAILABLE: ['bad',  'Unavailable'],
  };
  const [cls, label] = map[q] || ['', q || '—'];
  return `<span class="tag ${cls}"><span class="dot"></span>${esc(label)}</span>`;
}

function trustTag(badge) {
  if (!badge) return '';
  const band = badge.trust_band;
  const cls = band === 'STRONG' ? 'good'
    : band === 'DO_NOT_TRADE' || band === 'POOR' ? 'bad'
    : band === 'NEW' ? 'info' : 'warn';
  return `<span class="tag ${cls}">${esc(badge.label || band)}</span>`;
}

function statusTag(status) {
  const good = ['PUBLISHED', 'ACCEPTED', 'PAID', 'COMPLETED', 'RELEASED', 'ESCROW',
                'VERIFIED', 'DELIVERED', 'RESOLVED'];
  const bad = ['CANCELLED', 'DISPUTED', 'FAILED', 'SUSPENDED', 'REJECTED'];
  const warn = ['PENDING', 'PAYMENT_PENDING', 'OPEN', 'UNDER_REVIEW', 'DRAFT',
                'INITIATED', 'UNVERIFIED'];
  const cls = good.includes(status) ? 'good' : bad.includes(status) ? 'bad'
    : warn.includes(status) ? 'warn' : '';
  return `<span class="tag ${cls}">${esc((status || '').replace(/_/g, ' '))}</span>`;
}


/**
 * Present an engine reasoning string for reading.
 *
 * The API returns these written for the audit record ("price rose 16.0% over
 * the last 7 days"). That is the right form to store, but shown in a UI the
 * lower-case opening and bare symbols read like a log line rather than an
 * explanation.
 */
function humanise(text) {
  let out = String(text || '').trim()
    .replace(/%/g, ' percent')
    .replace(/(\d+)-day/g, '$1 day')
    .replace(/\s{2,}/g, ' ');
  if (out) out = out[0].toUpperCase() + out.slice(1);
  if (out && !/[.!?]$/.test(out)) out += '.';
  return out;
}

const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;
const loading = () => `<div class="empty"><span class="spinner"></span></div>`;

function card(title, bodyHtml, { hint = '', flush = false } = {}) {
  return `<div class="card">
    <div class="card-head"><h3>${esc(title)}</h3>${hint ? `<span class="hint">${esc(hint)}</span>` : ''}</div>
    <div class="card-body${flush ? ' flush' : ''}">${bodyHtml}</div>
  </div>`;
}

function metrics(items) {
  return `<div class="metrics">${items.map((m) => `
    <div class="metric">
      <div class="metric-label">${esc(m.label)}</div>
      <div class="metric-value">${m.value}</div>
      ${m.note ? `<div class="metric-note">${m.note}</div>` : ''}
    </div>`).join('')}</div>`;
}

function table(cols, rows) {
  if (!rows.length) return empty('Nothing to show yet.');
  return `<div class="table-wrap"><table class="data">
    <thead><tr>${cols.map((c) => `<th class="${c.align === 'right' ? 'right' : ''}">${esc(c.label)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr class="${r._highlight ? 'highlight' : ''}">${
      cols.map((c) => `<td class="${[c.align === 'right' ? 'right' : '', c.cls || ''].filter(Boolean).join(' ')}">${r[c.key] ?? '—'}</td>`).join('')
    }</tr>`).join('')}</tbody>
  </table></div>`;
}

/* ------------------------------------------------------------ data banner */

/**
 * A standing, unmissable statement of what the data actually is.
 *
 * If everything stored is demo fixture data, the console says so at the top of
 * every screen. An evaluator should never have to dig to find that out.
 */
async function renderBanner() {
  const el = $('#data-banner');
  try {
    const cov = await api('/api/market/coverage');
    state.coverage = cov;

    if (!cov.total_records) {
      el.innerHTML = `<div class="notice warn">
        <div><strong>No market data stored.</strong> Price discovery, trends and sale-window
        advice are unavailable until data is ingested. Register a data.gov.in API key and run
        an ingestion, or seed demonstration data.</div></div>`;
      return;
    }

    if (cov.is_demo_data) {
      el.innerHTML = `<div class="notice bad">
        <div><strong>All ${num(cov.total_records)} stored price records are demonstration data.</strong>
        Every figure below is generated for demonstration and is tagged <code>MOCK</code> end to end.
        It must not be used for a real selling decision. The live source (data.gov.in) is not
        reachable with the configured key.</div></div>`;
    } else if (cov.mock_records) {
      el.innerHTML = `<div class="notice warn">
        <div><strong>Mixed data.</strong> ${num(cov.real_records)} real and
        ${num(cov.mock_records)} demonstration records are stored. Rows are individually
        tagged; check the quality column before relying on any figure.</div></div>`;
    } else {
      el.innerHTML = `<div class="notice good">
        <div><strong>${num(cov.real_records)} real market records.</strong>
        No demonstration data is present.</div></div>`;
    }
  } catch (err) {
    el.innerHTML = `<div class="notice bad"><div><strong>Could not read data coverage.</strong>
      ${esc(err.message)}</div></div>`;
  }
}

/* ---------------------------------------------------------------- OVERVIEW */

async function viewOverview() {
  const el = $('#view-overview');
  el.innerHTML = loading();

  const [cov, runs, integrity, lots, buyersRes, disp] = await Promise.all([
    api('/api/market/coverage').catch(() => null),
    api('/api/market/ingestion/runs?limit=6').catch(() => ({ runs: [] })),
    api('/api/trade/audit/integrity').catch(() => null),
    api('/api/trade/lots?limit=500').catch(() => ({ lots: [] })),
    api('/api/trade/buyers?limit=500').catch(() => ({ buyers: [] })),
    api('/api/trade/disputes/statistics').catch(() => null),
  ]);

  const verified = (buyersRes.buyers || []).filter((b) => b.verification_status === 'VERIFIED');
  const openLots = (lots.lots || []).filter((l) => ['PUBLISHED', 'OFFER_RECEIVED'].includes(l.status));

  $('#c-lots').textContent = lots.lots ? lots.lots.length : '';
  $('#c-buyers').textContent = buyersRes.buyers ? buyersRes.buyers.length : '';
  $('#c-disp').textContent = disp && disp.open ? disp.open : '';

  const top = metrics([
    {
      label: 'Price records',
      value: num(cov ? cov.total_records : 0),
      note: cov && cov.is_demo_data
        ? '<span class="tag bad">all demo</span>'
        : cov ? `${num(cov.real_records)} real` : '',
    },
    {
      label: 'Markets covered',
      value: num(cov ? (cov.by_commodity[0] ? cov.by_commodity[0].markets : 0) : 0),
      note: 'per commodity',
    },
    { label: 'Open lots', value: num(openLots.length), note: `${num((lots.lots || []).length)} total` },
    { label: 'Verified buyers', value: num(verified.length), note: `${num((buyersRes.buyers || []).length)} registered` },
    { label: 'Open disputes', value: num(disp ? disp.open : 0), note: disp ? `${num(disp.total)} lifetime` : '' },
  ]);

  const covRows = (cov && cov.by_commodity || []).map((c) => ({
    commodity: `<strong>${esc(c.commodity)}</strong>`,
    records: num(c.records),
    markets: num(c.markets),
    range: `<span class="mono muted">${esc(c.first_date)} → ${esc(c.last_date)}</span>`,
    quality: qualityTag(c.data_quality),
  }));

  const runRows = (runs.runs || []).map((r) => ({
    source: `<span class="mono">${esc((r.source || '').split(':')[0])}</span>`,
    status: statusTag(r.status === 'SUCCESS' ? 'COMPLETED' : r.status),
    fetched: num(r.records_fetched),
    accepted: num(r.records_accepted),
    rejected: num(r.records_rejected),
    detail: r.error
      ? `<span class="muted" title="${esc(r.error)}">${esc(r.error.slice(0, 70))}…</span>`
      : `<span class="muted">${esc(r.scope || '')}</span>`,
  }));

  const integrityHtml = integrity ? `
    <div class="notice ${integrity.intact ? 'good' : 'bad'}" style="margin:0">
      <div><strong>${integrity.intact ? 'Audit trail intact.' : 'Audit trail compromised.'}</strong>
      ${esc(integrity.detail)} ${integrity.events ? `${num(integrity.events)} events recorded.` : ''}</div>
    </div>` : empty('Audit integrity could not be checked.');

  el.innerHTML = top
    + card('Data coverage', table([
        { key: 'commodity', label: 'Commodity' },
        { key: 'records', label: 'Records', align: 'right' },
        { key: 'markets', label: 'Markets', align: 'right' },
        { key: 'range', label: 'Date range' },
        { key: 'quality', label: 'Quality' },
      ], covRows), { flush: true, hint: 'what the system actually holds' })
    + `<div class="split">
        ${card('Ingestion runs', table([
          { key: 'source', label: 'Source' },
          { key: 'status', label: 'Status' },
          { key: 'fetched', label: 'Fetched', align: 'right' },
          { key: 'accepted', label: 'Stored', align: 'right' },
          { key: 'rejected', label: 'Rejected', align: 'right' },
          { key: 'detail', label: 'Detail' },
        ], runRows), { flush: true, hint: 'failures are never hidden' })}
        ${card('Audit integrity', integrityHtml)}
      </div>`;
}

/* --------------------------------------------------------------- DISCOVERY */

function discoveryControls() {
  const opts = state.commodities.map((c) =>
    `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join('');
  return `<div class="controls">
    <div class="field"><label for="d-commodity">Commodity</label>
      <select id="d-commodity">${opts}</select></div>
    <div class="field"><label for="d-location">Farmer location</label>
      <input id="d-location" value="Kalaburagi" placeholder="District or market"></div>
    <div class="field"><label for="d-qty">Quantity</label>
      <input id="d-qty" value="30 quintal" placeholder="e.g. 30 quintal"></div>
    <div class="field"><label for="d-radius">Radius (km)</label>
      <input id="d-radius" type="number" value="150" min="10" max="500" step="10"></div>
    <div class="field"><label>&nbsp;</label>
      <button class="btn" id="d-run">Compare markets</button></div>
  </div>`;
}

async function viewDiscovery() {
  const el = $('#view-discovery');
  el.innerHTML = card('Query', discoveryControls()) + `<div id="d-results"></div>`;
  $('#d-run').addEventListener('click', runDiscovery);
  runDiscovery();
}

async function runDiscovery() {
  const out = $('#d-results');
  const commodity = $('#d-commodity').value;
  const location = $('#d-location').value.trim() || 'Kalaburagi';
  const quantity = $('#d-qty').value.trim() || '10 quintal';
  const radius = $('#d-radius').value || 150;

  out.innerHTML = loading();

  const q = `commodity=${encodeURIComponent(commodity)}&location=${encodeURIComponent(location)}`;
  const [price, trend, cmp] = await Promise.all([
    api(`/api/market/price?${q}`).catch((e) => ({ _err: e.message })),
    api(`/api/market/trend?${q}`).catch((e) => ({ _err: e.message })),
    api(`/api/market/compare?${q}&quantity=${encodeURIComponent(quantity)}&radius_km=${radius}`)
      .catch((e) => ({ _err: e.message })),
  ]);

  if (price._err && cmp._err) {
    out.innerHTML = `<div class="notice bad"><div><strong>No data.</strong>
      ${esc(price._err)}</div></div>`;
    return;
  }

  let html = '';

  if (!price._err) {
    const w7 = trend.windows && trend.windows['7'];
    const w30 = trend.windows && trend.windows['30'];
    const arr30 = trend.arrivals && trend.arrivals['30'];
    html += metrics([
      {
        label: `${price.commodity} at ${price.market}`,
        value: rupeesShort(price.modal_price),
        note: `per quintal &middot; ${qualityTag(price.data_quality)}`,
      },
      { label: 'Per kilo', value: rupees(price.price_per_kg), note: 'modal' },
      {
        label: '7-day change',
        value: w7 && w7.sufficient_data ? pct(w7.change_pct) : '—',
        note: w7 ? `${w7.sample_count} observations` : '',
      },
      {
        label: '30-day average',
        value: w30 && w30.average ? rupeesShort(w30.average) : '—',
        note: w30 ? `volatility ${esc((w30.volatility || '').toLowerCase())}` : '',
      },
      {
        label: 'Arrivals (30d)',
        value: arr30 && arr30.change_pct !== null ? pct(arr30.change_pct) : '—',
        note: arr30 ? esc((arr30.direction || '').toLowerCase()) : '',
      },
    ]);

    if (price.caveat) {
      html += `<div class="notice warn"><div>${esc(price.caveat)}</div></div>`;
    }
  }

  if (!cmp._err && cmp.options && cmp.options.length) {
    const rows = cmp.options.map((o) => ({
      _highlight: o.is_origin,
      market: `<strong>${esc(o.market)}</strong>${o.is_origin
        ? ' <span class="tag info">your market</span>' : ''}`,
      distance: o.distance_km === 0 ? '—' : num(o.distance_km) + ' km',
      gross: rupeesShort(o.gross_price_per_quintal),
      net: `<strong>${rupeesShort(o.net_price_per_quintal)}</strong>`,
      total: rupeesShort(o.total_net),
      quality: qualityTag(o.data_quality),
    }));

    const best = cmp.options[0];
    const gain = cmp.advantage_per_quintal;
    let lead = '';
    if (gain && gain > 0) {
      lead = `<div class="notice good"><div>
        <strong>${esc(best.market)} nets ${rupeesShort(gain)} more per quintal</strong>
        than your local market after transport, loading, fees and expected rejection —
        about <strong>${rupeesShort(cmp.advantage_total)}</strong> more on this lot.
      </div></div>`;
    }

    html += lead + card('Net realisation by market', table([
      { key: 'market', label: 'Market' },
      { key: 'distance', label: 'Distance', align: 'right' },
      { key: 'gross', label: 'Gross / qtl', align: 'right' },
      { key: 'net', label: 'Net / qtl', align: 'right' },
      { key: 'total', label: 'Net for lot', align: 'right' },
      { key: 'quality', label: 'Quality' },
    ], rows), { flush: true, hint: 'ranked by what the farmer keeps, not the headline price' });

    // Cost breakdown for the winning market: the "why" behind the ranking.
    if (best.cost_breakdown) {
      const bd = best.cost_breakdown.map((line) => ({
        item: line.item === 'Net realisation'
          ? `<strong>${esc(line.item)}</strong>` : esc(line.item),
        amount: `<span class="${line.per_quintal < 0 ? 'muted' : ''}">${
          line.per_quintal < 0 ? '−' : ''}${rupeesShort(Math.abs(line.per_quintal))}</span>`,
        basis: `<span class="muted">${esc(line.basis)}</span>`,
      }));
      html += card(`Cost breakdown — ${best.market}`, table([
        { key: 'item', label: 'Item' },
        { key: 'amount', label: 'Per quintal', align: 'right' },
        { key: 'basis', label: 'Basis' },
      ], bd), { flush: true, hint: 'every deduction, with its basis' });
    }

    if (cmp.notes && cmp.notes.length) {
      html += card('Coverage notes',
        `<ul class="reasons">${cmp.notes.map((n) => `<li>${esc(humanise(n))}</li>`).join('')}</ul>`);
    }
  } else if (cmp._err) {
    html += `<div class="notice warn"><div><strong>Market comparison unavailable.</strong>
      ${esc(cmp._err)}</div></div>`;
  }

  $('#d-results').innerHTML = html;
}

/* ------------------------------------------------------------------ ADVICE */

async function viewAdvice() {
  const el = $('#view-advice');
  const opts = state.commodities.map((c) =>
    `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join('');

  el.innerHTML = card('Farmer situation', `<div class="controls">
      <div class="field"><label for="a-commodity">Commodity</label>
        <select id="a-commodity">${opts}</select></div>
      <div class="field"><label for="a-location">Location</label>
        <input id="a-location" value="Kalaburagi"></div>
      <div class="field"><label for="a-qty">Quantity</label>
        <input id="a-qty" value="30 quintal"></div>
      <div class="field"><label for="a-storage">Storage available (days)</label>
        <input id="a-storage" type="number" value="20" min="0" max="365"></div>
      <div class="field"><label for="a-cash">Needs cash urgently</label>
        <select id="a-cash"><option value="false">No</option><option value="true">Yes</option></select></div>
      <div class="field"><label>&nbsp;</label>
        <button class="btn" id="a-run">Get recommendation</button></div>
    </div>`, { hint: 'storage and cash need change the answer' })
    + `<div id="a-results"></div>`;

  $('#a-run').addEventListener('click', runAdvice);
  runAdvice();
}

async function runAdvice() {
  const out = $('#a-results');
  out.innerHTML = loading();

  const params = new URLSearchParams({
    commodity: $('#a-commodity').value,
    location: $('#a-location').value.trim() || 'Kalaburagi',
    quantity: $('#a-qty').value.trim() || '10 quintal',
    storage_days: $('#a-storage').value || 0,
    needs_cash_urgently: $('#a-cash').value,
  });

  let rec;
  try {
    rec = await api(`/api/market/sale-window?${params}`);
  } catch (err) {
    out.innerHTML = `<div class="notice bad"><div>${esc(err.message)}</div></div>`;
    return;
  }

  if (rec.decision === 'INSUFFICIENT_DATA') {
    out.innerHTML = `<div class="notice warn"><div>
      <strong>No recommendation.</strong>
      ${esc((rec.reasoning || []).map(humanise).join(' '))}
      <br><span class="muted">Declining to advise is the correct outcome when the data
      cannot support a decision.</span></div></div>`;
    return;
  }

  const verdictClass = rec.decision === 'WAIT' ? 'info'
    : rec.decision === 'SELL_NOW' ? 'good'
    : rec.decision === 'COMPARE_MARKETS' ? 'good' : 'warn';

  const head = metrics([
    {
      label: 'Decision',
      value: `<span class="tag ${verdictClass}" style="font-size:14px;padding:4px 10px">${
        esc(rec.decision.replace(/_/g, ' '))}</span>`,
      note: rec.recommended_days ? `hold about ${rec.recommended_days} days` : 'act now',
    },
    { label: 'Current price', value: rupeesShort(rec.current_price), note: 'per quintal' },
    {
      label: 'Expected range',
      value: rec.expected_price_range
        ? `${rupeesShort(rec.expected_price_range.low)}–${rupeesShort(rec.expected_price_range.high)}`
        : '—',
      note: rec.net_expected_gain_per_quintal !== null && rec.net_expected_gain_per_quintal !== undefined
        ? `net of storage: ${rupeesShort(rec.net_expected_gain_per_quintal)}/qtl` : '',
    },
    { label: 'Confidence', value: esc(rec.confidence), note: qualityTag(rec.data_quality) },
  ]);

  const signals = (rec.signals || []).map((s) => {
    const dirCls = s.direction === 'favours_waiting' ? 'info'
      : s.direction === 'favours_selling' ? 'warn' : '';
    return {
      signal: `<strong>${esc(s.signal.replace(/_/g, ' '))}</strong>`,
      direction: `<span class="tag ${dirCls}">${esc(s.direction.replace(/favours_/, ''))}</span>`,
      weight: `<div style="display:flex;align-items:center;gap:8px;justify-content:flex-end">
        <span>${s.weight.toFixed(1)}</span>
        <span class="bar"><span style="width:${Math.min(100, s.weight / 3 * 100)}%"></span></span></div>`,
      detail: `<span class="muted">${esc(s.detail)}</span>`,
    };
  });

  let alt = '';
  if (rec.alternative_market) {
    const a = rec.alternative_market;
    alt = `<div class="notice good"><div>
      <strong>Better market: ${esc(a.market)}</strong>, ${num(a.distance_km)} km away.
      Nets ${rupeesShort(a.advantage_per_quintal)} more per quintal after transport
      ${a.advantage_total ? `— about ${rupeesShort(a.advantage_total)} on this lot` : ''}.
    </div></div>`;
  }

  out.innerHTML = head + alt
    + `<div class="split">
        ${card('Why', `<ul class="reasons">${
          (rec.reasoning || []).map((r) => `<li>${esc(humanise(r))}</li>`).join('')}</ul>`,
          { hint: 'from the engine, not the language model' })}
        ${card('Risks', `<ul class="reasons risk">${
          (rec.risks || []).map((r) => `<li>${esc(humanise(r))}</li>`).join('')
          || '<li class="muted">None recorded.</li>'}</ul>`)}
      </div>`
    + card('Signal weighting', table([
        { key: 'signal', label: 'Signal' },
        { key: 'direction', label: 'Direction' },
        { key: 'weight', label: 'Weight', align: 'right' },
        { key: 'detail', label: 'Basis' },
      ], signals), { flush: true, hint: `engine ${rec.engine_version}` });
}

/* -------------------------------------------------------------------- LOTS */

async function viewLots() {
  const el = $('#view-lots');
  el.innerHTML = loading();
  const res = await api('/api/trade/lots?limit=200').catch(() => ({ lots: [] }));

  const rows = (res.lots || []).map((l) => ({
    lot: `<span class="mono">${esc(l.id.slice(0, 8))}</span>`
       + (l.is_aggregate ? ' <span class="tag info">FPO lot</span>' : ''),
    commodity: `<strong>${esc(l.commodity)}</strong>`,
    quantity: `${num(l.quantity_kg)} kg`,
    grade: l.grade
      ? `<span class="tag ${l.grade_is_evidence ? 'good' : ''}">${esc(l.grade)}</span>
         <span class="sub">${esc((l.grade_basis || '').replace(/_/g, ' ').toLowerCase())}</span>`
      : '<span class="muted">ungraded</span>',
    location: esc(l.pickup_district || '—'),
    status: statusTag(l.status),
    actions: `<button class="btn secondary" data-lot="${esc(l.id)}">Matches</button>`,
  }));

  el.innerHTML = card('Lots', table([
    { key: 'lot', label: 'Lot' },
    { key: 'commodity', label: 'Commodity' },
    { key: 'quantity', label: 'Quantity', align: 'right' },
    { key: 'grade', label: 'Grade', cls: 'cell-stack' },
    { key: 'location', label: 'Pickup' },
    { key: 'status', label: 'Status' },
    { key: 'actions', label: '' },
  ], rows), { flush: true, hint: `${rows.length} lots · aggregated member lots are not double counted` })
    + `<div id="lot-detail"></div>`;

  $$('[data-lot]', el).forEach((btn) => {
    btn.addEventListener('click', () => showMatches(btn.dataset.lot));
  });
}

async function showMatches(lotId) {
  const out = $('#lot-detail');
  out.innerHTML = loading();
  const res = await api(`/api/trade/lots/${lotId}/matches`).catch((e) => ({ _err: e.message }));

  if (res._err) { out.innerHTML = `<div class="notice bad"><div>${esc(res._err)}</div></div>`; return; }
  if (!res.matches.length) {
    out.innerHTML = `<div class="notice warn"><div><strong>No matching buyers.</strong>
      ${esc(res.note || '')} Buyers whose grade, timing or distance requirements this lot
      cannot meet are excluded rather than ranked low.</div></div>`;
    return;
  }

  out.innerHTML = res.matches.map((m) => {
    const factors = m.factors.map((f) => `
      <tr><td>${esc(f.factor)}</td>
      <td class="right">${f.score_pct.toFixed(0)}%</td>
      <td class="right">${f.weight.toFixed(0)}</td>
      <td class="right"><strong>${f.points.toFixed(1)}</strong></td>
      <td class="muted">${esc(f.detail)}</td></tr>`).join('');

    return card(`${m.buyer_name} — ${m.match_score}/100`, `
      <div class="kv" style="margin-bottom:14px">
        <dt>Offer</dt><dd>${rupeesShort(m.price_offered)} / quintal</dd>
        <dt>Distance</dt><dd>${m.distance_km !== null ? num(m.distance_km) + ' km' : '—'}</dd>
        <dt>Net to farmer</dt><dd><strong>${rupeesShort(m.net_price_per_quintal)}</strong> / quintal</dd>
        <dt>Estimated total</dt><dd>${rupeesShort(m.estimated_total_net)}</dd>
        <dt>Buyer</dt><dd>${trustTag(m.badge)}</dd>
      </div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Factor</th><th class="right">Score</th><th class="right">Weight</th>
        <th class="right">Points</th><th>Basis</th></tr></thead>
        <tbody>${factors}</tbody></table></div>
      ${m.warnings.length ? `<div style="margin-top:12px"><ul class="reasons risk">${
        m.warnings.map((w) => `<li>${esc(humanise(w))}</li>`).join('')}</ul></div>` : ''}
    `, { flush: false });
  }).join('');
}

/* ------------------------------------------------------------------ BUYERS */

async function viewBuyers() {
  const el = $('#view-buyers');
  el.innerHTML = loading();
  const [res, demand] = await Promise.all([
    api('/api/trade/buyers?limit=200').catch(() => ({ buyers: [] })),
    api('/api/trade/demand?limit=200').catch(() => ({ demand: [] })),
  ]);

  const rows = (res.buyers || []).map((b) => ({
    name: `<strong>${esc(b.name)}</strong><span class="sub">${
      esc((b.buyer_type || '').replace(/_/g, ' ').toLowerCase())}</span>`,
    status: statusTag(b.verification_status),
    trust: `<div style="display:flex;align-items:center;gap:8px">
      <span class="bar" style="width:60px"><span style="width:${b.trust.score}%"></span></span>
      <span>${b.trust.score}</span></div>`,
    record: b.trust.is_new
      ? '<span class="muted">no history</span>'
      : `${b.trust.completed_transactions}/${b.trust.total_transactions} completed`
        + (b.trust.avg_payment_days !== null
          ? `<br><span class="muted" style="font-size:11.5px">pays in ${b.trust.avg_payment_days}d</span>` : ''),
    flags: [
      b.trust.payment_defaults ? `<span class="tag bad">${b.trust.payment_defaults} default(s)</span>` : '',
      b.trust.disputes ? `<span class="tag warn">${b.trust.disputes} dispute(s)</span>` : '',
    ].filter(Boolean).join(' ') || '<span class="muted">—</span>',
    badge: trustTag(b.badge),
  }));

  const dRows = (demand.demand || []).map((d) => ({
    buyer: esc(d.buyer ? d.buyer.name : d.buyer_id.slice(0, 8)),
    commodity: `<strong>${esc(d.commodity)}</strong>`,
    needed: `${num(d.quantity_remaining_kg)} kg`,
    grade: d.min_grade ? `<span class="tag">${esc(d.min_grade)}+</span>` : '<span class="muted">any</span>',
    price: rupeesShort(d.price_offered),
    terms: `${d.payment_terms_days}d`,
    where: esc(d.delivery_district || 'any'),
  }));

  el.innerHTML =
    card('Buyers', table([
      { key: 'name', label: 'Buyer', cls: 'cell-name' },
      { key: 'status', label: 'Verification' },
      { key: 'trust', label: 'Trust', align: 'right' },
      { key: 'record', label: 'Record' },
      { key: 'flags', label: 'Flags' },
      { key: 'badge', label: 'Farmer-facing badge' },
    ], rows), { flush: true, hint: 'badges are computed from behaviour, never hardcoded' })
    + card('Open demand', table([
      { key: 'buyer', label: 'Buyer', cls: 'cell-name' },
      { key: 'commodity', label: 'Commodity' },
      { key: 'needed', label: 'Still needed', align: 'right' },
      { key: 'grade', label: 'Min grade' },
      { key: 'price', label: 'Offer / qtl', align: 'right' },
      { key: 'terms', label: 'Terms', align: 'right' },
      { key: 'where', label: 'Delivery' },
    ], dRows), { flush: true, hint: 'verified buyers only' });
}

/* --------------------------------------------------------------------- FPO */

async function viewFpo() {
  const el = $('#view-fpo');
  el.innerHTML = loading();
  const res = await api('/api/trade/fpos').catch(() => ({ fpos: [] }));

  if (!res.fpos.length) {
    el.innerHTML = card('FPOs', empty('No FPOs registered yet.'), { flush: true });
    return;
  }

  const options = res.fpos.map((f) =>
    `<option value="${esc(f.id)}">${esc(f.name)} — ${f.member_count} members</option>`).join('');

  el.innerHTML = card('Aggregation planner', `<div class="controls">
      <div class="field"><label for="f-fpo">FPO</label><select id="f-fpo">${options}</select></div>
      <div class="field"><label for="f-price">Expected price / quintal</label>
        <input id="f-price" type="number" value="3280" step="10"></div>
      <div class="field"><label for="f-dist">Distance to market (km)</label>
        <input id="f-dist" type="number" value="136" step="5"></div>
      <div class="field"><label>&nbsp;</label>
        <button class="btn" id="f-run">Find poolable lots</button></div>
    </div>`, { hint: 'lots are only pooled when commodity, grade and variety match' })
    + `<div id="f-results"></div>`;

  $('#f-run').addEventListener('click', runFpo);
  runFpo();
}

async function runFpo() {
  const out = $('#f-results');
  out.innerHTML = loading();

  const id = $('#f-fpo').value;
  const price = $('#f-price').value || 3280;
  const dist = $('#f-dist').value || 100;

  const res = await api(
    `/api/trade/fpos/${id}/aggregation-groups?price_per_quintal=${price}&distance_km=${dist}`
  ).catch((e) => ({ _err: e.message }));

  if (res._err) { out.innerHTML = `<div class="notice bad"><div>${esc(res._err)}</div></div>`; return; }
  if (!res.groups.length) {
    out.innerHTML = `<div class="notice warn"><div>No published member lots available to pool.</div></div>`;
    return;
  }

  out.innerHTML = res.groups.map((g) => {
    const memberRows = g.lots.map((l) => ({
      farmer: `<span class="mono">${esc((l.farmer_phone || '').slice(-4).padStart(4, '·'))}</span>`,
      village: esc(l.pickup_village || '—'),
      qty: `${num(l.quantity_kg)} kg`,
      share: ((l.quantity_kg / g.total_kg) * 100).toFixed(1) + '%',
    }));

    const b = g.benefit;
    const poolable = g.lot_count > 1;

    // A single lot cannot be pooled with anything, so quoting a benefit of
    // zero there is noise. Say why it is not poolable instead.
    let benefitHtml = '';
    if (!poolable) {
      benefitHtml = `<div class="notice info" style="margin:0 0 14px">
        <div>Only one lot in this group, so there is nothing to pool with.
        Aggregation needs at least two compatible lots.</div></div>`;
    } else if (b && b.total_gain > 0) {
      benefitHtml = `<div class="notice good" style="margin:0 0 14px">
        <div><strong>Pooling earns ${rupeesShort(b.total_gain)} more</strong>
        — about ${rupeesShort(b.gain_per_farmer)} per farmer,
        ${rupeesShort(b.gain_per_quintal)} per quintal.
        <br><span class="muted">${esc(b.explanation)}
        Separately: ${rupeesShort(b.net_if_sold_separately)}.
        Pooled: ${rupeesShort(b.net_if_aggregated)}
        using one ${esc(b.vehicle_when_aggregated)}.</span></div>
      </div>`;
    } else if (b) {
      benefitHtml = `<div class="notice warn" style="margin:0 0 14px">
        <div>Pooling these lots would not improve net realisation at this price
        and distance. They already fit one vehicle.</div></div>`;
    }

    return card(
      `${g.commodity} · Grade ${g.grade} · ${num(g.total_kg)} kg`,
      benefitHtml + table([
        { key: 'farmer', label: 'Farmer' },
        { key: 'village', label: 'Village' },
        { key: 'qty', label: 'Quantity', align: 'right' },
        { key: 'share', label: 'Share', align: 'right' },
      ], memberRows),
      { hint: poolable && g.viable
          ? `${g.farmer_count} farmer${g.farmer_count === 1 ? '' : 's'} · poolable`
          : !poolable
            ? 'single lot'
            : 'below the minimum worth aggregating' }
    );
  }).join('');
}

/* ---------------------------------------------------------------- PAYMENTS */

async function viewPayments() {
  const el = $('#view-payments');
  el.innerHTML = loading();
  const res = await api('/api/trade/payments?limit=200').catch(() => ({ payments: [] }));

  $('#c-pay').textContent = res.payments ? res.payments.length : '';

  const rows = (res.payments || []).map((p) => ({
    id: `<span class="mono">${esc(p.id.slice(0, 8))}</span>`,
    lot: `<span class="mono muted">${esc(p.lot_id.slice(0, 8))}</span>`,
    amount: `<strong>${rupees(p.amount_rupees)}</strong>`,
    status: statusTag(p.status),
    due: p.is_overdue
      ? `<span class="tag bad">${p.days_overdue}d overdue</span>`
      : `<span class="muted">${when(p.due_date)}</span>`,
    utr: p.utr_ref ? `<span class="mono">${esc(p.utr_ref)}</span>` : '<span class="muted">—</span>',
  }));

  const escrowed = (res.payments || []).filter((p) => p.status === 'ESCROW');
  const released = (res.payments || []).filter((p) => p.status === 'RELEASED');
  const overdue = (res.payments || []).filter((p) => p.is_overdue);
  const sum = (list) => list.reduce((a, p) => a + p.amount_rupees, 0);

  el.innerHTML = metrics([
    { label: 'In escrow', value: rupeesShort(sum(escrowed)), note: `${escrowed.length} payments` },
    { label: 'Released', value: rupeesShort(sum(released)), note: `${released.length} settled` },
    { label: 'Overdue', value: rupeesShort(sum(overdue)), note: `${overdue.length} late` },
    { label: 'Total tracked', value: rupeesShort(sum(res.payments || [])), note: `${rows.length} records` },
  ]) + card('Payments', table([
    { key: 'id', label: 'Payment' },
    { key: 'lot', label: 'Lot' },
    { key: 'amount', label: 'Amount', align: 'right' },
    { key: 'status', label: 'Status' },
    { key: 'due', label: 'Due' },
    { key: 'utr', label: 'Reference' },
  ], rows), { flush: true, hint: 'amounts stored in integer paise' });
}

/* ---------------------------------------------------------------- DISPUTES */

async function viewDisputes() {
  const el = $('#view-disputes');
  el.innerHTML = loading();
  const [res, stats] = await Promise.all([
    api('/api/trade/disputes?limit=200').catch(() => ({ disputes: [] })),
    api('/api/trade/disputes/statistics').catch(() => null),
  ]);

  const rows = (res.disputes || []).map((d) => ({
    id: `<span class="mono">${esc(d.id.slice(0, 8))}</span>`,
    category: `<span class="tag">${esc(d.category)}</span>`,
    raised: `${esc(d.raised_by_role)}<br><span class="muted mono" style="font-size:11px">${
      esc((d.raised_by || '').slice(-4))}</span>`,
    description: `<span class="muted">${esc((d.description || '').slice(0, 90))}</span>`,
    claim: d.claim_rupees ? rupees(d.claim_rupees) : '<span class="muted">—</span>',
    status: statusTag(d.status),
    age: `${d.age_days}d`,
  }));

  const head = stats ? metrics([
    { label: 'Open', value: num(stats.open), note: `${num(stats.total)} lifetime` },
    { label: 'Resolved', value: num(stats.resolved), note: 'closed' },
    {
      label: 'Avg resolution',
      value: stats.avg_resolution_days !== null ? stats.avg_resolution_days + 'd' : '—',
      note: 'from open to resolved',
    },
    {
      label: 'Top category',
      value: Object.keys(stats.by_category || {}).length
        ? esc(Object.entries(stats.by_category).sort((a, b) => b[1] - a[1])[0][0]) : '—',
      note: 'most common',
    },
  ]) : '';

  el.innerHTML = head + card('Disputes', table([
    { key: 'id', label: 'Dispute' },
    { key: 'category', label: 'Category' },
    { key: 'raised', label: 'Raised by' },
    { key: 'description', label: 'Description' },
    { key: 'claim', label: 'Claim', align: 'right' },
    { key: 'status', label: 'Status' },
    { key: 'age', label: 'Age', align: 'right' },
  ], rows), { flush: true, hint: 'opening a dispute freezes the associated payment' });
}

/* ------------------------------------------------------------------- AUDIT */

async function viewAudit() {
  const el = $('#view-audit');
  el.innerHTML = loading();
  const [res, integrity] = await Promise.all([
    api('/api/trade/audit?limit=120').catch(() => ({ events: [] })),
    api('/api/trade/audit/integrity').catch(() => null),
  ]);

  const banner = integrity ? `<div class="notice ${integrity.intact ? 'good' : 'bad'}">
    <div><strong>${integrity.intact ? 'Trail intact' : 'Trail compromised'}</strong> —
    ${esc(integrity.detail)}
    ${integrity.sequence_range
      ? `<span class="muted">Sequence ${integrity.sequence_range[0]}–${integrity.sequence_range[1]}.</span>` : ''}
    </div></div>` : '';

  const items = (res.events || []).map((e) => {
    const change = (e.old_value && e.new_value && e.old_value.status && e.new_value.status)
      ? `<span class="muted">${esc(e.old_value.status)} → ${esc(e.new_value.status)}</span>` : '';
    return `<li class="done">
      <div class="t-action">${esc(e.action.replace(/_/g, ' '))}
        <span class="tag" style="margin-left:6px">${esc(e.entity_type)}</span></div>
      <div class="t-meta">#${e.sequence} · <span class="mono">${esc(e.entity_id.slice(0, 8))}</span>
        · ${esc(e.actor || 'system')} · ${when(e.created_at)} ${change ? '· ' + change : ''}</div>
    </li>`;
  }).join('');

  el.innerHTML = banner + card('Recent events',
    items ? `<ul class="timeline">${items}</ul>` : empty('No events recorded yet.'),
    { hint: 'append-only; never updated or deleted' });
}

/* ------------------------------------------------------------------ router */

const VIEWS = {
  overview:  { fn: viewOverview,  title: 'Overview',        sub: 'System state and data provenance' },
  discovery: { fn: viewDiscovery, title: 'Price discovery', sub: 'Multi-market prices and net realisation' },
  advice:    { fn: viewAdvice,    title: 'Sale window',     sub: 'Sell or wait, with the reasoning behind it' },
  lots:      { fn: viewLots,      title: 'Lots',            sub: 'Consignments and their buyer matches' },
  buyers:    { fn: viewBuyers,    title: 'Buyers',          sub: 'Verification state and computed trust' },
  fpo:       { fn: viewFpo,       title: 'FPO aggregation', sub: 'Pool member lots into buyer-ready volumes' },
  payments:  { fn: viewPayments,  title: 'Payments',        sub: 'Escrow, settlement and overdue tracking' },
  disputes:  { fn: viewDisputes,  title: 'Disputes',        sub: 'Grievances and their resolution' },
  audit:     { fn: viewAudit,     title: 'Audit trail',     sub: 'Append-only record of every economic action' },
};

async function show(name) {
  const view = VIEWS[name];
  if (!view) return;

  state.view = name;
  $('#view-title').textContent = view.title;
  $('#view-sub').textContent = view.sub;

  $$('.nav-item').forEach((b) => b.setAttribute('aria-current', String(b.dataset.view === name)));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));

  try {
    await view.fn();
  } catch (err) {
    $(`#view-${name}`).innerHTML =
      `<div class="notice bad"><div><strong>Could not load this view.</strong>
        ${esc(err.message)}</div></div>`;
  }
}

async function boot() {
  $$('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => show(btn.dataset.view));
  });
  $('#refresh').addEventListener('click', async () => {
    await renderBanner();
    show(state.view);
  });

  try {
    const res = await api('/api/market/commodities');
    state.commodities = res.commodities || [];
  } catch { state.commodities = [{ name: 'Onion' }]; }

  await renderBanner();
  await show('overview');
}

boot();
