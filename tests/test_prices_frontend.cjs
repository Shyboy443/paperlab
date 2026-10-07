const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup() {
  let now = 0;
  const context = vm.createContext({ performance: { now: () => now }, document: { hidden: true },
    window: {}, setInterval() {}, requestAnimationFrame() {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/dashboard/prices.js'), 'utf8') + '\nthis.live = LivePrices;', context);
  const p = context.live;
  p.transport = true;
  return { p, advance: (ms) => { now += ms; } };
}
function packet(seq = 1, mid = 110, stream = 'a') {
  return { stream_id: stream, seq, connected: true, stale_after_ms: 5000, symbols: ['XRPUSDT'],
    quotes: { XRPUSDT: { mid, stale: false, age_ms: 100 } } };
}
function row(side = 'long') {
  return { key: side, symbol: 'XRPUSDT', equity_now: 22, start_equity: 20,
    open_positions: [{ side, entry: 100, qty: 2, mark: 101, upnl: side === 'long' ? 2 : -2 }] };
}

test('exact long/short PnL without accumulating or mutating authoritative books', () => {
  const { p } = setup(); p.accept(packet());
  const r = row(), original = JSON.stringify(r);
  assert.equal(p.book(r).upnl, 20); assert.equal(p.book(r).equity, 40);
  for (let i = 0; i < 100; i++) assert.equal(p.book(r).equity, 40);
  assert.equal(JSON.stringify(r), original);
  assert.equal(p.book(row('short')).upnl, -20);
  assert.equal(p.book(row('short')).equity, 4);
  r.open_positions = []; r.equity_now = 20.15;
  assert.equal(p.book(r).equity, 20.15); // A closed position never receives quote gains.
});
test('duplicate/out-of-order snapshots and retired streams cannot move numbers backwards', () => {
  const { p } = setup(); assert.ok(p.accept(packet(3)));
  assert.equal(p.accept(packet(2, 1)), false); assert.equal(p.accept(packet(3, 1)), false);
  assert.equal(p.quote('XRPUSDT').mid, 110);
  assert.ok(p.accept(packet(1, 120, 'b')));
  assert.equal(p.accept(packet(999, 1, 'a')), false);
  assert.equal(p.quote('XRPUSDT').mid, 120);
});
test('staleness decays with monotonic time; disconnected quotes cannot change PnL', () => {
  const { p, advance } = setup(); p.accept(packet());
  advance(5001); assert.equal(p.quote('XRPUSDT').stale, true);
  assert.equal(p.book(row()).equity, 22);
  p.accept(packet(2)); assert.equal(p.book(row()).equity, 40);
  p.transport = false; assert.equal(p.book(row()).equity, 22);
});
test('realized fees/funding remain in the authoritative equity baseline', () => {
  const { p } = setup(); p.accept(packet());
  const r = row(); r.equity_now = 21.7;
  assert.ok(Math.abs(p.book(r).equity - 39.7) < 1e-9);
  p.accept(packet(2, 111));
  r.equity_now = 39.7; r.open_positions[0].upnl = 20; r.open_positions[0].mark = 110;
  assert.ok(Math.abs(p.book(r).equity - 41.7) < 1e-9);
});
test('same-coin books are valued independently and totals equal their sum', () => {
  const { p } = setup(); p.accept(packet());
  const d = { leaderboard: [row('long'), row('short')], pairs: [] };
  const result = p.model(d);
  assert.equal(result.equity, 44); assert.equal(result.net, 4);
});
test('small-coin prices and small paper PnL remain visible', () => {
  const { p } = setup();
  assert.equal(p.format(0.095305, 'price'), '0.095305');
  assert.equal(p.format(0.0008, 'money'), '+0.0008');
  assert.equal(p.format(NaN, 'price'), '—');
});
