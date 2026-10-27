/* PaperLab home (2026-10-24): the two strategies the research kept, one page each, plus Research and Settings.
   Public mode (/public/competition): read-only GETs from /api/public/*. Private mode (/): the same pages plus the live
   switches, exchange keys, linked accounts and the kill switch, with the password sent as Basic auth on every /api call
   (sessionStorage only) and X-PaperLab: 1 on every POST. Data is always set with textContent, never as HTML. */
'use strict';

const PRIVATE = document.body.dataset.mode === 'private';
const PW_KEY = 'paperlab.pw';
const $ = (s) => document.querySelector(s);

// ---- tiny DOM helper -------------------------------------------------------------------------------------------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'text') el.textContent = v;
    else if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
const svg = (tag, attrs) => { const el = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };

// ---- formatting --------------------------------------------------------------------------------------------------
const pct = (v, d = 1) => v == null || isNaN(v) ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
const money = (v, d = 2) => v == null || isNaN(v) ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const tone = (v) => v > 0 ? 'up' : v < 0 ? 'down' : '';
const day = (ts) => ts ? new Date(ts).toISOString().slice(0, 10) : '—';
const when = (ts) => ts ? new Date(ts).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '—';
const LIVE = ['ARMED', 'CLOSING', 'HALTED'];
const statusPill = (s) => h('span', { class: 'pill ' + (s === 'ARMED' ? 'live' : (s === 'HALTED' || s === 'CLOSING') ? 'bad' : ''),
  text: s === 'ARMED' ? 'Live' : s === 'HALTED' ? 'Halted' : s === 'CLOSING' ? 'Closing' : 'Off' });

// ---- data access -----------------------------------------------------------------------------------------------
class ApiError extends Error { constructor(m, s) { super(m); this.status = s; } }
const Auth = {
  get: () => sessionStorage.getItem(PW_KEY), set: (pw) => sessionStorage.setItem(PW_KEY, pw), clear: () => sessionStorage.removeItem(PW_KEY),
  header: () => 'Basic ' + btoa(unescape(encodeURIComponent('admin:' + (Auth.get() || '')))),
};
async function pub(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new ApiError('HTTP ' + r.status, r.status);
  return r.json();
}
async function api(path, body) {
  if (Auth.get() == null) { showLogin(); throw new ApiError('not logged in', 401); }
  const init = { method: body ? 'POST' : 'GET', cache: 'no-store', headers: { Authorization: Auth.header() } };
  if (body) { init.headers['X-PaperLab'] = '1'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(path, init);
  if (r.status === 401) { Auth.clear(); showLogin('Wrong password, or the session ended.'); throw new ApiError('unauthorized', 401); }
  let d = null;
  try { d = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok || (d && d.ok === false)) throw new ApiError((d && (d.error || d.detail)) || 'HTTP ' + r.status, r.status);
  return d;
}
const post = (path, body) => api(path, body || {});

function showLogin(msg) { $('#login').hidden = false; $('#login-err').textContent = msg || ''; setTimeout(() => $('#login-pw').focus(), 30); }
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  Auth.set($('#login-pw').value);
  $('#login-pw').value = '';
  try { await api('/api/accounts'); $('#login').hidden = true; route(); } catch (err) { if (err.status !== 401) { $('#login').hidden = true; route(); } }
});

function confirmBox(title, text) {
  return new Promise((resolve) => {
    $('#confirm-title').textContent = title; $('#confirm-text').textContent = text; $('#confirm').hidden = false;
    const done = (v) => { $('#confirm').hidden = true; $('#confirm-yes').onclick = $('#confirm-no').onclick = null; resolve(v); };
    $('#confirm-yes').onclick = () => done(true); $('#confirm-no').onclick = () => done(false);
  });
}

// ---- charts ------------------------------------------------------------------------------------------------------
function lineChart(points, opts = {}) {             // points: [[label, value], ...]
  const W = 720, H = opts.h || 220, [L, R, T, B] = opts.bare ? [2, 2, 4, 4] : [52, 10, 12, 26];
  const el = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: opts.cls || 'chart', preserveAspectRatio: 'none', role: 'img' });
  if (!points || points.length < 2) return el;
  const ys = points.map((p) => p[1]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (i) => L + (i / (points.length - 1)) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  if (!opts.bare) {
    for (let k = 0; k <= 3; k++) {
      const v = lo + (hi - lo) * k / 3, yy = y(v);
      el.append(svg('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid' }));
      const t = svg('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', class: 'ax' }); t.textContent = opts.fmt ? opts.fmt(v) : v.toFixed(0); el.append(t);
    }
    for (const i of [0, Math.floor((points.length - 1) / 2), points.length - 1]) {
      const t = svg('text', { x: x(i), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle', class: 'ax' });
      t.textContent = points[i][0]; el.append(t);
    }
  }
  const d = points.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join(' ');
  el.append(svg('path', { d: d + ` L${x(points.length - 1).toFixed(1)} ${H - B} L${L} ${H - B} Z`, class: 'ar' }));
  el.append(svg('path', { d, class: 'ln', 'vector-effect': 'non-scaling-stroke' }));
  return el;
}

// ---- research facts (fixed: from the studies, not from live data) -------------------------------------------------------
const FACTS = {
  ensemble: [
    ['Tested on', '70 months, Jan 2021 – Oct 2026, at 1x'],
    ['Typical month (median)', '+2.7%'], ['Average month', '+4.1%'], ['Compounded month', '+3.8%'],
    ['Losing months', '24 of 70'], ['Worst month', '−12.4%'], ['Worst drop from a peak', '−23%'],
    ['Luck checks passed', '8 of 8'],
  ],
  worker2: [
    ['Tested on', '70 months, Jan 2021 – Oct 2026, at 1x'],
    ['Typical month (median), all years', '+4.4%'], ['Compounded month, all years', '+5.3%'],
    ['Typical / compounded month, Nov 2024 – Oct 2026', '+2.2% / +3.0%'],
    ['Losing months', '20 of 70'], ['Worst month', '−17.4%'], ['Worst drop from a peak', '−20%'],
    ['Trades', 'about 50 a month, held 10 hours'], ['Luck checks passed', '8 of 8'],
    ['Weak spot', 'getting weaker: about +10% a month in 2021, +2–3% in 2025–26'],
  ],
  worker3: [
    ['Chosen on', 'Jan 2021 – Oct 2024 only, then tested on two periods it never saw'],
    ['Compounded month, Nov 2024 – Oct 2026', '+10.3% (typical month +10.2%)'],
    ['Compounded month, 2018 – 2020 (unseen)', '+5.2%'],
    ['Months up', '16 of 24 (2024–26) · 24 of 36 (2018–20)'],
    ['Worst drop from a peak', '−40% (2024–26) · −29% (2018–20)'],
    ['Luck checks passed', '7 of 8 (2024–26) · 8 of 8 (2018–20)'],
    ['Weak spot', 'in 2025–26 most profit came from a few new meme coins; shorts on coins that double in a day are wiped out (9 times in 2 years)'],
    ['Every year 2018 – 2026', 'positive (2022, the weakest, +14%)'],
    ['Any 12 months in a row', 'positive 56 of 60 times since 2021 (worst −15%)'],
    ['From 200 USDT', 'works: about the same results (BTC and ETH skipped below their Binance minimums)'],
    ['Best use', 'next to Workers 1 and 2: the three together had smaller drops than any one alone'],
  ],
  ibs: [
    ['Tested on', 'QQQ 2010 – 2026'], ['Trades', 'about 18 a year, held about 6 days'],
    ['Average trade (QQQ)', '+0.85%'], ['Winning trades 2022–2026', '74%'],
    ['Worst trade', 'QQQ −22% · TQQQ about −50%'], ['Worst drop from a peak', 'QQQ −16% · TQQQ −43%'],
  ],
};

// ---- pages -------------------------------------------------------------------------------------------------------
const PAGES = { '': home, worker1: ensemble, ensemble, worker2, worker3, ibs, research, settings };
const NAV = [['', 'Home'], ['worker1', 'Worker 1'], ['worker2', 'Worker 2'], ['worker3', 'Worker 3'], ['ibs', 'IBS stocks'], ['research', 'Research'], ...(PRIVATE ? [['settings', 'Settings']] : [])];
let seq = 0;

function route() {
  const name = (location.hash.replace(/^#\/?/, '').split('/')[0]) || '';
  const page = PAGES[name] ? (name === 'ensemble' ? 'worker1' : name) : '';
  const nav = $('#nav'); fill(nav, ...NAV.map(([k, t]) => h('a', { href: '#/' + k, class: k === page ? 'on' : '', text: t })));
  $('#mode').textContent = PRIVATE ? 'private' : 'public · read-only';
  const my = ++seq, root = $('#page');
  fill(root, h('p', { class: 'loading', text: 'Loading…' }));
  Promise.resolve(PAGES[page]()).then((el) => { if (my === seq) fill(root, el); })
    .catch((e) => { if (my === seq) fill(root, h('p', { class: 'note bad', text: 'Could not load this page: ' + e.message })); });
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);

// ---- home helpers ------------------------------------------------------------------------------------------------
const BOTS = [
  { key: 'w1', path: 'worker1', name: 'Worker 1', what: 'Ensemble · about 55 coins, long and short', venue: 'Binance futures',
    how: 'Buys the strongest coins and shorts the weakest, using three simple methods mixed together. Re-balances once a day.',
    often: 'Every day at 00:00 UTC', typical: '+2.7%', worst: '−23%', luck: '8 of 8', risk: 'Medium' },
  { key: 'w2', path: 'worker2', name: 'Worker 2', what: 'Afternoon momentum · up to 5 coins', venue: 'Binance futures',
    how: 'At 16:00 UTC it picks coins that made an unusually big move since midnight and rides that move in the same direction until 02:00 UTC.',
    often: 'Opens 16:00, closes 02:00 UTC', typical: '+2.2%', worst: '−20%', luck: '8 of 8', risk: 'Medium' },
  { key: 'w3', path: 'worker3', name: 'Worker 3', what: '30-day highs · top 20 coins, 5 long + 5 short', venue: 'Binance futures',
    how: 'Buys the 5 big coins closest to their 30-day high and shorts the 5 closest to their 30-day low. Coins near their high tend to keep rising.',
    often: 'Every day at 00:00 UTC', typical: '+3.4%', worst: '−40%', luck: '8 of 8 (2018–20)', risk: 'High' },
  { key: 'ibs', path: 'ibs', name: 'IBS stocks', what: 'US stocks · QQQ and TQQQ', venue: 'Alpaca',
    how: 'Buys the Nasdaq-100 fund on days it closes near its daily low, and sells when it closes above the previous day\'s high. About 18 trades a year.',
    often: 'Near the US market close', typical: '+0.85% a trade', worst: '−16% (QQQ)', luck: 'passed', risk: 'Medium' },
];
const RISK_TONE = { Low: 'good', Medium: '', High: 'bad' };

function maxDD(curve) {
  let pk = -Infinity, dd = 0;
  for (const [, v] of curve) { pk = Math.max(pk, v); dd = Math.max(dd, 1 - v / pk); }
  return dd * 100;
}

function nextUtc(hh, mm = 0) {                       // the next time the UTC clock shows hh:mm
  const n = new Date(), t = new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate(), hh, mm));
  if (t <= n) t.setUTCDate(t.getUTCDate() + 1);
  return t;
}
function untilText(t) {
  const m = Math.max(0, Math.round((t - new Date()) / 60000)), hrs = Math.floor(m / 60);
  return 'in ' + (hrs ? hrs + ' h ' : '') + (m % 60) + ' min';
}
const localTime = (t) => t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

function multiChart(series) {                        // series: [{name, cls, points: [[label, pct], ...]}]
  const W = 720, H = 240, [L, R, T, B] = [46, 12, 12, 26];
  const el = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart multi', preserveAspectRatio: 'none', role: 'img' });
  const all = series.flatMap((s) => s.points.map((p) => p[1]));
  if (!all.length) return el;
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const n = Math.max(...series.map((s) => s.points.length));
  const x = (i) => L + (i / Math.max(n - 1, 1)) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  for (let k = 0; k <= 3; k++) {
    const v = lo + (hi - lo) * k / 3, yy = y(v);
    el.append(svg('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid' }));
    const t = svg('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', class: 'ax' }); t.textContent = (v > 0 ? '+' : '') + v.toFixed(0) + '%'; el.append(t);
  }
  el.append(svg('line', { x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'zero' }));
  const longest = series.reduce((a, s) => (s.points.length > a.points.length ? s : a), series[0]);
  for (const i of [0, n - 1]) {
    const t = svg('text', { x: x(i), y: H - 6, 'text-anchor': i === 0 ? 'start' : 'end', class: 'ax' });
    t.textContent = longest.points[i] ? longest.points[i][0] : ''; el.append(t);
  }
  for (const s of series) {
    if (s.points.length < 2) continue;
    const off = n - s.points.length;                   // align the ends (today) of shorter books
    const d = s.points.map((p, i) => (i ? 'L' : 'M') + x(i + off).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join(' ');
    el.append(svg('path', { d, class: 'ln ' + s.cls, 'vector-effect': 'non-scaling-stroke' }));
  }
  return el;
}

async function home() {
  const [ens, ibsd, ensLive, ibsLive, w2, w2Live, w3, w3Live] = await Promise.all([
    pub('/api/public/ensemble').catch(() => null), pub('/api/public/ibs-bot').catch(() => null),
    PRIVATE ? api('/api/ensemble/live').catch(() => null) : null, PRIVATE ? api('/api/ibs-bot').catch(() => null) : null,
    pub('/api/public/worker2').catch(() => null), PRIVATE ? api('/api/worker2/live').catch(() => null) : null,
    pub('/api/public/worker3').catch(() => null), PRIVATE ? api('/api/worker3/live').catch(() => null) : null]);
  const qqq = ibsd && ibsd.shadow && ibsd.shadow.books ? ibsd.shadow.books['IBS-QQQ'] : null;
  const ibsLiveView = (ibsLive && ibsLive.live) || (ibsd && ibsd.live) || {};
  const data = {
    w1: { d: ens, book: (ens && ens.book) || {}, live: ensLive || {} },
    w2: { d: w2, book: (w2 && w2.book) || {}, live: w2Live || {} },
    w3: { d: w3, book: (w3 && w3.book) || {}, live: w3Live || {} },
    ibs: { d: ibsd, book: {}, live: ibsLiveView },
  };
  const liveBots = BOTS.filter((b) => LIVE.includes(data[b.key].live.status));
  const putIn = liveBots.reduce((a, b) => a + (Number(data[b.key].live.amount) || 0), 0);
  const worth = liveBots.reduce((a, b) => a + (Number(data[b.key].live.equity ?? data[b.key].live.amount) || 0), 0);

  // 1. the summary strip
  const summary = PRIVATE
    ? h('div', { class: 'kpis' },
        h('div', { class: 'kpi' }, h('b', { text: liveBots.length + ' of ' + BOTS.length }), h('span', { text: 'bots trading real money' })),
        h('div', { class: 'kpi' }, h('b', { text: money(putIn, 0) }), h('span', { text: 'USDT put in' })),
        h('div', { class: 'kpi' }, h('b', { text: money(worth) }), h('span', { text: 'worth now (USDT)' })),
        h('div', { class: 'kpi' }, h('b', { class: tone(worth - putIn), text: (worth - putIn > 0 ? '+' : '') + money(worth - putIn) }), h('span', { text: 'real profit / loss (USDT)' })))
    : h('div', { class: 'kpis' }, ...BOTS.slice(0, 3).map((b) => {
        const bk = data[b.key].book;
        return h('div', { class: 'kpi' }, h('b', { class: tone(bk.net_pct), text: pct(bk.net_pct) }), h('span', { text: b.name + ' paper result' }));
      }), h('div', { class: 'kpi' }, h('b', { class: tone(qqq && qqq.realized_pct), text: qqq ? pct(qqq.realized_pct) : '—' }), h('span', { text: 'IBS paper result' })));

  // 2. what happens next
  const events = [
    { t: nextUtc(0, 2), what: 'Workers 1 and 3 re-balance', note: 'they compare all coins and adjust every position' },
    { t: nextUtc(16, 0), what: 'Worker 2 opens its positions', note: 'rides the coins that moved unusually far since midnight' },
    { t: nextUtc(2, 0), what: 'Worker 2 closes everything', note: 'it never holds overnight past 02:00 UTC' },
  ].sort((a, c) => a.t - c.t);
  const next = h('div', { class: 'panel' }, h('h2', { text: 'Up next' }),
    h('ul', { class: 'timeline' }, ...events.map((e) => h('li', {},
      h('div', { class: 'tl-when' }, h('b', { class: 'num', text: e.t.toISOString().slice(11, 16) + ' UTC' }), h('span', { text: localTime(e.t) + ' your time · ' + untilText(e.t) })),
      h('div', {}, h('div', { text: e.what }), h('div', { class: 'muted', style: 'font-size:13px', text: e.note }))))),
    h('p', { class: 'muted', style: 'font-size:13px;margin:10px 0 0', text: 'IBS stocks checks the market shortly before the US close on trading days.' }));

  // 3. the bot cards
  const card = (b) => {
    const { d, book: bk, live } = data[b.key];
    const curve = bk.curve || [], months = bk.months || [];
    const isLive = LIVE.includes(live.status);
    const status = isLive
      ? h('div', { class: 'note good', style: 'margin:0 0 14px' }, 'Trading real money: ' + money(live.amount, 0) + ' USDT put in',
          live.equity != null && live.amount != null ? h('b', { class: 'num ' + tone(live.equity - live.amount), text: ' · ' + (live.equity - live.amount > 0 ? '+' : '') + money(live.equity - live.amount) + ' USDT' }) : null)
      : h('div', { class: 'note', style: 'margin:0 0 14px', text: PRIVATE ? 'Paper only: it is not trading real money. Open it to switch on.' : 'Results below are a paper book (no real money).' });
    let held = '—';
    if (b.key === 'ibs') held = qqq && qqq.holding ? 'QQQ since ' + qqq.entry_day : 'nothing (waiting for a buy day)';
    else if (d && d.positions) {
      const L_ = d.positions.filter((p) => p[1] > 0).length, S_ = d.positions.filter((p) => p[1] < 0).length;
      held = L_ + S_ ? L_ + ' long · ' + S_ + ' short' : 'nothing right now';
    }
    const up = months.filter((m) => m[1] > 0).length;
    const lastM = months.slice(-1)[0];
    const big = b.key === 'ibs' ? (qqq ? qqq.realized_pct : null) : bk.net_pct;
    return h('a', { class: 'panel bot', href: '#/' + b.path },
      h('div', { class: 'bot-head' },
        h('div', {}, h('div', { class: 'bot-title', text: b.name }), h('div', { class: 'bot-what', text: b.what + ' · ' + b.venue })),
        statusPill(live.status || 'OFF')),
      status,
      h('p', { class: 'bot-how', text: b.how }),
      h('div', { class: 'bot-row' },
        h('div', {}, h('div', { class: 'bot-big num ' + tone(big), text: big != null ? pct(big) : '—' }),
          h('div', { class: 'bot-sub', text: b.key === 'ibs' ? 'paper trades so far' : curve.length ? 'paper result since ' + curve[0][0] : 'paper book loading' })),
        h('span', { class: 'risk ' + RISK_TONE[b.risk], text: b.risk + ' risk' })),
      curve.length > 1 ? lineChart(curve, { bare: true, h: 46, cls: 'spark' }) : null,
      h('dl', { class: 'mini' },
        h('div', {}, h('dt', { text: 'This month' }), h('dd', { class: 'num ' + tone(lastM && lastM[1]), text: lastM ? pct(lastM[1]) : '—' })),
        h('div', {}, h('dt', { text: 'Months up' }), h('dd', { class: 'num', text: months.length ? up + ' of ' + months.length : '—' })),
        h('div', {}, h('dt', { text: 'Worst drop so far' }), h('dd', { class: 'num', text: curve.length > 1 ? '−' + maxDD(curve).toFixed(1) + '%' : '—' })),
        h('div', {}, h('dt', { text: 'Holding now' }), h('dd', { text: held }))),
      h('div', { class: 'bot-foot' }, h('span', { text: 'Tests: typical month ' + b.typical + ', worst drop ' + b.worst }), h('span', { class: 'go-arrow', text: 'Details →' })));
  };

  // 4. growth since 1 July, all three crypto workers on one chart
  const norm = (curve) => curve.map(([dd, v]) => [dd, (v / 10000 - 1) * 100]);   // every paper book starts at 10,000 USDT
  const series = [
    { name: 'Worker 1', cls: 's1', points: norm(data.w1.book.curve || []) },
    { name: 'Worker 2', cls: 's2', points: norm(data.w2.book.curve || []) },
    { name: 'Worker 3', cls: 's3', points: norm(data.w3.book.curve || []) },
  ];
  const growth = h('div', { class: 'panel' }, h('h2', { text: 'Paper growth since 1 July' }),
    h('div', { class: 'legend' }, ...series.map((s) => h('span', { class: s.cls }, h('i'), s.name + ' ',
      h('b', { class: 'num ' + tone(s.points.slice(-1)[0]?.[1]), text: s.points.length ? pct(s.points.slice(-1)[0][1]) : '—' })))),
    multiChart(series),
    h('p', { class: 'muted', style: 'font-size:13px;margin:10px 0 0', text: 'Each line starts at 0% on 1 July and shows what 10,000 USDT on paper would have done. Days before each bot went on the server are replayed with the server\'s own Binance data; every day after is real forward paper trading.' }));

  // 5. side by side
  const table = h('div', { class: 'panel' }, h('h2', { text: 'Side by side' }),
    h('div', { class: 'scroll' }, h('table', { class: 'compare' },
      h('thead', {}, h('tr', {}, ...['', 'Trades', 'How often', 'Typical month*', 'Worst drop*', 'Luck checks', 'Risk'].map((c) => h('th', { text: c })))),
      h('tbody', {}, ...BOTS.map((b) => h('tr', {},
        h('td', {}, h('a', { href: '#/' + b.path, text: b.name })), h('td', { text: b.what.split(' · ')[1] || b.what }),
        h('td', { text: b.often }), h('td', { class: 'num', text: b.typical }), h('td', { class: 'num', text: b.worst }),
        h('td', { text: b.luck }), h('td', {}, h('span', { class: 'risk ' + RISK_TONE[b.risk], text: b.risk })))))),
    h('p', { class: 'muted', style: 'font-size:13px;margin:10px 0 0', text: '* From the research tests at 1x after all trading costs (crypto: Jan 2021 – Oct 2026; Worker 3 also 2018–2020). A "typical month" is the middle month: half the months were better, half worse.' })));

  // 6. how to read this page
  const words = [
    ['Paper book', 'The bot\'s trades on pretend money, using real prices and real costs. It shows how the bot is doing without risking anything.'],
    ['Live', 'The bot copies its paper book onto your Binance (or Alpaca) account with real money. Only you can switch it on or off.'],
    ['Long / short', 'Long = bet a coin goes up. Short = bet it goes down. Mixing both means the bots can earn when the market falls.'],
    ['1x', 'No borrowed money: a position is never bigger than the cash behind it.'],
    ['Worst drop', 'The biggest fall from a high point to a low point. A bot with a −40% worst drop can lose 40% before it recovers.'],
    ['Luck checks', '8 tests we run on every strategy to make sure its results are not a fluke, e.g. it must beat random coin picks and keep working with small changes.'],
  ];
  const glossary = h('div', { class: 'panel' }, h('h2', { text: 'How to read this page' }),
    h('div', { class: 'gloss' }, ...words.map(([a, c]) => h('div', {}, h('b', { text: a }), h('p', { text: c })))));

  return h('div', { class: 'stack' },
    h('div', {}, h('h1', { text: 'Your bots' }),
      h('p', { class: 'lead', style: 'margin-bottom:0', text: 'Four trading bots that passed our tests out of more than 165 strategies we tried. Workers 1, 2 and 3 trade crypto in different ways, so they rarely lose at the same time; IBS trades US stocks. Every bot runs on paper all the time, and trades real money only when you switch it on.' })),
    summary,
    h('div', { class: 'grid-bots' }, ...BOTS.map(card)),
    h('div', { class: 'grid-home' }, growth, next),
    table,
    glossary,
    h('p', { class: 'muted', style: 'margin-top:6px;font-size:13px', text: 'Past results do not promise future ones. Crypto bots can lose 20–40% in a bad stretch, and a losing period can last months. Only put in money you can leave alone through a drop.' }));
}

async function ensemble() {
  const d = await pub('/api/public/ensemble');
  const b = d.book || {}, curve = b.curve || [];
  const w = (d.today && d.today.weights) || {};
  const pos = d.positions || [];
  const longs = pos.filter((p) => p[1] > 0).sort((a, c) => c[1] - a[1]), shorts = pos.filter((p) => p[1] < 0).sort((a, c) => a[1] - c[1]);
  const out = h('div', { class: 'stack' },
    h('a', { class: 'back', href: '#/', text: '← All bots' }),
    h('div', {}, h('h1', { text: 'Worker 1' }), h('p', { class: 'muted', style: 'margin:-6px 0 8px', text: 'The Ensemble strategy' }),
      h('p', { class: 'lead', text: 'Three simple crypto methods in one book: buy the strongest coins and short the weakest (weekly), a machine-learning pick of 3 best and 3 worst coins (daily), and 60-day momentum. Rebalanced every day just after 00:00 UTC, at 1x.' })),
    !curve.length ? h('p', { class: 'note', text: 'The paper book is being computed (' + ((d.status && d.status.phase) || 'starting') + '). This takes a few minutes after a restart.' }) : null,
    curve.length ? h('div', { class: 'kpis' },
      h('div', { class: 'kpi' }, h('b', { class: tone(b.net_pct), text: pct(b.net_pct) }), h('span', { text: 'paper book since ' + curve[0][0] })),
      h('div', { class: 'kpi' }, h('b', { class: tone((b.months || []).slice(-1)[0]?.[1]), text: pct((b.months || []).slice(-1)[0]?.[1]) }), h('span', { text: 'this month' })),
      h('div', { class: 'kpi' }, h('b', { text: b.longs + ' / ' + b.shorts }), h('span', { text: 'coins long / short' })),
      h('div', { class: 'kpi' }, h('b', { text: day(b.computed_ts) }), h('span', { text: 'last rebalance' }))) : null,
    curve.length ? h('div', { class: 'panel' }, h('h2', { text: 'Paper book (10,000 USDT start)' }), lineChart(curve, { fmt: (v) => (v / 1000).toFixed(1) + 'k' }),
      h('p', { class: 'muted', style: 'font-size:13px;margin:10px 0 0', text: 'Replayed from 1 July on the server\'s own Binance data; every day from ' + b.first_forward_day + ' on is forward (not replay).' })) : null,
    (b.months || []).length ? h('div', { class: 'panel' }, h('h2', { text: 'Month by month' }),
      h('div', { class: 'months' }, ...b.months.map(([m, v]) => h('div', { class: 'month' }, h('span', { text: m }), h('b', { class: tone(v), text: pct(v) }))))) : null,
    h('div', { class: 'grid2' },
      h('div', { class: 'panel' }, h('h2', { text: 'What the research says' }), facts(FACTS.ensemble)),
      h('div', { class: 'panel' }, h('h2', { text: 'Today\'s book' }),
        h('p', { class: 'muted', style: 'font-size:13px;margin:0 0 10px', text: 'Share of the book per method: rotation ' + Math.round((w.R || 0) * 100) + '%, ML ' + Math.round((w.M || 0) * 100) + '%, momentum ' + Math.round((w.T || 0) * 100) + '%. Figures are % of the book.' }),
        h('h3', { text: 'Long (' + longs.length + ')' }), h('div', { class: 'chips' }, ...longs.map(([c, v]) => h('span', { class: 'up', text: c + ' ' + v.toFixed(1) }))),
        h('h3', { style: 'margin-top:14px', text: 'Short (' + shorts.length + ')' }), h('div', { class: 'chips' }, ...shorts.map(([c, v]) => h('span', { class: 'down', text: c + ' ' + v.toFixed(1) }))))),
    PRIVATE ? await liveBox(LIVE_CFG.worker1) : h('p', { class: 'note', text: 'The live switch is on the private dashboard.' }));
  return out;
}

const LIVE_CFG = {
  worker1: { path: 'ensemble', name: 'Worker 1',
    blurb: 'Copies the paper book onto a Binance account: it opens all of today\'s positions at once (market orders, 1x), then trades whatever the book trades each day. Use a dedicated account with no other positions.',
    onText: (v) => 'Real orders: it opens the ' + (v.book_positions || '') + ' book positions at market (1x), then follows the book every day until you switch it off.' },
  worker3: { path: 'worker3', name: 'Worker 3',
    blurb: 'Copies the paper book onto a Binance account: it opens the book\'s 10 positions at once (5 long, 5 short, market orders, 1x), then follows the daily re-sizing just after 00:00 UTC. Use a dedicated account with no other positions (not Worker 1\'s or Worker 2\'s).',
    onText: (v) => 'Real orders: it opens the ' + (v.book_positions || '') + ' book positions at market (1x), then follows the book every day until you switch it off.' },
  worker2: { path: 'worker2', name: 'Worker 2',
    blurb: 'Copies the paper book onto a Binance account: every day at 16:00 UTC it opens that day\'s positions (market orders, 1x) and at 02:00 UTC it closes them. Switched on during the day, it waits for the next 16:00. Use a dedicated account with no other positions (not Worker 1\'s).',
    onText: () => 'Real orders: every day at 16:00 UTC it opens the day\'s positions at market (1x) and closes them at 02:00 UTC, until you switch it off.' },
};

async function liveBox(cfg) {
  const box = h('div', { class: 'panel' });
  let msg = '', busy = false;
  const draw = async () => {
    let v;
    try { v = await api('/api/' + cfg.path + '/live'); } catch (e) { fill(box, h('h2', { text: 'Live trading' }), h('p', { class: 'note bad', text: e.message })); return; }
    const on = LIVE.includes(v.status);
    const el = [h('div', { class: 'row', style: 'justify-content:space-between' }, h('h2', { style: 'margin:0', text: 'Live trading' }), statusPill(v.status))];
    if (msg) el.push(h('p', { class: 'note ' + (msg.startsWith('Error') ? 'bad' : 'good'), text: msg }));
    if (v.status === 'HALTED' || v.status === 'CLOSING') el.push(h('p', { class: 'note bad', text: v.status + ': ' + (v.reason || '') }));
    if (on) {
      const pnl = v.equity != null && v.amount != null ? v.equity - v.amount : null;
      el.push(h('div', { class: 'kpis', style: 'margin-top:14px' },
        h('div', { class: 'kpi' }, h('b', { text: money(v.amount, 0) }), h('span', { text: 'USDT put in' })),
        h('div', { class: 'kpi' }, h('b', { text: money(v.equity) }), h('span', { text: 'worth now' })),
        h('div', { class: 'kpi' }, h('b', { class: tone(pnl), text: (pnl > 0 ? '+' : '') + money(pnl) }), h('span', { text: 'profit / loss (USDT)' })),
        h('div', { class: 'kpi' }, h('b', { text: String(v.n_positions) }), h('span', { text: 'open positions' }))));
      el.push(h('p', { class: 'muted', style: 'font-size:13px', text: 'Account ' + (v.account ? v.account.label + ' (' + v.account.network + ')' : '—') + (v.max_loss ? ' · loss limit ' + money(v.max_loss, 0) + ' USDT' : '') + ' · on since ' + when(v.armed_ts) + (v.event ? ' · book: ' + (v.event.kind === 'open' ? 'holding since ' : 'flat since ') + v.event.date : ' · last rebalance ' + day(v.last_day)) }));
      if ((v.skipped || []).length) el.push(h('p', { class: 'note', text: 'Skipped at the last rebalance (too small for Binance, or an error): ' + v.skipped.map((s) => s.symbol.replace('USDT', '') + ' (' + s.why + ')').join(', ') }));
      el.push(h('button', { class: 'btn stop', disabled: busy, text: 'Switch off (closes every position)', onclick: async () => {
        if (!await confirmBox('Switch ' + cfg.name + ' off?', 'This closes all ' + v.n_positions + ' positions at market and switches live trading off.')) return;
        busy = true; msg = 'Switching off: closing every position…'; draw();
        try { await post('/api/' + cfg.path + '/live/stop', { sure: true }); msg = cfg.name + ' is off.'; } catch (e) { msg = 'Error: ' + e.message; }
        busy = false; draw();
      } }));
    } else {
      const free = (v.accounts || []).filter((a) => !a.owner && a.exchange === 'binance');
      const acct = h('select', {}, ...(free.length ? free.map((a) => h('option', { value: a.id, text: a.label + ' · ' + a.network })) : [h('option', { value: '', text: 'no free Binance account: link one in Settings' })]));
      const amount = h('input', { type: 'number', min: String(v.rules.min_amount), step: '1', placeholder: 'at least ' + v.rules.min_amount });
      const capTooLow = v.max_amount < v.rules.min_amount;
      el.push(h('p', { style: 'margin:12px 0 0;color:var(--ink-2)', text: cfg.blurb }));
      if (capTooLow) el.push(h('p', { class: 'note bad', text: 'The server\'s safety cap is ' + v.max_amount + ' USDT per bot, below ' + cfg.name + '\'s minimum of ' + v.rules.min_amount + ' USDT, so it cannot be switched on. Raise LIVE_MIRROR_MAX_AMOUNT_USDT to at least ' + v.rules.min_amount + ' first.' }));
      el.push(h('div', { class: 'form' },
        h('label', { class: 'wide' }, 'Binance account', acct),
        h('label', { class: 'wide' }, 'Amount (USDT): minimum ' + v.rules.min_amount + ', maximum ' + v.max_amount, amount)),
        h('button', { class: 'btn go', disabled: busy || !free.length || capTooLow, text: 'Switch on live trading', onclick: async () => {
          const amt = Number(amount.value);
          if (!(amt >= v.rules.min_amount && amt <= v.max_amount)) { msg = 'Enter an amount between ' + v.rules.min_amount + ' and ' + v.max_amount + ' USDT.'; draw(); return; }
          if (!await confirmBox('Switch ' + cfg.name + ' on with ' + amt + ' USDT?', 'Account ' + acct.options[acct.selectedIndex].text + '. ' + cfg.onText(v))) return;
          busy = true; msg = 'Switching on: checking the account, then opening the positions (can take a minute)…'; draw();
          try { await post('/api/' + cfg.path + '/live/start', { account_id: acct.value, amount: amt, confirm: v.confirm_phrase }); msg = cfg.name + ' is live.'; }
          catch (e) { msg = 'Error: ' + e.message; }
          busy = false; draw();
        } }));
    }
    if ((v.trades || []).length) el.push(h('details', { style: 'margin-top:14px' }, h('summary', { text: 'Live orders (' + v.trades.length + ')' }), tradesTable(v.trades)));
    if ((v.log || []).length) el.push(h('details', { style: 'margin-top:10px' }, h('summary', { text: 'Live log' }), logList(v.log)));
    fill(box, ...el);
  };
  await draw();
  return box;
}

async function worker2() {
  const d = await pub('/api/public/worker2');
  const b = d.book || {}, curve = b.curve || [], ev = d.event || {};
  const pos = d.positions || [];
  const last = (b.months || []).slice(-1)[0];
  const now = ev.kind === 'open'
    ? (pos.length ? 'Holding ' + pos.length + ' position' + (pos.length > 1 ? 's' : '') + ' from ' + ev.date + ' until 02:00 UTC.' : 'No coin qualified at ' + ev.date + '. Next check: 16:00 UTC.')
    : 'Flat. The next check is at 16:00 UTC.';
  return h('div', { class: 'stack' },
    h('a', { class: 'back', href: '#/', text: '← All bots' }),
    h('div', {}, h('h1', { text: 'Worker 2' }), h('p', { class: 'muted', style: 'margin:-6px 0 8px', text: 'Afternoon momentum' }),
      h('p', { class: 'lead', text: 'Every day at 16:00 UTC it looks at the 40 most-traded Binance futures (not BTC or ETH). Coins that made an unusually big move since 00:00 tend to keep going into the evening: it rides that move until 02:00 UTC. At most 5 coins, 1x, smaller positions for wild coins.' })),
    !curve.length ? h('p', { class: 'note', text: 'The paper book is being computed (' + ((d.status && d.status.phase) || 'starting') + '). The first run fetches hourly data and takes a few minutes.' }) : null,
    curve.length ? h('div', { class: 'kpis' },
      h('div', { class: 'kpi' }, h('b', { class: tone(b.net_pct), text: pct(b.net_pct) }), h('span', { text: 'paper book since ' + curve[0][0] })),
      h('div', { class: 'kpi' }, h('b', { class: tone(last && last[1]), text: last ? pct(last[1]) : '—' }), h('span', { text: 'this month' })),
      h('div', { class: 'kpi' }, h('b', { text: String(b.n_trades || 0) }), h('span', { text: 'paper trades' })),
      h('div', { class: 'kpi' }, h('b', { text: String(pos.length) }), h('span', { text: 'open now' }))) : null,
    ev.kind ? h('p', { class: 'note', text: now }) : null,
    pos.length ? h('div', { class: 'chips' }, ...pos.map(([c, v]) => h('span', { class: v > 0 ? 'up' : 'down', text: (v > 0 ? 'long ' : 'short ') + c + ' ' + Math.abs(v).toFixed(1) + '%' }))) : null,
    curve.length ? h('div', { class: 'panel' }, h('h2', { text: 'Paper book (10,000 USDT start)' }), lineChart(curve, { fmt: (v) => (v / 1000).toFixed(1) + 'k' }),
      h('p', { class: 'muted', style: 'font-size:13px;margin:10px 0 0', text: 'Replayed from 1 July on the server\'s own Binance data; every day from ' + b.first_forward_day + ' on is forward (not replay).' })) : null,
    (b.months || []).length ? h('div', { class: 'panel' }, h('h2', { text: 'Month by month' }),
      h('div', { class: 'months' }, ...b.months.map(([m, v]) => h('div', { class: 'month' }, h('span', { text: m }), h('b', { class: tone(v), text: pct(v) }))))) : null,
    h('div', { class: 'grid2' },
      h('div', { class: 'panel' }, h('h2', { text: 'What the research says' }), facts(FACTS.worker2)),
      h('div', { class: 'panel' }, h('h2', { text: 'Recent paper trades' }), (b.trades || []).length ? h('div', { class: 'scroll' }, h('table', {},
        h('thead', {}, h('tr', {}, ...['Day', 'Coin', 'Side', 'Result'].map((c) => h('th', { text: c })))),
        h('tbody', {}, ...[...b.trades].reverse().slice(0, 25).map((t) => h('tr', {}, h('td', { text: t.day }), h('td', { text: t.symbol.replace('USDT', '') }),
          h('td', { text: t.side > 0 ? 'long' : 'short' }), h('td', { class: 'num ' + tone(t.ret_pct), text: pct(t.ret_pct) })))))) : h('p', { class: 'muted', text: 'No trades yet.' }))),
    PRIVATE ? await liveBox(LIVE_CFG.worker2) : h('p', { class: 'note', text: 'The live switch is on the private dashboard.' }));
}

async function worker3() {
  const d = await pub('/api/public/worker3');
  const b = d.book || {}, curve = b.curve || [];
  const pos = d.positions || [];
  const longs = pos.filter((p) => p[1] > 0), shorts = pos.filter((p) => p[1] < 0).sort((a, c) => a[1] - c[1]);
  const last = (b.months || []).slice(-1)[0];
  return h('div', { class: 'stack' },
    h('a', { class: 'back', href: '#/', text: '← All bots' }),
    h('div', {}, h('h1', { text: 'Worker 3' }), h('p', { class: 'muted', style: 'margin:-6px 0 8px', text: '30-day highs (new, paper test)' }),
      h('p', { class: 'lead', text: 'Every day at 00:00 UTC it looks at the 20 most-traded Binance futures. Coins closing near their 30-day high tend to keep rising, coins near their 30-day low tend to keep falling: it buys the 5 nearest their high and shorts the 5 nearest their low, a tenth of the money each, 1x.' })),
    !curve.length ? h('p', { class: 'note', text: 'The paper book is being computed (' + ((d.status && d.status.phase) || 'starting') + '). It runs just after 00:00 UTC, after Worker 1 has fetched the day\'s data.' }) : null,
    curve.length ? h('div', { class: 'kpis' },
      h('div', { class: 'kpi' }, h('b', { class: tone(b.net_pct), text: pct(b.net_pct) }), h('span', { text: 'paper book since ' + curve[0][0] })),
      h('div', { class: 'kpi' }, h('b', { class: tone(last && last[1]), text: last ? pct(last[1]) : '—' }), h('span', { text: 'this month' })),
      h('div', { class: 'kpi' }, h('b', { text: (b.longs || 0) + ' / ' + (b.shorts || 0) }), h('span', { text: 'coins long / short' })),
      h('div', { class: 'kpi' }, h('b', { text: day(b.computed_ts) }), h('span', { text: 'last rebalance' }))) : null,
    pos.length ? h('div', { class: 'panel' }, h('h2', { text: 'Today\'s book (% of the money)' }),
      h('h3', { text: 'Long (' + longs.length + ')' }), h('div', { class: 'chips' }, ...longs.map(([c, v]) => h('span', { class: 'up', text: c + ' ' + v.toFixed(1) }))),
      h('h3', { style: 'margin-top:14px', text: 'Short (' + shorts.length + ')' }), h('div', { class: 'chips' }, ...shorts.map(([c, v]) => h('span', { class: 'down', text: c + ' ' + v.toFixed(1) })))) : null,
    curve.length ? h('div', { class: 'panel' }, h('h2', { text: 'Paper book (10,000 USDT start)' }), lineChart(curve, { fmt: (v) => (v / 1000).toFixed(1) + 'k' }),
      h('p', { class: 'muted', style: 'font-size:13px;margin:10px 0 0', text: 'Replayed from 1 July on the server\'s own Binance data; every day from ' + b.first_forward_day + ' on is forward (not replay).' + ((b.liquidations || []).length ? ' Shorts wiped out by a coin doubling: ' + b.liquidations.map((x) => x[0].replace('USDT', '') + ' ' + x[1]).join(', ') + '.' : '') })) : null,
    (b.months || []).length ? h('div', { class: 'panel' }, h('h2', { text: 'Month by month' }),
      h('div', { class: 'months' }, ...b.months.map(([m, v]) => h('div', { class: 'month' }, h('span', { text: m }), h('b', { class: tone(v), text: pct(v) }))))) : null,
    h('div', { class: 'grid2' },
      h('div', { class: 'panel' }, h('h2', { text: 'What the research says' }), facts(FACTS.worker3)),
      h('div', { class: 'panel' }, h('h2', { text: 'Recent closed positions' }), (b.trades || []).length ? h('div', { class: 'scroll' }, h('table', {},
        h('thead', {}, h('tr', {}, ...['Closed', 'Coin', 'Side', 'Days', 'Result (USDT)'].map((c) => h('th', { text: c })))),
        h('tbody', {}, ...[...b.trades].reverse().slice(0, 25).map((t) => h('tr', {}, h('td', { text: t.closed }), h('td', { text: t.symbol.replace('USDT', '') }),
          h('td', { text: t.side > 0 ? 'long' : 'short' }), h('td', { text: String(t.days) }), h('td', { class: 'num ' + tone(t.pnl), text: (t.pnl > 0 ? '+' : '') + money(t.pnl) })))))) : h('p', { class: 'muted', text: 'No closed positions yet.' }))),
    PRIVATE ? await liveBox(LIVE_CFG.worker3) : h('p', { class: 'note', text: 'The live switch is on the private dashboard.' }));
}

async function ibs() {
  const d = await pub('/api/public/ibs-bot');
  const priv = PRIVATE ? await api('/api/ibs-bot').catch((e) => ({ _err: e.message })) : null;
  const books = (d.shadow && d.shadow.books) || {};
  const decisions = d.decisions || [];
  return h('div', { class: 'stack' },
    h('a', { class: 'back', href: '#/', text: '← All bots' }),
    h('div', {}, h('h1', { text: 'IBS stocks' }),
      h('p', { class: 'lead', text: 'Buys the Nasdaq-100 (QQQ, and the 3x fund TQQQ) on days it closes near its daily low, and sells when it closes above the previous day\'s high. Decides 2 minutes before the US close. Long only.' })),
    priv && priv.error ? h('p', { class: 'note bad', text: 'The bot reports: ' + priv.error.replace('System -> Providers & Live', 'Settings → Exchange keys') }) : null,
    h('div', { class: 'kpis' },
      ...Object.entries(books).map(([k, b]) => h('div', { class: 'kpi' }, h('b', { class: tone(b.realized_pct), text: pct(b.realized_pct) }), h('span', { text: b.symbol + ' practice book' + (b.holding ? ' · holding' : '') }))),
      h('div', { class: 'kpi' }, h('b', { text: String(decisions.length) }), h('span', { text: 'days recorded' })),
      h('div', { class: 'kpi' }, h('b', { text: d.data_ok ? 'OK' : 'No data' }), h('span', { text: 'market data' }))),
    h('div', { class: 'grid2' },
      h('div', { class: 'panel' }, h('h2', { text: 'What the research says' }), facts(FACTS.ibs)),
      h('div', { class: 'panel' }, h('h2', { text: 'Recent decisions' }), decisions.length ? h('div', { class: 'scroll' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', { text: 'Day' }), h('th', { text: 'IBS' }), h('th', { text: 'Action' }))),
        h('tbody', {}, ...decisions.slice(0, 15).map((x) => h('tr', {}, h('td', { class: 'num', text: x.day || '' }), h('td', { class: 'num', text: x.ibs != null ? Number(x.ibs).toFixed(2) : '—' }), h('td', { text: x.action || x.decision || '' }))))))
        : h('p', { class: 'muted', text: 'No trading day recorded yet.' }))),
    PRIVATE ? ibsLive(priv) : h('p', { class: 'note', text: 'The live switch is on the private dashboard.' }));
}

function ibsLive(v) {
  const box = h('div', { class: 'panel' });
  if (!v || v._err) { box.append(h('h2', { text: 'Live trading' }), h('p', { class: 'note bad', text: (v && v._err) || 'unavailable' })); return box; }
  let msg = '', busy = false;
  const draw = (v) => {
    const s = v.live.status, on = LIVE.includes(s), c = v.controls || {};
    const el = [h('div', { class: 'row', style: 'justify-content:space-between' }, h('h2', { style: 'margin:0', text: 'Live trading' }), statusPill(s))];
    if (msg) el.push(h('p', { class: 'note ' + (msg.startsWith('Error') ? 'bad' : 'good'), text: msg }));
    if (on) {
      el.push(h('p', { text: 'On ' + (v.live.network || '') + ' since ' + when(v.live.armed_ts) + ' · capital ' + money(v.live.capital, 0) + ' USD · loss limit ' + money(v.live.max_loss, 0) + ' USD' }));
      el.push(h('button', { class: 'btn stop', disabled: busy, text: 'Switch off (sells QQQ / TQQQ)', onclick: async () => {
        if (!await confirmBox('Switch the IBS bot off?', 'This sells its QQQ and TQQQ at market (at the next open if the market is closed).')) return;
        busy = true; msg = 'Switching off…'; draw(v);
        try { const r = await post('/api/ibs-bot/stop', { sure: true }); msg = 'The IBS bot is off.'; v = { ...v, live: { ...v.live, ...r.bot.live } }; } catch (e) { msg = 'Error: ' + e.message; }
        busy = false; draw(await api('/api/ibs-bot').catch(() => v));
      } }));
    } else {
      const net = h('select', {}, h('option', { value: 'mainnet', text: 'Real money (mainnet)' }), h('option', { value: 'testnet', text: 'Alpaca paper account (testnet)' }));
      const cq = h('input', { type: 'number', min: '0', step: '1', placeholder: 'e.g. 100' });
      const ct = h('input', { type: 'number', min: '0', step: '1', placeholder: 'e.g. 100' });
      const loss = h('input', { type: 'number', min: '1', step: '1', placeholder: 'e.g. 60' });
      const phrase = h('input', { autocomplete: 'off', spellcheck: 'false', placeholder: c.confirm_phrase });
      const keys = c.keys || {};
      el.push(h('p', { style: 'margin:12px 0 0;color:var(--ink-2)', text: 'Trades QQQ and TQQQ on your Alpaca account. Total capital is capped at ' + c.max_capital + ' USD by the server. Alpaca keys: mainnet ' + (keys.mainnet ? 'saved' : 'missing') + ', paper ' + (keys.testnet ? 'saved' : 'missing') + ' (Settings).' }));
      el.push(h('div', { class: 'form' },
        h('label', { class: 'wide' }, 'Account', net),
        h('label', {}, 'QQQ capital (USD)', cq), h('label', {}, 'TQQQ capital (USD)', ct),
        h('label', {}, 'Loss limit (USD)', loss),
        h('label', {}, h('span', {}, 'Type ', h('code', { text: c.confirm_phrase })), phrase)),
        h('button', { class: 'btn go', disabled: busy, text: 'Switch on live trading', onclick: async () => {
          busy = true; msg = 'Switching on…'; draw(v);
          try { await post('/api/ibs-bot/start', { network: net.value, capital_qqq: Number(cq.value || 0), capital_tqqq: Number(ct.value || 0), max_loss: Number(loss.value), confirm: phrase.value }); msg = 'The IBS bot is live.'; }
          catch (e) { msg = 'Error: ' + e.message; }
          busy = false; draw(await api('/api/ibs-bot').catch(() => v));
        } }));
    }
    if ((v.journal || []).length) el.push(h('details', { style: 'margin-top:14px' }, h('summary', { text: 'Bot journal' }),
      h('ul', { class: 'log' }, ...v.journal.slice(0, 40).map((j) => h('li', { text: when(j.ts) + '  ' + j.event + '  ' + (j.detail || '') })))));
    fill(box, ...el);
  };
  draw(v);
  return box;
}

function research() {
  const rows = [
    ['Worker 2: afternoon momentum (alts, 16:00–02:00 UTC)', 'Kept', '8 of 8 luck checks after vol-scaled sizes; +2–3% a month lately, weakening'],
    ['Retail-positioning contrarian', 'Not used', '7 of 8: one coin carried too much of the profit'],
    ['Worker 1: Ensemble (crypto rotation + ML + momentum)', 'Kept', '8 of 8 luck checks; about +2.7% in a typical month, worst drop 23%'],
    ['IBS on QQQ / TQQQ (US stocks)', 'Kept', 'Passed its pre-registered test; about 18 trades a year'],
    ['Swing trend bots V17.3, V17.4, V18.4', 'Not used', 'Positive in all 4 test periods but small (about +2–3% a month)'],
    ['ZEC bots and MACD coin bots', 'Removed', 'Coins picked after seeing the results; one coin\'s rally carries the profit'],
    ['Donchian, No-Wick, divergence and TradingView desks', 'Removed', 'Lost after costs, or only worked on hand-picked coins'],
    ['Scalping and day-trading bots (V7–V15)', 'Removed', 'Every one lost after trading costs'],
    ['52 YouTube, 35 Scribd and 9 gold 1-minute strategies', 'Removed', '0 passed; win rates were no better than chance'],
    ['Hunting +20% a month', 'Stopped', 'Only reachable with leverage that brings 60–95% drops'],
    ['AI trading desk, pump.fun recorder, other paper tests', 'Stopped', 'Research only; nothing ready for real money'],
  ];
  return h('div', { class: 'stack' },
    h('h1', { text: 'What we tested' }),
    h('p', { class: 'lead', text: 'More than 165 strategies were tested on years of data with real trading costs, then checked for luck (does it survive without its best months, on other coins, against random picks?). This is where each family ended up.' }),
    h('div', { class: 'panel scroll' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', { text: 'Strategy' }), h('th', { text: 'Status' }), h('th', { text: 'Why' }))),
      h('tbody', {}, ...rows.map(([a, b, c]) => h('tr', {}, h('td', { text: a }), h('td', {}, h('span', { class: 'pill ' + (b === 'Kept' ? 'live' : ''), text: b })), h('td', { class: 'muted', text: c })))))),
    h('div', { class: 'panel' }, h('h2', { text: 'The honest ceiling' }),
      h('p', { style: 'margin:0', text: 'Across everything we tried, strategies that are not luck make about +2% to +5% a month on average, with drops of 20–45% along the way. Anything promising much more relied on leverage, a few lucky trades, or coins picked with hindsight.' })));
}

async function settings() {
  if (!PRIVATE) return h('p', { class: 'note', text: 'Settings are on the private dashboard.' });
  const root = h('div', { class: 'stack' }, h('h1', { text: 'Settings' }), h('p', { class: 'lead', text: 'Exchange keys, the accounts the bots trade on, and the emergency stop. Keys are stored encrypted on the server and never shown again.' }));
  root.append(await keysPanel(), await accountsPanel(), killPanel());
  return root;
}

async function keysPanel() {
  const box = h('div', { class: 'panel' });
  const draw = async (msg) => {
    let d;
    try { d = await api('/api/mirror/providers'); } catch (e) { fill(box, h('h2', { text: 'Exchange keys' }), h('p', { class: 'note bad', text: e.message })); return; }
    const rows = (d.providers || []).filter((p) => ['binance', 'alpaca', 'telegram'].includes(p.exchange));
    fill(box, h('h2', { text: 'Exchange keys' }),
      msg ? h('p', { class: 'note ' + (msg.startsWith('Error') ? 'bad' : 'good'), text: msg }) : null,
      h('p', { class: 'muted', style: 'font-size:13px;margin:0 0 8px', text: 'Real-money cap per bot: ' + d.max_amount_usdt + ' USDT (server setting LIVE_MIRROR_MAX_AMOUNT_USDT).' }),
      h('div', { class: 'scroll' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', { text: 'Provider' }), h('th', { text: 'Keys' }), h('th', { class: 'r', text: 'Balance' }), h('th', {}))),
        h('tbody', {}, ...rows.map((p) => {
          const bal = p.balance && p.balance.ok !== false && p.balance.wallet != null ? money(p.balance.wallet) : '—';
          return h('tr', {}, h('td', { text: p.label }), h('td', { class: p.keys_configured ? 'up' : 'muted', text: p.keys_configured ? 'saved' : 'not set' }),
            h('td', { class: 'r num', text: bal }),
            h('td', { class: 'r' }, p.can_save_keys && p.exchange !== 'telegram' ? h('button', { class: 'btn', text: p.keys_configured ? 'Replace' : 'Add', onclick: () => keyForm(p, draw) }) : null,
              p.keys_configured && p.key_source !== 'env' && p.exchange !== 'telegram' ? h('button', { class: 'btn', style: 'margin-left:6px', text: 'Remove', onclick: async () => {
                if (!await confirmBox('Remove these keys?', p.label + ': the bots can no longer use this account.')) return;
                try { await post('/api/mirror/providers/' + p.exchange + '/' + p.network + '/keys/clear'); draw('Keys removed.'); } catch (e) { draw('Error: ' + e.message); }
              } }) : null));
        })))),
      h('div', { id: 'keyform' }));
  };
  const keyForm = (p, redraw) => {
    const k = h('input', { autocomplete: 'off', spellcheck: 'false', placeholder: p.exchange === 'telegram' ? 'bot token' : 'API key' });
    const s = h('input', { type: 'password', autocomplete: 'new-password', placeholder: p.exchange === 'telegram' ? 'chat id' : 'API secret' });
    fill(box.querySelector('#keyform'), h('div', { class: 'note', style: 'margin-top:14px' }, h('b', { text: p.label }),
      h('div', { class: 'form' }, h('label', {}, p.exchange === 'telegram' ? 'Token' : 'API key', k), h('label', {}, p.exchange === 'telegram' ? 'Chat id' : 'Secret', s)),
      h('div', { class: 'row' }, h('button', { class: 'btn go', text: 'Save (checked with a read-only call)', onclick: async () => {
        try { await post('/api/mirror/providers/' + p.exchange + '/' + p.network + '/keys', { key: k.value, secret: s.value }); k.value = s.value = ''; redraw('Keys saved.'); }
        catch (e) { k.value = s.value = ''; redraw('Error: ' + e.message); }
      } }), h('button', { class: 'btn', text: 'Cancel', onclick: () => fill(box.querySelector('#keyform'), ) }))));
  };
  await draw();
  return box;
}

async function accountsPanel() {
  const box = h('div', { class: 'panel' });
  const draw = async (msg) => {
    let a, bal = {};
    try { a = (await api('/api/accounts')).accounts || []; } catch (e) { fill(box, h('h2', { text: 'Trading accounts' }), h('p', { class: 'note bad', text: e.message })); return; }
    try { bal = (await api('/api/accounts/balances')).balances || {}; } catch (e) { /* balances are optional */ }
    const label = h('input', { placeholder: 'e.g. Binance main' });
    fill(box, h('h2', { text: 'Trading accounts' }),
      h('p', { class: 'muted', style: 'font-size:13px;margin:0 0 8px', text: 'Each bot trades on its own linked account; one bot per account.' }),
      msg ? h('p', { class: 'note ' + (msg.startsWith('Error') ? 'bad' : 'good'), text: msg }) : null,
      a.length ? h('div', { class: 'scroll' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', { text: 'Account' }), h('th', { text: 'Used by' }), h('th', { class: 'r', text: 'USDT' }), h('th', {}))),
        h('tbody', {}, ...a.map((r) => {
          const b = bal[r.id];
          return h('tr', {}, h('td', { text: r.label + ' · ' + r.exchange + ' ' + r.network }), h('td', { class: r.owner ? '' : 'muted', text: r.owner ? r.owner.ref : 'free' }),
            h('td', { class: 'r num', text: b && b.ok ? money(b.wallet) : '—' }),
            h('td', { class: 'r' }, r.owner ? null : h('button', { class: 'btn', text: 'Unlink', onclick: async () => {
              if (!await confirmBox('Unlink this account?', r.label + ' will no longer be available to the bots.')) return;
              try { await post('/api/accounts/' + r.id + '/remove'); draw('Account unlinked.'); } catch (e) { draw('Error: ' + e.message); }
            } })));
        })))) : h('p', { class: 'muted', text: 'No account linked yet.' }),
      h('div', { class: 'row', style: 'margin-top:14px' }, label, h('button', { class: 'btn', text: 'Link the saved Binance mainnet key', onclick: async () => {
        try { await post('/api/accounts/import', { exchange: 'binance', network: 'mainnet', label: label.value || 'Binance mainnet' }); draw('Account linked.'); } catch (e) { draw('Error: ' + e.message); }
      } })));
  };
  await draw();
  return box;
}

function killPanel() {
  const out = h('p', { class: 'msg' });
  return h('div', { class: 'panel' }, h('h2', { text: 'Emergency stop' }),
    h('p', { style: 'margin:0 0 12px;color:var(--ink-2)', text: 'Switches every live bot off and closes all their positions at market.' }),
    h('button', { class: 'btn stop', text: 'Stop everything', onclick: async () => {
      if (!await confirmBox('Stop everything?', 'Every live bot switches off and closes its positions at market.')) return;
      out.textContent = 'Stopping…';
      try { await post('/api/kill'); out.textContent = 'Every live bot is off.'; out.className = 'msg up'; } catch (e) { out.textContent = 'Error: ' + e.message; out.className = 'msg down'; }
    } }), out);
}

// ---- small shared views ------------------------------------------------------------------------------------------
function facts(rows) { return h('ul', { class: 'facts' }, ...rows.map(([a, b]) => h('li', {}, h('span', { text: a }), h('span', { class: 'num', text: b })))); }
function logList(log) {
  return h('ul', { class: 'log' }, ...[...log].reverse().slice(0, 40).map((l) => h('li', { text: when(l.ts) + '  ' + l.kind + '  '
    + Object.entries(l).filter(([k]) => !['ts', 'kind'].includes(k)).map(([k, x]) => k + '=' + (typeof x === 'object' ? JSON.stringify(x) : x)).join(' ') })));
}
function tradesTable(trades) {
  const cols = Object.keys(trades[0] || {}).filter((k) => typeof trades[0][k] !== 'object').slice(0, 7);
  return h('div', { class: 'scroll' }, h('table', {}, h('thead', {}, h('tr', {}, ...cols.map((c) => h('th', { text: c })))),
    h('tbody', {}, ...[...trades].reverse().slice(0, 60).map((t) => h('tr', {}, ...cols.map((c) => h('td', { class: 'num', text: c === 'ts' ? when(t[c]) : String(t[c] ?? '') })))))));
}

if (PRIVATE && Auth.get() == null) showLogin();
route();
