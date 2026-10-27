"""Map DefiLlama protocols/chains to Binance USD-M perps (symbol match + price check) and build day x coin arrays
of TVL, fees and revenue on the survivorship-free daily panel grid. Point-in-time rule: the value used at the open of
day i is the latest DefiLlama point stamped <= t[i] - 1 day (one full day of lag on top of DefiLlama's 00:00 stamp).
Caveat: DefiLlama history is today's vintage (adapters are re-run / back-filled), not what was published at the time."""
import json, sys, urllib.request, time
from pathlib import Path
import numpy as np
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_tvl_value")
PANEL = r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_rotation/panel_1d.npz"
DAY = 86_400_000
L = lambda f: json.load(open(f, encoding="utf-8"))


def series(rows):
    a = np.array([[float(x[0]), float(x[1] or 0)] for x in rows if x[1] is not None], float) if rows else np.zeros((0, 2))
    return a


def to_grid(t, a, lag_days=1):
    """value at day i = last point with stamp_ms <= t[i] - lag; NaN before first point."""
    out = np.full(len(t), np.nan)
    if not len(a):
        return out
    ms = a[:, 0] * 1000
    idx = np.searchsorted(ms, t - lag_days * DAY, side="right") - 1
    ok = idx >= 0
    out[ok] = a[idx[ok], 1]
    # stale data: if the last point is > 5 days older than the target, treat as missing
    stale = ok & ((t - lag_days * DAY) - ms[np.clip(idx, 0, None)] > 5 * DAY)
    out[stale] = np.nan
    return out


def gecko_prices(ids):
    px = {}
    ids = sorted({i for i in ids if i})
    for k in range(0, len(ids), 60):
        q = ",".join("coingecko:" + i for i in ids[k:k + 60])
        with urllib.request.urlopen(urllib.request.Request(f"https://coins.llama.fi/prices/current/{q}",
                                                           headers={"User-Agent": "paperlab-research"}), timeout=60) as r:
            d = json.loads(r.read().decode())
        for key, v in d.get("coins", {}).items():
            px[key.split(":", 1)[1]] = v["price"]
    return px


def hist_price(gid, ts):
    f = OUT / "hist_px" / f"{gid}_{ts}.json"
    if f.exists():
        return json.loads(f.read_text()).get("p")
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://coins.llama.fi/prices/historical/{ts}/coingecko:{gid}",
                                                           headers={"User-Agent": "paperlab-research"}), timeout=60) as r:
            d = json.loads(r.read().decode())
        p = d.get("coins", {}).get(f"coingecko:{gid}", {}).get("price")
    except Exception:
        p = None
    f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps({"p": p}))
    return p


def build():
    z = np.load(PANEL)
    t = z["t"]; syms = [str(s) for s in z["syms"]]; C = z["c"]
    cand = L(OUT / "candidates.json")
    B = {}
    for j, s in enumerate(syms):
        k = s[:-4]
        for kk, mult in ((k, 1), (k[4:] if k.startswith("1000") else None, 1000), (k[7:] if k.startswith("1000000") else None, 1e6)):
            if kk:
                B[kk] = (j, mult)
    # candidate entries: (sym, kind, key, gecko)
    ents = []
    for key, tvl in cand["groups"].items():
        sym, slug = key.split("|")
        f = OUT / "protocol" / f"{slug}.json"
        if not f.exists():
            continue
        d = L(f)
        if "_error" in d or not d.get("tvl"):
            continue
        ents.append(dict(sym=sym, kind="protocol", key=slug, gecko=d.get("gecko_id"), tvl_now=tvl, cat=d.get("category")))
    # manual aliases for renamed / delisted gas tokens (price-checked like everything else)
    for sym, name, gecko in cand["chains"] + [["MATIC", "Polygon", "matic-network"], ["LUNA", "Terra Classic", "terra-luna"]]:
        f = OUT / "chain" / f"{name}.json"
        if not f.exists() or isinstance(L(f), dict):
            continue
        a = L(f)
        ents.append(dict(sym=sym, kind="chain", key=name, gecko=gecko, tvl_now=a[-1][1] if a else 0, cat="Chain"))
    px = gecko_prices([e["gecko"] for e in ents])
    # last valid Binance close per coin
    lastc = {}
    for kk, (j, mult) in B.items():
        col = C[:, j]; ok = ~np.isnan(col)
        lastc[kk] = (col[ok][-1] / mult if ok.any() else np.nan, bool(z["trading_today"][j]))
    chosen, rejected = {}, []
    for e in ents:
        bc, live = lastc[e["sym"]]
        gp = px.get(e["gecko"]) if e["gecko"] else None
        if live:
            okp = gp is not None and bc > 0 and 0.8 < gp / bc < 1.25
        else:
            # delisted: compare the Binance last close with DefiLlama's historical gecko price on that day
            # (the panel forward-fills after delisting, so use the last archived bar minus 3 days)
            j, mult = B[e["sym"]]
            col = C[:, j]; mv = np.where(np.isfinite(col[1:]) & (col[1:] != col[:-1]) & (np.nan_to_num(z["qv"][1:, j]) > 0))[0] + 1
            di = int(mv[-1]) - 3 if len(mv) else -1
            bc = C[di, j] / mult if di >= 0 else np.nan
            gp = hist_price(e["gecko"], int(t[di] // 1000)) if (e["gecko"] and di >= 0) else None
            okp = gp is not None and bc > 0 and 0.67 < gp / bc < 1.5
            e["price_ratio"] = gp / bc if (gp and bc > 0) else None
        e["price_ratio"] = (gp / bc) if (gp and bc > 0) else None
        if not okp:
            rejected.append(e); continue
        cur = chosen.get(e["sym"])
        rank = lambda x: (x["kind"] == "chain", x["tvl_now"])
        if cur is None or rank(e) > rank(cur):
            chosen[e["sym"]] = e
    N = len(syms)
    TVL, FEE, REV = (np.full((len(t), N), np.nan) for _ in range(3))
    for sym, e in chosen.items():
        j, _ = B[sym]
        if e["kind"] == "protocol":
            a = series(L(OUT / "protocol" / f"{e['key']}.json")["tvl"])
            fslug = e["key"]
        else:
            a = series(L(OUT / "chain" / f"{e['key']}.json"))
            fslug = e["key"].lower().replace(" ", "-")
        a = a[a[:, 1] > 0] if len(a) else a
        TVL[:, j] = to_grid(t, a)
        for arr, dt in ((FEE, "dailyFees"), (REV, "dailyRevenue")):
            f = OUT / dt / f"{fslug}.json"
            if f.exists():
                d = L(f)
                if "_error" not in d and d.get("chart"):
                    s = series(d["chart"])
                    # daily flows: 30-day trailing sum, known with the same lag
                    if len(s):
                        days = ((s[:, 0] * 1000 - t[0]) // DAY).astype(int)
                        flow = np.zeros(len(t)); okd = (days >= 0) & (days < len(t))
                        np.add.at(flow, days[okd], s[okd, 1])
                        first = days.min()
                        cs = np.cumsum(flow)
                        roll = np.full(len(t), np.nan)
                        roll[30:] = cs[30:] - cs[:-30]
                        # roll[d] = sum of flows of days d-29..d ; known after day d ends -> shift by 2 (lag 1 day)
                        sh = np.full(len(t), np.nan); sh[2:] = roll[:-2]
                        sh[: max(first + 31, 0)] = np.nan
                        arr[:, j] = sh
    meta = dict(chosen=chosen, rejected=[{k: r[k] for k in ("sym", "kind", "key", "gecko", "price_ratio")} for r in rejected])
    np.savez_compressed(OUT / "fund_grid.npz", TVL=TVL, FEE30=FEE, REV30=REV)
    json.dump(meta, open(OUT / "mapping.json", "w", encoding="utf-8"), indent=1)
    print(len(chosen), "coins mapped;", len(rejected), "rejected")
    return TVL, FEE, REV, meta


if __name__ == "__main__":
    build()
