/* =============================================================================
   AI Krishi Farmer Portal

   Registration, sign-in, the digital Farmer ID card and the farmer's own
   records. Every request goes to /api/portal with the session cookie; nothing
   about the farmer is kept in browser storage.
   ============================================================================= */

'use strict';

/* ------------------------------------------------------------------ utils */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (v) => String(v ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const nf = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

function toDate(v) {
  if (v === null || v === undefined || v === '') return null;
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(/^\d{4}-\d{2}-\d{2}$/.test(v) ? `${v}T00:00:00` : v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDate(v) {
  const d = toDate(v);
  return d ? d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
}

function fmtDateTime(v) {
  const d = toDate(v);
  return d ? d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
}

function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'Just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return fmtDate(ts);
}

function fmtAcres(v) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  return `${nf.format(n)} ${n === 1 ? 'acre' : 'acres'}`;
}

function fmtBytes(n) {
  if (!n && n !== 0) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtMobile(m) {
  const d = String(m || '').replace(/\D/g, '');
  if (d.length >= 10 && (d.length === 10 || d.startsWith('91'))) {
    const last = d.slice(-10);
    return `+91 ${last.slice(0, 5)} ${last.slice(5)}`;
  }
  if (d.length === 11 && d.startsWith('1')) return `+1 ${d.slice(1, 4)} ${d.slice(4, 7)} ${d.slice(7)}`;
  return m || '—';
}

function initials(name) {
  return String(name || '?').trim().split(/\s+/).slice(0, 2).map((p) => p[0] || '').join('').toUpperCase() || '?';
}

function todayISO() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function val(v, fallback = '—') {
  return v === null || v === undefined || v === '' ? fallback : v;
}

function dd(v) {
  return v === null || v === undefined || v === '' ? '<dd class="empty-val">Not added</dd>' : `<dd>${esc(v)}</dd>`;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
  }
}

/* ------------------------------------------------------------------ icons */

const PATHS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
  map: '<path d="m9 4-6 2.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
  sprout: '<path d="M12 21v-9"/><path d="M12 12C12 8 9.5 5.5 5 5.5 5 10 7.5 12.3 12 12Z"/><path d="M12 14.5c0-3.4 2.3-5.5 6.5-5.5 0 3.8-2.3 5.8-6.5 5.5Z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  bell: '<path d="M18 16V11a6 6 0 0 0-12 0v5l-2 2h16z"/><path d="M10 21h4"/>',
  idcard: '<rect x="2.5" y="5" width="19" height="14" rx="2"/><circle cx="8.5" cy="11" r="2.2"/><path d="M5 16c.6-1.6 2-2.4 3.5-2.4S11.4 14.4 12 16M14.5 10h4M14.5 13.5h3"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5M5 12h11"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.3 6.3C3.8 8 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.7-1.8M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  error: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16h.01"/>',
  shield: '<path d="M12 3 4.5 6v6c0 4.6 3.2 7.9 7.5 9 4.3-1.1 7.5-4.4 7.5-9V6z"/><path d="m8.8 12 2.2 2.2 4.3-4.4"/>',
  shieldPlain: '<path d="M12 3 4.5 6v6c0 4.6 3.2 7.9 7.5 9 4.3-1.1 7.5-4.4 7.5-9V6z"/><path d="M12 8v4.5M12 15.5h.01"/>',
  phone: '<path d="M21 16.5v3a2 2 0 0 1-2.2 2A19 19 0 0 1 2.5 5.2 2 2 0 0 1 4.5 3h3a1.5 1.5 0 0 1 1.5 1.3l.5 3a1.5 1.5 0 0 1-.4 1.3L7.6 10a15 15 0 0 0 6.4 6.4l1.4-1.5a1.5 1.5 0 0 1 1.3-.4l3 .5a1.5 1.5 0 0 1 1.3 1.5Z"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  printer: '<path d="M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path d="M7 14h10v7H7z"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13.5" r="3.5"/>',
  cloud: '<path d="M7 18a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 9.5a4 4 0 0 1-.5 8.5z"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  pin: '<path d="M12 21s7-6.2 7-11.5a7 7 0 0 0-14 0C5 14.8 12 21 12 21Z"/><circle cx="12" cy="9.5" r="2.5"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  megaphone: '<path d="M3 10v4h3l7 4V6L6 10zM17 9a4 4 0 0 1 0 6"/>',
  gift: '<path d="M4 11h16v9H4zM3 7h18v4H3zM12 7v13"/><path d="M12 7C10 3 7 4 7.5 5.8 8 7 12 7 12 7Zm0 0c2-4 5-3 4.5-1.2C16 7 12 7 12 7Z"/>',
  drop: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11Z"/>',
  wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 16h14a3 3 0 1 1-3 3M3 12h17"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  external: '<path d="M14 4h6v6M20 4 11 13M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20v-4h-4"/>',
  harvest: '<path d="M5 20h14M7 20l1.5-8h7L17 20M9 12V8a3 3 0 0 1 6 0v4"/>',
  location: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="7"/>',
};

function icon(name, cls = '') {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || ''}</svg>`;
}

const LEAF_MARK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21V10.5M12 10.5c0-3.2-2.4-5.3-6.5-5.3 0 3.9 2.6 5.7 6.5 5.3Zm0 0c0-3.2 2.4-5.3 6.5-5.3 0 3.9-2.6 5.7-6.5 5.3Zm0 4.4c0-2.3 1.8-3.7 4.6-3.7 0 2.7-1.8 4-4.6 3.7Z"/></svg>';
const SILHOUETTE = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="8.6" r="4.3"/><path d="M3.6 21.5c.6-4.6 4.1-7.4 8.4-7.4s7.8 2.8 8.4 7.4z"/></svg>';

function brand(sub = 'Farmer Portal') {
  return `<a class="brand" href="/portal" data-link><span class="brand-mark">${LEAF_MARK}</span><span>AI Krishi<small>${esc(sub)}</small></span></a>`;
}

/* -------------------------------------------------------------------- api */

class ApiError extends Error {
  constructor(message, status, code = '', fields = {}) {
    super(message);
    this.status = status; this.code = code; this.fields = fields || {};
  }
}

async function api(path, { method = 'GET', body, form } = {}) {
  const headers = { 'X-Portal-Client': 'web', Accept: 'application/json' };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }

  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' });
  } catch {
    throw new ApiError('Could not reach the server. Check your internet connection and try again.', 0, 'NETWORK');
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }

  if (!res.ok) {
    const d = data && data.detail;
    let err;
    if (Array.isArray(d)) err = new ApiError('Some required information is missing. Check the form and try again.', res.status, 'VALIDATION');
    else if (d && typeof d === 'object') err = new ApiError(d.message || `Request failed (${res.status})`, res.status, d.code, d.fields);
    else err = new ApiError(typeof d === 'string' ? d : res.status >= 500 ? 'Something went wrong on our side. Please try again in a moment.' : `Request failed (${res.status})`, res.status);

    if (err.code === 'UNAUTHENTICATED' && S.farmer) signedOut('Your session has ended. Please sign in again.');
    if (err.code === 'PASSWORD_CHANGE_REQUIRED' && S.farmer) { S.farmer.must_change_password = true; navigate('/portal/change-password', { replace: true }); }
    throw err;
  }
  return data;
}

/* ------------------------------------------------------------------ state */

const S = {
  meta: null,
  farmer: undefined, // undefined: not checked yet; null: signed out
  unread: 0,
  renderToken: 0,
  justRegistered: null,
};

function photoUrl(f = S.farmer) {
  return f && f.photo_available ? `/api/portal/photo?v=${f.photo_version}` : '';
}

function avatar(f = S.farmer, cls = '') {
  const url = photoUrl(f);
  return `<span class="avatar ${cls}">${url ? `<img src="${esc(url)}" alt="" onerror="this.replaceWith(document.createTextNode('${esc(initials(f && f.full_name))}'))">` : esc(initials(f && f.full_name))}</span>`;
}

const VERIFY_BADGE = {
  VERIFIED: ['green', 'Verified', 'shield'],
  PENDING: ['amber', 'Pending verification', 'clock'],
  SUSPENDED: ['red', 'Suspended', 'alert'],
};
const CARD_BADGE = { ACTIVE: ['green', 'Active'], PENDING: ['amber', 'Pending verification'], EXPIRED: ['red', 'Expired'], SUSPENDED: ['red', 'Suspended'] };
const CROP_BADGE = { PLANNED: 'blue', SOWN: 'violet', GROWING: 'green', HARVESTED: '', FAILED: 'red' };

function verifyBadge(status) {
  const [cls, label, ic] = VERIFY_BADGE[status] || ['', status];
  return `<span class="badge ${cls}">${ic ? icon(ic) : ''}${esc(label)}</span>`;
}

function badge(cls, label, dot = false) {
  return `<span class="badge ${cls}">${dot ? '<span class="dot"></span>' : ''}${esc(label)}</span>`;
}

/* --------------------------------------------------------------- feedback */

function toast(message, kind = 'success') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  el.innerHTML = `${icon(kind === 'error' ? 'error' : 'check')}<div class="grow">${esc(message)}</div><button type="button" aria-label="Dismiss">${icon('x')}</button>`;
  $('button', el).addEventListener('click', () => el.remove());
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 4000);
}

function alertBox(kind, html, title = '') {
  const ic = { info: 'info', success: 'check', warning: 'alert', error: 'error' }[kind] || 'info';
  return `<div class="alert ${kind}" role="${kind === 'error' ? 'alert' : 'status'}">${icon(ic)}<div class="alert-body">${title ? `<strong>${esc(title)}</strong> ` : ''}${html}</div></div>`;
}

function emptyState({ icon: ic = 'file', title, text = '', action = '' }) {
  return `<div class="state"><div class="state-icon">${icon(ic)}</div><h4>${esc(title)}</h4>${text ? `<p>${esc(text)}</p>` : ''}${action}</div>`;
}

function errorState(err, retryLabel = 'Try again') {
  return `<div class="card"><div class="state"><div class="state-icon error">${icon('error')}</div>
    <h4>This page could not be loaded</h4><p>${esc(err && err.message ? err.message : 'Something went wrong.')}</p>
    <button class="btn secondary" type="button" data-retry>${icon('refresh')}${esc(retryLabel)}</button></div></div>`;
}

function pageSkeleton() {
  return `<div class="page-head"><div style="width:100%;max-width:360px"><div class="skeleton sk-line" style="height:22px;width:60%"></div><div class="skeleton sk-line" style="width:90%"></div></div></div>
    <div class="stats">${'<div class="stat"><div style="width:100%"><div class="skeleton sk-line" style="width:50%"></div><div class="skeleton sk-line" style="height:22px;width:40%"></div></div></div>'.repeat(4)}</div>
    <div class="grid main-side" style="margin-top:20px"><div class="card"><div class="card-body"><div class="skeleton sk-block"></div><div class="skeleton sk-line"></div><div class="skeleton sk-line" style="width:70%"></div></div></div>
    <div class="card"><div class="card-body"><div class="skeleton sk-line"></div><div class="skeleton sk-line" style="width:80%"></div><div class="skeleton sk-line" style="width:60%"></div></div></div></div>`;
}

function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.innerHTML = `<span class="spinner"></span>${busyLabel ? `<span>${esc(busyLabel)}</span>` : ''}`;
  } else {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    if (button.dataset.label !== undefined) button.innerHTML = button.dataset.label;
  }
}

/* ----------------------------------------------------------------- modals */

function openModal({ title, description = '', body = '', footer = '', size = '', onClose } = {}) {
  const id = `m${Math.random().toString(36).slice(2, 8)}`;
  const wrap = document.createElement('div');
  wrap.className = 'modal-backdrop';
  wrap.innerHTML = `<div class="modal ${size}" role="dialog" aria-modal="true" aria-labelledby="${id}-t">
      <div class="modal-head"><div><h3 id="${id}-t">${esc(title)}</h3>${description ? `<p>${esc(description)}</p>` : ''}</div>
        <button type="button" class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
    </div>`;
  const previous = document.activeElement;
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    const focusables = $$('button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select, textarea', wrap).filter((el) => el.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0]; const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  function close() {
    if (!wrap.isConnected) return;
    wrap.remove();
    document.removeEventListener('keydown', onKey);
    if (!$('.modal-backdrop')) document.body.style.overflow = '';
    if (previous && previous.focus && previous.isConnected) previous.focus();
    if (onClose) onClose();
  }
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
  $$('[data-close]', wrap).forEach((b) => b.addEventListener('click', close));
  document.addEventListener('keydown', onKey);
  $('#overlay-root').appendChild(wrap);
  document.body.style.overflow = 'hidden';
  setTimeout(() => {
    const target = $('.modal-body input:not([type="hidden"]):not([type="file"]), .modal-body select, .modal-body textarea', wrap) || $('.modal-foot .btn:last-child', wrap);
    if (target) target.focus();
  }, 40);
  return { el: wrap, close };
}

function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = true }) {
  return new Promise((resolve) => {
    let answered = false;
    const m = openModal({
      title, size: 'sm',
      body: `<div class="confirm-icon ${danger ? '' : 'neutral'}">${icon(danger ? 'trash' : 'info')}</div><p class="text-2">${esc(message)}</p>`,
      footer: `<button class="btn secondary" type="button" data-close>Cancel</button><button class="btn ${danger ? 'danger' : ''}" type="button" data-ok>${esc(confirmLabel)}</button>`,
      onClose: () => { if (!answered) resolve(false); },
    });
    $('[data-ok]', m.el).addEventListener('click', () => { answered = true; m.close(); resolve(true); });
  });
}

/* ------------------------------------------------------------------ forms */

function field({
  name, label, type = 'text', value = '', required = false, placeholder = '', help = '',
  options = null, span = '', attrs = '', optional = false, prefix = '', rows = 3, labelMsg = '',
}) {
  const id = `f-${name}`;
  const v = value === null || value === undefined ? '' : value;
  const common = `id="${id}" name="${esc(name)}" data-label="${esc(labelMsg || label)}" ${required ? 'required aria-required="true"' : ''} ${attrs}`;
  let control;
  if (type === 'select') {
    const opts = (options || []).map((o) => {
      const [ov, ol] = Array.isArray(o) ? o : [o, o];
      return `<option value="${esc(ov)}" ${String(ov) === String(v) ? 'selected' : ''}>${esc(ol)}</option>`;
    }).join('');
    control = `<select class="input" ${common}>${placeholder !== null ? `<option value="">${esc(placeholder || 'Select')}</option>` : ''}${opts}</select>`;
  } else if (type === 'textarea') {
    control = `<textarea class="input" rows="${rows}" ${common} placeholder="${esc(placeholder)}">${esc(v)}</textarea>`;
  } else if (type === 'password') {
    control = `<div class="input-group"><input class="input" type="password" ${common} value="${esc(v)}" placeholder="${esc(placeholder)}">
      <button type="button" class="input-addon" data-toggle-pw aria-label="Show password" title="Show password">${icon('eye')}</button></div>`;
  } else {
    const input = `<input class="input" type="${type}" ${common} value="${esc(v)}" placeholder="${esc(placeholder)}">`;
    control = prefix ? `<div class="input-prefix"><span>${esc(prefix)}</span>${input}</div>` : input;
  }
  return `<div class="field ${span}">
    <label for="${id}">${esc(label)}${required ? '<span class="req" aria-hidden="true">*</span>' : ''}${optional ? '<span class="optional">(optional)</span>' : ''}</label>
    ${control}
    ${help ? `<div class="help" id="${id}-help">${esc(help)}</div>` : ''}
  </div>`;
}

function formValues(form) {
  const out = {};
  $$('input, select, textarea', form).forEach((el) => {
    if (!el.name || el.type === 'file') return;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'radio') { if (el.checked) out[el.name] = el.value; }
    else out[el.name] = el.value.trim();
  });
  return out;
}

function clearFieldErrors(form) {
  $$('.field .error', form).forEach((e) => e.remove());
  $$('[aria-invalid="true"]', form).forEach((e) => { e.removeAttribute('aria-invalid'); });
  const alert = formAlertHost(form);
  if (alert) alert.innerHTML = '';
}

function formAlertHost(form) {
  return $('[data-form-alert]', form) || (form.closest('.modal') && $('[data-form-alert]', form.closest('.modal')));
}

function setFormAlert(form, message, kind = 'error') {
  const host = formAlertHost(form);
  if (host) { host.innerHTML = message ? alertBox(kind, esc(message)) : ''; if (message) host.scrollIntoView({ block: 'nearest' }); }
  else if (message) toast(message, kind === 'error' ? 'error' : 'success');
}

function setFieldErrors(form, fields) {
  let first = null;
  const unplaced = [];
  Object.entries(fields || {}).forEach(([name, message]) => {
    const el = $(`[name="${CSS.escape(name)}"]`, form);
    const container = el ? el.closest('.field') || el.closest('.checkbox-field') : null;
    if (!container) { unplaced.push(message); return; }
    el.setAttribute('aria-invalid', 'true');
    const errId = `err-${name}`;
    el.setAttribute('aria-describedby', errId);
    container.insertAdjacentHTML('beforeend', `<div class="error" id="${errId}">${icon('error')}<span>${esc(message)}</span></div>`);
    if (!first) first = el;
  });
  if (unplaced.length) setFormAlert(form, unplaced.join(' '));
  if (first) first.focus({ preventScroll: false });
}

function clientValidate(form) {
  const errors = {};
  $$('input, select, textarea', form).forEach((el) => {
    if (!el.name || el.disabled) return;
    const label = el.dataset.label || el.name;
    if (el.type === 'checkbox') {
      if (el.required && !el.checked) errors[el.name] = el.dataset.requiredMsg || `${label} is required.`;
      return;
    }
    if (el.type === 'file') {
      if (el.required && !(el.files && el.files.length)) errors[el.name] = el.dataset.requiredMsg || 'Choose a file.';
      return;
    }
    const v = String(el.value || '').trim();
    if (!v) { if (el.required) errors[el.name] = el.dataset.requiredMsg || `${label} is required.`; return; }
    if (el.dataset.pattern && !new RegExp(`^(?:${el.dataset.pattern})$`).test(v)) {
      errors[el.name] = el.dataset.patternMsg || `${label} is not valid.`;
    } else if (el.type === 'email' && !/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(v)) {
      errors[el.name] = 'Enter a valid email address.';
    } else if (el.type === 'number') {
      const n = Number(v);
      if (Number.isNaN(n)) errors[el.name] = `${label} must be a number.`;
      else if (el.dataset.min !== undefined && n <= Number(el.dataset.min)) errors[el.name] = `${label} must be more than ${el.dataset.min}.`;
      else if (el.min !== '' && n < Number(el.min)) errors[el.name] = `${label} must be at least ${el.min}.`;
      else if (el.max !== '' && n > Number(el.max)) errors[el.name] = `${label} must be at most ${el.max}.`;
    } else if (el.minLength > 0 && v.length < el.minLength) {
      errors[el.name] = `${label} must be at least ${el.minLength} characters.`;
    } else if (el.type === 'date') {
      if (el.max && v > el.max) errors[el.name] = el.dataset.maxMsg || `${label} cannot be after ${fmtDate(el.max)}.`;
      else if (el.min && v < el.min) errors[el.name] = el.dataset.minMsg || `${label} cannot be before ${fmtDate(el.min)}.`;
    }
  });
  return errors;
}

async function submitWith(form, button, fn, extraValidate) {
  clearFieldErrors(form);
  const errors = clientValidate(form);
  if (extraValidate) Object.assign(errors, extraValidate(formValues(form)) || {});
  Object.keys(errors).forEach((k) => { if (!errors[k]) delete errors[k]; });
  if (Object.keys(errors).length) {
    setFieldErrors(form, errors);
    setFormAlert(form, 'Please correct the highlighted fields.');
    return undefined;
  }
  setBusy(button, true);
  try {
    return await fn(formValues(form));
  } catch (err) {
    if (err.fields && Object.keys(err.fields).length) setFieldErrors(form, err.fields);
    if (err.code !== 'UNAUTHENTICATED') setFormAlert(form, err.message);
    return undefined;
  } finally {
    setBusy(button, false);
  }
}

/* ----------------------------------------------------------------- router */

const NAV = [
  { group: 'Workspace' },
  { path: '/portal', label: 'Overview', icon: 'home' },
  { path: '/portal/id-card', label: 'Farmer ID card', icon: 'idcard' },
  { group: 'My records' },
  { path: '/portal/profile', label: 'My profile', icon: 'user' },
  { path: '/portal/farm', label: 'My farm', icon: 'map' },
  { path: '/portal/crops', label: 'Crop management', icon: 'sprout' },
  { path: '/portal/documents', label: 'Documents', icon: 'file' },
  { group: 'Updates' },
  { path: '/portal/notifications', label: 'Notifications', icon: 'bell', count: true },
];

const PAGES = {
  '/portal': ['Overview', viewOverview],
  '/portal/overview': ['Overview', viewOverview],
  '/portal/id-card': ['Farmer ID card', viewCard],
  '/portal/profile': ['My profile', viewProfile],
  '/portal/farm': ['My farm', viewFarm],
  '/portal/crops': ['Crop management', viewCrops],
  '/portal/documents': ['Documents', viewDocuments],
  '/portal/notifications': ['Notifications', viewNotifications],
};

function navigate(href, { replace = false } = {}) {
  const url = new URL(href, location.origin);
  const target = url.pathname + url.search + url.hash;
  if (target !== location.pathname + location.search + location.hash) history[replace ? 'replaceState' : 'pushState'](null, '', target);
  render();
}

function signedOut(message) {
  S.farmer = null;
  S.unread = 0;
  if (message) toast(message, 'error');
  navigate('/portal/login', { replace: true });
}

async function ensureMeta() {
  if (S.meta) return S.meta;
  try { S.meta = await api('/api/portal/meta'); } catch { S.meta = null; }
  return S.meta;
}

async function ensureSession() {
  if (S.farmer !== undefined) return S.farmer;
  try {
    const r = await api('/api/portal/session');
    S.farmer = r.farmer; S.unread = r.unread_notifications || 0;
  } catch (err) {
    if (err.status === 401) S.farmer = null;
    else throw err;
  }
  return S.farmer;
}

async function render() {
  const token = ++S.renderToken;
  $('#overlay-root').innerHTML = '';
  document.body.style.overflow = '';
  const path = location.pathname.replace(/\/+$/, '') || '/portal';

  if (path.startsWith('/verify/')) { await ensureMeta(); return viewVerify(decodeURIComponent(path.slice('/verify/'.length)), token); }

  try {
    await Promise.all([ensureMeta(), ensureSession()]);
  } catch (err) {
    $('#root').innerHTML = `<div class="auth-main" style="min-height:100vh">${errorState(err, 'Reload')}</div>`;
    $('[data-retry]').addEventListener('click', () => location.reload());
    return undefined;
  }
  if (token !== S.renderToken) return undefined;

  if (path === '/portal/login' || path === '/portal/register') {
    if (S.farmer) return navigate(S.farmer.must_change_password ? '/portal/change-password' : '/portal', { replace: true });
    document.title = path === '/portal/login' ? 'Sign in · AI Krishi Farmer Portal' : 'Register · AI Krishi Farmer Portal';
    return path === '/portal/login' ? viewLogin() : viewRegister();
  }
  if (!S.farmer) {
    const next = path === '/portal' ? '' : `?next=${encodeURIComponent(path + location.search)}`;
    return navigate(`/portal/login${next}`, { replace: true });
  }
  if (S.farmer.must_change_password) {
    if (path !== '/portal/change-password') return navigate('/portal/change-password', { replace: true });
    document.title = 'Set a new password · AI Krishi Farmer Portal';
    return viewChangePassword();
  }
  if (path === '/portal/change-password') return navigate('/portal', { replace: true });

  const [title, view] = PAGES[path] || ['Page not found', viewNotFound];
  document.title = `${title} · AI Krishi Farmer Portal`;
  mountShell();
  updateShell(path, title);
  const main = $('#main');
  main.innerHTML = pageSkeleton();
  window.scrollTo(0, 0);
  const alive = () => token === S.renderToken;
  try {
    await view(main, alive);
  } catch (err) {
    if (!alive() || err.code === 'UNAUTHENTICATED' || err.code === 'PASSWORD_CHANGE_REQUIRED') return undefined;
    console.error(err);
    main.innerHTML = errorState(err);
    $('[data-retry]', main).addEventListener('click', () => render());
  }
  if (location.hash && alive()) {
    const target = document.getElementById(location.hash.slice(1));
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  return undefined;
}

/* ------------------------------------------------------------------ shell */

function mountShell() {
  if ($('#shell')) return;
  const helpline = S.meta && S.meta.helpline;
  $('#root').innerHTML = `
  <div class="shell" id="shell">
    <aside class="sidebar" id="sidebar" aria-label="Portal navigation">
      <div class="sidebar-head">${brand()}</div>
      <nav class="sidebar-nav">
        ${NAV.map((n) => n.group
          ? `<div class="nav-group-label">${esc(n.group)}</div>`
          : `<a class="nav-item" href="${n.path}" data-link data-path="${n.path}">${icon(n.icon)}<span>${esc(n.label)}</span>${n.count ? '<span class="count" data-unread hidden></span>' : ''}</a>`).join('')}
      </nav>
      <div class="sidebar-foot">
        <div class="help-card"><strong>Need help?</strong>${helpline
          ? `Call the AI Krishi helpline<br><a href="tel:${esc(helpline)}">${esc(fmtMobile(helpline))}</a>`
          : 'Contact your nearest Raitha Samparka Kendra.'}</div>
      </div>
    </aside>
    <div class="scrim" id="scrim" hidden></div>
    <div class="main-area">
      <header class="topbar">
        <button class="icon-btn menu-btn" type="button" id="menu-btn" aria-label="Open navigation" aria-controls="sidebar" aria-expanded="false">${icon('menu')}</button>
        <div class="topbar-title"><div class="crumbs">Farmer Portal</div><h1 id="page-title">Overview</h1></div>
        <div class="topbar-actions">
          <button class="btn sm call-now-btn" type="button" data-call-now>${icon('phone')}<span>Call now</span></button>
          <a class="icon-btn" href="/portal/notifications" data-link aria-label="Notifications">${icon('bell')}<span class="pip" data-unread hidden></span></a>
          <div class="user-menu">
            <button class="user-btn" type="button" id="user-btn" aria-haspopup="menu" aria-expanded="false">
              <span id="user-avatar"></span>
              <span class="who"><strong id="user-name"></strong><span id="user-fid"></span></span>
              ${icon('chevronDown')}
            </button>
            <div class="dropdown" id="user-dropdown" role="menu" hidden>
              <div class="dropdown-head"><strong id="dd-name"></strong><span id="dd-login"></span></div>
              <a href="/portal/profile" data-link role="menuitem">${icon('user')}My profile</a>
              <a href="/portal/id-card" data-link role="menuitem">${icon('idcard')}Farmer ID card</a>
              <a href="/portal/profile#security" data-link role="menuitem">${icon('lock')}Change password</a>
              <div class="sep"></div>
              <button type="button" class="danger" id="logout-btn" role="menuitem">${icon('logout')}Sign out</button>
            </div>
          </div>
        </div>
      </header>
      <main class="content" id="main" tabindex="-1"></main>
      <footer class="site-foot"><span>AI Krishi Farmer Registry</span><span><a href="/">AI Krishi home</a></span></footer>
    </div>
  </div>`;

  const shell = $('#shell');
  const setNav = (open) => {
    shell.classList.toggle('nav-open', open);
    $('#scrim').hidden = !open;
    $('#menu-btn').setAttribute('aria-expanded', String(open));
  };
  $('#menu-btn').addEventListener('click', () => setNav(!shell.classList.contains('nav-open')));
  $('#scrim').addEventListener('click', () => setNav(false));
  $$('.nav-item', shell).forEach((a) => a.addEventListener('click', () => setNav(false)));

  const dd = $('#user-dropdown');
  const setMenu = (open) => { dd.hidden = !open; $('#user-btn').setAttribute('aria-expanded', String(open)); };
  $('#user-btn').addEventListener('click', (e) => { e.stopPropagation(); setMenu(dd.hidden); });
  document.addEventListener('click', (e) => { if (!dd.hidden && !e.target.closest('.user-menu')) setMenu(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !dd.hidden) setMenu(false); });
  $$('a', dd).forEach((a) => a.addEventListener('click', () => setMenu(false)));
  $('#logout-btn').addEventListener('click', async (e) => {
    setMenu(false);
    const ok = await confirmDialog({ title: 'Sign out?', message: 'You will need your login ID and password to sign in again.', confirmLabel: 'Sign out', danger: false });
    if (!ok) return;
    setBusy(e.currentTarget, true);
    try { await api('/api/portal/logout', { method: 'POST' }); } catch { /* signed out either way */ }
    S.farmer = null; S.unread = 0;
    toast('You have been signed out.');
    navigate('/portal/login', { replace: true });
  });
}

function updateShell(path, title) {
  const f = S.farmer;
  $('#page-title').textContent = title;
  $$('.nav-item').forEach((a) => {
    const active = a.dataset.path === path || (a.dataset.path === '/portal' && path === '/portal/overview');
    if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  $('#user-avatar').innerHTML = avatar(f, 'sm');
  $('#user-name').textContent = f.full_name;
  $('#user-fid').textContent = f.farmer_id;
  $('#dd-name').textContent = f.full_name;
  $('#dd-login').textContent = `Login ID: ${f.login_id}`;
  setUnread(S.unread);
}

function setUnread(n) {
  S.unread = Math.max(0, n || 0);
  $$('[data-unread]').forEach((el) => { el.hidden = !S.unread; el.textContent = S.unread > 99 ? '99+' : String(S.unread); });
}

function pageHead(title, text, actions = '') {
  return `<div class="page-head"><div><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ''}</div>${actions ? `<div class="actions">${actions}</div>` : ''}</div>`;
}

/* ============================================================ AUTH PAGES */

function authLayout(inner, wide = false) {
  $('#root').innerHTML = `
  <div class="auth">
    <aside class="auth-aside">
      ${brand('Farmer Registry')}
      <div>
        <h2>One farmer identity for every farm service.</h2>
        <p class="lead">Register once to get a verifiable Farmer ID card and keep your land, crop and document records in one secure place.</p>
        <ul class="auth-points">
          <li><span class="pt-icon">${icon('idcard')}</span><div><strong>Digital Farmer ID card</strong><span>With a QR code anyone can scan to verify it.</span></div></li>
          <li><span class="pt-icon">${icon('sprout')}</span><div><strong>Farm and crop records</strong><span>Plots, sowing, inputs and harvests in one place.</span></div></li>
          <li><span class="pt-icon">${icon('bell')}</span><div><strong>Alerts that matter</strong><span>Weather warnings, schemes and application updates.</span></div></li>
        </ul>
      </div>
      <p class="fine">AI Krishi Farmer Registry. Your details are used only to provide these services.</p>
    </aside>
    <main class="auth-main">
      <div class="auth-card ${wide ? 'wide' : ''}">
        <div class="auth-mobile-brand">${brand('Farmer Registry')}</div>
        ${inner}
      </div>
    </main>
  </div>`;
}

function viewLogin() {
  const params = new URLSearchParams(location.search);
  authLayout(`
    <div class="card">
      <div class="auth-head"><h1>Sign in</h1><p>Use your Farmer ID, login ID or registered mobile number.</p></div>
      <form id="login-form" class="stack-16" novalidate>
        <div data-form-alert></div>
        ${field({ name: 'identifier', label: 'Farmer ID, login ID or mobile', required: true, value: params.get('id') || '', placeholder: 'e.g. KA-KLB-26-00001-7', attrs: 'autocomplete="username" autocapitalize="none" spellcheck="false"' })}
        ${field({ name: 'password', label: 'Password', type: 'password', required: true, attrs: 'autocomplete="current-password"' })}
        <div class="form-actions between"><span></span><button type="button" class="link-btn" id="forgot-btn">Forgot password?</button></div>
        <button class="btn lg block" type="submit">Sign in</button>
      </form>
    </div>
    <p class="auth-foot">New to AI Krishi? <a href="/portal/register" data-link>Register as a farmer</a></p>`);

  const form = $('#login-form');
  if (params.get('id')) $('#f-password').focus(); else $('#f-identifier').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[type="submit"]', form), (v) => api('/api/portal/login', { method: 'POST', body: v }));
    if (!r) return;
    S.farmer = r.farmer;
    S.unread = 0;
    const next = params.get('next');
    const safeNext = next && next.startsWith('/portal/') && !next.startsWith('//') ? next : '/portal';
    navigate(r.farmer.must_change_password ? '/portal/change-password' : safeNext, { replace: true });
    if (!r.farmer.must_change_password) toast(`Welcome back, ${r.farmer.full_name}.`);
  });
  $('#forgot-btn').addEventListener('click', () => {
    const helpline = S.meta && S.meta.helpline;
    openModal({
      title: 'Reset your password', size: 'sm',
      body: `<p class="text-2">For your security, passwords are reset only by the AI Krishi registry office after confirming your identity.</p>
        <p class="text-2" style="margin-top:12px">${helpline ? `Call the helpline on <a href="tel:${esc(helpline)}"><strong>${esc(fmtMobile(helpline))}</strong></a> from your registered mobile number` : 'Contact your nearest Raitha Samparka Kendra'} and keep your Farmer ID ready. You will receive a new temporary password.</p>`,
      footer: '<button class="btn" type="button" data-close>OK</button>',
    });
  });
}

function stateOptions() {
  return ((S.meta && S.meta.states) || ['Karnataka']).map((s) => [s, s]);
}

function viewRegister() {
  if (S.justRegistered) return viewCredentials(S.justRegistered);
  authLayout(`
    <div class="card">
      <div class="auth-head"><h1>Register as a farmer</h1><p>Takes about two minutes. Your Farmer ID and login details are created as soon as you submit.</p></div>
      <form id="reg-form" novalidate>
        <div data-form-alert style="margin-bottom:16px"></div>
        <div class="form-section">
          <div class="form-section-title">Personal details</div>
          <div class="form-section-desc">Enter your name as it appears on your land records.</div>
          <div class="form-grid">
            ${field({ name: 'full_name', label: 'Full name', required: true, span: 'span-2', placeholder: 'e.g. Ramesh Basappa Patil', attrs: 'autocomplete="name" minlength="2" maxlength="80"' })}
            ${field({ name: 'mobile', label: 'Mobile number', type: 'tel', required: true, prefix: '+91', placeholder: '98765 43210', help: 'Used to sign in and to contact you. One account per number.', attrs: 'inputmode="numeric" autocomplete="tel-national" maxlength="11" data-pattern="[6-9]\\d{4} ?\\d{5}" data-pattern-msg="Enter a valid 10-digit mobile number starting with 6, 7, 8 or 9."' })}
          </div>
        </div>
        <div class="form-section">
          <div class="form-section-title">Address</div>
          <div class="form-section-desc">Where you live and farm. This is printed on your Farmer ID card.</div>
          <div class="form-grid">
            ${field({ name: 'village', label: 'Village', required: true, attrs: 'minlength="2" maxlength="60"' })}
            ${field({ name: 'taluk', label: 'Taluk', optional: true, attrs: 'maxlength="60"' })}
            ${field({ name: 'district', label: 'District', required: true, attrs: 'minlength="2" maxlength="60"' })}
            ${field({ name: 'state', label: 'State', type: 'select', required: true, options: stateOptions(), value: 'Karnataka', placeholder: 'Select state' })}
            ${field({ name: 'pincode', label: 'PIN code', optional: true, attrs: 'inputmode="numeric" maxlength="6" data-pattern="[1-9]\\d{5}" data-pattern-msg="Enter a 6-digit PIN code."' })}
            ${field({ name: 'declared_land_acres', label: 'Total land', type: 'number', optional: true, placeholder: 'In acres', help: 'You can add each plot after signing in.', attrs: 'step="0.01" max="1000" data-min="0" inputmode="decimal"' })}
          </div>
        </div>
        <div class="form-section">
          <div class="field checkbox-field">
            <label class="checkbox"><input type="checkbox" name="consent" required data-label="Consent" data-required-msg="Confirm that the details are correct to continue.">
            <span>I confirm that these details are correct and belong to me, and I agree that AI Krishi may use them to provide farmer services.</span></label>
          </div>
          <div class="form-actions" style="margin-top:20px"><a class="btn ghost" href="/portal/login" data-link>Cancel</a><button class="btn lg" type="submit">Create account</button></div>
        </div>
      </form>
    </div>
    <p class="auth-foot">Already registered? <a href="/portal/login" data-link>Sign in</a></p>`, true);

  const form = $('#reg-form');
  $('#f-full_name').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[type="submit"]', form), (v) => api('/api/portal/register', {
      method: 'POST', body: { ...v, mobile: v.mobile.replace(/\s/g, '') },
    }));
    if (!r) return;
    S.justRegistered = r;
    viewCredentials(r);
  });
}

function viewCredentials(r) {
  const c = r.credentials;
  authLayout(`
    <div class="card">
      <div class="auth-head">
        <div class="confirm-icon neutral" style="background:var(--primary-50);color:var(--primary)">${icon('check')}</div>
        <h1>Registration complete</h1>
        <p>Welcome, ${esc(r.farmer.full_name)}. Your Farmer ID has been issued and your digital ID card is ready.</p>
      </div>
      <div class="cred-list">
        ${[['Farmer ID', c.farmer_id], ['Login ID', c.login_id], ['Temporary password', c.temporary_password]].map(([label, value]) => `
          <div class="cred"><div class="grow"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>
            <button class="btn secondary sm" type="button" data-copy="${esc(value)}" aria-label="Copy ${esc(label)}">${icon('copy')}Copy</button></div>`).join('')}
      </div>
      <div style="margin-top:16px">${alertBox('warning', 'The temporary password is shown only once. Write these details down or print this page now. You will set your own password when you first sign in.', 'Save these details.')}</div>
      <div class="form-actions" style="margin-top:24px">
        <button class="btn secondary" type="button" id="print-cred">${icon('printer')}Print</button>
        <button class="btn" type="button" id="to-login">Continue to sign in${icon('chevronRight')}</button>
      </div>
    </div>`);
  $('#to-login').addEventListener('click', () => { S.justRegistered = null; navigate(`/portal/login?id=${encodeURIComponent(c.login_id)}`); });
  $('#print-cred').addEventListener('click', () => {
    const sheet = document.createElement('div');
    sheet.className = 'print-cred-root print-only';
    sheet.innerHTML = `<h2 style="margin:0 0 4mm">AI Krishi Farmer Registry: login details</h2>
      <p style="margin:0 0 6mm">Farmer: ${esc(r.farmer.full_name)} · Mobile: ${esc(fmtMobile(r.farmer.mobile))} · Registered: ${esc(fmtDate(r.farmer.registered_at))}</p>
      <table style="border-collapse:collapse;font-size:12pt">${[['Farmer ID', c.farmer_id], ['Login ID', c.login_id], ['Temporary password', c.temporary_password]].map(([l, v]) => `<tr><td style="padding:2mm 8mm 2mm 0;color:#555">${esc(l)}</td><td style="padding:2mm 0;font-family:monospace;font-size:13pt"><b>${esc(v)}</b></td></tr>`).join('')}</table>
      <p style="margin-top:8mm;color:#555">Keep this sheet private. Sign in at ${esc(location.origin)}/portal and set your own password.</p>`;
    $('#root').appendChild(sheet);
    printWith('printing-credentials', () => sheet.remove());
  });
}

function passwordRules(pw, confirm, mobile) {
  const digits = String(mobile || '').replace(/\D/g, '').slice(-10);
  return [
    ['At least 8 characters', pw.length >= 8],
    ['At least one letter and one number', /[A-Za-z]/.test(pw) && /\d/.test(pw)],
    ['Does not contain your mobile number', !!pw && !(digits && pw.includes(digits))],
    ['Both new passwords match', !!pw && pw === confirm],
  ];
}

function passwordForm(forced) {
  return `
    ${field({ name: 'current_password', label: forced ? 'Temporary password' : 'Current password', type: 'password', required: true, attrs: 'autocomplete="current-password"' })}
    ${field({ name: 'new_password', label: 'New password', type: 'password', required: true, attrs: 'autocomplete="new-password" maxlength="128"' })}
    ${field({ name: 'confirm_password', label: 'Confirm new password', type: 'password', required: true, attrs: 'autocomplete="new-password" maxlength="128"' })}
    <ul class="pw-rules" id="pw-rules" aria-live="polite"></ul>`;
}

function bindPasswordRules(form) {
  const update = () => {
    const v = formValues(form);
    $('#pw-rules', form).innerHTML = passwordRules(v.new_password || '', v.confirm_password || '', S.farmer && S.farmer.mobile)
      .map(([label, ok]) => `<li class="${ok ? 'ok' : ''}">${icon(ok ? 'check' : 'info')}${esc(label)}</li>`).join('');
  };
  form.addEventListener('input', update);
  update();
}

function passwordValidate(v) {
  const rules = passwordRules(v.new_password || '', v.confirm_password || '', S.farmer && S.farmer.mobile);
  const errors = {};
  if (v.new_password && rules.slice(0, 3).some(([, ok]) => !ok)) errors.new_password = 'Choose a password that meets the rules below.';
  if (v.confirm_password && v.new_password !== v.confirm_password) errors.confirm_password = 'The passwords do not match.';
  return errors;
}

function viewChangePassword() {
  authLayout(`
    <div class="card">
      <div class="auth-head"><h1>Set your new password</h1><p>You signed in with a temporary password. Choose a password only you know to continue.</p></div>
      <form id="pw-form" class="stack-16" novalidate>
        <div data-form-alert></div>
        ${alertBox('info', `Signed in as <strong>${esc(S.farmer.full_name)}</strong> · <span class="mono">${esc(S.farmer.farmer_id)}</span>`)}
        ${passwordForm(true)}
        <button class="btn lg block" type="submit">Save password and continue</button>
      </form>
    </div>
    <p class="auth-foot">Not you? <button class="link-btn" type="button" id="pw-logout">Sign out</button></p>`);
  const form = $('#pw-form');
  bindPasswordRules(form);
  $('#f-current_password').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[type="submit"]', form), (v) => api('/api/portal/password', {
      method: 'POST', body: { current_password: v.current_password, new_password: v.new_password },
    }), passwordValidate);
    if (!r) return;
    S.farmer = r.farmer;
    toast('Your password has been set.');
    navigate('/portal', { replace: true });
  });
  $('#pw-logout').addEventListener('click', async () => {
    try { await api('/api/portal/logout', { method: 'POST' }); } catch { /* ignore */ }
    S.farmer = null;
    navigate('/portal/login', { replace: true });
  });
}

/* ============================================================== OVERVIEW */

function cropStatusBadge(c) {
  return badge(CROP_BADGE[c.status] ?? '', c.status_label, true);
}

async function viewOverview(main, alive) {
  const d = await api('/api/portal/overview');
  if (!alive()) return;
  S.farmer = d.farmer;
  setUnread(d.stats.unread_notifications);
  updateShell('/portal', 'Overview');
  const f = d.farmer;
  const s = d.stats;
  const pct = Math.round((d.checklist_done / d.checklist.length) * 100);
  const w = d.weather;
  const greeting = (() => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'; })();

  main.innerHTML = `
    <section class="card" style="margin-bottom:20px"><div class="card-body">
      <div class="profile-hero">
        ${avatar(f, 'xl')}
        <div class="grow">
          <div class="muted" style="font-size:13px">${greeting},</div>
          <h2>${esc(f.full_name)}</h2>
          <div class="id-line"><span class="fid">${esc(f.farmer_id)}</span>${verifyBadge(f.verification_status)}</div>
          <div class="meta">
            <span>${icon('pin')}${esc([f.village, f.district, f.state].filter(Boolean).join(', '))}</span>
            <span>${icon('calendar')}Registered ${esc(fmtDate(f.registered_at))}</span>
            ${f.last_login_at ? `<span>${icon('clock')}Last sign-in ${esc(fmtDateTime(f.last_login_at))}</span>` : ''}
          </div>
        </div>
        <div class="page-head" style="margin:0"><div class="actions">
          <a class="btn secondary" href="/portal/id-card" data-link>${icon('idcard')}View ID card</a>
          <a class="btn secondary" href="/portal/crops?add=1" data-link>${icon('plus')}Add crop</a>
          <button class="btn" type="button" data-call-now>${icon('phone')}Call now</button>
        </div></div>
      </div>
    </div></section>

    ${f.verification_status === 'PENDING' ? `<div style="margin-bottom:20px">${alertBox('warning', 'Your details are waiting to be checked by the registry office. Your ID card shows <strong>Pending verification</strong> until then. Uploading a land record helps speed this up.', 'Verification pending.')}</div>` : ''}

    <div class="stats">
      ${stat('green', 'map', 'Total land', `${nf.format(s.total_acres || 0)}<small>acres</small>`, s.land_source === 'plots' ? `Across ${s.plots} plot${s.plots === 1 ? '' : 's'}` : (s.total_acres ? 'Declared at registration' : 'Not recorded yet'))}
      ${stat('blue', 'sprout', 'Current crops', String(s.current_crops), s.current_crops ? `${fmtAcres(s.area_under_crops)} under cultivation` : 'No crops recorded')}
      ${stat('violet', 'file', 'Documents', String(s.documents), 'Including your Farmer ID card')}
      ${stat('amber', 'bell', 'Unread notifications', String(s.unread_notifications), s.unread_notifications ? 'Alerts and updates waiting' : 'You are all caught up')}
    </div>

    <div class="grid main-side" style="margin-top:20px">
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h3>Current crops</h3><p>Planned, sown and growing crops</p></div><div class="actions"><a class="btn secondary sm" href="/portal/crops" data-link>View all</a></div></div>
          <div class="card-body flush">${d.current_crops.length ? `
            <div class="table-wrap"><table class="table stackable">
              <thead><tr><th>Crop</th><th>Plot</th><th class="right">Area</th><th>Sown</th><th>Expected harvest</th><th>Status</th></tr></thead>
              <tbody>${d.current_crops.map((c) => `<tr>
                <td class="cell-title" data-label="Crop"><strong>${esc(c.crop_name)}</strong>${c.variety ? `<span class="sub">${esc(c.variety)} · ${esc(c.season)}</span>` : `<span class="sub">${esc(c.season)}</span>`}</td>
                <td data-label="Plot">${esc(val(c.plot_name))}</td>
                <td class="right num" data-label="Area">${esc(fmtAcres(c.area_acres))}</td>
                <td data-label="Sown">${esc(fmtDate(c.sowing_date))}</td>
                <td data-label="Expected harvest">${esc(fmtDate(c.expected_harvest_date))}</td>
                <td data-label="Status">${cropStatusBadge(c)}</td></tr>`).join('')}</tbody></table></div>`
            : emptyState({ icon: 'sprout', title: 'No current crops', text: 'Record what you are growing this season to track inputs and harvests.', action: '<a class="btn sm" href="/portal/crops?add=1" data-link>Add a crop</a>' })}
          </div>
        </section>

        <section class="card">
          <div class="card-head"><div><h3>Recent activity</h3><p>Changes to your account and helpline calls</p></div></div>
          ${d.recent_activity.length ? `<ul class="timeline">${d.recent_activity.map((a) => `
            <li><span class="t-dot ${a.kind === 'call' ? 'call' : /LOCK|PASSWORD|SUSPEND/.test(a.action) ? 'warn' : ''}"></span>
              <div class="t-text">${esc(a.detail || a.action)}</div><div class="t-time">${esc(timeAgo(a.at))}</div></li>`).join('')}</ul>`
            : emptyState({ icon: 'clock', title: 'No activity yet' })}
        </section>
      </div>

      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h3>Complete your profile</h3><p>${d.checklist_done} of ${d.checklist.length} steps done</p></div><strong class="num">${pct}%</strong></div>
          <div class="card-body">
            <div class="progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Profile completion"><span style="width:${pct}%"></span></div>
            <ul class="checklist" style="margin-top:12px">${d.checklist.map((c) => `
              <li class="${c.done ? 'done' : ''}"><span class="ck">${icon('check')}</span><span class="ck-label">${esc(c.label)}</span>
              ${c.done ? '' : `<a class="btn ghost sm" href="${esc(c.link)}" data-link aria-label="${esc(c.label)}">${icon('chevronRight')}</a>`}</li>`).join('')}</ul>
          </div>
        </section>

        <section class="card">
          <div class="card-head"><div><h3>Weather now</h3><p>${esc(f.district)}</p></div>${w && w.live ? badge('green', 'Live', true) : ''}</div>
          <div class="card-body">${w && w.live ? `
            <div class="weather-now"><div class="stat-icon blue">${icon('cloud')}</div><div><div class="temp">${Math.round(w.temperature)}°C</div><div class="desc">${esc(w.description || '')}</div></div></div>
            <div class="mini-stats">
              <div><span>Humidity</span><strong>${esc(val(w.humidity))}%</strong></div>
              <div><span>Rain (1 h)</span><strong>${esc(nf.format(w.rainfall_mm || 0))} mm</strong></div>
              <div><span>Wind</span><strong>${esc(val(w.wind_kmh))} km/h</strong></div>
            </div>
            <p class="muted" style="font-size:12px;margin-top:12px">Source: OpenWeather · checked ${esc(timeAgo(w.checked_at))}</p>`
            : `<p class="muted">Live weather is not available right now. Weather alerts will appear in notifications when conditions need attention.</p>`}
          </div>
        </section>

        <section class="card">
          <div class="card-head"><div><h3>Latest notifications</h3></div><div class="actions"><a class="btn secondary sm" href="/portal/notifications" data-link>View all</a></div></div>
          ${d.notifications.length ? `<ul class="list">${d.notifications.map((n) => `
            <li class="list-item"><span class="notif-icon ${esc(n.category)}">${icon(NOTIF_ICON[n.category] || 'bell')}</span>
              <div class="grow"><div class="list-title">${esc(n.title)}</div><div class="list-sub">${esc(n.category_label)} · ${esc(timeAgo(n.created_at))}</div></div>
              ${n.read ? '' : '<span class="unread-dot" style="width:8px;height:8px;border-radius:50%;background:var(--primary);margin-top:6px" aria-label="Unread"></span>'}</li>`).join('')}</ul>`
            : emptyState({ icon: 'bell', title: 'No notifications' })}
        </section>
      </div>
    </div>`;
}

function stat(color, ic, label, value, note) {
  return `<div class="stat"><div class="stat-icon ${color}">${icon(ic)}</div><div style="min-width:0"><div class="stat-label">${esc(label)}</div><div class="stat-value">${value}</div><div class="stat-note">${esc(note)}</div></div></div>`;
}

/* =============================================================== PROFILE */

async function viewProfile(main, alive) {
  const { farmer: f } = await api('/api/portal/profile');
  if (!alive()) return;
  S.farmer = f;
  updateShell('/portal/profile', 'My profile');
  const meta = S.meta || {};
  const langs = meta.languages || { kn: 'Kannada', hi: 'Hindi', en: 'English' };

  const viewMode = () => `
    <section class="card">
      <div class="card-head"><div><h3>Personal information</h3></div><div class="actions"><button class="btn secondary sm" type="button" data-edit>${icon('edit')}Edit profile</button></div></div>
      <div class="card-body"><dl class="dl">
        <div><dt>Full name</dt>${dd(f.full_name)}</div>
        <div><dt>Father's or spouse's name</dt>${dd(f.guardian_name)}</div>
        <div><dt>Date of birth</dt>${dd(f.date_of_birth ? fmtDate(f.date_of_birth) : '')}</div>
        <div><dt>Gender</dt>${dd(f.gender)}</div>
      </dl></div>
    </section>
    <section class="card">
      <div class="card-head"><div><h3>Contact information</h3></div></div>
      <div class="card-body"><dl class="dl">
        <div><dt>Registered mobile</dt><dd>${esc(fmtMobile(f.mobile))}</dd></div>
        <div><dt>Alternate mobile</dt>${dd(f.alternate_mobile ? fmtMobile(f.alternate_mobile) : '')}</div>
        <div><dt>Email</dt>${dd(f.email)}</div>
        <div><dt>Preferred language</dt>${dd(langs[f.preferred_language])}</div>
      </dl></div>
    </section>
    <section class="card">
      <div class="card-head"><div><h3>Address</h3></div></div>
      <div class="card-body"><dl class="dl">
        <div><dt>House / street</dt>${dd(f.address_line)}</div>
        <div><dt>Village</dt>${dd(f.village)}</div>
        <div><dt>Taluk</dt>${dd(f.taluk)}</div>
        <div><dt>District</dt>${dd(f.district)}</div>
        <div><dt>State</dt>${dd(f.state)}</div>
        <div><dt>PIN code</dt>${dd(f.pincode)}</div>
        <div><dt>Total land (declared)</dt>${dd(f.declared_land_acres ? fmtAcres(f.declared_land_acres) : '')}</div>
      </dl></div>
    </section>`;

  const editMode = () => `
    <section class="card">
      <div class="card-head"><div><h3>Edit profile</h3><p>Fields marked * are required.</p></div></div>
      <form id="profile-form" novalidate>
        <div class="card-body">
          <div data-form-alert></div>
          ${f.verification_status === 'VERIFIED' ? `<div style="margin-bottom:16px">${alertBox('info', 'Changing your name, village, district or state sends your registration back for verification, because these details are printed on your ID card.')}</div>` : ''}
          <div class="form-section">
            <div class="form-section-title">Personal information</div>
            <div class="form-grid" style="margin-top:12px">
              ${field({ name: 'full_name', label: 'Full name', required: true, value: f.full_name, attrs: 'autocomplete="name" minlength="2" maxlength="80"' })}
              ${field({ name: 'guardian_name', label: "Father's or spouse's name", optional: true, value: f.guardian_name, attrs: 'maxlength="80"' })}
              ${field({ name: 'date_of_birth', label: 'Date of birth', type: 'date', optional: true, value: f.date_of_birth, attrs: `max="${todayISO()}" min="1915-01-01"` })}
              ${field({ name: 'gender', label: 'Gender', type: 'select', optional: true, value: f.gender, options: meta.genders || [], placeholder: 'Select' })}
            </div>
          </div>
          <div class="form-section">
            <div class="form-section-title">Contact information</div>
            <div class="form-grid" style="margin-top:12px">
              <div class="field"><label>Registered mobile</label><input class="input" value="${esc(fmtMobile(f.mobile))}" readonly aria-readonly="true"><div class="help">Contact the registry office to change your registered number.</div></div>
              ${field({ name: 'alternate_mobile', label: 'Alternate mobile', type: 'tel', optional: true, prefix: '+91', value: f.alternate_mobile ? f.alternate_mobile.slice(-10) : '', attrs: 'inputmode="numeric" maxlength="11" data-pattern="[6-9]\\d{4} ?\\d{5}" data-pattern-msg="Enter a valid 10-digit mobile number."' })}
              ${field({ name: 'email', label: 'Email', type: 'email', optional: true, value: f.email, attrs: 'autocomplete="email" maxlength="120"' })}
              ${field({ name: 'preferred_language', label: 'Preferred language', type: 'select', required: true, value: f.preferred_language, options: Object.entries(langs), placeholder: null })}
            </div>
          </div>
          <div class="form-section">
            <div class="form-section-title">Address</div>
            <div class="form-grid" style="margin-top:12px">
              ${field({ name: 'address_line', label: 'House / street', optional: true, span: 'span-2', value: f.address_line, attrs: 'maxlength="160" autocomplete="street-address"' })}
              ${field({ name: 'village', label: 'Village', required: true, value: f.village, attrs: 'minlength="2" maxlength="60"' })}
              ${field({ name: 'taluk', label: 'Taluk', optional: true, value: f.taluk, attrs: 'maxlength="60"' })}
              ${field({ name: 'district', label: 'District', required: true, value: f.district, attrs: 'minlength="2" maxlength="60"' })}
              ${field({ name: 'state', label: 'State', type: 'select', required: true, value: f.state, options: stateOptions(), placeholder: 'Select state' })}
              ${field({ name: 'pincode', label: 'PIN code', optional: true, value: f.pincode, attrs: 'inputmode="numeric" maxlength="6" data-pattern="[1-9]\\d{5}" data-pattern-msg="Enter a 6-digit PIN code."' })}
              ${field({ name: 'declared_land_acres', label: 'Total land (acres)', type: 'number', optional: true, value: f.declared_land_acres, attrs: 'step="0.01" max="1000" data-min="0" inputmode="decimal"' })}
            </div>
          </div>
        </div>
        <div class="card-foot form-actions"><button class="btn secondary" type="button" data-cancel>Cancel</button><button class="btn" type="submit">Save changes</button></div>
      </form>
    </section>`;

  main.innerHTML = `
    ${pageHead('My profile', 'Your personal, contact and address details. Details printed on your ID card are marked on the card page.')}
    <div class="grid side-main">
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h3>Profile photograph</h3><p>Printed on your Farmer ID card</p></div></div>
          <div class="card-body">
            <div class="photo-edit">
              <div class="photo-frame">${f.photo_available ? `<img src="${esc(photoUrl(f))}" alt="Profile photograph of ${esc(f.full_name)}">` : SILHOUETTE}</div>
              <div class="stack-16" style="flex:1;min-width:160px">
                <p class="muted" style="font-size:13px">Use a clear, front-facing photo with a plain background. JPG or PNG, up to 5 MB.</p>
                <div class="form-actions" style="justify-content:flex-start">
                  <label class="btn secondary sm" for="photo-input" tabindex="0" role="button">${icon('camera')}${f.photo_available ? 'Change photo' : 'Upload photo'}</label>
                  ${f.photo_available ? `<button class="btn ghost sm" type="button" id="photo-remove">${icon('trash')}Remove</button>` : ''}
                </div>
                <input type="file" id="photo-input" accept="image/jpeg,image/png" hidden>
              </div>
            </div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><div><h3>Account</h3></div>${verifyBadge(f.verification_status)}</div>
          <div class="card-body"><dl class="dl one">
            <div><dt>Farmer ID</dt><dd class="mono">${esc(f.farmer_id)}</dd></div>
            <div><dt>Login ID</dt><dd class="mono">${esc(f.login_id)}</dd></div>
            <div><dt>Registered on</dt><dd>${esc(fmtDate(f.registered_at))}</dd></div>
            <div><dt>ID card valid until</dt><dd>${esc(fmtDate(f.valid_until))}</dd></div>
          </dl></div>
        </section>
        <section class="card" id="security">
          <div class="card-head"><div><h3>Sign-in and security</h3></div></div>
          <div class="card-body stack-16">
            <dl class="dl one"><div><dt>Last sign-in</dt><dd>${esc(fmtDateTime(f.last_login_at))}</dd></div></dl>
            <button class="btn secondary" type="button" id="change-pw">${icon('lock')}Change password</button>
          </div>
        </section>
      </div>
      <div class="stack" id="profile-main">${viewMode()}</div>
    </div>`;

  const host = $('#profile-main', main);
  const showView = () => { host.innerHTML = viewMode(); $('[data-edit]', host).addEventListener('click', showEdit); };
  function showEdit() {
    host.innerHTML = editMode();
    const form = $('#profile-form', host);
    $('#f-full_name', form).focus();
    $('[data-cancel]', form).addEventListener('click', showView);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const r = await submitWith(form, $('button[type="submit"]', form), (v) => api('/api/portal/profile', {
        method: 'PUT', body: { ...v, alternate_mobile: (v.alternate_mobile || '').replace(/\s/g, '') },
      }));
      if (!r) return;
      const wentPending = f.verification_status === 'VERIFIED' && r.farmer.verification_status === 'PENDING';
      toast(wentPending ? 'Profile saved. Your registration is pending re-verification.' : 'Profile saved.');
      S.farmer = r.farmer;
      render();
    });
  }
  $('[data-edit]', host).addEventListener('click', showEdit);

  $('#photo-input', main).addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (!['image/jpeg', 'image/png'].includes(file.type)) { toast('Choose a JPG or PNG photo.', 'error'); return; }
    if (file.size > 5 * 1024 * 1024) { toast('The photo is larger than 5 MB.', 'error'); return; }
    const label = $('label[for="photo-input"]', main);
    label.innerHTML = '<span class="spinner"></span>Uploading';
    const form = new FormData();
    form.append('photo', file);
    try {
      const r = await api('/api/portal/profile/photo', { method: 'POST', form });
      S.farmer = r.farmer;
      toast('Photograph updated.');
      render();
    } catch (err) {
      toast(err.message, 'error');
      label.innerHTML = `${icon('camera')}${f.photo_available ? 'Change photo' : 'Upload photo'}`;
    }
  });
  $('label[for="photo-input"]', main).addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#photo-input', main).click(); } });

  const removeBtn = $('#photo-remove', main);
  if (removeBtn) removeBtn.addEventListener('click', async () => {
    if (!await confirmDialog({ title: 'Remove photograph?', message: 'Your ID card will show a blank photo area until you upload a new one.', confirmLabel: 'Remove photo' })) return;
    try {
      const r = await api('/api/portal/profile/photo', { method: 'DELETE' });
      S.farmer = r.farmer; toast('Photograph removed.'); render();
    } catch (err) { toast(err.message, 'error'); }
  });

  $('#change-pw', main).addEventListener('click', openPasswordModal);
}

function openPasswordModal() {
  const m = openModal({
    title: 'Change password', description: 'Other devices signed in to your account will be signed out.',
    body: `<form id="pwm-form" class="stack-16" novalidate><div data-form-alert></div>${passwordForm(false)}</form>`,
    footer: '<button class="btn secondary" type="button" data-close>Cancel</button><button class="btn" type="submit" form="pwm-form">Update password</button>',
  });
  const form = $('#pwm-form', m.el);
  bindPasswordRules(form);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[form="pwm-form"]', m.el), (v) => api('/api/portal/password', {
      method: 'POST', body: { current_password: v.current_password, new_password: v.new_password },
    }), passwordValidate);
    if (!r) return;
    S.farmer = r.farmer;
    m.close();
    toast('Password updated.');
  });
}

/* ================================================================== FARM */

async function viewFarm(main, alive) {
  const [farm, cropsRes] = await Promise.all([api('/api/portal/farm'), api('/api/portal/crops')]);
  if (!alive()) return;
  const t = farm.totals;
  const history = cropsRes.crops.filter((c) => !c.is_current);
  const soils = {};
  farm.plots.forEach((p) => { if (p.soil_type) soils[p.soil_type] = (soils[p.soil_type] || 0) + p.area_acres; });
  const ref = farm.soil_reference;

  main.innerHTML = `
    ${pageHead('My farm', 'Your land, plots, soil and irrigation. Record each plot separately so crops can be tracked against it.', `<button class="btn" type="button" id="add-plot">${icon('plus')}Add plot</button>`)}
    <div class="stats">
      ${stat('green', 'map', 'Total land', `${nf.format(t.total_acres)}<small>acres</small>`, t.plots ? `Recorded across ${t.plots} plot${t.plots === 1 ? '' : 's'}` : (t.declared_acres ? `${fmtAcres(t.declared_acres)} declared, no plots yet` : 'No plots recorded'))}
      ${stat('blue', 'layers', 'Plots', String(t.plots), `${fmtAcres(t.in_use_acres)} under current crops`)}
      ${stat('violet', 'drop', 'Irrigated', `${nf.format(t.irrigated_acres)}<small>acres</small>`, t.total_acres ? `${Math.round((t.irrigated_acres / t.total_acres) * 100)}% of recorded land` : '—')}
      ${stat('amber', 'cloud', 'Rainfed', `${nf.format(t.rainfed_acres)}<small>acres</small>`, 'Includes plots with no irrigation recorded')}
    </div>

    <div class="grid main-side" style="margin-top:20px">
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h3>Plots</h3><p>Individual pieces of land you farm</p></div></div>
          <div class="card-body flush">${farm.plots.length ? `
            <div class="table-wrap"><table class="table stackable table-min">
              <thead><tr><th>Plot</th><th class="right">Area</th><th>Ownership</th><th>Soil</th><th>Irrigation</th><th>Location</th><th class="right"><span class="sr-only">Actions</span></th></tr></thead>
              <tbody>${farm.plots.map((p) => `<tr>
                <td class="cell-title" data-label="Plot"><strong>${esc(p.name)}</strong><span class="sub">${p.survey_number ? `Survey no. ${esc(p.survey_number)}` : 'No survey number'}</span></td>
                <td class="right num" data-label="Area">${esc(fmtAcres(p.area_acres))}${p.area_in_use ? `<span class="sub">${esc(fmtAcres(p.area_in_use))} in use</span>` : ''}</td>
                <td data-label="Ownership">${esc(p.ownership_label)}</td>
                <td data-label="Soil">${esc(val(p.soil_type))}</td>
                <td data-label="Irrigation">${esc(val(p.irrigation_type))}</td>
                <td data-label="Location">${esc(val(p.village))}${p.latitude !== null && p.latitude !== undefined ? `<span class="sub"><a href="https://www.openstreetmap.org/?mlat=${p.latitude}&mlon=${p.longitude}#map=16/${p.latitude}/${p.longitude}" target="_blank" rel="noopener">View on map</a></span>` : ''}</td>
                <td class="cell-actions" data-label=""><div class="row-actions">
                  <button class="btn ghost icon sm" type="button" data-edit-plot="${esc(p.id)}" aria-label="Edit ${esc(p.name)}" title="Edit">${icon('edit')}</button>
                  <button class="btn ghost icon sm" type="button" data-del-plot="${esc(p.id)}" aria-label="Delete ${esc(p.name)}" title="Delete">${icon('trash')}</button>
                </div></td></tr>`).join('')}</tbody></table></div>`
            : emptyState({ icon: 'map', title: 'No plots recorded', text: 'Add each piece of land with its survey number, area, soil and irrigation.', action: `<button class="btn sm" type="button" data-add-plot>${icon('plus')}Add your first plot</button>` })}
          </div>
        </section>

        <section class="card">
          <div class="card-head"><div><h3>Crop history</h3><p>Harvested and failed crops</p></div><div class="actions"><a class="btn secondary sm" href="/portal/crops" data-link>Crop management</a></div></div>
          <div class="card-body flush">${history.length ? `
            <div class="table-wrap"><table class="table stackable">
              <thead><tr><th>Crop</th><th>Season</th><th>Plot</th><th class="right">Area</th><th>Sown</th><th>Harvest</th><th>Status</th></tr></thead>
              <tbody>${history.map((c) => `<tr>
                <td class="cell-title" data-label="Crop"><strong>${esc(c.crop_name)}</strong>${c.variety ? `<span class="sub">${esc(c.variety)}</span>` : ''}</td>
                <td data-label="Season">${esc(c.season)}</td>
                <td data-label="Plot">${esc(val(c.plot_name))}</td>
                <td class="right num" data-label="Area">${esc(fmtAcres(c.area_acres))}</td>
                <td data-label="Sown">${esc(fmtDate(c.sowing_date))}</td>
                <td data-label="Harvest">${esc(harvestTotal(c))}</td>
                <td data-label="Status">${cropStatusBadge(c)}</td></tr>`).join('')}</tbody></table></div>`
            : emptyState({ icon: 'harvest', title: 'No crop history yet', text: 'Crops you mark as harvested or failed appear here.' })}
          </div>
        </section>
      </div>

      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h3>Land location</h3><p>From your registered address</p></div></div>
          <div class="card-body"><dl class="dl">
            <div><dt>Village</dt>${dd(farm.location.village)}</div>
            <div><dt>Taluk</dt>${dd(farm.location.taluk)}</div>
            <div><dt>District</dt>${dd(farm.location.district)}</div>
            <div><dt>State</dt>${dd(farm.location.state)}</div>
            <div><dt>PIN code</dt>${dd(farm.location.pincode)}</div>
          </dl></div>
        </section>

        <section class="card">
          <div class="card-head"><div><h3>Soil information</h3></div></div>
          <div class="card-body stack-16">
            <div>
              <div class="field-label" style="margin-bottom:8px">Recorded on your plots</div>
              ${Object.keys(soils).length ? `<ul class="list">${Object.entries(soils).map(([k, a]) => `<li style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--border)"><span>${esc(k)}</span><strong class="num">${esc(fmtAcres(a))}</strong></li>`).join('')}</ul>`
                : '<p class="muted" style="font-size:13px">No soil type recorded yet. Add it when you add or edit a plot.</p>'}
            </div>
            <div style="padding-top:16px;border-top:1px solid var(--border)">
              <div class="field-label" style="margin-bottom:8px">District reference: ${esc(val(ref.region))}</div>
              <dl class="dl one">
                <div><dt>Typical soil</dt>${dd(ref.soil_type)}</div>
                <div><dt>pH range</dt>${dd(ref.ph_range)}</div>
                <div><dt>Characteristics</dt>${dd(ref.characteristics)}</div>
              </dl>
              ${ref.suitable_crops.length ? `<div class="chips" style="margin-top:12px">${ref.suitable_crops.map((c) => badge('green', c)).join('')}</div>` : ''}
              <p class="muted" style="font-size:12px;margin-top:12px">${esc(ref.source)}. A Soil Health Card test gives exact values for your field.</p>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-head"><div><h3>Irrigation and ownership</h3></div></div>
          <div class="card-body"><dl class="dl">
            <div><dt>Irrigated</dt><dd class="num">${esc(fmtAcres(t.irrigated_acres))}</dd></div>
            <div><dt>Rainfed</dt><dd class="num">${esc(fmtAcres(t.rainfed_acres))}</dd></div>
            ${Object.entries((S.meta && S.meta.ownership) || {}).map(([k, label]) => `<div><dt>${esc(label)}</dt><dd class="num">${esc(fmtAcres(t.by_ownership[k] || 0))}</dd></div>`).join('')}
          </dl></div>
        </section>
      </div>
    </div>`;

  const byId = Object.fromEntries(farm.plots.map((p) => [p.id, p]));
  $$('#add-plot, [data-add-plot]', main).forEach((b) => b.addEventListener('click', () => openPlotModal()));
  $$('[data-edit-plot]', main).forEach((b) => b.addEventListener('click', () => openPlotModal(byId[b.dataset.editPlot])));
  $$('[data-del-plot]', main).forEach((b) => b.addEventListener('click', async () => {
    const p = byId[b.dataset.delPlot];
    if (!await confirmDialog({ title: `Delete ${p.name}?`, message: 'The plot will be removed from your farm records. Past crops on it are kept but no longer linked to a plot.', confirmLabel: 'Delete plot' })) return;
    try { await api(`/api/portal/plots/${p.id}`, { method: 'DELETE' }); toast('Plot deleted.'); render(); }
    catch (err) { toast(err.message, 'error'); }
  }));
}

function openPlotModal(p = null) {
  const meta = S.meta || {};
  const f = S.farmer;
  const m = openModal({
    title: p ? 'Edit plot' : 'Add plot', size: 'lg',
    description: p ? p.name : 'Record one piece of land. Survey number is on your RTC / Pahani.',
    body: `<form id="plot-form" novalidate>
      <div data-form-alert style="margin-bottom:12px"></div>
      <div class="form-grid">
        ${field({ name: 'name', label: 'Plot name', required: true, value: p && p.name, placeholder: 'e.g. Near canal field', attrs: 'minlength="2" maxlength="60"' })}
        ${field({ name: 'survey_number', label: 'Survey number', optional: true, value: p && p.survey_number, placeholder: 'e.g. 124/2A', attrs: 'maxlength="40"' })}
        ${field({ name: 'area_acres', label: 'Area (acres)', type: 'number', required: true, value: p && p.area_acres, attrs: 'step="0.01" max="1000" data-min="0" inputmode="decimal"' })}
        ${field({ name: 'ownership', label: 'Ownership', type: 'select', required: true, value: p ? p.ownership : 'OWNED', options: Object.entries(meta.ownership || { OWNED: 'Owned' }), placeholder: null })}
        ${field({ name: 'soil_type', label: 'Soil type', type: 'select', optional: true, value: p && p.soil_type, options: meta.soil_types || [], placeholder: 'Select soil type' })}
        ${field({ name: 'irrigation_type', label: 'Irrigation', type: 'select', optional: true, value: p && p.irrigation_type, options: meta.irrigation_types || [], placeholder: 'Select irrigation' })}
      </div>
      <div class="form-section">
        <div class="form-section-title">Location</div>
        <div class="form-section-desc">Leave blank to use your registered village and district.</div>
        <div class="form-grid three">
          ${field({ name: 'village', label: 'Village', optional: true, value: p && p.village, placeholder: f.village, attrs: 'maxlength="60"' })}
          ${field({ name: 'taluk', label: 'Taluk', optional: true, value: p && p.taluk, placeholder: f.taluk || '', attrs: 'maxlength="60"' })}
          ${field({ name: 'district', label: 'District', optional: true, value: p && p.district, placeholder: f.district, attrs: 'maxlength="60"' })}
          ${field({ name: 'latitude', label: 'Latitude', type: 'number', optional: true, value: p && p.latitude, attrs: 'step="0.000001" min="-90" max="90" inputmode="decimal"' })}
          ${field({ name: 'longitude', label: 'Longitude', type: 'number', optional: true, value: p && p.longitude, attrs: 'step="0.000001" min="-180" max="180" inputmode="decimal"' })}
          <div class="field"><span class="field-label">&nbsp;</span><button class="btn secondary" type="button" id="use-location">${icon('location')}Use my location</button></div>
        </div>
      </div>
    </form>`,
    footer: `<button class="btn secondary" type="button" data-close>Cancel</button><button class="btn" type="submit" form="plot-form">${p ? 'Save plot' : 'Add plot'}</button>`,
  });
  const form = $('#plot-form', m.el);
  $('#use-location', m.el).addEventListener('click', (e) => {
    const btn = e.currentTarget;
    if (!navigator.geolocation) { toast('Location is not available on this device.', 'error'); return; }
    setBusy(btn, true, 'Locating');
    navigator.geolocation.getCurrentPosition((pos) => {
      $('#f-latitude', form).value = pos.coords.latitude.toFixed(6);
      $('#f-longitude', form).value = pos.coords.longitude.toFixed(6);
      setBusy(btn, false);
      toast('Location added. Stand inside the plot for the best accuracy.');
    }, () => { setBusy(btn, false); toast('Could not get your location. Check location permission.', 'error'); }, { enableHighAccuracy: true, timeout: 12000 });
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[form="plot-form"]', m.el),
      (v) => api(p ? `/api/portal/plots/${p.id}` : '/api/portal/plots', { method: p ? 'PUT' : 'POST', body: v }),
      (v) => ((v.latitude === '') !== (v.longitude === '') ? { [v.latitude === '' ? 'latitude' : 'longitude']: 'Enter both latitude and longitude, or neither.' } : {}));
    if (!r) return;
    m.close();
    toast(p ? 'Plot updated.' : 'Plot added.');
    render();
  });
}

function harvestTotal(c) {
  if (!c.harvests.length) return '—';
  const byUnit = {};
  c.harvests.forEach((h) => { byUnit[h.unit] = (byUnit[h.unit] || 0) + h.quantity; });
  return Object.entries(byUnit).map(([u, q]) => `${nf.format(q)} ${u}`).join(', ');
}

/* ================================================================= CROPS */

async function viewCrops(main, alive) {
  const [cropsRes, farm] = await Promise.all([api('/api/portal/crops'), api('/api/portal/farm')]);
  if (!alive()) return;
  const params = new URLSearchParams(location.search);
  const crops = cropsRes.crops;
  const tabs = [['current', 'Current', crops.filter((c) => c.is_current)], ['history', 'History', crops.filter((c) => !c.is_current)], ['all', 'All crops', crops]];
  let active = ['current', 'history', 'all'].includes(params.get('tab')) ? params.get('tab') : 'current';
  const expanded = new Set();

  main.innerHTML = `
    ${pageHead('Crop management', 'Plan, track and close out each crop: dates, area, inputs and harvest records.', `<button class="btn" type="button" id="add-crop">${icon('plus')}Add crop</button>`)}
    <div class="tabs" role="tablist" aria-label="Crop views">${tabs.map(([k, label, list]) => `<button class="tab" role="tab" type="button" data-tab="${k}" aria-selected="${k === active}">${esc(label)}<span class="tab-count">${list.length}</span></button>`).join('')}</div>
    <section class="card"><div class="card-body flush" id="crop-table"></div></section>`;

  const draw = () => {
    const list = tabs.find(([k]) => k === active)[2];
    $$('[data-tab]', main).forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === active)));
    const host = $('#crop-table', main);
    if (!list.length) {
      host.innerHTML = active === 'history'
        ? emptyState({ icon: 'harvest', title: 'No crop history', text: 'When you mark a crop as harvested or failed, it moves here.' })
        : emptyState({ icon: 'sprout', title: 'No crops recorded', text: 'Add the crops you are planning or growing to keep dates, inputs and harvests in one place.', action: `<button class="btn sm" type="button" data-add-crop>${icon('plus')}Add a crop</button>` });
    } else {
      host.innerHTML = `<div class="table-wrap"><table class="table stackable table-min">
        <thead><tr><th>Crop</th><th>Plot</th><th class="right">Area</th><th>Sown</th><th>Expected harvest</th><th>Status</th><th>Harvested</th><th class="right"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${list.map((c) => `
          <tr>
            <td class="cell-title" data-label="Crop"><strong>${esc(c.crop_name)}</strong><span class="sub">${esc([c.variety, c.season].filter(Boolean).join(' · '))}</span></td>
            <td data-label="Plot">${esc(val(c.plot_name))}</td>
            <td class="right num" data-label="Area">${esc(fmtAcres(c.area_acres))}</td>
            <td data-label="Sown">${esc(fmtDate(c.sowing_date))}</td>
            <td data-label="Expected harvest">${esc(fmtDate(c.expected_harvest_date))}</td>
            <td data-label="Status">${cropStatusBadge(c)}</td>
            <td data-label="Harvested">${esc(harvestTotal(c))}</td>
            <td class="cell-actions" data-label=""><div class="row-actions">
              <button class="btn ghost sm" type="button" data-toggle="${esc(c.id)}" aria-expanded="${expanded.has(c.id)}">${expanded.has(c.id) ? 'Hide' : 'Details'}</button>
              <button class="btn ghost icon sm" type="button" data-edit-crop="${esc(c.id)}" aria-label="Edit ${esc(c.crop_name)}" title="Edit">${icon('edit')}</button>
              <button class="btn ghost icon sm" type="button" data-del-crop="${esc(c.id)}" aria-label="Delete ${esc(c.crop_name)}" title="Delete">${icon('trash')}</button>
            </div></td>
          </tr>
          ${expanded.has(c.id) ? `<tr class="crop-row-detail"><td colspan="8" class="cell-title">${cropDetail(c)}</td></tr>` : ''}`).join('')}</tbody></table></div>`;
    }
    const byId = Object.fromEntries(crops.map((c) => [c.id, c]));
    $$('[data-add-crop]', host).forEach((b) => b.addEventListener('click', () => openCropModal(null, farm.plots)));
    $$('[data-toggle]', host).forEach((b) => b.addEventListener('click', () => {
      const id = b.dataset.toggle;
      if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
      draw();
    }));
    $$('[data-edit-crop]', host).forEach((b) => b.addEventListener('click', () => openCropModal(byId[b.dataset.editCrop], farm.plots)));
    $$('[data-del-crop]', host).forEach((b) => b.addEventListener('click', async () => {
      const c = byId[b.dataset.delCrop];
      if (!await confirmDialog({ title: `Delete ${c.crop_name}?`, message: `This removes the crop record${c.harvests.length ? ` and its ${c.harvests.length} harvest record${c.harvests.length === 1 ? '' : 's'}` : ''}. This cannot be undone.`, confirmLabel: 'Delete crop' })) return;
      try { await api(`/api/portal/crops/${c.id}`, { method: 'DELETE' }); toast('Crop deleted.'); render(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    $$('[data-add-harvest]', host).forEach((b) => b.addEventListener('click', () => openHarvestModal(byId[b.dataset.addHarvest])));
    $$('[data-del-harvest]', host).forEach((b) => b.addEventListener('click', async () => {
      if (!await confirmDialog({ title: 'Delete harvest record?', message: 'This harvest record will be removed permanently.', confirmLabel: 'Delete record' })) return;
      try { await api(`/api/portal/harvests/${b.dataset.delHarvest}`, { method: 'DELETE' }); toast('Harvest record deleted.'); render(); }
      catch (err) { toast(err.message, 'error'); }
    }));
  };

  $$('[data-tab]', main).forEach((b) => b.addEventListener('click', () => {
    active = b.dataset.tab;
    history.replaceState(null, '', `/portal/crops?tab=${active}`);
    draw();
  }));
  $('#add-crop', main).addEventListener('click', () => openCropModal(null, farm.plots));
  draw();
  if (params.get('add') === '1') {
    history.replaceState(null, '', '/portal/crops');
    openCropModal(null, farm.plots);
  }
}

function cropDetail(c) {
  const input = (label, v) => `<div><dt>${esc(label)}</dt>${dd(v)}</div>`;
  return `<div style="padding:4px 0 8px">
    <div class="field-label" style="margin-bottom:10px">Input requirements</div>
    <dl class="dl detail-grid">
      ${input('Seed', c.seed_requirement)}${input('Fertiliser', c.fertilizer_plan)}${input('Plant protection', c.pesticide_plan)}${input('Irrigation', c.irrigation_plan)}
    </dl>
    ${c.notes ? `<div style="margin-top:14px"><div class="field-label">Notes</div><p class="text-2" style="margin-top:4px">${esc(c.notes)}</p></div>` : ''}
    <div style="margin-top:18px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <div class="field-label">Harvest records</div>
      <button class="btn secondary sm" type="button" data-add-harvest="${esc(c.id)}">${icon('plus')}Record harvest</button>
    </div>
    ${c.harvests.length ? `<div class="table-wrap" style="margin-top:10px;background:var(--surface);border:1px solid var(--border);border-radius:8px">
      <table class="table"><thead><tr><th>Date</th><th class="right">Quantity</th><th>Grade</th><th>Notes</th><th class="right"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${c.harvests.map((h) => `<tr><td>${esc(fmtDate(h.harvest_date))}</td><td class="right num"><strong>${esc(nf.format(h.quantity))} ${esc(h.unit)}</strong></td>
        <td>${h.quality_grade ? badge('outline', `Grade ${h.quality_grade}`) : '—'}</td><td>${esc(val(h.notes))}</td>
        <td class="right"><button class="btn ghost icon sm" type="button" data-del-harvest="${esc(h.id)}" aria-label="Delete harvest record" title="Delete">${icon('trash')}</button></td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted" style="font-size:13px;margin-top:8px">No harvest recorded for this crop yet.</p>'}
  </div>`;
}

function openCropModal(c, plots) {
  const meta = S.meta || {};
  const plotOptions = plots.map((p) => {
    const free = Math.max(0, p.area_acres - p.area_in_use + (c && c.plot_id === p.id && c.is_current ? c.area_acres : 0));
    return [p.id, `${p.name} (${fmtAcres(free)} free of ${fmtAcres(p.area_acres)})`];
  });
  const m = openModal({
    title: c ? `Edit ${c.crop_name}` : 'Add crop', size: 'lg',
    description: 'Dates, area and the inputs you plan to use.',
    body: `<form id="crop-form" novalidate>
      <div data-form-alert style="margin-bottom:12px"></div>
      <div class="form-section">
        <div class="form-section-title">Crop details</div>
        <div class="form-grid" style="margin-top:12px">
          <div class="field">
            <label for="f-crop_name">Crop<span class="req" aria-hidden="true">*</span></label>
            <input class="input" id="f-crop_name" name="crop_name" list="crop-names" required data-label="Crop" value="${esc(c ? c.crop_name : '')}" placeholder="Start typing, e.g. Tur" minlength="2" maxlength="40" autocomplete="off">
            <datalist id="crop-names">${(meta.crop_names || []).map((n) => `<option value="${esc(n)}">`).join('')}</datalist>
          </div>
          ${field({ name: 'variety', label: 'Variety', optional: true, value: c && c.variety, placeholder: 'e.g. BSMR 736', attrs: 'maxlength="60"' })}
          ${field({ name: 'plot_id', label: 'Plot', type: 'select', optional: true, value: c && c.plot_id, options: plotOptions, placeholder: plots.length ? 'Not linked to a plot' : 'No plots recorded yet', help: plots.length ? '' : 'Add plots on the My farm page to link crops to them.' })}
          ${field({ name: 'season', label: 'Season', type: 'select', required: true, value: c ? c.season : '', options: meta.seasons || [], placeholder: 'Select season' })}
          ${field({ name: 'area_acres', label: 'Area cultivated (acres)', type: 'number', required: true, value: c && c.area_acres, attrs: 'step="0.01" max="1000" data-min="0" inputmode="decimal"' })}
          ${field({ name: 'status', label: 'Status', type: 'select', required: true, value: c ? c.status : 'PLANNED', options: Object.entries(meta.crop_statuses || {}), placeholder: null })}
          ${field({ name: 'sowing_date', label: 'Sowing date', type: 'date', required: true, value: c && c.sowing_date, help: 'Planned date if not sown yet.' })}
          ${field({ name: 'expected_harvest_date', label: 'Expected harvest date', type: 'date', optional: true, value: c && c.expected_harvest_date })}
        </div>
      </div>
      <div class="form-section">
        <div class="form-section-title">Input requirements</div>
        <div class="form-section-desc">What this crop needs. Write it the way you would tell your supplier.</div>
        <div class="form-grid">
          ${field({ name: 'seed_requirement', label: 'Seed', type: 'textarea', optional: true, rows: 2, value: c && c.seed_requirement, placeholder: 'e.g. 5 kg certified seed per acre', attrs: 'maxlength="300"' })}
          ${field({ name: 'fertilizer_plan', label: 'Fertiliser', type: 'textarea', optional: true, rows: 2, value: c && c.fertilizer_plan, placeholder: 'e.g. 50 kg DAP at sowing, 25 kg urea at 30 days', attrs: 'maxlength="300"' })}
          ${field({ name: 'pesticide_plan', label: 'Plant protection', type: 'textarea', optional: true, rows: 2, value: c && c.pesticide_plan, placeholder: 'e.g. Neem oil spray if pod borer seen', attrs: 'maxlength="300"' })}
          ${field({ name: 'irrigation_plan', label: 'Irrigation', type: 'textarea', optional: true, rows: 2, value: c && c.irrigation_plan, placeholder: 'e.g. Drip, every 5 days after flowering', attrs: 'maxlength="300"' })}
          ${field({ name: 'notes', label: 'Notes', type: 'textarea', optional: true, span: 'span-2', rows: 2, value: c && c.notes, attrs: 'maxlength="500"' })}
        </div>
      </div>
    </form>`,
    footer: `<button class="btn secondary" type="button" data-close>Cancel</button><button class="btn" type="submit" form="crop-form">${c ? 'Save crop' : 'Add crop'}</button>`,
  });
  const form = $('#crop-form', m.el);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[form="crop-form"]', m.el),
      (v) => api(c ? `/api/portal/crops/${c.id}` : '/api/portal/crops', { method: c ? 'PUT' : 'POST', body: v }),
      (v) => {
        const errs = {};
        if (v.sowing_date && v.expected_harvest_date && v.expected_harvest_date < v.sowing_date) errs.expected_harvest_date = 'Expected harvest must be on or after the sowing date.';
        if (v.sowing_date && ['SOWN', 'GROWING', 'HARVESTED'].includes(v.status) && v.sowing_date > todayISO()) errs.sowing_date = 'The sowing date is in the future. Set the status to Planned.';
        return errs;
      });
    if (!r) return;
    m.close();
    toast(c ? 'Crop updated.' : 'Crop added.');
    render();
  });
}

function openHarvestModal(c) {
  const meta = S.meta || {};
  const m = openModal({
    title: 'Record harvest', description: `${c.crop_name}${c.variety ? ` · ${c.variety}` : ''}, sown ${fmtDate(c.sowing_date)}`,
    body: `<form id="harvest-form" novalidate>
      <div data-form-alert style="margin-bottom:12px"></div>
      <div class="form-grid">
        ${field({ name: 'harvest_date', label: 'Harvest date', type: 'date', required: true, value: todayISO(), attrs: `max="${todayISO()}" min="${esc(c.sowing_date)}" data-min-msg="The harvest date cannot be before the sowing date." data-max-msg="The harvest date cannot be in the future."` })}
        ${field({ name: 'quality_grade', label: 'Quality grade', type: 'select', optional: true, options: (meta.quality_grades || []).map((g) => [g, `Grade ${g}`]), placeholder: 'Not graded' })}
        ${field({ name: 'quantity', label: 'Quantity', type: 'number', required: true, attrs: 'step="0.01" max="1000000" data-min="0" inputmode="decimal"' })}
        ${field({ name: 'unit', label: 'Unit', type: 'select', required: true, value: 'quintal', options: meta.harvest_units || ['quintal'], placeholder: null })}
        ${field({ name: 'notes', label: 'Notes', type: 'textarea', optional: true, span: 'span-2', rows: 2, placeholder: 'e.g. First picking, sold at Kalaburagi APMC', attrs: 'maxlength="300"' })}
      </div>
      ${c.is_current ? `<label class="checkbox" style="margin-top:16px"><input type="checkbox" name="mark_harvested"><span>Also mark this crop as <strong>Harvested</strong> (final harvest)</span></label>` : ''}
    </form>`,
    footer: '<button class="btn secondary" type="button" data-close>Cancel</button><button class="btn" type="submit" form="harvest-form">Save harvest</button>',
  });
  const form = $('#harvest-form', m.el);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[form="harvest-form"]', m.el), async (v) => {
      const res = await api(`/api/portal/crops/${c.id}/harvests`, { method: 'POST', body: v });
      if (v.mark_harvested) {
        const cr = res.crop;
        await api(`/api/portal/crops/${c.id}`, { method: 'PUT', body: { ...cr, status: 'HARVESTED' } });
      }
      return res;
    });
    if (!r) return;
    m.close();
    toast('Harvest recorded.');
    render();
  });
}

/* ============================================================= DOCUMENTS */

async function viewDocuments(main, alive) {
  const d = await api('/api/portal/documents');
  if (!alive()) return;
  const card0 = d.issued[0];
  const [cls, label] = CARD_BADGE[card0.status] || ['', card0.status_label];

  main.innerHTML = `
    ${pageHead('Documents', 'Your Farmer ID and the records you keep ready for scheme applications and verification.', `<button class="btn" type="button" id="upload-doc">${icon('upload')}Upload document</button>`)}
    <div class="stack">
      <section class="card">
        <div class="card-head"><div><h3>Issued by AI Krishi</h3><p>Generated for you by the registry</p></div></div>
        <ul class="list">
          <li class="list-item" style="align-items:center">
            <span class="file-icon card">${icon('idcard')}</span>
            <div class="grow">
              <div class="list-title">${esc(card0.title)}</div>
              <div class="list-sub"><span class="mono">${esc(card0.reference_number)}</span> · Serial ${esc(card0.serial)} · Issued ${esc(fmtDate(card0.issued_at))} · Valid until ${esc(fmtDate(card0.valid_until))}</div>
            </div>
            ${badge(cls, label, true)}
            <a class="btn secondary sm" href="/portal/id-card" data-link>${icon('eye')}View</a>
          </li>
        </ul>
      </section>

      <section class="card">
        <div class="card-head"><div><h3>Uploaded documents</h3><p>${d.uploaded.length} of ${d.limits.max_documents} · PDF, JPG or PNG up to ${fmtBytes(d.limits.max_bytes)} each</p></div></div>
        <div class="card-body flush">${d.uploaded.length ? `
          <div class="table-wrap"><table class="table stackable table-min">
            <thead><tr><th>Document</th><th>Type</th><th>Reference no.</th><th class="right">Size</th><th>Uploaded</th><th class="right"><span class="sr-only">Actions</span></th></tr></thead>
            <tbody>${d.uploaded.map((doc) => `<tr>
              <td class="cell-title" data-label="Document"><div style="display:flex;gap:12px;align-items:center">
                <span class="file-icon ${doc.mime === 'application/pdf' ? '' : 'img'}">${doc.mime === 'application/pdf' ? 'PDF' : doc.mime === 'image/png' ? 'PNG' : 'JPG'}</span>
                <div style="min-width:0"><strong>${esc(doc.title)}</strong><span class="sub" style="overflow-wrap:anywhere">${esc(doc.original_name)}</span></div></div></td>
              <td data-label="Type">${esc(doc.category_label)}</td>
              <td data-label="Reference no.">${doc.reference_number ? `<span class="mono">${esc(doc.reference_number)}</span>` : '—'}</td>
              <td class="right num" data-label="Size">${esc(fmtBytes(doc.size_bytes))}</td>
              <td data-label="Uploaded">${esc(fmtDate(doc.uploaded_at))}</td>
              <td class="cell-actions" data-label=""><div class="row-actions">
                <a class="btn ghost icon sm" href="/api/portal/documents/${esc(doc.id)}/file" target="_blank" rel="noopener" aria-label="View ${esc(doc.title)}" title="View">${icon('eye')}</a>
                <a class="btn ghost icon sm" href="/api/portal/documents/${esc(doc.id)}/file?download=true" aria-label="Download ${esc(doc.title)}" title="Download">${icon('download')}</a>
                <button class="btn ghost icon sm" type="button" data-del-doc="${esc(doc.id)}" aria-label="Delete ${esc(doc.title)}" title="Delete">${icon('trash')}</button>
              </div></td></tr>`).join('')}</tbody></table></div>`
          : emptyState({ icon: 'file', title: 'No documents uploaded', text: 'Upload your land record (RTC / Pahani), certificates or bank passbook so they are ready when you apply for a scheme.', action: `<button class="btn sm" type="button" data-upload>${icon('upload')}Upload a document</button>` })}
        </div>
        <div class="card-foot">${icon('lock', '')} Documents are private to your account and are opened only when you choose to share them.</div>
      </section>
    </div>`;
  $('.card-foot svg', main).setAttribute('style', 'width:14px;height:14px;vertical-align:-2px;margin-right:4px');

  $$('#upload-doc, [data-upload]', main).forEach((b) => b.addEventListener('click', () => openUploadModal(d.limits)));
  $$('[data-del-doc]', main).forEach((b) => b.addEventListener('click', async () => {
    const doc = d.uploaded.find((x) => x.id === b.dataset.delDoc);
    if (!await confirmDialog({ title: 'Delete document?', message: `“${doc.title}” will be permanently deleted from your account.`, confirmLabel: 'Delete document' })) return;
    try { await api(`/api/portal/documents/${doc.id}`, { method: 'DELETE' }); toast('Document deleted.'); render(); }
    catch (err) { toast(err.message, 'error'); }
  }));
}

function openUploadModal(limits) {
  const meta = S.meta || {};
  const m = openModal({
    title: 'Upload document', description: 'PDF, JPG or PNG, up to 5 MB.',
    body: `<form id="doc-form" class="stack-16" novalidate>
      <div data-form-alert></div>
      ${field({ name: 'category', label: 'Document type', type: 'select', required: true, options: Object.entries(meta.document_categories || {}), placeholder: 'Select type' })}
      ${field({ name: 'title', label: 'Document name', required: true, placeholder: 'e.g. RTC for survey no. 124/2A', attrs: 'minlength="2" maxlength="80"' })}
      ${field({ name: 'reference_number', label: 'Reference number', optional: true, placeholder: 'e.g. certificate or RTC number', attrs: 'maxlength="60"' })}
      <div class="field">
        <span class="field-label">File<span class="req" aria-hidden="true">*</span></span>
        <label class="dropzone" id="dropzone" for="f-file" tabindex="0">
          ${icon('upload')}
          <strong id="drop-title">Choose a file or drag it here</strong>
          <span id="drop-sub">PDF, JPG or PNG up to ${fmtBytes(limits.max_bytes)}</span>
        </label>
        <input type="file" id="f-file" name="file" accept="application/pdf,image/jpeg,image/png" required data-label="File" data-required-msg="Choose a file to upload." hidden>
      </div>
    </form>`,
    footer: '<button class="btn secondary" type="button" data-close>Cancel</button><button class="btn" type="submit" form="doc-form">Upload</button>',
  });
  const form = $('#doc-form', m.el);
  const input = $('#f-file', form);
  const zone = $('#dropzone', form);
  const OK = ['application/pdf', 'image/jpeg', 'image/png'];

  const pick = (file) => {
    const err = !file ? '' : !OK.includes(file.type) ? 'Choose a PDF, JPG or PNG file.' : file.size > limits.max_bytes ? `The file is larger than ${fmtBytes(limits.max_bytes)}.` : '';
    $$('.error', zone.parentElement).forEach((x) => x.remove());
    if (err) {
      input.value = '';
      zone.insertAdjacentHTML('afterend', `<div class="error">${icon('error')}<span>${esc(err)}</span></div>`);
      $('#drop-title', zone).textContent = 'Choose a file or drag it here';
      $('#drop-sub', zone).textContent = `PDF, JPG or PNG up to ${fmtBytes(limits.max_bytes)}`;
      return;
    }
    $('#drop-title', zone).innerHTML = `<span class="file-picked">${esc(file.name)}</span>`;
    $('#drop-sub', zone).textContent = `${fmtBytes(file.size)} · Click to choose a different file`;
    const title = $('#f-title', form);
    if (!title.value) title.value = file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').slice(0, 80);
  };
  input.addEventListener('change', () => pick(input.files[0]));
  zone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('drag'); }));
  zone.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (!file) return;
    const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files;
    pick(file);
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const r = await submitWith(form, $('button[form="doc-form"]', m.el), (v) => {
      const fd = new FormData();
      fd.append('category', v.category); fd.append('title', v.title); fd.append('reference_number', v.reference_number || '');
      fd.append('file', input.files[0]);
      return api('/api/portal/documents', { method: 'POST', form: fd });
    });
    if (!r) return;
    m.close();
    toast('Document uploaded.');
    render();
  });
}

/* ========================================================= NOTIFICATIONS */

const NOTIF_ICON = { WEATHER: 'cloud', ALERT: 'alert', SCHEME: 'gift', GOVERNMENT: 'megaphone', APPLICATION: 'file' };
const SEVERITY_BADGE = { WARNING: ['amber', 'Warning'], CRITICAL: ['red', 'Critical'] };

async function viewNotifications(main, alive) {
  const params = new URLSearchParams(location.search);
  const category = params.get('category') || '';
  const [all, filtered] = await Promise.all([
    api('/api/portal/notifications'),
    category ? api(`/api/portal/notifications?category=${encodeURIComponent(category)}`) : null,
  ]);
  if (!alive()) return;
  const data = filtered || all;
  setUnread(all.unread);
  const cats = (S.meta && S.meta.notification_categories) || {};

  main.innerHTML = `
    ${pageHead('Notifications', 'Weather alerts, scheme and government updates, and news about your registration.', all.unread ? `<button class="btn secondary" type="button" id="read-all">${icon('check')}Mark all as read</button>` : '')}
    <div class="chips" role="group" aria-label="Filter notifications" style="margin-bottom:16px">
      <button class="chip" type="button" data-cat="" aria-pressed="${!category}">All${all.unread ? `<span class="chip-count">${all.unread}</span>` : ''}</button>
      ${Object.entries(cats).map(([k, label]) => `<button class="chip" type="button" data-cat="${esc(k)}" aria-pressed="${category === k}">${esc(label)}${all.unread_by_category[k] ? `<span class="chip-count">${all.unread_by_category[k]}</span>` : ''}</button>`).join('')}
    </div>
    <section class="card">${data.items.length ? data.items.map((n) => {
      const sev = SEVERITY_BADGE[n.severity];
      return `<article class="notif ${n.read ? '' : 'unread'}" data-id="${esc(n.id)}">
        <span class="notif-icon ${esc(n.category)}">${icon(NOTIF_ICON[n.category] || 'bell')}</span>
        <div class="grow">
          <div class="notif-top"><h4 class="notif-title">${esc(n.title)}</h4>${sev ? badge(sev[0], sev[1]) : ''}</div>
          <p class="notif-body">${esc(n.body)}</p>
          <div class="notif-meta">
            <span>${esc(n.category_label)}</span><span>${esc(timeAgo(n.created_at))}</span>${n.source ? `<span>Source: ${esc(n.source)}</span>` : ''}
            ${n.link ? `<a href="${esc(n.link)}" data-link data-open="${esc(n.id)}">Open${icon('chevronRight', '')}</a>` : ''}
            ${n.read ? '' : `<button class="link-btn" type="button" data-read="${esc(n.id)}">Mark as read</button>`}
          </div>
        </div>
        ${n.read ? '' : '<span class="unread-dot" aria-label="Unread"></span>'}
      </article>`;
    }).join('') : emptyState({ icon: 'bell', title: category ? 'Nothing in this category' : 'No notifications', text: 'Weather alerts and updates about your registration will appear here.' })}</section>`;

  $$('.notif-meta a svg', main).forEach((s) => s.setAttribute('style', 'width:13px;height:13px;vertical-align:-2px'));
  $$('[data-cat]', main).forEach((b) => b.addEventListener('click', () => navigate(`/portal/notifications${b.dataset.cat ? `?category=${b.dataset.cat}` : ''}`)));
  const readAll = $('#read-all', main);
  if (readAll) readAll.addEventListener('click', async () => {
    setBusy(readAll, true);
    try { await api('/api/portal/notifications/read-all', { method: 'POST' }); toast('All notifications marked as read.'); render(); }
    catch (err) { toast(err.message, 'error'); setBusy(readAll, false); }
  });
  $$('[data-read]', main).forEach((b) => b.addEventListener('click', async () => {
    try {
      await api(`/api/portal/notifications/${b.dataset.read}/read`, { method: 'POST' });
      const item = b.closest('.notif');
      item.classList.remove('unread');
      $('.unread-dot', item)?.remove();
      b.remove();
      setUnread(S.unread - 1);
    } catch (err) { toast(err.message, 'error'); }
  }));
  $$('[data-open]', main).forEach((a) => a.addEventListener('click', () => {
    api(`/api/portal/notifications/${a.dataset.open}/read`, { method: 'POST' }).catch(() => {});
  }));
}

/* =============================================================== ID CARD */

function cardFront(c, photo) {
  const verified = c.status === 'ACTIVE';
  const land = c.land.total_acres ? fmtAcres(c.land.total_acres) : 'Not recorded';
  return `<div class="idc" role="img" aria-label="Front of Farmer ID card for ${esc(c.full_name)}, Farmer ID ${esc(c.farmer_id)}">
    <div class="idc-watermark">${LEAF_MARK}</div>
    <div class="idc-inner">
      <div class="idc-head">
        <span class="idc-emblem">${LEAF_MARK}</span>
        <div class="idc-org"><strong>AI Krishi Farmer Registry</strong><span>Digital Farmer Identity Card</span></div>
        <div class="idc-head-right"><strong>Farmer ID</strong><span>${esc(c.state)}</span></div>
      </div>
      <div class="idc-rule"></div>
      <div class="idc-body">
        <div>
          <div class="idc-photo">${photo ? `<img src="${esc(photo)}" alt="">` : SILHOUETTE}</div>
          <div class="idc-status ${esc(c.status)}">${esc(c.status === 'PENDING' ? 'Pending' : c.status_label)}</div>
        </div>
        <div class="idc-fields">
          <div><span class="idc-label">Name</span><span class="idc-name">${esc(c.full_name)}</span></div>
          <div><span class="idc-label">Farmer ID</span><span class="idc-fid">${esc(c.farmer_id)}</span></div>
          <div class="idc-pair">
            <div><span class="idc-label">Mobile</span><span class="idc-value">${esc(fmtMobile(c.mobile))}</span></div>
            <div><span class="idc-label">Land holding</span><span class="idc-value">${esc(land)}</span></div>
            <div><span class="idc-label">Village / District</span><span class="idc-value">${esc(c.village)}, ${esc(c.district)}</span></div>
            <div><span class="idc-label">State</span><span class="idc-value">${esc(c.state)}</span></div>
          </div>
        </div>
        <div class="idc-qr">
          <div class="idc-qr-box">${c.qr_svg}</div>
          <div class="idc-qr-cap">Scan to verify</div>
          <div class="idc-verified ${verified ? '' : 'pending'}">${icon(verified ? 'shield' : 'shieldPlain')}<span>${verified ? 'Verified record' : 'Verification pending'}</span></div>
        </div>
      </div>
      <div class="idc-foot">
        <span>Issued <b>${esc(fmtDate(c.registered_on))}</b> &nbsp;·&nbsp; Valid till <b>${esc(fmtDate(c.valid_until))}</b></span>
        <span>S. No. <b class="serial">${esc(c.card_serial)}</b></span>
      </div>
    </div>
  </div>`;
}

function cardBack(c) {
  const l = c.land;
  const landLine = l.total_acres ? `${fmtAcres(l.total_acres)}${l.plots ? ` in ${l.plots} plot${l.plots === 1 ? '' : 's'}` : ''}${l.basis === 'declared at registration' ? ' (declared)' : ''}` : 'Not recorded';
  const helpline = c.helpline ? fmtMobile(c.helpline) : 'your nearest Raitha Samparka Kendra';
  return `<div class="idc" role="img" aria-label="Back of Farmer ID card">
    <div class="idc-watermark">${LEAF_MARK}</div>
    <div class="idc-inner">
      <div class="idc-head slim">
        <span class="idc-emblem">${LEAF_MARK}</span>
        <div class="idc-org"><strong>AI Krishi Farmer Registry</strong></div>
        <div class="idc-head-right"><strong class="mono" style="letter-spacing:.04em">${esc(c.farmer_id)}</strong></div>
      </div>
      <div class="idc-rule"></div>
      <div class="idc-back-body">
        <div>
          <div class="idc-block"><span class="idc-label">Address</span><span class="idc-text">${esc(c.address || '—')}</span></div>
          <div class="idc-block"><span class="idc-label">Land details</span><span class="idc-value">${esc(landLine)}</span>
            ${l.survey_numbers.length ? `<span class="idc-value minor">Survey no. ${esc(l.survey_numbers.join(', '))}</span>` : ''}
            ${l.irrigation.length ? `<span class="idc-value minor">${esc(l.irrigation.join(', '))}</span>` : ''}</div>
          <div class="idc-block idc-pair">
            <div><span class="idc-label">Registered on</span><span class="idc-value">${esc(fmtDate(c.registered_on))}</span></div>
            <div><span class="idc-label">Verified on</span><span class="idc-value">${esc(c.verified_on ? fmtDate(c.verified_on) : 'Pending')}</span></div>
          </div>
        </div>
        <div>
          <div class="idc-terms-title">Important</div>
          <ol class="idc-terms">
            <li>This card identifies the holder as a farmer registered with the AI Krishi Farmer Registry.</li>
            <li>Check it by scanning the QR code. The details shown must match this card.</li>
            <li>This card is not proof of land ownership.</li>
            <li>If found, please inform the AI Krishi helpline on ${esc(helpline)}.</li>
          </ol>
          <div class="idc-sign-note">Issued electronically. No signature is required.</div>
        </div>
      </div>
      <div class="idc-foot">
        <span>Verification code <b class="serial">${esc(c.verification_code)}</b></span>
        <span>S. No. <b class="serial">${esc(c.card_serial)}</b></span>
      </div>
    </div>
  </div>`;
}

function printWith(bodyClass, cleanup) {
  document.body.classList.add(bodyClass);
  const done = () => {
    document.body.classList.remove(bodyClass);
    window.removeEventListener('afterprint', done);
    if (cleanup) cleanup();
  };
  window.addEventListener('afterprint', done);
  setTimeout(() => window.print(), 50);
}

async function viewCard(main, alive) {
  const c = await api('/api/portal/card');
  if (!alive()) return;
  const photo = c.photo_available ? `/api/portal/photo?v=${c.photo_version}` : '';
  const [cls, label] = CARD_BADGE[c.status] || ['', c.status_label];

  const statusAlert = {
    ACTIVE: alertBox('success', 'Your card is active. Anyone can confirm it is genuine by scanning the QR code with a phone camera.', 'Verified.'),
    PENDING: alertBox('warning', 'The card shows <strong>Pending</strong> until the registry office verifies your details. You can still print it and use the QR code, which will show the pending status.', 'Verification pending.'),
    EXPIRED: alertBox('error', 'This card has passed its validity date. Contact the registry office to renew it.', 'Expired.'),
    SUSPENDED: alertBox('error', 'This registration is suspended. Contact the AI Krishi helpline.', 'Suspended.'),
  }[c.status] || '';

  main.innerHTML = `
    ${pageHead('Digital Farmer ID card', 'Your official AI Krishi farmer identity. Print it on card stock or keep it on your phone.', `
      <button class="btn secondary" type="button" id="copy-link">${icon('copy')}Copy verification link</button>
      <button class="btn" type="button" id="print-card">${icon('printer')}Print or save as PDF</button>`)}
    <div style="margin-bottom:20px">${statusAlert}</div>
    ${!c.photo_available ? `<div style="margin-bottom:20px">${alertBox('info', 'Your card has no photograph. <a href="/portal/profile" data-link>Upload a photo</a> so the card can be matched to you.', 'Add your photo.')}</div>` : ''}
    <section class="card" style="margin-bottom:20px"><div class="card-body">
      <div class="card-sheet">
        <div class="card-side"><div class="card-side-label">Front</div>${cardFront(c, photo)}</div>
        <div class="card-side"><div class="card-side-label">Back</div>${cardBack(c)}</div>
      </div>
    </div></section>

    <div class="grid cols-2">
      <section class="card">
        <div class="card-head"><div><h3>Card details</h3></div>${badge(cls, label, true)}</div>
        <div class="card-body stack-16">
          <dl class="dl">
            <div><dt>Farmer ID</dt><dd class="mono">${esc(c.farmer_id)}</dd></div>
            <div><dt>Card serial number</dt><dd class="mono">${esc(c.card_serial)}</dd></div>
            <div><dt>Issued on</dt><dd>${esc(fmtDate(c.registered_on))}</dd></div>
            <div><dt>Valid until</dt><dd>${esc(fmtDate(c.valid_until))}</dd></div>
            <div><dt>Verified on</dt><dd>${esc(c.verified_on ? fmtDate(c.verified_on) : 'Not yet verified')}</dd></div>
            <div><dt>Verification code</dt><dd class="mono">${esc(c.verification_code)}</dd></div>
          </dl>
          <div class="field"><label for="verify-url">Verification link</label>
            <div class="verify-link"><input class="input" id="verify-url" value="${esc(c.verify_url)}" readonly>
            <a class="btn secondary icon" href="${esc(c.verify_url)}" target="_blank" rel="noopener" aria-label="Open verification page" title="Open verification page">${icon('external')}</a></div>
            <div class="help">This is the link inside the QR code. It shows your name, photo and status, never your full mobile number.</div>
          </div>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><div><h3>How verification works</h3></div></div>
        <div class="card-body">
          <ul class="list">
            ${[
              ['shield', 'Digitally signed QR code', 'The QR code carries a signature issued by the registry. A copied or edited card fails the check.'],
              ['eye', 'Instant check with any phone', 'Scanning opens the registry page with the photo, name, Farmer ID and current status.'],
              ['refresh', 'Always up to date', 'If your registration is verified, suspended or expires, the check shows the new status immediately.'],
            ].map(([ic, t, s]) => `<li style="display:flex;gap:12px;padding:10px 0"><span class="stat-icon green" style="width:36px;height:36px">${icon(ic)}</span><div><div class="list-title">${esc(t)}</div><div class="list-sub">${esc(s)}</div></div></li>`).join('')}
          </ul>
        </div>
      </section>
    </div>`;

  $('#copy-link', main).addEventListener('click', async () => { await copyText(c.verify_url); toast('Verification link copied.'); });
  $('#print-card', main).addEventListener('click', async () => {
    const sheet = document.createElement('div');
    sheet.className = 'print-card-root print-only';
    sheet.innerHTML = `<p class="print-caption">Front</p><div class="print-side">${cardFront(c, photo)}</div>
      <p class="print-caption">Back</p><div class="print-side">${cardBack(c)}</div>
      <p class="print-note">Print at 100% scale (actual size). Cut along the card edges and fold, or print front and back on card stock.</p>`;
    $('#root').appendChild(sheet);
    await Promise.all($$('img', sheet).map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve())));
    printWith('printing-card', () => sheet.remove());
  });
}

/* ================================================================ VERIFY */

async function viewVerify(farmerId, token) {
  const t = new URLSearchParams(location.search).get('t') || '';
  document.title = 'Verify Farmer ID · AI Krishi Farmer Registry';
  const frame = (inner) => {
    $('#root').innerHTML = `<div class="verify-wrap">
      <header class="verify-top">${brand('Farmer Registry verification').replace('href="/portal"', 'href="/"').replace(' data-link', '')}</header>
      <main class="verify-main"><div class="verify-card">${inner}</div></main>
      <footer class="site-foot"><span>AI Krishi Farmer Registry</span><span>Public verification service</span></footer>
    </div>`;
  };
  frame('<div class="card"><div class="state"><span class="spinner lg"></span><p>Checking the registry…</p></div></div>');

  let r;
  try {
    r = await api(`/api/portal/verify/${encodeURIComponent(farmerId)}?t=${encodeURIComponent(t)}`);
  } catch (err) {
    if (token !== S.renderToken) return;
    frame(`<div class="card">
      <div class="verify-result bad"><span class="vr-icon">${icon('error')}</span><div><h2>Not verified</h2><p>Checked ${esc(fmtDateTime(Date.now() / 1000))}</p></div></div>
      <div class="card-body stack-16">
        <p class="text-2">${esc(err.status === 404 ? err.message : 'The registry could not be reached. Check your connection and scan the code again.')}</p>
        ${alertBox('warning', 'Do not accept this card as proof of registration until it verifies successfully.')}
      </div></div>`);
    return;
  }
  if (token !== S.renderToken) return;

  const outcome = {
    ACTIVE: ['', 'shield', 'Farmer ID verified', 'This card is genuine and the registration is active.'],
    PENDING: ['warn', 'clock', 'Registered, pending verification', 'This card is genuine, but the details have not been verified by the registry office yet.'],
    EXPIRED: ['bad', 'alert', 'Card expired', 'This card is genuine but has passed its validity date.'],
    SUSPENDED: ['bad', 'alert', 'Registration suspended', 'This card is genuine but the registration is suspended.'],
  }[r.status] || ['warn', 'info', r.status_label, ''];
  const photo = r.photo_available ? `/api/portal/verify/${encodeURIComponent(r.farmer_id)}/photo?t=${encodeURIComponent(t)}` : '';

  frame(`<div class="card">
    <div class="verify-result ${outcome[0]}"><span class="vr-icon">${icon(outcome[1])}</span><div><h2>${esc(outcome[2])}</h2><p>${esc(outcome[3])}</p></div></div>
    <div class="card-body">
      <div class="verify-person">
        <div class="photo-frame">${photo ? `<img src="${esc(photo)}" alt="Registered photograph">` : SILHOUETTE}</div>
        <dl class="dl one" style="flex:1">
          <div><dt>Name</dt><dd style="font-size:16px;font-weight:700">${esc(r.full_name)}</dd></div>
          <div><dt>Farmer ID</dt><dd class="mono">${esc(r.farmer_id)}</dd></div>
          <div><dt>Village / District</dt><dd>${esc(r.village)}, ${esc(r.district)}, ${esc(r.state)}</dd></div>
        </dl>
      </div>
      <dl class="dl" style="margin-top:20px;padding-top:20px;border-top:1px solid var(--border)">
        <div><dt>Status</dt><dd>${badge((CARD_BADGE[r.status] || [''])[0], r.status_label, true)}</dd></div>
        <div><dt>Mobile</dt><dd class="mono">${esc(r.mobile_masked)}</dd></div>
        <div><dt>Registered on</dt><dd>${esc(fmtDate(r.registered_on))}</dd></div>
        <div><dt>Valid until</dt><dd>${esc(fmtDate(r.valid_until))}</dd></div>
        <div><dt>Verified on</dt><dd>${esc(r.verified_on ? fmtDate(r.verified_on) : 'Not yet verified')}</dd></div>
        <div><dt>Card serial number</dt><dd class="mono">${esc(r.card_serial)}</dd></div>
      </dl>
    </div>
    <div class="card-foot">Checked against the ${esc(r.issuer)} on ${esc(fmtDateTime(r.checked_at))}. Confirm the photo and name match the person presenting the card.</div>
  </div>`);
}

/* ============================================================== CALL NOW */

async function callNow(button) {
  const number = (S.meta && S.meta.call_number) || '+918618075133';
  const ok = await confirmDialog({
    title: 'Call now?',
    message: `The AI Krishi assistant will call ${fmtMobile(number)} now. Answer the call and choose Kannada, Hindi or English to start talking.`,
    confirmLabel: 'Call now',
    danger: false,
  });
  if (!ok) return;
  setBusy(button, true, 'Calling');
  try {
    const r = await api('/api/portal/call', { method: 'POST' });
    toast(`Calling ${fmtMobile(r.to || number)}. Please answer the phone.`);
  } catch (err) {
    if (err.code !== 'UNAUTHENTICATED') toast(err.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

/* ============================================================== NOT FOUND */

async function viewNotFound(main) {
  main.innerHTML = `<div class="card">${emptyState({ icon: 'info', title: 'Page not found', text: 'The page you are looking for does not exist in the farmer portal.', action: '<a class="btn" href="/portal" data-link>Go to overview</a>' })}</div>`;
}

/* ================================================================== boot */

document.addEventListener('click', (e) => {
  const link = e.target.closest('a[data-link]');
  if (link && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0 && link.target !== '_blank') {
    e.preventDefault();
    navigate(link.getAttribute('href'));
    return;
  }
  const toggle = e.target.closest('[data-toggle-pw]');
  if (toggle) {
    const input = toggle.parentElement.querySelector('input');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    toggle.innerHTML = icon(show ? 'eyeOff' : 'eye');
    toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    toggle.title = show ? 'Hide password' : 'Show password';
    return;
  }
  const callBtn = e.target.closest('[data-call-now]');
  if (callBtn) {
    callNow(callBtn);
    return;
  }
  const copy = e.target.closest('[data-copy]');
  if (copy) {
    copyText(copy.dataset.copy).then(() => {
      const original = copy.innerHTML;
      copy.innerHTML = `${icon('check')}Copied`;
      setTimeout(() => { if (copy.isConnected) copy.innerHTML = original; }, 1600);
    });
  }
});

window.addEventListener('popstate', render);
render();
