"""Calendar/event strategies -> hourly target-weight frames. Every rule uses only schedules known in advance
plus prices from bars that closed before the entry bar opens."""
from base import *


def load(syms):
    frames = {s: frame(s) for s in syms}
    idx = pd.date_range(pd.Timestamp(DEV_START - 7 * DAY, unit="ms", tz="UTC"),
                        pd.Timestamp(TEST_END - HOUR, unit="ms", tz="UTC"), freq="h")
    return frames, idx


def _spread(sig, coins, idx):
    """sig: Series on idx in [-1, 1] -> equal split over coins."""
    return pd.DataFrame({s: sig / len(coins) for s in coins}, index=idx)


def gap_momentum(frames, idx, coins, thr=0.01, hold=24):
    """CME BTC weekend gap: BTC price at the Sunday 18:00 NY CME re-open vs the Friday 17:00 NY CME close.
    At the open of the Sunday 18:00 NY bar (the gap is known from bar opens), go sign(gap) for `hold` hours."""
    o = frames["BTCUSDT"].o
    sig = pd.Series(0.0, index=idx)
    ny = idx.tz_convert(NY)
    for t in idx[(ny.dayofweek == 6) & (ny.hour == 18)]:
        fri = t - pd.Timedelta(hours=49)
        if t not in o.index or fri not in o.index:
            continue
        g = o[t] / o[fri] - 1
        if abs(g) > thr:
            sig.loc[t: t + pd.Timedelta(hours=hold - 1)] = np.sign(g)
    return _spread(sig, coins, idx)


def event_long(idx, coins, dates, hh, mm, before=4, after=1):
    """Long from `before` hours ahead of the release bar to the end of the release bar + (after-1) hours."""
    sig = pd.Series(0.0, index=idx)
    for d in dates:
        ev = ny_time(d, hh, mm).floor("h")
        sig.loc[ev - pd.Timedelta(hours=before): ev + pd.Timedelta(hours=after - 1)] = 1.0
    return _spread(sig, coins, idx)


def hours_long(idx, coins, hours):
    sig = pd.Series(np.isin(idx.hour, hours).astype(float), index=idx)
    return _spread(sig, coins, idx)


def stack(*Ws):
    """BUGGY (as pre-registered): DataFrames with different columns align to NaN, so coins missing from any leg
    (all but BTC here) were dropped. Kept to reproduce the pre-registered STACK attempts."""
    W = sum(w.fillna(0.0) for w in Ws)
    gross = W.abs().sum(axis=1).clip(lower=1.0)
    return W.div(gross, axis=0)


def stack_fixed(*Ws):
    """Sum of weights over the union of coins (missing = 0), gross capped at 1 per row."""
    cols = sorted(set().union(*[w.columns for w in Ws]))
    W = sum(w.reindex(columns=cols).fillna(0.0) for w in Ws)
    gross = W.abs().sum(axis=1).clip(lower=1.0)
    return W.div(gross, axis=0)
