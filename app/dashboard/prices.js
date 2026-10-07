/* Display-only quotes and PnL. Exact observed prices; no invented ticks or order changes. */
'use strict';

const LivePrices = {
  packet: null, receivedAt: 0, transport: false, pending: false, wired: false,
  retired: new Set(), values: new WeakMap(),
  accept(packet, now = performance.now()) {
    if (!packet || !packet.stream_id || !Number.isFinite(packet.seq)) return false;
    const old = this.packet;
    if (this.retired.has(packet.stream_id)) return false;
    if (old && packet.stream_id === old.stream_id && packet.seq <= old.seq) return false;
    if (old && old.stream_id !== packet.stream_id) {
      this.retired.add(old.stream_id);
      if (this.retired.size > 20) this.retired.delete(this.retired.values().next().value);
    }
    this.packet = packet; this.receivedAt = now;
    this.schedule();
    return true;
  },
  quote(symbol, now = performance.now()) {
    const p = this.packet, q = p && (p.quotes || {})[symbol];
    if (!q) return null;
    const age = Math.max(0, q.age_ms || 0) + Math.max(0, now - this.receivedAt);
    return { ...q, age_ms: age, stale: !this.transport || !p.connected || q.stale || age > p.stale_after_ms };
  },
  // Re-mark from each authoritative book snapshot, never add tick deltas to earlier estimates.
  book(row) {
    let delta = 0, upnl = 0, stale = false;
    const positions = (row.open_positions || []).map((p) => {
      const q = this.quote(p.symbol || row.symbol || (row.coin + 'USDT'));   // a scanner book: each position's own coin
      const usable = q && !q.stale && Number.isFinite(q.mid) && q.mid > 0;
      const mark = usable ? q.mid : p.mark;
      const value = usable ? (mark - p.entry) * p.qty * (p.side === 'long' ? 1 : -1) : (p.upnl || 0);
      delta += value - (p.upnl || 0); upnl += value; stale ||= !usable;
      return { ...p, mark, upnl: value, stale: !usable };
    });
    const base = Number.isFinite(row.equity_now) ? row.equity_now : row.equity;
    const equity = Number.isFinite(base) ? base + delta : null;
    const net = equity === null ? null : equity - (row.start_equity || 20);
    return { positions, equity, net, upnl, stale, return: net === null ? null : net / (row.start_equity || 20) };
  },
  model(data) {
    const rows = new Map((data?.leaderboard || []).map((r) => [r.key, this.book(r)]));
    let equity = 0, net = 0, stale = false;
    for (const b of rows.values()) { equity += b.equity || 0; net += b.net || 0; stale ||= b.stale; }
    let jev = 0;
    for (const p of data?.pairs || []) jev += (rows.get(p.jev_key)?.net || 0) - (rows.get(p.control_key)?.net || 0);
    return { rows, equity, net, stale, jev };
  },
  node(kind, key = '', field = '', value = null, format = 'money') {
    return h('span', { class: 'live-number', 'data-live-kind': kind, 'data-live-key': key,
      'data-live-field': field, 'data-live-format': format, text: this.format(value, format) });
  },
  tile(label, kind, field, value, sub, format = 'money') {
    return h('div', { class: 'tile' }, h('span', { class: 'lbl', text: label }),
      h('span', { class: 'val num' }, this.node(kind, '', field, value, format)), h('span', { class: 'sub', text: sub }));
  },
  format(value, format) {
    if (!Number.isFinite(value)) return '—';
    if (format === 'price') return value >= 100 ? value.toFixed(2) : value >= 1 ? value.toFixed(4) : value.toFixed(6);
    if (format === 'pct') return (value >= 0 ? '+' : '') + (value * 100).toFixed(2) + '%';
    if (format === 'bps') return value.toFixed(2) + ' bps';
    if (format === 'usd') return value.toFixed(2);                 // a balance: no sign, cents
    return (value > 0 ? '+' : '') + value.toFixed(4);
  },
  strip() {
    const symbols = this.packet?.symbols || ['ARBUSDT', 'ENAUSDT', 'XRPUSDT', 'DOGEUSDT', 'BTCUSDT', 'ETHUSDT'];
    return h('div', { class: 'card live-prices-card', id: 'live-price-strip' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Live prices' }),
        h('span', { id: 'live-price-health', class: 'pill muted', text: 'CONNECTING' })),
      h('div', { class: 'live-price-grid' }, symbols.map((s) => h('div', { class: 'live-price-quote' },
        h('span', { class: 'lbl', text: s.replace('USDT', '') + ' / USDT' }),
        this.node('quote', s, 'mid', this.quote(s)?.mid, 'price'),
        h('span', { class: 'sub' }, '24h ', this.node('quote', s, 'change_24h', this.quote(s)?.change_24h, 'pct'))))),
      h('p', { class: 'sub', text: 'Best bid/ask midpoint · updates up to 4×/sec · PnL is a live estimate including booked costs; final fills come from the bot.' }));
  },
  schedule() {
    if (this.pending || document.hidden) return;
    this.pending = true;
    requestAnimationFrame(() => { this.pending = false; this.paint(); });
  },
  paint() {
    if (document.hidden) return;
    const src = typeof Arena !== 'undefined' ? Arena.data : { v6: V6Home.data, v7: V6Home.challenger };
    const models = { v6: this.model(src.v6), v7: this.model(src.v7), v8: this.model(src.v8), v9: this.model(src.v9),
      v11: this.model(src.v11), v12: this.model(src.v12), v13: this.model(src.v13), v14: this.model(src.v14) };
    for (const el of document.querySelectorAll('[data-live-kind]')) {
      if (!el.getClientRects().length) continue;
      const { liveKind: kind, liveKey: key, liveField: field, liveFormat: format } = el.dataset;
      let value = null, stale = false;
      if (kind === 'quote') { const q = this.quote(key); value = q?.[field]; stale = !q || q.stale; }
      else {
        const model = models[kind];
        const item = key ? model?.rows.get(key) : model;
        if (field === 'mark') value = item?.positions[0]?.mark;
        else value = item?.[field];
        stale = item?.stale || false;
      }
      const text = this.format(value, format), previous = this.values.get(el);
      if (el.textContent !== text) {
        el.textContent = text;
        // Green / red means profit / loss, so only signed values (PnL, returns) are tinted. A price or a balance
        // gets a brief tick in the direction it moved instead; the feedback stays on the number itself.
        if (format !== 'money' && format !== 'pct' && Number.isFinite(previous) && Number.isFinite(value) && value !== previous) {
          el.classList.remove('tick-up', 'tick-down');
          void el.offsetWidth;
          el.classList.add(value > previous ? 'tick-up' : 'tick-down');
        }
      }
      this.values.set(el, value);
      el.classList.toggle('quote-stale', stale);
      el.title = stale ? 'Stale quote — showing the last available value' : 'Live display estimate';
      if (format === 'money' || format === 'pct') {
        el.classList.toggle('up', value > 0); el.classList.toggle('down', value < 0);
      }
    }
    const badge = document.getElementById('live-price-health');
    if (badge) {
      const quotes = (this.packet?.symbols || []).map((s) => this.quote(s));
      const fresh = quotes.filter((q) => q && !q.stale);
      const age = fresh.length ? Math.max(...fresh.map((q) => q.age_ms)) : null;
      const state = !this.transport ? 'RECONNECTING' : !fresh.length ? 'STALE' : fresh.length < quotes.length ? 'PARTIAL' : 'LIVE';
      setText(badge, state + (age === null ? '' : ' · quote age ' + (age < 1000 ? Math.round(age) + ' ms' : (age / 1000).toFixed(1) + ' s')));
      badge.className = 'pill ' + (state === 'LIVE' ? 'up' : 'warn');
    }
  },
  wire() {
    if (this.wired) return;
    this.wired = true;
    Stream.on('prices', (p) => this.accept(p));
    Stream.onStatus((s) => { this.transport = s.status === 'LIVE'; this.schedule(); });
    setInterval(() => this.schedule(), 500); // Freshness must decay even when packets stop.
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { this.schedule(); (typeof Arena !== 'undefined' ? Arena : V6Home).load(); } });
  },
};
