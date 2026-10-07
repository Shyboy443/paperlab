/* PaperLab - PROVIDERS & LIVE (private dashboard only; never loaded by the public page).

   Providers: Bybit and Binance (testnet / mainnet) and Alpaca (paper / live). Keys can be typed in here: the server
   checks them with a read-only account call and keeps them only if accepted, ENCRYPTED; they are never shown again.
   Each card shows where its keys come from, the balance, the mainnet switch and the round trip that unlocks mainnet.
   Live mirrors: every go-live, its amount, limits, live position, realized PnL and a STOP button.
   GO LIVE (on a QUALIFIED V8 bot in Bots): a form for exchange, network, amount, risk and loss limits, confirmed by
   typing "GO LIVE <bot>". The server re-checks everything; the kill switch stops every mirror. */
'use strict';

window.PAPERLAB_PRIVATE = true;

const LiveMirror = {
  data: null,
  async load() {
    const grid = $('#mirror-grid');
    if (!grid) return;
    try {
      const [prov, mir] = await Promise.all([api('/api/mirror/providers'), api('/api/mirror/mirrors')]);
      this.data = { prov, mir };
    } catch (e) {
      clear(grid).append(h('div', { class: 'card' }, h('p', { class: 'err', text: 'live mirror unavailable: ' + e.message })));
      return;
    }
    this.render(grid);
  },
  render(grid) {
    const { prov, mir } = this.data;
    const when = (ts) => new Date(ts).toISOString().slice(0, 16).replace('T', ' ');
    const cards = (prov.providers || []).map((p) => {
      const main = p.network === 'mainnet', stocks = p.market === 'stocks', data = p.market === 'data';
      const bal = p.balance || {};
      const src = p.key_source === 'dashboard' ? 'saved here, encrypted on the server' + (p.key_saved_ts ? ' · ' + when(p.key_saved_ts) + ' UTC' : '')
        : p.key_source === 'railway' ? 'from Railway variables (' + p.key_env.join(' + ') + ')' : 'not set';
      const canTrip = !main && p.keys_configured && !stocks && !data;
      const last = (prov.checks || []).find((c) => c.exchange === p.exchange && c.network === 'testnet');
      const trip = p.testnet_verified_ts && (!last || last.ok) ? h('b', { class: 'up', text: '✓ passed ' + when(p.testnet_verified_ts) + ' UTC' })
        : last && !last.ok ? h('span', { class: 'down', text: '✗ FAILED ' + when(last.ts) + ' UTC — ' + ((last.detail || {}).error || 'see the live log') })
          : stocks ? 'arrives with the V9 stock bots' : 'not yet — press "Run testnet round trip" on the ' + p.exchange + ' testnet card';
      return h('div', { class: 'card c6 provider ' + (main ? 'mainnet' : 'testnet') },
        h('div', { class: 'card-head' }, h('h2', { text: p.label }),
          pill(p.keys_configured ? 'KEYS SET' : 'NO KEYS', p.keys_configured ? 'up' : 'ghost')),
        kvList({
          market: p.exchange === 'telegram' ? 'notifications: every paper trade, TP hit and close (never trades)'
            : data ? 'data source for the V10 scout (never trades)' : stocks ? 'US stocks & ETFs' : 'crypto USDT perpetuals',
          keys: src,
          [data ? 'access' : 'balance']: p.keys_configured ? (bal.ok ? (data ? bal.access || 'OK' : bal.available + ' ' + (bal.currency || 'USDT') + ' available (equity ' + bal.wallet + ')') : 'error: ' + (bal.error || '?')) : DASH,
          ...(p.telegram ? this.telegramRows(p, bal) : {}),
          ...(data ? {} : { [stocks ? 'paper round trip' : 'testnet round trip']: trip }),
          ...(main ? { 'mainnet switch': p.mainnet_switch ? 'LIVE_MIRROR_MAINNET_ENABLED=true' : 'off (LIVE_MIRROR_MAINNET_ENABLED)',
            'real money': p.mainnet_allowed ? 'UNLOCKED' : 'locked' } : {}),
        }),
        this.keyForm(p),
        p.telegram && p.telegram.linked ? h('button', { type: 'button', class: 'btn', text: 'Send test message',
          onclick: async (ev) => {
            ev.target.disabled = true;
            try { await post('/api/mirror/telegram/test', {}); toast('test message sent — check Telegram'); }
            catch (e) { toast(e.message, 'err'); }
            ev.target.disabled = false;
          } }) : null,
        canTrip ? h('button', { type: 'button', class: 'btn', text: 'Run testnet round trip',
          title: 'opens the smallest ETHUSDT long with test money, places a stop on the exchange, closes it and checks the account is flat',
          onclick: async (ev) => {
            const b = ev.target;
            b.disabled = true; b.textContent = 'Running the round trip… (up to a minute)';
            try { const r = await post('/api/mirror/providers/' + p.exchange + '/testnet/verify', {}); toast(r.ok ? 'testnet round trip passed' : 'round trip failed: ' + (r.error || 'see log'), r.ok ? '' : 'err'); }
            catch (e) { toast(e.message, 'err'); }
            this.load();
          } }) : null);
    });
    const t = h('table', { class: 'tbl' });
    renderTable(t, [
      { h: 'bot', cell: (m) => h('span', { class: 'sid', text: m.bot_key }) },
      { h: 'where', cell: (m) => m.exchange + ' ' + m.network },
      { h: 'status', cell: (m) => pill(m.status, m.status === 'ARMED' ? (m.network === 'mainnet' ? 'down' : 'up') : 'muted') },
      { h: 'amount', cell: (m) => fmtNum(m.amount_usdt, 2), num: true },
      { h: 'risk', cell: (m) => (m.risk_pct * 100).toFixed(2) + '%', num: true },
      { h: 'position', cell: (m) => (m.position ? m.position.side + ' ' + m.position.qty + ' @ ' + fmtNum(m.position.entry, 4) : DASH) },
      { h: 'trades', cell: (m) => String(m.trades || 0), num: true },
      { h: 'realized', cell: (m) => h('span', { class: signClass(m.realized), text: fmtNum(m.realized, 4) }), num: true },
      { h: 'limits', cell: (m) => 'day ' + m.max_daily_loss + ' · total ' + m.max_total_loss },
      { h: '', cell: (m) => (m.status === 'ARMED' || m.position ? h('button', { type: 'button', class: 'btn danger', text: 'STOP',
        onclick: async () => { if (!confirm('Stop ' + m.bot_key + ' and close its live position?')) return;
          try { await post('/api/mirror/' + m.id + '/stop', {}); toast('stopped'); } catch (e) { toast(e.message, 'err'); } this.load(); } }) : null) },
    ], mir.mirrors || [], { empty: 'nothing is live' });
    const logs = h('ul', { class: 'feed' }, (mir.log || []).slice(0, 30).map((l) => h('li', { class: 'act' },
      h('span', { class: 'act-time', text: new Date(l.ts).toISOString().slice(11, 19) }), h('span', { class: 'act-icon', text: '·' }),
      h('span', { class: 'act-body' }, h('b', { text: l.kind + ' ' }), h('span', { class: 'sub', text: JSON.stringify(l.detail).slice(0, 220) })))));
    clear(grid).append(
      h('div', { class: 'card c12 banner warn' }, h('b', { text: 'REAL ORDERS. ' }),
        'A live mirror copies a QUALIFIED paper bot onto an exchange account. Testnet first; mainnet stays locked until you set ',
        h('code', { text: 'LIVE_MIRROR_MAINNET_ENABLED=true' }), ' and that exchange passes a testnet round trip. The kill switch stops every mirror. Max per mirror: ' + prov.max_amount_usdt + ' USDT.'),
      ...cards,
      h('div', { class: 'card c12' }, h('div', { class: 'card-head' }, h('h2', { text: 'Live mirrors' })), h('div', { class: 'tablewrap' }, t)),
      h('div', { class: 'card c12' }, h('div', { class: 'card-head' }, h('h2', { text: 'Live log' })), logs));
  },

  /* Enter / replace / remove a provider's keys. The server checks them with a read-only account call and keeps them
     only if accepted, encrypted; nothing is ever sent back, and the inputs are wiped after every attempt. */
  keyForm(p) {
    const base = '/api/mirror/providers/' + p.exchange + '/' + p.network + '/keys';
    if (!p.can_save_keys) {
      return h('p', { class: 'sub', text: 'Key entry needs DASHBOARD_PASSWORD on the server; until then set ' + p.key_env.join(' + ') + ' in Railway variables.' });
    }
    const [kLabel, sLabel] = p.exchange === 'reddit' ? ['app client ID', 'app secret']
      : p.exchange === 'telegram' ? ['bot token (from @BotFather)', 'chat id (optional: or send /start to your bot)']
        : ['API key', 'API secret'];
    const keyIn = h('input', { type: 'password', placeholder: kLabel, autocomplete: 'off', spellcheck: 'false' });
    const secIn = h('input', { type: 'password', placeholder: sLabel, autocomplete: 'new-password', spellcheck: 'false' });
    const err = h('p', { class: 'err' });
    const form = h('div', { class: 'key-form', hidden: true },
      h('label', { class: 'field' }, h('span', { text: kLabel }), keyIn),
      h('label', { class: 'field' }, h('span', { text: sLabel }), secIn),
      p.exchange === 'reddit' ? h('p', { class: 'sub', text: 'Create a free "script" app at reddit.com/prefs/apps (any name; redirect uri http://localhost:8080). The client ID is the short code under the app name.' }) : null,
      p.exchange === 'telegram' ? h('p', { class: 'sub', text: 'Paste the token @BotFather gave you, save, then open your bot in Telegram and send /start: that chat gets every paper trade (TP hits and closes reply to the trade). Leave the chat id empty unless you know it.' }) : null,
      err,
      h('button', { type: 'button', class: 'btn', text: 'Save & verify', onclick: async (ev) => {
        ev.target.disabled = true; setText(err, '');
        try {
          const r = await post(base, { key: keyIn.value, secret: secIn.value });
          toast(p.label + ' keys saved · ' + ((r.balance || {}).available ?? '?') + ' ' + ((r.balance || {}).currency || '') + ' available');
          keyIn.value = secIn.value = '';
          this.load();
        } catch (e) { keyIn.value = secIn.value = ''; setText(err, e.message); ev.target.disabled = false; }
      } }));
    const toggle = h('button', { type: 'button', class: 'chip', text: p.keys_configured ? 'Replace keys' : 'Enter keys',
      onclick: () => { form.hidden = !form.hidden; if (!form.hidden) keyIn.focus(); } });
    const remove = p.key_source === 'dashboard' ? h('button', { type: 'button', class: 'chip', text: 'Remove saved keys', onclick: async () => {
      if (!confirm('Remove the ' + p.label + ' keys saved in the dashboard?')) return;
      try { await post(base + '/clear', {}); toast('removed'); } catch (e) { toast(e.message, 'err'); }
      this.load();
    } }) : null;
    return h('div', { class: 'key-box' },
      p.vault_locked ? h('p', { class: 'err', text: 'Saved keys could not be opened (the dashboard password changed) — enter them again.' }) : null,
      h('div', { class: 'chips' }, toggle, remove), form);
  },

  /* the Telegram card: is a chat linked, and is anything being delivered */
  telegramRows(p, bal) {
    const t = p.telegram, when = (ts) => new Date(ts).toISOString().slice(11, 16) + ' UTC';
    const bot = ((bal.access || '').match(/@(\w+)/) || [])[1];
    const chat = !p.keys_configured ? DASH : t.linked ? h('b', { class: 'up', text: '✓ linked' })
      : h('span', { class: 'down' }, 'NOT LINKED — open ',
          bot ? h('a', { href: 'https://t.me/' + bot, target: '_blank', rel: 'noopener', text: '@' + bot }) : 'your bot',
          ' in Telegram and send /start');
    const sent = t.sent + ' sent' + (t.replies ? ' (' + t.replies + ' replies)' : '') +
      (t.last_sent_ts ? ' · last ' + when(t.last_sent_ts) : '') + (t.errors ? ' · ' + t.errors + ' errors' : '');
    return { chat, 'messages since restart': t.last_error && t.errors ? sent + ' — ' + t.last_error : sent };
  },

  /* every mirror, fetched at most once per 8 s however many cards ask */
  mirrorsCached() {
    const now = Date.now();
    if (!this._mc || now - this._mc.t > 8000) this._mc = { t: now, p: api('/api/mirror/mirrors').then((d) => d.mirrors || []) };
    return this._mc.p;
  },

  /* the home card's strip: where this bot is live and what its live copy holds (empty when it is not mirrored) */
  cardStrip(x) {
    const strip = h('div', { class: 'rs-live', hidden: true });
    this.mirrorsCached().then((all) => {
      const m = all.find((y) => y.bot_key === x.key && y.program === x.program && (y.status === 'ARMED' || y.position));
      if (!m) return;
      const real = m.network === 'mainnet', p = m.position, coin = String(m.symbol || '').replace(/USDT$/, '');
      const holding = p ? 'holding ' + p.qty + ' ' + coin + ' ' + (p.side === 'long' ? 'long' : 'short') + ' · stop on ' + m.exchange
        : (x.positions || []).length ? 'flat — this trade is not copied (see the live log); it copies the next one' : 'flat — copies the next trade';
      const done = (m.trades || 0) + ' copied · ' + (isNum(m.realized) ? (m.realized >= 0 ? '+' : '') + m.realized.toFixed(3) : '0.000') + ' USDT';
      strip.className = 'rs-live ' + (real ? 'real' : 'test');
      strip.title = 'open Providers & Live: the mirror, its live log and its STOP button';
      strip.onclick = (e) => { e.stopPropagation(); Nav.go('system', 'live-mirror'); };
      clear(strip).append(
        h('div', { class: 'rs-live-top' }, h('b', { text: '● LIVE · ' + m.exchange.toUpperCase() + (real ? ' · REAL MONEY' : ' · TESTNET') }),
          h('span', { text: done })),
        h('div', { class: 'rs-live-sub', text: holding }));
      strip.hidden = false;
    }).catch(() => {});
    return strip;
  },

  /* the bot page's slot: "● LIVE · binance testnet · ARMED" when this bot is mirrored, else the GO LIVE button */
  goLiveSlot(bot) {
    const slot = h('span', { class: 'golive-slot' });
    const button = () => h('button', { type: 'button', class: 'btn danger', text: 'GO LIVE', onclick: () => this.openForm(bot) });
    api('/api/mirror/mirrors').then((d) => {
      const m = (d.mirrors || []).find((x) => x.bot_key === bot.key && (x.status === 'ARMED' || x.position));
      if (!m) { clear(slot).append(button()); return; }
      const real = m.network === 'mainnet';
      const pos = m.position ? ' · ' + m.position.side + ' open' : ' · waiting for its next trade';
      clear(slot).append(h('button', { type: 'button', class: 'live-badge ' + (real ? 'real' : 'test'),
        title: 'open Providers & Live: the mirror, its live log and its STOP button',
        text: '● LIVE · ' + m.exchange + ' ' + (real ? 'REAL MONEY' : 'testnet') + ' · ' + (m.trades || 0) + ((m.trades || 0) === 1 ? ' trade' : ' trades') + pos,
        onclick: () => Nav.go('system', 'live-mirror') }));
    }).catch(() => clear(slot).append(button()));
    return slot;
  },

  /* why a crypto provider cannot take a live mirror yet (null: it can) */
  blocker(p) {
    if (!p.keys_configured) return 'no keys yet — add them in Providers & Live';
    if (p.network !== 'mainnet') return null;
    const name = p.exchange.charAt(0).toUpperCase() + p.exchange.slice(1);
    const steps = [];
    if (!p.testnet_verified_ts) steps.push('pass the ' + name + ' testnet round trip');
    if (!p.mainnet_switch) steps.push('turn on the mainnet switch (LIVE_MIRROR_MAINNET_ENABLED)');
    if (steps.length) return 'locked — ' + steps.join(', then ');
    return p.mainnet_allowed ? null : 'locked';
  },

  /* why a V6 bot may go live: its two-year backtest (app/live/v6_golive.py), with the honest caveats */
  basis(bot) {
    const g = bot.golive, b = g && g.backtest;
    if (!b) return null;
    const pct = (x) => (isNum(x) ? (x >= 0 ? '+' : '') + x.toFixed(1) + '%' : DASH);
    return h('div', { class: 'golive-basis' },
      h('p', null, h('b', { text: 'Two-year backtest (Oct 2024 – Oct 2026): ' }),
        pct(b.return_pct) + ' on a 20 USDT book, ' + b.trades + ' trades, profit factor ' + (isNum(b.profit_factor) ? b.profit_factor.toFixed(2) : DASH)
        + ', worst drop ' + (isNum(b.max_dd_pct) ? '−' + Math.abs(b.max_dd_pct).toFixed(1) + '%' : DASH) + ' (year 1 ' + fmtNum(b.year1, 2) + ', year 2 ' + fmtNum(b.year2, 2) + ' USDT).'),
      h('p', { class: 'sub', text: 'A backtest is not a promise: this strategy trades a few times a month, its settings were designed on part of '
        + 'this period, and its live paper record is still short. Start small.' }));
  },

  /* the GO LIVE form, opened from a QUALIFIED V8 bot or an eligible V6.2 / V6.6 bot: every crypto venue is listed, the ones not ready yet greyed out
     with the reason, so the operator sees exactly what is missing (the server re-checks everything anyway) */
  async openForm(bot) {
    let prov = [];
    try { prov = (await api('/api/mirror/providers')).providers || []; } catch (e) { toast(e.message, 'err'); return; }
    const crypto = prov.filter((p) => p.market === 'crypto');                   // V6 / V8 trade crypto
    const usable = crypto.filter((p) => !this.blocker(p));
    const overlay = h('div', { class: 'modal-overlay' });
    const field = (label, input) => h('label', { class: 'field' }, h('span', { text: label }), input);
    const avail = (p) => (p && p.balance && p.balance.ok ? p.balance.available : null);
    const where = h('select', null, crypto.map((p) => {
      const why = this.blocker(p), bal = avail(p);
      const short = !why ? (isNum(bal) ? fmtNum(bal, 2) + ' USDT available' : 'ready') : !p.keys_configured ? 'no keys' : 'locked';
      return h('option', { value: p.exchange + '|' + p.network, disabled: !!why, text: p.label + ' — ' + short });   // details in the box above
    }));
    if (usable.length) where.value = usable[0].exchange + '|' + usable[0].network;
    const chosen = () => crypto.find((p) => p.exchange + '|' + p.network === where.value);
    const amount = h('input', { type: 'number', min: '1', step: '1', value: '20' });
    const fitAmount = () => { const b = avail(chosen()); if (isNum(b) && b > 0) amount.value = String(Math.max(1, Math.floor(Math.min(b, 50)))); };
    where.addEventListener('change', fitAmount);
    fitAmount();
    const risk = h('input', { type: 'number', min: '0.1', max: '2', step: '0.1', value: '1' });
    const daily = h('input', { type: 'number', min: '0.1', step: '0.1', value: '5' });
    const total = h('input', { type: 'number', min: '0.1', step: '0.1', value: '12.5' });
    const confirmIn = h('input', { type: 'text', placeholder: 'GO LIVE ' + bot.key, autocomplete: 'off' });
    const err = h('p', { class: 'err' });
    const submit = h('button', { type: 'button', class: 'btn danger', text: 'GO LIVE', disabled: !usable.length, onclick: async () => {
      const [exchange, network] = String(where.value || '').split('|');
      submit.disabled = true;
      try {
        await post('/api/mirror/start', { program: bot.program || 'v8', bot_key: bot.key, exchange, network, amount_usdt: Number(amount.value),
          risk_pct: Number(risk.value) / 100, max_daily_loss: Number(daily.value), max_total_loss: Number(total.value), confirm: confirmIn.value });
        toast(bot.key + ' is live on ' + exchange + ' ' + network + ' — it copies the bot\'s NEXT trade; follow it in Providers & Live');
        overlay.remove();
        if (typeof Arena !== 'undefined' && Arena.botKey === bot.key && Arena.refreshInPlace) Arena.refreshInPlace();
      } catch (e) { setText(err, e.message); submit.disabled = false; }
    } });
    const todo = usable.length ? null : h('div', { class: 'golive-todo' },
      h('p', { class: 'err', text: 'Not ready to go live yet. Every exchange still has a step open:' }),
      h('ul', null, crypto.filter((p) => p.keys_configured || p.network === 'testnet')
        .sort((x, y) => Number(y.keys_configured && y.network === 'mainnet') - Number(x.keys_configured && x.network === 'mainnet'))
        .map((p) => h('li', null, h('b', { text: p.label + ': ' }), this.blocker(p) || 'ready'))),
      h('button', { type: 'button', class: 'chip', text: 'Open Providers & Live →', onclick: () => { overlay.remove(); Nav.go('system', 'live-mirror'); } }));
    overlay.append(h('div', { class: 'card modal' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Go live · ' + bot.key }), h('button', { type: 'button', class: 'chip', text: 'cancel', onclick: () => overlay.remove() })),
      todo,
      this.basis(bot),
      field('exchange', where), field('amount (USDT)', amount), field('risk per trade (%, max 2)', risk),
      field('max daily loss (USDT)', daily), field('max total loss (USDT, then it stops)', total),
      field('type "GO LIVE ' + bot.key + '" to confirm', confirmIn), err, submit));
    document.body.append(overlay);
  },
};
