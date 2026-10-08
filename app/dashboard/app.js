/* PaperLab dashboard - auth, polling, top bar, tabs, Strategies + drawer, Tape, Controls.
   Formatters, DOM builders, the table renderer, the equity chart, the Overview and Positions tabs and the
   shared trades/signals tables live in charts.js (loaded first). Every /api call carries Basic auth from
   sessionStorage; POSTs add X-PaperLab: 1. */
'use strict';

// ---- auth + fetch ----------------------------------------------------------------------------
const PW_KEY = 'paperlab.pw';
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
const Auth = {
  get: () => sessionStorage.getItem(PW_KEY),
  set: (pw) => sessionStorage.setItem(PW_KEY, pw),
  clear: () => sessionStorage.removeItem(PW_KEY),
  header: () => 'Basic ' + btoa(unescape(encodeURIComponent('admin:' + (Auth.get() || '')))),
};
async function api(path, opts = {}) {
  if (Auth.get() == null) { showLogin(); throw new ApiError('not logged in', 401); }
  const headers = { Authorization: Auth.header() };
  const init = { method: opts.method || 'GET', headers, cache: 'no-store' };
  if (init.method === 'POST') {
    headers['X-PaperLab'] = '1';
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body || {});
  }
  const res = await fetch(path, init);
  if (res.status === 401) { Auth.clear(); showLogin('session rejected — enter the password again'); throw new ApiError('unauthorized', 401); }
  if (opts.raw) return res;
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }
  if (!res.ok || (data && data.ok === false)) throw new ApiError((data && data.error) || 'HTTP ' + res.status, res.status);
  return data;
}
const post = (path, body) => api(path, { method: 'POST', body });

function showLogin(msg) {
  Poll.stop();
  $('#login').hidden = false;
  setText($('#login-err'), msg || '');
  $('#login-pw').value = '';
  setTimeout(() => $('#login-pw').focus(), 50);
}
async function tryLogin(pw) {
  Auth.set(pw);
  try {
    const st = await api('/api/state?curves=1');
    $('#login').hidden = true;
    Poll.accept(st);
    Poll.start();
  } catch (e) { setText($('#login-err'), e.status === 401 ? 'wrong password' : e.message); }
}

// ---- live state: pushed, never polled ----------------------------------------------------------
/* The server streams /api/state as a full snapshot on connect, then per-second DELTAS of only what
   changed, chained by seq (app/core/realtime.py). A delta whose base is not the seq we hold means
   something was missed: we reconnect and take a fresh snapshot instead of guessing. REST is still
   used for one-off reads -- right after an operator action, and for the equity chart's history --
   but never on a timer. The name `Poll` is kept because every action handler calls Poll.tick(). */
const Poll = {
  state: null, seq: 0, lastOk: 0, lastCurves: 0, busy: false,
  start() {
    Stream.start({ ws: '/api/ws', sse: '/api/stream' }, () => Auth.header());
    this.refresh(Nav.panel === 'overview');
  },
  stop() { Stream.stop(); this.seq = 0; },
  accept(st) {
    this.state = st; this.lastOk = Date.now();
    if (st.equity_curves) { this.lastCurves = Date.now(); Charts.setCurves(st.equity_curves); }
    try { render(st); } catch (e) { console.error('render failed', e); }   // a render bug must not read as a dead server
  },
  /** One REST read now (after an action, or when the chart needs its curves). */
  tick() { return this.refresh(Nav.panel === 'overview'); },
  async refresh(curves) {
    if (this.busy || Auth.get() == null) return;
    this.busy = true;
    try { this.accept(await api('/api/state' + (curves ? '?curves=1' : ''))); }
    catch (e) { if (e.status !== 401) console.warn('[state] refresh failed', e.message); }
    finally { this.busy = false; updateStale(); }
  },
  onState(msg) {
    if (msg.full) {
      if (!msg.state) return;
      this.seq = msg.seq;
      this.accept(Object.assign({}, msg.state));
      return;
    }
    if (!this.state || msg.seq <= this.seq) return;
    if (msg.base !== this.seq) { Stream.resync(); return; }
    const st = this.state;
    Object.assign(st, msg.set || {});
    for (const k of msg.unset || []) delete st[k];
    if (msg.rows && Object.keys(msg.rows).length) {
      st.strategies = (st.strategies || []).map((r) => msg.rows[r.id] || r);
      for (const [id, row] of Object.entries(msg.rows)) if (!st.strategies.some((r) => r.id === id)) st.strategies.push(row);
    }
    if (msg.order) {
      const by = new Map((st.strategies || []).map((r) => [r.id, r]));
      st.strategies = msg.order.map((id) => by.get(id)).filter(Boolean);
    }
    st.ts = msg.ts;
    this.seq = msg.seq;
    this.accept(st);
  },
};
function updateStale() {
  const b = $('#stale'), info = Stream.info();
  const quietFor = info.lastEventAt ? (Date.now() - info.lastEventAt) / 1000 : Infinity;
  const stale = Auth.get() != null && $('#login').hidden && (info.status !== 'LIVE' || quietFor > 45);
  b.hidden = !stale;
  if (stale) setText(b, 'STREAM ' + (info.status === 'LIVE' ? 'SILENT' : info.status) + ' — last event '
    + (info.lastEventAt ? fmtDur(quietFor) + ' ago' : 'never') + (info.retries ? ' · reconnect attempt ' + info.retries : ''));
  setText($('#updated'), info.status === 'LIVE' ? 'live · last change ' + (Poll.lastOk ? ago(Poll.lastOk) : '—') : 'stream ' + info.status.toLowerCase());
  const sp = $('#stream-pill');
  if (sp) {
    sp.className = 'pill ' + (info.status === 'LIVE' ? 'up' : info.status === 'OFF' ? 'ghost' : 'warn');
    setText(sp, (info.transport === 'sse' ? 'SSE' : 'WS') + ' ● ' + info.status);
    sp.title = info.transport === 'sse' ? 'live push over server-sent events (WebSocket unavailable on this network)'
      : 'live push over WebSocket';
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) updateStale(); });

// ---- navigation: ARENA / TRADERS / MARKET / VALIDATION / ACTIVITY (+ system) -------------------------
/* Every existing panel keeps its markup and renderer; the navigation decides which one shows. A
   competition sub-view is the `competition` panel with Competition.showTab(ctab). */
const NAV = {
  dashboard: { label: 'Dashboard', items: [{ id: 'home', label: 'Dashboard', panel: 'home', view: 'dashboard' }] },
  bots: { label: 'Bots', items: [{ id: 'bots', label: 'Bots', panel: 'home', view: 'bots' }] },
  markets: { label: 'Markets', items: [{ id: 'markets', label: 'Markets', panel: 'home', view: 'markets' }] },
  programs: { label: 'Programs', items: [{ id: 'programs', label: 'Programs', panel: 'programs' }] },
  analyzer: { label: 'Cost analyzer', items: [{ id: 'analyzer', label: 'Cost analyzer', panel: 'analyzer' }] },
  scout: { label: 'Scout', items: [{ id: 'scout', label: 'Scout', panel: 'scout' }] },
  system: { label: 'System', items: [
    { id: 'system', label: 'System', panel: 'system' },
    { id: 'controls', label: 'Controls', panel: 'controls' },
    { id: 'live-mirror', label: 'Providers & Live', panel: 'mirror' },
    { id: 'health', label: 'Health', panel: 'health' },
    { id: 'settings', label: 'Research', panel: 'competition', ctab: 'settings' }] },
};
const LEGACY_HASH = { overview: 'traders/bakeoff', strategies: 'traders/books', positions: 'market/positions', tape: 'market/tape',
  competition: 'validation/overview', controls: 'system/controls',
  // screens that moved when the research history left the front door
  'arena/aggressive': 'validation/aggressive', 'arena/aggressive-v3': 'validation/aggressive-v3',
  'arena/discovery': 'validation/discovery', 'arena/jev': 'validation/jev', 'arena/v4': 'validation/v4',
  'traders/leaderboard': 'validation/leaderboard', 'traders/bots': 'validation/bots',
  // V6: the live forward arena is the home; V5 and the V2 live shadow are research history
  'arena/v5': 'validation/v5', 'arena/live': 'validation/shadow', 'system/research': 'system/settings' };
const Nav = {
  primary: 'dashboard', secondary: 'home', panel: 'home',
  init() {
    const bar = $('#nav');
    for (const [id, grp] of Object.entries(NAV)) {
      if (grp.hidden) continue;
      bar.append(navButton(id, grp.label, () => this.go(id)));
    }
    $('#sys-btn').addEventListener('click', () => this.go('system'));
    window.addEventListener('hashchange', () => this.fromHash());
    this.fromHash(true);
  },
  fromHash(silent) {
    let raw = location.hash.replace(/^#\/?/, '');
    raw = LEGACY_HASH[raw] || raw;
    const [p, s] = raw.split('/');
    this.go(NAV[p] ? p : 'dashboard', s, silent);
  },
  openTrader(botId) { this.go('traders', 'traders', false, { traderId: botId || '' }); },
  goArena(view) { this.go(NAV[view] ? view : 'dashboard'); },
  go(primary, secondary, silent, extra) {
    const grp = NAV[primary] || NAV.dashboard;
    const item = grp.items.find((i) => i.id === secondary) || grp.items[0];
    this.primary = primary; this.secondary = item.id; this.panel = item.panel;
    Tabs.active = item.panel;
    $$('#nav [data-nav]').forEach((b) => b.classList.toggle('active', b.dataset.nav === primary));
    $('#sys-btn').classList.toggle('active', primary === 'system');
    const sub = clear($('#subnav'));
    if (grp.items.length > 1) for (const i of grp.items) {
      sub.append(h('button', { role: 'tab', type: 'button', class: i.id === item.id ? 'active' : '', text: i.label, onclick: () => this.go(primary, i.id) }));
    }
    sub.hidden = grp.items.length < 2;
    $('#alive').hidden = item.panel !== 'live';
    $$('main [data-panel]').forEach((el) => { el.hidden = el.dataset.panel !== item.panel; });
    const hash = '#' + primary + '/' + item.id;
    if (!silent && location.hash !== hash) history.replaceState(null, '', hash);
    if (item.panel === 'competition') { Competition.activate(); Competition.showTab(item.ctab); } else Competition.deactivate();
    if (item.panel === 'home') { Arena.view = item.view || 'dashboard'; Arena.load(); } else Arena.deactivate();
    if (item.panel === 'research') Home.load(); else Home.deactivate();
    if (item.panel === 'mirror') LiveMirror.load();
    if (item.panel === 'scout') Scout.load(); else Scout.deactivate();
    if (item.panel === 'traders') { Traders.botId = (extra && extra.traderId) || ''; Traders.load(); }
    if (item.panel === 'v4') V4View.load();
    if (item.panel === 'v5') V5View.load();
    if (item.panel === 'system') SystemView.load();
    if (item.panel === 'programs') ProgramsView.load();
    if (item.panel === 'analyzer') AnalyzerView.load();
    if (item.panel === 'live') Shadow.load();
    if (item.panel === 'activity') { if (!Activity.loaded) Activity.load(); else Activity.render(); }
    if (item.panel === 'health') Health.render();
    if (item.panel === 'candidates') Candidates.load();
    if (item.panel === 'v3') V3.load();
    if (item.panel === 'v31') V31.load();
    if (item.panel === 'overview') Poll.lastCurves = 0;
    if (Poll.state) { renderTab(Poll.state); if (item.panel === 'overview') Poll.tick(); }
  },
};
/** Kept for the renderers and handlers that read Tabs.active / call Tabs.show(name). */
const Tabs = {
  active: 'live',
  show(name) { const legacy = LEGACY_HASH[name]; if (legacy) { const [p, s] = legacy.split('/'); Nav.go(p, s); } },
};
function render(st) { renderTop(st); Lives.note(st.strategies); Tape.ingest(st.tape); Tape.syncPicker(st.strategies); renderTab(st); }
function renderTab(st) {
  if (Tabs.active === 'overview') renderOverview(st);
  else if (Tabs.active === 'strategies') renderStrategies(st);
  else if (Tabs.active === 'positions') renderPositions(st);
  else if (Tabs.active === 'tape') Tape.render();
  else if (Tabs.active === 'controls') renderControls(st);
  // competition, live, market, activity and health render from their own pushes
}

// ---- top bar ---------------------------------------------------------------------------------
const MODE_CLASS = { TESTNET: 'testnet', DEMO: 'demo', 'SPOT TESTNET': 'spot', 'BYBIT TESTNET': 'testnet', LIVE: 'live' };
/** Any LIVE* label must get the red badge — the live venue label carries the exchange name
 *  ("LIVE BYBIT"), so an exact-match lookup would silently downgrade the real-money warning. */
const modeClass = (label) => (String(label || '').startsWith('LIVE') ? 'live' : (MODE_CLASS[label] || 'other'));
function renderTop(st) {
  const eng = st.engine || {}, feed = (st.feed || {}).market || {}, risk = st.risk || {}, eq = st.equity || {};
  const badge = $('#mode-badge');
  setText(badge, st.venue_label || '?');
  badge.className = 'badge mode ' + modeClass(st.venue_label);
  $('#dry-pill').hidden = !st.dry_run;
  const age = feed.last_msg_age_s, fresh = feed.connected && isNum(age) && age < 15;
  const conn = $('#conn');
  conn.className = 'conn ' + (fresh ? 'ok' : feed.connected ? 'stale' : 'off');
  conn.title = 'market feed: ' + (feed.detail || '?') + (isNum(age) ? ' · last message ' + age.toFixed(1) + 's ago' : '') + ' · ' + (feed.reconnects || 0) + ' reconnects';
  setText($('#conn-txt'), !feed.connected ? 'feed down' : fresh ? 'feed ' + Math.round(age) + 's' : 'feed stale ' + Math.round(age || 0) + 's');
  const eqEl = $('#eq-total'), before = eqEl.dataset.v;
  setText(eqEl, fmtMoney(eq.total));
  if (isNum(eq.total) && before !== undefined && +before !== eq.total) flash(eqEl, eq.total - +before);
  if (isNum(eq.total)) eqEl.dataset.v = String(eq.total);
  const pnl = $('#eq-pnl');
  setText(pnl, fmtMoney(eq.pnl, true) + ' (' + fmtPct(eq.pnl_pct) + ')');
  pnl.className = 'num ' + signClass(eq.pnl);
  const daily = $('#daily'), dp = risk.daily_pnl_pct, limit = risk.halt_at_pct;
  setText(daily, risk.daily_halted ? 'DAILY HALT ' + fmtPct(dp) + ' (limit ' + fmtPct(limit, false, 1) + ')' : 'day ' + fmtPct(dp) + ' / halt at ' + fmtPct(limit, false, 1));
  daily.className = 'chip ' + (risk.daily_halted ? 'bad' : isNum(dp) && isNum(limit) && dp <= limit / 2 ? 'warn' : signClass(dp));
  const desync = Object.keys(st.desync || {}), db = $('#desync-badge');
  db.hidden = !desync.length;
  if (desync.length) { setText(db, 'DESYNC ' + desync.join(',')); db.title = desync.map((k) => k + ': ' + st.desync[k]).join('\n'); }
  const chip = $('#engine-chip'), state = eng.state || '?';
  setText(chip, 'engine ' + (eng.killed ? 'KILLED' : state === 'paused' ? 'paused: ' + (eng.paused_reason || '?') : state + (eng.warming ? ' · warming' : '')));
  chip.className = 'chip ' + (eng.killed || state === 'error' ? 'bad' : state === 'running' ? 'ok' : state === 'paused' ? 'warn' : 'accent');
  const kill = $('#kill-btn');
  setText(kill, eng.killed ? 'RESUME' : 'KILL ALL');
  kill.className = 'btn ' + (eng.killed ? 'warn' : 'danger');
}
async function killOrResume() {
  const killed = !!(Poll.state && Poll.state.engine && Poll.state.engine.killed);
  const ok = killed
    ? await confirmDialog('Resume engine', 'Clears the kill switch (and any daily halt) and sets the engine running. Strategies stay off until re-enabled.', { kind: 'warn', okText: 'Resume' })
    : await confirmDialog('KILL ALL', 'Flattens every virtual and exchange position, disables all strategies and pauses the engine.', { okText: 'Kill everything' });
  if (!ok) return;
  try {
    const r = killed ? await post('/api/engine/resume', { confirm: true }) : await post('/api/kill');
    toast(killed ? 'engine ' + r.state : 'killed — closed ' + r.closed_positions + ' positions', killed ? 'ok' : 'warn');
    Poll.tick();
  } catch (e) { toast(e.message, 'bad'); }
}

// ---- modal + toasts ---------------------------------------------------------------------------
function confirmDialog(title, text, opts = {}) {
  return new Promise((resolve) => {
    const m = $('#modal'), input = $('#modal-input'), ok = $('#modal-ok'), cancel = $('#modal-cancel');
    setText($('#modal-title'), title); setText($('#modal-text'), text);
    input.hidden = !opts.word; input.value = ''; input.placeholder = opts.word ? 'type ' + opts.word : '';
    ok.disabled = !!opts.word; ok.textContent = opts.okText || 'Confirm'; ok.className = 'btn ' + (opts.kind || 'danger');
    m.hidden = false;
    const done = (v) => { m.hidden = true; document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(false); else if (e.key === 'Enter' && !ok.disabled) done(true); };
    input.oninput = () => { ok.disabled = input.value.trim() !== opts.word; };
    ok.onclick = () => done(true); cancel.onclick = () => done(false);
    document.addEventListener('keydown', onKey);
    setTimeout(() => (opts.word ? input : ok).focus(), 30);
  });
}
function toast(msg, kind = 'ok') {
  const t = h('div', { class: 'toast ' + kind, text: msg });
  $('#toasts').append(t);
  setTimeout(() => t.classList.add('out'), 3600);
  setTimeout(() => t.remove(), 4000);
}

// ---- Strategies tab -----------------------------------------------------------------------------
const Pending = { enable: {} };
function renderStrategies(st) {
  const rows = st.strategies || [], killed = !!(st.engine && st.engine.killed), w = st.wallets || {};
  const base = isNum(w.balance_each) ? w.balance_each : 100;
  setText($('#strat-count'), rows.filter((r) => r.enabled).length + ' armed / ' + rows.length + ' loaded · own wallet of ' + fmtMoney(base) + ' each');
  renderTable($('#strat-table'), [
    { h: 'on', cls: 'sw', cell: (r) => switchEl(r, killed) },
    { h: 'strategy', cell: (r) => [h('span', { class: 'sid', text: r.id }), ' ', h('span', { class: 'sname', text: r.name }), badges(r.badges, r)] },
    { h: 'balance', num: true, cell: (r) => liveNum(r.id, r.equity, fmtMoney(r.equity), 'num ' + signClass(isNum(r.equity) ? r.equity - base : null)) },
    { h: 'open', cell: (r) => [String(r.open_positions), r.position_summary ? h('span', { class: 'sub', text: ' ' + r.position_summary }) : null] },
    { h: 'uPnL', num: true, cell: (r) => num(r.upnl) },
    { h: 'realized', num: true, cell: (r) => num(r.realized) },
    { h: 'all lives', num: true, cell: (r) => num(r.realized_all_lives) },
    { h: 'life', num: true, cell: (r) => lifeCell(r) },
    { h: 'rejects', num: true, cell: (r) => rejectsCell(r) },
    { h: 'trades', num: true, cell: (r) => String(r.trades_total || 0) },
    { h: 'WR', num: true, cell: (r) => [wrText(r.win_rate), h('span', { class: 'sub', text: ' ' + (r.wins || 0) + '/' + (r.losses || 0) })] },
    { h: 'last signal', cell: (r) => lastSignal(r.last_signal) },
    { h: 'error', cls: 'err', cell: (r) => (r.last_error ? h('span', { class: 'down', title: r.last_error, text: truncate(r.last_error, 40) }) : '') },
  ], rows, { onRow: (r) => Drawer.open(r.id), rowClass: lbRowClass, empty: 'no strategies loaded' });
}
/** A number cell that flashes when its pushed value changed since the last render. */
const LIVE_PREV = new Map();
function liveNum(key, value, text, cls) {
  const el = h('span', { class: cls || 'num', text });
  const prev = LIVE_PREV.get(key);
  if (isNum(value) && isNum(prev) && prev !== value) el.classList.add(value > prev ? 'flash-up' : 'flash-down');
  if (isNum(value)) LIVE_PREV.set(key, value);
  return el;
}
/** A respawned book keeps no HALTED badge: the badge survives only while the row itself is still halted. */
function badges(list, row) {
  return (list || []).filter((b) => !(b === 'HALTED' && row && !row.halted))
    .map((b) => h('span', { class: 'tag ' + (b === 'HALTED' ? 'down' : b === 'venue unsupported' ? 'warn' : 'ghost'), text: b }));
}
function switchEl(r, killed) {
  const why = r.halted ? 'halted — clear the halt first' : !r.supported ? 'venue unsupported' : killed ? 'killed — resume first' : '';
  const on = r.id in Pending.enable ? Pending.enable[r.id] : !!r.enabled;
  const input = h('input', { type: 'checkbox', checked: on, disabled: !!why && !on });
  input.addEventListener('change', async () => {
    const want = input.checked;
    Pending.enable[r.id] = want; input.disabled = true;
    try { await post('/api/strategies/' + r.id + '/enable', { enabled: want }); toast(r.id + (want ? ' enabled' : ' disabled')); }
    catch (e) { toast(r.id + ': ' + e.message, 'bad'); input.checked = !want; }
    delete Pending.enable[r.id];
    Poll.tick();
  });
  return h('label', { class: 'switch', title: why || (on ? 'enabled — click to disable' : 'off — click to enable') }, input, h('i'));
}
function lastSignal(s) {
  if (!s) return h('span', { class: 'muted', text: DASH });
  return h('span', { class: 'lastsig' }, h('span', { class: 'sub', text: ago(s.ts) + ' ' }), (s.symbol || '') + ' ', pill(s.side || s.kind, s.side), ' ',
    pill(s.status, s.status, s.reason || null), s.status === 'rejected' && s.code ? rejectCode(s.code, s.reason) : null);
}

// ---- strategy drawer ----------------------------------------------------------------------------
const Drawer = {
  sid: null,
  async open(sid) {
    this.sid = sid;
    $('#drawer').hidden = false; $('#drawer-backdrop').hidden = false;
    setText($('#drawer-id'), sid); setText($('#drawer-name'), ''); clear($('#drawer-badges')); clear($('#drawer-switch'));
    clear($('#drawer-body')).append(h('p', { class: 'muted', text: 'loading…' }));
    await this.load();
  },
  close() { this.sid = null; $('#drawer').hidden = true; $('#drawer-backdrop').hidden = true; },
  async load() {
    const sid = this.sid;
    if (!sid) return;
    try { const d = await api('/api/strategies/' + sid); if (this.sid === sid) this.render(d); }
    catch (e) { clear($('#drawer-body')).append(h('p', { class: 'down', text: e.message })); }
  },
  render(d) {
    const row = d.row || {}, sc = d.scoreboard || row, body = clear($('#drawer-body'));
    const open = d.open_positions || [], wrap = (t) => h('div', { class: 'tablewrap' }, t);
    setText($('#drawer-name'), d.name || '');
    clear($('#drawer-switch')).append(switchEl(row, !!(Poll.state && Poll.state.engine && Poll.state.engine.killed)));
    clear($('#drawer-badges')).append(h('span', null, badges(row.badges, row),
      lifeOf(row) > 1 ? h('span', { class: 'tag life-badge', text: 'LIFE ' + lifeOf(row) }) : null,
      h('span', { class: 'tag ghost', text: (d.timeframes || []).join(' / ') }),
      h('span', { class: 'tag ghost', text: 'min R:R ' + d.min_rr }), h('span', { class: 'tag ghost', text: 'max pos ' + d.max_positions }),
      h('span', { class: 'tag ghost', text: 'warmup ' + d.warmup_bars + ' bars' }), d.contributes_votes ? h('span', { class: 'tag accent', text: 'votes' }) : null));
    body.append(scoreTiles(sc, d.meta), section('lives', livesBlock(d.lives)), section('trade history', tradeHistory(d)));
    if (open.length) body.append(section('open positions', wrap(vposTable(h('table', { class: 'tbl' }), open))));
    body.append(section('last signals', wrap(signalsTable(h('table', { class: 'tbl' }), d.signals || [], true))),
      section('rejects today', rejectList(d.rejects_today)), section('notes', notesBlock(d)),
      section('doc', docBlock(d.doc)), section('params', paramsForm(d)), section('allocation', allocForm(d)), section('actions', actions(d)),
      section('state', d.id === 'S20' ? voteBlock(d.state) : kvList(d.state)));
  },
};
function docBlock(doc) {
  doc = doc || {};
  const el = h('div', { class: 'doc' });
  if (doc.idea) el.append(h('div', { class: 'idea', text: doc.idea }));
  for (const k of ['timeframe', 'symbols', 'entry', 'stop', 'targets', 'sizing', 'why_aggressive']) {
    if (doc[k]) el.append(h('span', { class: 'k', text: k.replace('_', ' ') }), h('span', { text: doc[k] }));
  }
  return el;
}
function paramsForm(d) {
  const specs = (d.params || {}).specs || [], values = (d.params || {}).values || {}, readers = {}, setters = {};
  const form = h('form', { class: 'params' });
  for (const s of specs) {
    const cur = values[s.name] != null ? values[s.name] : s.default;
    let ctrl;
    if (s.type === 'bool') {
      const cb = h('input', { type: 'checkbox', checked: !!cur });
      readers[s.name] = () => cb.checked; setters[s.name] = (v) => { cb.checked = !!v; };
      ctrl = h('label', { class: 'check' }, cb, h('span', { text: 'enabled' }));
    } else {
      const step = s.step != null ? s.step : s.type === 'int' ? 1 : 0.01;
      const range = h('input', { type: 'range', min: s.min, max: s.max, step, value: cur });
      const box = h('input', { type: 'number', min: s.min, max: s.max, step, value: cur, class: 'numbox' });
      range.addEventListener('input', () => { box.value = range.value; });
      box.addEventListener('input', () => { range.value = box.value; });
      readers[s.name] = () => (s.type === 'int' ? parseInt(box.value, 10) : +(+box.value).toFixed(6));
      setters[s.name] = (v) => { box.value = v; range.value = v; };
      ctrl = h('div', { class: 'slider' }, range, box);
    }
    form.append(h('div', { class: 'param' }, h('div', { class: 'plabel' }, h('span', { text: s.label || s.name }),
      h('span', { class: 'sub', text: ' default ' + fmtVal(s.default) + (s.min != null ? ' · ' + s.min + '–' + s.max : '') })),
      ctrl, s.help ? h('div', { class: 'help', text: s.help }) : null));
  }
  const msg = h('div', { class: 'msg' }), btn = h('button', { type: 'submit', class: 'btn primary', text: 'Apply params' });
  form.append(h('div', { class: 'btn-row' }, btn, h('button', { type: 'button', class: 'btn ghost', text: 'defaults',
    onclick: () => specs.forEach((s) => setters[s.name](s.default)) }), msg));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const changed = {};
    for (const s of specs) { const v = readers[s.name](); if (!Number.isNaN(v) && v !== values[s.name]) changed[s.name] = v; }
    if (!Object.keys(changed).length) { setText(msg, 'no changes'); msg.className = 'msg'; return; }
    btn.disabled = true;
    try {
      const r = await post('/api/strategies/' + d.id + '/params', changed);
      Object.assign(values, r.values || changed);
      for (const [k, v] of Object.entries(values)) if (setters[k]) setters[k](v);
      const warns = r.warnings || [];
      setText(msg, 'applied ' + Object.keys(changed).join(', ') + (warns.length ? ' — ' + warns.join('; ') : ''));
      msg.className = 'msg ' + (warns.length ? 'warn' : 'ok');
      toast(d.id + ' params applied');
    } catch (err) { setText(msg, err.message); msg.className = 'msg bad'; }
    btn.disabled = false;
  });
  return form;
}
function allocForm(d) {
  const row = d.row || {};
  const a = h('input', { type: 'number', min: 10, step: 10, value: row.allocation });
  const l = h('input', { type: 'number', min: 1, max: 25, step: 1, value: row.leverage });
  const m = h('input', { type: 'number', min: 0.05, max: 3, step: 0.05, value: row.size_mult });
  const msg = h('div', { class: 'msg', text: row.open_positions ? 'position open: allocation and leverage are locked until it closes (size mult still applies)' : '' });
  const btn = h('button', { type: 'submit', class: 'btn primary', text: 'Apply allocation' });
  const form = h('form', { class: 'alloc' }, field('allocation $', a), field('leverage (virtual)', l), field('size mult', m), h('div', { class: 'btn-row' }, btn, msg));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    if (+a.value !== row.allocation) body.allocation = +a.value;
    if (+l.value !== row.leverage) body.leverage = +l.value;
    if (+m.value !== row.size_mult) body.size_mult = +m.value;
    if (!Object.keys(body).length) { setText(msg, 'no changes'); msg.className = 'msg'; return; }
    btn.disabled = true;
    try { await post('/api/strategies/' + d.id + '/allocation', body); Object.assign(row, body); setText(msg, 'applied'); msg.className = 'msg ok'; toast(d.id + ' allocation updated'); Poll.tick(); }
    catch (err) { setText(msg, err.message); msg.className = 'msg bad'; }
    btn.disabled = false;
  });
  return form;
}
function actions(d) {
  const row = d.row || {}, msg = h('div', { class: 'msg' });
  const run = async (fn) => { try { await fn(); Drawer.load(); Poll.tick(); } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; } };
  return h('div', { class: 'btn-row' },
    h('button', { type: 'button', class: 'btn danger', text: 'Flatten ' + d.id, onclick: async () => {
      if (await confirmDialog('Flatten ' + d.id, 'Closes every open position of this strategy at the last price.', { okText: 'Flatten' })) {
        run(async () => { const r = await post('/api/strategies/' + d.id + '/flatten'); toast('closed ' + r.closed_positions + ' positions'); });
      } } }),
    h('button', { type: 'button', class: 'btn warn', text: 'Clear halt', hidden: !row.halted, onclick: () =>
      run(async () => { const r = await post('/api/strategies/' + d.id + '/halt/clear'); toast('halt cleared — new floor ' + fmtMoney(r.halt_floor)); }) }),
    msg);
}
// ---- Tape tab ----------------------------------------------------------------------------------------
const Tape = {
  fills: new Map(), signals: new Map(),
  ingest(t) {
    if (!t) return;
    for (const f of t.fills || []) this.fills.set(f.id, f);
    for (const s of t.signals || []) this.signals.set(s.id, s);
    for (const m of [this.fills, this.signals]) {
      if (m.size <= 200) continue;
      const keep = [...m.values()].sort((a, b) => b.ts - a.ts).slice(0, 200);
      m.clear();
      for (const r of keep) m.set(r.id, r);
    }
  },
  rows(map, applyRejected) {
    const q = $('#tape-filter').value.trim().toLowerCase(), rej = applyRejected && $('#tape-rejected').checked;
    const sid = $('#tape-strategy').value;
    return [...map.values()].sort((a, b) => b.ts - a.ts).filter((r) => (!rej || r.status === 'rejected') && (!sid || r.strategy_id === sid)
      && (!q || [r.strategy_id, r.symbol, r.status, r.kind, r.side].some((v) => v && String(v).toLowerCase().includes(q))));
  },
  /** Keep the strategy dropdown in step with the loaded set without clobbering the current choice. */
  syncPicker(strategies) {
    const sel = $('#tape-strategy'), ids = (strategies || []).map((s) => s.id);
    if (sel.dataset.ids === ids.join(',')) return;
    sel.dataset.ids = ids.join(',');
    const keep = sel.value;
    clear(sel).append(h('option', { value: '', text: 'all strategies' }));
    for (const s of strategies || []) sel.append(h('option', { value: s.id, text: s.id + ' · ' + s.name }));
    sel.value = ids.includes(keep) ? keep : '';
  },
  render() {
    const fills = this.rows(this.fills, false), sigs = this.rows(this.signals, true);
    setText($('#tape-count'), fills.length + ' fills · ' + sigs.length + ' signals');
    renderTable($('#fills-table'), [
      { h: 'time', cell: (r) => h('span', { title: ago(r.ts), text: fmtTime(r.ts) }) },
      { h: 'strat', cell: (r) => h('button', { type: 'button', class: 'sid linkish', text: r.strategy_id || DASH, title: 'open ' + r.strategy_id,
        onclick: () => r.strategy_id && Drawer.open(r.strategy_id) }) },
      { h: 'sym', cell: (r) => r.symbol },
      { h: 'side', cell: (r) => [pill(r.side, r.side), r.position_side ? h('span', { class: 'sub', text: ' ' + r.position_side + (r.is_open ? ' open' : ' close') }) : null] },
      { h: 'qty', num: true, cell: (r) => fmtQty(r.qty) }, { h: 'price', num: true, cell: (r) => fmtPrice(r.price) },
      { h: 'fee', num: true, cell: (r) => fmtMoney(r.fee) }, { h: 'slip', num: true, cell: (r) => (isNum(r.slippage_bps) ? r.slippage_bps.toFixed(1) + 'bp' : DASH) },
      { h: 'kind', cell: (r) => [pill(r.kind, r.kind), r.simulated ? [' ', pill('SIMULATED', 'ghost')] : null] },
      { h: 'pnl', num: true, cell: (r) => (r.is_open ? h('span', { class: 'muted', text: DASH }) : num(r.pnl)) },
      { h: 'reason', cell: (r) => h('span', { class: 'sub', title: r.reason || null, text: truncate(r.reason || '', 36) }) },
    ], fills, { empty: 'no fills yet' });
    signalsTable($('#signals-table'), sigs, false);
  },
};

// ---- Controls tab ------------------------------------------------------------------------------------
const EXPORTS = ['fills', 'signals', 'orders', 'equity', 'events', 'strategy_daily', 'analysis_notes'];
/** ARM LIVE panel. Pre-flight checks must be green and a strategy chosen; then the operator holds a
 *  3s button (the physical commitment is the confirmation -- no typed phrase, no modal). */
const HOLD_MS = 3000;       // real hold duration before arming
const RING_LEN = 289.03;    // 2*PI*46
const Live = {
  status: null, hold: { active: false, started: 0, raf: 0, triggered: false },
  pick() { const el = $('#solo-pick'); return el && el.value ? el.value : ''; },
  async load() {
    // Evaluate the checks against the strategy selected for live trading, not "everything enabled" -
    // the other books stay enabled on purpose so the paper bake-off keeps running beside the live one.
    const sid = this.pick();
    try { this.status = await api('/api/live' + (sid ? '?strategy=' + encodeURIComponent(sid) : '')); }
    catch (e) { this.status = null; }
    this.paint();
  },
  render(st) {
    const pick = $('#solo-pick');
    if (pick && pick.options.length !== (st.strategies || []).length) {
      clear(pick);
      // A placeholder first, so the picker never silently pre-selects a book for REAL trading. It
      // defaulted to the first enabled strategy once and armed S01 instead of the intended S15.
      pick.append(h('option', { value: '', text: '— choose a strategy —' }));
      for (const s of st.strategies || []) pick.append(h('option', { value: s.id, text: s.id + ' — ' + s.name }));
      pick.value = (st.live_strategies || [])[0] || (st.winners || [])[0] || '';
    }
    // /api/state already knows whether orders are real; refresh the detailed checks alongside it
    if (!this.status || this.status.live !== !st.dry_run) this.load(); else this.paint();
  },
  paint() {
    const s = this.status;
    const badge = $('#live-badge'), ul = clear($('#live-checks'));
    if (!s) { setText(badge, 'unknown'); badge.className = 'pill'; return; }
    setText(badge, s.live ? 'LIVE — REAL MONEY' : 'paper');
    badge.className = 'pill ' + (s.live ? 'down' : 'up');
    $('.live-card').classList.toggle('armed', !!s.live);
    for (const c of s.checks || []) {
      ul.append(h('li', { class: c.ok ? 'ok' : 'bad' },
        h('span', { class: 'tick', text: c.ok ? '✓' : '✗' }),
        h('span', { class: 'nm', text: c.id }),
        h('span', { class: 'sub', text: c.detail || '' }),
        c.ok ? null : h('span', { class: 'why', text: c.help || '' })));
    }
    if (!ul.children.length) ul.append(h('li', { class: 'muted', text: 'no checks reported' }));
    const cred = s.credentials || {}, cb = $('#cred-badge');
    setText(cb, cred.connected ? 'connected ' + (cred.fingerprint || '') + ' (' + cred.source + ')' : 'not connected');
    cb.className = 'pill ' + (cred.connected ? 'up' : '');
    $('#cred-clear').hidden = !cred.connected;
    if (s.wallet && isNum(s.wallet.wallet)) {
      setText($('#cred-msg'), 'exchange wallet ' + fmtMoney(s.wallet.wallet)
        + ' · available ' + fmtMoney(s.wallet.available));
      $('#cred-msg').className = 'msg ok';
    }
    // The stepper + hold button: shown only when not yet armed. The arm button stays disabled until
    // every check is green and a strategy is picked -- same gating as before, no typed phrase.
    const stepper = $('#live-stepper');
    stepper.hidden = !!s.live;
    $('#live-disarm').hidden = !s.live;
    const arm = $('#live-arm');
    arm.disabled = !s.ready || !this.pick();
    const msg = $('#live-msg'), progress = $('#live-hold-progress');
    if (progress) setText(progress, HOLD_MS / 1000 + 's');
    if (!s.live && arm.disabled) {
      const bad = (s.checks || []).filter((c) => !c.ok).map((c) => c.id);
      setText(msg, bad.length ? 'cannot arm yet — ' + bad.join(', ') + ' (see the red rows above)'
        : 'pick a strategy to enable the arm button');
      msg.className = 'msg warn';
    } else if (!s.live) {
      setText(msg, 'all checks pass — hold the button for ' + (HOLD_MS / 1000) + ' seconds to arm');
      msg.className = 'msg ok';
    }
    arm.title = arm.disabled ? 'blocked: ' + (((s.checks || []).filter((c) => !c.ok).map((c) => c.id).join(', ')) || 'pick a strategy')
      : 'press and hold to arm live trading';
  },
  /** Tick the ring + label while a hold is in progress. requestAnimationFrame so it tracks the actual
   *  elapsed time and stops cleanly on release. */
  holdTick(btn) {
    const H = this.hold;
    if (!H.active) return;
    const elapsed = Date.now() - H.started;
    const pct = Math.min(1, elapsed / HOLD_MS);
    btn.dataset.progress = Math.round(pct * 100);
    const ring = btn.querySelector('.hold-ring-fill');
    ring.style.strokeDashoffset = String(RING_LEN * (1 - pct));
    const remaining = Math.max(0, Math.ceil((HOLD_MS - elapsed) / 100) / 10).toFixed(1);
    setText($('#live-hold-progress'), remaining + 's');
    if (pct >= 1) { H.triggered = true; this.holdEnd(btn, true); return; }
    H.raf = requestAnimationFrame(() => this.holdTick(btn));
  },
  holdStart(btn) {
    if (btn.disabled) return;
    this.hold = { active: true, started: Date.now(), raf: 0, triggered: false };
    btn.dataset.pressing = 'true'; btn.dataset.progress = '0';
    btn.querySelector('.hold-ring-fill').style.strokeDashoffset = String(RING_LEN);
    setText($('#live-hold-progress'), (HOLD_MS / 1000).toFixed(1) + 's');
    this.hold.raf = requestAnimationFrame(() => this.holdTick(btn));
  },
  holdEnd(btn, completed) {
    cancelAnimationFrame(this.hold.raf);
    btn.dataset.pressing = 'false';
    btn.querySelector('.hold-ring-fill').style.strokeDashoffset = String(RING_LEN);
    if (completed && this.hold.triggered) {
      btn.dataset.armed = 'true';
      setText(btn.querySelector('.hold-action'), 'Arming…');
      this.hold = { active: false, started: 0, raf: 0, triggered: false };
      this.commit();
      setTimeout(() => { btn.dataset.armed = 'false'; this.paint(); }, 1500);
    } else {
      this.hold = { active: false, started: 0, raf: 0, triggered: false };
      setText($('#live-hold-progress'), (HOLD_MS / 1000) + 's');
    }
  },
  async commit() {
    const s = this.status || {}, msg = $('#live-msg'), sid = this.pick();
    try {
      const r = await post('/api/live/arm', { strategies: [sid] });
      setText(msg, 'ARMED — real orders are live' + (r.wallet ? ' — wallet ' + fmtMoney(r.wallet.wallet) : ''));
      msg.className = 'msg bad'; await this.load(); Poll.tick();
    } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
  },
  init() {
    const arm = $('#live-arm');
    // Pointer events cover mouse + touch + pen; we also honour Space for keyboard a11y.
    const onDown = (e) => { e.preventDefault(); this.holdStart(arm); };
    const onUp = () => { if (this.hold.active) this.holdEnd(arm, false); };
    arm.addEventListener('pointerdown', onDown);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
    arm.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); this.holdStart(arm); } });
    arm.addEventListener('keyup', (e) => { if (e.key === ' ' || e.key === 'Enter') onUp(); });
    const msg = $('#live-msg');
    $('#cred-save').addEventListener('click', async () => {
      const k = $('#cred-key'), sec = $('#cred-secret'), cmsg = $('#cred-msg');
      setText(cmsg, 'verifying against the exchange…'); cmsg.className = 'msg';
      try {
        const r = await post('/api/live/credentials', { key: k.value.trim(), secret: sec.value.trim() });
        k.value = ''; sec.value = '';       // never leave the secret sitting in the DOM
        setText(cmsg, 'connected ' + (r.fingerprint || '') + ' — wallet '
          + fmtMoney((r.wallet || {}).wallet) + ' · available ' + fmtMoney((r.wallet || {}).available));
        cmsg.className = 'msg ok'; await this.load(); Poll.tick();
      } catch (e) { setText(cmsg, e.message); cmsg.className = 'msg bad'; }
    });
    $('#solo-pick').addEventListener('change', () => this.load());
    $('#solo-btn').addEventListener('click', async () => {
      const sid = $('#solo-pick').value, smsg = $('#solo-msg');
      if (!sid) { setText(smsg, 'choose a strategy first'); smsg.className = 'msg bad'; return; }
      if (!(await confirmDialog('Disable the other strategies?',
        'This STOPS the paper bake-off: only ' + sid + ' will be able to open positions. It is NOT '
        + 'required for live trading — only the armed book reaches the exchange either way.',
        { kind: 'warn', okText: 'Disable the others' }))) return;
      try {
        const r = await post('/api/strategies/' + sid + '/solo', {});
        setText(smsg, sid + ' is now the only enabled strategy (' + (r.disabled || []).length + ' disabled)');
        smsg.className = 'msg ok'; await this.load(); Poll.tick();
      } catch (e) { setText(smsg, e.message); smsg.className = 'msg bad'; }
    });
    $('#cred-clear').addEventListener('click', async () => {
      const cmsg = $('#cred-msg');
      try {
        await post('/api/live/credentials/clear', {});
        setText(cmsg, 'disconnected'); cmsg.className = 'msg'; await this.load();
      } catch (e) { setText(cmsg, e.message); cmsg.className = 'msg bad'; }
    });
    $('#live-disarm').addEventListener('click', async () => {
      if (!(await confirmDialog('Back to paper?', 'Flattens the exchange and returns to simulated fills.',
        { okText: 'Disarm' }))) return;
      try {
        await post('/api/live/disarm', {});
        setText(msg, 'disarmed — exchange flattened, back to paper fills'); msg.className = 'msg ok';
        await this.load(); Poll.tick();
      } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
    });
  },
};

function initControls() {
  Live.init();
  $$('#controls-engine [data-act]').forEach((b) => b.addEventListener('click', async () => {
    const act = b.dataset.act, msg = $('#engine-msg');
    if (act === 'resume' && !(await confirmDialog('Resume engine', 'Clears the kill switch / daily halt and runs the engine.', { kind: 'warn', okText: 'Resume' }))) return;
    if (act === 'flatten' && !(await confirmDialog('Flatten all', 'Closes every virtual position at the last price and flattens the exchange.', { okText: 'Flatten' }))) return;
    try {
      const r = await post(act === 'resume' ? '/api/engine/resume' : '/api/' + act, act === 'resume' ? { confirm: true } : {});
      setText(msg, act + ': ok' + (r.state ? ' — engine ' + r.state : '') + (isNum(r.closed_positions) ? ' — closed ' + r.closed_positions + ' positions' : ''));
      msg.className = 'msg ok'; Poll.tick();
    } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
  }));
  for (const [id, on] of [['#arm-all', true], ['#disarm-all', false]]) {
    $(id).addEventListener('click', async () => {
      const m = $('#armall-msg');
      if (!on && !(await confirmDialog('Disable every strategy?',
        'Nothing will open new positions. Open positions are NOT closed.', { okText: 'Disable all' }))) return;
      try {
        const r = await post('/api/strategies/arm-all', { enabled: on });
        setText(m, (on ? 'armed ' : 'disabled ') + r.changed.length + ' — ' + r.enabled + ' now enabled'
          + (r.skipped.length ? ' (skipped ' + r.skipped.join(', ') + ')' : ''));
        m.className = 'msg ok'; Poll.tick();
      } catch (e) { setText(m, e.message); m.className = 'msg bad'; }
    });
  }
  const word = $('#reset-word'), rbtn = $('#reset-btn'), symInput = $('#symbols-input');
  word.addEventListener('input', () => { rbtn.disabled = word.value.trim() !== 'RESET'; });
  rbtn.addEventListener('click', async () => {
    const msg = $('#reset-msg');
    try {
      const r = await post('/api/reset', { confirm: true });
      setText(msg, 'reset done — epoch ' + r.epoch); msg.className = 'msg ok'; word.value = ''; rbtn.disabled = true;
      Tape.fills.clear(); Tape.signals.clear(); Poll.lastCurves = 0; Poll.tick();
    } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
  });
  symInput.addEventListener('input', () => { symInput.dataset.dirty = '1'; });
  $('#symbols-btn').addEventListener('click', async () => {
    const msg = $('#symbols-msg'), syms = symInput.value.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
    try { const r = await post('/api/symbols', { symbols: syms }); setText(msg, 'symbols: ' + r.symbols.join(', ')); msg.className = 'msg ok'; delete symInput.dataset.dirty; Poll.tick(); }
    catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
  });
  for (const t of EXPORTS) $('#exports').append(h('button', { type: 'button', class: 'btn', text: t + '.csv', onclick: () => exportCsv(t) }));
}
async function exportCsv(table) {
  const msg = $('#export-msg');
  try {
    const res = await api('/api/export.csv?table=' + table, { raw: true });
    if (!res.ok) { let err = ''; try { err = (await res.json()).error; } catch (e) { /* not json */ } throw new Error(err || 'HTTP ' + res.status); }
    const blob = await res.blob(), url = URL.createObjectURL(blob), a = h('a', { href: url, download: 'paperlab_' + table + '.csv' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    setText(msg, 'downloaded paperlab_' + table + '.csv (' + NF.n1.format(blob.size / 1024) + ' KB)'); msg.className = 'msg ok';
  } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
}
function renderControls(st) {
  const eng = st.engine || {}, feed = st.feed || {}, symInput = $('#symbols-input');
  if (document.activeElement !== symInput && !symInput.dataset.dirty) symInput.value = (eng.symbols || []).join(', ');
  clear($('#runtime')).append(kvList({ mode: st.mode, venue: st.venue_label, venue_kind: st.venue_kind, dry_run: st.dry_run, epoch: st.epoch,
    engine: eng.state + (eng.paused_reason ? ' (' + eng.paused_reason + ')' : ''), killed: eng.killed, warming: eng.warming, uptime: fmtDur(eng.uptime_s),
    strategies_loaded: eng.strategies_loaded, symbols: (eng.symbols || []).join(', '), server_time: fmtTime(st.ts) }));
  Live.render(st);
  clear($('#boot')).append(kvList(eng.boot || {}));
  clear($('#feed')).append(kvList({ market: feed.market, user: feed.user }));
  const ul = clear($('#feed-events'));
  for (const ev of (st.feed_events || []).slice(-10).reverse()) ul.append(h('li', null, h('span', { class: 'sub', text: fmtTime(ev.ts) + ' ' }), pill(ev.stream, ev.connected ? 'up' : 'down'), ' ', ev.detail || ''));
  if (!ul.children.length) ul.append(h('li', { class: 'muted', text: 'no feed events yet' }));
}

// ---- boot ------------------------------------------------------------------------------------------------
function init() {
  Competition.init();
  Shadow.base = Activity.base = Candidates.base = V3.base = V31.base = '/api/competition';
  Activity.private = true;
  wireLiveViews();
  wireArena();
  Stream.on('state', (m) => Poll.onState(m));
  Stream.on('fill', (d) => Activity.onEvent('fill', d));
  Stream.on('signal', (d) => Activity.onEvent('signal', d));
  Stream.on('note', (d) => Activity.onEvent('note', d));
  Stream.on('competition', (d) => Competition.onEvent(d));
  Stream.on('equity', () => { if (Nav.panel === 'overview' && !document.hidden) Poll.refresh(true); });
  Stream.on('hello', (d) => { if (d.resumed === false && Poll.state && Nav.panel === 'overview') Poll.refresh(true); });
  Stream.on('unauthorized', () => { Auth.clear(); showLogin('session rejected — enter the password again'); });
  Stream.onStatus(() => updateStale());
  Safety.init(['ws', 'data', 'jev']);        // execution, kill switch and engine have their own header controls here
  Nav.init(); initControls(); Charts.init($('#equity-chart'));
  $('#login-form').addEventListener('submit', (e) => { e.preventDefault(); tryLogin($('#login-pw').value); });
  $('#logout').addEventListener('click', (e) => { e.preventDefault(); Auth.clear(); Poll.stop(); Poll.state = null; Drawer.close(); showLogin('logged out'); });
  $('#kill-btn').addEventListener('click', killOrResume);
  $('#drawer-close').addEventListener('click', () => Drawer.close());
  $('#drawer-backdrop').addEventListener('click', () => Drawer.close());
  $('#drawer-refresh').addEventListener('click', () => Drawer.load());
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#drawer').hidden && $('#modal').hidden) Drawer.close(); });
  $('#tape-filter').addEventListener('input', () => Tape.render());
  $('#tape-rejected').addEventListener('change', () => Tape.render());
  $('#tape-strategy').addEventListener('change', () => Tape.render());
  setInterval(updateStale, 1000);
  if (Auth.get() == null) showLogin(); else Poll.start();
}
document.addEventListener('DOMContentLoaded', init);
