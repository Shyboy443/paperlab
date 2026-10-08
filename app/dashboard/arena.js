/* PaperLab - ARENA: the lean dashboard (public page and private dashboard).

   One screen per forward program, switched at the top: V11 SCAN (bots that scan 30 coins), V8 SCALP (aggressive 5m
   scalpers), V9 (US stocks), V7 (15m) and V6 (1h / 4h).
   Every number comes from the read-only public API (GET /api/public/competition/{program}, /{program}/candles) and
   the public push channel ('{program}_state' books, '{program}' events, 'prices' quotes). All results are PAPER.

     Dashboard   KPI strip, candlestick chart (coin / timeframe, paper fills marked), capital growth of every bot,
                 open positions, activity
     Bots        every bot: status (ACTIVE / QUALIFIED / ELIMINATED), capital-growth sparkline, trades, costs;
                 a bot opens with its own candles, growth curve and trade list
     Markets     live Bybit prices and a large candlestick chart

   Charts use lightweight-charts (candles, growth lines) and inline SVG (sparklines). Nothing here can mutate. */
'use strict';

// quote: the symbol suffix (crypto perps are COINUSDT, stocks are the bare ticker); ccy / book: how money is shown
const ARENA_PROGRAMS = {
  video: { label: 'Video breakout', sub: 'Bitcoin · 7-day high / 3-day low', tf: '4h', step: 14_400_000, quote: 'USDT', ccy: 'USDT', book: 'configured USDT' },
  v14: { label: 'V14 HTF', sub: 'best strategies · higher-timeframe trend first', tf: '5m', step: 300_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT', scan: true },
  v13: { label: 'V13 Snapback', sub: '29 coins · limit-order reversion', tf: '5m', step: 300_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT', scan: true },
  v12: { label: 'V12 Bizzy', sub: 'beebots · one day breakout a day', tf: '15m', step: 60_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT', scan: true },
  v11: { label: 'V11 Scan', sub: '30 coins · best setups', tf: '15m', step: 300_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT', scan: true },
  v8: { label: 'V8 Scalp', sub: 'V8.3 5m VWAP snap-back · 3 h max hold', tf: '5m', step: 300_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT' },
  v9: { label: 'V9 Stocks', sub: 'US stocks · 5m scalps · market hours', tf: '5m', step: 300_000, quote: '', ccy: 'USD', book: '1,000 USD', stocks: true },
  v7: { label: 'V7', sub: '15m trend · 6 h max hold', tf: '15m', step: 900_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT' },
  v6: { label: 'V6', sub: '1h / 4h swing', tf: '1h', step: 3_600_000, quote: 'USDT', ccy: 'USDT', book: '20 USDT' },
};
// Switched off on 2026-10-08 after the two-year backtest; only V6.2 / V6.6 stay in V6 (app/core/programs.py).
const ARENA_RETIRED = ['v7', 'v8', 'v9', 'v11', 'v12', 'v13', 'v14'];
const ARENA_RETIRED_V6 = ['V6.1', 'V6.3', 'V6.4', 'V6.5'];
for (const p of ARENA_RETIRED) delete ARENA_PROGRAMS[p];
const arenaBotRetired = (prog, key) => ARENA_RETIRED.includes(prog)
  || (prog === 'v6' && ARENA_RETIRED_V6.includes(String(key || '').split('-')[0]));
const progOf = (p) => ARENA_PROGRAMS[p] || ARENA_PROGRAMS.v6;
const STATUS_KIND = { ACTIVE: 'muted', QUALIFIED: 'up', ELIMINATED: 'down' };
// what each V11 scanner looks for (app/strategies/v11/scan.py)
const SCAN_BLURB = {
  'V11.1': 'Relative-strength breakout · the strongest (weakest) coins breaking their 4h high (low) on volume · 15m, 2R, 4 h max',
  'V11.2': 'Relative-strength pullback · a leader (laggard) dipping to its 15m EMA20 inside the trend · 15m, 2R, 4 h max',
  'V11.3': 'Capitulation snap-back · a 4+ ATR hourly flush on climax volume, then a reversal bar · 5m, 2R, 2 h max',
  'V11.4': '1-hour reversal ranker · every hour, fade the 2 biggest 4h gainers and buy the 2 biggest losers · held 58 min',
  'V14.1': 'V8.3 VWAP snap-back + HTF · enters only with the 4h and daily trend (from 1h candles) · everything else is V8.3',
  'V14.2': 'V11.1 breakout scanner + HTF · only setups in the direction of the 4h and daily trend · 25/50/25 ladder',
  'V14.3': 'V11.2 pullback scanner + HTF · only setups in the direction of the 4h and daily trend · 25/50/25 ladder',
  'V14.4': 'V13 Snapback + HTF · limit orders only in the direction of the 4h and daily trend · maker entries',
  'V13.1': 'Snapback · when a coin stretches far from its 24 h VWAP, a post-only LIMIT rests a little further out (maker fee) · stop ≥ 1%, target 1R, cancelled if unfilled in 15 min',
  'V12.1': 'Bizzy Bee (beebots) · long when ETH / SOL / HYPE trade above today’s open + ½ yesterday’s range · full size 2x, stop at the open, out at the UTC close · 1 trade a day',
};
const aMoney = (v, d = 2) => (isNum(v) ? (v > 0 ? '+' : v < 0 ? '-' : '') + Math.abs(v).toFixed(d) : DASH);
const aPct = (v, d = 1) => (isNum(v) ? (v * 100).toFixed(d) + '%' : DASH);
const aPrice = (v) => (isNum(v) ? (Math.abs(v) >= 100 ? v.toFixed(2) : Math.abs(v) >= 1 ? v.toFixed(4) : v.toPrecision(4)) : DASH);
const aClock = (ms) => (ms ? new Date(ms).toISOString().slice(11, 16) : DASH);
const aSide = (s) => pill(String(s || '?').toUpperCase(), s === 'long' ? 'up' : 'down');
function aAge(ms) {
  if (!isNum(ms) || ms <= 0) return '0m';
  const m = Math.floor(ms / 60_000), d = Math.floor(m / 1440), hh = Math.floor((m % 1440) / 60), mm = m % 60;
  return d ? d + 'd ' + hh + 'h' : hh ? hh + 'h ' + String(mm).padStart(2, '0') + 'm' : mm + 'm';
}
function sparkline(curve, w = 90, hgt = 24) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${hgt}`); svg.setAttribute('class', 'spark');
  if (!curve || curve.length < 2) return svg;
  const xs = curve.map((p) => p[0]), ys = curve.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys, curve[0][1]), y1 = Math.max(...ys, curve[0][1]);
  const X = (t) => (x1 > x0 ? ((t - x0) / (x1 - x0)) * w : w), Y = (v) => (y1 > y0 ? hgt - ((v - y0) / (y1 - y0)) * (hgt - 2) - 1 : hgt / 2);
  const base = document.createElementNS(NS, 'line');
  Object.entries({ x1: 0, x2: w, y1: Y(curve[0][1]), y2: Y(curve[0][1]), class: 'spark-base' }).forEach(([k, v]) => base.setAttribute(k, v));
  const path = document.createElementNS(NS, 'path');
  path.setAttribute('d', curve.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' '));
  path.setAttribute('class', 'spark-line ' + (ys[ys.length - 1] >= curve[0][1] ? 'up' : 'down'));
  svg.append(base, path);
  return svg;
}
const chartLib = () => (typeof LightweightCharts !== 'undefined' ? LightweightCharts : null);
function chartTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (n, f) => (css.getPropertyValue(n) || f).trim();
  return { bg: v('--panel', '#0c1210'), text: v('--muted', '#7a9087'), grid: 'rgba(122,144,135,.10)', up: v('--up', '#1ff29a'),
    down: v('--down', '#ff4d61'), accent: v('--accent', '#38d6ff') };
}
function withAlpha(color, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(color).trim());
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

/* Position boxes (a lightweight-charts series primitive), drawn like a trading terminal's long / short tool: a green
   zone from the entry to the target, a red zone from the entry to the stop, the entry line, and for a closed position
   a dashed path to its exit. Open positions run to their 45-minute time stop. Kept quiet on purpose: no labels and no
   price lines until a box is hovered -- then that box is highlighted with its target, stop and result, and the others
   dim. (A single bot's page labels its open position without hovering.) A position with a take-profit ladder draws
   every rung (TP1 / TP2 / TP3) as a line inside its green zone, tagged outside the box when highlighted. A chosen
   closed trade (a click in the trades table) stays highlighted: `pinned`. */
class PositionBoxes {
  constructor(bars, boxes, step, th, labelOpen = false) {
    this.times = bars.map((b) => b.time); this.boxes = boxes; this.step = step; this.th = th; this.labelOpen = labelOpen;
    this.rects = []; this.hover = null; this.pinned = null;
  }
  /** every take-profit level of a box, nearest first (a single target is a one-rung ladder) */
  static tps(b) { return (b.tps && b.tps.length ? b.tps : [b.target]).filter(isNum); }
  static far(b) { const t = PositionBoxes.tps(b); return t.length ? t[t.length - 1] : null; }
  attached({ chart, series, requestUpdate }) {
    this.chart = chart; this.series = series; this.requestUpdate = requestUpdate;
    this.onMove = (p) => {
      const pt = p && p.point;
      // overlapping boxes: the one whose entry line is nearest the cursor
      const hits = pt ? this.rects.filter((r) => pt.x >= r.x0 && pt.x <= r.x1 && pt.y >= r.top && pt.y <= r.bottom) : [];
      hits.sort((a, c) => Math.abs(pt.y - a.yE) - Math.abs(pt.y - c.yE));
      const b = hits.length ? hits[0].box : this.pinned;
      if (b !== this.hover) { this.hover = b; this.requestUpdate(); }
    };
    chart.subscribeCrosshairMove(this.onMove);
  }
  detached() { if (this.chart) this.chart.unsubscribeCrosshairMove(this.onMove); }
  updateAllViews() {}
  /** logical bar index of a time in ms (fractional past the last bar, so open boxes can extend into the future) */
  index(ms) {
    const t = Math.floor(ms / 1000 / this.step) * this.step, n = this.times.length;
    if (!n) return null;
    if (t > this.times[n - 1]) return n - 1 + (t - this.times[n - 1]) / this.step;
    let lo = 0, hi = n - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (this.times[mid] <= t) lo = mid; else hi = mid - 1; }
    return this.times[lo] <= t ? lo : null;
  }
  autoscaleInfo(from, to) {
    let lo = Infinity, hi = -Infinity;
    for (const b of this.boxes) {
      const i0 = this.index(b.entry_ts), i1 = b.open ? this.times.length - 1 : this.index(b.exit_ts);
      if (i0 === null || i1 === null || i1 < from || i0 > to) continue;
      for (const v of [b.entry, b.stop, ...PositionBoxes.tps(b)]) if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    return isFinite(lo) ? { priceRange: { minValue: lo, maxValue: hi } } : null;
  }
  paneViews() {
    const self = this;
    return [
      { zOrder: () => 'bottom', renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace(({ context }) => self.drawBoxes(context)) }) },
      { zOrder: () => 'top', renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace(({ context, mediaSize }) => self.drawLabels(context, mediaSize)) }) },
    ];
  }
  geometry(b) {
    const ts = this.chart.timeScale(), s = this.series;
    const sp = Math.max(1, (ts.logicalToCoordinate(1) ?? 0) - (ts.logicalToCoordinate(0) ?? 0));
    const i0 = this.index(b.entry_ts);
    if (i0 === null) return null;
    let i1 = b.open ? (b.time_stop_ts ? this.index(b.time_stop_ts) : null) : this.index(b.exit_ts);
    if (i1 === null || i1 < i0 + 2) i1 = i0 + (b.open ? 3 : 2);       // a one-bar trade still gets a visible box
    const x0 = ts.logicalToCoordinate(i0) - sp / 2, x1 = ts.logicalToCoordinate(i1) + sp / 2;
    const far = PositionBoxes.far(b);
    const yE = s.priceToCoordinate(b.entry), yT = isNum(far) ? s.priceToCoordinate(far) : null;
    const yS = isNum(b.stop) ? s.priceToCoordinate(b.stop) : null;
    if (x0 === null || x1 === null || yE === null) return null;
    const tps = PositionBoxes.tps(b);
    const yTps = tps.length > 1 ? tps.map((v) => s.priceToCoordinate(v)).filter((y) => y !== null) : [];
    const ys = [yE, yT, yS].filter((y) => y !== null);
    const xExit = !b.open && isNum(b.exit) ? ts.logicalToCoordinate(this.index(b.exit_ts) ?? i1) : null;
    return { box: b, x0, x1, yE, yT, yS, yTps, top: Math.min(...ys), bottom: Math.max(...ys), xExit,
      yExit: xExit !== null ? s.priceToCoordinate(b.exit) : null, xEntry: ts.logicalToCoordinate(i0),
      xNow: ts.logicalToCoordinate(this.times.length - 1) + sp / 2 };
  }
  drawBoxes(ctx) {
    const th = this.th;
    this.rects = this.boxes.map((b) => this.geometry(b)).filter(Boolean);
    const focus = this.hover;
    const order = focus ? [...this.rects.filter((r) => r.box !== focus), ...this.rects.filter((r) => r.box === focus)] : this.rects;
    for (const r of order) {
      const b = r.box, on = b === focus, dim = !!focus && !on, w = r.x1 - r.x0;
      const fill = on ? 0.22 : dim ? 0.03 : b.open ? 0.1 : 0.05, edge = on ? 0.8 : dim ? 0.1 : b.open ? 0.35 : 0.18;
      for (const [y, col] of [[r.yT, th.up], [r.yS, th.down]]) {
        if (y === null) continue;
        ctx.fillStyle = withAlpha(col, fill);
        ctx.fillRect(r.x0, Math.min(r.yE, y), w, Math.abs(y - r.yE));
        ctx.strokeStyle = withAlpha(col, edge); ctx.lineWidth = 1;
        ctx.strokeRect(Math.round(r.x0) + 0.5, Math.round(Math.min(r.yE, y)) + 0.5, Math.round(w), Math.round(Math.abs(y - r.yE)));
      }
      if (r.yTps.length) {                     // the ladder: one dashed line per take-profit inside the green zone
        ctx.strokeStyle = withAlpha(th.up, on ? 0.95 : dim ? 0.12 : 0.5); ctx.lineWidth = 1; ctx.setLineDash([5, 3]);
        for (const y of r.yTps) { ctx.beginPath(); ctx.moveTo(r.x0, Math.round(y) + 0.5); ctx.lineTo(r.x1, Math.round(y) + 0.5); ctx.stroke(); }
        ctx.setLineDash([]);
      }
      ctx.strokeStyle = withAlpha('#c8d6d0', on ? 0.9 : dim ? 0.12 : 0.4); ctx.lineWidth = 1; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(r.x0, Math.round(r.yE) + 0.5); ctx.lineTo(r.x1, Math.round(r.yE) + 0.5); ctx.stroke();
      if (r.xExit !== null && r.yExit !== null && !dim) {
        const win = (b.net || []).reduce((a, v) => a + (v || 0), 0) >= 0;
        ctx.strokeStyle = withAlpha(win ? th.up : th.down, on ? 0.95 : 0.45); ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(r.xEntry, r.yE); ctx.lineTo(r.xExit, r.yExit); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = win ? th.up : th.down;
        ctx.beginPath(); ctx.arc(r.xExit, r.yExit, 3, 0, Math.PI * 2); ctx.fill();
      }
    }
  }
  pill(ctx, size, text, cx, y, bg, fg, above) {
    ctx.font = '600 11px "JetBrains Mono", monospace';
    const w = ctx.measureText(text).width + 12, hgt = 18;
    const x = Math.max(2, Math.min(size.width - w - 2, cx - w / 2));
    let top = above ? y - hgt - 3 : y + 3;
    // step away from labels already placed (several open positions sit close together)
    for (let i = 0; i < 8 && this.placed.some((p) => x < p.x + p.w && x + w > p.x && top < p.top + hgt && top + hgt > p.top); i++) {
      top += above ? -(hgt + 2) : hgt + 2;
    }
    this.placed.push({ x, w, top });
    ctx.fillStyle = bg;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, top, w, hgt, 4); else ctx.rect(x, top, w, hgt);
    ctx.fill();
    ctx.fillStyle = fg; ctx.textBaseline = 'middle'; ctx.fillText(text, x + 6, top + hgt / 2 + 0.5);
  }
  drawLabels(ctx, size) {
    const th = this.th;
    this.placed = [];
    for (const r of this.rects) {
      const b = r.box;
      if (b !== this.hover && !(this.labelOpen && b.open && !this.hover)) continue;
      const cx = (r.x0 + r.x1) / 2, pct = (v) => ((v / b.entry - 1) * 100).toFixed(2) + '%';
      const risk = isNum(b.stop) ? Math.abs(b.entry - b.stop) : null;
      const tps = PositionBoxes.tps(b), far = PositionBoxes.far(b);
      const rr = risk && isNum(far) ? (Math.abs(far - b.entry) / risk).toFixed(2) : DASH;
      const nets = [...new Set((b.net || []).filter(isNum).map((v) => v.toFixed(3)))].map(Number);   // a pair's equal results once
      const result = nets.map((v) => aMoney(v, 3)).join(' / ');
      // nothing is drawn INSIDE the box: the details ride on the target tag, outside it
      const extra = b.open ? ` · R:R ${rr}` : ` · closed ${result}${isNum(b.r) ? ` (${aMoney(b.r, 2)}R)` : ''} · R:R ${rr}`;
      const sh3 = b.shares && b.shares.length === 3 ? b.shares : [0.25, 0.5, 0.25];     // how much each TP closes
      const shares = tps.length === 3 ? sh3.map((f) => 'close ' + Math.round(f * 100) + '%') : tps.map(() => '');
      const tLabel = tps.length > 1 ? 'TP' + tps.length + (tps.length === 3 ? ` (${shares[2]})` : '') : 'Target';
      if (r.yT !== null) this.pill(ctx, size, `${tLabel} ${aPrice(far)} (${pct(far)})${extra}`, cx, r.yT, th.up, '#04130c', r.yT < r.yE);
      if (tps.length > 1) {                     // the lower rungs: small tags just right of the box, never inside it
        tps.slice(0, -1).forEach((v, i) => { const y = r.yTps[i]; if (isNum(y)) this.tag(ctx, size, `TP${i + 1} ${aPrice(v)} · ${shares[i]}`, r.x1 + 4, y, th.up); });
      }
      if (r.yS !== null) this.pill(ctx, size, `Stop ${aPrice(b.stop)} (${pct(b.stop)})`, cx, r.yS, th.down, '#1a0508', r.yS < r.yE);
      if (b.open && nets.length) {        // live result: a small tag just right of the newest candle, where no candle is yet
        const v = nets[0];
        this.tag(ctx, size, `${b.side === 'long' ? 'LONG' : 'SHORT'} ${aMoney(v, 3)}`, r.xNow + 6, r.yE, v >= 0 ? th.up : th.down);
      }
    }
  }
  tag(ctx, size, text, x, y, color) {
    ctx.font = '600 10px "JetBrains Mono", monospace';
    const w = ctx.measureText(text).width + 10, hgt = 16;
    const left = Math.max(2, Math.min(size.width - w - 2, x)), top = y - hgt / 2;
    ctx.fillStyle = 'rgba(8,12,11,.85)'; ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(left, top, w, hgt, 3); else ctx.rect(left, top, w, hgt);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.fillText(text, left + 5, top + hgt / 2 + 0.5);
  }
}

const Arena = {
  base: '/api/public/competition',
  prog: 'v6', data: { video: null, v6: null, v7: null, v8: null, v9: null, v11: null, v12: null, v13: null, v14: null }, feed: { video: [], v6: [], v7: [], v8: [], v9: [], v11: [], v12: [], v13: [], v14: [] },
  seen: new Set(), coin: '', tf: '', view: 'dashboard', filter: 'all', botKey: '', bot: null, chartBot: {}, botCoin: {}, focus: null,
  charts: [], timer: null, clock: null, reloadTimer: null, drawTimer: null, loading: false,

  grid() { return $('#home-grid'); },
  visible() { const g = this.grid(); return !!g && !g.closest('[hidden]'); },
  init() {
    try { const p = localStorage.getItem('arena.prog'); if (ARENA_PROGRAMS[p]) this.prog = p; } catch (e) { /* storage blocked */ }
    const q = new URLSearchParams(location.search).get('p');
    if (ARENA_PROGRAMS[q]) this.prog = q;
    if (q === 'video' && new URLSearchParams(location.search).get('bot') === 'BTC-4H-BREAKOUT') this.botKey = 'BTC-4H-BREAKOUT';
  },
  setProgram(p) {
    this.prog = p; this.coin = ''; this.tf = ''; this.botKey = ''; this.bot = null;
    try { localStorage.setItem('arena.prog', p); } catch (e) { /* ignore */ }
    this.load();
  },
  show(view) { this.view = view; this.load(); },

  async load() {
    if (!this.grid()) return;
    if (this.loading) { this.again = true; return; }        // a view switch mid-load: load again right after
    this.loading = true;
    try {
      // the home screen is the roster (roster.js); a bot's page and the markets need their program's payload
      const roster = typeof Roster !== 'undefined' && this.view !== 'markets';
      const [d] = await Promise.all([this.view !== 'dashboard' || !roster ? getJSON(this.base + '/' + this.prog) : null,
        roster ? Roster.fetch(true).catch(() => null) : null]);
      if (d) {
        this.data[this.prog] = d;
        this.addEvents(this.prog, (d.activity || []).slice().reverse(), true);
      }
      if (this.view === 'bots' && this.botKey && !this.bot) this.bot = await getJSON(`${this.base}/${this.prog}/bot/${encodeURIComponent(this.botKey)}`);
      // the same screen as last time: refresh its numbers, trades and candles IN PLACE (the chart keeps its zoom,
      // scroll and the chosen trade); a different screen is built from scratch
      if (this.view !== 'dashboard' && this.screenKey() === this.renderedKey && this.liveChartHost()) await this.refreshInPlace();
      else this.render();
    } catch (e) {
      clear(this.grid()).append(h('div', { class: 'card' }, h('p', { class: 'err', text: 'arena unavailable: ' + e.message })));
    }
    this.loading = false;
    if (this.again) { this.again = false; return this.load(); }
    clearInterval(this.timer);
    this.timer = setInterval(() => { if (!document.hidden && this.visible()) this.load(); }, 30_000);
    clearInterval(this.clock);
    this.clock = setInterval(() => this.tick(), 1000);
  },
  deactivate() { clearInterval(this.timer); clearInterval(this.clock); this.timer = this.clock = null; },
  reloadSoon(ms) {
    if (this.reloadTimer) return;
    this.reloadTimer = setTimeout(() => { this.reloadTimer = null; if (this.visible()) this.load(); }, ms);
  },

  /* ---- live pushes: '{program}_state' re-values the books in place; events feed the activity list ---- */
  onState(prog, d) {
    const x = this.data[prog];
    if (!x || !x.experiment || !d) return;
    if (d.health) x.status = Object.assign({}, x.status || {}, d.health);
    const rows = new Map((x.leaderboard || []).map((r) => [r.key, r]));
    let changed = false;
    for (const b of d.bots || []) {
      const r = rows.get(b.key);
      if (!r) { this.reloadSoon(3000); continue; }
      for (const k of ['live', 'equity', 'equity_live', 'trades', 'risk_state']) if (k in b) r[k] = b[k];
      const eq = isNum(b.equity_live) ? b.equity_live : b.equity;
      if (isNum(eq)) { r.equity_now = eq; r.net_now = eq - (r.start_equity || 20); }
      const old = r.open_positions || [];
      r.open_positions = (b.positions || []).map((p) => Object.assign({}, old.find((o) => o.side === p.side && o.entry === p.entry) || {}, p));
      if ((b.positions || []).length !== old.length) changed = true;
    }
    this.recompute(x);
    if (changed) this.reloadSoon(2000);
    if (prog === this.prog) this.drawSoon();
    if (typeof LivePrices !== 'undefined') LivePrices.schedule();
  },
  onEvent(prog, d) {
    if (d && arenaBotRetired(prog, d.bot_key)) return;            // out of the arena
    this.addEvents(prog, [d], false);
    if (['open', 'closed', 'eliminated', 'system'].includes(d.type) && prog === this.prog) this.reloadSoon(1500);
    if (prog === this.prog && this.visible()) this.renderFeed();
  },
  recompute(x) {
    const hero = x.hero || {}, rows = x.leaderboard || [];
    hero.active_bots = rows.filter((r) => r.live).length;
    hero.total_virtual_equity = rows.reduce((a, r) => a + (isNum(r.equity_now) ? r.equity_now : 0), 0);
    hero.net_pnl = hero.total_virtual_equity - (hero.start_equity_total || 0);
    x.positions = [];
    for (const r of rows) for (const p of r.open_positions || []) {
      const coin = r.coin === 'ALL' && p.symbol ? p.symbol.replace(/USDT$/, '') : r.coin;    // a scanner book: the position's coin
      x.positions.push(Object.assign({ bot_key: r.key, coin, symbol: r.symbol, role: r.role }, p,
        { hold_s: p.entry_ts ? Math.floor((Date.now() - p.entry_ts) / 1000) : null }));
    }
    hero.positions_open = x.positions.length;
  },
  drawSoon() {
    if (this.drawTimer || !this.visible()) return;
    this.drawTimer = setTimeout(() => { this.drawTimer = null; this.renderLive(); }, 3000);
  },

  /* ---- activity ---- */
  addEvents(prog, list, history) {
    const feed = this.feed[prog];
    for (const raw of list) {
      const e = Object.assign({}, raw, { type: raw.type || raw.kind });
      if (!['candidate', 'open', 'closed', 'eliminated', 'system'].includes(e.type)) continue;
      if (e.type === 'closed' && e.counterfactual) continue;
      const k = [prog, e.type, e.event, e.session_id, e.bot_key, e.side, e.signal_ts, e.entry_ts, e.exit_ts, e.at,
        e.type === 'open' ? e.ts : ''].join('|');
      if (this.seen.has(k)) continue;
      this.seen.add(k);
      e.at_ms = e.ts || Date.now();
      if (history) feed.push(e); else feed.unshift(e);
    }
    feed.sort((a, b) => (b.at_ms || 0) - (a.at_ms || 0));
    if (feed.length > 200) feed.length = 200;
  },
  line(e) {
    const who = h('b', { class: 'mono', text: e.bot_key || '' });
    const sub = (s) => h('span', { class: 'sub', text: s });
    const coin = progOf(this.prog).scan && e.coin && e.coin !== 'ALL' ? h('b', { class: 'accent', text: ' ' + e.coin }) : null;
    if (coin) who.append(coin);
    if (e.type === 'candidate') return [who, ' setup ', aSide(e.side), ' ', sub('@ ' + aPrice(e.price) + ' → '),
      pill(String(e.outcome || 'PENDING').split(':')[0].replace(/_/g, ' '), e.outcome === 'ORDERED' ? 'up' : 'ghost')];
    if (e.type === 'open') return [aSide(e.side), h('b', { text: ' OPEN ' }), who, ' ', sub('@ ' + aPrice(e.price) + ' · risk ' + aPct(e.risk_pct, 2))];
    if (e.type === 'closed') return [h('b', { text: 'CLOSE ' }), who, ' ', h('b', { class: signClass(e.net), text: aMoney(e.net, 3) + ' ' + progOf(this.prog).ccy }),
      ' ', sub((isNum(e.r_net) ? (e.r_net >= 0 ? '+' : '') + e.r_net.toFixed(2) + 'R · ' : '') + String(e.exit_kind || '').replace(/_/g, ' '))];
    if (e.type === 'eliminated') return [pill('ELIMINATED', 'down'), ' ', who, ' ', sub(String(e.reason || '').replace(/_/g, ' ').toLowerCase())];
    const ev = e.event || '';
    return [pill('SYSTEM', 'accent'), ' ', sub(ev === 'FORWARD_START' ? 'forward start' : ev === 'RESUMED' ? 'resumed after a restart'
      : ev === 'LIVE' ? 'all bots live' + (e.continuity_checks ? ' · continuity ' + e.continuity_ok + '/' + e.continuity_checks : '') : ev.toLowerCase())];
  },
  renderFeed() {
    const box = $('#arena-feed');
    if (!box) return;
    const items = this.feed[this.prog].slice(0, 80);
    const ul = h('ul', { class: 'feed arena-feed' });
    if (!items.length) ul.append(h('li', { class: 'act' }, h('span'), h('span'), h('span', { class: 'sub', text: 'waiting for the first setup' })));
    for (const e of items) ul.append(h('li', { class: 'act act-' + e.type },
      h('span', { class: 'act-time', text: aClock(e.at_ms) }),
      h('span', { class: 'act-icon', text: { candidate: '◆', open: '▲', closed: '■', eliminated: '✕', system: '●' }[e.type] || '·' }),
      h('span', { class: 'act-body' }, this.line(e))));
    clear(box).append(ul);
  },

  /* ---- building blocks ---- */
  statusOf(d) {
    const s = ((d || {}).status || {}).status || 'DISABLED';
    return ({ LIVE: ['LIVE', 'up'], DEGRADED: ['DEGRADED', 'warn'], WARMING_UP: ['WARMING UP', 'accent'], STARTING: ['STARTING', 'accent'],
      STARTING_BOTS: ['STARTING', 'accent'], RESTARTING: ['RESTARTING', 'warn'], FROZEN_MISMATCH: ['NOT FROZEN', 'down'],
      ERROR: ['ERROR', 'down'], DISABLED: ['OFF', 'ghost'], MARKET_CLOSED: ['MARKET CLOSED', 'accent'],
      NO_DATA_KEYS: ['NEEDS ALPACA KEYS', 'warn'] })[s] || [s, 'warn'];
  },
  switcher() {
    return h('div', { class: 'arena-switch', role: 'tablist' }, Object.entries(ARENA_PROGRAMS).map(([k, p]) =>
      h('button', { type: 'button', role: 'tab', class: 'arena-tab' + (k === this.prog ? ' on' : ''), onclick: () => this.setProgram(k) },
        h('b', { text: p.label }), h('span', { text: p.sub }))));
  },
  kpis(d) {
    const x = d.hero || {}, [stText, stKind] = this.statusOf(d);
    const P = ARENA_PROGRAMS[this.prog];
    const tiles = [
      h('div', { class: 'tile' }, h('span', { class: 'lbl', text: 'status' }),
        h('span', { class: 'val' }, h('span', { class: 'arena-status ' + stKind }, h('i', { class: 'dot' }), stText)),
        h('span', { class: 'sub', id: 'arena-age', text: 'age ' + aAge(Date.now() - (x.forward_start_ms || Date.now())) })),
      tile('bots live', (x.active_bots ?? 0) + ' / ' + (x.bots ?? 0), '', (this.prog === 'v8' || this.prog === 'v9' || this.prog === 'v11' || this.prog === 'v12' || this.prog === 'v13' || this.prog === 'v14')
        ? (x.qualified ?? 0) + ' qualified · ' + (x.eliminated ?? 0) + ' eliminated' : 'paper books, ' + P.book + ' each'),
      tile('open positions', String(x.positions_open ?? 0), '', 'trades / 24h: ' + (x.trades_24h ?? 0)),
      LivePrices.tile('total equity · ' + P.ccy, this.prog, 'equity', x.total_virtual_equity, 'started ' + (x.start_equity_total ?? DASH), 'usd'),
      LivePrices.tile('net PnL · ' + P.ccy, this.prog, 'net', x.net_pnl, P.stocks ? 'after spread and regulatory fees' : 'after fees, spread and funding'),
      h('div', { class: 'tile' }, h('span', { class: 'lbl', text: 'next decision' }), h('span', { class: 'val accent', id: 'arena-next', text: '…' }),
        h('span', { class: 'sub', text: P.scan ? 'every 5m close · 15m / 1h by bot' : 'every ' + P.tf + ' close' })),
    ];
    return h('div', { class: 'card arena-kpis', id: 'arena-kpis' }, h('div', { class: 'tiles six' }, tiles));
  },
  coins(d) {
    if (progOf(this.prog).scan && (d.universe || []).length) return d.universe;       // a scanner program: all 30 coins
    return (d.experiment || {}).coins || [...new Set((d.leaderboard || []).map((r) => r.coin))];
  },
  /** A scanner program trades 30 coins: chips for the coins in play (open positions first, then the most traded), the
      rest of the universe in a menu. */
  scanCoins(d, extra = []) {
    const n = new Map();
    for (const c of extra) n.set(c, 1e9);
    for (const p of d.positions || []) if (p.coin && p.coin !== 'ALL') n.set(p.coin, 1e8 + (n.get(p.coin) || 0));
    for (const r of d.leaderboard || []) for (const [c, k] of Object.entries(r.coins_traded || {})) n.set(c, (n.get(c) || 0) + k);
    return [...n.entries()].sort((a, b) => b[1] - a[1]).map((x) => x[0]);
  },
  candleCard(d, opts = {}) {
    const P = progOf(this.prog), all = this.coins(d);
    let coins = all;
    if (P.scan) {
      const hot = opts.coins || this.scanCoins(d);
      coins = hot.length ? hot.slice(0, 10) : all.slice(0, 8);
      if (!this.coin || !all.includes(this.coin)) this.coin = coins[0] || 'BTC';
      if (!coins.includes(this.coin)) coins = [this.coin, ...coins];
    } else if (!this.coin || !coins.includes(this.coin)) this.coin = coins[0] || 'ETH';
    const tf = this.tf || ARENA_PROGRAMS[this.prog].tf;
    const host = h('div', { class: 'arena-chart' + (opts.tall ? ' tall' : '') });
    const choose = (c) => { if (opts.onCoin) opts.onCoin(c); else this.coin = c; this.render(); };
    const menu = P.scan ? h('select', { class: 'chip coin-menu', 'aria-label': 'any coin of the universe', onchange: (e) => choose(e.target.value) },
      h('option', { value: this.coin, text: 'all ' + all.length + ' coins…' }), all.map((c) => h('option', { value: c, text: c }))) : null;
    const pick = h('div', { class: 'chips' },
      coins.map((c) => h('button', { type: 'button', class: 'chip' + (c === this.coin ? ' on' : ''), text: c, onclick: () => choose(c) })), menu,
      h('span', { class: 'chip-gap' }),
      (this.prog === 'video' ? ['4h'] : ['1m', '5m', '15m', '1h']).map((t) => h('button', { type: 'button', class: 'chip' + (t === tf ? ' on' : ''), text: t, onclick: () => { this.tf = t; this.render(); } })));
    const who = opts.botKey ? null : h('div', { class: 'chips chart-bots' });
    const card = h('div', { class: 'card', id: opts.id || 'arena-candles' },
      h('div', { class: 'card-head' }, h('h2', { text: this.coin + (progOf(this.prog).quote ? ' / ' + progOf(this.prog).quote : '') }),
        h('span', { class: 'sub', text: this.prog === 'video' ? 'latest 42 completed 4h candles · breakout has no fixed stop or take-profit' : opts.botKey ? 'click a closed trade below to show it · hover a box for TP1 / TP2 / TP3, stop and result' : 'one bot at a time · hover a box for its target, stop and result' })),
      pick, who, host);
    // a timer, not requestAnimationFrame: rAF never fires in a background tab, which would leave the chart blank.
    // The symbol is fixed NOW: a bot's page sets this.coin to the bot's coin only while building this card.
    const symbol = this.coin + progOf(this.prog).quote;
    const focus = opts.botKey && this.focus && this.focus.bot === opts.botKey && this.focus.symbol === symbol ? this.focus : null;
    setTimeout(() => this.drawCandles(host, symbol, tf, opts.botKey, who, focus), 0);
    return card;
  },
  /** The chart shows ONE bot's positions (a bot holds one position at a time, so its boxes never pile up). The picker
      lists the bots that traded this coin; it starts on the most recent one and remembers the choice per coin. */
  botPicker(who, all, coin) {
    const last = new Map(), open = new Set();
    for (const b of all) for (const k of b.bots) {
      last.set(k, Math.max(last.get(k) || 0, b.entry_ts || 0));
      if (b.open) open.add(k);
    }
    const keys = [...last.keys()].sort();
    let pick = this.chartBot[coin];
    if (!keys.includes(pick)) pick = [...last.entries()].sort((a, b) => b[1] - a[1]).map((x) => x[0])[0] || '';
    if (who) {
      const short = (k) => k.replace('-' + coin + '-', ' ').replace(/ (5M|15M)\b/, '').replace(/\+(JEV|LADDER)$/, ' +$1').replace(/-SCAN$/, '');
      clear(who).append(keys.length ? h('span', { class: 'sub', text: 'bot' }) : h('span', { class: 'sub', text: 'no trades on this coin yet' }),
        ...keys.map((k) => h('button', { type: 'button', class: 'chip' + (k === pick ? ' on' : ''), title: k + (open.has(k) ? ' · in a trade' : ''),
          onclick: () => { this.chartBot[coin] = k; this.render(); } }, open.has(k) ? h('i', { class: 'chip-dot' }) : null, short(k))));
    }
    return pick;
  },
  async drawCandles(host, symbol, tf, botKey, who, focus) {
    const L = chartLib();
    if (!L) { host.append(h('p', { class: 'sub', text: 'chart library unavailable' })); return; }
    const stepMs = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400 }[tf] * 1000;
    // a chosen trade: end the window a little after it, so an older trade is still in the picture
    const until = focus ? Math.min(Date.now(), (focus.exit_ts || focus.entry_ts) + 60 * stepMs) : 0;
    let d;
    try { d = await getJSON(`${this.base}/${this.prog}/candles?symbol=${symbol}&tf=${tf}&limit=300${until ? '&until=' + until : ''}`); } catch (e) { d = { candles: [] }; }
    if (!host.isConnected) return;
    const th = chartTheme();
    const chart = L.createChart(host, { autoSize: true, layout: { background: { color: 'transparent' }, textColor: th.text, fontFamily: 'JetBrains Mono, monospace' },
      grid: { vertLines: { color: th.grid }, horzLines: { color: th.grid } }, rightPriceScale: { borderColor: 'rgba(122,144,135,.25)' },
      timeScale: { borderColor: 'rgba(122,144,135,.25)', timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 } });
    this.charts.push(chart);
    const s = chart.addCandlestickSeries({ upColor: th.up, downColor: th.down, borderUpColor: th.up, borderDownColor: th.down, wickUpColor: th.up, wickDownColor: th.down });
    const bars = (d.candles || []).map((c) => ({ time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4] }));
    s.setData(bars);
    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '', color: 'rgba(56,214,255,.25)' });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData((d.candles || []).map((c) => ({ time: c[0] / 1000, value: c[5], color: c[4] >= c[1] ? 'rgba(31,242,154,.22)' : 'rgba(255,77,97,.22)' })));
    const step = ARENA_PROGRAMS[this.prog] && { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400 }[tf];
    const quote = progOf(this.prog).quote;
    const pick = botKey || this.botPicker(who, this.positionBoxes(d, symbol, ''), quote ? symbol.slice(0, -quote.length) : symbol);
    const boxes = pick ? this.positionBoxes(d, symbol, pick) : [];
    if (bars.length && s.attachPrimitive) { host.positionBoxes = new PositionBoxes(bars, boxes, step, th, true); s.attachPrimitive(host.positionBoxes); }
    host.chartRefs = { chart, s, vol, symbol, tf, step, botKey, who, focus, prog: this.prog };      // for in-place refreshes
    s.setMarkers(this.exitMarks(d, boxes, step));
    // the latest ~120 bars with room on the right for open boxes running to their time stop -- or the chosen trade
    const n = bars.length, pb = host.positionBoxes;
    const chosen = focus && pb ? boxes.find((b) => !b.open && Math.abs((b.entry_ts || 0) - focus.entry_ts) < 1000
      && (!focus.side || b.side === focus.side)) : null;
    if (chosen) {
      pb.pinned = pb.hover = chosen;
      const i0 = pb.index(chosen.entry_ts) ?? n - 60, i1 = pb.index(chosen.exit_ts) ?? i0 + 4;
      const pad = Math.max(25, (i1 - i0) * 1.5);
      chart.timeScale().setVisibleLogicalRange({ from: i0 - pad, to: i1 + pad });
    } else if (n > 120) chart.timeScale().setVisibleLogicalRange({ from: n - 120, to: n + 10 });
    else chart.timeScale().fitContent();
  },
  /** closed boxes from the candles payload plus the program's open positions on this coin (a pair's twin fills merged) */
  positionBoxes(d, symbol, botKey) {
    const mine = (b) => !botKey || (b.bots || []).includes(botKey);
    const sh = d.ladder_shares || {}, shareOf = (b) => sh[String((b.bots || [])[0] || '').split('-')[0]] || null;
    const closed = (d.positions || []).filter(mine);
    for (const b of closed) b.shares = shareOf(b);
    const open = new Map();
    for (const p of ((this.data[this.prog] || {}).positions || [])) {
      if ((p.symbol || (p.coin + progOf(this.prog).quote)) !== symbol || (botKey && p.bot_key !== botKey) || !isNum(p.entry)) continue;
      const k = [p.side, p.entry_ts, p.entry].join('|');
      const b = open.get(k) || { open: true, side: p.side, entry_ts: p.entry_ts, entry: p.entry, stop: p.stop, target: p.target,
        tps: p.tps, time_stop_ts: p.time_stop_ts, bots: [], net: [] };
      b.bots.push(p.bot_key); b.net.push(p.upnl);
      if (!b.shares) b.shares = shareOf(b);
      open.set(k, b);
    }
    return [...closed, ...open.values()];
  },
  growthCard(d) {
    const host = h('div', { class: 'arena-chart' });
    const rows = (d.leaderboard || []).filter((r) => (r.curve || []).length > 1);
    const card = h('div', { class: 'card', id: 'arena-growth' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Capital growth · every bot' }),
        h('span', { class: 'sub', text: '% of each bot\'s ' + progOf(this.prog).book + ' · top 5 and bottom 3 highlighted · eliminated bots dashed' })), host);
    setTimeout(() => {
      const L = chartLib();
      if (!L || !host.isConnected) return;
      const th = chartTheme();
      const chart = L.createChart(host, { autoSize: true, layout: { background: { color: 'transparent' }, textColor: th.text, fontFamily: 'JetBrains Mono, monospace' },
        grid: { vertLines: { color: th.grid }, horzLines: { color: th.grid } }, rightPriceScale: { borderColor: 'rgba(122,144,135,.25)' },
        timeScale: { borderColor: 'rgba(122,144,135,.25)', timeVisible: true }, crosshair: { mode: 0 }, handleScroll: false, handleScale: false });
      this.charts.push(chart);
      const ranked = rows.slice().sort((a, b) => (b.net_now || 0) - (a.net_now || 0));
      const top = new Set(ranked.slice(0, 5).map((r) => r.key)), bottom = new Set(ranked.slice(-3).map((r) => r.key));
      for (const r of ranked.slice().reverse()) {
        const start = r.curve[0][1] || 20;
        const pts = [];
        for (const [t, v] of r.curve) {
          const time = Math.floor(t / 1000);
          if (pts.length && time <= pts[pts.length - 1].time) pts[pts.length - 1].value = (v / start - 1) * 100;
          else pts.push({ time, value: (v / start - 1) * 100 });
        }
        const hi = top.has(r.key) || bottom.has(r.key);
        const line = chart.addLineSeries({ color: top.has(r.key) ? th.up : bottom.has(r.key) ? th.down : 'rgba(122,144,135,.35)',
          lineWidth: hi ? 2 : 1, lineStyle: r.program_status === 'ELIMINATED' ? 2 : 0, lastValueVisible: hi, priceLineVisible: false,
          title: hi ? r.key : '', priceFormat: { type: 'custom', formatter: (v) => v.toFixed(1) + '%' } });
        line.setData(pts);
      }
      chart.timeScale().fitContent();
    }, 0);
    return card;
  },
  positionsCard(d) {
    const list = h('div', { class: 'pos-list' });
    const pos = d.positions || [];
    if (!pos.length) list.append(h('div', { class: 'pos-empty', text: 'no open paper positions' }));
    const scan = progOf(this.prog).scan;
    for (const p of pos) list.append(h('div', { class: 'pos-item ' + (p.side === 'short' ? 'short' : 'long'), onclick: () => this.openBot(p.bot_key) },
      h('span', { class: 'who' }, aSide(p.side), ' ', scan ? h('b', { text: p.coin + ' ' }) : null, p.bot_key),
      h('span', { class: 'pnl' }, LivePrices.node(this.prog, p.bot_key, 'upnl', p.upnl)),
      h('span', { class: 'meta' }, 'entry ' + aPrice(p.entry) + ' → ', LivePrices.node(this.prog, p.bot_key, 'mark', p.mark, 'price'),
        ' · stop ' + aPrice(p.stop) + ' · target ' + aPrice(p.target) + ' · risk ' + aPct(p.risk_pct, 2) + (isNum(p.hold_s) ? ' · ' + fmtDur(p.hold_s) : ''))));
    return h('div', { class: 'card', id: 'arena-positions' }, h('div', { class: 'card-head' }, h('h2', { text: 'Open positions · ' + pos.length })), list);
  },
  feedCard() {
    return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Activity' })), h('div', { id: 'arena-feed' }));
  },
  botsCard(d, compact) {
    const rows = (d.leaderboard || []).filter((r) => this.filter === 'all' || (r.program_status || 'ACTIVE') === this.filter.toUpperCase()
      || (this.filter === 'open' && (r.open_positions || []).length));
    const t = h('table', { class: 'tbl lb' });
    renderTable(t, [
      { h: '#', cell: (r) => String(r.rank), num: true, cls: 'rank' },
      { h: 'bot', cell: (r) => h('span', { class: 'sid', text: r.key }) },
      { h: 'status', cell: (r) => pill(r.program_status || (r.live ? 'ACTIVE' : 'WARMING'), STATUS_KIND[r.program_status] || 'muted') },
      { h: 'growth', cell: (r) => sparkline(r.curve) },
      { h: 'equity', cell: (r) => LivePrices.node(this.prog, r.key, 'equity', r.equity_now, 'usd'), num: true },
      { h: 'net', cell: (r) => LivePrices.node(this.prog, r.key, 'net', r.net_now), num: true },
      { h: 'trades', cell: (r) => String(r.trades ?? 0), num: true },
      { h: '24h', cell: (r) => String(r.trades_24h ?? 0), num: true },
      { h: 'win', cell: (r) => aPct(r.win_rate, 0), num: true },
      { h: 'PF', cell: (r) => (isNum(r.profit_factor) ? (r.profit_factor >= 999 ? '∞' : r.profit_factor.toFixed(2)) : DASH), num: true },
      { h: 'max DD', cell: (r) => aPct(r.max_dd), num: true },
      { h: 'fees', cell: (r) => (isNum(r.fees) ? r.fees.toFixed(3) : DASH), num: true },
      progOf(this.prog).scan ? { h: 'coins traded', cell: (r) => Object.entries(r.coins_traded || {}).slice(0, 4).map(([c, k]) => c + ' ' + k).join(' · ') || DASH } : null,
    ].filter(Boolean), compact ? rows.slice(0, 10) : rows, { onRow: (r) => this.openBot(r.key), rowClass: (r) => (r.program_status === 'ELIMINATED' ? 'dim' : ''), empty: 'no bots match' });
    const filters = h('div', { class: 'chips' }, [['all', 'All'], ['active', 'Active'], ['qualified', 'Qualified'], ['eliminated', 'Eliminated'], ['open', 'In a trade']]
      .map(([f, l]) => h('button', { type: 'button', class: 'chip' + (f === this.filter ? ' on' : ''), text: l, onclick: () => { this.filter = f; this.render(); } })));
    return h('div', { class: 'card', id: 'arena-bots' }, h('div', { class: 'card-head' }, h('h2', { text: compact ? 'Top bots' : 'Bots' }),
      compact ? h('button', { type: 'button', class: 'chip', text: 'all bots →', onclick: () => ArenaNav.go('bots') }) : filters),
    h('div', { class: 'tablewrap' }, t));
  },
  /** CONTROL vs +JEV: each pair trades the same candidates; the twin only lets Jev SKIP, TAKE or ATTACK them. */
  pairsCard(d) {
    const pairs = (d.pairs || []).slice().sort((a, b) => (b.delta ?? 0) - (a.delta ?? 0));
    const j = d.jev || {}, ahead = pairs.filter((p) => (p.delta ?? 0) > 0).length;
    const acts = (p) => ['SKIP', 'TAKE', 'ATTACK'].map((k) => (p.actions || {})[k] || 0).join(' / ');
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'pair', cell: (p) => h('span', { class: 'sid', text: p.control_key || p.pair_id }) },
      { h: 'control net', cell: (p) => h('span', { class: signClass(p.control_net), text: aMoney(p.control_net, 3) }), num: true },
      { h: '+JEV net', cell: (p) => h('span', { class: signClass(p.jev_net), text: aMoney(p.jev_net, 3) }), num: true },
      { h: 'Jev Δ', cell: (p) => h('b', { class: signClass(p.delta), text: aMoney(p.delta, 3) }), num: true },
      { h: 'trades c / j', cell: (p) => (p.control_trades ?? 0) + ' / ' + (p.jev_trades ?? 0), num: true },
      { h: 'skip / take / attack', cell: acts, num: true },
      { h: 'latency', cell: (p) => (isNum(p.latency_p50_ms) ? (p.latency_p50_ms / 1000).toFixed(1) + ' s' : DASH), num: true },
    ], pairs, { onRow: (p) => this.openBot(p.jev_key), empty: 'no pairs yet' });
    return h('div', { class: 'card', id: 'arena-pairs' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Jev vs control · ' + pairs.length + ' pairs' }),
        h('span', { class: 'sub', text: 'Jev ahead in ' + ahead + ' · Δ total ' + aMoney((d.hero || {}).jev_edge, 3) + ' ' + progOf(this.prog).ccy + ' · '
          + (j.decisions ?? 0) + ' calls, ' + (j.errors ?? 0) + ' errors, $' + (isNum(j.cost_usd) ? j.cost_usd.toFixed(4) : '0') })),
      h('div', { class: 'tablewrap' }, t));
  },
  /** CONTROL vs +LADDER: the same entries; the twin exits with TP1 0.75R / TP2 1.5R / TP3 2.5R and stop moves. */
  ladderCard(d) {
    const rows = d.leaderboard || [];
    const ctl = new Map(rows.filter((r) => r.role === 'CONTROL').map((r) => [r.key, r]));
    const pairs = rows.filter((r) => r.role === 'LADDER').map((l) => ({ l, c: ctl.get(l.control_key) || {} }));
    if (!pairs.length) return null;
    const sum = (xs, f) => xs.reduce((a, x) => a + (Number(f(x)) || 0), 0);
    const wr = (xs) => { const n = sum(xs, (r) => r.trades); return n ? sum(xs, (r) => (r.win_rate || 0) * (r.trades || 0)) / n : null; };
    const cs = pairs.map((p) => p.c), ls = pairs.map((p) => p.l);
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'bot', cell: (p) => h('span', { class: 'sid', text: p.c.key || p.l.control_key }) },
      { h: 'control net', cell: (p) => h('span', { class: signClass(p.c.net_now), text: aMoney(p.c.net_now, 3) }), num: true },
      { h: 'ladder net', cell: (p) => h('span', { class: signClass(p.l.net_now), text: aMoney(p.l.net_now, 3) }), num: true },
      { h: 'ladder − control', cell: (p) => { const v = (p.l.net_now || 0) - (p.c.net_now || 0); return h('b', { class: signClass(v), text: aMoney(v, 3) }); }, num: true },
      { h: 'win rate c / l', cell: (p) => aPct(p.c.win_rate, 0) + ' / ' + aPct(p.l.win_rate, 0), num: true },
      { h: 'trades c / l', cell: (p) => (p.c.trades ?? 0) + ' / ' + (p.l.trades ?? 0), num: true },
    ], pairs.sort((a, b) => ((b.l.net_now || 0) - (b.c.net_now || 0)) - ((a.l.net_now || 0) - (a.c.net_now || 0))),
    { onRow: (p) => this.openBot(p.l.key), empty: 'no pairs yet' });
    const dn = sum(ls, (r) => r.net_now) - sum(cs, (r) => r.net_now);
    return h('div', { class: 'card', id: 'arena-ladder' },
      h('div', { class: 'card-head' }, h('h2', { text: 'TP ladder vs control · ' + pairs.length + ' pairs' }),
        h('span', { class: 'sub', text: 'win rate ' + aPct(wr(cs), 1) + ' → ' + aPct(wr(ls), 1) + ' · net ' + aMoney(sum(cs, (r) => r.net_now), 2)
          + ' → ' + aMoney(sum(ls, (r) => r.net_now), 2) + ' ' + progOf(this.prog).ccy + ' (' + aMoney(dn, 2) + ')' })),
      h('p', { class: 'sub', text: 'Same entries. Ladder: TP1 +0.75R closes ⅓ and moves the stop to entry + fees; TP2 +1.5R closes ⅓ and locks +0.75R; TP3 +2.5R closes the rest.' }),
      h('div', { class: 'tablewrap' }, t));
  },
  async openBot(key) {
    this.botKey = key; this.bot = null;
    if (this.view !== 'bots') { ArenaNav.go('bots'); }
    this.render();
    try { this.bot = await getJSON(`${this.base}/${this.prog === 'v6' ? 'v6' : this.prog}/bot/${encodeURIComponent(key)}`); }
    catch (e) { this.bot = { ok: false, error: e.message }; }
    this.render();
    const el = $('#arena-bot');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },
  /** A click on a closed trade: its coin on the chart, zoomed onto it, its box highlighted with TP1 / TP2 / TP3. */
  showTrade(x) {
    const quote = progOf(this.prog).quote, sym = String(x.symbol || '');
    const coin = quote && sym.endsWith(quote) ? sym.slice(0, -quote.length) : sym;
    this.focus = { bot: this.botKey, symbol: sym, side: x.side, entry_ts: x.entry_ts, exit_ts: x.exit_ts };
    if (progOf(this.prog).scan) this.botCoin[this.botKey] = coin;
    const mins = ((x.exit_ts || x.entry_ts) - x.entry_ts) / 60_000;
    this.tf = mins <= 90 ? '5m' : mins <= 480 ? '15m' : '1h';          // the whole trade in a readable number of bars
    if (this.prog === 'video') { this.tf = '4h'; this.focus = null; }
    this.render();
    const el = $('#arena-bot-candles');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  },
  botCard(d, keepCandles) {
    const r = (d.leaderboard || []).find((x) => x.key === this.botKey) || {};
    const close = h('button', { type: 'button', class: 'chip', text: 'close', onclick: () => { this.botKey = ''; this.bot = null; this.render(); } });
    const trades = ((this.bot || {}).trades || []).filter((t) => !t.counterfactual);
    const t = h('table', { class: 'tbl' });
    const scan = progOf(this.prog).scan;
    renderTable(t, [{ h: 'opened', cell: (x) => this.prog === 'video' ? new Date(x.entry_ts).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : aClock(x.entry_ts) + ' UTC' },
      this.prog === 'video' ? { h: 'closed', cell: (x) => new Date(x.exit_ts).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' } : null,
      scan ? { h: 'coin', cell: (x) => h('b', { text: String(x.symbol || '').replace(/USDT$/, '') || DASH }) } : null,
      { h: 'side', cell: (x) => aSide(x.side) },
      { h: 'entry → exit', cell: (x) => aPrice(x.entry_price) + ' → ' + aPrice(x.exit_price) },
      { h: 'net', cell: (x) => h('b', { class: signClass(x.net), text: aMoney(x.net, 3) }), num: true },
      { h: 'fees', cell: (x) => (isNum(x.fees) ? x.fees.toFixed(3) : DASH), num: true }, { h: 'exit', cell: (x) => String(x.exit_kind || '').replace(/_/g, ' ') }].filter(Boolean),
    trades.slice(0, 50), { empty: this.bot ? 'no closed trades yet' : 'loading…', onRow: (x) => this.showTrade(x),
      rowClass: (x) => (this.focus && this.focus.bot === this.botKey && this.focus.entry_ts === x.entry_ts
        && this.focus.symbol === x.symbol && this.focus.side === x.side ? 'on' : '') });
    const saveCoin = this.coin;
    let candles;
    if (keepCandles) candles = keepCandles;
    else if (scan) {            // a scanner's chart: the coins this bot holds or traded (latest first), any coin on request
      const held = (r.open_positions || []).map((p) => String(p.symbol || '').replace(/USDT$/, '')).filter(Boolean);
      const recent = trades.map((x) => String(x.symbol || '').replace(/USDT$/, '')).filter(Boolean);
      const mine = [...new Set([...held, ...recent, ...Object.keys(r.coins_traded || {})])];
      this.coin = this.botCoin[this.botKey] || mine[0] || this.coin;
      candles = this.candleCard(d, { id: 'arena-bot-candles', botKey: this.botKey, coins: mine,
        onCoin: (c) => { this.botCoin[this.botKey] = c; } });
    } else {
      this.coin = r.coin || this.coin;
      candles = this.candleCard(d, { id: 'arena-bot-candles', botKey: this.botKey });
    }
    this.coin = saveCoin;
    return h('div', { class: 'card', id: 'arena-bot' },
      h('div', { class: 'card-head' }, h('h2', { text: typeof Roster !== 'undefined' ? Roster.label(this.prog, this.botKey) : this.botKey },
        typeof Roster !== 'undefined' ? Roster.jevBadge({ program: this.prog, key: this.botKey }) : null), h('span', null, pill(r.program_status || 'ACTIVE', STATUS_KIND[r.program_status] || 'muted'), ' ',
        // private dashboard only: a QUALIFIED V8 bot, or a V6.2 / V6.6 bot whose two-year backtest made money
        // (app/live/v6_golive.py), can be mirrored live by the operator (mirror.js; the server re-checks)
        window.PAPERLAB_PRIVATE && typeof LiveMirror !== 'undefined' && r.role !== 'LADDER'
          && ((this.prog === 'v8' && r.program_status === 'QUALIFIED') || (this.prog === 'v6' && r.golive && r.golive.eligible))
          ? [LiveMirror.goLiveSlot({ ...r, program: this.prog }), ' '] : null,
        close)),
      h('div', { class: 'tiles six' },
        tile('equity', isNum(r.equity_now) ? r.equity_now.toFixed(2) : DASH, '', 'started ' + (isNum(r.start_equity) ? r.start_equity.toFixed(2) : '20.00')),
        tile('net', aMoney(r.net_now, 3), signClass(r.net_now), 'after all costs'),
        tile('trades', String(r.trades ?? 0), '', (r.trades_24h ?? 0) + ' in 24h'),
        tile('win rate', aPct(r.win_rate, 0), '', 'PF ' + (isNum(r.profit_factor) ? r.profit_factor.toFixed(2) : DASH)),
        tile('max drawdown', aPct(r.max_dd), '', 'risk state ' + (r.risk_state || DASH)),
        tile('growth', '', '', '')),
      scan && SCAN_BLURB[r.strategy_id] ? h('p', { class: 'sub', text: SCAN_BLURB[r.strategy_id] }) : null,
      this.prog === 'video' && typeof BreakoutUI !== 'undefined' ? BreakoutUI.details(d) : null,
      candles, h('h3', { text: 'closed trades' }), h('div', { class: 'tablewrap' }, t));
  },
  /** V11: what the scanners are, the universe, and the study's honest verdict. */
  scanCard(d) {
    const rows = (d.leaderboard || []).slice().sort((a, b) => String(a.strategy_id).localeCompare(String(b.strategy_id)));
    return h('div', { class: 'card', id: 'arena-scan' },
      h('div', { class: 'card-head' }, h('h2', { text: 'The scanners · ' + (d.universe || []).length + ' coins' }),
        h('span', { class: 'sub', text: 'each bot is ONE ' + progOf(this.prog).book + ' book that looks at every coin and takes the best setups, up to 8 coins at once (the 1h ranker: 2 long + 2 short)' })),
      h('ul', { class: 'scan-list' }, rows.map((r) => h('li', { onclick: () => this.openBot(r.key) },
        h('b', { class: 'sid', text: r.key }), ' ', h('span', { class: 'sub', text: SCAN_BLURB[r.strategy_id] || r.family || '' })))),
      h('p', { class: 'sub', text: 'Universe: ' + (d.universe || []).join(' · ') }),
      h('p', { class: 'sub', text: 'Before launch, 90 days of these 30 coins were replayed: no scanner rule beat fees in both halves '
        + '(best: the RS breakout ≈ −0.09R and the 1h reversal ≈ −0.06R per trade, vs V8 ≈ −0.28R). Paper only; live data judges them.' }));
  },

  /* ---- views ---- */
  destroyCharts() { for (const c of this.charts) { try { c.remove(); } catch (e) { /* gone */ } } this.charts = []; },
  /** What is on screen: a reload may refresh it in place only while this is unchanged. */
  screenKey() {
    return [this.view, this.prog, this.botKey, this.coin, this.tf, this.botCoin[this.botKey] || '',
      this.focus ? this.focus.symbol + '@' + this.focus.entry_ts : ''].join('|');
  },
  liveChartHost() {
    const el = $(this.view === 'bots' ? '#arena-bot-candles .arena-chart' : '#arena-candles .arena-chart');
    return el && el.chartRefs ? el : null;
  },
  async refreshInPlace() {
    const d = this.data[this.prog], host = this.liveChartHost();
    if (!d || !host) return this.render();
    const y = window.scrollY;
    if (this.view === 'bots' && this.botKey) {
      try { this.bot = await getJSON(`${this.base}/${this.prog}/bot/${encodeURIComponent(this.botKey)}`); } catch (e) { /* keep the last */ }
      const card = $('#arena-bot'), keep = $('#arena-bot-candles');
      if (card && keep) {
        const fresh = this.botCard(d, keep);
        card.replaceWith(fresh);
        const growth = fresh.querySelector('.tiles .tile:last-child .val');
        const r = (d.leaderboard || []).find((x) => x.key === this.botKey);
        if (growth) growth.append(sparkline(r && r.curve, 140, 30));
      }
      const list = $('#arena-bots');
      if (list && typeof Roster !== 'undefined') list.replaceWith(Roster.botsCard());
    }
    await this.updateCandles(host);
    if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
  },
  /** New candles, boxes and markers for a chart that stays on screen: the view keeps showing the same stretch of time
      (or keeps following the newest bar if it was there), the chosen trade stays chosen. */
  async updateCandles(host) {
    const r = host.chartRefs, pb = host.positionBoxes;
    if (!r) return;
    const stepMs = r.step * 1000;
    const until = r.focus ? Math.min(Date.now(), (r.focus.exit_ts || r.focus.entry_ts) + 60 * stepMs) : 0;
    let d;
    try { d = await getJSON(`${this.base}/${r.prog}/candles?symbol=${r.symbol}&tf=${r.tf}&limit=300${until ? '&until=' + until : ''}`); } catch (e) { return; }
    if (!host.isConnected || !(d.candles || []).length) return;
    const bars = d.candles.map((c) => ({ time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4] }));
    const ts = r.chart.timeScale(), lr = ts.getVisibleLogicalRange();
    const old = pb ? pb.times : [];
    const atEnd = lr && old.length && lr.to >= old.length - 1.5;
    r.s.setData(bars);
    r.vol.setData(d.candles.map((c) => ({ time: c[0] / 1000, value: c[5], color: c[4] >= c[1] ? 'rgba(31,242,154,.22)' : 'rgba(255,77,97,.22)' })));
    const quote = progOf(r.prog).quote;
    const pick = r.botKey || this.botPicker(r.who, this.positionBoxes(d, r.symbol, ''), quote ? r.symbol.slice(0, -quote.length) : r.symbol);
    const boxes = pick ? this.positionBoxes(d, r.symbol, pick) : [];
    if (pb) {
      pb.times = bars.map((b) => b.time);
      pb.boxes = boxes;
      const same = (a, b) => a && b && !b.open && Math.abs((a.entry_ts || 0) - (b.entry_ts || 0)) < 1000 && a.side === b.side;
      pb.pinned = pb.pinned ? boxes.find((b) => same(pb.pinned, b)) || null : null;
      pb.hover = pb.hover ? boxes.find((b) => same(pb.hover, b) || (pb.hover.open && b.open && b.entry_ts === pb.hover.entry_ts)) || pb.pinned : pb.pinned;
      if (pb.requestUpdate) pb.requestUpdate();
    }
    r.s.setMarkers(this.exitMarks(d, boxes, r.step));
    if (lr && old.length) {
      const shift = Math.round((bars[0].time - old[0]) / r.step);             // bars that fell off the left edge
      let from = lr.from - shift, to = lr.to - shift;
      if (atEnd) { const grow = (bars.length - 1) - (old.length - 1 - shift); from += grow; to += grow; }
      ts.setVisibleLogicalRange({ from, to });
    }
  },
  /** that bot's exits keep a small marker with the result */
  exitMarks(d, boxes, step) {
    const th = chartTheme();
    const exits = new Set(boxes.filter((b) => !b.open).map((b) => b.exit_ts + '|' + b.exit));
    const seen = new Set();
    return (d.markers || []).filter((m) => m.kind === 'exit' && exits.has(m.ts + '|' + m.price)).filter((m) => {
      const k = m.ts + '|' + m.price; if (seen.has(k)) return false; seen.add(k); return true;
    }).map((m) => ({ time: Math.floor(m.ts / 1000 / step) * step, position: m.side === 'long' ? 'aboveBar' : 'belowBar',
      color: m.net >= 0 ? th.up : th.down, shape: 'circle', size: 0.5, text: aMoney(m.net, 2) })).sort((a, b) => a.time - b.time);
  },
  render() {
    const grid = this.grid(), d = this.data[this.prog];
    if (!grid) return;
    this.renderedKey = this.screenKey();
    this.destroyCharts();
    const y = window.scrollY;
    clear(grid);
    if (this.view === 'dashboard' && typeof Roster !== 'undefined') {      // the named contenders, beebots-style
      Roster.render(grid);
      if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
      return;
    }
    if (this.view === 'markets' || typeof Roster === 'undefined') grid.append(this.switcher());
    if (!d) { grid.append(h('div', { class: 'card' }, h('p', { class: 'sub', text: 'loading…' }))); return; }
    if (!d.experiment) {
      const [txt, kind] = this.statusOf(d);
      grid.append(h('div', { class: 'card' }, h('span', { class: 'arena-status ' + kind }, h('i', { class: 'dot' }), txt), ' ',
        h('span', { class: 'sub', text: d.note || 'this program has not started on this server' })));
      return;
    }
    if (this.view === 'bots') {
      if (typeof Roster === 'undefined') grid.append(this.kpis(d));
      if (this.botKey) grid.append(this.botCard(d));
      grid.append(typeof Roster !== 'undefined' ? Roster.botsCard() : this.botsCard(d, false));
    } else if (this.view === 'markets') {
      grid.append(LivePrices.strip(), this.candleCard(d, { tall: true }));
    } else {
      grid.append(this.kpis(d),
        h('div', { class: 'cc-left' }, this.candleCard(d), this.growthCard(d)),
        h('div', { class: 'cc-right' }, this.positionsCard(d), this.feedCard()),
        this.botsCard(d, true));
      if (progOf(this.prog).scan) grid.append(this.scanCard(d));
      if ((d.pairs || []).length) grid.append(this.pairsCard(d));
      const lad = this.ladderCard(d);
      if (lad) grid.append(lad);
    }
    this.renderFeed();
    const growth = $('#arena-bot .tiles .tile:last-child .val');
    if (growth) { const r = (d.leaderboard || []).find((x) => x.key === this.botKey); growth.append(sparkline(r && r.curve, 140, 30)); }
    this.tick();
    if (typeof LivePrices !== 'undefined') LivePrices.schedule();
    if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
  },
  /** Push updates touch only the KPI strip and the positions list; charts refresh with the 30 s reload. */
  renderLive() {
    const d = this.data[this.prog];
    if (!d || !d.experiment) return;
    for (const [id, build] of [['arena-kpis', () => this.kpis(d)], ['arena-positions', () => this.positionsCard(d)]]) {
      const el = document.getElementById(id);
      if (el) el.replaceWith(build());
    }
    this.tick();
    if (typeof LivePrices !== 'undefined') LivePrices.schedule();
  },
  tick() {
    const d = this.data[this.prog];
    if (!d || !d.hero || !this.visible()) return;
    const now = Date.now(), step = ARENA_PROGRAMS[this.prog].step;
    const next = (Math.floor(now / step) + 1) * step, s = Math.floor((next - now) / 1000);
    const a = $('#arena-next'), age = $('#arena-age');
    const opens = progOf(this.prog).stocks && d.hero.market_open === false ? d.hero.next_open_ms : null;
    if (a && opens) setText(a, 'opens in ' + aAge(opens - now));          // stocks: nothing decides while closed
    else if (a) setText(a, Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'));
    if (age && d.hero.forward_start_ms) setText(age, 'age ' + aAge(now - d.hero.forward_start_ms));
  },
};

/* The lean navigation shared by both pages: Dashboard / Bots / Markets map to Arena views in the home panel. */
const ArenaNav = {
  go(view) {
    if (typeof Nav !== 'undefined' && Nav.goArena) Nav.goArena(view);
    else { Arena.show(view); }
  },
};

function wireArena() {
  if (typeof Stream === 'undefined') return;
  LivePrices.wire();
  for (const p of Object.keys(ARENA_PROGRAMS)) {
    Stream.on(p + '_state', (d) => Arena.onState(p, d));
    Stream.on(p, (d) => Arena.onEvent(p, d));
  }
  Arena.init();
}
