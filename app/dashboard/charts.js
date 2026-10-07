/* PaperLab dashboard - render toolkit, equity chart, Overview (bake-off leaderboard + lab journal),
   Positions, the shared trade/signal tables and the drawer's content blocks (scoreboard, trade history,
   rejects, notes, votes). app.js (loaded after this file) owns auth, polling, the top bar, the Strategies
   tab, the drawer shell, Tape and Controls. Everything is built with textContent: no innerHTML anywhere. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const DASH = '—';

// ---- formatters ----------------------------------------------------------------------------
const NF = {
  money: new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  qty: new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }),
  price: new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }),
  n1: new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }),
};
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
function fmtMoney(v, signed) {
  if (!isNum(v)) return DASH;
  return (v < 0 ? '-$' : signed && v > 0 ? '+$' : '$') + NF.money.format(Math.abs(v));
}
function fmtPct(v, signed = true, d = 2) { return isNum(v) ? (signed && v > 0 ? '+' : '') + v.toFixed(d) + '%' : DASH; }
function fmtNum(v, d = 2) { return isNum(v) ? v.toFixed(d) : DASH; }
function fmtQty(v) { return isNum(v) ? NF.qty.format(v) : DASH; }
function fmtPrice(v) { return isNum(v) ? NF.price.format(v) : DASH; }
function fmtTime(ts) { return ts ? new Date(ts).toLocaleTimeString('en-GB', { hour12: false }) : DASH; }
function fmtDur(s) {
  if (!isNum(s)) return DASH;
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm' + (s % 60 ? ' ' + (s % 60) + 's' : '');
  if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
  return Math.floor(s / 86400) + 'd ' + Math.floor((s % 86400) / 3600) + 'h';
}
function ago(ts) { return ts ? fmtDur((Date.now() - ts) / 1000) + ' ago' : ''; }
function signClass(v) { return !isNum(v) || v === 0 ? '' : v > 0 ? 'up' : 'down'; }
function truncate(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function fmtVal(v) {
  if (v == null) return DASH;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (isNum(v)) {
    if (v > 1e12 && v < 4e12) return fmtTime(v) + ' (' + ago(v) + ')'; // ms timestamps
    return Number.isInteger(v) ? String(v) : Math.abs(v) >= 1000 ? v.toFixed(2) : String(+v.toFixed(6));
  }
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

// ---- DOM builders --------------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (typeof v === 'boolean') el[k] = v; // checked, disabled, hidden
      else el.setAttribute(k, v);
    }
  }
  for (const c of children.flat(3)) if (c != null && c !== false) el.append(c.nodeType ? c : String(c));
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function setText(el, s) { if (el.textContent !== String(s)) el.textContent = s; }
function num(v, fmt = fmtMoney) { return h('span', { class: 'num ' + signClass(v), text: fmt(v, true) }); }
const PILL_KIND = { approved: 'up', rejected: 'down', shadow: 'muted', warmup: 'accent', stale: 'ghost', exit: 'warn',
  exit_noop: 'ghost', long: 'up', short: 'down', live: 'up', open: 'accent', close: 'warn', halt: 'down', kill: 'down' };
function pill(text, kind, title) {
  return h('span', { class: 'pill ' + (PILL_KIND[kind] || kind || PILL_KIND[text] || 'muted'), text: text == null ? '?' : text, title: title || null });
}
function tile(label, value, cls, sub) {
  return h('div', { class: 'tile' }, h('span', { class: 'lbl', text: label }), h('span', { class: 'val num ' + (cls || ''), text: value }),
    sub != null ? h('span', { class: 'sub', text: sub }) : null);
}
function section(title, node) { return h('section', null, h('h3', { text: title }), node); }
function field(label, input) { return h('label', { class: 'field' }, h('span', { text: label }), input); }

/** Flat key/value grid. Nested objects are flattened with dotted keys; arrays are JSON-stringified. */
function kvList(obj) {
  const rows = [];
  (function walk(o, prefix) {
    for (const [k, v] of Object.entries(o || {})) {
      const key = prefix ? prefix + '.' + k : k;
      if (v && typeof v === 'object' && !v.nodeType && !Array.isArray(v) && Object.keys(v).length && rows.length < 400) walk(v, key);
      else rows.push([key, v]);
    }
  })(obj, '');
  if (!rows.length) return h('div', { class: 'sub', text: 'empty' });
  return h('div', { class: 'kv' }, rows.map(([k, v]) => [h('span', { class: 'k', text: k }),
    h('span', { class: 'v ' + (typeof v === 'boolean' ? (v ? 'up' : 'muted') : ''), text: v && v.nodeType ? null : fmtVal(v) },
      v && v.nodeType ? v : null)]));          // a value may be a ready-made node (e.g. a coloured status)
}

/** cols: [{h, cell(row) -> node|string|array, num, cls, sort}]; opts: {onRow, rowClass, empty}. Header is built once
    per distinct header set, so a sorted column whose caption carries the arrow rebuilds (and rebinds) the header. */
function renderTable(table, cols, rows, opts = {}) {
  const key = cols.map((c) => c.h).join('|');
  if (table.dataset.key !== key) {
    clear(table);
    table.dataset.key = key;
    table.append(h('thead', null, h('tr', null, cols.map((c) => h('th', {
      class: (c.num ? 'num ' : '') + (c.cls || '') + (c.sort ? ' sortable' : ''), text: c.h, onclick: c.sort || null,
      title: c.title || null,
    })))), h('tbody'));
  }
  const body = h('tbody');
  if (!rows.length) body.append(h('tr', { class: 'empty' }, h('td', { colspan: cols.length, text: opts.empty || 'nothing yet' })));
  rows.forEach((r, i) => {
    const tr = h('tr', { class: opts.rowClass ? opts.rowClass(r) : null });
    // cell() gets the row index too: a rank column has no other way to know its position
    for (const c of cols) tr.append(h('td', { class: (c.num ? 'num ' : '') + (c.cls || '') }, c.cell(r, i)));
    if (opts.onRow) {
      tr.classList.add('click');
      tr.addEventListener('click', (e) => { if (!e.target.closest('input,button,label,a')) opts.onRow(r); });
    }
    body.append(tr);
  });
  table.tBodies[0].replaceWith(body);
}

// ---- equity chart --------------------------------------------------------------------------
const SERIES_COLORS = ['#58a6ff', '#f778ba', '#d2a8ff', '#ffa657', '#7ee787', '#79c0ff', '#ff7b72', '#e3b341', '#56d4dd',
  '#a5d6ff', '#f0883e', '#8ddb8c', '#ff9bce', '#bc8cff', '#ffdf5d', '#39c5cf', '#db6d28', '#9e9e9e', '#c9d1d9', '#3fb950'];
const Charts = (() => {
  let chart = null, el = null, live = false, curves = {}, sig = '';
  const series = new Map();
  const shown = new Set(JSON.parse(sessionStorage.getItem('paperlab.curves') || '[]'));
  const colorFor = (k) => SERIES_COLORS[(parseInt(k.replace(/\D/g, ''), 10) || 0) % SERIES_COLORS.length];

  function init(container) {
    el = container;
    if (!window.LightweightCharts) { el.classList.add('chart-fallback'); el.textContent = 'chart library unavailable (offline)'; return; }
    chart = LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 11,
        fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { color: 'rgba(42,49,64,.55)' }, horzLines: { color: 'rgba(42,49,64,.55)' } },
      rightPriceScale: { borderColor: '#2a3140' }, leftPriceScale: { visible: false, borderColor: '#2a3140' },
      timeScale: { borderColor: '#2a3140', timeVisible: true, secondsVisible: false },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      handleScroll: { vertTouchDrag: false },
      localization: { priceFormatter: (p) => NF.money.format(p) },
    });
    live = true;
  }
  function toPoints(arr) { // lightweight-charts labels the axis in UTC, so shift to local time like every other timestamp
    const out = [];
    let last = -1;
    for (const [ts, v] of arr || []) {
      const t = Math.floor(ts / 1000) - new Date(ts).getTimezoneOffset() * 60;
      if (t <= last) { if (t === last) out[out.length - 1].value = v; continue; }
      out.push({ time: t, value: v });
      last = t;
    }
    return out;
  }
  function setCurves(c) {
    curves = c || {};
    renderToggles();
    if (!live) { fallback(); return; }
    const keys = Object.keys(curves).filter((k) => k === 'TOTAL' || shown.has(k)).sort((a, b) => (a === 'TOTAL' ? -1 : b === 'TOTAL' ? 1 : a.localeCompare(b)));
    for (const [k, s] of [...series]) if (!keys.includes(k)) { chart.removeSeries(s); series.delete(k); }
    for (const k of keys) {
      let s = series.get(k);
      if (!s) {
        s = chart.addLineSeries(k === 'TOTAL'
          ? { color: '#e6edf3', lineWidth: 2, priceScaleId: 'right', title: 'TOTAL' }
          : { color: colorFor(k), lineWidth: 1, priceScaleId: 'left', title: k, priceLineVisible: false, lastValueVisible: false });
        series.set(k, s);
      }
      s.setData(toPoints(curves[k]));
    }
    chart.applyOptions({ leftPriceScale: { visible: keys.length > 1 } });
    const s2 = keys.join(',') + ':' + (curves.TOTAL || []).length;
    if (s2 !== sig) { sig = s2; chart.timeScale().fitContent(); }
  }
  function fallback() {
    const t = curves.TOTAL || [], lastPt = t[t.length - 1];
    el.textContent = 'chart library unavailable (offline) — TOTAL ' + (lastPt ? fmtMoney(lastPt[1]) : DASH) + ' over ' + t.length + ' points';
  }
  function renderToggles() {
    const box = clear($('#curve-toggles'));
    const keys = Object.keys(curves).filter((k) => k !== 'TOTAL').sort();
    box.append(h('span', { class: 'legend-total', text: 'TOTAL' }));
    for (const k of keys) {
      box.append(h('button', { type: 'button', class: 'tg' + (shown.has(k) ? ' on' : ''), text: k, style: 'color:' + (shown.has(k) ? colorFor(k) : ''),
        onclick: () => { shown.has(k) ? shown.delete(k) : shown.add(k); persist(); } }));
    }
    if (keys.length) box.append(h('button', { type: 'button', class: 'tg', text: 'all', onclick: () => { keys.forEach((k) => shown.add(k)); persist(); } }),
      h('button', { type: 'button', class: 'tg', text: 'none', onclick: () => { shown.clear(); persist(); } }));
  }
  function persist() { sessionStorage.setItem('paperlab.curves', JSON.stringify([...shown])); setCurves(curves); }
  return { init, setCurves };
})();

// ---- Overview: bake-off leaderboard ------------------------------------------------------------
/* 20 isolated wallets, one per strategy. Sorted by realized PnL this epoch by default; the numeric
   headers toggle the sort key (second click flips direction). Nulls always sink to the bottom. */
const LB = { key: 'realized', dir: -1 };
const pfText = (v) => (v == null ? DASH : v >= 999 ? '∞' : v.toFixed(2));
const wrText = (v) => (v == null ? DASH : (v * 100).toFixed(0) + '%');

// ---- lives + rejects (a book that loses 25% is flattened and respawned at 100 as a new life) ----
const rejectPairs = (r) => Object.entries((r && r.rejects_today) || {}).sort((a, b) => b[1] - a[1]);
function rejectTotal(r) { let n = 0; for (const [, v] of rejectPairs(r)) n += isNum(v) ? v : 0; return n; }
function lifeOf(r) { return isNum(r.life) ? r.life : 1; }
/** Derived sort values so object-valued columns (rejects) sort like the numeric ones. */
const LB_VAL = { rejects_today: rejectTotal, life: lifeOf };
const lbVal = (r, k) => (LB_VAL[k] ? LB_VAL[k](r) : r[k]);
/** L1 stays quiet; every life past the first is an amber badge so the graveyard is visible. */
function lifeCell(r) {
  const n = lifeOf(r);
  return h('span', { class: n > 1 ? 'life-badge' : 'sub', text: 'L' + n,
    title: 'life ' + n + ' · ' + (isNum(r.lives_today) ? r.lives_today : n) + ' lives today (cap 8/day)' });
}
function rejectsCell(r) {
  const pairs = rejectPairs(r), total = rejectTotal(r);
  if (!total) return h('span', { class: 'muted', title: 'nothing rejected today', text: DASH });
  return h('span', { class: 'rejects', title: pairs.map(([k, v]) => k + ': ' + v).join('\n') },
    h('span', { class: 'num', text: String(total) }), h('span', { class: 'sub', text: ' ' + pairs[0][0] }));
}
/** Watches `life` across polls; a row whose life just went up flashes red for ~2s before settling at $100. */
const Lives = {
  seen: {}, flashed: {}, MS: 2200,
  note(rows) {
    for (const r of rows || []) {
      const n = lifeOf(r), prev = this.seen[r.id];
      this.seen[r.id] = n;
      if (prev == null || n <= prev) continue;
      this.flashed[r.id] = Date.now();
      setTimeout(() => { delete this.flashed[r.id]; if (typeof Poll !== 'undefined' && Poll.state) renderTab(Poll.state); }, this.MS);
    }
  },
  fresh(id) { return Date.now() - (this.flashed[id] || 0) < this.MS; },
};
const lbRowClass = (r) => (r.halted ? 'halted' : '') + (r.enabled ? '' : ' off') + (Lives.fresh(r.id) ? ' respawn' : '');

function lbSorted(rows) {
  const k = LB.key, d = LB.dir;
  return rows.slice().sort((a, b) => {
    const av = lbVal(a, k), bv = lbVal(b, k), an = isNum(av), bn = isNum(bv);
    if (!an || !bn) return an === bn ? a.id.localeCompare(b.id) : an ? -1 : 1;
    return av === bv ? a.id.localeCompare(b.id) : (av < bv ? -1 : 1) * d;
  });
}
/** WINNERS: books promoted out of the bake-off and trading real money. Hidden until one exists. */
function renderWinners(card, table, winners, base, liveIds) {
  if (!card || !table) return;
  card.hidden = !winners.length;
  if (!winners.length) return;
  renderTable(table, [
    { h: '', cls: 'rank', cell: (r, i) => h('span', { class: 'pill down', text: 'WINNER ' + (i + 1) }) },
    // Promotion is permanent; being armed is not. A winner sitting on paper (after a restart cleared the
    // in-memory keys, say) must not look like it is trading real money.
    { h: 'routing', cell: (r) => (liveIds && liveIds.has(r.id)
      ? pill('LIVE — real orders', 'down') : pill('paper — not armed', 'warn')) },
    { h: 'strategy', cell: (r) => [h('span', { class: 'sid', text: r.id }), ' ',
      h('span', { class: 'sname', text: r.name })] },
    { h: 'equity', num: true, cell: (r) => h('span', { class: 'num ' + signClass(isNum(r.equity) ? r.equity - base : null), text: fmtMoney(r.equity) }) },
    { h: 'uPnL', num: true, cell: (r) => num(r.upnl) },
    { h: 'realized', num: true, cell: (r) => num(r.realized) },
    { h: 'trades', num: true, cell: (r) => [String(r.trades_total || 0), r.trades_open ? h('span', { class: 'sub', text: ' +' + r.trades_open }) : null] },
    { h: 'WR', num: true, cell: (r) => [wrText(r.win_rate), h('span', { class: 'sub', text: ' ' + (r.wins || 0) + '/' + (r.losses || 0) })] },
    { h: 'position', cell: (r) => h('span', { class: 'sub', text: r.position_summary || 'flat' }) },
    { h: 'last trade', num: true, cell: (r) => h('span', { class: 'sub', text: r.last_fill_ts ? ago(r.last_fill_ts) : DASH }) },
  ], winners, { onRow: (r) => Drawer.open(r.id), rowClass: () => 'winner', empty: '' });
}

function renderLeaderboard(st) {
  const all = lbSorted(st.strategies || []), w = st.wallets || {}, base = isNum(w.balance_each) ? w.balance_each : 100;
  // Winners (live, real money) are listed separately from the paper bake-off: mixing a real-money book
  // into a leaderboard of simulated ones makes the ranking meaningless.
  const liveIds = new Set(st.live_strategies || []);
  const order = st.winners || [];                       // promotion order: WINNER 1, WINNER 2, ...
  const wonIds = new Set(order);
  const winners = order.map((id) => all.find((r) => r.id === id)).filter(Boolean);
  const rows = all.filter((r) => !wonIds.has(r.id));
  renderWinners($('#winners-card'), $('#winners-table'), winners, base, liveIds);
  setText($('#lb-sub'), (w.armed != null ? w.armed : rows.filter((r) => r.enabled).length) + ' armed / ' + (w.count != null ? w.count : rows.length)
    + ' wallets · ' + fmtMoney(base) + ' each · paper total ' + fmtMoney(w.paper_total)
    + (winners.length ? ' · ' + winners.length + ' promoted to live' : ''));
  const col = (k, label, cell) => ({ h: label + (LB.key === k ? (LB.dir < 0 ? ' ▼' : ' ▲') : ''), num: true, cell,
    sort: () => { if (LB.key === k) LB.dir = -LB.dir; else { LB.key = k; LB.dir = -1; } renderLeaderboard(st); } });
  renderTable($('#lb-table'), [
    { h: '#', cls: 'rank', cell: (r) => String(rows.indexOf(r) + 1) },
    { h: 'strategy', cell: (r) => [h('i', { class: 'armed' + (r.halted ? ' halt' : r.enabled ? ' on' : ''),
      title: r.halted ? 'halted' : r.enabled ? 'armed' : 'disabled' }), h('span', { class: 'sid', text: r.id }), ' ',
      h('span', { class: 'sname', text: r.name })] },
    col('equity', 'equity', (r) => h('span', { class: 'num ' + signClass(isNum(r.equity) ? r.equity - base : null), text: fmtMoney(r.equity) })),
    { h: 'uPnL', num: true, cell: (r) => num(r.upnl) },
    col('realized', 'realized', (r) => num(r.realized)),
    col('realized_all_lives', 'all lives', (r) => num(r.realized_all_lives)),
    col('life', 'life', lifeCell),
    col('rejects_today', 'rejects', rejectsCell),
    col('trades_total', 'trades', (r) => [String(r.trades_total || 0), r.trades_open ? h('span', { class: 'sub', text: ' +' + r.trades_open }) : null]),
    col('win_rate', 'WR', (r) => [wrText(r.win_rate), h('span', { class: 'sub', text: ' ' + (r.wins || 0) + '/' + (r.losses || 0) })]),
    col('profit_factor', 'PF', (r) => h('span', { class: 'num ' + (r.profit_factor == null ? '' : r.profit_factor >= 1 ? 'up' : 'down'), text: pfText(r.profit_factor) })),
    { h: 'max DD', num: true, cell: (r) => h('span', { class: 'num ' + (r.max_dd_pct ? 'down' : ''), text: fmtNum(r.max_dd_pct, 2) + '%' }) },
    { h: 'last trade', num: true, cell: (r) => h('span', { class: 'sub', text: r.last_fill_ts ? ago(r.last_fill_ts) : DASH }) },
  ], rows, { onRow: (r) => Drawer.open(r.id), rowClass: lbRowClass, empty: 'no strategies loaded' });
}
const J_KIND = { open: 'accent', close: 'muted', reject: 'warn', halt: 'down', kill: 'down', desync: 'down', user: 'text',
  life_ended: 'down', life_respawned: 'accent', starved: 'warn' };
function renderJournal(list) {
  const ul = clear($('#journal'));
  for (const j of list || []) {
    const kind = J_KIND[j.kind] || 'muted';
    ul.append(h('li', { class: 'jl' }, h('span', { class: 'sub jt-time', text: fmtTime(j.ts) }),
      j.strategy_id ? h('button', { type: 'button', class: 'sid linkish', text: j.strategy_id, title: 'open ' + j.strategy_id,
        onclick: () => Drawer.open(j.strategy_id) }) : h('span', { class: 'sub linkish', text: 'lab' }),
      pill(j.kind, kind), h('span', { class: 'jtext ' + kind, text: j.text })));
  }
  if (!ul.children.length) ul.append(h('li', { class: 'muted', text: 'journal is empty' }));
}

// ---- Overview tab ---------------------------------------------------------------------------
function renderOverview(st) {
  const s = st.stats || {}, eq = st.equity || {}, w = st.wallets || {};
  renderLeaderboard(st);
  renderJournal(st.journal);
  const pf = s.profit_factor == null ? DASH : s.profit_factor >= 999 ? '∞' : s.profit_factor.toFixed(2);
  const dd = Math.abs(s.max_dd_pct || 0); // magnitude; the backend's sign convention is not relied on
  clear($('#stat-tiles')).append(
    tile('paper total', fmtMoney(eq.total), signClass(eq.pnl), (w.armed != null ? w.armed : DASH) + ' / ' + (w.count != null ? w.count : DASH)
      + ' armed · base ' + fmtMoney(w.paper_total != null ? w.paper_total : eq.starting)),
    tile('win rate', s.win_rate == null ? DASH : (s.win_rate * 100).toFixed(1) + '%', '',
      (s.wins || 0) + 'W / ' + (s.losses || 0) + 'L · ' + (s.trades_open || 0) + ' open'),
    tile('profit factor', pf, s.profit_factor == null ? '' : s.profit_factor >= 1 ? 'up' : 'down', 'GP ' + fmtMoney(s.gross_profit) + ' · GL ' + fmtMoney(s.gross_loss)),
    tile('max drawdown', (dd > 0 ? '-' : '') + fmtNum(dd, 2) + '%', dd > 0 ? 'down' : '', 'TOTAL equity, this epoch'),
    tile('avg R', s.avg_r == null ? DASH : fmtNum(s.avg_r, 2) + 'R', signClass(s.avg_r), 'expectancy ' + fmtMoney(s.expectancy, true)),
    tile('trades', (s.trades_today || 0) + ' / ' + (s.trades_total || 0), '', 'today / total'),
    tile('realized', fmtMoney(eq.realized, true), signClass(eq.realized), 'funding ' + fmtMoney(eq.funding, true)),
    tile('fees', fmtMoney(eq.fees), (eq.fees || 0) > 0 ? 'down' : '', 'paid this epoch'),
    tile('unrealized', fmtMoney(eq.upnl, true), signClass(eq.upnl), 'starting ' + fmtMoney(w.paper_total != null ? w.paper_total : eq.starting)));
  renderExitMix(st);
  renderNotional($('#notional'), st);
  const xw = st.exchange_wallet;
  $('#wallet-card').hidden = !xw;
  if (xw) clear($('#wallet')).append(kvList({ wallet: fmtMoney(xw.wallet), available: fmtMoney(xw.available), upnl: fmtMoney(xw.upnl, true) }));
  renderTable($('#prices-table'), [
    { h: 'symbol', cell: (r) => r.sym },
    { h: 'last', num: true, cell: (r) => fmtPrice(r.last) },
    { h: 'mark', num: true, cell: (r) => fmtPrice(r.mark) },
    { h: 'basis', num: true, cell: (r) => isNum(r.last) && isNum(r.mark) && r.last
      ? h('span', { class: 'num ' + signClass(r.mark - r.last), text: ((r.mark - r.last) / r.last * 1e4).toFixed(1) + 'bp' }) : DASH },
    { h: 'funding', num: true, cell: (r) => isNum(r.funding) ? h('span', { class: 'num ' + signClass(r.funding), text: fmtPct(r.funding * 100, true, 4) }) : DASH },
  ], Object.entries(st.prices || {}).map(([sym, p]) => Object.assign({ sym }, p)), { empty: 'no prices yet' });
}
/** Exit-kind mix: the hold experiment is judged on tp+trail rising and time falling, so it belongs on
 *  the Overview rather than buried in the tape. fee_gt_r is a reject, not an exit, and is labelled so. */
const EXIT_LABELS = [['tp', 'take profit'], ['trail', 'trailing stop'], ['stop', 'stop loss'],
                     ['time', 'max hold'], ['liq', 'paper liq'], ['manual', 'manual/flatten']];
function renderExitMix(st) {
  const x = st.exit_kinds || {}, kinds = x.kinds || {}, total = x.total || 0;
  const pct = (n) => total ? (n / total * 100).toFixed(0) + '%' : DASH;
  const el = clear($('#exit-tiles'));
  for (const [key, label] of EXIT_LABELS) {
    const k = kinds[key];
    if (!k && !['tp', 'trail', 'stop', 'time'].includes(key)) continue;   // always show the four we track
    const n = k ? k.n : 0, pnl = k ? k.pnl : 0;
    el.append(tile(label, n + ' · ' + pct(n), signClass(pnl), fmtMoney(pnl, true)));
  }
  const other = Object.entries(kinds).filter(([k]) => !EXIT_LABELS.some(([e]) => e === k));
  if (other.length) {
    el.append(tile('other', other.reduce((a, [, v]) => a + v.n, 0) + ' · ' + pct(other.reduce((a, [, v]) => a + v.n, 0)),
      '', other.map(([k, v]) => k + ' ' + v.n).join(' · ')));
  }
  el.append(tile('fee > 0.25R', x.fee_gt_r_today || 0, (x.fee_gt_r_today || 0) > 0 ? 'warn' : '',
    'entries refused today (reject, not an exit)'));
  $('#exit-sub').textContent = total
    ? total + ' closed · target (tp+trail) ' + (x.target_share * 100).toFixed(0) + '% · time ' + (x.time_share * 100).toFixed(0) + '%'
    : 'nothing closed yet this epoch';
}
function renderNotional(el, st) {
  const r = st.risk || {}, router = (st.positions || {}).router || {}, prices = st.prices || {};
  clear(el).append(bar('gross virtual notional', r.gross_notional, r.gross_cap));
  for (const sym of (st.engine || {}).symbols || []) {
    const px = (prices[sym] || {}).mark || (prices[sym] || {}).last || 0;
    el.append(bar(sym + ' net virtual', (r.net_notional || {})[sym] || 0, r.net_cap),
      bar(sym + ' net exchange', ((router[sym] || {}).exchange_net || 0) * px, r.net_cap, true));
  }
}
function bar(label, value, cap, thin) {
  const pct = isNum(value) && isNum(cap) && cap > 0 ? Math.abs(value) / cap * 100 : 0;
  return h('div', { class: 'bar' + (pct >= 100 ? ' over' : pct >= 80 ? ' warn' : '') + (thin ? ' thin' : '') },
    h('span', { class: 'lbl', text: label }),
    h('span', { class: 'num', text: fmtMoney(value) + ' / ' + fmtMoney(cap) + ' (' + pct.toFixed(0) + '%)' }),
    h('div', { class: 'track' }, h('div', { class: 'fill', style: 'width:' + Math.min(100, pct).toFixed(1) + '%' })));
}

// ---- shared trade / signal tables ----------------------------------------------------------------
const tpText = (tps) => (tps || []).map((tp) => fmtPrice(tp[0]) + '×' + Math.round(tp[1] * 100) + '%').join(' ');
/** Every trade of an epoch, open ones included (exit columns stay blank until they close). */
function tradesTable(t, rows) {
  renderTable(t, [
    { h: 'time', cell: (r) => h('span', { title: (r.open ? 'opened ' : 'closed ') + ago(r.exit_ts || r.entry_ts), text: fmtTime(r.exit_ts || r.entry_ts) }) },
    { h: 'sym', cell: (r) => r.symbol }, { h: 'side', cell: (r) => pill(r.side, r.side) },
    { h: 'qty', num: true, cell: (r) => fmtQty(r.qty) },
    { h: 'entry → exit', num: true, cell: (r) => fmtPrice(r.entry_price) + ' → ' + (r.open ? DASH : fmtPrice(r.exit_price)) },
    { h: 'net', num: true, cell: (r) => num(r.net) }, { h: 'R', num: true, cell: (r) => num(r.r, (v) => fmtNum(v, 2) + 'R') },
    { h: 'exit', cell: (r) => [r.open ? pill('OPEN', 'accent') : pill(r.exit_kind, r.exit_kind), r.simulated ? [' ', pill('SIM', 'ghost')] : null,
      (r.exits || []).length > 1 ? [' ', pill((r.exits || []).length + ' legs', 'ghost')] : null] },
    { h: 'hold', num: true, cell: (r) => fmtDur(r.hold_s) },
  ], rows, { empty: 'no trades this epoch yet', rowClass: (r) => (r.open ? 'openrow' : '') });
  return t;
}
function signalsTable(t, rows, compact) {
  renderTable(t, [
    { h: 'time', cell: (r) => h('span', { title: ago(r.ts), text: fmtTime(r.ts) }) },
    compact ? null : { h: 'strat', cell: (r) => r.strategy_id },
    { h: 'sym', cell: (r) => r.symbol },
    { h: 'kind', cell: (r) => [pill(r.side, r.side), r.kind !== 'entry' ? [' ', pill(r.kind, 'warn')] : null, r.tf ? h('span', { class: 'sub', text: ' ' + r.tf }) : null] },
    { h: 'entry', num: true, cell: (r) => fmtPrice(r.entry_price) },
    { h: 'stop', num: true, cell: (r) => fmtPrice(r.stop) },
    { h: 'tps', num: true, cell: (r) => tpText(r.tps) || DASH },
    { h: 'status', cell: (r) => statusCell(r) },
  ].filter(Boolean), rows, { empty: 'no signals' });
  return t;
}
function statusCell(r) {
  const p = pill(r.status, r.status, r.reason || null);
  if (r.status === 'rejected') return [p, r.code ? rejectCode(r.code, r.reason) : null,
    h('div', { class: 'reason down', text: /^blocked/i.test(r.reason || '') ? r.reason : 'blocked by risk: ' + (r.reason || 'rejected') })];
  return r.reason && r.status !== 'approved' ? [p, h('span', { class: 'sub', text: ' ' + truncate(r.reason, 36) })] : p;
}

// ---- drawer blocks (the drawer itself lives in app.js) ------------------------------------------------
/** The whole per-strategy scoreboard as a tile grid. `sc` is /api/strategies/{id}.scoreboard. */
function scoreTiles(sc, meta) {
  return h('div', { class: 'tiles small' },
    tile('equity', fmtMoney(sc.equity), signClass(isNum(sc.equity) ? sc.equity - (sc.allocation || 100) : null), 'of ' + fmtMoney(sc.allocation)),
    tile('uPnL', fmtMoney(sc.upnl, true), signClass(sc.upnl)),
    tile('realized', fmtMoney(sc.realized, true), signClass(sc.realized), 'funding ' + fmtMoney(sc.funding, true)),
    tile('fees', fmtMoney(sc.fees), (sc.fees || 0) > 0 ? 'down' : ''),
    tile('trades', String(sc.trades_total || 0), '', (sc.trades_today || 0) + ' today'),
    tile('W / L', (sc.wins || 0) + ' / ' + (sc.losses || 0), signClass((sc.wins || 0) - (sc.losses || 0))),
    tile('win rate', wrText(sc.win_rate)),
    tile('profit factor', pfText(sc.profit_factor), sc.profit_factor == null ? '' : sc.profit_factor >= 1 ? 'up' : 'down'),
    tile('avg R', sc.avg_r == null ? DASH : fmtNum(sc.avg_r, 2) + 'R', signClass(sc.avg_r)),
    tile('expectancy', fmtMoney(sc.expectancy, true), signClass(sc.expectancy), 'per trade'),
    tile('max DD', fmtNum(sc.max_dd_pct, 2) + '%', sc.max_dd_pct ? 'down' : ''),
    tile('time in market', fmtDur(sc.time_in_market_s)),
    tile('open positions', String(sc.trades_open || 0), '', 'margin ' + fmtMoney(sc.margin_used)),
    tile('halt floor', fmtMoney((meta || {}).halt_floor), '', 'last fill ' + (sc.last_fill_ts ? ago(sc.last_fill_ts) : 'never')));
}
/** Trade list seeded from the detail payload, paged further through /api/strategies/{id}/trades. */
function tradeHistory(d) {
  const tbl = h('table', { class: 'tbl' }), info = h('span', { class: 'sub' });
  const btn = h('button', { type: 'button', class: 'btn ghost', text: 'load more' });
  const rows = (d.trades || []).slice();
  let total = rows.length;
  const draw = () => {
    tradesTable(tbl, rows);
    setText(info, rows.length + ' of ' + total + ' trades this epoch');
    btn.hidden = rows.length >= total;
  };
  const page = async (limit) => {
    const r = await api('/api/strategies/' + d.id + '/trades?skip=' + rows.length + '&limit=' + limit);
    if (Drawer.sid !== d.id) return;
    if (isNum(r.total)) total = r.total;
    const seen = new Set(rows.map((t) => t.position_id));
    for (const t of r.trades || []) if (!seen.has(t.position_id)) rows.push(t);
    draw();
  };
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try { await page(50); } catch (e) { toast(e.message, 'bad'); }
    btn.disabled = false;
  });
  draw();
  api('/api/strategies/' + d.id + '/trades?skip=0&limit=1') // the seeded rows render immediately; this only learns the true total
    .then((r) => { if (Drawer.sid === d.id && isNum(r.total)) { total = r.total; draw(); } }).catch(() => {});
  return h('div', null, h('div', { class: 'tablewrap' }, tbl), h('div', { class: 'btn-row' }, btn, info));
}
/** The short reject code that rides along with a rejected signal; the full reason stays in the tooltip. */
function rejectCode(code, reason) { return h('span', { class: 'rcode', title: reason || null, text: ' ' + code }); }
/** One line per life from /api/strategies/{id}.lives — dead lives first, the live one last. */
function livesBlock(lives) {
  const rows = lives || [];
  if (!rows.length) return h('p', { class: 'muted', text: 'no life history yet' });
  return h('div', { class: 'lives' }, rows.map((L) => h('div', { class: 'life-row' + (L.current ? ' current' : '') },
    h('span', { class: 'lname', text: 'life ' + (isNum(L.life) ? L.life : '?') }),
    h('span', { class: 'sub', text: ' · ' + (L.trades || 0) + ' trades · ' }),
    h('span', { class: 'num ' + signClass(L.net), text: 'net ' + fmtMoney(L.net, true) }),
    h('span', { class: 'sub', text: (isNum(L.fees) ? ' · fees ' + fmtMoney(L.fees) : '') + (isNum(L.equity) ? ' · equity ' + fmtMoney(L.equity) : '') + ' ' }),
    pill(L.current ? 'current' : 'halted', L.current ? 'up' : 'halt',
      L.current ? 'the live book' : 'flattened at the 25% floor, then respawned at $100'),
    (L.days || []).length ? h('span', { class: 'sub', text: ' ' + L.days.join(', ') }) : null)));
}
function rejectList(obj) {
  const ents = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  if (!ents.length) return h('p', { class: 'muted', text: 'nothing rejected today' });
  return h('div', { class: 'kv' }, ents.map(([k, v]) => [h('span', { class: 'k', text: k }), h('span', { class: 'v num', text: String(v) })]));
}
/** Journal notes for one strategy plus a box that POSTs /api/notes and re-reads the list. */
function notesBlock(d) {
  const list = h('ul', { class: 'list notes' }), msg = h('div', { class: 'msg' });
  const box = h('input', { type: 'text', placeholder: 'note for ' + d.id + '…', maxlength: 500, autocomplete: 'off' });
  const btn = h('button', { type: 'button', class: 'btn', text: 'Add note' });
  const draw = (notes) => {
    clear(list);
    for (const n of notes || []) list.append(h('li', null, h('span', { class: 'sub', text: fmtTime(n.ts) + ' ' }),
      pill(n.kind, n.author === 'user' ? 'accent' : 'ghost'), ' ', h('span', { text: n.text })));
    if (!list.children.length) list.append(h('li', { class: 'muted', text: 'no notes yet — jot what you see here' }));
  };
  const submit = async () => {
    const text = box.value.trim();
    if (!text) { setText(msg, 'write something first'); msg.className = 'msg warn'; return; }
    btn.disabled = true;
    try {
      await post('/api/notes', { strategy_id: d.id, text });
      box.value = '';
      setText(msg, 'note saved'); msg.className = 'msg ok';
      const r = await api('/api/notes?strategy=' + d.id + '&limit=20');
      draw(r.notes);
    } catch (e) { setText(msg, e.message); msg.className = 'msg bad'; }
    btn.disabled = false;
  };
  btn.addEventListener('click', submit);
  box.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
  draw(d.notes);
  return h('div', null, list, h('div', { class: 'btn-row' }, box, btn, msg));
}
function voteBlock(state) {
  const syms = (state || {}).symbols || {}, out = h('div', { class: 'votes' });
  if (!Object.keys(syms).length) out.append(h('p', { class: 'muted', text: 'no evaluations yet' }));
  for (const [sym, v] of Object.entries(syms)) {
    out.append(h('div', { class: 'vote-head' }, h('b', { text: sym }), h('span', { class: 'num ' + signClass(v.net), text: 'net ' + (v.net > 0 ? '+' : '') + v.net }),
      h('span', { class: 'sub', text: v.live_contributors + ' live contributors · ' + v.counted + ' counted · ema ' + (v.ema_ok == null ? DASH : v.ema_ok ? 'ok' : 'blocked') + ' · eval ' + ago(v.last_eval_ts) }),
      v.notice ? h('div', { class: 'warn notice', text: v.notice }) : null));
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'strategy', cell: (x) => x.strategy_id }, { h: 'side', cell: (x) => pill(x.side, x.side) },
      { h: 'age', num: true, cell: (x) => fmtDur(x.age_s) }, { h: 'source', cell: (x) => pill(x.source, x.source === 'live' ? 'up' : 'muted') },
      { h: 'status', cell: (x) => pill(x.status, x.status) }, { h: 'tf', cell: (x) => x.tf }, { h: 'stop', num: true, cell: (x) => fmtPrice(x.stop) },
    ], v.votes || [], { empty: 'no votes' });
    out.append(h('div', { class: 'tablewrap' }, t));
  }
  if (state && state.contributors) out.append(h('div', { class: 'sub', text: 'contributors: ' + state.contributors.join(', ') }));
  return out;
}

// ---- Positions tab --------------------------------------------------------------------------------
function renderPositions(st) {
  const P = st.positions || {};
  vposTable($('#vpos-table'), P.virtual || []);
  renderTable($('#xpos-table'), [
    { h: 'symbol', cell: (r) => r.symbol }, { h: 'qty', num: true, cell: (r) => num(r.qty, fmtQty) },
    { h: 'entry', num: true, cell: (r) => fmtPrice(r.entry_price) }, { h: 'mark', num: true, cell: (r) => fmtPrice(r.mark) },
    { h: 'liq', num: true, cell: (r) => fmtPrice(r.liq_price) },
    { h: 'liq dist', num: true, cell: (r) => h('span', { class: 'num ' + (isNum(r.liq_distance_pct) ? (r.liq_distance_pct < 10 ? 'down' : r.liq_distance_pct < 25 ? 'warn' : '') : ''), text: fmtPct(r.liq_distance_pct, false) }) },
    { h: 'lev', num: true, cell: (r) => r.lev_exchange + 'x' }, { h: 'margin', num: true, cell: (r) => fmtMoney(r.margin) }, { h: 'uPnL', num: true, cell: (r) => num(r.upnl) },
  ], P.exchange || [], { empty: st.dry_run ? 'dry run — no exchange orders are sent' : 'flat on the exchange' });
  renderRouter($('#router'), P.router || {}, st);
}
function vposTable(t, rows) {
  renderTable(t, [
    { h: 'strat', cell: (r) => h('span', { class: 'sid', text: r.strategy_id }) },
    { h: 'sym', cell: (r) => [r.symbol, r.tf ? h('span', { class: 'sub', text: ' ' + r.tf }) : null] },
    { h: 'side', cell: (r) => pill(r.side, r.side) },
    { h: 'qty', num: true, cell: (r) => [fmtQty(r.qty), r.qty !== r.qty_initial ? h('span', { class: 'sub', text: ' / ' + fmtQty(r.qty_initial) }) : null] },
    { h: 'entry', num: true, cell: (r) => fmtPrice(r.entry_price) },
    { h: 'stop', num: true, cell: (r) => [fmtPrice(r.stop), h('span', { class: 'sub', text: ' ' + (r.stop_kind || 'stop') + (r.trail ? ' · trail ' + r.trail : '') + (r.be_done ? ' · BE' : '') })] },
    { h: 'TPs', num: true, cell: (r) => tpText(r.take_profits) || (r.tp1_done ? 'tp1 done' : DASH) },
    { h: 'lev v/x', num: true, cell: (r) => r.lev_virtual + 'x / ' + r.lev_exchange + 'x' },
    { h: 'margin', num: true, cell: (r) => fmtMoney(r.margin) },
    { h: 'uPnL', num: true, cell: (r) => num(r.upnl) }, { h: 'R', num: true, cell: (r) => num(r.r, (v) => fmtNum(v, 2) + 'R') },
    { h: 'liq', num: true, cell: (r) => [fmtPrice(r.liq_paper), ' ', pill(r.liq_label || 'PAPER', r.liq_label === 'NETTED' ? 'warn' : 'ghost',
      r.liq_label === 'NETTED' ? 'the exchange net position is much smaller than the virtual gross: this paper liq is not the exchange liq' : 'paper liquidation estimate at the virtual leverage')] },
    { h: 'age', num: true, cell: (r) => fmtDur(r.age_s) },
    { h: 'tags', cell: (r) => [r.simulated ? pill('SIM', 'ghost', 'simulated fill (dry run)') : null, r.leg ? [' ', pill(r.leg, 'ghost')] : null] },
  ], rows, { empty: 'no virtual positions', rowClass: (r) => (r.liq_label === 'NETTED' ? 'netted' : '') });
  return t;
}
function renderRouter(el, router, st) {
  clear(el);
  const prices = st.prices || {};
  for (const [sym, r] of Object.entries(router)) {
    const bs = r.backstop, pct = (bs && isNum(bs.pct) ? bs.pct : r.backstop_pct || 0) * 100;
    el.append(h('div', { class: 'card sub-card' + (r.desync ? ' bad' : '') },
      h('div', { class: 'card-head' }, h('h3', { text: sym }), h('span', { class: 'sub', text: 'mark ' + fmtPrice((prices[sym] || {}).mark) + ' · lev ' + r.lev_exchange + 'x · mmr ' + fmtPct((r.mmr || 0) * 100, false, 2) })),
      kvList({ 'virtual net': fmtQty(r.virtual_net), 'exchange net': fmtQty(r.exchange_net), 'gross notional': fmtMoney(r.gross), residual: fmtQty(r.residual),
        backstop: bs ? bs.side + ' ' + fmtQty(bs.qty) + ' @ ' + fmtPrice(bs.price) + ' (' + fmtPct(pct, false, 2) + ')' : 'none (' + fmtPct(pct, false, 2) + ' when placed)',
        'last drift': isNum(r.last_drift_bps) ? r.last_drift_bps.toFixed(1) + ' bps' : DASH, 'exchange entry / liq': fmtPrice(r.exchange_entry) + ' / ' + fmtPrice(r.exchange_liq) }),
      r.desync ? h('div', { class: 'desync-text', text: 'DESYNC: ' + r.desync }) : null));
  }
  if (!el.children.length) el.append(h('p', { class: 'muted', text: 'no symbols' }));
}
