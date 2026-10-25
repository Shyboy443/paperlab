'use strict';
/* PaperLab - IBS BOT panel (Strategy check page, "IBS live bot" tab): round 3's frozen QQQ IBS rule on Alpaca with a QQQ
   and a TQQQ book (docs/IBS_LIVE_BOT.md). Everyone sees the read-only view (GET /api/public/ibs-bot): the decision two
   minutes before each close, the official record, the shadow books next to buy and hold. On the PRIVATE dashboard only
   (app.js's Auth exists) the operator also gets the switch: capital per book, loss limit, the typed phrase, switch off.
   Every private call goes through app.js's api()/post() (Basic auth + X-PaperLab). Keys are never shown or entered here:
   they live in System -> Providers & Live. */
const IbsPanel = {
  data: null, host: null, timer: null, msg: '', busy: false,
  priv() { return typeof Auth !== 'undefined' && Auth.get() != null; },
  esc(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); },
  n(v, d = 2) { return v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); },
  pct(v, d = 2) { return v == null || !Number.isFinite(Number(v)) ? '—' : `<span class="${v >= 0 ? 'pos' : 'neg'}">${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}%</span>`; },
  when(t) { return t ? new Date(t).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'; },

  async mount(host) {
    this.host = host;
    host.innerHTML = '<div class="card"><p class="sub">loading the IBS bot…</p></div>';
    await this.refresh();
    clearInterval(this.timer);
    this.timer = setInterval(() => { if (this.host && document.body.contains(this.host)) this.refresh(); else clearInterval(this.timer); }, 30000);
  },
  async refresh() {
    try {
      if (this.priv()) this.data = await api('/api/ibs-bot');
      else {
        const r = await fetch('/api/public/ibs-bot', { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        this.data = await r.json();
      }
      // never wipe a half-filled switch-on form under the operator's hands
      const typing = this.host && [...this.host.querySelectorAll('input')].some((i) => i.value || i === document.activeElement);
      if (!this.busy && !typing) this.render();
    } catch (e) {
      if (this.host) this.host.innerHTML = `<div class="card"><p class="err">IBS bot unavailable: ${this.esc(e.message)}</p></div>`;
    }
  },

  chart(curve) {
    if (!curve || curve.length < 2) return '<p class="sub">The curve starts after the first two recorded closes.</p>';
    const W = 640, H = 160, P = 6, keys = [['shadow_tqqq_pct', 'var(--accent, #e8b04b)', 'IBS TQQQ'], ['hold_tqqq_pct', '#888', 'hold TQQQ'],
      ['shadow_qqq_pct', '#4fb3a9', 'IBS QQQ'], ['hold_qqq_pct', '#5a6b7b', 'hold QQQ']];
    const all = curve.flatMap((r) => keys.map(([k]) => r[k])).concat([0]);
    const lo = Math.min(...all), hi = Math.max(...all), sx = (i) => P + (i / (curve.length - 1)) * (W - 2 * P), sy = (v) => H - P - ((v - lo) / (hi - lo || 1)) * (H - 2 * P);
    const lines = keys.map(([k, c]) => `<polyline fill="none" stroke="${c}" stroke-width="${k.startsWith('shadow') ? 2 : 1.2}" ${k.startsWith('hold') ? 'stroke-dasharray="4 3"' : ''} points="${curve.map((r, i) => sx(i).toFixed(1) + ',' + sy(r[k]).toFixed(1)).join(' ')}"/>`).join('');
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Shadow books vs buy and hold">
      <line x1="${P}" x2="${W - P}" y1="${sy(0)}" y2="${sy(0)}" stroke="var(--line, #333)"/>${lines}</svg>
      <p class="sub">${keys.map(([, c, l]) => `<span style="color:${c}">■</span> ${l}`).join(' · ')} — % since ${this.esc(curve[0].day)}</p>`;
  },

  render() {
    const d = this.data, host = this.host;
    if (!d || !host) return;
    const L = d.live || {}, S = d.shadow || {}, sch = d.schedule || {}, last = (d.curve || []).slice(-1)[0];
    const on = ['ARMED', 'CLOSING', 'HALTED'].includes(L.status);
    const net = L.network === 'mainnet' ? 'Alpaca LIVE (real money)' : L.network === 'testnet' ? 'Alpaca paper' : '';
    const rows = (d.decisions || []).map((x) => {
      const e = x.estimate || {}, o = x.official || {};
      const act = e.error ? `<span class="neg">no data</span>` : (e.shadow ? e.shadow['IBS-QQQ'] : (o.missed ? 'missed' : '—'));
      return `<tr><td>${this.esc(x.day)}</td><td>${e.ibs == null ? '—' : e.ibs.toFixed(3)}</td><td>${o.ibs == null ? '—' : o.ibs.toFixed(3)}</td>
        <td>${this.esc(act)}</td><td>${o.slip ? '<span class="neg">slip</span>' : o.slip === false ? 'ok' : '—'}</td>
        <td>${x.live_orders ? x.live_orders + ' order(s)' : this.esc(x.live_status || '')}</td></tr>`;
    }).join('');
    const trades = (list, money) => (list || []).slice(0, 30).map((t) => `<tr><td>${this.esc(t.book)}</td><td>${this.esc(t.entry_day)}</td><td>${this.esc(t.exit_day)}</td>
      <td>${this.n(t.entry_px)}</td><td>${this.n(t.exit_px)}</td><td>${this.pct(t.ret_pct)}</td>${money ? `<td>${this.n(t.pnl)}</td>` : ''}<td class="sub">${this.esc(t.reason)}</td></tr>`).join('');
    const books = (b, money) => Object.entries(b || {}).map(([k, x]) => `<tr><td>${k}</td><td>${x.holding ? `holding since ${this.esc(x.entry_day)} at ${this.n(x.entry_px)}` : 'flat'}</td>
      <td>${this.pct(x.realized_pct)}</td>${money ? `<td>${this.n(x.capital)}</td><td>${this.n(x.cash)}</td><td>${x.qty ? x.qty.toFixed(6) : '0'}</td>` : ''}</tr>`).join('');
    host.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>IBS bot · QQQ + TQQQ on Alpaca</h3>
          <span class="${L.status === 'ARMED' ? 'pos' : L.status === 'HALTED' ? 'neg' : 'sub'}"><b>${this.esc(L.status || 'OFF')}</b>${net ? ' · ' + net : ''}</span></div>
        <p class="sub">Round 3's frozen rule (freeze ${this.esc(d.rule?.freeze_hash)}): QQQ IBS = (close − low) / (high − low). Flat and IBS &lt; 0.10 → buy at the close;
          holding and the close above the previous session's high → sell at the close. Long only, no stop. Both books follow the QQQ signal.</p>
        <p>Next decision: <b>${this.when(sch.decision_at)}</b>${sch.decision_at ? ` (${new Date(sch.decision_at).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' })} New York)` : ''}${sch.early_close ? ' <span class="neg">(early close)</span>' : ''} · official record ${this.when(sch.record_at)}
          · feed ${this.esc(d.execution?.feed)} · ${d.data_ok ? 'data ok' : '<span class="neg">data problem</span>'} · checked ${this.when(d.heartbeat)}</p>
        ${this.priv() && d.error ? `<p class="err">${this.esc(d.error)}</p>` : ''}
      </div>
      ${this.priv() ? this.controls(d, on) : (on ? `<div class="card"><h4>Live books</h4><table class="tbl"><tr><th>Book</th><th>Position</th><th>Realized</th></tr>${books(L.books, false)}</table></div>` : '')}
      <div class="card"><h4>Shadow books (paper, every decision, ${this.n(S.capital_each, 0)} USD each, since ${this.esc(S.since || 'the first decision')})</h4>
        <p>IBS QQQ ${this.pct(S.equity_pct?.['IBS-QQQ'])} · IBS TQQQ ${this.pct(S.equity_pct?.['IBS-TQQQ'])}${last ? ` · buy and hold QQQ ${this.pct(last.hold_qqq_pct)} · TQQQ ${this.pct(last.hold_tqqq_pct)}` : ''}</p>
        ${this.chart(d.curve)}
        <table class="tbl"><tr><th>Book</th><th>Position</th><th>Realized</th></tr>${books(S.books, false)}</table></div>
      <div class="card"><h4>Decisions</h4><p class="sub">Estimated two minutes before the close; official from the daily candle. "Slip": the two disagree.</p>
        <div style="overflow-x:auto"><table class="tbl"><tr><th>Session</th><th>IBS est.</th><th>IBS official</th><th>Rule</th><th>Check</th><th>Live</th></tr>${rows || '<tr><td colspan="6" class="sub">No decision yet: the first comes two minutes before the next close.</td></tr>'}</table></div></div>
      <div class="card"><h4>Trades</h4><div style="overflow-x:auto"><table class="tbl"><tr><th>Book</th><th>In</th><th>Out</th><th>Entry</th><th>Exit</th><th>Return</th>${this.priv() ? '<th>USD</th>' : ''}<th></th></tr>
        ${trades(L.trades, this.priv()) ? `<tr><td colspan="8"><b>Live</b></td></tr>${trades(L.trades, this.priv())}` : ''}
        ${trades(S.trades, false) ? `<tr><td colspan="8"><b>Shadow</b></td></tr>${trades(S.trades, false)}` : '<tr><td colspan="8" class="sub">No closed trade yet.</td></tr>'}</table></div></div>`;
    this.wire();
  },

  controls(d, on) {
    const L = d.live || {}, c = d.controls || {};
    const block = [];
    if (!c.keys?.mainnet) block.push('No Alpaca <b>live</b> keys on the server: add them in System → Providers &amp; Live → "Alpaca (real money)".');
    if (!c.mainnet_switch) block.push('Real money is locked on the server (LIVE_MIRROR_MAINNET_ENABLED).');
    if (c.other_user?.mainnet) block.push(this.esc(c.other_user.mainnet) + ' uses the live Alpaca account (one strategy per account).');
    if (!on) {
      return `<div class="card"><h4>Switch on</h4>
        <p class="sub">Starts flat; the first order can only come two minutes before the next close. Orders: QQQ and TQQQ only, fractional market orders, no margin.
          The loss limit (both books together, marked to market) sells everything and switches the bot off. TQQQ fell up to about 50% within single past trades.</p>
        ${block.length ? '<div class="ml-block">' + block.map((x) => `<div>${x}</div>`).join('') + '</div>' : ''}
        <div class="form-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px">
          <label>Account<select id="ibs-net"><option value="mainnet">Alpaca live (real money)</option><option value="testnet"${c.keys?.testnet ? '' : ' disabled'}>Alpaca paper</option></select></label>
          <label>QQQ book (USD)<input id="ibs-cq" type="number" min="0" step="1" placeholder="e.g. 1000"></label>
          <label>TQQQ book (USD)<input id="ibs-ct" type="number" min="0" step="1" placeholder="0 = unused"></label>
          <label>Loss limit (USD)<input id="ibs-ml" type="number" min="1" step="1"></label>
          <label>Type <b>${this.esc(c.confirm_phrase)}</b><input id="ibs-cf" type="text" autocomplete="off"></label>
        </div>
        <p class="sub">Total capital at most ${this.n(c.max_capital, 0)} USD on this server; each book 0 or at least ${this.n(c.min_book, 0)} USD.</p>
        <button id="ibs-on" class="btn primary" type="button">Switch on</button> <span id="ibs-msg" class="err">${this.esc(this.msg)}</span></div>`;
    }
    const acct = L.account || {};
    return `<div class="card"><h4>Live: ${this.esc(L.status)} · ${L.network === 'mainnet' ? 'Alpaca LIVE (real money)' : 'Alpaca paper'}</h4>
      ${L.reason ? `<p class="${L.status === 'HALTED' ? 'err' : 'sub'}">${this.esc(L.reason)}</p>` : ''}
      <p>Since ${this.when(L.armed_ts)} · QQQ book ${this.n(L.capital?.['IBS-QQQ'])} · TQQQ book ${this.n(L.capital?.['IBS-TQQQ'])} · loss limit ${this.n(L.max_loss)} USD
        · account cash ${this.n(acct.cash)} / equity ${this.n(acct.equity)}</p>
      <table class="tbl"><tr><th>Book</th><th>Position</th><th>Realized</th><th>Capital</th><th>Cash</th><th>Shares</th></tr>
        ${Object.entries(L.books || {}).map(([k, x]) => `<tr><td>${k}</td><td>${x.holding ? `since ${this.esc(x.entry_day)} at ${this.n(x.entry_px)}` : 'flat'}</td><td>${this.pct(x.realized_pct)}</td>
          <td>${this.n(x.capital)}</td><td>${this.n(x.cash)}</td><td>${x.qty ? x.qty.toFixed(6) : '0'}</td></tr>`).join('')}</table>
      <details><summary>Orders and journal</summary>
        <table class="tbl">${(L.orders || []).map((o) => `<tr><td>${this.esc(o.ts)}</td><td>${this.esc(o.book)}</td><td>${this.esc(o.side)}</td><td>${this.esc(o.status)}</td>
          <td>${o.filled_qty ?? ''}</td><td>${this.n(o.avg_px)}</td><td class="sub">${this.esc(o.reason || '')}</td></tr>`).join('')}</table>
        <table class="tbl">${(d.journal || []).map((j) => `<tr><td>${this.esc(j.ts)}</td><td>${this.esc(j.event)}</td><td class="sub">${this.esc(j.detail)}</td></tr>`).join('')}</table></details>
      <button id="ibs-off" class="btn danger" type="button">${L.status === 'HALTED' ? 'Switch off (positions stay for you to settle)' : 'Switch off and sell'}</button>
      <span id="ibs-msg" class="err">${this.esc(this.msg)}</span></div>`;
  },

  wire() {
    const $i = (id) => document.getElementById(id);
    const act = async (fn) => {
      this.busy = true; this.msg = '';
      try { const r = await fn(); if (r?.bot) this.data = r.bot; } catch (e) { this.msg = e.message; } finally { this.busy = false; this.render(); }
    };
    if ($i('ibs-on')) $i('ibs-on').onclick = () => act(() => post('/api/ibs-bot/start', {
      network: $i('ibs-net').value, capital_qqq: Number($i('ibs-cq').value || 0), capital_tqqq: Number($i('ibs-ct').value || 0),
      max_loss: Number($i('ibs-ml').value || 0), confirm: $i('ibs-cf').value }));
    if ($i('ibs-off')) $i('ibs-off').onclick = () => {
      if (!confirm('Switch the IBS bot off? Its QQQ/TQQQ positions are sold at market (at the next open if the market is closed).')) return;
      act(() => post('/api/ibs-bot/stop', { sure: true }));
    };
  },
};
