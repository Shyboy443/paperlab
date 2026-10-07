/* Bitcoin breakout uses the Competition's bot page, charts, tables and authenticated controls. */
'use strict';
const BreakoutUI = {
  busy: false, snapshot: null,
  controls(d) {
    this.snapshot = d.breakout;
    const s = this.snapshot;
    if (!s) return null;
    if (!window.PAPERLAB_PRIVATE) return h('p', { class: 'sub' },
      'Sign in to start, pause or close this bot. ',
      h('a', { class: 'btn ghost', href: '/?p=video&bot=BTC-4H-BREAKOUT#bots/bots', text: 'Open Competition controls' }));
    let form = $('#breakout-controls');
    if (!form) {
      const field = (name, label, value, min, max, step) => h('label', null, label,
        h('input', { type: 'number', name, value, min, max, step, required: true }));
      form = h('form', { id: 'breakout-controls', class: 'breakout-controls', onsubmit: (e) => {
        e.preventDefault();
        const f = e.currentTarget.elements;
        this.act('start', { balance: Number(f.balance.value), allocation: Number(f.allocation.value) / 100,
          fee_bps: Number(f.fee_bps.value), slippage_bps: Number(f.slippage_bps.value) });
      } },
      h('div', { class: 'controls-row' }, field('balance', 'Starting balance (USDT)', s.config.initial_cash, 10, 10000000, 'any'),
        field('allocation', 'Cash per entry (%)', s.config.allocation * 100, .01, 100, 'any'),
        field('fee_bps', 'Fee per side (bps)', s.config.fee_bps, 0, 100, 'any'),
        field('slippage_bps', 'Slippage per side (bps)', s.config.slippage_bps, 0, 100, 'any')),
      h('div', { class: 'chips' }, h('button', { type: 'submit', name: 'start', class: 'btn', text: 'Start bot' }),
        h('button', { type: 'button', name: 'pause', class: 'btn ghost', text: 'Pause entries', onclick: () => this.act('pause') }),
        h('button', { type: 'button', name: 'flatten', class: 'btn ghost', text: 'Close position', onclick: () => this.act('flatten') }),
        h('button', { type: 'button', name: 'reset', class: 'btn ghost', text: 'Reset wallet', onclick: () => {
          if (confirm('Archive this bot’s history and reset its wallet?')) this.act('reset');
        } })),
      h('p', { class: 'sub', 'data-note': '' }), h('p', { class: 'err', role: 'status', 'data-feedback': '' }));
    }
    const f = form.elements;
    f.start.disabled = this.busy || s.enabled || !s.can_start;
    f.start.textContent = s.enabled ? 'Bot running' : 'Start bot';
    f.pause.disabled = this.busy || !s.enabled;
    f.flatten.disabled = this.busy || !s.qty;
    f.reset.disabled = this.busy || s.enabled || !!s.qty;
    for (const name of ['balance', 'allocation', 'fee_bps', 'slippage_bps']) f[name].disabled = this.busy || s.enabled || s.has_fills;
    form.querySelector('[data-note]').textContent = s.has_fills
      ? 'Pause entries and close the position, then reset to change wallet settings. Pausing keeps automatic exits active.'
      : 'Runs on live Bitcoin prices with simulated fills. Start waits for the next completed 4h candle.';
    return form;
  },
  async act(action, body = {}) {
    if (this.busy) return;
    this.busy = true;
    const form = $('#breakout-controls');
    const feedback = form.querySelector('[data-feedback]');
    feedback.textContent = '';
    form.querySelectorAll('button,input').forEach((x) => { x.disabled = true; });
    try {
      await post('/api/video-breakout/' + action, body);
      if (action === 'reset') form.remove();
      toast(action === 'start' ? 'Bitcoin breakout started' : action === 'pause' ? 'New entries paused' : action === 'flatten' ? 'Position closed' : 'Wallet reset');
    } catch (e) { feedback.textContent = e.message; }
    finally { this.busy = false; await Arena.load(); }
  },
  details(d) {
    const s = d.breakout;
    if (!s) return null;
    const table = (heading, fields, rows, empty) => {
      const t = h('table', { class: 'tbl' });
      renderTable(t, fields, rows, { empty });
      return h('div', null, h('h3', { text: heading }), h('div', { class: 'tablewrap' }, t));
    };
    const when = (ms) => ms ? new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : DASH;
    return h('div', { class: 'breakout-details' },
      h('p', { class: 'sub', text: s.rules }),
      h('div', { class: 'tiles six' }, tile('Bitcoin price', aPrice(s.quote?.price)), tile('Entry price', aPrice(d.positions?.[0]?.entry)),
        tile('7-day entry high', aPrice(s.entry_high)), tile('3-day exit low', aPrice(s.exit_low)),
        tile('cash · USDT', aPrice(s.cash)), tile('Bitcoin held', Number(s.qty).toFixed(8))),
      h('p', { class: 'sub', text: s.execution + (s.quote ? ' Last quote: ' + when(s.quote.ts) + (s.quote_age_seconds > 60 ? ' · stale' : '') : ' Waiting for prices.') }),
      this.controls(d),
      table('Fills', [{ h: 'time', cell: (f) => when(f.observed_ms || f.fill_time) },
        { h: 'side', cell: (f) => f.side }, { h: 'BTC', cell: (f) => f.qty.toFixed(8) },
        { h: 'price', cell: (f) => aPrice(f.price) }, { h: 'fee · USDT', cell: (f) => f.fee.toFixed(3) }], d.fills || [], 'No fills yet. Start the bot to wait for its next breakout.'),
      table('Signals', [{ h: '4h close', cell: (x) => when(x.signal_time) }, { h: 'action', cell: (x) => x.side },
        { h: 'outcome', cell: (x) => x.expired ? 'Expired: missed open' : x.canceled ? 'Canceled' : x.suppressed || x.reason || 'Next-open signal' }], d.signals || [], 'No qualifying signals yet.'));
  },
};
