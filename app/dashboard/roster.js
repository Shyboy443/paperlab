/* PaperLab - THE ROSTER: the home screen. The few named bots that are actually winning, each on its own card (an avatar,
   a name and a personality, rank, equity, the position it holds, its equity curve, its last call), a leaderboard and a
   stream of their decisions. Everyone else is retired from view: still paper trading in the background, back on stage
   the moment it climbs into profit. Data: GET /api/public/competition/roster (read-only, cached 15 s server-side).
   Clicking a bot opens its full page (candles, position boxes, trades) in the Bots view. */
'use strict';

const RS_ACCENTS = ['#f5b53d', '#9d8cff', '#ff5c8a', '#3fd0ff', '#7fe36b', '#ff8a3d', '#d37cff', '#40e0b0'];
const RS_NS = 'http://www.w3.org/2000/svg';

function rsSvg(tag, attrs, ...kids) {
  const el = document.createElementNS(RS_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  for (const c of kids.flat()) if (c) el.append(c);
  return el;
}
function rsHash(s) { let x = 2166136261; for (const ch of String(s)) { x ^= ch.charCodeAt(0); x = Math.imul(x, 16777619); } return x >>> 0; }

/** A little robot face, different for every name: head shape, eyes, antenna and mouth all come from the name's hash. */
function rsAvatar(name, color, size = 46) {
  const k = rsHash(name), pick = (n, shift) => (k >>> shift) % n;
  const svg = rsSvg('svg', { viewBox: '0 0 48 48', width: size, height: size, class: 'rs-ava-svg', 'aria-hidden': 'true' });
  const dark = '#0b0e13';
  svg.append(rsSvg('circle', { cx: 24, cy: 24, r: 23, fill: color, opacity: 0.16 }));
  const ant = pick(3, 3);
  if (ant === 0) svg.append(rsSvg('line', { x1: 24, y1: 6, x2: 24, y2: 13, stroke: color, 'stroke-width': 2 }), rsSvg('circle', { cx: 24, cy: 6, r: 2.6, fill: color }));
  if (ant === 1) svg.append(rsSvg('path', { d: 'M17 13 L14 6 M31 13 L34 6', stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round' }),
    rsSvg('circle', { cx: 14, cy: 6, r: 2, fill: color }), rsSvg('circle', { cx: 34, cy: 6, r: 2, fill: color }));
  if (ant === 2) svg.append(rsSvg('path', { d: 'M18 13 Q24 3 30 13', fill: 'none', stroke: color, 'stroke-width': 2 }));
  const rx = [4, 9, 14][pick(3, 6)];
  svg.append(rsSvg('rect', { x: 9, y: 13, width: 30, height: 26, rx, fill: color }));
  svg.append(rsSvg('rect', { x: 5.5, y: 22, width: 4, height: 9, rx: 2, fill: color, opacity: 0.8 }),
    rsSvg('rect', { x: 38.5, y: 22, width: 4, height: 9, rx: 2, fill: color, opacity: 0.8 }));
  const eyes = pick(4, 9);
  if (eyes === 0) svg.append(rsSvg('circle', { cx: 18, cy: 24, r: 3.4, fill: dark }), rsSvg('circle', { cx: 30, cy: 24, r: 3.4, fill: dark }),
    rsSvg('circle', { cx: 19, cy: 23, r: 1.1, fill: '#fff' }), rsSvg('circle', { cx: 31, cy: 23, r: 1.1, fill: '#fff' }));
  if (eyes === 1) svg.append(rsSvg('rect', { x: 13, y: 20, width: 22, height: 7, rx: 3.5, fill: dark }),
    rsSvg('rect', { x: 16, y: 22, width: 6, height: 3, rx: 1.5, fill: '#fff', opacity: 0.85 }));
  if (eyes === 2) svg.append(rsSvg('path', { d: 'M14 25 Q18 20 22 25 M26 25 Q30 20 34 25', fill: 'none', stroke: dark, 'stroke-width': 2.6, 'stroke-linecap': 'round' }));
  if (eyes === 3) svg.append(rsSvg('rect', { x: 14, y: 21, width: 7, height: 6, rx: 1.5, fill: dark }), rsSvg('rect', { x: 27, y: 21, width: 7, height: 6, rx: 1.5, fill: dark }));
  const mouth = pick(3, 12);
  if (mouth === 0) svg.append(rsSvg('path', { d: 'M18 32 Q24 36 30 32', fill: 'none', stroke: dark, 'stroke-width': 2.2, 'stroke-linecap': 'round' }));
  if (mouth === 1) svg.append(rsSvg('rect', { x: 17, y: 31, width: 14, height: 4, rx: 1, fill: dark }),
    rsSvg('path', { d: 'M20.5 31v4 M24 31v4 M27.5 31v4', stroke: color, 'stroke-width': 1 }));
  if (mouth === 2) svg.append(rsSvg('line', { x1: 19, y1: 33, x2: 29, y2: 33, stroke: dark, 'stroke-width': 2.4, 'stroke-linecap': 'round' }));
  return svg;
}

/** Equity curve as % of the start: area + line in the bot's colour, a dashed "start" line, the range on the right. */
function rsCurve(curve, start, color, id) {
  const W = 300, H = 118, pad = { l: 4, r: 44, t: 10, b: 18 };
  const box = h('div', { class: 'rs-chart' });
  if (!curve || curve.length < 2 || !start) { box.append(h('div', { class: 'rs-chart-empty', text: 'collecting equity…' })); return box; }
  const pts = curve.map(([t, v]) => [t, (v / start - 1) * 100]);
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  let lo = Math.min(0, ...pts.map((p) => p[1])), hi = Math.max(0, ...pts.map((p) => p[1]));
  if (hi - lo < 0.5) { hi += 0.25; lo -= 0.25; }
  const X = (t) => pad.l + (t1 > t0 ? (t - t0) / (t1 - t0) : 1) * (W - pad.l - pad.r);
  const Y = (v) => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
  let d = '';
  pts.forEach(([t, v], i) => { d += (i ? ` L${X(t).toFixed(1)} ${Y(pts[i - 1][1]).toFixed(1)} L` : 'M') + `${X(t).toFixed(1)} ${Y(v).toFixed(1)}`; });
  const area = d + ` L${X(t1).toFixed(1)} ${Y(lo).toFixed(1)} L${X(t0).toFixed(1)} ${Y(lo).toFixed(1)} Z`;
  const gid = 'rsg-' + id;
  const svg = rsSvg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', class: 'rs-chart-svg' },
    rsSvg('defs', null, rsSvg('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
      rsSvg('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': 0.34 }), rsSvg('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': 0 }))),
    rsSvg('line', { x1: pad.l, x2: W - pad.r, y1: Y(0), y2: Y(0), class: 'rs-zero' }),
    rsSvg('path', { d: area, fill: `url(#${gid})` }),
    rsSvg('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.8, 'vector-effect': 'non-scaling-stroke' }),
    rsSvg('circle', { cx: X(t1), cy: Y(pts[pts.length - 1][1]), r: 3, fill: color }));
  const lab = (v, y, cls) => h('span', { class: 'rs-y ' + (cls || ''), style: `top:${(y / H * 100).toFixed(1)}%`, text: (v > 0 ? '+' : '') + v.toFixed(1) + '%' });
  const tm = (ms) => new Date(ms).toISOString().slice(5, 16).replace('T', ' ');
  box.append(svg, lab(hi, Y(hi)), lab(lo, Y(lo)), h('span', { class: 'rs-x l', text: tm(t0) }), h('span', { class: 'rs-x r', text: tm(t1) }));
  if (Math.abs(Y(0) - Y(hi)) > 12 && Math.abs(Y(0) - Y(lo)) > 12) box.append(h('span', { class: 'rs-y start', style: `top:${(Y(0) / H * 100).toFixed(1)}%`, text: 'start' }));
  return box;
}

const Roster = {
  base: '/api/public/competition',
  data: null, loadedAt: 0, loading: null, showRetired: false,

  async fetch(force = false) {
    if (!force && this.data && Date.now() - this.loadedAt < 10_000) return this.data;
    if (this.loading) return this.loading;
    this.loading = getJSON(this.base + '/roster').then((d) => { this.data = d; this.loadedAt = Date.now(); return d; })
      .finally(() => { this.loading = null; });
    return this.loading;
  },
  color(x) {
    const used = new Map();
    for (const r of [...((this.data || {}).roster || []), ...((this.data || {}).scanners || [])]) {
      let i = rsHash(r.name) % RS_ACCENTS.length;
      while ([...used.values()].includes(i) && used.size < RS_ACCENTS.length) i = (i + 1) % RS_ACCENTS.length;
      used.set(r.program + '|' + r.key, i);
    }
    const i = used.get(x.program + '|' + x.key);
    return RS_ACCENTS[i ?? (rsHash(x.name || x.key) % RS_ACCENTS.length)];
  },
  /** the scanners' Jev twins: Jev decides their take-profits (and may skip the trade) */
  isJevTp(x) { return (x.program === 'v11' || x.prog === 'v11') && String(x.key || '').endsWith('+JEV'); },
  isJevGo(x) { return (x.program === 'v12' || x.prog === 'v12') && String(x.key || '').endsWith('+JEV'); },
  jevBadge(x, small) {
    if (this.isJevGo(x)) return h('span', { class: 'rs-jev' + (small ? ' small' : ''), title: 'Jev decides whether each breakout is real: it takes it at full size, or says WAIT and Bizzy keeps watching', text: small ? 'JEV GO/WAIT' : 'JEV SAYS GO / WAIT' });
    return this.isJevTp(x) ? h('span', { class: 'rs-jev' + (small ? ' small' : ''), title: 'Jev decides the take-profits of this bot: it skips a trade Jev contradicts, and stretches TP1 / TP2 / TP3 further the more confident Jev is', text: small ? 'JEV TP' : 'JEV DECIDES TPs' }) : null;
  },
  nameOf(prog, key) { return (((this.data || {}).names || {})[prog + '|' + key] || {}).name || ''; },
  label(prog, key) { const n = this.nameOf(prog, key); return n ? n + ' · ' + key : key; },
  money(v, ccy, d = 2) {
    if (!isNum(v)) return DASH;
    return ccy === 'USD' ? (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : v.toFixed(d);
  },
  delta(v, ccy, d = 2) {
    if (!isNum(v)) return DASH;
    const s = v > 0 ? '+' : v < 0 ? '−' : '';
    return s + (ccy === 'USD' ? '$' : '') + Math.abs(v).toFixed(d);
  },
  pct(v, d = 2) { return isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v * 100).toFixed(d) + '%' : DASH; },
  ago(ms) { return ms ? aAge(Date.now() - ms) + ' ago' : ''; },
  open(x) {
    Arena.prog = x.program; Arena.coin = ''; Arena.tf = '';
    try { localStorage.setItem('arena.prog', x.program); } catch (e) { /* ignore */ }
    if (Arena.view !== 'bots') ArenaNav.go('bots');
    Arena.openBot(x.key);
  },

  /* ---- the home screen ---- */
  render(grid) {
    const d = this.data;
    if (!d) { grid.append(h('div', { class: 'card' }, h('p', { class: 'sub', text: 'loading the roster…' }))); return; }
    const k = d.kpis || {};
    const stage = d.roster || [];
    const kpi = (label, value, sub, cls) => h('div', { class: 'rs-kpi' }, h('span', { class: 'lbl', text: label }),
      h('b', { class: cls || '', text: value }), sub ? h('span', { class: 'sub', text: sub }) : null);
    const top = h('div', { class: 'rs-kpis' },
      kpi('on stage', (k.on_stage ?? 0) + ' / ' + (k.bots_total ?? 0), (k.in_profit ?? 0) + ' bots in profit'),
      kpi('stage P&L', this.delta(k.roster_net, 'USD'), 'after fees, spread, funding', signClass(k.roster_net)),
      kpi('fees paid', '$' + (k.roster_fees ?? 0).toFixed(2), 'by the bots on stage'),
      kpi('funding', this.delta(k.roster_funding, 'USD', 3), 'paid (−) / received (+)', signClass(k.roster_funding)),
      kpi('trades · 24h', String(k.roster_trades_24h ?? 0), (k.positions_open ?? 0) + ' positions open'),
      kpi('retired', String(k.retired ?? 0), 'still paper trading, off screen'),
      kpi('every bot', this.delta(k.all_net, 'USD'), (k.all_trades ?? 0).toLocaleString() + ' trades · all programs', signClass(k.all_net)));
    const cards = h('div', { class: 'rs-cards' }, stage.length ? stage.map((x) => this.card(x)) :
      h('div', { class: 'card', text: 'No bot is in profit yet with enough trades. The stage fills itself as soon as one is.' }));
    const ready = d.ready || [];
    const readySection = ready.length ? [h('div', { class: 'rs-section' }, h('h2', { text: 'Backtest winners · V6.6 trend follower & V6.2 momentum rider' }),
      h('span', { class: 'sub', text: 'always on screen · the only bots whose two-year backtest (Oct 2024 – Oct 2026) made money after fees · '
        + 'they trade a few times a month · open one to see it' + (window.PAPERLAB_PRIVATE ? ' and press GO LIVE' : '') })),
    h('div', { class: 'rs-cards' }, ready.map((x) => this.card(x)))] : [];
    const scan = (d.scanners || []).filter((x) => x.program === 'v11');
    const bees = (d.scanners || []).filter((x) => x.program === 'v12');
    const snaps = (d.scanners || []).filter((x) => x.program === 'v13');
    const htfs = (d.scanners || []).filter((x) => x.program === 'v14');
    const breakouts = (d.scanners || []).filter((x) => x.program === 'video');
    const scanners = scan.length ? [h('div', { class: 'rs-section' }, h('h2', { text: 'The scanners · 30 coins each' }),
      h('span', { class: 'sub', text: 'always on screen · each bot trades up to 8 coins at once · TP1 closes 25% (stop → past entry + fees) · TP2 50% (stop → TP1) · TP3 the last 25%' })),
    h('div', { class: 'rs-cards' }, scan.map((x) => this.card(x)))] : [];
    if (bees.length) scanners.push(h('div', { class: 'rs-section' }, h('h2', { text: 'Bizzy Bee · from beebots' }),
      h('span', { class: 'sub', text: 'always on screen · one day breakout a day on ETH / SOL / HYPE: long above today’s open + ½ yesterday’s range, full size 2x, stop at the open, out at the UTC close · unproven: lost in its back-test' })),
    h('div', { class: 'rs-cards' }, bees.map((x) => this.card(x))));
    if (snaps.length) scanners.push(h('div', { class: 'rs-section' }, h('h2', { text: 'Bounce · limit-order snapback' }),
      h('span', { class: 'sub', text: 'always on screen · rests post-only LIMIT orders where 29 coins stretch far from their 24 h average, so it pays the maker fee instead of the taker fee · up to 4 coins at once · unproven: its pre-registered back-test FAILED (every setting lost on the second half)' })),
    h('div', { class: 'rs-cards' }, snaps.map((x) => this.card(x))));
    if (htfs.length) scanners.push(h('div', { class: 'rs-section' }, h('h2', { text: 'HTF copies · the best strategies, higher timeframes first' }),
      h('span', { class: 'sub', text: 'always on screen · the leading strategies (V8.3 snap-back, V11.1 / V11.2 scanners, V13 Snapback) re-run with one extra rule: enter only in the direction of the 4h and daily trend · each runs next to its original as an A/B test' })),
    h('div', { class: 'rs-cards' }, htfs.map((x) => this.card(x))));
    if (breakouts.length) scanners.unshift(h('div', { class: 'rs-section' }, h('h2', { text: 'Bitcoin breakout · from your video' }),
      h('span', { class: 'sub', text: 'always shown · BTCUSDT 4h · open the bot for controls, fills and trade history' })),
      h('div', { class: 'rs-cards' }, breakouts.map((x) => this.card(x))));
    const side = h('aside', { class: 'rs-side' }, this.board(stage), this.streamCard(), this.retiredCard());
    grid.append(h('div', { class: 'rs-wrap' }, top, h('div', { class: 'rs-main' }, h('div', { class: 'rs-left' }, cards, ...readySection, scanners), side),
      h('p', { class: 'sub rs-rule', text: ((d.rule || {}).text || '') + ' Paper only: simulated fills on live market data.' })));
  },
  card(x) {
    const c = this.color(x), ccy = x.currency;
    const lead = x.program === 'video' ? x.status.toLowerCase().replaceAll('_', ' ') : x.ready ? 'backtest ' + this.pct((x.backtest || {}).return_pct / 100, 1) + ' · 2 years'
      : x.pinned ? (x.rank === 1 ? 'best scanner' : this.pct(x.behind, 1).replace('+', '') + ' behind')
      : x.rising ? 'new · ' + x.trades + (x.trades === 1 ? ' trade' : ' trades')
      : x.rank === 1 ? (x.chasing ? 'closest' : 'leading') : this.pct(x.behind, 1).replace('+', '') + ' behind';
    const pos = (x.positions || [])[0];
    let posBox;
    if ((x.positions || []).length > 1) {            // a scanner holding several coins: one line each
      const upnl = x.positions.reduce((a, p) => a + (isNum(p.upnl) ? p.upnl : 0), 0);
      posBox = h('div', { class: 'rs-pos multi' },
        h('div', { class: 'rs-pos-top' }, h('b', { text: x.positions.length + ' OPEN' }),
          h('span', { class: 'rs-pos-val ' + signClass(upnl), text: this.delta(upnl, ccy, 3) + ' unrealised' })),
        x.positions.slice(0, 5).map((p) => h('div', { class: 'rs-pos-row' },
          h('b', { class: p.side === 'long' ? 'up' : 'down', text: p.side === 'long' ? '▲' : '▼' }),
          h('b', { text: String(p.symbol || '').replace(/USDT$/, '') }),
          h('span', { class: 'sub', text: aAge(Date.now() - (p.entry_ts || Date.now())) }),
          h('span', { class: signClass(p.upnl), text: this.delta(p.upnl, ccy, 3) }))),
        x.positions.length > 5 ? h('div', { class: 'rs-pos-lv', text: '+' + (x.positions.length - 5) + ' more' }) : null);
    } else if (pos) {
      const coin = String(pos.symbol || x.where).replace(/USDT$/, '');
      const more = x.positions.length > 1 ? ' · +' + (x.positions.length - 1) + ' more' : '';
      posBox = h('div', { class: 'rs-pos ' + pos.side },
        h('div', { class: 'rs-pos-top' }, h('b', { class: pos.side === 'long' ? 'up' : 'down', text: (pos.side === 'long' ? '▲ LONG ' : '▼ SHORT ') }),
          h('b', { text: coin }), h('span', { class: 'rs-pos-val', text: isNum(pos.qty) && isNum(pos.mark) ? this.money(pos.qty * pos.mark, ccy) + ' ' + (ccy === 'USD' ? '' : ccy) : '' })),
        h('div', { class: 'rs-pos-pnl ' + signClass(pos.upnl), text: this.delta(pos.upnl, ccy, 3) + ' unrealised · ' + aAge(Date.now() - (pos.entry_ts || Date.now())) + ' held' + more }),
        h('div', { class: 'rs-pos-lv', text: 'entry ' + aPrice(pos.entry) + ' → mark ' + aPrice(pos.mark) + ' · stop ' + aPrice(pos.stop) + ' · target ' + aPrice(pos.target) }));
    } else {
      posBox = h('div', { class: 'rs-pos flat' }, h('div', { class: 'rs-pos-top' }, h('b', { text: 'FLAT' }), h('span', { class: 'sub', text: 'all in cash' })),
        h('div', { class: 'rs-pos-lv', text: '⏳ waiting for its setup on ' + x.where }));
    }
    const art = h('article', { class: 'rs-card' + (x.chasing ? ' chasing' : '') + (this.isJevTp(x) ? ' jevtp' : ''), style: '--acc:' + c, tabindex: 0, role: 'button',
      'aria-label': x.name + ', rank ' + x.rank, onclick: () => this.open(x), onkeydown: (e) => { if (e.key === 'Enter') this.open(x); } },
      h('header', { class: 'rs-head' },
        h('div', { class: 'rs-ava' }, rsAvatar(x.name, c)),
        h('div', { class: 'rs-id' }, h('b', { class: 'rs-name', text: x.name }), h('span', { class: 'rs-persona', text: x.persona }),
          this.jevBadge(x)),
        h('div', { class: 'rs-rank' }, h('b', { text: '#' + x.rank }), h('span', { text: lead }))),
      h('div', { class: 'rs-tags' }, h('span', { text: x.program_label }), h('span', { text: x.where }), h('span', { text: x.timeframe }),
        x.pinned && x.program === 'v11' ? h('span', { class: 'q', text: 'TP 25·50·25' }) : null,
        x.program === 'v13' ? h('span', { class: 'q', text: 'LIMIT ENTRY · MAKER' }) : null,
        x.program === 'v14' ? h('span', { class: 'q', text: 'HTF TREND FIRST' }) : null,
        x.program === 'v12' ? h('span', { class: 'q', text: 'DAY BREAKOUT · 2x' }) : null,
        x.status === 'QUALIFIED' ? h('span', { class: 'q', text: 'QUALIFIED' }) : null),
      // private dashboard only: where this bot is copied onto a real exchange account (mirror.js)
      x.program !== 'video' && window.PAPERLAB_PRIVATE && typeof LiveMirror !== 'undefined' ? LiveMirror.cardStrip(x) : null,
      h('div', { class: 'rs-eq' }, h('b', { text: this.money(x.equity, ccy) }), ccy === 'USD' ? null : h('span', { text: ccy })),
      h('div', { class: 'rs-chg ' + signClass(x.net) }, (x.net >= 0 ? '▲ ' : '▼ ') + this.delta(x.net, ccy, 3) + ' (' + this.pct(x.return) + ')'),
      posBox,
      rsCurve(x.curve, x.start_equity, c, rsHash(x.program + x.key)),
      this.call(x, c),
      h('div', { class: 'rs-job', text: x.job }),
      h('footer', { class: 'rs-foot' },
        [['trades', String(x.trades)], ['win', isNum(x.win_rate) ? Math.round(x.win_rate * 100) + '%' : DASH],
          ['PF', isNum(x.profit_factor) ? (x.profit_factor >= 999 ? '∞' : x.profit_factor.toFixed(2)) : DASH], ['fees', this.money(x.fees, ccy, 3)]]
          .map(([l, v]) => h('div', null, h('span', { text: l }), h('b', { text: v })))));
    return art;
  },
  /** Its last decision: Jev's odds for a +JEV twin, the rule's verdict otherwise. */
  call(x, c) {
    const lc = x.last_call;
    const box = h('div', { class: 'rs-call' }, h('div', { class: 'rs-call-head' }, h('span', { text: 'LAST CALL' }),
      h('span', { class: 'sub', text: lc ? this.ago((lc.signal_ts || 0) + 1) : '' })));
    if (!lc) { box.append(h('div', { class: 'sub', text: 'waiting for its first setup…' })); return box; }
    const coin = String(lc.symbol || x.where).replace(/USDT$/, '');
    const took = lc.final_level && lc.final_level !== 'SKIP';
    if (isNum(lc.p_take) || isNum(lc.p_skip)) {
      for (const [lbl, v] of [['TAKE', lc.p_take], ['SKIP', lc.p_skip], ['ATTACK', lc.p_attack]]) {
        if (!isNum(v)) continue;
        box.append(h('div', { class: 'rs-bar' }, h('span', { text: lbl }), h('i', null, h('em', { style: `width:${Math.round(v * 100)}%;background:${c}` })),
          h('b', { text: Math.round(v * 100) + '%' })));
      }
    }
    const why = String(lc.reason || '').replace(/_/g, ' ').toLowerCase();
    box.append(h('div', { class: 'rs-call-v' }, h('b', { class: took ? 'up' : 'muted', text: took ? 'TOOK ' + String(lc.side || '').toUpperCase() : 'PASSED' }),
      ' ' + coin + (why && why !== 'control' ? ' · ' + why : '')));
    return box;
  },
  board(stage) {
    const max = Math.max(0.0001, ...stage.map((x) => Math.abs(x.return)));
    return h('div', { class: 'card rs-board' }, h('div', { class: 'card-head' }, h('h2', { text: 'Leaderboard' }), h('span', { class: 'sub', text: 'return' })),
      stage.map((x) => h('div', { class: 'rs-lb', onclick: () => this.open(x) },
        h('span', { class: 'rs-lb-n', text: String(x.rank) }), rsAvatar(x.name, this.color(x), 24), h('b', { text: x.name }),
        h('i', null, h('em', { style: `width:${Math.max(4, Math.abs(x.return) / max * 100).toFixed(0)}%;background:${this.color(x)}` })),
        h('span', { class: 'num ' + signClass(x.return), text: this.pct(x.return, 1) }))));
  },
  /** One row per TRADE IDEA: the coin and direction, then one line per bot that saw it -- took it (entry, and the
      result once closed) or skipped it (why). A scanner and its Jev twin share their ideas, so both appear here. */
  streamCard() {
    const ideas = (this.data || {}).ideas;
    if (!ideas) return this.eventsCard();
    const color = new Map([...((this.data || {}).roster || []), ...((this.data || {}).scanners || [])].map((x) => [x.program + '|' + x.key, this.color(x)]));
    const botLine = (i, b) => {
      const ccy = i.program === 'v9' ? 'USD' : 'USDT';
      let what;
      if (isNum(b.net)) what = [h('b', { class: signClass(b.net), text: (b.net >= 0 ? 'won ' : 'lost ') + this.delta(b.net, ccy, 3) }),
        isNum(b.r_net) ? h('span', { class: 'sub', text: ' (' + (b.r_net >= 0 ? '+' : '') + b.r_net.toFixed(2) + 'R, ' + String(b.exit_kind || '').replace(/_/g, ' ') + ')' }) : null];
      else if (isNum(b.open_price)) what = [h('b', { class: 'accent', text: 'in the trade' }), h('span', { class: 'sub', text: ' since ' + aClock(b.open_ts) + ' @ ' + aPrice(b.open_price) })];
      else if (b.verdict && b.verdict !== 'SKIP') what = [h('b', { class: 'up', text: 'took it' }), h('span', { class: 'sub', text: ' · order sent' })];
      else {
        const why = String(b.reason || '').replace(/^Jev: /, '').replace(/_/g, ' ').toLowerCase();
        what = [h('b', { class: 'muted', text: 'skipped' }), h('span', { class: 'sub', text: ' · ' + (isNum(b.p_support) ? 'Jev ' + Math.round(b.p_support * 100) + '% sure' : why || 'no trade') })];
      }
      const jev = b.verdict && b.verdict !== 'SKIP' && isNum(b.p_support) && !isNum(b.net) ? h('span', { class: 'sub', text: ' · Jev ' + Math.round(b.p_support * 100) + '%' }) : null;
      return h('div', { class: 'rs-idea-bot' }, h('span', { class: 'rs-dot', style: 'background:' + (color.get(i.program + '|' + b.key) || '#888') }),
        h('b', { class: 'rs-who' }, b.name || b.key, this.jevBadge({ program: i.program, key: b.key }, true)), h('span', { class: 'rs-what' }, what, jev));
    };
    return h('div', { class: 'card rs-stream' }, h('div', { class: 'card-head' }, h('h2', { text: 'Trade ideas' }),
      h('span', { class: 'sub', text: 'one row per idea · every bot that saw it' })),
      ideas.length ? h('ul', null, ideas.map((i) => h('li', { class: 'rs-idea' },
        h('div', { class: 'rs-idea-head' }, h('b', { class: i.side === 'long' ? 'up' : 'down', text: (i.side === 'long' ? '▲ LONG ' : '▼ SHORT ') }),
          h('b', { text: String(i.symbol || '').replace(/USDT$/, '') }), h('span', { class: 'sub', text: aClock(i.ts) })),
        i.bots.map((b) => botLine(i, b)))))
        : h('p', { class: 'sub', text: 'no trade ideas yet' }));
  },
  eventsCard() {
    const items = ((this.data || {}).stream || []).slice(0, 18);
    const color = new Map([...((this.data || {}).roster || []), ...((this.data || {}).scanners || [])].map((x) => [x.program + '|' + x.key, this.color(x)]));
    const line = (e) => {
      const coin = String(e.symbol || '').replace(/USDT$/, '');
      if (e.kind === 'open') return [h('b', { class: e.side === 'long' ? 'up' : 'down', text: 'OPEN ' + String(e.side || '').toUpperCase() }), ' ' + coin + ' @ ' + aPrice(e.price)];
      if (e.kind === 'closed') return [h('b', { text: 'CLOSE ' }), coin + ' ', h('b', { class: signClass(e.net), text: this.delta(e.net, e.program === 'v9' ? 'USD' : 'USDT', 3) }),
        isNum(e.r_net) ? h('span', { class: 'sub', text: ' ' + (e.r_net >= 0 ? '+' : '') + e.r_net.toFixed(2) + 'R' }) : null];
      const took = e.final_level && e.final_level !== 'SKIP';
      return [h('b', { class: took ? 'up' : 'muted', text: took ? 'TAKE' : 'PASS' }), ' ' + coin + (e.reason && e.reason !== 'CONTROL' ? ' · ' + String(e.reason).replace(/_/g, ' ').toLowerCase() : '')];
    };
    return h('div', { class: 'card rs-stream' }, h('div', { class: 'card-head' }, h('h2', { text: 'Decision stream' }), h('span', { class: 'sub', text: 'bots on stage' })),
      items.length ? h('ul', null, items.map((e) => h('li', null, h('span', { class: 'rs-dot', style: 'background:' + (color.get(e.program + '|' + e.bot_key) || '#888') }),
        h('b', { class: 'rs-who', text: e.name }), h('span', { class: 'rs-what' }, line(e)), h('span', { class: 'sub rs-when', text: aClock(e.ts) }))))
        : h('p', { class: 'sub', text: 'no decisions yet' }));
  },
  retiredCard() {
    const r = (this.data || {}).retired || [];
    return h('div', { class: 'card rs-retired' }, h('div', { class: 'card-head' }, h('h2', { text: 'Retired · ' + r.length }),
      h('button', { type: 'button', class: 'chip', text: 'see them →', onclick: () => { this.showRetired = true; ArenaNav.go('bots'); } })),
    h('p', { class: 'sub', text: 'Not close to winning, so off the screen. They keep paper trading in the background and return to the stage if they climb into profit.' }));
  },

  /* ---- the Bots view: the stage as a table, then the retired list (collapsed) ---- */
  botsCard() {
    const d = this.data || {};
    const t = h('table', { class: 'tbl lb' });
    renderTable(t, [
      { h: '#', cell: (x) => String(x.rank), num: true, cls: 'rank' },
      { h: 'bot', cell: (x) => h('span', { class: 'rs-cell' }, rsAvatar(x.name, this.color(x), 22), h('b', { text: x.name }), this.jevBadge(x, true), h('span', { class: 'sub', text: ' ' + x.persona })) },
      { h: 'program', cell: (x) => x.program_label },
      { h: 'trades on', cell: (x) => x.where + ' · ' + x.timeframe },
      { h: 'equity', cell: (x) => this.money(x.equity, x.currency) + (x.currency === 'USD' ? '' : ' ' + x.currency), num: true },
      { h: 'return', cell: (x) => h('b', { class: signClass(x.return), text: this.pct(x.return) }), num: true },
      { h: 'trades', cell: (x) => String(x.trades), num: true },
      { h: 'win', cell: (x) => (isNum(x.win_rate) ? Math.round(x.win_rate * 100) + '%' : DASH), num: true },
      { h: 'PF', cell: (x) => (isNum(x.profit_factor) ? (x.profit_factor >= 999 ? '∞' : x.profit_factor.toFixed(2)) : DASH), num: true },
      { h: 'id', cell: (x) => h('span', { class: 'sub mono', text: x.key }) },
    ], d.roster || [], { onRow: (x) => this.open(x), empty: 'nobody on stage yet' });
    const st = h('table', { class: 'tbl lb' });
    renderTable(st, [
      { h: '#', cell: (x) => String(x.rank), num: true, cls: 'rank' },
      { h: 'bot', cell: (x) => h('span', { class: 'rs-cell' }, rsAvatar(x.name, this.color(x), 22), h('b', { text: x.name }), this.jevBadge(x, true), h('span', { class: 'sub', text: ' ' + x.persona })) },
      { h: 'decides', cell: (x) => x.timeframe },
      { h: 'status', cell: (x) => pill(x.status, STATUS_KIND[x.status] || 'muted') },
      { h: 'equity', cell: (x) => this.money(x.equity, x.currency) + ' ' + x.currency, num: true },
      { h: 'return', cell: (x) => h('b', { class: signClass(x.return), text: this.pct(x.return) }), num: true },
      { h: 'trades', cell: (x) => String(x.trades), num: true },
      { h: 'open', cell: (x) => String((x.positions || []).length), num: true },
      { h: 'win', cell: (x) => (isNum(x.win_rate) ? Math.round(x.win_rate * 100) + '%' : DASH), num: true },
      { h: 'id', cell: (x) => h('span', { class: 'sub mono', text: x.key }) },
    ], d.scanners || [], { onRow: (x) => this.open(x), empty: 'no scanners' });
    const ready = d.ready || [];
    const rd = h('table', { class: 'tbl lb' });
    renderTable(rd, [
      { h: '#', cell: (x) => String(x.rank), num: true, cls: 'rank' },
      { h: 'bot', cell: (x) => h('span', { class: 'rs-cell' }, rsAvatar(x.name, this.color(x), 22), h('b', { text: x.name }), h('span', { class: 'sub', text: ' ' + x.persona })) },
      { h: 'trades on', cell: (x) => x.where + ' · ' + x.timeframe },
      { h: '2-year backtest', cell: (x) => h('b', { class: 'up', text: this.pct(((x.backtest || {}).return_pct || 0) / 100, 1) }), num: true },
      { h: 'live paper', cell: (x) => h('span', { class: signClass(x.return), text: this.pct(x.return) + ' · ' + x.trades + (x.trades === 1 ? ' trade' : ' trades') }), num: true },
      { h: '', cell: () => (window.PAPERLAB_PRIVATE ? h('span', { class: 'pill down', text: 'GO LIVE inside' }) : '') },
      { h: 'id', cell: (x) => h('span', { class: 'sub mono', text: x.key }) },
    ], ready, { onRow: (x) => this.open(x), empty: 'none' });
    const ret = d.retired || [];
    const rt = h('table', { class: 'tbl' });
    renderTable(rt, [
      { h: 'bot', cell: (x) => h('span', null, h('b', { text: x.name }), h('span', { class: 'sub', text: ' ' + x.persona })) },
      { h: 'program', cell: (x) => x.program_label }, { h: 'on', cell: (x) => x.where },
      { h: 'status', cell: (x) => pill(x.status, STATUS_KIND[x.status] || 'muted') },
      { h: 'return', cell: (x) => h('span', { class: signClass(x.return), text: this.pct(x.return) }), num: true },
      { h: 'trades', cell: (x) => String(x.trades), num: true },
      { h: 'id', cell: (x) => h('span', { class: 'sub mono', text: x.key }) },
    ], ret, { onRow: (x) => this.open(x), empty: 'nobody retired' });
    const det = h('details', { class: 'rs-ret', open: this.showRetired || null, ontoggle: (e) => { this.showRetired = e.target.open; } },
      h('summary', null, h('b', { text: 'Retired · ' + ret.length }), h('span', { class: 'sub', text: ' still paper trading in the background · not close to winning' })),
      h('div', { class: 'tablewrap' }, rt));
    return h('div', { class: 'card', id: 'arena-bots' },
      ready.length ? [h('div', { class: 'card-head' }, h('h2', { text: 'Backtest winners · ' + ready.length + ' · can go live' }),
        h('span', { class: 'sub', text: 'V6.6 trend follower and V6.2 momentum rider bots whose two-year backtest made money · click one to open it'
          + (window.PAPERLAB_PRIVATE ? ' and press GO LIVE' : '') })), h('div', { class: 'tablewrap' }, rd)] : null,
      h('div', { class: 'card-head' }, h('h2', { text: 'On stage · ' + (d.roster || []).length }), h('span', { class: 'sub', text: (d.rule || {}).text || '' })),
      h('div', { class: 'tablewrap' }, t),
      (d.scanners || []).length ? [h('h3', { text: 'Always shown bots · ' + d.scanners.length }), h('div', { class: 'tablewrap' }, st)] : null,
      det);
  },
};
