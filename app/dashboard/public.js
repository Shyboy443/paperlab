/* PaperLab - public read-only page: the lean arena (Dashboard / Bots / Markets / System).

   Every request is a plain GET against /api/public/... (no credential, no cookie); the push channel is the PUBLIC
   one (WebSocket /api/public/ws, SSE fallback), which only carries events the server published as public. Nothing
   here can mutate. `post()` refuses before any request is made, and the server has no public POST route anyway.

   URLs are real paths so a link survives a reload:
     /public/competition            Dashboard (?p=v8|v7|v6 picks the program)
     /public/competition/bots       Bots
     /public/competition/markets    Markets
     /public/competition/programs   Programs (lab.js)
     /public/competition/analyzer   Cost analyzer (lab.js; asks for the dashboard password)
     /public/competition/system     System (research history links to the server-rendered report)
   Older paths from the research-era page land on the Dashboard. */
'use strict';

const PUBLIC_BASE = '/api/public/competition';

class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

/** Plain GET. No Authorization header, no X-PaperLab, no cookies -- a normal browser request. */
async function api(path) {
  let res;
  try {
    res = await fetch(path, { method: 'GET', cache: 'no-store' });
  } catch (e) {
    throw new ApiError('network error: ' + (e && e.message ? e.message : e), 0);
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }
  if (!res.ok || (data && data.ok === false)) throw new ApiError((data && data.error) || ('HTTP ' + res.status), res.status);
  return data;
}

/** Read-only: refuse locally and never issue the request. */
async function post() {
  throw new ApiError('read-only inspection: this page cannot change anything', 403);
}

function toast(msg, kind) {
  const box = $('#toasts');
  if (!box) return;
  const el = h('div', { class: 'toast ' + (kind === 'err' ? 'err' : ''), text: String(msg) });
  box.append(el);
  setTimeout(() => el.remove(), 4000);
}

const NAV = [
  { id: 'dashboard', path: '', label: 'Dashboard', panel: 'home', view: 'dashboard' },
  { id: 'bots', path: 'bots', label: 'Bots', panel: 'home', view: 'bots' },
  { id: 'markets', path: 'markets', label: 'Markets', panel: 'home', view: 'markets' },
  { id: 'programs', path: 'programs', label: 'Programs', panel: 'programs' },
  { id: 'analyzer', path: 'analyzer', label: 'Cost analyzer', panel: 'analyzer' },
  { id: 'scout', path: 'scout', label: 'Scout', panel: 'scout' },
  { id: 'system', path: 'system', label: 'System', panel: 'system' },
];

function routeFromLocation() {
  const first = location.pathname.replace(/^\/public\/competition\/?/, '').split('/').filter(Boolean)[0] || '';
  return NAV.find((n) => n.path === first) || NAV[0];
}

const Nav = {
  panel: 'home',
  init() {
    const bar = $('#nav');
    for (const n of NAV) bar.append(navButton(n.id, n.label, () => this.go(n.id)));
  },
  go(id) {
    const n = NAV.find((x) => x.id === id) || NAV[0];
    const url = '/public/competition' + (n.path ? '/' + n.path : '') + location.search;
    if (location.pathname + location.search !== url) history.pushState(null, '', url);
    this.show(n);
  },
  goArena(view) { this.go(view); },
  show(n) {
    this.panel = n.panel;
    $$('#nav [data-nav]').forEach((b) => b.classList.toggle('active', b.dataset.nav === n.id));
    $$('main [data-panel]').forEach((el) => { el.hidden = el.dataset.panel !== n.panel; });
    if (n.panel === 'home') { Arena.view = n.view; Arena.load(); } else Arena.deactivate();
    if (n.panel === 'system') SystemView.load();
    if (n.panel === 'programs') ProgramsView.load();
    if (n.panel === 'analyzer') AnalyzerView.load();
    window.scrollTo(0, 0);
    if (n.panel === 'scout') Scout.load(); else Scout.deactivate();
  },
};

/* One status light instead of a row of pills: paper execution, the push stream and the live market data. */
const StatusLight = {
  stream: 'CONNECTING', data: null,
  paint() {
    const el = $('#status-light');
    if (!el) return;
    const ok = this.stream === 'LIVE' && this.data !== false;
    setText(el, 'PAPER ● ' + (this.stream !== 'LIVE' ? 'STREAM ' + this.stream : this.data === false ? 'DATA STALE' : 'LIVE'));
    el.className = 'pill ' + (ok ? 'up' : 'warn');
  },
};

function init() {
  Nav.init();
  wireArena();
  Stream.onStatus((info) => {
    StatusLight.stream = info.status; StatusLight.paint();
    setText($('#pub-updated'), info.status === 'LIVE' ? 'live since ' + fmtTime(info.connectedAt) : 'stream ' + info.status.toLowerCase());
  });
  Stream.on('health', (d) => {
    const feeds = ['v14', 'v13', 'v12', 'v11', 'v8', 'v7', 'v6'].map((k) => (d || {})[k]).filter((x) => x && x.enabled);   // V9's stocks sleep overnight
    if (feeds.length) StatusLight.data = feeds.every((f) => f.ws !== false && !(isNum(f.klines_age_s) && f.klines_age_s > 150));
    StatusLight.paint();
  });
  Nav.show(routeFromLocation());
  Stream.start({ ws: '/api/public/ws', sse: '/api/public/stream' });
  window.addEventListener('popstate', () => Nav.show(routeFromLocation()));
}

window.addEventListener('error', (e) => console.error('[public] uncaught error', e.error || e.message));
window.addEventListener('unhandledrejection', (e) => console.error('[public] unhandled rejection', e.reason));
document.addEventListener('DOMContentLoaded', init);
