'use strict';
// Desk LIVE controls + linked accounts (MACD, Donchian). PRIVATE dashboard only: index.html loads this file, public.html
// does not. Every call goes through app.js's api()/post() (Basic auth + X-PaperLab). Keys are sent once to link an
// account and never come back (the server returns a fingerprint only). One factory instance per desk; element ids are
// prefixed per desk because both desk cards can be in the page at the same time.
function makeDeskLiveUI(desk, cfg) {
  let host = null, data = null, timer = null, busy = false, msg = '', loadErr = '', shown = '', bals = {};
  let pending = null, pendingAt = 0, tickTimer = null;          // 'on' | 'off' while the server works
  const P = `ml-${desk}-`;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n = (v, d = 2) => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
  const px = v => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: Number(v) < 1 ? 6 : Number(v) < 10 ? 4 : 2});
  const when = t => t ? new Date(t).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '—';
  const coin = s => String(s || '').replace(/USDT$/, '');
  const $l = id => document.getElementById(P + id);
  const isOn = () => ['ARMED', 'CLOSING', 'HALTED'].includes(data?.status);
  const balText = (id, long = true) => {
    const b = bals[id];
    if (!b) return '<span class="sub">loading…</span>';
    if (!b.ok) return `<span class="neg" title="${esc(b.error)}">unavailable</span>`;
    return `<b>${n(b.available)}</b> available` + (long ? ` · ${n(b.wallet)} wallet` + (Math.abs(b.upnl) >= 0.005 ? ` · <span class="${b.upnl >= 0 ? 'pos' : 'neg'}">${b.upnl >= 0 ? '+' : ''}${n(b.upnl)}</span> open P&amp;L` : '') : '');
  };
  const availLine = id => id && bals[id]?.ok ? `available ${n(bals[id].available)} USDT` : '';
  // what will stop a switch-on, shown before it is pressed (the server re-checks everything)
  function blockers(id) {
    const a = (data?.accounts || []).find(x => x.id === id), g = data?.mainnet || {}, out = [];
    if (!a) return '<p class="ml-block">Link a Binance account below first (one account per strategy).</p>';
    if (a.network === 'mainnet' && !g.switch) out.push('Mainnet is switched off on the server (LIVE_MIRROR_MAINNET_ENABLED).');
    if (a.network === 'mainnet' && !g.testnet_passed) out.push(`<b>Mainnet is locked until one Binance testnet round trip has passed on this server.</b><ol>
      <li>Get free testnet keys at <b>testnet.binancefuture.com</b> (log in, API Key tab).</li>
      <li>Below: <b>Link a Binance account</b>, network <b>testnet</b>, paste the key and secret, <b>Link account</b>.</li>
      <li>Press <b>Run testnet round trip</b> on that row (a tiny open, stop, close on testnet). Mainnet then unlocks for every bot.</li></ol>`);
    const b = bals[id];
    if (b?.ok && Number(b.available) < 6) out.push(`Only ${n(b.available)} USDT available in USD-M Futures on this account: Binance's minimum order is about 5 USDT plus fees.`);
    return out.length ? '<div class="ml-block">' + out.map(x => '<div>' + x + '</div>').join('') + '</div>' : '';
  }

  async function refresh(force = false) {
    const [v, b] = await Promise.allSettled([api(`/api/${desk}/live`), api('/api/accounts/balances')]);
    if (v.status === 'fulfilled') { data = v.value; loadErr = ''; }
    else { data = null; loadErr = v.reason?.status === 401 ? 'Log in to see the live controls.' : 'Unavailable: ' + v.reason?.message; }
    if (b.status === 'fulfilled') bals = b.value.balances || {};
    const sig = JSON.stringify([data, loadErr, msg, busy, bals]);
    if (force || sig !== shown) { shown = sig; render(); }       // keep the DOM (and any half-typed form) when nothing changed
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const typing = host && host.contains(document.activeElement) && /INPUT|SELECT/.test(document.activeElement.tagName);
      if (!busy && $l('modal')?.hidden !== false && !typing) await refresh();
      schedule();
    }, data ? 20000 : 5000);
  }

  function render() {
    if (!host) return;
    host.hidden = false;
    if (!data) { host.innerHTML = `<h3 class="dt-h3">Live trading</h3><p class="empty">${esc(loadErr || 'Loading…')}</p>`; return; }
    const on = isOn(), p = data.position, g = data.mainnet, r = data.rules || {};
    const free = (data.accounts || []).filter(a => !a.owner);
    const syms = r.symbols || [];
    host.innerHTML = `
      <div class="ml-head"><div><h3 class="dt-h3">Live trading · ${esc(cfg.title(data))}</h3>
        <p class="sub">${cfg.desc(data)} Every entry gets an exchange-side disaster stop ${n(r.disaster_stop_pct, 0)}% against it, plus your loss limit. It starts flat and only acts on ${esc(data.interval || '4h')} candles that close after you switch it on. Real money when the account is mainnet.</p></div>
        <label class="ml-switch ${(pending ? pending === 'on' : on) ? 'on' : ''} ${pending ? 'pending' : ''}" title="${pending ? 'working…' : on ? 'Switch off (asks first)' : 'Switch on'}"><input type="checkbox" id="${P}toggle" ${(pending ? pending === 'on' : on) ? 'checked' : ''} ${busy ? 'disabled' : ''}><span></span><b>${pending ? (pending === 'on' ? 'TURNING ON…' : 'TURNING OFF…') : esc(data.status)}</b></label></div>
      ${pending ? `<p class="ml-pending"><i class="ml-spin"></i>${pending === 'on'
        ? 'Switching on: checking the account on Binance (balance, open positions, open orders) and setting 1x isolated mode.'
        : 'Switching off: cancelling the disaster stop and closing any open position at market.'} <span class="sub" id="${P}secs">${Math.round((Date.now() - pendingAt) / 1000)} s</span> · this usually takes 5–15 s.</p>` : ''}
      ${msg ? `<p class="ml-msg ${msg.startsWith('Error') ? 'neg' : 'pos'}">${esc(msg)}</p>` : ''}
      ${data.status === 'HALTED' ? `<p class="ml-msg neg">HALTED: ${esc(data.reason)}. Nothing is traded automatically. Check the account on Binance, then switch off.</p>` : ''}
      ${data.status === 'CLOSING' ? `<p class="ml-msg neg">CLOSING: ${esc(data.reason)}. The close failed, so the disaster stop is back in place and the close is retried every ${r.tick_s} s.</p>` : ''}
      ${on ? `<dl class="ml-dl"><div><dt>Account</dt><dd>${esc(data.account?.label)} · ${esc(data.account?.network)}</dd></div>
          <div><dt>Coin</dt><dd>${esc(coin(data.symbol))} perp, 1x</dd></div>
          <div><dt>Account balance (USDT)</dt><dd>${data.account ? balText(data.account.id) : '—'}</dd></div>
          <div><dt>Amount</dt><dd>${n(data.amount)} USDT</dd></div><div><dt>Loss limit</dt><dd>${n(data.max_loss)} USDT</dd></div>
          <div><dt>Realized</dt><dd class="${(data.realized || 0) >= 0 ? 'pos' : 'neg'}">${(data.realized || 0) >= 0 ? '+' : ''}${n(data.realized)} USDT</dd></div>
          <div><dt>Position</dt><dd>${p ? `${esc(p.side)} ${n(p.qty, 3)} ${esc(coin(data.symbol))} @ ${px(p.entry)} · stop ${px(p.stop)}` : 'flat, waiting for a signal'}</dd></div>
          <div><dt>On since</dt><dd>${esc(when(data.armed_ts))}</dd></div></dl>`
        : `<div class="ml-form">
          <label>Account<select id="${P}acct">${free.length ? free.map(a => `<option value="${esc(a.id)}">${esc(a.label)} · ${esc(a.network)}</option>`).join('') : '<option value="">link an account below first</option>'}</select></label>
          ${syms.length > 1 ? `<label>Coin<select id="${P}symbol">${syms.map(s => `<option value="${esc(s)}" ${s === cfg.defaultSymbol ? 'selected' : ''}>${esc(coin(s))}</option>`).join('')}</select></label>` : ''}
          <label>Amount (USDT, max ${n(data.max_amount, 0)}) <span class="ml-avail" id="${P}avail">${availLine(free[0]?.id)}</span><input id="${P}amount" type="number" min="1" step="1" placeholder="50"></label>
          <label>Loss limit (USDT)${data.backtest?.dd ? ` <span class="ml-avail">backtest max drawdown ${n(data.backtest.dd, 0)}%: set at least ${Math.min(100, Math.ceil(data.backtest.dd / 5) * 5 + 10)}% of the amount, or the full amount to let the strategy run</span>` : ''}<input id="${P}loss" type="number" min="1" step="1" placeholder="15"></label>
          <label>Type <code>${esc(data.confirm_phrase)}</code><input id="${P}confirm" autocomplete="off" spellcheck="false"></label></div>
          <div class="ml-blockers" id="${P}blockers">${blockers(free[0]?.id)}</div>
          <div class="ml-go-row"><button type="button" class="ml-go" id="${P}go" ${busy || !free.length ? 'disabled' : ''}>Switch ON live trading</button>
            <span class="sub">checks the account on Binance first; nothing is bought until the next ${esc(data.interval || '4h')} signal</span></div>
          <p class="ml-msg neg" id="${P}formmsg" hidden></p>
          <p class="sub">Mainnet: server switch ${g.switch ? '<b class="pos">ON</b>' : '<b class="neg">OFF</b> (set LIVE_MIRROR_MAINNET_ENABLED=true on Railway)'} · testnet round trip ${g.testnet_waived ? '<b class="pos">waived by the operator</b> (LIVE_TESTNET_GATE_WAIVED)' : g.testnet_passed ? `<b class="pos">passed</b> ${esc(when(g.testnet_ts))}` : '<b class="neg">not yet</b> (link a testnet account and run it below)'}. Testnet accounts can be switched on any time.</p>`}
      <h4 class="ml-h4">Linked accounts · one strategy per account</h4>
      <div class="nw-scroll"><table class="nw-table"><thead><tr><th>Label</th><th>Network</th><th>Balance (USDT)</th><th>Key</th><th>Used by</th><th></th></tr></thead><tbody>
        ${(data.accounts || []).map(a => `<tr><td><b>${esc(a.label)}</b></td><td>${esc(a.network)}</td><td class="ml-bal">${balText(a.id)}</td><td class="sub">…${esc(a.key_fp)}${a.shared_with_mirror ? ' · <span title="This is also the Binance key saved in Providers & Live. Only one path may trade it at a time.">shared with Providers &amp; Live</span>' : ''}</td>
          <td>${a.owner ? esc(a.owner.ref) : '<span class="sub">free</span>'}</td>
          <td>${a.network === 'testnet' && !a.owner ? `<button class="chip" data-ml-verify="${esc(a.id)}">Run testnet round trip</button>` : ''}
              ${!a.owner ? `<button class="chip" data-ml-remove="${esc(a.id)}">Remove</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">No linked accounts yet.</td></tr>'}
      </tbody></table></div>
      ${(data.shared_keys || []).filter(k => !k.linked).map(k => `<div class="ml-shared"><span>The <b>${esc(k.exchange)} ${esc(k.network)}</b> key saved in <b>Providers &amp; Live</b> (the one the bot mirrors use) is not linked here yet.</span>
        <button class="chip" data-ml-import="${esc(k.exchange)}|${esc(k.network)}">Use it for desks</button></div>`).join('')}
      <details class="ml-add"><summary>Link a Binance account</summary>
        <p class="sub">Create an API key on the <b>sub-account</b> with <b>Futures trading enabled</b> and <b>withdrawals disabled</b>, and restrict it to the server's IP if you can. The key is checked with a read-only balance call, then stored encrypted on the server. It is never shown again. A linked account can be used by one strategy at a time.</p>
        <div class="ml-form"><label>Label<input id="${P}new-label" placeholder="Sub-account"></label>
          <label>Network<select id="${P}new-net"><option value="testnet">testnet (futures testnet)</option><option value="mainnet">mainnet (real money)</option></select></label>
          <label>API key<input id="${P}new-key" type="password" autocomplete="off"></label>
          <label>Secret<input id="${P}new-secret" type="password" autocomplete="off"></label></div>
        <button class="chip" id="${P}link">Link account</button></details>
      ${(data.trades || []).length ? `<h4 class="ml-h4">Live trades</h4><div class="nw-scroll"><table class="nw-table"><thead><tr><th>Side</th><th>Entry</th><th class="num">Entry</th><th>Exit</th><th class="num">Exit</th><th class="num">Qty</th><th class="num">P&amp;L (USDT)</th><th>Why</th></tr></thead><tbody>
        ${[...data.trades].reverse().map(t => `<tr><td>${esc(t.side || 'long')}</td><td>${esc(when(t.entry_ts))}</td><td class="num">${px(t.entry)}</td><td>${esc(when(t.exit_ts))}</td><td class="num">${px(t.exit)}</td><td class="num">${n(t.qty, 3)}</td><td class="num ${t.pnl >= 0 ? 'pos' : 'neg'}">${n(t.pnl)}</td><td class="sub">${esc(t.reason)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      ${(data.log || []).length ? `<details class="ml-add"><summary>Live log</summary><ul class="ml-log">${[...data.log].reverse().slice(0, 25).map(l => `<li><span class="sub">${esc(when(l.ts))}</span> <b>${esc(l.kind)}</b> ${esc(Object.entries(l).filter(([k]) => !['ts', 'kind'].includes(k)).map(([k, v]) => k + '=' + (typeof v === 'number' ? +v.toFixed(6) : v)).join(' '))}</li>`).join('')}</ul></details>` : ''}`;
    renderModal();
  }

  // the dialog lives under <body>: a transformed ancestor would trap position:fixed inside the card
  function modalHost() {
    let m = document.getElementById(P + 'modal-host');
    if (!m) { m = document.createElement('div'); m.id = P + 'modal-host'; m.className = 'ml-modal-host'; document.body.appendChild(m); m.addEventListener('click', onClick); }
    return m;
  }
  function renderModal() {
    const p = data?.position;
    modalHost().innerHTML = `<div id="${P}modal" class="ml-modal" hidden><div class="ml-dialog" role="dialog" aria-modal="true" aria-labelledby="${P}modal-title">
        <h3 id="${P}modal-title">Stop live trading on the ${esc(data?.desk || desk)}?</h3>
        <p>${p ? `This closes the open <b>${esc(p.side)} ${n(p.qty, 3)} ${esc(coin(data.symbol))}</b> position at market on <b>${esc(data.account?.label)}</b> (${esc(data.account?.network)}) and switches the strategy off.` : `This switches the strategy off on <b>${esc(data?.account?.label)}</b>. Nothing is open.`}</p>
        <p class="sub">Are you sure?</p>
        <div class="ml-actions"><button class="chip" id="${P}cancel">Cancel</button><button class="chip ml-danger" id="${P}yes">Yes, stop live trading</button></div></div></div>`;
  }

  async function act(fn, ok, mode = null) {
    busy = true; msg = ''; pending = mode; pendingAt = Date.now(); render();
    clearInterval(tickTimer);
    if (mode) tickTimer = setInterval(() => { const el = $l('secs'); if (el) el.textContent = Math.round((Date.now() - pendingAt) / 1000) + ' s'; }, 1000);
    try { await fn(); msg = ok; } catch (e) { msg = 'Error: ' + e.message; }
    clearInterval(tickTimer); pending = null; busy = false; await refresh(true);
  }

  function onClick(e) {
    const t = e.target;
    if (t.id === P + 'toggle' || t.id === P + 'go') {
      e.preventDefault();
      if (isOn()) { $l('modal').hidden = false; $l('cancel')?.focus(); return; }
      const body = {account_id: $l('acct').value, amount: Number($l('amount').value), max_loss: Number($l('loss').value),
        confirm: $l('confirm').value, symbol: $l('symbol')?.value || null};   // read before act() re-renders the form
      const avail = bals[body.account_id]?.ok ? Number(bals[body.account_id].available) : null;
      const why = !body.account_id ? 'choose an account' : !(body.amount > 0) ? 'enter the amount (USDT)'
        : avail != null && body.amount > avail ? `the amount ${n(body.amount)} is above the available ${n(avail)} USDT`
        : !(body.max_loss > 0) ? 'enter the loss limit (USDT)' : body.max_loss > body.amount ? 'the loss limit must be at most the amount'
        : body.confirm.trim() !== data.confirm_phrase ? `type ${data.confirm_phrase} exactly` : '';
      if (why) { const m = $l('formmsg'); if (m) { m.textContent = 'Error: ' + why; m.hidden = false; } return; }   // nothing sent, no re-render
      act(() => post(`/api/${desk}/live/start`, body), 'Live trading is ON. It waits for the next signal at a ' + (data?.interval || '4h') + ' close.', 'on');
      return;
    }
    if (t.id === P + 'cancel' || t.id === P + 'modal') { $l('modal').hidden = true; return; }
    if (t.id === P + 'yes') { $l('modal').hidden = true; act(() => post(`/api/${desk}/live/stop`, {sure: true}), 'Live trading is OFF.', 'off'); return; }
    if (t.id === P + 'link') {
      const body = {label: $l('new-label').value, exchange: 'binance', network: $l('new-net').value,
        api_key: $l('new-key').value, secret: $l('new-secret').value};      // read before act() re-renders the form
      act(() => post('/api/accounts', body), 'Account linked.');
      return;
    }
    if (t.dataset.mlImport) {
      const [exchange, network] = t.dataset.mlImport.split('|');
      act(() => post('/api/accounts/import', {exchange, network, label: `Providers & Live ${exchange} ${network}`}), 'Account linked from Providers & Live.');
      return;
    }
    if (t.dataset.mlRemove) { if (confirm('Remove this linked account? Its keys are deleted from the server.')) act(() => post(`/api/accounts/${t.dataset.mlRemove}/remove`), 'Account removed.'); return; }
    if (t.dataset.mlVerify) {
      act(async () => { const r = await post(`/api/accounts/${t.dataset.mlVerify}/verify`); if (!r.ok) throw Error('round trip failed: ' + (r.error || JSON.stringify(r.steps))); }, 'Testnet round trip passed: mainnet can be unlocked.');
    }
  }
  function onChange(e) {
    if (e.target.id === P + 'acct' && $l('avail')) $l('avail').innerHTML = availLine(e.target.value);
    if (e.target.id === P + 'acct' && $l('blockers')) $l('blockers').innerHTML = blockers(e.target.value);
  }
  function onKey(e) { if (e.key === 'Escape' && $l('modal') && !$l('modal').hidden) $l('modal').hidden = true; }

  function load(el) {
    if (!el) return;
    if (host !== el) { host = el; host.addEventListener('click', onClick); host.addEventListener('change', onChange); document.addEventListener('keydown', onKey); }
    refresh(true).then(schedule);
  }
  function deactivate() { clearTimeout(timer); timer = null; }
  return {load, deactivate};
}

const MacdLiveUI = makeDeskLiveUI('macd', {
  title: () => 'ZEC perpetual, 1x, long or flat',
  desc: () => 'Trades the rule from the monthly table on ONE linked Binance account: buy on a bullish MACD cross, close on the bearish cross.',
});
const DonchianLiveUI = makeDeskLiveUI('donchian', {
  defaultSymbol: 'XRPUSDT',
  title: d => (d.symbol ? d.symbol.replace(/USDT$/, '') : 'one coin') + ' perpetual, 1x, long and short',
  desc: () => 'Trades the Donchian Trend Rules on ONE coin you choose, on ONE linked Binance account: long on a close above the prior 20-bar high, short below the 20-bar low, an opposite breakout reverses, a close beyond the prior 10-bar low / high exits.',
});

// MACD coin bots (operator 2026-10-17): the MACD rule on STX / DASH / XLM perpetuals, long and short, one account each.
const MacdCoinUIs = Object.fromEntries(['stx', 'dash', 'xlm'].map((c) => [c, makeDeskLiveUI('macd-' + c, {
  defaultSymbol: c.toUpperCase() + 'USDT',
  title: () => c.toUpperCase() + ' perpetual, 1x, long and short',
  desc: (d) => {
    const b = d.backtest || {}, p = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%';
    return 'The MACD rule on ' + c.toUpperCase() + ' on ONE linked Binance account: long on a bullish cross, short on a bearish cross (each reverses the other). Each position uses the <b>full amount</b> (plus gains) at 1x, no leverage, so it cannot be liquidated; expect deep swings (STX, DASH and XLM together: max drawdown ~48% in the backtest). '
      + (b.net2y !== undefined ? '2-year backtest with all costs: <b>' + p(b.net2y) + '</b> (year 1 ' + p(b.y1) + ', year 2 ' + p(b.y2) + '), profitable months ' + b.pos_months + ' of ' + b.months
        + ', worst month ' + p(b.worst_month) + ', max drawdown ' + Number(b.dd).toFixed(0) + '%, profitable weeks ' + b.pos_weeks + ' of ' + b.weeks
        + '. <b>Picked with hindsight</b>: the fair year-1 pick / year-2 check passed no coin (docs/MACD_TRIO_STUDY.md).' : '');
  },
})]));

// ZEC bots (operator 2026-10-18): six TradingView strategies on the ZEC perpetual, long-only, one account each. Their
// switches live on the home page (roster.js), not on a Trading Desk tab.
const ZecLiveUIs = Object.fromEntries(['emarsi', 'momentum', 'emacross', 'macross', 'scalpslayer', 'wilder'].map((k) => [k, makeDeskLiveUI('zec-' + k, {
  defaultSymbol: 'ZECUSDT',
  title: (d) => 'ZEC perpetual, ' + (d.interval || '') + ', 1x, long or flat',
  desc: (d) => {
    const b = d.backtest || {}, p = (v) => (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%';
    return '<b>' + (b.name || k) + '</b> on ZEC, on ONE linked Binance account: buy on its buy signal, sell on its sell signal (long-only; the source\'s short side lowered the return and raised the drawdown on ZEC). Each position uses the <b>full amount</b> (plus gains) at 1x, so it cannot be liquidated. '
      + (b.net2y !== undefined ? '2-year backtest with all costs: <b>' + p(b.net2y) + '</b> (year 1 ' + p(b.y1) + ', year 2 ' + p(b.y2) + '), max drawdown ' + Number(b.dd).toFixed(0) + '%, worst month ' + p(b.worst_month)
        + ', profitable months ' + b.pos_months + ' of ' + b.months + '. <b>ZEC was picked with hindsight</b> and rose about 3,500% in that window. All six ZEC bots trade the same coin. ' : '');
  },
})]));

/* "Are you sure?" for any live switch (bot cards, bot pages): resolves true on Yes. Rendered under <body> so no
   transformed ancestor can trap it; Esc or Cancel resolves false. */
function confirmLive(title, html, yes = 'Yes, stop live trading') {
  return new Promise(resolve => {
    const host = document.createElement('div');
    host.className = 'ml-modal-host';
    host.innerHTML = `<div class="ml-modal"><div class="ml-dialog" role="dialog" aria-modal="true" aria-label="${String(title).replace(/"/g, '&quot;')}">
      <h3></h3><p class="ml-body"></p><p class="sub">Are you sure?</p>
      <div class="ml-actions"><button class="chip" data-a="no">Cancel</button><button class="chip ml-danger" data-a="yes"></button></div></div></div>`;
    host.querySelector('h3').textContent = title;
    host.querySelector('.ml-body').innerHTML = html;
    host.querySelector('[data-a="yes"]').textContent = yes;
    const done = v => { document.removeEventListener('keydown', key); host.remove(); resolve(v); };
    const key = e => { if (e.key === 'Escape') done(false); };
    host.addEventListener('click', e => {
      const a = e.target.dataset.a;
      if (a) done(a === 'yes'); else if (e.target.classList.contains('ml-modal')) done(false);
    });
    document.addEventListener('keydown', key);
    document.body.appendChild(host);
    host.querySelector('[data-a="no"]').focus();
  });
}
