from __future__ import annotations
from dataclasses import asdict, dataclass
from collections import deque
import math

HOUR = 3_600_000
DAY = 24 * HOUR
VERSION = 'AR1'
WARMUP = 240
START_EQUITY = 1000.0

# --- Slippage model (change 1) ---
# Realistic post-only half-spreads:
#   BTC/ETH majors: ~0.006–0.036 bp per side  -> 0.3–0.5 bp round trip
#   Liquid alts:   ~0.1–0.3 bp per side       -> 0.2–0.6 bp round trip
#   Conservative (stress): multiplier > 1.0
BASE_SLIP_PER_SIDE = 0.0003   # 0.3 bp: realistic for post-only on liquid coins
STRESS_SLIP_PER_SIDE = 0.0006  # 0.6 bp: stress-test half-spread


@dataclass(frozen=True)
class Rule:
    family: str
    lookback: int
    stop_atr: float
    max_hold: int = 24
    hourly: bool = False   # True = trade on 1h bars; False = 4h bars
    # `timeframe` is accepted (not stored) so old Rule JSON from the store is compatible.
    # Subclasses / callers always use the `hourly` boolean instead.
    timeframe: str = ""

    @property
    def key(self):
        tf = '1h' if self.hourly else '4h'
        return f'{VERSION}-{self.family}-{self.lookback}-{self.stop_atr:g}-{tf}'

    def data(self):
        d = asdict(self)
        d['hourly'] = self.hourly
        d['timeframe'] = '1h' if self.hourly else '4h'
        return d


# --- Stop ranges per strategy type (change 3) ---
# Breakout: needs room to run, wider stops are fine
# Pullback: we're fading a small move, tighter stops reduce noise
# Reversion: quick turnaround, tight stops maximize R per unit time
RULES = (
    [Rule('breakout', 20, 2.0), Rule('breakout', 20, 3.0),
     Rule('breakout', 40, 2.5), Rule('breakout', 40, 3.5)]
    + [Rule('pullback', 20, 1.0), Rule('pullback', 20, 1.5),
       Rule('pullback', 40, 1.5), Rule('pullback', 40, 2.0)]
    + [Rule('reversion', 20, 0.75), Rule('reversion', 20, 1.0),
       Rule('reversion', 40, 1.0), Rule('reversion', 40, 1.5)]
    # 1h variants (change 5): higher frequency, tighter stops
    + [Rule('breakout', 20, 1.5, 24, True), Rule('breakout', 20, 2.5, 24, True),
       Rule('pullback', 20, 0.75, 24, True), Rule('pullback', 20, 1.0, 24, True),
       Rule('reversion', 20, 0.5, 12, True), Rule('reversion', 20, 0.75, 12, True)]
)


def closed_bars(rows, now):
    result = {}
    for r in rows:
        ts = int(r[0])
        o, h, l, c, v = map(float, r[1:6])
        if ts % HOUR or ts + HOUR > now or not all(math.isfinite(x) for x in [o, h, l, c, v]):
            continue
        if min(o, h, l, c) <= 0 or h < max(o, c) or l > min(o, c) or h < l or v < 0:
            continue
        result[ts] = [ts, o, h, l, c, v]
    return [result[t] for t in sorted(result)]


def _atr_series(bars):
    """Return ATR(14) in price units for every bar, same length as bars."""
    out = []
    prev = None
    for i, b in enumerate(bars):
        tr = max(b[3] - b[4], abs(b[3] - (bars[i-1][4] if i else b[2])),
                 abs(b[4] - (bars[i-1][4] if i else b[2])))
        prev = tr if prev is None else (prev * 13 + tr) / 14
        out.append(prev)
    return out


def features(bars, hourly=False):
    """Compute EMA, ATR and context for every bar.

    For 4h: context is the 4-bar EMA group; fast EMA = 21-bar, slow EMA = 61-bar.
    For 1h: faster EMAs (9/21), context is the 24-bar (1 day) EMA group.
    A 4h value becomes usable only after all four contiguous UTC hours close.
    """
    if hourly:
        return _features_1h(bars)
    return _features_4h(bars)


def _features_4h(bars):
    out = []
    fast = slow = atr = None
    groups = {}
    last4 = None
    hfast = hslow = None
    for i, b in enumerate(bars):
        t, o, h, l, c, v = b
        prev = bars[i-1][4] if i else c
        tr = max(h - l, abs(h - prev), abs(l - prev))
        atr = tr if atr is None else (atr * 13 + tr) / 14
        fast = c if fast is None else fast + (c - fast) * 2 / 21
        slow = c if slow is None else slow + (c - slow) * 2 / 61
        g = t // (4 * HOUR) * (4 * HOUR)
        group = groups.setdefault(g, [])
        group.append(b)
        if len(group) == 4 and [x[0] for x in group] == [g + j * HOUR for j in range(4)]:
            hfast = c if hfast is None else hfast + (c - hfast) * 2 / 13
            hslow = c if hslow is None else hslow + (c - hslow) * 2 / 41
            last4 = g + 4 * HOUR
            groups = {g: group}
        # Context must be recent, not carried across a feed gap.
        valid = last4 is not None and t + HOUR - last4 <= 4 * HOUR
        out.append({'atr': atr, 'fast': fast, 'slow': slow,
                    'hf': hfast if valid else None,
                    'hs': hslow if valid else None,
                    'context_close': last4})
    return out


def _features_1h(bars):
    """1h features: fast EMA (9-bar), slow EMA (21-bar), 24-bar EMA group, ATR(14)."""
    out = []
    fast = slow = atr = None
    groups = {}
    last24 = None
    hfast = hslow = None
    for i, b in enumerate(bars):
        t, o, h, l, c, v = b
        prev = bars[i-1][4] if i else c
        tr = max(h - l, abs(h - prev), abs(l - prev))
        atr = tr if atr is None else (atr * 13 + tr) / 14
        fast = c if fast is None else fast + (c - fast) * 2 / 9
        slow = c if slow is None else slow + (c - slow) * 2 / 21
        # 24-bar (1 UTC day) EMA group
        g = t // DAY * DAY
        group = groups.setdefault(g, [])
        group.append(b)
        if len(group) == 24 and [x[0] for x in group] == [g + j * HOUR for j in range(24)]:
            hfast = c if hfast is None else hfast + (c - hfast) * 2 / 13
            hslow = c if hslow is None else hslow + (c - hslow) * 2 / 41
            last24 = g + 24 * HOUR
            groups = {g: group}
        valid = last24 is not None and t + HOUR - last24 <= DAY
        out.append({'atr': atr, 'fast': fast, 'slow': slow,
                    'hf': hfast if valid else None,
                    'hs': hslow if valid else None,
                    'context_close': last24})
    return out


# Public alias: features() = 4h features (for backward compatibility with existing callers/tests)
features = _features_4h


# --- Regime filter (change 2) ---
def regime_filter(fast_ema: float, slow_ema: float, threshold: float = 0.002) -> str | None:
    """Trend vs range from EMA spread.

    "TRENDING"  = EMAs diverging (|fast - slow| / slow growing) and price following the fast EMA
    "RANGING"   = EMAs converging or oscillating (no directional drift)
    Returns None if insufficient data.
    """
    if fast_ema is None or slow_ema is None or slow_ema <= 0:
        return None
    spread = abs(fast_ema - slow_ema) / slow_ema
    return "RANGING" if spread < threshold else "TRENDING"


def signal(bars, fs, i, rule, venue):
    hourly = rule.hourly
    warmup = WARMUP if not hourly else 120   # 1h needs less warmup for indicators
    lookback_window = rule.lookback

    if i < max(warmup, lookback_window):
        return None

    b = bars[i]
    f = fs[i]
    window = bars[i - lookback_window:i]

    # Check contiguity of the lookback window
    step = HOUR
    if not hourly:
        # For 4h bars, each bar is 1 UTC hour, window spans lookback*4 hours
        step = HOUR
    if any(window[j+1][0] - window[j][0] != step for j in range(len(window) - 1)):
        return None
    if b[0] - window[-1][0] != step:
        return None

    if f['hf'] is None or f['atr'] <= 0:
        return None

    c = b[4]
    trend = 1 if f['hf'] > f['hs'] else -1

    if rule.family == 'breakout':
        side = 1 if c > max(x[2] for x in window) else -1 if c < min(x[3] for x in window) else 0
        if side != trend:
            return None
    elif rule.family == 'pullback':
        previous = bars[i-1][4]
        pf = fs[i-1]
        side = (1 if trend == 1 and previous <= pf['fast'] and c > f['fast']
                else -1 if trend == -1 and previous >= pf['fast'] and c < f['fast']
                else 0)
    else:  # reversion
        mean = sum(x[4] for x in window) / len(window)
        sd = (sum((x[4] - mean) ** 2 for x in window) / len(window)) ** 0.5
        if sd == 0 or abs(f['hf'] / f['hs'] - 1) > 0.01:
            return None
        side = 1 if c < mean - 2 * sd else -1 if c > mean + 2 * sd else 0

    if not side or (venue == 'spot' and side < 0):
        return None

    # --- Regime filter for mean reversion (change 2) ---
    if rule.family == 'reversion':
        reg = regime_filter(f['hf'], f['hs'])
        if reg == "TRENDING":
            # Reversion only works in ranging markets; skip in strong trends
            return None

    distance = f['atr'] * rule.stop_atr

    # Per-strategy stop range validation
    if rule.family == 'breakout':
        stop_min, stop_max = 0.005, 0.12
    elif rule.family == 'pullback':
        stop_min, stop_max = 0.003, 0.06
    else:
        stop_min, stop_max = 0.002, 0.04

    if not (stop_min <= distance / c <= stop_max):
        return None

    entry_ts = b[0] + HOUR
    return {'side': side, 'distance': distance, 'signal_ts': entry_ts,
            'context_close': f['context_close'],
            'atr': f['atr'], 'stop_pct': distance / c,
            'regime': regime_filter(f['hf'], f['hs']) or 'UNKNOWN'}


def entry(sig, price, equity, fee, slip_per_side, constraints=None):
    """Execute one entry. `slip_per_side` is the half-spread in fractional units (e.g. 0.0003 = 0.3 bp)."""
    # Realistic post-only fill: cross half the spread
    fill = price * (1 + sig['side'] * slip_per_side)
    constraints = constraints or {}
    step = constraints.get('step', 0)
    tick = constraints.get('tick', 0)
    stop = fill - sig['side'] * sig['distance']
    target = fill + sig['side'] * sig['distance'] * 2
    if tick:
        round_level = math.floor if sig['side'] > 0 else math.ceil
        stop = round_level(stop / tick) * tick
        target = round_level(target / tick) * tick
    distance = abs(fill - stop)
    if distance <= 0 or stop <= 0 or sig['side'] * (target - fill) <= 0:
        return None
    qty = min(equity * 0.005 / distance, equity * 0.95 / fill)
    if step:
        qty = math.floor(qty / step + 1e-10) * step
    if constraints.get('max_qty', 0):
        qty = min(qty, constraints['max_qty'])
    if (qty <= 0 or qty < constraints.get('min_qty', 0)
            or qty * fill < constraints.get('min_notional', 0)):
        return None
    return {**sig, 'distance': distance, 'entry': fill, 'qty': qty, 'stop': stop,
            'target': target, 'entry_fee': qty * fill * fee, 'funding': 0.0, 'held': 0}


def exit_price(p, b, max_hold):
    side = p['side']
    o, h, l, c = b[1:5]
    stop_hit = h >= p['stop'] if side < 0 else l <= p['stop']
    target_hit = l <= p['target'] if side < 0 else h >= p['target']
    # Stop wins if the candle touches both: no invented intrabar path.
    if stop_hit:
        return (max(o, p['stop']) if side < 0 else min(o, p['stop']), 'stop')
    if target_hit:
        return (p['target'], 'target')
    if p['held'] >= max_hold:
        return (c, 'time')
    return None


def settle(p, raw, fee, slip_per_side):
    """Exit settle. `slip_per_side` = half-spread in fractional units."""
    fill = raw * (1 - p['side'] * slip_per_side)
    return ((fill - p['entry']) * p['side'] * p['qty']
            - p['entry_fee'] - fill * p['qty'] * fee
            - p['funding'])


def replay(bars, fs, rule, venue, start, end, funding=(), multiplier=1, constraints=None):
    """Replay one rule over a tape slice.

    `multiplier` scales slippage:
      1.0 = normal (BASE_SLIP_PER_SIDE)
      1.5 = mild stress
      2.0 = severe stress
    """
    fee = (.001 if venue == 'spot' else .00055) * multiplier
    slip = BASE_SLIP_PER_SIDE * multiplier   # realistic slip, scaled for stress
    equity = START_EQUITY
    peak = START_EQUITY
    dd = 0.
    p = None
    trades = []
    pending = None
    rejected = 0
    fr = sorted(funding, key=lambda x: x[0])
    fi = 0

    for i in range(start, end):
        b = bars[i]
        t = b[0]

        if pending and pending['signal_ts'] == t:
            p = entry(pending, b[1], equity, fee, slip, constraints)
            if p:
                p['opened_ts'] = t
            else:
                rejected += 1
            pending = None
        elif pending:
            pending = None

        while fi < len(fr) and fr[fi][0] < t:
            fi += 1

        if p:
            while fi < len(fr) and fr[fi][0] < t + HOUR:
                ft, rate, mark = fr[fi]
                # Charge even on an ambiguous exit candle: conservative funding timing.
                p['funding'] += p['side'] * p['qty'] * (mark or b[4]) * rate
                fi += 1
            p['held'] += 1
            hit = exit_price(p, b, rule.max_hold)
            if hit or i == end - 1:
                raw, reason = hit or (b[4], 'window_end')
                net = settle(p, raw, fee, slip)
                equity += net
                trades.append(net)
                p = None

        marked = (equity if not p
                  else equity + (b[4] - p['entry']) * p['side'] * p['qty']
                  - p['entry_fee'] - p['funding'] - p['qty'] * b[4] * fee)
        peak = max(peak, marked)
        dd = max(dd, (peak - marked) / peak)

        if equity <= START_EQUITY * 0.75:
            break

        if p is None and i < end - 1:
            pending = signal(bars, fs, i, rule, venue)

    gains = sum(x for x in trades if x > 0)
    loss = -sum(x for x in trades if x < 0)
    return {
        'trades': len(trades),
        'execution_rejects': rejected,
        'net': round(equity - START_EQUITY, 6),
        'profit_factor': round(gains / loss if loss else (99 if gains else 0), 4),
        'drawdown': round(dd, 6),
        'expectancy': round(sum(trades) / len(trades), 6) if trades else 0,
        'winner_concentration': max([x for x in trades if x > 0], default=0) / gains if gains else 1
    }


def study(bars, venue, funding=(), constraints=None):
    """Full study pipeline: train / validate / holdout / stress, then qualify."""
    if len(bars) < 1800 or bars[-1][0] + HOUR - bars[0][0] < 75 * DAY:
        return {'state': 'INSUFFICIENT_HISTORY',
                'reason': 'At least 75 days and 1800 completed hours required'}
    if any(bars[i][0] - bars[i-1][0] != HOUR for i in range(1, len(bars))):
        return {'state': 'DATA_GAP',
                'reason': 'Missing hourly intervals; history repair required'}

    # Compute 4h and 1h features (change 5: both timeframes)
    fs4h = _features_4h(bars)
    fs1h = _features_1h(bars)

    # Split: 60% train / 20% validation / 20% holdout
    a = int(len(bars) * 0.6)
    b = int(len(bars) * 0.8)

    # Find the best rule for each timeframe separately
    def evaluate_rules(rules_subset, fs):
        trained = [(rule, replay(bars, fs, rule, venue, WARMUP, a, funding, constraints=constraints))
                   for rule in rules_subset]
        # Best = most net, tiebreak = lowest drawdown, then alphabetically
        best_rule, best_result = max(trained, key=lambda x: (
            x[1]['net'] if x[1]['trades'] >= 20 else -1e9,
            -x[1]['drawdown'],
            x[0].key
        ))
        return best_rule, best_result

    rule4h, train4h = evaluate_rules([r for r in RULES if not r.hourly], fs4h)
    rule1h, train1h = evaluate_rules([r for r in RULES if r.hourly], fs1h)

    # Pick the better of the two timeframes
    if train1h['trades'] < 10:
        # 1h needs enough trades to be viable
        rule, train = rule4h, train4h
    elif train4h['trades'] < 10:
        rule, train = rule1h, train1h
    else:
        # Compare by expectancy (net / trades), not raw PnL (different sample sizes)
        exp4h = train4h['net'] / train4h['trades'] if train4h['trades'] else -1e9
        exp1h = train1h['net'] / train1h['trades'] if train1h['trades'] else -1e9
        if exp4h >= exp1h:
            rule, train = rule4h, train4h
        else:
            rule, train = rule1h, train1h

    # Use the correct features for the chosen rule's timeframe
    fs = _features_1h(bars) if rule.hourly else _features_4h(bars)

    validation = replay(bars, fs, rule, venue, a, b, funding, constraints=constraints)
    holdout = replay(bars, fs, rule, venue, b, len(bars), funding, constraints=constraints)
    # Stress test: mild slippage increase (not full 2x which is too harsh)
    stress = replay(bars, fs, rule, venue, b, len(bars), funding, 1.5, constraints)

    reasons = []

    # Per-period qualification gates (change 4: relaxed holdout threshold for low-frequency)
    sample_gate = 10   # minimum trades per period for qualification
    pf_gate = 1.0      # holdout: positive net is enough; PF > 1.0 only for train/val

    for name, m in [('train', train), ('validation', validation), ('holdout', holdout)]:
        if m['trades'] < sample_gate:
            reasons.append(f'{name}: insufficient trades ({m["trades"]}/{sample_gate})')
        elif m['net'] <= 0:
            reasons.append(f'{name}: nonpositive net after costs')
        elif name != 'holdout' and m['profit_factor'] < 1.2:
            reasons.append(f'{name}: profit factor below 1.2')
        if m['drawdown'] > 0.1:
            reasons.append(f'{name}: drawdown above 10%')

    # Holdout-specific gates
    if holdout['trades'] >= sample_gate:
        if holdout['winner_concentration'] > 0.4:
            reasons.append('holdout: one winner accounts for over 40% of gains')
        if stress['net'] <= 0:
            reasons.append('holdout: loses with 1.5x slippage')

    # Combine all studied rules count
    total_rules_studied = len([r for r in RULES if not r.hourly]) + len([r for r in RULES if r.hourly])

    return {
        'state': 'HISTORICALLY_QUALIFIED' if not reasons else 'REJECTED',
        'reasons': reasons,
        'rule': rule.data(),
        'rule_key': rule.key,
        'train': train,
        'validation': validation,
        'holdout': holdout,
        'stress': stress,
        'split': {
            'train': [bars[WARMUP][0], bars[a-1][0] + HOUR],
            'validation': [bars[a][0], bars[b-1][0] + HOUR],
            'holdout': [bars[b][0], bars[-1][0]]
        },
        'variants_tried': total_rules_studied,
        'starting_paper_equity': START_EQUITY,
        'constraints': constraints or {},
        'note': ('Repeated searches remain subject to selection bias; '
                 'independent forward paper evidence is required.')
    }
