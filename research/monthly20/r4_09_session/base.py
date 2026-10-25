"""Shared arrays + event evaluator for the session/clock family (1h bars, survivorship-free top-40 universe)."""
import sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, DEV_START, DEV_END, TEST_START, TEST_END, DAY, HOUR, DATA_FROM
from hpanel import make

H = make()
th, td, syms = H["th"], H["td"], [str(s) for s in H["syms"]]
O, Hh, L, C, QV = (H[k].astype(np.float64) for k in ("o", "h", "l", "c", "qv"))
U40, U20 = H["U40"], H["U20"]
FUND = H["fund"].astype(np.float64)
FCUM = np.vstack([np.zeros((1, FUND.shape[1])), np.cumsum(FUND, 0)])  # FCUM[b]-FCUM[a] = stamps in bars a..b-1
ND = len(td)
J = {s: i for i, s in enumerate(syms)}
BTC, ETH = J["BTCUSDT"], J["ETHUSDT"]
# hourly log return of bar t (open->next open), and trailing vol known at the start of bar t (previous 480 bars)
LR = np.full_like(O, np.nan); LR[:-1] = np.log(O[1:] / O[:-1])
_x = np.nan_to_num(LR); _n = (~np.isnan(LR)).astype(float)
_c1 = np.vstack([np.zeros((1, O.shape[1])), np.cumsum(_x, 0)]); _c2 = np.vstack([np.zeros((1, O.shape[1])), np.cumsum(_x * _x, 0)])
_cn = np.vstack([np.zeros((1, O.shape[1])), np.cumsum(_n, 0)])


def hvol(t, W=480):
    """std of hourly log returns over bars t-W-1 .. t-2 (all fully known at the open of bar t). t: array of bar indices."""
    a, b = np.maximum(t - W - 1, 0), np.maximum(t - 1, 0)
    n = _cn[b] - _cn[a]; s1 = _c1[b] - _c1[a]; s2 = _c2[b] - _c2[a]
    with np.errstate(invalid="ignore", divide="ignore"):
        v = s2 / n - (s1 / n) ** 2
    return np.where(n > W * 0.8, np.sqrt(np.maximum(v, 0)), np.nan)


def dayidx(ms):
    return int((ms - DATA_FROM) // DAY)


def dow(d):  # 0=Mon
    return datetime.fromtimestamp(td[d] / 1000, timezone.utc).weekday()


DOW = np.array([dow(d) for d in range(ND)])


def trade_ret(ie, ix, j, side, cost_mult=1.0):
    """net return of a 1x position: entry at open of bar ie, exit at open of bar ix, side +1/-1."""
    g = side * (O[ix, j] / O[ie, j] - 1)
    f = side * (FCUM[ix, j] - FCUM[ie, j])
    return g - f - 2 * (FEE + SLIP) * cost_mult


def mae(ie, ix, j, side):
    """worst adverse move during the holding bars ie..ix-1 (fraction, positive = loss)."""
    out = np.empty(len(ie))
    for k in range(len(ie)):
        a, b, jj = ie[k], ix[k], j[k]
        if side[k] > 0:
            out[k] = 1 - np.nanmin(L[a:b, jj]) / O[a, jj]
        else:
            out[k] = np.nanmax(Hh[a:b, jj]) / O[a, jj] - 1
    return out
